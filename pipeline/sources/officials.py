"""Adapter for the tenure of BCRA presidents and ministers of the economy.

The list is kept by hand in data/manual/officials.csv (columns role, name,
start, end, note, source; `end` empty for the official in office). Each run:

- writes the table officials/tenure.json for the tenure cards (days in office
  are computed on the site, so the incumbent's count is always current);
- returns one annual series per role, "officials:<role>": the number of
  officials who took office in each calendar year (the cards' status series);
- checks that the incumbent in the file is still in office, against the BCRA's
  board page (which lists "<name> | Presidente") and the Ministry of Economy's
  home page (which names the minister in its news). A mismatch, or a page that
  no longer names the incumbent, is reported as a warning on the series: the
  file is never changed automatically. When an official changes, close the old
  row (`end`) and add the new one.
"""
from __future__ import annotations

import html
import json
import re
import unicodedata
from pathlib import Path

import pandas as pd
import requests

from ..registry import ROOT
from . import FetchResult

FILE = ROOT / "data" / "manual" / "officials.csv"
ROLES = {"bcra": "President of the BCRA", "economia": "Minister of the Economy"}
CHECK_PAGES = {
    "bcra": "https://www.bcra.gob.ar/directorio/",
    "economia": "https://www.argentina.gob.ar/economia",
}
TIMEOUT = 60
USER_AGENT = "argentina-economic-monitor (+https://github.com/ncachanosky/argentina-economic-monitor)"

LAST: pd.DataFrame | None = None


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z ]+", " ", s)


def surname(name: str) -> str:
    """Last word of the name, e.g. "Santiago Bausili" -> "bausili"."""
    return _norm(name).split()[-1]


def load(path: Path | None = None) -> pd.DataFrame:
    df = pd.read_csv(path or FILE, dtype=str, keep_default_na=False)
    missing = {"role", "name", "start", "end"} - set(df.columns)
    if missing:
        raise ValueError(f"officials.csv: missing columns {sorted(missing)}")
    df["start"] = pd.to_datetime(df["start"], errors="raise")
    df["end"] = pd.to_datetime(df["end"].replace("", None), errors="raise")
    bad = sorted(set(df["role"]) - set(ROLES))
    if bad:
        raise ValueError(f"officials.csv: unknown role(s) {bad}")
    for role, g in df.groupby("role"):
        g = g.sort_values("start")
        if g["end"].isna().sum() > 1:
            raise ValueError(f"officials.csv: more than one open-ended {role} row")
        if (g["end"].dropna() < g.loc[g["end"].notna(), "start"]).any():
            raise ValueError(f"officials.csv: a {role} row ends before it starts")
        nxt = g["start"].shift(-1)
        over = g[(g["end"].notna()) & (nxt.notna()) & (g["end"] > nxt)]
        if not over.empty:
            raise ValueError(f"officials.csv: overlapping {role} tenures at {over['name'].iloc[0]}")
    return df.sort_values(["role", "start"]).reset_index(drop=True)


def appointments(df: pd.DataFrame, role: str, today: pd.Timestamp) -> pd.Series:
    """Officials taking office per calendar year, zeros included, through the current year."""
    g = df[df["role"] == role]
    years = range(g["start"].dt.year.min(), today.year + 1)
    counts = g["start"].dt.year.value_counts()
    return pd.Series([float(counts.get(y, 0)) for y in years], index=pd.to_datetime([f"{y}-01-01" for y in years]))


def page_text(url: str) -> str:
    r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    return html.unescape(re.sub(r"<[^>]+>", " ", r.text))


def check_incumbent(role: str, name: str, text: str) -> str | None:
    """A warning if the page does not match the incumbent in the file, else None."""
    sn = surname(name)
    if role == "bcra":
        m = re.search(r"([^|>\n]{3,80}?)\s*\|\s*Presidente\b", text)
        if not m:
            return f"BCRA board page no longer lists a 'Presidente'; check that {name} is still in office"
        if sn not in _norm(m.group(1)):
            return f"BCRA board page lists '{m.group(1).strip()}' as Presidente, not {name}: update data/manual/officials.csv"
        return None
    if sn not in _norm(text):
        return f"Ministry of Economy page does not mention {name}: check whether the minister changed (data/manual/officials.csv)"
    return None


def table(df: pd.DataFrame, today: pd.Timestamp) -> dict:
    out = {"as_of": today.strftime("%Y-%m-%d"), "roles": {}}
    for role, label in ROLES.items():
        g = df[df["role"] == role]
        out["roles"][role] = {"label": label, "rows": [
            {"name": r["name"], "start": r["start"].strftime("%Y-%m-%d"),
             "end": None if pd.isna(r["end"]) else r["end"].strftime("%Y-%m-%d"),
             "note": r.get("note", "") or None}
            for _, r in g.iterrows()]}
    out["latest"] = max(df["start"].max(), df["end"].max()).strftime("%Y-%m-%d")
    return out


def fetch(ids, path: Path | None = None, today: pd.Timestamp | None = None, check: bool = True) -> FetchResult:
    global LAST
    today = today or pd.Timestamp.today().normalize()
    try:
        df = load(path)
    except Exception as exc:
        return FetchResult(data=pd.DataFrame(columns=["date", "series_id", "value"]), errors={sid: str(exc) for sid in ids})
    LAST = df
    frames, meta, errors, warnings = [], {}, {}, {}
    for sid in ids:
        role = sid.split(":", 1)[1]
        if role not in ROLES:
            errors[sid] = f"unknown role {role!r}"
            continue
        s = appointments(df, role, today)
        frames.append(pd.DataFrame({"date": s.index, "series_id": sid, "value": s.values}))
        meta[sid] = {"units": "officials taking office per year", "time_index_end": s.index.max().strftime("%Y-%m-%d")}
        open_rows = df[(df["role"] == role) & df["end"].isna()]
        if check and not open_rows.empty:
            name = open_rows["name"].iloc[0]
            try:
                w = check_incumbent(role, name, page_text(CHECK_PAGES[role]))
            except Exception as exc:
                w = None
                print(f"officials: could not check {role} page: {exc}")
            if w:
                warnings[sid] = [w]
    data = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["date", "series_id", "value"])
    return FetchResult(data=data, meta=meta, errors=errors, warnings=warnings)


def write_tables(directory) -> list[str]:
    if LAST is None:
        return []
    t = table(LAST, pd.Timestamp.today().normalize())
    t.pop("as_of")          # keep the file stable between runs; the site counts days itself
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    p = d / "tenure.json"
    text = json.dumps(t, ensure_ascii=False, separators=(",", ":"))
    if not p.exists() or p.read_text(encoding="utf-8") != text:
        p.write_text(text, encoding="utf-8")
        return ["tenure.json"]
    return []
