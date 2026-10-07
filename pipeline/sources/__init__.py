"""Source adapters. Each adapter exposes `fetch(ids) -> FetchResult`."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class FetchResult:
    # Long format: columns date (Timestamp, month start), series_id, value
    data: pd.DataFrame
    # Per-series metadata reported by the source (units, last date, ...)
    meta: dict[str, dict] = field(default_factory=dict)
    # series_id -> error message for ids that could not be fetched
    errors: dict[str, str] = field(default_factory=dict)


def get_adapter(name: str):
    if name == "datos_gob":
        from . import datos_gob

        return datos_gob
    if name == "bcra":
        from . import bcra

        return bcra
    if name == "bcra_balance":
        from . import bcra_balance

        return bcra_balance
    if name == "bcra_weekly":
        from . import bcra_weekly

        return bcra_weekly
    if name == "bcra_fx":
        from . import bcra_fx

        return bcra_fx
    if name == "bcra_banks":
        from . import bcra_banks

        return bcra_banks
    if name == "bcra_entities":
        from . import bcra_entities

        return bcra_entities
    if name == "finanzas":
        from . import finanzas

        return finanzas
    if name == "dolar":
        from . import dolar

        return dolar
    if name == "bcra_cambiario":
        from . import bcra_cambiario

        return bcra_cambiario
    if name == "rem":
        from . import rem

        return rem
    if name == "ustreasury":
        from . import ustreasury

        return ustreasury
    if name == "manual":
        from . import manual

        return manual
    raise KeyError(f"no adapter for source {name!r}")
