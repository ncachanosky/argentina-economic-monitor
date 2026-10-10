"""EMAE vintages from INDEC's informes técnicos (PDF): Cuadro 2 of each release
(original, seasonally adjusted and trend-cycle indices, 2004 = 100, and their
changes) as published at the time.

    python -m pipeline.emae_vintages data/manual/emae_vintages data/manual/emae_trend_vintages.csv

Reads every PDF in the folder (pdftotext -layout), takes the release date from
"Buenos Aires, <day> de <month> de <year>" and writes one row per release and
month. Used to measure how many releases the trend-cycle takes to signal a
turning point (methodology: EMAE trend-cycle in real time).
"""
import re, subprocess, sys, glob, os
import pandas as pd
MES = {m: i + 1 for i, m in enumerate(["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"])}
MES["setiembre"] = 9
NUM = r"-?\d+(?:,\d+)?|///|\.\.\.|-"
def num(t):
    return None if t in ("///", "...", "-") else float(t.replace(",", "."))
def parse(path):
    txt = subprocess.run(["pdftotext", "-layout", path, "-"], capture_output=True, text=True).stdout
    m = re.search(r"Buenos Aires,\s+(\d{1,2})\s+de\s+(\w+)\s+(?:de\s+)?(\d{4})", txt)
    rel = pd.Timestamp(int(m.group(3)), MES[m.group(2).lower()], int(m.group(1))) if m else None
    rows, year, seen = [], None, set()
    for line in txt.splitlines():
        s = re.sub(r"[–−]\s*[–−]", " 0 ", line.strip())
        s = re.sub(r"[–−]\s*(?=\d)", "-", s)
        y = re.match(r"^((?:19|20)\d{2})(?:\s*[*()|\d ]{0,8})?$", s)
        if y:
            year = int(y.group(1)); continue
        mm = re.match(r"^(Enero|Febrero|Marzo|Abril|Mayo|Junio|Julio|Agosto|Septiembre|Setiembre|Octubre|Noviembre|Diciembre)\s*\*?\s+(.*)$", s, re.I)
        if not (mm and year):
            continue
        toks = re.findall(NUM, mm.group(2).replace("*", " "))
        if len(toks) != 6:
            continue
        key = (year, MES[mm.group(1).lower()])
        if key in seen:
            continue
        seen.add(key)
        v = [num(t) for t in toks]
        rows.append({"period": pd.Timestamp(key[0], key[1], 1), "original": v[0], "yoy": v[1], "sa": v[2], "sa_mom": v[3], "trend": v[4], "trend_mom": v[5]})
    df = pd.DataFrame(rows)
    return rel, df
if __name__ == "__main__":
    out = []
    for p in sorted(glob.glob(sys.argv[1] + "/*.pdf")):
        rel, df = parse(p)
        name = os.path.basename(p)
        if df.empty:
            print(name, rel, "NO ROWS"); continue
        last = df.period.max()
        print(name, rel.date() if rel is not None else None, len(df), df.period.min().date(), last.date(), "trend nulls", int(df.trend.isna().sum()))
        df.insert(0, "release", rel); df.insert(1, "file", name)
        out.append(df)
    pd.concat(out).to_csv(sys.argv[2], index=False)
