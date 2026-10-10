"""
Landing Rate analysis engine
============================
Parses the log written by Resources/plugins/FlyWithLua/Scripts/LandingRate.lua
and derives everything the dashboard (landingrate/app.py) shows. It can
also be run on its own for a text report:

    python -m landingrate report                    # LandingRate.log in the current directory
    python -m landingrate report some.log --aircraft B738 --since 2026-01-01
    python -m landingrate report --logbook ""      # skip matching Output/logbooks/X-Plane Pilot.txt

Log formats written by lrl_postLandingRate():
    non-VR: time,ICAO,VS,G,nose,float,"flare grade",q,|Qrad|
    VR:     time,ICAO,"Vertical Speed: xFPM / yG","flare grade","Nose: x deg/sec | Float: y secs","RATING","q,|Qrad|"

Field meanings (from the Lua source):
    vs      ground-relative vertical speed at first wheel contact, fpm (negative = descending)
    g       average normal G over the last ~30 frames before touchdown
    nose    pitch rate (deg/s) at the moment ALL wheels are down; negative = nose lowering
    float   seconds from passing 15 m AGL to first wheel contact
    q       pitch rate (deg/s) at first wheel contact; >1 = still rotating up ("early"),
            <-1 = nose dropping ("late")
    qrad    |Qrad| (rad/s); >1 marks the flare as "Aggressive"

Dependencies: pandas, numpy
"""

from __future__ import annotations

import argparse
import csv
import difflib
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

COLUMNS = ["time", "aircraft", "vs", "g", "nose", "float", "flare", "q", "qrad"]

# ----------------------------------------------------------------------
# Rating bands - identical to lrl_evalRating() in LandingRate.lua
# ----------------------------------------------------------------------
RATING_ORDER = ["BUTTER", "GREAT", "ACCEPTABLE", "HARD", "WASTED"]


def rating_for(vs: float) -> str:
    if vs >= -125:
        return "BUTTER"
    if vs >= -250:
        return "GREAT"
    if vs >= -350:
        return "ACCEPTABLE"
    if vs >= -600:
        return "HARD"
    return "WASTED"


# Flare grades as produced by lrl_populatePopupStats(), collapsed to 3 buckets
FLARE_ORDER = ["Very good", "Early", "Late"]


def flare_bucket(text: str) -> str:
    t = str(text).lower()
    if "early" in t:
        return "Early"
    if "late" in t:
        return "Late"
    return "Very good"


# ----------------------------------------------------------------------
# Aircraft classes. Unknown ICAO codes fall into "Other".
# ----------------------------------------------------------------------
_CLASSES = {
    "Helicopter": "B429 S76 H500 EC35 EC45 EC30 R22 R44 R66 AS50 AS55 AS65 B06 B407 B412 B505 H135 H145 H160 "
                  "A109 A139 UH1 S92 MI8 BK17 EC75",
    "Piston single": "C150 C152 C162 C170 C172 C177 C182 C206 C210 SR20 SR22 DR40 L5 Y18T PA28 P28A P28B P28R "
                     "PA18 PA24 PA32 PA46 J3 DA40 DA20 RV7 RV10 M20P BE33 BE35 BE36 TBM7 ASTR CUB",
    "Piston twin": "BE58 BE55 BE76 PA34 PA44 DA42 DA62 C310 C340 C414 P68 DC3 B17 AC50",
    "Turboprop": "BE9L BE20 B350 BE99 C208 C08T PC12 PC6T PC21 DHC6 DHC2 DH8A DH8B DH8C DH8D AT43 AT45 AT72 "
                 "AT75 AT76 MU2 P180 KODI TBM8 TBM9 C130 C30J SW4 JS32 JS41 F27 SF34 E120 L410 AN2",
    "Business jet": "E55P E50P SF50 CL30 CL35 CL60 GLF4 GLF5 GLF6 GLEX C510 C525 C25A C25B C25C C56X C680 C68A "
                    "C700 C750 LJ35 LJ45 LJ75 FA7X FA8X F2TH PRM1 HDJT H25B EA50 PC24",
    "Airliner": "B732 B733 B734 B735 B736 B737 B738 B739 B38M B39M B752 B753 B762 B763 B764 B772 B77L B77W "
                "B788 B789 B78X B744 B748 B712 B722 B703 A318 A319 A320 A321 A20N A21N A332 A333 A330 A339 "
                "A343 A346 A359 A35K A388 A306 A310 E170 E175 E190 E195 E290 E295 CRJ2 CRJ7 CRJ9 CRJX MD11 "
                "MD82 MD83 MD88 MD90 DC10 DC93 F100 F70 B461 B462 B463 RJ85 RJ1H BCS1 BCS3 C919 SU95 CONC",
}
AIRCRAFT_CLASS = {code.upper(): cls for cls, codes in _CLASSES.items() for code in codes.split()}
CLASS_ORDER = list(_CLASSES) + ["Other"]


