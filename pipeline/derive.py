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
          month's average (`how: mean`, default), last value (`how: last`)
          or total (`how: sum`, for daily flows).

flows     Monthly sources of a stock, in percentage points of the stock at
          the end of the previous month: per variant, the month's sum of the
          daily flows in `sum_of`, over `stock` at the previous month end,
          x 100 (e.g. the factors that explain base money).

formula   Arithmetic on stored series: `vars` maps short names to series ids;
          each variant has an `expr` over those names (+ - * / and nz(x),
          which treats a missing value as zero). Dates where the `anchor`
          variable is missing are dropped. `positive_only: true` blanks
          results <= 0 (e.g. an implied exchange rate when net reserves are
          negative); `max: <expr>` blanks results above that bound. A var may name another derived indicator's series as
          "@indicator:variant" (taken at month end for monthly indicators).
          Also: sum4(x) and sum12(x), sums of the last four or twelve
          periods; avg12(x), the average of the last twelve; lag(x, k), the
          value k periods earlier; cum12(x), twelve periods of a % return
          compounded; gdp12(gdp, cpi), nominal GDP of the last
          twelve months at a monthly frequency (see gdp12); `start` drops
          earlier dates; in a quarterly indicator, monthly inputs are taken
          at the last month of each quarter, and in a monthly one daily
          inputs at the last day of each month.

net_reserves  Gross reserves minus foreign-currency liabilities, under
          several conventions, weekly; see NET_CONVENTIONS below.

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
        how = v.get("how", "mean")
        out[ind.variants[k].source_id] = g.last() if how == "last" else g.sum(min_count=1) if how == "sum" else g.mean()
    df = pd.DataFrame(out).sort_index()
    # The current month is partial: keep it once it has 10 business days of
    # data (an average of a few days is mostly noise), and say so.
    last = max(f.index.max() for f in frames.values())
    partial = last + pd.offsets.Day(1) <= last + pd.offsets.MonthEnd(0)
    if partial:
        days = sum(1 for d in next(iter(frames.values())).index if d >= last.replace(day=1))
        if days < 10:
            df = df[df.index < last.replace(day=1)]
            return df, {"partial_month": None}
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


def gdp12(gdp: pd.Series, cpi: pd.Series) -> pd.Series:
    """Nominal GDP of the twelve months ending in each month, on the index of `gdp`.

    Quarterly GDP (at quarter-start dates, at annual rates as INDEC publishes
    it) averaged over four quarters is the GDP of the twelve months ending in
    the quarter's last month; between
    quarter ends it is interpolated log-linearly, and after the last quarter
    published it is carried forward with the CPI (twelve-month average), so
    ratios to GDP for the latest months do not wait for the national accounts.
    """
    idx = gdp.index
    q = gdp.dropna()
    q = q[q.index.month.isin([1, 4, 7, 10])]
    s4 = q.rolling(4).mean().dropna()                    # INDEC publishes quarters at annual rates
    s4.index = s4.index + pd.DateOffset(months=2)        # quarter's last month
    months = pd.date_range(s4.index.min(), idx.max(), freq="MS")
    out = np.exp(np.log(s4).reindex(months).interpolate(limit_area="inside"))
    last = s4.index.max()
    c = cpi.dropna()
    c = c.reindex(pd.date_range(c.index.min(), c.index.max(), freq="MS")).rolling(12).mean()
    if last in c.index and pd.notna(c.get(last)):
        ahead = months[months > last]
        out.loc[ahead] = s4[last] * (c.reindex(ahead) / c[last]).values
    return out.reindex(idx)


