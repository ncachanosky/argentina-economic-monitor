"""Load and validate the series registry (registry/series.yaml)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "registry" / "series.yaml"
RELEASES_PATH = ROOT / "registry" / "releases.yaml"

KINDS = {"variants", "panel", "contributions"}
TRANSFORMS = {"level", "yoy", "mom"}
FREQUENCIES = {"M", "Q"}


@dataclass(frozen=True)
class Variant:
    key: str
    label: str
    source_id: str


@dataclass
class Indicator:
    id: str
    topic: str
    kind: str
    title: str
    short_title: str
    description: str
    source: str
    source_label: str
    frequency: str
    units: str
    variants: dict[str, Variant]
    default: dict
    headline: dict | None = None
    release: str | None = None
    measure: str = "index"          # "index" (changes in %) or "rate" (changes in pp)
    transforms: list[str] | None = None
    component_noun: str | None = None
    wide: bool = False
    index_base: str | None = None     # e.g. "2004": levels shown as index, that year's average = 100
    source_units: str | None = None   # units of the source data (noted on charts; kept in raw CSV)
    note: str | None = None
    contributions: dict | None = None


HEX = __import__("re").compile(r"^#[0-9A-Fa-f]{6}$")


@dataclass
class Registry:
    site: dict
    topics: list[dict]
    indicators: list[Indicator] = field(default_factory=list)
    presidencies: list[dict] = field(default_factory=list)
    party_colors: dict = field(default_factory=dict)
    releases: dict = field(default_factory=dict)

    def next_release(self, ind: "Indicator", last_obs: str) -> dict | None:
        """First scheduled release covering a month after `last_obs` (YYYY-MM-DD)."""
        cal = self.releases.get(ind.release or "")
        if not cal:
            return None
        for d in sorted(cal["dates"], key=lambda x: x["date"]):
            if d["period"] > last_obs[:7]:
                return {"date": d["date"], "period": d["period"], "name": cal.get("name"),
                        "calendar_url": cal.get("calendar_url")}
        return None

    def series_by_source(self) -> dict[str, set[str]]:
        """Unique source series ids, grouped by source adapter."""
        out: dict[str, set[str]] = {}
        for ind in self.indicators:
            out.setdefault(ind.source, set()).update(v.source_id for v in ind.variants.values())
        return out


class RegistryError(ValueError):
    pass


def load(path: Path = REGISTRY_PATH, releases_path: Path = RELEASES_PATH) -> Registry:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    releases = {}
    if releases_path.exists():
        releases = yaml.safe_load(releases_path.read_text(encoding="utf-8")) or {}
        for key, cal in releases.items():
            for d in cal.get("dates", []):
                d["date"], d["period"] = str(d["date"]), str(d["period"])
    topics = raw.get("topics", [])
    topic_ids = {t["id"] for t in topics}
    seen: set[str] = set()
    indicators: list[Indicator] = []

    for item in raw.get("indicators", []):
        iid = item.get("id")
        where = f"indicator '{iid}'"
        if not iid or iid in seen:
            raise RegistryError(f"{where}: missing or duplicate id")
        seen.add(iid)
        if item.get("topic") not in topic_ids:
            raise RegistryError(f"{where}: unknown topic {item.get('topic')!r}")
        if item.get("kind") not in KINDS:
            raise RegistryError(f"{where}: kind must be one of {sorted(KINDS)}")
        if item.get("frequency") not in FREQUENCIES:
            raise RegistryError(f"{where}: unsupported frequency {item.get('frequency')!r}")

        variants = {}
        for key, v in (item.get("variants") or {}).items():
            if not v.get("id") or not v.get("label"):
                raise RegistryError(f"{where}: variant '{key}' needs id and label")
            variants[key] = Variant(key=key, label=v["label"], source_id=str(v["id"]))
        if not variants:
            raise RegistryError(f"{where}: no variants")

        default = item.get("default") or {}
        dvar = default.get("variant")
        dvars = dvar if isinstance(dvar, list) else [dvar]
        if any(d not in variants for d in dvars):
            raise RegistryError(f"{where}: default variant {dvar!r} not defined")
        if default.get("transform", "level") not in TRANSFORMS:
            raise RegistryError(f"{where}: unknown default transform")

        headline = item.get("headline")
        if item.get("measure", "index") not in {"index", "rate", "flow"}:
            raise RegistryError(f"{where}: measure must be 'index', 'rate' or 'flow'")
        contrib = item.get("contributions")
        if item["kind"] == "contributions":
            if not contrib or contrib.get("total") not in variants:
                raise RegistryError(f"{where}: contributions need a 'total' variant")
            for g in contrib.get("groups", []):
                for k in (g.get("add", []) + g.get("subtract", [])):
                    if k not in variants:
                        raise RegistryError(f"{where}: group {g.get('key')} uses unknown variant {k}")
            if sum(1 for g in contrib.get("groups", []) if g.get("residual")) > 1:
                raise RegistryError(f"{where}: at most one residual group")
        if item.get("transforms") and not set(item["transforms"]) <= TRANSFORMS:
            raise RegistryError(f"{where}: unknown transforms {item['transforms']}")
        if item.get("release") and item["release"] not in releases:
            raise RegistryError(f"{where}: release {item['release']!r} not in releases.yaml")
        if headline and headline.get("variant") not in variants:
            raise RegistryError(f"{where}: headline variant not defined")

        indicators.append(
            Indicator(
                id=iid,
                topic=item["topic"],
                kind=item["kind"],
                title=item["title"],
                short_title=item.get("short_title", item["title"]),
                description=" ".join(str(item.get("description", "")).split()),
                source=item["source"],
                source_label=item.get("source_label", item["source"]),
                frequency=item["frequency"],
                units=item.get("units", ""),
                variants=variants,
                default=default,
                headline=headline,
                release=item.get("release"),
                measure=item.get("measure", "index"),
                transforms=item.get("transforms"),
                component_noun=item.get("component_noun"),
                wide=bool(item.get("wide", False)),
                index_base=str(item["index_base"]) if item.get("index_base") else None,
                source_units=item.get("source_units"),
                note=" ".join(str(item["note"]).split()) if item.get("note") else None,
                contributions=contrib,
            )
        )

    party_colors = raw.get("party_colors", {}) or {}
    for key, c in party_colors.items():
        if not (HEX.match(str(c.get("light", ""))) and HEX.match(str(c.get("dark", "")))):
            raise RegistryError(f"party color '{key}': needs light and dark hex colors")

    presidencies = []
    prev_end = None
    for p in raw.get("presidencies", []) or []:
        where = f"presidency '{p.get('id')}'"
        for k in ("id", "name", "start", "party"):
            if not p.get(k):
                raise RegistryError(f"{where}: missing {k}")
        if p["party"] not in party_colors:
            raise RegistryError(f"{where}: party {p['party']!r} not in party_colors")
        start, end = str(p["start"]), (str(p["end"]) if p.get("end") else None)
        if end and end <= start:
            raise RegistryError(f"{where}: end before start")
        if prev_end and start < prev_end:
            raise RegistryError(f"{where}: overlaps the previous term")
        prev_end = end or "9999"
        presidencies.append({**p, "start": start, "end": end, "short": p.get("short", p["name"])})

    return Registry(site=raw.get("site", {}), topics=topics, indicators=indicators,
                    presidencies=presidencies, party_colors=party_colors,
                    releases=releases)
