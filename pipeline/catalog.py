"""The data page: every card's files with stable links, a machine-readable catalog of every
series, and two bulk downloads, rebuilt at each update.

Written after the export (build.py), so it lists the files actually published:
  data/catalog.json      every card (files, source, frequency, units, latest observation) and every
                         stored series (source id, source, frequency, label, cards, latest observation,
                         its revision history)
  data/all_data.zip      every card's CSV (English headers), its JSON, the tables of the table cards,
                         catalog.json and a README
  data/vintages.zip      every stored vintage of every series (data/vintages/ in the repository)
The page's HTML replaces the <!--DATA_CATALOG--> marker in data/index.html and es/data/index.html.
"""
from __future__ import annotations

import html
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from . import store
from .registry import ROOT

FREQ = {"en": {"D": "Daily", "W": "Weekly", "M": "Monthly", "Q": "Quarterly", "S": "Semiannual", "Y": "Annual"},
        "es": {"D": "Diaria", "W": "Semanal", "M": "Mensual", "Q": "Trimestral", "S": "Semestral", "Y": "Anual"}}
L = {
    "en": {"card": "Chart", "src": "Source", "freq": "Frequency", "latest": "Latest", "files": "Files",
           "table": "table", "count": "{c} charts and {s} series. Updated {when} (Buenos Aires time).",
           "bulk": "Bulk downloads", "all": "All data", "all_d": "every chart's CSV and JSON, the tables behind the table charts, and the catalog",
           "vint": "Revision histories", "vint_d": "every value each series has had, with the date the Monitor received it",
           "cat": "Catalog (JSON)", "cat_d": "every chart and every stored series, with sources, frequencies, latest observations and file paths"},
    "es": {"card": "Gráfico", "src": "Fuente", "freq": "Frecuencia", "latest": "Último dato", "files": "Archivos",
           "table": "tabla", "count": "{c} gráficos y {s} series. Actualizado {when} (hora de Buenos Aires).",
           "bulk": "Descargas completas", "all": "Todos los datos", "all_d": "el CSV y el JSON de cada gráfico, las tablas detrás de los gráficos en tabla, y el catálogo",
           "vint": "Historiales de revisiones", "vint_d": "cada valor que tuvo cada serie, con la fecha en que el Monitor lo recibió",
           "cat": "Catálogo (JSON)", "cat_d": "cada gráfico y cada serie guardada, con fuentes, frecuencias, últimos datos y rutas de archivo"},
}
README = """Argentina Economic Monitor - all data
=====================================
Built {when} UTC from https://ncachanosky.github.io/argentina-economic-monitor/

<card>.csv      the chart's series as published on the site (English headers; *.es.csv in Spanish)
<card>.json     the chart's data as the site reads it (dates, variants, units, sources)
tables/         the tables behind the table charts (one folder per source)
catalog.json    every chart and every stored series: source, frequency, units, latest observation, files

Revision histories: vintages.zip, or data/vintages/ in the repository
(https://github.com/ncachanosky/argentina-economic-monitor).
How every number is calculated: https://ncachanosky.github.io/argentina-economic-monitor/methodology/
Cite: see https://ncachanosky.github.io/argentina-economic-monitor/about/#cite
"""


def _period(iso: str | None, freq: str | None) -> str:
    """2026-08 for a month, 2026-Q2 for a quarter, 2026 for a year, the full date otherwise."""
    if not iso:
        return ""
    y, m = iso[:4], int(iso[5:7])
    return {"M": f"{y}-{m:02d}", "Q": f"{y}-Q{(m - 1) // 3 + 1}", "S": f"{y}-H{(m - 1) // 6 + 1}", "Y": y}.get(freq or "", iso[:10])


def _mb(p: Path) -> str:
    return f"{p.stat().st_size / 1e6:.1f} MB" if p.exists() else ""


