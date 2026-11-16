from pipeline import catalog


def test_period_labels():
    assert catalog._period("2026-08-01", "M") == "2026-08"
    assert catalog._period("2026-04-01", "Q") == "2026-Q2"
    assert catalog._period("2026-07-01", "S") == "2026-H2"
    assert catalog._period("2024-01-01", "Y") == "2024"
    assert catalog._period("2026-11-13", "D") == "2026-11-13"
    assert catalog._period(None, "M") == ""
