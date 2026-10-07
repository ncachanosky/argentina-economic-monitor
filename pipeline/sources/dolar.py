"""Adapter for market exchange rates that no official body publishes: the informal
("blue") dollar, the MEP and CCL rates implied by bond trades, the card ("tarjeta")
rate and the crypto dollar.

Sources (unofficial; both are open-source community projects that compile quotes
published by the financial press):
  ArgentinaDatos  https://api.argentinadatos.com/v1/cotizaciones/dolares/<casa>
                  daily history (blue since 2011, CCL since 2013, MEP since 2018)
  DolarAPI        https://dolarapi.com/v1/dolares  -- today's quotes

Safeguards: the latest value of each series is checked against the other source
when both have the same day; a disagreement of more than MAX_DISAGREEMENT makes
the series fail validation for that run (the store keeps the last good data and
the site marks it stale). DolarAPI's quote fills in today when the history has
not caught up yet. Values are selling ("venta") prices, pesos per US dollar.

Series ids: "dolar:<key>" with key in SERIES.
"""
from __future__ import annotations

import time

import pandas as pd
import requests

from . import FetchResult

HISTORY = "https://api.argentinadatos.com/v1/cotizaciones/dolares/{casa}"
LATEST = "https://dolarapi.com/v1/dolares"
TIMEOUT = 60
USER_AGENT = "argentina-economic-monitor (+https://github.com/ncachanosky/argentina-economic-monitor)"
SERIES = {"blue": "blue", "mep": "bolsa", "ccl": "contadoconliqui", "tarjeta": "tarjeta", "cripto": "cripto"}
MAX_DISAGREEMENT = 0.05


def _get(url: str, retries: int = 3):
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
            r.raise_for_status()
            return r.json()
        except Exception as exc:  # network hiccups: retry with backoff
            last = exc
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"{url}: {last}")


def history(rows: list[dict]) -> pd.Series:
    s = pd.Series({pd.Timestamp(r["fecha"]): float(r["venta"]) for r in rows
                   if r.get("fecha") and r.get("venta") not in (None, 0)}).sort_index()
    return s[~s.index.duplicated(keep="last")]


def combine(hist: pd.Series, latest: dict | None) -> tuple[pd.Series, str | None]:
    """History plus today's quote; an error message if the two sources disagree."""
    if not latest or latest.get("venta") in (None, 0):
        return hist, None
    day = pd.Timestamp(str(latest.get("fechaActualizacion", ""))[:10])
    v = float(latest["venta"])
    if day in hist.index:
        if abs(hist[day] / v - 1) > MAX_DISAGREEMENT:
            return hist, f"sources disagree on {day:%Y-%m-%d}: {hist[day]} vs {v}"
        return hist, None
    if len(hist) and day > hist.index.max():
        prev = hist.iloc[-1]
        if abs(v / prev - 1) > 0.25:
            return hist, f"today's quote {v} is {v / prev - 1:+.0%} from the last close {prev}"
        hist = pd.concat([hist, pd.Series({day: v})])
    return hist, None


def fetch(ids) -> FetchResult:
    frames, meta, errors = [], {}, {}
    try:
        latest = {d.get("casa"): d for d in _get(LATEST)}
    except Exception:
        latest = {}                       # history alone is still usable
    for sid in sorted(ids):
        key = sid.split(":", 1)[1]
        casa = SERIES.get(key)
        if casa is None:
            errors[sid] = f"unknown series {key!r}"
            continue
        try:
            s, problem = combine(history(_get(HISTORY.format(casa=casa))), latest.get(casa))
        except Exception as exc:
            errors[sid] = str(exc)
            continue
        if problem:
            errors[sid] = problem
            continue
        frames.append(pd.DataFrame({"date": s.index, "series_id": sid, "value": s.values}))
        meta[sid] = {"units": "pesos per US dollar (selling)", "time_index_end": s.index.max().strftime("%Y-%m-%d")}
    data = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["date", "series_id", "value"])
    return FetchResult(data=data, meta=meta, errors=errors)
