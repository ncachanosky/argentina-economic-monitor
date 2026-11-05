"""Adapter for Argentina's country risk ("riesgo país"): JP Morgan's EMBI spread
for Argentina, in basis points over US Treasuries, daily since 1999.

Source (unofficial): ArgentinaDatos, an open-source community project, which
compiles the series as published by Ámbito Financiero.
  history  https://api.argentinadatos.com/v1/finanzas/indices/riesgo-pais
  latest   https://api.argentinadatos.com/v1/finanzas/indices/riesgo-pais/ultimo

Cleaning: weekend rows are dropped (in 2018-2024 the source repeats Friday's
value on Saturdays and Sundays). The latest value is checked against the
history; a jump of more than MAX_JUMP from the previous close in the newest
observation is reported as a warning, since changes in the bonds included in
the index produce one-day breaks (June 2005, September 2020) worth marking.

Corrections: data/manual/riesgo_corrections.csv (date, value, note) holds
hand-checked fixes to the source's history; a row with an empty value drops
that day, a row with a value replaces it. Checked and kept as published: 30 July
2014 (560, a one-day rally on hopes of a deal before the default).

Series id: "riesgo:embi".

Other countries (riesgo:<code>, e.g. riesgo:br) come from the Banco Central de la República
Dominicana (BCRD), which republishes JP Morgan's EMBI Global Diversified subindices daily
since October 2007 in one workbook (spreads in percentage points, converted to basis points
here). Its Argentina column tracks ArgentinaDatos within a few basis points; it is the
fallback for riesgo:embi when ArgentinaDatos fails. Its Latin America aggregate is not used:
it has level breaks when countries enter or leave the index (April 2024, September 2026).
A one-day move of more than SPIKE that the next day undoes, with the days around it within
10% of each other, is dropped as a glitch (Uruguay, 23 October 2025: 41 between 69 and 70).
"""
from __future__ import annotations

import time

import pandas as pd
import requests

from ..registry import ROOT

from . import FetchResult

HISTORY = "https://api.argentinadatos.com/v1/finanzas/indices/riesgo-pais"
LATEST = HISTORY + "/ultimo"
TIMEOUT = 60
USER_AGENT = "argentina-economic-monitor (+https://github.com/ncachanosky/argentina-economic-monitor)"
MAX_JUMP = 0.40
CORRECTIONS = ROOT / "data" / "manual" / "riesgo_corrections.csv"
BCRD = "https://cdn.bancentral.gov.do/documents/entorno-internacional/documents/Serie_Historica_Spread_del_EMBI.xlsx"
BCRD_PAGE = "https://www.bancentral.gov.do/a/d/2585-entorno-internacional"
# id code -> column in the BCRD workbook
BCRD_COLS = {"ar": "Argentina", "br": "Brasil", "cl": "Chile", "co": "Colombia", "mx": "México", "pe": "Perú",
             "uy": "Uruguay", "ec": "Ecuador", "bo": "Bolivia", "py": "Paraguay", "pa": "Panamá"}
SPIKE = 0.20


def _get(url: str, retries: int = 3):
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            last = exc
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"{url}: {last}")


def clean(rows: list[dict], latest: dict | None = None) -> pd.Series:
    s = pd.Series({pd.Timestamp(r["fecha"]): float(r["valor"]) for r in rows
                   if r.get("fecha") and r.get("valor") not in (None, 0)}).sort_index()
    if latest and latest.get("fecha") and latest.get("valor"):
        d = pd.Timestamp(latest["fecha"])
        if d not in s.index:
            s = pd.concat([s, pd.Series({d: float(latest["valor"])})]).sort_index()
    s = s[~s.index.duplicated(keep="last")]
    return correct(s[s.index.dayofweek < 5])


def correct(s: pd.Series, path=CORRECTIONS) -> pd.Series:
    """Apply the hand-checked corrections: an empty value drops the day, a value replaces it."""
    try:
        c = pd.read_csv(path, parse_dates=["date"])
    except FileNotFoundError:
        return s
    s = s.copy()
    for _, r in c.iterrows():
        if pd.isna(r["value"]):
            s = s.drop(r["date"], errors="ignore")
        elif r["date"] in s.index:
            s[r["date"]] = float(r["value"])
    return s


def _norm(x) -> str:
    import unicodedata
    return unicodedata.normalize("NFKD", str(x)).encode("ascii", "ignore").decode().strip().lower()


