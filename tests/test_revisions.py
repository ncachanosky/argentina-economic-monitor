import pandas as pd

from pipeline.sources import revisions


def test_first_print_latest_and_path():
    idx = pd.date_range("2016-01-01", periods=8, freq="QS")
    v1 = pd.DataFrame({"sa": [100, 101, 102, 103, 104, 105, None, None], "orig": range(8)}, index=idx).dropna()
    v2 = pd.DataFrame({"sa": [100, 101, 102, 103, 104, 106, 107, None], "orig": range(8)}, index=idx).dropna()
    v3 = pd.DataFrame({"sa": [100, 101, 102, 103, 104, 106, 106.5, 108], "orig": range(8)}, index=idx)
    t = revisions.table([("2016-09", "2016-09-20", v1), ("2016-12", "2016-12-20", v2), ("2017-03", "2017-03-20", v3)], 1, 4, "gdp")
    rows = {r["period"]: r for r in t["measures"]["short"]["rows"]}
    q3 = rows["2017-07-01"]                      # first published in Dec 2016 at +0.9%, today +0.5%
    assert q3["first"] == 0.9 and q3["latest"] == 0.5 and q3["first_release"] == "2016-12"
    assert t["measures"]["short"]["paths"]["2017-07-01"] == [["2016-12-20", 0.9], ["2017-03-20", 0.5]]
    assert t["latest"] == "2017-10-01"


def test_archives_load():
    g = revisions.gdp_vintages()
    assert len(g) >= 41 and g[0][0] == "2016-09"
    e = revisions.emae_vintages()
    assert len(e) >= 121


def test_tracker_error_band_narrows_with_months_published():
    b = revisions.tracker_errors()
    assert set(b) == {1, 2, 3}
    assert b[1]["p68"] >= b[2]["p68"] > b[3]["p68"] > 0
