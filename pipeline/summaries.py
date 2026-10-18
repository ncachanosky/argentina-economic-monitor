"""Plain-text summaries of the latest data, for readers, search engines and AI assistants.

  latest_lines()   one sentence per chart: "Headline CPI: 1.9% m/m in September 2026
                   (2.1% in August). Source: INDEC. Next release: 12 November 2026."
  llms_txt()       /llms.txt, the convention for a site summary that language models read:
                   every chart with its sentence, its address and its CSV.
  calendar_html()  the release-calendar page: upcoming official releases and whether the
                   latest ones are already in the Monitor.

They read the exported card payloads (_site/data/<id>.json), so they always match the charts.
"""
from __future__ import annotations

import html
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

MONTHS = {"en": ["January", "February", "March", "April", "May", "June", "July", "August", "September",
                 "October", "November", "December"],
          "es": ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
                 "octubre", "noviembre", "diciembre"]}
W = {
    "en": {"in": "in", "source": "Source", "next": "Next release", "mom": "% m/m", "qoq": "% q/q", "yoy": "% y/y",
           "q": "Q", "updated": "Data as of", "csv": "CSV"},
    "es": {"in": "en", "source": "Fuente", "next": "Próximo dato", "mom": "% mensual", "qoq": "% trimestral", "yoy": "% interanual",
           "q": "T", "updated": "Datos al", "csv": "CSV"},
}


def period(d: pd.Timestamp, freq: str, lang: str) -> str:
    if freq == "Q":
        return f"{W[lang]['q']}{(d.month - 1) // 3 + 1} {d.year}"
    if freq in ("Y", "A"):
        return str(d.year)
    if freq == "D":
        return f"{d.day} {MONTHS[lang][d.month - 1]} {d.year}" if lang == "es" else f"{d.day} {MONTHS[lang][d.month - 1]} {d.year}"
    return f"{MONTHS[lang][d.month - 1]} {d.year}" if lang == "en" else f"{MONTHS[lang][d.month - 1]} de {d.year}"


def day(s: str, lang: str) -> str:
    d = pd.Timestamp(s)
    return f"{d.day} {MONTHS[lang][d.month - 1]} {d.year}" if lang == "en" else f"{d.day} de {MONTHS[lang][d.month - 1]} de {d.year}"


def fmt(x: float, lang: str, pct: bool = False) -> str:
    a = abs(x)
    dec = 1 if pct else (0 if a >= 1000 else 1 if a >= 100 else 2)
    s = f"{x:,.{dec}f}"
    if lang == "es":
        s = s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return s.replace("-", "−")


def headline(p: dict) -> dict | None:
    """Value, previous value and their dates for a card's headline series, as the chart shows it."""
    if p.get("kind") != "variants" or not p.get("variants") or not p.get("dates"):
        return None
    hl, df = p.get("headline") or {}, p.get("default") or {}
    var = hl.get("variant") or df.get("variant")
    var = var[0] if isinstance(var, list) else var
    if var not in p["variants"]:
        var = next(iter(p["variants"]))
    tf = hl.get("transform") or df.get("transform") or "level"
    idx = pd.to_datetime(p["dates"])
    s = pd.Series(p["variants"][var]["values"], index=idx, dtype=float)
    if df.get("real") and p.get("deflator"):
        f = p["deflator"]["values"]
        s = (s.iloc[:len(f)] / pd.Series(f, index=idx[:len(f)], dtype=float))
    s = s.dropna()
    freq = p.get("frequency") or "M"
    if p.get("measure") == "rate" or tf not in ("mom", "yoy"):
        tf = "level"
    if tf != "level":
        lag = 1 if tf == "mom" else (4 if freq == "Q" else 12)
        if freq == "D":
            return None
        s = (s / s.shift(lag) - 1).dropna() * 100
    if len(s) < 2:
        return None
    return {"var": var, "tf": tf, "freq": freq, "value": float(s.iloc[-1]), "date": s.index[-1],
            "prev": float(s.iloc[-2]), "prev_date": s.index[-2], "label": p["variants"][var].get("label")}


