# activitytrack-tui

[ActivityTrack](https://github.com/zipclaude/activitytrack) in the terminal, full screen: the
activities running and the Home list of the app (to-dos and stopped activities), worked from the
keyboard; the sync with [ActivityTrack Web](https://github.com/zipclaude/activitytrack-web) runs in
the background.

It is the same application as [ActivityTrack Desktop](https://github.com/zipclaude/activitytrack-desktop)
and the command line [activitytrack-cli](https://github.com/zipclaude/activitytrack-cli): they share the
data folder, the pairing and the token through the library
[python3-activitytrack](https://github.com/zipclaude/python3-activitytrack). To pair the PC use the desktop
application or `activitytrack pair`.

| Key | |
|---|---|
| ↑ ↓, j k | move |
| Enter | the actions of the row |
| n, t | new activity, new to-do |
| p, s, v, u | pause/resume, stop, travel, suspend |
| d | to-do done |
| f, g | period, states of the list |
| r | sync now |
| q, Esc | quit, close a popup |

`man activitytrack-tui` has the details.

## Install

From the [releases](https://github.com/zipclaude/activitytrack-tui/releases):

- **Debian 13 / 14, Ubuntu 24.04 / 26.04**: `sudo apt install ./python3-activitytrack_<version>-1-<distro>_all.deb ./activitytrack-tui_<version>-1-<distro>_all.deb`
  (the library from its own release);
- **Windows 10 / 11**: `activitytrack-tui-<version>-windows.zip`, `activitytrack-tui.exe` (Windows Terminal recommended);
- **elsewhere**: `pip install` the library, then the tarball.

## Development

```sh
apt install python3-cryptography python3-keyring flake8
git clone https://github.com/zipclaude/python3-activitytrack ../python3-activitytrack
export PYTHONPATH=$PWD/../python3-activitytrack
python3 -m activitytrack_tui
python3 -m unittest discover -s tests -t .
python3 scripts/strings.py --check
flake8 activitytrack_tui tests packaging/activitytrack-tui
```

## License

GPL-3.0-or-later.
