"""Where each datos.gob.ar series lives as a file: series id -> distribution CSV and column.

datos.gob.ar publishes every dataset twice: as a CSV file per distribution, and through
the time-series API, which serves an index built from those files. Since October 2026
the API has returned incomplete or empty answers for series whose CSV is complete, and
it updates later than the files. So the Monitor reads the CSVs and keeps the API only
as a last fallback.

The map comes from the portal's catalog (data.json), which lists each distribution's
download URL and its fields (series id and CSV column). Rebuild it after adding a
datos.gob.ar series to the registry:

    python -m pipeline.datos_catalog

A test fails if a registered datos.gob.ar series is missing from the map.
"""
from __future__ import annotations

import json

import requests

from .registry import ROOT, load

CATALOG_URL = "https://infra.datos.gob.ar/catalog/sspm/data.json"
MAP_FILE = ROOT / "registry" / "datos_catalog.json"
USER_AGENT = "argentina-economic-monitor (+https://github.com/ncachanosky/argentina-economic-monitor)"


def datos_ids() -> list[str]:
    return sorted(k for k, v in load().series_meta().items()
                  if v["source"] == "datos_gob" and not k.startswith("indec_"))


def build(catalog: dict, ids) -> dict[str, dict]:
    wanted, out = set(ids), {}
    for ds in catalog.get("dataset", []):
        for d in ds.get("distribution", []):
            for f in d.get("field", []):
                if f.get("id") in wanted and d.get("downloadURL"):
                    out[f["id"]] = {"distribution": d["identifier"], "url": d["downloadURL"],
                                    "column": f["title"], "title": d.get("title")}
    return dict(sorted(out.items()))


def load_map() -> dict[str, dict]:
    try:
        return json.loads(MAP_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def main() -> None:
    r = requests.get(CATALOG_URL, timeout=180, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    ids = datos_ids()
    m = build(r.json(), ids)
    MAP_FILE.write_text(json.dumps(m, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    missing = [i for i in ids if i not in m]
    print(f"{len(m)} of {len(ids)} series mapped to {len({v['url'] for v in m.values()})} files"
          + (f"; not in the catalog: {missing}" if missing else ""))


if __name__ == "__main__":
    main()
