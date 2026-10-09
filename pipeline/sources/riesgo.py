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

Series id: "riesgo:embi".
"""
from __future__ import annotations

import time

import pandas as pd
import requests

from . import FetchResult

HISTORY = "https://api.argentinadatos.com/v1/finanzas/indices/riesgo-pais"
LATEST = HISTORY + "/ultimo"
TIMEOUT = 60
USER_AGENT = "argentina-economic-monitor (+https://github.com/ncachanosky/argentina-economic-monitor)"
MAX_JUMP = 0.40


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
    return s[s.index.dayofweek < 5]


def fetch(ids) -> FetchResult:
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
