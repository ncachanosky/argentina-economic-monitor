"""Build the static site: one page per topic plus a home page, in English and Spanish.

    python -m pipeline.build --out _site [--tag BUILD_TAG]

Copies site/ to the output, renders site/index.html (a template) as the home
page, as <slug>/index.html for every topic, and as the methodology, about and
embed pages; then the same pages in Spanish under es/ (texts from
registry/i18n/es.yaml), and writes the data files with pipeline.export. Both
languages read the same data/ directory; data/i18n/es.json carries the Spanish
interface strings for the browser.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import export, registry
from .registry import ROOT

SITE = ROOT / "site"
I18N_DIR = ROOT / "registry" / "i18n"

# English page texts; registry/i18n/<lang>.yaml `site:` overrides them for other languages.
EN = {
    "home_h1": "Argentina's economy, updated daily",
    "home_lede": ("Official statistics, checked every morning against the source and charted as soon "
                  "as a new release lands. Every chart is interactive and exportable as an image or data file."),
    "home_desc": ("Interactive, daily-updated charts of Argentina's economy: activity, GDP, inflation and more. "
                  "From Economic Order."),
    "methodology_title": "Methodology",
    "methodology_lede": "Sources, checks and every calculation behind the charts.",
    "methodology_desc": "How the Argentina Economic Monitor gets, checks and transforms its data.",
    "about_title": "About",
    "about_lede": "What the Monitor is, who is behind it, and how to cite it or report an error.",
    "about_desc": "About the Argentina Economic Monitor, a project of Economic Order.",
    "embed_title": "Chart",
    "embed_desc": "A chart from the Argentina Economic Monitor.",
    "loading": "Loading data…",
    "by": "by Economic Order",
    "home_aria": "Argentina Economic Monitor, home",
    "topics_aria": "Topics",
    "subscribe": "Subscribe",
    "theme": "Toggle dark mode",
    "onpage": "On this page",
    "latest": "Latest readings",
    "switch_label": "Español",
    "switch_title": "Ver este sitio en español",
    "pending_note": "",
    "footer": """      <div>
        <h4>About</h4>
        <p>The Argentina Economic Monitor is a project of <a id="eo-link" href="#">Economic Order</a>, by Nicolás Cachanosky. Data are pulled automatically from their sources twice a day, validated, and stored with their full revision history.</p>
        <p>Source data: INDEC, the BCRA, the Ministry of Economy and other official agencies; market quotes from market-data services, marked as unofficial. Charts and transformations are ours; errors are ours too. Corrections are welcome on <a id="repo-link" href="#">GitHub</a>. <a href="__ROOT__about/">More about the Monitor</a>.</p>
      </div>
      <div>
        <h4>Data</h4>
        <p>Each chart offers the series as CSV. Full revision histories (every published vintage) are in <code>data/vintages/</code>. How every number is calculated: <a href="__ROOT__methodology/">Methodology</a>.</p>
      </div>""",
}

ANALYTICS = ""   # set in build() from the registry's site.goatcounter


def analytics_tag(code: str) -> str:
    """GoatCounter's script (no cookies); empty when no code is configured."""
    if not code or not re.fullmatch(r"[a-z0-9-]+", code):
        return ""
    return (f'  <script data-goatcounter="https://{code}.goatcounter.com/count" '
            f'async src="//gc.zgo.at/count.js"></script>')


def load_i18n(lang: str) -> dict:
    p = I18N_DIR / f"{lang}.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}


def render(template: str, *, page: str, root: str, title: str, desc: str, h1: str, lede: str, tag: str,
           content: str | None = None, url: str = "", image: str = "", lang: str = "en", assets: str | None = None,
           data: str | None = None, texts: dict | None = None, alt_url: str = "", hreflang: str = "",
           switch: bool = False) -> str:
    T = {**EN, **(texts or {})}
    esc = lambda s: html.escape(" ".join(str(s).split()), quote=True)
    assets = root if assets is None else assets
    switch_html = (f'        <a class="btn lang-switch" href="{esc(alt_url)}" hreflang="{"en" if lang == "es" else "es"}" '
                   f'lang="{"en" if lang == "es" else "es"}" title="{esc(T["switch_title"])}">{esc(T["switch_label"])}</a>\n') if switch and alt_url else ""
    content = content if content is not None else f'<div class="loading">{esc(T["loading"])}</div>'
    if T.get("pending_note") and content and not content.startswith('<div class="loading">'):
        content = f'<p class="caveat">{esc(T["pending_note"])}</p>\n' + content
    vals = {
        "__TITLE__": esc(title), "__DESC__": esc(desc), "__H1__": esc(h1), "__LEDE__": esc(lede),
        "__PAGE__": page, "__BUILD__": tag, "__URL__": esc(url), "__OGIMAGE__": esc(image),
        "__ANALYTICS__": ANALYTICS, "__LANG__": lang, "__DATA__": data if data is not None else f"{assets}data/",
        "__HREFLANG__": hreflang, "__LANGSWITCH__": switch_html,
        "__T_HOME_ARIA__": esc(T["home_aria"]), "__T_BY__": esc(T["by"]), "__T_TOPICS__": esc(T["topics_aria"]),
        "__T_SUBSCRIBE__": esc(T["subscribe"]), "__T_THEME__": esc(T["theme"]), "__T_ONPAGE__": esc(T["onpage"]),
        "__T_LATEST__": esc(T["latest"]), "__FOOTER__": T["footer"],
        "__CONTENT__": content,
    }
    out = template
    for key in ("__FOOTER__", "__CONTENT__"):     # blocks first: they contain __ROOT__ links
        out = out.replace(key, vals.pop(key))
    out = out.replace("__ASSETS__", assets).replace("__ROOT__", root)
    for key, val in vals.items():
        out = out.replace(key, val)
    return out


