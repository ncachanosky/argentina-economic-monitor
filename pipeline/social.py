"""Social-sharing preview images (Open Graph, 1200x630) and share pages.

For each page (home, topics) a PNG at social/<slug>.png: the page title and
description on the left, and a chart of the page's headline series (or its first
chart) over the last ten years on the right, in the Economic Order palette. For
each card a tiny page at share/<id>/ that carries the card's title and
description for link previews and sends people on to the card.

Run by pipeline.build after the data are exported (it reads <out>/data/*.json).
"""
from __future__ import annotations

import html
import json
import textwrap
from pathlib import Path

import pandas as pd

INK, INK2, INK3, BG, COPPER, RULE = "#36454F", "#5A6872", "#85909A", "#FBFAF7", "#B87333", "#E3E0D9"
W, H, DPI = 12, 6.3, 100
LAG = {"M": 12, "Q": 4}


def _font():
    from matplotlib import font_manager
    names = {f.name for f in font_manager.fontManager.ttflist}
    return next((n for n in ("Carlito", "Calibri", "Lato", "DejaVu Sans") if n in names), "sans-serif")


def _series(payload: dict) -> tuple[pd.Series, str] | None:
    """The series to draw and its label: the headline transform, else the default variant's level."""
    hl = payload.get("headline") or {}
    var = hl.get("variant") or (payload.get("default") or {}).get("variant")
    if isinstance(var, list):
        var = var[0]
    V = payload.get("variants") or {}
    if var not in V:
        var = next(iter(V), None)
    if var is None:
        return None
    s = pd.Series(V[var]["values"], index=pd.to_datetime(payload["dates"]), dtype=float).dropna()
    if s.empty:
        return None
    freq, tr = payload.get("frequency"), hl.get("transform", "level")
    vl = V[var]["label"]
    label = payload["short_title"] if vl.lower() in payload["short_title"].lower() else f"{payload['short_title']}: {vl[0].lower() + vl[1:] if not vl[:2].isupper() else vl}"
    if payload.get("frequency") == "D":
        s = s.resample("MS").mean()
        freq = "M"
    if tr in ("yoy", "mom") and freq in LAG:
        lag = LAG[freq] if tr == "yoy" else 1
        s = (100 * (s / s.shift(lag) - 1)) if payload.get("measure") != "rate" else s - s.shift(lag)
        label += ", y/y %" if tr == "yoy" else (", q/q %" if freq == "Q" else ", m/m %")
    elif payload.get("units"):
        label += f" ({payload['units']})"
    s = s.dropna()
    s = s[s.index >= s.index.max() - pd.DateOffset(years=10)]
    return (s, label) if len(s) > 3 else None


def image(path: Path, title: str, desc: str, chart: tuple[pd.Series, str] | None, site: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    font = _font()
    fig = plt.figure(figsize=(W, H), dpi=DPI, facecolor=BG)
    fig.text(0.05, 0.88, site.upper(), fontsize=15, color=COPPER, family=font, weight="bold")
    fig.text(0.05, 0.84, "by Economic Order", fontsize=13, color=INK3, family=font, va="top")
    tl = textwrap.wrap(title, 22)[:3]
    fig.text(0.05, 0.72, "\n".join(tl), fontsize=40, color=INK, family=font, weight="bold", va="top", linespacing=1.05)
    dl = textwrap.wrap(desc, 46)
    if len(dl) > 5:   # cut at the last full stop or comma that fits, else with an ellipsis
        cut = " ".join(dl[:5])
        k = max(cut.rfind(". "), cut.rfind(", "))
        dl = textwrap.wrap(cut[:k] + ("." if cut[k] == "." else "…") if k > 60 else cut.rstrip(",;: ") + "…", 46)
    fig.text(0.05, 0.72 - 0.105 * len(tl) - 0.04, "\n".join(dl), fontsize=15, color=INK2, family=font, va="top", linespacing=1.35)
    fig.text(0.05, 0.05, "Official data, updated twice a day", fontsize=12, color=INK3, family=font)
    if chart:
        s, label = chart
        ax = fig.add_axes([0.53, 0.17, 0.42, 0.62], facecolor=BG)
        bars = label.endswith("m/m %") or label.endswith("q/q %")
        if bars:
            ax.bar(s.index, s.values, width=25 if "m/m" in label else 70, color=COPPER)
        else:
            ax.plot(s.index, s.values, color=COPPER, lw=2.6)
        ax.axhline(0, color=INK3, lw=0.8) if (s.min() < 0 < s.max()) else None
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(RULE)
        ax.tick_params(colors=INK3, labelsize=12, length=0)
        ax.grid(axis="y", color=RULE, lw=0.8)
        for t in ax.get_xticklabels() + ax.get_yticklabels():
            t.set_family(font)
        last = s.iloc[-1]
        ax.annotate(f"{last:,.1f}", (s.index[-1], last), xytext=(6, 0), textcoords="offset points", color=INK, fontsize=14,
                    family=font, weight="bold", va="center")
        fig.text(0.53, 0.83, "\n".join(textwrap.wrap(label, 52)[:2]), fontsize=12.5, color=INK2, family=font, va="bottom")
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=DPI, facecolor=BG)
    plt.close(fig)


