"""International comparisons: the IMF's World Economic Outlook and the World Bank's World Development Indicators.

Every country both cover (about 190), annual:
  IMF WEO (SDMX API, one request for all indicators): real GDP growth, GDP per capita (PPP),
      inflation, investment, general government revenue, expenditure, net lending/borrowing and
      gross debt, the current account and unemployment, with the IMF's projections. Years from
      the edition's own year on are estimates or projections (the WEO publishes no per-country
      "last actual year" through the API); the edition is April or October, read from the
      countries' update dates.
  World Bank WDI (API): trade openness, FDI net inflows, the Gini index.

Writes one file per indicator, data/tables/intl/<key>.json ({years, values: {iso: [...]},
proj_from, ...}), and index.json with the countries, the groups (registry/geo/groups.yaml,
filtered to the countries covered) and each group's largest economies (GDP at PPP), drawn by
default. The cards' status series: "intl:ARG:<key>".
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yaml

from ..registry import ROOT
from . import FetchResult

WEO_URL = ("https://api.imf.org/external/sdmx/2.1/data/IMF.RES,WEO/"
           ".NGDP_RPCH+PPPPC+PPPGDP+PCPIPCH+NID_NGDP+GGR_NGDP+GGX_NGDP+GGXCNL_NGDP+GGXWDG_NGDP+BCA_NGDPD+LUR.A")
WB = "https://api.worldbank.org/v2"
GROUPS = ROOT / "registry" / "geo" / "groups.yaml"
USER_AGENT = "argentina-economic-monitor (+https://github.com/ncachanosky/argentina-economic-monitor)"
FIRST = 1980

# key: (source, code, label, unit, decimals)
INDICATORS = {
    "growth": ("weo", "NGDP_RPCH", "Real GDP growth", "%", 1),
    "gdppc": ("weo", "PPPPC", "GDP per capita, PPP", "current international dollars", 0),
    "inflation": ("weo", "PCPIPCH", "Inflation, average consumer prices", "%", 1),
    "investment": ("weo", "NID_NGDP", "Total investment", "% of GDP", 1),
    "revenue": ("weo", "GGR_NGDP", "General government revenue", "% of GDP", 1),
    "expenditure": ("weo", "GGX_NGDP", "General government expenditure", "% of GDP", 1),
    "balance": ("weo", "GGXCNL_NGDP", "General government net lending (+) / borrowing (−)", "% of GDP", 1),
    "debt": ("weo", "GGXWDG_NGDP", "General government gross debt", "% of GDP", 1),
    "current_account": ("weo", "BCA_NGDPD", "Current account balance", "% of GDP", 1),
    "unemployment": ("weo", "LUR", "Unemployment rate", "% of labor force", 1),
    "openness": ("wb", "NE.TRD.GNFS.ZS", "Trade openness (exports plus imports)", "% of GDP", 1),
    "fdi": ("wb", "BX.KLT.DINV.WD.GD.ZS", "Foreign direct investment, net inflows", "% of GDP", 1),
    "gini": ("wb", "SI.POV.GINI", "Gini index", "0-100", 1),
}
EXTRA_NAMES = {"TWN": ("Taiwan", "TW")}   # in the WEO, not in the World Bank's list
SHORT_NAMES = {"VEN": "Venezuela", "EGY": "Egypt", "KOR": "South Korea", "IRN": "Iran", "YEM": "Yemen", "HKG": "Hong Kong",
               "MAC": "Macao", "RUS": "Russia", "TUR": "Türkiye", "COD": "DR Congo", "COG": "Republic of the Congo",
               "GMB": "The Gambia", "BHS": "The Bahamas", "FSM": "Micronesia", "LAO": "Laos", "KGZ": "Kyrgyzstan",
               "SVK": "Slovakia", "SYR": "Syria", "VNM": "Vietnam", "PSE": "West Bank and Gaza", "PRI": "Puerto Rico"}

LAST: dict | None = None


def _get(url: str, retries: int = 3, timeout: int = 180) -> requests.Response:
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
            r.raise_for_status()
            return r
        except Exception as exc:
            last = exc
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"{url[:80]}: {last}")


def parse_weo(xml: str) -> tuple[dict, str]:
    """({indicator code: {iso: {year: value}}}, edition label)."""
    out: dict = {}
    dates = []
    for attrs, body in re.findall(r"<Series ([^>]*)>(.*?)</Series>", xml, re.S):
        a = dict(re.findall(r'(\w+)="([^"]*)"', attrs))
        if a.get("COUNTRY_UPDATE_DATE"):
            dates.append(pd.to_datetime(a["COUNTRY_UPDATE_DATE"], format="%m/%d/%Y", errors="coerce"))
        obs = {int(y): float(v) for y, v in re.findall(r'TIME_PERIOD="(\d{4})" OBS_VALUE="([^"]+)"', body) if v not in ("", "NaN")}
        out.setdefault(a["INDICATOR"], {})[a["COUNTRY"]] = obs
    if "NGDP_RPCH" not in out or "ARG" not in out["NGDP_RPCH"]:
        raise ValueError("WEO: Argentina's GDP growth missing (layout changed?)")
    d = max(x for x in dates if pd.notna(x))
    edition = f"{'April' if d.month <= 6 else 'October'} {d.year}"
    return out, edition


def parse_wb(js) -> dict:
    """{iso: {year: value}} from a World Bank indicator response."""
    rows = js[1] if isinstance(js, list) and len(js) > 1 and js[1] else []
    out: dict = {}
    for r in rows:
        iso, v = r.get("countryiso3code"), r.get("value")
        if iso and v is not None:
            out.setdefault(iso, {})[int(r["date"])] = float(v)
    return out


def build(weo_xml: str, wb_countries, wb_series: dict) -> dict:
    weo, edition = parse_weo(weo_xml)
    proj_from = int(edition.split()[1])
    econ = {c["id"]: (c["name"], c["iso2Code"]) for c in wb_countries[1] if c["region"]["value"].strip() != "Aggregates"}
    econ.update(EXTRA_NAMES)
    covered = sorted(set(weo["NGDP_RPCH"]) & set(econ))
    last_year = max(max(s) for s in weo["NGDP_RPCH"].values() if s)
    years = list(range(FIRST, last_year + 1))
    tables = {}
    for key, (src, code, label, unit, nd) in INDICATORS.items():
        raw = weo.get(code, {}) if src == "weo" else wb_series.get(code, {})
        vals = {iso: [None if raw.get(iso, {}).get(y) is None else round(raw[iso][y], nd if nd else 0) for y in years] for iso in covered}
        vals = {iso: v for iso, v in vals.items() if any(x is not None for x in v)}
        tables[key] = {"key": key, "label": label, "unit": unit, "decimals": nd, "source": "IMF, World Economic Outlook" if src == "weo" else "World Bank, World Development Indicators",
                       "code": code, "years": years, "proj_from": proj_from if src == "weo" else None, "edition": edition if src == "weo" else None, "values": vals,
                       "latest": f"{(proj_from - 1) if src == 'weo' else max([y for k, y in enumerate(years) if any(v[k] is not None for v in vals.values())] or [years[-1]])}-01-01"}
    g = yaml.safe_load(GROUPS.read_text(encoding="utf-8"))
    gdp = weo.get("PPPGDP", {})
    size = {iso: gdp.get(iso, {}).get(proj_from - 1) or 0 for iso in covered}

    def grp(x):
        m = [c for c in x.get("members", []) if c in covered]
        return {"key": x["key"], "label": x["label"], "members": m, "main": sorted(m, key=lambda c: -size[c])[:5]}
    regions = []
    for r in g["regions"]:
        if r.get("sub"):
            subs = [grp(s) for s in r["sub"]]
            members = [c for s in subs for c in s["members"]]
            regions.append({**grp({"key": r["key"], "label": r["label"], "members": members}), "sub": subs})
        else:
            regions.append(grp(r))
    blocs = [grp(b) for b in g["blocs"]]
    world = {"key": "world", "label": "World (all countries)", "members": covered, "main": sorted(covered, key=lambda c: -size[c])[:5]}
    index = {"edition": edition, "proj_from": proj_from, "years": years,
             "countries": {iso: {"n": SHORT_NAMES.get(iso, econ[iso][0]), "a2": econ[iso][1]} for iso in covered},
             "groups": {"world": world, "regions": regions, "blocs": blocs},
             "indicators": [{"key": k, "label": t["label"], "unit": t["unit"], "source": t["source"], "decimals": t["decimals"]} for k, t in tables.items()],
             "latest": f"{proj_from - 1}-01-01"}
    return {"index": index, "tables": tables}


def fetch(ids) -> FetchResult:
    global LAST
    empty = pd.DataFrame(columns=["date", "series_id", "value"])
    try:
        weo_xml = _get(WEO_URL, timeout=300).text
        wb_countries = _get(f"{WB}/country?format=json&per_page=400").json()
        wb_series = {code: parse_wb(_get(f"{WB}/country/all/indicator/{code}?format=json&per_page=20000&date={FIRST}:2030").json())
                     for _, (src, code, *_r) in INDICATORS.items() if src == "wb"}
        LAST = build(weo_xml, wb_countries, wb_series)
    except Exception as exc:
        return FetchResult(data=empty, errors={sid: f"intl: {exc}" for sid in ids})
    frames, meta, errors = [], {}, {}
    proj_from = LAST["index"]["proj_from"]
    for sid in ids:
        _, iso, key = (sid.split(":") + ["", ""])[:3]
        t = LAST["tables"].get(key)
        if not t or iso not in t["values"]:
            errors[sid] = f"unknown series {sid!r}"
            continue
        s = pd.Series(t["values"][iso], index=t["years"], dtype=float)
        if t["proj_from"]:
            s = s[s.index < proj_from]       # status: published data only, not projections
        s = s.dropna()
        frames.append(pd.DataFrame({"date": pd.to_datetime([f"{y}-01-01" for y in s.index]), "series_id": sid, "value": s.values}))
        meta[sid] = {"units": t["unit"]}
    return FetchResult(data=pd.concat(frames, ignore_index=True) if frames else empty, meta=meta, errors=errors)


def write_tables(directory) -> list[str]:
    if LAST is None:
        return []
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    changed = []
    for name, obj in [("index.json", LAST["index"]), *((f"{k}.json", t) for k, t in LAST["tables"].items())]:
        p = d / name
        text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        if not p.exists() or p.read_text(encoding="utf-8") != text:
            p.write_text(text, encoding="utf-8")
            changed.append(name)
    return changed