def i18n_payload(i18n: dict) -> dict:
    """What the browser needs: interface strings, patterns, hover phrases, page names."""
    pats = []
    for item in i18n.get("patterns") or []:
        re.compile(item[0])          # fail the build on a bad pattern
        pats.append([item[0], item[1]])
    return {"ui": i18n.get("ui") or {}, "patterns": pats, "hover": i18n.get("hover") or [], "topics": i18n.get("topics") or {}}


def build(out: Path, tag: str, with_data: bool = True) -> None:
    reg = registry.load()
    global ANALYTICS
    ANALYTICS = analytics_tag(str(reg.site.get("goatcounter") or ""))
    site_name = reg.site.get("title", "Argentina Economic Monitor")
    base = reg.site.get("site_url", "")
    spanish_live = bool(reg.site.get("spanish"))          # show the language switch
    img = lambda slug: f"{base}social/{slug}.png?v={tag}"
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(SITE, out, ignore=shutil.ignore_patterns("data"))
    template = (SITE / "index.html").read_text(encoding="utf-8")
    es = load_i18n("es")

    def pages(lang: str):
        """(path under the language root, page id, title, h1, lede, desc, content, image) for every page."""
        T = {**EN, **((es.get("site") or {}) if lang == "es" else {})}
        tops = {t["id"]: {**t, **((es.get("topics") or {}).get(t["id"], {}) if lang == "es" else {})} for t in reg.topics}
        yield "", "home", site_name, T["home_h1"], T["home_lede"], T["home_desc"], None, img("home")
        for t in reg.topics:
            tt = tops[t["id"]]
            yield (f"{t['slug']}/", t["id"], f"{tt['title']} · {site_name}", tt["title"], tt.get("description", ""),
                   f"{tt['title']}: {tt.get('description', '')}", None, img(t["slug"]))
        for key in ("methodology", "about"):
            src = SITE / (f"{key}.es.html" if lang == "es" and (SITE / f"{key}.es.html").exists() else f"{key}.html")
            yield (f"{key}/", key, f"{T[key + '_title']} · {site_name}", T[key + "_title"], T[key + "_lede"], T[key + "_desc"],
                   src.read_text(encoding="utf-8"), img("home"))
        yield "embed/", "embed", f"{T['embed_title']} · {site_name}", "", "", T["embed_desc"], None, img("home")

    for lang, prefix in (("en", ""), ("es", "es/")):
        texts = (es.get("site") or {}) if lang == "es" else {}
        if lang == "es" and not es:
            continue
        for path, page, title, h1, lede, desc, content, image in pages(lang):
            depth = path.count("/") + prefix.count("/")
            assets = "../" * depth
            root = "../" * path.count("/")
            other = ("" if lang == "es" else "es/") + path
            hreflang = (f'  <link rel="alternate" hreflang="en" href="{base}{path}">\n'
                        f'  <link rel="alternate" hreflang="es" href="{base}es/{path}">') if page != "embed" and spanish_live else ""
            if lang == "es" and texts.get("pending_note") and page not in ("methodology", "about"):
                t_use = {**texts, "pending_note": ""}
            else:
                t_use = texts
            d = out / prefix / path
            d.mkdir(parents=True, exist_ok=True)
            html_text = render(
                template, page=page, root=root, assets=assets, title=title, h1=h1, lede=lede, desc=desc, tag=tag,
                content=content, url=f"{base}{prefix}{path}" if page != "embed" else "", image=image, lang=lang,
                data=f"{assets}data/", texts=t_use, alt_url=f"{root}{'../' if lang == 'es' else ''}{other}" if lang == "es" else f"{root}{other}",
                hreflang=hreflang, switch=spanish_live)
            if lang == "es" and not spanish_live:   # in preparation: keep it out of search engines
                html_text = html_text.replace("<head>", '<head>\n  <meta name="robots" content="noindex">', 1)
            (d / "index.html").write_text(html_text, encoding="utf-8")
        # Renamed pages: a redirect from each old path keeps shared links working.
        for t in reg.topics:
            for old in t.get("old_slugs") or []:
                d = out / prefix / old
                d.mkdir(parents=True, exist_ok=True)
                target = f"../{t['slug']}/"
                (d / "index.html").write_text(
                    f'<!doctype html><meta charset="utf-8"><title>Moved</title>'
                    f'<meta http-equiv="refresh" content="0; url={target}">'
                    f'<link rel="canonical" href="{target}">'
                    f'<script>location.replace("{target}" + location.hash)</script>'
                    f'<a href="{target}">This page moved.</a>\n', encoding="utf-8")
    for key in ("methodology", "about"):
        for f in (f"{key}.html", f"{key}.es.html"):
            (out / f).unlink(missing_ok=True)
    if with_data:
        export.export(out / "data")
        if es:
            (out / "data" / "i18n").mkdir(parents=True, exist_ok=True)
            (out / "data" / "i18n" / "es.json").write_text(json.dumps(i18n_payload(es), ensure_ascii=False, separators=(",", ":")),
                                                         encoding="utf-8")
        from . import social
        print(f"social: {social.build(out, reg, base)} preview images")
    print(f"built {4 + len(reg.topics)} pages per language in {out}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=ROOT / "_site")
    ap.add_argument("--tag", default=datetime.now(timezone.utc).strftime("%Y%m%d%H%M"))
    args = ap.parse_args(argv)
    build(args.out, args.tag)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
