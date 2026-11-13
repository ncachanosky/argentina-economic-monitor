"""Adapter for the Archbridge Institute's Social Mobility Index ("Social Mobility Around the World").

One cross-section per edition (the 2026 edition covers 132 countries, data year 2024): an
overall score from 0 to 10, three pillars, six sub-pillars and 25 components, with ranks and
quartiles. The index scores the conditions for mobility (markets, institutions, education and
civil society), not mobility outcomes.

The data are kept by hand: data/manual/social_mobility_<edition>.csv is Archbridge's own
"Download data" file, and data/manual/social_mobility_iso.csv maps its country names to ISO
codes. Each run writes the table smi/index.json for the card (plus the map geometry,
registry/geo/latam_50m.json) and returns Argentina's overall score and rank as annual series,
"smi:ARG:overall" and "smi:ARG:rank" (the card's status series). It also reads Archbridge's page
and warns when a newer edition is out; the file is never replaced automatically.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pandas as pd
import requests

from ..registry import ROOT
from . import FetchResult

MANUAL = ROOT / "data" / "manual"
ISO_FILE = MANUAL / "social_mobility_iso.csv"
MAP_FILE = ROOT / "registry" / "geo" / "latam_50m.json"
PAGE = "https://www.archbridgeinstitute.org/social-mobility-index/"
USER_AGENT = "argentina-economic-monitor (+https://github.com/ncachanosky/argentina-economic-monitor)"
LATAM = "Latin America & Caribbean"

# key, label in Archbridge's file, parent pillar (None for the overall index and the pillars)
MEASURES = [
    ("overall", "Overall", None),
    ("eg", "Entrepreneurship and Growth", None),
    ("eg_rt", "Regulation and Taxes", "eg"),
    ("eg_ca", "Costs, Affordability, and Infrastructure", "eg"),
    ("rl", "Institutions and Rule of Law", None),
    ("rl_sp", "State Predatory Action", "rl"),
    ("rl_lp", "Legal System Quality and Property Rights", "rl"),
    ("ecs", "Education and Civil Society", None),
    ("ecs_ed", "Education", "ecs"),
    ("ecs_cs", "Civil Society and Charity", "ecs"),
]
QUARTILES = {"Top Quartile": 1, "Second Quartile": 2, "Third Quartile": 3, "Bottom Quartile": 4}

LAST: dict | None = None


def latest_file(directory: Path = MANUAL) -> tuple[int, Path]:
    files = sorted((int(m.group(1)), p) for p in directory.glob("social_mobility_*.csv")
                   if (m := re.fullmatch(r"social_mobility_(\d{4})\.csv", p.name)))
    if not files:
        raise FileNotFoundError("no data/manual/social_mobility_<edition>.csv")
    return files[-1]


def _num(v):
    return None if pd.isna(v) else round(float(v), 4)


def load(path: Path | None = None, iso_path: Path = ISO_FILE) -> dict:
    """The edition as the card's table: measures, component labels and one record per country."""
    edition, path = (int(re.search(r"(\d{4})", path.name).group(1)), path) if path else latest_file()
    df = pd.read_csv(path, encoding="utf-8-sig")
    codes = pd.read_csv(iso_path, keep_default_na=False)
    iso = dict(zip(codes["country"], codes["iso3"]))
    iso2 = dict(zip(codes["country"], codes["iso2"]))
    missing = [c for c in df["Country"] if c not in iso]
    if missing:
        raise ValueError(f"social_mobility_iso.csv: no ISO code for {missing}")
    comps = {k: [c.split(": ", 1)[1] for c in df.columns if c.startswith(label + ": ")] for k, label, parent in MEASURES if parent}
    countries = []
    for _, r in df.iterrows():
        rec = {"iso": iso[r["Country"]], "iso2": iso2.get(r["Country"]) or None, "name": r["Country"], "region": r["Region"],
               "region_rank": int(r["Regional Rank"]), "year": int(r["Data Year"]), "score": {}, "rank": {}, "q": {}, "comp": {}}
        for k, label, parent in MEASURES:
            col = "Overall" if k == "overall" else label
            rec["score"][k] = _num(r[f"{col} Score"])
            rec["rank"][k] = int(r[f"{col} Rank"])
            qcol = "Quartile" if k == "overall" else f"{label} Quartile"
            if qcol in df.columns:
                rec["q"][k] = QUARTILES.get(r[qcol])
            if parent:
                rec["comp"][k] = [_num(r[f"{label}: {c}"]) for c in comps[k]]
        countries.append(rec)
    if len({c["iso"] for c in countries}) != len(countries):
        raise ValueError("social mobility file: duplicate countries")
    years = sorted({c["year"] for c in countries})
    return {
        "edition": edition, "data_year": years[-1], "count": len(countries),
        "measures": [{"key": k, "label": label, "parent": parent} for k, label, parent in MEASURES],
        "components": comps, "regions": sorted({c["region"] for c in countries}), "latam": LATAM,
        "countries": sorted(countries, key=lambda c: c["rank"]["overall"]),
        "latest": f"{years[-1]}-01-01", "source_file": f"data/manual/{path.name}",
    }


