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
        (out / f"{ind.id}.json").write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")

        csv = wide.rename(columns={v.source_id: v.label for v in ind.variants.values()})
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
