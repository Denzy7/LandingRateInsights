"""Build the system-libs version: a single executable zipapp of the landingrate package only.

It runs on the machine's own Python 3.10+ and uses the installed PySide6/Qt, pandas, numpy and
matplotlib (Arch: pacman -S pyside6 python-pandas python-matplotlib; Debian/Ubuntu 25.10+:
apt install python3-pyside6.qtwidgets python3-pyside6.qtsvg python3-pandas python3-matplotlib; anywhere: pip install
-r requirements.txt). On Linux it is directly executable; on Windows run it with `py file.pyz`.

    python tools/build_pyz.py [-o dist/LandingRateInsights.pyz]
"""
import argparse
import shutil
import stat
import sys
import tempfile
import zipapp
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MAIN = '''import sys

if sys.version_info < (3, 10):
    sys.exit("Landing Rate Insights needs Python 3.10 or newer")
try:
    import PySide6, pandas, numpy, matplotlib  # noqa: F401,E401
except ImportError as e:
    sys.exit(f"Missing dependency: {e.name}. Install PySide6, pandas, numpy and matplotlib "
             "with your package manager (or: pip install PySide6 pandas numpy matplotlib).")
from landingrate.app import main

sys.exit(main())
'''


def build(out: Path) -> Path:
    with tempfile.TemporaryDirectory() as d:
        stage = Path(d)
        shutil.copytree(ROOT / "landingrate", stage / "landingrate",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (stage / "__main__.py").write_text(MAIN, encoding="utf-8")
        out.parent.mkdir(parents=True, exist_ok=True)
        zipapp.create_archive(stage, out, interpreter="/usr/bin/env python3", compressed=True)
    out.chmod(out.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return out


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("-o", "--output", default=str(ROOT / "dist" / "LandingRateInsights.pyz"))
    out = build(Path(p.parse_args().output))
    print(f"{out}  ({out.stat().st_size / 1024:.0f} KiB)")
    sys.exit(0)