def sentence(p: dict, lang: str, T) -> str | None:
    h = headline(p)
    if not h:
        return None
    w = W[lang]
    pct = h["tf"] != "level"
    key = "qoq" if (h["tf"] == "mom" and h["freq"] == "Q") else h["tf"]
    unit = w[key] if pct else T(p.get("units") or "")
    v = f"{fmt(h['value'], lang, pct)}{'' if pct else ' '}{unit}".strip()
    pv = f"{fmt(h['prev'], lang, pct)}{'%' if pct else ''}"
    name = T(p.get("short_title") or p.get("title") or p["id"])
    lab = h.get("label")
    if lab and len(p["variants"]) > 1:
        name = f"{name} ({T(lab)})"
    out = (f"{name}: {v} {w['in']} {period(h['date'], h['freq'], lang)} "
           f"({pv} {w['in']} {period(h['prev_date'], h['freq'], lang)}). {w['source']}: {T(p.get('source_label') or '')}.")
    nr = p.get("next_release")
    if nr and nr.get("date"):
        out += f" {w['next']}: {day(nr['date'], lang)}."
    return out


def latest_lines(data_dir: Path, reg, lang: str, content: dict) -> dict[str, list[tuple[str, str, str]]]:
    """topic id -> [(indicator id, title, sentence)], in the site's order."""
    T = (lambda s: content.get(s, s)) if lang == "es" else (lambda s: s)
    out: dict[str, list] = {}
    for ind in reg.indicators:
        if ind.hidden or ind.unlisted:
            continue
        f = data_dir / f"{ind.id}.json"
        if not f.exists():
            continue
        try:
            p = json.loads(f.read_text(encoding="utf-8"))
            s = sentence(p, lang, T)
        except Exception:
            s = None
        if s:
            out.setdefault(ind.topic, []).append((ind.id, T(p.get("title") or ind.id), s))
    return out


def topic_block(lines: list, lang: str) -> str:
    """Static list for a topic page (read by crawlers; the charts replace the page's content)."""
    title = "Latest values" if lang == "en" else "Últimos valores"
    items = "".join(f'<li><a href="#ind-{i}">{html.escape(t)}</a>. {html.escape(s.split(": ", 1)[1])}</li>' for i, t, s in lines)
    return f'<details class="latest-text"><summary>{title}</summary><ul>{items}</ul></details>\n'


def llms_txt(lines: dict, reg, base: str) -> str:
    when = datetime.now(timezone.utc).strftime("%d %B %Y")
    head = [f"# {reg.site.get('title', 'Argentina Economic Monitor')}", "",
            "> Argentina's main economic statistics in one place: activity and GDP, trade and the balance of "
            "payments, prices, money and the central bank, the exchange rate, banks, public accounts and debt, "
            "labor and poverty. Data from INDEC, the BCRA, the Ministry of Economy and other official sources, "
            "updated twice a day and validated before publication. A project of Economic Order "
            "(https://economicorder.substack.com) by Nicolás Cachanosky.", "",
            f"Data as of {when}. Each value is for the period stated, which is not the publication date. "
            "Cite the original source of each series and the Monitor for charts and transformations. "
            f"Methodology: {base}methodology/ · Release calendar: {base}calendar/ · Spanish: {base}es/", ""]
    body = []
    for t in reg.topics:
        if t["id"] not in lines:
            continue
        body += [f"## {t['title']}", ""]
        for i, title, s in lines[t["id"]]:
            body.append(f"- [{title}]({base}{t['slug']}/#ind-{i}): {s.split(': ', 1)[1]} [CSV]({base}data/{i}.csv)")
        body.append("")
    return "\n".join(head + body)


