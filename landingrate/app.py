"""
Landing Rate Insights
=====================
PySide6 dashboard for the log written by LandingRate.lua (FlyWithLua).
Analysis lives in landingrate/analysis.py (also usable as a CLI report).

Tabs
----
- Overview   KPI tiles, recent form, VS distribution, rating mix, plain-language insights
- Progress   rolling median + spread over your history, learning curve per type, monthly volume
- Aircraft   per-type scorecard (trend, consistency, flare habits, bounces) + VS spread per type
- Technique  what drives your VS: float time, pitch rate at touchdown, G, flare grade
- Sessions   per-session table, session replay, warm-up effect, time of day, weekday
- Landings   every log entry with exclusion flags; click one for a full breakdown
- Logbook    X-Plane's pilot logbook matched to the Lua log: airports, night, cloud, circuits,
             flight length, and landings the Lua missed
- Tips       where your score points go, and how to win them back (a random tip sits in the sidebar)

Hover any chart point for details; click a point to open that landing.
Live mode watches the log and refreshes as soon as the Lua script writes a new landing.

Run:   python -m landingrate [path/to/LandingRate.log]
       python -m landingrate report [log] [--aircraft B738] [--since 2026-01-01]   (text report)
Needs: pip install PySide6 pandas numpy matplotlib
"""

import os

# Must run before matplotlib picks a Qt binding.
os.environ.setdefault("QT_API", "pyside6")

import sys
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import numpy as np
import pandas as pd
from PySide6.QtCore import (QAbstractTableModel, QDate, QEvent, QFileSystemWatcher, QModelIndex, QObject, QSettings,
                            QSortFilterProxyModel, Qt, QTimer, QUrl, Signal)
from PySide6.QtGui import (QAction, QActionGroup, QColor, QCursor, QDesktopServices, QGuiApplication, QIcon,
                           QPalette, QPixmap)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDateEdit,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGridLayout, QGroupBox,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMainWindow, QMessageBox, QPushButton, QScrollArea, QSpinBox, QSplitter,
                               QTableView, QTabWidget, QTextBrowser, QToolTip, QVBoxLayout, QWidget)

import matplotlib

matplotlib.use("QtAgg")
import matplotlib.dates as mdates
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator

from landingrate import APP_NAME, __version__
from landingrate import analysis as lra
from landingrate import updates

# where to look for LandingRate.log / exports: next to a frozen executable, else the working dir
APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path.cwd()


def asset_pixmap(name: str) -> QPixmap:
    """A bundled image; read through importlib so it also works from the .pyz and frozen builds."""
    pm = QPixmap()
    try:
        pm.loadFromData(resources.files("landingrate").joinpath("assets", name).read_bytes())
    except (OSError, FileNotFoundError):
        pass
    return pm


def app_icon() -> QIcon:
    icon = QIcon()
    for s in (16, 32, 48, 64, 128, 256, 512):
        pm = asset_pixmap(f"icon_{s}.png")
        if not pm.isNull():
            icon.addPixmap(pm)
    return icon


class _UpdateRelay(QObject):
    """Carries the update check result from its worker thread to the GUI thread."""
    done = Signal(dict, bool)


# ----------------------------------------------------------------------
# Theme tokens (light + dark chosen separately, validated palette)
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class Theme:
    name: str
    surface: str
    page: str
    ink: str
    ink2: str
    muted: str
    grid: str
    axis: str
    series: tuple
    good: str = "#0ca30c"
    warning: str = "#fab219"
    serious: str = "#ec835a"
    critical: str = "#d03b3b"
    good_text: str = "#006300"
    notice_text: str = "#9a6400"  # amber that stays readable on the light surface

    @property
    def rating(self):
        return {"BUTTER": self.series[0], "GREAT": self.good, "ACCEPTABLE": self.warning,
                "HARD": self.serious, "WASTED": self.critical}


LIGHT = Theme("light", "#fcfcfb", "#f9f9f7", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7",
              ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7"))
DARK = Theme("dark", "#1a1a19", "#0d0d0d", "#ffffff", "#c3c2b7", "#898781", "#2c2c2a", "#383835",
             ("#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9"),
             good_text="#0ca30c", notice_text="#fab219")

TARGET = (-200, -60)  # shaded "target window" on VS axes


def system_is_dark() -> bool:
    try:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:
        return QApplication.palette().color(QPalette.Window).lightness() < 128


def apply_qt_theme(app: QApplication, t: Theme):
    app.setStyle("Fusion")
    p = QPalette()
    surface, page, ink = QColor(t.surface), QColor(t.page), QColor(t.ink)
    p.setColor(QPalette.Window, page)
    p.setColor(QPalette.WindowText, ink)
    p.setColor(QPalette.Base, surface)
    p.setColor(QPalette.AlternateBase, QColor(t.grid if t.name == "dark" else "#f3f2ee"))
    p.setColor(QPalette.Text, ink)
    p.setColor(QPalette.Button, surface)
    p.setColor(QPalette.ButtonText, ink)
    p.setColor(QPalette.ToolTipBase, surface)
    p.setColor(QPalette.ToolTipText, ink)
    p.setColor(QPalette.PlaceholderText, QColor(t.muted))
    p.setColor(QPalette.Highlight, QColor(t.series[0]))
    p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.Mid, QColor(t.axis))
    p.setColor(QPalette.Midlight, QColor(t.grid))
    p.setColor(QPalette.Disabled, QPalette.Text, QColor(t.muted))
    p.setColor(QPalette.Disabled, QPalette.WindowText, QColor(t.muted))
    p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(t.muted))
    app.setPalette(p)
    border = "rgba(255,255,255,0.10)" if t.name == "dark" else "rgba(11,11,11,0.10)"
    app.setStyleSheet(f"""
        QFrame#tile {{ background: {t.surface}; border: 1px solid {border}; border-radius: 8px; }}
        QLabel#tileValue {{ font-size: 20pt; font-weight: 600; color: {t.ink}; }}
        QLabel#tileCaption {{ color: {t.ink2}; }}
        QLabel#tileSub {{ color: {t.muted}; }}
        QFrame#panel {{ background: {t.surface}; border: 1px solid {border}; border-radius: 8px; }}
        QLabel#panelTitle {{ font-weight: 600; color: {t.ink}; }}
        QLabel#panelSub {{ color: {t.muted}; }}
        QToolTip {{ background: {t.surface}; color: {t.ink}; border: 1px solid {t.axis}; padding: 4px; }}
        QTableView {{ gridline-color: {t.grid}; }}
        QLabel#brandName {{ font-size: 13pt; font-weight: 600; color: {t.ink}; }}
        QLabel#brandVersion {{ color: {t.muted}; }}
    """)


def style_axes(ax, t: Theme, grid="y"):
    ax.set_facecolor(t.surface)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(t.axis)
        ax.spines[side].set_linewidth(0.8)
    ax.tick_params(colors=t.axis, labelcolor=t.ink2, labelsize=8.5, length=3)
    if grid:
        ax.grid(True, axis=grid, color=t.grid, linewidth=0.7)
    ax.set_axisbelow(True)
    ax.xaxis.label.set_color(t.ink2)
    ax.yaxis.label.set_color(t.ink2)
    ax.xaxis.label.set_size(9)
    ax.yaxis.label.set_size(9)


def ax_title(ax, t: Theme, text, sub=None):
    ax.set_title(text, loc="left", fontsize=10, color=t.ink, fontweight="bold", pad=14 if sub else 6)
    if sub:
        ax.text(0, 1.01, sub, transform=ax.transAxes, fontsize=8.5, color=t.muted, va="bottom")


def target_band(ax, t: Theme, label=True):
    ax.axhspan(*TARGET, color=t.good, alpha=0.07, lw=0, zorder=0)
    if label:
        ax.text(0.005, TARGET[1] - 6, "target", transform=ax.get_yaxis_transform(), fontsize=7.5,
                color=t.muted, va="top")


def vs_ylim(ax, vs: pd.Series):
    if len(vs):
        floor = vs.min() if len(vs) < 200 else vs.quantile(0.005)
        lo = min(-400, float(floor) - 40)
        ax.set_ylim(max(lo, -1100), max(10, float(vs.max()) + 15))


def fmt_fpm(v) -> str:
    return f"{v:+.0f} fpm" if pd.notna(v) else "–"


def landing_tip(r) -> str:
    fl = f"{r['float_s']:.1f}s" if pd.notna(r["float_s"]) else "–"
    return (f"{r['time']:%Y-%m-%d %H:%M} · {r['aircraft'] or '?'}\n"
            f"{r['vs']:+.0f} fpm · {r['rating']} · score {r['score']:.0f}\n"
            f"G {r['g']:.2f} · float {fl} · {r['flare']}")


# ----------------------------------------------------------------------
# Chart canvas with hover tooltips and click-to-open
# ----------------------------------------------------------------------
class Chart(FigureCanvasQTAgg):
    landingClicked = Signal(int)

    def __init__(self, height=3.0):
        self.fig = Figure(figsize=(6, height), layout="constrained")
        super().__init__(self.fig)
        self.setMinimumHeight(int(height * 70))
        self._points = []   # (ax, x, y, tips, ids)
        self._patches = []  # (ax, patch, tip)
        self.mpl_connect("motion_notify_event", self._on_move)
        self.mpl_connect("button_press_event", self._on_click)

    def reset(self, t: Theme):
        self.fig.clear()
        self.fig.set_facecolor(t.surface)
        self._points, self._patches = [], []

    def hover_points(self, ax, x, y, tips, ids=None):
        self._points.append((ax, np.asarray(x, float), np.asarray(y, float), list(tips),
                             None if ids is None else list(ids)))

    def hover_patch(self, ax, patch, tip):
        self._patches.append((ax, patch, tip))

    def _hit(self, event):
        if event.inaxes is None:
            return None
        best = None
        for ax, x, y, tips, ids in self._points:
            if ax is not event.inaxes or not len(x):
                continue
            pts = ax.transData.transform(np.column_stack([x, y]))
            d = np.hypot(pts[:, 0] - event.x, pts[:, 1] - event.y)
            i = int(np.nanargmin(d)) if np.isfinite(d).any() else None
            if i is not None and d[i] <= 14 and (best is None or d[i] < best[0]):
                best = (d[i], tips[i], ids[i] if ids else None)
        if best:
            return best[1], best[2]
        for ax, patch, tip in self._patches:
            if ax is event.inaxes and patch.contains(event)[0]:
                return tip, None
        return None

    def _on_move(self, event):
        hit = self._hit(event)
        if hit:
            QToolTip.showText(QCursor.pos(), hit[0], self)
            self.setCursor(Qt.PointingHandCursor if hit[1] is not None else Qt.ArrowCursor)
        else:
            QToolTip.hideText()
            self.setCursor(Qt.ArrowCursor)

    def _on_click(self, event):
        hit = self._hit(event)
        if hit and hit[1] is not None and event.button == 1 and not event.dblclick:
            self.landingClicked.emit(int(hit[1]))


def panel(title: str, sub: str, *widgets, toolbar_for: Chart | None = None) -> QFrame:
    f = QFrame(objectName="panel")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(12, 10, 12, 10)
    head = QHBoxLayout()
    col = QVBoxLayout()
    col.setSpacing(0)
    col.addWidget(QLabel(title, objectName="panelTitle"))
    if sub:
        s = QLabel(sub, objectName="panelSub")
        s.setWordWrap(True)
        col.addWidget(s)
    head.addLayout(col, 1)
    if toolbar_for is not None:
        tb = NavigationToolbar2QT(toolbar_for, f, coordinates=False)
        tb.setIconSize(tb.iconSize() * 0.75)
        head.addWidget(tb)
    lay.addLayout(head)
    for w in widgets:
        lay.addWidget(w, 1)
    return f


