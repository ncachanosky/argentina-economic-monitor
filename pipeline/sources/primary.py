"""Primary source files: series read from the publisher's own file, ahead of datos.gob.ar.

datos.gob.ar republishes INDEC's and the ministries' files, sometimes days late, and since
October 2026 its series API has been returning incomplete answers. For the series listed
here the datos_gob adapter asks this module first; the series keep their datos.gob.ar ids
(so the site, the store and the report don't change) and the API remains the fallback.

Each provider downloads one file and returns {series id: pd.Series}. Readers locate their
data by labels ("Período", "Desestacionalizada", "Empleo asalariado en el sector privado"),
not by cell positions, so an inserted row or column doesn't shift the numbers. Every result
then goes through two checks before it is used:

  structure   expected sheet / header labels / series present (else LayoutChanged)
  agreement   on the months both have, the values match the stored history within
              tolerance (else LayoutChanged: the reader is probably on the wrong row)

A provider that fails falls back to the API for its series, and its state is written to
data/source_files.json; pipeline.alerts opens one GitHub issue per broken file (label
"source-layout") with what was expected, what was found and the first rows of the file,
and closes it after the next good run.
"""
from __future__ import annotations

import io
import json
import re
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from .. import store
from ..registry import ROOT

STATUS_FILE = ROOT / "data" / "source_files.json"
TIMEOUT = 90
USER_AGENT = "argentina-economic-monitor (+https://github.com/ncachanosky/argentina-economic-monitor)"
INDEC = "https://www.indec.gob.ar/ftp/cuadros"

MONTHS = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8,
          "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12}


class LayoutChanged(Exception):
    """The file is there but doesn't look as the reader expects."""

    def __init__(self, detail: str, preview: str = ""):
        super().__init__(detail)
        self.preview = preview


class FileNotFound(Exception):
    """The file couldn't be located or downloaded."""


# ── helpers ──────────────────────────────────────────────────────────────────

def download(url: str, retries: int = 3) -> bytes:
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
            r.raise_for_status()
            return r.content
        except Exception as exc:
            last = exc
            time.sleep(5 * (attempt + 1))
    raise FileNotFound(f"{url}: {last}")


def preview(df: pd.DataFrame, rows: int = 8, cols: int = 8) -> str:
    return df.iloc[:rows, :cols].to_string(max_colwidth=40)


def norm(x) -> str:
    return re.sub(r"\s+", " ", str(x)).strip().lower() if pd.notna(x) else ""


def find_row(df: pd.DataFrame, pattern: str, col: int | None = None) -> int:
    """First row whose cell (in `col`, or any column) matches the regex."""
    rx = re.compile(pattern, re.I)
    cells = df.iloc[:, [col]] if col is not None else df
    for i, row in cells.iterrows():
        if any(rx.search(norm(v)) for v in row):
            return i
    raise LayoutChanged(f"no row matching /{pattern}/", preview(df))


def find_col(df: pd.DataFrame, row: int, pattern: str) -> int:
    rx = re.compile(pattern, re.I)
    for j, v in enumerate(df.iloc[row]):
        if rx.search(norm(v)):
            return j
    raise LayoutChanged(f"no column matching /{pattern}/ in header row {row}: "
                        f"{[norm(v) for v in df.iloc[row] if norm(v)]}", preview(df))


def month_label(text: str) -> int | None:
    t = norm(text)
    m = re.match(r"([a-záéíóú]{3})", t)
    return MONTHS.get(m.group(1).replace("á", "a")) if m else None


def num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


# ── readers ──────────────────────────────────────────────────────────────────

def _emae_rows(df: pd.DataFrame, header: int) -> pd.DatetimeIndex:
    """Dates for an INDEC EMAE table: year in column 0 (first month only), month name in column 1."""
    year, dates = None, []
    for _, r in df.iloc[header + 1:].iterrows():
        y = re.match(r"((?:19|20)\d{2})(?:\D|$)", norm(r.iloc[0]).replace(".0", ""))   # 2024, "2024*", 2024.0
        if y:
            year = int(y.group(1))
        m = month_label(r.iloc[1]) if pd.notna(r.iloc[1]) else None
        dates.append(pd.Timestamp(year, m, 1) if (year and m) else pd.NaT)
    return pd.DatetimeIndex(dates)


def read_emae(raw: bytes) -> dict[str, pd.Series]:
    df = pd.read_excel(io.BytesIO(raw), sheet_name=0, header=None)
    h = find_row(df, r"^per[ií]odo", col=0)
    cols = {"143.3_NO_PR_2004_A_21": find_col(df, h, r"serie original"),
            "143.3_NO_PR_2004_A_31": find_col(df, h, r"desestacionalizada"),
            "143.3_NO_PR_2004_A_28": find_col(df, h, r"tendencia")}
    dates = _emae_rows(df, h)
    body = df.iloc[h + 1:]
    return {sid: pd.Series(num(body.iloc[:, j]).values, index=dates).dropna().loc[lambda s: s.index.notna()]
            for sid, j in cols.items()}


EMAE_LETTERS = {"A": "11.3_ISOM_2004_M_39", "B": "11.3_VIPAA_2004_M_5", "C": "11.3_ISD_2004_M_26",
                "D": "11.3_VMASD_2004_M_23", "E": "11.3_ITC_2004_M_21", "F": "11.3_VMATC_2004_M_12",
                "G": "11.3_AGCS_2004_M_41", "H": "11.3_P_2004_M_20", "I": "11.3_EMC_2004_M_25",
                "J": "11.3_IM_2004_M_25", "K": "11.3_SEGA_2004_M_48", "L": "11.3_C_2004_M_60",
                "M": "11.3_CMMR_2004_M_10", "N": "11.3_HR_2004_M_24", "O": "11.3_TAC_2004_M_60"}


def read_emae_sectors(raw: bytes) -> dict[str, pd.Series]:
    x = pd.ExcelFile(io.BytesIO(raw))
    sheet = next((s for s in x.sheet_names if re.search(r"letras", s, re.I) and not re.search(r"var", s, re.I)), None)
    if sheet is None:
        raise LayoutChanged(f"no index sheet ('Tabla Letras'); sheets: {x.sheet_names}")
    df = x.parse(sheet, header=None)
    h = find_row(df, r"^per[ií]odo", col=0)
    cols = {sid: find_col(df, h, rf"^{letter}\s*-") for letter, sid in EMAE_LETTERS.items()}
    cols["11.3_IF_2004_M_25"] = find_col(df, h, r"impuestos netos")
    dates = _emae_rows(df, h)
    body = df.iloc[h + 1:]
    return {sid: pd.Series(num(body.iloc[:, j]).values, index=dates).dropna().loc[lambda s: s.index.notna()]
            for sid, j in cols.items()}


