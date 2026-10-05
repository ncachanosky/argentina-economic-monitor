import json

import pandas as pd
import pytest

from pipeline import registry, store, validate
from pipeline.sources import datos_gob


def monthly(values, start="2020-01-01"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="MS"), dtype=float)


# --- registry -------------------------------------------------------------

def test_registry_loads_and_ids_unique():
    reg = registry.load()
    assert {i.id for i in reg.indicators} >= {"emae", "emae_sectors", "ipi", "isac"}
    # A source id may feed several indicators, but always at the same frequency.
    freq = {}
    for i in reg.indicators:
        for v in i.variants.values():
            assert freq.setdefault(v.source_id, i.frequency) == i.frequency


def test_registry_rejects_bad_default(tmp_path):
    p = tmp_path / "r.yaml"
    p.write_text("""
topics: [{id: t, title: T}]
indicators:
  - {id: x, topic: t, kind: variants, title: X, source: s, frequency: M,
     variants: {a: {label: A, id: "1"}}, default: {variant: b}}
""")
    with pytest.raises(registry.RegistryError):
        registry.load(p)


# --- vintage store --------------------------------------------------------

def test_store_writes_only_deltas_and_reconstructs_vintages(tmp_path):
    sid = "TEST_1"
    r1 = store.append(sid, monthly([100, 101, 102]), "2026-01-10", base=tmp_path)
    assert (r1.new_obs, r1.revised_obs) == (3, 0)

    r2 = store.append(sid, monthly([100, 101, 102]), "2026-01-11", base=tmp_path)
    assert not r2.changed  # identical data writes nothing

    r3 = store.append(sid, monthly([100, 101.5, 102, 103]), "2026-02-10", base=tmp_path)
    assert (r3.new_obs, r3.revised_obs) == (1, 1)

    assert len(store.load(sid, base=tmp_path)) == 5
    assert store.as_of(sid, "2026-01-31", base=tmp_path).tolist() == [100, 101, 102]
    assert store.as_of(sid, base=tmp_path).tolist() == [100, 101.5, 102, 103]
    assert store.vintages(sid, base=tmp_path) == ["2026-01-10", "2026-02-10"]


def test_store_same_day_rerun_replaces(tmp_path):
    sid = "TEST_2"
    store.append(sid, monthly([100, 101]), "2026-01-10", base=tmp_path)
    store.append(sid, monthly([100, 101, 105]), "2026-02-10", base=tmp_path)
    store.append(sid, monthly([100, 101, 104]), "2026-02-10", base=tmp_path)
    df = store.load(sid, base=tmp_path)
    assert len(df) == 3
    assert store.as_of(sid, base=tmp_path).iloc[-1] == 104


def test_store_full_reconciliation_every_run(tmp_path):
    """Each run must leave the latest vintage identical to what the source sent:
    every NSA value revised, a new month added, a dropped month removed."""
    sid = "TEST_3"
    first = monthly([100, 101, 102, 103, 104])
    store.append(sid, first, "2026-01-10", base=tmp_path)

    # Next release: all history revised (as with NSA and SA re-estimation),
    # one new month, and the first month no longer published.
    second = monthly([101.2, 102.3, 103.1, 104.4, 105.0], start="2020-02-01")
    r = store.append(sid, second, "2026-02-10", base=tmp_path)
    assert (r.new_obs, r.revised_obs, r.deleted_obs) == (1, 4, 1)

    latest = store.as_of(sid, base=tmp_path)
    pd.testing.assert_series_equal(latest, second, check_names=False, check_freq=False)
    # ...and the earlier vintage is still recoverable exactly.
    pd.testing.assert_series_equal(store.as_of(sid, "2026-01-31", base=tmp_path), first,
                                   check_names=False, check_freq=False)


# --- validation -----------------------------------------------------------

def test_validate_rejects_gaps_shrinks_and_empty():
    old = monthly(range(100, 130))
    gap = old.drop(old.index[10])
    assert not validate.check_series(gap, pd.Series(dtype=float)).ok
    assert not validate.check_series(old.iloc[:-1], old).ok           # latest date went back
    assert not validate.check_series(pd.Series(dtype=float), old).ok  # empty
    assert validate.check_series(monthly(range(100, 131)), old).ok


def test_validate_warns_on_large_revision_and_jump():
    old = monthly([100 + 0.5 * i for i in range(36)])
    new = old.copy()
    new.iloc[5] *= 1.10
    new = pd.concat([new, monthly([new.iloc[-1] * 1.30], start=new.index[-1] + pd.offsets.MonthBegin())])
    chk = validate.check_series(new, old)
    assert chk.ok
    assert any("revised" in w for w in chk.warnings)
    assert any("moves" in w for w in chk.warnings)


