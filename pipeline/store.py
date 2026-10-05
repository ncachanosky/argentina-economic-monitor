"""Append-only vintage store.

Each source series lives in data/vintages/<series_id>.csv with columns
`vintage, date, value`. A row is written only when an observation is new
or its value changed, so the file is a compact revision history:

    the value of `date` as known on day V
        = the value in the last row for `date` with vintage <= V

This turns the daily pipeline into a real-time dataset as a byproduct.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .registry import ROOT

VINTAGE_DIR = ROOT / "data" / "vintages"
REL_TOL = 1e-9


def _path(series_id: str, base: Path | None = None) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", series_id)
    return (base or VINTAGE_DIR) / f"{safe}.csv"


def load(series_id: str, base: Path | None = None) -> pd.DataFrame:
    p = _path(series_id, base)
    if not p.exists():
        return pd.DataFrame({"vintage": pd.Series(dtype="object"),
                             "date": pd.Series(dtype="datetime64[ns]"),
                             "value": pd.Series(dtype="float64")})
    df = pd.read_csv(p, dtype={"vintage": str})
    df["date"] = pd.to_datetime(df["date"])
    return df


def as_of(series_id: str, vintage: str | None = None, base: Path | None = None) -> pd.Series:
    """Series as known at `vintage` (YYYY-MM-DD); latest if None."""
    df = load(series_id, base)
    if vintage is not None:
        df = df[df["vintage"] <= vintage]
    if df.empty:
        return pd.Series(dtype="float64", name=series_id)
    s = (
        df.sort_values(["date", "vintage"], kind="stable")
        .groupby("date")["value"]
        .last()
        .sort_index()
    )
    s.name = series_id
    return s


def vintages(series_id: str, base: Path | None = None) -> list[str]:
    return sorted(load(series_id, base)["vintage"].unique().tolist())


@dataclass
class AppendResult:
    new_obs: int
    revised_obs: int

    @property
    def changed(self) -> bool:
        return bool(self.new_obs or self.revised_obs)


def append(series_id: str, values: pd.Series, vintage: str, base: Path | None = None) -> AppendResult:
    """Record `values` (indexed by date) as of `vintage`; writes only deltas."""
    values = values.dropna().sort_index()
    current = as_of(series_id, base=base)

    joined = pd.concat({"old": current, "new": values}, axis=1)
    is_new = joined["old"].isna() & joined["new"].notna()
    both = joined["old"].notna() & joined["new"].notna()
    rel = (joined["new"] - joined["old"]).abs() / joined["old"].abs().clip(lower=1e-12)
    is_rev = both & (rel > REL_TOL)
    delta = joined.loc[is_new | is_rev, "new"]

    result = AppendResult(new_obs=int(is_new.sum()), revised_obs=int(is_rev.sum()))
    if delta.empty:
        return result

    df = load(series_id, base)
    # Re-running on the same day replaces that day's rows instead of stacking them.
    df = df[~((df["vintage"] == vintage) & df["date"].isin(delta.index))]
    add = pd.DataFrame({"vintage": vintage, "date": delta.index, "value": delta.values})
    out = pd.concat([df, add], ignore_index=True).sort_values(["vintage", "date"])

    p = _path(series_id, base)
    p.parent.mkdir(parents=True, exist_ok=True)
    out.assign(date=out["date"].dt.strftime("%Y-%m-%d")).to_csv(
        p, index=False, float_format="%.10g", lineterminator="\n"
    )
    return result


def latest_frame(series_ids: list[str], base: Path | None = None) -> pd.DataFrame:
    """Wide frame (date x series_id) of the latest vintage of each series."""
    cols = {sid: as_of(sid, base=base) for sid in series_ids}
    df = pd.concat(cols, axis=1) if cols else pd.DataFrame()
    return df.sort_index()
