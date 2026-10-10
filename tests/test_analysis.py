import pandas as pd

from landingrate import analysis as lra
from landingrate import sample


def load(tmp_path, n=200):
    raw, skipped = lra.load_log(sample.write_log(tmp_path / "LandingRate.log", n=n))
    return raw, skipped


def test_load_log_parses_every_line(tmp_path):
    raw, skipped = load(tmp_path)
    assert skipped == 0
    assert len(raw) == len(sample.lines(200))
    assert list(raw.columns[:len(lra.COLUMNS)]) == lra.COLUMNS


def test_vr_format_line(tmp_path):
    p = tmp_path / "vr.log"
    p.write_text('2025-03-01 10:00:00,B738,"Vertical Speed: -142.5FPM / 1.08G","Very good flare",'
                 '"Nose: -1.2 deg/sec | Float: 6.4 secs","GREAT","0.3,0.02"\n')
    raw, skipped = lra.load_log(p)
    assert skipped == 0 and len(raw) == 1
    assert raw["vs"].iloc[0] == -142.5


def test_rating_bands_match_lua():
    assert lra.rating_for(-100) == "BUTTER"
    assert lra.rating_for(-200) == "GREAT"
    assert lra.rating_for(-300) == "ACCEPTABLE"
    assert lra.rating_for(-500) == "HARD"
    assert lra.rating_for(-700) == "WASTED"


def test_enrich_flags_crash_and_retouch(tmp_path):
    raw, _ = load(tmp_path)
    df = lra.enrich(raw)
    assert (df["vs"] < -1000).sum() >= 1
    assert not df.loc[df["vs"] < -1000, "clean"].any()
    assert df["clean"].sum() < len(df)
    assert df["score"].between(0, 100).all()


def test_session_table(tmp_path):
    raw, _ = load(tmp_path)
    df = lra.enrich(raw)
    st = lra.session_table(df[df["clean"]])
    assert st["Landings"].sum() == df["clean"].sum()
    assert st["Start"].is_monotonic_decreasing
    assert lra.session_table(pd.DataFrame()).empty


def test_report_runs(tmp_path, capsys):
    log = sample.write_log(tmp_path / "LandingRate.log")
    assert lra.main([str(log)]) == 0
    assert "Insights" in capsys.readouterr().out


def test_spearman_matches_pandas():
    import numpy as np
    pytest = __import__("pytest")
    pytest.importorskip("scipy")
    r = np.random.default_rng(1)
    a = pd.Series(r.normal(size=200)).round(1)  # ties
    b = a * 0.4 + pd.Series(r.normal(size=200))
    a[3] = np.nan
    assert abs(lra.spearman(a, b) - a.corr(b, method="spearman")) < 1e-12
