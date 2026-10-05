"""Indicators computed from stored series rather than fetched directly.

Two methods, chosen by `derive.method` in registry/series.yaml:

splice    Chain-link several source series into one long index (monthly or
          quarterly, as the indicator's frequency).
          Each variant lists `segments` ({id, as: index|mom, from, to});
          within a segment the month-on-month change comes from that source
          (`index`: I_t / I_{t-1}; `mom`: 1 + v/100). The chained level is
          scaled to equal `anchor.id` at `anchor.date`, or, with `align`,
          to equal another variant at a given date. A month no segment
          covers is a gap: with `fill_gaps_from` the level carries on with
          that variant's changes (so the level after the gap is comparable)
          but the gap months themselves are left empty.

ratio     num / den * scale per variant ({num, den, scale, rolling}); with
          `rolling: k` both are summed over the last k periods first (e.g.
          investment as a share of GDP over the last four quarters).

tracker   Quarterly averages of a monthly activity index (EMAE), next to
          quarterly GDP, including the quarter in progress: q/q of the months
          published so far against the previous quarter's average (s.a.) and
          y/y of the same months (original series). INDEC reconciles EMAE with
          quarterly GDP, so once GDP is out the two match.

monthly   Monthly series from daily ones: per variant, the daily sum of
          `sum_of` ids (days missing any of them are dropped), then the
          month's average (`how: mean`, default) or last value (`how: last`).

flows     Monthly sources of a stock, in percentage points of the stock at
          the end of the previous month: per variant, the month's sum of the
          daily flows in `sum_of`, over `stock` at the previous month end,
          x 100 (e.g. the factors that explain base money).

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
    months = pd.date_range(months.min(), months.max(), freq="QS" if ind.frequency == "Q" else "MS")
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


def ratio(ind, frames: dict[str, pd.Series]) -> tuple[pd.DataFrame, dict]:
    out = {}
    for k, v in ind.derive["variants"].items():
        num, den = frames[v["num"]], frames[v["den"]]
        if v.get("rolling"):
            r = int(v["rolling"])
            num, den = num.rolling(r).sum(), den.rolling(r).sum()
        out[ind.variants[k].source_id] = (num / den * float(v.get("scale", 1))).dropna()
    return pd.DataFrame(out).sort_index(), {}


def tracker(ind, frames: dict[str, pd.Series]) -> tuple[pd.DataFrame, dict]:
    spec = ind.derive
    sa, orig, gdp = frames[spec["monthly_sa"]], frames[spec["monthly_original"]], frames[spec["quarterly"]]
    q_sa = sa.resample("QS").mean()
    months_in = sa.resample("QS").count()
    q_sa = q_sa[months_in > 0]
    last_q = q_sa.index.max()
    n = int(months_in[last_q])
    have = sa[sa.index >= last_q]
    info = {"quarter": last_q.strftime("%Y-%m-%d"), "months": [d.strftime("%Y-%m-%d") for d in have.index],
            "complete": n == 3, "gdp_published": bool(last_q in gdp.index)}
    prev_q = last_q - pd.offsets.QuarterBegin(1, startingMonth=1)
    if prev_q in q_sa.index:
        info["qoq"] = round(float((q_sa[last_q] / q_sa[prev_q] - 1) * 100), 4)
        # If the remaining months of the quarter stayed at the latest month's level.
        flat = (have.sum() + have.iloc[-1] * (3 - n)) / 3
        info["qoq_if_flat"] = round(float((flat / q_sa[prev_q] - 1) * 100), 4)
    cur = orig[orig.index >= last_q]
    ly = orig.reindex(cur.index - pd.DateOffset(years=1))
    if len(cur) and ly.notna().all():
        info["yoy_original"] = round(float((cur.sum() / ly.sum() - 1) * 100), 4)
    # How closely complete-quarter EMAE averages match published GDP (q/q, s.a.).
    full = sa.resample("QS").mean()[months_in == 3]
    d = (full.pct_change() * 100 - gdp.pct_change() * 100).dropna()
    if len(d):
        info["fit_max_pp"] = round(float(d.abs().max()), 3)
        info["fit_since"] = d.index.min().strftime("%Y-%m-%d")
    df = pd.DataFrame({ind.variants["emae"].source_id: q_sa, ind.variants["gdp"].source_id: gdp})
    return df.sort_index(), {"tracker": info}


def _daily_sum(frames: dict[str, pd.Series], ids: list[str]) -> pd.Series:
    df = pd.concat([frames[i] for i in ids], axis=1)
    return df.dropna().sum(axis=1)


def monthly(ind, frames: dict[str, pd.Series]) -> tuple[pd.DataFrame, dict]:
    out = {}
    for k, v in ind.derive["variants"].items():
        d = _daily_sum(frames, v["sum_of"])
        g = d.resample("MS")
        out[ind.variants[k].source_id] = g.last() if v.get("how") == "last" else g.mean()
    df = pd.DataFrame(out).sort_index()
    # The current month is partial: keep it, and say so.
    last = max(f.index.max() for f in frames.values())
    partial = last + pd.offsets.Day(1) <= last + pd.offsets.MonthEnd(0)
    return df, {"partial_month": last.strftime("%Y-%m-%d") if partial else None}


def flows(ind, frames: dict[str, pd.Series]) -> tuple[pd.DataFrame, dict]:
    spec = ind.derive
    stock = frames[spec["stock"]]
    prev_end = stock.resample("MS").last().shift(1)
    out = {}
    for k, v in spec["variants"].items():
        f = _daily_sum(frames, v["sum_of"]).resample("MS").sum(min_count=1)
        out[ind.variants[k].source_id] = (f / prev_end * 100).dropna()
    df = pd.DataFrame(out).sort_index()
    end = stock.resample("MS").last()
    info = {"stock_change_pct": {d.strftime("%Y-%m-%d"): round(float(v), 4)
                                 for d, v in ((end / prev_end - 1) * 100).dropna().items()}}
    return df, info


def expectations(ind, frames: dict[str, pd.Series]) -> tuple[pd.DataFrame, dict]:
    """Expected inflation for the next 12 months (survey month t) against
    realized inflation P_{t+12}/P_t - 1 and past inflation P_t/P_{t-12} - 1."""
    spec = ind.derive
    p = frames[spec["cpi"]]
    cols = {"expected": frames[spec["expected"]],
            "realized": (p.shift(-12) / p - 1) * 100,
            "past": (p / p.shift(12) - 1) * 100}
    df = pd.DataFrame({ind.variants[k].source_id: v for k, v in cols.items() if k in ind.variants})
    return df.dropna(how="all").sort_index(), {}


def compute(ind, reg) -> tuple[pd.DataFrame, dict]:
    ids = ind.input_ids()
    frames = {sid: store.as_of(sid) for sid in ids}
    empty = [sid for sid, s in frames.items() if s.empty]
    if empty:
        raise ValueError(f"no stored data for {empty}")
    if ind.derive["method"] == "splice":
        return splice(ind, frames)
    if ind.derive["method"] == "ratio":
        return ratio(ind, frames)
    if ind.derive["method"] == "tracker":
        return tracker(ind, frames)
    if ind.derive["method"] == "expectations":
        return expectations(ind, frames)
    if ind.derive["method"] == "monthly":
        return monthly(ind, frames)
    if ind.derive["method"] == "flows":
        return flows(ind, frames)
    return reweight(ind, reg, frames)
