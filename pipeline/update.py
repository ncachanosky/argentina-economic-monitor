"""Daily update: fetch every registry series, validate, record new vintages.

    python -m pipeline.update            # all sources
    python -m pipeline.update --dry-run  # fetch and validate, write nothing

Failures are soft: a series that fails to fetch or validate keeps its last
good data and is marked in data/status.json. The run only exits non-zero
with --strict, so one broken source never blocks the rest of the site.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

import pandas as pd

from . import registry, store, validate
from .registry import ROOT
from .sources import get_adapter

STATUS_PATH = ROOT / "data" / "status.json"
TABLES_DIR = ROOT / "data" / "tables"


def _load_status() -> dict:
    if STATUS_PATH.exists():
        return json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    return {}


def run(dry_run: bool = False, strict: bool = False) -> int:
    reg = registry.load()
    now = datetime.now(timezone.utc)
    vintage = now.strftime("%Y-%m-%d")
    today = pd.Timestamp(now.date())
    meta = reg.series_meta()
    freq_of = {sid: m["frequency"] for sid, m in meta.items()}
    measure_of = {sid: m["measure"] for sid, m in meta.items()}

    status = _load_status()
    lines: list[str] = []
    n_err = n_changed = 0

    timings: list[tuple[float, str, int]] = []
    for (source, _freq), ids in reg.series_by_source().items():
        adapter = get_adapter(source)
        t0 = time.monotonic()
        try:
            res = adapter.fetch(ids)
            # Sources that also feed a full table (the BCRA balance sheet) write it here.
            if not dry_run and hasattr(adapter, "write_tables"):
                changed_tables = adapter.write_tables(TABLES_DIR / source)
                if changed_tables:
                    lines.append(f"- table `{source}`: {len(changed_tables)} file(s) updated")
        except Exception as exc:  # whole source down
            res = None
            source_error = str(exc)
        timings.append((time.monotonic() - t0, f"{source} ({_freq})", len(ids)))

        for sid in sorted(ids):
            entry = status.get(sid, {})
            entry.update({"source": source, "last_checked": now.isoformat(timespec="seconds")})
            old = store.as_of(sid)

            if res is None:
                problem = f"source unavailable: {source_error}"
            elif sid in res.errors:
                problem = f"fetch failed: {res.errors[sid]}"
            else:
                problem = None

            if problem is None:
                new = res.data.loc[res.data["series_id"] == sid].set_index("date")["value"]
                chk = validate.check_series(new, old, freq_of[sid], measure_of[sid],
                                            allow_gaps=meta[sid].get("allow_gaps", False))
                if res.meta.get(sid, {}).get("units"):
                    entry["source_units"] = res.meta[sid]["units"]
                if not chk.ok:
                    problem = "validation failed: " + "; ".join(chk.errors)
                else:
                    if dry_run:
                        joined = new.index.difference(old.index)
                        lines.append(f"- `{sid}`: OK ({len(joined)} new obs, dry run)")
                    else:
                        ar = store.append(sid, new, vintage)
                        if ar.changed:
                            n_changed += 1
                            entry["last_changed"] = vintage
                        lines.append(
                            f"- `{sid}`: OK, {ar.new_obs} new / {ar.revised_obs} revised / {ar.deleted_obs} removed obs"
                        )
                    entry["warnings"] = chk.warnings + list(getattr(res, "warnings", {}).get(sid, []))
                    for w in entry["warnings"]:
                        lines.append(f"  - ⚠️ {w}")
                        print(f"::warning title={sid}::{w}")

            if problem is not None:
                n_err += 1
                entry["error"] = problem
                entry.setdefault("error_since", now.isoformat(timespec="seconds"))
                lines.append(f"- `{sid}`: ❌ {problem} (keeping last good data)")
                print(f"::error title={sid}::{problem}")
            else:
                entry.pop("error", None)
                entry.pop("error_since", None)

            latest = store.as_of(sid) if not dry_run else old
            if not latest.empty:
                entry["last_obs"] = latest.index.max().strftime("%Y-%m-%d")
                # Discontinued or hand-maintained history is never "stale".
                stale = not meta[sid]["static"] and validate.is_stale(latest.index.max(), today, freq_of[sid],
                                                                          meta[sid].get("stale_days"))
            else:
                stale = True
            entry["status"] = "error" if problem else ("stale" if stale else "ok")
            status[sid] = entry

    # Flag indicators whose release calendar has run out, so it gets updated.
    for ind in reg.indicators:
        if not ind.release:
            continue
        ids = ind.input_ids()
        lasts = [status.get(i, {}).get("last_obs") for i in ids if status.get(i, {}).get("last_obs")]
        if lasts and reg.next_release(ind, max(lasts)) is None:
            msg = f"no upcoming '{ind.release}' release in registry/releases.yaml; add the next INDEC calendar"
            lines.append(f"- ⚠️ `{ind.id}`: {msg}")
            print(f"::warning title={ind.id}::{msg}")

    # Forget series that are no longer in the registry.
    registered = {sid for ids in reg.series_by_source().values() for sid in ids}
    for sid in [k for k in status if k not in registered]:
        del status[sid]

    # Where the run's time goes (slowest sources first), to keep the twice-daily run short.
    timings.sort(reverse=True)
    lines.append("\n<details><summary>Fetch time by source</summary>\n")
    lines += [f"- {name}: {secs:.0f}s, {n} series" for secs, name, n in timings]
    lines.append(f"- total: {sum(t[0] for t in timings):.0f}s\n</details>")
    (STATUS_PATH.parent / "timings.json").write_text(json.dumps(
        [{"source": name, "seconds": round(secs, 1), "series": n} for secs, name, n in timings], indent=1) + "\n", encoding="utf-8") if not dry_run else None

    header = (
        f"## Data update {vintage}\n\n"
        f"{len(status)} series checked · {n_changed} changed · {n_err} with errors\n\n"
    )
    summary = header + "\n".join(lines) + "\n"
    print(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(summary)

    if not dry_run:
        STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATUS_PATH.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return 1 if (strict and n_err) else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--strict", action="store_true", help="exit non-zero if any series failed")
    args = ap.parse_args(argv)
    return run(dry_run=args.dry_run, strict=args.strict)


if __name__ == "__main__":
    sys.exit(main())
