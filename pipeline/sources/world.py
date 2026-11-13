"""Annual cross-country tables in the International page's format (one file per measure)."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


def table(key: str, label: str, unit: str, decimals: int, source: str, wide: pd.DataFrame, first: int | None = None) -> dict:
    """wide: index of years (int or Timestamp), one column per ISO3 code."""
    w = wide.copy()
    w.index = [int(getattr(i, "year", i)) for i in w.index]
    if first:
        w = w[w.index >= first]
    years = list(range(int(w.index.min()), int(w.index.max()) + 1))
    w = w.reindex(years)
    vals = {}
    for iso in w.columns:
        if not isinstance(iso, str) or len(iso) != 3:
            continue
        v = [None if pd.isna(x) else round(float(x), decimals) for x in w[iso].values]
        if any(x is not None for x in v):
            vals[iso] = v
    return {"key": key, "label": label, "unit": unit, "decimals": decimals, "source": source,
            "years": years, "proj_from": None, "edition": None, "values": vals, "latest": f"{years[-1]}-01-01"}


def write(directory, tables: dict) -> list[str]:
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    changed = []
    for name, obj in tables.items():
        p = d / name
        text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        if not p.exists() or p.read_text(encoding="utf-8") != text:
            p.write_text(text, encoding="utf-8")
            changed.append(name)
    return changed
