"""Adapter for the BCRA's monthly archive of financial-institution data
("Información de Entidades Financieras", one .7z of .txt files per month).

We read the historical balance sheets of every institution (Tec_Cont/bal_hist:
entity, month, account code of the BCRA chart of accounts, balance in
thousands of pesos, debits positive and credits negative, monthly since
November 1994, complete from June 1996), the names of current and past institutions, and the BCRA's
group of each current institution. From them:

  series  "bcraef:<measure>_<base>": concentration of the system, monthly.
          measure: hhi (Herfindahl-Hirschman, shares in %, 0-10,000), top5,
          top10 (combined share, %), n (number of institutions with data);
          base: assets, deposits, loans.
  table   data/tables/bcra_entities/top10.json: the ten largest institutions
          by assets, with the composition of their balance sheets, at the
          latest month and each December, next to the system and bank groups.
"""
from __future__ import annotations

import io
import re
import time
from datetime import date

import pandas as pd
import requests

from . import FetchResult

BASE = "https://www.bcra.gob.ar/archivos/Pdfs/PublicacionesEstadisticas/Entidades/{ym}d.7z"
TIMEOUT = 600
USER_AGENT = "argentina-economic-monitor (+https://github.com/ncachanosky/argentina-economic-monitor)"
LAST: dict | None = None     # parsed archive, kept for write_tables
START = "199606"             # before June 1996 the archive omits the public banks (Nación, Provincia)

# ---- chart-of-accounts groupings (6-digit codes, by prefix) ----
LOANS_PUBLIC = ("1311", "1312", "1313", "1351", "1352", "1353", "1381", "1391")
LOANS_PRIVATE = ("1317", "1318", "1319", "1357", "1358", "1359", "1383", "1393")
DEPOSITS_ARS = ("311", "312", "318")
DEPOSITS_FX = ("315", "316", "319")
BCRA_WORDS = re.compile(r"BCRA|B\.C\.R\.A|LEBAC|LELIQ|NOBAC|NOTALIQ|LEDIV|LETRAS DE LIQUIDEZ|NOTAS DE LIQUIDEZ")
PRIVATE_WORDS = re.compile(r"PRIVAD")
# Repos with the BCRA (chart of accounts, Com. "A" plan de cuentas): principal and accrued interest,
# in pesos and in foreign currency, on Treasury securities and on BCRA monetary instruments.
BCRA_REPO = {"141143", "141144", "141222", "145106", "145129", "145222"}

ASSET_ROWS = [
    ("liquid", "Cash and BCRA current accounts"),
    ("gov_sec", "Government securities (Treasury, provinces; incl. LEFI)"),
    ("bcra_sec", "BCRA notes and repos with the BCRA"),
    ("priv_sec", "Private securities"),
    ("loans_private", "Loans to the private sector"),
    ("loans_public", "Loans to the public sector"),
    ("loans_other", "Other loans (financial sector, non-residents)"),
    ("other_fin", "Other financial claims"),
    ("other_assets", "Other assets"),
]
FUNDING_ROWS = [
    ("dep_ars", "Peso deposits"),
    ("dep_fx", "Foreign-currency deposits"),
    ("other_liab", "Other liabilities"),
    ("equity", "Net equity"),
]


def _candidates(today: date | None = None) -> list[str]:
    t = today or date.today()
    out = []
    y, m = t.year, t.month
    for _ in range(6):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        out.append(f"{y}{m:02d}")
    return out


def download() -> tuple[bytes, str]:
    last = None
    with requests.Session() as s:
        s.headers["User-Agent"] = USER_AGENT
        for ym in _candidates():
            try:
                r = s.get(BASE.format(ym=ym), timeout=TIMEOUT)
                if r.status_code == 404 or len(r.content) < 1_000_000:
                    continue
                r.raise_for_status()
                expected = r.headers.get("Content-Length")
                if expected and int(expected) != len(r.content):
                    raise IOError(f"incomplete download: {len(r.content)} of {expected} bytes")
                return r.content, ym
            except Exception as exc:
                last = exc
                time.sleep(5)
    raise RuntimeError(f"no entity archive found for the last six months ({last})")


def _read_txt(raw: bytes, names: list[str]) -> pd.DataFrame:
    return pd.read_csv(io.BytesIO(raw), sep="\t", header=None, names=names, dtype=str, encoding="latin1",
                       quotechar='"', keep_default_na=False)


