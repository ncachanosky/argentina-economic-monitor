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
topics: [{id: t, slug: t, title: T}]
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
        assert a["end"] <= b["start"]   # interim presidents may sit in between


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


def test_registry_flow_maps_have_no_stray_keys():
    """An unquoted comma in a YAML flow map ({label: A, b, id: x}) silently
    truncates the label and adds a stray key; catch that."""
    import yaml
    raw = yaml.safe_load(registry.REGISTRY_PATH.read_text(encoding="utf-8"))
    for ind in raw["indicators"]:
        for k, v in (ind.get("variants") or {}).items():
            assert set(v) <= {"label", "id"}, (ind["id"], k, v)
        for g in (ind.get("contributions") or {}).get("groups", []):
            assert set(g) <= {"key", "label", "color", "add", "subtract", "residual"}, (ind["id"], g)


def test_series_grouped_by_frequency():
    """Monthly and quarterly ids must never share an API request."""
    reg = registry.load()
    meta = reg.series_meta()
    for (source, f), ids in reg.series_by_source().items():
        assert all(meta[sid]["frequency"] == f for sid in ids)
    # Derived indicators are never fetched; their inputs are.
    assert not any(src == "derived" for src, _ in reg.series_by_source())
    fetched = {sid for ids in reg.series_by_source().values() for sid in ids}
    for ind in reg.indicators:
        if ind.derive:
            # "@indicator:variant" reads another derived indicator, not a fetched series.
            assert {i for i in ind.input_ids() if not i.startswith("@")} <= fetched, ind.id


# --- manual source and derived indicators ---------------------------------

def test_manual_adapter_and_composite_file(tmp_path):
    from pipeline.sources import manual
    (tmp_path / "x.csv").write_text("date,value\n2020-01-01,1.5\n2020-02-01,\n2020-03-01,2\n")
    res = manual.fetch(["x", "missing"], base=tmp_path)
    assert res.data["value"].tolist() == [1.5, 2.0] and set(res.errors) == {"missing"}
    # The committed composite is a gap-free monthly index.
    comp = manual.fetch(["cpi_private_composite"]).data.set_index("date")["value"]
    assert validate.check_series(comp, pd.Series(dtype=float)).ok
    assert comp.index.min() <= pd.Timestamp("2007-01-01") and comp.index.max() >= pd.Timestamp("2016-04-01")


def _splice_ind(spec):
    from pipeline.registry import Indicator, Variant
    return Indicator(id="t", topic="t", kind="variants", title="t", short_title="t", description="",
                     source="derived", source_label="", frequency="M", units="",
                     variants={k: Variant(k, k, f"t:{k}") for k in spec["variants"]}, default={}, derive=spec)


def test_splice_chains_anchors_aligns_and_leaves_gaps():
    from pipeline import derive
    a = monthly([100, 110, 121, 133.1, 146.41, 161.051])          # +10%/month, Jan-Jun 2020
    b = monthly([1.0, 1.0, 1.0], start="2020-02-01")               # +1%/month (as m/m %), Feb-Apr
    c = monthly([50, 55, 60.5], start="2020-04-01")                # +10%/month, Apr-Jun
    spec = {"method": "splice", "anchor": {"id": "A", "date": "2020-06-01"}, "variants": {
        "corr": {"segments": [{"id": "A", "as": "index"}]},
        "off": {"fill_gaps_from": "corr", "align": {"variant": "corr", "date": "2020-01-01"},
                "segments": [{"id": "A", "as": "index", "to": "2020-01-01"},
                             {"id": "B", "as": "mom", "from": "2020-02-01", "to": "2020-03-01"},
                             {"id": "C", "as": "index", "from": "2020-05-01"}]}}}
    df, info = derive.splice(_splice_ind(spec), {"A": a, "B": b, "C": c})
    corr, off = df["t:corr"], df["t:off"]
    assert corr.iloc[-1] == pytest.approx(161.051)                 # anchored
    assert off.iloc[0] == pytest.approx(corr.iloc[0])              # aligned at the start
    assert off["2020-03-01"] / off["2020-01-01"] == pytest.approx(1.01 ** 2)
    assert pd.isna(off["2020-04-01"])                              # gap month left empty...
    assert info["gaps"] == {"off": [["2020-04-01", "2020-04-01"]]}
    # ...but the level carries on with the corrected series' change through it.
    assert off["2020-05-01"] / off["2020-03-01"] == pytest.approx(1.1 * 1.1)