def calendar_html(reg, status: dict, lang: str, content: dict, root: str, today: date | None = None) -> str:
    """Upcoming official releases (next 75 days) and the last 30 days with their status in the Monitor."""
    T = (lambda s: content.get(s, s)) if lang == "es" else (lambda s: s)
    today = today or (datetime.now(timezone.utc) - timedelta(hours=3)).date()     # Buenos Aires
    by_release: dict[str, list] = {}
    topic_slug = {t["id"]: t["slug"] for t in reg.topics}
    for ind in reg.indicators:
        if ind.release and not ind.hidden and not ind.unlisted:
            by_release.setdefault(ind.release, []).append(ind)
    rows_up, rows_past = [], []
    for key, cal in reg.releases.items():
        inds = by_release.get(key, [])
        if not inds:
            continue
        last = max((status.get(i, {}).get("last_obs") or "" for ind in inds for i in ind.input_ids()), default="")
        links = ", ".join(f'<a href="{root}{topic_slug.get(ind.topic, "")}/#ind-{ind.id}">{html.escape(T(ind.short_title or ind.title))}</a>'
                          for ind in inds[:6])
        for d in cal.get("dates", []):
            dd = date.fromisoformat(d["date"])
            per = pd.Timestamp(d["period"] + "-01")
            per_s = period(per, cal.get("frequency", "M"), lang)
            row = (dd, html.escape(cal.get("name") or key), per_s, links, last[:7] >= d["period"])
            if today <= dd <= today + timedelta(days=75):
                rows_up.append(row)
            elif today - timedelta(days=30) <= dd < today:
                rows_past.append(row)
    L = {"en": {"up": "Coming up", "past": "Last 30 days", "date": "Date", "rel": "Release", "per": "Data for",
                "on": "In the Monitor", "in": "In the Monitor", "yes": "Published", "no": "Not yet in the Monitor",
                "today": "today", "note": "Official release dates from INDEC's published calendar. The Monitor checks every source twice a day; "
                "a release usually appears within hours. If a release is marked “not yet in the Monitor” a day after its date, "
                "the source file has not been updated or failed our checks."},
         "es": {"up": "Próximas publicaciones", "past": "Últimos 30 días", "date": "Fecha", "rel": "Publicación", "per": "Datos de",
                "on": "En el Monitor", "in": "En el Monitor", "yes": "Publicado", "no": "Todavía no está en el Monitor",
                "today": "hoy", "note": "Fechas oficiales del calendario de difusión del INDEC. El Monitor revisa todas las fuentes dos veces por día; "
                "una publicación suele aparecer en pocas horas. Si un dato figura como «todavía no está en el Monitor» un día después de su fecha, "
                "el archivo de la fuente no se actualizó o no pasó nuestros controles."}}[lang]

    def table(rows, past):
        rows = sorted(rows, key=lambda r: r[0], reverse=past)
        body = []
        for dd, name, per_s, links, have in rows:
            d_txt = L["today"] if dd == today else day(dd.isoformat(), lang)
            state = (f'<span class="cal-ok">{L["yes"]}</span>' if have else f'<span class="cal-late">{L["no"]}</span>') if past else ""
            body.append(f"<tr><td>{d_txt}</td><td>{name}</td><td>{per_s}</td><td>{links}</td>"
                        + (f"<td>{state}</td>" if past else "") + "</tr>")
        head = (f"<tr><th>{L['date']}</th><th>{L['rel']}</th><th>{L['per']}</th><th>{L['on']}</th>"
                + (f"<th></th>" if past else "") + "</tr>")
        return f'<div class="table-scroll"><table class="cal-table"><thead>{head}</thead><tbody>{"".join(body)}</tbody></table></div>'

    return (f'<article class="prose cal">\n<p>{L["note"]}</p>\n<h2 id="upcoming">{L["up"]}</h2>\n{table(rows_up, False)}\n'
            f'<h2 id="recent">{L["past"]}</h2>\n{table(rows_past, True)}\n</article>\n')
