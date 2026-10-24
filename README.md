# Sora Battery

A tiny Windows system-tray battery indicator for the **Ninjutso Sora V2** wireless mouse.

The official configurator ([NinjaForce](https://www.ninjaforce.co)) only runs in the browser, so there's no way to see the battery level at a glance. This app sits in the tray, shows the percentage as a number, and warns you before the mouse dies.

![Tray icon states](docs/icons.png)

| Icon | Meaning |
|------|---------|
| Green | Battery above 20% |
| Orange | 20% or less |
| Red | 10% or less |
| Blue | Charging (cable plugged in) |
| Grey `?` | Mouse not found or asleep |

## Features

- Battery percentage right in the tray icon, with a tooltip
- Windows notifications at 20% and 10% (they re-arm once you charge)
- Works over the 2.4 GHz dongle and over the USB cable
- Optional **Start with Windows** toggle in the menu (adds an entry to your user's `Run` registry key)
- No driver, no background service, no network access; it's one Python file

## Install

Requires Windows and Python 3.9+.

```bash
git clone https://github.com/lllexalllepexa/sora_battery.git
cd sora_battery
pip install -r requirements.txt
```

## Usage

Start the tray app with no console window:

```bash
pythonw sora_battery.py
```

Print the current reading once and exit:

```bash
python sora_battery.py --once
```

Output is `(percent, charging)`, for example `(88, False)`, or `None` if the mouse wasn't found.

**Tray menu:** the current status, **Refresh now** (also triggered by left-clicking the icon), **Start with Windows**, **Quit**.

The battery is checked every 2 minutes. To change the interval or the warning levels, edit `POLL_SECONDS`, `LOW_PERCENT` and `CRITICAL_PERCENT` at the top of `sora_battery.py`.

### Build a standalone .exe

```bash
pip install pyinstaller
pyinstaller --onefile --noconsole sora_battery.py
```

The executable is written to `dist\sora_battery.exe`.

## How it works

The NinjaForce web driver talks to the mouse over WebHID. The battery query below was found by reading its JavaScript.

**Device**

| | VID | PID |
|---|---|---|
| 2.4 GHz dongle | `0x1915` | `0xAE1C` |
| Mouse on USB cable | `0x1915` | `0xAE12` |

Commands go to the vendor-defined HID collection (usage page `0xFFA0`), which only uses feature reports: `0x05` (31 bytes) for commands and `0x04` (703 bytes) for bulk data.

**Battery query**

Send feature report `0x05` with a 31-byte payload:

```
offset  0     1     2     3     4     5     6     7 ... 30
value   0x15  0x00  0x00  0x01  0x00  0x00  0x04  0x00 ... 0x00
```

Then read feature report `0x05`. With the report ID at offset 0:

| Offset | Meaning |
|--------|---------|
| 1 | Command echo (`0x15`) |
| 9 | Battery percent (0 means no data yet, so retry) |
| 10 | `1` if charging |

Example reply: `05 15 00 00 01 00 00 04 00 58 00 …` → 88%, not charging.

The app matches any device with VID `0x1915`, usage page `0xFFA0` and "Sora" in the product name, and tries the wired connection first. Other Sora variants (for example the 8K version) will probably work but haven't been tested.

## Troubleshooting

- **Grey `?` icon:** the mouse is switched off or asleep. Move it to wake it up, then choose **Refresh now**.
- **Doesn't work while NinjaForce is open:** close the browser tab. Two programs querying the mouse at the same moment can interfere with each other.
- **"Start with Windows" opens the wrong file:** the startup entry stores the script's path. If you move the folder, turn the option off and on again.

## Disclaimer

This is an unofficial project and isn't affiliated with or endorsed by Ninjutso. The app only sends the read-only battery query and never changes mouse settings, but you use it at your own risk.

## License

[MIT](LICENSE)
