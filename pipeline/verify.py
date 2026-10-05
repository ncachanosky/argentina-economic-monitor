"""Check every registry id against the live source and report what it is.

    python -m pipeline.verify

Run after editing registry/series.yaml. Prints each id with the source's
own description, units and last date, so a wrong id is caught before it
reaches the site.
"""
from __future__ import annotations

import sys

from . import registry
from .sources import get_adapter


def main() -> int:
    reg = registry.load()
    label = {v.source_id: f"{ind.id}.{v.key}" for ind in reg.indicators for v in ind.variants.values()}
    bad = 0
    for source, ids in reg.series_by_source().items():
        res = get_adapter(source).fetch(ids)
        for sid in sorted(ids):
            if sid in res.errors:
                bad += 1
                print(f"FAIL {label[sid]:28} {sid}: {res.errors[sid][:120]}")
                continue
            m = res.meta.get(sid, {})
            n = int((res.data["series_id"] == sid).sum())
            print(f"ok   {label[sid]:28} {sid:34} {n:4d} obs, ends {m.get('time_index_end')} "
                  f"| {m.get('description')} [{m.get('units')}]")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
