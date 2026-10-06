"""Adapter for hand-maintained series committed to the repo.

Each series is a CSV at data/manual/<id>.csv with columns `date,value`
(month-start dates for monthly series). Extra columns are allowed (sources,
notes); an id written "<file>#<column>" reads that column instead of `value`
(e.g. "bcra_fx_repos#short_usd_m"). Use it for data no API publishes, such as the private
CPI composite that replaces INDEC's 2007-2015 figures. Editing the file is a
revision like any other: the next run records it in the vintage store.
"""
from __future__ import annotations

import pandas as pd

from ..registry import ROOT
from . import FetchResult

MANUAL_DIR = ROOT / "data" / "manual"


def fetch(ids, base=None) -> FetchResult:
    base = base or MANUAL_DIR
    frames, meta, errors = [], {}, {}
    for sid in sorted(ids):
        name, _, col = sid.partition("#")
        p = base / f"{name}.csv"
        try:
            df = pd.read_csv(p)
            if list(df.columns[:2]) != ["date", "value"]:
                raise ValueError(f"expected columns date,value; got {list(df.columns)}")
            if col:
                if col not in df.columns:
                    raise ValueError(f"no column {col!r} in {p.name}")
                df["value"] = pd.to_numeric(df[col], errors="coerce")
            df["date"] = pd.to_datetime(df["date"])
            df = df.dropna(subset=["value"])
        except Exception as exc:  # missing or malformed file
            errors[sid] = str(exc)
            continue
        frames.append(df.assign(series_id=sid)[["date", "series_id", "value"]])
        meta[sid] = {"units": None, "time_index_end": df["date"].max().strftime("%Y-%m-%d"),
                     "description": f"manual file data/manual/{name}.csv"}
    data = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["date", "series_id", "value"])
    return FetchResult(data=data, meta=meta, errors=errors)
