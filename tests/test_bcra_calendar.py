from pipeline import registry
from pipeline.sources import bcra_calendar

PAGE = """<table><tr><th>Publicaciones</th><th>Fecha</th></tr>
<tr>    <td>Relevamiento de Expectativas de Mercado (REM)</td>    <td>05 nov 2026</td>    </tr>
<tr><td>Informe sobre Bancos</td><td>20 nov 2026</td></tr>
<tr><td>Informe Monetario Mensual</td><td>09 dic 2026</td></tr>
<tr><td>Informe de Evolución del Mercado de Cambios y Balance Cambiario</td><td>31 dic 2026</td></tr>
<tr><td>Informe sobre Bancos</td><td>18 dic 2026</td></tr></table>"""


def test_parse_keeps_the_three_reports_with_their_data_month():
    out = bcra_calendar.parse(PAGE)
    assert out["rem_bcra"]["dates"] == [{"date": "2026-11-05", "period": "2026-10"}]
    assert [d["date"] for d in out["bcra_bancos"]["dates"]] == ["2026-11-20", "2026-12-18"]
    assert out["bcra_bancos"]["dates"][0]["period"] == "2026-09"
    assert out["bcra_cambiario"]["dates"] == [{"date": "2026-12-31", "period": "2026-11"}]
    assert all(c["publisher"] == "BCRA" for c in out.values())


def test_bcra_releases_link_cards_without_release_keys():
    reg = registry.load()
    for key in bcra_calendar.REPORTS:
        if key in reg.releases:
            assert reg.release_cards(key), key
            assert not [i for i in reg.indicators if i.release == key]
    assert all(cal.get("publisher") for cal in reg.releases.values())
