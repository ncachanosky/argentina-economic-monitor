"""Adapter for the BCRA statistics API (api.bcra.gob.ar, v4.0 "monetarias").

Series ids are BCRA's idVariable numbers, written in the registry as
"bcra:<id>" so they cannot collide with datos.gob.ar ids. Daily series come
back newest first, paged (`limit` up to 3000, `offset`).

    GET /estadisticas/v4.0/monetarias/{id}?desde=YYYY-MM-DD&hasta=YYYY-MM-DD&limit=3000&offset=N
    -> {"status": 200, "metadata": {"resultset": {"count", "offset", "limit"}},
        "results": [{"idVariable": id, "detalle": [{"fecha", "valor"}, ...]}]}
"""
from __future__ import annotations

import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from . import FetchResult

BASE_URL = "https://api.bcra.gob.ar/estadisticas/v4.0/monetarias"
PAGE_SIZE = 3000
TIMEOUT = 60
RETRIES = 3
START = "1900-01-01"
USER_AGENT = "argentina-economic-monitor (+https://github.com/ncachanosky/argentina-economic-monitor)"


def bcra_id(series_id: str) -> int:
    if not series_id.startswith("bcra:"):
        raise ValueError(f"not a BCRA series id: {series_id!r}")
    return int(series_id.split(":", 1)[1])


def _get(session: requests.Session, url: str, params: dict) -> dict:
    last: Exception | None = None
    for attempt in range(RETRIES):
        try:
            r = session.get(url, params=params, timeout=TIMEOUT)
            if r.status_code in (400, 404):
                raise ValueError(r.text[:300])
            r.raise_for_status()
            return r.json()
        except ValueError:
            raise
        except Exception as exc:  # network errors, 5xx, TLS
            last = exc
            time.sleep(2 ** attempt * 3)
    raise RuntimeError(f"BCRA API request failed after {RETRIES} attempts: {last}")


def _fetch_one(session: requests.Session, sid: str) -> pd.DataFrame:
    url = f"{BASE_URL}/{bcra_id(sid)}"
    rows, offset = [], 0
    while True:
        payload = _get(session, url, {"desde": START, "hasta": datetime.now(ZoneInfo("America/Argentina/Buenos_Aires")).date().isoformat(),
                                      "limit": PAGE_SIZE, "offset": offset})
        detail = [d for res in payload.get("results", []) for d in res.get("detalle", [])]
        rows.extend(detail)
        offset += len(detail)
        count = (payload.get("metadata") or {}).get("resultset", {}).get("count")
        if len(detail) < PAGE_SIZE or (count is not None and offset >= count):
            break
        time.sleep(0.3)
    df = pd.DataFrame(rows, columns=["fecha", "valor"])
    df = df.rename(columns={"fecha": "date", "valor": "value"})
    df["date"] = pd.to_datetime(df["date"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["value"]).drop_duplicates("date", keep="first").sort_values("date")
    # Monthly series are dated at month end; the site dates months by their first day.
    if len(df) > 2 and df["date"].dt.is_month_end.all() and df["date"].diff().dt.days.dropna().ge(28).all():
        df["date"] = df["date"].dt.to_period("M").dt.to_timestamp()
    return df.assign(series_id=sid)[["date", "series_id", "value"]]


def fetch(ids) -> FetchResult:
    frames, meta, errors = [], {}, {}
    with requests.Session() as session:
        session.headers["User-Agent"] = USER_AGENT
        for sid in sorted(set(ids)):
            try:
                df = _fetch_one(session, sid)
                if df.empty:
                    raise ValueError("no observations returned")
                frames.append(df)
                meta[sid] = {"units": None, "time_index_end": df["date"].max().strftime("%Y-%m-%d")}
            except Exception as exc:
                errors[sid] = str(exc)
            time.sleep(0.2)
    data = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["date", "series_id", "value"])
    return FetchResult(data=data, meta=meta, errors=errors)
