"""Adapter for the BCRA's balance cambiario: the foreign-exchange market's flows by
concept, monthly since January 2003, millions of US dollars.

Source: the statistical annex of the "Informe de Evolución del Mercado de Cambios y
Balance Cambiario" (sheet "Balance Cambiario"), whose columns carry codes bal000,
bal001, ... above the data. Series ids are "bcrabc:<key>" (see CODES).

The labels above each column sit one column off their data in places, so the codes
are mapped by meaning and checked by identity each run: the current account plus
the capital and financial accounts equals the change in reserves from transactions.
"""
from __future__ import annotations

import io
import time

import pandas as pd
import requests

from . import FetchResult

URL = "https://www.bcra.gob.ar/archivos/Pdfs/PublicacionesEstadisticas/informes/anexo-estadistico-mercado-cambios-balance-cambiario.xlsx"
TIMEOUT = 240
USER_AGENT = "argentina-economic-monitor (+https://github.com/ncachanosky/argentina-economic-monitor)"
CODES = {
    "current": "bal001",        # current account
    "goods": "bal002",          # goods: export receipts minus import payments
    "services": "bal005",
    "primary": "bal008",        # interest, profits and dividends
    "interest": "bal009",
    "secondary": "bal019",
    "capital": "bal023",
    "financial": "bal024",      # financial account, total
    "fdi": "bal025",            # direct investment of non-residents
    "portfolio": "bal028",
    "loans": "bal031",          # financial loans, debt securities and credit lines
    "imf": "bal034",
    "ifis": "bal037",           # other international organizations and bilateral
    "cash_purchases": "bal040",  # purchase and sale of banknotes and foreign currency with no specific purpose
    "reserves": "bal050",       # change in reserves from foreign-exchange transactions
}


def download(retries: int = 4) -> bytes:
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(URL, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
            r.raise_for_status()
            return r.content
        except Exception as exc:
            last = exc
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"balance cambiario: {last}")


def parse(content: bytes) -> dict[str, pd.Series]:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    rows = list(wb["Balance Cambiario"].iter_rows(values_only=True))
    ci = next(i for i, r in enumerate(rows[:30]) if any(isinstance(c, str) and c.strip() == "bal001" for c in r))
    col = {str(c).strip(): j for j, c in enumerate(rows[ci]) if isinstance(c, str) and c.strip().startswith("bal")}
    data = [r for r in rows[ci + 1:] if r and hasattr(r[0], "year")]
    out = {}
    for key, code in CODES.items():
        j = col[code]
        out[key] = pd.Series({pd.Timestamp(r[0].year, r[0].month, 1): float(r[j]) for r in data
                              if j < len(r) and isinstance(r[j], (int, float))}).sort_index()
    # Identity check: current + capital + financial = change in reserves from transactions.
    chk = (out["current"] + out["capital"] + out["financial"] - out["reserves"]).abs().dropna()
    if len(chk) and chk.tail(36).max() > 5:
        raise ValueError(f"balance cambiario columns do not add up (max gap {chk.tail(36).max():.0f}); layout changed?")
    gd = (out["goods"] + out["services"] + out["primary"] + out["secondary"] - out["current"]).abs().dropna()
    if len(gd) and gd.tail(36).max() > 5:
        raise ValueError("current account components do not add up; layout changed?")
    return out


def fetch(ids, content: bytes | None = None) -> FetchResult:
    try:
        parsed = parse(content if content is not None else download())
    except Exception as exc:
        return FetchResult(data=pd.DataFrame(columns=["date", "series_id", "value"]),
                           errors={sid: str(exc) for sid in ids})
    frames, meta, errors = [], {}, {}
    for sid in ids:
        key = sid.split(":", 1)[1]
        if key not in parsed:
            errors[sid] = f"unknown series {key!r}"
            continue
        s = parsed[key]
        frames.append(pd.DataFrame({"date": s.index, "series_id": sid, "value": s.values}))
        meta[sid] = {"units": "millions of US dollars", "time_index_end": s.index.max().strftime("%Y-%m-%d")}
    data = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["date", "series_id", "value"])
    return FetchResult(data=data, meta=meta, errors=errors)
