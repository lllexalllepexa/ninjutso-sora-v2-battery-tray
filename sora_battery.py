"""
Ninjutso Sora V2 battery indicator for the Windows system tray.

Protocol (taken from the NinjaForce web driver, www.ninjaforce.co):
  device     VID 0x1915, PID 0xAE1C (2.4GHz dongle) or 0xAE12 (mouse on cable),
             vendor collection usage page 0xFFA0
  request    feature report 0x05, 31 bytes: [0]=0x15 (21), [3]=0x01, [6]=0x04
  response   get feature report 0x05 -> byte 9 = battery %, byte 10 = 1 if charging,
             byte 11 = 1 if fully charged, byte 12 = 1 if the mouse is connected to the dongle
             (byte 9 == 0 means "no data yet" / mouse asleep -> retry)
             While charging, byte 9 stays at the level from before the cable was plugged in.
             Byte 12 is only meaningful through the dongle; on the cable it reads 0.

Run:  pythonw sora_battery.py
"""
import ctypes
import ctypes.wintypes
import sys
import threading
import time
import winreg
from typing import NamedTuple

import hid
import pystray
from PIL import Image, ImageDraw, ImageFont

VID, PID = 0x1915, 0xAE1C
VENDOR_USAGE_PAGE = 0xFFA0
REPORT_ID = 0x05

POLL_SECONDS = 120
LOW_PERCENT = 20
CRITICAL_PERCENT = 10

APP_NAME = "SoraBattery"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


class Reading(NamedTuple):
    percent: int
    charging: bool
    full: bool
    online: bool  # False: the dongle answered but the mouse is off or asleep


def find_devices():
    """Vendor interfaces of any Sora: wired cable first, then the 2.4GHz dongle."""
    found = [d for d in hid.enumerate(VID, 0)
             if d["usage_page"] == VENDOR_USAGE_PAGE and "Sora" in (d["product_string"] or "")]
    found.sort(key=lambda d: d["product_id"] == PID)  # dongle (PID) last
    return found


def query(info):
    dev = hid.device()
    try:
        dev.open_path(info["path"])
        cmd = bytearray(31)
        cmd[0], cmd[3], cmd[6] = 21, 1, 4
        resp = None
        for _ in range(3):
            dev.send_feature_report(bytes([REPORT_ID]) + bytes(cmd))
            time.sleep(0.03)
            resp = dev.get_feature_report(REPORT_ID, 32)
            if len(resp) > 12 and resp[1] == 21 and resp[9]:
                break
        if not resp or len(resp) <= 12 or resp[1] != 21:
            return None
        wired = info["product_id"] != PID
        online = wired or resp[12] == 1
        if not resp[9]:
            if wired:
                return None
            online = False  # dongle has no data from the mouse
        return Reading(resp[9], resp[10] == 1, resp[11] == 1, online)
    except OSError:
        return None
    finally:
        dev.close()


def cursor_pos():
    pt = ctypes.wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def ninjaforce_open():
    """True if a visible window title mentions NinjaForce (the browser's active tab).

    The dongle has a single reply buffer, so our query could clobber the site's reads.
    """
    user32 = ctypes.windll.user32
    found = False

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def check(hwnd, _):
        nonlocal found
        if user32.IsWindowVisible(hwnd):
            # InternalGetWindowText never sends WM_GETTEXT, so a hung window can't block us.
            buf = ctypes.create_unicode_buffer(512)
            if user32.InternalGetWindowText(hwnd, buf, 512):
                found = "ninjaforce" in buf.value.lower()
        return not found

    user32.EnumWindows(check, 0)
    return found


def wired_connected():
    return any(d["product_id"] != PID for d in find_devices())


def read_battery():
    """Return a Reading, or None if neither the mouse nor the dongle answers."""
    offline = None
    for info in find_devices():
        result = query(info)
        if result and result.online:
            return result
        offline = offline or result
    return offline