def test_reweight_with_unchanged_weights_reproduces_official(tmp_path, monkeypatch):
    import numpy as np
    from pipeline import derive, export
    monkeypatch.setattr(store, "VINTAGE_DIR", tmp_path)
    reg = registry.load()
    div = next(i for i in reg.indicators if i.id == "cpi_divisions")
    ind = next(i for i in reg.indicators if i.id == "cpi_newbasket")
    rng = np.random.default_rng(1)
    idx = pd.date_range("2016-12-01", "2026-06-01", freq="MS")
    w = pd.Series(rng.uniform(1, 10, len(div.variants)), index=list(div.variants))
    w = w / w.sum() * 100
    comps = {}
    for k, v in div.variants.items():
        s = pd.Series(100 * np.cumprod(1 + rng.uniform(0, 0.06, len(idx))), index=idx)
        s.iloc[0] = 100
        comps[k] = s
        store.append(v.source_id, s, "2026-07-01")
    head = sum(w[k] / 100 * comps[k] for k in comps)
    store.append(ind.derive["headline"], head, "2026-07-01")
    # New weights equal to the old basket at base-period prices, price-updated the same way.
    spec = {**ind.derive, "new_weights": w.to_dict(), "new_weights_reference": ["2016-12-01", "2016-12-01"]}
    monkeypatch.setattr(ind, "derive", spec)
    df, info = derive.reweight(ind, reg, {ind.derive["headline"]: store.as_of(ind.derive["headline"])})
    pd.testing.assert_series_equal(df.iloc[:, 0], df.iloc[:, 1], check_names=False, rtol=1e-6)
    assert info["replication_max_error_pct"] < 1e-4


def test_datos_gob_pages_past_misleading_count(monkeypatch):
    """The API's "count" is the first series' length, not the row count."""
    monkeypatch.setattr(datos_gob, "PAGE_SIZE", 2)
    dates = [f"2020-{m:02d}-01" for m in range(1, 6)]

    def fake_get(self, url, params=None, timeout=None):
        data = [[d, None if i < 3 else 1.0, float(i)] for i, d in enumerate(dates)]
        start, limit = params["start"], params["limit"]
        meta = [{}] + [{"field": {"id": i}} for i in params["ids"].split(",")]
        return FakeResp({"data": data[start:start + limit], "count": 2, "meta": meta})

    monkeypatch.setattr(datos_gob.requests.Session, "get", fake_get)
    res = datos_gob.fetch(["A", "B"])
    assert res.data[res.data.series_id == "B"]["value"].tolist() == [0.0, 1.0, 2.0, 3.0, 4.0]


# --- daily data and the BCRA adapter --------------------------------------

