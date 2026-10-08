"""Adapter for the Fraser Institute's Economic Freedom of the World (EFW) index.

Source: the "master index data for researchers" workbook, sheet "EFW Ratings":
one row per country and year (1970-2000 every five years, annual since), with
the summary rating (0-10), the world rank, and the five area ratings.

The workbook's address changes with each annual edition (usually published in
September with data to two years earlier): update URL below when the new
edition appears. Until then the series simply stay at the last edition (they
are flagged stale 30 months after their last year).

Series ids: "efw:<ISO3>:<key>" with key in summary, rank, a1..a5;
"efw:LATAM:<key>" is the median of the Latin American countries rated that year
(LATAM below); "efw:ALL:count" the number of countries ranked.
"""
from __future__ import annotations

import io
import time

import pandas as pd
import requests

from . import FetchResult

# 2026 edition (data to 2024). Update yearly.
URL = "https://efotw.org/sites/all/modules/custom/ftw_maps_pages/files/efotw-2026-master-index-data-for-researchers-iso.xlsx"
SHEET = "EFW Ratings"
TIMEOUT = 180
USER_AGENT = "argentina-economic-monitor (+https://github.com/ncachanosky/argentina-economic-monitor)"

# Latin America for medians: the Spanish- and Portuguese-speaking countries of the
# Americas plus Haiti (the same set is used for V-Dem; Cuba has no EFW rating).
LATAM = ["ARG", "BOL", "BRA", "CHL", "COL", "CRI", "CUB", "DOM", "ECU", "SLV", "GTM", "HTI",
         "HND", "MEX", "NIC", "PAN", "PRY", "PER", "URY", "VEN"]

# Column of each key, matched by the start of its header (headers carry long descriptions).
COLUMNS = {
    "summary": "ECONOMIC FREEDOM ALL AREAS",
    "rank": "EFW RANK",
    "a1": "Area 1 Size of Government",
    "a2": "Area 2 Legal System & Property Rights -- With Gender Adjustment",
    "a3": "Area 3 Sound Money",
    "a4": "Area 4 Freedom to trade internationally",
    "a5": "Area 5 Regulation",
}


def download(retries: int = 3) -> bytes:
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(URL, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
            r.raise_for_status()
            if r.content[:2] != b"PK":
                raise ValueError("not an xlsx file (has the edition's address changed?)")
            return r.content
        except Exception as exc:
            last = exc
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"EFW workbook: {last}")


def parse(content: bytes) -> pd.DataFrame:
    """Long frame: year (Timestamp, 1 January), iso, key, value."""
    raw = pd.read_excel(io.BytesIO(content), sheet_name=SHEET, header=None)
    hdr = next(i for i in range(15) if str(raw.iat[i, 0]).strip() == "Year")
    names = [str(c).strip() for c in raw.iloc[hdr]]
    body = raw.iloc[hdr + 1:]
    iso_col = names.index("ISO_Code")
    cols = {}
    for key, head in COLUMNS.items():
        hit = [i for i, n in enumerate(names) if n.lower() == head.lower()] or \
              [i for i, n in enumerate(names) if n.lower().startswith(head.lower())]
        if not hit:
            raise ValueError(f"column {head!r} not found; layout changed?")
        cols[key] = hit[0]
    year = pd.to_numeric(body.iloc[:, 0], errors="coerce")
    frames = []
    for key, i in cols.items():
        frames.append(pd.DataFrame({"year": year.values, "iso": body.iloc[:, iso_col].astype(str).str.strip().values,
                                    "key": key, "value": pd.to_numeric(body.iloc[:, i], errors="coerce").values}))
    out = pd.concat(frames).dropna(subset=["year", "value"])
    out["year"] = pd.to_datetime(out["year"].astype(int).astype(str) + "-01-01")
    arg = out[(out.iso == "ARG") & (out.key == "summary")]
    if arg.empty or not arg["value"].between(0, 10).all():
        raise ValueError("Argentina's summary rating missing or outside 0-10")
    return out


def series(df: pd.DataFrame, iso: str, key: str) -> pd.Series:
    if iso == "ALL" and key == "count":
        s = df[(df.key == "rank")].groupby("year")["value"].count().astype(float)
    elif iso == "LATAM":
        s = df[(df.key == key) & df.iso.isin(LATAM)].groupby("year")["value"].median()
    else:
        s = df[(df.iso == iso) & (df.key == key)].set_index("year")["value"]
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s


def fetch(ids) -> FetchResult:
    try:
        df = parse(download())
    except Exception as exc:
        return FetchResult(data=pd.DataFrame(columns=["date", "series_id", "value"]), errors={sid: str(exc) for sid in ids})
    frames, meta, errors = [], {}, {}
    for sid in ids:
        _, iso, key = sid.split(":")
        s = series(df, iso, key)
        if s.empty:
            errors[sid] = f"no data for {iso} {key}"
            continue
        frames.append(pd.DataFrame({"date": s.index, "series_id": sid, "value": s.values}))
        meta[sid] = {"units": "rating, 0-10" if key not in ("rank", "count") else key,
                     "time_index_end": s.index.max().strftime("%Y-%m-%d")}
    data = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["date", "series_id", "value"])
    return FetchResult(data=data, meta=meta, errors=errors)
