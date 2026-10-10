"""Adapter for the national public sector's monthly accounts (Esquema
Ahorro-Inversión-Financiamiento, base caja, metodología 2017), Secretaría de
Hacienda, as the full distribution file on datos.gob.ar: every line of the
statement in one CSV, millions of pesos, monthly since January 2015.

  https://infra.datos.gob.ar/catalog/sspm/dataset/379/distribution/379.9/download/sector-publico-nacional-valores-mensuales-17.csv

Series ids are "aif:<column>", the column names of that file (for example
"aif:ing_antes_figurativos_2017"). Reading the whole file keeps the statement
complete without looking up one series id per line.
"""
from __future__ import annotations

import io
import time

import pandas as pd
import requests

from . import FetchResult

URL = "https://infra.datos.gob.ar/catalog/sspm/dataset/379/distribution/379.9/download/sector-publico-nacional-valores-mensuales-17.csv"
TIMEOUT = 90
USER_AGENT = "argentina-economic-monitor (+https://github.com/ncachanosky/argentina-economic-monitor)"


def download(retries: int = 3) -> str:
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(URL, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
            r.raise_for_status()
            return r.content.decode("utf-8-sig")
        except Exception as exc:
            last = exc
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"AIF file: {last}")


def parse(text: str) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(text))
    if "indice_tiempo" not in df.columns:
        raise ValueError("no indice_tiempo column; layout changed?")
    df["indice_tiempo"] = pd.to_datetime(df["indice_tiempo"])
    return df.set_index("indice_tiempo").sort_index()


def fetch(ids) -> FetchResult:
    empty = pd.DataFrame(columns=["date", "series_id", "value"])
    try:
        df = parse(download())
    except Exception as exc:
        return FetchResult(data=empty, errors={sid: str(exc) for sid in ids})
    frames, meta, errors = [], {}, {}
    for sid in ids:
        col = sid.split(":", 1)[1]
        if col not in df.columns:
            errors[sid] = f"column {col!r} not in the AIF file"
            continue
        s = pd.to_numeric(df[col], errors="coerce").dropna()
        if s.empty:
            errors[sid] = f"column {col!r} is empty"
            continue
        frames.append(pd.DataFrame({"date": s.index, "series_id": sid, "value": s.values}))
        meta[sid] = {"units": "millions of pesos", "time_index_end": s.index.max().strftime("%Y-%m-%d")}
    data = pd.concat(frames, ignore_index=True) if frames else empty
    return FetchResult(data=data, meta=meta, errors=errors)
