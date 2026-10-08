"""Adapter for selected V-Dem (Varieties of Democracy) indices, via Our World in Data.

OWID republishes each V-Dem index as a full country-year CSV (all countries,
1789 to the latest V-Dem release, usually out in March), at
https://ourworldindata.org/grapher/<slug>.csv, columns entity, code, year,
<variable>, ... The value is V-Dem's point estimate ("best"), 0-1.

Series ids: "vdem:<key>:<ISO3>" with key in INDICES; "vdem:<key>:LATAM" is the
median of the Latin American countries with data that year (efw.LATAM).
"""
from __future__ import annotations

import io
import time

import pandas as pd
import requests

from . import FetchResult
from .efw import LATAM

BASE = "https://ourworldindata.org/grapher/{slug}.csv?v=1&csvType=full&useColumnShortNames=true"
INDICES = {
    "libdem": "liberal-democracy-index",
    "electdem": "electoral-democracy-index",
    "judicial": "judicial-constraints-on-the-executive-index",
    "legislative": "legislative-constraints-on-the-executive-index",
    "rol": "rule-of-law-index",
    "corruption": "political-corruption-index",
}
TIMEOUT = 120
USER_AGENT = "argentina-economic-monitor (+https://github.com/ncachanosky/argentina-economic-monitor)"


def download(slug: str, retries: int = 3) -> bytes:
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(BASE.format(slug=slug), timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
            r.raise_for_status()
            return r.content
        except Exception as exc:
            last = exc
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"OWID {slug}: {last}")


def parse(content: bytes) -> pd.DataFrame:
    """Frame indexed by year (Timestamp, 1 January) with one column per ISO3 code."""
    df = pd.read_csv(io.BytesIO(content))
    df.columns = [c.lower() for c in df.columns]
    if not {"code", "year"} <= set(df.columns):
        raise ValueError(f"unexpected columns {list(df.columns)}")
    val = [c for c in df.columns if c.endswith("__estimate_best")] or \
          [c for c in df.columns if c not in ("entity", "code", "year", "owid_region")]
    if not val:
        raise ValueError("no value column")
    df = df.dropna(subset=["code"])
    df = df[~df["code"].astype(str).str.startswith("OWID")]
    wide = df.pivot_table(index="year", columns="code", values=val[0], aggfunc="last")
    wide.index = pd.to_datetime(wide.index.astype(int).astype(str) + "-01-01")
    if "ARG" not in wide or not wide["ARG"].dropna().between(0, 1).all():
        raise ValueError("Argentina missing or outside 0-1")
    return wide.sort_index()


def fetch(ids) -> FetchResult:
    frames, meta, errors = [], {}, {}
    by_key: dict[str, list[str]] = {}
    for sid in ids:
        by_key.setdefault(sid.split(":")[1], []).append(sid)
    for key, sids in by_key.items():
        try:
            if key not in INDICES:
                raise KeyError(f"unknown V-Dem index {key!r}")
            wide = parse(download(INDICES[key]))
        except Exception as exc:
            errors.update({sid: str(exc) for sid in sids})
            continue
        for sid in sids:
            iso = sid.split(":")[2]
            if iso == "LATAM":
                s = wide[[c for c in LATAM if c in wide]].median(axis=1, skipna=True)
            else:
                s = wide[iso] if iso in wide else pd.Series(dtype=float)
            s = s.dropna()
            if s.empty:
                errors[sid] = f"no data for {iso}"
                continue
            frames.append(pd.DataFrame({"date": s.index, "series_id": sid, "value": s.values}))
            meta[sid] = {"units": "index, 0-1", "time_index_end": s.index.max().strftime("%Y-%m-%d")}
    data = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["date", "series_id", "value"])
    return FetchResult(data=data, meta=meta, errors=errors)
