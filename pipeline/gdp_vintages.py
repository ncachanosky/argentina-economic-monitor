"""Quarterly GDP vintages: INDEC's national-accounts workbooks as published at each release.

INDEC keeps every version of its quarterly supply-and-demand workbooks on its server, named
by release month (…_MM_YY.xls), since the September 2016 release (the first after the 2016
statistical emergency). For each release this reads:

  sh_oferta_demanda_desest_MM_YY.xls   seasonally adjusted series, millions of 2004 pesos:
                                       GDP, imports, private and public consumption,
                                       gross fixed capital formation, exports
  sh_oferta_demanda_MM_YY.xls          original series (Cuadro 1): GDP

and writes one row per release and quarter to data/manual/gdp_vintages.csv. Release dates
come from the press releases (informes de avance, "Buenos Aires, <day> de <month> de
<year>"); where a press release could not be found, the column is empty.

One-time load: the files are fetched on GitHub's network (the fetch-file workflow) and kept
in data/manual/gdp_vintages/ (the seasonally adjusted workbooks; the original-series ones
are large and only their GDP row is kept). Later releases are recorded by the daily update
in data/vintages/ like every other series.

    python -m pipeline.gdp_vintages <folder with the .xls and .pdf files> data/manual/gdp_vintages.csv
"""
from __future__ import annotations

import glob
import os
import re
import subprocess
import sys
import unicodedata

import pandas as pd

ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4}
MES = {m: i + 1 for i, m in enumerate(["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
                                       "septiembre", "octubre", "noviembre", "diciembre"])}
MES["setiembre"] = 9
SA_COLS = {"pib": "gdp_sa", "importaciones": "imports_sa", "consumo privado": "cons_priv_sa", "consumo publico": "cons_pub_sa",
           "fbcf": "gfcf_sa", "iibf": "gfcf_sa", "exportaciones": "exports_sa"}


def vintage_of(path: str) -> str:
    m = re.search(r"_(\d{2})_(\d{2})\.(?:xls|pdf)$", os.path.basename(path))
    if m:
        return f"20{m.group(2)}-{m.group(1)}"
    m = re.search(r"pib_(\d{4})_(\d{2})\.pdf$", os.path.basename(path))
    return f"{m.group(1)}-{m.group(2)}"


def read_sa(path: str) -> pd.DataFrame:
    d = pd.read_excel(path, sheet_name=0, header=None)
    hdr = next(i for i in range(len(d)) if any(str(v).strip().lower() == "trimestre" for v in d.iloc[i].values))
    cols = {}
    for j, v in enumerate(d.iloc[hdr].values):
        k = unicodedata.normalize("NFKD", str(v).strip().lower()).encode("ascii", "ignore").decode()
        if k in SA_COLS and SA_COLS[k] not in cols.values():
            cols[j] = SA_COLS[k]
    qcol = next(j for j, v in enumerate(d.iloc[hdr].values) if str(v).strip().lower() == "trimestre")
    rows, year = [], None
    for i in range(hdr + 1, len(d)):
        y = d.iat[i, 0]
        if isinstance(y, (int, float)) and not pd.isna(y) and 1990 < y < 2100:
            year = int(y)
        q = str(d.iat[i, qcol]).strip().upper()
        if year and q in ROMAN:
            rows.append({"period": pd.Timestamp(year, 3 * ROMAN[q] - 2, 1), **{c: float(d.iat[i, j]) for j, c in cols.items()}})
    return pd.DataFrame(rows)


def read_original_gdp(path: str) -> pd.Series:
    x = pd.ExcelFile(path)
    sheet = next(s for s in x.sheet_names if s.strip().lower().replace(" ", "") == "cuadro1")
    d = pd.read_excel(x, sheet, header=None)
    yrow = next(i for i in range(12) if sum(bool(re.match(r"^\d{4}", str(v).strip())) for v in d.iloc[i].values) > 3)
    qrow = yrow + 1
    grow = next(i for i in range(qrow, len(d)) if any("producto interno bruto" in str(v).lower() for v in d.iloc[i, :3].values))
    out, year = {}, None
    for j in range(len(d.columns)):
        m = re.match(r"^(\d{4})", str(d.iat[yrow, j]).strip())
        if m:
            year = int(m.group(1))
        q = re.match(r"^(\d)º", str(d.iat[qrow, j]).strip())
        if year and q:
            v = d.iat[grow, j]
            if isinstance(v, (int, float)) and not pd.isna(v):
                out[pd.Timestamp(year, 3 * int(q.group(1)) - 2, 1)] = float(v)
    return pd.Series(out, name="gdp_orig")


def release_date(pdf: str):
    txt = subprocess.run(["pdftotext", "-layout", pdf, "-"], capture_output=True, text=True).stdout
    m = re.search(r"Buenos Aires,\s+(\d{1,2})\s+de\s+(\w+)\s+(?:de\s+)?(\d{4})", txt)
    return pd.Timestamp(int(m.group(3)), MES[m.group(2).lower()], int(m.group(1))) if m else None


def main(folder: str, out: str) -> None:
    dates = {vintage_of(p): release_date(p) for p in glob.glob(os.path.join(folder, "pib_*.pdf"))}
    frames = []
    for p in sorted(glob.glob(os.path.join(folder, "sh_oferta_demanda_desest_*.xls"))):
        v = vintage_of(p)
        sa = read_sa(p).set_index("period")
        orig = os.path.join(folder, os.path.basename(p).replace("_desest", ""))
        if os.path.exists(orig):
            sa = sa.join(read_original_gdp(orig), how="left")
        sa.insert(0, "release", dates.get(v))
        sa.insert(0, "vintage", v)
        frames.append(sa.reset_index())
        print(v, dates.get(v), len(sa), sa.index.max().date(), "orig" if "gdp_orig" in sa else "-")
    df = pd.concat(frames, ignore_index=True)
    df.to_csv(out, index=False, float_format="%.3f", date_format="%Y-%m-%d")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