def build(out: Path, reg, status: dict) -> dict:
    """Write catalog.json and the two archives into out (the site's data/ folder); return the catalog."""
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    listed = {m["id"]: m for m in manifest["indicators"]}
    topics = {t["id"]: t for t in reg.topics}
    cards = []
    for ind in reg.indicators:
        m = listed.get(ind.id)
        if not m or ind.topic not in topics:
            continue
        files = [f for f in (f"{ind.id}.csv", f"{ind.id}.es.csv", f"{ind.id}.json") if (out / f).exists()]
        tbl = (ind.table or {}).get("source") if getattr(ind, "table", None) else None
        if tbl and (out / "tables" / tbl).is_dir():
            files += [f"tables/{tbl}/{p.name}" for p in sorted((out / "tables" / tbl).glob("*.json"))
                      if p.name == (ind.table or {}).get("file") or not (ind.table or {}).get("file")]
        cards.append({"id": ind.id, "title": ind.title, "short_title": ind.short_title, "topic": ind.topic,
                      "page": f"{topics[ind.topic]['slug']}/#ind-{ind.id}", "source": ind.source_label,
                      "frequency": ind.frequency, "units": ind.units, "latest": m.get("last_obs"), "files": files,
                      "series": [s for s in ind.input_ids()] if hasattr(ind, "input_ids") else []})
    meta = reg.series_meta()
    series = []
    for sid, sm in sorted(meta.items()):
        st = status.get(sid, {})
        vp = store._path(sid)
        series.append({"id": sid, "source": sm.get("source"), "frequency": sm.get("frequency"), "label": sm.get("label"),
                       "latest": st.get("last_obs"), "status": st.get("status"),
                       "cards": [c["id"] for c in cards if sid in c["series"]],
                       "vintages": f"data/vintages/{vp.name}" if vp.exists() else None})
    when = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cat = {"built_at": when, "base_url": "https://ncachanosky.github.io/argentina-economic-monitor/data/",
           "cards": cards, "series": series}
    (out / "catalog.json").write_text(json.dumps(cat, ensure_ascii=False, indent=1), encoding="utf-8")
    # Archives: fixed timestamps, so an unchanged file gives the same bytes.
    stamp = (2026, 1, 1, 0, 0, 0)
    with zipfile.ZipFile(out / "all_data.zip", "w", zipfile.ZIP_DEFLATED) as z:
        names = sorted({f for c in cards for f in c["files"]} | {"catalog.json", "manifest.json"})
        for f in names:
            p = out / f
            if p.exists():
                z.writestr(zipfile.ZipInfo(f"argentina-economic-monitor/{f}", stamp), p.read_bytes(), zipfile.ZIP_DEFLATED)
        z.writestr(zipfile.ZipInfo("argentina-economic-monitor/README.txt", stamp), README.format(when=when[:16].replace("T", " ")), zipfile.ZIP_DEFLATED)
    vdir = ROOT / "data" / "vintages"
    with zipfile.ZipFile(out / "vintages.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(vdir.glob("*.csv")):
            z.writestr(zipfile.ZipInfo(f"vintages/{p.name}", stamp), p.read_bytes(), zipfile.ZIP_DEFLATED)
    return cat


def page_html(cat: dict, reg, lang: str, content: dict, root: str, data: str, topic_titles: dict | None = None) -> str:
    """The list on the data page: bulk downloads, then every card by page."""
    T = (lambda s: content.get(s, s)) if lang == "es" else (lambda s: s)
    t = L[lang]
    esc = html.escape
    when = datetime.fromisoformat(cat["built_at"]).astimezone(timezone.utc)
    ba = (when.replace(tzinfo=None) - (datetime(2000, 1, 1, 3) - datetime(2000, 1, 1))).strftime("%Y-%m-%d %H:%M")
    out = [f'<p class="data-count">{esc(t["count"].format(c=len(cat["cards"]), s=len(cat["series"]), when=ba))}</p>',
           f'<h2 id="bulk">{t["bulk"]}</h2><ul class="data-bulk">',
           f'<li><a href="{data}all_data.zip" download>{t["all"]}</a> (ZIP, {_mb(Path(cat["_out"]) / "all_data.zip")}): {t["all_d"]}.</li>',
           f'<li><a href="{data}vintages.zip" download>{t["vint"]}</a> (ZIP, {_mb(Path(cat["_out"]) / "vintages.zip")}): {t["vint_d"]}.</li>',
           f'<li><a href="{data}catalog.json">{t["cat"]}</a>: {t["cat_d"]}.</li></ul>']
    by_topic: dict = {}
    for c in cat["cards"]:
        by_topic.setdefault(c["topic"], []).append(c)
    for tp in reg.topics:
        cs = by_topic.get(tp["id"])
        if not cs:
            continue
        title = ((topic_titles or {}).get(tp["id"], {}).get("title") or T(tp["title"])) if lang == "es" else tp["title"]
        out.append(f'<h2 id="t-{tp["slug"]}">{esc(title)}</h2>')
        out.append(f'<div class="data-wrap"><table class="data-table"><thead><tr><th>{t["card"]}</th><th>{t["src"]}</th>'
                   f'<th>{t["freq"]}</th><th>{t["latest"]}</th><th>{t["files"]}</th></tr></thead><tbody>')
        for c in cs:
            links = []
            for f in c["files"]:
                lab = ("CSV (ES)" if f.endswith(".es.csv") else "CSV" if f.endswith(".csv") else
                       "JSON" if not f.startswith("tables/") else f'{t["table"]}: {f.rsplit("/", 1)[-1]}')
                if lang == "es" and lab == "CSV (ES)":
                    lab = "CSV"
                elif lang == "es" and lab == "CSV":
                    lab = "CSV (EN)"
                links.append(f'<a href="{data}{esc(f)}">{esc(lab)}</a>')
            out.append(f'<tr><td><a href="{root}{esc(c["page"])}">{esc(T(c["title"]))}</a><br><code>{esc(c["id"])}</code></td>'
                       f'<td>{esc(T(c["source"] or ""))}</td><td>{FREQ[lang].get(c["frequency"], c["frequency"] or "")}</td>'
                       f'<td class="data-latest">{esc(_period(c["latest"], c["frequency"]))}</td><td class="data-files">{" · ".join(links)}</td></tr>')
        out.append("</tbody></table></div>")
    return "\n".join(out)
