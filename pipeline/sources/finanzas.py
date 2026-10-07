"""Adapter for the Finance Secretariat's public-debt statistics (Ministerio de Economía).

Three sets of Excel workbooks, found by scraping their landing pages (file
names carry dates and arbitrary suffixes):

  monthly bulletin   boletin_mensual_<date>.xlsx, central administration gross
                     debt, monthly from January 2019, millions of US dollars:
                       A.2  stock by governing law
                       A.3  stock by currency
                       A.4  flows and valuation changes: issuance and
                            amortization by instrument, capitalized interest,
                            CER and exchange-rate revaluation
                       A.5  payments by currency, instrument and creditor
  quarterly report   deuda_publica_<dd-mm-yyyy>.xlsx: the maturity profile
                     (capital and interest by month for the next 18 months,
                     A.3.2-A.3.5, and by year, A.3.7-A.3.8), millions of US
                     dollars at the exchange rates of the report's date
  placements         colocaciones_<date>.xlsx, one per year: every placement
                     of bonds and letters (settlement date, nominal and cash
                     value, issue price, terms). From 2021 (earlier files use
                     other layouts)

Series ids are "fin:<block>|<key>" (see SERIES below). The maturity profile
and the placements are written as tables (write_tables) for the site.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import math
import re
import time
import unicodedata
from pathlib import Path

import pandas as pd
import requests

from . import FetchResult

BASE = "https://www.argentina.gob.ar"
MONTHLY_PAGES = [f"{BASE}/economia/finanzas/datos-mensuales", f"{BASE}/economia/finanzas/datos-mensuales-de-la-deuda/datos"]
QUARTERLY_PAGE = f"{BASE}/economia/finanzas/datos-trimestrales-de-la-deuda"
PLACEMENTS_PAGE = f"{BASE}/economia/finanzas/deudapublica/colocacionesdedeuda"
PLACEMENTS_FROM = 2021
TIMEOUT = 120
USER_AGENT = "argentina-economic-monitor (+https://github.com/ncachanosky/argentina-economic-monitor)"
MONTHS_ES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8, "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12}

LAST: dict = {}       # parsed content of the last fetch, for write_tables


# ---------- downloads ----------

def _get(url: str, retries: int = 4) -> bytes:
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
            r.raise_for_status()
            return r.content
        except Exception as exc:  # network hiccups: retry with backoff
            last = exc
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"{url}: {last}")


def _links(html: str, pattern: str) -> list[str]:
    out = []
    for href in re.findall(r'href="([^"]+)"', html):
        if re.search(pattern, href, re.I):
            url = href if href.startswith("http") else BASE + href
            if url not in out:
                out.append(url)
    return out


def _file_date(url: str) -> dt.date | None:
    """31-08-26, 31_12_2021, 30-06-2026 in a file name -> date."""
    name = url.rsplit("/", 1)[-1]
    m = re.search(r"(\d{1,2})[-_](\d{1,2})[-_](\d{4}|\d{2})(?!\d)", name)
    if not m:
        return None
    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    y = y + 2000 if y < 100 else y
    try:
        return dt.date(y, mo, d)
    except ValueError:
        return None


def find_monthly(pages_html: list[str]) -> str:
    links = [u for h in pages_html for u in _links(h, r"boletin_mensual[^\"]*\.xlsx$")]
    if not links:
        raise RuntimeError("no monthly bulletin link on the landing page")
    return max(links, key=lambda u: _file_date(u) or dt.date.min)


def find_quarterly(html: str) -> str:
    links = [u for u in _links(html, r"deuda_publica_[^\"]*\.xlsx$") if _file_date(u)]
    if not links:
        raise RuntimeError("no quarterly debt report link on the landing page")
    return max(links, key=_file_date)


def find_placements(html: str, first_year: int = PLACEMENTS_FROM) -> dict[int, str]:
    """Year -> latest placements file of that year (a year can have a final and a preliminary file)."""
    by_year: dict[int, tuple[dt.date, str]] = {}
    for u in _links(html, r"coloc[^\"]*\.xlsx$"):
        d = _file_date(u)
        if d and d.year >= first_year and (d.year not in by_year or d > by_year[d.year][0]):
            by_year[d.year] = (d, u)
    return {y: u for y, (d, u) in sorted(by_year.items())}


# ---------- workbook helpers ----------

def _norm(s) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    s = re.sub(r"\(\d\)|\(\*+\)", "", s)
    return re.sub(r"\s+", " ", s.replace("·", " ").replace(" . ", " ")).strip(" .").lower()


def _period(v) -> pd.Timestamp | None:
    if isinstance(v, (dt.datetime, dt.date)):
        return pd.Timestamp(v.year, v.month, 1)
    if isinstance(v, str):
        m = re.match(r"\s*([a-z]{3})[a-z]*[-/ ](\d{2}|\d{4})", v.strip().lower())
        if m and m.group(1) in MONTHS_ES:
            y = int(m.group(2))
            return pd.Timestamp(y + 2000 if y < 100 else y, MONTHS_ES[m.group(1)], 1)
    return None


def _num(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return None if (isinstance(v, float) and math.isnan(v)) else float(v)
    try:
        return float(str(v).replace(",", "."))
    except ValueError:
        return None


def _rows(ws) -> list[tuple]:
    return list(ws.iter_rows(values_only=True))


def _label(r) -> str | None:
    for c in r[:3]:
        if isinstance(c, str) and c.strip():
            return c
    return None


def monthly_block(rows: list[tuple]) -> tuple[dict[int, pd.Timestamp], list[tuple[int, str, dict]]]:
    """Header of month columns, then (row index, label, {date: value}) for each labelled row."""
    hdr_i = next(i for i, r in enumerate(rows) if sum(_period(c) is not None for c in r) >= 5)
    cols = {j: _period(c) for j, c in enumerate(rows[hdr_i]) if _period(c) is not None}
    out = []
    for i, r in enumerate(rows[hdr_i + 1:], hdr_i + 1):
        lab = _label(r)
        if not lab:
            continue
        vals = {d: _num(r[j]) for j, d in cols.items() if j < len(r) and _num(r[j]) is not None}
        out.append((i, lab, vals))
    return cols, out


def _find(items, label: str, start: int = 0, exact: bool = True):
    key = _norm(label)
    for i, lab, vals in items:
        if i < start:
            continue
        n = _norm(lab)
        if (n == key) if exact else n.startswith(key):
            return i, vals
    raise KeyError(f"row {label!r} not found")


# ---------- monthly bulletin ----------

def peso_market(name: str) -> bool:
    """Peso-denominated marketable Treasury securities (rollover). Excludes letters placed with
    public agencies (LETRA <agency>), LEFI (a BCRA liquidity operation), promissory notes and
    dollar or dollar-linked instruments."""
    n = name.strip().upper()
    if "U$S" in n or "$" not in n:
        return False
    return not n.startswith(("LETRA ", "LETRAS EN", "LEFI", "PAGAR"))


def parse_monthly(content: bytes) -> dict[str, dict]:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    out: dict[str, dict] = {}

    # A.2 by governing law
    _, it = monthly_block(_rows(wb["A.2"]))
    out["fin:a2|total"] = _find(it, "DEUDA BRUTA")[1]
    out["fin:a2|local_law"] = _find(it, "Legislacion Argentina")[1]
    out["fin:a2|foreign_law"] = _find(it, "Legislacion extranjera")[1]

    # A.3 by currency (the "composición por moneda" block)
    _, it = monthly_block(_rows(wb["A.3"]))
    i0 = next(i for i, lab, _ in it if _norm(lab).startswith("composicion por moneda")) if any(
        _norm(lab).startswith("composicion por moneda") for _, lab, _ in it) else 0
    for key, lab in [("pesos_other", "Pesos no ajustable por CER"), ("pesos_cer", "Pesos ajustable por CER"),
                     ("usd", "Dolares"), ("eur", "Euros"), ("jpy", "Yenes"), ("sdr", "DEG"), ("other", "Otras monedas")]:
        out[f"fin:a3|{key}"] = _find(it, lab, start=i0)[1]

    # A.1 stock of capitalizing peso securities (all LECAP and BONCAP lines, short and long term)
    _, it = monthly_block(_rows(wb["A.1"]))
    for key, lab in [("lecap", "LECAP"), ("boncap", "BONCAP"), ("advances", "ADELANTOS TRANSITORIOS BCRA")]:
        tot: dict = {}
        for i, l, vals in it:
            if _norm(l) == _norm(lab) or (key == "advances" and _norm(l).startswith(_norm(lab))):
                for d, v in vals.items():
                    tot[d] = tot.get(d, 0.0) + v
        out[f"fin:a1|{key}"] = tot

    # A.4 flows and valuation changes
    _, it = monthly_block(_rows(wb["A.4"]))
    out["fin:a4|capitalized"] = _find(it, "Capitalizacion de Bonos, Letras y otros prestamos", exact=False)[1]
    d_i = _find(it, "d) Ajustes de valuacion", exact=False)[0]
    out["fin:a4|cer_adj"] = _find(it, "Variacion de la deuda ajustable por CER", start=d_i, exact=False)[1]
    out["fin:a4|fx_adj"] = _find(it, "Tipo de Cambio", start=d_i, exact=False)[1]
    out["fin:a4|gross_debt"] = _find(it, "VII - DEUDA BRUTA", exact=False)[1]
    s1 = _find(it, "1 - Financiamiento", exact=False)[0]
    s2 = _find(it, "2 - Amortizaciones", exact=False)[0]
    s3 = _find(it, "a) Operaciones netas", exact=False)[0]
    for key, lo, hi in [("peso_mkt_issued", s1, s2), ("peso_mkt_amortized", s2, s3)]:
        tot: dict = {}
        for i, lab, vals in it:
            if lo < i < hi and peso_market(lab):
                for d, v in vals.items():
                    tot[d] = tot.get(d, 0.0) + v
        out[f"fin:a4|{key}"] = tot

    # A.5 payments: by currency, then by instrument and creditor
    _, it = monthly_block(_rows(wb["A.5"]))
    lc = _find(it, "- MONEDA NACIONAL", exact=False)[0]
    fc = _find(it, "- MONEDA EXTRANJERA", exact=False)[0]
    out["fin:a5|peso_capital"] = _find(it, "CAPITAL", start=lc)[1]
    out["fin:a5|peso_interest"] = _find(it, "INTERES", start=lc)[1]
    out["fin:a5|fx_capital"] = _find(it, "CAPITAL", start=fc)[1]
    out["fin:a5|fx_interest"] = _find(it, "INTERES", start=fc)[1]
    for key, lab in [("imf", "FMI"), ("idb", "BID"), ("wb", "BIRF"), ("caf", "CAF")]:
        j = _find(it, lab)[0]
        out[f"fin:a5|{key}_capital"] = _find(it, "CAPITAL", start=j)[1]
        out[f"fin:a5|{key}_interest"] = _find(it, "INTERES", start=j)[1]
    out["fin:a5|total"] = _find(it, "TOTAL PAGADO POR MONEDA")[1]
    return out


# ---------- quarterly report: maturity profile ----------

# Groups shown on the site, from the profile's summary rows (millions of US dollars).
SCHEDULE_GROUPS = [
    ("pesos_fixed", "Pesos: fixed-rate and other", "copper"),
    ("pesos_cer", "Pesos: CER-linked", "gold"),
    ("fx_bonds", "Foreign currency: bonds and other", "sky"),
    ("imf", "IMF", "rose"),
    ("ifis", "Other multilaterals and bilateral", "sage"),
    ("bcra", "BCRA (advances and non-transferable letters)", "slate"),
]


def _profile_rows(rows: list[tuple]) -> tuple[list, dict[str, list]]:
    """Columns (month dates or years) and the summary rows of one profile sheet."""
    hdr_i = next(i for i, r in enumerate(rows[:15]) if sum(isinstance(c, (dt.datetime, dt.date, int)) and not isinstance(c, bool) for c in r) >= 5)
    hdr = rows[hdr_i]
    cols = []
    for j, c in enumerate(hdr):
        if isinstance(c, (dt.datetime, dt.date)):
            cols.append((j, pd.Timestamp(c.year, c.month, 1).strftime("%Y-%m-%d")))
        elif isinstance(c, int) and not isinstance(c, bool) and 1990 < c < 2200:
            # an int header is a year column in the annual sheets, a subtotal in the monthly ones
            if not any(isinstance(x, (dt.datetime, dt.date)) for x in hdr):
                cols.append((j, str(c)))
        elif isinstance(c, str) and re.match(r"^\d{4}\s*-\s*\d{4}$", c.strip()):
            cols.append((j, c.strip().replace(" ", "")))
    items = []
    for i, r in enumerate(rows[hdr_i + 1:], hdr_i + 1):
        lab = _label(r)
        if lab:
            items.append((i, lab, [(_num(r[j]) if j < len(r) else None) or 0.0 for j, _ in cols]))

    def get(label, start=0, default=None):
        key = _norm(label)
        for i, lab, v in items:
            if i >= start and _norm(lab) == key:
                return v
        if default is not None:
            return default
        raise KeyError(f"profile row {label!r} not found")

    zero = [0.0] * len(cols)
    tp = next(i for i, lab, _ in items if _norm(lab) == "total deuda denominada en pesos")
    rows_out = {
        "total": get("TOTAL"),
        "pesos_cer": get("Deuda ajustable por CER", start=tp),
        "pesos_nocer": get("Deuda no ajustable por CER", start=tp),
        "fx_total": get("TOTAL DEUDA EN MONEDA EXTRANJERA"),
        "oi": get("Organismos Internacionales"),
        "imf": get("FMI"),
        "oo": get("Organismos Oficiales", default=zero),
        "at": get("ADELANTOS TRANSITORIOS BCRA", default=zero),
        "intransf": get("LETRA INTRANSFERIBLE - BCRA", default=zero),
    }
    return [c for _, c in cols], rows_out


def _groups(r: dict) -> dict[str, list]:
    n = len(r["total"])
    g = {
        "pesos_fixed": [r["pesos_nocer"][i] - r["at"][i] for i in range(n)],
        "pesos_cer": r["pesos_cer"],
        "imf": r["imf"],
        "ifis": [r["oi"][i] - r["imf"][i] + r["oo"][i] for i in range(n)],
        "bcra": [r["at"][i] + r["intransf"][i] for i in range(n)],
    }
    g["fx_bonds"] = [r["fx_total"][i] - r["imf"][i] - g["ifis"][i] - r["intransf"][i] for i in range(n)]
    return g


def parse_quarterly(content: bytes) -> dict:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    names = wb.sheetnames
    title = " ".join(str(c) for r in _rows(wb["A.3.2"])[:9] for c in r if isinstance(c, str))
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", title)
    as_of = f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None

    def merge(sheets):
        cols, groups, totals = [], {k: [] for k, *_ in SCHEDULE_GROUPS}, []
        for s in sheets:
            if s not in names:
                continue
            c, r = _profile_rows(_rows(wb[s]))
            g = _groups(r)
            cols += c
            totals += r["total"]
            for k in groups:
                groups[k] += g[k]
        return cols, groups, totals

    months_c, cap_m, tot_cap_m = merge(["A.3.2", "A.3.4"])
    months_i, int_m, tot_int_m = merge(["A.3.3", "A.3.5"])
    years_c, cap_y, tot_cap_y = merge(["A.3.7"])
    years_i, int_y, tot_int_y = merge(["A.3.8"])
    if months_c != months_i or years_c != years_i:
        raise ValueError("capital and interest profiles have different columns")
    return {"as_of": as_of, "months": months_c, "years": years_c,
            "monthly": {"capital": cap_m, "interest": int_m, "total_capital": tot_cap_m, "total_interest": tot_int_m},
            "annual": {"capital": cap_y, "interest": int_y, "total_capital": tot_cap_y, "total_interest": tot_int_y}}


def schedule_table(q: dict, monthly: dict | None) -> dict:
    """The site's table: groups, labels and colors; months already elapsed with actual payments."""
    groups = [{"key": k, "label": lab, "color": col} for k, lab, col in SCHEDULE_GROUPS]
    paid = {}
    if monthly and "fin:a5|total" in monthly:
        for d, v in monthly["fin:a5|total"].items():
            iso = d.strftime("%Y-%m-%d")
            if iso in q["months"]:
                paid[iso] = round(v, 1)
    r = lambda xs: [round(x, 1) for x in xs]
    return {
        "latest": q["as_of"], "as_of": q["as_of"], "groups": groups,
        "monthly": {"dates": q["months"], "capital": {k: r(v) for k, v in q["monthly"]["capital"].items()},
                    "interest": {k: r(v) for k, v in q["monthly"]["interest"].items()},
                    "check_total": r([a + b for a, b in zip(q["monthly"]["total_capital"], q["monthly"]["total_interest"])])},
        "annual": {"years": q["years"], "capital": {k: r(v) for k, v in q["annual"]["capital"].items()},
                   "interest": {k: r(v) for k, v in q["annual"]["interest"].items()},
                   "check_total": r([a + b for a, b in zip(q["annual"]["total_capital"], q["annual"]["total_interest"])])},
        "paid": paid,
    }


