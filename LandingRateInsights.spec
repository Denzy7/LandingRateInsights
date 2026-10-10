# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build: one self-contained file with Python, Qt/PySide6, pandas, numpy and matplotlib.

    pyinstaller --noconfirm LandingRateInsights.spec

Output: dist/LandingRateInsights (Linux) or dist/LandingRateInsights.exe (Windows).

Options (environment variables):
    LRI_ONEDIR=1    build dist/LandingRateInsights/ (a folder) instead; starts faster
    LRI_CONSOLE=1   keep a console window (handy for tracebacks on Windows)

For a small build that uses the system's Python, Qt and PySide6 instead, see tools/build_pyz.py.
"""
import os

HERE = os.path.abspath(SPECPATH)  # noqa: F821  (provided by PyInstaller)

a = Analysis(  # noqa: F821
    [os.path.join(HERE, "run_insights.py")],
    pathex=[HERE],
    hiddenimports=["matplotlib.backends.backend_qtagg"],
    # nothing here uses these; leaving them out keeps the file a lot smaller
    excludes=["tkinter", "matplotlib.backends.backend_tkagg", "scipy", "IPython", "pytest",
              "PyQt5", "PyQt6", "PySide2",
              "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQuick", "PySide6.QtQml",
              "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtPdf"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821
opts = dict(name="LandingRateInsights", console=os.environ.get("LRI_CONSOLE") == "1", upx=False)
if os.environ.get("LRI_ONEDIR") == "1":
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, **opts)  # noqa: F821
    coll = COLLECT(exe, a.binaries, a.datas, name="LandingRateInsights", upx=False)  # noqa: F821
else:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], **opts)  # noqa: F821