def test_bcra_adapter_pages_and_normalizes_monthly_dates(monkeypatch):
    from pipeline.sources import bcra
    monkeypatch.setattr(bcra, "PAGE_SIZE", 2)
    daily = [{"fecha": f"2026-09-{d:02d}", "valor": float(d)} for d in (30, 29, 26, 25, 24)]
    monthly = [{"fecha": d, "valor": v} for d, v in (("2026-08-31", 21.0), ("2026-07-31", 22.0), ("2026-06-30", 23.0))]

    def fake_get(self, url, params=None, timeout=None):
        rows = daily if url.endswith("/15") else monthly
        o, l = params["offset"], params["limit"]
        return FakeResp({"status": 200, "metadata": {"resultset": {"count": len(rows), "offset": o, "limit": l}},
                         "results": [{"idVariable": 1, "detalle": rows[o:o + l]}]})

    monkeypatch.setattr(bcra.requests.Session, "get", fake_get)
    monkeypatch.setattr(bcra.time, "sleep", lambda s: None)
    res = bcra.fetch(["bcra:15", "bcra:29"])
    d = res.data[res.data.series_id == "bcra:15"].set_index("date")["value"]
    assert d.tolist() == [24.0, 25.0, 26.0, 29.0, 30.0]          # all pages, oldest first
    m = res.data[res.data.series_id == "bcra:29"].set_index("date")["value"]
    assert list(m.index.strftime("%Y-%m-%d")) == ["2026-06-01", "2026-07-01", "2026-08-01"]
    assert validate.check_series(d, pd.Series(dtype=float), "D").ok


def test_daily_validation_allows_weekends_and_staleness():
    idx = pd.bdate_range("2026-01-01", "2026-03-31")
    s = pd.Series(range(1, len(idx) + 1), index=idx, dtype=float)
    assert validate.check_series(s, pd.Series(dtype=float), "D").ok
    assert validate.is_stale(pd.Timestamp("2026-09-20"), pd.Timestamp("2026-10-05"), "D")
    assert not validate.is_stale(pd.Timestamp("2026-10-01"), pd.Timestamp("2026-10-05"), "D")


def test_monthly_and_flows_from_daily():
    from pipeline import derive
    from pipeline.registry import Indicator, Variant
    idx = pd.bdate_range("2026-01-01", "2026-03-31")
    stock = pd.Series(100.0, index=idx)
    flow = pd.Series(0.0, index=idx)
    flow[pd.Timestamp("2026-02-02")] = 10.0          # +10 in February
    stock[stock.index >= "2026-02-02"] = 110.0
    def ind(method, variants, **kw):
        return Indicator(id="t", topic="t", kind="variants", title="t", short_title="t", description="",
                         source="derived", source_label="", frequency="M", units="",
                         variants={k: Variant(k, k, f"t:{k}") for k in variants}, default={},
                         derive={"method": method, "variants": variants, **kw})
    frames = {"S": stock, "F": flow}
    df, _ = derive.monthly(ind("monthly", {"avg": {"sum_of": ["S"]}, "eom": {"sum_of": ["S"], "how": "last"}}), frames)
    assert df.loc["2026-02-01", "t:eom"] == 110 and df.loc["2026-01-01", "t:avg"] == 100
    df, info = derive.flows(ind("flows", {"f": {"sum_of": ["F"]}}, stock="S"), frames)
    assert df.loc["2026-02-01", "t:f"] == pytest.approx(10.0)    # pp of January's closing stock
    assert info["stock_change_pct"]["2026-02-01"] == pytest.approx(10.0)


def test_formula_derivation_is_safe_and_anchored():
    from pipeline import derive
    from pipeline.registry import Indicator, Variant
    idx = pd.date_range("2026-01-01", periods=3, freq="MS")
    frames = {"A": pd.Series([10.0, 20.0, None], index=idx), "B": pd.Series([5.0, None, 5.0], index=idx)}
    def ind(expr):
        return Indicator(id="t", topic="t", kind="variants", title="t", short_title="t", description="",
                         source="derived", source_label="", frequency="M", units="",
                         variants={"x": Variant("x", "x", "t:x")}, default={},
                         derive={"method": "formula", "anchor": "a", "vars": {"a": "A", "b": "B"},
                                 "variants": {"x": {"expr": expr}}})
    df, _ = derive.formula(ind("100 * (a + nz(b)) / a"), frames)
    assert df["t:x"].tolist() == [150.0, 100.0]          # March dropped: anchor missing
    with pytest.raises(ValueError):
        derive.formula(ind("__import__('os')"), frames)


