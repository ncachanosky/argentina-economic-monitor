"""Monthly report draft (Economic Order): data, charts, rule-based text, HTML.

Run after the site export (it reads the card payloads in _site/data):
    python -m pipeline.report [--out DIR] [--cpi YYYY-MM]

Writes DIR/report.html (paste-friendly: h2, p, table, img) and DIR/img/*.png.
The issue is keyed on a CPI month (default: the latest one published). Every other
figure is the latest available on the day the report is built, with its own period.
The text is built from rules and thresholds, not free prose; the author's analysis
and the teaser hook are left as marked slots.
"""
from __future__ import annotations

import argparse
import html
import json
from datetime import date
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

from . import store
from .registry import ROOT
from .social import BG, COPPER, INK, INK2, INK3, RULE, _font

DATA = ROOT / "_site" / "data"
SITE = "https://ncachanosky.github.io/argentina-economic-monitor/"
TEAL = "#4F7C82"          # third line colour, muted, from the EO palette family
FONT = _font()


# ── data ────────────────────────────────────────────────────────────────────

def _payload(card: str) -> dict:
    return json.loads((DATA / f"{card}.json").read_text())


def card(c: str, v: str) -> pd.Series:
    d = _payload(c)
    s = pd.Series(d["variants"][v]["values"], index=pd.to_datetime(d["dates"]), dtype=float)
    return s.dropna()


def real(c: str, v: str) -> pd.Series:
    """A nominal card variant deflated by the card's own deflator (the CPI, as on the site)."""
    d = _payload(c)
    idx = pd.to_datetime(d["dates"])
    x = pd.Series(d["variants"][v]["values"], index=idx, dtype=float)
    f = pd.Series(d["deflator"]["values"], index=idx[:len(d["deflator"]["values"])], dtype=float)
    return (x / f).dropna()


def sectors() -> pd.DataFrame:
    """EMAE sectors at the latest month: y/y change (original series) and its approximate
    contribution to the EMAE's y/y change (2004 weights times the sector's level a year earlier)."""
    d = _payload("emae_sectors")
    w = d["weights"]["values"]
    idx = pd.to_datetime(d["dates"])
    V = {k: pd.Series(v["values"], index=idx, dtype=float) for k, v in d["variants"].items()}
    t = idx[-1]
    t0 = t - pd.DateOffset(months=12)
    base = {k: w[k] * V[k][t0] for k in V}
    tot = sum(base.values())
    rows = [(k, d["variants"][k]["label"], (V[k][t] / V[k][t0] - 1) * 100, base[k] / tot * (V[k][t] / V[k][t0] - 1) * 100)
            for k in V]
    out = pd.DataFrame(rows, columns=["id", "label", "yoy", "contrib"]).set_index("id")
    out.attrs["month"] = t
    return out


def rem(k: str) -> pd.Series:
    return store.as_of(f"rem:{k}").dropna()


def mom(s: pd.Series) -> pd.Series:
    return (s / s.shift(1) - 1) * 100


def yoy(s: pd.Series) -> pd.Series:
    s = s.asfreq("MS")
    return (s / s.shift(12) - 1) * 100


def ago(s: pd.Series, days: int = 30) -> tuple[pd.Timestamp, float]:
    """The last observation on or before `days` before the series' last date."""
    t = s.index[-1] - pd.Timedelta(days=days)
    p = s[s.index <= t]
    return p.index[-1], float(p.iloc[-1])


# ── words ───────────────────────────────────────────────────────────────────

M = lambda d: pd.Timestamp(d).strftime("%B")
MY = lambda d: pd.Timestamp(d).strftime("%b %Y")
DM = lambda d: f"{pd.Timestamp(d).day} {pd.Timestamp(d).strftime('%b')}"
p1 = lambda x: f"{x:.1f}%"
pp = lambda x: f"{abs(x):.1f} pp"


def signed(x: float, d: int = 1, unit: str = "%") -> str:
    return f"{'+' if x > 0 else '−' if x < 0 else ''}{abs(x):.{d}f}{unit}"


def money(x: float) -> str:
    """US$ millions -> 'US$4.5bn' or 'US$473m'."""
    return f"US${x / 1000:,.1f}bn" if abs(x) >= 1000 else f"US${x:,.0f}m"


def versus(actual: float, expected: float) -> str:
    """Compared at one decimal, the precision INDEC and the REM publish."""
    d = round(actual, 1) - round(expected, 1)
    if abs(d) < 0.05:
        return "in line with"
    return f"{pp(d)} {'above' if d > 0 else 'below'}"


def direction(now: float, before: float, up: str, down: str, flat: str, tol: float = 0.05) -> str:
    return flat if abs(now - before) < tol else (up if now > before else down)


def andjoin(xs: list[str], sep: str = ", ") -> str:
    xs = [x for x in xs if x]
    return xs[0] if len(xs) == 1 else f"{sep.join(xs[:-1])}{sep if sep != ', ' else ' '}and {xs[-1]}"


def num(n: int) -> str:
    return ("zero one two three four five six seven eight nine".split()[n]) if 0 <= n < 10 else str(n)


