"""Adapter for the BCRA's export commodity price index (IPMP, Índice de Precios de las Materias Primas).

One workbook, "Serie histórica del IPMP" (current methodology, 2026): the index of the international
prices of Argentina's main commodity exports, each weighted by its share of exports, December
2001 = 100, in US dollars, with three sub-indices (agricultural products; metals and chemicals;
hydrocarbons). Sheets: daily, monthly, monthly in real terms (deflated by the BCRA), quarterly,
annual and the weights.

Ids: "ipmp:<group>" monthly nominal and "ipmp:<group>_real" monthly real, group in
total, agro, metals, energy; "ipmp:w_<product>" the monthly weights (%), product as in WEIGHTS.
"""
from __future__ import annotations

import io
import time

import pandas as pd
import requests

from . import FetchResult

URL = "https://www.bcra.gob.ar/archivos/Pdfs/PublicacionesEstadisticas/ipmp-serie-historica-metodologia-2026.xlsx"
PAGE = "https://www.bcra.gob.ar/precios-de-materias-primas/"
USER_AGENT = "argentina-economic-monitor (+https://github.com/ncachanosky/argentina-economic-monitor)"
GROUPS = ["total", "agro", "metals", "energy"]
SHEETS = {"": "IPMP mensual", "_real": "IPMP mensual real"}
WEIGHTS = {"Soja": "soy", "Trigo": "wheat", "Maiz": "corn", "Cebada": "barley", "Sorgo": "sorghum",
           "Aceite de soja": "soy_oil", "Aceite de Girasol": "sunflower_oil", "Pellets de soja": "soy_meal",
           "Leche": "milk", "Carne": "beef", "Petroleo": "oil", "Oro": "gold", "Cobre": "copper",
           "Aluminio": "aluminium", "Complejo plata": "silver", "Carbonato de litio": "lithium"}


def download(retries: int = 3) -> bytes:
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(URL, timeout=120, headers={"User-Agent": USER_AGENT})
            r.raise_for_status()
            if not r.content.startswith(b"PK"):
                raise IOError("not an xlsx file (the BCRA may have moved it; see " + PAGE + ")")
            return r.content
        except Exception as exc:
            last = exc
            time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"could not download the IPMP workbook: {last}")


def parse(content: bytes) -> pd.DataFrame:
    """Long frame (date at month start, series_id, value)."""
    x = pd.ExcelFile(io.BytesIO(content))
    frames = []
    for suffix, sheet in SHEETS.items():
        d = x.parse(sheet, header=0)
        d = d[pd.to_datetime(d.iloc[:, 0], errors="coerce").notna()]
        dates = pd.to_datetime(d.iloc[:, 0]).dt.to_period("M").dt.to_timestamp()
        for k, g in enumerate(GROUPS):
            frames.append(pd.DataFrame({"date": dates, "series_id": f"ipmp:{g}{suffix}", "value": pd.to_numeric(d.iloc[:, 1 + k], errors="coerce")}))
    w = x.parse("Ponderadores", header=0)
    w = w[pd.to_datetime(w.iloc[:, 0], errors="coerce").notna()]
    wd = pd.to_datetime(w.iloc[:, 0]).dt.to_period("M").dt.to_timestamp()
    for col in w.columns[1:]:
        key = WEIGHTS.get(str(col).strip())
        if key:
            frames.append(pd.DataFrame({"date": wd, "series_id": f"ipmp:w_{key}", "value": pd.to_numeric(w[col], errors="coerce")}))
    out = pd.concat(frames, ignore_index=True).dropna()
    if not (out["series_id"] == "ipmp:total").any():
        raise ValueError("IPMP workbook: no total index (layout changed?)")
    return out


def fetch(ids) -> FetchResult:
    empty = pd.DataFrame(columns=["date", "series_id", "value"])
    try:
        df = parse(download())
    except Exception as exc:
        return FetchResult(data=empty, errors={sid: f"ipmp: {exc}" for sid in ids})
    df = df[df["series_id"].isin(ids)]
    errors = {sid: "not in the IPMP workbook" for sid in ids if sid not in set(df["series_id"])}
    return FetchResult(data=df.reset_index(drop=True), meta={sid: {"units": "%" if ":w_" in sid else "index, Dec 2001 = 100"} for sid in ids}, errors=errors)
