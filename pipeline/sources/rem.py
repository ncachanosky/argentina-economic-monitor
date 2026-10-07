"""Adapter for the BCRA's market expectations survey (Relevamiento de Expectativas de
Mercado, REM), historical workbook, sheet "Base de Datos Completa".

Series ids: "rem:fx12" -- median expected official (wholesale) exchange rate for the
month twelve months ahead ("Próx. 12 meses"), pesos per US dollar, dated by the
month of the survey.
"""
from __future__ import annotations

import io
import time

import pandas as pd
import requests

from . import FetchResult

URL = "https://www.bcra.gob.ar/archivos/Pdfs/PublicacionesEstadisticas/informes/relevamiento-expectativas-mercado-historico.xlsx"
TIMEOUT = 180
USER_AGENT = "argentina-economic-monitor (+https://github.com/ncachanosky/argentina-economic-monitor)"


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
    raise RuntimeError(f"REM: {last}")


def parse(content: bytes) -> dict[str, pd.Series]:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb["Base de Datos Completa"]
    rows = ws.iter_rows(values_only=True)
    hdr = None
    fx = {}
    for r in rows:
        if hdr is None:
            if r and r[0] == "Fecha de pronóstico":
                hdr = [str(c).strip() if c else "" for c in r]
                im = hdr.index("Mediana")
            continue
        if not r or not hasattr(r[0], "year"):
            continue
        var, period = str(r[1] or ""), str(r[3] or "")
        if var.startswith("Tipo de cambio nominal") and period.lower().startswith("próx. 12"):
            if isinstance(r[im], (int, float)):
                fx[pd.Timestamp(r[0].year, r[0].month, 1)] = float(r[im])
    if not fx:
        raise ValueError("no exchange-rate expectations found")
    return {"fx12": pd.Series(fx).sort_index()}


def fetch(ids, content: bytes | None = None) -> FetchResult:
    try:
        parsed = parse(content if content is not None else download())
    except Exception as exc:
        return FetchResult(data=pd.DataFrame(columns=["date", "series_id", "value"]), errors={sid: str(exc) for sid in ids})
    frames, meta, errors = [], {}, {}
    for sid in ids:
        key = sid.split(":", 1)[1]
        if key not in parsed:
            errors[sid] = f"unknown series {key!r}"
            continue
        s = parsed[key]
        frames.append(pd.DataFrame({"date": s.index, "series_id": sid, "value": s.values}))
        meta[sid] = {"units": None, "time_index_end": s.index.max().strftime("%Y-%m-%d")}
    data = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["date", "series_id", "value"])
    return FetchResult(data=data, meta=meta, errors=errors)
