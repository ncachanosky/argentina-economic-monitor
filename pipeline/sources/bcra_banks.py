"""Adapter for the statistical annex of the BCRA's "Informe sobre Bancos".

One Excel workbook: indicators (liquidity, credit to the public and private
sectors, non-performing loans, profitability, capital), a standardized
balance sheet (all currencies, and pesos only), loan quality (by group and by
loan type) and income statements, for the financial system and bank groups.
December values from 2002, monthly values from about 2010.

Series ids are "bcrabk:<sheet>|<group>|<line>", with lines keyed by their
(slugged) label, prefixed by their parent line when indented or ambiguous:
    bcrabk:ind|system|4_credito_al_sector_publico
    bcrabk:bal|private|depositos/sector_privado
Values as published (percent, or millions of current pesos).
"""
from __future__ import annotations

import datetime as dt
import io
import re
import time
import unicodedata

import pandas as pd
import requests

from . import FetchResult

URL = "https://www.bcra.gob.ar/archivos/Pdfs/PublicacionesEstadisticas/informes/informe-bancos-anexo.xlsx"
TIMEOUT = 180
USER_AGENT = "argentina-economic-monitor (+https://github.com/ncachanosky/argentina-economic-monitor)"

SHEETS = {
    "Indicadores": "ind",
    "Estado de Sit. Financiera": "bal",
    "Estado de Sit. Financiera ($)": "bal_ars",
    "Calidad de Cartera": "npl",
    "Calidad de Cartera (por líneas)": "npl_lines",
    "Estado de Resultado desde 2020": "res",
}
GROUPS = {
    "sistema financiero": "system",
    "bancos privados": "private",
    "bancos privados nacionales": "private_domestic",
    "bancos privados extranjeros": "private_foreign",
    "bancos privados extranjera": "private_foreign",
    "bancos publicos": "public",
    "entidades financieras no bancarias (efnb)": "nbfi",
}
GROUP_LABELS = {"system": "Financial system", "private": "Private banks", "private_domestic": "Private banks, domestic",
                "private_foreign": "Private banks, foreign", "public": "Public banks", "nbfi": "Non-bank financial institutions"}

LAST_CONTENT: bytes | None = None


def download(retries: int = 4) -> bytes:
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(URL, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
            r.raise_for_status()
            expected = r.headers.get("Content-Length")
            if expected and int(expected) != len(r.content):
                raise IOError(f"incomplete download: {len(r.content)} of {expected} bytes")
            return r.content
        except Exception as exc:
            last = exc
            time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"could not download the banking annex: {last}")


def slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"(?<=[a-z])\d+$", "", s.strip())          # footnote marks: "Disponibilidades1"
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s


def _as_date(v):
    if isinstance(v, (dt.datetime, dt.date)):
        return pd.Timestamp(v.year, v.month, 1)
    if isinstance(v, (int, float)) and 1990 < v < 2100 and float(v).is_integer():
        return pd.Timestamp(int(v), 12, 1)                 # yearly columns are December
    if isinstance(v, str) and re.fullmatch(r"\d{4}", v.strip()):
        return pd.Timestamp(int(v), 12, 1)
    return None


