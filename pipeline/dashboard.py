"""Home-page summary dashboard: data/dashboard.json, built from the exported card data.

For each row of registry/dashboard.yaml it takes the card's series, puts it on a
monthly calendar (daily data: the month's average or last value; quarterly data
stay quarterly and sit in the column of the quarter's last month), and computes
two views:

  mm  month-on-month change (quarter-on-quarter for quarterly series), on the
      seasonally adjusted series where there is one
  yy  change over twelve months (four quarters), on the original series

Each change carries a z-score against the same change over the previous ten
years, with a robust center and scale (median and 1.4826 x the median absolute
deviation), signed so that a positive score is an improvement (`good`) and
capped at +/-3. The site colors cells by that score, so a move is strong only if
it is unusual for that series.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .registry import ROOT

SPEC = ROOT / "registry" / "dashboard.yaml"
COLUMNS = 7          # months shown
HISTORY_YEARS = 10   # window for the z-scores
SPARK_MONTHS = 36


def _series(payload: dict, row: dict, key: str | None = None) -> pd.Series:
    dates = pd.to_datetime(payload["dates"])
    V = payload["variants"]
    keys = row.get("sum") or [key or row["variant"]]
    vals = None
    for k in keys:
        v = pd.Series(V[k]["values"], index=dates, dtype=float)
        vals = v if vals is None else vals.add(v, fill_value=0) if row.get("sum") else v
    if row.get("sum"):   # a sum is only valid where every component is present
        present = pd.concat([pd.Series(V[k]["values"], index=dates, dtype=float).notna() for k in keys], axis=1).all(axis=1)
        vals = vals.where(present)
    if row.get("abs"):   # e.g. imports, stored as negative flows
        vals = vals.abs()
    if row.get("real") and payload.get("deflator"):
        d = pd.Series(payload["deflator"]["values"], index=dates, dtype=float)
        vals = vals / d * payload["deflator"]["latest_value"]
    return vals.dropna()


def _calendar(s: pd.Series, freq: str, agg: str) -> tuple[pd.Series, str | None]:
    """Monthly (or quarterly) series; for daily data also the last day used in a partial month."""
    if s.empty:
        return s, None
    if freq == "D":
        g = s.groupby(s.index.to_period("M"))
        m = (g.last() if agg == "last" else g.mean())
        m.index = m.index.to_timestamp()
        last = s.index.max()
        partial = last.strftime("%Y-%m-%d") if last.day < 25 else None
        return m, partial
    return s, None


def _change(s: pd.Series, how: str | None, lag: int, scale: float = 1.0) -> pd.Series | None:
    if not how:
        return None
    if how == "pct":
        return 100 * (s / s.shift(lag) - 1)
    return (s - s.shift(lag)) * scale


def _z(ch: pd.Series, good: str) -> pd.Series:
    """Robust z-score of each change against the previous HISTORY_YEARS of changes, signed by `good`."""
    sign = {"up": 1, "down": -1}.get(good, 0)
    out = pd.Series(np.nan, index=ch.index)
    for d in ch.dropna().index:
        hist = ch[(ch.index < d) & (ch.index >= d - pd.DateOffset(years=HISTORY_YEARS))].dropna()
        if len(hist) < 8:
            continue
        med = hist.median()
        scale = 1.4826 * (hist - med).abs().median() or hist.std()
        if not scale or np.isnan(scale):
            continue
        z = (ch[d] - med) / scale
        out[d] = float(np.clip(z * (sign or 1), -3, 3))
    return out


def _col_of(d: pd.Timestamp, freq: str) -> pd.Timestamp:
    """Column (month) where an observation sits: quarterly data in the quarter's last month."""
    return d + pd.DateOffset(months=2) if freq == "Q" else d


def build(data_dir: Path, spec_path: Path = SPEC) -> dict:
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    rows_out, latest_month = [], None
    for g in spec["groups"]:
        for row in g["rows"]:
            p = data_dir / f"{row['ind']}.json"
            if not p.exists():
                continue
            payload = json.loads(p.read_text(encoding="utf-8"))
            freq = payload["frequency"]
            lag_m, lag_y = 1, (4 if freq == "Q" else 12)
            s, partial = _calendar(_series(payload, row), freq, row.get("agg", "mean"))
            if s.empty:
                continue
            ys = s if not row.get("yy_variant") else _calendar(_series(payload, row, row["yy_variant"]), freq, row.get("agg", "mean"))[0]
            dscale = row.get("diff_scale", 1.0)
            views = {}
            for name, ch in (("mm", _change(s, row.get("mm"), lag_m, dscale)), ("yy", _change(ys, row.get("yy"), lag_y, dscale))):
                if ch is None:
                    views[name] = None
                    continue
                ch = ch.dropna()
                z = _z(ch, row.get("good", "none"))
                views[name] = {"kind": row.get(name), "values": {_col_of(d, freq).strftime("%Y-%m-%d"): [round(float(v), 3), None if np.isnan(z[d]) else round(float(z[d]), 2)]
                                                                 for d, v in ch.items() if d >= ch.index.max() - pd.DateOffset(months=30)}}
            last_col = _col_of(s.index.max(), freq)
            if freq != "Q":
                latest_month = max(latest_month, last_col) if latest_month is not None else last_col
            spark = s[s.index >= s.index.max() - pd.DateOffset(months=SPARK_MONTHS)]
            scale = row.get("scale", 1.0)
            rows_out.append({
                "group": g["label"], "label": row["label"], "ind": row["ind"], "topic": payload["topic"], "freq": freq,
                "good": row.get("good", "none"), "mm_nsa": bool(row.get("mm_nsa")), "unit": row.get("unit", ""),
                "level": round(float(s.iloc[-1]) * scale, 4), "level_date": s.index.max().strftime("%Y-%m-%d"), "partial": partial,
                "real": bool(row.get("real")), "status": payload.get("status", "ok"),
                "spark": [round(float(v) * scale, 4) for v in spark.values], "spark_start": spark.index.min().strftime("%Y-%m-%d"),
                "mm": views["mm"], "yy": views["yy"],
            })
    cols = [(latest_month - pd.DateOffset(months=k)).strftime("%Y-%m-%d") for k in range(COLUMNS - 1, -1, -1)] if latest_month is not None else []
    for r in rows_out:   # keep only the shown columns
        for name in ("mm", "yy"):
            if r[name]:
                r[name]["values"] = {c: r[name]["values"].get(c) for c in cols}
    return {"columns": cols, "history_years": HISTORY_YEARS, "rows": rows_out}


def write(data_dir: Path) -> None:
    out = build(data_dir)
    (data_dir / "dashboard.json").write_text(json.dumps(out, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    print(f"dashboard: {len(out['rows'])} rows, columns {out['columns'][:1]}..{out['columns'][-1:]}")