# ---------- quarterly presentations: debt by holder ----------

HOLDERS = [("public", "agencias del sector publico"), ("private", "sector privado"), ("total", "total deuda publica bruta")]
HOLDER_LABELS = {"public": "agencias del sector", "private": "sector privado", "multi": "multilaterales",
                 "eligible": "deuda elegible", "total": "total deuda publica"}
HOLDERS_FROM_STORE_MIN = 8          # with this much history stored, only the latest presentations are read


def find_presentations(html: str) -> list[tuple[dt.date, str]]:
    """(report date, presentation PDF) pairs: on the landing page each quarter lists its
    workbook and then its presentation, whose file names do not carry a usable date."""
    out, last = [], None
    for href in re.findall(r'href="([^"]+)"', html):
        url = href if href.startswith("http") else BASE + href
        name = url.rsplit("/", 1)[-1].lower()
        if re.search(r"deuda_publica_[^/]*\.xlsx$", name):
            last = _file_date(url)
        elif name.endswith(".pdf") and "presentaci" in name and last:
            if all(u != url for _, u in out):
                out.append((last, url))
            last = None
    return out


def _amounts(line: str) -> list[float]:
    """Amounts with thousands separators (129.078, (3.304)); percentages and footnote marks are not."""
    out = []
    for m in re.finditer(r"(\(?)(-?\d{1,3}(?:\.\d{3})+)(\)?)(?![\d,%])", line):
        v = float(m.group(2).replace(".", ""))
        out.append(-v if m.group(1) and m.group(3) else v)
    return out


