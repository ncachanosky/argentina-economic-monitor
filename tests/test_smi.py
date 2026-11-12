import json

from pipeline.sources import smi


def test_edition_file_loads_with_iso_codes_and_consistent_scores():
    t = smi.load()
    assert t["count"] == len(t["countries"]) == 132
    arg = next(c for c in t["countries"] if c["iso"] == "ARG")
    assert arg["rank"]["overall"] == 61 and arg["iso2"] == "AR"
    assert abs(arg["score"]["overall"] - 5.7514) < 1e-3
    for c in t["countries"]:
        # Archbridge's aggregation: pillars average their sub-pillars, the overall index the pillars.
        pillars = [m["key"] for m in t["measures"] if m["parent"] is None and m["key"] != "overall"]
        assert abs(c["score"]["overall"] - sum(c["score"][p] for p in pillars) / 3) < 0.01
        for p in pillars:
            subs = [m["key"] for m in t["measures"] if m["parent"] == p]
            assert abs(c["score"][p] - sum(c["score"][s] for s in subs) / 2) < 0.01, (c["name"], p)


def test_every_latin_american_country_is_on_the_map():
    t = smi.load()
    geo = json.loads(smi.MAP_FILE.read_text(encoding="utf-8"))
    latam = [c["iso"] for c in t["countries"] if c["region"] == t["latam"]]
    assert len(latam) == 21
    assert all(i in geo["paths"] for i in latam)


def test_series_and_edition_check():
    res = smi.fetch(["smi:ARG:overall", "smi:ARG:rank"], check=False)
    assert not res.errors
    assert set(res.data["series_id"]) == {"smi:ARG:overall", "smi:ARG:rank"}
    assert smi.page_edition('<script id="smi-data">{"edition":2027,"dataYears":[2025]}</script>') == 2027
    assert smi.page_edition("2026 RANKINGS + SCORES") == 2026
