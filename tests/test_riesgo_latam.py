import pandas as pd

from pipeline.sources import riesgo


def test_latam_median_excludes_argentina_and_fills_short_gaps():
    days = pd.bdate_range("2026-08-24", periods=3)
    b = {c: pd.Series([100.0 + k] * 3, index=days) for k, c in enumerate(riesgo.LATAM)}
    b["ar"] = pd.Series([5000.0] * 3, index=days)
    b["pa"] = b["pa"].drop(days[1])          # missing for a day: keeps its last value
    m = riesgo.latam_median(b)
    assert list(m.index) == list(days)
    assert m.nunique() == 1 and m.iloc[0] == 104.5   # median of 100..109, Argentina left out


def test_latam_median_needs_enough_countries():
    days = pd.bdate_range("2010-01-04", periods=2)
    b = {c: pd.Series([100.0, 100.0], index=days) for c in riesgo.LATAM[: riesgo.MIN_LATAM - 1]}
    assert riesgo.latam_median(b).empty
