"""The economy under each presidency: one table, one column per term since December 1983.

Built at export time from the cards' own exported data (site data/<card>.json), so every
cell matches what the card shows. Terms follow the site's convention (site/js/app.js,
termWindow): the handover month belongs to the outgoing president; a term's first month is
the first full month after inauguration and its base month the one before; quarters and
semesters likewise; daily data start on inauguration day, with the day before as the base.
Annual data: a year belongs to the president in office on July 1.

Measures, per row:
  ann    annualized % change of the level, base to last period
  cum    cumulative % change of the level, base to last period
  diff   change in level, base to last (in the row's units, e.g. US$ billion)
  se     level at the base and at the last period ("start -> end")
  pre12  12-month % change to the base month (the inflation each president inherited)

A cell whose series starts after the term began is marked partial (*); if it covers less
than half the term it is left blank. No scores, ranks or colors.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

ROWS = [
    # key, section, label, card, variant, measure, unit suffix, decimals, scale
    ("infl_start", "Prices", "Inflation inherited, 12 months to the base month", "cpi_long", "corrected", "pre12", "%", 1, 1),
    ("infl_ann", "Prices", "Inflation, annualized", "cpi_long", "corrected", "ann", "%", 1, 1),
    ("infl_cum", "Prices", "Inflation, cumulative", "cpi_long", "corrected", "cum", "%", 0, 1),
    ("gdp_pc", "Activity and incomes", "Real GDP per capita, annualized", "gdp_long", "original", "gdppc", "%", 1, 1),
    ("wage_real", "Activity and incomes", "Real wages (RIPTE, registered), cumulative", "labor_wages_real", "ripte", "cum", "%", 1, 1),
    ("min_wage", "Activity and incomes", "Real minimum wage, cumulative", "labor_minimum_wage", "smvm", "cum", "%", 1, 1),
    ("unemp", "Activity and incomes", "Unemployment rate, %", "labor_rates", "unemployment", "se", "", 1, 1),
    ("poverty", "Activity and incomes", "Poverty, % of persons", "poverty", "persons", "se", "", 1, 1),
    ("rer", "External sector", "Real exchange rate, cumulative (+ = weaker peso)", "fx_rer", "m", "cum", "%", 0, 1),
    ("gap", "External sector", "Exchange-rate gap (CCL), % over official", "fx_gaps", "ccl", "se", "", 0, 1),
    ("res_gross", "External sector", "Gross reserves, change, US$ billion", "net_reserves", "gross", "diff", "", 1, 0.001),
    ("res_net", "External sector", "Net reserves (standard), change, US$ billion", "net_reserves", "standard", "diff", "", 1, 0.001),
    ("risk", "Fiscal and markets", "Country risk (EMBI), basis points", "country_risk", "embi", "se", "", 0, 1),
    ("primary", "Fiscal and markets", "Primary balance, % of GDP (12 months)", "fiscal_balance", "primary", "se", "", 1, 1),
    ("efw", "Institutions", "Economic freedom (EFW), 0-10", "inst_efw", "arg", "se", "", 2, 1),
    ("vdem", "Institutions", "Liberal democracy (V-Dem), 0-1", "inst_vdem", "libdem", "se", "", 2, 1),
]
FIRST_TERM = "1983-12-10"
POP_EXT = 0.0052          # population growth after the Maddison release's last year (as in the long-run card)


def _d(iso: str) -> date:
    return date.fromisoformat(iso[:10])


def _add_months(d: date, k: int) -> date:
    m = d.year * 12 + d.month - 1 + k
    return date(m // 12, m % 12 + 1, 1)


def window(p: dict, freq: str) -> tuple[date, date, date | None]:
    """(base, first, last) periods of a term, as the site's termWindow."""
    s = _d(p["start"])
    e = _d(p["end"]) if p.get("end") else None
    if freq == "D":
        return s - timedelta(days=1), s, (e - timedelta(days=1)) if e else None
    if freq == "Y":
        first = s.year + (0 if s.month < 7 or (s.month == 7 and s.day == 1) else 1)
        last = (e.year - (0 if (e.month, e.day) > (7, 1) else 1)) if e else None
        return date(first - 1, 1, 1), date(first, 1, 1), date(last, 1, 1) if last else None
    step = {"M": 1, "Q": 3, "S": 6}[freq]
    p0 = date(s.year, ((s.month - 1) // step) * step + 1, 1)
    first = p0 if s == p0 else _add_months(p0, step)
    last = date(e.year, ((e.month - 1) // step) * step + 1, 1) if e else None
    if last and freq == "S":            # a semester counts only if it ended within the term
        last = last if _add_months(last, 6) <= e else _add_months(last, -6)
    return _add_months(first, -step), first, last


# Rows deflated by the CPI (corrected for the INDEC intervention), and periods left out.
DEFLATE = {"wage_real", "min_wage"}
EXCLUDE = {"poverty": ("2007-01-01", "2016-06-01")}   # INDEC's poverty rates during the intervention (see methodology)


def _series(card: dict, variant: str, cpi: dict | None = None, exclude=None) -> list[tuple[date, float]]:
    vals = card["variants"][variant]["values"]
    s = [(_d(d), v) for d, v in zip(card["dates"], vals) if v is not None]
    if cpi is not None:
        s = [(d, 100 * v / cpi[d]) for d, v in s if d in cpi]
    if exclude:
        lo, hi = _d(exclude[0]), _d(exclude[1])
        s = [(d, v) for d, v in s if not lo <= d <= hi]
    return s


def cell(card: dict, variant: str, measure: str, p: dict, aux: dict) -> dict | None:
    freq = card["frequency"]
    s = _series(card, variant, aux.get("cpi"), aux.get("exclude"))
    if not s:
        return None
    base, first, last = window(p, freq)
    term_end = last or s[-1][0]
    inside = [(d, v) for d, v in s if first <= d <= term_end]
    if not inside:
        return None
    before = [(d, v) for d, v in s if d <= base] if freq in ("D", "Y") else [(d, v) for d, v in s if d == base]
    b = before[-1] if before else None
    if freq == "Y" and b and b[0].year < base.year - 5:
        b = None
    partial = b is None
    if partial:                       # series begins after the term started: from its first value in the term
        b, inside = inside[0], inside[1:]
        if not inside:
            return None
    end = inside[-1]
    span_term = ((term_end - base).days or 1)
    if (end[0] - b[0]).days < span_term / 2:
        return None
    years = (end[0] - b[0]).days / 365.25
    out = {"from": b[0].isoformat(), "to": end[0].isoformat(), "partial": partial, "ongoing": not p.get("end")}
    if measure == "se":
        out.update(start=b[1], end=end[1])
    elif measure == "diff":
        out["value"] = end[1] - b[1]
    elif measure == "cum":
        out["value"] = 100 * (end[1] / b[1] - 1)
    elif measure == "ann":
        out["value"] = 100 * ((end[1] / b[1]) ** (1 / years) - 1) if years > 0 else None
    elif measure == "pre12":
        if partial:
            return None
        prev = dict(s).get(_add_months(b[0], -12))
        if prev is None:
            return None
        return {"from": b[0].isoformat(), "to": b[0].isoformat(), "partial": False, "ongoing": not p.get("end"),
                "value": 100 * (b[1] / prev - 1)}
    return out


def gdp_pc(card: dict, p: dict, mpd: dict) -> dict | None:
    """Real GDP per capita, annualized. From 1993: INDEC's quarterly GDP (sums of four quarters, so
    seasonality cancels) less population growth; before: the Maddison Project's annual GDP per capita."""
    years = mpd["years"]
    a = mpd["countries"]["ARG"]
    pop = {y: v for y, v in zip(years, a["p"]) if v}
    gdppc = {y: v for y, v in zip(years, a["g"]) if v}
    ext = mpd.get("extension") or {}
    for y, v in zip(ext.get("years", []), ext.get("values", [])):
        gdppc[y] = v
    ly = max(pop)

    def popl(y: float) -> float:   # population, interpolated geometrically within years
        y0 = int(y)
        def at(k):
            return pop[k] if k in pop else pop[ly] * (1 + POP_EXT) ** (k - ly)
        return at(y0) * (at(y0 + 1) / at(y0)) ** (y - y0)

    s = _series(card, "original")
    base, first, last = window(p, "Q")
    if s and base >= _add_months(s[0][0], 9):      # four quarters needed at the base
        idx = {d: v for d, v in s}
        term_end = last or s[-1][0]
        q_in = [d for d, _ in s if first <= d <= term_end]
        if not q_in:
            return None
        e = q_in[-1]

        def sum4(q):
            ks = [_add_months(q, -3 * k) for k in range(4)]
            return sum(idx[k] for k in ks) if all(k in idx for k in ks) else None
        sb, se = sum4(base), sum4(e)
        if sb is None or se is None:
            return None
        yrs = (e.year + (e.month - 1) / 12) - (base.year + (base.month - 1) / 12)
        if yrs <= 0 or (e - base).days < ((term_end - base).days or 1) / 2:
            return None
        mid_b, mid_e = base.year + (base.month + 1.5 - 1) / 12 - 0.5, e.year + (e.month + 1.5 - 1) / 12 - 0.5
        g = (se / sb) / (popl(mid_e) / popl(mid_b))
        return {"from": base.isoformat(), "to": e.isoformat(), "partial": False, "ongoing": not p.get("end"),
                "value": 100 * (g ** (1 / yrs) - 1)}
    yb, yf, yl = window(p, "Y")
    yl = yl or date(max(gdppc), 1, 1)
    if yb.year not in gdppc or yl.year not in gdppc or yl.year <= yb.year:
        return None
    n = yl.year - yb.year
    return {"from": str(yb.year), "to": str(yl.year), "partial": False, "ongoing": not p.get("end"), "annual": True,
            "value": 100 * ((gdppc[yl.year] / gdppc[yb.year]) ** (1 / n) - 1)}


def build(out: Path, presidencies: list[dict]) -> dict:
    terms = [p for p in presidencies if p["start"] >= FIRST_TERM]
    cards, rows = {}, []
    mpd_path = out / "tables" / "maddison" / "mpd2023.json"
    mpd = json.loads(mpd_path.read_text(encoding="utf-8")) if mpd_path.exists() else None
    cpi_card = json.loads((out / "cpi_long.json").read_text(encoding="utf-8"))
    cpi = dict(_series(cpi_card, "corrected"))
    for key, section, label, cid, var, measure, unit, nd, scale in ROWS:
        path = out / f"{cid}.json"
        if not path.exists():
            continue
        card = cards.setdefault(cid, json.loads(path.read_text(encoding="utf-8")))
        cells = []
        for p in terms:
            try:
                c = gdp_pc(card, p, mpd) if measure == "gdppc" else cell(card, var, measure, p, {"cpi": cpi if key in DEFLATE else None, "exclude": EXCLUDE.get(key)})
            except Exception:
                c = None
            if c:
                for k in ("value", "start", "end"):
                    if c.get(k) is not None:
                        c[k] = round(c[k] * scale, nd)
            cells.append(c)
        rows.append({"key": key, "section": section, "label": label, "card": cid, "topic": card.get("topic"),
                     "measure": "ann" if measure == "gdppc" else measure, "unit": unit, "decimals": nd, "cells": cells})
    latest = max((c["to"] for r in rows for c in r["cells"] if c and len(c["to"]) == 10), default=None)
    return {"terms": [{"name": p["name"], "short": p["short"], "start": p["start"], "end": p.get("end"), "party": p.get("party")}
                      for p in terms],
            "rows": rows, "latest": latest}