def read_wages(raw: bytes) -> dict[str, pd.Series]:
    df = pd.read_csv(io.BytesIO(raw), sep=";", dtype=str, encoding="latin-1")
    df.columns = [c.strip().lstrip("﻿") for c in df.columns]
    want = {"149.1_SOR_PRIADO_OCTU_0_25": "IS_sector_privado_registrado",
            "149.1_SOR_PUBICO_OCTU_0_14": "IS_sector_publico",
            "149.1_SOR_PRIADO_OCTU_0_28": "IS_sector_no_registrado",
            "149.1_TL_INDIIOS_OCTU_0_21": "IS_indice_total"}
    missing = [c for c in ["periodo", *want.values()] if c not in df.columns]
    if missing:
        raise LayoutChanged(f"missing columns {missing}; found {list(df.columns)}", df.head(8).to_string())
    dates = pd.to_datetime(df["periodo"].str.strip(), format="%d/%m/%Y", errors="coerce")
    out = {}
    for sid, col in want.items():
        v = pd.to_numeric(df[col].str.replace(".", "", regex=False).str.replace(",", ".", regex=False), errors="coerce")
        out[sid] = pd.Series(v.values, index=dates).dropna().loc[lambda s: s.index.notna()]
    return out


SIPA_PAGE = "https://www.argentina.gob.ar/trabajo/estadisticas/situacion-y-evolucion-del-trabajo-registrado"
SIPA_COLS = {"151.1_AARIADOTAC_2012_M_26": r"asalariado en el sector privado",
             "151.1_AARIADOTAC_2012_M_25": r"asalariado en el sector p[uú]blico",
             "151.1_AARIADOTAC_2012_M_40": r"casas particulares",
             "151.1_IPENDIETAC_2012_M_34": r"aut[oó]nomos",
             "151.1_IPENDIETAC_2012_M_36": r"monotributo$",
             "151.1_IPENDIETAC_2012_M_43": r"monotributo social",
             "151.1_TL_SIN_TAC_2012_M_15": r"^total$"}


def sipa_url() -> str:
    """The monthly workbook's name changes with each release (trabajoregistrado_YYMM_estadisticas.xlsx)."""
    html = download(SIPA_PAGE).decode("utf-8", "replace")
    found = sorted(set(re.findall(r"trabajoregistrado_(\d{4})_estadisticas\.xlsx", html)))
    if not found:
        raise FileNotFound(f"no trabajoregistrado_YYMM_estadisticas.xlsx link on {SIPA_PAGE}")
    return f"https://www.argentina.gob.ar/sites/default/files/trabajoregistrado_{found[-1]}_estadisticas.xlsx"


def _sipa_date(v) -> pd.Timestamp | None:
    if isinstance(v, (pd.Timestamp, datetime)):
        return pd.Timestamp(v).replace(day=1)
    m = re.match(r"([a-z]{3})-(\d{2})", norm(v))
    if m and m.group(1) in MONTHS:
        return pd.Timestamp(2000 + int(m.group(2)), MONTHS[m.group(1)], 1)
    return None


def read_sipa(raw: bytes) -> dict[str, pd.Series]:
    x = pd.ExcelFile(io.BytesIO(raw))
    # T.2.2: registered workers by type of employment, seasonally adjusted ("sin estacionalidad").
    sheet = next((s for s in x.sheet_names if s.strip().upper().startswith("T.2.2")), None)
    if sheet is None:
        raise LayoutChanged(f"no sheet T.2.2; sheets: {x.sheet_names}")
    df = x.parse(sheet, header=None)
    title = norm(df.iloc[0, 0])
    if "sin estacionalidad" not in title or "modalidad" not in title:
        raise LayoutChanged(f"sheet {sheet} title changed: {df.iloc[0, 0]!r}", preview(df))
    h = find_row(df, r"^per[ií]odo", col=0)
    cols = {sid: find_col(df, h, pat) for sid, pat in SIPA_COLS.items()}
    dates = pd.DatetimeIndex([_sipa_date(v) for v in df.iloc[h + 1:, 0]])
    body = df.iloc[h + 1:]
    return {sid: pd.Series(num(body.iloc[:, j]).values, index=dates).dropna().loc[lambda s: s.index.notna()]
            for sid, j in cols.items()}


RIPTE_PAGE = "https://www.argentina.gob.ar/trabajo/seguridadsocial/ripte"


def read_ripte(raw: bytes) -> dict[str, pd.Series]:
    """The RIPTE page's table: the last ~20 months (month, amount in pesos, ...)."""
    tables = pd.read_html(io.BytesIO(raw), decimal=",", thousands=".")
    tab = next((t for t in tables if any("monto" in norm(c) for c in t.columns)), None)
    if tab is None:
        raise LayoutChanged("no table with a 'Monto' column on the RIPTE page")
    mcol = next(c for c in tab.columns if norm(c).startswith("mes"))
    vcol = next(c for c in tab.columns if "monto" in norm(c))
    vals = {}
    for m, v in zip(tab[mcol], tab[vcol]):
        mm = re.match(r"([a-záéíóú]+)\W*(\d{4})", norm(m))
        if not mm or month_label(mm.group(1)) is None:
            continue
        amount = pd.to_numeric(re.sub(r"[^\d,]", "", str(v)).replace(",", "."), errors="coerce")
        if pd.notna(amount):
            vals[pd.Timestamp(int(mm.group(2)), month_label(mm.group(1)), 1)] = float(amount)
    if len(vals) < 6:
        raise LayoutChanged(f"RIPTE table parsed to {len(vals)} months", tab.head(8).to_string())
    return {"158.1_REPTE_0_0_5": pd.Series(vals).sort_index()}


def read_cpi(raw: bytes) -> dict[str, pd.Series]:
    from . import indec_ipc
    df = indec_ipc.parse(raw)
    out = {}
    for sid, (code, region) in indec_ipc.MAP.items():
        s = df[(df["Codigo"] == code) & (df["Region"] == region)].set_index("date")["value"].sort_index()
        if s.empty:
            raise LayoutChanged(f"no rows for Codigo={code!r}, Region={region!r}", df.head(8).to_string())
        out[sid] = s
    return out


# ── foreign trade (INDEC ICA and price/quantity indices; datos.gob.ar full CSVs) ───────────

ICA_PAGE = "https://www.indec.gob.ar/Nivel4/Tema/3/2/40"


def ica_file(pattern: str):
    """Resolver for an ICA file whose name carries the years (e.g. expo_grandes_rubros_2025_2026.xls)."""
    def resolve() -> str:
        html = download(ICA_PAGE).decode("utf-8", "replace")
        found = sorted(set(re.findall(pattern, html)))
        if not found:
            raise FileNotFound(f"no file matching /{pattern}/ on {ICA_PAGE}")
        return f"{INDEC}/economia/{found[-1]}"
    return resolve


