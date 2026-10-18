"""Primary-source readers, on sample copies of the real files (tests/fixtures/primary).

When a publisher changes a file's layout, add the new file here next to the old one and
make the reader pass on both.
"""
from pathlib import Path

import pandas as pd
import pytest

from pipeline.sources import primary as P

FIX = Path(__file__).parent / "fixtures" / "primary"


def raw(name: str) -> bytes:
    return (FIX / name).read_bytes()


CASES = [
    ("read_emae", "emae.xls", 3, "143.3_NO_PR_2004_A_31", "2026-07-01", 149.6479),
    ("read_emae_sectors", "emae_act.xls", 16, "11.3_VMASD_2004_M_23", "2026-07-01", 119.1344),
    ("read_wages", "indice_salarios.csv", 4, "149.1_SOR_PRIADO_OCTU_0_25", "2026-07-01", 9879.87),
    ("read_sipa", "trabajoregistrado.xlsx", 7, "151.1_AARIADOTAC_2012_M_26", "2026-07-01", 6106.796),
    ("read_ripte", "ripte.html", 1, "158.1_REPTE_0_0_5", "2026-08-01", 1982518.19),
    ("read_cpi", "serie_ipc_divisiones.csv", 22, "148.3_INIVELNAL_DICI_M_26", "2026-09-01", 12514.1508),
]


@pytest.mark.filterwarnings("ignore")
@pytest.mark.parametrize("reader,file,n,sid,date,value", CASES)
def test_reader_reads_the_sample_file(reader, file, n, sid, date, value):
    out = getattr(P, reader)(raw(file))
    assert len(out) == n
    s = out[sid]
    assert s.index.is_monotonic_increasing and not s.index.has_duplicates
    assert s.index.max() == pd.Timestamp(date)
    assert s[pd.Timestamp(date)] == pytest.approx(value, rel=1e-6)


def test_every_provider_covers_its_ids():
    for p in P.PROVIDERS:
        assert P._ids(p), p["name"]


def test_moved_header_is_still_found():
    """An inserted column must not shift the numbers: readers find columns by label."""
    df = pd.read_excel(FIX / "emae.xls", header=None)
    df.insert(2, "new", None)
    h = P.find_row(df, r"^per[ií]odo", col=0)
    assert P.find_col(df, h, r"desestacionalizada") == 5


def test_renamed_column_raises_layout_changed():
    text = raw("indice_salarios.csv").decode("latin-1").replace("IS_sector_privado_registrado", "IS_privado")
    with pytest.raises(P.LayoutChanged) as e:
        P.read_wages(text.encode("latin-1"))
    assert "IS_sector_privado_registrado" in str(e.value)


def test_missing_sheet_raises_layout_changed(tmp_path):
    f = tmp_path / "x.xlsx"
    pd.DataFrame({"a": [1]}).to_excel(f, sheet_name="T.9", index=False)
    with pytest.raises(P.LayoutChanged):
        P.read_sipa(f.read_bytes())


def test_agreement_flags_a_shifted_series(monkeypatch):
    idx = pd.date_range("2015-01-01", periods=60, freq="MS")
    stored = pd.Series(range(100, 160), index=idx, dtype=float)
    monkeypatch.setattr(P.store, "as_of", lambda sid: stored)
    assert P.agreement("x", stored * 1.001) is None             # rounding: fine
    assert P.agreement("x", stored.shift(1).dropna()) is not None  # one row off: flagged


def test_failed_provider_reports_and_falls_back(monkeypatch, tmp_path):
    monkeypatch.setattr(P, "STATUS_FILE", tmp_path / "source_files.json")
    def broken(url, retries=3):
        raise P.FileNotFound("gone")
    monkeypatch.setattr(P, "download", broken)
    data, meta, served = P.fetch(["143.3_NO_PR_2004_A_31"])
    assert not served and data.empty
    st = P._load_status()
    entry = st["INDEC EMAE (sh_emae_mensual_base2004.xls)"]
    assert entry["status"] == "error" and entry["kind"] == "not_found" and entry["error_since"]


def test_source_issue_rules():
    from datetime import datetime, timedelta, timezone
    from pipeline import alerts
    now = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
    recent = (now - timedelta(hours=1)).isoformat()
    old = (now - timedelta(hours=30)).isoformat()
    files = {
        "ok file": {"status": "ok"},
        "new layout": {"status": "error", "kind": "layout", "error_since": recent, "error": "no column /total/",
                       "preview": "Período | ...", "url": "https://x/y.xls"},
        "briefly missing": {"status": "error", "kind": "not_found", "error_since": recent, "error": "timeout"},
        "long missing": {"status": "error", "kind": "not_found", "error_since": old, "error": "404"},
    }
    r = alerts.source_reports(files, now)
    assert r["ok file"] is None
    assert r["new layout"][0] == "Source file changed: new layout" and "no column /total/" in r["new layout"][1]
    assert "<!-- source: new layout -->" in r["new layout"][1]
    assert "briefly missing" not in r                      # waits MIN_HOURS
    assert r["long missing"][0].startswith("Source file not found")
