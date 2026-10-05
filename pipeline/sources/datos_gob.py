"""Adapter for the Argentine government time-series API (apis.datos.gob.ar).

Docs: https://datosgobar.github.io/series-tiempo-ar-api/
"""
from __future__ import annotations

import time

import pandas as pd
import requests

from . import FetchResult

BASE_URL = "https://apis.datos.gob.ar/series/api/series/"
MAX_IDS_PER_CALL = 40     # API limit on ids per request
PAGE_SIZE = 1000          # API limit on rows per request
TIMEOUT = 60
RETRIES = 3
USER_AGENT = "argentina-economic-monitor (+https://github.com/ncachanosky/argentina-economic-monitor)"


def _get(params: dict, session: requests.Session) -> dict:
    last_exc: Exception | None = None
    for attempt in range(RETRIES):
        try:
            r = session.get(BASE_URL, params=params, timeout=TIMEOUT)
            if r.status_code == 400:
                # Invalid id(s): the API explains which in the body. Don't retry.
                raise ValueError(r.text[:500])
            r.raise_for_status()
            return r.json()
        except ValueError:
            raise
        except Exception as exc:  # network errors, 5xx
            last_exc = exc
            time.sleep(2 ** attempt * 5)
    raise RuntimeError(f"datos.gob.ar request failed after {RETRIES} attempts: {last_exc}")


def _fetch_chunk(ids: list[str], session: requests.Session) -> tuple[pd.DataFrame, dict]:
    rows: list[list] = []
    meta: dict[str, dict] = {}
    start = 0
    while True:
        payload = _get(
            {
                "ids": ",".join(ids),
                "format": "json",
                "metadata": "full",
                "limit": PAGE_SIZE,
                "start": start,
            },
            session,
        )
        data = payload.get("data", [])
        rows.extend(data)
        if not meta:
            # meta[0] describes the query; meta[1:] are the fields, in id order
            for entry in payload.get("meta", [])[1:]:
                f = entry.get("field", {})
                meta[f.get("id")] = {
                    "units": f.get("units"),
                    "description": f.get("description"),
                    "time_index_end": f.get("time_index_end"),
                    "dataset": (entry.get("dataset") or {}).get("title"),
                }
        start += len(data)
        if not data or start >= payload.get("count", 0):
            break

    wide = pd.DataFrame(rows, columns=["date", *ids])
    long = wide.melt(id_vars="date", var_name="series_id", value_name="value")
    long["date"] = pd.to_datetime(long["date"])
    long["value"] = pd.to_numeric(long["value"], errors="coerce")
    long = long.dropna(subset=["value"])
    return long, meta


def fetch(ids: list[str] | set[str]) -> FetchResult:
    ids = sorted(set(ids))
    frames, meta, errors = [], {}, {}
    with requests.Session() as session:
        session.headers["User-Agent"] = USER_AGENT
        for i in range(0, len(ids), MAX_IDS_PER_CALL):
            chunk = ids[i : i + MAX_IDS_PER_CALL]
            try:
                df, m = _fetch_chunk(chunk, session)
                frames.append(df)
                meta.update(m)
            except Exception as exc:
                # One bad id fails the whole chunk; retry one by one to isolate it.
                for sid in chunk:
                    try:
                        df, m = _fetch_chunk([sid], session)
                        frames.append(df)
                        meta.update(m)
                    except Exception as exc_one:
                        errors[sid] = str(exc_one) or str(exc)

    data = (
        pd.concat(frames, ignore_index=True)
        if frames
        else pd.DataFrame(columns=["date", "series_id", "value"])
    )
    return FetchResult(data=data, meta=meta, errors=errors)