def aircraft_class(icao: str) -> str:
    return AIRCRAFT_CLASS.get(str(icao).upper(), "Other")


# ----------------------------------------------------------------------
# Parsing
# ----------------------------------------------------------------------
_VR_VS = re.compile(r"Vertical Speed:\s*(-?[\d.]+)FPM\s*/\s*(-?[\d.]+)G")
_VR_NOSE = re.compile(r"Nose:\s*(-?[\d.]+)\s*deg/sec(?:\s*\|\s*Float:\s*(-?[\d.]+))?")


def _parse_row(row: list[str]) -> list | None:
    row = [c.strip() for c in row]
    if len(row) < 3 or not re.match(r"\d{4}-\d{2}-\d{2}", row[0]):
        return None
    time, ac = row[0], row[1]
    m = _VR_VS.search(row[2])
    if m:  # VR format
        if len(row) < 7:
            return None
        n = _VR_NOSE.search(row[4])
        qparts = row[6].split(",")
        try:
            q = float(qparts[0])
            qrad = float(qparts[1]) if len(qparts) > 1 else np.nan
        except ValueError:
            q, qrad = np.nan, np.nan
        return [time, ac, float(m.group(1)), float(m.group(2)),
                float(n.group(1)) if n else np.nan,
                float(n.group(2)) if n and n.group(2) else np.nan,
                row[3], q, qrad]
    if len(row) < 9:
        return None
    try:
        return [time, ac, float(row[2]), float(row[3]), float(row[4]), float(row[5]),
                row[6], float(row[7]), float(row[8])]
    except ValueError:
        return None


def load_log(path: str | Path) -> tuple[pd.DataFrame, int]:
    """Return (raw dataframe, number of unparseable lines skipped)."""
    rows, skipped = [], 0
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        for row in csv.reader(fh):
            if not row or not "".join(row).strip():
                continue
            parsed = _parse_row(row)
            if parsed is None:
                skipped += 1
            else:
                rows.append(parsed)
    df = pd.DataFrame(rows, columns=COLUMNS)
    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    skipped += int(df["time"].isna().sum())
    df = df.dropna(subset=["time"]).sort_values("time", kind="stable").reset_index(drop=True)
    df["aircraft"] = df["aircraft"].fillna("").astype(str)
    return df, skipped


# ----------------------------------------------------------------------
# Enrichment: flags, sessions, experience, score
# ----------------------------------------------------------------------
@dataclass
class Settings:
    crash_vs: float = -1000.0       # anything harder is a crash / reset, not a landing
    max_g: float = 3.0              # ditto for touchdown G (or G <= 0)
    retouch_window_s: float = 60.0  # second log entry this soon after the previous = bounce / re-touch
    session_gap_min: float = 90.0   # a gap longer than this starts a new sim session
    float_max_s: float = 60.0       # longer "float" = the 15 m timer armed on the ground; ignore value
    min_descent: float = -10.0      # VS above this was not a descent (sim reset on the ground etc.)
    target_soft: float = -60.0      # score target window (fpm)
    target_firm: float = -200.0


FLAG_LABELS = {
    "f_noac": "No aircraft code",
    "f_heli": "Helicopter",
    "f_crash": "Crash / outlier",
    "f_nodesc": "Not a descent",
    "f_retouch": "Bounce / re-touch",
}


def score_parts(vs, g, q, qrad, s: Settings = Settings()):
    """Score components (vs_pts /60, g_pts /20, flare_pts /20). Vectorised."""
    vs, g, q, qrad = (np.asarray(x, dtype=float) for x in (vs, g, q, qrad))
    # VS: full marks inside the target window; very soft loses a little (floaty,
    # long landings), firm loses linearly to zero at -600 fpm.
    vs_pts = np.where(
        vs > s.target_soft,
        60 - 15 * np.clip((vs - s.target_soft) / (0 - s.target_soft), 0, 1),
        np.where(vs >= s.target_firm, 60,
                 60 * np.clip((vs + 600) / (s.target_firm + 600), 0, 1)))
    g_pts = 20 * np.clip(1 - (np.abs(g - 1.0) - 0.08) / 0.32, 0, 1)
    aq = np.abs(q)
    flare_pts = np.where(aq <= 1, 20, np.where(aq <= 2, 12, 4))
    flare_pts = np.where(np.nan_to_num(qrad) > 1, 0, flare_pts)
    flare_pts = np.where(np.isnan(q), 10, flare_pts)
    return vs_pts, g_pts, flare_pts


def landing_score(vs, g, q, qrad, s: Settings = Settings()):
    """0-100 composite: 60 pts vertical speed (full marks inside the target window),
    20 pts touchdown G near 1.0, 20 pts flare pitch rate. Not the Lua's rating."""
    vs_pts, g_pts, flare_pts = score_parts(vs, g, q, qrad, s)
    return np.round(vs_pts + g_pts + flare_pts, 1)