def _year_blocks(df: pd.DataFrame, cats: dict[str, str]) -> dict[str, pd.Series]:
    """Monthly tables laid out as one block per category, each with a column per year
    ("Año 2026e", "Año 2025*") and a row per month (Ene ... Dic)."""
    hrow = find_row(df, cats[next(iter(cats))])
    starts = {sid: find_col(df, hrow, pat) for sid, pat in cats.items()}
    yrow = next((i for i in range(hrow, min(hrow + 6, len(df)))
                 if any(re.search(r"a[ñn]o \d{4}", norm(v)) for v in df.iloc[i])), None)
    if yrow is None:
        raise LayoutChanged("no 'Año YYYY' header under the category row", preview(df, 12, 12))
    pcol = find_col(df, find_row(df, r"^per[ií]odo"), r"^per[ií]odo")
    edges = sorted(starts.values()) + [df.shape[1]]
    out = {}
    for sid, c in starts.items():
        end = next(e for e in edges if e > c)
        vals = {}
        for j in range(c, end):
            m = re.search(r"a[ñn]o (\d{4})", norm(df.iat[yrow, j]))
            if not m:
                continue
            year = int(m.group(1))
            for i in range(yrow + 1, len(df)):
                lab = norm(df.iat[i, pcol])
                mon = MONTHS.get(lab[:3]) if re.fullmatch(r"[a-z]{3}\.?\*?", lab) else None
                v = pd.to_numeric(df.iat[i, j], errors="coerce")
                if mon and pd.notna(v):
                    vals[pd.Timestamp(year, mon, 1)] = float(v)
        if not vals:
            raise LayoutChanged(f"no monthly values for {sid} under {df.iat[hrow, c]!r}", preview(df, 14, 12))
        out[sid] = pd.Series(vals).sort_index()
    return out


def read_trade_totals(raw: bytes) -> dict[str, pd.Series]:
    df = pd.read_excel(io.BytesIO(raw), sheet_name=0, header=None)
    h = find_row(df, r"^per[ií]odo", col=0)
    ce, ci, cs = find_col(df, h, r"^exportaciones"), find_col(df, h, r"^importaciones"), find_col(df, h, r"^saldo")
    sub = next(i for i in range(h, h + 3) if "total mensual" in norm(df.iat[i, ce]))
    if "total mensual" not in norm(df.iat[sub, ci]):
        raise LayoutChanged("imports block has no 'Total mensual' column", preview(df, 8, 12))
    dates = _emae_rows(df, sub)
    body = df.iloc[sub + 1:]
    cols = {"74.3_IET_0_M_16": ce, "74.3_IIT_0_M_25": ci, "74.3_ISC_0_M_19": cs}
    return {sid: pd.Series(num(body.iloc[:, j]).values, index=dates).dropna().loc[lambda s: s.index.notna()]
            for sid, j in cols.items()}


def read_export_groups(raw: bytes) -> dict[str, pd.Series]:
    df = pd.read_excel(io.BytesIO(raw), sheet_name=0, header=None)
    return _year_blocks(df, {"74.3_IEPP_0_M_35": r"productos primarios",
                             "74.3_IEMOA_0_M_48": r"origen agropecuario",
                             "74.3_IEMOI_0_M_46": r"origen industrial",
                             "74.3_IECE_0_M_35": r"^combustibles y energ"})


def read_import_uses(raw: bytes) -> dict[str, pd.Series]:
    df = pd.read_excel(io.BytesIO(raw), sheet_name=0, header=None)
    return _year_blocks(df, {"74.3_IIBCA_0_M_32": r"^bienes de capital",
                             "74.3_IIBI_0_M_36": r"^bienes intermedios",
                             "74.3_IICL_0_M_42": r"^combustibles y lubricantes",
                             "74.3_IIPABC_0_M_50": r"^piezas y accesorios",
                             "74.3_IIBCO_0_M_32": r"^bienes de consumo",
                             "74.3_IIVAP_0_M_49": r"^veh[ií]culos automotores",
                             "74.3_IIR_0_M_23": r"^resto"})


def read_trade_indices(raw: bytes) -> dict[str, pd.Series]:
    """Quarterly value, price and quantity indices (2004 = 100) and the terms of trade."""
    df = pd.read_excel(io.BytesIO(raw), sheet_name=0, header=None)
    h = find_row(df, r"^per[ií]odo", col=0)
    ce, ci, ct = find_col(df, h, r"^exportaciones"), find_col(df, h, r"^importaciones"), find_col(df, h, r"t[ée]rminos")
    def sub(lo, hi, pat):
        for j in range(lo, hi):
            if re.search(pat, norm(df.iat[h + 1, j])):
                return j
        raise LayoutChanged(f"no /{pat}/ column between {lo} and {hi}", preview(df, 8, 12))
    cols = {"80.2_IIPENG_0_T_45": sub(ce, ci, r"precio"), "80.2_IICENG_0_T_47": sub(ce, ci, r"cantidad"),
            "81.2_IIPING_0_T_45": sub(ci, ct, r"precio"), "81.2_IICING_0_T_47": sub(ci, ct, r"cantidad"),
            "82.2_ITI_2004_T_27": ct}
    cols["82.2_IPE_2004_T_26"], cols["82.2_IPI_2004_T_26"] = cols["80.2_IIPENG_0_T_45"], cols["81.2_IIPING_0_T_45"]
    year, dates = None, []
    for _, r in df.iloc[h + 2:].iterrows():
        label = f"{norm(r.iloc[0])} {norm(r.iloc[1]) if not isinstance(r.iloc[1], (int, float)) else ''}"
        y = re.match(r"\s*((?:19|20)\d{2})", label)          # year only on each year's first quarter
        if y:
            year = int(y.group(1))
        q = re.search(r"([1-4])\s*[º°o]?\.?\s*trim", label)
        dates.append(pd.Timestamp(year, 3 * int(q.group(1)) - 2, 1) if (year and q) else pd.NaT)
    dates = pd.DatetimeIndex(dates)
    body = df.iloc[h + 2:]
    return {sid: pd.Series(num(body.iloc[:, j]).values, index=dates).dropna().loc[lambda s: s.index.notna()]
            for sid, j in cols.items()}


def read_trade_1910(raw: bytes) -> dict[str, pd.Series]:
    """Annual exports and imports since 1910, thousands of US dollars (stored in millions)."""
    df = pd.read_excel(io.BytesIO(raw), sheet_name=0, header=None)
    h = find_row(df, r"^a[ñn]os", col=0)
    ce, ci = find_col(df, h, r"^exportaci"), find_col(df, h, r"^importaci")
    if not any("miles de usd" in norm(v) for v in df.iloc[h:h + 6, 0]):
        raise LayoutChanged("units are no longer 'Miles de USD'", preview(df, 10, 6))
    years = pd.to_numeric(df.iloc[h + 1:, 0].astype(str).str.extract(r"^(\d{4})")[0], errors="coerce")
    idx = pd.DatetimeIndex([pd.Timestamp(int(y), 1, 1) if pd.notna(y) else pd.NaT for y in years])
    body = df.iloc[h + 1:]
    return {sid: (pd.Series(num(body.iloc[:, j]).values, index=idx) / 1000).dropna().loc[lambda s: s.index.notna()]
            for sid, j in {"74.1_IET_0_A_16": ce, "74.1_IIT_0_A_25": ci}.items()}


# ── industry, wholesale prices, income distribution ─────────────────────────

