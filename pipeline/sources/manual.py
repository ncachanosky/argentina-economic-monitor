"""Adapter for hand-maintained series committed to the repo.

Each series is a CSV at data/manual/<id>.csv with columns `date,value`
(month-start dates). Use it for data no API publishes, such as the private
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
        p = base / f"{sid}.csv"
        try:
            df = pd.read_csv(p)
            if list(df.columns[:2]) != ["date", "value"]:
                raise ValueError(f"expected columns date,value; got {list(df.columns)}")
            df["date"] = pd.to_datetime(df["date"])
            df = df.dropna(subset=["value"])
        except Exception as exc:  # missing or malformed file
            errors[sid] = str(exc)
            continue
        frames.append(df.assign(series_id=sid)[["date", "series_id", "value"]])
        meta[sid] = {"units": None, "time_index_end": df["date"].max().strftime("%Y-%m-%d"),
                     "description": f"manual file data/manual/{sid}.csv"}
    data = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["date", "series_id", "value"])
    return FetchResult(data=data, meta=meta, errors=errors)
