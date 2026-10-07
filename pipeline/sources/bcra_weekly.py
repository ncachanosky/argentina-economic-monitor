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


def _text_date(v: str):
    """Header dates typed as text: "01/15/2002" (month first) in early sheets,
    "31/12/2019 (**)" (day first, provisional year-end balances) later. Balances
    fall on the 7th, 15th, 23rd and last day of the month, which settles the
    order when both readings are valid dates."""
    m = re.match(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})", str(v))
    if not m:
        return None
    a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    cands = []
    for mo, d in ((a, b), (b, a)):
        try:
            cands.append(pd.Timestamp(y, mo, d))
        except ValueError:
            pass
    if not cands:
        return None
    weekly = [c for c in cands if c.day in (7, 15, 23) or c.is_month_end]
    return (weekly or cands)[0]


def _is_balance_day(d) -> bool:
    return d.day in (7, 15, 23) or d.is_month_end


def _fix_order(dates: list) -> list:
    """Repair header dates. In the 2002-2006 sheets every 7th-of-the-month
    cell was stored day-for-month (Jul 2 for Feb 7); in 2003 the August
    columns are labeled as July. Balances fall on the 7th, 15th, 23rd and last
    day of each month, so: a date on day 1-12 other than the 7th is swapped
    when that gives a 7th; a date that still does not follow its predecessor
    becomes the next balance day. A repeated date (the 2022 year-end, restated
    under a new scheme) is left as is: the later column wins."""
    out = []
    for d in dates:
        if d.day <= 12 and d.day != 7 and d.month == 7:
            try:
                d = pd.Timestamp(d.year, d.day, d.month)
            except ValueError:
                pass
        out.append(d)
    for k in range(1, len(out)):
        if out[k] < out[k - 1]:
            d = out[k - 1] + pd.Timedelta(days=1)
            while not _is_balance_day(d):
                d += pd.Timedelta(days=1)
            out[k] = d
    return out


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
            if t == xlrd.XL_CELL_TEXT:
                return _text_date(v)
            return None

        hdr = next((r for r in range(min(10, sh.nrows))
                    if sum(cdate(r, c) is not None for c in range(1, sh.ncols)) > 2), None)
        if hdr is None:
            continue
        dcols = [c for c in range(1, sh.ncols) if cdate(hdr, c) is not None]
        dates = dict(zip(dcols, _fix_order([cdate(hdr, c) for c in dcols])))
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
    # A date repeated in a sheet (the 2022 year-end, old and new scheme): the later column wins.
    return df.pivot_table(index="date", columns="item", values="value", aggfunc="last").sort_index()


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
    global LAST_CONTENT
    try:
        content = content if content is not None else download()
        LAST_CONTENT = content
        usd = to_usd(parse(content))
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


# ---------- the full balance sheet, for the site's balance-sheet table ----------
#
# Every line of every weekly balance, in the BCRA's own layout and wording for
# that year (labels and grouping change over time). One JSON per year:
#   {year, dates, fx, rows: [{side, level, kind, label, values}], notes}
# side: A (assets) or L (liabilities and net equity); kind: item, header
# (a line with no values), less (a "less:" marker), total; values in millions
# of pesos (None where blank or an error cell).

LAST_CONTENT: bytes | None = None   # set by fetch() so update can write the tables without a second download


def _is_upper(s: str) -> bool:
    letters = [ch for ch in s if ch.isalpha()]
    return bool(letters) and sum(ch.isupper() for ch in letters) / len(letters) > 0.8


def _clean_label(raw: str) -> str:
    s = re.sub(r"[.…]{2,}", " ", str(raw)).replace("…", " ")
    s = re.sub(r"\s+", " ", s).strip().lstrip("-").strip()
    return s