def dated_file(stem: str, ext: str = "xls", back: int = 14):
    """Resolver for an INDEC file named after its publication month, e.g.
    sh_capacidad_10_26.xls (October 2026): tries this month and then earlier ones."""
    def resolve() -> str:
        today = datetime.now(timezone.utc).date()
        y, m = today.year, today.month
        for _ in range(back):
            url = f"{INDEC}/economia/{stem}_{m:02d}_{y % 100:02d}.{ext}"
            try:
                r = requests.get(url, timeout=30, headers={"User-Agent": USER_AGENT}, stream=True)
                head = next(r.iter_content(8), b"") if r.ok else b""
                r.close()
                if head[:4] in (b"\xd0\xcf\x11\xe0", b"PK\x03\x04"):       # an Excel file, not an error page
                    return url
            except requests.RequestException:
                pass
            y, m = (y, m - 1) if m > 1 else (y - 1, 12)
        raise FileNotFound(f"no {stem}_MM_YY.{ext} in the last {back} months")
    return resolve


UCII_COLS = {"31.3_UNG_2004_M_18": r"^nivel general", "31.3_UPAB_2004_M_35": r"alimenticios",
             "31.3_UPT_2004_M_21": r"tabaco", "31.3_UPT_2004_M_23": r"textiles", "31.3_UPC_2004_M_17": r"^papel",
             "31.3_UEI_2004_M_22": r"^edici[oó]n", "31.3_URP_2004_M_24": r"^refinaci[oó]n",
             "31.3_USPQ_2004_M_34": r"qu[ií]micos", "31.3_UCP_2004_M_20": r"caucho",
             "31.3_UMNM_2004_M_27": r"minerales no met", "31.3_UIMB_2004_M_33": r"met[aá]licas b[aá]sicas",
             "31.3_UV_2004_M_25": r"^industria automotriz", "31.3_UMNIA_2004_M_42": r"^metalmec"}


def read_ucii(raw: bytes) -> dict[str, pd.Series]:
    """Capacity utilization (%), total and the twelve blocks: a 'Año YYYY' row, then one row per month."""
    df = pd.read_excel(io.BytesIO(raw), sheet_name=0, header=None)
    h = find_row(df, r"^per[ií]odo", col=0)
    cols = {sid: find_col(df, h, pat) for sid, pat in UCII_COLS.items()}
    year, dates = None, []
    for v in df.iloc[h + 1:, 0]:
        y = re.match(r"a[ñn]o ((?:19|20)\d{2})", norm(v))
        if y:
            year = int(y.group(1))
        m = month_label(v) if (pd.notna(v) and not y) else None
        dates.append(pd.Timestamp(year, m, 1) if (year and m) else pd.NaT)
    dates = pd.DatetimeIndex(dates)
    body = df.iloc[h + 1:]
    return {sid: pd.Series(num(body.iloc[:, j]).values, index=dates).dropna().loc[lambda s: s.index.notna()]
            for sid, j in cols.items()}


IPIM_CODES = {"448.1_NIVEL_GENERAL_0_0_13_46": "ng_nivel_general",
              # INDEC-only ids: datos.gob.ar has these aggregates only in the old base (to 2015)
              "indec_ipim_domestic": "n_productos_nacionales",
              "indec_ipim_primary": "1_primarios",
              "indec_ipim_manufactured": "2_industria_manufacturera_ y_energia_electrica",
              "indec_ipim_imported": "i_productos_importados"}


def read_ipim(raw: bytes) -> dict[str, pd.Series]:
    """Wholesale price index (IPIM, Dec 2015 = 100), long CSV: periodo; apertura; índice."""
    df = pd.read_csv(io.BytesIO(raw), sep=";", dtype=str, encoding="latin-1")
    df.columns = [c.strip().lstrip("ï»¿﻿") for c in df.columns]
    need = ["periodo", "nivel_general_aperturas", "indice_ipim"]
    if any(c not in df.columns for c in need):
        raise LayoutChanged(f"expected columns {need}; found {list(df.columns)}", df.head(8).to_string())
    df["date"] = pd.to_datetime(df["periodo"].str.strip(), format="%Y-%m-%d", errors="coerce")
    df["v"] = pd.to_numeric(df["indice_ipim"].str.replace(",", ".", regex=False), errors="coerce")
    out = {}
    for sid, code in IPIM_CODES.items():
        s = df[df["nivel_general_aperturas"].str.strip() == code]
        if s.empty:
            raise LayoutChanged(f"no rows for {code!r}; codes now: {sorted(df['nivel_general_aperturas'].str.strip().unique())[:12]}")
        out[sid] = pd.Series(s["v"].values, index=pd.DatetimeIndex(s["date"])).dropna().sort_index()
    return out


def _quarter(label: str) -> int | None:
    q = re.search(r"([1-4])\s*[º°o]?\.?\s*trim", norm(label))
    return int(q.group(1)) if q else None


def read_gini(raw: bytes) -> dict[str, pd.Series]:
    """Gini coefficient of per capita household income, quarterly EPH (the October/May
    rounds of the pre-2003 survey are left out, as in the stored series)."""
    df = pd.read_excel(io.BytesIO(raw), sheet_name=0, header=None)
    h = find_row(df, r"^a[ñn]o$", col=0)
    c = find_col(df, h, r"gini")
    year, vals = None, {}
    for _, r in df.iloc[h + 1:].iterrows():
        y = re.match(r"((?:19|20)\d{2})(?:\D|$)", norm(r.iloc[0]).replace(".0", ""))
        if y:
            year = int(y.group(1))
        q = _quarter(r.iloc[1]) if pd.notna(r.iloc[1]) else None
        v = pd.to_numeric(r.iloc[c], errors="coerce")
        if year and q and pd.notna(v):
            vals[pd.Timestamp(year, 3 * q - 2, 1)] = float(v)
    return {"65.1_CGI_0_0_21": pd.Series(vals, dtype=float).sort_index()}


CGI_SHARES = {"53.1_TRTA_0_0_37": "RTA", "53.1_TIMB_0_0_25": "IBM", "53.1_EBE_0_0_27": "EEB"}


def _cgi_total(x: pd.ExcelFile, sheet: str) -> pd.Series:
    if sheet not in x.sheet_names:
        raise LayoutChanged(f"no sheet {sheet!r}; sheets: {x.sheet_names}")
    df = x.parse(sheet, header=None)
    qrow = find_row(df, r"trimestre$")
    trow = find_row(df, r"^total general$")
    year, vals = None, {}
    for j in range(df.shape[1]):
        y = re.match(r"((?:19|20)\d{2})", norm(df.iat[qrow - 1, j]).replace(".0", ""))
        if y:
            year = int(y.group(1))
        q = _quarter(df.iat[qrow, j]) if pd.notna(df.iat[qrow, j]) else None
        v = pd.to_numeric(df.iat[trow, j], errors="coerce")
        if year and q and pd.notna(v):
            vals[pd.Timestamp(year, 3 * q - 2, 1)] = float(v)
    if not vals:
        raise LayoutChanged(f"no quarterly totals in sheet {sheet!r}", preview(df, 8, 10))
    return pd.Series(vals).sort_index()


def read_cgi(raw: bytes) -> dict[str, pd.Series]:
    """Generation-of-income account: compensation of employees, mixed income and operating
    surplus as shares of gross value added at basic prices (the shares datos.gob.ar publishes)."""
    x = pd.ExcelFile(io.BytesIO(raw))
    vab = _cgi_total(x, "VAB_pb")
    return {sid: (_cgi_total(x, sh) / vab).dropna() for sid, sh in CGI_SHARES.items()}


