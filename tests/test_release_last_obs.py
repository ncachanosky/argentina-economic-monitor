import pandas as pd

from pipeline import export, registry


def test_tracker_follows_the_monthly_emae(monkeypatch):
    reg = registry.load()
    ind = next(i for i in reg.indicators if i.id == "gdp_tracker")
    wide = pd.DataFrame({"emae": [1.0, 2.0]}, index=pd.to_datetime(["2026-04-01", "2026-07-01"]))
    monthly = pd.Series([1.0, 2.0], index=pd.to_datetime(["2026-07-01", "2026-08-01"]))
    monkeypatch.setattr(export.store, "as_of", lambda sid, *a, **k: monthly)
    assert export.release_last_obs(ind, wide) == "2026-08-01"


def test_other_cards_use_their_own_last_date():
    reg = registry.load()
    ind = next(i for i in reg.indicators if i.id == "cpi")
    wide = pd.DataFrame({"x": [1.0]}, index=pd.to_datetime(["2026-09-01"]))
    assert export.release_last_obs(ind, wide) == "2026-09-01"
