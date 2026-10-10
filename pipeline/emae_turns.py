"""How long the EMAE trend-cycle takes to signal a turning point, in real time.

Inputs: the trend-cycle as published at each release, from INDEC's informes
técnicos since August 2016 (data/manual/emae_trend_vintages.csv, see
pipeline/emae_vintages.py), extended with the releases the daily update has
stored since (data/vintages, series 143.3_NO_PR_2004_A_28); and the
trend-cycle as published today.

Definitions:
  turning point   on today's trend-cycle, the last month of a phase of rising
                  (peak) or falling (trough) months; phases shorter than
                  MIN_PHASE months are merged into their neighbours; zero
                  changes continue the previous phase.
  signal          a release whose latest month shows a trend-cycle change
                  (rounded to 0.1, as published) with the sign of the new
                  phase.
  first signal    the first such release after the turning point.
  confirmed       the first release that starts a run of CONFIRM releases in a
                  row, all with the new sign.
  reversed signal a change of sign in the latest month that turns back within
                  CONFIRM - 1 releases.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from . import store
from .registry import ROOT

SID = "143.3_NO_PR_2004_A_28"
VINTAGES_CSV = ROOT / "data" / "manual" / "emae_trend_vintages.csv"
MIN_PHASE = 4
CONFIRM = 3
START = pd.Timestamp("2016-06-01")   # the first release (August 2016) covers data to June 2016


def releases() -> pd.DataFrame:
    """One row per release: release date, last month of data, latest trend-cycle change (as published, %)."""
    v = pd.read_csv(VINTAGES_CSV, parse_dates=["release", "period"])
    rows = []
    for (rel, f), g in v.groupby(["release", "file"]):
        g = g.sort_values("period")
        rows.append({"release": rel, "last": g.period.iloc[-1], "mom": float(g.trend_mom.iloc[-1]), "source": "pdf"})
    out = pd.DataFrame(rows)
    # Releases stored by the daily update after the PDFs: each vintage that adds a month.
    last_pdf = out["last"].max()
    for vin in store.vintages(SID):
        s = store.as_of(SID, vin).dropna()
        if len(s) < 2 or s.index.max() <= out["last"].max():
            continue
        rows.append({"release": pd.Timestamp(vin), "last": s.index.max(), "mom": round(float((s.iloc[-1] / s.iloc[-2] - 1) * 100), 1), "source": "store"})
        out = pd.DataFrame(rows)
    return out.sort_values("release").reset_index(drop=True)


def turning_points(final: pd.Series) -> list[tuple[str, pd.Timestamp]]:
    m = ((final / final.shift(1) - 1) * 100).round(1)
    sign = np.sign(m).replace(0, np.nan).ffill().dropna()
    runs, cur, start, prev = [], None, None, None
    for d, s in sign.items():
        if s != cur:
            if cur is not None:
                runs.append([cur, start, prev])
            cur, start = s, d
        prev = d
    runs.append([cur, start, prev])
    merged = True
    while merged:
        merged = False
        for i in range(1, len(runs) - 1):
            s, a, b = runs[i]
            if (b.year - a.year) * 12 + b.month - a.month + 1 < MIN_PHASE:
                runs[i - 1:i + 2] = [[runs[i - 1][0], runs[i - 1][1], runs[i + 1][2]]]
                merged = True
                break
    return [("peak" if r1[0] > 0 else "trough", r1[2]) for r1, r2 in zip(runs, runs[1:])]


def months(a: pd.Timestamp, b: pd.Timestamp) -> int:
    return (b.year - a.year) * 12 + b.month - a.month


def build() -> dict:
    rel = releases()
    final = store.as_of(SID).dropna()
    fm = ((final / final.shift(1) - 1) * 100).round(2)
    tps = [(k, t) for k, t in turning_points(final) if t >= START]
    v = pd.read_csv(VINTAGES_CSV, parse_dates=["release", "period"])
    sgn = np.sign(rel["mom"].round(1))
    turns = []
    for n, (kind, T) in enumerate(tps):
        want = -1 if kind == "peak" else 1
        nxt = tps[n + 1][1] if n + 1 < len(tps) else None
        after = rel[rel["last"] > T]
        if nxt is not None:
            after = after[after["last"] <= nxt + pd.DateOffset(months=CONFIRM)]
        s = np.sign(after["mom"].round(1))
        first = after[s == want].index.min() if (s == want).any() else None
        conf = None
        for i in range(len(after) - CONFIRM + 1):
            if (s.iloc[i:i + CONFIRM] == want).all():
                conf = after.index[i]
                break
        def lags(ix):
            if ix is None or pd.isna(ix):
                return None
            r = rel.loc[ix]
            return {"release": r["release"].strftime("%Y-%m-%d"), "data_to": r["last"].strftime("%Y-%m-%d"),
                    "data_lag": months(T, r["last"]), "lag": months(T, r["release"])}
        dated = None
        if conf is not None and rel.loc[conf, "source"] == "pdf":
            g = v[v.release == rel.loc[conf, "release"]].set_index("period").trend
            g = g[(g.index >= T - pd.DateOffset(months=6)) & (g.index <= min(T + pd.DateOffset(months=6), rel.loc[conf, "last"]))]
            if len(g):
                d = g.idxmax() if kind == "peak" else g.idxmin()
                # A release that shows no turn near this month (its extreme sits at the window's edge) dates nothing.
                if abs(months(T, d)) < 6 and d not in (g.index.min(), g.index.max()):
                    dated = d.strftime("%Y-%m-%d")
        turns.append({"kind": kind, "month": T.strftime("%Y-%m-%d"), "first": lags(first), "confirmed": lags(conf),
                      "dated_then": dated})
    # Reversed signals: the latest month changes sign, but today's trend-cycle has the opposite sign for that month.
    reversed_ = []
    prev = None
    for i in range(len(rel)):
        sg = sgn.iloc[i]
        if sg == 0:
            continue
        last = rel.loc[i, "last"]
        fs = np.sign(round(float(fm[last]), 1)) if last in fm.index and not pd.isna(fm[last]) else 0
        if prev is not None and sg != prev and fs == -sg:
            reversed_.append({"release": rel.loc[i, "release"].strftime("%Y-%m-%d"),
                              "data_to": last.strftime("%Y-%m-%d"), "mom": float(rel.loc[i, "mom"])})
        prev = sg
    firsts = [t["first"]["data_lag"] for t in turns if t["first"] and t["confirmed"]]
    confs = [t["confirmed"]["data_lag"] for t in turns if t["confirmed"]]
    firsts_cal = [t["first"]["lag"] for t in turns if t["first"] and t["confirmed"]]
    confs_cal = [t["confirmed"]["lag"] for t in turns if t["confirmed"]]
    med = lambda x: float(np.median(x)) if x else None
    return {
        "turns": turns,
        "reversed": reversed_,
        "releases": [{"release": r["release"].strftime("%Y-%m-%d"), "data_to": r["last"].strftime("%Y-%m-%d"), "mom": r["mom"],
                      "final": (None if r["last"] not in fm.index or pd.isna(fm[r["last"]]) else round(float(fm[r["last"]]), 2))}
                     for _, r in rel.iterrows()],
        "summary": {"n_releases": int(len(rel)), "first_release": rel["release"].min().strftime("%Y-%m-%d"),
                    "last_release": rel["release"].max().strftime("%Y-%m-%d"),
                    "median_first_data_lag": med(firsts), "median_first_lag": med(firsts_cal),
                    "median_confirmed_data_lag": med(confs), "median_confirmed_lag": med(confs_cal),
                    "range_first_data_lag": [min(firsts), max(firsts)] if firsts else None,
                    "n_reversed": len(reversed_), "confirm": CONFIRM, "min_phase": MIN_PHASE},
    }


if __name__ == "__main__":
    import json
    print(json.dumps(build(), indent=1)[:4000])
