import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from landingrate import app  # noqa: E402


def test_self_test_draws_every_tab():
    assert app.self_test() == 0


def test_frame_model_formats_and_sorts():
    import numpy as np
    import pandas as pd
    m = app.FrameModel({"G": "{:.2f}"})
    m.set_frame(pd.DataFrame({"G": [1.234, np.nan], "Name": ["a", None],
                              "When": pd.to_datetime(["2025-01-02 03:04", None])}))
    idx = m.index
    assert m.data(idx(0, 0)) == "1.23"
    assert m.data(idx(1, 0)) == "–"
    assert m.data(idx(1, 1)) == "–"
    assert m.data(idx(0, 2)) == "2025-01-02 03:04"
    assert m.data(idx(1, 0), app.Qt.UserRole) == -1e18