def share_page(title: str, desc: str, image_url: str, target: str, site: str) -> str:
    e = lambda s: html.escape(" ".join(str(s).split()), quote=True)
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{e(title)} · {e(site)}</title>'
            f'<meta name="description" content="{e(desc)}">'
            f'<meta property="og:title" content="{e(title)}"><meta property="og:description" content="{e(desc)}">'
            f'<meta property="og:type" content="article"><meta property="og:site_name" content="{e(site)}">'
            f'<meta property="og:image" content="{e(image_url)}"><meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">'
            f'<meta name="twitter:card" content="summary_large_image"><meta name="twitter:title" content="{e(title)}">'
            f'<meta name="twitter:description" content="{e(desc)}"><meta name="twitter:image" content="{e(image_url)}">'
            f'<link rel="canonical" href="{e(target)}"><meta http-equiv="refresh" content="0; url={e(target)}">'
            f'</head><body><script>location.replace({json.dumps(target)})</script>'
            f'<a href="{e(target)}">{e(title)}</a></body></html>\n')


def build(out: Path, reg, site_url: str) -> int:
    """Images for home and every topic page, and a share page per card. Returns the number of images."""
    data = out / "data"
    manifest = json.loads((data / "manifest.json").read_text(encoding="utf-8"))
    site = reg.site.get("title", "Argentina Economic Monitor")
    payloads = {}
    for m in manifest["indicators"]:
        p = data / f"{m['id']}.json"
        if p.exists():
            payloads[m["id"]] = json.loads(p.read_text(encoding="utf-8"))
    n = 0
    slug_of = {t["id"]: t["slug"] for t in reg.topics}
    for t in reg.topics:
        inds = [payloads[m["id"]] for m in manifest["indicators"] if m["topic"] == t["id"] and m["id"] in payloads]
        chart = next((c for c in (_series(p) for p in sorted(inds, key=lambda p: not p.get("headline")) if p.get("variants")) if c), None)
        image(out / "social" / f"{t['slug']}.png", t["title"], t.get("description", ""), chart, site)
        n += 1
    home_chart = _series(payloads["cpi"]) if "cpi" in payloads else None
    image(out / "social" / "home.png", "Argentina's economy, updated daily",
          "Activity, prices, money, the exchange rate, trade, fiscal accounts, labor and institutions: "
          "interactive charts from official sources.", home_chart, site)
    n += 1
    for m in manifest["indicators"]:
        p = payloads.get(m["id"])
        if not p:
            continue
        slug = slug_of.get(m["topic"])
        target = f"{site_url}{slug}/#ind-{m['id']}"
        d = out / "share" / m["id"]
        d.mkdir(parents=True, exist_ok=True)
        (d / "index.html").write_text(share_page(p["title"], p.get("description", ""), f"{site_url}social/{slug}.png", target, site),
                                      encoding="utf-8")
    return n