def holders_from_text(text: str) -> dict[str, float]:
    """The table's second date column for each holder. Layouts vary across years: a label may
    be split over two lines, with its numbers on the label's line, the next or the previous."""
    lines = text.splitlines()
    norm = [_norm(l) for l in lines]
    is_label = lambda n: any(n.startswith(lab) for lab in HOLDER_LABELS.values())
    out = {}
    for key, lab in HOLDER_LABELS.items():
        i = next((k for k, n in enumerate(norm) if n.startswith(lab)), None)
        if i is None:
            continue
        for j in (i, i + 1, i - 1):
            if 0 <= j < len(lines) and (j == i or not is_label(norm[j])):
                a = [x for x in _amounts(lines[j]) if x > 0]
                if len(a) >= 2:
                    out[key] = a[1]
                    break
    # Fill one missing piece from the total (public + private + multilateral; the debt eligible
    # for the restructurings is shown apart and not added in).
    elig = 0.0
    if "total" in out:
        parts = ["public", "private", "multi"]
        missing = [k for k in parts if k not in out]
        if len(missing) == 1:
            out[missing[0]] = out["total"] - elig - sum(out[k] for k in parts if k != missing[0])
    return out


def parse_holders(content: bytes) -> dict[str, float]:
    """Debt by holder at the report's date (US$ millions), from the table "Deuda Bruta de la
    Administración Central por acreedor" of the quarterly presentation."""
    import pdfplumber
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            if "Privado" not in text or "Total Deuda" not in text:
                continue
            out = holders_from_text(text)
            if {"public", "private", "total"} <= set(out):
                tot_parts = out["public"] + out["private"] + out.get("multi", 0)
                if out.get("multi") and abs(tot_parts / out["total"] - 1) > 0.01:
                    continue                      # the columns did not line up
                return out
    raise ValueError("holder table not found")


