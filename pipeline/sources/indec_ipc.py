"""INDEC's own CPI file, used for the CPI series the Monitor otherwise reads from datos.gob.ar.

  https://www.indec.gob.ar/ftp/cuadros/economia/serie_ipc_divisiones.csv

The file has every index since December 2016 (= 100) by region (national and the six
regions), for the general level, the twelve COICOP divisions, the three categories
(seasonal, core, regulated) and goods/services. INDEC updates it on the day of the
release; datos.gob.ar republishes the same numbers, sometimes days later, and since
October 2026 its API has been returning incomplete answers. The values are identical
(checked over the full history), so the series keep their datos.gob.ar ids: the
datos_gob adapter asks this module first and falls back to the API.
"""
from __future__ import annotations

import io
import time

import pandas as pd
import requests

URL = "https://www.indec.gob.ar/ftp/cuadros/economia/serie_ipc_divisiones.csv"
TIMEOUT = 90
USER_AGENT = "argentina-economic-monitor (+https://github.com/ncachanosky/argentina-economic-monitor)"

# datos.gob.ar id -> (INDEC Codigo, Region)
MAP = {
    "148.3_INIVELNAL_DICI_M_26": ("0", "Nacional"),
    "148.3_INUCLEONAL_DICI_M_19": ("Núcleo", "Nacional"),
    "148.3_IREGULANAL_DICI_M_22": ("Regulados", "Nacional"),
    "148.3_IESTACINAL_DICI_M_25": ("Estacional", "Nacional"),
    "148.3_INIVELGBA_DICI_M_21": ("0", "GBA"),
    "148.3_INIVELANA_DICI_M_26": ("0", "Pampeana"),
    "148.3_INIVELNEA_DICI_M_21": ("0", "Noreste"),
    "148.3_INIVELNOA_DICI_M_21": ("0", "Noroeste"),
    "148.3_INIVELUYO_DICI_M_22": ("0", "Cuyo"),
    "148.3_INIVELNIA_DICI_M_27": ("0", "Patagonia"),
    "146.3_IALIMENNAL_DICI_M_45": ("01", "Nacional"),
    "146.3_IBEBIDANAL_DICI_M_39": ("02", "Nacional"),
    "146.3_IPRENDANAL_DICI_M_35": ("03", "Nacional"),
    "146.3_IVIVIENNAL_DICI_M_52": ("04", "Nacional"),
    "146.3_IEQUIPANAL_DICI_M_46": ("05", "Nacional"),
    "146.3_ISALUDNAL_DICI_M_18": ("06", "Nacional"),
    "146.3_ITRANSPNAL_DICI_M_23": ("07", "Nacional"),
    "146.3_ICOMUNINAL_DICI_M_27": ("08", "Nacional"),
    "146.3_IRECREANAL_DICI_M_31": ("09", "Nacional"),
    "146.3_IEDUCACNAL_DICI_M_22": ("10", "Nacional"),
    "146.3_IRESTAUNAL_DICI_M_33": ("11", "Nacional"),
    "146.3_IBIENESNAL_DICI_M_36": ("12", "Nacional"),
}


def download(retries: int = 3) -> bytes:
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(URL, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
            r.raise_for_status()
            return r.content
        except Exception as exc:
            last = exc
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"INDEC CPI file: {last}")


def parse(raw: bytes) -> pd.DataFrame:
    """Long frame: Codigo, Region, date, value (index, Dec 2016 = 100)."""
    text = raw.decode("utf-8") if raw[:3] == b"\xef\xbb\xbf" else raw.decode("latin-1")
    df = pd.read_csv(io.StringIO(text.lstrip("﻿")), sep=";", dtype=str)
    need = {"Codigo", "Periodo", "Indice_IPC", "Region"}
    if not need <= set(df.columns):
        raise ValueError(f"unexpected columns {list(df.columns)}; layout changed?")
    df["date"] = pd.to_datetime(df["Periodo"].str.strip(), format="%Y%m")
    df["value"] = pd.to_numeric(df["Indice_IPC"].str.replace(".", "", regex=False).str.replace(",", ".", regex=False),
                                errors="coerce")
    df["Codigo"] = df["Codigo"].str.strip()
    df["Region"] = df["Region"].str.strip()
    return df.dropna(subset=["value"])[["Codigo", "Region", "date", "value"]]


def fetch(ids) -> tuple[pd.DataFrame, dict, set]:
    """Series for the mapped ids among `ids`: (long data, meta, ids served)."""
    wanted = [sid for sid in ids if sid in MAP]
    if not wanted:
        return pd.DataFrame(columns=["date", "series_id", "value"]), {}, set()
    df = parse(download())
    frames, meta = [], {}
    for sid in wanted:
        code, region = MAP[sid]
        s = df[(df["Codigo"] == code) & (df["Region"] == region)].sort_values("date")
        if s.empty:
            continue
        frames.append(pd.DataFrame({"date": s["date"].values, "series_id": sid, "value": s["value"].values}))
        meta[sid] = {"units": "Índice", "description": None,
                     "time_index_end": s["date"].max().strftime("%Y-%m-%d"),
                     "dataset": "INDEC, serie_ipc_divisiones.csv"}
    data = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["date", "series_id", "value"])
    return data, meta, set(meta)
