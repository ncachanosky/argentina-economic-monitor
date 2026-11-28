"""Summary statistics of a card, as the site's summary line shows them (site/js/app.js, summaryStats),
and the two-quarter rule on the EMAE (recessionSpells / spellStats there). English only.

The monthly report uses these so its numbers match the site's for the same view. The rules, in short:
  flow   amounts per period that add up: total over the range shown (over five years: the last
         twelve months against the twelve before), the latest against a year earlier, this year's
         average so far against the same months of last year
  rate   percentages and changes: the latest, a year earlier (or the 12-month change of a monthly
         rate), the average of the current presidency
  level  prices, indices, stocks: the latest, the change since the start of the range shown, the
         change since the current president took office (bounded scales: changes in points)
Keep this file and summaryStats in app.js in step; tests/test_summary.py pins the rules.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

import pandas as pd

RANGES = {"2Y": 2, "5Y": 5, "10Y": 10, "25Y": 25, "50Y": 50, "Max": None}


@dataclass
class Item:
    big: str
    small: str

    def text(self) -> str:
        return f"{self.big} {self.small}"


# ── dates ──────────────────────────────────────────────────────────────────

def _mi(iso) -> int:
    t = pd.Timestamp(iso)
    return t.year * 12 + t.month - 1


def add_months(iso, k: int) -> pd.Timestamp:
    m = _mi(iso) + k
    return pd.Timestamp(m // 12, m % 12 + 1, 1)


def range_from_default(payload: dict) -> str:
    d = payload.get("default") or {}
    if d.get("range"):
        return d["range"]
    s = d.get("start")
    if not s:
        return "10Y"
    yrs = (_mi(payload["last_obs"]) - _mi(s if len(s) > 7 else s + "-01")) / 12
    return "2Y" if yrs <= 2.5 else "5Y" if yrs <= 6 else "10Y" if yrs <= 12 else "Max"


def view_start(payload: dict, rng: str | None = None) -> pd.Timestamp | None:
    rng = rng or range_from_default(payload)
    m = re.match(r"^Since (\d{4})$", rng or "")
    if m:
        return pd.Timestamp(int(m.group(1)), 1, 1)
    yrs = RANGES.get(rng)
    if not yrs:
        return None
    t = pd.Timestamp(payload["last_obs"])
    return pd.Timestamp(t.year - yrs, t.month, 1)


def term_window(p: dict, freq: str) -> dict:
    """First, base and last period of a presidential term (as termWindow in app.js)."""
    start = pd.Timestamp(p["start"])
    end = pd.Timestamp(p["end"]) if p.get("end") else None
    if freq == "D":
        return {"first": start, "base": start - pd.Timedelta(days=1), "last": end - pd.Timedelta(days=1) if end is not None else None}
    if freq == "Q":
        q0 = pd.Timestamp(start.year, (start.month - 1) // 3 * 3 + 1, 1)
        first = q0 if start == q0 else add_months(q0, 3)
        return {"first": first, "base": add_months(first, -3), "last": pd.Timestamp(end.year, (end.month - 1) // 3 * 3 + 1, 1) if end is not None else None}
    first = pd.Timestamp(start.year, start.month, 1) if start.day == 1 else add_months(start, 1)
    return {"first": first, "base": add_months(first, -1), "last": pd.Timestamp(end.year, end.month, 1) if end is not None else None}


# ── formatting ─────────────────────────────────────────────────────────────

def nf(v: float, d: int) -> str:
    return f"{v:,.{d}f}"


def signed(v: float, d: int, sfx: str = "") -> str:
    return ("+" if v > 0 else "−" if v < 0 else "") + nf(abs(v), d) + sfx


def money(v: float, cur: str = "usd") -> str:
    a, s, c = abs(v), "−" if v < 0 else "", "US$ " if cur == "usd" else "$ "
    if a >= 1e6:
        return f"{s}{c}{nf(a / 1e6, 1)} tn"
    if a >= 1e4:
        return f"{s}{c}{nf(a / 1e3, 1)} bn"
    return f"{s}{c}{nf(a, 0)} m"


def _auto(v: float) -> int:
    return 0 if abs(v) >= 100 else 2 if 0 < abs(v) < 1 else 1


def value_fmt(payload: dict, units: str, change: bool, ref: float | None):
    u = (units or "").lower()
    if change:
        return lambda v: signed(v, 1, "%") if v != 0 else "0.0%"
    bounded = bool(re.match(r"^(rating|index, 0|gini|hhi|times|years)", u))
    hl = payload.get("headline") or {}
    a = abs(ref or 0)
    d = hl["digits"] if hl.get("digits") is not None and hl.get("transform") != "yoy" else (
        None if ref is None else (2 if a < 10 else 1 if a < 100 else 0) if bounded else (0 if a >= 100 else 1 if a >= 10 else 2))
    if u.startswith("millions of us dollars"):
        return lambda v: money(v, "usd")
    if u.startswith("billions of us dollars"):
        return lambda v: money(v * 1000, "usd")
    if u.startswith("pesos per month"):
        return lambda v: f"$ {nf(v, 0)}"
    if u.startswith("millions of pesos"):
        return lambda v: money(v, "ars")
    if u.startswith("us dollars"):
        return lambda v: f"US$ {nf(v, d if d is not None else _auto(v))}"
    if u.startswith("basis points"):
        return lambda v: f"{nf(v, 0)} bp"
    if u.startswith("pesos per us dollar"):
        return lambda v: f"$ {nf(v, d if d is not None else (0 if v >= 100 else 2))}"
    if re.match(r"^(%|percentage points|probability)", u):
        return lambda v: ("−" if v < 0 else "") + nf(abs(v), 1) + (" pp" if u.startswith("percentage points") else "%")
    return lambda v: ("−" if v < 0 else "") + nf(abs(v), d if d is not None else _auto(v))


def stats_kind(payload: dict, units: str, change: bool, tkey: str) -> str | None:
    u = (units or "").lower()
    if change:
        return "rate"
    if tkey == "share":
        return None
    if re.match(r"^(%|percentage points|probability)", u) or payload.get("measure") == "rate":
        return "rate"
    if payload.get("measure") == "flow" and re.match(r"^(millions|billions) of (us dollars|pesos)(, .* prices)?$", u):
        return "flow"
    if re.match(r"^(rating|index, 0|gini|hhi|times|years)", u):
        return "bounded"
    return "level"


def _per(t: pd.Timestamp, freq: str) -> str:
    if freq == "Y":
        return str(t.year)
    if freq == "Q":
        return f"Q{(t.month - 1) // 3 + 1} {t.year}"
    if freq == "S":
        return f"{'H1' if t.month < 7 else 'H2'} {t.year}"
    if freq == "D":
        return f"{t.strftime('%b')} {t.day}, {t.year}"
    return t.strftime("%b %Y")


# ── the summary ────────────────────────────────────────────────────────────

def summarize(payload: dict, y: pd.Series, *, units: str, tkey: str = "level", change: bool = False,
              levels: pd.Series | None = None, start: pd.Timestamp | None = None,
              presidencies: list | None = None, compare: tuple[str, pd.Series] | None = None) -> list[Item]:
    """Summary items of the series on screen; y is the shown series on the card's dates (NaN allowed)."""
    S = payload.get("stats")
    if S is False:
        return []
    S = S or {}
    freq = payload["frequency"]
    kind = S.get("kind") or stats_kind(payload, units, change, tkey)
    y = y.astype(float)
    obs = y.dropna()
    if not kind or obs.empty:
        return []
    dates = list(y.index)
    L = dates.index(obs.index[-1])
    fmt = value_fmt(payload, units, change, float(obs.iloc[-1]))
    ok = lambda i: i is not None and 0 <= i < len(dates) and not math.isnan(y.iloc[i])
    per = lambda t: _per(t, freq)
    # Window start: the range shown, or a later dated break.
    brks = sorted(pd.Timestamp(b["start"]) for b in payload.get("bands") or [] if b.get("break") not in (None, False, "False") and pd.Timestamp(b["start"]) <= dates[L])
    brk = brks[-1] if brks else None
    brk_label = next((b.get("label") or "" for b in payload.get("bands") or [] if brk is not None and pd.Timestamp(b["start"]) == brk), "")
    s0 = next((i for i, d in enumerate(dates) if start is None or d >= start), 0)
    from_break = False
    if brk is not None and dates[s0] < brk:
        s0 = next(i for i, d in enumerate(dates) if d >= brk)
        from_break = True
    while s0 < L and not ok(s0):
        s0 += 1

    def year_ago(i):
        if freq == "D":
            target = dates[i] - pd.Timedelta(days=365)
            js = [j for j, d in enumerate(dates) if d <= target]
            return js[-1] if js else None
        t = add_months(dates[i], -12)
        return dates.index(t) if t in dates else None

    A = year_ago(L)
    term = next((p for p in (presidencies or []) if not p.get("end")), None) if not payload.get("no_presidency") and not payload.get("plain_level") else None

    def pct_txt(a, b):
        r = a / b
        if r >= 10:
            return f"{nf(r, 0)} times"
        p = (r - 1) * 100
        return signed(p, 0 if abs(p) >= 10 else 1, "%")

    cmp_txt = ""
    if compare is not None:
        cs = compare[1].dropna()
        cs = cs[cs.index <= dates[L]]
        if len(cs) and (dates[L] - cs.index[-1]).days < 10:
            cv = float(cs.iloc[-1])
            cmp_txt = f"; {compare[0]} {fmt(cv)}" + (f" ({nf(y.iloc[L] / cv, 1)}×)" if S.get("ratio") and cv else "")
    on = "on" if freq == "D" else "in"
    latest = Item(fmt(y.iloc[L]), f"{on} {per(dates[L])}{cmp_txt}")
    items: list[Item] = []
    rng = [i for i in range(s0, L + 1) if ok(i)]

    if kind == "flow":
        cap = 21 if freq == "Q" else 6 if freq == "Y" else 61
        if len(rng) > cap and freq != "D":
            k = 4 if freq == "Q" else 1 if freq == "Y" else 12
            last, prev = rng[-k:], rng[-2 * k:-k]
            if len(last) == k and len(prev) == k and add_months(dates[last[0]], -12) == dates[prev[0]]:
                s1, s2 = sum(y.iloc[i] for i in last), sum(y.iloc[i] for i in prev)
                span = "last four quarters" if freq == "Q" else per(dates[L]) if freq == "Y" else "last 12 months"
                items.append(Item(fmt(s1), f"total, {span} to {per(dates[L])} ({fmt(s2)} in the {'four quarters' if freq == 'Q' else '12 months'} before)"))
        elif len(rng) > 1 and freq != "D":
            tot = sum(y.iloc[i] for i in rng)
            n = len(rng)
            unit = ("quarter" if freq == "Q" else "year" if freq == "Y" else "month") + ("" if n == 1 else "s")
            items.append(Item(fmt(tot), f"total since {brk_label} ({per(dates[rng[0]])} – {per(dates[L])})" if from_break
                              else f"total, {per(dates[rng[0]])} – {per(dates[L])} ({n} {unit})"))
        if ok(A):
            pc = f" ({pct_txt(y.iloc[L], y.iloc[A])})" if y.iloc[L] > 0 and y.iloc[A] > 0 else ""
            items.append(Item(fmt(y.iloc[L]), f"in {per(dates[L])}, vs. {fmt(y.iloc[A])} a year earlier{pc}"))
        else:
            items.append(latest)
        if freq in ("M", "Q"):
            t = dates[L]
            word = "quarterly" if freq == "Q" else "monthly"

            def avg(a, b):
                v = [y.iloc[i] for i, d in enumerate(dates) if a <= d <= b and ok(i)]
                return (sum(v) / len(v), len(v)) if v else None
            if t.month >= 3 or freq == "Q":
                a1, a0 = avg(pd.Timestamp(t.year, 1, 1), t), avg(pd.Timestamp(t.year - 1, 1, 1), add_months(t, -12))
                q = (t.month - 1) // 3 + 1
                span = (str(t.year) if q == 4 else f"Q1 {t.year}" if q == 1 else f"Q1–Q{q} {t.year}") if freq == "Q" else (
                    str(t.year) if t.month == 12 else f"Jan–{t.strftime('%b')} {t.year}")
                if a1 and a0 and a1[1] == a0[1] and (brk is None or pd.Timestamp(t.year - 1, 1, 1) >= brk):
                    items.append(Item(fmt(a1[0]), f"{word} average, {span} ({fmt(a0[0])} in {t.year - 1})"))
            else:
                a1, a0 = avg(add_months(t, -11), t), avg(add_months(t, -23), add_months(t, -12))
                if a1 and a0 and a1[1] == 12 and a0[1] == 12 and (brk is None or add_months(t, -23) >= brk):
                    items.append(Item(fmt(a1[0]), f"{word} average, last 12 months ({fmt(a0[0])} in the 12 before)"))
        return items[:3]

    if kind == "rate":
        items.append(latest)
        if tkey == "mom" and levels is not None and payload.get("measure") != "rate" and freq != "D":
            lv = levels.reindex(y.index)
            t12 = add_months(dates[L], -12)
            if t12 in lv.index and not math.isnan(lv[t12]) and not math.isnan(lv.iloc[L]):
                v = (lv.iloc[L] / lv[t12] - 1) * 100
                items.append(Item(signed(v, 1, "%"), f"year over year in {per(dates[L])}" if freq == "Q" else f"over the 12 months to {per(dates[L])}"))
        elif ok(A) and (brk is None or dates[A] >= brk):
            items.append(Item(fmt(y.iloc[A]), f"a year earlier ({per(dates[A])}); change {signed(y.iloc[L] - y.iloc[A], 1, ' pp')}"))
        if term:
            w = term_window(term, freq)
            vals = [(d, y.iloc[i]) for i, d in enumerate(dates) if d >= w["first"] and (w["last"] is None or d <= w["last"]) and ok(i)]
            if vals and (brk is None or w["first"] >= brk):
                if tkey == "mom" and payload.get("measure") != "rate":
                    avg = (math.exp(sum(math.log(1 + v / 100) for _, v in vals) / len(vals)) - 1) * 100
                    items.append(Item(fmt(avg), f"a {'quarter' if freq == 'Q' else 'month'} on average under {term['short']} (geometric, since {per(vals[0][0])})"))
                elif tkey == "yoy" and payload.get("measure") != "rate" and levels is not None:
                    lv = levels.dropna()
                    b = lv[lv.index <= w["base"]]
                    if len(b):
                        months = (lv.index[-1] - b.index[-1]).days / 30.4375
                        ann = (math.pow(lv.iloc[-1] / b.iloc[-1], 12 / months) - 1) * 100
                        items.append(Item(signed(ann, 1, "%"), f"a year on average under {term['short']} (annualized since {per(b.index[-1])})"))
                else:
                    items.append(Item(fmt(sum(v for _, v in vals) / len(vals)), f"average under {term['short']}, since {per(vals[0][0])}"))
        elif len(rng) > 2:
            items.append(Item(fmt(sum(y.iloc[i] for i in rng) / len(rng)), f"average since {per(dates[rng[0]])}"))
        return items[:3]

    # level / bounded
    items.append(latest)

    def chg(i):
        a, b = y.iloc[L], y.iloc[i]
        if kind == "bounded":
            return signed(a - b, 2 if abs(a) < 10 else 0 if abs(a) >= 100 else 1)
        return pct_txt(a, b) if a > 0 and b > 0 else None
    if ok(s0) and s0 < L:
        c = chg(s0)
        if c:
            items.append(Item(c, f"since {brk_label} ({fmt(y.iloc[s0])} {on} {per(dates[s0])})" if from_break
                              else f"since {per(dates[s0])} ({fmt(y.iloc[s0])})"))
    if term:
        w = term_window(term, freq)
        js = [j for j, d in enumerate(dates) if d <= w["base"]] if freq in ("D", "W") else ([dates.index(w["base"])] if w["base"] in dates else [])
        b = js[-1] if js else None
        if freq == "D":
            while b is not None and b > 0 and not ok(b):
                b -= 1
        if ok(b) and (brk is None or dates[b] >= brk) and b < L:
            c = chg(b)
            if c:
                items.append(Item(c, f"since {term['short']} took office ({fmt(y.iloc[b])} {on} {per(dates[b])})"))
    elif ok(A) and (brk is None or dates[A] >= brk):
        c = chg(A)
        if c:
            items.append(Item(c, f"over a year ({fmt(y.iloc[A])} {on} {per(dates[A])})"))
    return items[:3]