def came(actual: float, expected: float) -> str:
    v = versus(actual, expected)
    return "matched" if v == "in line with" else f"came in {v}"


class Gloss:
    """Argentine terms, glossed on first use in an issue."""
    TERMS = {
        "REM": "the REM (the central bank's monthly survey of professional forecasters)",
        "EMAE": "the EMAE (INDEC's monthly economic activity estimator)",
        "CCL": "the CCL (contado con liquidación, the dollar rate implicit in buying a security in pesos and selling it abroad)",
        "TAMAR": "the TAMAR (the rate banks pay on large peso deposits)",
        "SIPA": "the SIPA (the pension-system registry of formal workers)",
        "band": "the exchange-rate band (the bandas cambiarias, inside which the peso floats)",
    }

    def __init__(self):
        self.used = set()

    def __call__(self, k: str, short: str | None = None) -> str:
        if k in self.used:
            return short or ("the " + k if k in ("REM", "EMAE", "CCL", "TAMAR", "SIPA") else "the band")
        self.used.add(k)
        return self.TERMS[k]


# ── charts ──────────────────────────────────────────────────────────────────

def _fig(title: str, subtitle: str, source: str):
    fig, ax = plt.subplots(figsize=(8, 4.6), dpi=200, facecolor=BG)
    fig.subplots_adjust(left=0.08, right=0.97, top=0.78, bottom=0.14)
    ax.set_facecolor(BG)
    fig.text(0.08, 0.94, title, fontsize=14, color=INK, family=FONT, weight="bold", va="top")
    fig.text(0.08, 0.875, subtitle, fontsize=10, color=INK3, family=FONT, va="top")
    fig.text(0.08, 0.03, f"Source: {source}. Argentina Economic Monitor, Economic Order.",
             fontsize=8, color=INK3, family=FONT)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(INK3)
    ax.grid(axis="y", color=RULE, lw=0.8)
    ax.set_axisbelow(True)
    ax.tick_params(colors=INK2, labelsize=9, length=0)
    for lab in ax.get_xticklabels() + ax.get_yticklabels():
        lab.set_family(FONT)
    return fig, ax


def _legend(ax, **kw):
    kw.pop("loc", None)    # legends always sit in a row above the plot, clear of the lines
    kw.pop("ncol", None)
    lg = ax.legend(frameon=False, fontsize=9, loc="lower left", bbox_to_anchor=(-0.01, 0.99), ncol=4,
                   handlelength=1.6, columnspacing=1.4, borderaxespad=0, **kw)
    for t in lg.get_texts():
        t.set_color(INK2)
        t.set_family(FONT)


def _months(ax, every: int = 3):
    ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=range(1, 13, every)))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b\n%Y"))


_N = [0]


def _save(fig, out: Path, name: str) -> str:
    _N[0] += 1
    name = f"{_N[0]:02d}-{name.split('-', 1)[-1]}"
    p = out / "img" / f"{name}.png"
    p.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(p, facecolor=BG)
    plt.close(fig)
    return f"img/{name}.png"


def _end_label(ax, s: pd.Series, fmt: str, color: str):
    ax.annotate(fmt.format(s.iloc[-1]), (s.index[-1], s.iloc[-1]), xytext=(4, 0), textcoords="offset points",
                fontsize=9, color=color, family=FONT, va="center", weight="bold")


# ── the issue ───────────────────────────────────────────────────────────────