# ---------- placements ----------

KINDS = [
    ("fixed", "Fixed-rate pesos"), ("cer", "CER-linked"), ("floating", "Floating (TAMAR, BADLAR)"),
    ("dual", "Dual"), ("dlinked", "Dollar-linked (paid in pesos)"), ("usd", "Hard dollar"),
]
PESO_KINDS = ("fixed", "cer", "floating", "dual")


def classify(name: str, coupon: str, currency_type: str, origin: str, amortization: str = "") -> str:
    n, c, t, o = (name or "").upper(), (coupon or "").upper(), (currency_type or "").upper(), (origin or "").upper()
    a = (amortization or "").upper()
    if "DUAL" in n:
        return "dual"
    if ("LELINK" in n or "/DLK" in n or "LINKED" in n or "PAGA EN PESOS" in n or "PAGAN EN PESOS" in n
            or "TIPO DE CAMBIO" in a):
        return "dlinked"
    if "EXTRANJERA" in t:
        return "usd"
    if o in ("USD", "U$S") or "U$S" in n or "U$D" in n:
        return "dlinked"
    if o == "UCP" or "CER" in n or "CER" in c:
        return "cer"
    if "TAMAR" in n or "TAMAR" in c or "BADLAR" in n or "BADLAR" in c:
        return "floating"
    return "fixed"


