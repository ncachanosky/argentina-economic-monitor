"""Revision tracker: how GDP and EMAE growth rates change between their first publication and today.

Each release is a vintage of the whole series. Vintages come from:

  GDP    data/manual/gdp_vintages.csv: INDEC's national-accounts workbooks as published at each
         quarterly release, September 2016 to September 2026 (pipeline/gdp_vintages.py),
         then the copies the daily update stores (data/vintages/) for later releases;
  EMAE   data/manual/emae_trend_vintages.csv: Cuadro 2 of INDEC's 121 EMAE reports, August
         2016 to September 2026 (pipeline/emae_vintages.py), then the stored copies.

For each period: the growth rate in the first release that included it, in every later
release (its revision path) and in the latest. Growth rates are rounded to one decimal, as
INDEC publishes them, so a revision of 0.1 can be rounding. Statistics exclude March to
June 2020 (EMAE) and the second and third quarters of 2020 (GDP), whose swings dwarf every
other revision. Ids (the cards' status series):

  revisions:gdp_first_qq    GDP q/q (seasonally adjusted), first print, by quarter
  revisions:emae_first_mm   EMAE m/m (seasonally adjusted), first print, by month
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .. import store
from ..registry import ROOT
from . import FetchResult

GDP_CSV = ROOT / "data" / "manual" / "gdp_vintages.csv"
EMAE_CSV = ROOT / "data" / "manual" / "emae_trend_vintages.csv"
GDP_SA, GDP_ORIG = "3.2_OGP_D_2004_T_17", "4.2_OGP_2004_T_17"
EMAE_SA, EMAE_ORIG = "143.3_NO_PR_2004_A_31", "143.3_NO_PR_2004_A_21"
EXCLUDE = {"gdp": pd.to_datetime(["2020-04-01", "2020-07-01"]),
           "emae": pd.date_range("2020-03-01", "2020-06-01", freq="MS")}
PATH_FROM = pd.Timestamp("2016-01-01")

LAST: dict = {}


def _growth(level: pd.Series, lag: int) -> pd.Series:
    level = level.dropna().sort_index()
    return (100 * (level / level.shift(lag) - 1)).round(1)


def gdp_vintages() -> list[tuple[str, str | None, pd.DataFrame]]:
    """[(vintage label, release date, DataFrame[sa, orig] by quarter)] in release order."""
    d = pd.read_csv(GDP_CSV, parse_dates=["period"])
    out = [(v, (g["release"].dropna().iloc[0] if g["release"].notna().any() else None),
            g.set_index("period")[["gdp_sa", "gdp_orig"]].rename(columns={"gdp_sa": "sa", "gdp_orig": "orig"}))
           for v, g in d.groupby("vintage")]
    last = max(f.index.max() for _, _, f in out)
    for vin in store.vintages(GDP_SA):
        sa, orig = store.as_of(GDP_SA, vin).dropna(), store.as_of(GDP_ORIG, vin).dropna()
        if len(sa) and sa.index.max() > last:
            out.append((vin[:7], vin, pd.DataFrame({"sa": sa, "orig": orig})))
            last = sa.index.max()
    return sorted(out, key=lambda x: x[0])


def emae_vintages() -> list[tuple[str, str | None, pd.DataFrame]]:
    d = pd.read_csv(EMAE_CSV, parse_dates=["release", "period"])
    # The reports' own rates (published to one decimal): their index levels are rounded too coarsely to recompute them.
    out = [(r.strftime("%Y-%m-%d"), r.strftime("%Y-%m-%d"),
            g.set_index("period")[["sa", "original", "sa_mom", "yoy"]].rename(columns={"original": "orig", "sa_mom": "short_g", "yoy": "yy_g"}))
           for r, g in d.groupby("release")]
    last = max(f.index.max() for _, _, f in out)
    for vin in store.vintages(EMAE_SA):
        sa, orig = store.as_of(EMAE_SA, vin).dropna(), store.as_of(EMAE_ORIG, vin).dropna()
        if len(sa) and sa.index.max() > last:
            out.append((vin, vin, pd.DataFrame({"sa": sa, "orig": orig})))
            last = sa.index.max()
    return sorted(out, key=lambda x: x[0])


def table(vintages, short_lag: int, year_lag: int, key: str) -> dict:
    """First print, latest and revision path of the short-term (SA) and y/y (original) growth rates."""
    measures = {}
    for m, col, lag in (("short", "sa", short_lag), ("yy", "orig", year_lag)):
        given = f"{m}_g"
        by_v = {v: (f[given].round(1) if given in f and f[given].notna().any() else _growth(f[col], lag)) for v, _, f in vintages}
        first, path = {}, {}
        for v, rel, _ in vintages:
            g = by_v[v].dropna()
            for p, val in g.items():
                if p < PATH_FROM:
                    continue
                if p not in first:
                    first[p] = (v, val)
                path.setdefault(p, []).append([rel or v, float(val)])
        latest = by_v[vintages[-1][0]]
        # Periods already in the earliest archived release were published before it (their first print
        # is unknown), except its latest period, which that release published for the first time.
        v0 = vintages[0][0]
        newest_v0 = by_v[v0].dropna().index.max()
        periods = sorted(p for p in first if first[p][0] != v0 or p == newest_v0)
        rows = [{"period": p.strftime("%Y-%m-%d"), "first": float(first[p][1]), "first_release": first[p][0],
                 "latest": float(latest.get(p, np.nan)) if p in latest.index else None, "releases": len(path[p])}
                for p in periods]
        # Statistics: periods first published after the earliest archived release, with at least three later releases.
        r = pd.DataFrame(rows)
        r["period_ts"] = pd.to_datetime(r["period"])
        r = r[(r["first_release"] > vintages[0][0]) & (r["releases"] >= 4) & ~r["period_ts"].isin(EXCLUDE[key]) & r["latest"].notna()]
        rev = r["latest"] - r["first"]
        stats = {"n": int(len(r)), "mean": round(float(rev.mean()), 2), "mean_abs": round(float(rev.abs().mean()), 2),
                 "sd": round(float(rev.std()), 2), "max_abs": round(float(rev.abs().max()), 2),
                 "sign_changes": int((np.sign(r["first"]) != np.sign(r["latest"])).sum()),
                 "from": r["period"].min(), "to": r["period"].max()}
        measures[m] = {"rows": rows, "paths": {p.strftime("%Y-%m-%d"): path[p] for p in periods}, "stats": stats}
    return {"releases": [{"vintage": v, "release": rel} for v, rel, _ in vintages], "measures": measures,
            "latest": measures["short"]["rows"][-1]["period"],
            "latest_release": vintages[-1][1] or vintages[-1][0]}


def fetch(ids) -> FetchResult:
    global LAST
    empty = pd.DataFrame(columns=["date", "series_id", "value"])
    try:
        LAST = {"gdp": table(gdp_vintages(), 1, 4, "gdp"), "emae": table(emae_vintages(), 1, 12, "emae")}
    except Exception as exc:
        return FetchResult(data=empty, errors={sid: f"revisions: {exc}" for sid in ids})
    frames, meta = [], {}
    for sid in ids:
        key = "gdp" if "gdp" in sid else "emae"
        rows = LAST[key]["measures"]["short"]["rows"]
        frames.append(pd.DataFrame({"date": pd.to_datetime([r["period"] for r in rows]), "series_id": sid, "value": [r["first"] for r in rows]}))
        meta[sid] = {"units": "%, first print"}
    return FetchResult(data=pd.concat(frames, ignore_index=True), meta=meta)


def write_tables(directory) -> list[str]:
    if not LAST:
        return []
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    changed = []
    for key, t in LAST.items():
        p = d / f"{key}.json"
        text = json.dumps(t, separators=(",", ":"))
        if not p.exists() or p.read_text(encoding="utf-8") != text:
            p.write_text(text, encoding="utf-8")
            changed.append(p.name)
    return changed


def tracker_errors() -> dict:
    """How far INDEC's first GDP print has been from the quarter-tracker figure, by EMAE months published.

    For each EMAE release (as published), the quarter in progress: EMAE's average over the months
    published so far against the previous quarter's average (the tracker's bar), compared with
    the first print of GDP q/q (seasonally adjusted) for that quarter. 2020 is left out.
    Returns {months published: {"p68", "p90", "n", "bias"}} in percentage points.
    """
    from .recession import releases
    gv = pd.read_csv(GDP_CSV, parse_dates=["period"]).sort_values(["vintage", "period"])
    gv["qq"] = gv.groupby("vintage")["gdp_sa"].pct_change() * 100
    first = gv.groupby("period").first()["qq"]
    for vin in store.vintages(GDP_SA):                       # later releases, from the stored copies
        sa = store.as_of(GDP_SA, vin).dropna()
        q = sa.index.max()
        if q not in first.index and len(sa) > 1:
            first.loc[q] = 100 * (sa.iloc[-1] / sa.iloc[-2] - 1)
    rows = []
    for _, lv in releases():
        lv = lv.sort_index()
        last = lv.index.max()
        q = last.to_period("Q").start_time
        pq = q - pd.DateOffset(months=3)
        prev, cur = lv.loc[pq:pq + pd.DateOffset(months=2)], lv.loc[q:]
        if len(prev) < 3 or q not in first.index or q.year == 2020:
            continue
        rows.append({"n": len(cur), "err": 100 * (cur.mean() / prev.mean() - 1) - first[q]})
    d = pd.DataFrame(rows)
    out = {}
    for n, g in d.groupby("n"):
        e = g["err"].abs()
        out[int(n)] = {"p68": round(float(e.quantile(0.68)), 2), "p90": round(float(e.quantile(0.9)), 2),
                       "n": int(len(g)), "bias": round(float(g["err"].mean()), 2)}
    return out