def build(out: Path, cpi_month: str | None = None) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    g = Gloss()
    today = pd.Timestamp(date.today())

    # Prices
    idx = {v: card("cpi", v) for v in ("headline", "core", "regulated", "seasonal")}
    m = {v: mom(s) for v, s in idx.items()}
    y = {v: yoy(s) for v, s in idx.items()}
    t = pd.Timestamp(cpi_month + "-01") if cpi_month else idx["headline"].index[-1]
    prev = t - pd.DateOffset(months=1)
    for v in m:
        m[v], y[v] = m[v][:t], y[v][:t]
    rem_h, rem_c = rem("cpi_m0"), rem("core_m0")
    h, hp, c, r = m["headline"][t], m["headline"][prev], m["core"][t], m["regulated"][t]
    eh, ec = rem_h.get(t), rem_c.get(t)
    month = M(t)

    # Activity
    sa, tr, og = card("emae", "sa"), card("emae", "trend"), card("emae", "original")
    ta = sa.index[-1]
    e_sa, e_sa1, e_yoy = mom(sa)[ta], mom(sa).iloc[-2], yoy(og)[ta]
    e_3m = (sa[-3:].mean() / sa[-6:-3].mean() - 1) * 100            # last three months over the previous three
    win = sa[ta - pd.DateOffset(months=24):]
    pk_d, pk = win.idxmax(), win.max()
    dec = sa.get(pd.Timestamp(ta.year - 1, 12, 1))
    e_ytd = (sa[ta] / dec - 1) * 100 if dec is not None and ta.month > 1 else None
    sec = sectors()

    # Wages and employment
    wr = real("labor_wages_real", "private")
    tw = wr.index[-1]
    w_yoy, w_mom = yoy(wr)[tw], mom(wr)[tw]
    wpub = real("labor_wages_real", "public")
    wpub_yoy = yoy(wpub)[wpub.index[-1]]
    sipa = card("labor_registered", "private")
    ts = sipa.index[-1]
    s_yoy, s_lvl = yoy(sipa)[ts], sipa[ts]
    s_chg_y = sipa[ts] - sipa[ts - pd.DateOffset(months=12)]

    # Exchange rate
    fx = card("fx_official", "wholesale")
    ceil = card("fx_official", "ceiling")
    tf = fx.index[-1]
    d30, fx30 = ago(fx)
    fx_chg = (fx[tf] / fx30 - 1) * 100
    room = card("fx_band_room", "room")
    ccl = card("fx_gaps", "ccl")
    tc = ccl.index[-1]
    _, ccl30 = ago(ccl)
    fx12 = rem("fx12")
    fxm = fx.resample("MS").mean()[:t]
    cpi_i = idx["headline"][:t]
    dec0 = pd.Timestamp(t.year - 1, 12, 1)
    hz = []   # (label, depreciation, inflation, real change of the dollar)
    for lab, b in ((f"{month}", prev), (f"Since December", dec0), ("Last twelve months", t - pd.DateOffset(months=12))):
        dep = (fxm[t] / fxm[b] - 1) * 100
        inf = (cpi_i[t] / cpi_i[b] - 1) * 100
        hz.append((lab, dep, inf, ((1 + dep / 100) / (1 + inf / 100) - 1) * 100))

    # Reserves
    net = card("net_reserves", "standard")
    gross = card("reserves", "gross")
    tn = net.index[-1]
    dn30, net30 = ago(net)
    tg = gross.index[-1]
    _, gross30 = ago(gross)
    buy = card("fx_bcra_purchases", "purchases")
    tb = buy.index[-1]
    buy_ytd = buy[buy.index.year == tb.year].sum()

    # Country risk
    embi = card("country_risk", "embi")
    te = embi.index[-1]
    _, embi30 = ago(embi)
    em_max = embi[embi.index > te - pd.Timedelta(days=30)]
    em_peak_d, em_peak = em_max.idxmax(), em_max.max()

    # Fiscal
    fp, ff = card("fiscal_balance", "primary"), card("fiscal_balance", "financial")
    fa = card("fiscal_balance_adjusted", "adjusted")
    tfi = fp.index[-1]
    fp12y = fp[tfi - pd.DateOffset(months=12)]

    # Rates and expectations
    rt = card("policy_rate_real", "tamar")
    tr_ = rt.index[-1]
    cpi12 = rem("cpi12")
    tx = cpi12.index[-1]

    charts = {}

    # ── charts
    W = 24
    # 1. CPI vs REM median (free)
    hh = m["headline"][-W:]
    fig, ax = _fig("Monthly inflation and what forecasters expected",
                   "Headline CPI, % change on the previous month; dots: REM median for the same month",
                   "INDEC, BCRA (REM)")
    ax.bar(hh.index, hh.values, width=22, color=INK, label="Headline CPI")
    rr = rem_h.reindex(hh.index).dropna()
    ax.scatter(rr.index, rr.values, s=28, color=COPPER, zorder=3, label="REM median")
    for d_, v_ in hh[-1:].items():
        ax.annotate(f"{v_:.1f}%", (d_, max(v_, rr.get(d_, v_))), xytext=(0, 7), textcoords="offset points", ha="center",
                    fontsize=9, color=INK, family=FONT, weight="bold")
    _months(ax); _legend(ax, loc="upper right")
    charts["cpi_rem"] = _save(fig, out, "1-cpi-rem")

    # 2. headline, core, regulated
    fig, ax = _fig("Headline, core and regulated prices",
                   "% change on the previous month", "INDEC")
    for v, col, lab in (("headline", INK, "Headline"), ("core", COPPER, "Core"), ("regulated", TEAL, "Regulated")):
        s = m[v][-W:]
        ax.plot(s.index, s.values, color=col, lw=2, label=lab)
    _months(ax); _legend(ax, loc="upper right")
    charts["prices"] = _save(fig, out, "2-prices")

    # 3. EMAE
    since = ta - pd.DateOffset(months=36)
    fig, ax = _fig("Economic activity", "EMAE, seasonally adjusted, index 2004 = 100", "INDEC")
    ax.plot(sa[since:].index, sa[since:].values, color=INK, lw=2)
    ax.scatter([ta], [sa[ta]], s=24, color=COPPER, zorder=3)
    _end_label(ax, sa[since:], "{:.1f}", COPPER)
    _months(ax, 6)
    charts["activity"] = _save(fig, out, "3-activity")

    # 3b. Sectors: contributions to the y/y change, y/y as labels
    sc = sec.drop(index="net_taxes", errors="ignore").sort_values("contrib")
    fig, ax = _fig(f"Which sectors moved activity, {MY(sec.attrs['month'])}",
                   "Approximate contribution to the EMAE's change on a year earlier, percentage points; labels: sector's own change",
                   "INDEC; monitor's calculations")
    ax.grid(axis="y", visible=False); ax.grid(axis="x", color=RULE, lw=0.8)
    ax.barh(sc["label"], sc["contrib"], color=[COPPER if v > 0 else INK for v in sc["contrib"]], height=0.7)
    ax.axvline(0, color=INK3, lw=0.8)
    lo, hi = sc["contrib"].min(), sc["contrib"].max()
    pad = (hi - lo) * 0.02
    for i, (c_, y_) in enumerate(zip(sc["contrib"], sc["yoy"])):
        ax.text(c_ + (pad if c_ >= 0 else -pad), i, f"{signed(y_)}", va="center", ha="left" if c_ >= 0 else "right",
                fontsize=8, color=INK2, family=FONT)
    ax.set_xlim(lo - (hi - lo) * 0.18, hi + (hi - lo) * 0.18)
    ax.tick_params(axis="y", labelsize=8)
    fig.subplots_adjust(left=0.30)
    charts["sectors"] = _save(fig, out, "3b-sectors")

    # 4. FX with band and CCL
    start = pd.Timestamp("2025-04-14")
    f = fx[start:]
    cc = (fx * (1 + ccl / 100)).dropna()[start:]
    cl = ceil[start:]
    fig, ax = _fig("The peso inside the band", "Pesos per US dollar, daily",
                   "BCRA; market quotes")
    ax.plot(cl.index, cl.values, color=INK3, lw=1.2, ls="--", label="Band ceiling")
    ax.plot(cc.index, cc.values, color=COPPER, lw=1.4, label="CCL")
    ax.plot(f.index, f.values, color=INK, lw=2, label="Official (wholesale)")
    _months(ax); _legend(ax, ncol=3)
    charts["fx"] = _save(fig, out, "4-fx")

    fig, ax = _fig("Depreciation against inflation",
                   f"Official rate (monthly average) and CPI, % change to {month}", "BCRA, INDEC; monitor's calculations")
    import numpy as np
    xs = np.arange(len(hz))
    ax.bar(xs - 0.19, [h_[1] for h_ in hz], width=0.36, color=INK, label="Peso depreciation")
    ax.bar(xs + 0.19, [h_[2] for h_ in hz], width=0.36, color=COPPER, label="CPI inflation")
    for x_, h_ in zip(xs, hz):
        for dx, v_ in ((-0.19, h_[1]), (0.19, h_[2])):
            ax.annotate(f"{v_:.1f}%", (x_ + dx, v_), xytext=(0, 3), textcoords="offset points", ha="center",
                        fontsize=9, color=INK2, family=FONT)
    ax.set_xticks(xs, [h_[0] for h_ in hz])
    _legend(ax)
    charts["fx_real"] = _save(fig, out, "4b-fx-real")

    # 5. Reserves
    start = pd.Timestamp("2024-01-01")
    fig, ax = _fig("Gross and net reserves", "US$ billions, daily", "BCRA; monitor's calculations")
    ax.plot(gross[start:].index, gross[start:].values / 1000, color=INK, lw=2, label="Gross")
    ax.plot(net[start:].index, net[start:].values / 1000, color=COPPER, lw=2, label="Net (standard definition)")
    ax.axhline(0, color=INK3, lw=0.8)
    _months(ax, 6); _legend(ax, ncol=2)
    charts["reserves"] = _save(fig, out, "5-reserves")

    # 6. Fiscal
    start = fa.index[0]
    fig, ax = _fig("The fiscal balance, three ways",
                   "% of GDP, last twelve months", "Ministry of Economy, BCRA; monitor's calculations")
    ax.plot(fp[start:].index, fp[start:].values, color=TEAL, lw=2, label="Primary")
    ax.plot(ff[start:].index, ff[start:].values, color=INK, lw=2, label="Financial (official, cash)")
    ax.plot(fa.index, fa.values, color=COPPER, lw=2, label="Financial, accrual (incl. capitalized interest)")
    ax.axhline(0, color=INK3, lw=0.8)
    _months(ax, 6); _legend(ax, ncol=1, loc="lower left")
    charts["fiscal"] = _save(fig, out, "6-fiscal")

    # 7. Real rates
    start = pd.Timestamp("2024-01-01")
    fig, ax = _fig("The real interest rate", "TAMAR deflated by REM expected inflation, % per year",
                   "BCRA (TAMAR, REM); monitor's calculations")
    s = rt[start:]
    ax.bar(s.index, s.values, width=22, color=[COPPER if v >= 0 else INK3 for v in s.values])
    ax.axhline(0, color=INK3, lw=0.8)
    _months(ax, 6)
    charts["rates"] = _save(fig, out, "7-rates")

    # 8. Real wages
    base = pd.Timestamp("2023-11-01")
    fig, ax = _fig("Real wages since November 2023", "Index, November 2023 = 100", "INDEC")
    for v, col, lab in (("private", INK, "Registered private"), ("public", COPPER, "Public")):
        s = real("labor_wages_real", v)
        s = (s / s[base] * 100)[base - pd.DateOffset(months=12):]
        ax.plot(s.index, s.values, color=col, lw=2, label=lab)
    ax.axhline(100, color=INK3, lw=0.8)
    _months(ax, 6); _legend(ax, ncol=3, loc="lower right")
    charts["labor"] = _save(fig, out, "8-labor")

    # 9. Surprises
    sur = (m["headline"] - rem_h).dropna()[-W:]
    fig, ax = _fig("Inflation surprises", "Headline CPI minus the REM median for the same month, percentage points",
                   "INDEC, BCRA (REM); monitor's calculations")
    ax.bar(sur.index, sur.values, width=22, color=[COPPER if v > 0 else INK for v in sur.values])
    ax.axhline(0, color=INK3, lw=0.8)
    _months(ax)
    charts["surprises"] = _save(fig, out, "9-surprises")

    # ── table
    rows = [
        ("CPI, headline", MY(t), f"{p1(h)} m/m", f"{p1(hp)} in {M(prev)}",
         f"{p1(y['headline'][t])} y/y; REM median {p1(eh)}" if eh is not None else f"{p1(y['headline'][t])} y/y"),
        ("CPI, core", MY(t), f"{p1(c)} m/m", f"{p1(m['core'][prev])} in {M(prev)}",
         f"{p1(y['core'][t])} y/y; REM median {p1(ec)}" if ec is not None else ""),
        ("CPI, regulated", MY(t), f"{p1(r)} m/m", f"{p1(m['regulated'][prev])} in {M(prev)}", f"{p1(y['regulated'][t])} y/y"),
        ("EMAE (activity)", MY(ta), f"{signed(e_sa)} m/m s.a.", f"{signed(e_sa1)} in {M(ta - pd.DateOffset(months=1))}",
         f"{signed(e_yoy)} y/y; {signed(e_3m)} 3m/3m"),
        ("Real registered private wages", MY(tw), f"{signed(w_yoy)} y/y", f"{signed(w_mom)} m/m", ""),
        ("Official exchange rate", DM(tf), f"${fx[tf]:,.1f}", f"{signed(fx_chg)} in 30 days",
         f"{p1(room[room.index[-1]])} room to the band ceiling; 12m: {signed(hz[2][1])} vs CPI {p1(hz[2][2])}"),
        ("CCL gap", DM(tc), p1(ccl[tc]), f"{p1(ccl30)} a month earlier", ""),
        ("Net reserves", DM(tn), money(net[tn]), f"{money(net30)} on {DM(dn30)}",
         f"BCRA purchases: {money(buy[tb])} in {M(tb)}"),
        ("Country risk (EMBI)", DM(te), f"{embi[te]:.0f} bp", f"{embi30:.0f} bp a month earlier", ""),
    ]

    # ── text
    # Comparisons are made at the precision INDEC and the REM publish (one decimal).
    T = {}
    hs, ehs = round(h, 1), (round(eh, 1) if eh is not None else None)
    T["headline"] = (f"Inflation {'slows' if round(h, 1) < round(hp, 1) else 'rises' if round(h, 1) > round(hp, 1) else 'holds'} "
                     f"to {p1(h)} in {month}"
                     + (f", {'below' if hs < ehs else 'above' if hs > ehs else 'in line with'} the REM median" if eh is not None else ""))
    T["dek"] = f"The month in numbers: data as of {today.day} {today.strftime('%B %Y')}."

    # Prices
    gap_cr = round(c, 1) - round(h, 1)
    T["prices"] = [
        f"Headline CPI rose {p1(h)} in {month}, "
        + (f"{'down' if h < hp else 'up'} from {p1(hp)} in {M(prev)}" if round(h, 1) != round(hp, 1) else f"the same as in {M(prev)}")
        + (f", and {versus(h, eh)} the {p1(eh)} median forecast in {g('REM')}." if eh is not None else ".")
        + f" Twelve-month inflation {direction(y['headline'][t], y['headline'][prev], 'rose', 'eased', 'held')} to {p1(y['headline'][t])}.",
        f"Core inflation, which excludes seasonal and regulated items, was {p1(c)}"
        + (f", {versus(c, ec)} the REM median of {p1(ec)}" if ec is not None else "")
        + (f". It ran {pp(gap_cr)} {'above' if gap_cr > 0 else 'below'} the headline: " if abs(gap_cr) >= 0.05 else ". ")
        + (f"a {p1(abs(m['seasonal'][t]))} fall in seasonal prices held the headline below the core." if gap_cr > 0 and m["seasonal"][t] < 0 else
           "underlying inflation is running above the headline." if gap_cr > 0 else
           "seasonal and regulated items pushed the headline above the core." if gap_cr < 0 else
           "the headline is a fair read of underlying inflation."),
        f"Regulated prices rose {p1(r)} ({p1(y['regulated'][t])} over twelve months), "
        + ("still ahead of the headline as administered prices catch up." if round(r, 1) > round(h, 1) else
           "below the headline." if round(r, 1) < round(h, 1) else "in line with the headline.")
        + ("" if gap_cr > 0 and m["seasonal"][t] < 0 else
           f" Seasonal items {'fell' if m['seasonal'][t] < 0 else 'rose'} {p1(abs(m['seasonal'][t]))}."),
    ]

    # Activity
    named = sec.drop(index="net_taxes", errors="ignore")
    up_, dn_ = named[named["yoy"] > 0].sort_values("yoy", ascending=False), named[named["yoy"] < 0].sort_values("yoy")
    lst = lambda df_: andjoin([f"{r_.label.lower()} ({signed(r_.yoy)})" for r_ in df_.head(3).itertuples()])
    drag, push = named.sort_values("contrib").head(2), named.sort_values("contrib", ascending=False).head(2)
    pts = lambda df_: " and ".join(f"{r_.label.lower()} ({abs(r_.contrib):.1f} pp)" for r_ in df_.itertuples())
    wild = named[named["yoy"].abs() > 50]
    T["activity"] = [
        f"Economic activity, as measured by {g('EMAE')}, {'fell' if e_sa < 0 else 'rose'} {p1(abs(e_sa))} in {M(ta)}, "
        f"seasonally adjusted, after {signed(e_sa1)} in {M(ta - pd.DateOffset(months=1))}. "
        f"The last three months were {p1(abs(e_3m))} {'below' if e_3m < 0 else 'above'} the previous three"
        + (f", and the index is {p1(abs((sa[ta] / pk - 1) * 100))} below its {MY(pk_d)} peak" if pk_d != ta else ", and the index is at a two-year high")
        + (f" ({signed(e_ytd)} since December)." if e_ytd is not None else ".")
        + f" Activity was {p1(abs(e_yoy))} {'higher' if e_yoy > 0 else 'lower'} than a year earlier.",
        f"Sector data come without seasonal adjustment, so we compare each with the same month a year earlier. "
        f"{num(len(dn_)).capitalize()} of {len(named)} sectors were below their {MY(ta - pd.DateOffset(months=12))} level. "
        + (f"The largest declines were in {lst(dn_)}; " if len(dn_) else "")
        + (f"the largest increases, in {lst(up_)}." if len(up_) else "no sector grew."),
        f"Weighted by size, {pts(drag)} subtracted the most from the year-on-year change; "
        f"{pts(push)} added the most."
        + (f" {' and '.join(wild['label'])} {'moves' if len(wild) == 1 else 'move'} by large amounts from year to year "
           f"on a small weight; read {'it' if len(wild) == 1 else 'them'} with care." if len(wild) else ""),
    ]

    # Exchange rate
    T["fx"] = [
        f"The official wholesale rate closed at ${fx[tf]:,.1f} per dollar on {DM(tf)}, {signed(fx_chg)} over thirty days. "
        f"The peso floats inside an exchange-rate band set by the BCRA (the bandas cambiarias); the rate sits "
        f"{p1(room[room.index[-1]])} below the ceiling of ${ceil[ceil.index[-1]]:,.1f}.",
        f"The gap with {g('CCL')} was {p1(ccl[tc])}, against {p1(ccl30)} a month earlier"
        + ("; the parallel markets are pricing a little more depreciation." if ccl[tc] > ccl30 + 0.5 else
           "; the gap narrowed." if ccl[tc] < ccl30 - 0.5 else "; little change.")
        + f" The REM median expects ${fx12[fx12.index[-1]]:,.0f} per dollar twelve months ahead ({MY(fx12.index[-1])} survey), "
        f"{p1(abs((fx12[fx12.index[-1]] / fx[tf] - 1) * 100))} {'above' if fx12[fx12.index[-1]] > fx[tf] else 'below'} today's rate.",
        "Against prices, using monthly averages to match the CPI: "
        + andjoin([f"{w_}, the peso {'depreciated' if d_ >= 0 else 'appreciated'} {p1(abs(d_))} while prices rose {p1(i__)}"
                   for w_, (_, d_, i__, _) in zip((f"in {month}", "since December", "over the last twelve months"), hz)], "; ")
        + ". " + (f"The peso has appreciated in real terms: by {andjoin([p1(abs(h_[3])) for h_ in hz])} over the three horizons."
                  if all(h_[3] < 0 for h_ in hz) else
                  f"The peso has depreciated in real terms: by {andjoin([p1(h_[3]) for h_ in hz])} over the three horizons."
                  if all(h_[3] > 0 for h_ in hz) else
                  "In real terms, the peso " + andjoin([f"{'appreciated' if h_[3] < 0 else 'depreciated'} {p1(abs(h_[3]))} {w_}"
                                                        for h_, w_ in zip(hz, ("in the month", "since December", "over twelve months"))]) + "."),
    ]

    # Reserves
    T["reserves"] = [
        f"Net reserves (standard definition) stood at {money(net[tn])} on {DM(tn)}, "
        f"{'down' if net[tn] < net30 else 'up'} {money(abs(net[tn] - net30))} from {DM(dn30)}. "
        f"Gross reserves were {money(gross[tg])}.",
        f"The BCRA bought {money(buy[tb])} in the foreign exchange market in {M(tb)}"
        + (f", after {money(buy.iloc[-2])} in {M(buy.index[-2])}" if len(buy) > 1 else "")
        + f"; {money(buy_ytd)} so far in {tb.year}.",
        f"Country risk (JP Morgan's EMBI spread) was {embi[te]:.0f} basis points on {DM(te)}, "
        f"{'up' if embi[te] > embi30 else 'down'} from {embi30:.0f} a month earlier"
        + (f", after peaking at {em_peak:.0f} on {DM(em_peak_d)}." if em_peak > embi[te] + 15 else "."),
    ]

    # Fiscal
    bal = lambda x: f"a {'surplus' if x >= 0 else 'deficit'} of {abs(x):.2f}% of GDP"
    T["fiscal"] = [
        f"Over the twelve months to {M(tfi)}, the primary balance was {bal(fp[tfi])} ({fp12y:.2f}% a year earlier) "
        f"and the official financial balance {bal(ff[tfi])}.",
        f"Counting interest as it accrues, including the interest capitalized on peso debt, the financial balance was "
        f"{bal(fa[tfi])}. The difference with the cash figure is interest the Treasury owes but has not yet paid.",
    ]

    # Rates and money
    T["rates"] = [
        f"{(lambda x: x[0].upper() + x[1:])(g('TAMAR'))} was {p1(rt[tr_])} a year in real terms in {M(tr_)}, deflated by expected inflation. "
        f"Forecasters in the REM expect {p1(cpi12[tx])} inflation over the next twelve months ({MY(tx)} survey), "
        f"well below the {p1(y['headline'][t])} of the last twelve.",
    ]

    # Labor
    T["labor"] = [
        f"Real registered private wages were {p1(abs(w_yoy))} {'higher' if w_yoy > 0 else 'lower'} than a year earlier in {M(tw)} "
        f"({signed(w_mom)} in the month); real public-sector wages, {signed(wpub_yoy)}.",
        f"Registered private employment in {g('SIPA')} was {s_lvl / 1000:,.2f} million workers in {M(ts)}, "
        f"{abs(s_chg_y):,.0f} thousand {'more' if s_chg_y > 0 else 'fewer'} than a year earlier ({signed(s_yoy)}).",
    ]

    # Surprises
    sur_r = (m["headline"].round(1) - rem_h.round(1)).dropna()[-12:]
    n_below, n_above = int((sur_r < 0).sum()), int((sur_r > 0).sum())
    T["surprises"] = [
        (f"Headline inflation {came(h, eh)} the REM median; core inflation {came(c, ec)} its median. ")
        if eh is not None and ec is not None else "",
        f"Over the last twelve months, headline inflation came in below the median {n_below} times, above it {n_above} times, "
        f"and on it {len(sur_r) - n_below - n_above} times (at one decimal)"
        + ("; forecasters have tended to underpredict inflation." if n_above >= n_below + 4 else
           "; forecasters have tended to overpredict inflation." if n_below >= n_above + 4 else
           "; no consistent bias."),
    ]

    # Calendar
    from . import export, registry
    reg = registry.load()
    up = export.updates(reg, days_back=35, days_ahead=50)
    cpis = [a["date"] for a in up["ahead"] if any(i["id"] == "cpi" for i in a["indicators"])]
    end = next((d for d in cpis if pd.Timestamp(d) >= today + pd.Timedelta(days=20)), cpis[-1] if cpis else None)
    cal = []
    for a in up["ahead"]:
        if not a["indicators"] or (end and a["date"] > end):
            continue
        per = pd.Timestamp(a["period"] + "-01")
        per_s = (f"Q{(per.month - 1) // 3 + 1} {per.year}" if a["frequency"] == "Q" else MY(per))
        cal.append((DM(a["date"]), a["indicators"][0]["title"], per_s))

    # Quarterly releases seen since the last issue
    quarterly = [r_ for r_ in up["recent"] if r_["frequency"] == "Q"]

    data = dict(t=t, rows=rows, charts=charts, text=T, calendar=cal, quarterly=quarterly, today=today)
    (out / "report.html").write_text(render(data))
    return data