def line(items: list[Item]) -> str:
    return " · ".join(i.text() for i in items)


# ── the two-quarter rule ───────────────────────────────────────────────────

def _q3(s: pd.Series, m: pd.Timestamp) -> float | None:
    xs = [s.get(add_months(m, -k)) for k in range(3)]
    return None if any(x is None or (isinstance(x, float) and math.isnan(x)) for x in xs) else sum(xs) / 3


def two_quarter_spells(s: pd.Series, mode: str = "calendar") -> list[dict]:
    """Spells of two or more quarters of falling activity on a monthly SA level series.
    calendar: calendar-quarter averages, a run of two or more falling quarters, shaded quarter by quarter.
    rolling:  at each month t, the quarter ending in t against the one before, and that one against the
              one before it; when both fall, months t-5..t are shaded."""
    s = s.dropna()
    s.index = pd.to_datetime(s.index)
    spells: list[dict] = []
    if mode == "calendar":
        Q = []
        for d in s.index:
            if d.month % 3 == 1:
                a = _q3(s, add_months(d, 2))
                if a is not None:
                    Q.append((d, a))
        run: list = []

        def close():
            if len(run) >= 2:
                spells.append({"start": run[0], "end": add_months(run[-1], 2)})
            run.clear()
        for k in range(1, len(Q)):
            if add_months(Q[k - 1][0], 3) != Q[k][0]:
                close()
                continue
            if Q[k][1] < Q[k - 1][1]:
                run.append(Q[k][0])
            else:
                close()
        close()
        return spells
    on = set()
    for d in s.index:
        a, b, c = _q3(s, d), _q3(s, add_months(d, -3)), _q3(s, add_months(d, -6))
        if None not in (a, b, c) and a < b and b < c:
            for k in range(6):
                on.add(add_months(d, -k))
    for m in sorted(on):
        if spells and add_months(spells[-1]["end"], 1) == m:
            spells[-1]["end"] = m
        else:
            spells.append({"start": m, "end": m})
    return spells