# ── monthly activity (IPI, ISAC) and quarterly national accounts ─────────────

def year_file(stem: str, ext: str = "xls"):
    """Resolver for an INDEC file named after its year (sh_ipi_manufacturero_2026.xls):
    this year's file, or last year's early in January before the new one appears."""
    def resolve() -> str:
        y = datetime.now(timezone.utc).year
        for year in (y, y - 1):
            url = f"{INDEC}/economia/{stem}_{year}.{ext}"
            try:
                r = requests.get(url, timeout=30, headers={"User-Agent": USER_AGENT}, stream=True)
                head = next(r.iter_content(8), b"") if r.ok else b""
                r.close()
                if head[:4] in (b"\xd0\xcf\x11\xe0", b"PK\x03\x04"):
                    return url
            except requests.RequestException:
                pass
        raise FileNotFound(f"no {stem}_{y}.{ext} or {stem}_{y - 1}.{ext}")
    return resolve


def _monthly_blocks(df: pd.DataFrame, cols: dict[str, str]) -> dict[str, pd.Series]:
    """INDEC monthly tables with year and month in the first columns and one column group per
    series ("Serie original", "Serie desestacionalizada", "Serie tendencia-ciclo"): the index
    column of each group is the first one whose units read "Números índice"."""
    h = find_row(df, r"^per[ií]odo")
    units = find_row(df.iloc[h:h + 8], r"n[uú]meros [ií]ndice")
    out_cols = {}
    for sid, pat in cols.items():
        start = find_col(df, h, pat)
        j = next((j for j in range(start, min(start + 4, df.shape[1]))
                  if re.search(r"n[uú]meros [ií]ndice", norm(df.iat[units, j]))), None)
        if j is None:
            raise LayoutChanged(f"no 'Números índice' column under /{pat}/", preview(df, 10, 12))
        out_cols[sid] = j
    year, dates = None, []
    for i in range(units + 1, len(df)):
        lead = [df.iat[i, k] for k in range(min(3, df.shape[1]))]
        for v in lead:
            y = re.match(r"((?:19|20)\d{2})(?:\D|$)", norm(v).replace(".0", ""))
            if y:
                year = int(y.group(1))
        m = next((month_label(v) for v in lead if isinstance(v, str) and month_label(v)), None)
        dates.append(pd.Timestamp(year, m, 1) if (year and m) else pd.NaT)
    idx = pd.DatetimeIndex(dates)
    body = df.iloc[units + 1:]
    return {sid: pd.Series(num(body.iloc[:, j]).values, index=idx).dropna().loc[lambda s: s.index.notna()]
            for sid, j in out_cols.items()}


def read_ipi(raw: bytes) -> dict[str, pd.Series]:
    """Manufacturing production index (IPI manufacturero, 2004 = 100), Cuadro 1."""
    x = pd.ExcelFile(io.BytesIO(raw))
    if "Cuadro 1" not in x.sheet_names:
        raise LayoutChanged(f"no sheet 'Cuadro 1'; sheets: {x.sheet_names}")
    return _monthly_blocks(x.parse("Cuadro 1", header=None),
                           {"453.1_SERIE_ORIGNAL_0_0_14_46": r"^serie original",
                            "453.1_SERIE_DESEADA_0_0_24_58": r"^serie desestacionalizada"})


def read_isac(raw: bytes) -> dict[str, pd.Series]:
    """Construction activity indicator (ISAC, 2004 = 100), Cuadro 1."""
    x = pd.ExcelFile(io.BytesIO(raw))
    if "Cuadro 1" not in x.sheet_names:
        raise LayoutChanged(f"no sheet 'Cuadro 1'; sheets: {x.sheet_names}")
    return _monthly_blocks(x.parse("Cuadro 1", header=None),
                           {"33.2_ISAC_NIVELRAL_0_M_18_63": r"^serie original",
                            "33.2_ISAC_SIN_EDAD_0_M_23_56": r"^serie desestacionalizada",
                            "33.2_ISAC_CICLOCIA_0_M_20_62": r"^serie tendencia"})


CA_PAGE = "https://www.indec.gob.ar/Nivel4/Tema/3/9/47"


def ca_file(pattern: str):
    """Resolver for a national-accounts file named after its publication month
    (sh_oferta_demanda_09_26.xls), from INDEC's national accounts page: the latest one."""
    def resolve() -> str:
        html = download(CA_PAGE).decode("utf-8", "replace")
        found = set(re.findall(pattern, html))
        if not found:
            raise FileNotFound(f"no file matching /{pattern}/ on {CA_PAGE}")
        def when(name):
            m, y = re.findall(r"_(\d{2})_(\d{2})\.xls", name)[-1]
            return (int(y), int(m))
        return f"{INDEC}/economia/{max(found, key=when)}"
    return resolve


def _quarter_table(df: pd.DataFrame, rows: dict[str, str]) -> dict[str, pd.Series]:
    """National accounts tables: one row per aggregate (code in column 0, name in column 1),
    one column per quarter under a year header ("2026 (2)"), with an annual "Total" column."""
    qrow = find_row(df, r"[1-4]\s*[º°o]?\s*trimestre")
    year, qcols = None, {}
    for j in range(2, df.shape[1]):
        y = re.match(r"((?:19|20)\d{2})", norm(df.iat[qrow - 1, j]).replace(".0", ""))
        if y:
            year = int(y.group(1))
        q = _quarter(df.iat[qrow, j]) if pd.notna(df.iat[qrow, j]) else None
        if year and q:
            qcols[j] = pd.Timestamp(year, 3 * q - 2, 1)
    if not qcols:
        raise LayoutChanged("no quarter columns", preview(df, 8, 10))
    out = {}
    for sid, pat in rows.items():
        rx = re.compile(pat, re.I)
        i = next((i for i in range(qrow + 1, len(df)) if rx.search(norm(df.iat[i, 1])) or rx.search(norm(df.iat[i, 0]))), None)
        if i is None:
            raise LayoutChanged(f"no row matching /{pat}/", preview(df, 30, 3))
        out[sid] = pd.Series({d: pd.to_numeric(df.iat[i, j], errors="coerce") for j, d in qcols.items()},
                             dtype=float).dropna().sort_index()
    return out


OD_CONSTANT = {"4.2_OGP_2004_T_17": r"^producto interno bruto", "4.2_OGI_2004_T_25": r"^importaciones",
               "4.2_MGCP_2004_T_25": r"^consumo privado", "4.2_DGCP_2004_T_30": r"^consumo p[uú]blico",
               "4.2_DGE_2004_T_26": r"^exportaciones", "4.2_DGIT_2004_T_25": r"^formaci[oó]n bruta de capital fijo",
               "4.2_VE_2004_T_21": r"^variaci[oó]n de existencias", "4.2_DE_2004_T_24": r"^discrepancia"}
OD_INVESTMENT = {"4.2_DGIC_2004_T_32": r"^construcciones$", "4.2_DGIEDPMET_2004_T_51": r"^maquinaria y equipo",
                 "4.2_DGIEDPMTT_2004_T_53": r"^equipo de transporte"}