def formula(ind, frames: dict[str, pd.Series]) -> tuple[pd.DataFrame, dict]:
    import ast
    spec = ind.derive
    names = spec["vars"]
    idx = pd.DatetimeIndex(sorted(set().union(*[frames[i].index for i in names.values()])))
    # How a higher-frequency input is brought to the formula's frequency: its
    # last value (default), or the period's sum (flows) or mean (averages).
    how = spec.get("vars_how") or {}
    agg = lambda r, v: (r.sum(min_count=1) if how.get(v) == "sum" else r.mean() if how.get(v) == "mean" else r.last())
    if ind.frequency == "Q":
        # Monthly (and daily) inputs enter a quarterly formula at their quarter's last value, or as set in vars_how.
        def q(sr, k):
            sr = sr.dropna()
            if len(sr) > 2 and sr.index.to_series().diff().dt.days.median() < 40:
                sr = agg(sr.resample("QS"), k)
            return sr
        frames = {**frames, **{v: q(frames[v], k) for k, v in names.items()}}
        idx = pd.DatetimeIndex(sorted(set().union(*[frames[i].index for i in names.values()])))
    elif ind.frequency == "M":
        # Daily inputs enter a monthly formula at their month's last value.
        def m(sr, k):
            sr = sr.dropna()
            if len(sr) > 2 and sr.index.to_series().diff().dt.days.median() < 20:
                sr = agg(sr.resample("MS"), k)
            return sr
        frames = {**frames, **{v: m(frames[v], k) for k, v in names.items()}}
        idx = pd.DatetimeIndex(sorted(set().union(*[frames[i].index for i in names.values()])))
    env = {k: frames[v].reindex(idx) for k, v in names.items()}
    env["nz"] = lambda x: x.fillna(0)
    env["sum4"] = lambda x: x.rolling(4).sum()          # last four quarters (or periods)
    env["sum12"] = lambda x: x.rolling(12).sum()        # last twelve months (or periods)
    env["avg12"] = lambda x: x.rolling(12).mean()       # average of the last twelve periods
    env["lag"] = lambda x, k=1: x.shift(int(k))         # value k periods earlier
    env["gdp12"] = lambda gdp, cpi: gdp12(gdp, cpi)     # nominal GDP of the last twelve months, monthly
    env["cum12"] = lambda x: ((1 + x / 100).rolling(12).apply(np.prod, raw=True) - 1) * 100   # compounded % over 12 periods
    funcs = {"nz", "sum4", "sum12", "avg12", "lag", "gdp12", "cum12"}
    allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.USub,
               ast.Name, ast.Load, ast.Constant, ast.Call)
    out = {}
    for k, v in spec["variants"].items():
        tree = ast.parse(v["expr"], mode="eval")
        for node in ast.walk(tree):
            if not isinstance(node, allowed) or (isinstance(node, ast.Call) and getattr(node.func, "id", None) not in funcs):
                raise ValueError(f"{ind.id}.{k}: unsupported expression {v['expr']!r}")
            if isinstance(node, ast.Name) and node.id not in env:
                raise ValueError(f"{ind.id}.{k}: unknown name {node.id!r}")
        res = eval(compile(tree, "<expr>", "eval"), {"__builtins__": {}}, env)
        if v.get("positive_only"):
            res = res.where(res > 0)
        if v.get("max"):
            mtree = ast.parse(v["max"], mode="eval")
            for node in ast.walk(mtree):
                if not isinstance(node, allowed) or isinstance(node, ast.Call) or (isinstance(node, ast.Name) and node.id not in env):
                    raise ValueError(f"{ind.id}.{k}: unsupported max {v['max']!r}")
            res = res.where(res <= eval(compile(mtree, "<max>", "eval"), {"__builtins__": {}}, env))
        out[ind.variants[k].source_id] = res
    df = pd.DataFrame(out)
    anchor = env[spec["anchor"]] if spec.get("anchor") else None
    if anchor is not None:
        df = df[anchor.notna()]
    df = df.replace([np.inf, -np.inf], np.nan)
    if spec.get("start"):
        df = df[df.index >= pd.Timestamp(str(spec["start"]))]
    df = df.dropna(how="all").sort_index()
    info = {}
    import re
    m = next((re.search(r"gdp12\(\s*(\w+)", v["expr"]) for v in spec["variants"].values() if "gdp12(" in v["expr"]), None)
    if m and m.group(1) in names:
        # Last month the national accounts cover; later months use GDP extended with the CPI.
        q = frames[names[m.group(1)]].dropna()
        if len(q) and len(df):
            through = q.index.max() + pd.DateOffset(months=2)
            info["gdp_through"] = through.strftime("%Y-%m-%d")
            info["gdp_estimated"] = bool(df.index.max() > through)
    return df, info


