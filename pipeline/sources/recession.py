"""The Monitor's EMAE-based recession probability (Markov switching), with a Bry-Boschan check.

What "recession" means here: a period in which the economy is in the low-growth regime of a
two-regime model of EMAE growth, not two quarters of falling GDP. The model:

  y_t = 100 x (ln EMAE_t - ln EMAE_t-3) / 3      3-month growth of INDEC's seasonally adjusted EMAE,
                                                  % a month, from 2004
  y_t = mu_{S_t} + gamma' d_t + e_t,  e_t ~ N(0, sigma^2)
  S_t in {expansion, recession}, a first-order Markov chain

Two regimes with switching means and a common variance (Hamilton 1989), with one dummy
for each month from March to August 2020 so the pandemic does not form a regime of its own.
A variance that also switches splits the sample by volatility, not by growth; monthly
(rather than 3-month) growth gives one-month "regimes". Ids:

  recession:smoothed   P(recession regime) for every month, estimated on the full sample
  recession:realtime   the track record: for each month since 2008, the filtered probability
                       of that month from the model re-estimated on data up to that month

Checks, written to data/tables/recession/model.json for the card and the methodology page:
  - a Bry-Boschan (Harding-Pagan) chronology of the SA EMAE: peaks and troughs that are
    the highest or lowest point within five months on each side, alternating, phases of at
    least six months and cycles of at least fifteen, none in the last six months;
  - how many months after each dated peak (trough) the real-time probability first rose
    above (fell below) 0.5, and the months above 0.5 outside any dated recession;
  - a cross-check: the same switching model on a common factor of EMAE, industrial
    production (IPI), construction (ISAC) and registered private employment, as known when
    the latest IPI and ISAC are published (EMAE a month behind, employment two).

The real-time track record uses today's revised data, not the data as first published.
The EMAE model is re-estimated only when EMAE changes, the cross-check when any of its inputs does.
"""
from __future__ import annotations

import hashlib
import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from .. import store
from ..registry import ROOT
from . import FetchResult

EMAE = "143.3_NO_PR_2004_A_31"
PANEL = {"emae": EMAE, "ipi": "453.1_SERIE_DESEADA_0_0_24_58", "isac": "33.2_ISAC_SIN_EDAD_0_M_23_56",
         "emp": "151.1_AARIADOTAC_2012_M_26"}
OUTLIERS_3M = pd.date_range("2020-03-01", "2020-08-01", freq="MS")   # months whose 3-month growth includes Mar-Jun 2020
OUTLIERS_1M = pd.date_range("2020-03-01", "2020-06-01", freq="MS")
REALTIME_FROM = pd.Timestamp("2008-01-01")
TABLE_DIR = ROOT / "data" / "tables" / "recession"
MODEL_FILE = TABLE_DIR / "model.json"
VERSION = 1          # bump to force a re-estimation when the method changes

LAST: dict | None = None


# ---------- Markov switching ----------

def growth3(level: pd.Series) -> pd.Series:
    level = level.dropna().asfreq("MS")
    return (100 * (np.log(level) - np.log(level).shift(3)) / 3).dropna()


def fit_ms(y: pd.Series, start_params=None):
    """Two regimes, switching mean, common variance, 2020 dummies. Returns (results, recession regime)."""
    import statsmodels.api as sm
    dums = {f"d{d:%Y%m}": (y.index == d).astype(float) for d in OUTLIERS_3M if d in y.index}
    exog = pd.DataFrame(dums, index=y.index) if dums else None
    mod = sm.tsa.MarkovRegression(y, k_regimes=2, trend="c", exog=exog, switching_exog=False, switching_variance=False)
    best = None
    tries = ([dict(start_params=start_params)] if start_params is not None and len(start_params) == len(mod.start_params) else [])
    tries += [dict(search_reps=20, seed=s) for s in (1, 2)]
    for kw in tries:
        seed = kw.pop("seed", None)
        if seed is not None:
            kw["rng"] = seed            # seeded start-value search: the same data give the same estimates
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                r = mod.fit(disp=False, **kw)
        except Exception:
            continue
        P = r.regime_transition[:, :, 0]
        if min(P[0, 0], P[1, 1]) <= 0.5:          # both regimes must persist
            continue
        if best is None or r.llf > best.llf:
            best = r
    if best is None:
        raise RuntimeError("no persistent two-regime fit")
    k = int(np.argmin([best.params["const[0]"], best.params["const[1]"]]))
    return best, k


def realtime(y: pd.Series, start: pd.Timestamp = REALTIME_FROM) -> pd.Series:
    out, prev = {}, None
    for end in y.loc[start:].index:
        try:
            r, k = fit_ms(y.loc[:end], prev)
        except RuntimeError:
            continue
        prev = r.params.values
        out[end] = float(r.filtered_marginal_probabilities[k].iloc[-1])
    return pd.Series(out, dtype=float)


