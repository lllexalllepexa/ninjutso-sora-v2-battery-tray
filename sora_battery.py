"""
Ninjutso Sora V2 battery indicator for the Windows system tray.

Protocol (taken from the NinjaForce web driver, www.ninjaforce.co):
  device     VID 0x1915, PID 0xAE1C (2.4GHz dongle) or 0xAE12 (mouse on cable),
             vendor collection usage page 0xFFA0
  request    feature report 0x05, 31 bytes: [0]=0x15 (21), [3]=0x01, [6]=0x04
  response   get feature report 0x05 -> byte 9 = battery %, byte 10 = 1 if charging
             (byte 9 == 0 means "no data yet" / mouse asleep -> retry once)

Run:  pythonw sora_battery.py
"""
import sys
import threading
import time
import winreg

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


def find_paths():
    """Vendor interfaces of any Sora: wired cable first, then the 2.4GHz dongle."""
    found = [d for d in hid.enumerate(VID, 0)
             if d["usage_page"] == VENDOR_USAGE_PAGE and "Sora" in (d["product_string"] or "")]
    found.sort(key=lambda d: d["product_id"] == PID)  # dongle (PID) last
    return [d["path"] for d in found]


def query(path):
    dev = hid.device()
    try:
        dev.open_path(path)
        cmd = bytearray(31)
        cmd[0], cmd[3], cmd[6] = 21, 1, 4
        for _ in range(3):
            dev.send_feature_report(bytes([REPORT_ID]) + bytes(cmd))
            time.sleep(0.03)
            resp = dev.get_feature_report(REPORT_ID, 32)
            if len(resp) > 10 and resp[1] == 21 and resp[9]:
                return resp[9], resp[10] == 1
        return None
    except OSError:
        return None
    finally:
        dev.close()


def read_battery():
    """Return (percent, charging) or None if the mouse isn't reachable."""
    for path in find_paths():
        result = query(path)
        if result:
            return result
    return None


def _font(size):
    for name in ("segoeuib.ttf", "arialbd.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def make_icon(percent, charging):
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if percent is None:
        color, text = (128, 128, 128), "?"
    else:
        if charging:
            color = (60, 150, 255)
        elif percent <= CRITICAL_PERCENT:
            color = (230, 40, 40)
        elif percent <= LOW_PERCENT:
            color = (245, 170, 0)
        else:
            color = (40, 200, 80)
        text = "F" if percent >= 100 else str(percent)
    draw.rounded_rectangle((0, 0, 63, 63), radius=12, fill=color)
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
        if self.percent is None:
            return "Mouse not found / asleep"
        return f"Battery {self.percent}%" + (" (charging)" if self.charging else "")

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
        while not self.stop.is_set():
            result = read_battery()
            self.percent, self.charging = result if result else (None, False)
            self.icon.icon = make_icon(self.percent, self.charging)
            self.icon.title = "Sora V2: " + self.status_text()
            self.icon.update_menu()
            self.check_warning()
            self.wake.wait(POLL_SECONDS)
            self.wake.clear()

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