def page_edition(text: str) -> int | None:
    """The edition the Archbridge page shows: its embedded data ("edition":2026) or its heading."""
    m = re.search(r'"edition"\s*:\s*(\d{4})', text) or re.search(r"(\d{4})\s+RANKINGS", text, re.I) \
        or re.search(r"(\d{4})\s+Edition", text)
    return int(m.group(1)) if m else None


def fetch(ids, path: Path | None = None, check: bool = True) -> FetchResult:
    global LAST
    empty = pd.DataFrame(columns=["date", "series_id", "value"])
    try:
        t = load(path)
    except Exception as exc:
        return FetchResult(data=empty, errors={sid: str(exc) for sid in ids})
    LAST = t
    by_iso = {c["iso"]: c for c in t["countries"]}
    frames, meta, errors, warnings = [], {}, {}, {}
    newer = None
    if check:
        try:
            r = requests.get(PAGE, timeout=60, headers={"User-Agent": USER_AGENT})
            r.raise_for_status()
            ed = page_edition(r.text)
            if ed and ed > t["edition"]:
                newer = (f"Archbridge's page shows the {ed} edition of the Social Mobility Index; the Monitor has the "
                         f"{t['edition']} edition: add data/manual/social_mobility_{ed}.csv")
        except Exception as exc:
            print(f"smi: could not check the edition: {exc}")
    for sid in ids:
        _, code, what = (sid.split(":") + ["", ""])[:3]
        c = by_iso.get(code)
        if c is None or what not in ("overall", "rank"):
            errors[sid] = f"unknown series {sid!r}"
            continue
        value = c["score"]["overall"] if what == "overall" else c["rank"]["overall"]
        frames.append(pd.DataFrame({"date": [pd.Timestamp(f"{c['year']}-01-01")], "series_id": [sid], "value": [float(value)]}))
        meta[sid] = {"units": "score, 0-10" if what == "overall" else f"rank of {t['count']}",
                     "time_index_end": f"{c['year']}-01-01"}
        if newer:
            warnings[sid] = [newer]
    data = pd.concat(frames, ignore_index=True) if frames else empty
    return FetchResult(data=data, meta=meta, errors=errors, warnings=warnings)


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
    # Every country's overall score and pillars, for the International page (world_<key>.json).
    from .world import table, write
    year = max(c["year"] for c in LAST["countries"])
    for m_ in [m_ for m_ in LAST["measures"] if not m_.get("parent")]:
        wide = pd.DataFrame({c["iso"]: [c["score"].get(m_["key"])] for c in LAST["countries"]}, index=[year])
        changed += write(d, {f"world_{m_['key']}.json": table(f"smi_{m_['key']}", m_["label"] if m_["key"] != "overall" else "Social Mobility Index",
                                                                "score, 0-10", 2, "Archbridge Institute, Social Mobility Index", wide)})
    m = d / "latam_map.json"
    if not m.exists() or m.read_bytes() != MAP_FILE.read_bytes():
        shutil.copyfile(MAP_FILE, m)
        changed.append("latam_map.json")
    return changed
