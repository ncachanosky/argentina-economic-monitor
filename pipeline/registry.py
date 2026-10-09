"""Load and validate the series registry (registry/series.yaml)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "registry" / "series.yaml"
RELEASES_PATH = ROOT / "registry" / "releases.yaml"

KINDS = {"variants", "panel", "contributions", "balance_sheet", "top10", "schedule", "placements", "curve", "statement", "rer_calc", "tenure", "rem_revisions"}
TABLE_KINDS = {"balance_sheet", "top10", "schedule", "placements", "curve", "tenure", "rem_revisions"}   # cards that read a full table (data/tables/<source>), no series
DERIVE_METHODS = {"splice", "reweight", "ratio", "tracker", "monthly", "flows", "expectations", "formula", "net_reserves"}
RANGE_KEYS = {"2Y", "5Y", "10Y", "25Y", "50Y", "Max"}


def valid_range(key: str) -> bool:
    """A range button: a fixed window ("10Y") or a start year ("Since 1946")."""
    return key in RANGE_KEYS or bool(__import__("re").fullmatch(r"Since \d{4}", str(key)))
TRANSFORMS = {"level", "yoy", "mom", "ytd", "acc", "share"}
FREQUENCIES = {"M", "Q", "D", "Y", "S"}


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
    weights: dict | None = None
    bar_transform: str | None = None   # panels: metric for the ranked bars (yoy, mom, level)
    derive: dict | None = None         # computed indicator (source: derived); see pipeline/derive.py
    overlay: bool = False              # variants card: draw all variants together
    log_level: bool = False            # variants card: log scale for levels
    bands: list | None = None          # shaded periods [{start, end, label}]
    summary_windows: list | None = None  # cumulative change over [{label, base, end}]
    ranges: list | None = None         # time-range buttons, e.g. ["10Y", "25Y", "Max"]
    annual_table: dict | None = None   # variants card: yearly table {accumulated: variant, within_year: variant}
    bar_transforms: list | None = None # panels: bar metrics to choose from (first is the default)
    view_start: str | None = None      # charts never start before this month (fixed window, no range buttons)
    static: bool = False               # discontinued source: never flagged stale
    hidden: bool = False               # defined and fetched, but not shown on the site yet
    emphasis: str | None = None        # overlay cards: variant drawn as a thick dark line (e.g. a total)
    composition: list | None = None    # variants whose shares of their sum form the "share" view
    validate_as: str | None = None     # measure used to check source data, if not `measure` (e.g. rates above 100%)
    deflate_with: dict | None = None   # {indicator, variant}: offer a real-terms view deflated by this price index
    caveat: str | None = None          # highlighted warning shown above the chart
    table: dict | None = None          # balance_sheet cards: {source, status_id, guide, valuation_notes}
    plain_level: bool = False          # levels only: no rebasing or presidency view (e.g. series that go negative)
    yearly: dict | None = None         # monthly cards: a yearly view {how: december|sum, variants: [...]} (see app.js)
    ref_line: float | None = None      # variants card: a dashed horizontal reference line (e.g. 100)
    method_links: list | None = None   # [{anchor, label}]: links to sections of the methodology page
    statement: dict | None = None      # statement card: {rows: [{key, label, indent, bold}], ...} over the variants
    overlay_default: list | None = None  # overlay cards: variants drawn at first; the rest are toggled with chips
    muted: list | None = None          # overlay cards: variants drawn as thin grey reference lines (e.g. a regional median)
    term_shading: bool = False         # shade presidential terms behind the chart (annual series)
    term_colored: bool = False         # overlay cards: the emphasis variant drawn in presidency colors
    secondary_bar: dict | None = None  # overlay cards: {variant, of, label, axis}: bars on a right axis (e.g. a rank)
    forecast: list | None = None       # [{variant, rem, transform}]: the latest REM path drawn after the data (see export)
    fan: dict | None = None            # overlay cards: {lo, hi, label}: a shaded band between two variants

    def input_ids(self) -> list[str]:
        """Source series this indicator reads (its variants, or a derivation's inputs)."""
        if not self.derive:
            return [v.source_id for v in self.variants.values()]
        ids: list[str] = []
        if self.derive.get("method") == "splice":
            ids.append(self.derive["anchor"]["id"])
            for v in self.derive["variants"].values():
                ids += [seg["id"] for seg in v["segments"]]
        elif self.derive.get("method") == "reweight":
            ids.append(self.derive["headline"])
        elif self.derive.get("method") in ("monthly", "flows"):
            if self.derive.get("stock"):
                ids.append(self.derive["stock"])
            for v in self.derive["variants"].values():
                ids += list(v["sum_of"])
        elif self.derive.get("method") == "formula":
            ids += list(self.derive["vars"].values())
        elif self.derive.get("method") == "net_reserves":
            ids += [str(x) for x in self.derive["inputs"].values()]
        elif self.derive.get("method") == "expectations":
            ids += [self.derive["expected"], self.derive["cpi"]]
        elif self.derive.get("method") == "tracker":
            ids += [self.derive["monthly_sa"], self.derive["monthly_original"], self.derive["quarterly"]]
        elif self.derive.get("method") == "ratio":
            for v in self.derive["variants"].values():
                ids += [v["num"], v["den"]]
        return list(dict.fromkeys(ids))


HEX = __import__("re").compile(r"^#[0-9A-Fa-f]{6}$")


@dataclass
class Registry:
    site: dict
    topics: list[dict]
    indicators: list[Indicator] = field(default_factory=list)
    presidencies: list[dict] = field(default_factory=list)
    party_colors: dict = field(default_factory=dict)
    releases: dict = field(default_factory=dict)
    # Source series fetched and stored only to feed derived indicators.
    inputs: list[dict] = field(default_factory=list)
    episodes: list[dict] = field(default_factory=list)

    def episodes_for(self, ind_id: str) -> list[dict]:
        return [{k: v for k, v in e.items() if k != "indicators"} for e in self.episodes if ind_id in e["indicators"]]

    def series_meta(self) -> dict[str, dict]:
        """source_id -> {source, frequency, measure, static}, over indicators and inputs.

        A shared id keeps the strictest measure ("flow" only if never an index).
        """
        out: dict[str, dict] = {}
        for ind in self.indicators:
            if ind.source in ("derived", "table"):
                continue
            vm = ind.validate_as or ind.measure
            for v in ind.variants.values():
                m = out.setdefault(v.source_id, {"source": ind.source, "frequency": ind.frequency,
                                                 "measure": vm, "static": ind.static})
                if m["measure"] == "flow":
                    m["measure"] = vm
        for inp in self.inputs:
            m = out.setdefault(inp["id"], {"source": inp["source"], "frequency": inp["frequency"],
                                           "measure": inp.get("measure", "index"),
                                           "static": bool(inp.get("static")), "allow_gaps": bool(inp.get("allow_gaps")),
                                           "stale_days": inp.get("stale_days")})
            if m["measure"] == "flow":
                m["measure"] = inp.get("measure", "index")
            # An input entry's tolerances also apply when an indicator shows the same series.
            m["allow_gaps"] = bool(m.get("allow_gaps")) or bool(inp.get("allow_gaps"))
            m["stale_days"] = m.get("stale_days") or inp.get("stale_days")
        return out

    def next_release(self, ind: "Indicator", last_obs: str) -> dict | None:
        """First scheduled release covering a month after `last_obs` (YYYY-MM-DD)."""
        cal = self.releases.get(ind.release or "")
        if not cal:
            return None
        for d in sorted(cal["dates"], key=lambda x: x["date"]):
            if d["period"] > last_obs[:7]:
                return {"date": d["date"], "period": d["period"], "name": cal.get("name"), "frequency": cal.get("frequency", "M"),
                        "calendar_url": cal.get("calendar_url")}
        return None

    def series_by_source(self) -> dict[tuple[str, str], set[str]]:
        """Unique source series ids, grouped by (source adapter, frequency).

        Frequencies must be fetched separately: the datos.gob.ar API collapses
        every series in a request to the lowest common frequency, so mixing
        monthly and quarterly ids silently returns quarterly averages.
        """
        out: dict[tuple[str, str], set[str]] = {}
        for sid, m in self.series_meta().items():
            out.setdefault((m["source"], m["frequency"]), set()).add(sid)
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
    slugs = [t.get("slug") for t in topics]
    if any(not sl or not __import__("re").fullmatch(r"[a-z0-9-]+", sl) for sl in slugs) or len(set(slugs)) != len(slugs):
        raise RegistryError("every topic needs a unique lowercase slug (its page path)")
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

        derive = item.get("derive")
        if derive:
            if item.get("source") != "derived":
                raise RegistryError(f"{where}: a derived indicator needs source: derived")
            if derive.get("method") not in DERIVE_METHODS:
                raise RegistryError(f"{where}: derive.method must be one of {sorted(DERIVE_METHODS)}")
            if item.get("variants"):
                raise RegistryError(f"{where}: derived indicators define variants under derive")
            item = {**item, "variants": {k: {"label": v.get("label"), "id": f"{iid}:{k}"}
                                         for k, v in (derive.get("variants") or {}).items()}}
        elif item.get("source") == "derived":
            raise RegistryError(f"{where}: source: derived needs a derive block")
        if item.get("ranges") and not all(valid_range(r) for r in item["ranges"]):
            raise RegistryError(f"{where}: unknown ranges {item['ranges']}")
        variants = {}
        for key, v in (item.get("variants") or {}).items():
            if not v.get("id") or not v.get("label"):
                raise RegistryError(f"{where}: variant '{key}' needs id and label")
            variants[key] = Variant(key=key, label=v["label"], source_id=str(v["id"]))
        if item["kind"] in TABLE_KINDS:
            # A full table written by a source adapter (data/tables/<source>), no series.
            if not (item.get("table") or {}).get("source"):
                raise RegistryError(f"{where}: a {item['kind']} card needs table.source")
        elif not variants:
            raise RegistryError(f"{where}: no variants")

        default = item.get("default") or {}
        dvar = default.get("variant")
        dvars = dvar if isinstance(dvar, list) else [dvar]
        if item["kind"] not in TABLE_KINDS and any(d not in variants for d in dvars):
            raise RegistryError(f"{where}: default variant {dvar!r} not defined")
        if default.get("transform", "level") not in TRANSFORMS:
            raise RegistryError(f"{where}: unknown default transform")

        headline = item.get("headline")
        if item.get("measure", "index") not in {"index", "rate", "flow"}:
            raise RegistryError(f"{where}: measure must be 'index', 'rate' or 'flow'")
        contrib = item.get("contributions")
        if item["kind"] == "contributions":
            if not contrib or (contrib.get("total") not in variants and not contrib.get("precomputed")):
                raise RegistryError(f"{where}: contributions need a 'total' variant")
            for g in contrib.get("groups", []):
                for k in (g.get("add", []) + g.get("subtract", [])):
                    if k not in variants:
                        raise RegistryError(f"{where}: group {g.get('key')} uses unknown variant {k}")
            if sum(1 for g in contrib.get("groups", []) if g.get("residual")) > 1:
                raise RegistryError(f"{where}: at most one residual group")
        if item.get("bar_transforms") and not set(item["bar_transforms"]) <= TRANSFORMS:
            raise RegistryError(f"{where}: unknown bar_transforms {item['bar_transforms']}")
        at = item.get("annual_table")
        if at and not all(at.get(k) in (item.get("variants") or {}) for k in ("accumulated", "within_year")):
            raise RegistryError(f"{where}: annual_table needs accumulated and within_year variants")
        if item.get("transforms") and not set(item["transforms"]) <= TRANSFORMS:
            raise RegistryError(f"{where}: unknown transforms {item['transforms']}")
        if item.get("release") and item["release"] not in releases:
            raise RegistryError(f"{where}: release {item['release']!r} not in releases.yaml")
        if headline and headline.get("variant") not in variants:
            raise RegistryError(f"{where}: headline variant not defined")
        for f in item.get("forecast") or []:
            if item["kind"] not in TABLE_KINDS and f.get("variant") not in variants:
                raise RegistryError(f"{where}: forecast for unknown variant {f.get('variant')!r}")
        fan = item.get("fan")
        if fan and not {fan.get("lo"), fan.get("hi")} <= set(variants):
            raise RegistryError(f"{where}: fan names unknown variants")
        sb = item.get("secondary_bar")
        if sb and not ({sb.get("variant")} | ({sb["of"]} if sb.get("of") else set())) <= set(variants):
            raise RegistryError(f"{where}: secondary_bar names unknown variants")
        for fld in ("overlay_default", "muted"):
            if item.get(fld) and not set(item[fld]) <= set(variants):
                raise RegistryError(f"{where}: {fld} names unknown variants")

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
                weights=item.get("weights"),
                bar_transform=item.get("bar_transform"),
                derive=derive,
                overlay=bool(item.get("overlay", False)),
                log_level=bool(item.get("log_level", False)),
                bands=[{k: (str(v) if v is not None else None) for k, v in b.items()} for b in item.get("bands") or []] or None,
                summary_windows=[{k: (str(v) if v is not None else None) for k, v in w.items()}
                                 for w in item.get("summary_windows") or []] or None,
                ranges=item.get("ranges"),
                annual_table=item.get("annual_table"),
                bar_transforms=item.get("bar_transforms"),
                static=bool(item.get("static", False)),
                hidden=bool(item.get("hidden", False)),
                emphasis=item.get("emphasis"),
                composition=item.get("composition"),
                validate_as=item.get("validate_as"),
                deflate_with=item.get("deflate_with"),
                caveat=" ".join(str(item["caveat"]).split()) if item.get("caveat") else None,
                table=item.get("table"),
                plain_level=bool(item.get("plain_level", False)),
                yearly=item.get("yearly"),
                ref_line=item.get("ref_line"),
                method_links=item.get("method_links"),
                statement=item.get("statement"),
                overlay_default=item.get("overlay_default"),
                muted=item.get("muted"),
                term_shading=bool(item.get("term_shading", False)),
                term_colored=bool(item.get("term_colored", False)),
                secondary_bar=item.get("secondary_bar"),
                forecast=item.get("forecast"),
                fan=item.get("fan"),
                view_start=str(item["view_start"]) + ("-01" if len(str(item["view_start"])) == 7 else "") if item.get("view_start") else None,
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
        if prev_end and start < prev_end:   # gaps are allowed (interim presidents)
            raise RegistryError(f"{where}: overlaps the previous term")
        prev_end = end or "9999"
        presidencies.append({**p, "start": start, "end": end, "short": p.get("short", p["name"])})

    inputs = []
    for inp in raw.get("inputs", []) or []:
        where = f"input '{inp.get('id')}'"
        for k in ("id", "source", "frequency", "label"):
            if not inp.get(k):
                raise RegistryError(f"{where}: missing {k}")
        if inp["frequency"] not in FREQUENCIES:
            raise RegistryError(f"{where}: unsupported frequency")
        if inp.get("measure", "index") not in {"index", "rate", "flow"}:
            raise RegistryError(f"{where}: bad measure")
        inputs.append({**inp, "id": str(inp["id"])})

    ind_ids = {i.id for i in indicators}
    episodes = []
    for e in raw.get("episodes", []) or []:
        where = f"episode '{e.get('id')}'"
        for k in ("id", "start", "end", "label", "indicators"):
            if not e.get(k):
                raise RegistryError(f"{where}: missing {k}")
        e = {**e, "start": str(e["start"]), "end": str(e["end"])}
        if e["end"] < e["start"]:
            raise RegistryError(f"{where}: end before start")
        if not set(e["indicators"]) <= ind_ids:
            raise RegistryError(f"{where}: unknown indicators {sorted(set(e['indicators']) - ind_ids)}")
        episodes.append(e)

    reg = Registry(site=raw.get("site", {}), topics=topics, indicators=indicators,
                   presidencies=presidencies, party_colors=party_colors,
                   releases=releases, inputs=inputs, episodes=episodes)

    # Every series a derivation reads must be fetched by something.
    known = set(reg.series_meta())
    for ind in indicators:
        if not ind.derive:
            continue
        refs = [sid for sid in ind.input_ids() if sid.startswith("@")]
        bad = [r for r in refs if r[1:].split(":", 1)[0] not in {i.id for i in indicators}]
        if bad:
            raise RegistryError(f"indicator '{ind.id}': unknown indicator references {bad}")
        missing = [sid for sid in ind.input_ids() if sid not in known and not sid.startswith("@")]
        if missing:
            raise RegistryError(f"indicator '{ind.id}': inputs not in the registry: {missing}")
        if ind.derive["method"] == "reweight":
            comp = next((i for i in indicators if i.id == ind.derive.get("components")), None)
            if comp is None:
                raise RegistryError(f"indicator '{ind.id}': components indicator not found")
            nw = ind.derive.get("new_weights") or {}
            if set(nw) != set(comp.variants):
                raise RegistryError(f"indicator '{ind.id}': new_weights keys must match {comp.id} variants")
            if set(ind.derive["variants"]) != {"official", "reweighted"}:
                raise RegistryError(f"indicator '{ind.id}': reweight variants must be official and reweighted")
    return reg