def enrich(raw: pd.DataFrame, s: Settings = Settings()) -> pd.DataFrame:
    df = raw.copy()
    df["rating"] = pd.Categorical([rating_for(v) for v in df["vs"]], RATING_ORDER, ordered=True)
    df["flare_grade"] = pd.Categorical([flare_bucket(t) for t in df["flare"]], FLARE_ORDER)
    df["aggressive"] = df["flare"].str.contains("Aggressive", case=False, na=False)
    df["ac_class"] = [aircraft_class(a) if a else "Other" for a in df["aircraft"]]
    df["float_s"] = df["float"].where((df["float"] > 0) & (df["float"] <= s.float_max_s))
    df["score"] = landing_score(df["vs"], df["g"], df["q"], df["qrad"], s)

    df["f_noac"] = df["aircraft"].str.strip() == ""
    df["f_heli"] = df["ac_class"] == "Helicopter"
    df["f_crash"] = (df["vs"] < s.crash_vs) | (df["g"] > s.max_g) | (df["g"] <= 0)
    df["f_nodesc"] = df["vs"] > s.min_descent

    # Re-touch: a second log entry for the same aircraft shortly after the previous
    # one. The previous entry is the real touchdown and is marked as "bounced".
    gap = df.groupby("aircraft")["time"].diff().dt.total_seconds()
    df["f_retouch"] = gap <= s.retouch_window_s
    nxt = df.groupby("aircraft")["time"].diff(-1).dt.total_seconds().abs()
    df["bounced"] = nxt <= s.retouch_window_s

    flag_cols = list(FLAG_LABELS)
    df["flags"] = df[flag_cols].apply(
        lambda r: ", ".join(FLAG_LABELS[c] for c in flag_cols if r[c]), axis=1)
    df["clean"] = ~df[flag_cols].any(axis=1)

    # Sessions: split on long gaps, regardless of aircraft
    new_sess = df["time"].diff().dt.total_seconds().div(60).gt(s.session_gap_min) | (df.index == 0)
    df["session"] = new_sess.cumsum().astype(int)
    df["session_idx"] = df.groupby("session").cumcount() + 1

    # Experience in type: nth clean landing in this aircraft
    df["type_idx"] = np.nan
    clean = df["clean"]
    df.loc[clean, "type_idx"] = df[clean].groupby("aircraft").cumcount() + 1

    df["hour"] = df["time"].dt.hour
    df["weekday"] = df["time"].dt.dayofweek
    return df


# ----------------------------------------------------------------------
# Aggregates
# ----------------------------------------------------------------------
def spearman(a: pd.Series, b: pd.Series) -> float:
    """Spearman rank correlation without scipy (pandas' method="spearman" imports it)."""
    ok = a.notna() & b.notna()
    return float(a[ok].rank().corr(b[ok].rank()))


def pct(series: pd.Series, cond) -> float:
    return float(cond.mean() * 100) if len(series) else float("nan")


def summary(df: pd.DataFrame) -> dict:
    """Headline numbers for a set of (normally clean) landings."""
    if df.empty:
        return {"n": 0}
    vs = df["vs"]
    return {
        "n": len(df),
        "median_vs": float(vs.median()),
        "mean_vs": float(vs.mean()),
        "iqr_vs": float(vs.quantile(0.75) - vs.quantile(0.25)),
        "p90_hard": float(vs.quantile(0.10)),  # 90% of landings are softer than this
        "pct_great": pct(vs, vs >= -250),
        "pct_hard": pct(vs, vs < -350),
        "mean_score": float(df["score"].mean()),
        "median_g": float(df["g"].median()),
        "median_float": float(df["float_s"].median()),
        "bounce_rate": pct(vs, df["bounced"]),
        "current_streak": current_streak(df),
        "best_streak": best_streak(df),
        "best": df.loc[vs.idxmax()] if len(df) else None,
    }


def _streak_runs(df: pd.DataFrame, threshold: float):
    ok = (df.sort_values("time")["vs"] >= threshold).to_numpy()
    runs, cur = [], 0
    for v in ok:
        cur = cur + 1 if v else 0
        runs.append(cur)
    return runs


def current_streak(df: pd.DataFrame, threshold: float = -250) -> int:
    runs = _streak_runs(df, threshold)
    return runs[-1] if runs else 0


def best_streak(df: pd.DataFrame, threshold: float = -250) -> int:
    runs = _streak_runs(df, threshold)
    return max(runs) if runs else 0


def trend_slope(y: pd.Series, x: pd.Series | None = None) -> float:
    """Least-squares slope of y against x (default: landing index). NaN if too short."""
    y = pd.Series(y).astype(float)
    x = pd.Series(np.arange(len(y)) if x is None else x, index=y.index).astype(float)
    ok = y.notna() & x.notna()
    if ok.sum() < 10 or x[ok].nunique() < 2:
        return float("nan")
    return float(np.polyfit(x[ok], y[ok], 1)[0])


