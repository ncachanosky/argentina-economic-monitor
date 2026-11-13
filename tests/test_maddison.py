from pipeline.sources import maddison


def test_releases_load_with_argentina_and_regions():
    for rel in maddison.RELEASES:
        t = maddison.table(rel)
        assert "ARG" in t["countries"] and "USA" in t["countries"]
        i = t["years"].index(1913)
        assert t["countries"]["ARG"]["g"][i] and t["regions"]["we12"]["g"][i]
        assert t["citation"]


def test_extension_follows_indec_growth():
    t = maddison.table(maddison.LATEST)
    e = t["extension"]
    if e:
        a = t["countries"]["ARG"]["g"][-1]
        g0, p = e["gdp_growth"][0], e["pop_growth"]
        assert abs(e["values"][0] / (a * (1 + g0 / 100) / (1 + p / 100)) - 1) < 0.001   # rates are rounded to 0.01
