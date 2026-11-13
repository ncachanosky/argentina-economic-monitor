"""RSS feeds: a weekly digest (Mondays, about 07:30 Buenos Aires) and one item per release.

  feed.xml      weekly digest: the past week's new data with their latest values, the cards
                whose official release date passed without new figures ("Late"), and the
                coming week's scheduled releases (INDEC and the BCRA's reports)
  releases.xml  one item each time a card gets a new month or quarter (last three weeks)

Spanish versions under es/. Links carry ?ref=rss so visits from the feeds show in the
site's analytics (GoatCounter records `ref` as the referrer); readers' fetches of the
feeds themselves cannot be counted on GitHub Pages.

The digest for a week (Monday to Sunday, Buenos Aires) is written once, by the first
update run on the following Monday or Tuesday, to data/feed/weekly.json, which the update
workflow commits; the build renders the feeds from that file and from the latest data.

    python -m pipeline.feed --record     # add last week's digest if it is due
"""
from __future__ import annotations

import argparse
import html
import json
import tempfile
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .registry import ROOT

BA = ZoneInfo("America/Argentina/Buenos_Aires")
WEEKLY = ROOT / "data" / "feed" / "weekly.json"
KEEP = 26                     # digests kept in the feed (about six months)
DUE_HOUR = 7                  # Monday 07:00 Buenos Aires

MONTHS = {"en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
          "es": ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]}
T = {
    "en": {"week": "Week of {a} to {b}, {y}", "n_new": "{n} new releases", "one_new": "1 new release", "none_new": "no new releases",
           "new": "New data", "late": "Late: the release date passed and the figures are not on the Monitor yet",
           "coming": "Coming this week", "nothing": "Nothing.", "site": "Argentina Economic Monitor",
           "weekly_title": "Argentina Economic Monitor: weekly summary",
           "weekly_desc": "Every Monday: the past week's new Argentine economic data, what is late and what comes next.",
           "rel_title": "Argentina Economic Monitor: new data",
           "rel_desc": "One item each time a chart on the Argentina Economic Monitor gets new official data.",
           "rel_item": "{t}: {p}", "data_for": "Data for {p}", "mm": "m/m", "qq": "q/q", "yy": "y/y", "latest": "latest",
           "calendar": "Full release calendar"},
    "es": {"week": "Semana del {a} al {b} de {y}", "n_new": "{n} publicaciones nuevas", "one_new": "1 publicación nueva",
           "none_new": "sin publicaciones nuevas", "new": "Datos nuevos",
           "late": "Demorados: pasó la fecha de publicación y los datos todavía no están en el Monitor",
           "coming": "Esta semana", "nothing": "Nada.", "site": "Monitor de la Economía Argentina",
           "weekly_title": "Monitor de la Economía Argentina: resumen semanal",
           "weekly_desc": "Cada lunes: los datos económicos argentinos de la semana pasada, lo demorado y lo que viene.",
           "rel_title": "Monitor de la Economía Argentina: datos nuevos",
           "rel_desc": "Un ítem cada vez que un gráfico del Monitor de la Economía Argentina recibe datos oficiales nuevos.",
           "rel_item": "{t}: {p}", "data_for": "Datos de {p}", "mm": "m/m", "qq": "t/t", "yy": "i.a.", "latest": "último",
           "calendar": "Calendario completo de publicaciones"},
}


def _period(iso: str, freq: str, lang: str) -> str:
    y, m = int(iso[:4]), int(iso[5:7])
    if freq == "Q":
        return f"{'T' if lang == 'es' else 'Q'}{(m - 1) // 3 + 1} {y}"
    if freq == "S":
        return f"{'S' if lang == 'es' else 'H'}{(m - 1) // 6 + 1} {y}"
    if freq in ("Y", "A"):
        return str(y)
    return f"{MONTHS[lang][m - 1]} {y}"


def _day(d, lang: str) -> str:
    return f"{d.day} de {MONTHS[lang][d.month - 1]}" if lang == "es" else f"{MONTHS[lang][d.month - 1]} {d.day}"


def _span(a, b, lang: str) -> str:
    """'Nov 2 to Nov 8, 2026' / 'del 2 al 8 de nov de 2026'."""
    if lang == "es":
        first = str(a.day) if a.month == b.month else _day(a, lang)
        return f"Semana del {first} al {_day(b, lang)} de {b.year}"
    return f"Week of {_day(a, lang)} to {_day(b, lang)}, {b.year}"


def _num(v: float, digits: int, lang: str) -> str:
    s = f"{v:+,.{digits}f}"
    if lang == "es":
        s = s.replace(",", "\0").replace(".", ",").replace("\0", ".")
    return s.replace("-", "−")


def _link(base: str, lang: str, topic_slug: str, ind_id: str) -> str:
    return f"{base}{'es/' if lang == 'es' else ''}{topic_slug}/?ref=rss#ind-{ind_id}"