def aircraft_scorecard(df: pd.DataFrame, min_n: int = 1) -> pd.DataFrame:
    """One row per aircraft over clean landings."""
    rows = []
    for ac, g in df.groupby("aircraft"):
        if len(g) < min_n:
            continue
        g = g.sort_values("time")
        recent = g.tail(10)["vs"].median()
        rows.append({
            "Aircraft": ac,
            "Class": g["ac_class"].iloc[0],
            "Landings": len(g),
            "Median VS": g["vs"].median(),
            "Best VS": g["vs"].max(),
            "Hardest VS": g["vs"].min(),
            "Spread (IQR)": g["vs"].quantile(0.75) - g["vs"].quantile(0.25),
            "Last 10 median": recent,
            "Trend /10 ldg": trend_slope(g["vs"]) * 10,
            "% Great+": (g["vs"] >= -250).mean() * 100,
            "% Hard": (g["vs"] < -350).mean() * 100,
            "Avg score": g["score"].mean(),
            "Median G": g["g"].median(),
            "Median float s": g["float_s"].median(),
            "% Early flare": (g["flare_grade"] == "Early").mean() * 100,
            "% Late flare": (g["flare_grade"] == "Late").mean() * 100,
            "Bounce %": g["bounced"].mean() * 100,
            "Last flown": g["time"].iloc[-1],
        })
    out = pd.DataFrame(rows)
    return out.sort_values("Landings", ascending=False).reset_index(drop=True) if not out.empty else out


