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
        y = pd.to_numeric(r.iloc[0], errors="coerce")
        if pd.notna(y) and 1990 <= y <= 2100:
            year = int(y)
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
]


def _ids(p: dict) -> list[str]:
    if p["ids"] is None:
        from . import indec_ipc
        return list(indec_ipc.MAP)
    return p["ids"]


AGREE_TOL = 0.005        # median relative difference allowed against the stored history
AGREE_SHARE = 0.9        # share of overlapping months that must be within 2 x tolerance
RECENT_EXCLUDED = 12     # latest months left out of the comparison (they get revised)


def agreement(sid: str, new: pd.Series) -> str | None:
    """None if `new` agrees with the stored history; else a description of the mismatch."""
    old = store.as_of(sid).dropna()
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
        missing = [sid for sid in _ids(p) if sid in wanted and (sid not in series or series[sid].empty)]
        if missing:
            raise LayoutChanged(f"no data for {missing}")
        bad = [msg for sid in _ids(p) if sid in wanted and (msg := agreement(sid, series[sid]))]
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
        if not wanted & set(_ids(p)):
            continue
        series, entry = run_provider(p, wanted)
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