def _changes(row: dict, lang: str) -> str:
    """'−0.1% m/m, −1.7% y/y' from a home-table row (latest month with a value)."""
    out = []
    for key, lab in (("mm", "qq" if row.get("freq") == "Q" else "mm"), ("yy", "yy")):
        blk = row.get(key) or {}
        vals = [(d, v) for d, v in (blk.get("values") or {}).items() if v]
        if not vals:
            continue
        d, v = max(vals)
        unit = "%" if blk.get("kind") == "pct" else " pp"
        out.append(f"{_num(v[0], 1, lang)}{unit} {T[lang][lab]}")
    return ", ".join(out)


def _context(data: Path) -> dict:
    from . import registry
    from .build import load_content
    reg = registry.load()
    from .build import load_i18n
    es_topics = (load_i18n("es").get("topics") or {})
    manifest = json.loads((data / "manifest.json").read_text(encoding="utf-8"))
    return {
        "reg": reg,
        "titles": {m["id"]: m["title"] for m in manifest["indicators"]},
        "topics": [(t["id"], {"en": t["title"], "es": (es_topics.get(t["id"]) or {}).get("title", t["title"])}) for t in reg.topics],
        "base": reg.site.get("site_url", ""),
        "slug": {t["id"]: t["slug"] for t in reg.topics},
        "updates": json.loads((data / "updates.json").read_text(encoding="utf-8")),
        "dash": {r["ind"]: r for r in json.loads((data / "dashboard.json").read_text(encoding="utf-8")).get("rows", [])},
        "manifest": manifest,
        "es": load_content("es"),
        "data": data,
    }


def _tr(ctx: dict, s: str, lang: str) -> str:
    return ctx["es"].get(s, s) if lang == "es" else s


# ---------- weekly digest ----------

def week_bounds(now: datetime) -> tuple[datetime, datetime]:
    """Monday and Sunday (dates, Buenos Aires) of the week before `now`'s week."""
    today = now.astimezone(BA).date()
    this_monday = today - timedelta(days=today.weekday())
    return this_monday - timedelta(days=7), this_monday - timedelta(days=1)


def due(now: datetime, items: list) -> bool:
    local = now.astimezone(BA)
    if local.weekday() > 1 or (local.weekday() == 0 and local.hour < DUE_HOUR):
        return False                                   # Mondays from 07:00, or Tuesday if Monday's run was missed
    a, _ = week_bounds(now)
    return not any(i.get("week_start") == a.isoformat() for i in items)


def digest(ctx: dict, now: datetime) -> dict:
    a, b = week_bounds(now)
    U = ctx["updates"]
    new = [r for r in U.get("recent_all", U.get("recent", [])) if a.isoformat() <= r["seen"] <= b.isoformat()]
    new.sort(key=lambda r: (r["seen"], r["title"]))
    # Late: the official date for the next figures has passed (a day of grace), as on the cards.
    today = now.astimezone(BA).date().isoformat()
    late = []
    for m in ctx["manifest"]["indicators"]:
        p = ctx["data"] / f"{m['id']}.json"
        if not p.exists():
            continue
        nr = json.loads(p.read_text(encoding="utf-8")).get("next_release")
        if nr and (datetime.fromisoformat(today) - datetime.fromisoformat(nr["date"])).days >= 1:
            late.append({"id": m["id"], "title": m["short_title"] if "short_title" in m else m["title"], "topic": m["topic"],
                         "date": nr["date"], "period": nr["period"], "frequency": nr.get("frequency", "M")})
    this_monday = b + timedelta(days=1)
    coming = [r for r in U.get("ahead", []) if this_monday.isoformat() <= r["date"] <= (this_monday + timedelta(days=6)).isoformat()]
    item = {"week_start": a.isoformat(), "week_end": b.isoformat(),
            "published": now.astimezone(timezone.utc).isoformat(timespec="seconds")}
    for lang in ("en", "es"):
        L = T[lang]
        n = len(new)
        count = L["one_new"] if n == 1 else L["none_new"] if n == 0 else L["n_new"].format(n=n)
        title = f"{_span(a, b, lang)}: {count}"
        parts = [f"<h3>{L['new']}</h3>"]
        if new:
            for tid, tname in ctx["topics"]:
                rows = [r for r in new if r["topic"] == tid]
                if not rows:
                    continue
                li = []
                for r in rows:
                    row = ctx["dash"].get(r["id"])
                    ch = _changes(row, lang) if row else ""
                    name = ctx["titles"].get(r["id"], r["title"])
                    li.append(f'<li><a href="{_link(ctx["base"], lang, ctx["slug"].get(r["topic"], ""), r["id"])}">'
                              f'{html.escape(_tr(ctx, name, lang))}</a> ({_period(r["period"], r["frequency"], lang)})'
                              f'{": " + ch if ch else ""}</li>')
                parts.append(f"<p><b>{html.escape(tname[lang])}</b></p><ul>" + "".join(li) + "</ul>")
        else:
            parts.append(f"<p>{L['nothing']}</p>")
        if late:
            parts.append(f"<h3>{L['late']}</h3><ul>" + "".join(
                f'<li><a href="{_link(ctx["base"], lang, ctx["slug"].get(x["topic"], ""), x["id"])}">{html.escape(_tr(ctx, x["title"], lang))}</a>'
                f' ({_period(x["period"], x["frequency"], lang)}, {_day(datetime.fromisoformat(x["date"]).date(), lang)})</li>' for x in late) + "</ul>")
        parts.append(f"<h3>{L['coming']}</h3>")
        if coming:
            parts.append("<ul>" + "".join(
                f'<li>{_day(datetime.fromisoformat(c["date"]).date(), lang)}: {html.escape(c.get("publisher", "INDEC"))}, '
                f'{html.escape(c["name"])} ({_period(c["period"], c.get("frequency", "M"), lang)})</li>' for c in coming) + "</ul>")
        else:
            parts.append(f"<p>{L['nothing']}</p>")
        parts.append(f'<p><a href="{ctx["base"]}{"es/" if lang == "es" else ""}calendar/?ref=rss">{L["calendar"]}</a></p>')
        item[lang] = {"title": title, "html": "".join(parts),
                      "link": f'{ctx["base"]}{"es/" if lang == "es" else ""}?ref=rss&week={a.isoformat()}'}
    return item