class Tile(QFrame):
    def __init__(self, caption: str):
        super().__init__(objectName="tile")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(2)
        self.cap = QLabel(caption, objectName="tileCaption")
        self.val = QLabel("–", objectName="tileValue")
        self.sub = QLabel("", objectName="tileSub")
        self.sub.setTextFormat(Qt.RichText)
        self.sub.setWordWrap(True)  # long captions must not set the tile's minimum width
        self.cap.setWordWrap(True)
        for w in (self.cap, self.val, self.sub):
            lay.addWidget(w)

    def set(self, value: str, sub: str = ""):
        self.val.setText(value)
        self.sub.setText(sub)


# ----------------------------------------------------------------------
# DataFrame table model
# ----------------------------------------------------------------------
# Qt.<name> lookups cost microseconds each in PySide6 6.x and data() runs per cell, so bind once.
_DISPLAY, _USER = Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.UserRole
_ALIGN, _FOREGROUND = Qt.ItemDataRole.TextAlignmentRole, Qt.ItemDataRole.ForegroundRole
_HORIZONTAL = Qt.Orientation.Horizontal
_RIGHT = int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)


class FrameModel(QAbstractTableModel):
    """Read-only table over a DataFrame.

    Every cell's text, sort key and alignment is worked out once in set_frame(): Qt asks for
    data() hundreds of thousands of times (painting, sorting, resizeColumnsToContents), and a
    pandas lookup per call is what made the table tabs take seconds to open.
    """
    ID_ROLE = int(_USER) + 1

    def __init__(self, formats=None):
        super().__init__()
        self.df = pd.DataFrame()
        self.ids = []
        self.formats = formats or {}
        self.muted = None
        self.muted_color = QColor("#898781")
        self._n = 0
        self._cols, self._text, self._keys, self._numeric = [], [], [], []

    def _column(self, name, values):
        fmt = self.formats.get(name)
        if values.dtype.kind in "iuf":
            arr = values.to_numpy(dtype=float, na_value=np.nan)
            ok = np.isfinite(arr)
            f = fmt or "{:.0f}"
            text = [f.format(v) if k else "–" for v, k in zip(arr.tolist(), ok.tolist())]
            return text, np.where(ok, arr, -1e18).tolist(), True
        if values.dtype.kind == "M":
            f = fmt or "%Y-%m-%d %H:%M"
            text = [v.strftime(f) if v is not pd.NaT else "–" for v in values]
            return text, [v.value if v is not pd.NaT else -(2 ** 62) for v in values], False
        text, keys = [], []
        for v in values.tolist():
            if v is None or v is pd.NaT or (isinstance(v, float) and not np.isfinite(v)):
                text.append("–")
                keys.append("")
            else:
                text.append(str(v))
                keys.append(str(v))
        return text, keys, False

    def set_frame(self, df: pd.DataFrame, ids=None, muted=None):
        self.beginResetModel()
        self.df = df.reset_index(drop=True)
        self.ids = list(ids) if ids is not None else list(range(len(df)))
        self.muted = None if muted is None else [bool(m) for m in muted]
        self._n = len(self.df)
        self._cols = [str(c) for c in self.df.columns]
        self._text, self._keys, self._numeric = [], [], []
        for c in self.df.columns:
            text, keys, numeric = self._column(c, self.df[c])
            self._text.append(text)
            self._keys.append(keys)
            self._numeric.append(numeric)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else self._n

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._cols)

    def headerData(self, section, orientation, role=_DISPLAY):
        if role == _DISPLAY:
            return self._cols[section] if orientation == _HORIZONTAL else str(section + 1)
        return None

    def fit_columns(self, view: QTableView, max_width=360):
        """resizeColumnsToContents() without asking data() for every cell."""
        fm = view.fontMetrics()
        hfm = view.horizontalHeader().fontMetrics()
        for c, name in enumerate(self._cols):
            longest = sorted(set(self._text[c]), key=len)[-5:]
            w = max([fm.horizontalAdvance(t) for t in longest] + [0]) + 18
            view.setColumnWidth(c, min(max(w, hfm.horizontalAdvance(name) + 28), max_width))

    def data(self, index, role=_DISPLAY):
        if not index.isValid():
            return None
        r, c = index.row(), index.column()
        if role == _DISPLAY:
            return self._text[c][r]
        if role == _USER:  # sort key
            return self._keys[c][r]
        if role == self.ID_ROLE:
            return self.ids[r]
        if role == _ALIGN and self._numeric[c]:
            return _RIGHT
        if role == _FOREGROUND and self.muted is not None and self.muted[r]:
            return self.muted_color
        return None


def make_table(model: FrameModel) -> tuple[QTableView, QSortFilterProxyModel]:
    proxy = QSortFilterProxyModel()
    proxy.setSourceModel(model)
    proxy.setSortRole(Qt.UserRole)
    proxy.setFilterKeyColumn(-1)
    proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
    view = QTableView()
    view.setModel(proxy)
    view.setSortingEnabled(True)
    view.setSelectionBehavior(QAbstractItemView.SelectRows)
    view.setSelectionMode(QAbstractItemView.SingleSelection)
    view.setAlternatingRowColors(True)
    view.verticalHeader().setVisible(False)
    view.verticalHeader().setDefaultSectionSize(22)
    view.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
    view.horizontalHeader().setResizeContentsPrecision(200)  # size columns from a sample of rows
    view.setEditTriggers(QAbstractItemView.NoEditTriggers)
    return view, proxy


class FitPage(QWidget):
    """Holds a tab page inside its scroll area, so the page fits the window and only scrolls once the
    window is smaller than the page's minimum size. Given the page directly, the scroll area grows it
    to its preferred height whenever any word-wrapped label makes its layout height-for-width."""

    def __init__(self, page: QWidget):
        super().__init__()
        self.page = page
        page.setParent(self)
        page.installEventFilter(self)

    def eventFilter(self, obj, e):
        if e.type() == QEvent.LayoutRequest:  # the page's minimum size may have changed
            self.updateGeometry()
        return False

    def minimumSizeHint(self):
        return self.page.minimumSizeHint()

    def sizeHint(self):
        return self.page.minimumSizeHint()

    def resizeEvent(self, e):
        self.page.setGeometry(self.rect())
        super().resizeEvent(e)


# ----------------------------------------------------------------------
# Main window
# ----------------------------------------------------------------------
TIPS_TAB = 7  # index of the Tips tab
DATE_PRESETS = ["All time", "Last 7 days", "Last 30 days", "Last 90 days", "Last 365 days", "This year", "Custom"]