def spell_stats(s: pd.Series, spells: list[dict], mode: str = "calendar") -> list[dict]:
    """Length, fall (quarterly averages, the quarter before to the lowest inside) and recovery of each spell."""
    s = s.dropna()
    step = 3 if mode == "calendar" else 1
    full = [d for d in s.index if _q3(s, d) is not None and (mode != "calendar" or d.month % 3 == 0)]
    last_full = full[-1] if full else None
    out = []
    for n, sp in enumerate(spells):
        months = _mi(sp["end"]) - _mi(sp["start"]) + 1
        base = _q3(s, add_months(sp["start"], -1))
        trough, trough_at = None, None
        m = add_months(sp["start"], 2)
        while m <= sp["end"]:
            a = _q3(s, m)
            if a is not None and (trough is None or a < trough):
                trough, trough_at = a, m
            m = add_months(m, step)
        nxt = spells[n + 1] if n + 1 < len(spells) else None
        back = cut = None
        if trough_at is not None and base:
            m = add_months(trough_at, step)
            while last_full is not None and m <= last_full:
                a = _q3(s, m)
                if nxt and add_months(nxt["start"], 2) <= m and (a is None or a < base):
                    cut = nxt["start"]
                    break
                if a is not None and a >= base:
                    back = m
                    break
                m = add_months(m, step)
        out.append({**sp, "months": months, "depth": (trough / base - 1) * 100 if base and trough is not None else None,
                    "trough_at": trough_at, "ongoing": sp["end"] == last_full, "back": back,
                    "recovery_months": _mi(back) - _mi(trough_at) if back is not None else None, "cut": cut})
    return out