def load_archive(content: bytes) -> dict:
    import py7zr
    import tempfile
    from pathlib import Path
    wanted = re.compile(r"(bal_hist/balhist\.txt|cuentas/cuentas\.txt|Entfin/Nomina\.txt|Entfin/Grupos\.txt|"
                        r"Tec_Cont/entint/\d+\.txt|Info_Hist/(Activas|Bajas)/.*)$", re.I)
    with tempfile.TemporaryDirectory() as tmp:
        with py7zr.SevenZipFile(io.BytesIO(content)) as z:
            names = [n for n in z.getnames() if wanted.search(n)]
            z.extract(path=tmp, targets=names)
        root = Path(tmp)
        bal = next(root.rglob("balhist.txt"))
        df = pd.read_csv(bal, sep="\t", header=None, names=["ent", "date", "acct", "v"],
                         dtype={"ent": str, "date": str, "acct": str, "v": "float64"}, quotechar='"')
        cu = _read_txt(next(root.rglob("cuentas.txt")).read_bytes(), ["acct", "desc", "baja"])
        names: dict[str, str] = {}
        for folder in ("Bajas", "Activas"):          # current names override old ones
            for p in sorted(root.rglob(f"Info_Hist/{folder}/*")):
                m = re.match(r"(\d{5})(.+)\.txt$", p.name)
                if m:
                    names[m.group(1)] = m.group(2).strip()
        nom = _read_txt(next(root.rglob("Nomina.txt")).read_bytes(), ["ent", "name", "short"])
        for _, r in nom.iterrows():
            names.setdefault(r.ent, r["name"].title())
        short = {r.ent: r.short for _, r in nom.iterrows()}
        groups: dict[str, set] = {}
        for p in root.rglob("entint/*.txt"):
            try:
                t = _read_txt(p.read_bytes(), ["ent", "name", "date", "group"])
            except Exception:
                continue
            for _, r in t.iterrows():
                groups.setdefault(r.ent, set()).add(r.group.strip())
    desc = dict(zip(cu.acct, cu.desc.str.strip().str.upper()))
    return {"bal": df, "desc": desc, "names": names, "short": short, "groups": groups}


def _category(acct: str, desc: dict) -> str:
    a = acct
    if a[0] not in "12":
        if a.startswith(DEPOSITS_ARS):
            return "dep_ars"
        if a.startswith(DEPOSITS_FX):
            return "dep_fx"
        if a[0] == "3":
            return "other_liab"
        return "equity"                                   # 4, 5, 6: net equity and results of the year
    if a.startswith("11"):
        return "liquid"
    if a.startswith("12"):
        d = desc.get(a, "")
        if BCRA_WORDS.search(d):
            return "bcra_sec"
        if PRIVATE_WORDS.search(d):
            return "priv_sec"
        return "gov_sec"
    if a.startswith("13"):
        if a.startswith(LOANS_PUBLIC):
            return "loans_public"
        if a.startswith(LOANS_PRIVATE):
            return "loans_private"
        return "loans_other"
    if a.startswith("14"):
        return "bcra_sec" if a in BCRA_REPO else "other_fin"
    return "other_assets"


def concentration(arc: dict) -> pd.DataFrame:
    df = arc["bal"]
    df = df[df.acct.str[0].isin(list("123456")) & (df.date >= START)]
    keys = {"assets": df.acct.str[0].isin(["1", "2"]), "deposits": df.acct.str.startswith("31"),
            "loans": df.acct.str.startswith("13")}
    rows = []
    for base, mask in keys.items():
        s = df[mask].groupby(["date", "ent"]).v.sum()
        if base == "deposits":
            s = -s
        for d, g in s.groupby(level=0):
            g = g[g > 0]
            if g.empty:
                continue
            sh = g / g.sum() * 100
            top = sh.sort_values(ascending=False)
            stamp = pd.Timestamp(int(d[:4]), int(d[4:6]), 1)
            rows += [(stamp, f"bcraef:hhi_{base}", float((sh ** 2).sum())),
                     (stamp, f"bcraef:top5_{base}", float(top.iloc[:5].sum())),
                     (stamp, f"bcraef:top10_{base}", float(top.iloc[:10].sum())),
                     (stamp, f"bcraef:n_{base}", float(len(g)))]
    return pd.DataFrame(rows, columns=["date", "series_id", "value"])


