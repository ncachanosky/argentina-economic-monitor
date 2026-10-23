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
    ("read_trade_totals", "balanmensual.xls", 3, "74.3_IET_0_M_16", "2026-08-01", 8882.5825),
    ("read_export_groups", "expo_grandes_rubros.xls", 4, "74.3_IEMOI_0_M_46", "2026-08-01", 2356.2457),
    ("read_import_uses", "impo_uso_economico.xls", 7, "74.3_IIBCA_0_M_32", "2026-08-01", 1310.2484),
    ("read_ica_partners", "ica_cuadros.xls", 8, "77.3_IEB_0_A_24", "2026-09-01", 1205.694386),
    ("read_ica_annex_partners", "ica_anexo_cuadros.xls", 4, "78.3_IIRU_0_A_23_52", "2026-09-01", 1016.544843),
    ("read_trade_indices", "sh_indices_comext_04.xls", 7, "82.2_ITI_2004_T_27", "2026-04-01", 145.1401),
    ("read_trade_1910", "balan_1910.xls", 2, "74.1_IET_0_A_16", "2025-01-01", 87111.1968),
    ("read_ipi", "sh_ipi_manufacturero.xlsx", 2, "453.1_SERIE_DESEADA_0_0_24_58", "2026-08-01", 114.286133),
    ("read_isac", "sh_isac.xlsx", 3, "33.2_ISAC_CICLOCIA_0_M_20_62", "2026-08-01", 142.428332),
    ("read_national_accounts", "sh_oferta_demanda.xlsx", 34, "6.2_IPNS_2004_T_35", "2026-04-01", 65507.9437645984),
    ("read_gdp_sa", "sh_oferta_demanda_desest.xls", 1, "3.2_OGP_D_2004_T_17", "2026-04-01", 747969.9),
    ("read_ucii", "sh_capacidad.xls", 13, "31.3_UV_2004_M_25", "2026-08-01", 46.2),
    ("read_ipim", "indice_ipim.csv", 5, "448.1_NIVEL_GENERAL_0_0_13_46", "2026-09-01", 17104.023329),
    ("read_cgi", "serie_cgi.xlsx", 3, "53.1_TRTA_0_0_37", "2026-04-01", 0.429375),
    ("read_gini", "serie_coeficiente_gini.xlsx", 1, "65.1_CGI_0_0_21", "2026-04-01", 0.428),
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


def test_partner_csv_reader():
    out = P.datos_csv(P.EXPO_PAISES)(raw("expo_paises.csv"))
    assert out["77.3_IEB_0_A_24"][pd.Timestamp("2026-08-01")] == pytest.approx(1183.9973, rel=1e-6)


def test_ica_files_are_found_on_the_page(monkeypatch):
    monkeypatch.setattr(P, "download", lambda url, retries=3: raw("indec_ica_page.html"))
    found = {p["name"]: p["url"]() for p in P.PROVIDERS if p.get("page") == P.ICA_PAGE}
    assert any(u.endswith("expo_grandes_rubros_2025_2026.xls") for u in found.values())
    assert any(u.endswith("balan_1910_2025.xls") for u in found.values())
    assert any(u.endswith("ica_cuadros_18_09_26.xls") for u in found.values())


def test_release_file_takes_the_newest_date(monkeypatch):
    page = b'<a href="ftp/cuadros/economia/ica_cuadros_19_10_26.xls"> <a href="ftp/cuadros/economia/ica_cuadros_20_12_25.xls">'
    monkeypatch.setattr(P, "download", lambda url, retries=3: page)
    assert P.ica_release_file("ica_cuadros")().endswith("ica_cuadros_19_10_26.xls")


@pytest.mark.filterwarnings("ignore")
def test_partner_tables_read_both_years_and_stop_at_the_next_table():
    out = P.read_ica_partners(raw("ica_cuadros.xls"))
    br, cn = out["78.3_IIB_0_A_24"], out["78.3_IIC_0_A_23"]
    assert br.index.min() == pd.Timestamp("2025-01-01") and len(br) == 21
    assert br[pd.Timestamp("2025-12-01")] == pytest.approx(1137.530385)
    assert cn[pd.Timestamp("2025-01-01")] == pytest.approx(1533.817965)   # China's block, not Brazil's
    assert out["77.3_IET_0_A_25"][pd.Timestamp("2026-09-01")] == pytest.approx(10035.975188)
    annex = P.read_ica_annex_partners(raw("ica_anexo_cuadros.xls"))
    assert annex["77.3_IETM_0_A_32"][pd.Timestamp("2025-10-01")] == pytest.approx(1623.406629)


def test_fallback_provider_only_fills_what_primary_missed(monkeypatch, tmp_path):
    """If the INDEC trade-balance file fails, its series come from the datos.gob.ar CSV;
    if it works, the CSV is not used for them."""
    monkeypatch.setattr(P, "STATUS_FILE", tmp_path / "s.json")
    monkeypatch.setattr(P, "agreement", lambda sid, s, *a: None)
    calls = []
    def fake(url, retries=3):
        calls.append(url)
        if url.endswith("balanmensual.xls"):
            raise P.FileNotFound("down")
        idx = pd.date_range("2020-01-01", periods=3, freq="MS")
        cols = {c: [1.0, 2.0, 3.0] for c in P.ICA_CSV.values()}
        return pd.DataFrame({"indice_tiempo": idx.strftime("%Y-%m-%d"), **cols}).to_csv(index=False).encode()
    monkeypatch.setattr(P, "download", fake)
    data, meta, served = P.fetch(["74.3_IET_0_M_16"])
    assert served == {"74.3_IET_0_M_16"} and "datos.gob.ar" in meta["74.3_IET_0_M_16"]["dataset"]
    assert any("intercambio-comercial" in u for u in calls)


