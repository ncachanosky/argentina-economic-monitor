import io

import pandas as pd

from pipeline.sources import ipmp, pcps


def _workbook() -> bytes:
    buf = io.BytesIO()
    dates = pd.to_datetime(["2026-07-01", "2026-08-01"])
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        for sheet, base in [("IPMP mensual", 200.0), ("IPMP mensual real", 100.0)]:
            pd.DataFrame({"Fecha": ["nota", *dates], "IPMP": [None, base, base + 1], "Agro": [None, 1, 2],
                          "Metales": [None, 3, 4], "Petroleo": [None, 5, 6]}).to_excel(w, sheet_name=sheet, index=False)
        pd.DataFrame({"Fecha": dates, "Soja": [6.0, 6.1], "Desconocido": [1, 1]}).to_excel(w, sheet_name="Ponderadores", index=False)
    return buf.getvalue()


def test_ipmp_parse_maps_columns():
    d = ipmp.parse(_workbook())
    v = d.set_index(["series_id", "date"])["value"]
    assert v[("ipmp:total", pd.Timestamp("2026-08-01"))] == 201
    assert v[("ipmp:energy", pd.Timestamp("2026-07-01"))] == 5
    assert v[("ipmp:total_real", pd.Timestamp("2026-07-01"))] == 100
    assert v[("ipmp:w_soy", pd.Timestamp("2026-08-01"))] == 6.1
    assert not d["series_id"].str.contains("Desconocido").any()


def test_pcps_parse():
    xml = ('<Series COUNTRY="G001" INDICATOR="PSOYB" DATA_TRANSFORMATION="USD" FREQUENCY="M">'
           '<Obs TIME_PERIOD="2026-M09" OBS_VALUE="402.5"/><Obs TIME_PERIOD="2026-M10" OBS_VALUE="410"/></Series>')
    d = pcps.parse(xml)
    assert list(d["series_id"].unique()) == ["pcps:PSOYB"]
    assert d["date"].tolist() == [pd.Timestamp("2026-09-01"), pd.Timestamp("2026-10-01")]
    assert d["value"].tolist() == [402.5, 410.0]