def _font(size):
    for name in ("segoeuib.ttf", "arialbd.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def make_icon(percent, charging, full=False):
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if percent is None:
        color, text = (128, 128, 128), "?"
    else:
        if charging and full:
            color = (40, 200, 80)
        elif charging:
            color = (60, 150, 255)
        elif percent <= CRITICAL_PERCENT:
            color = (230, 40, 40)
        elif percent <= LOW_PERCENT:
            color = (245, 170, 0)
        else:
            color = (40, 200, 80)
        text = "F" if percent >= 100 else str(percent)
    draw.rounded_rectangle((0, 0, 63, 63), radius=12, fill=color)
    if charging and percent is not None:
        # The mouse keeps reporting the pre-charge level while on the cable, so draw a bolt.
        draw.polygon([(36, 6), (14, 36), (30, 36), (26, 58), (50, 26), (34, 26)], fill="white")
        return img
    font = _font(40 if len(text) <= 2 else 30)
    draw.text((32, 33), text, font=font, fill="white", anchor="mm")
    return img


def autostart_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except OSError:
        return False


def set_autostart(enable):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enable:
            exe = sys.executable.replace("python.exe", "pythonw.exe")
            target = f'"{sys.argv[0]}"' if getattr(sys, "frozen", False) else f'"{exe}" "{__file__}"'
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, target)
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass


class TrayApp:
    def __init__(self):
        self.percent = None
        self.charging = False
        self.full = False
        self.found = False  # dongle or cable seen, even if the mouse is off
        self.paused = False  # NinjaForce is open
        self.warned_at = None  # lowest threshold already warned about
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.icon = pystray.Icon(
            APP_NAME,
            make_icon(None, False),
            "Sora V2: connecting...",
            menu=pystray.Menu(
                pystray.MenuItem(lambda _: self.status_text(), None, enabled=False),
                pystray.MenuItem("Refresh now", lambda: self.wake.set(), default=True),
                pystray.MenuItem("Start with Windows", self.toggle_autostart,
                                 checked=lambda _: autostart_enabled()),
                pystray.MenuItem("Quit", self.quit),
            ),
        )

    def status_text(self):
        if self.paused:
            return "Paused while NinjaForce is open"
        if self.percent is None:
            return "Mouse off or asleep" if self.found else "Mouse / receiver not found"
        if self.charging and self.full:
            return "Fully charged"
        if self.charging:
            return f"Charging (was {self.percent}%, updates when unplugged)"
        return f"Battery {self.percent}%"

    def toggle_autostart(self, icon, item):
        set_autostart(not autostart_enabled())

    def quit(self, icon, item):
        self.stop.set()
        self.wake.set()
        icon.stop()

    def check_warning(self):
        if self.percent is None or self.charging or self.percent > LOW_PERCENT + 5:
            self.warned_at = None  # re-arm once charging / recovered
            return
        for threshold in (CRITICAL_PERCENT, LOW_PERCENT):
            if self.percent <= threshold and (self.warned_at is None or threshold < self.warned_at):
                self.warned_at = threshold
                self.icon.notify(f"Sora V2 battery at {self.percent}%. Plug it in soon.",
                                 "Mouse battery low")
                break

    def poll_loop(self):
        # Every query reaches the mouse over 2.4GHz and resets its sleep timer, so only
        # ask while the cursor is moving (the mouse is awake anyway) or on the cable.
        last_pos = None
        forced = True
        while not self.stop.is_set():
            pos = cursor_pos()
            paused = ninjaforce_open()  # stay off the dongle while the web driver uses it
            if paused != self.paused:
                self.paused = paused
                self.icon.title = "Sora V2: " + self.status_text()
                self.icon.update_menu()
            if not paused and (forced or pos != last_pos or wired_connected()):
                self.refresh()
            last_pos = pos
            forced = self.wake.wait(POLL_SECONDS)
            self.wake.clear()

    def refresh(self):
        result = read_battery()
        self.found = result is not None
        if result and result.online:
            self.percent, self.charging, self.full = result.percent, result.charging, result.full
        else:
            self.percent, self.charging, self.full = None, False, False
        self.icon.icon = make_icon(self.percent, self.charging, self.full)
        self.icon.title = "Sora V2: " + self.status_text()
        self.icon.update_menu()
        self.check_warning()

    def run(self):
        def setup(icon):
            icon.visible = True
            threading.Thread(target=self.poll_loop, daemon=True).start()
        self.icon.run(setup)


if __name__ == "__main__":
    if "--once" in sys.argv:
        print(read_battery())
    else:
        TrayApp().run()