# ── four ways to call a recession ─────────────────────────────────────────

METHODS = {
    "bb": "Bry-Boschan turning points (peak to trough)",
    "calendar": "Two quarters of decline, calendar quarters",
    "rolling": "Two quarters of decline, monthly rolling",
    "markov": "Recession probability above 50% (full sample)",
}


def _months_of(spells: list[dict]) -> set:
    out = set()
    for sp in spells:
        m = pd.Timestamp(sp["start"])
        while m <= pd.Timestamp(sp["end"]):
            out.add(m)
            m = add_months(m, 1)
    return out


def _runs(months: list[pd.Timestamp]) -> list[dict]:
    spells: list[dict] = []
    for m in sorted(months):
        if spells and add_months(spells[-1]["end"], 1) == m:
            spells[-1]["end"] = m
        else:
            spells.append({"start": m, "end": m})
    return spells


def compare_methods(sa: pd.Series, prob: pd.Series, bb_bands: list[dict], open_phase: str | None) -> dict:
    """Each measure's spells (monthly), and how they compare with the Bry-Boschan chronology.

    Comparison sample: from the first month every measure can be computed to six months before the
    last observation (Bry-Boschan needs six months after a turn to date it)."""
    sa = sa.dropna().asfreq("MS")
    last = sa.index[-1]
    bb = [{"start": pd.Timestamp(b["start"]), "end": pd.Timestamp(b["end"])} for b in bb_bands if b.get("end")]
    if open_phase:
        bb.append({"start": pd.Timestamp(open_phase), "end": last, "open": True})
    spells = {
        "bb": bb,
        "calendar": two_quarter_spells(sa, "calendar"),
        "rolling": two_quarter_spells(sa, "rolling"),
        "markov": _runs([d for d, v in prob.dropna().items() if v > 50]),
    }
    first = max(sa.index[0] + pd.DateOffset(months=6), prob.dropna().index[0])
    end = add_months(last, -6)
    sample = [m for m in pd.date_range(first, end, freq="MS")]
    on = {k: _months_of([x for x in v if not x.get("open")]) for k, v in spells.items()}
    rows = []
    for k in METHODS:
        mk = on[k]
        inrec = sum(m in mk for m in sample)
        agree = sum((m in mk) == (m in on["bb"]) for m in sample)
        # For each dated recession: did this measure flag it, and how many months after (+) or before (−) its start?
        hits, leads = 0, []
        for b in [x for x in bb if not x.get("open")]:
            over = [x for x in spells[k] if x["start"] <= b["end"] and x["end"] >= b["start"]]
            if over:
                hits += 1
                leads.append(_mi(over[0]["start"]) - _mi(b["start"]))
        false = [x for x in spells[k] if not any(x["start"] <= b["end"] and x["end"] >= b["start"] for b in bb)]
        cur = next((x for x in spells[k] if pd.Timestamp(x["end"]) >= add_months(last, -1)), None)
        rows.append({"key": k, "label": METHODS[k], "spells": len([x for x in spells[k] if not x.get("open") and x is not cur]),
                     "share": round(100 * inrec / len(sample), 1) if sample else None,
                     "agree": round(100 * agree / len(sample), 1) if sample else None,
                     "dated_found": hits, "dated_total": len([x for x in bb if not x.get("open")]),
                     "median_start": float(pd.Series(leads).median()) if leads else None,
                     "other_spells": len(false),
                     "now": pd.Timestamp(cur["start"]).strftime("%Y-%m-%d") if cur else None})
    fmt = lambda x: {"start": pd.Timestamp(x["start"]).strftime("%Y-%m-%d"), "end": pd.Timestamp(x["end"]).strftime("%Y-%m-%d"), **({"open": True} if x.get("open") else {})}
    return {"spells": {k: [fmt(x) for x in v] for k, v in spells.items()}, "table": rows,
            "sample": [sample[0].strftime("%Y-%m-%d"), sample[-1].strftime("%Y-%m-%d")] if sample else None,
            "labels": METHODS}