OD_CURRENT = {"4.4_OGP_2004_T_17": r"^producto interno bruto", "4.4_DGI_2004_T_19": r"^formaci[oó]n bruta de capital fijo"}
OD_DEFLATOR = {"25.2_OGP_2004_T_17": r"^producto interno bruto"}
GDP_BY_SECTOR = {"6.2_PIBPM_2004_T_38": r"^producto interno bruto", "6.2_IPNS_2004_T_35": r"^d214", "6.2_IVA_2004_T_23": r"^d211$", "6.2_DI_2004_T_20": r"^d212$",
                 "6.2_AGCS_2004_T_39": r"^a$", "6.2_P_2004_T_5": r"^b$", "6.2_EMC_2004_T_26": r"^c$",
                 "6.2_IM_2004_T_23": r"^d$", "6.2_SEGA_2004_T_32": r"^e$", "6.2_C_2004_T_12": r"^f$",
                 "6.2_CMMR_2004_T_41": r"^g$", "6.2_HR_2004_T_20": r"^h$", "6.2_TAC_2004_T_40": r"^i$",
                 "6.2_IF_2004_T_25": r"^j$", "6.2_AIEA_2004_T_48": r"^k$", "6.2_APDPSSAO_2004_T_60": r"^l$",
                 "6.2_E_2004_T_10": r"^m$", "6.2_SSS_2004_T_24": r"^n$", "6.2_OASCSP_2004_T_60": r"^o$",
                 "6.2_HPSD_2004_T_32": r"^p$"}


def read_national_accounts(raw: bytes) -> dict[str, pd.Series]:
    """INDEC's supply-and-demand workbook (sh_oferta_demanda_MM_YY.xls): GDP and its demand
    components at 2004 prices (cuadro 1), GDP by sector (cuadro 4), fixed investment by type
    (cuadro 6), current prices (cuadro 8) and implicit deflators (cuadro 9)."""
    x = pd.ExcelFile(io.BytesIO(raw))
    sheets = {norm(s): s for s in x.sheet_names}
    out = {}
    for name, rows in [("cuadro 1", OD_CONSTANT), ("cuadro 4", GDP_BY_SECTOR), ("cuadro 6", OD_INVESTMENT),
                       ("cuadro 8", OD_CURRENT), ("cuadro 9", OD_DEFLATOR)]:
        if name not in sheets:
            raise LayoutChanged(f"no sheet {name!r}; sheets: {x.sheet_names}")
        out.update(_quarter_table(x.parse(sheets[name], header=None), rows))
    # The statistical discrepancy is left blank in the definitive years (2004-2023): it is zero.
    gdp = out["4.2_OGP_2004_T_17"]
    out["4.2_DE_2004_T_24"] = out["4.2_DE_2004_T_24"].reindex(gdp.index).fillna(0.0)
    return out


def read_gdp_sa(raw: bytes) -> dict[str, pd.Series]:
    """Seasonally adjusted GDP at 2004 prices (sh_oferta_demanda_desest_MM_YY.xls): year in
    column 0 (first quarter only), quarter as a Roman numeral in column 1."""
    x = pd.ExcelFile(io.BytesIO(raw))
    df = x.parse(x.sheet_names[0], header=None)
    h = find_row(df, r"^a[ñn]o$", col=0)
    c = find_col(df, h, r"^pib$")
    roman = {"i": 1, "ii": 2, "iii": 3, "iv": 4}
    year, vals = None, {}
    for i in range(h + 1, len(df)):
        y = re.match(r"((?:19|20)\d{2})", norm(df.iat[i, 0]).replace(".0", ""))
        if y:
            year = int(y.group(1))
        q = roman.get(re.sub(r"[^ivx]", "", norm(df.iat[i, 1])))
        v = pd.to_numeric(df.iat[i, c], errors="coerce")
        if year and q and pd.notna(v):
            vals[pd.Timestamp(year, 3 * q - 2, 1)] = float(v)
    return {"3.2_OGP_D_2004_T_17": pd.Series(vals, dtype=float).sort_index()}


def datos_csv(columns: dict[str, str]):
    """Reader for a datos.gob.ar distribution CSV (the whole dataset as one file, not the API)."""
    def read(raw: bytes) -> dict[str, pd.Series]:
        df = pd.read_csv(io.BytesIO(raw))
        if "indice_tiempo" not in df.columns:
            raise LayoutChanged(f"no indice_tiempo column; columns {list(df.columns)[:10]}")
        missing = [c for c in columns.values() if c not in df.columns]
        if missing:
            raise LayoutChanged(f"missing columns {missing}")
        df.index = pd.to_datetime(df["indice_tiempo"])
        return {sid: pd.to_numeric(df[c], errors="coerce").dropna() for sid, c in columns.items()}
    return read


DATOS = "https://infra.datos.gob.ar/catalog/sspm/dataset"
ICA_CSV = {"74.3_IET_0_M_16": "ica_expo_totales", "74.3_IIT_0_M_25": "ica_importaciones_totales",
           "74.3_ISC_0_M_19": "ica_saldo_comercial", "74.3_IEPP_0_M_35": "ica_exportacion_productos_primarios",
           "74.3_IEMOA_0_M_48": "ica_exportacion_manufacturas_origen_agropecuario",
           "74.3_IEMOI_0_M_46": "ica_exportacion_manufacturas_origen_industrial",
           "74.3_IECE_0_M_35": "ica_exportacion_combustible_energia",
           "74.3_IIBCA_0_M_32": "ica_importaciones_bienes_capital", "74.3_IIBI_0_M_36": "ica_importaciones_bienes_intermedios",
           "74.3_IICL_0_M_42": "ica_importaciones_combustibles_lubricantes",
           "74.3_IIPABC_0_M_50": "ica_importaciones_piezas_accesorios_bienes_capital",
           "74.3_IIBCO_0_M_32": "ica_importaciones_bienes_consumo",
           "74.3_IIVAP_0_M_49": "ica_importaciones_vehiculos_automotores_pasajeros", "74.3_IIR_0_M_23": "ica_importaciones_resto"}
EXPO_PAISES = {"77.3_IEB_0_A_24": "ica_exportaciones_brasil", "77.3_IEC_0_A_23": "ica_exportaciones_china",
               "77.3_IEEU_0_A_32": "ica_exportaciones_estados_unidos", "77.3_IERU_0_A_29_20": "ica_exportaciones_resto_ue_28",
               "77.3_IETM_0_A_32": "ica_exportaciones_total_mercosur", "77.3_IET_0_A_25": "ica_exportaciones_totales"}
IMPO_PAISES = {"78.3_IIB_0_A_24": "ica_importaciones_brasil", "78.3_IIC_0_A_23": "ica_importaciones_china",
               "78.3_IIM_0_A_22_15": "ica_importaciones_estados_unidos", "78.3_IIRU_0_A_23_52": "ica_importaciones_resto_ue_28",
               "78.3_IITM_0_A_32": "ica_importaciones_total_mercosur", "78.3_IIT_0_A_25": "ica_importaciones_totales"}