def test_formula_bounds_and_positive_only():
    from pipeline import derive
    from pipeline.registry import Indicator, Variant
    idx = pd.date_range("2026-01-01", periods=3, freq="MS")
    frames = {"A": pd.Series([100.0, 100.0, 100.0], index=idx), "N": pd.Series([50.0, -5.0, 5.0], index=idx)}
    ind = Indicator(id="t", topic="t", kind="variants", title="t", short_title="t", description="",
                    source="derived", source_label="", frequency="M", units="",
                    variants={"x": Variant("x", "x", "t:x")}, default={},
                    derive={"method": "formula", "anchor": "a", "vars": {"a": "A", "n": "N"},
                            "variants": {"x": {"expr": "a / n", "positive_only": True, "max": "10"}}})
    df, _ = derive.formula(ind, frames)
    assert df["t:x"].tolist() == [2.0]          # Feb negative, Mar above max: blank (and dropped)


def test_manual_adapter_reads_named_column(tmp_path):
    from pipeline.sources import manual
    (tmp_path / "r.csv").write_text("date,value,short,source\n2016-01-29,5000,5000,x\n2025-01-03,1000,0,y\n")
    res = manual.fetch(["r", "r#short"], base=tmp_path)
    assert not res.errors
    got = res.data.pivot(index="date", columns="series_id", values="value")
    assert got["r"].tolist() == [5000, 1000] and got["r#short"].tolist() == [5000, 0]


def test_net_reserves_conventions_and_breakdown():
    from pipeline import derive
    from pipeline.registry import Indicator, Variant
    w = pd.to_datetime(["2025-12-23", "2025-12-31", "2026-01-07", "2026-01-15", "2026-01-23", "2026-01-31"])
    s = lambda v: pd.Series(v, index=w, dtype=float)
    frames = {
        "g": s([40000] * 6), "e": s([10000] * 6), "o": s([100] * 6), "au": s([8000] * 6),
        "rl": s([3500, 3500, 3500, 6500, 6500, 6500]), "fx": s([1450] * 6),
        "cny": pd.Series([130.0], index=pd.to_datetime(["2018-12-17"])),
        "rate": pd.Series([0.14, 0.15], index=pd.to_datetime(["2025-12-01", "2026-01-10"])),
        "rp": pd.Series([3000.0, 6000.0], index=pd.to_datetime(["2025-06-11", "2026-01-07"])),
        "rps": pd.Series([0.0], index=pd.to_datetime(["2025-01-03"])),
        # Treasury dollar deposits: thousands of pesos at month end (dated by month start) and the balance-sheet rate.
        "t": pd.Series([2000 * 1450 * 1000.0], index=pd.to_datetime(["2025-11-01"])),
        "tfx": pd.Series([1450.0], index=pd.to_datetime(["2025-11-01"])),
        "imf": pd.Series([12000.0, 2000.0], index=pd.to_datetime(["2025-04-15", "2025-08-05"])),
    }
    keys = ["standard", "liquid", "imf", "gross"]
    ind = Indicator(id="nr", topic="t", kind="variants", title="t", short_title="t", description="",
                    source="derived", source_label="", frequency="D", units="",
                    variants={k: Variant(k, k, f"nr:{k}") for k in keys}, default={},
                    derive={"method": "net_reserves", "start": "2003-01-01", "inputs": {
                        "gross": "g", "encaje": "e", "intl_org": "o", "gold": "au", "repo_line": "rl", "fx": "fx",
                        "swap_cny": "cny", "cny_usd": "rate", "repos": "rp", "repos_short": "rps",
                        "treasury": "t", "treasury_fx": "tfx", "imf_purchases": "imf"}})
    df, info = derive.net_reserves(ind, frames)
    last = df.iloc[-1]
    swap = 130 * 1000 * 0.15
    assert last["nr:standard"] == pytest.approx(40000 - 10000 - swap - 100 - 6000 - 2000)
    assert last["nr:liquid"] == pytest.approx(last["nr:standard"] - 8000)
    assert last["nr:imf"] == pytest.approx(40000 - 10000 - swap - 100 - 0 - 14000)
    b = info["breakdown"]
    assert b["date"] == "2026-01-31" and b["treasury_as_of"] == "2025-11-30"
    for conv in ("standard", "liquid", "imf"):
        rows = sum(r["sign"] * (r["values"][conv] or 0) for r in b["rows"])
        assert rows == pytest.approx(b["total"][conv], abs=0.5)
    # The repo line rose by 3,000 with a matching file entry: no warning.
    assert info["checks"] == []
    frames["rp"] = pd.Series([3000.0], index=pd.to_datetime(["2025-06-11"]))
    _, info = derive.net_reserves(ind, frames)
    assert info["checks"] and "bcra_fx_repos" in info["checks"][0]