def dollar_unit(origin: str, ve: float, vn: float | None, year: int) -> str:
    """Currency of the cash value reported for a dollar or dollar-linked placement. The files are
    not consistent: dollar-linked placements were reported in pesos through 2024 (in 2021 with the
    face value in dollars), and in dollars from 2025."""
    if vn and ve / vn > 20:
        return "ARS"                  # face value in dollars, cash value in pesos
    if origin.strip().upper() in ("USD", "U$S"):
        return "USD"
    if ve > 20000 or year <= 2024:
        return "ARS"                  # more than US$20 billion in one placement is not plausible
    return "USD"


def _date(v) -> dt.date | None:
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    if isinstance(v, str):
        for f in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
            try:
                return dt.datetime.strptime(v.strip()[:10], f).date()
            except ValueError:
                pass
    return None


def implied_yield(kind: str, coupon: str, issue: dt.date, maturity: dt.date, settle: dt.date, price: float):
    """Yield implied by the issue price of capitalizing fixed-rate letters and bonds (LECAP, BONCAP)
    and zero-coupon peso discount letters (LETES, LEDE): (payoff / price)^(30/days) - 1 (effective monthly) and
    ^(365/days) (effective annual). Payoff per 1,000 face: 1,000 (1 + TEM)^(days issue->maturity / 30)."""
    if kind != "fixed" or not (issue and maturity and settle and price) or price <= 0:
        return None, None
    c = (coupon or "").lower()
    days = (maturity - settle).days
    if days <= 0:
        return None, None
    m = re.search(r"capitalizable\s*([\d.,]+)", c)
    if m:
        tem = float(m.group(1).replace(",", ".")) / 100
        payoff = 1000 * (1 + tem) ** ((maturity - issue).days / 30)
    elif "cero" in c or "descuento" in c:
        payoff = 1000.0
    else:
        return None, None
    g = payoff / price
    return round((g ** (30 / days) - 1) * 100, 2), round((g ** (365 / days) - 1) * 100, 2)