AIF_CSV = {"379.9_GTOS_CAP_2017__13_60": "gtos_cap_2017",
           "379.9_GTOS_CAP_T017__50_91": "gtos_cap_transf_cap_prov_caba_total_prov_caba_2017",
           "379.9_GTOS_CORR_017_3_0_M_50_79": "gtos_corr_transf_corr_sec_priv_total_sec_priv_2017",
           "379.9_GTOS_CORR_017_5_0_M_51_42": "gtos_corr_transf_corr_sect_pub_caba_total_caba_2017",
           "379.9_GTOS_CORR_017__35_92": "gtos_corr_prestaciones_seg_soc_2017",
           "379.9_GTOS_CORR_017__40_84": "gtos_corr_int_ot_ren_prop_int_netos_2017",
           "379.9_GTOS_CORR_017__49_26": "gtos_corr_gtos_cons_operacion_remuneraciones_2017",
           "379.9_GTOS_PRIMA017__37_33": "gtos_primario_antes_figurativos_2017",
           "379.9_ING_ANTES_017__26_83": "ing_antes_figurativos_2017",
           "379.9_ING_CORR_I017__29_10": "ing_corr_ing_tributarios_2017",
           "379.9_RESULTADO_017__18_38": "resultado_fin_2017",
           "379.9_SUPERAVIT_017__23_94": "superavit_primario_2017"}


# ── providers ────────────────────────────────────────────────────────────────
# mode "full": the file has the whole history. "recent": only the latest months, merged
# onto the stored history (the newer file wins where they overlap).

PROVIDERS = [
    {"name": "INDEC CPI (serie_ipc_divisiones.csv)", "url": f"{INDEC}/economia/serie_ipc_divisiones.csv",
     "read": read_cpi, "mode": "full", "ids": None},
    {"name": "INDEC EMAE (sh_emae_mensual_base2004.xls)", "url": f"{INDEC}/economia/sh_emae_mensual_base2004.xls",
     "read": read_emae, "mode": "full",
     "ids": ["143.3_NO_PR_2004_A_21", "143.3_NO_PR_2004_A_31", "143.3_NO_PR_2004_A_28"]},
    {"name": "INDEC EMAE by sector (sh_emae_actividad_base2004.xls)",
     "url": f"{INDEC}/economia/sh_emae_actividad_base2004.xls",
     "read": read_emae_sectors, "mode": "full", "ids": [*EMAE_LETTERS.values(), "11.3_IF_2004_M_25"]},
    {"name": "INDEC wage index (indice_salarios.csv)", "url": f"{INDEC}/sociedad/indice_salarios.csv",
     "read": read_wages, "mode": "full",
     "ids": ["149.1_SOR_PRIADO_OCTU_0_25", "149.1_SOR_PUBICO_OCTU_0_14", "149.1_SOR_PRIADO_OCTU_0_28",
             "149.1_TL_INDIIOS_OCTU_0_21"]},
    {"name": "Secretaría de Trabajo, registered employment (trabajoregistrado_*_estadisticas.xlsx)",
     "url": sipa_url, "page": SIPA_PAGE, "read": read_sipa, "mode": "full", "ids": list(SIPA_COLS)},
    {"name": "Seguridad Social, RIPTE (web page table)", "url": RIPTE_PAGE,
     "read": read_ripte, "mode": "recent", "ids": ["158.1_REPTE_0_0_5"]},
    # Foreign trade. Totals have full history; the product-group and use files cover the current
    # and previous year only and are merged onto the stored history.
    {"name": "INDEC trade balance (balanmensual.xls)", "url": f"{INDEC}/economia/balanmensual.xls",
     "read": read_trade_totals, "mode": "full", "ids": ["74.3_IET_0_M_16", "74.3_IIT_0_M_25", "74.3_ISC_0_M_19"]},
    {"name": "INDEC exports by product group (expo_grandes_rubros_*.xls)",
     "url": ica_file(r"expo_grandes_rubros_\d{4}_\d{4}\.xls"), "page": ICA_PAGE,
     "read": read_export_groups, "mode": "recent",
     "ids": ["74.3_IEPP_0_M_35", "74.3_IEMOA_0_M_48", "74.3_IEMOI_0_M_46", "74.3_IECE_0_M_35"]},
    {"name": "INDEC imports by use (impo_uso_economico_*.xls)",
     "url": ica_file(r"impo_uso_economico_\d{4}_\d{4}\.xls"), "page": ICA_PAGE,
     "read": read_import_uses, "mode": "recent",
     "ids": ["74.3_IIBCA_0_M_32", "74.3_IIBI_0_M_36", "74.3_IICL_0_M_42", "74.3_IIPABC_0_M_50",
             "74.3_IIBCO_0_M_32", "74.3_IIVAP_0_M_49", "74.3_IIR_0_M_23"]},
    {"name": "INDEC trade price and quantity indices (sh_indices_comext_04.xls)",
     "url": f"{INDEC}/economia/sh_indices_comext_04.xls", "read": read_trade_indices, "mode": "full",
     "ids": ["80.2_IIPENG_0_T_45", "80.2_IICENG_0_T_47", "81.2_IIPING_0_T_45", "81.2_IICING_0_T_47",
             "82.2_IPE_2004_T_26", "82.2_IPI_2004_T_26", "82.2_ITI_2004_T_27"]},
    {"name": "INDEC trade since 1910 (balan_1910_*.xls)", "url": ica_file(r"balan_1910_\d{4}\.xls"),
     "page": ICA_PAGE, "read": read_trade_1910, "mode": "full", "ids": ["74.1_IET_0_A_16", "74.1_IIT_0_A_25"]},
    # Monthly activity and quarterly national accounts.
    {"name": "INDEC manufacturing production (sh_ipi_manufacturero_YYYY.xls)",
     "url": year_file("sh_ipi_manufacturero"), "read": read_ipi, "mode": "full",
     "ids": ["453.1_SERIE_ORIGNAL_0_0_14_46", "453.1_SERIE_DESEADA_0_0_24_58"]},
    {"name": "INDEC construction activity (sh_isac_YYYY.xls)", "url": year_file("sh_isac"), "read": read_isac,
     "mode": "full", "ids": ["33.2_ISAC_NIVELRAL_0_M_18_63", "33.2_ISAC_SIN_EDAD_0_M_23_56", "33.2_ISAC_CICLOCIA_0_M_20_62"]},
    {"name": "INDEC supply and demand (sh_oferta_demanda_MM_YY.xls)",
     "url": ca_file(r"sh_oferta_demanda_\d{2}_\d{2}\.xls"), "page": CA_PAGE, "read": read_national_accounts,
     "mode": "full", "ids": [*OD_CONSTANT, *OD_INVESTMENT, *OD_CURRENT, *OD_DEFLATOR, *GDP_BY_SECTOR]},
    {"name": "INDEC seasonally adjusted GDP (sh_oferta_demanda_desest_MM_YY.xls)",
     "url": ca_file(r"sh_oferta_demanda_desest_\d{2}_\d{2}\.xls"), "page": CA_PAGE, "read": read_gdp_sa,
     "mode": "full", "ids": ["3.2_OGP_D_2004_T_17"]},
    # Industry, wholesale prices, income distribution.
    {"name": "INDEC capacity utilization (sh_capacidad_MM_YY.xls)", "url": dated_file("sh_capacidad"),
     "read": read_ucii, "mode": "full", "ids": list(UCII_COLS)},
    {"name": "INDEC wholesale prices (indice_ipim.csv)", "url": f"{INDEC}/economia/indice_ipim.csv",
     "read": read_ipim, "mode": "full", "ids": list(IPIM_CODES)},
    {"name": "INDEC generation-of-income account (serie_cgi_MM_YY.xls)", "url": dated_file("serie_cgi"),
     "read": read_cgi, "mode": "full", "ids": list(CGI_SHARES)},
    {"name": "INDEC Gini coefficient (serie_coeficiente_gini.xlsx)",
     "url": f"{INDEC}/sociedad/serie_coeficiente_gini.xlsx", "read": read_gini, "mode": "full",
     "ids": ["65.1_CGI_0_0_21"],
     # INDEC has revised 2003-2006 since datos.gob.ar copied the series, and no longer publishes
     # the first half of 2007; its file is the official one, so the check starts in 2007.
     "agree_from": "2007-01-01"},
    # Public sector accounts (Secretaría de Hacienda, base caja): the whole statement as one file,
    # the same file the "aif:" series read.
    {"name": "datos.gob.ar public sector accounts (CSV)",
     "url": f"{DATOS}/379/distribution/379.9/download/sector-publico-nacional-valores-mensuales-17.csv",
     "read": datos_csv(AIF_CSV), "mode": "full", "ids": list(AIF_CSV)},
    # datos.gob.ar's full CSV downloads: the only monthly source by partner, and the fallback
    # for the INDEC trade files above (series already served are skipped).
    {"name": "datos.gob.ar exports by partner (CSV)", "url": f"{DATOS}/77/distribution/77.3/download/exportaciones-por-paises-regiones-mensual.csv",
     "read": datos_csv(EXPO_PAISES), "mode": "full", "ids": list(EXPO_PAISES)},
    {"name": "datos.gob.ar imports by partner (CSV)", "url": f"{DATOS}/78/distribution/78.3/download/importaciones-por-paises-regiones-mensual.csv",
     "read": datos_csv(IMPO_PAISES), "mode": "full", "ids": list(IMPO_PAISES)},
    {"name": "datos.gob.ar trade by group and use (CSV, fallback)", "url": f"{DATOS}/74/distribution/74.3/download/intercambio-comercial-argentino-mensual.csv",
     "read": datos_csv(ICA_CSV), "mode": "full", "ids": list(ICA_CSV), "fallback": True},
]