def _num(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    return None


def parse(content: bytes) -> dict:
    """{sheet_key: [{group, key, label, level, parent, values: {date: value}}]} in sheet order."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    out = {}
    for name, skey in SHEETS.items():
        if name not in wb.sheetnames:
            continue
        rows_out = []
        group, cols, parent, block, sub = None, {}, None, None, None
        seen = set()
        for row in wb[name].iter_rows(values_only=True):
            cells = list(row)
            first = cells[0] if cells else None
            label = str(first) if first is not None else ""
            low = slug(label).replace("_", " ")
            if label and all(c is None for c in cells[1:]):
                g = GROUPS.get(re.sub(r"\s+", " ", unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode().strip().lower()))
                if g:
                    group, cols, parent, block, sub, seen = g, {}, None, None, None, set()
                    continue
                if re.match(r"^\d+(\.\d+)*\.\s", label.strip()):   # "2. Familias - Total", "2.1. Familias - En UVA"
                    block, sub = slug(label), None
                    continue
                if label.strip().startswith(".."):                # ".. Ratio de irregularidad"
                    sub = slug(label.strip(". "))
                    continue
                continue
            dates = {i: _as_date(c) for i, c in enumerate(cells) if i > 0 and _as_date(c) is not None}
            if len(dates) > 5:
                cols, parent = dates, None
                if skey == "npl_lines" and group is None:
                    group = "system"
                continue
            if not cols or group is None or not label.strip() or low.startswith("fuente"):
                continue
            vals = {}
            for i, d in cols.items():
                v = _num(cells[i]) if i < len(cells) else None
                if v is not None:
                    vals[d] = v                      # later columns (monthly) win over yearly ones
            indent = len(label) - len(label.lstrip(" "))
            base = slug(label)
            if not base:
                continue
            if indent == 0 and base != "en_moneda_nacional":
                parent = base
                key = base
            else:
                key = f"{parent}/{base}" if parent else base
            prefix = "/".join(p for p in (block, sub) if p)
            if prefix:
                key = f"{prefix}/{key}"
            k, n = key, 2
            while k in seen:
                k, n = f"{key}_{n}", n + 1
            seen.add(k)
            rows_out.append({"group": group, "key": k, "label": label.strip(), "level": 0 if indent == 0 else 1,
                             "values": vals})
        out[skey] = rows_out
    return out


def to_long(parsed: dict) -> pd.DataFrame:
    rows = []
    for skey, items in parsed.items():
        for it in items:
            sid = f"bcrabk:{skey}|{it['group']}|{it['key']}"
            rows += [(d, sid, v) for d, v in it["values"].items()]
    return pd.DataFrame(rows, columns=["date", "series_id", "value"])


def fetch(ids, content: bytes | None = None) -> FetchResult:
    global LAST_CONTENT
    try:
        content = content if content is not None else download()
        LAST_CONTENT = content
        data = to_long(parse(content))
    except Exception as exc:
        return FetchResult(data=pd.DataFrame(columns=["date", "series_id", "value"]),
                           errors={sid: f"banking annex: {exc}" for sid in ids})
    have = set(data.series_id)
    errors = {sid: "not in the banking annex" for sid in ids if sid not in have}
    data = data[data.series_id.isin(set(ids))].sort_values(["series_id", "date"])
    meta = {sid: {"units": None, "time_index_end": g.date.max().strftime("%Y-%m-%d")} for sid, g in data.groupby("series_id")}
    return FetchResult(data=data.reset_index(drop=True), meta=meta, errors=errors)


# ---------- the standardized balance sheet, for the site's table ----------

LABELS_EN = {
    "activo": "Total assets", "disponibilidades": "Cash and accounts at the BCRA",
    "titulos_publicos": "Government securities", "lebac_nobac_leliq": "BCRA notes (LEBAC, NOBAC, LELIQ)",
    "tenencia_por_cartera_propia": "Own portfolio", "por_operaciones_de_pase_activo": "Received in reverse repos",
    "titulos_privados": "Private securities", "prestamos": "Loans", "sector_publico": "Public sector",
    "sector_privado": "Private sector", "sector_financiero": "Financial sector",
    "previsiones_por_prestamos": "Loan-loss provisions",
    "otros_creditos_por_intermediacion_financiera": "Other claims from financial intermediation",
    "on_y_os": "Corporate and subordinated bonds", "fideicomisos_sin_cotizacion": "Unlisted trusts",
    "compensacion_a_recibir": "Compensation receivable (2002 pesification)", "otros": "Other",
    "bienes_en_locacion_financiera_leasing": "Leasing", "participacion_en_otras_sociedades": "Equity holdings",
    "bienes_de_uso_y_diversos": "Fixed and other assets", "filiales_en_el_exterior": "Branches abroad",
    "otros_activos": "Other assets", "pasivo": "Total liabilities", "depositos": "Deposits",
    "cuenta_corriente": "Current accounts", "caja_de_ahorros": "Savings accounts", "plazo_fijo": "Time deposits",
    "cedro": "CEDRO (rescheduled deposits, 2002)",
    "otras_obligaciones_por_intermediacion_financiera": "Other liabilities from financial intermediation",
    "obligaciones_interfinancieras": "Interbank liabilities", "obligaciones_con_el_bcra": "Liabilities to the BCRA",
    "obligaciones_negociables": "Corporate bonds issued", "lineas_de_prestamos_del_exterior": "Foreign credit lines",
    "obligaciones_subordinadas": "Subordinated debt", "otros_pasivos": "Other liabilities",
    "patrimonio_neto": "Net equity",
}
LEVEL2 = {"tenencia_por_cartera_propia", "por_operaciones_de_pase_activo", "cuenta_corriente", "caja_de_ahorros",
          "plazo_fijo", "cedro"}
BAL_NOTES = [
    "Cash and accounts at the BCRA include guarantee accounts.",
    "Securities received in reverse repos: book value, all counterparties; from January 2018 they are no longer on the balance sheet (IFRS).",
    "Deposits by sector exclude accrued interest, CER adjustments and IFRS adjustments, so they do not add up to total deposits.",
    "Since January 2020 banks report in constant currency (IAS 29), restated to each month's prices.",
    "Lines in italics are computed here: the difference between a line and the sub-lines the BCRA publishes for it (for government securities, everything other than BCRA notes, mostly Treasury securities).",
]

RESIDUAL_LABELS = {
    "Government securities": ("Treasury and other government securities", "first"),
    "Deposits": ("Accrued interest and adjustments", "last"),
    "Private sector": ("Other deposits", "last"),
}


def add_residuals(rows: list[dict]) -> list[dict]:
    """Where a line's published sub-lines do not add up to it, add the difference
    as a computed sub-line (e.g. Treasury securities = government securities
    minus BCRA notes, the only sub-line the BCRA publishes)."""
    out = []
    for i, r in enumerate(rows):
        out.append(r)
        if r["kind"] == "total" or r["values"] is None:
            continue
        kids, j = [], i + 1
        while j < len(rows) and rows[j]["level"] > r["level"] and rows[j]["side"] == r["side"]:
            if rows[j]["level"] == r["level"] + 1:
                kids.append(rows[j])
            j += 1
        if not kids:
            continue
        res = []
        for t, v in enumerate(r["values"]):
            if v is None:
                res.append(None)
                continue
            res.append(v - sum((k["values"][t] or 0) for k in kids))
        if not any(x is not None and abs(x) > max(1.0, 0.005 * abs(v or 0)) for x, v in zip(res, r["values"])):
            continue
        label, where = RESIDUAL_LABELS.get(r["label"], ("Other, not itemized", "last"))
        row = {"side": r["side"], "level": r["level"] + 1, "kind": "computed", "label": label,
               "values": [round(x, 3) if x is not None else None for x in res]}
        r["_residual"] = (row, where)
    # Place the computed lines first or last among their parent's sub-lines.
    final = []
    for i, r in enumerate(out):
        final.append(r)
        if "_residual" not in r:
            continue
        row, where = r.pop("_residual")
        if where == "first":
            final.append(row)
        else:
            r["_pending"] = row
    result, stack = [], []
    for r in final:
        while stack and r["level"] <= stack[-1]["level"]:
            result.append(stack.pop()["_pending"])
        if "_pending" in r:
            stack.append(r)
        result.append(r)
    while stack:
        result.append(stack.pop()["_pending"])
    for r in result:
        r.pop("_pending", None)
    return result


def balance_tables(parsed: dict) -> dict:
    """{group: {year: {year, dates, fx, rows, notes}}} in the layout the balance-sheet card reads.

    Rows come from the all-currency balance sheet ("bal"); assets until "Pasivo",
    then liabilities and net equity. Values in millions of current pesos.
    """
    out: dict = {}
    by_group: dict = {}
    for it in parsed.get("bal", []):
        by_group.setdefault(it["group"], []).append(it)
    for g, items in by_group.items():
        dates = sorted({d for it in items for d in it["values"]})
        side = "A"
        rows = []
        for it in items:
            k = it["key"].split("/")[-1]
            if it["level"] == 0 and k in ("memo",):
                break
            if it["level"] == 0 and k.startswith("activo_neteado"):
                break
            if it["level"] == 0 and k == "pasivo":
                side = "L"
            kind = "total" if it["level"] == 0 and k in ("activo", "pasivo") else "item"
            label = LABELS_EN.get(slug(k), re.sub(r"(?<=[a-zA-Z])\d+$", "", it["label"]))
            level = 2 if slug(k) in LEVEL2 else it["level"]
            rows.append({"side": side, "level": level, "kind": kind, "label": label,
                         "values": [it["values"].get(d) for d in dates]})
        rows = add_residuals(rows)
        # Totals at the bottom of each side, like the BCRA table.
        a = [r for r in rows if r["side"] == "A"]
        l = [r for r in rows if r["side"] == "L"]
        a = [r for r in a if r["kind"] != "total"] + [r for r in a if r["kind"] == "total"]
        pas = [r for r in l if r["kind"] == "total"]
        rest = [r for r in l if r["kind"] != "total"]
        eq = [r for r in rest if r["label"] == "Net equity"]
        rest = [r for r in rest if r not in eq]
        for r in pas:
            r["label"] = "Total liabilities"
        for r in a:
            if r["kind"] == "total":
                r["label"] = "Total assets"
        total_le = None
        if pas and eq:
            total_le = {"side": "L", "level": 0, "kind": "total", "label": "Total liabilities + net equity",
                        "values": [(p + e) if p is not None and e is not None else None
                                   for p, e in zip(pas[0]["values"], eq[0]["values"])]}
        rows = a + rest + pas + eq + ([total_le] if total_le else [])
        years: dict = {}
        for j, d in enumerate(dates):
            years.setdefault(d.year, []).append(j)
        out[g] = {}
        for y, idx in years.items():
            out[g][y] = {"year": y, "dates": [dates[j].strftime("%Y-%m-%d") for j in idx], "fx": None,
                         "rows": [{**r, "values": [r["values"][j] for j in idx]} for r in rows], "notes": BAL_NOTES}
    return out


def write_tables(directory) -> list[str]:
    import json
    from pathlib import Path
    if LAST_CONTENT is None:
        return []
    tables = balance_tables(parse(LAST_CONTENT))
    d = Path(directory)
    written = []
    index = {"groups": {}, "group_labels": GROUP_LABELS}
    for g, years in tables.items():
        (d / g).mkdir(parents=True, exist_ok=True)
        index["groups"][g] = {str(y): t["dates"] for y, t in sorted(years.items())}
        for y, t in years.items():
            p = d / g / f"{y}.json"
            text = json.dumps(t, separators=(",", ":"), ensure_ascii=False)
            if not p.exists() or p.read_text(encoding="utf-8") != text:
                p.write_text(text, encoding="utf-8")
                written.append(f"{g}/{y}.json")
    first = next(iter(index["groups"].values()), {})
    index["years"] = first
    (d / "index.json").write_text(json.dumps(index, separators=(",", ":")), encoding="utf-8")
    return written