def parse_placements(content: bytes, year: int) -> list[dict]:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    out = []
    for s in wb.sheetnames:
        ns = _norm(s)
        if ns not in ("bonos", "letras"):
            continue                       # intra-public-sector letters, swaps, other operations: not market placements
        rows = _rows(wb[s])
        hi = next((i for i, r in enumerate(rows[:20]) if any(isinstance(c, str) and _norm(c).startswith("nombre del instrumento") for c in r)), None)
        if hi is None:
            continue
        hdr = [(_norm(c) if c else "") for c in rows[hi]]

        def col(prefix):
            return next((j for j, h in enumerate(hdr) if h.startswith(prefix)), None)
        J = {k: col(p) for k, p in [("name", "nombre del instrumento"), ("issue", "fecha de emision"), ("maturity", "vencimiento"),
                                    ("coupon", "cupon"), ("amort", "amortizacion"), ("ctype", "tipo moneda"), ("origin", "moneda de origen"),
                                    ("settle", "fecha colocacion"), ("vn", "valor nominal"), ("ve", "valor efectivo"),
                                    ("price", "precio de emision"), ("life", "vida promedio")]}
        life_days = "dias" in hdr[J["life"]] if J["life"] is not None else False
        for r in rows[hi + 1:]:
            g = lambda k: r[J[k]] if J[k] is not None and J[k] < len(r) else None
            name, settle = g("name"), _date(g("settle"))
            if not isinstance(name, str) or not name.strip() or not settle:
                continue
            ve, vn = _num(g("ve")), _num(g("vn"))
            if not ve:
                continue
            coupon = str(g("coupon") or "")
            kind = classify(name, coupon, str(g("ctype") or ""), str(g("origin") or ""), str(g("amort") or ""))
            price = _num(g("price"))
            issue, maturity = _date(g("issue")), _date(g("maturity"))
            # Average life: letters are bullet, so time to maturity (the files mix days and years);
            # bonds as reported, unless it exceeds the time to maturity (then it is in days).
            life = _num(g("life"))
            to_mat = (maturity - settle).days / 365 if maturity else None
            if ns == "letras" and to_mat is not None:
                life = to_mat
            elif life is not None and life_days:
                life = life / 365
            if to_mat is not None and (life is None or life <= 0 or life > to_mat + 0.05):
                life = to_mat
            unit = dollar_unit(str(g("origin") or ""), ve, vn, settle.year) if kind in ("usd", "dlinked") else "ARS"
            tem, tea = implied_yield(kind, coupon, issue, maturity, settle, price)
            out.append({"settle": settle.isoformat(), "name": name.strip(), "kind": kind, "sheet": ns,
                        "issue": issue.isoformat() if issue else None, "maturity": maturity.isoformat() if maturity else None,
                        "coupon": re.sub(r"\s+", " ", coupon).strip()[:120], "currency": unit,
                        "vn": vn, "ve": ve, "price": price, "life": round(life, 3) if life else None, "tem": tem, "tea": tea})
    return [p for p in out if p["settle"][:4] == str(year)] or out