def test_dated_file_takes_the_newest_month(monkeypatch):
    """sh_capacidad_MM_YY.xls: the resolver walks back from this month to the first real Excel file."""
    class R:
        def __init__(self, ok, body): self.ok, self.body = ok, body
        def iter_content(self, n): yield self.body[:n]
        def close(self): pass
    tried = []
    def get(url, **kw):
        tried.append(url)
        return R(len(tried) == 3, b"\xd0\xcf\x11\xe0rest")
    monkeypatch.setattr(P.requests, "get", get)
    url = P.dated_file("sh_capacidad")()
    assert url == tried[2] and len(tried) == 3 and url.endswith(".xls")


def test_ucii_months_follow_the_year_rows():
    s = P.read_ucii(raw("sh_capacidad.xls"))["31.3_UNG_2004_M_18"]
    assert s[pd.Timestamp("2016-01-01")] == pytest.approx(62.9)
    assert s[pd.Timestamp("2025-12-01")] == pytest.approx(53.8)


def test_national_accounts_rows_and_quarters():
    out = P.read_national_accounts(raw("sh_oferta_demanda.xlsx"))
    gdp = out["4.2_OGP_2004_T_17"]
    assert gdp.index.min() == pd.Timestamp("2004-01-01") and len(gdp) == 90   # quarters only, no annual totals
    assert out["4.2_DGIC_2004_T_32"][pd.Timestamp("2026-04-01")] == pytest.approx(42858.498, rel=1e-6)  # not "Otras construcciones"
    assert out["4.2_DE_2004_T_24"][pd.Timestamp("2010-01-01")] == 0.0                                  # blank in definitive years


def test_latest_national_accounts_file_is_picked(monkeypatch):
    page = raw("indec_ca_page.html").decode("utf-8", "replace") + ' href="/ftp/cuadros/economia/sh_oferta_demanda_12_25.xls"'
    monkeypatch.setattr(P, "download", lambda url, retries=3: page.encode())
    assert P.ca_file(r"sh_oferta_demanda_\d{2}_\d{2}\.xls")().endswith("sh_oferta_demanda_09_26.xls")


def test_public_sector_csv_reader():
    out = P.datos_csv(P.AIF_CSV)(raw("aif_17.csv"))
    assert len(out) == 12 and all(s.index.min() == pd.Timestamp("2015-01-01") for s in out.values())
    assert out["379.9_GTOS_PRIMA017__37_33"].index.max() == pd.Timestamp("2026-08-01")


@pytest.mark.parametrize("mapping,file,last", [("BOP_TRIM", "bop_mbp6_trim.csv", "2026-04-01"),
                                                ("IMIG_MENSUAL", "imig_mensual.csv", "2026-08-01")])
def test_dataset_csv_readers(mapping, file, last):
    cols = getattr(P, mapping)
    out = P.datos_csv(cols)(raw(file))
    assert set(out) == set(cols)
    assert max(s.index.max() for s in out.values()) == pd.Timestamp(last)


def test_every_datos_series_has_a_csv_file():
    """Adding a datos.gob.ar series to the registry needs `python -m pipeline.datos_catalog`."""
    from pipeline import datos_catalog
    m = datos_catalog.load_map()
    missing = [sid for sid in datos_catalog.datos_ids() if sid not in m]
    assert not missing, f"not in registry/datos_catalog.json: {missing}"


def test_catalog_providers_group_by_file():
    provs = P.catalog_providers({"43.2_ECTAT_0_T_33", "45.2_ECTDT_0_T_33", "370.3_ICG_NIVEL_RAL_0_0_17_94",
                                 "370.3_HONESTIDADDAD_0_0_10_58"})
    assert len(provs) == 3 and sorted(len(p["ids"]) for p in provs) == [1, 1, 2]


def test_x13_removes_a_seasonal_pattern():
    """The Monitor's seasonal adjustment (X-13ARIMA-SEATS) flattens a series with a known seasonal pattern."""
    pytest.importorskip("x13binary")
    import numpy as np, tempfile
    from pathlib import Path as _P
    from pipeline.sources import x13
    idx = pd.date_range("2010-01-01", periods=120, freq="MS")
    trend = np.linspace(100, 130, 120)
    season = np.tile([0.94, 0.95, 1.03, 1.01, 1.06, 1.02, 0.99, 1.0, 0.99, 1.0, 1.01, 1.0], 10)
    s = pd.Series(trend * season, index=idx)
    with tempfile.TemporaryDirectory() as d:
        sa = x13.adjust(s, _P(d))
    assert len(sa) == 120
    assert sa.pct_change().std() < 0.25 * s.pct_change().std()      # the monthly seasonal swings are gone