# ---------- net international reserves ----------
#
# Net reserves have no official or universal definition. Three conventions,
# each gross reserves minus a set of foreign-currency liabilities (USD
# millions, at each weekly balance's exchange rates):
#
#   standard  the usual market estimate: minus banks' dollar reserve
#             requirements, the China swap in full, obligations with
#             international agencies (BIS and others), the BCRA's repos with
#             foreign banks and the Treasury's dollar deposits at the BCRA.
#   liquid    standard, minus gold.
#   imf       the IMF program's NIR (TMU, May 2026): minus reserve
#             requirements, swaps, obligations with international agencies,
#             repos of one year or less, and the Fund's net purchases since the
#             program started (April 2025). Treasury deposits are not
#             deducted. Items the weekly balance does not show (deposit
#             insurance, BOPREAL due within a year, forwards) are left out, so
#             this is an approximation.
NET_CONVENTIONS = [("standard", "Standard (market)"), ("liquid", "Liquid"), ("imf", "IMF-style (approx.)")]
NET_ROWS = [
    # key, label, conventions that deduct it ("imf" reads the short-maturity repos)
    ("encaje", "Banks' dollar reserve requirements (encajes)", ("standard", "liquid", "imf")),
    ("swap", "China swap (PBoC), full amount", ("standard", "liquid", "imf")),
    ("intl_org", "Obligations with international agencies (BIS, others)", ("standard", "liquid", "imf")),
    ("repos", "BCRA repos with foreign banks", ("standard", "liquid", "imf")),
    ("treasury", "Treasury dollar deposits at the BCRA", ("standard", "liquid")),
    ("gold", "Gold", ("liquid",)),
    ("imf_credit", "IMF net purchases since April 2025", ("imf",)),
]
REPO_CHECK_FROM = "2024-08-01"     # peso repos ended in July 2024: the line is dollar items only
REPO_CHECK_USD_M = 500


def _asof(s: pd.Series, idx: pd.DatetimeIndex) -> pd.Series:
    """Value in force at each date of `idx` (last observation on or before it)."""
    s = s.dropna().sort_index()
    return s.reindex(s.index.union(idx)).ffill().reindex(idx)


