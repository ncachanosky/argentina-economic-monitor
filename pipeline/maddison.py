"""Maddison Project Database releases -> compact files in data/manual/maddison/ (one-time load).

    python -m pipeline.maddison <folder with mpd2013.xlsx, mpd2018.xlsx, mpd2020.xlsx, mpd2023.xlsx>

For each release writes mpd<release>.csv (iso3, year, gdppc, pop; years from 1820) and
mpd<release>_sources.json (the original sources by country, from the release's Sources sheet,
which its citation policy asks to cite when the data are shown in a chart).

Releases and terms (checked November 2026):
  2023, 2020, 2018  Creative Commons Attribution 4.0 (rug.nl release pages)
  2013              "When using these data (for whatever purpose), please make the following
                    reference: Maddison Project Database, version 2013. Bolt, J. and J. L. van
                    Zanden (2014)." No population: the 2018 release's population is used for
                    weights.
  2010              (Maddison's original database) no terms stated: not included.

Measures: 2020 and 2023 "gdppc" (2011 international dollars); 2018 "cgdppc" (2011 dollars,
the series meant for cross-country comparisons); 2013 GDP per capita in 1990 Geary-Khamis
dollars.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pandas as pd

from .registry import ROOT

OUT = ROOT / "data" / "manual" / "maddison"
FIRST_YEAR = 1820

# 2013 column names that differ from the 2018 country names (aggregates are dropped).
NAMES_2013 = {
    "(Centre-   North)           Italy": "ITA", "Holland/     Netherlands": "NLD", "England/GB/UK": "GBR", "N. Zealand": "NZL",
    "USA": "USA", "Czecho-slovakia": None, "Yugoslavia": None, "F. Yugoslavia": None, "Bosnia": "BIH", "Macedonia": "MKD",
    "Kosovo": None, "Cape Colony/ South Africa": "ZAF", "Congo 'Brazzaville'": "COG", "Congo-Kinshasa": "COD",
    "Centr. Afr. Rep.": "CAF", "Côte d'Ivoire": "CIV", "Eritrea & Ethiopia": None, "Guinea Bissau": "GNB",
    "Sao Tomé & Principe": "STP", "Swaziland": "SWZ", "Comoro Islands": "COM", "W. Bank & Gaza": "PSE",
    "Cape Verde": "CPV", "Gambia": "GMB", "F. USSR": None, "Hong Kong": "HKG", "S. Korea": "KOR", "N. Korea": "PRK",
    "Burma": "MMR", "Iran": "IRN", "Syria": "SYR", "Laos": "LAO", "Vietnam": "VNM", "Taiwan": "TWN", "Russia": "RUS",
    "Moldova": "MDA", "Czech Rep.": "CZE", "Slovakia": "SVK", "Dominican Rep.": "DOM", "Trinidad Tobago": "TTO",
    "Puerto Rico": "PRI", "Jamaica": "JAM", "Bolivia": "BOL", "Venezuela": "VEN", "Tanzania": "TZA", "Libya": "LBY",
    "Kyrgyzstan": "KGZ", "Turkmenistan": "TKM", "Uzbekistan": "UZB", "Kazakhstan": "KAZ", "Tajikistan": "TJK",
    "Belarus": "BLR", "Ukraine": "UKR", "Georgia": "GEO", "Armenia": "ARM", "Azerbaijan": "AZE", "Lithuania": "LTU",
    "Latvia": "LVA", "Estonia": "EST", "UAE": "ARE", "Saudi Arabia": "SAU", "Montenegro": "MNE", "Serbia": "SRB",
    "F. Czecho-slovakia": None, "Turk-menistan": "TKM", "Haïti": "HTI", "T. & Tobago": "TTO", "North Korea": "PRK",
    "Somalia": "SOM", "Sudan": "SDN",
}


def _long(df: pd.DataFrame, value: str) -> pd.DataFrame:
    return df.melt(id_vars="year", var_name="iso3", value_name=value).dropna()


def read_2013(path: Path, names: dict[str, str]) -> pd.DataFrame:
    d = pd.read_excel(path, header=None)
    cols = [str(v).strip() for v in d.iloc[2].values]
    rows = d.iloc[3:].copy()
    rows = rows[pd.to_numeric(rows[0], errors="coerce").notna()]
    out, unmatched = {}, []
    for j, name in enumerate(cols):
        if j == 0 or name in ("nan", ""):
            continue
        iso = NAMES_2013.get(name, names.get(name))
        if iso is None:
            if name not in NAMES_2013 and not re.search(r"\d|Total|Asia$|W\. Offshoots|Africa|America|Europe", name):
                unmatched.append(name)
            continue
        out[iso] = pd.to_numeric(rows[j], errors="coerce").values
    if unmatched:
        print("2013: no ISO code for", unmatched)
    df = pd.DataFrame(out)
    df.insert(0, "year", pd.to_numeric(rows[0]).astype(int).values)
    return _long(df, "gdppc")


def read_full(path: Path, measure: str) -> pd.DataFrame:
    d = pd.read_excel(path, "Full data")
    return d.rename(columns={"countrycode": "iso3", measure: "gdppc"})[["iso3", "year", "gdppc", "pop"]]


def sources(path: Path) -> dict[str, list[list[str]]]:
    """{iso3: [[period, source], ...]} from a release's Sources sheet."""
    try:
        x = pd.read_excel(path, "Sources", header=None)
    except ValueError:
        return {}
    out, iso = {}, None
    for _, r in x.iterrows():
        a, b, c = (str(v).strip() if pd.notna(v) else "" for v in r.values[:3])
        if re.fullmatch(r"[A-Z]{3}", a):
            iso = a
            out.setdefault(iso, [])
        elif iso and c:
            out[iso].append([b, c])
    return out


def main(folder: str) -> None:
    f = Path(folder)
    OUT.mkdir(parents=True, exist_ok=True)
    full18 = read_full(f / "mpd2018.xlsx", "cgdppc")
    names18 = dict(pd.read_excel(f / "mpd2018.xlsx", "Full data")[["country", "countrycode"]].drop_duplicates().values)
    countries = pd.read_excel(f / "mpd2023.xlsx", "Full data")[["countrycode", "country", "region"]].drop_duplicates()
    releases = {
        "2023": read_full(f / "mpd2023.xlsx", "gdppc"),
        "2020": read_full(f / "mpd2020.xlsx", "gdppc"),
        "2018": full18,
        "2013": read_2013(f / "mpd2013.xlsx", names18).merge(full18[["iso3", "year", "pop"]], on=["iso3", "year"], how="left"),
    }
    for rel, d in releases.items():
        d = d[(d["year"] >= FIRST_YEAR) & d["gdppc"].notna()].sort_values(["iso3", "year"])
        d.to_csv(OUT / f"mpd{rel}.csv", index=False, float_format="%.6g")
        print(rel, d["iso3"].nunique(), "countries", int(d["year"].min()), "-", int(d["year"].max()), len(d), "rows")
        src = sources(f / f"mpd{rel}.xlsx")
        if src:
            (OUT / f"mpd{rel}_sources.json").write_text(json.dumps(src, ensure_ascii=False, indent=0), encoding="utf-8")
    countries.rename(columns={"countrycode": "iso3"}).to_csv(OUT / "countries.csv", index=False)


if __name__ == "__main__":
    main(sys.argv[1])