# ---------- Bry-Boschan ----------

def bry_boschan(y: pd.Series, k: int = 5, min_phase: int = 6, min_cycle: int = 15, edge: int = 6) -> list[tuple[pd.Timestamp, str]]:
    """Peaks ("P") and troughs ("T") of a (log) level, Harding-Pagan monthly rules."""
    y = y.dropna()
    v = y.values
    n = len(v)
    tp = []
    for t in range(k, n - k):
        w = v[t - k:t + k + 1]
        if v[t] == w.max():
            tp.append((t, "P"))
        elif v[t] == w.min():
            tp.append((t, "T"))

    def alternate(tp):
        out = []
        for t, s in tp:
            if out and out[-1][1] == s:
                better = v[t] > v[out[-1][0]] if s == "P" else v[t] < v[out[-1][0]]
                if better:
                    out[-1] = (t, s)
            else:
                out.append((t, s))
        return out

    tp = alternate(tp)
    changed = True
    while changed:
        changed = False
        for i in range(len(tp) - 1):
            if tp[i + 1][0] - tp[i][0] < min_phase:
                tp = alternate(tp[:i] + tp[i + 2:])
                changed = True
                break
        if changed:
            continue
        for i in range(len(tp) - 2):
            if tp[i + 2][0] - tp[i][0] < min_cycle:
                a, b = tp[i], tp[i + 2]
                keep_first = v[a[0]] >= v[b[0]] if a[1] == "P" else v[a[0]] <= v[b[0]]
                tp = alternate(tp[:i + 1] + tp[i + 3:] if keep_first else tp[:i] + tp[i + 2:])
                changed = True
                break
    return [(y.index[t], s) for t, s in tp if edge <= t < n - edge]


def months(a: pd.Timestamp, b: pd.Timestamp) -> int:
    return (b.year - a.year) * 12 + b.month - a.month


def evaluate(rt: pd.Series, turns) -> dict:
    """Months from each dated turn to the first real-time reading on the other side of 0.5."""
    entry, exit_ = [], []
    for d, s in turns:
        if d < rt.index.min() + pd.DateOffset(months=3):
            continue
        w = rt.loc[d - pd.DateOffset(months=3):]
        hit = w[w > 0.5] if s == "P" else w[w < 0.5]
        if len(hit):
            (entry if s == "P" else exit_).append({"turn": d.strftime("%Y-%m"), "months": months(d, hit.index[0])})
    rec = pd.Series(False, index=rt.index)
    peak = None
    for d, s in turns:
        if s == "P":
            peak = d
        elif peak is not None:
            rec[(rec.index > peak - pd.DateOffset(months=3)) & (rec.index <= d + pd.DateOffset(months=7))] = True
            peak = None
    last_dated = max((d for d, _ in turns), default=rt.index.min())
    window = rt.loc[:last_dated]
    false = window[(window > 0.5) & ~rec.loc[window.index]]
    med = lambda xs: float(np.median([x["months"] for x in xs])) if xs else None
    return {"entry": entry, "exit": exit_, "median_entry": med(entry), "median_exit": med(exit_),
            "false_alarm_months": [d.strftime("%Y-%m") for d in false.index]}


# ---------- factor cross-check ----------

def factor_check(panel: pd.DataFrame) -> dict | None:
    """Recession probability from a common factor, as known when the latest IPI/ISAC month is out."""
    import statsmodels.api as sm
    G = 100 * np.log(panel.asfreq("MS")).diff()
    t = G[["ipi", "isac"]].dropna(how="all").index.max()
    X = G.loc[:t].copy()
    X.loc[X.index > t - pd.DateOffset(months=1), "emae"] = np.nan
    X.loc[X.index > t - pd.DateOffset(months=2), "emp"] = np.nan
    X.loc[X.index.isin(OUTLIERS_1M)] = np.nan
    X = X.loc["2004-02-01":]
    Z = (X - X.mean()) / X.std()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        r = sm.tsa.DynamicFactor(Z, k_factors=1, factor_order=2, error_order=1).fit(disp=False, maxiter=500, method="lbfgs")
    f = pd.Series(r.smoothed_state[0], index=Z.index)
    e = X["emae"].dropna()
    b = np.polyfit(f.loc[e.index], e, 1)                      # factor in EMAE growth units (also fixes its sign)
    g3 = (b[0] * f + b[1]).rolling(3).mean().dropna()
    ms, k = fit_ms(g3)
    return {"month": t.strftime("%Y-%m-%d"), "probability": round(float(ms.filtered_marginal_probabilities[k].iloc[-1]), 3),
            "emae_month": X["emae"].dropna().index.max().strftime("%Y-%m-%d")}


# ---------- adapter ----------

def _hash(obj) -> str:
    return hashlib.sha256((str(VERSION) + obj.to_csv(float_format="%.6g")).encode()).hexdigest()[:16]