def test_stale_days_override():
    t = pd.Timestamp("2026-10-06")
    assert not validate.is_stale(pd.Timestamp("2026-09-23"), t, "D", stale_days=21)
    assert validate.is_stale(pd.Timestamp("2026-09-23"), t, "D")


def test_weekly_balance_header_dates_are_repaired():
    from pipeline.sources import bcra_weekly as w
    T = pd.Timestamp
    # 7th-of-month cells stored day-for-month, and August labeled as July (2003).
    raw = [T("2003-07-01"), T("2003-01-15"), T("2003-07-07"), T("2003-07-31"), T("2003-07-08"), T("2003-07-15")]
    assert w._fix_order(raw) == [T("2003-01-07"), T("2003-01-15"), T("2003-07-07"), T("2003-07-31"), T("2003-08-07"), T("2003-08-15")]
    assert w._text_date("31/12/2019 (**)") == T("2019-12-31")
    assert w._text_date("01/15/2002") == T("2002-01-15")
    assert w._text_date("01/07/2002") == T("2002-01-07")


def test_entity_accounts_and_concentration():
    from pipeline.sources import bcra_entities as e
    desc = {"121056": "LETRAS DE LIQUIDEZ DEL BCRA - MEDIC", "121049": "TÍTULOS PRIVADOS - TÍTULOS DE DEUDA",
            "121016": "TÍTULOS PÚBLICOS - MEDICIÓN A COSTO"}
    assert e._category("121056", desc) == "bcra_sec"
    assert e._category("121049", desc) == "priv_sec"
    assert e._category("121016", desc) == "gov_sec"
    assert e._category("131715", desc) == "loans_private" and e._category("131109", desc) == "loans_public"
    assert e._category("315700", desc) == "dep_fx" and e._category("311700", desc) == "dep_ars"
    bal = pd.DataFrame({"ent": ["A", "B", "A", "B"], "date": ["202601"] * 4, "acct": ["111001", "111001", "311700", "311700"],
                        "v": [75.0, 25.0, -60.0, -40.0]})
    c = e.concentration({"bal": bal}).set_index("series_id").value
    assert c["bcraef:hhi_assets"] == pytest.approx(75 ** 2 + 25 ** 2)
    assert c["bcraef:top10_deposits"] == pytest.approx(100) and c["bcraef:n_assets"] == 2


def test_bank_annex_helpers():
    from pipeline.sources import bcra_banks as b
    assert b.slug("Disponibilidades1") == "disponibilidades"
    assert b.slug("4.- Crédito al sector público") == "4_credito_al_sector_publico"
    assert b._as_date(2004) == pd.Timestamp("2004-12-01")
    import datetime as dt
    assert b._as_date(dt.datetime(2026, 7, 1)) == pd.Timestamp("2026-07-01")


def test_every_registry_source_has_an_adapter():
    from pipeline.sources import get_adapter
    reg = registry.load()
    for source, _ in reg.series_by_source():
        assert get_adapter(source) is not None


