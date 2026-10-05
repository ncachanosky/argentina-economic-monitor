"""Sanity checks on freshly fetched data.

Errors reject the fetch: the store keeps the last good vintage and the
site shows that series as stale. Warnings accept the data but are logged
and surfaced in the run summary, so a person looks before readers do.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

LARGE_REVISION = 0.05      # 5% change to an already-published observation
JUMP_FLOOR = 0.15          # 15% month-on-month move on a new observation
JUMP_MADS = 6.0            # ...or this many median absolute deviations
STALE_MONTHS = 4           # monthly series this far behind are flagged stale


@dataclass
class Check:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def check_series(new: pd.Series, old: pd.Series, frequency: str = "M", measure: str = "index") -> Check:
    c = Check()
    new = new.dropna().sort_index()

    if new.empty:
        c.errors.append("no observations returned")
        return c
    if new.index.has_duplicates:
        c.errors.append("duplicate dates")
    if frequency == "M":
        if not (new.index.day == 1).all():
            c.errors.append("monthly dates not on month start")
        expected = pd.date_range(new.index.min(), new.index.max(), freq="MS")
        missing = expected.difference(new.index)
        if len(missing):
            c.errors.append(f"{len(missing)} missing month(s), first {missing[0]:%Y-%m}")
    if measure == "index" and (new <= 0).any():
        c.errors.append("non-positive index values")
    if measure == "rate" and ((new < 0) | (new > 100)).any():
        c.errors.append("rate outside 0-100")

    if not old.empty:
        if new.index.max() < old.index.max():
            c.errors.append(
                f"latest date went backwards ({old.index.max():%Y-%m} -> {new.index.max():%Y-%m})"
            )
        lost = old.index.difference(new.index)
        if len(lost) > 2:
            c.errors.append(f"{len(lost)} previously published observations disappeared")

        common = old.index.intersection(new.index)
        if measure == "rate":  # revisions to a percentage, in points (5 pp = "large")
            rel = ((new[common] - old[common]).abs() / 100).dropna()
        else:
            rel = ((new[common] - old[common]).abs() / old[common].abs()).dropna()
        big = rel[rel > LARGE_REVISION]
        if len(big):
            unit = (lambda v: f"{v * 100:.1f} pp") if measure == "rate" else (lambda v: f"{v:.1%}")
            c.warnings.append(
                f"{len(big)} observation(s) revised by more than {unit(LARGE_REVISION)}; "
                f"largest {unit(big.max())} at {big.idxmax():%Y-%m}"
            )

        fresh = new.index.difference(old.index)
        if len(fresh) and len(new) > 24:
            pct = new.diff() / 100 if measure == "rate" else new.pct_change()
            mad = (pct - pct.median()).abs().median()
            threshold = max(JUMP_FLOOR, JUMP_MADS * mad)
            jumps = pct[fresh][pct[fresh].abs() > threshold]
            for d, v in jumps.items():
                c.warnings.append(f"new observation {d:%Y-%m} moves {v:+.1%} vs prior month")

    return c


def is_stale(last_obs: pd.Timestamp, today: pd.Timestamp, frequency: str = "M") -> bool:
    if frequency == "M":
        months = (today.year - last_obs.year) * 12 + (today.month - last_obs.month)
        return months > STALE_MONTHS
    return False
