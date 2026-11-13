import pandas as pd

from pipeline.sources import lallpi


def test_table_and_leaders():
    t = lallpi.load()
    assert t["years"][0] == 2000 and t["years"][-1] == lallpi.END == 2019
    assert len(t["countries"]) == 22
    arg = t["countries"]["ARG"]
    i = t["years"].index(2013)
    # The index's own construction: POP = POP_R x (IP + EP) / 2.
    assert abs(arg["pop"][i] - arg["r"][i] * (arg["ip"][i] + arg["ep"][i]) / 2) < 0.02
    assert arg["leader"][i] == "Cristina Fernández de Kirchner" and arg["party"][i] == "FPV-PJ"
    # Every country-year has a head of government.
    assert all(all(c["leader"]) for c in t["countries"].values())


def test_leaders_file_has_no_overlaps():
    L = pd.read_csv(lallpi.LEADERS)
    years = [(r.iso3, y) for r in L.itertuples() for y in range(r._2, r.to + 1)]
    assert len(years) == len(set(years)) == 22 * 20
