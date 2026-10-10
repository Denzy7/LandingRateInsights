# Landing Rate Insights

A desktop dashboard and text report for the landing log written by the **LandingRate.lua**
FlyWithLua script in X-Plane. It also matches each landing against X-Plane's own pilot logbook
(`Output/logbooks/X-Plane Pilot.txt`).

Tabs: Overview · Progress · Aircraft · Technique · Sessions · Landings · Logbook. Hover a chart
point for details and click it to open that landing. In live mode the dashboard refreshes as
soon as the Lua script logs a new landing.

## Run from source

```sh
pip install -r requirements.txt        # or the distro packages listed below
python -m landingrate /path/to/X-Plane\ 11/LandingRate.log
python -m landingrate report /path/to/LandingRate.log --aircraft B738 --since 2026-01-01
```

With no path given, it opens the last log you used, then `$LANDINGRATE_LOG`, then a
`LandingRate.log` in the current folder (or next to the executable). The logbook is picked up
from `Output/logbooks/` next to the log, or you can choose one with *File ▸ Open logbook…*.

## Builds

One CI run (`.github/workflows/build.yml`) builds four files. Each one is uploaded as the file
itself, not a zip:

| File | Contains | Needs on the machine |
|---|---|---|
| `LandingRateInsights-x.y.z-win64.exe` | Python, Qt/PySide6, pandas, numpy, matplotlib | nothing |
| `LandingRateInsights-x.y.z-linux64` | the same, for Linux | nothing |
| `LandingRateInsights-x.y.z-linux-system` (~40 KB) | only this app's code | Python ≥ 3.10 with PySide6, pandas, numpy, matplotlib |
| `LandingRateInsights-x.y.z-win-system.pyz` (~40 KB) | only this app's code | the same, on Windows |

System packages for the `-system` builds:

* Arch: `pacman -S pyside6 qt6-svg python-pandas python-matplotlib`
* Debian 13 / Ubuntu 25.10+: `apt install python3-pyside6.qtwidgets python3-pyside6.qtsvg python3-pandas python3-matplotlib`
* Windows or anything else: `pip install -r requirements.txt`, then `py LandingRateInsights-x.y.z-win-system.pyz`

On Linux, `chmod +x` the downloaded file and run it. To publish a release, push a `vX.Y.Z` tag.
The run stamps that version into the build and attaches all four files to the GitHub Release.

Local builds:

```sh
pip install -r requirements-dev.txt
pyinstaller --noconfirm LandingRateInsights.spec   # dist/LandingRateInsights[.exe]
python tools/build_pyz.py                           # dist/LandingRateInsights.pyz
```

Both builds have `--version` and `--self-test`. The self-test draws every tab from a generated
log, uses throwaway settings and exits non-zero on failure. Set `QT_QPA_PLATFORM=offscreen` to
run it without a display.

## Tests

```sh
QT_QPA_PLATFORM=offscreen python -m pytest -q
```