def net_reserves(ind, frames: dict[str, pd.Series]) -> tuple[pd.DataFrame, dict]:
    spec = ind.derive
    i = spec["inputs"]
    gross = frames[i["gross"]]
    gross = gross[gross.index >= pd.Timestamp(spec.get("start", "2003-01-01"))].dropna()
    idx = gross.index
    c = {"gross": gross}
    c["encaje"] = _asof(frames[i["encaje"]], idx).fillna(0)
    c["intl_org"] = _asof(frames[i["intl_org"]], idx).fillna(0)
    c["gold"] = _asof(frames[i["gold"]], idx).fillna(0)
    cny = _asof(frames[i["swap_cny"]], idx).fillna(0)
    rate = _asof(frames[i["cny_usd"]], idx)
    c["swap"] = (cny * 1000 * rate).where(cny > 0, 0.0)
    c["repos"] = _asof(frames[i["repos"]], idx).fillna(0)
    c["repos_short"] = _asof(frames[i["repos_short"]], idx).fillna(0)
    # Treasury dollar deposits: monthly balance sheet, end-of-month stock
    # (dated by month start), in force from that month's end.
    tre = (frames[i["treasury"]] / frames[i["treasury_fx"]] / 1000).dropna()
    tre.index = tre.index + pd.offsets.MonthEnd(0)
    c["treasury"] = _asof(tre, idx).fillna(0)
    imf = frames[i["imf_purchases"]].sort_index().cumsum()
    c["imf_credit"] = _asof(imf, idx).fillna(0)

    deduct = {conv: sum(c[k if not (conv == "imf" and k == "repos") else "repos_short"]
                        for k, _, convs in NET_ROWS if conv in convs) for conv, _ in NET_CONVENTIONS}
    out = {"gross": gross}
    out.update({conv: gross - deduct[conv] for conv, _ in NET_CONVENTIONS})
    df = pd.DataFrame({ind.variants[k].source_id: s for k, s in out.items() if k in ind.variants}).sort_index()

    # Line-by-line construction at the latest balance.
    t = idx.max()
    r = lambda v: round(float(v), 1)
    rows = [{"key": "gross", "label": "Gross reserves", "sign": 1,
             "values": {conv: r(gross[t]) for conv, _ in NET_CONVENTIONS}}]
    for k, label, convs in NET_ROWS:
        vals = {}
        for conv, _ in NET_CONVENTIONS:
            if conv not in convs:
                vals[conv] = None
            else:
                vals[conv] = r(c["repos_short" if (conv == "imf" and k == "repos") else k][t])
        rows.append({"key": k, "label": label, "sign": -1, "values": vals})
    breakdown = {
        "date": t.strftime("%Y-%m-%d"),
        "columns": [{"key": k, "label": lab} for k, lab in NET_CONVENTIONS],
        "rows": rows,
        "total": {conv: r(out[conv][t]) for conv, _ in NET_CONVENTIONS},
        "fx": r(frames[i["fx"]].get(t, float("nan"))) if i.get("fx") else None,
        "cny_usd": float(rate[t]) if pd.notna(rate[t]) else None,
        "swap_cny_bn": float(cny[t]),
        "treasury_as_of": (tre.index[tre.index <= t].max().strftime("%Y-%m-%d") if (tre.index <= t).any() else None),
        "notes": {"imf_repos": "IMF-style deducts only repos with an original maturity of one year or less; "
                               "the BCRA's current repos run longer."},
    }

    # Hand-kept files need a person when the BCRA moves: flag a change in the
    # balance sheet's repo line that the repo file does not explain.
    checks = []
    line = frames.get(i.get("repo_line", ""), pd.Series(dtype=float))
    line = line[line.index >= pd.Timestamp(REPO_CHECK_FROM)]
    if len(line) > 4:
        gap = (line - _asof(frames[i["repos"]], line.index).fillna(0)).dropna()
        if len(gap) > 4:
            move = gap.iloc[-1] - gap.iloc[-5]
            events = frames[i["repos"]].index
            recent = (events > gap.index[-5]).any()
            if abs(move) > REPO_CHECK_USD_M and not recent:
                checks.append(f"the BCRA's repo liability line moved by USD {move:,.0f}m over the last four balances "
                              f"(to {gap.index[-1]:%Y-%m-%d}) with no matching entry in data/manual/bcra_fx_repos.csv")
    for msg in checks:
        print(f"::warning title={ind.id}::{msg}")
    return df, {"breakdown": breakdown, "checks": checks}


def _resolve(ref: str, reg, freq: str) -> pd.Series:
    """'@indicator:variant' -> that indicator's series (derived or fetched), at month ends for
    monthly targets; '@indicator:variant:avg' takes the month's average instead."""
    ind_id, var, *how = ref[1:].split(":")
    other = next(x for x in reg.indicators if x.id == ind_id)
    sid = other.variants[var].source_id
    s = (compute(other, reg)[0][sid] if other.derive else store.as_of(sid)).dropna()
    if freq == "M":
        s = s.resample("MS").mean() if how == ["avg"] else s.resample("MS").last()
    return s


def compute(ind, reg) -> tuple[pd.DataFrame, dict]:
    ids = ind.input_ids()
    frames = {sid: (_resolve(sid, reg, ind.frequency) if sid.startswith("@") else store.as_of(sid)) for sid in ids}
    empty = [sid for sid, s in frames.items() if s.empty]
    if empty:
        raise ValueError(f"no stored data for {empty}")
    if ind.derive["method"] == "splice":
        return splice(ind, frames)
    if ind.derive["method"] == "ratio":
        return ratio(ind, frames)
    if ind.derive["method"] == "tracker":
        return tracker(ind, frames)
    if ind.derive["method"] == "formula":
        return formula(ind, frames)
    if ind.derive["method"] == "expectations":
        return expectations(ind, frames)
    if ind.derive["method"] == "monthly":
        return monthly(ind, frames)
    if ind.derive["method"] == "flows":
        return flows(ind, frames)
    if ind.derive["method"] == "net_reserves":
        return net_reserves(ind, frames)
    return reweight(ind, reg, frames)
