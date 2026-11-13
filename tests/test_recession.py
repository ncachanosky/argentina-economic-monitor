import numpy as np
import pandas as pd

from pipeline.sources import recession


def synthetic(seed=0):
    """A level series with known expansions and recessions (monthly growth +0.6% / -0.8%)."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2004-01-01", periods=240, freq="MS")
    regime = np.zeros(len(idx), dtype=int)
    for a, b in [(40, 52), (100, 115), (170, 182)]:
        regime[a:b] = 1
    g = np.where(regime == 1, -0.8, 0.6) + rng.normal(0, 0.35, len(idx))
    return pd.Series(100 * np.exp(np.cumsum(g) / 100), index=idx), pd.Series(regime, index=idx)


def test_bry_boschan_dates_the_known_recessions():
    level, regime = synthetic()
    turns = recession.bry_boschan(np.log(level))
    peaks = [d for d, s in turns if s == "P"]
    assert len(peaks) == 3
    for p, start in zip(peaks, [40, 100, 170]):
        assert abs(recession.months(level.index[start - 1], p)) <= 2


def test_markov_switching_finds_the_recession_regime_and_is_deterministic():
    level, regime = synthetic()
    y = recession.growth3(level)
    r, k = recession.fit_ms(y)
    sm = r.smoothed_marginal_probabilities[k]
    truth = regime.reindex(sm.index).astype(bool)
    assert ((sm > 0.5) == truth).mean() > 0.9
    assert r.params[f"const[{k}]"] < 0 < r.params[f"const[{1 - k}]"]
    r2, k2 = recession.fit_ms(y)
    assert np.allclose(r2.smoothed_marginal_probabilities[k2], sm)


def test_evaluate_counts_lags_and_false_alarms():
    idx = pd.date_range("2010-01-01", periods=36, freq="MS")
    rt = pd.Series(0.1, index=idx)
    rt.loc["2011-04-01":"2011-09-01"] = 0.9          # signal 3 months after a Jan 2011 peak, ends 3 after a Jul 2011 trough
    rt.loc["2012-06-01"] = 0.8                        # outside any recession
    turns = [(pd.Timestamp("2011-01-01"), "P"), (pd.Timestamp("2011-07-01"), "T"), (pd.Timestamp("2012-11-01"), "P")]
    ev = recession.evaluate(rt, turns)
    assert ev["entry"][0]["months"] == 3 and ev["exit"][0]["months"] == 3
    assert ev["false_alarm_months"] == ["2012-06"]


def test_evaluate_marks_a_missed_recession():
    idx = pd.date_range("2010-01-01", periods=48, freq="MS")
    rt = pd.Series(0.1, index=idx)
    turns = [(pd.Timestamp("2011-01-01"), "P"), (pd.Timestamp("2011-09-01"), "T")]
    ev = recession.evaluate(rt, turns)
    assert ev["entry"][0]["months"] is None and ev["missed"] == 1