def parse_bcrd(raw: bytes) -> dict[str, pd.Series]:
    """BCRD workbook -> code -> spread in basis points (business days, glitches dropped)."""
    import io
    df = pd.read_excel(io.BytesIO(raw), header=None)
    hrow = next((i for i in range(min(10, len(df))) if _norm(df.iat[i, 0]) == "fecha"), None)
    if hrow is None:
        raise RuntimeError("BCRD EMBI workbook: no 'Fecha' header in the first rows")
    cols = {_norm(v): j for j, v in enumerate(df.iloc[hrow]) if pd.notna(v)}
    dates = pd.to_datetime(df.iloc[hrow + 1:, 0], errors="coerce")
    out = {}
    for code, name in BCRD_COLS.items():
        j = cols.get(_norm(name))
        if j is None:
            raise RuntimeError(f"BCRD EMBI workbook: no column '{name}' (columns: {list(cols)})")
        s = pd.Series(pd.to_numeric(df.iloc[hrow + 1:, j], errors="coerce").values, index=dates.values).dropna() * 100
        s = s[s.index.notna() & (s.index.dayofweek < 5) & (s > 0)].sort_index()
        s = s[~s.index.duplicated(keep="last")]
        out[code] = drop_spikes(s)
    return out


def drop_spikes(s: pd.Series) -> pd.Series:
    p, n = s.shift(1), s.shift(-1)
    spike = ((s / p - 1).abs() > SPIKE) & ((s / n - 1).abs() > SPIKE) & ((n / p - 1).abs() < 0.10)
    return s[~spike]


def _get_bytes(url: str, retries: int = 3) -> bytes:
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
            r.raise_for_status()
            return r.content
        except Exception as exc:
            last = exc
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"{url}: {last}")


def _frame(sid: str, s: pd.Series) -> pd.DataFrame:
    return pd.DataFrame({"date": s.index, "series_id": sid, "value": s.values})


def fetch(ids) -> FetchResult:
    ids = list(ids)
    ar_ids = [i for i in ids if i == "riesgo:embi"]
    region = [i for i in ids if i != "riesgo:embi"]
    frames, meta, errors, warnings = [], {}, {}, {}
    bcrd, bcrd_err = None, None
    if region or ar_ids:
        try:
            bcrd = parse_bcrd(_get_bytes(BCRD))
        except Exception as exc:
            bcrd_err = str(exc)
    for sid in region:
        code = sid.split(":", 1)[1]
        if bcrd is None or code not in bcrd or bcrd[code].empty:
            errors[sid] = bcrd_err or f"no BCRD column for '{code}'"
            continue
        s = bcrd[code]
        frames.append(_frame(sid, s))
        meta[sid] = {"units": "basis points", "time_index_end": s.index.max().strftime("%Y-%m-%d"),
                     "dataset": "JP Morgan EMBI Global Diversified, via the Banco Central de la República Dominicana"}
    if ar_ids:
        r = fetch_argentina(ar_ids)
        if r.errors and bcrd is not None and not bcrd.get("ar", pd.Series(dtype=float)).empty:
            r = _argentina_fallback(ar_ids, bcrd["ar"], next(iter(r.errors.values())))
        frames.append(r.data)
        meta.update(r.meta)
        errors.update(r.errors)
        warnings.update(r.warnings)
    data = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["date", "series_id", "value"])
    return FetchResult(data=data, meta=meta, errors=errors, warnings=warnings)


def _argentina_fallback(ids, s: pd.Series, why: str) -> FetchResult:
    """ArgentinaDatos failed: new days from the BCRD's Argentina column, appended to the stored history."""
    from .. import store
    old = store.as_of(ids[0]).dropna()
    new = s[s.index > old.index.max()] if len(old) else s
    merged = pd.concat([old, new]).sort_index()
    return FetchResult(data=pd.concat([_frame(sid, merged) for sid in ids], ignore_index=True),
                       meta={sid: {"units": "basis points", "time_index_end": merged.index.max().strftime("%Y-%m-%d")} for sid in ids},
                       warnings={sid: [f"ArgentinaDatos failed ({why[:120]}); days after {old.index.max():%Y-%m-%d} "
                                       "taken from the BCRD's EMBI file"] for sid in ids} if len(new) else {})


def fetch_argentina(ids) -> FetchResult:
    empty = pd.DataFrame(columns=["date", "series_id", "value"])
    try:
        rows = _get(HISTORY)
    except Exception as exc:
        return FetchResult(data=empty, errors={sid: str(exc) for sid in ids})
    try:
        latest = _get(LATEST)
    except Exception:
        latest = None
    s = clean(rows, latest)
    if len(s) < 2:
        return FetchResult(data=empty, errors={sid: "too few observations" for sid in ids})
    jump = s.iloc[-1] / s.iloc[-2] - 1
    warnings = {}
    if abs(jump) > MAX_JUMP:
        warnings = {sid: [f"latest value {s.iloc[-1]:.0f} is {jump:+.0%} from the previous close: "
                          "check for a change in the index's bonds and mark it on the chart (registry bands)"] for sid in ids}
    data = pd.concat([pd.DataFrame({"date": s.index, "series_id": sid, "value": s.values}) for sid in ids], ignore_index=True)
    meta = {sid: {"units": "basis points", "time_index_end": s.index.max().strftime("%Y-%m-%d")} for sid in ids}
    return FetchResult(data=data, meta=meta, warnings=warnings)
