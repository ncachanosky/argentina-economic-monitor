"""Adapter for the BCRA's historical balance sheet (monthly, 1940 to date).

One Excel file, "Balance del Banco Central de la República Argentina",
in BCRA's analytical presentation: sources of base money (net external
assets, credit to the government, credit to banks), absorption (deposits,
BCRA notes, miscellaneous accounts) and base money.

Series ids are "bcrabal:<cd_serie>", the series code BCRA prints above each
column (e.g. bcrabal:115 is base money), plus "bcrabal:fx" for the valuation
exchange rate. Each row says in which monetary unit it is expressed; values
are converted to thousands of current pesos with BCRA's own factors:
value x units represented x equivalence / 1000.
"""
from __future__ import annotations

import io

import pandas as pd
import requests

from . import FetchResult

URL = "https://www.bcra.gob.ar/archivos/Pdfs/PublicacionesEstadisticas/balbcrhis.xls"
TIMEOUT = 120
USER_AGENT = "argentina-economic-monitor (+https://github.com/ncachanosky/argentina-economic-monitor)"
FX_COL = 42


def download() -> bytes:
    r = requests.get(URL, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    return r.content


def parse(content: bytes) -> pd.DataFrame:
    """Long frame (date, series_id, value) of every column, in thousands of current pesos."""
    import xlrd
    sh = xlrd.open_workbook(file_contents=content).sheet_by_index(0)
    code_row = next(r for r in range(sh.nrows) if sh.cell_value(r, 0) == "cd_serie")
    codes = {c: f"bcrabal:{int(sh.cell_value(code_row, c))}" for c in range(6, sh.ncols)
             if isinstance(sh.cell_value(code_row, c), float)}
    codes[FX_COL] = "bcrabal:fx"
    rows = []
    for r in range(code_row + 1, sh.nrows):
        ym, month = sh.cell_value(r, 0), sh.cell_value(r, 1)
        if not isinstance(ym, float) or not month:
            continue
        year, mon = int(ym), int(round((ym - int(ym)) * 100))
        if not 1 <= mon <= 12:
            continue
        units, equiv = sh.cell_value(r, 4), sh.cell_value(r, 5)
        if not isinstance(units, float) or not isinstance(equiv, float):
            continue
        date = pd.Timestamp(year, mon, 1)
        for c, sid in codes.items():
            x = sh.cell_value(r, c)
            if not isinstance(x, float):
                continue                       # blank: not published (yet)
            if c == FX_COL:
                if x == 0:
                    continue
                rows.append((date, sid, x * equiv))
            else:
                rows.append((date, sid, x * units * equiv / 1000))
    df = pd.DataFrame(rows, columns=["date", "series_id", "value"])
    # The table starts with empty months (all zeros) before the first balance.
    base = df[df.series_id == "bcrabal:115"].set_index("date")["value"]
    first = base[base != 0].index.min()
    return df[df.date >= first].reset_index(drop=True)


def fetch(ids, content: bytes | None = None) -> FetchResult:
    try:
        data = parse(content if content is not None else download())
    except Exception as exc:
        return FetchResult(data=pd.DataFrame(columns=["date", "series_id", "value"]),
                           errors={sid: f"balance sheet file: {exc}" for sid in ids})
    have = set(data.series_id)
    errors = {sid: "not in the balance sheet file" for sid in ids if sid not in have}
    data = data[data.series_id.isin(set(ids))]
    meta = {sid: {"units": "thousands of pesos", "time_index_end": g.date.max().strftime("%Y-%m-%d")}
            for sid, g in data.groupby("series_id")}
    return FetchResult(data=data.reset_index(drop=True), meta=meta, errors=errors)
