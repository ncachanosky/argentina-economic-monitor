"""Nominal GDP against target paths (an NGDP-targeting benchmark).

Monthly nominal GDP, seasonally adjusted, at annual rates:
  quarters   INDEC's real GDP, seasonally adjusted (3.2_OGP_D_2004_T_17), times the
             implicit deflator (nominal / real, both original: 4.4_OGP / 4.2_OGP).
             Each quarter is placed at its middle month; months in between are
             interpolated log-linearly.
  proxy      after the last published quarter, the monthly EMAE (seasonally adjusted)
             times the CPI (long-run, corrected), chained to the last quarter: the
             quarter's NGDP times X_m / (average of X over the quarter's months).
             It is replaced by INDEC's figure when the quarter is published.
Real GDP is built the same way (EMAE alone for the proxy), so real growth and the
deflator can be read off the same months.

REM-implied NGDP growth, next twelve months, for each survey:
  (1 + expected CPI inflation, next 12 months) x (1 + expected real growth) - 1, where
  expected real growth over the next twelve months weighs the survey's forecasts for
  the current and the next calendar year by the months of each it covers.

Private M2 (currency held by the public + private current and savings accounts in
pesos), monthly average of daily data, for the money-and-velocity view: velocity is
nominal GDP (annual rate) over private M2.

Target paths and gaps are computed in the browser (site/js/app.js, ngdpCard), so the
reader can choose the base quarter and the rate.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from . import store
from .registry import ROOT

REAL_SA, REAL, NOM = "3.2_OGP_D_2004_T_17", "4.2_OGP_2004_T_17", "4.4_OGP_2004_T_17"
EMAE_SA = "143.3_NO_PR_2004_A_31"
M2_PRIVATE = ("bcra:17", "bcra:94", "bcra:95")
REM_TABLE = ROOT / "data" / "tables" / "rem" / "revisions.json"


def _mid(q: pd.Series) -> pd.Series:
    """Quarter-start dates -> the quarter's middle month."""
    q = q.dropna()
    q.index = q.index + pd.DateOffset(months=1)
    return q


def _monthly(q: pd.Series, x: pd.Series, last_q: pd.Timestamp) -> tuple[pd.Series, pd.Timestamp | None]:
    """Quarterly levels (at mid months) interpolated monthly, then chained forward with x."""
    months = pd.date_range(q.index.min(), q.index.max(), freq="MS")
    out = np.exp(np.log(q).reindex(months).interpolate(limit_area="inside"))
    qm = pd.date_range(last_q, periods=3, freq="MS")
    x = x.dropna()
    if not set(qm) <= set(x.index):
        return out, None
    anchor = x.reindex(qm).mean()
    ahead = x[x.index > q.index.max()]
    if ahead.empty:
        return out, None
    proxy = q.iloc[-1] * ahead / anchor
    return pd.concat([out, proxy]), ahead.index.min()


def rem_rate() -> pd.Series:
    """REM-implied nominal GDP growth over the next twelve months, %, by survey month."""
    cpi12 = store.as_of("rem:cpi12").dropna()
    try:
        gdp = json.loads(REM_TABLE.read_text())["vars"]["gdp"]
    except Exception:
        return pd.Series(dtype=float)
    by = {}   # survey -> {year: median growth}
    for year, rows in gdp.items():
        for survey, med, *_ in rows:
            if med is not None:
                by.setdefault(pd.Timestamp(survey), {})[int(year)] = med
    out = {}
    for s, inf in cpi12.items():
        f = by.get(s)
        if not f or s.year not in f:
            continue
        w = (12 - s.month) / 12          # months of the current year still ahead
        g = w * f[s.year] + (1 - w) * f.get(s.year + 1, f[s.year])
        out[s] = round(((1 + inf / 100) * (1 + g / 100) - 1) * 100, 2)
    return pd.Series(out).sort_index()


def build() -> dict:
    real_sa, real, nom = (store.as_of(k).dropna() for k in (REAL_SA, REAL, NOM))
    defl = (nom / real).dropna()
    ngdp_q = _mid(real_sa * defl)
    real_q = _mid(real_sa.reindex(defl.index))
    last_q = defl.index.max()
    cpi = _cpi()
    emae = store.as_of(EMAE_SA).dropna()
    ngdp_m, p0 = _monthly(ngdp_q, emae * cpi.reindex(emae.index), last_q)
    real_m, _ = _monthly(real_q, emae, last_q)
    idx = ngdp_m.index
    rr = rem_rate()
    # Private M2, monthly average of days with all three components.
    m2d = pd.concat([store.as_of(k) for k in M2_PRIVATE], axis=1).dropna()
    m2 = m2d.sum(axis=1).resample("MS").mean()
    m2 = m2[m2.index <= m2d.index.max().replace(day=1) - pd.DateOffset(days=1)]   # complete months only
    r = lambda s, d=1: [None if pd.isna(v) else round(float(v), d) for v in s]
    return {
        "dates": [d.strftime("%Y-%m-%d") for d in idx],
        "ngdp": r(ngdp_m.reindex(idx) / 1e6, 4),            # trillions of pesos, annual rate
        "real": r(real_m.reindex(idx) / 1e3, 2),            # billions of 2004 pesos, annual rate
        "m2": r(m2.reindex(idx) / 1e6, 4),                 # trillions of pesos, monthly average
        "proxy_from": p0.strftime("%Y-%m-%d") if p0 is not None else None,
        "last_quarter": last_q.strftime("%Y-%m-%d"),
        "quarters": [d.strftime("%Y-%m-%d") for d in defl.index],
        "rem": {"dates": [d.strftime("%Y-%m-%d") for d in rr.index], "rate": r(rr, 2)},
    }


def _cpi() -> pd.Series:
    """Long-run CPI (corrected for 2007-2016), monthly."""
    from . import derive, registry
    reg = registry.load()
    return derive._resolve("@cpi_long:corrected", reg, "M")


if __name__ == "__main__":
    b = build()
    print(b["dates"][0], b["dates"][-1], b["proxy_from"], b["last_quarter"], b["rem"]["dates"][-3:], b["rem"]["rate"][-3:])