class MainWindow(QMainWindow):
    def __init__(self, path: Path | None, qs: QSettings | None = None):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        # 1500x950, or the whole screen when it's smaller (e.g. 1366x768), leaving room for the title bar
        avail = QGuiApplication.primaryScreen().availableGeometry()
        self.resize(min(1500, avail.width()), min(950, avail.height() - 40))
        self.qs = qs or QSettings("LandingRateInsights", "dashboard")
        self.settings = lra.Settings()
        self.raw = pd.DataFrame()
        self.all = pd.DataFrame()      # enriched, every entry
        self.view = pd.DataFrame()     # date/aircraft filtered, every entry
        self.sel = pd.DataFrame()      # ... and not excluded -> what the charts use
        self.ac_colors = {}
        self.path: Path | None = None
        self.lb_path: Path | None = None
        self.lb_raw = pd.DataFrame()   # logbook as read
        self.flights = pd.DataFrame()  # ... with reconciliation against the Lua log
        self.acf_map = {}
        self.theme_mode = self.qs.value("theme", "system")
        self.theme = DARK if (self.theme_mode == "dark" or
                              (self.theme_mode == "system" and system_is_dark())) else LIGHT
        apply_qt_theme(QApplication.instance(), self.theme)

        self.watcher = QFileSystemWatcher(self)
        self.watcher.fileChanged.connect(self._on_file_changed)
        self.reload_timer = QTimer(self, singleShot=True, interval=600, timeout=self._live_reload)
        self.refresh_timer = QTimer(self, singleShot=True, interval=120, timeout=self.refresh)
        self._dirty = set()  # tabs that need redrawing; drawn lazily when shown
        # ...or ahead of time: once the UI is idle, hidden dirty tabs are drawn one per tick so a
        # tab switch only has to show an already rendered page.
        self.prefetch_timer = QTimer(self, singleShot=True, interval=250, timeout=self._prefetch)

        self.update_info = None
        self.tip = None
        self._update_relay = _UpdateRelay(self)
        self._update_relay.done.connect(self._show_update)

        self._build_menu()
        self._build_ui()
        self.live_lbl = QLabel()
        self.statusBar().addPermanentWidget(self.live_lbl)
        self._load(path or self._default_path())
        if self.tip is None:  # no log loaded: show an unweighted tip
            self._new_tip()
        if self.qs.value("check_updates", True, type=bool):
            QTimer.singleShot(1500, lambda: self.check_updates(manual=False))

    # ---------------- setup ----------------
    def _default_path(self) -> Path | None:
        last = self.qs.value("last_path")
        for cand in (last, os.environ.get("LANDINGRATE_LOG"), APP_DIR / "LandingRate.log",
                     Path.cwd() / "LandingRate.log"):
            if cand and Path(cand).exists():
                return Path(cand)
        return None

    def _build_menu(self):
        m = self.menuBar().addMenu("&File")
        m.addAction("&Open log…", self.open_file, "Ctrl+O")
        m.addAction("Open &logbook…", self.open_logbook)
        m.addAction("&Reload", self.reload, "F5")
        m.addAction("&Export shown landings to CSV…", self.export_csv, "Ctrl+E")
        m.addSeparator()
        m.addAction("&Quit", self.close, "Ctrl+Q")
        v = self.menuBar().addMenu("&View")
        grp = QActionGroup(self)
        for mode in ("system", "light", "dark"):
            a = QAction(mode.capitalize(), self, checkable=True, checked=self.theme_mode == mode)
            a.triggered.connect(lambda _=False, md=mode: self.set_theme(md))
            grp.addAction(a)
            v.addAction(a)
        h = self.menuBar().addMenu("&Help")
        h.addAction("About the score", self._about_score)
        h.addSeparator()
        h.addAction("Check for &updates now", lambda: self.check_updates(manual=True))
        self.auto_update_act = QAction("Check for updates on start", self, checkable=True,
                                       checked=self.qs.value("check_updates", True, type=bool))
        self.auto_update_act.toggled.connect(lambda on: self.qs.setValue("check_updates", on))
        h.addAction(self.auto_update_act)
        h.addAction("&Releases on GitHub", lambda: QDesktopServices.openUrl(QUrl(updates.RELEASES_URL)))
        h.addSeparator()
        h.addAction(f"&About {APP_NAME}", self._about)

    def _build_ui(self):
        split = QSplitter()
        split.addWidget(self._build_sidebar())
        self.tabs = QTabWidget(documentMode=True)
        for page, name in [(self._build_overview(), "Overview"), (self._build_progress(), "Progress"),
                           (self._build_aircraft(), "Aircraft"), (self._build_technique(), "Technique"),
                           (self._build_sessions(), "Sessions"), (self._build_landings(), "Landings"),
                           (self._build_logbook(), "Logbook"), (self._build_tips(), "Tips")]:
            # On a small screen a page scrolls instead of squashing its charts below their minimum size.
            scroll = QScrollArea(widgetResizable=True, frameShape=QFrame.NoFrame)
            scroll.setWidget(FitPage(page))
            self.tabs.addTab(scroll, name)
        self.tabs.currentChanged.connect(lambda _: self._draw_current())
        split.addWidget(self.tabs)
        split.setStretchFactor(1, 1)
        split.setSizes([split.widget(0).minimumWidth(), 1220])
        self.setCentralWidget(split)
        # Redrawing every chart on each pixel of a drag is what makes it stutter: show a
        # guide line while dragging and lay out once on release. Never collapse a pane.
        for s in self.findChildren(QSplitter):
            s.setOpaqueResize(False)
            s.setChildrenCollapsible(False)
        for c in self.findChildren(Chart):
            c.landingClicked.connect(self.show_landing)

    def _build_sidebar(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 4, 8)
        lay.addWidget(self._build_brand())

        g = QGroupBox("Tip")
        gl = QVBoxLayout(g)
        self.tip_lbl = QLabel(wordWrap=True, textFormat=Qt.RichText)
        gl.addWidget(self.tip_lbl)
        row = QHBoxLayout()
        row.addWidget(QPushButton("Another tip", clicked=self._new_tip))
        row.addWidget(QPushButton("All tips", clicked=lambda: self.tabs.setCurrentIndex(TIPS_TAB)))
        gl.addLayout(row)
        lay.addWidget(g)

        g = QGroupBox("Log file")
        gl = QVBoxLayout(g)
        self.path_lbl = QLabel("–", wordWrap=True)
        gl.addWidget(self.path_lbl)
        self.lb_lbl = QLabel("Logbook: –", wordWrap=True)
        gl.addWidget(self.lb_lbl)
        row = QHBoxLayout()
        row.addWidget(QPushButton("Open…", clicked=self.open_file))
        row.addWidget(QPushButton("Reload", clicked=self.reload))
        gl.addLayout(row)
        self.live_cb = QCheckBox("Live: refresh when a landing is logged",
                                 checked=self.qs.value("live", True, type=bool))
        self.live_cb.toggled.connect(self._update_watch)
        gl.addWidget(self.live_cb)
        lay.addWidget(g)

        g = QGroupBox("Date range")
        gl = QFormLayout(g)
        self.preset = QComboBox()
        self.preset.addItems(DATE_PRESETS)
        self.preset.currentTextChanged.connect(self._on_preset)
        self.date_from, self.date_to = QDateEdit(calendarPopup=True), QDateEdit(calendarPopup=True)
        for d in (self.date_from, self.date_to):
            d.setDisplayFormat("yyyy-MM-dd")
            d.dateChanged.connect(self._on_custom_date)
        gl.addRow(self.preset)
        gl.addRow("From", self.date_from)
        gl.addRow("To", self.date_to)
        lay.addWidget(g)

        g = QGroupBox("Aircraft")
        gl = QVBoxLayout(g)
        self.class_combo = QComboBox()
        self.class_combo.addItem("All classes")
        self.class_combo.addItems(lra.CLASS_ORDER)
        self.class_combo.currentTextChanged.connect(self._fill_aircraft_list)
        gl.addWidget(self.class_combo)
        self.ac_list = QListWidget()
        self.ac_list.itemChanged.connect(lambda _: self.schedule_refresh())
        gl.addWidget(self.ac_list, 1)
        row = QHBoxLayout()
        row.addWidget(QPushButton("All", clicked=lambda: self._check_all(True)))
        row.addWidget(QPushButton("None", clicked=lambda: self._check_all(False)))
        gl.addLayout(row)
        lay.addWidget(g, 1)

        g = QGroupBox("Exclude from analysis")
        gl = QVBoxLayout(g)
        self.flag_cbs = {}
        for col, label in lra.FLAG_LABELS.items():
            cb = QCheckBox(label, checked=True)
            cb.toggled.connect(lambda _: self.schedule_refresh())
            self.flag_cbs[col] = cb
            gl.addWidget(cb)
        lay.addWidget(g)

        g = QGroupBox("Tuning")
        gl = QFormLayout(g)
        s = self.settings
        self.crash_sb = self._spin(gl, "Crash below (fpm)", -5000, -300, s.crash_vs, 50)
        self.retouch_sb = self._spin(gl, "Re-touch window (s)", 0, 600, s.retouch_window_s, 5)
        self.session_sb = self._spin(gl, "Session gap (min)", 10, 1440, s.session_gap_min, 10)
        self.roll_sb = QSpinBox(minimum=3, maximum=100, value=15)
        self.roll_sb.valueChanged.connect(lambda _: self.schedule_refresh())
        gl.addRow("Rolling window", self.roll_sb)
        for sb in (self.crash_sb, self.retouch_sb, self.session_sb):
            sb.valueChanged.connect(self._on_tuning)
        lay.addWidget(g)

        scroll = QScrollArea(widgetResizable=True, frameShape=QFrame.NoFrame)
        scroll.setWidget(w)
        # Never scroll sideways: be at least as wide as the contents plus the vertical scrollbar.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(max(260, w.minimumSizeHint().width() + scroll.verticalScrollBar().sizeHint().width()))
        return scroll

    @staticmethod
    def _spin(form, label, lo, hi, val, step):
        sb = QDoubleSpinBox(minimum=lo, maximum=hi, value=val, singleStep=step, decimals=0)
        form.addRow(label, sb)
        return sb

    def _build_brand(self) -> QWidget:
        """Logo, name, version and the quiet update note (like MailTool's)."""
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(4, 0, 0, 6)
        logo = QLabel()
        pm = asset_pixmap("icon_128.png")
        if not pm.isNull():
            dpr = self.devicePixelRatioF()
            pm = pm.scaled(int(40 * dpr), int(40 * dpr), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            pm.setDevicePixelRatio(dpr)
            logo.setPixmap(pm)
        row.addWidget(logo)
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(QLabel(APP_NAME, objectName="brandName"))
        ver = QHBoxLayout()
        ver.setSpacing(0)
        ver.addWidget(QLabel(f"v{__version__}", objectName="brandVersion"))
        self.update_lbl = QLabel(objectName="brandVersion")
        self.update_lbl.setTextFormat(Qt.RichText)
        self.update_lbl.setOpenExternalLinks(True)
        ver.addWidget(self.update_lbl)
        ver.addStretch(1)
        col.addLayout(ver)
        row.addLayout(col, 1)
        return w

    # ---------------- updates ----------------
    def check_updates(self, manual: bool):
        if manual:
            self.update_lbl.setText("&nbsp;·&nbsp;checking…")
        relay = self._update_relay
        updates.check_async(lambda info: relay.done.emit(info, manual))

    def _show_update(self, info: dict, manual: bool):
        self.update_info = info
        if info["status"] == "available":
            self.update_lbl.setText(f"&nbsp;·&nbsp;<a href='{info['url']}' style='color:{self.theme.notice_text}; "
                                    f"text-decoration:none'>v{info['latest']} available</a>")
            self.update_lbl.setToolTip(f"Open the v{info['latest']} release page")
            self.statusBar().showMessage(f"{APP_NAME} v{info['latest']} is available (you have "
                                         f"v{info['current']}). Help ▸ Releases on GitHub", 15000)
            if manual:
                if QMessageBox.question(self, "Update available",
                                        f"Version {info['latest']} is available. You have {info['current']}."
                                        "\n\nOpen the release page?") == QMessageBox.Yes:
                    QDesktopServices.openUrl(QUrl(info["url"]))
        elif info["status"] == "current":
            self.update_lbl.setText("&nbsp;·&nbsp;up to date")
            self.update_lbl.setToolTip("")
            if manual:
                QMessageBox.information(self, "No updates", f"You have the latest version ({info['current']}).")
        else:
            self.update_lbl.setText("")
            if manual:
                QMessageBox.warning(self, "Update check failed",
                                    "Couldn't get the latest release from GitHub (offline, or no release "
                                    f"published yet).\n\nReleases: {updates.RELEASES_URL}")

    def _about(self):
        box = QMessageBox(self)
        box.setWindowTitle(f"About {APP_NAME}")
        pm = asset_pixmap("icon_128.png")
        if not pm.isNull():
            box.setIconPixmap(pm.scaled(96, 96, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        note = ""
        if self.update_info and self.update_info["status"] == "available":
            note = (f"<p><a href='{self.update_info['url']}'>Version {self.update_info['latest']} "
                    f"is available</a></p>")
        box.setTextFormat(Qt.RichText)
        box.setText(f"<h3 style='margin-bottom:2px'>{APP_NAME}</h3><p>Version {__version__}</p>{note}"
                    "<p>Dashboard and report for the landing log written by the LandingRate.lua "
                    "FlyWithLua script in X-Plane.</p>"
                    f"<p><a href='https://github.com/{updates.REPO}'>github.com/{updates.REPO}</a></p>")
        box.exec()

    def _build_overview(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        tiles = QGridLayout()
        self.tiles = {}
        for i, (key, cap) in enumerate([("n", "Landings"), ("median", "Median VS"), ("great", "GREAT or better"),
                                        ("hard", "Harder than −350"), ("score", "Avg score"),
                                        ("streak", "Current streak"), ("last", "Latest landing")]):
            self.tiles[key] = Tile(cap)
            tiles.addWidget(self.tiles[key], 0, i)
        lay.addLayout(tiles)
        split = QSplitter()
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.c_form = Chart(2.6)
        ll.addWidget(panel("Recent form", "Last 50 landings, oldest → newest. Line = rolling median. "
                                          "Click a dot to open it.", self.c_form), 3)
        row = QHBoxLayout()
        self.c_hist = Chart(2.6)
        self.c_mix = Chart(2.6)
        row.addWidget(panel("Vertical speed distribution", "25 fpm bins, coloured by Lua rating", self.c_hist), 3)
        row.addWidget(panel("Rating mix", "", self.c_mix), 2)
        ll.addLayout(row, 3)
        split.addWidget(left)
        self.insights_view = QTextBrowser(openExternalLinks=False)
        split.addWidget(panel("Insights", "Generated from the landings currently shown", self.insights_view))
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([800, 420])
        lay.addWidget(split, 1)
        return w

    def _build_progress(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        top = QHBoxLayout()
        top.addWidget(QLabel("X axis:"))
        self.prog_x = QComboBox()
        self.prog_x.addItems(["Landing number", "Date"])
        self.prog_x.currentTextChanged.connect(lambda _: self._redraw(1))
        top.addWidget(self.prog_x)
        top.addStretch(1)
        lay.addLayout(top)
        self.c_hist_trend = Chart(3.0)
        lay.addWidget(panel("Vertical speed over time", "Dots = landings · line = rolling median · "
                                                        "band = rolling middle 50%",
                            self.c_hist_trend, toolbar_for=self.c_hist_trend), 4)
        row = QHBoxLayout()
        self.c_learn = Chart(2.8)
        self.c_month = Chart(2.8)
        row.addWidget(panel("Learning curve by type", "Rolling median VS vs. your nth landing in that "
                                                      "aircraft (types with 15+ landings)",
                            self.c_learn, toolbar_for=self.c_learn), 3)
        row.addWidget(panel("Monthly", "Landings flown and median VS per month", self.c_month), 2)
        lay.addLayout(row, 3)
        return w

    def _build_aircraft(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        fmt = {"Median G": "{:.2f}", "Trend /10 ldg": "{:+.0f}", "Median float s": "{:.1f}",
               "Last flown": "%Y-%m-%d"}
        self.card_model = FrameModel(fmt)
        self.card_view, _ = make_table(self.card_model)
        self.card_view.doubleClicked.connect(self._card_to_technique)
        lay.addWidget(panel("Scorecard", "Clean landings per type. Trend = fpm change per 10 landings "
                                         "(positive = getting softer; needs 15+). Double-click a row to see "
                                         "its technique.", self.card_view), 2)
        self.c_box = Chart(3.2)
        lay.addWidget(panel("Vertical speed spread per type", "Box = middle 50%, whiskers = 10th–90th "
                                                              "percentile, | = median (types with 5+ landings)",
                            self.c_box), 3)
        return w

    def _build_technique(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        top = QHBoxLayout()
        top.addWidget(QLabel("Focus aircraft:"))
        self.focus = QComboBox(minimumContentsLength=10)
        self.focus.currentTextChanged.connect(lambda _: self._redraw(3))
        top.addWidget(self.focus)
        top.addStretch(1)
        lay.addLayout(top)
        self.c_tech = Chart(6.0)
        lay.addWidget(panel("What drives your vertical speed",
                            "Each dot is a landing. Line = median VS per bin. ρ = Spearman rank correlation "
                            "(±0.3 or more is a real relationship).", self.c_tech,
                            toolbar_for=self.c_tech), 1)
        return w

    def _build_sessions(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        top = QSplitter()
        self.sess_model = FrameModel({"Duration min": "{:.0f}", "Start": "%Y-%m-%d %H:%M"})
        self.sess_view, self.sess_proxy = make_table(self.sess_model)
        self.sess_view.selectionModel().currentRowChanged.connect(lambda *_: self._draw_session())
        top.addWidget(panel("Sessions", "A new session starts after the gap set in Tuning", self.sess_view))
        self.c_session = Chart(2.8)
        top.addWidget(panel("Selected session", "Each landing in order", self.c_session))
        top.setSizes([700, 600])
        lay.addWidget(top, 1)
        self.c_habits = Chart(2.6)
        lay.addWidget(panel("Habits", "Median VS compared with your overall median: up = softer than usual, "
                                      "down = firmer. Bars with fewer than 5 landings are faded.",
                            self.c_habits), 1)
        return w

    def _build_landings(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.search = QLineEdit(placeholderText="Search (aircraft, airport, rating, flare, flag, date…)")
        row.addWidget(self.search, 1)
        self.show_excl = QCheckBox("Show excluded entries", checked=True)
        self.show_excl.toggled.connect(lambda _: self._fill_landings())
        row.addWidget(self.show_excl)
        row.addWidget(QPushButton("Export CSV…", clicked=self.export_csv))
        ll.addLayout(row)
        self.land_model = FrameModel({"G": "{:.2f}", "Float s": "{:.1f}", "Pitch q": "{:+.2f}",
                                      "Nose °/s": "{:+.2f}", "Time": "%Y-%m-%d %H:%M:%S"})
        self.land_view, self.land_proxy = make_table(self.land_model)
        self.search.textChanged.connect(self.land_proxy.setFilterFixedString)
        self.land_view.selectionModel().currentRowChanged.connect(self._on_landing_row)
        ll.addWidget(self.land_view, 1)
        self.excl_lbl = QLabel(wordWrap=True)
        ll.addWidget(self.excl_lbl)
        split = QSplitter()
        split.addWidget(left)
        self.detail = QTextBrowser()
        split.addWidget(panel("Landing detail", "", self.detail))
        split.setSizes([950, 450])
        lay.addWidget(split)
        return w

    # ---------------- loading ----------------
    def open_file(self):
        start = str(self.path.parent if self.path else APP_DIR)
        fn, _ = QFileDialog.getOpenFileName(self, "Open landing log", start, "Logs (*.log *.csv *.txt *.bak);;All (*)")
        if fn:
            self._load(Path(fn))

    def reload(self):
        if self.path:
            self._load(self.path, keep_filters=True)

    def _load(self, path: Path | None, keep_filters=False):
        if path is None:
            self.path_lbl.setText("No log found – use Open…")
            return
        try:
            raw, skipped = lra.load_log(path)
        except Exception as e:  # noqa: BLE001 - show any read/parse problem to the user
            QMessageBox.critical(self, "Could not read log", f"{path}\n\n{e}")
            return
        self.path = path = path.resolve()
        self.qs.setValue("last_path", str(path))
        self.raw = raw
        self.skipped = skipped
        self.path_lbl.setText(f"{path.name}\n{len(raw)} entries"
                              + (f", {skipped} unreadable lines" if skipped else ""))
        self.path_lbl.setToolTip(str(path))
        self._load_logbook()
        self._update_watch()
        self._enrich()
        if not keep_filters:
            self._init_filters()
        self.refresh()

    def _enrich(self):
        self.all = lra.enrich(self.raw, self.settings)
        self.all, self.flights, self.acf_map = lra.match_logbook(self.all, self.lb_raw)
        clean = self.all[self.all["clean"]]
        top = clean["aircraft"].value_counts().index[:len(self.theme.series)]
        self.ac_slots = {ac: i for i, ac in enumerate(top)}

    def ac_color(self, ac):
        i = self.ac_slots.get(ac)
        return self.theme.series[i] if i is not None else self.theme.muted

    def _init_filters(self):
        if self.all.empty:
            return
        self.date_from.blockSignals(True)
        self.date_to.blockSignals(True)
        d0, d1 = self.all["time"].min(), self.all["time"].max()
        self.date_from.setDate(QDate(d0.year, d0.month, d0.day))
        self.date_to.setDate(QDate(d1.year, d1.month, d1.day))
        self.date_from.blockSignals(False)
        self.date_to.blockSignals(False)
        self.preset.setCurrentText("All time")
        self._fill_aircraft_list()

    def _fill_aircraft_list(self, *_):
        prev = {self.ac_list.item(i).data(Qt.UserRole): self.ac_list.item(i).checkState()
                for i in range(self.ac_list.count())}
        cls = self.class_combo.currentText()
        counts = self.all["aircraft"].value_counts()
        self.ac_list.blockSignals(True)
        self.ac_list.clear()
        for ac, n in counts.items():
            acls = lra.aircraft_class(ac) if ac else "Other"
            if cls != "All classes" and acls != cls:
                continue
            it = QListWidgetItem(f"{ac or '(none)'}   {n}")
            it.setData(Qt.UserRole, ac)
            it.setToolTip(acls)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(prev.get(ac, Qt.Checked))
            self.ac_list.addItem(it)
        self.ac_list.blockSignals(False)
        self.schedule_refresh()

    def _check_all(self, on: bool):
        self.ac_list.blockSignals(True)
        for i in range(self.ac_list.count()):
            self.ac_list.item(i).setCheckState(Qt.Checked if on else Qt.Unchecked)
        self.ac_list.blockSignals(False)
        self.schedule_refresh()

    def _on_preset(self, text):
        if self.all.empty or text == "Custom":
            return
        end = self.all["time"].max().normalize()
        days = {"Last 7 days": 7, "Last 30 days": 30, "Last 90 days": 90, "Last 365 days": 365}
        if text == "All time":
            start = self.all["time"].min()
        elif text == "This year":
            start = pd.Timestamp(year=pd.Timestamp.now().year, month=1, day=1)
        else:
            start = pd.Timestamp.now().normalize() - pd.Timedelta(days=days[text])
            end = max(end, pd.Timestamp.now().normalize())
        for de, d in ((self.date_from, start), (self.date_to, end)):
            de.blockSignals(True)
            de.setDate(QDate(d.year, d.month, d.day))
            de.blockSignals(False)
        self.schedule_refresh()

    def _on_custom_date(self, *_):
        self.preset.blockSignals(True)
        self.preset.setCurrentText("Custom")
        self.preset.blockSignals(False)
        self.schedule_refresh()

    def _on_tuning(self, *_):
        self.settings = lra.Settings(crash_vs=self.crash_sb.value(), retouch_window_s=self.retouch_sb.value(),
                                     session_gap_min=self.session_sb.value())
        self._enrich()
        self.schedule_refresh()

    # ---------------- live mode ----------------
    def _update_watch(self, *_):
        self.qs.setValue("live", self.live_cb.isChecked())
        if self.watcher.files():
            self.watcher.removePaths(self.watcher.files())
        if self.live_cb.isChecked():
            for p in (self.path, self.lb_path):
                if p and p.exists():
                    self.watcher.addPath(str(p))

    def _on_file_changed(self, _):
        self.reload_timer.start()

    def _live_reload(self):
        if not self.path or not self.path.exists():
            return
        before = len(self.raw)
        # keep date range open-ended so the new landing is in view
        follow = self.date_to.date() >= QDate(*self.all["time"].max().timetuple()[:3]) if len(self.all) else True
        self._load(self.path, keep_filters=True)
        if follow and len(self.all):
            d = self.all["time"].max()
            self.date_to.blockSignals(True)
            self.date_to.setDate(QDate(d.year, d.month, d.day))
            self.date_to.blockSignals(False)
            self._fill_aircraft_list()
        if len(self.raw) > before:
            r = self.all.iloc[-1]
            pct = self._pct_in_type(r)
            msg = (f"New landing: {r['aircraft']} {r['vs']:+.0f} fpm · {r['rating']} · score {r['score']:.0f}"
                   + (f" · better than {pct:.0f}% of your {r['aircraft']} landings" if pct == pct else ""))
            self.live_lbl.setText(f"{r['time']:%H:%M}  {msg}")
            self._new_tip()
            self.tabs.setCurrentIndex(0)

    # ---------------- filtering ----------------
    def schedule_refresh(self):
        self.refresh_timer.start()

    def refresh(self):
        if self.all.empty:
            return
        df = self.all
        d0 = pd.Timestamp(self.date_from.date().toPython())
        d1 = pd.Timestamp(self.date_to.date().toPython()) + pd.Timedelta(days=1)
        df = df[(df["time"] >= d0) & (df["time"] < d1)]
        chosen = {self.ac_list.item(i).data(Qt.UserRole) for i in range(self.ac_list.count())
                  if self.ac_list.item(i).checkState() == Qt.Checked}
        df = df[df["aircraft"].isin(chosen)]
        self.view = df
        excl = [c for c, cb in self.flag_cbs.items() if cb.isChecked()]
        self.sel = df[~df[excl].any(axis=1)] if excl else df
        n_excl = len(df) - len(self.sel)
        self.statusBar().showMessage(f"{len(self.sel)} landings analysed · {n_excl} excluded · "
                                     f"{len(self.all)} entries in log")
        self._fill_focus()
        if self.tip is None:
            self._new_tip()
        self._dirty = set(range(self.tabs.count()))
        self._draw_current()

    def _draw_tab(self, i: int):
        self._dirty.discard(i)
        [self.draw_overview, self.draw_progress, self.draw_aircraft, self.draw_technique,
         self.draw_sessions, self._fill_landings, self.draw_logbook, self.draw_tips][i]()

    def _draw_current(self):
        if self.all.empty:
            return
        i = self.tabs.currentIndex()
        if i in self._dirty:
            self._draw_tab(i)
        if self._dirty:
            self.prefetch_timer.start()

    def _prefetch(self):
        """Draw one hidden tab at its real size, then yield to the event loop."""
        if self.all.empty or not self._dirty:
            return
        if QApplication.mouseButtons() != Qt.NoButton or QApplication.activePopupWidget():
            self.prefetch_timer.start()  # user is busy (dragging, menu open): try again later
            return
        cur = self.tabs.currentIndex()
        i = min(self._dirty, key=lambda k: (abs(k - cur), k))  # neighbours first
        page = self.tabs.widget(i)
        page.setGeometry(self.tabs.currentWidget().geometry())
        self._draw_tab(i)
        # Painting a hidden page delivers its pending resize events first, so splitters and
        # labels settle and each chart renders once at its final size; showing it is then a blit.
        page.grab()
        if self._dirty:
            self.prefetch_timer.start()

    def _redraw(self, tab: int):
        self._dirty.add(tab)
        self._draw_current()

    def set_theme(self, mode):
        self.theme_mode = mode
        self.qs.setValue("theme", mode)
        self.theme = DARK if (mode == "dark" or (mode == "system" and system_is_dark())) else LIGHT
        apply_qt_theme(QApplication.instance(), self.theme)
        for m in (self.card_model, self.sess_model, self.land_model, self.fl_model):
            m.muted_color = QColor(self.theme.muted)
        if self.update_info:
            self._show_update(self.update_info, False)
        self._show_tip()
        self.refresh()

    # ---------------- Overview ----------------
    def draw_overview(self):
        t, df = self.theme, self.sel.sort_values("time")
        s = lra.summary(df)
        tl = self.tiles
        if not s["n"]:
            for k in tl:
                tl[k].set("–")
            for c in (self.c_form, self.c_hist, self.c_mix):
                c.reset(t)
                c.draw_idle()
            self.insights_view.setHtml(self._insights_html([lra.Insight("info", "No landings",
                                                                         "Nothing matches the filters.")]))
            return
        prior = df.iloc[:-20] if len(df) >= 40 else None

        def delta(now, before, better_if_higher=True, unit=""):
            if prior is None or not np.isfinite(before):
                return ""
            d = now - before
            if abs(d) < 0.5:
                return "same as before last 20"
            good = (d > 0) == better_if_higher
            arrow = "▲" if d > 0 else "▼"
            col = t.good_text if good else t.critical
            return f"<span style='color:{col}'>{arrow} {abs(d):.0f}{unit}</span> last 20 vs before"

        last20 = df.tail(20)
        tl["n"].set(f"{s['n']}", f"{df['aircraft'].nunique()} types · {df['session'].nunique()} sessions")
        tl["median"].set(f"{s['median_vs']:+.0f}",
                         delta(last20["vs"].median(), prior["vs"].median() if prior is not None else np.nan,
                               True, " fpm") or "fpm")
        tl["great"].set(f"{s['pct_great']:.0f}%",
                        delta((last20["vs"] >= -250).mean() * 100,
                              (prior["vs"] >= -250).mean() * 100 if prior is not None else np.nan, True, " pts")
                        or "softer than −250 fpm")
        tl["hard"].set(f"{s['pct_hard']:.0f}%",
                       delta((last20["vs"] < -350).mean() * 100,
                             (prior["vs"] < -350).mean() * 100 if prior is not None else np.nan, False, " pts")
                       or "HARD or WASTED")
        tl["score"].set(f"{s['mean_score']:.0f}",
                        delta(last20["score"].mean(), prior["score"].mean() if prior is not None else np.nan,
                              True) or "out of 100 (Help ▸ About)")
        tl["streak"].set(f"{s['current_streak']}", f"GREAT or better · best {s['best_streak']}")
        r = df.iloc[-1]
        pct = self._pct_in_type(r)
        tl["last"].set(f"{r['vs']:+.0f}",
                       f"{r['aircraft']} · {r['rating']} · {r['time']:%d %b %H:%M}"
                       + (f"<br>beats {pct:.0f}% in type" if pct == pct else ""))

        # Recent form
        c = self.c_form
        c.reset(t)
        ax = c.fig.add_subplot()
        style_axes(ax, t)
        rec = df.tail(50)
        x = np.arange(len(rec))
        target_band(ax, t)
        roll = df["vs"].rolling(self.roll_sb.value(), min_periods=3).median().tail(50)
        ax.plot(x, roll.to_numpy(), color=t.ink2, lw=2, zorder=3, solid_capstyle="round")
        cols = [t.rating[str(v)] for v in rec["rating"]]
        ax.scatter(x, rec["vs"], c=cols, s=42, edgecolors=t.surface, linewidths=1.6, zorder=4)
        c.hover_points(ax, x, rec["vs"], [landing_tip(r) for _, r in rec.iterrows()], rec.index)
        ax.text(x[-1] + 0.6, roll.iloc[-1], f"median {roll.iloc[-1]:+.0f}", color=t.ink2, fontsize=8, va="center")
        ticks = np.linspace(0, len(rec) - 1, min(6, len(rec))).round().astype(int)
        ax.set_xticks(ticks, [f"{rec['time'].iloc[i]:%d %b}" for i in ticks])
        ax.set_xlim(-1, len(rec) + 6)
        vs_ylim(ax, rec["vs"])
        ax.set_ylabel("fpm")
        c.draw_idle()

        # Histogram
        c = self.c_hist
        c.reset(t)
        ax = c.fig.add_subplot()
        style_axes(ax, t)
        lo = max(-1000, np.floor(df["vs"].quantile(0.005) / 25) * 25)
        edges = np.arange(lo, 25, 25)
        counts, _ = np.histogram(df["vs"].clip(lower=lo), edges)
        centers = edges[:-1] + 12.5
        bars = ax.bar(centers, counts, width=22, color=[t.rating[lra.rating_for(v)] for v in centers],
                      edgecolor="none")
        for b, lo_, n in zip(bars, edges[:-1], counts):
            c.hover_patch(ax, b, f"{lo_:+.0f} to {lo_ + 25:+.0f} fpm\n{n} landings ({n / len(df) * 100:.1f}%)")
        med = df["vs"].median()
        ax.axvline(med, color=t.ink, lw=1)
        ax.text(med, ax.get_ylim()[1], f" median {med:+.0f}", color=t.ink, fontsize=8, va="top")
        ax.set_xlabel("Vertical speed at touchdown (fpm)")
        ax.yaxis.set_major_locator(MaxNLocator(integer=True))
        c.draw_idle()

        # Rating mix
        c = self.c_mix
        c.reset(t)
        ax = c.fig.add_subplot()
        style_axes(ax, t, grid=None)
        vc = df["rating"].value_counts().reindex(lra.RATING_ORDER, fill_value=0)
        y = np.arange(len(vc))[::-1]
        bars = ax.barh(y, vc.to_numpy(), height=0.62, color=[t.rating[k] for k in vc.index])
        for b, (k, n) in zip(bars, vc.items()):
            ax.text(b.get_width(), b.get_y() + b.get_height() / 2, f"  {n / len(df) * 100:.0f}%  ({n})",
                    va="center", fontsize=8.5, color=t.ink2)
            c.hover_patch(ax, b, f"{k}: {n} landings")
        bands = {"BUTTER": "0 to −125", "GREAT": "−125 to −250", "ACCEPTABLE": "−250 to −350",
                 "HARD": "−350 to −600", "WASTED": "< −600"}
        ax.set_yticks(y, [f"{k}\n{bands[k]}" for k in vc.index], fontsize=8)
        ax.set_xlim(0, vc.max() * 1.7 + 1)
        ax.set_xticks([])
        ax.spines["bottom"].set_visible(False)
        c.draw_idle()

        self.insights_view.setHtml(self._insights_html(lra.insights(df, self.view, flights=self.flights)))

    def _insights_html(self, items) -> str:
        t = self.theme
        icon = {"good": ("▲", t.good_text, "Good"), "warn": ("⚠", t.serious if t.name == "dark" else "#b5502a",
                                                                "Watch"), "info": ("●", t.series[0], "Note")}
        parts = [f"<body style='color:{t.ink}; background:{t.surface}'>"]
        for i in items:
            ic, col, word = icon[i.kind]
            parts.append(f"<p style='margin:0 0 2px 0'><span style='color:{col}'>{ic} <b>{word}</b></span>"
                         f" &nbsp;<b>{i.title}</b></p>"
                         f"<p style='margin:0 0 12px 0; color:{t.ink2}'>{i.text}</p>")
        return "".join(parts) + "</body>"

    def _pct_in_type(self, r) -> float:
        ref = self.all[self.all["clean"] & (self.all["aircraft"] == r["aircraft"]) & (self.all.index != r.name)]
        return lra.percentile_in(r["vs"], ref["vs"]) if len(ref) >= 5 else float("nan")

    # ---------------- Progress ----------------
    def draw_progress(self):
        t, df = self.theme, self.sel.sort_values("time")
        w = self.roll_sb.value()
        by_date = self.prog_x.currentText() == "Date"
        c = self.c_hist_trend
        c.reset(t)
        ax = c.fig.add_subplot()
        style_axes(ax, t)
        if len(df):
            x = mdates.date2num(df["time"]) if by_date else np.arange(1, len(df) + 1)
            target_band(ax, t)
            ax.scatter(x, df["vs"], s=12, color=t.muted, alpha=0.55, lw=0, zorder=2)
            roll = df["vs"].rolling(w, min_periods=max(3, w // 3))
            q1, q3, med = roll.quantile(0.25), roll.quantile(0.75), roll.median()
            ax.fill_between(x, q1, q3, color=t.series[0], alpha=0.16, lw=0, zorder=1)
            ax.plot(x, med, color=t.series[0], lw=2, zorder=3)
            c.hover_points(ax, x, df["vs"], [landing_tip(r) for _, r in df.iterrows()], df.index)
            ax.text(x[-1], med.iloc[-1], f"  {med.iloc[-1]:+.0f}", color=t.ink, fontsize=8.5, va="center")
            vs_ylim(ax, df["vs"])
            ax.set_ylabel("fpm")
            if by_date:
                loc = mdates.AutoDateLocator()
                ax.xaxis.set_major_locator(loc)
                ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(loc))
            else:
                ax.set_xlabel("Landing #")
        c.draw_idle()

        # Learning curves
        c = self.c_learn
        c.reset(t)
        ax = c.fig.add_subplot()
        style_axes(ax, t)
        target_band(ax, t, label=False)
        counts = df["aircraft"].value_counts()
        types = [a for a in counts.index if counts[a] >= 15 and a in self.ac_slots][:7]
        for ac in types:
            g = df[df["aircraft"] == ac].sort_values("time")
            x = g["type_idx"].to_numpy()
            if np.isnan(x).all():
                x = np.arange(1, len(g) + 1)
            y = g["vs"].rolling(w, min_periods=max(3, w // 3)).median()
            ax.plot(x, y, color=self.ac_color(ac), lw=2, label=ac)
            if y.notna().any():
                ax.text(x[-1], y.iloc[-1], f" {ac}", color=t.ink2, fontsize=8, va="center")
            c.hover_points(ax, x, y, [f"{ac} · landing {int(i)} in type\nrolling median {v:+.0f} fpm"
                                      if v == v else "" for i, v in zip(x, y)])
        if types:
            ax.legend(frameon=False, fontsize=8, labelcolor=t.ink2, ncols=min(4, len(types)), loc="lower right")
        ax.set_xlabel("Landing # in type")
        ax.set_ylabel("fpm")
        c.draw_idle()

        # Monthly volume + median (two panels, shared x)
        c = self.c_month
        c.reset(t)
        a1, a2 = c.fig.subplots(2, 1, sharex=True, height_ratios=[1, 1.3])
        for a in (a1, a2):
            style_axes(a, t)
        if len(df):
            m = df.set_index("time").resample("MS")["vs"].agg(["count", "median"])
            x = mdates.date2num(m.index)
            bars = a1.bar(x, m["count"], width=24, color=t.series[0])
            for b, (ts, r) in zip(bars, m.iterrows()):
                c.hover_patch(a1, b, f"{ts:%b %Y}: {int(r['count'])} landings")
            a1.set_ylabel("landings")
            mm = m[m["count"] >= 3]
            a2.plot(mdates.date2num(mm.index), mm["median"], color=t.ink2, lw=1.5, marker="o", ms=5,
                    mec=t.surface, mew=1.5)
            target_band(a2, t, label=False)
            c.hover_points(a2, mdates.date2num(mm.index), mm["median"],
                           [f"{ts:%b %Y}: median {r['median']:+.0f} fpm ({int(r['count'])} landings)"
                            for ts, r in mm.iterrows()])
            a2.set_ylabel("median fpm")
            loc = mdates.AutoDateLocator(maxticks=7)
            a2.xaxis.set_major_locator(loc)
            a2.xaxis.set_major_formatter(mdates.ConciseDateFormatter(loc))
        c.draw_idle()

    # ---------------- Aircraft ----------------
    def draw_aircraft(self):
        t, df = self.theme, self.sel
        card = lra.aircraft_scorecard(df)
        if not card.empty:
            card.loc[card["Landings"] < 15, "Trend /10 ldg"] = np.nan
        self.card_model.set_frame(card)
        self.card_view.sortByColumn(2, Qt.DescendingOrder)
        self.card_model.fit_columns(self.card_view)

        c = self.c_box
        c.reset(t)
        ax = c.fig.add_subplot()
        style_axes(ax, t, grid="x")
        groups = [(ac, g["vs"]) for ac, g in df.groupby("aircraft") if len(g) >= 5]
        groups.sort(key=lambda p: p[1].median())
        if groups:
            ax.axvspan(*TARGET, color=t.good, alpha=0.07, lw=0)
            bp = ax.boxplot([g for _, g in groups], orientation="horizontal", whis=(10, 90), showfliers=False,
                            patch_artist=True, widths=0.55, medianprops=dict(color=t.ink, lw=1.5),
                            whiskerprops=dict(color=t.axis), capprops=dict(color=t.axis))
            for patch, (ac, g) in zip(bp["boxes"], groups):
                patch.set_facecolor(self.ac_color(ac))
                patch.set_alpha(0.85)
                patch.set_edgecolor(t.surface)
                c.hover_patch(ax, patch, f"{ac} · {len(g)} landings\nmedian {g.median():+.0f} fpm\n"
                                         f"middle 50%: {g.quantile(.25):+.0f} to {g.quantile(.75):+.0f}\n"
                                         f"10th–90th: {g.quantile(.1):+.0f} to {g.quantile(.9):+.0f}")
            ax.set_yticks(range(1, len(groups) + 1), [f"{ac}  ({len(g)})" for ac, g in groups], fontsize=8.5)
            ax.set_xlabel("Vertical speed (fpm) — right is softer")
            ax.text(TARGET[1], len(groups) + 0.6, "target", fontsize=7.5, color=t.muted, ha="right")
        c.draw_idle()

    def _card_to_technique(self, idx):
        ac = idx.sibling(idx.row(), 0).data()
        self.focus.setCurrentText(ac)
        self.tabs.setCurrentIndex(3)

    # ---------------- Technique ----------------
    def _fill_focus(self):
        cur = self.focus.currentText()
        self.focus.blockSignals(True)
        self.focus.clear()
        self.focus.addItem("All aircraft")
        for ac, n in self.sel["aircraft"].value_counts().items():
            if n >= 5:
                self.focus.addItem(ac)
        if self.focus.findText(cur) >= 0:
            self.focus.setCurrentText(cur)
        self.focus.blockSignals(False)

    def draw_technique(self):
        t, df = self.theme, self.sel
        focus = self.focus.currentText()
        fdf = df if focus in ("", "All aircraft") else df[df["aircraft"] == focus]
        rest = None if fdf is df else df
        c = self.c_tech
        c.reset(t)
        axes = c.fig.subplots(2, 2)
        specs = [
            (axes[0, 0], "float_s", "Float: 15 m AGL → touchdown (s)", (0, None),
             "Longer float = more time in the flare"),
            (axes[0, 1], "q", "Pitch rate at touchdown (°/s)  − nose dropping · + still pitching up", (-6, 6),
             "Shaded = Lua's 'very good' zone (±1 °/s); beyond ±6 drawn on the edge"),
            (axes[1, 0], "g", "Touchdown G", (0.6, 1.6), "Average over the last ~30 frames before contact"),
        ]
        for ax, col, xlabel, (lo, hi), note in specs:
            style_axes(ax, t, grid="both")
            target_band(ax, t, label=False)
            if col == "q":
                ax.axvspan(-1, 1, color=t.series[0], alpha=0.08, lw=0)
            if rest is not None:
                r = rest.dropna(subset=[col])
                ax.scatter(r[col].clip(lo, hi), r["vs"], s=10, color=t.muted, alpha=0.25, lw=0)
            f = fdf.dropna(subset=[col])
            xs = f[col].clip(lo, hi)
            ax.scatter(xs, f["vs"], s=22, color=t.series[0], alpha=0.7, edgecolors=t.surface, linewidths=0.8,
                       zorder=3)
            c.hover_points(ax, xs, f["vs"], [landing_tip(r) for _, r in f.iterrows()], f.index)
            rho = lra.spearman(f[col], f["vs"]) if len(f) >= 8 else np.nan
            if len(f) >= 20:
                bins = pd.qcut(xs, min(8, len(f) // 10), duplicates="drop")
                med = f.groupby(bins, observed=True).agg(x=(col, "median"), y=("vs", "median"))
                ax.plot(med["x"].clip(lo, hi), med["y"], color=t.ink, lw=2, zorder=4)
            ax.set_xlabel(xlabel)
            ax.set_ylabel("fpm")
            ax_title(ax, t, f"{'All aircraft' if fdf is df else focus}" +
                     (f"   ρ = {rho:+.2f}" if rho == rho else ""), note)
            vs_ylim(ax, fdf["vs"])
            if hi is not None:
                ax.set_xlim(lo - (hi - lo) * 0.03, hi + (hi - lo) * 0.03)

        ax = axes[1, 1]
        style_axes(ax, t)
        target_band(ax, t, label=False)
        data = [fdf[fdf["flare_grade"] == gname]["vs"] for gname in lra.FLARE_ORDER]
        pos = [i + 1 for i, d in enumerate(data) if len(d)]
        if pos:
            bp = ax.boxplot([d for d in data if len(d)], positions=pos, whis=(10, 90), showfliers=False,
                            patch_artist=True, widths=0.5, medianprops=dict(color=t.ink, lw=1.5),
                            whiskerprops=dict(color=t.axis), capprops=dict(color=t.axis))
            for p, i in zip(bp["boxes"], pos):
                d = data[i - 1]
                p.set_facecolor(t.series[0] if i == 1 else t.muted)
                p.set_alpha(0.8)
                p.set_edgecolor(t.surface)
                c.hover_patch(ax, p, f"{lra.FLARE_ORDER[i - 1]} flare · {len(d)} landings\n"
                                     f"median {d.median():+.0f} fpm")
        ax.set_xticks([1, 2, 3], [f"{gname}\n{len(d)} ({len(d) / max(len(fdf), 1) * 100:.0f}%)"
                                  for gname, d in zip(lra.FLARE_ORDER, data)], fontsize=8.5)
        ax.set_xlim(0.4, 3.6)
        ax.set_ylabel("fpm")
        ax_title(ax, t, "VS by Lua flare grade", "Whiskers = 10th–90th percentile")
        vs_ylim(ax, fdf["vs"])
        c.draw_idle()

    # ---------------- Sessions ----------------
    def draw_sessions(self):
        t, df = self.theme, self.sel
        st = lra.session_table(df)
        self.sess_model.set_frame(st)
        self.sess_model.fit_columns(self.sess_view)
        self.sess_view.sortByColumn(1, Qt.DescendingOrder)
        if len(st):
            self.sess_view.setCurrentIndex(self.sess_proxy.index(0, 0))
        self._draw_session()

        c = self.c_habits
        c.reset(t)
        axes = c.fig.subplots(1, 3, sharey=True, width_ratios=[1, 2.2, 1.1])
        if len(df):
            idx = df["session_idx"].clip(upper=8)
            days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            overall = df["vs"].median()
            lims = []
            specs = [
                (axes[0], idx, list(range(1, 9)), [str(i) if i < 8 else "8+" for i in range(1, 9)],
                 "Landing # in session", [f"Landing {i if i < 8 else '8+'} of session" for i in range(1, 9)]),
                (axes[1], df["hour"], list(range(24)), [str(h) if h % 3 == 0 else "" for h in range(24)],
                 "Hour of day", [f"{h:02d}:00–{h:02d}:59" for h in range(24)]),
                (axes[2], df["weekday"], list(range(7)), list("MTWTFSS"), "Weekday", days),
            ]
            for ax, key, cats, labels, title, tips in specs:
                style_axes(ax, t)
                agg = df.groupby(key)["vs"].agg(["median", "count"]).reindex(cats)
                x = np.arange(len(cats))
                bars = ax.bar(x, (agg["median"] - overall).fillna(0), width=0.78, color=t.series[0])
                for b, lbl, (_, r) in zip(bars, tips, agg.iterrows()):
                    n = 0 if pd.isna(r["count"]) else int(r["count"])
                    if n < 5:
                        b.set_alpha(0.3)
                    c.hover_patch(ax, b, f"{lbl}: median {r['median']:+.0f} fpm "
                                         f"({r['median'] - overall:+.0f} vs overall) · {n} landings" if n
                                  else f"{lbl}: no landings")
                ax.set_xticks(x, labels, fontsize=8)
                ax.set_title(title, loc="left", fontsize=9.5, color=t.ink)
                ax.axhline(0, color=t.ink, lw=1)
                reliable = (agg["median"] - overall)[agg["count"] >= 5].abs()
                span = max(reliable.max() if len(reliable) else 0, 40)
                lims.append(span * 1.25)
            axes[0].set_ylim(-max(lims), max(lims))  # sparse bars clip; hover shows the value
            axes[0].set_ylabel(f"fpm vs overall median ({overall:+.0f})")
        c.draw_idle()

    def _draw_session(self):
        t = self.theme
        c = self.c_session
        c.reset(t)
        idx = self.sess_view.currentIndex()
        if not idx.isValid():
            c.draw_idle()
            return
        sid = int(self.sess_proxy.index(idx.row(), 0).data(Qt.UserRole))
        g = self.sel[self.sel["session"] == sid].sort_values("time")
        ax = c.fig.add_subplot()
        style_axes(ax, t)
        target_band(ax, t)
        x = np.arange(1, len(g) + 1)
        ax.plot(x, g["vs"], color=t.axis, lw=1, zorder=2)
        ax.scatter(x, g["vs"], c=[t.rating[str(r)] for r in g["rating"]], s=48, edgecolors=t.surface,
                   linewidths=1.6, zorder=3)
        c.hover_points(ax, x, g["vs"], [landing_tip(r) for _, r in g.iterrows()], g.index)
        if len(g):
            b = g["vs"].idxmax()
            ax.annotate(f"best {g.loc[b, 'vs']:+.0f}", (x[g.index.get_loc(b)], g.loc[b, "vs"]),
                        xytext=(0, 8), textcoords="offset points", ha="center", fontsize=8, color=t.ink2)
            ax_title(ax, t, f"{g['time'].iloc[0]:%a %d %b %Y %H:%M} · {', '.join(g['aircraft'].unique())}")
        ax.set_xticks(x if len(x) <= 20 else x[::max(1, len(x) // 10)])
        ax.set_xlabel("Landing # in session")
        ax.set_ylabel("fpm")
        vs_ylim(ax, g["vs"])
        c.draw_idle()

    # ---------------- Logbook ----------------
    def _build_logbook(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        top = QHBoxLayout()
        self.lb_summary = QLabel(wordWrap=True)
        top.addWidget(self.lb_summary, 1)
        self.lb_hide_empty = QCheckBox("Hide flights without landings", checked=True)
        self.lb_hide_empty.toggled.connect(lambda _: self._redraw(6))
        top.addWidget(self.lb_hide_empty)
        lay.addLayout(top)
        split = QSplitter()
        inner = QTabWidget(documentMode=True)
        self.fl_model = FrameModel({"Date": "%Y-%m-%d", "Hours": "{:.1f}", "Night h": "{:.1f}", "IFR h": "{:.1f}"})
        self.fl_view, self.fl_proxy = make_table(self.fl_model)
        self.fl_view.selectionModel().currentRowChanged.connect(lambda *_: self._draw_flight())
        inner.addTab(self.fl_view, "Flights")
        self.map_model = FrameModel()
        self.map_view, _ = make_table(self.map_model)
        inner.addTab(self.map_view, "Aircraft mapping")
        split.addWidget(panel("Logbook vs. Lua log", "Matched by date, aircraft and landing order (the logbook has "
                                                     "no times). Greyed rows can't be compared.", inner))
        self.c_flight = Chart(2.8)
        split.addWidget(panel("Selected flight", "Each Lua-recorded landing in order", self.c_flight))
        split.setSizes([800, 450])
        lay.addWidget(split, 1)
        row = QHBoxLayout()
        self.c_airports = Chart(3.0)
        self.c_cond = Chart(3.0)
        row.addWidget(panel("By airport", "Airports with 3+ landings, most visited 20. Box = middle 50%, "
                                          "| = median", self.c_airports), 3)
        row.addWidget(panel("Conditions", "From the logbook: night time, time in cloud (IFR), circuit work, "
                                          "trip length", self.c_cond), 2)
        lay.addLayout(row, 1)
        return w

    def open_logbook(self):
        start = str(self.lb_path.parent if self.lb_path else APP_DIR)
        fn, _ = QFileDialog.getOpenFileName(self, "Open X-Plane logbook", start, "Logbooks (*.txt);;All (*)")
        if fn:
            self.qs.setValue("logbook_path", str(Path(fn).resolve()))
            self.reload()

    def _load_logbook(self):
        cands = [self.qs.value("logbook_path"), self.path.parent / lra.LOGBOOK_REL if self.path else None,
                 lra.LOGBOOK_REL]
        self.lb_path = next((Path(c) for c in cands if c and Path(c).exists()), None)
        self.lb_raw = pd.DataFrame(columns=lra.LOGBOOK_COLUMNS)
        if self.lb_path:
            try:
                self.lb_raw = lra.load_logbook(self.lb_path)
            except OSError as e:
                QMessageBox.warning(self, "Could not read logbook", f"{self.lb_path}\n\n{e}")
        self.lb_lbl.setText(f"Logbook: {self.lb_path.name}, {len(self.lb_raw)} flights" if self.lb_path
                            else "Logbook: not found (File ▸ Open logbook…)")
        self.lb_lbl.setToolTip(str(self.lb_path or ""))

    def draw_logbook(self):
        t, fl = self.theme, self.flights
        for c in (self.c_flight, self.c_airports, self.c_cond):
            c.reset(t)
        if fl.empty:
            self.lb_summary.setText("No logbook loaded. File ▸ Open logbook… (X-Plane writes it to "
                                    "Output/logbooks/X-Plane Pilot.txt).")
            self.fl_model.set_frame(pd.DataFrame())
            for c in (self.c_flight, self.c_airports, self.c_cond):
                c.draw_idle()
            return
        d0 = pd.Timestamp(self.date_from.date().toPython())
        d1 = pd.Timestamp(self.date_to.date().toPython())
        chosen = {self.ac_list.item(i).data(Qt.UserRole).upper() for i in range(self.ac_list.count())
                  if self.ac_list.item(i).checkState() == Qt.Checked}
        f = fl[(fl["date"] >= d0) & (fl["date"] <= d1) & (fl["icao"].str.upper().isin(chosen) | (fl["icao"] == ""))]
        if self.lb_hide_empty.isChecked():
            f = f[f["landings"] > 0]
        stats = self.sel.groupby("flight")["vs"].agg(["median", "min"])
        stats = stats.reindex(f.index)
        out = pd.DataFrame({
            "#": f.index, "Date": f["date"], "From": f["dep"], "To": f["arr"], "Aircraft file": f["acf"],
            "ICAO": f["icao"], "Tail": f["tail"], "Hours": f["hours"], "Night h": f["night_h"], "IFR h": f["ifr_h"],
            "Logbook ldg": f["landings"], "Lua ldg": f["lua"], "Status": f["status"],
            "Median VS": stats["median"], "Hardest VS": stats["min"]})
        self.fl_model.muted_color = QColor(t.muted)
        self.fl_model.set_frame(out, ids=f.index,
                                muted=f["status"].isin(["no landing", "before Lua log", "aircraft not matched"]))
        self.fl_view.sortByColumn(0, Qt.DescendingOrder)
        self.fl_model.fit_columns(self.fl_view)

        cmp_ = f[~f["status"].isin(["no landing", "before Lua log"])]
        missed = cmp_[cmp_["status"].str.startswith("Lua missed")]
        orphan = int((self.view["flight"].isna() & (self.view["aircraft"] != "")).sum())
        self.lb_summary.setText(
            f"<b>{len(f)}</b> flights in range · <b>{int((cmp_['status'] == 'match').sum())}</b> of {len(cmp_)} "
            f"comparable flights match exactly · <b>{int((missed['landings'] - missed['lua']).sum())}</b> logbook "
            f"landings the Lua never recorded ({len(missed)} flights) · "
            f"{int((cmp_['status'] == 'aircraft not matched').sum())} flights with an unmatched aircraft · "
            f"{orphan} Lua landings not found in the logbook")

        used = fl[fl["landings"] > 0].groupby("acf").agg(Flights=("date", "size"), Landings=("landings", "sum"),
                                                        Matched=("lua", "sum"))
        mp = pd.DataFrame({"Aircraft file": used.index, "ICAO": [self.acf_map.get(a, "") for a in used.index],
                           "Flights": used["Flights"].to_numpy(), "Logbook ldg": used["Landings"].to_numpy(),
                           "Lua ldg matched": used["Matched"].to_numpy()})
        self.map_model.set_frame(mp.sort_values("Flights", ascending=False), muted=(mp["ICAO"] == "").to_numpy())
        self.map_model.fit_columns(self.map_view)
        if self.fl_proxy.rowCount():
            self.fl_view.setCurrentIndex(self.fl_proxy.index(0, 0))
        self._draw_flight()

        m = self.sel[self.sel["flight"].notna()]
        # By airport
        ax = self.c_airports.fig.add_subplot()
        style_axes(ax, t, grid="x")
        apt = m[m["airport"] != ""]
        counts = apt["airport"].value_counts()
        top = counts[counts >= 3].index[:20]
        groups = sorted(((a, apt[apt["airport"] == a]["vs"]) for a in top), key=lambda p: p[1].median())
        if groups:
            ax.axvspan(*TARGET, color=t.good, alpha=0.07, lw=0)
            bp = ax.boxplot([g for _, g in groups], orientation="horizontal", whis=(10, 90), showfliers=False, patch_artist=True,
                            widths=0.6, medianprops=dict(color=t.ink, lw=1.5), whiskerprops=dict(color=t.axis),
                            capprops=dict(color=t.axis))
            for patch, (a, g) in zip(bp["boxes"], groups):
                patch.set_facecolor(t.series[0])
                patch.set_alpha(0.8)
                patch.set_edgecolor(t.surface)
                types = ", ".join(apt[apt["airport"] == a]["aircraft"].value_counts().index[:3])
                self.c_airports.hover_patch(ax, patch, f"{a} · {len(g)} landings ({types})\n"
                                                        f"median {g.median():+.0f} fpm · hardest {g.min():+.0f}")
            ax.set_yticks(range(1, len(groups) + 1), [f"{a}  ({len(g)})" for a, g in groups], fontsize=8)
            ax.set_xlabel("Vertical speed (fpm) — right is softer")
        else:
            ax.text(0.5, 0.5, "No airport has 3+ matched landings", transform=ax.transAxes, ha="center",
                    color=t.muted)
        self.c_airports.draw_idle()

        # Conditions: pairs of boxes, baseline vs condition
        ax = self.c_cond.fig.add_subplot()
        style_axes(ax, t)
        target_band(ax, t, label=False)
        multi = m["flight"].map(fl["landings"]) > 1
        circuit = (m["dep"] == m["arr"]) & multi
        arrivals = m[(m["dep"] != m["arr"]) & (m["airport"] != "")]
        pairs = [("Day", m[~m["night"].astype(bool)]["vs"], "Night", m[m["night"].astype(bool)]["vs"]),
                 ("Clear", m[~m["imc"].astype(bool)]["vs"], "Cloud", m[m["imc"].astype(bool)]["vs"]),
                 ("Trip", m[~circuit]["vs"], "Circuit", m[circuit]["vs"]),
                 ("< 1.5 h", arrivals[arrivals["flight_hours"] < 1.5]["vs"],
                  "≥ 1.5 h", arrivals[arrivals["flight_hours"] >= 1.5]["vs"])]
        pos, ticks, labels = 1, [], []
        for a_name, a, b_name, b in pairs:
            for j, (name, g) in enumerate(((a_name, a), (b_name, b))):
                ticks.append(pos)
                labels.append(f"{name}\n{len(g)}")
                if len(g) >= 3:
                    bp = ax.boxplot([g], positions=[pos], whis=(10, 90), showfliers=False, patch_artist=True,
                                    widths=0.7, medianprops=dict(color=t.ink, lw=1.5),
                                    whiskerprops=dict(color=t.axis), capprops=dict(color=t.axis))
                    box = bp["boxes"][0]
                    box.set_facecolor(t.series[j])
                    box.set_alpha(0.85)
                    box.set_edgecolor(t.surface)
                    self.c_cond.hover_patch(ax, box, f"{name} · {len(g)} landings\nmedian {g.median():+.0f} fpm\n"
                                                     f"middle 50%: {g.quantile(.25):+.0f} to {g.quantile(.75):+.0f}")
                pos += 1
            pos += 0.8
        ax.set_xticks(ticks, labels, fontsize=8)
        ax.set_ylabel("fpm")
        vs_ylim(ax, m["vs"])
        self.c_cond.draw_idle()

    def _draw_flight(self):
        t = self.theme
        c = self.c_flight
        c.reset(t)
        idx = self.fl_view.currentIndex()
        if idx.isValid():
            fid = self.fl_proxy.index(idx.row(), 0).data(FrameModel.ID_ROLE)
            f = self.flights.loc[fid]
            g = self.all[self.all["flight"] == fid].sort_values("time")
            ax = c.fig.add_subplot()
            style_axes(ax, t)
            ax_title(ax, t, f"{f['dep']} → {f['arr']} · {f['date']:%d %b %Y} · {f['acf']}",
                     f"{f['landings']} landings in logbook · {len(g)} recorded by the Lua · {f['hours']:.1f} h")
            if len(g):
                target_band(ax, t)
                x = np.arange(1, len(g) + 1)
                ax.plot(x, g["vs"], color=t.axis, lw=1, zorder=2)
                ax.scatter(x, g["vs"], c=[t.rating[str(r)] for r in g["rating"]], s=48, edgecolors=t.surface,
                           linewidths=1.6, zorder=3)
                c.hover_points(ax, x, g["vs"], [landing_tip(r) for _, r in g.iterrows()], g.index)
                ax.set_xticks(x if len(x) <= 20 else x[::max(1, len(x) // 10)])
                ax.set_xlim(0.5, max(len(g), f["landings"]) + 0.5)
                vs_ylim(ax, g["vs"])
                ax.set_xlabel("Landing # in flight")
                ax.set_ylabel("fpm")
            else:
                ax.text(0.5, 0.5, "No Lua landings matched to this flight", transform=ax.transAxes,
                        ha="center", color=t.muted)
                ax.set_xticks([])
                ax.set_yticks([])
        c.draw_idle()

    # ---------------- Landings ----------------
    def _fill_landings(self):
        df = self.view if self.show_excl.isChecked() else self.sel
        df = df.sort_values("time", ascending=False)
        excluded = ~df.index.isin(self.sel.index)
        out = pd.DataFrame({
            "Time": df["time"], "Aircraft": df["aircraft"], "Class": df["ac_class"], "VS": df["vs"],
            "Rating": df["rating"].astype(str), "Airport": df["airport"],
            "Route": [f"{a}→{b}" if a else "" for a, b in zip(df["dep"], df["arr"])],
            "Score": df["score"], "G": df["g"], "Float s": df["float_s"],
            "Flare": df["flare"], "Pitch q": df["q"], "Nose °/s": df["nose"], "Session": df["session"],
            "# in session": df["session_idx"], "# in type": df["type_idx"],
            "Flags": [f + (" (bounced)" if b else "") for f, b in zip(df["flags"], df["bounced"])],
        })
        self.land_model.muted_color = QColor(self.theme.muted)
        self.land_model.set_frame(out, ids=df.index, muted=excluded)
        self.land_model.fit_columns(self.land_view)
        counts = {lbl: int(self.view[c].sum()) for c, lbl in lra.FLAG_LABELS.items() if self.view[c].sum()}
        self.excl_lbl.setText("Flags in range: " + (" · ".join(f"{k} {v}" for k, v in counts.items()) or "none")
                              + ". Greyed rows are excluded from the analysis (sidebar).")

    def show_landing(self, idx: int):
        self.tabs.setCurrentIndex(5)
        self.search.clear()
        for row in range(self.land_proxy.rowCount()):
            pi = self.land_proxy.index(row, 0)
            if pi.data(FrameModel.ID_ROLE) == idx:
                self.land_view.setCurrentIndex(pi)
                self.land_view.scrollTo(pi, QAbstractItemView.PositionAtCenter)
                return

    def _on_landing_row(self, cur, _prev):
        if not cur.isValid():
            return
        idx = self.land_proxy.index(cur.row(), 0).data(FrameModel.ID_ROLE)
        self.detail.setHtml(self._detail_html(self.all.loc[idx]))

    def _detail_html(self, r) -> str:
        t = self.theme
        vs_p, g_p, f_p = (float(x) for x in lra.score_parts(r["vs"], r["g"], r["q"], r["qrad"], self.settings))
        ref = self.all[self.all["clean"] & (self.all["aircraft"] == r["aircraft"]) & (self.all.index != r.name)]
        before = ref[ref["time"] < r["time"]].tail(20)
        pct_all = lra.percentile_in(r["vs"], ref["vs"]) if len(ref) >= 5 else np.nan
        pct_recent = lra.percentile_in(r["vs"], before["vs"]) if len(before) >= 5 else np.nan
        sess = self.all[self.all["session"] == r["session"]]
        col = t.rating[str(r["rating"])]

        def row(k, v):
            return f"<tr><td style='color:{t.ink2}; padding-right:14px'>{k}</td><td>{v}</td></tr>"

        fl = f"{r['float_s']:.1f} s" if pd.notna(r["float_s"]) else f"invalid ({r['float']:.0f} s logged)"
        html = [f"<body style='color:{t.ink}'>",
                f"<p style='font-size:26pt; font-weight:600; margin:0'>{r['vs']:+.0f} "
                f"<span style='font-size:11pt; color:{t.ink2}'>fpm</span></p>",
                f"<p style='margin:0 0 10px 0'><span style='color:{col}'>■</span> <b>{r['rating']}</b> · "
                f"{r['aircraft'] or '?'} ({r['ac_class']}) · {r['time']:%a %d %b %Y %H:%M:%S}</p>"]
        if r["flags"] or r["bounced"]:
            html.append(f"<p style='color:{t.critical}'>⚠ {r['flags'] or ''}"
                        f"{' · followed by a re-touch' if r['bounced'] else ''}</p>")
        html.append("<table cellspacing='0' cellpadding='2'>")
        html.append(row("Score", f"<b>{r['score']:.0f}</b> / 100 &nbsp; (VS {vs_p:.0f}/60 · G {g_p:.0f}/20 · "
                                 f"flare {f_p:.0f}/20)"))
        if pct_all == pct_all:
            html.append(row("vs your " + (r["aircraft"] or "?"),
                            f"softer than {pct_all:.0f}% of {len(ref)} other landings"))
        if pct_recent == pct_recent:
            html.append(row("vs the 20 before", f"softer than {pct_recent:.0f}% · their median "
                                                f"{before['vs'].median():+.0f} fpm"))
        html.append(row("Touchdown G", f"{r['g']:.2f}"))
        html.append(row("Float", fl))
        html.append(row("Flare", f"{r['flare']}" + (" (aggressive)" if r["aggressive"] else "")))
        html.append(row("Pitch rate at contact", f"{r['q']:+.2f} °/s "
                                                 f"({'still pitching up' if r['q'] > 1 else 'nose dropping' if r['q'] < -1 else 'steady'})"))
        html.append(row("Nose lowering rate", f"{r['nose']:+.2f} °/s"))
        html.append(row("Session", f"#{r['session']} · landing {r['session_idx']} of {len(sess)}"))
        if pd.notna(r["flight"]):
            f = self.flights.loc[int(r["flight"])]
            extra = " · night" * bool(r["night"]) + " · cloud time" * bool(r["imc"])
            html.append(row("Flight", f"{f['dep']} → {f['arr']} · {f['date']:%d %b %Y} · {f['hours']:.1f} h{extra}"))
            html.append(row("Logbook aircraft", f"{f['acf']} ({f['tail']})"))
            html.append(row("Landing in flight", f"{int(r['leg'])} of {f['landings']} logged"
                                                 + (f" ({f['status']})" if f["status"] != "match" else "")))
            html.append(row("Airport", r["airport"] or "en route (not the final landing of the flight)"))
        else:
            html.append(row("Flight", "not found in the logbook"))
        if pd.notna(r["type_idx"]):
            html.append(row("Experience", f"landing {int(r['type_idx'])} in type"))
        html.append("</table></body>")
        return "".join(html)

    def export_csv(self):
        if self.sel.empty:
            return
        fn, _ = QFileDialog.getSaveFileName(self, "Export landings", str((self.path.parent if self.path else APP_DIR) / "landings_export.csv"), "CSV (*.csv)")
        if fn:
            cols = ["time", "aircraft", "ac_class", "vs", "rating", "score", "g", "float_s", "flare", "flare_grade",
                    "q", "qrad", "nose", "session", "session_idx", "type_idx", "bounced", "dep", "arr",
                    "airport", "flight_hours", "night", "imc"]
            self.sel[cols].to_csv(fn, index=False)
            self.statusBar().showMessage(f"Exported {len(self.sel)} landings to {fn}", 8000)

    # ---------------- Tips ----------------
    def _build_tips(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.tips_view = QTextBrowser(openExternalLinks=False)
        lay.addWidget(panel("How to improve your score", "Where the landings currently shown lose points, "
                                                         "then tips for each part of the score",
                            self.tips_view), 1)
        return w

    def _new_tip(self):
        lost = lra.points_lost(self.sel, self.settings) if len(self.sel) else None
        self.tip = lra.random_tip(lost, avoid=self.tip)
        self._show_tip()

    def _show_tip(self):
        if self.tip is None:
            return
        t = self.theme
        area = lra.TIP_AREAS[self.tip.area][0]
        self.tip_lbl.setText(f"<span style='color:{t.muted}'>{area}</span><br><b>{self.tip.title}</b>"
                             f"<br><span style='color:{t.ink2}'>{lra.tip_text(self.tip, self.settings)}</span>")

    def draw_tips(self):
        t, s = self.theme, self.settings
        lost = lra.points_lost(self.sel, s)
        parts = [f"<body style='color:{t.ink}; background:{t.surface}'>"]
        order = [a for a in lra.TIP_AREAS if lra.TIP_AREAS[a][1]]
        if lost:
            order.sort(key=lambda a: -lost[a])
            total = sum(lost[a] for a in order)
            parts.append(f"<h3 style='margin:0 0 4px 0'>Where your points go</h3>"
                         f"<p style='margin:0 0 8px 0; color:{t.ink2}'>Across {len(self.sel)} landings you "
                         f"lose {total:.0f} of 100 points on average.</p><table cellspacing='0' cellpadding='3'>")
            for a in order:
                name, avail = lra.TIP_AREAS[a]
                frac = lost[a] / avail
                col = t.good_text if frac < 0.15 else t.critical if frac > 0.4 else t.notice_text
                bar = "█" * round(frac * 20) + f"<span style='color:{t.grid}'>{'█' * (20 - round(frac * 20))}</span>"
                parts.append(f"<tr><td><b>{name}</b></td><td style='color:{t.ink2}'>&nbsp;−{lost[a]:.1f} of "
                             f"{avail}&nbsp;</td><td style='color:{col}; font-family:monospace'>{bar}</td></tr>")
            parts.append("</table>")
            if lost["vs"] >= 1:
                firm = lost["vs_firm"] >= lost["vs_soft"]
                parts.append(f"<p style='margin:6px 0 0 0; color:{t.ink2}'>Most vertical-speed points go to "
                             + (f"landing firmer than {s.target_firm:.0f} fpm." if firm else
                                f"landing softer than {s.target_soft:.0f} fpm (floating)."))
            parts.append(f"<p style='margin:6px 0 14px 0'>Biggest gain: <b>{lra.TIP_AREAS[order[0]][0]}</b>. "
                         "Start with the tips below.</p>")
        for a in order + ["practice"]:
            name, avail = lra.TIP_AREAS[a]
            sub = f" &nbsp;<span style='color:{t.muted}'>{avail} pts</span>" if avail else ""
            parts.append(f"<h3 style='margin:10px 0 4px 0'>{name}{sub}</h3>")
            for tip in lra.TIPS:
                if tip.area == a:
                    parts.append(f"<p style='margin:0 0 2px 0'><b>{tip.title}</b></p>"
                                 f"<p style='margin:0 0 10px 0; color:{t.ink2}'>{lra.tip_text(tip, s)}</p>")
        self.tips_view.setHtml("".join(parts) + "</body>")

    def _about_score(self):
        s = self.settings
        QMessageBox.information(self, "About the score", (
            "The score (0–100) is this dashboard's own composite, not the Lua rating:\n\n"
            f"• Vertical speed – 60 pts. Full marks from {s.target_firm:.0f} to {s.target_soft:.0f} fpm. "
            "Softer than that loses up to 15 pts (floaty, long landings); firmer loses points linearly "
            "down to 0 at −600 fpm.\n"
            "• Touchdown G – 20 pts. Full marks within ±0.08 of 1.0 G, 0 beyond ±0.40.\n"
            "• Flare – 20 pts from pitch rate at contact: |q| ≤ 1 °/s → 20, ≤ 2 → 12, more → 4, "
            "aggressive → 0.\n\n"
            "Excluded entries (sidebar) are crashes, re-touches, helicopters, entries with no aircraft code "
            "and sim resets. A re-touch is a second entry for the same aircraft within the re-touch window; "
            "the landing before it is counted as 'bounced'."))

    def closeEvent(self, e):
        self.qs.setValue("live", self.live_cb.isChecked())
        super().closeEvent(e)


def self_test() -> int:
    """Build the window on a generated log and draw every tab (used by CI on the built app)."""
    import tempfile
    from landingrate import sample
    with tempfile.TemporaryDirectory() as d:
        log = Path(d) / "LandingRate.log"
        sample.write_log(log, n=300)
        app = QApplication.instance() or QApplication(sys.argv[:1])
        # throwaway settings so a test run never touches the user's last log, theme, etc.
        qs = QSettings(str(Path(d) / "selftest.ini"), QSettings.IniFormat)
        qs.setValue("check_updates", False)  # no network in CI
        w = MainWindow(log, qs)
        w.resize(1400, 900)
        w.show()
        for i in range(w.tabs.count()):
            w.tabs.setCurrentIndex(i)
            app.processEvents()
        n = len(w.sel)
        icon_ok = not asset_pixmap("icon_256.png").isNull()
        w.close()
    if not icon_ok:
        print("self-test FAILED: bundled icon assets are missing")
        return 1
    print(f"self-test ok: {n} landings, {w.tabs.count()} tabs drawn, icons bundled")
    return 0 if n else 1


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["report"]:
        return lra.main(argv[1:])
    if argv[:1] in (["--version"], ["-V"]):
        print(f"{APP_NAME} {__version__}")
        return 0
    if argv[:1] == ["--self-test"]:
        return self_test()
    if argv[:1] in (["-h"], ["--help"]):
        print(__doc__)
        return 0
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setDesktopFileName("landingrate-insights")
    app.setWindowIcon(app_icon())
    w = MainWindow(Path(argv[0]) if argv else None)
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
