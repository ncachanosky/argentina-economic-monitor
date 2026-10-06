"""Adapter for the BCRA's weekly summary balance sheet (2002 to date).

One Excel file, "Summary Balances of Assets and Liabilities - Annual series
from 1998 to date", one sheet per year, one column per weekly balance (the
7th, 15th, 23rd and last day of each month). Rows are line items in
thousands of pesos; the "Rate of Exchange USD/$" row gives the valuation
rate of each balance.

The layout and labels change over the years (the reserves block was
restated in 2010, repos were "term operations" before 2010, ...), so rows
are matched by normalized label against the aliases below, first match
within the sheet. Error cells and blanks are skipped.

Series ids are "bcrawk:<item>"; values are millions of US dollars at each
balance's own exchange rate, except "bcrawk:fx" (pesos per dollar).
"""
from __future__ import annotations

import re
import time

import pandas as pd
import requests

from . import FetchResult

URL = ("https://www.bcra.gob.ar/archivos/Pdfs/PublicacionesEstadisticas/"
       "summary-balances-assets-liabilities-bcra-annual-series-1998-to-date.xls")
TIMEOUT = 180
USER_AGENT = "argentina-economic-monitor (+https://github.com/ncachanosky/argentina-economic-monitor)"

# item -> labels (normalized: lower case, no leaders, no leading dash).
ALIASES = {
    # Gross reserves: since 2010 the "international reserves" row is a header
    # and the total sits on the next line; before, on the header itself.
    "gross": ["gold, currency, deposits to be realized and others", "international reserves"],
    "gold": ["gold (net of allowances)", "gold (net of allowance)", "gold"],
    "gold_allowance": ["gold purity allowance"],
    # Liabilities
    "base": ["monetary base"],
    "encaje": ["current accounts in others currencies", "current accounts in other currencies"],
    "gov_deposits": ["deposits from national argentine and other", "deposits from national government and other",
                     "national government deposits and others"],
    "intl_org": ["obligations with international agencies", "due to international agencies"],
    "bcra_fx_securities": ["bills and notes issued in foreign currency", "in foreign currency", "bills in foreign currency",
                           "bills in external currency", "bills issued in foreign currency", "notes issued in foreign currency"],
    "repo_line": ["due to repo transactions", "obligations deriving from term operations"],
    "fx": ["rate of exchange usd/$:", "usd exchange rate of the date"],
}


def download(retries: int = 4) -> bytes:
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(URL, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
            r.raise_for_status()
            expected = r.headers.get("Content-Length")
            if expected and int(expected) != len(r.content):
                raise IOError(f"incomplete download: {len(r.content)} of {expected} bytes")
            return r.content
        except Exception as exc:
            last = exc
            time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"could not download the weekly balance file: {last}")


def _norm(s) -> str:
    s = re.sub(r"[.…]+", " ", str(s)).strip().lower().lstrip("-").strip()
    return re.sub(r"\s+", " ", s)


def parse(content: bytes) -> pd.DataFrame:
    """Wide frame: one row per weekly balance, one column per item (thousands of pesos; fx in pesos)."""
    import xlrd
    book = xlrd.open_workbook(file_contents=content)
    rows = []
    for name in book.sheet_names():
        sh = book.sheet_by_name(name)

        def cdate(r, c):
            t, v = sh.cell_type(r, c), sh.cell_value(r, c)
            if t in (xlrd.XL_CELL_NUMBER, xlrd.XL_CELL_DATE) and 30000 < v < 60000:
                return pd.Timestamp("1899-12-30") + pd.Timedelta(days=int(v))
            if t == xlrd.XL_CELL_TEXT and re.fullmatch(r"\d\d/\d\d/\d{4}", v.strip()):
                return pd.to_datetime(v.strip(), format="%m/%d/%Y")
            return None

        hdr = next((r for r in range(min(10, sh.nrows))
                    if sum(cdate(r, c) is not None for c in range(1, sh.ncols)) > 2), None)
        if hdr is None:
            continue
        dates = {c: cdate(hdr, c) for c in range(1, sh.ncols) if cdate(hdr, c) is not None}
        seen = set()
        for r in range(hdr + 1, sh.nrows):
            lab = _norm(sh.cell_value(r, 0))
            if lab.startswith("see below") or lab.startswith("(*)"):
                break                      # restatement block at the bottom of the sheet
            for item, labels in ALIASES.items():
                if item in seen or lab not in labels:
                    continue
                vals = {c: sh.cell_value(r, c) for c in dates if sh.cell_type(r, c) == xlrd.XL_CELL_NUMBER}
                if not vals:
                    continue               # a header row; the total comes on a later line
                seen.add(item)
                rows += [(dates[c], item, float(v)) for c, v in vals.items()]
    df = pd.DataFrame(rows, columns=["date", "item", "value"])
    return df.pivot_table(index="date", columns="item", values="value", aggfunc="first").sort_index()


def to_usd(wide: pd.DataFrame) -> pd.DataFrame:
    """Millions of US dollars at each balance's exchange rate; gold net of the purity allowance."""
    fx = wide["fx"].where(wide["fx"] > 0)
    usd = wide.drop(columns=["fx"]).div(fx, axis=0) / 1000
    if "gold_allowance" in usd:
        usd["gold"] = usd["gold"] - usd["gold_allowance"].fillna(0)
        usd = usd.drop(columns=["gold_allowance"])
    usd["fx"] = fx
    return usd


def fetch(ids, content: bytes | None = None) -> FetchResult:
    try:
        usd = to_usd(parse(content if content is not None else download()))
    except Exception as exc:
        return FetchResult(data=pd.DataFrame(columns=["date", "series_id", "value"]),
                           errors={sid: f"weekly balance file: {exc}" for sid in ids})
    long = usd.rename(columns=lambda c: f"bcrawk:{c}").stack().rename("value").reset_index()
    long.columns = ["date", "series_id", "value"]
    have = set(long.series_id)
    errors = {sid: "not in the weekly balance file" for sid in ids if sid not in have}
    data = long[long.series_id.isin(set(ids))].dropna(subset=["value"])
    meta = {sid: {"units": "pesos per dollar" if sid == "bcrawk:fx" else "millions of dollars",
                  "time_index_end": g.date.max().strftime("%Y-%m-%d")}
            for sid, g in data.groupby("series_id")}
    return FetchResult(data=data.reset_index(drop=True), meta=meta, errors=errors)