def add_real_yields(placements: list[dict], cer: pd.Series) -> None:
    """Real yield (effective annual, %) implied by the issue price of zero-coupon CER-linked
    letters and bonds: the price per 1,000 of face value is the face value adjusted by the CER
    from issue to settlement, discounted at the real yield to maturity. The CER is taken ten
    business days before each date (about fourteen calendar days), as the bonds' terms do."""
    if cer is None or cer.empty:
        return
    cer = cer.dropna().sort_index()
    at = lambda d: cer.asof(pd.Timestamp(d) - pd.Timedelta(days=14))
    for p in placements:
        c = (p.get("coupon") or "").lower()
        if p["kind"] != "cer" or not ("cero" in c or "tasa cero" in p["name"].lower() or "lecer" in p["name"].lower()):
            continue
        if not (p.get("issue") and p.get("maturity") and p.get("price")):
            continue
        days = (dt.date.fromisoformat(p["maturity"]) - dt.date.fromisoformat(p["settle"])).days
        ci, cs = at(p["issue"]), at(p["settle"])
        if days <= 0 or not ci or not cs or pd.isna(ci) or pd.isna(cs):
            continue
        g = 1000 * cs / ci / p["price"]
        if 0.3 < g < 3:
            p["real"] = round((g ** (365 / days) - 1) * 100, 2)


def placement_series(placements: list[dict]) -> dict[str, dict]:
    """Monthly cash value placed by kind (pesos kinds: millions of pesos; dollar kinds: millions of
    US$, plus "<kind>_ars" for the part the files report in pesos),
    and the average life of peso placements, weighted by cash value (years)."""
    df = pd.DataFrame(placements)
    if df.empty:
        return {}
    df["date"] = pd.to_datetime(df["settle"]).dt.to_period("M").dt.to_timestamp()
    out = {}
    months = sorted(df["date"].unique())
    for k, _ in KINDS:
        if k in ("usd", "dlinked"):
            # Dollar kinds: the part reported in US$ and the part reported in pesos, separately.
            for unit, suffix in (("USD", ""), ("ARS", "_ars")):
                s = df[(df.kind == k) & (df.currency == unit)].groupby("date")["ve"].sum()
                out[f"fin:col|{k}{suffix}"] = {d: float(s.get(d, 0.0)) for d in months}
            continue
        s = df[df.kind == k].groupby("date")["ve"].sum()
        out[f"fin:col|{k}"] = {d: float(s.get(d, 0.0)) for d in months}
    # Average cost (TEM, %) of capitalizing fixed-rate placements (LECAP, BONCAP) over the last six
    # months: the yield implied by their issue prices, weighted by cash raised. The rate at which
    # the stock of such debt accrues interest for the Treasury.
    cap = df[df.coupon.str.contains("capitalizable", case=False, na=False) & (df.kind == "fixed") & df.tem.notna()]
    if not cap.empty:
        allm = pd.date_range(min(months), max(months), freq="MS")
        num = (cap.tem * cap.ve).groupby(cap.date).sum().reindex(allm, fill_value=0.0).rolling(6, min_periods=1).sum()
        den = cap.ve.groupby(cap.date).sum().reindex(allm, fill_value=0.0).rolling(6, min_periods=1).sum()
        r = (num / den.where(den > 0)).dropna()
        out["fin:col|tem_fixed"] = {d: float(v) for d, v in r.items()}
    peso = df[df.kind.isin(PESO_KINDS) & df.life.notna()]
    w = peso.groupby("date").apply(lambda g: (g.life * g.ve).sum() / g.ve.sum(), include_groups=False)
    out["fin:col|life"] = {d: float(v) for d, v in w.items()}
    return out


def placements_tables(placements: list[dict]) -> dict:
    """Per year: placements grouped by settlement date (one auction or operation per date)."""
    by_year: dict[str, dict] = {}
    for p in sorted(placements, key=lambda x: (x["settle"], x["kind"], x["maturity"] or "")):
        y = p["settle"][:4]
        by_year.setdefault(y, {}).setdefault(p["settle"], []).append({k: v for k, v in p.items() if k != "settle"})
    return by_year


# ---------- fetch ----------