# ── html ────────────────────────────────────────────────────────────────────

CSS = """
:root{--bg:#FBFAF7;--fg:#36454F;--muted:#6E7A84;--rule:#E3E0D9;--accent:#B87333;--slot:#FFF4E6;--slotfg:#7A4A1E}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#1D2328;--fg:#E7E4DE;--muted:#9AA5AE;--rule:#38424A;--accent:#D49563;--slot:#3A2D20;--slotfg:#F2C9A0;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#1D2328;--fg:#E7E4DE;--muted:#9AA5AE;--rule:#38424A;--accent:#D49563;--slot:#3A2D20;--slotfg:#F2C9A0;color-scheme:dark}
body{background:var(--bg);color:var(--fg);font:17px/1.6 Georgia,'Times New Roman',serif;margin:0}
main{max-width:720px;margin:0 auto;padding-inline:16px;padding-block:32px 64px}
.kicker{font:600 12px/1.3 system-ui,sans-serif;letter-spacing:.12em;text-transform:uppercase;color:var(--accent)}
h1{font-size:30px;line-height:1.2;margin:.3em 0 .2em;text-wrap:balance}
.dek{color:var(--muted);margin:0 0 1.5em}
h2{font-size:22px;margin:2em 0 .4em;text-wrap:balance}
p{margin:0 0 1em}
img{display:block;max-width:100%;height:auto;margin:1.2em 0;border:1px solid var(--rule);border-radius:4px}
.tbl{overflow-x:auto;margin:1em 0}
table{border-collapse:collapse;width:100%;font:14px/1.4 system-ui,sans-serif;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--rule);vertical-align:top}
th{font-weight:600;color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.05em}
td:first-child{font-weight:600}
.slot{background:var(--slot);color:var(--slotfg);border-radius:4px;padding:12px 14px;font:14px/1.5 system-ui,sans-serif;margin:1em 0}
.paywall{border:0;border-top:2px dashed var(--accent);margin:2.5em 0 .5em}
.paylabel{text-align:center;font:600 12px system-ui,sans-serif;letter-spacing:.1em;text-transform:uppercase;color:var(--accent);margin-bottom:2em}
.draft{font:12px/1.5 system-ui,sans-serif;color:var(--muted);border:1px solid var(--rule);border-radius:4px;padding:10px 12px;margin-bottom:2em}
.note{font:13px/1.5 system-ui,sans-serif;color:var(--muted)}
"""