def composition(arc: dict, months: list[str]) -> dict:
    """Balance-sheet composition of every institution in `months` (shares of total assets, %)."""
    df = arc["bal"]
    df = df[df.date.isin(months) & df.acct.str[0].isin(list("123456"))].copy()
    cats = {a: _category(a, arc["desc"]) for a in df.acct.unique()}
    df["cat"] = df.acct.map(cats)
    t = df.groupby(["date", "ent", "cat"]).v.sum().unstack(fill_value=0.0)
    for c in ("dep_ars", "dep_fx", "other_liab", "equity"):
        if c in t:
            t[c] = -t[c]
    t["assets"] = t[[c for c, _ in ASSET_ROWS if c in t]].sum(axis=1)
    return t


def top10_tables(arc: dict) -> dict:
    dates = sorted(arc["bal"].date.unique())
    latest = dates[-1]
    months = sorted({d for d in dates if d.endswith("12") and d >= START} | {latest})
    comp = composition(arc, months)
    out = {"dates": [], "rows": {"assets": ASSET_ROWS, "funding": FUNDING_ROWS}, "tables": {}}
    pub = {e for e, g in arc["groups"].items() if "AA110" in g}
    priv = {e for e, g in arc["groups"].items() if "AA120" in g}

    def shares(t: pd.DataFrame) -> dict:
        tot = t["assets"].sum()
        vals = {c: round(float(t[c].sum() / tot * 100), 2) if c in t else 0.0 for c, _ in ASSET_ROWS + FUNDING_ROWS}
        dep = t.get("dep_ars", 0).sum() + t.get("dep_fx", 0).sum()
        vals["fx_share_dep"] = round(float(t.get("dep_fx", 0).sum() / dep * 100), 2) if dep else None
        loans = t.get("loans_private", 0).sum() + t.get("loans_public", 0).sum() + t.get("loans_other", 0).sum()
        vals["loans_to_dep"] = round(float(loans / dep * 100), 2) if dep else None
        vals["public_total"] = round(vals["gov_sec"] + vals["loans_public"] + vals["bcra_sec"], 2)
        return vals

    for m in months:
        t = comp.loc[m]
        t = t[t["assets"] > 0]
        tot = t["assets"].sum()
        top = t.sort_values("assets", ascending=False).head(10)
        rows = []
        for rank, (ent, r) in enumerate(top.iterrows(), 1):
            grp = "public" if ent in pub else "private" if ent in priv else None
            rows.append({"rank": rank, "ent": ent, "name": arc["names"].get(ent, ent), "short": arc["short"].get(ent),
                         "group": grp if m == latest else None,
                         "assets_share": round(float(r["assets"] / tot * 100), 2),
                         **shares(top.loc[[ent]])})
        bench = [{"name": "Ten largest", **shares(top)}, {"name": "Financial system", **shares(t)}]
        if m == latest:
            for label, ids in (("Public banks", pub), ("Private banks", priv)):
                sub = t[t.index.isin(ids)]
                if len(sub):
                    bench.append({"name": label, **shares(sub)})
        stamp = f"{m[:4]}-{m[4:]}-01"
        out["dates"].append(stamp)
        out["tables"][stamp] = {"banks": rows, "benchmarks": bench, "n": int(len(t)), "assets_ars_thousands": float(tot)}
    out["latest"] = f"{latest[:4]}-{latest[4:]}-01"
    return out


def fetch(ids, content: bytes | None = None) -> FetchResult:
    global LAST
    try:
        if content is None:
            content, _ = download()
        LAST = load_archive(content)
        data = concentration(LAST)
    except Exception as exc:
        return FetchResult(data=pd.DataFrame(columns=["date", "series_id", "value"]),
                           errors={sid: f"entity archive: {exc}" for sid in ids})
    have = set(data.series_id)
    errors = {sid: "not computed from the entity archive" for sid in ids if sid not in have}
    data = data[data.series_id.isin(set(ids))]
    meta = {sid: {"units": None, "time_index_end": g.date.max().strftime("%Y-%m-%d")} for sid, g in data.groupby("series_id")}
    return FetchResult(data=data.reset_index(drop=True), meta=meta, errors=errors)


def write_tables(directory) -> list[str]:
    import json
    from pathlib import Path
    if LAST is None:
        return []
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    text = json.dumps(top10_tables(LAST), separators=(",", ":"), ensure_ascii=False)
    p = d / "top10.json"
    if p.exists() and p.read_text(encoding="utf-8") == text:
        return []
    p.write_text(text, encoding="utf-8")
    return ["top10.json"]
