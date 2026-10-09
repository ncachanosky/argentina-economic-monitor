"""Adapter for the BCRA's market expectations survey (Relevamiento de Expectativas de
Mercado, REM), historical workbook, sheet "Base de Datos Completa": one row per survey,
variable and forecast period, with the median, mean, percentiles and number of
respondents. The survey closes in the last days of each month; it is dated here by
that month.

Series ids ("rem:<key>"), by survey month:
  fx12                      median expected official (wholesale) rate twelve months ahead
  cpi12, cpi12_p10 ... _p90 expected CPI inflation over the next twelve months, %:
                            median and percentiles 10, 25, 75, 90

Tables (write_tables, data/tables/rem/):
  paths.json      the latest survey's expected path, month by month or quarter by
                  quarter, for CPI (headline, core), the exchange rate, TAMAR, GDP
                  (q/q, s.a.) and unemployment: median, p25, p75
  revisions.json  every survey's forecast for each calendar year: December CPI
                  inflation (y/y), GDP growth (annual average), the December
                  exchange rate and the primary balance of the non-financial
                  public sector (billions of pesos)
"""
from __future__ import annotations

import io
import json
import re
import time
from pathlib import Path

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


VARS = {   # workbook variable (lowercase, prefix) -> key
    "precios minoristas (ipc nivel general; indec)": "cpi",
    "precios minoristas (ipc núcleo; indec)": "core",
    "tipo de cambio nominal": "fx",
    "tasa de interés (tamar)": "tamar",
    "pib a precios constantes": "gdp",
    "desocupación abierta": "unemp",
    "resultado primario del spnf": "primary",
}
PATH_VARS = {"cpi": "m", "core": "m", "fx": "m", "tamar": "m", "gdp": "q", "unemp": "q"}
YEAR_VARS = ["cpi", "gdp", "fx", "primary"]
STATS = {"Mediana": "med", "Percentil 10": "p10", "Percentil 25": "p25", "Percentil 75": "p75", "Percentil 90": "p90",
         "Cantidad de participantes": "n"}
LAST: pd.DataFrame | None = None
QUARTER = re.compile(r"Trim\.\s*(I{1,3}|IV)-(\d{2})", re.I)
ROMAN = {"I": 1, "II": 4, "III": 7, "IV": 10}


def _period(p):
    """(kind, Timestamp | None): m = month, q = quarter, y = calendar year, n12/n24 = next months."""
    if hasattr(p, "year"):
        return "m", pd.Timestamp(p.year, p.month, 1)
    t = str(p).strip()
    if re.fullmatch(r"\d{4}", t):
        return "y", pd.Timestamp(int(t), 1, 1)
    m = QUARTER.search(t)
    if m:
        return "q", pd.Timestamp(2000 + int(m.group(2)), ROMAN[m.group(1).upper()], 1)
    if t.lower().startswith("próx. 12"):
        return "n12", None
    if t.lower().startswith("próx. 24"):
        return "n24", None
    return None, None