def balance_tables(content: bytes) -> dict[int, dict]:
    import xlrd
    book = xlrd.open_workbook(file_contents=content)
    out = {}
    for name in book.sheet_names():
        sh = book.sheet_by_name(name)

        def cdate(r, c):
            t, v = sh.cell_type(r, c), sh.cell_value(r, c)
            if t in (xlrd.XL_CELL_NUMBER, xlrd.XL_CELL_DATE) and 30000 < v < 60000:
                return pd.Timestamp("1899-12-30") + pd.Timedelta(days=int(v))
            if t == xlrd.XL_CELL_TEXT:
                return _text_date(v)
            return None

        hdr = next((r for r in range(min(10, sh.nrows))
                    if sum(cdate(r, c) is not None for c in range(1, sh.ncols)) > 2), None)
        if hdr is None:
            continue
        cols = [c for c in range(1, sh.ncols) if cdate(hdr, c) is not None]
        dates = _fix_order([cdate(hdr, c) for c in cols])
        keep = [k for k in range(len(cols)) if k + 1 == len(cols) or dates[k + 1] != dates[k]]
        cols, dates = [cols[k] for k in keep], [dates[k] for k in keep]

        def vals(r):
            return [round(sh.cell_value(r, c) / 1000, 1) if sh.cell_type(r, c) == xlrd.XL_CELL_NUMBER else None
                    for c in cols]

        side, rows, fx, notes, done = None, [], None, [], False
        last_upper_level = 0
        pending = None
        for r in range(hdr + 1, sh.nrows):
            raw = str(sh.cell_value(r, 0))
            lab = _clean_label(raw)
            if lab == "0" and r + 1 < sh.nrows and _clean_label(sh.cell_value(r + 1, 0)).upper() == "CENTRAL BANK HOLDINGS":
                raw = lab = "GOVERNMENT SECURITIES"      # the 2007 sheet lost this label
            low = lab.lower()
            if low.startswith("(*") or low.startswith("see below") or low.startswith("additionally"):
                notes.append(re.sub(r"\s+", " ", raw).strip())
                done = True
                continue
            if done or not lab:
                continue
            if low.replace(" ", "") == "assets":
                side = "A"; continue
            if low.replace(" ", "") == "liabilities":
                side = "L"; continue
            if low.startswith("rate of exchange") or low.startswith("usd exchange rate"):
                if fx is None:
                    fx = [sh.cell_value(r, c) if sh.cell_type(r, c) == xlrd.XL_CELL_NUMBER and sh.cell_value(r, c) > 0 else None
                          for c in cols]
                continue
            if low.startswith("control") or side is None:
                continue
            v = vals(r)
            has = any(x is not None for x in v)
            if low in ("less:", "less :"):
                rows.append({"side": side, "level": last_upper_level + 1, "kind": "less", "label": "Less:", "values": None})
                continue
            if low.startswith("total"):
                rows.append({"side": side, "level": 0, "kind": "total", "label": lab, "values": v})
                if low.startswith("total liabilities + net equity"):
                    done = True
                continue
            indent = len(raw) - len(raw.lstrip(" "))
            dash = raw.lstrip(" ").startswith("-")
            # A label broken over two lines: "FUNDS TRANSFERRED TO THE NATIONAL GOVERNMENT FOR" /
            # "PLACEMENTS WITH THE ...", "Deposit for de the encouragement" / "of international reserves ...".
            if not has and r + 1 < sh.nrows:
                nraw = str(sh.cell_value(r + 1, 0)).strip()
                if nraw and (re.search(r"\b(for|of|to|on|the|and)$", lab, re.I) or nraw[0].islower()):
                    pending = lab
                    continue
            if pending:
                lab, pending = pending + " " + lab, None
                low = lab.lower()
            if _is_upper(lab) and not dash and indent <= 1:
                # Headings broken over two lines: "... ON BEHALF OF" / "ARGENTINE GOVERNMENT AND OTHER".
                if not has and rows and rows[-1]["kind"] == "item" and rows[-1]["level"] == 0 and rows[-1]["side"] == side \
                        and (r + 1 >= sh.nrows or not _clean_label(sh.cell_value(r + 1, 0))):
                    rows[-1]["label"] += " " + lab
                    continue
                if rows and rows[-1]["kind"] == "header" and rows[-1]["level"] == 0 and has and indent == 0 \
                        and rows[-1].get("_merge_next"):
                    rows[-1].update({"kind": "item", "label": rows[-1]["label"] + " " + lab, "values": v})
                    rows[-1].pop("_merge_next", None)
                    continue
                level = 0 if indent == 0 else 1
                last_upper_level = level
                row = {"side": side, "level": level, "kind": "item" if has else "header", "label": lab, "values": v if has else None}
                if not has and indent == 0:
                    # A heading whose values come on the next line ("CONTRA ACCOUNT ... CONTRIBUTIONS" / "TO INTERNATIONAL AGENCIES").
                    nxt = _clean_label(sh.cell_value(r + 1, 0)) if r + 1 < sh.nrows else ""
                    if nxt and _is_upper(nxt) and str(sh.cell_value(r + 1, 0)).startswith(("TO ", "ARGENTINE", "NATIONAL")):
                        row["_merge_next"] = True
                rows.append(row)
                continue
            level = last_upper_level + (1 if dash or indent < 3 else 2)
            # "- Nontransferable Bills ..." sits among the indented children of
            # "Securities issued under Argentine Legislation" despite its dash.
            if dash and low.startswith("nontransferable") and rows and rows[-1]["level"] == level + 1:
                level += 1
            rows.append({"side": side, "level": level, "kind": "item" if has else "header", "label": lab, "values": v if has else None})
        for row in rows:
            row.pop("_merge_next", None)
        year = int(str(name).strip())
        out[year] = {"year": year, "dates": [d.strftime("%Y-%m-%d") for d in dates], "fx": fx,
                     "rows": rows, "notes": notes}
    return out


def write_tables(directory) -> list[str]:
    """Write <year>.json and index.json from the last downloaded file; returns the files written."""
    import json
    from pathlib import Path
    if LAST_CONTENT is None:
        return []
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    tables = balance_tables(LAST_CONTENT)
    written = []
    for year, t in tables.items():
        p = d / f"{year}.json"
        text = json.dumps(t, separators=(",", ":"), ensure_ascii=False)
        if not p.exists() or p.read_text(encoding="utf-8") != text:
            p.write_text(text, encoding="utf-8")
            written.append(p.name)
    index = {"years": {str(y): t["dates"] for y, t in sorted(tables.items())}}
    (d / "index.json").write_text(json.dumps(index, separators=(",", ":")), encoding="utf-8")
    return written
