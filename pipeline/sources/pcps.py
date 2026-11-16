"""Adapter for the IMF's Primary Commodity Prices (PCPS), monthly, in US dollars.

Read through the IMF's SDMX API (the same service as the World Economic Outlook on the
International page): world prices of the commodities that matter for Argentina's exports and
imports. Ids: "pcps:<IMF code>", e.g. pcps:PSOYB (soybeans, US$ per metric ton).
"""
from __future__ import annotations

import re
import time

import pandas as pd
import requests

from . import FetchResult

BASE = "https://api.imf.org/external/sdmx/2.1/data/IMF.RES,PCPS/G001.{codes}.USD.M"
USER_AGENT = "argentina-economic-monitor (+https://github.com/ncachanosky/argentina-economic-monitor)"


def download(codes: list[str], retries: int = 3) -> str:
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(BASE.format(codes="+".join(codes)), timeout=180, headers={"User-Agent": USER_AGENT})
            r.raise_for_status()
            return r.text
        except Exception as exc:
            last = exc
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"IMF PCPS: {last}")


def parse(xml: str) -> pd.DataFrame:
    rows = []
    for attrs, body in re.findall(r"<Series ([^>]*)>(.*?)</Series>", xml, re.S):
        code = re.search(r'INDICATOR="(\w+)"', attrs).group(1)
        for per, val in re.findall(r'TIME_PERIOD="(\d{4}-M\d{2})" OBS_VALUE="([^"]+)"', body):
            rows.append((pd.Timestamp(f"{per[:4]}-{per[6:8]}-01"), f"pcps:{code}", float(val)))
    return pd.DataFrame(rows, columns=["date", "series_id", "value"])


def fetch(ids) -> FetchResult:
    empty = pd.DataFrame(columns=["date", "series_id", "value"])
    codes = sorted({sid.split(":", 1)[1] for sid in ids})
    try:
        df = parse(download(codes))
    except Exception as exc:
        return FetchResult(data=empty, errors={sid: str(exc) for sid in ids})
    errors = {sid: "not in the IMF's PCPS" for sid in ids if sid not in set(df["series_id"])}
    return FetchResult(data=df[df["series_id"].isin(ids)].reset_index(drop=True), meta={sid: {"units": "US$"} for sid in ids}, errors=errors)
