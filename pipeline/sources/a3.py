"""Adapter for dollar futures on A3 Mercados (formerly MATba-Rofex): daily closing
prices of the DLR contracts (US dollar against the BCRA's Communication A 3500
rate, monthly expiries), from A3's public market-data API.

    GET https://apicem.matbarofex.com.ar/api/v2/closing-prices?product=DLR&segment=Monedas
        &type=FUT&excludeEmptyVol=false&from=YYYY-MM-DD&to=YYYY-MM-DD&page=1&pageSize=N

The API is not documented publicly; results are validated like any other source and
our own vintages keep the history if it changes.

Series ids:
  a3:f12   futures price twelve months ahead, pesos per dollar: each day, the curve of
           settlement prices interpolated (log-linearly in days to expiry) at 365 days;
           extrapolated from the two longest contracts when the longest expires
           between 300 and 365 days ahead, empty otherwise
  a3:f6    the same at 182 days (the curve has reached six months for longer than a year)
  a3:oi    open interest, all DLR contracts, millions of US dollars (US$1,000 per contract)
Table (write_tables): a3/curve.json, the latest curve and those of about one and
three months earlier, with today's official rate and the band's limits projected.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import re
import time
from pathlib import Path

import pandas as pd
import requests

from . import FetchResult

URL = ("https://apicem.matbarofex.com.ar/api/v2/closing-prices?product=DLR&segment=Monedas&type=FUT"
       "&excludeEmptyVol=false&from={a}&to={b}&page={p}&pageSize={n}")
FIRST = dt.date(2019, 1, 1)
PAGE = 5000
TIMEOUT = 90
CONTRACT_USD = 1000
USER_AGENT = "argentina-economic-monitor (+https://github.com/ncachanosky/argentina-economic-monitor)"

LAST: pd.DataFrame | None = None


def _get(url: str, retries: int = 3) -> dict:
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            last = exc
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"A3 futures API: {last}")


def download(first: dt.date = FIRST, last: dt.date | None = None) -> list[dict]:
    last = last or dt.date.today()
    rows = []
    for y in range(first.year, last.year + 1):
        a, b = max(first, dt.date(y, 1, 1)), min(last, dt.date(y, 12, 31))
        page = 1
        while True:
            d = _get(URL.format(a=a, b=b, p=page, n=PAGE))
            batch = d.get("data") or []
            rows += batch
            if len(batch) < PAGE:
                break
            page += 1
    return rows


def expiry(symbol: str) -> dt.date | None:
    """DLRmmyyyy -> last calendar day of that month (contracts expire on its last business day)."""
    m = re.fullmatch(r"DLR(\d{2})(\d{4})", symbol or "")
    if not m:
        return None
    mo, y = int(m.group(1)), int(m.group(2))
    return (dt.date(y + (mo == 12), mo % 12 + 1, 1) - dt.timedelta(days=1))


def frame(rows: list[dict]) -> pd.DataFrame:
    out = []
    for r in rows:
        e = expiry(r.get("symbol"))
        s = r.get("settlement")
        if not e or not s or s <= 0:
            continue
        d = dt.date.fromisoformat(r["dateTime"][:10])
        if e < d:
            continue
        out.append({"date": pd.Timestamp(d), "symbol": r["symbol"], "expiry": pd.Timestamp(e), "days": (e - d).days,
                    "settlement": float(s), "implied_rate": r.get("impliedRate"),
                    "oi": float(r.get("openInterest") or 0), "volume": float(r.get("volume") or 0)})
    df = pd.DataFrame(out)
    return df.drop_duplicates(["date", "symbol"], keep="last").sort_values(["date", "days"]) if len(df) else df


def at_horizon(curve: pd.DataFrame, h: int = 365) -> float | None:
    """Settlement price at `h` days, log-linear in days to expiry; extrapolated from the two
    longest contracts when the longest expires at least 82% of the way to `h`."""
    c = curve[curve.days > 0].sort_values("days")
    if len(c) < 2 or c.days.max() < 0.82 * h:
        return None
    x, y = c.days.to_numpy(float), c.settlement.map(math.log).to_numpy(float)
    if h <= x[-1]:
        j = int((x >= h).argmax())
        if j == 0:
            return None
        w = (h - x[j - 1]) / (x[j] - x[j - 1])
        return math.exp(y[j - 1] + w * (y[j] - y[j - 1]))
    slope = (y[-1] - y[-2]) / (x[-1] - x[-2])
    return math.exp(y[-1] + slope * (h - x[-1]))


def series(df: pd.DataFrame) -> dict[str, pd.Series]:
    f12, f6, oi = {}, {}, {}
    for d, g in df.groupby("date"):
        v = at_horizon(g, 365)
        if v is not None:
            f12[d] = v
        v = at_horizon(g, 182)
        if v is not None:
            f6[d] = v
        oi[d] = g.oi.sum() * CONTRACT_USD / 1e6
    return {"f12": pd.Series(f12).sort_index(), "f6": pd.Series(f6).sort_index(), "oi": pd.Series(oi).sort_index()}


def fetch(ids, rows: list[dict] | None = None) -> FetchResult:
    global LAST
    try:
        df = frame(rows if rows is not None else download())
        if df.empty:
            raise ValueError("no futures data returned")
        LAST = df
        parsed = series(df)
    except Exception as exc:
        return FetchResult(data=pd.DataFrame(columns=["date", "series_id", "value"]), errors={sid: str(exc) for sid in ids})
    frames, meta, errors = [], {}, {}
    for sid in ids:
        key = sid.split(":", 1)[1]
        if key not in parsed or parsed[key].empty:
            errors[sid] = f"no data for {key!r}"
            continue
        s = parsed[key]
        frames.append(pd.DataFrame({"date": s.index, "series_id": sid, "value": s.values}))
        meta[sid] = {"units": None, "time_index_end": s.index.max().strftime("%Y-%m-%d")}
    data = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["date", "series_id", "value"])
    return FetchResult(data=data, meta=meta, errors=errors)


def curve_table(df: pd.DataFrame, spot: pd.Series | None = None, lo: pd.Series | None = None, hi: pd.Series | None = None) -> dict:
    """Latest curve and the curves about one and three months earlier; band limits projected to each
    expiry at the pace of their last three months (an illustration, not the BCRA's rule)."""
    dates = sorted(df.date.unique())
    latest = dates[-1]

    def nearest(days_back):
        target = latest - pd.Timedelta(days=days_back)
        prior = [d for d in dates if d <= target]
        return prior[-1] if prior else None

    def curve(d):
        g = df[df.date == d].sort_values("days")
        return {"date": pd.Timestamp(d).strftime("%Y-%m-%d"),
                "contracts": [{"symbol": r.symbol, "expiry": r.expiry.strftime("%Y-%m-%d"), "settlement": r.settlement,
                               "implied_rate": r.implied_rate, "oi": r.oi, "volume": r.volume} for r in g.itertuples()]}
    out = {"latest": pd.Timestamp(latest).strftime("%Y-%m-%d"),
           "curves": [c for c in (curve(latest), curve(nearest(30)) if nearest(30) else None,
                                  curve(nearest(91)) if nearest(91) else None) if c]}
    if spot is not None and not spot.dropna().empty:
        s = spot.dropna()
        s = s[s.index <= latest]
        out["spot"] = {"date": s.index[-1].strftime("%Y-%m-%d"), "value": float(s.iloc[-1])}
    band = {}
    for key, ser in (("ceiling", hi), ("floor", lo)):
        if ser is None or ser.dropna().empty:
            continue
        ser = ser.dropna()
        last_d, last_v = ser.index[-1], float(ser.iloc[-1])
        ref = ser[ser.index <= last_d - pd.Timedelta(days=91)]
        if ref.empty:
            continue
        months = (last_d - ref.index[-1]).days / 30.4375
        pace = (last_v / float(ref.iloc[-1])) ** (1 / months) - 1
        band[key] = {"date": last_d.strftime("%Y-%m-%d"), "value": last_v, "monthly_pace": pace,
                     "projected": [{"expiry": c["expiry"], "value": last_v * (1 + pace) ** max(0.0, (pd.Timestamp(c["expiry"]) - last_d).days / 30.4375)}
                                   for c in out["curves"][0]["contracts"]]}
    out["band"] = band
    return out


def write_tables(directory) -> list[str]:
    if LAST is None or LAST.empty:
        return []
    try:
        from .. import store
        spot, lo, hi = store.as_of("bcra:5"), store.as_of("bcra:1187"), store.as_of("bcra:1188")
    except Exception:
        spot = lo = hi = None
    t = curve_table(LAST, spot, lo, hi)
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    p = d / "curve.json"
    text = json.dumps(t, separators=(",", ":"))
    if not p.exists() or p.read_text(encoding="utf-8") != text:
        p.write_text(text, encoding="utf-8")
        return ["curve.json"]
    return []