def record(now: datetime | None = None) -> bool:
    """Add last week's digest to data/feed/weekly.json if it is due. Returns True when one was added."""
    now = now or datetime.now(timezone.utc)
    items = json.loads(WEEKLY.read_text(encoding="utf-8")) if WEEKLY.exists() else []
    if not due(now, items):
        return False
    from . import export
    with tempfile.TemporaryDirectory() as tmp:
        export.export(Path(tmp))
        item = digest(_context(Path(tmp)), now)
    items = ([item] + items)[:KEEP]
    WEEKLY.parent.mkdir(parents=True, exist_ok=True)
    WEEKLY.write_text(json.dumps(items, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"feed: weekly digest for {item['week_start']} to {item['week_end']}")
    return True


# ---------- rendering ----------

def _rss(title: str, link: str, desc: str, self_url: str, lang: str, items: list[dict]) -> str:
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"><channel>',
           f"<title>{html.escape(title)}</title><link>{html.escape(link)}</link>",
           f"<description>{html.escape(desc)}</description><language>{'es-AR' if lang == 'es' else 'en'}</language>",
           f'<atom:link href="{html.escape(self_url)}" rel="self" type="application/rss+xml"/>']
    for it in items:
        out.append("<item>"
                   f"<title>{html.escape(it['title'])}</title><link>{html.escape(it['link'])}</link>"
                   f'<guid isPermaLink="false">{html.escape(it["guid"])}</guid>'
                   f"<pubDate>{format_datetime(it['date'])}</pubDate>"
                   f"<description><![CDATA[{it['html']}]]></description></item>")
    out.append("</channel></rss>")
    return "\n".join(out) + "\n"


def render(out: Path) -> None:
    """Write feed.xml and releases.xml (and the Spanish ones under es/) into the built site."""
    ctx = _context(out / "data")
    weekly = json.loads(WEEKLY.read_text(encoding="utf-8")) if WEEKLY.exists() else []
    base = ctx["base"]
    for lang in ("en", "es"):
        root = out / ("es" if lang == "es" else "")
        if not root.exists():
            continue
        L = T[lang]
        pre = "es/" if lang == "es" else ""
        w_items = [{"title": i[lang]["title"], "link": i[lang]["link"], "guid": f"aem-week-{i['week_start']}-{lang}",
                    "date": datetime.fromisoformat(i["published"]), "html": i[lang]["html"]} for i in weekly]
        (root / "feed.xml").write_text(_rss(L["weekly_title"], f"{base}{pre}", L["weekly_desc"], f"{base}{pre}feed.xml", lang, w_items),
                                       encoding="utf-8")
        r_items = []
        for r in sorted(ctx["updates"].get("recent_all", ctx["updates"].get("recent", [])), key=lambda r: (r["seen"], r["title"]), reverse=True):
            row = ctx["dash"].get(r["id"])
            ch = _changes(row, lang) if row else ""
            per = _period(r["period"], r["frequency"], lang)
            link = _link(base, lang, ctx["slug"].get(r["topic"], ""), r["id"])
            title = _tr(ctx, ctx["titles"].get(r["id"], r["title"]), lang)
            r_items.append({"title": L["rel_item"].format(t=title, p=per), "link": link,
                            "guid": f"aem-{r['id']}-{r['period']}-{lang}",
                            "date": datetime.fromisoformat(r["seen"]).replace(hour=12, tzinfo=timezone.utc),
                            "html": f'<p>{L["data_for"].format(p=per)}{": " + ch if ch else ""}. <a href="{link}">{html.escape(title)}</a></p>'})
        (root / "releases.xml").write_text(_rss(L["rel_title"], f"{base}{pre}", L["rel_desc"], f"{base}{pre}releases.xml", lang, r_items),
                                           encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--record", action="store_true", help="add last week's digest if it is due")
    args = ap.parse_args(argv)
    if args.record:
        record()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
