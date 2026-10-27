"""Adapter for the BCRA's real exchange rate indices (Índice Tipo de Cambio Real
Multilateral, ITCRM, and bilateral indices), daily since 1997, base 17-Dec-2015 = 100,
from the workbook itcrm-serie.xlsx (ITCRMSerie.xlsx until October 2026), sheet "ITCRM y bilaterales".

A rise is a real depreciation of the peso. The BCRA deflates with INDEC's CPI (and,
before 2017, provincial indices) and partner-country CPIs; for the latest months, not
yet covered by the CPI, it uses the inflation expected in its REM survey, so the most
recent values are revised when the CPI comes out.

Series ids: "itcrm:<key>" with key in COLUMNS.
"""
from __future__ import annotations

import io
import re
import time
from urllib.parse import urljoin

import pandas as pd
import requests

from . import FetchResult

# In October 2026 the BCRA renamed the workbook (ITCRMSerie.xlsx -> itcrm-serie.xlsx) and left
# the old file in place, frozen on October 16. The link is read from the BCRA's page first,
# so a future rename is followed; the URL below is the fallback. The old name is not tried:
# it would serve stale data without an error.
PAGE = "https://www.bcra.gob.ar/indices-de-tipo-de-cambio-multilateral/"
URLS = ["https://www.bcra.gob.ar/archivos/Pdfs/PublicacionesEstadisticas/itcrm-serie.xlsx"]
SHEET = 0                       # "ITCRM y bilaterales" (daily)
TIMEOUT = 180
USER_AGENT = "argentina-economic-monitor (+https://github.com/ncachanosky/argentina-economic-monitor)"
COLUMNS = {"m": "ITCRM", "br": "ITCRB Brasil", "us": "ITCRB Estados Unidos", "cn": "ITCRB China",
           "eu": "ITCRB Zona Euro"}


def page_url() -> str | None:
    """The ITCRM workbook linked on the BCRA's page, if the page can be read."""
    try:
        r = requests.get(PAGE, timeout=60, headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        m = re.search(r'href="([^"]*itcrm[^"/]*\.xlsx)"', r.text, re.I)
        return urljoin(PAGE, m.group(1)) if m else None
    except Exception:
        return None


def download(retries: int = 3) -> bytes:
    last = None
    linked = page_url()
    urls = [linked] + [u for u in URLS if u != linked] if linked else URLS
    for attempt in range(retries):
        for url in urls:
            try:
                r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
                r.raise_for_status()
                if r.content[:2] != b"PK":
                    raise ValueError("not an xlsx file")
                return r.content
            except Exception as exc:
                last = exc
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"ITCRM workbook: {last}")


def parse(content: bytes) -> dict[str, pd.Series]:
    raw = pd.read_excel(io.BytesIO(content), sheet_name=SHEET, header=None)
    hdr = next(i for i in range(10) if str(raw.iat[i, 0]).strip().lower().startswith("per"))
    names = [str(c).strip() for c in raw.iloc[hdr]]
    body = raw.iloc[hdr + 1:]
    dates = pd.to_datetime(body.iloc[:, 0], errors="coerce")
    out = {}
    for key, col in COLUMNS.items():
        if col not in names:
            raise ValueError(f"column {col!r} not found; layout changed?")
        v = pd.to_numeric(body.iloc[:, names.index(col)], errors="coerce")
        s = pd.Series(v.values, index=dates).dropna()
        s = s[s.index.notna()].sort_index()
        out[key] = s[~s.index.duplicated(keep="last")]
    # Base check: 17-Dec-2015 = 100 for the multilateral index.
    base = out["m"].get(pd.Timestamp("2015-12-17"))
    if base is None or abs(base - 100) > 0.5:
        raise ValueError(f"ITCRM on 17-Dec-2015 is {base}, expected 100: base changed?")
    return out


def fetch(ids) -> FetchResult:
    try:
        parsed = parse(download())
    except Exception as exc:
        return FetchResult(data=pd.DataFrame(columns=["date", "series_id", "value"]), errors={sid: str(exc) for sid in ids})
    frames, meta, errors = [], {}, {}
    for sid in ids:
        key = sid.split(":", 1)[1]
        s = parsed.get(key)
        if s is None or s.empty:
            errors[sid] = f"no data for {key!r}"
            continue
        frames.append(pd.DataFrame({"date": s.index, "series_id": sid, "value": s.values}))
        meta[sid] = {"units": "index, 17-Dec-2015 = 100", "time_index_end": s.index.max().strftime("%Y-%m-%d")}
    data = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["date", "series_id", "value"])
    return FetchResult(data=data, meta=meta, errors=errors)
