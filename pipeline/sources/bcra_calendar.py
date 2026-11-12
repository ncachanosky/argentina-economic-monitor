"""The BCRA's publication calendar ("Calendario de informes"), for the home page's
"Today's releases" strip and the release calendar page.

The page lists every scheduled report as a table of (report name, "05 nov 2026") rows in
plain HTML. Three reports feed the Monitor: the market expectations survey (REM), the
monthly banking report and the foreign-exchange market report. The update reads the page
and writes data/calendar_bcra.json; registry.load merges it into the release calendar.
These entries are not attached to cards with `release:` (the BCRA's data files are not
tied to the report date), so they never mark a card "Late". The BCRA publishes "al cierre
del día hábil": no time of day.
"""
from __future__ import annotations

import html as htmllib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

PAGE = "https://www.bcra.gob.ar/calendario-de-informes/"
PATH = Path(__file__).resolve().parents[2] / "data" / "calendar_bcra.json"
USER_AGENT = "argentina-economic-monitor (+https://github.com/ncachanosky/argentina-economic-monitor)"

# key -> report name on the page, months between the data covered and the publication, cards to link.
REPORTS = {
    "rem_bcra": {"name": "Relevamiento de Expectativas de Mercado (REM)", "lag": 1,
                 "cards": ["inflation_expectations", "inflation_expectations_range", "rem_revisions"]},
    "bcra_bancos": {"name": "Informe sobre Bancos", "lag": 2,
                    "cards": ["bank_npl", "bank_capital", "bank_roe", "bank_credit_depth"]},
    "bcra_cambiario": {"name": "Informe de Evolución del Mercado de Cambios y Balance Cambiario", "lag": 1,
                       "cards": ["fx_balance_cambiario", "bop_accrual_cash"]},
}
MONTHS = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8,
          "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12}
ROW = re.compile(r"<tr[^>]*>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>", re.S | re.I)
DAY = re.compile(r"(\d{1,2})\s+([a-z]{3})\w*\.?\s+(\d{4})", re.I)


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def parse(page: str) -> dict:
    """{key: {name, publisher, calendar_url, dates: [{date, period}]}} for the reports in REPORTS."""
    want = {_clean(r["name"]).lower(): k for k, r in REPORTS.items()}
    out = {k: {"name": r["name"], "publisher": "BCRA", "calendar_url": PAGE, "cards": r["cards"], "dates": []}
           for k, r in REPORTS.items()}
    for name, when in ROW.findall(page):
        key = want.get(_clean(name).lower())
        m = DAY.search(_clean(when))
        if not key or not m or m.group(2).lower() not in MONTHS:
            continue
        day = pd.Timestamp(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1)))
        period = (day - pd.DateOffset(months=REPORTS[key]["lag"])).strftime("%Y-%m")
        entry = {"date": day.strftime("%Y-%m-%d"), "period": period}
        if entry not in out[key]["dates"]:
            out[key]["dates"].append(entry)
    for cal in out.values():
        cal["dates"].sort(key=lambda d: d["date"])
    return out


def load(path: Path = PATH) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("releases", {})
    except (OSError, ValueError):
        return {}


def refresh(path: Path = PATH, today: str | None = None) -> list[str]:
    """Read the calendar page and update the stored dates. Returns warnings (never raises).

    Dates already stored are kept when the page drops them (the page lists the current
    year only), so the "last 30 days" view on the calendar page survives January.
    """
    warnings = []
    old = load(path)
    try:
        r = requests.get(PAGE, timeout=60, headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        new = parse(r.text)
    except Exception as exc:                                  # network, HTTP or layout
        new = None
        warnings.append(f"BCRA calendar not read ({type(exc).__name__}: {exc}); keeping the stored dates")
    if new is not None:
        if not any(cal["dates"] for cal in new.values()):
            warnings.append(f"BCRA calendar: no dates found on {PAGE} (page layout changed?); keeping the stored dates")
        else:
            for key, cal in new.items():
                kept = {d["date"]: d for d in old.get(key, {}).get("dates", [])}
                kept.update({d["date"]: d for d in cal["dates"]})
                cal["dates"] = sorted(kept.values(), key=lambda d: d["date"])
            path.write_text(json.dumps({"source": PAGE, "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                        "releases": new}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            old = new
    # The BCRA adds dates as they are confirmed, so a monthly report may show no next date for a
    # few weeks; warn only when the latest listed date is well past.
    cutoff = (pd.Timestamp(today) if today else pd.Timestamp(datetime.now(timezone.utc).date())) - pd.Timedelta(days=45)
    for key, cal in old.items():
        last = max((d["date"] for d in cal.get("dates", [])), default="")
        if last < cutoff.strftime("%Y-%m-%d"):
            warnings.append(f"BCRA calendar: no '{cal.get('name', key)}' date after {last or 'none'}; check {PAGE}")
    return warnings
