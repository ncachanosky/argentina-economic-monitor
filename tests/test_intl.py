import json

from pipeline.sources import intl, world
import pandas as pd

XML = """<x><Series COUNTRY="ARG" INDICATOR="NGDP_RPCH" FREQUENCY="A" COUNTRY_UPDATE_DATE="9/30/2026"><Obs TIME_PERIOD="2025" OBS_VALUE="4.48"/><Obs TIME_PERIOD="2026" OBS_VALUE="2.06"/></Series>
<Series COUNTRY="BRA" INDICATOR="NGDP_RPCH" FREQUENCY="A" COUNTRY_UPDATE_DATE="9/24/2026"><Obs TIME_PERIOD="2025" OBS_VALUE="2.3"/></Series>
<Series COUNTRY="ARG" INDICATOR="PPPGDP" FREQUENCY="A"><Obs TIME_PERIOD="2025" OBS_VALUE="1500"/></Series>
<Series COUNTRY="BRA" INDICATOR="PPPGDP" FREQUENCY="A"><Obs TIME_PERIOD="2025" OBS_VALUE="4800"/></Series></x>"""
WB_C = [{}, [{"id": "ARG", "iso2Code": "AR", "name": "Argentina", "region": {"value": "Latin America & Caribbean "}},
             {"id": "BRA", "iso2Code": "BR", "name": "Brazil", "region": {"value": "Latin America & Caribbean "}},
             {"id": "LCN", "iso2Code": "ZJ", "name": "LAC", "region": {"value": "Aggregates"}}]]


def test_weo_parse_and_edition():
    out, edition = intl.parse_weo(XML)
    assert edition == "October 2026"
    assert out["NGDP_RPCH"]["ARG"][2025] == 4.48


def test_build_groups_and_projections():
    b = intl.build(XML, WB_C, {})
    idx = b["index"]
    assert idx["proj_from"] == 2026 and set(idx["countries"]) == {"ARG", "BRA"}
    merc = next(g for g in idx["groups"]["blocs"] if g["key"] == "mercosur")
    assert merc["members"] == ["ARG", "BRA"] and merc["main"][0] == "BRA"   # largest economy first
    g = b["tables"]["growth"]
    assert g["values"]["ARG"][g["years"].index(2026)] == 2.1


def test_world_table():
    t = world.table("x", "X", "u", 1, "s", pd.DataFrame({"ARG": [1.0, None], "BRA": [None, None]}, index=[2000, 2001]))
    assert t["years"] == [2000, 2001] and t["values"] == {"ARG": [1.0, None]} and t["latest"] == "2001-01-01"