def collect() -> dict:
    """Download and parse everything; each part fails on its own."""
    parsed: dict = {"series": {}, "errors": {}}
    try:
        pages = []
        for u in MONTHLY_PAGES:
            try:
                pages.append(_get(u).decode("utf-8", "ignore"))
            except Exception:
                pass
        url = find_monthly(pages)
        parsed["monthly_url"] = url
        parsed["monthly"] = parse_monthly(_get(url))
        parsed["series"].update(parsed["monthly"])
    except Exception as exc:
        parsed["errors"]["monthly"] = f"monthly bulletin: {exc}"
    try:
        url = find_quarterly(_get(QUARTERLY_PAGE).decode("utf-8", "ignore"))
        parsed["quarterly_url"] = url
        parsed["quarterly"] = parse_quarterly(_get(url))
    except Exception as exc:
        parsed["errors"]["quarterly"] = f"quarterly report: {exc}"
    try:
        files = find_placements(_get(PLACEMENTS_PAGE).decode("utf-8", "ignore"))
        if not files:
            raise RuntimeError("no placement files found")
        pl = []
        for y, u in files.items():
            pl += parse_placements(_get(u), y)
        try:
            from .. import store
            add_real_yields(pl, store.as_of("bcra:30"))
        except Exception:
            pass
        parsed["placements"] = pl
        parsed["placements_urls"] = files
        parsed["series"].update(placement_series(pl))
    except Exception as exc:
        parsed["errors"]["placements"] = f"placements: {exc}"
    try:
        parsed["series"].update(collect_holders())
    except Exception as exc:
        parsed["errors"]["holders"] = f"debt by holder: {exc}"
    return parsed


def collect_holders() -> dict[str, dict]:
    """Debt by holder at each quarter end, from the quarterly presentations. Stored history is
    kept, and only the latest presentations are read once it is long enough."""
    from .. import store
    pres = find_presentations(_get(QUARTERLY_PAGE).decode("utf-8", "ignore"))
    if not pres:
        raise RuntimeError("no presentations found")
    out = {f"fin:hold|{k}": {} for k, _ in HOLDERS}
    for k, _ in HOLDERS:
        old = store.as_of(f"fin:hold|{k}")
        out[f"fin:hold|{k}"].update({d: float(v) for d, v in old.items()})
    have = len(out["fin:hold|total"])
    todo = sorted(pres, reverse=True)[: (3 if have >= HOLDERS_FROM_STORE_MIN else len(pres))]
    errors = []
    for d, url in todo:
        try:
            h = parse_holders(_get(url))
        except Exception as exc:
            errors.append(f"{d}: {exc}")
            continue
        m = pd.Timestamp(d.year, d.month, 1)
        for k, _ in HOLDERS:
            out[f"fin:hold|{k}"][m] = h[k]
    if not out["fin:hold|total"]:
        raise RuntimeError("; ".join(errors) or "no holder tables")
    return out


def to_long(series: dict[str, dict]) -> pd.DataFrame:
    rows = [(pd.Timestamp(d), sid, v) for sid, vals in series.items() for d, v in vals.items() if v is not None]
    return pd.DataFrame(rows, columns=["date", "series_id", "value"])


def fetch(ids, parsed: dict | None = None) -> FetchResult:
    global LAST
    parsed = parsed if parsed is not None else collect()
    LAST = parsed
    data = to_long(parsed["series"])
    have = set(data.series_id)
    errs = parsed.get("errors", {})
    errors = {}
    for sid in ids:
        if sid not in have:
            block = sid.split(":", 1)[1].split("|", 1)[0]
            part = "placements" if block == "col" else "holders" if block == "hold" else "monthly"
            errors[sid] = errs.get(part, "not in the Finance Secretariat files")
    data = data[data.series_id.isin(set(ids))].sort_values(["series_id", "date"])
    meta = {sid: {"units": None, "time_index_end": g.date.max().strftime("%Y-%m-%d")} for sid, g in data.groupby("series_id")}
    return FetchResult(data=data.reset_index(drop=True), meta=meta, errors=errors)


def write_tables(directory) -> list[str]:
    if not LAST:
        return []
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    written = []

    def put(rel, obj):
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
        if not p.exists() or p.read_text(encoding="utf-8") != text:
            p.write_text(text, encoding="utf-8")
            written.append(rel)

    if LAST.get("quarterly"):
        t = schedule_table(LAST["quarterly"], LAST.get("monthly"))
        t["source_file"] = LAST.get("quarterly_url")
        put("schedule.json", t)
    if LAST.get("placements"):
        years = placements_tables(LAST["placements"])
        for y, dates in years.items():
            put(f"placements/{y}.json", {"year": y, "auctions": dates})
        alld = sorted(dd for ds in years.values() for dd in ds)
        put("placements/index.json", {"latest": alld[-1], "years": {y: sorted(ds) for y, ds in years.items()},
                                      "kinds": dict(KINDS)})
    return written