def test_finanzas_helpers():
    import datetime as dt
    from pipeline.sources import finanzas as f
    # file names and links
    assert f._file_date("https://x/sites/default/files/colocaciones_31-08-26_preliminar.xlsx") == dt.date(2026, 8, 31)
    assert f._file_date("coloc_31_12_2020_.xlsx") == dt.date(2020, 12, 31)
    html = '<a href="/sites/default/files/colocaciones_31-07-26_1.xlsx"></a><a href="https://www.argentina.gob.ar/sites/default/files/colocaciones_31-08-26_preliminar.xlsx"></a><a href="/sites/default/files/coloc_31_12_2020_.xlsx"></a><a href="/sites/default/files/colocaciones_31-12-2025_0.xlsx"></a>'
    files = f.find_placements(html)
    assert list(files) == [2025, 2026] and files[2026].endswith("31-08-26_preliminar.xlsx")
    assert f._period("ago-26 (*)") == pd.Timestamp("2026-08-01")
    # instruments
    assert f.classify("LECAP/$/30-11-2026", "Tasa efectiva mensual capitalizable 2,30", "MONEDA NACIONAL", "ARP") == "fixed"
    assert f.classify("BONCER/$/TASA CERO/30-06-2027", "Cero cupón", "MONEDA NACIONAL", "UCP") == "cer"
    assert f.classify("BONCAP/$/TAMAR+6,5%/26-02-2027", "", "MONEDA NACIONAL", "ARP") == "floating"
    assert f.classify("BONCAP DUAL/$/15-12-2026", "", "MONEDA NACIONAL", "ARP") == "dual"
    assert f.classify("LETRA/U$S/0,4%/31-10-23", "", "MONEDA EXTRANJERA", "DLK", "Integra al vencimiento al tipo de cambio") == "dlinked"
    assert f.classify("BONAR/U$S/29-10-2027", "", "MONEDA EXTRANJERA", "USD") == "usd"
    assert f.peso_market("LECAP - $") and f.peso_market("BONCER/$/0%+CER/30-06-2027")
    assert not f.peso_market("LETRA FGS - $") and not f.peso_market("LEFI/$/TPM/17-07-2025") and not f.peso_market("BONAR/U$S/6%/29-10-2027")
    # cash value units of dollar placements
    assert f.dollar_unit("DLK", 4108.9, 43.8, 2021) == "ARS"
    assert f.dollar_unit("DLK", 421452.6, 421452.6, 2024) == "ARS"
    assert f.dollar_unit("USD", 126.8, 128.0, 2026) == "USD"
    # implied yield: a letter placed at par on its issue date yields its own TEM
    d0, d1 = dt.date(2026, 1, 30), dt.date(2026, 3, 16)
    tem, tea = f.implied_yield("fixed", "Tasa efectiva mensual capitalizable 2,99", d0, d1, d0, 1000.0)
    assert tem == pytest.approx(2.99, abs=0.01) and tea > tem * 12
    assert f.implied_yield("cer", "Cero cupón", d0, d1, d0, 1000.0) == (None, None)


def test_gdp12_and_formula_helpers():
    from pipeline import derive
    # Quarterly GDP at annual rates, constant 1200: the twelve-month GDP is 1200.
    q = pd.Series(1200.0, index=pd.date_range("2022-01-01", "2024-10-01", freq="QS"))
    idx = pd.date_range("2022-01-01", "2025-03-01", freq="MS")
    cpi = pd.Series(range(1, len(idx) + 1), index=idx, dtype=float)
    g = derive.gdp12(q.reindex(idx), cpi)
    assert g["2022-12-01"] == pytest.approx(1200.0) and g["2024-06-01"] == pytest.approx(1200.0)
    assert pd.isna(g["2022-06-01"])                       # fewer than four quarters
    assert g["2025-03-01"] > 1200.0                      # extended with the CPI after the last quarter


def test_finanzas_presentations_follow_their_workbook():
    from pipeline.sources import finanzas as f
    html = ('<a href="https://x/sites/default/files/deuda_publica_30-06-2026_0.xlsx"></a>'
            '<a href="https://x/sites/default/files/presentacion_grafica_2do_trim_26.pdf"></a>'
            '<a href="https://x/sites/default/files/deuda_publica_31-03-2026.xlsx"></a>'
            '<a href="https://x/sites/default/files/presentacion_grafica_it_26_c.pdf"></a>')
    p = f.find_presentations(html)
    assert [d.isoformat() for d, _ in p] == ["2026-06-30", "2026-03-31"]
    assert p[0][1].endswith("2do_trim_26.pdf")