def test_is_stale():
    assert validate.is_stale(pd.Timestamp("2026-01-01"), pd.Timestamp("2026-10-05"))
    assert not validate.is_stale(pd.Timestamp("2026-07-01"), pd.Timestamp("2026-10-05"))


# --- datos.gob.ar adapter (no network) ------------------------------------

class FakeResp:
    def __init__(self, payload, status=200):
        self.payload, self.status_code, self.text = payload, status, json.dumps(payload)

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def test_datos_gob_parses_paginates_and_isolates_bad_ids(monkeypatch):
    monkeypatch.setattr(datos_gob, "PAGE_SIZE", 2)
    rows = [["2020-01-01", 1.0, 10.0], ["2020-02-01", 2.0, None], ["2020-03-01", 3.0, 30.0]]

    def fake_get(self, url, params=None, timeout=None):
        ids = params["ids"].split(",")
        if "BAD" in ids:
            return FakeResp({"errors": [{"error": "invalid id BAD"}]}, status=400)
        cols = {"A": 1, "B": 2}
        data = [[r[0], *[r[cols[i]] for i in ids]] for r in rows]
        start, limit = params["start"], params["limit"]
        meta = [{}] + [{"field": {"id": i, "units": "idx", "time_index_end": "2020-03-01"}} for i in ids]
        return FakeResp({"data": data[start:start + limit], "count": len(data), "meta": meta})

    monkeypatch.setattr(datos_gob.requests.Session, "get", fake_get)
    res = datos_gob.fetch(["A", "B", "BAD"])
    assert set(res.errors) == {"BAD"}
    a = res.data[res.data.series_id == "A"].set_index("date")["value"]
    b = res.data[res.data.series_id == "B"].set_index("date")["value"]
    assert a.tolist() == [1.0, 2.0, 3.0]
    assert b.tolist() == [10.0, 30.0]  # nulls dropped
    assert res.meta["A"]["units"] == "idx"


def test_registry_presidencies_ordered_and_non_overlapping():
    reg = registry.load()
    ids = [p["id"] for p in reg.presidencies]
    assert ids[-1] == "milei" and reg.presidencies[-1]["end"] is None
    for a, b in zip(reg.presidencies, reg.presidencies[1:]):
        assert a["end"] == b["start"]


def test_next_release_picks_first_unpublished_period():
    reg = registry.load()
    emae = next(i for i in reg.indicators if i.id == "emae")
    nr = reg.next_release(emae, "2026-07-01")
    assert nr["period"] == "2026-08" and nr["date"] == "2026-10-21"
    # A release date that has passed but whose data hasn't landed is still "next".
    assert reg.next_release(emae, "2026-06-01")["period"] == "2026-07"
    assert reg.next_release(emae, "2026-10-01") is None  # calendar exhausted


def test_validate_quarterly():
    q = pd.Series([100.0, 101, 102, 103, 104], index=pd.date_range("2024-01-01", periods=5, freq="QS"))
    assert validate.check_series(q, pd.Series(dtype=float), "Q").ok
    assert not validate.check_series(q.drop(q.index[2]), pd.Series(dtype=float), "Q").ok
    flow = pd.Series([-5.0, 3, -1, 2, 0.5], index=q.index)
    assert validate.check_series(flow, pd.Series(dtype=float), "Q", "flow").ok


def test_contributions_add_up_to_total():
    from pipeline.export import contributions
    reg = registry.load()
    ind = next(i for i in reg.indicators if i.id == "gdp_demand")
    idx = pd.date_range("2020-01-01", periods=12, freq="QS")
    import numpy as np
    rng = np.random.default_rng(0)
    cols = {}
    for k in ["c", "g", "i", "x", "m", "ve", "de"]:
        cols[ind.variants[k].source_id] = pd.Series(rng.uniform(50, 150, 12), index=idx)
    v = {k: cols[ind.variants[k].source_id] for k in ["c", "g", "i", "x", "m", "ve", "de"]}
    cols[ind.variants["gdp"].source_id] = v["c"] + v["g"] + v["i"] + v["x"] - v["m"] + v["ve"] + v["de"] + 3
    out = contributions(ind, pd.DataFrame(cols))
    tot = np.array(out["total"]["values"])
    s = np.sum([g["values"] for g in out["groups"]], axis=0)
    assert np.allclose(tot, s, atol=1e-4)
    assert len(out["dates"]) == 8
