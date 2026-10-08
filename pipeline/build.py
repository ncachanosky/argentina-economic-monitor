"""Build the static site: one page per topic plus a home page.

    python -m pipeline.build --out _site [--tag BUILD_TAG]

Copies site/ to the output, renders site/index.html (a template) as the
home page and as <slug>/index.html for every topic in the registry, and
writes the data files with pipeline.export.
"""
from __future__ import annotations

import argparse
import html
import shutil
from datetime import datetime, timezone
from pathlib import Path

from . import export, registry
from .registry import ROOT

SITE = ROOT / "site"
HOME_LEDE = ("Official statistics, checked every morning against the source and charted as soon "
             "as a new release lands. Every chart is interactive and exportable as an image or data file.")


LOADING = '<div class="loading">Loading data…</div>'


def render(template: str, *, page: str, root: str, title: str, desc: str, h1: str, lede: str, tag: str,
           content: str = LOADING) -> str:
    esc = lambda s: html.escape(" ".join(str(s).split()), quote=True)
    out = template
    for key, val in {"__TITLE__": esc(title), "__DESC__": esc(desc), "__H1__": esc(h1), "__LEDE__": esc(lede),
                     "__PAGE__": page, "__ROOT__": root, "__BUILD__": tag, "__CONTENT__": content}.items():
        out = out.replace(key, val)
    return out


def build(out: Path, tag: str, with_data: bool = True) -> None:
    reg = registry.load()
    site_name = reg.site.get("title", "Argentina Economic Monitor")
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(SITE, out, ignore=shutil.ignore_patterns("data"))
    template = (SITE / "index.html").read_text(encoding="utf-8")
    (out / "index.html").write_text(render(
        template, page="home", root="", title=site_name, h1="Argentina's economy, updated daily", lede=HOME_LEDE,
        desc="Interactive, daily-updated charts of Argentina's economy: activity, GDP, inflation and more. "
             "From Economic Order.", tag=tag), encoding="utf-8")
    for t in reg.topics:
        d = out / t["slug"]
        d.mkdir(parents=True, exist_ok=True)
        (d / "index.html").write_text(render(
            template, page=t["id"], root="../", title=f"{t['title']} · {site_name}", h1=t["title"],
            lede=t.get("description", ""), desc=f"{t['title']}: {t.get('description', '')}", tag=tag), encoding="utf-8")
    # Renamed pages: a redirect from each old path keeps shared links working.
    for t in reg.topics:
        for old in t.get("old_slugs") or []:
            d = out / old
            d.mkdir(parents=True, exist_ok=True)
            target = f"../{t['slug']}/"
            (d / "index.html").write_text(
                f'<!doctype html><meta charset="utf-8"><title>Moved</title>'
                f'<meta http-equiv="refresh" content="0; url={target}">'
                f'<link rel="canonical" href="{target}">'
                f'<script>location.replace("{target}" + location.hash)</script>'
                f'<a href="{target}">This page moved.</a>\n', encoding="utf-8")

    # Methodology: a static page from the same template.
    d = out / "methodology"
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.html").write_text(render(
        template, page="methodology", root="../", title=f"Methodology · {site_name}", h1="Methodology",
        lede="Sources, checks and every calculation behind the charts.",
        desc="How the Argentina Economic Monitor gets, checks and transforms its data.", tag=tag,
        content=(SITE / "methodology.html").read_text(encoding="utf-8")), encoding="utf-8")
    (out / "methodology.html").unlink(missing_ok=True)
    d = out / "about"
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.html").write_text(render(
        template, page="about", root="../", title=f"About · {site_name}", h1="About",
        lede="What the Monitor is, who is behind it, and how to cite it or report an error.",
        desc="About the Argentina Economic Monitor, a project of Economic Order.", tag=tag,
        content=(SITE / "about.html").read_text(encoding="utf-8")), encoding="utf-8")
    (out / "about.html").unlink(missing_ok=True)
    if with_data:
        export.export(out / "data")
    print(f"built {3 + len(reg.topics)} pages in {out}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=ROOT / "_site")
    ap.add_argument("--tag", default=datetime.now(timezone.utc).strftime("%Y%m%d%H%M"))
    args = ap.parse_args(argv)
    build(args.out, args.tag)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