def test_market_rate_sources_are_cross_checked():
    from pipeline.sources import dolar
    h = dolar.history([{"fecha": "2026-10-06", "venta": 1540}, {"fecha": "2026-10-07", "venta": 1550}])
    assert dolar.combine(h, {"venta": 1555, "fechaActualizacion": "2026-10-07T18:58:00Z"})[1] is None
    assert "disagree" in dolar.combine(h, {"venta": 1700, "fechaActualizacion": "2026-10-07T18:58:00Z"})[1]
    s, err = dolar.combine(h, {"venta": 1560, "fechaActualizacion": "2026-10-08T12:00:00Z"})
    assert err is None and s.index.max() == pd.Timestamp("2026-10-08")


def test_auction_breakeven_devaluation():
    from pipeline.sources import finanzas as f
    pl = [{"settle": "2026-08-31", "kind": "fixed", "tea": 30.0, "maturity": "2027-01-29", "coupon": "capitalizable 2,25", "price": 1000},
          {"settle": "2026-08-31", "kind": "dlinked", "maturity": "2027-01-29", "coupon": "Cero cupón", "price": 990}]
    (d, be), = f.breakevens(pl)
    y = (1000 / 990) ** (365 / 151) - 1
    assert d == "2026-08-31" and be == pytest.approx((1.30 / (1 + y) - 1) * 100)


def test_futures_expiry_and_constant_horizon():
    import datetime as dt
    import math
    from pipeline.sources import a3
    assert a3.expiry("DLR022028") == dt.date(2028, 2, 29)
    assert a3.expiry("DLR122026") == dt.date(2026, 12, 31)
    assert a3.expiry("DLR122026A") is None
    curve = pd.DataFrame({"days": [100, 200, 320], "settlement": [1600.0, 1700.0, 1820.0]})
    # Interpolated log-linearly between the 100- and 200-day contracts.
    assert a3.at_horizon(curve, 182) == pytest.approx(math.exp(math.log(1600) + 0.82 * math.log(1700 / 1600)))
    # Extrapolated from the last two when the longest covers >= 82% of the horizon ...
    assert a3.at_horizon(curve, 365) == pytest.approx(math.exp(math.log(1820) + 45 * math.log(1820 / 1700) / 120))
    # ... and empty when it does not.
    assert a3.at_horizon(curve[curve.days < 300], 365) is None


def test_futures_rows_and_curve_table():
    from pipeline.sources import a3
    rows = [{"dateTime": f"2026-{m:02d}-01T00:00:00.000Z", "symbol": s, "settlement": p, "openInterest": 1000, "volume": 10, "impliedRate": 30}
            for m in (7, 9, 10) for s, p in (("DLR122026", 1600 + m), ("DLR062027", 1800 + m), ("DLR062026", 1500))]
    df = a3.frame(rows)
    assert "DLR062026" not in set(df[df.date == "2026-10-01"].symbol)          # expired contracts dropped
    s = a3.series(df)
    assert s["oi"].iloc[-1] == pytest.approx(2.0)                             # two contracts x 1,000 x US$1,000
    hi = pd.Series([1500.0, 1560.0], index=pd.to_datetime(["2026-07-01", "2026-10-01"]))
    t = a3.curve_table(df, spot=pd.Series([1550.0], index=pd.to_datetime(["2026-10-01"])), hi=hi)
    assert t["latest"] == "2026-10-01" and [c["date"] for c in t["curves"]] == ["2026-10-01", "2026-09-01", "2026-07-01"]
    assert t["spot"]["value"] == 1550.0 and t["band"]["ceiling"]["monthly_pace"] > 0
