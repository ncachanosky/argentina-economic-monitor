"""Adapter for the Latin American Left-Leaning Populism Index (LALLPI), V2025.1.

Cachanosky, Bastos and Padilla (2026), Harvard Dataverse, DOI 10.7910/DVN/KEJ9VF; documentation
at https://lallpi.netlify.app. 22 countries, 2000 on. The index multiplies populist rhetoric
(POP_R, 0-1: V-Party's populism score of the head of government's party) by populist policy:
institutional (IP, 0-100: V-Dem and Heritage components) and economic (EP, 0-100: Fraser and
Heritage components). PIP = POP_R x IP, PEP = POP_R x EP, POP = (PIP + PEP) / 2.

The data are kept by hand: data/manual/lallpi/lallpi_v2025_1.csv is the project's own file, and
data/manual/lallpi/leaders.csv the head of government each country-year, by the index's rule
(whoever served the longer part of the year), from public records, not part of the dataset.
Rhetoric has no 2020 values, so the card ends in 2019 (END). Each run writes the table
lallpi/index.json (plus the map geometry) and returns Argentina's overall index as the card's
status series, "lallpi:ARG:pop".
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd

from ..registry import ROOT
from . import FetchResult

DIR = ROOT / "data" / "manual" / "lallpi"
DATA_FILE = DIR / "lallpi_v2025_1.csv"
LEADERS = DIR / "leaders.csv"
MAP_FILE = ROOT / "registry" / "geo" / "latam_50m.json"
VERSION = "V2025.1"
END = 2019
MEASURES = [("pop", "POP", "Populism index"), ("pip", "PIP", "Institutional populism (rhetoric × policy)"),
            ("pep", "PEP", "Economic populism (rhetoric × policy)"), ("r", "POP_R", "Populist rhetoric"),
            ("ip", "IP", "Institutional populist policy"), ("ep", "EP", "Economic populist policy")]
CITATION = ("Cachanosky, Nicolas, João Pedro Bastos, and Alexandre Padilla. 2026. Latin American Left-Leaning "
            "Populism Index, V2025.1. Harvard Dataverse. https://doi.org/10.7910/DVN/KEJ9VF")

LAST: dict | None = None


def _r(v, nd):
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else round(float(v), nd)


def load() -> dict:
    d = pd.read_csv(DATA_FILE)
    d = d[d["YEAR"] <= END]
    years = list(range(int(d["YEAR"].min()), END + 1))
    lead = pd.read_csv(LEADERS, keep_default_na=False)
    who = {}
    for r in lead.itertuples():
        for y in range(int(r._2), int(r.to) + 1):
            who[(r.iso3, y)] = (r.leader, r.role, r.note)
    countries = {}
    for iso, g in d.groupby("ISO3"):
        g = g.set_index("YEAR").reindex(years)
        name = g["COUNTRY"].dropna().iloc[0]
        c = {"n": name, "a2": g["ISO2"].dropna().iloc[0], "region": g["REGION"].dropna().iloc[0]}
        for key, col, _ in MEASURES:
            c[key] = [_r(v, 4 if key == "r" else 2) for v in g[col].values]
        c["party"] = [p if isinstance(p, str) else None for p in g["PARTY_CODE"].values]
        c["party_name"] = [p if isinstance(p, str) else None for p in g["PARTY_NAME"].values]
        c["leader"] = [who.get((iso, y), (None, None, None))[0] for y in years]
        c["role"] = [who.get((iso, y), (None, None, None))[1] for y in years]
        c["leader_note"] = [who.get((iso, y), (None, None, None))[2] or None for y in years]
        countries[iso] = c
    median = {key: [_r(np.nanmedian([c[key][i] for c in countries.values() if c[key][i] is not None]) if any(c[key][i] is not None for c in countries.values()) else None, 4)
                    for i in range(len(years))] for key, _, _ in MEASURES}
    return {"version": VERSION, "citation": CITATION, "doi": "10.7910/DVN/KEJ9VF", "url": "https://lallpi.netlify.app",
            "years": years, "measures": [{"key": k, "label": lab} for k, _, lab in MEASURES],
            "countries": countries, "median": median, "latest": f"{END}-01-01"}


def fetch(ids) -> FetchResult:
    global LAST
    empty = pd.DataFrame(columns=["date", "series_id", "value"])
    try:
        LAST = load()
    except Exception as exc:
        return FetchResult(data=empty, errors={sid: f"lallpi: {exc}" for sid in ids})
    frames, meta, errors = [], {}, {}
    for sid in ids:
        _, iso, key = (sid.split(":") + ["", ""])[:3]
        c = LAST["countries"].get(iso)
        if c is None or key not in c:
            errors[sid] = f"unknown series {sid!r}"
            continue
        s = pd.Series(c[key], index=LAST["years"], dtype=float).dropna()
        frames.append(pd.DataFrame({"date": pd.to_datetime([f"{y}-01-01" for y in s.index]), "series_id": sid, "value": s.values}))
        meta[sid] = {"units": "index, 0-100" if key != "r" else "index, 0-1"}
    return FetchResult(data=pd.concat(frames, ignore_index=True) if frames else empty, meta=meta, errors=errors)


def write_tables(directory) -> list[str]:
    if LAST is None:
        return []
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    changed = []
    p = d / "index.json"
    text = json.dumps(LAST, ensure_ascii=False, separators=(",", ":"))
    if not p.exists() or p.read_text(encoding="utf-8") != text:
        p.write_text(text, encoding="utf-8")
        changed.append("index.json")
    m = d / "latam_map.json"
    if not m.exists() or m.read_bytes() != MAP_FILE.read_bytes():
        shutil.copyfile(MAP_FILE, m)
        changed.append("latam_map.json")
    return changed
