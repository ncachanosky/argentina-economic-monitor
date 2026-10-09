"""Write the JSON and CSV files the static site reads.

    python -m pipeline.export --out _site/data

Produces:
    manifest.json             site info, topics, indicator list, build time
    <indicator>.json          aligned dates + one value array per variant
    <indicator>.csv           same data, for download
    vintages/<indicator>.csv  full revision history (public real-time data)
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import derive, registry, store
from .registry import ROOT
from .update import STATUS_PATH

DATOS_GOB_SERIES_URL = "https://datos.gob.ar/series/api/series/?ids={id}"


def _clean(values) -> list:
    # Significant digits, not decimals: long-run price indices reach 1e-12.
    return [None if (v is None or (isinstance(v, float) and math.isnan(v))) else float(f"{float(v):.9g}")
            for v in values]


def contributions(ind, raw: pd.DataFrame) -> dict:
    """Contributions to y/y growth of the total, in percentage points.

    Fixed-base constant-price accounts are additive, so for each group
    contribution_t = (G_t - G_{t-k}) / Total_{t-k} * 100, with k = 4 quarters
    (12 months). The residual group closes the gap to total growth exactly.
    """
    spec = ind.contributions
    sid = {k: v.source_id for k, v in ind.variants.items()}
    if spec.get("precomputed"):
        # Variants already are contributions (pp); the total is their sum, or a
        # published total when one is given.
        valid = raw.dropna(how="all").index
        groups = [{"key": g["key"], "label": g["label"], "color": g.get("color"),
                   "values": _clean(raw[sid[g["add"][0]]].reindex(valid).tolist())} for g in spec["groups"]]
        tot = raw[sid[spec["total"]]] if spec.get("total") else raw[[sid[g["add"][0]] for g in spec["groups"]]].sum(axis=1)
        yearly = None
        if ind.yearly:
            # Yearly view: the same groups, or their twelve-month versions (variant key + suffix).
            sfx = ind.yearly.get("suffix", "")
            yv = raw[[sid[g["add"][0] + sfx] for g in spec["groups"]] + ([sid[spec["total"] + sfx]] if spec.get("total") else [])].dropna(how="all")
            ytot = raw[sid[spec["total"] + sfx]] if spec.get("total") else raw[[sid[g["add"][0] + sfx] for g in spec["groups"]]].sum(axis=1)
            yearly = {"how": ind.yearly.get("how", "december"), "dates": [d.strftime("%Y-%m-%d") for d in yv.index],
                      "groups": [_clean(raw[sid[g["add"][0] + sfx]].reindex(yv.index).tolist()) for g in spec["groups"]],
                      "total": _clean(ytot.reindex(yv.index).tolist()), "total_label": ind.yearly.get("total_label")}
        return {"dates": [d.strftime("%Y-%m-%d") for d in valid], "yearly": yearly,
                "total": {"label": spec.get("total_label", "Total"), "values": _clean(tot.reindex(valid).tolist())},
                "groups": groups, "lines": {}, "default_lines": [], "lines_title": spec.get("lines_title"),
                "units": spec.get("units", "pp"), "suffix": spec.get("suffix"), "precomputed": True,
                "hide_total": bool(spec.get("hide_total")), "style": spec.get("style", "bars")}
    lag = 4 if ind.frequency == "Q" else 12
    total = raw[sid[spec["total"]]]
    base = total.shift(lag)
    total_yoy = (total / base - 1) * 100
    groups, explained = [], 0
    for g in spec.get("groups", []):
        if g.get("residual"):
            continue
        level = sum(raw[sid[k]].fillna(0) for k in g.get("add", [])) - sum(raw[sid[k]].fillna(0) for k in g.get("subtract", []))
        c = (level - level.shift(lag)) / base * 100
        explained = explained + c
        groups.append({"key": g["key"], "label": g["label"], "color": g.get("color"), "values": c})
    for g in spec.get("groups", []):
        if g.get("residual"):
            groups.append({"key": g["key"], "label": g["label"], "color": g.get("color"), "values": total_yoy - explained})
    valid = total_yoy.dropna().index
    lines = {}
    yoy = lambda s: _clean(((s / s.shift(lag) - 1) * 100).reindex(valid).tolist())
    if spec.get("lines") == "groups":
        # One line per (non-residual) group: y/y growth of the group's level.
        for g in spec.get("groups", []):
            if g.get("residual"):
                continue
            level = sum(raw[sid[k]].fillna(0) for k in g.get("add", [])) - sum(raw[sid[k]].fillna(0) for k in g.get("subtract", []))
            lines[g["key"]] = {"label": g["label"], "color": g.get("color"), "values": yoy(level)}
    else:
        colors = spec.get("line_colors", {})
        for k in spec.get("lines", []):
            lines[k] = {"label": ind.variants[k].label, "color": colors.get(k), "values": yoy(raw[sid[k]])}
    return {
        "dates": [d.strftime("%Y-%m-%d") for d in valid],
        "total": {"label": ind.variants[spec["total"]].label, "values": _clean(total_yoy.reindex(valid).tolist())},
        "groups": [{**{k: v for k, v in g.items() if k != "values"}, "values": _clean(g["values"].reindex(valid).tolist())} for g in groups],
        "lines": lines,
        "default_lines": spec.get("default_lines") or list(lines)[:3],
        "lines_title": spec.get("lines_title", "Components, year-over-year change"),
    }


def fitted_weights(ind, reg, target) -> dict | None:
    """Recover fixed-base Laspeyres weights from the published indices.

    With a common base period, total_t = sum_i w_i * component_i,t exactly (up
    to rounding), so least squares without intercept returns the weights.
    Reports the fit so a bad recovery is visible, and is skipped if poor.
    """
    import numpy as np
    tgt_ind = next((i for i in reg.indicators if i.id == target["indicator"]), None)
    if tgt_ind is None:
        return None
    y = store.as_of(tgt_ind.variants[target["variant"]].source_id)
    X = store.latest_frame([v.source_id for v in ind.variants.values()])
    X.columns = list(ind.variants)
    df = pd.concat([X, y.rename("_y")], axis=1).dropna()
    if len(df) < 24:
        return None
    w, *_ = np.linalg.lstsq(df[list(ind.variants)].values, df["_y"].values, rcond=None)
    fitted = df[list(ind.variants)].values @ w
    max_err = float(np.max(np.abs(fitted / df["_y"].values - 1)) * 100)
    if max_err > 0.5 or (w < 0).any():
        print(f"  weights for {ind.id}: poor fit (max error {max_err:.2f}%), skipped")
        return None
    w = w / w.sum() * 100
    return {"year": "Dec 2016 base", "method": "fit", "label": "CPI basket weight", "max_error_pct": round(max_err, 4),
            "values": {k: round(float(v), 4) for k, v in zip(ind.variants, w)}}


def sector_weights(ind, reg) -> dict | None:
    """Fixed-base sector weights (% of base-year GDP) for incidence calculations."""
    spec = ind.weights
    if not spec:
        return None
    if spec.get("fit_to"):
        return fitted_weights(ind, reg, spec["fit_to"])
    src = next((i for i in reg.indicators if i.id == spec["from"]), None)
    if src is None or not src.contributions:
        return None
    raw = store.latest_frame([v.source_id for v in src.variants.values()])
    year = raw.loc[str(spec["year"])]
    if len(year) == 0:
        return None
    tot = year[src.variants[src.contributions["total"]].source_id].sum()
    share = {k: year[v.source_id].sum() / tot * 100 for k, v in src.variants.items() if k != src.contributions["total"]}
    out = {k: share[sk] for k, sk in spec["map"].items() if sk in share}
    if spec.get("residual"):
        excl = set(spec.get("exclude_from_residual", []))
        out[spec["residual"]] = 100 - sum(v for k, v in share.items() if k not in excl)
    return {"year": str(spec["year"]), "label": f"share of {spec['year']} GDP", "values": {k: round(v, 4) for k, v in out.items()}}


_SERIES_CACHE: dict = {}


def indicator_series(reg, ind_id: str, variant: str) -> pd.Series:
    """One variant of an indicator as exported (derived ones computed once)."""
    key = (ind_id, variant)
    if key not in _SERIES_CACHE:
        ind = next(i for i in reg.indicators if i.id == ind_id)
        sid = ind.variants[variant].source_id
        _SERIES_CACHE[key] = derive.compute(ind, reg)[0][sid].dropna() if ind.derive else store.as_of(sid)
    return _SERIES_CACHE[key]


def deflator_for(ind, reg, dates: pd.DatetimeIndex) -> dict | None:
    """Price index aligned to the indicator's dates (each date takes its month's value)."""
    spec = ind.deflate_with
    if not spec:
        return None
    p = indicator_series(reg, spec["indicator"], spec["variant"])
    months = dates.to_period("M").to_timestamp()
    vals = p.reindex(months).tolist()
    last = p.index.max()
    return {"values": _clean(vals), "latest": last.strftime("%Y-%m-%d"), "latest_value": float(p.iloc[-1]),
            "label": spec.get("label", "CPI")}


REM_LABELS = {"cpi": "REM expectation (median)", "core": "REM expectation, core (median)", "fx": "REM expectation (median)",
              "tamar": "REM expectation (median)", "gdp": "REM expectation (median)", "unemp": "REM expectation (median)"}


def rem_forecast(ind) -> dict | None:
    """The latest REM survey's expected path for each of the card's `forecast` entries."""
    if not ind.forecast:
        return None
    from .update import TABLES_DIR
    p = TABLES_DIR / "rem" / "paths.json"
    if not p.exists():
        return None
    paths = json.loads(p.read_text(encoding="utf-8"))
    items = []
    for f in ind.forecast:
        v = paths["vars"].get(f["rem"])
        if v and v["points"]:
            items.append({**f, "label": f.get("label") or REM_LABELS.get(f["rem"], "REM expectation"), "points": v["points"]})
    return {"survey": paths["survey"], "items": items} if items else None


def rem_outcomes() -> dict:
    """Actual values for the calendar years the REM forecasts, for comparison with its forecasts."""
    out = {}
    cpi = store.as_of("148.3_INIVELNAL_DICI_M_26")
    if not cpi.empty:
        dec = cpi[cpi.index.month == 12]
        out["cpi"] = {str(d.year): 100 * (v / dec.get(d - pd.DateOffset(years=1)) - 1) for d, v in dec.items()
                      if dec.get(d - pd.DateOffset(years=1)) is not None}
    gdp = store.as_of("3.2_OGP_D_2004_T_17")
    if not gdp.empty:
        yr = gdp.groupby(gdp.index.year).agg(["sum", "count"])
        yr = yr[yr["count"] == 4]["sum"]
        out["gdp"] = {str(y): 100 * (yr[y] / yr[y - 1] - 1) for y in yr.index if y - 1 in yr.index}
    fx = store.as_of("bcra:5")
    if not fx.empty:
        dec = fx[fx.index.month == 12]
        full = [y for y in sorted(set(dec.index.year)) if dec[dec.index.year == y].index.max().day >= 28]
        out["fx"] = {str(y): float(dec[dec.index.year == y].mean()) for y in full}
    prim = store.as_of("379.9_SUPERAVIT_017__23_94")
    if not prim.empty:
        yr = prim.groupby(prim.index.year).agg(["sum", "count"])
        out["primary"] = {str(y): float(r["sum"]) / 1000 for y, r in yr.iterrows() if r["count"] == 12}
    return {k: {y: float(v) for y, v in d.items()} for k, d in out.items()}


def export_table(ind, out: Path, status: dict) -> dict | None:
    """Copy a full table (data/tables/<source>/*.json) next to the site data and write the card's payload."""
    import shutil
    from .update import TABLES_DIR
    src = TABLES_DIR / ind.table["source"]
    main = src / ind.table.get("file", "index.json")
    if not main.exists():
        print(f"skip {ind.id}: no table yet")
        return None
    dst = out / "tables" / ind.table["source"]
    dst.mkdir(parents=True, exist_ok=True)
    for f in src.rglob("*.json"):
        target = dst / f.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(f, target)
    index = json.loads(main.read_text(encoding="utf-8"))
    if "years" in index:
        last = max(d for ds in index["years"].values() for d in ds)
    else:
        last = index.get("latest")
    st = status.get(ind.table.get("status_id", ""), {})
    payload = {
        "id": ind.id, "topic": ind.topic, "kind": ind.kind, "title": ind.title, "short_title": ind.short_title,
        "description": ind.description, "note": ind.note, "source_label": ind.source_label, "wide": True,
        "frequency": ind.frequency, "units": ind.units, "table": {**ind.table, "path": f"tables/{ind.table['source']}/"},
        "method_links": ind.method_links, "forecast": rem_forecast(ind),
        "outcomes": rem_outcomes() if ind.kind == "rem_revisions" else None,
        "years": index.get("years"), "groups": index.get("groups"), "group_labels": index.get("group_labels"),
        "status": st.get("status", "ok"), "last_obs": last,
        "last_checked": st.get("last_checked"), "last_changed": st.get("last_changed"),
    }
    (out / f"{ind.id}.json").write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    return {"id": ind.id, "topic": ind.topic, "kind": ind.kind, "title": ind.title, "short_title": ind.short_title,
            "status": payload["status"], "last_obs": last, "headline": False}


def first_seen(series_id: str) -> tuple[str, str] | None:
    """(latest period, vintage when it first appeared) for a stored series; None if it came with the first load."""
    h = store.load(series_id)
    if h.empty:
        return None
    last = h["date"].max()
    seen = h.loc[h["date"] == last, "vintage"].min()
    if seen == h["vintage"].min():          # part of the initial history, not a release we saw
        return None
    return pd.Timestamp(last).strftime("%Y-%m-%d"), str(seen)


def updates(reg, days_back: int = 21, days_ahead: int = 21) -> dict:
    """Recent releases (new periods seen in the last weeks) and scheduled INDEC releases ahead."""
    today = pd.Timestamp(datetime.now(timezone.utc).date())
    recent = []
    meta = reg.series_meta()
    for ind in reg.indicators:
        if ind.hidden or ind.frequency == "D" or ind.kind in registry.TABLE_KINDS:
            continue
        # Only releases of monthly or slower source series (daily inputs change every day).
        ids = [sid for sid in ind.input_ids() if sid in meta and meta[sid]["frequency"] != "D"]
        seen = [x for x in (first_seen(sid) for sid in ids) if x]
        if not seen:
            continue
        period, vintage = max(seen, key=lambda x: (x[1], x[0]))
        if pd.Timestamp(vintage) >= today - pd.Timedelta(days=days_back):
            recent.append({"id": ind.id, "title": ind.short_title, "topic": ind.topic, "frequency": ind.frequency,
                           "period": period, "seen": vintage})
    recent.sort(key=lambda r: (r["seen"], r["title"]), reverse=True)
    by_release = {}
    for ind in reg.indicators:
        if ind.release and not ind.hidden:
            by_release.setdefault(ind.release, []).append({"id": ind.id, "title": ind.short_title, "topic": ind.topic})
    ahead = []
    for key, cal in reg.releases.items():
        for d in cal.get("dates", []):
            day = pd.Timestamp(d["date"])
            if today <= day <= today + pd.Timedelta(days=days_ahead):
                ahead.append({"date": d["date"], "period": d["period"], "name": cal.get("name"), "frequency": cal.get("frequency", "M"),
                              "indicators": by_release.get(key, [])[:3]})
    ahead.sort(key=lambda r: (r["date"], r["name"]))
    return {"today": today.strftime("%Y-%m-%d"), "recent": recent[:12], "ahead": ahead}


def export(out: Path) -> None:
    reg = registry.load()
    status = json.loads(STATUS_PATH.read_text(encoding="utf-8")) if STATUS_PATH.exists() else {}
    out.mkdir(parents=True, exist_ok=True)
    (out / "vintages").mkdir(exist_ok=True)

    manifest_inds = []
    for ind in reg.indicators:
        if ind.hidden:
            continue
        if ind.kind in registry.TABLE_KINDS:
            entry = export_table(ind, out, status)
            if entry:
                manifest_inds.append(entry)
            continue
        ids = [v.source_id for v in ind.variants.values()]
        derived_info = None
        if ind.derive:
            try:
                wide, derived_info = derive.compute(ind, reg)
            except Exception as exc:
                print(f"skip {ind.id}: {exc}")
                continue
        else:
            wide = store.latest_frame(ids)
        if wide.empty:
            print(f"skip {ind.id}: no data yet")
            continue

        raw_wide = wide.copy()
        if ind.index_base:
            # Show levels as an index: the base year's average = 100 (per series).
            yr = wide.loc[str(ind.index_base)]
            wide = wide.apply(lambda col: col / yr[col.name].mean() * 100 if yr[col.name].notna().any() else col)

        status_ids = ind.input_ids()
        if ind.derive and ind.derive["method"] == "reweight":
            comp = next(i for i in reg.indicators if i.id == ind.derive["components"])
            status_ids += [v.source_id for v in comp.variants.values()]
        st = [status.get(sid, {}) for sid in status_ids]
        worst = "error" if any(s.get("status") == "error" for s in st) else (
            "stale" if any(s.get("status") == "stale" for s in st) else "ok")
        changed = [s.get("last_changed") for s in st if s.get("last_changed")]
        checked = [s.get("last_checked") for s in st if s.get("last_checked")]

        payload = {
            "id": ind.id,
            "topic": ind.topic,
            "kind": ind.kind,
            "title": ind.title,
            "short_title": ind.short_title,
            "description": ind.description,
            "units": ind.units,
            "measure": ind.measure,
            "transforms": ind.transforms,
            "component_noun": ind.component_noun,
            "wide": ind.wide,
            "bar_transform": ind.bar_transform,
            "frequency": ind.frequency,
            "source_units": ind.source_units,
            "note": ind.note,
            "caveat": ind.caveat,
            "plain_level": ind.plain_level,
            "yearly": ind.yearly,
            "ref_line": ind.ref_line,
            "method_links": ind.method_links,
            "statement": ind.statement,
            "overlay_default": ind.overlay_default,
            "muted": ind.muted,
            "term_shading": ind.term_shading,
            "term_colored": ind.term_colored,
            "forecast": rem_forecast(ind),
            "fan": ind.fan,
            "secondary_bar": ind.secondary_bar,
            "source_label": ind.source_label,
            "default": ind.default,
            "headline": ind.headline,
            "overlay": ind.overlay,
            "log_level": ind.log_level,
            "bands": ind.bands,
            "summary_windows": ind.summary_windows,
            "annual_table": ind.annual_table,
            "bar_transforms": ind.bar_transforms,
            "view_start": ind.view_start,
            "emphasis": ind.emphasis,
            "composition": ind.composition,
            "episodes": reg.episodes_for(ind.id) or None,
            "ranges": ind.ranges,
            "derived": derived_info,
            "status": worst,
            "last_obs": wide.dropna(how="all").index.max().strftime("%Y-%m-%d"),
            "last_changed": max(changed) if changed else None,
            "last_checked": max(checked) if checked else None,
            "next_release": reg.next_release(ind, wide.dropna(how="all").index.max().strftime("%Y-%m-%d")),
            "dates": [d.strftime("%Y-%m-%d") for d in wide.index],
            "variants": {
                k: {
                    "label": v.label,
                    "source_id": v.source_id,
                    "source_url": DATOS_GOB_SERIES_URL.format(id=v.source_id)
                    if ind.source == "datos_gob" else None,
                    "values": _clean(wide[v.source_id].tolist()),
                }
                for k, v in ind.variants.items()
            },
        }
        if ind.deflate_with:
            payload["deflator"] = deflator_for(ind, reg, wide.index)
        if ind.kind == "contributions":
            payload["contributions"] = contributions(ind, raw_wide)
        if ind.weights:
            payload["weights"] = sector_weights(ind, reg)
        (out / f"{ind.id}.json").write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")

        # The downloadable "all data" CSV keeps the source units (e.g. millions of 2004 pesos).
        csv = raw_wide.rename(columns={v.source_id: v.label for v in ind.variants.values()})
        csv.index.name = "date"
        csv.to_csv(out / f"{ind.id}.csv", float_format="%.6g", date_format="%Y-%m-%d")

        hist = []
        for k, v in ind.variants.items():
            h = store.load(v.source_id)
            if not h.empty:
                hist.append(h.assign(variant=k, series_id=v.source_id))
        if hist:
            pd.concat(hist)[["variant", "series_id", "vintage", "date", "value"]].to_csv(
                out / "vintages" / f"{ind.id}.csv", index=False, date_format="%Y-%m-%d"
            )

        manifest_inds.append({
            "id": ind.id, "topic": ind.topic, "kind": ind.kind, "title": ind.title,
            "short_title": ind.short_title, "status": worst, "last_obs": payload["last_obs"],
            "headline": bool(ind.headline),
        })

    manifest = {
        "site": reg.site,
        "topics": reg.topics,
        "presidencies": reg.presidencies,
        "party_colors": reg.party_colors,
        "indicators": manifest_inds,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"exported {len(manifest_inds)} indicators to {out}")
    from . import dashboard
    dashboard.write(out)
    (out / "updates.json").write_text(json.dumps(updates(reg), separators=(",", ":")), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=ROOT / "site" / "data")
    args = ap.parse_args(argv)
    export(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
