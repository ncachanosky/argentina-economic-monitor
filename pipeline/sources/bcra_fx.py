"""Adapter for BCRA's official quotes by currency (api.bcra.gob.ar, estadisticascambiarias v1.0).

Series ids are "bcracot:<ISO code>", e.g. "bcracot:CNY": US dollars per unit
of the currency ("tipoPase"), the rate the BCRA itself uses to value
foreign-currency positions. One request per calendar year (the API returns
at most 1000 rows and rejects end dates after today).

    GET /estadisticascambiarias/v1.0/Cotizaciones/{code}?fechadesde=YYYY-01-01&fechahasta=YYYY-12-31
"""
from __future__ import annotations

import time
from datetime import date

import pandas as pd
import requests

from . import FetchResult

BASE_URL = "https://api.bcra.gob.ar/estadisticascambiarias/v1.0/Cotizaciones"
TIMEOUT = 60
USER_AGENT = "argentina-economic-monitor (+https://github.com/ncachanosky/argentina-economic-monitor)"
FIRST_YEAR = {"CNY": 2014}


def _year(session: requests.Session, code: str, year: int) -> list[tuple]:
    end = min(date(year, 12, 31), date.today())
    params = {"fechadesde": f"{year}-01-01", "fechahasta": end.isoformat()}
    last = None
    for attempt in range(3):
        try:
            r = session.get(f"{BASE_URL}/{code}", params=params, timeout=TIMEOUT)
            r.raise_for_status()
            out = []
            for res in r.json().get("results", []):
                for d in res.get("detalle", []):
                    if d.get("codigoMoneda") == code and d.get("tipoPase"):
                        out.append((pd.Timestamp(res["fecha"]), float(d["tipoPase"])))
            return out
        except Exception as exc:
            last = exc
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"{code} {year}: {last}")


def fetch(ids) -> FetchResult:
    frames, meta, errors = [], {}, {}
    with requests.Session() as session:
        session.headers["User-Agent"] = USER_AGENT
        for sid in sorted(set(ids)):
            code = sid.split(":", 1)[1]
            try:
                rows = []
                for y in range(FIRST_YEAR.get(code, 2002), date.today().year + 1):
                    rows += _year(session, code, y)
                    time.sleep(0.2)
                if not rows:
                    raise ValueError("no observations returned")
                df = pd.DataFrame(rows, columns=["date", "value"]).drop_duplicates("date").sort_values("date")
                frames.append(df.assign(series_id=sid)[["date", "series_id", "value"]])
                meta[sid] = {"units": "US dollars per unit", "time_index_end": df.date.max().strftime("%Y-%m-%d")}
            except Exception as exc:
                errors[sid] = str(exc)
    data = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["date", "series_id", "value"])
    return FetchResult(data=data, meta=meta, errors=errors)