def _inputs() -> tuple[pd.Series, pd.DataFrame]:
    level = store.as_of(EMAE).dropna()
    panel = pd.DataFrame({k: store.as_of(v).dropna() for k, v in PANEL.items()})
    return level, panel


def build(level: pd.Series) -> dict:
    level = level.dropna().asfreq("MS")
    y = growth3(level)
    r, k = fit_ms(y)
    smoothed = r.smoothed_marginal_probabilities[k]
    rt = realtime(y)
    turns = bry_boschan(np.log(level))
    ev = evaluate(rt, turns)
    # Dated recessions (peak to trough) and, after the last dated turn, a current low-growth run too
    # recent to date: the months since the smoothed probability last rose above 0.5.
    bands, peak = [], None
    for d, s in turns:
        if s == "P":
            peak = d
        elif peak is not None:
            bands.append({"start": (peak + pd.DateOffset(months=1)).strftime("%Y-%m-%d"), "end": d.strftime("%Y-%m-%d")})
            peak = None
    last_turn = turns[-1] if turns else None
    open_phase = None
    if last_turn and last_turn[1] == "T" and smoothed.iloc[-1] > 0.5:
        run = smoothed.loc[last_turn[0]:]
        below = run[run <= 0.5]
        start = run.index[run.index > below.index.max()][0] if len(below) else run.index[0]
        open_phase = start.strftime("%Y-%m-%d")
    elif last_turn and last_turn[1] == "P":
        open_phase = (last_turn[0] + pd.DateOffset(months=1)).strftime("%Y-%m-%d")
    params = {
        "mu_recession": round(float(r.params[f"const[{k}]"]), 3),
        "mu_expansion": round(float(r.params[f"const[{1 - k}]"]), 3),
        "sigma": round(float(np.sqrt(r.params["sigma2"])), 3),
        "duration_recession": round(float(1 / (1 - r.regime_transition[k, k, 0])), 1),
        "duration_expansion": round(float(1 / (1 - r.regime_transition[1 - k, 1 - k, 0])), 1),
        "share_recession": round(float((smoothed > 0.5).mean()), 3),
    }
    return {
        "version": VERSION, "sample": [y.index.min().strftime("%Y-%m-%d"), y.index.max().strftime("%Y-%m-%d")],
        "latest": {"month": y.index.max().strftime("%Y-%m-%d"), "filtered": round(float(r.filtered_marginal_probabilities[k].iloc[-1]), 3),
                   "smoothed": round(float(smoothed.iloc[-1]), 3)},
        "params": params,
        "turns": [{"date": d.strftime("%Y-%m-%d"), "type": "peak" if s == "P" else "trough"} for d, s in turns],
        "bands": bands, "open_phase": open_phase, "evaluation": ev,
        "series": {"smoothed": {d.strftime("%Y-%m-%d"): round(float(v), 4) for d, v in smoothed.items()},
                   "realtime": {d.strftime("%Y-%m-%d"): round(float(v), 4) for d, v in rt.items()}},
    }


def fetch(ids) -> FetchResult:
    global LAST
    empty = pd.DataFrame(columns=["date", "series_id", "value"])
    try:
        level, panel = _inputs()
        cached = json.loads(MODEL_FILE.read_text(encoding="utf-8")) if MODEL_FILE.exists() else {}
        # The EMAE model (about two minutes with the real-time re-estimation) only when EMAE changes;
        # the factor cross-check (seconds) when any of its four series changes.
        h_emae, h_panel = _hash(level), _hash(panel)
        if cached.get("inputs_emae") == h_emae:
            model = cached
        else:
            model = build(level)
            model["inputs_emae"] = h_emae
        if model.get("inputs_panel") != h_panel:
            try:
                model["factor_check"] = factor_check(panel)
            except Exception as exc:
                print(f"recession: factor cross-check failed: {exc}")
                model["factor_check"] = None
            model["inputs_panel"] = h_panel
    except Exception as exc:
        return FetchResult(data=empty, errors={sid: f"recession model: {exc}" for sid in ids})
    LAST = model
    frames, meta, errors = [], {}, {}
    for sid in ids:
        key = sid.split(":", 1)[1]
        s = model["series"].get(key)
        if not s:
            errors[sid] = f"unknown series {sid!r}"
            continue
        frames.append(pd.DataFrame({"date": pd.to_datetime(list(s)), "series_id": sid, "value": [100 * v for v in s.values()]}))
        meta[sid] = {"units": "probability, %"}
    data = pd.concat(frames, ignore_index=True) if frames else empty
    return FetchResult(data=data, meta=meta, errors=errors)


def write_tables(directory) -> list[str]:
    if LAST is None:
        return []
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    p = d / "model.json"
    text = json.dumps(LAST, separators=(",", ":"))
    if p.exists() and p.read_text(encoding="utf-8") == text:
        return []
    p.write_text(text, encoding="utf-8")
    return ["model.json"]