def render(d: dict) -> str:
    e = html.escape
    t, T, C = d["t"], d["text"], d["charts"]
    P = lambda xs: "".join(f"<p>{e(x)}</p>" for x in xs if x)
    img = lambda k, alt: f'<img src="{C[k]}" alt="{e(alt)}">'
    rows = "".join(f"<tr><td>{e(a)}</td><td>{e(b)}</td><td>{e(c)}</td><td>{e(dd)}</td><td>{e(f)}</td></tr>"
                   for a, b, c, dd, f in d["rows"])
    cal = "".join(f"<tr><td>{e(a)}</td><td>{e(b)}</td><td>{e(c)}</td></tr>" for a, b, c in d["calendar"])
    q = d["quarterly"]
    qsec = ("<h2>Quarterly releases</h2>" + P([f"{x['title']} ({x['period'][:7]}) was published on {x['seen']}." for x in q])) if q else ""
    return f"""<title>Argentina Monthly Report Draft</title>
<style>{CSS}</style>
<main>
<div class="draft">Draft built {d['today'].strftime('%d %b %Y')} from the Monitor's data. Rule-based text: edit freely before pasting into Substack.
Charts are in the <code>img/</code> folder, numbered in order.</div>
<div class="kicker">Argentina Economic Monitor · Monthly report · {t.strftime('%B %Y')} CPI</div>
<h1>{e(T['headline'])}</h1>
<p class="dek">{e(T['dek'])}</p>

<h2>The month in numbers</h2>
<div class="tbl"><table><thead><tr><th>Indicator</th><th>Period</th><th>Latest</th><th>Previous</th><th>Context</th></tr></thead>
<tbody>{rows}</tbody></table></div>
{img('cpi_rem', 'Monthly CPI inflation with REM median expectations')}

<h2>This month's analysis</h2>
<div class="slot">[Teaser: one-line hook for the analysis below the paywall.]</div>

<hr class="paywall"><div class="paylabel">Paywall break: insert Substack's paywall here</div>

<h2>Prices</h2>{P(T['prices'])}{img('prices', 'Headline, core and regulated CPI, monthly change')}
<h2>Activity</h2>{P(T['activity'][:1])}{img('activity', 'EMAE seasonally adjusted')}{P(T['activity'][1:])}{img('sectors', 'EMAE sectors, contributions to the year-on-year change')}
<h2>Exchange rate</h2>{P(T['fx'][:2])}{img('fx', 'Official exchange rate, band ceiling and CCL')}{P(T['fx'][2:])}{img('fx_real', 'Peso depreciation against CPI inflation')}
<h2>Reserves and the BCRA</h2>{P(T['reserves'])}{img('reserves', 'Gross and net reserves')}
<h2>Fiscal</h2>{P(T['fiscal'])}{img('fiscal', 'Primary, financial and accrual fiscal balances')}
<h2>Rates and money</h2>{P(T['rates'])}{img('rates', 'Real TAMAR')}
<h2>Labor</h2>{P(T['labor'])}{img('labor', 'Real wages index')}
<h2>Surprises</h2>{P(T['surprises'])}{img('surprises', 'CPI minus REM median')}
{qsec}
<h2>Analysis</h2>
<div class="slot">[Your analysis.]</div>

<h2>Next month's calendar</h2>
<div class="tbl"><table><thead><tr><th>Date</th><th>Release</th><th>Period</th></tr></thead><tbody>{cal}</tbody></table></div>
<p class="note">All series, with their sources and methods, are on the <a href="{SITE}">Argentina Economic Monitor</a>.</p>
</main>
"""


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "_report"))
    ap.add_argument("--cpi", default=None, help="CPI month of the issue, YYYY-MM (default: latest)")
    a = ap.parse_args()
    d = build(Path(a.out), a.cpi)
    print(Path(a.out) / "report.html")