def session_table(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    d = df.sort_values("time")
    g = d.groupby("session")
    out = pd.DataFrame({
        "Session": g.size().index,
        "Start": g["time"].first().to_numpy(),
        "Duration min": ((g["time"].last() - g["time"].first()).dt.total_seconds() / 60).to_numpy(),
        # most-flown type first, as value_counts() orders them
        "Aircraft": g["aircraft"].agg(lambda s: ", ".join(s.value_counts().index)).to_numpy(),
        "Landings": g.size().to_numpy(),
        "Median VS": g["vs"].median().to_numpy(),
        "Best VS": g["vs"].max().to_numpy(),
        "Hardest VS": g["vs"].min().to_numpy(),
        "First VS": g["vs"].first().to_numpy(),
        "Avg score": g["score"].mean().to_numpy(),
    })
    return out.sort_values("Start", ascending=False).reset_index(drop=True)


def percentile_in(value: float, ref: pd.Series) -> float:
    """% of reference landings that were HARDER than value (higher = better)."""
    ref = ref.dropna()
    return float((ref < value).mean() * 100) if len(ref) else float("nan")


# ----------------------------------------------------------------------
# X-Plane pilot logbook (Output/logbooks/X-Plane Pilot.txt)
#   2 YYMMDD DEP ARR landings total_h night_h ifr_h xc_h TAIL acf_file_name
# No time of day, so landings are matched by date + aircraft + order.
# ----------------------------------------------------------------------
# relative to the X-Plane folder (where LandingRate.log lives)
LOGBOOK_REL = Path("Output") / "logbooks" / "X-Plane Pilot.txt"
LOGBOOK_COLUMNS = ["date", "dep", "arr", "landings", "hours", "night_h", "ifr_h", "xc_h", "tail", "acf"]


def load_logbook(path: str | Path) -> pd.DataFrame:
    """One row per logbook flight, in file (= chronological) order; index is the flight id."""
    rows = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            p = line.split()
            if len(p) < 11 or p[0] != "2":
                continue
            try:
                rows.append([pd.to_datetime(p[1], format="%y%m%d"), p[2], p[3], int(p[4]), float(p[5]),
                             float(p[6]), float(p[7]), float(p[8]), p[9], " ".join(p[10:])])
            except ValueError:
                continue
    return pd.DataFrame(rows, columns=LOGBOOK_COLUMNS)


def _name_similarity(acf: str, icao: str) -> float:
    """How plausibly an .acf file name refers to an ICAO type (0..1)."""
    a = re.sub(r"[^A-Z0-9]", "", acf.upper())
    digits = re.sub(r"\D", "", icao)
    if len(digits) >= 3 and digits in a:
        return 1.0
    return difflib.SequenceMatcher(None, a, icao.upper()).ratio()


def _best_votes(votes: Counter) -> dict:
    """acf -> ICAO with the most votes; name similarity breaks ties."""
    best = {}
    for acf in {a for a, _ in votes}:
        options = [(n, _name_similarity(acf, icao), icao) for (a, icao), n in votes.items() if a == acf]
        best[acf] = max(options)[2]
    return best


def match_logbook(df: pd.DataFrame, flights: pd.DataFrame):
    """Link Lua landings (enriched df) to logbook flights.

    The logbook names aircraft by .acf file (Baron_58, C90B...), the Lua by ICAO, so the
    mapping is learned: first from days where each side flew a single type, then from
    Lua landings still unclaimed on the days an unmapped aircraft flew. Within a day and
    type, Lua landings fill each flight's landing count in order.
    Returns (df with flight columns, flights with reconciliation columns, {acf: ICAO}).
    """
    df = df.copy()
    for col in ("dep", "arr", "airport", "acf", "tail"):
        df[col] = ""
    for col in ("flight", "leg", "flight_hours"):
        df[col] = np.nan
    df["night"] = False
    df["imc"] = False
    fl = flights.copy()
    fl["icao"], fl["lua"], fl["status"] = "", 0, ""
    if df.empty or fl.empty:
        return df, fl, {}

    lua = df[df["aircraft"].str.strip() != ""]
    keys = pd.DataFrame({"sdate": lua.groupby("session")["time"].transform("min").dt.normalize(),
                         "icao": lua["aircraft"].str.upper()})
    cand = fl[(fl["landings"] > 0) & (fl["date"] >= keys["sdate"].min())]

    votes = Counter()
    icaos_by_day = keys.groupby("sdate")["icao"].unique()
    for d, acfs in cand.groupby("date")["acf"].unique().items():
        icaos = icaos_by_day.get(d)
        if icaos is not None and len(acfs) == 1 and len(icaos) == 1:
            votes[(acfs[0], icaos[0])] += 1
    mapping = _best_votes(votes)
    assigned: dict = {}

    def assign(subset):
        for (d, acf), g in subset.groupby(["date", "acf"], sort=False):
            icao = mapping.get(acf)
            if not icao:
                continue
            pool = [i for i in keys.index[(keys["sdate"] == d) & (keys["icao"] == icao)] if i not in assigned]
            pos = 0
            for fid, n in zip(g.index, g["landings"]):
                for i in pool[pos:pos + n]:
                    assigned[i] = fid
                pos += n

    assign(cand)
    votes = Counter()
    for _, f in cand[~cand["acf"].isin(list(mapping))].iterrows():
        left = keys[(keys["sdate"] == f["date"]) & ~keys.index.isin(list(assigned))]["icao"]
        for icao in left.unique():
            votes[(f["acf"], icao)] += 1
    # A guess may not take a type that's already mapped (or another guess's), unless the names agree.
    second, taken = {}, set(mapping.values())
    ranked = sorted(_best_votes(votes).items(),
                    key=lambda kv: (-votes[kv], -_name_similarity(*kv)))
    for acf, icao in ranked:
        if icao not in taken or _name_similarity(acf, icao) >= 0.6:
            second[acf] = icao
            taken.add(icao)
    mapping.update(second)
    assign(cand[cand["acf"].isin(list(second))])

    ids = pd.Series(assigned, dtype=float)
    df.loc[ids.index, "flight"] = ids.to_numpy()
    m = df["flight"].notna()
    f = fl.loc[df.loc[m, "flight"].astype(int)]
    for col in ("dep", "arr", "acf", "tail"):
        df.loc[m, col] = f[col].to_numpy()
    df.loc[m, "flight_hours"] = f["hours"].to_numpy()
    df.loc[m, "night"] = (f["night_h"] >= 0.5 * f["hours"]).to_numpy() & (f["night_h"] > 0).to_numpy()
    df.loc[m, "imc"] = (f["ifr_h"] > 0).to_numpy()
    df.loc[m, "leg"] = df[m].groupby("flight").cumcount() + 1
    legs = df[m].groupby("flight")["leg"].transform("max")
    circuit = df.loc[m, "dep"] == df.loc[m, "arr"]
    df.loc[m, "airport"] = np.where(circuit, df.loc[m, "dep"],
                                    np.where(df.loc[m, "leg"] == legs, df.loc[m, "arr"], ""))

    fl["icao"] = fl["acf"].map(mapping).fillna("")
    fl["lua"] = df[m].groupby("flight").size().reindex(fl.index, fill_value=0).astype(int)
    first = keys["sdate"].min()
    fl["status"] = np.select(
        [fl["landings"] == 0, fl["date"] < first, fl["icao"] == "", fl["lua"] == fl["landings"]],
        ["no landing", "before Lua log", "aircraft not matched", "match"],
        default="Lua missed " + (fl["landings"] - fl["lua"]).astype(str))
    return df, fl, mapping


def logbook_insights(df: pd.DataFrame, flights: pd.DataFrame | None = None) -> list[Insight]:
    """Findings that need the logbook: airports, day/night, cloud, circuits, missed landings."""
    out: list[Insight] = []
    if "airport" not in df or df["flight"].notna().sum() < 10:
        return out
    m = df[df["flight"].notna()]

    if flights is not None and len(flights) and len(df):
        rng = flights[(flights["date"] >= df["time"].min().normalize()) & (flights["date"] <= df["time"].max())]
        missed = rng[rng["status"].str.startswith("Lua missed")]
        n = int((missed["landings"] - missed["lua"]).sum())
        if n:
            none = int((missed["lua"] == 0).sum())
            out.append(Insight("info", f"{n} logbook landings were never recorded by the Lua",
                               f"Across {len(missed)} flights ({none} with nothing recorded at all). Possible causes: "
                               "the nose wheel never touched before lifting off (the Lua writes only once all "
                               "wheels are down), the sim was paused or in replay at touchdown (the Lua skips "
                               "those), a low bounce that X-Plane counts as a second landing, or FlyWithLua "
                               "wasn't running. See the Logbook tab."))

    by_apt = m[m["airport"] != ""].groupby("airport")["vs"].agg(["median", "count"])
    by_apt = by_apt[by_apt["count"] >= 5]
    if len(by_apt) >= 3:
        b, w = by_apt["median"].idxmax(), by_apt["median"].idxmin()
        out.append(Insight("info", f"Softest airport {b} · firmest {w}",
                           f"{b}: median {_fpm(by_apt.loc[b, 'median'])} ({int(by_apt.loc[b, 'count'])} landings); "
                           f"{w}: {_fpm(by_apt.loc[w, 'median'])} ({int(by_apt.loc[w, 'count'])}). "
                           "Airports with 5+ landings; see the Logbook tab."))

    def compare(mask, a_name, b_name, title_good, title_bad, min_n=10, min_d=20):
        a, b = m[mask]["vs"], m[~mask]["vs"]
        if len(a) >= min_n and len(b) >= min_n and abs(a.median() - b.median()) >= min_d:
            d = a.median() - b.median()
            out.append(Insight("info" if d > 0 else "warn", title_good if d > 0 else title_bad,
                               f"{a_name}: median {_fpm(a.median())} ({len(a)}); {b_name}: {_fpm(b.median())} "
                               f"({len(b)})."))

    compare(m["night"].astype(bool), "Night flights", "day flights",
            "You land softer at night", "Night landings are firmer")
    compare(m["imc"].astype(bool), "Flights with cloud (IFR) time", "clear flights",
            "Flights with cloud time end softer", "Flights with cloud time end firmer")
    circuit = (m["dep"] == m["arr"]) & (m.groupby("flight")["vs"].transform("size") > 1)
    compare(circuit, "Circuit / pattern landings", "landings after a trip",
            "Circuit practice is your soft spot", "Arrivals after a trip beat your circuit landings")
    arrivals = m[(m["dep"] != m["arr"]) & (m["airport"] != "")]
    if len(arrivals) >= 20:
        long_ = arrivals["flight_hours"] >= 1.5
        a, b = arrivals[long_]["vs"], arrivals[~long_]["vs"]
        if len(a) >= 8 and len(b) >= 8 and abs(a.median() - b.median()) >= 20:
            d = a.median() - b.median()
            out.append(Insight("info" if d > 0 else "warn",
                               "Long flights end softer" if d > 0 else "Long flights end in firmer landings",
                               f"After 1.5 h or more: median {_fpm(a.median())} ({len(a)}); shorter trips "
                               f"{_fpm(b.median())} ({len(b)})."))
    return out


# ----------------------------------------------------------------------
# Insights - plain-language findings
# ----------------------------------------------------------------------
@dataclass
class Insight:
    kind: str      # "good" | "warn" | "info"
    title: str
    text: str


def _fpm(v: float) -> str:
    return f"{v:+.0f} fpm" if np.isfinite(v) else "n/a"


def insights(df: pd.DataFrame, all_rows: pd.DataFrame | None = None, min_n: int = 8,
             flights: pd.DataFrame | None = None) -> list[Insight]:
    """Findings for a set of clean landings. all_rows (unfiltered by clean) adds data-quality notes;
    landings run through match_logbook() (plus its flights table) add logbook findings."""
    out: list[Insight] = []
    if len(df) < 3:
        return [Insight("info", "Not enough data", "Fewer than 3 landings match the current filters.")]
    df = df.sort_values("time")
    vs = df["vs"]

    # Recent form
    if len(df) >= 2 * 10:
        last, prior = df.tail(20), df.iloc[:-20]
        d = last["vs"].median() - prior["vs"].median()
        if abs(d) >= 15:
            kind = "good" if d > 0 else "warn"
            word = "softer" if d > 0 else "firmer"
            out.append(Insight(kind, f"Recent form: {abs(d):.0f} fpm {word}",
                               f"Your last 20 landings have a median of {_fpm(last['vs'].median())} vs "
                               f"{_fpm(prior['vs'].median())} before that. "
                               f"Great-or-better rate: {(last['vs'] >= -250).mean()*100:.0f}% recently vs "
                               f"{(prior['vs'] >= -250).mean()*100:.0f}% before."))
        else:
            out.append(Insight("info", "Recent form is steady",
                               f"Last 20 median {_fpm(last['vs'].median())}, within 15 fpm of your history."))

    # Consistency
    iqr = vs.quantile(0.75) - vs.quantile(0.25)
    out.append(Insight("info", f"Consistency: half your landings fall in a {iqr:.0f} fpm window",
                       f"Middle 50% between {_fpm(vs.quantile(0.25))} and {_fpm(vs.quantile(0.75))}. "
                       f"1 in 10 landings is harder than {_fpm(vs.quantile(0.10))}. Narrowing that tail "
                       f"usually matters more than chasing butter."))

    # Streaks
    cs, bs = current_streak(df), best_streak(df)
    if bs >= 3:
        title = (f"Streak: {cs} in a row at GREAT or better" if cs >= 2
                 else f"Best streak: {bs} in a row at GREAT or better")
        out.append(Insight("good" if cs >= 3 else "info", title,
                           f"Your best ever run is {bs} landings softer than -250 fpm; current run {cs}."))

    # Per-aircraft learning and comparison
    card = aircraft_scorecard(df, min_n=min_n)
    if len(card) >= 2:
        best = card.loc[card["Median VS"].idxmax()]
        worst = card.loc[card["Median VS"].idxmin()]
        out.append(Insight("info", f"Softest type: {best['Aircraft']} · firmest: {worst['Aircraft']}",
                           f"Median {_fpm(best['Median VS'])} in the {best['Aircraft']} ({best['Landings']} landings) "
                           f"vs {_fpm(worst['Median VS'])} in the {worst['Aircraft']} ({worst['Landings']}). "
                           f"(Types with at least {min_n} clean landings.)"))
    for _, r in card.iterrows():
        slope = r["Trend /10 ldg"]
        if np.isfinite(slope) and r["Landings"] >= 15 and abs(slope) >= 8:
            kind = "good" if slope > 0 else "warn"
            word = "improving" if slope > 0 else "getting firmer"
            out.append(Insight(kind, f"{r['Aircraft']}: {word} ({slope:+.0f} fpm per 10 landings)",
                               f"Trend across {r['Landings']} landings in type. Last 10 median "
                               f"{_fpm(r['Last 10 median'])} vs overall {_fpm(r['Median VS'])}."))

    # Flare tendency - per aircraft, and what it costs
    for ac, g in df.groupby("aircraft"):
        if len(g) < min_n:
            continue
        shares = g["flare_grade"].value_counts(normalize=True)
        for grade in ("Early", "Late"):
            share = shares.get(grade, 0)
            if share < 0.35:
                continue
            good = g[g["flare_grade"] == "Very good"]["vs"].median()
            bad = g[g["flare_grade"] == grade]["vs"].median()
            what = ("the nose was still rotating up as the wheels touched (Lua grades this \"early\")"
                    if grade == "Early" else
                    "the nose was already dropping as the wheels touched (Lua grades this \"late\")")
            if np.isfinite(good) and np.isfinite(bad) and bad < good - 15:
                hint = ("Aim to have the flare attitude set just before contact rather than still pulling."
                        if grade == "Early" else "Hold the flare attitude through touchdown.")
                out.append(Insight("warn", f"{ac}: {grade.lower()} flare in {share*100:.0f}% of landings",
                                   f"In those landings {what}. They median {_fpm(bad)} vs {_fpm(good)} "
                                   f"with a very good flare. {hint}"))
            else:
                out.append(Insight("info", f"{ac}: {grade.lower()} flare in {share*100:.0f}% of landings",
                                   f"In those landings {what}, but it isn't costing you: median "
                                   f"{_fpm(bad)} vs {_fpm(good)} with a very good flare."))

    # Float time vs VS
    f = df.dropna(subset=["float_s"])
    if len(f) >= 15:
        rho = spearman(f["float_s"], f["vs"])
        if abs(rho) >= 0.25:
            short = f[f["float_s"] <= f["float_s"].quantile(0.25)]["vs"].median()
            long_ = f[f["float_s"] >= f["float_s"].quantile(0.75)]["vs"].median()
            out.append(Insight("info", f"Float time matters (Spearman ρ = {rho:+.2f})",
                               f"Quickest 25% from 15 m to touchdown: median {_fpm(short)}; longest 25%: "
                               f"{_fpm(long_)}. "
                               + ("Taking more time in the flare is buying you softer landings - watch runway "
                                  "length though." if rho > 0 else
                                  "Longer floats are not making you softer - you may be holding off then dropping it in.")))

    # Warm-up effect
    first = df[df["session_idx"] == 1]["vs"]
    later = df[df["session_idx"] > 1]["vs"]
    if len(first) >= 5 and len(later) >= 5:
        d = later.median() - first.median()
        if abs(d) >= 20:
            out.append(Insight("warn" if d > 0 else "info",
                               "Rusty first landing" if d > 0 else "First landing of a session is your best",
                               f"First landing of a session: median {_fpm(first.median())}; later landings "
                               f"{_fpm(later.median())} ({len(first)} sessions)."))

    # Time of day
    by_hour = df.assign(block=pd.cut(df["hour"], [-1, 11, 17, 21, 23], labels=["Morning", "Afternoon",
                                                                              "Evening", "Late night"]))
    agg = by_hour.groupby("block", observed=True)["vs"].agg(["median", "count"])
    agg = agg[agg["count"] >= 10]
    if len(agg) >= 2 and agg["median"].max() - agg["median"].min() >= 30:
        b, w = agg["median"].idxmax(), agg["median"].idxmin()
        out.append(Insight("info", f"You land best in the {b.lower()}",
                           f"{b}: median {_fpm(agg.loc[b, 'median'])}; {w.lower()}: {_fpm(agg.loc[w, 'median'])}."))

    # G vs VS sanity (high G but soft VS = hard nose/second contact)
    weird = df[(df["vs"] >= -200) & (df["g"] >= 1.3)]
    if len(weird) >= 3:
        out.append(Insight("warn", f"{len(weird)} soft-rate landings with high G",
                           "Vertical speed was gentle but touchdown G was 1.3+. That usually means a firm "
                           "nose-wheel contact or a late, aggressive flare correction."))

    # Bounces
    br = df["bounced"].mean() * 100
    if br >= 3:
        out.append(Insight("warn", f"Bounce / re-touch rate {br:.0f}%",
                           "A second touchdown was logged within the re-touch window after these landings."))

    out.extend(logbook_insights(df, flights))

    # Data quality
    if all_rows is not None and len(all_rows):
        counts = {lbl: int(all_rows[c].sum()) for c, lbl in FLAG_LABELS.items() if all_rows[c].sum()}
        if counts:
            out.append(Insight("info", f"{len(all_rows) - int(all_rows['clean'].sum())} log entries excluded",
                               " · ".join(f"{k}: {v}" for k, v in counts.items())
                               + ". Toggle these in the sidebar."))
    return out


# ----------------------------------------------------------------------
# CLI report
# ----------------------------------------------------------------------
def _report(args) -> int:
    path = Path(args.log)
    if not path.exists():
        print(f"Log not found: {path}", file=sys.stderr)
        return 1
    raw, skipped = load_log(path)
    df = enrich(raw)
    flights = None
    if args.logbook is None:
        args.logbook = str(path.resolve().parent / LOGBOOK_REL)
    if args.logbook and Path(args.logbook).exists():
        df, flights, _ = match_logbook(df, load_logbook(args.logbook))
    sel = df[df["clean"]]
    if args.aircraft:
        sel = sel[sel["aircraft"].str.upper().isin([a.upper() for a in args.aircraft])]
    if args.since:
        sel = sel[sel["time"] >= pd.Timestamp(args.since)]

    print(f"{path}  -  {len(raw)} entries, {int(df['clean'].sum())} clean, {skipped} unparseable lines\n")
    s = summary(sel)
    if not s["n"]:
        print("No landings match.")
        return 0
    print(f"Landings {s['n']}   median {s['median_vs']:.0f} fpm   IQR {s['iqr_vs']:.0f}   "
          f"great+ {s['pct_great']:.0f}%   hard {s['pct_hard']:.0f}%   score {s['mean_score']:.0f}   "
          f"streak {s['current_streak']} (best {s['best_streak']})\n")
    if flights is not None:
        cand = flights[~flights["status"].isin(["no landing", "before Lua log"])]
        print(f"Logbook: {len(flights)} flights, {int((cand['status'] == 'match').sum())} of {len(cand)} since the "
              f"Lua log began match exactly; {int(df['flight'].notna().sum())} landings linked to a flight\n")
    print("Insights")
    for i in insights(sel, df, flights=flights):
        mark = {"good": "+", "warn": "!", "info": "-"}[i.kind]
        print(f" {mark} {i.title}\n     {i.text}")
    print("\nAircraft (clean landings)")
    card = aircraft_scorecard(sel)
    cols = ["Aircraft", "Landings", "Median VS", "Best VS", "Spread (IQR)", "Last 10 median",
            "Trend /10 ldg", "% Great+", "Avg score", "Bounce %"]
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        print(card[cols].to_string(index=False, float_format=lambda v: f"{v:.0f}"))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="landingrate report", description="Text report for LandingRate.lua logs")
    p.add_argument("log", nargs="?", default="LandingRate.log")
    p.add_argument("--aircraft", nargs="*", help="only these ICAO codes")
    p.add_argument("--since", help="only landings on/after this date (YYYY-MM-DD)")
    p.add_argument("--logbook", default=None,
                   help="X-Plane pilot logbook to match against (default: Output/logbooks/X-Plane Pilot.txt "
                        "next to the log; '' to skip)")
    return _report(p.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
