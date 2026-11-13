"""Adapter for the Maddison Project Database: GDP per capita since 1820, by release.

Reads the compact files in data/manual/maddison/ (built once by pipeline/maddison.py from
the 2013, 2018, 2020 and 2023 releases) and writes one table per release for the long-run
card, data/tables/maddison/mpd<release>.json:

  countries   GDP per capita (and population) by year from 1820, with name, region and ISO codes
  regions     population-weighted averages of the release's own country figures, only for years
              with every member present: Western Europe (Maddison's 12) and Latin America (the
              region's main economies, without Argentina)
  extension   (latest release only) Argentina after the release's last year: the release's last
              level carried forward with INDEC's real GDP growth (constant 2004 prices, annual
              totals of the quarterly original series) less population growth. Since 2012 the
              2023 release's Argentine growth equals INDEC's real GDP growth over its own
              population growth, so the extension follows the same rule; population is assumed
              to grow as in the release's last year.
  sources     the release's original sources by country (its citation policy asks for them
              when the data are shown in a chart)

Ids: "maddison:ARG" (Argentina, latest release, with the extension), the card's status series.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .. import store
from ..registry import ROOT
from . import FetchResult

DIR = ROOT / "data" / "manual" / "maddison"
GDP_ORIG = "4.2_OGP_2004_T_17"        # INDEC quarterly GDP, original series, constant 2004 prices
RELEASES = {
    "2023": {"unit": "2011 international dollars", "citation": "Maddison Project Database, version 2023. Bolt, Jutta and Jan Luiten van Zanden (2024), \"Maddison style estimates of the evolution of the world economy: A new 2023 update\", Journal of Economic Surveys, 1-41. DOI: 10.1111/joes.12618. CC BY 4.0."},
    "2020": {"unit": "2011 international dollars", "citation": "Maddison Project Database, version 2020. Bolt, Jutta and Jan Luiten van Zanden (2020), \"Maddison style estimates of the evolution of the world economy. A new 2020 update\". CC BY 4.0."},
    "2018": {"unit": "2011 international dollars (cgdppc, for cross-country comparisons)", "citation": "Maddison Project Database, version 2018. Bolt, Jutta, Robert Inklaar, Herman de Jong and Jan Luiten van Zanden (2018), \"Rebasing 'Maddison': new income comparisons and the shape of long-run economic development\", Maddison Project Working Paper 10. CC BY 4.0. Original country sources: Appendix A of that paper."},
    "2013": {"unit": "1990 international Geary-Khamis dollars", "citation": "Maddison Project Database, version 2013. Bolt, J. and J. L. van Zanden (2014). The Maddison Project: collaborative research on historical national accounts. The Economic History Review, 67(3): 627-651. Population weights from the 2018 release. Original country sources: appendix of Bolt and van Zanden (2014)."},
}
LATEST = max(RELEASES)
REGIONS = {
    "we12": {"label": "Western Europe (12)", "members": ["AUT", "BEL", "DNK", "FIN", "FRA", "DEU", "ITA", "NLD", "NOR", "SWE", "CHE", "GBR"]},
    "latam": {"label": "Latin America (main economies, without Argentina)", "members": ["BRA", "CHL", "COL", "MEX", "PER", "URY", "VEN"]},
}

LAST: dict = {}


def _r(v, nd=0):
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else (round(float(v), nd) if nd else int(round(float(v))))


def extension(d: pd.DataFrame) -> dict | None:
    """Argentina after the release's last year, from INDEC's real GDP growth less population growth."""
    a = d[d["iso3"] == "ARG"].set_index("year").sort_index()
    last = int(a.index.max())
    q = store.as_of(GDP_ORIG).dropna()
    if q.empty:
        return None
    n = q.groupby(q.index.year).count()
    ann = q.groupby(q.index.year).sum()[n == 4]
    pop_g = float(a["pop"].iloc[-1] / a["pop"].iloc[-2]) if a["pop"].notna().iloc[-2:].all() else 1.0
    level, years, vals = float(a["gdppc"].iloc[-1]), [], []
    for y in range(last + 1, int(ann.index.max()) + 1):
        if y not in ann.index or y - 1 not in ann.index:
            break
        level *= float(ann[y] / ann[y - 1]) / pop_g
        years.append(y)
        vals.append(round(level, 0))
    if not years:
        return None
    return {"from": last, "years": years, "values": vals, "pop_growth": round(100 * (pop_g - 1), 2),
            "gdp_growth": [round(100 * float(ann[y] / ann[y - 1] - 1), 2) for y in years]}


def table(rel: str) -> dict:
    d = pd.read_csv(DIR / f"mpd{rel}.csv")
    meta = pd.read_csv(DIR / "countries.csv", keep_default_na=False).set_index("iso3")
    first, last = int(d["year"].min()), int(d["year"].max())
    years = list(range(first, last + 1))
    g = d.pivot_table(index="year", columns="iso3", values="gdppc").reindex(years)
    p = d.pivot_table(index="year", columns="iso3", values="pop").reindex(years)
    countries = {}
    for iso in g.columns:
        m = meta.loc[iso] if iso in meta.index else None
        countries[iso] = {"n": (m["country"] if m is not None else iso), "r": (m["region"] if m is not None else ""),
                          "a2": (m["iso2"] if m is not None and m["iso2"] else None),
                          "g": [_r(v) for v in g[iso].values], "p": [_r(v) for v in (p[iso].values if iso in p else [None] * len(years))]}
    regions = {}
    for key, R in REGIONS.items():
        mem = [c for c in R["members"] if c in g.columns]
        gg, pp = g[mem], p.reindex(columns=mem)
        ok = gg.notna().all(axis=1) & pp.notna().all(axis=1) & (len(mem) == len(R["members"]))
        avg = (gg * pp).sum(axis=1) / pp.sum(axis=1)
        regions[key] = {"label": R["label"], "members": R["members"], "g": [_r(v) if ok[y] else None for y, v in avg.items()]}
    src_path = DIR / f"mpd{rel}_sources.json"
    return {"release": rel, "unit": RELEASES[rel]["unit"], "citation": RELEASES[rel]["citation"], "first": first, "last": last,
            "years": years, "countries": countries, "regions": regions,
            "extension": extension(d) if rel == LATEST else None,
            "sources": json.loads(src_path.read_text(encoding="utf-8")) if src_path.exists() else None,
            "latest": f"{last}-01-01"}


def fetch(ids) -> FetchResult:
    global LAST
    empty = pd.DataFrame(columns=["date", "series_id", "value"])
    try:
        LAST = {rel: table(rel) for rel in RELEASES}
    except Exception as exc:
        return FetchResult(data=empty, errors={sid: f"maddison: {exc}" for sid in ids})
    t = LAST[LATEST]
    a = t["countries"]["ARG"]
    s = pd.Series(a["g"], index=t["years"], dtype=float).dropna()
    ext = t["extension"]
    if ext:
        s = pd.concat([s, pd.Series(ext["values"], index=ext["years"], dtype=float)])
        t["latest"] = f"{ext['years'][-1]}-01-01"
    frames = [pd.DataFrame({"date": pd.to_datetime([f"{y}-01-01" for y in s.index]), "series_id": sid, "value": s.values})
              for sid in ids]
    return FetchResult(data=pd.concat(frames, ignore_index=True) if frames else empty,
                       meta={sid: {"units": RELEASES[LATEST]["unit"]} for sid in ids})


def write_tables(directory) -> list[str]:
    if not LAST:
        return []
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    changed = []
    index = {"releases": sorted(LAST, reverse=True), "latest": LAST[LATEST]["latest"],
             "units": {r: RELEASES[r]["unit"] for r in LAST}}
    for name, obj in [*((f"mpd{r}.json", t) for r, t in LAST.items()), ("index.json", index)]:
        p = d / name
        text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        if not p.exists() or p.read_text(encoding="utf-8") != text:
            p.write_text(text, encoding="utf-8")
            changed.append(name)
    return changed
