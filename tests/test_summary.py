import pandas as pd

from pipeline import summary as sm

PRES = [{"id": "a", "short": "A", "start": "2019-12-10", "end": "2023-12-10"}, {"id": "b", "short": "B", "start": "2023-12-10", "end": None}]


def _m(values, start="2024-01-01"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="MS"), dtype=float)


def test_flow_total_latest_and_year_to_date():
    y = _m([100.0] * 12 + [200.0] * 10)   # Jan 2024 .. Oct 2025
    p = {"frequency": "M", "measure": "flow", "units": "Millions of US dollars", "last_obs": "2025-10-01"}
    it = sm.summarize(p, y, units=p["units"], start=pd.Timestamp("2024-01-01"), presidencies=PRES)
    assert it[0].big == "US$ 3,200 m" and "22 months" in it[0].small
    assert it[1].big == "US$ 200 m" and "vs. US$ 100 m a year earlier (+100%)" in it[1].small
    assert it[2].big == "US$ 200 m" and "Jan–Oct 2025 (US$ 100 m in 2024)" in it[2].small


def test_monthly_rate_twelve_months_and_geometric_term_average():
    lv = _m([100 * 1.02 ** k for k in range(30)])
    y = (lv / lv.shift(1) - 1) * 100
    p = {"frequency": "M", "measure": "index", "units": "Index", "last_obs": str(lv.index[-1].date())}
    it = sm.summarize(p, y, units="Index", tkey="mom", change=True, levels=lv, presidencies=PRES)
    assert it[0].big == "+2.0%"
    assert it[1].big == "+26.8%"                      # 1.02^12
    assert it[2].big == "+2.0%" and "geometric" in it[2].small


def test_level_changes_since_start_and_term():
    y = _m([100.0, 110.0, 121.0], start="2023-11-01")   # base month of term B is Dec 2023
    p = {"frequency": "M", "measure": "index", "units": "Index, 2004 = 100", "last_obs": "2024-01-01"}
    it = sm.summarize(p, y, units=p["units"], start=pd.Timestamp("2023-11-01"), presidencies=PRES)
    assert it[1].big == "+21%" and "since Nov 2023 (100)" in it[1].small
    assert it[2].big == "+10%" and "since B took office (110 in Dec 2023)" in it[2].small


def test_two_quarter_rule_calendar_and_rolling():
    # Flat, then two falling quarters (Q2 and Q3), then flat again at the lower level.
    vals = [100] * 3 + [99] * 3 + [98] * 3 + [98] * 3
    s = _m(vals, start="2025-01-01")
    cal = sm.two_quarter_spells(s, "calendar")
    assert [(x["start"].strftime("%Y-%m"), x["end"].strftime("%Y-%m")) for x in cal] == [("2025-04", "2025-09")]
    rol = sm.two_quarter_spells(s, "rolling")
    # Rolling: Sep passes (Jul-Sep < Apr-Jun < Jan-Mar); months Apr..Sep shaded, as one spell.
    assert rol and rol[0]["start"] <= pd.Timestamp("2025-04-01") and rol[0]["end"] >= pd.Timestamp("2025-09-01")
    st = sm.spell_stats(s, cal, "calendar")[0]
    assert round(st["depth"], 1) == -2.0 and st["back"] is None and not st["ongoing"]


def test_spell_recovery():
    vals = [100] * 3 + [99] * 3 + [98] * 3 + [101] * 3
    s = _m(vals, start="2025-01-01")
    st = sm.spell_stats(s, sm.two_quarter_spells(s, "calendar"), "calendar")[0]
    assert st["back"] == pd.Timestamp("2025-12-01") and st["recovery_months"] == 3


def test_compare_methods_table():
    vals = [100] * 12 + [99] * 3 + [98] * 3 + [98] * 3 + [100] * 12
    sa = _m(vals, start="2020-01-01")
    prob = pd.Series([90.0 if pd.Timestamp("2021-01-01") <= d <= pd.Timestamp("2021-06-01") else 5.0 for d in sa.index], index=sa.index)
    bb = [{"start": "2021-01-01", "end": "2021-06-01"}]
    r = sm.compare_methods(sa, prob, bb, None)
    rows = {x["key"]: x for x in r["table"]}
    assert rows["bb"]["agree"] == 100.0
    assert rows["calendar"]["dated_found"] == 1 and rows["markov"]["dated_found"] == 1
    assert rows["markov"]["median_start"] == 0.0
