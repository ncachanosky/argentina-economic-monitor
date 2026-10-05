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

from . import registry, store
from .registry import ROOT
from .update import STATUS_PATH

DATOS_GOB_SERIES_URL = "https://datos.gob.ar/series/api/series/?ids={id}"


def _clean(values) -> list:
    return [None if (v is None or (isinstance(v, float) and math.isnan(v))) else round(float(v), 6)
            for v in values]


def contributions(ind, raw: pd.DataFrame) -> dict:
    """Contributions to y/y growth of the total, in percentage points.

    Fixed-base constant-price accounts are additive, so for each group
    contribution_t = (G_t - G_{t-k}) / Total_{t-k} * 100, with k = 4 quarters
    (12 months). The residual group closes the gap to total growth exactly.
    """
    spec = ind.contributions
    sid = {k: v.source_id for k, v in ind.variants.items()}
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
    for k in spec.get("lines", []):
        s = raw[sid[k]]
        lines[k] = {"label": ind.variants[k].label, "values": _clean(((s / s.shift(lag) - 1) * 100).reindex(valid).tolist())}
    return {
        "dates": [d.strftime("%Y-%m-%d") for d in valid],
        "total": {"label": ind.variants[spec["total"]].label, "values": _clean(total_yoy.reindex(valid).tolist())},
        "groups": [{**{k: v for k, v in g.items() if k != "values"}, "values": _clean(g["values"].reindex(valid).tolist())} for g in groups],
        "lines": lines,
        "default_lines": spec.get("default_lines", spec.get("lines", [])[:3]),
    }


def export(out: Path) -> None:
    reg = registry.load()
    status = json.loads(STATUS_PATH.read_text(encoding="utf-8")) if STATUS_PATH.exists() else {}
    out.mkdir(parents=True, exist_ok=True)
    (out / "vintages").mkdir(exist_ok=True)

    manifest_inds = []
    for ind in reg.indicators:
        ids = [v.source_id for v in ind.variants.values()]
        wide = store.latest_frame(ids)
        if wide.empty:
            print(f"skip {ind.id}: no data yet")
            continue

        raw_wide = wide.copy()
        if ind.index_base:
            # Show levels as an index: the base year's average = 100 (per series).
            yr = wide.loc[str(ind.index_base)]
            wide = wide.apply(lambda col: col / yr[col.name].mean() * 100 if yr[col.name].notna().any() else col)

        st = [status.get(sid, {}) for sid in ids]
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
            "frequency": ind.frequency,
            "source_units": ind.source_units,
            "note": ind.note,
            "frequency": ind.frequency,
            "source_label": ind.source_label,
            "default": ind.default,
            "headline": ind.headline,
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
        if ind.kind == "contributions":
            payload["contributions"] = contributions(ind, raw_wide)
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=ROOT / "site" / "data")
    args = ap.parse_args(argv)
    export(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
