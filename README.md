# Landing Rate Insights

A desktop dashboard and text report for the landing log written by the **LandingRate.lua**
FlyWithLua script in X-Plane. It also matches each landing against X-Plane's own pilot logbook
(`Output/logbooks/X-Plane Pilot.txt`).

Tabs: Overview · Progress · Aircraft · Technique · Sessions · Landings · Logbook. Hover a chart
point for details and click it to open that landing. In live mode the dashboard refreshes as
soon as the Lua script logs a new landing.

## Download and run

Get a file from the [latest release](../../releases/latest). Each file is the program itself, not a zip.

**Not sure which?** Use the first two. They work without installing anything.

| You have | Download | How to run |
|---|---|---|
| Windows | `LandingRateInsights-x.y.z-win64.exe` | Double-click it. Windows SmartScreen may warn about an unsigned app: *More info ▸ Run anyway*. |
| Linux | `LandingRateInsights-x.y.z-linux64` | `chmod +x LandingRateInsights-*-linux64 && ./LandingRateInsights-*-linux64` |
| Linux, with PySide6 already installed | `LandingRateInsights-x.y.z-linux-system` (~40 KB) | `chmod +x` it, then run it. It uses your distro's Python and Qt (packages below). |
| Windows, with Python already installed | `LandingRateInsights-x.y.z-win-system.pyz` (~40 KB) | `py -m pip install PySide6 pandas numpy matplotlib` once, then double-click the file or run `py LandingRateInsights-x.y.z-win-system.pyz`. A console window stays open next to the app. |

The two bundled builds (`win64`, `linux64`) are large, about 85–120 MB, and take a few seconds to
start because they unpack themselves on launch. The `-system` builds are tiny and start
instantly, but they need these packages:

* Arch: `pacman -S pyside6 qt6-svg python-pandas python-matplotlib`
* Debian 13 / Ubuntu 25.10+: `apt install python3-pyside6.qtwidgets python3-pyside6.qtsvg python3-pandas python3-matplotlib`
* Windows or anything else: `pip install -r requirements.txt`, or the `pip` line above

If something is missing, the `-system` build says which package to install.

### Updates

On start the app asks GitHub once for the latest release, quietly, in the background. When a
newer one exists, the sidebar shows **· vX.Y.Z available** next to the version. Click it to open the
release page. *Help ▸ Check for updates now* checks on demand. *Help ▸ Check for updates on start*
turns the automatic check off. Nothing is downloaded or installed automatically. If you're
offline, the check fails silently.

Any build also takes a log path, the `report` subcommand, `--version` and `--self-test`:
`./LandingRateInsights-x.y.z-linux64 ~/X-Plane\ 11/LandingRate.log`

## Run from source

```sh
pip install -r requirements.txt        # or the distro packages listed below
python -m landingrate /path/to/X-Plane\ 11/LandingRate.log
python -m landingrate report /path/to/LandingRate.log --aircraft B738 --since 2026-01-01
```

With no path given, it opens the last log you used, then `$LANDINGRATE_LOG`, then a
`LandingRate.log` in the current folder (or next to the executable). The logbook is picked up
from `Output/logbooks/` next to the log, or you can choose one with *File ▸ Open logbook…*.

## Builds and releases

`.github/workflows/build.yml` runs on every push and pull request:

1. **test**: pyflakes and pytest on Linux and Windows.
2. **build-bundled**: PyInstaller one-file builds, `win64.exe` and `linux64`. The Linux one is built
   on Ubuntu 22.04 for an older glibc, so it also runs on older distros. Each build runs its own
   `--self-test`.
3. **build-system**: the zipapp, as `linux-system` and `win-system.pyz`. The Windows one is
   self-tested against pip-installed libraries.
4. **test-system**: runs the `linux-system` build and the test suite inside Arch and Debian 13
   containers, against their packaged PySide6. No pip.
5. **release**: only for tags. Attaches all four files to the GitHub Release.

Each run's four files are also on its Actions page (*Artifacts*). They upload as plain files,
not zips.

**To publish a release:**

```sh
git tag v1.2.0
git push origin v1.2.0
```

The tag's run writes `1.2.0` into `landingrate/__init__.py` for the build, so file names and
`--version` match the tag. When all jobs pass, it creates the *v1.2.0* release with the four
files. The version in the repo stays as committed. Bump `__version__` there too if you want
source checkouts to report the new version. If a job fails, no release is published. Fix it,
then delete and re-push the tag:
`git tag -d v1.2.0 && git push origin :v1.2.0`.

The app icon is drawn by `tools/make_assets.py` (needs Pillow) into `landingrate/assets/`.
Rerun it after changing the brand colours and commit the output.

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
