"""Seasonally adjusted series computed by the Monitor with X-13ARIMA-SEATS.

INDEC publishes the EMAE seasonally adjusted only for the total; its sectors come
original. Ids "x13:<stored id>" are that stored series adjusted here with the US
Census Bureau's X-13ARIMA-SEATS (the program INDEC uses), through the `x13binary`
package:

  - log transform; regARIMA model chosen automatically (automdl);
  - trading-day (td) and Easter (8 days) regressors; automatic additive-outlier,
    level-shift and temporary-change detection;
  - X-11 filters, final seasonally adjusted series (table D11).

INDEC's exact settings (its outliers, calendar and model per series) are not public
in full, so this is an approximation, not INDEC's series. Run on the total EMAE, these
settings track INDEC's own adjusted series closely: correlation of monthly changes
0.94 since 2017, mean absolute gap 0.6 points, levels within about 0.6%.

The whole history is re-estimated each run, as with any seasonal adjustment, so past
values can be revised when a new month arrives.
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import pandas as pd

from .. import store
from . import FetchResult

SPEC = """series{{ start={y}.{m} period=12
data=(
{data}
) }}
transform{{ function=log }}
regression{{ variables=(td easter[8]) }}
outlier{{ types=(ao ls tc) }}
automdl{{ }}
forecast{{ maxlead=12 }}
x11{{ save=(d11) }}
"""


def _binary() -> str:
    import x13binary
    return x13binary.find_x13_bin()


def adjust(s: pd.Series, workdir: Path, name: str = "s") -> pd.Series:
    """X-13 seasonally adjusted version of a monthly series (positive values)."""
    s = s.dropna().sort_index()
    if len(s) < 36 or (s <= 0).any():
        raise ValueError("needs three years of positive monthly data")
    rows = [" ".join(f"{v:.6f}" for v in s.values[i:i + 8]) for i in range(0, len(s), 8)]   # spec lines stay under 132 characters
    (workdir / f"{name}.spc").write_text(SPEC.format(y=s.index[0].year, m=s.index[0].month, data="\n".join(rows)))
    run = subprocess.run([_binary(), name, "-q"], cwd=workdir, capture_output=True, text=True, timeout=120)
    out = workdir / f"{name}.d11"
    if not out.exists():
        raise RuntimeError(f"X-13 failed: {(run.stdout or run.stderr)[-300:]}")
    t = pd.read_csv(out, sep="\t", skiprows=2, header=None, names=["d", "v"])
    t.index = pd.to_datetime(t["d"].astype(str), format="%Y%m")
    return t["v"].astype(float)


def fetch(ids) -> FetchResult:
    frames, meta, errors = [], {}, {}
    with tempfile.TemporaryDirectory() as tmp:
        for n, sid in enumerate(sorted(ids)):
            src = sid.split(":", 1)[1]
            try:
                sa = adjust(store.as_of(src), Path(tmp), f"s{n}")
            except Exception as exc:
                errors[sid] = str(exc)
                continue
            frames.append(pd.DataFrame({"date": sa.index, "series_id": sid, "value": sa.values}))
            meta[sid] = {"units": None, "description": f"{src}, seasonally adjusted by the Monitor (X-13ARIMA-SEATS)",
                         "time_index_end": sa.index.max().strftime("%Y-%m-%d"), "dataset": "Monitor estimate (X-13ARIMA-SEATS)"}
    data = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["date", "series_id", "value"])
    return FetchResult(data=data, meta=meta, errors=errors)
