"""Indicators computed from stored series rather than fetched directly.

Two methods, chosen by `derive.method` in registry/series.yaml:

splice    Chain-link several source series into one long monthly index.
          Each variant lists `segments` ({id, as: index|mom, from, to});
          within a segment the month-on-month change comes from that source
          (`index`: I_t / I_{t-1}; `mom`: 1 + v/100). The chained level is
          scaled to equal `anchor.id` at `anchor.date`, or, with `align`,
          to equal another variant at a given date. A month no segment
          covers is a gap: with `fill_gaps_from` the level carries on with
          that variant's changes (so the level after the gap is comparable)
          but the gap months themselves are left empty.

reweight  What a fixed-base CPI would show with a different basket, from the
          month the new basket would have started (`link`). With division
          indices I_i and price relatives r_i,t = I_i,t / I_i,link:

              CF_t = H_t * sum_i w_new,i r_i,t / sum_i w_old,i r_i,t

          w_old are the current basket's weights recovered from the index
          (pipeline.export.fitted_weights) and price-updated to the link
          month; w_new are the new basket's division weights, taken as
          applying at the link month (`new_weights_reference: link`) or
          price-updated from the survey period (`survey: [from, to]`). The
          denominator replicates the official index from the same division
          data, so the gap between the two lines is the reweighting effect
          only, free of replication error. Before `link`, CF = H.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import store


def _ratios(seg: dict, src: pd.Series, months: pd.DatetimeIndex) -> pd.Series:
    """Gross month-on-month change from one segment, over the months it covers."""
    lo = pd.Timestamp(seg["from"]) if seg.get("from") else months.min()
    hi = pd.Timestamp(seg["to"]) if seg.get("to") else months.max()
    s = src.reindex(months)
    g = (1 + s / 100) if seg.get("as") == "mom" else s / s.shift(1)
    return g[(g.index >= lo) & (g.index <= hi)]


def chain(segments: list[dict], frames: dict[str, pd.Series], months: pd.DatetimeIndex) -> pd.Series:
    """Gross m/m changes over `months`; NaN where no segment has data."""
    g = pd.Series(np.nan, index=months)
    for seg in segments:
        r = _ratios(seg, frames[seg["id"]], months).dropna()
        g.loc[r.index] = r
    return g


def splice(ind, frames: dict[str, pd.Series]) -> pd.DataFrame:
    spec = ind.derive
    months = pd.DatetimeIndex(sorted(set().union(*[s.index for s in frames.values()])))
    months = pd.date_range(months.min(), months.max(), freq="MS")
    gross = {k: chain(v["segments"], frames, months) for k, v in spec["variants"].items()}
    # Earliest month at which a segment starts is the first level point.
    starts = {}
    for k, v in spec["variants"].items():
        first_ids = [seg for seg in v["segments"] if not seg.get("from")]
        s0 = frames[first_ids[0]["id"]].first_valid_index() if first_ids else gross[k].first_valid_index()
        starts[k] = s0
    out, gaps = {}, {}
    for k, v in spec["variants"].items():
        g = gross[k].copy()
        g[g.index <= starts[k]] = np.nan
        gaps[k] = g.index[(g.index > starts[k]) & g.isna()]
        if v.get("fill_gaps_from"):
            g.loc[gaps[k]] = gross[v["fill_gaps_from"]].loc[gaps[k]]
        lvl = g.fillna(1.0).cumprod()
        lvl[lvl.index < starts[k]] = np.nan
        out[k] = lvl
    # Scale: anchor first, then variants aligned to another variant.
    anchor = spec["anchor"]
    a_date = pd.Timestamp(anchor["date"])
    a_val = frames[anchor["id"]].get(a_date)
    for k, v in spec["variants"].items():
        if v.get("align"):
            continue
        out[k] = out[k] / out[k][a_date] * a_val
    for k, v in spec["variants"].items():
        al = v.get("align")
        if al:
            d = pd.Timestamp(al["date"])
            out[k] = out[k] / out[k][d] * out[al["variant"]][d]
    for k in out:
        out[k].loc[gaps[k]] = np.nan          # gap months stay empty
        out[k] = out[k][out[k].index <= gross[k].last_valid_index()]
    info = {"gaps": {k: [[g[0].strftime("%Y-%m-%d"), g[-1].strftime("%Y-%m-%d")] for g in _runs(gaps[k])]
                     for k in out if len(gaps[k])}}
    return pd.DataFrame({ind.variants[k].source_id: s for k, s in out.items()}), info


def _runs(idx: pd.DatetimeIndex) -> list[list[pd.Timestamp]]:
    """Consecutive months grouped into runs."""
    runs: list[list[pd.Timestamp]] = []
    for d in idx:
        if runs and d == runs[-1][-1] + pd.offsets.MonthBegin(1):
            runs[-1].append(d)
        else:
            runs.append([d])
    return runs


def price_update(w: pd.Series, X: pd.DataFrame, to: pd.Timestamp, period) -> pd.Series:
    """Re-express expenditure shares from `period` ([first, last] month) at month `to` prices."""
    base = X.loc[pd.Timestamp(str(period[0])):pd.Timestamp(str(period[1]))].mean()
    u = w * X.loc[to] / base
    return u / u.sum()


def reweight(ind, reg, frames: dict[str, pd.Series]) -> tuple[pd.DataFrame, dict]:
    from .export import fitted_weights
    spec = ind.derive
    comp = next(i for i in reg.indicators if i.id == spec["components"])
    keys = list(comp.variants)
    X = store.latest_frame([comp.variants[k].source_id for k in keys])
    X.columns = keys
    H = frames[spec["headline"]]
    link = pd.Timestamp(spec["link"])
    fit = fitted_weights(comp, reg, {"indicator": spec.get("fit_to", "cpi"), "variant": "headline"})
    if fit is None:
        raise ValueError("could not recover the current basket's weights")
    # Recovered weights satisfy H_t = sum_i w_i I_i,t (all indices share the
    # base), so at link-month prices the current basket's shares are w_i I_i,link.
    w_old = pd.Series(fit["values"])[keys]
    w_old_link = w_old * X.loc[link]
    w_old_link = w_old_link / w_old_link.sum()
    w_new = pd.Series(spec["new_weights"], dtype=float)[keys]
    w_new = w_new / w_new.sum()
    ref = spec.get("new_weights_reference", "link")
    if ref != "link":   # survey-period expenditure shares: [first month, last month]
        w_new_link = price_update(w_new, X, link, ref)
    else:
        w_new_link = w_new
    r = X.loc[link:] / X.loc[link]
    num = (r * w_new_link).sum(axis=1)
    den = (r * w_old_link).sum(axis=1)
    cf = H.copy()
    after = H.index[H.index >= link]
    common = after.intersection(num.dropna().index)
    cf.loc[after] = np.nan
    cf.loc[common] = H.loc[common] * num.loc[common] / den.loc[common]
    cf = cf.dropna()
    df = pd.DataFrame({ind.variants["official"].source_id: H, ind.variants["reweighted"].source_id: cf})
    official_rep = (H.loc[common] / H.loc[link]) / den.loc[common]
    info = {
        "link": link.strftime("%Y-%m-%d"),
        "replication_max_error_pct": round(float((official_rep - 1).abs().max() * 100), 4),
        "weights": {
            "labels": {k: comp.variants[k].label for k in keys},
            "old_at_link": {k: round(float(v * 100), 2) for k, v in w_old_link.items()},
            "new": {k: round(float(v * 100), 2) for k, v in w_new_link.items()},
        },
    }
    return df, info


def compute(ind, reg) -> tuple[pd.DataFrame, dict]:
    ids = ind.input_ids()
    frames = {sid: store.as_of(sid) for sid in ids}
    empty = [sid for sid, s in frames.items() if s.empty]
    if empty:
        raise ValueError(f"no stored data for {empty}")
    if ind.derive["method"] == "splice":
        return splice(ind, frames)
    return reweight(ind, reg, frames)
