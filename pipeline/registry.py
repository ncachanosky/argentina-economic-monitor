"""Load and validate the series registry (registry/series.yaml)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "registry" / "series.yaml"

KINDS = {"variants", "panel"}
TRANSFORMS = {"level", "yoy", "mom"}
FREQUENCIES = {"M"}  # extend as new frequencies are added


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


@dataclass
class Registry:
    site: dict
    topics: list[dict]
    indicators: list[Indicator] = field(default_factory=list)

    def series_by_source(self) -> dict[str, set[str]]:
        """Unique source series ids, grouped by source adapter."""
        out: dict[str, set[str]] = {}
        for ind in self.indicators:
            out.setdefault(ind.source, set()).update(v.source_id for v in ind.variants.values())
        return out


class RegistryError(ValueError):
    pass


def load(path: Path = REGISTRY_PATH) -> Registry:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
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
            )
        )

    return Registry(site=raw.get("site", {}), topics=topics, indicators=indicators)