def _ids(p: dict) -> list[str]:
    if p["ids"] is None:
        from . import indec_ipc
        return list(indec_ipc.MAP)
    return p["ids"]


AGREE_TOL = 0.005        # median relative difference allowed against the stored history
AGREE_SHARE = 0.9        # share of overlapping months that must be within 2 x tolerance
RECENT_EXCLUDED = 12     # latest months left out of the comparison (they get revised)


def agreement(sid: str, new: pd.Series, since: str | None = None) -> str | None:
    """None if `new` agrees with the stored history (from `since`, if given); else a description of the mismatch."""
    old = store.as_of(sid).dropna()
    if since:
        old = old[old.index >= pd.Timestamp(since)]
    if old.empty or new.empty:
        return None
    common = old.index.intersection(new.index)
    if len(common) > RECENT_EXCLUDED + 12:
        common = common[:-RECENT_EXCLUDED]
    if len(common) < 6:
        return None
    rel = ((new[common] - old[common]).abs() / old[common].abs().clip(lower=1e-9))
    if rel.median() > AGREE_TOL or (rel <= 2 * AGREE_TOL).mean() < AGREE_SHARE:
        worst = rel.idxmax()
        return (f"{sid}: values disagree with the stored history on {len(common)} common months "
                f"(median difference {rel.median():.2%}; worst {worst:%Y-%m}: file {new[worst]:g}, stored {old[worst]:g})")
    return None


def _load_status() -> dict:
    try:
        return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def run_provider(p: dict, wanted: set[str]) -> tuple[dict[str, pd.Series], dict]:
    """Series for the provider's ids among `wanted`, and its status entry. Never raises."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    entry = {"checked": now, "page": p.get("page")}
    try:
        url = p["url"]() if callable(p["url"]) else p["url"]
        entry["url"] = url
        series = p["read"](download(url))
        missing = [sid for sid in wanted if sid not in series or series[sid].empty]
        if missing:
            raise LayoutChanged(f"no data for {missing}")
        bad = [msg for sid in sorted(wanted) if (msg := agreement(sid, series[sid], p.get("agree_from")))]
        if bad:
            raise LayoutChanged("; ".join(bad))
        if p["mode"] == "recent":
            for sid in list(series):
                old = store.as_of(sid).dropna()
                series[sid] = pd.concat([old[~old.index.isin(series[sid].index)], series[sid]]).sort_index()
        entry.update(status="ok", last=max(s.index.max() for s in series.values()).strftime("%Y-%m-%d"))
        return {sid: s for sid, s in series.items() if sid in wanted}, entry
    except LayoutChanged as exc:
        entry.update(status="error", kind="layout", error=str(exc)[:1500], preview=exc.preview[:3000])
    except FileNotFound as exc:
        entry.update(status="error", kind="not_found", error=str(exc)[:1500])
    except Exception as exc:  # a reader bug: report it as a layout problem, with the traceback
        entry.update(status="error", kind="layout", error=f"{type(exc).__name__}: {exc}"[:1500],
                     preview=traceback.format_exc()[-3000:])
    return {}, entry


def fetch(ids) -> tuple[pd.DataFrame, dict, set]:
    """Long data, meta and the set of ids served from primary files, for the ids they cover."""
    wanted = set(ids)
    status = _load_status()
    frames, meta, served = [], {}, set()
    for p in PROVIDERS:
        todo = (wanted - served) & set(_ids(p))
        if not todo:
            if p.get("fallback") and wanted & set(_ids(p)):
                status[p["name"]] = {**status.get(p["name"], {}), "status": "standby",
                                     "checked": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            continue
        series, entry = run_provider(p, todo)
        prev = status.get(p["name"], {})
        if entry["status"] == "error":
            entry["error_since"] = prev.get("error_since") or entry["checked"]
            print(f"::warning title=primary source::{p['name']}: {entry['error'][:300]} (using datos.gob.ar)")
        status[p["name"]] = entry
        for sid, s in series.items():
            s = s[~s.index.duplicated(keep="last")].sort_index()
            frames.append(pd.DataFrame({"date": s.index, "series_id": sid, "value": s.values}))
            meta[sid] = {"units": None, "description": None, "time_index_end": s.index.max().strftime("%Y-%m-%d"),
                         "dataset": p["name"]}
            served.add(sid)
    try:
        STATUS_FILE.write_text(json.dumps(status, indent=1, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    data = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["date", "series_id", "value"])
    return data, meta, served
