"""Open, update or close a GitHub issue when data sources keep failing.

    python -m pipeline.alerts            # print the report
    python -m pipeline.alerts --issue    # sync the "data-alert" issue (needs gh and GH_TOKEN)

A series counts as broken when its last two update runs failed (error for more
than MIN_HOURS), so a source that is down for one run does not page anyone.
Warnings that ask for a hand edit (e.g. a new BCRA president, a release
calendar that ran out) are listed too. GitHub notifies the repository's
watchers when the issue is opened or commented on; the comment is only added
when the set of broken series changes.

Primary source files (pipeline/sources/primary.py) get one issue each, labelled
"source-layout": a changed layout is reported at once (it won't fix itself), a file
that can't be found after MIN_HOURS. The issue shows what the reader expected, what
it found and the first rows of the file, and closes after the next good run.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

from .update import STATUS_PATH

LABEL = "data-alert"
TITLE = "Data sources failing"
MIN_HOURS = 10          # runs are ~10 hours apart: two consecutive failures
HAND_WARNINGS = re.compile(r"officials\.csv|release|calendar|edition|update data/manual", re.I)


def report(status: dict, now: datetime | None = None) -> tuple[list[str], str]:
    now = now or datetime.now(timezone.utc)
    broken, notes = [], []
    for sid, e in sorted(status.items()):
        since = e.get("error_since")
        if e.get("status") == "error" and since and now - datetime.fromisoformat(since) >= timedelta(hours=MIN_HOURS):
            broken.append(sid)
            notes.append(f"- `{sid}` ({e.get('source')}), failing since {since[:16].replace('T', ' ')} UTC: {e.get('error', '')[:300]}")
    hand = [f"- `{sid}`: {w}" for sid, e in sorted(status.items()) for w in e.get("warnings", []) if HAND_WARNINGS.search(w)]
    body = []
    if notes:
        body += ["### Series failing in consecutive runs", "The site keeps showing their last good data, marked \"Update failed\".", "", *notes, ""]
    if hand:
        body += ["### Needs a hand edit", *hand, ""]
    ids = sorted(broken) + sorted({h.split("`")[1] for h in hand})
    body.append(f"<!-- ids: {','.join(ids)} -->")
    return ids, "\n".join(body)


def gh(*args: str, input: str | None = None) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True, input=input).stdout


def sync_issue(ids: list[str], body: str) -> str:
    gh("label", "create", LABEL, "--color", "B4513C", "--description", "Automatic alert: data sources failing", "--force")
    open_ = json.loads(gh("issue", "list", "--label", LABEL, "--state", "open", "--json", "number,body"))
    issue = open_[0] if open_ else None
    if not ids:
        if issue:
            gh("issue", "close", str(issue["number"]), "--comment", "All sources updated normally in the latest run.")
            return f"closed #{issue['number']}"
        return "nothing to report"
    if not issue:
        url = gh("issue", "create", "--title", TITLE, "--label", LABEL, "--body-file", "-", input=body)
        return f"opened {url.strip()}"
    old = re.search(r"<!-- ids: (.*?) -->", issue.get("body") or "")
    gh("issue", "edit", str(issue["number"]), "--body-file", "-", input=body)
    if not old or old.group(1) != ",".join(ids):
        gh("issue", "comment", str(issue["number"]), "--body", "The list of failing series changed; see the updated description.")
    return f"updated #{issue['number']}"


SOURCE_LABEL = "source-layout"
SOURCE_STATUS = STATUS_PATH.parent / "source_files.json"


def source_reports(files: dict, now: datetime | None = None) -> dict[str, tuple[str, str] | None]:
    """name -> (title, body) for files that need an issue, None for files that are fine."""
    now = now or datetime.now(timezone.utc)
    out = {}
    for name, e in files.items():
        if e.get("status") != "error":
            out[name] = None
            continue
        since = e.get("error_since") or e.get("checked")
        if e.get("kind") != "layout" and now - datetime.fromisoformat(since) < timedelta(hours=MIN_HOURS):
            continue                        # a download failure may be transient: wait
        what = "changed" if e.get("kind") == "layout" else "not found"
        body = [f"The primary source file **{name}** could not be read (since {since[:16].replace('T', ' ')} UTC). "
                "Its series are being filled from the datos.gob.ar API meanwhile, or keep their last good data.", "",
                f"- File: {e.get('url') or 'n/a'}"]
        if e.get("page"):
            body.append(f"- Page that links to it: {e['page']}")
        body += ["- Reader: `pipeline/sources/primary.py` (tests: `tests/test_primary.py`)", "",
                 "### What went wrong", "```", e.get("error", ""), "```"]
        if e.get("preview"):
            body += ["", "### First rows of the file as read", "```", e["preview"], "```"]
        body += ["", "To fix: update the reader, add the new file to `tests/fixtures/primary/` next to the old one, "
                 "and check that the tests pass on both.", f"<!-- source: {name} -->"]
        out[name] = (f"Source file {what}: {name}", "\n".join(body))
    return out


def sync_source_issues(files: dict) -> list[str]:
    gh("label", "create", SOURCE_LABEL, "--color", "D4745E", "--description",
       "A primary source file changed and its reader needs an update", "--force")
    open_ = json.loads(gh("issue", "list", "--label", SOURCE_LABEL, "--state", "open", "--json", "number,body"))
    by_name = {}
    for i in open_:
        m = re.search(r"<!-- source: (.*?) -->", i.get("body") or "")
        if m:
            by_name[m.group(1)] = i["number"]
    done = []
    for name, rep in source_reports(files).items():
        num = by_name.get(name)
        if rep is None:
            if num:
                gh("issue", "close", str(num), "--comment", "The file was read normally in the latest run.")
                done.append(f"closed #{num}")
            continue
        title, body = rep
        if num:
            gh("issue", "edit", str(num), "--body-file", "-", input=body)
            done.append(f"updated #{num}")
        else:
            url = gh("issue", "create", "--title", title, "--label", SOURCE_LABEL, "--body-file", "-", input=body)
            done.append(f"opened {url.strip()}")
    return done


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--issue", action="store_true", help="open, update or close the GitHub issue")
    args = ap.parse_args(argv)
    status = json.loads(STATUS_PATH.read_text(encoding="utf-8")) if STATUS_PATH.exists() else {}
    ids, body = report(status)
    print(body if ids else "No failing sources.")
    files = json.loads(SOURCE_STATUS.read_text(encoding="utf-8")) if SOURCE_STATUS.exists() else {}
    reps = {k: v for k, v in source_reports(files).items() if v}
    for title, _ in reps.values():
        print(title)
    if args.issue:
        print(sync_issue(ids, body))
        for line in sync_source_issues(files):
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
