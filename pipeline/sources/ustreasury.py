"""Adapter for US Treasury bill rates (daily Treasury bill rates, U.S. Department of
the Treasury), one CSV per year since FIRST_YEAR.

Series ids: "ust:<weeks>w" -- coupon-equivalent yield, percent, e.g. "ust:52w".
"""
from __future__ import annotations

import datetime as dt
import io
import time

import pandas as pd
import requests

from . import FetchResult

URL = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/"
       "{y}/all?type=daily_treasury_bill_rates&field_tdr_date_value={y}&page&_format=csv")
FIRST_YEAR = 2016
TIMEOUT = 60
USER_AGENT = "argentina-economic-monitor (+https://github.com/ncachanosky/argentina-economic-monitor)"


def _get(url: str, retries: int = 3) -> str:
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
            r.raise_for_status()
            return r.text
        except Exception as exc:
            last = exc
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"{url}: {last}")


def parse(text: str) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(text))
    df["Date"] = pd.to_datetime(df["Date"], format="%m/%d/%Y")
    return df.set_index("Date").sort_index()


def fetch(ids) -> FetchResult:
    frames_y, errors = [], {}
    for y in range(FIRST_YEAR, dt.date.today().year + 1):
        try:
            frames_y.append(parse(_get(URL.format(y=y))))
        except Exception as exc:
            if y == dt.date.today().year:
                errors["_"] = str(exc)
    if not frames_y:
        return FetchResult(data=pd.DataFrame(columns=["date", "series_id", "value"]),
                           errors={sid: errors.get("_", "no data") for sid in ids})
    df = pd.concat(frames_y).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    frames, meta, errs = [], {}, {}
    for sid in ids:
        w = sid.split(":", 1)[1].rstrip("w")
        col = f"{w} WEEKS COUPON EQUIVALENT"
        if col not in df.columns:
            errs[sid] = f"no column {col!r}"
            continue
        s = pd.to_numeric(df[col], errors="coerce").dropna()
        frames.append(pd.DataFrame({"date": s.index, "series_id": sid, "value": s.values}))
        meta[sid] = {"units": "percent", "time_index_end": s.index.max().strftime("%Y-%m-%d")}
    data = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["date", "series_id", "value"])
    return FetchResult(data=data, meta=meta, errors=errs)
