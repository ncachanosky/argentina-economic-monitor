from pathlib import Path

import pandas as pd

from pipeline.sources import riesgo as R

FIX = Path(__file__).parent / "fixtures" / "bcrd_embi.xlsx"


def test_bcrd_workbook_in_basis_points():
    out = R.parse_bcrd(FIX.read_bytes())
    assert set(R.BCRD_COLS) <= set(out)
    assert out["ar"][pd.Timestamp("2026-11-04")] == pytest_approx(846.433)
    assert out["br"].index.max() == pd.Timestamp("2026-11-04")
    assert (out["cl"].index.dayofweek < 5).all()


def test_uruguay_glitch_is_dropped():
    out = R.parse_bcrd(FIX.read_bytes())
    assert pd.Timestamp("2025-10-23") not in out["uy"].index
    assert pd.Timestamp("2025-10-22") in out["uy"].index


def test_drop_spikes_keeps_real_moves():
    s = pd.Series([100, 100, 150, 155, 160], index=pd.bdate_range("2026-01-05", periods=5))
    assert len(R.drop_spikes(s)) == 5          # a jump that stays is kept
    s2 = pd.Series([100, 100, 40, 101, 100], index=pd.bdate_range("2026-01-05", periods=5))
    assert pd.Timestamp("2026-01-07") not in R.drop_spikes(s2).index


def pytest_approx(x):
    import pytest
    return pytest.approx(x, abs=0.01)