def parse(content: bytes) -> pd.DataFrame:
    """Long frame: survey (month start), var, kind, period, med, p10, p25, p75, p90, n."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb["Base de Datos Completa"]
    hdr, out = None, []
    for r in ws.iter_rows(values_only=True):
        if hdr is None:
            if r and r[0] == "Fecha de pronóstico":
                hdr = [str(c).strip() if c else "" for c in r]
                cols = {k: hdr.index(h) for h, k in STATS.items() if h in hdr}
                if "med" not in cols:
                    raise ValueError("no 'Mediana' column; layout changed?")
            continue
        if not r or not hasattr(r[0], "year"):
            continue
        name = str(r[1] or "").strip().lower()
        var = next((k for v, k in VARS.items() if name == v), None)
        if var is None:
            continue
        kind, period = _period(r[3])
        if kind is None:
            continue
        row = {"survey": pd.Timestamp(r[0].year, r[0].month, 1), "var": var, "kind": kind, "period": period}
        for k, i in cols.items():
            v = r[i]
            row[k] = float(v) if isinstance(v, (int, float)) else None
        out.append(row)
    df = pd.DataFrame(out)
    if not df.empty:   # the workbook spells some variables two ways; keep one row per survey and period
        df = df.drop_duplicates(["survey", "var", "kind", "period"], keep="last")
    if df.empty or not {"cpi", "fx"} <= set(df["var"]):
        raise ValueError("no inflation or exchange-rate expectations found")
    return df


def series(df: pd.DataFrame) -> dict[str, pd.Series]:
    out = {}
    fx = df[(df["var"] == "fx") & (df["kind"] == "n12")].groupby("survey")["med"].last().dropna()
    out["fx12"] = fx.sort_index()
    c = df[(df["var"] == "cpi") & (df["kind"] == "n12")].groupby("survey").last()
    out["cpi12"] = c["med"].dropna().sort_index()
    for q in ("p10", "p25", "p75", "p90"):
        if q in c:
            out[f"cpi12_{q}"] = c[q].dropna().sort_index()
    return out


def _num(v):
    return None if v is None or v != v else float(v)


def paths(df: pd.DataFrame) -> dict:
    last = df["survey"].max()
    d = df[df["survey"] == last]
    out = {"survey": last.strftime("%Y-%m-%d"), "latest": last.strftime("%Y-%m-%d"), "vars": {}}
    for var, kind in PATH_VARS.items():
        g = d[(d["var"] == var) & (d["kind"] == kind)].sort_values("period")
        if g.empty:
            continue
        out["vars"][var] = {"freq": "M" if kind == "m" else "Q", "points": [
            {"date": r.period.strftime("%Y-%m-%d"), "med": r.med, "p25": _num(getattr(r, "p25", None)), "p75": _num(getattr(r, "p75", None)), "n": _num(getattr(r, "n", None))}
            for r in g.itertuples() if r.med is not None]}
    return out


def revisions(df: pd.DataFrame) -> dict:
    out = {"latest": df["survey"].max().strftime("%Y-%m-%d"), "vars": {}}
    for var in YEAR_VARS:
        g = df[(df["var"] == var) & (df["kind"] == "y")].dropna(subset=["med"]).sort_values("survey")
        out["vars"][var] = {str(y.year): [[r.survey.strftime("%Y-%m-%d"), r.med, _num(r.p25), _num(r.p75)] for r in gy.itertuples()]
                    for y, gy in g.groupby("period")}
    return out


def fetch(ids, content: bytes | None = None) -> FetchResult:
    global LAST
    try:
        df = parse(content if content is not None else download())
        parsed = series(df)
        LAST = df
    except Exception as exc:
        return FetchResult(data=pd.DataFrame(columns=["date", "series_id", "value"]), errors={sid: str(exc) for sid in ids})
    frames, meta, errors = [], {}, {}
    for sid in ids:
        key = sid.split(":", 1)[1]
        if key not in parsed or parsed[key].empty:
            errors[sid] = f"unknown series {key!r}"
            continue
        s = parsed[key]
        frames.append(pd.DataFrame({"date": s.index, "series_id": sid, "value": s.values}))
        meta[sid] = {"units": None, "time_index_end": s.index.max().strftime("%Y-%m-%d")}
    data = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["date", "series_id", "value"])
    return FetchResult(data=data, meta=meta, errors=errors)


def write_tables(directory) -> list[str]:
    if LAST is None or LAST.empty:
        return []
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    changed = []
    for name, obj in (("paths.json", paths(LAST)), ("revisions.json", revisions(LAST))):
        p = d / name
        text = json.dumps(obj, separators=(",", ":"), allow_nan=False)
        if not p.exists() or p.read_text(encoding="utf-8") != text:
            p.write_text(text, encoding="utf-8")
            changed.append(name)
    return changed
