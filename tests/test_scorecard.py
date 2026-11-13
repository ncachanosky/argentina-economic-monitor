from datetime import date

from pipeline import scorecard

MILEI = {"name": "Javier Milei", "short": "Milei", "start": "2023-12-10", "end": None}
FERNANDEZ = {"name": "Alberto Fernández", "short": "A. Fernández", "start": "2019-12-10", "end": "2023-12-10"}
ALFONSIN = {"name": "Raúl Alfonsín", "short": "Alfonsín", "start": "1983-12-10", "end": "1989-07-08"}


def test_windows_follow_the_site_convention():
    # The handover month belongs to the outgoing president.
    assert scorecard.window(MILEI, "M")[:2] == (date(2023, 12, 1), date(2024, 1, 1))
    assert scorecard.window(FERNANDEZ, "M")[2] == date(2023, 12, 1)
    assert scorecard.window(ALFONSIN, "M")[2] == date(1989, 7, 1)
    assert scorecard.window(MILEI, "Q")[:2] == (date(2023, 10, 1), date(2024, 1, 1))
    # Annual data: the president in office on 1 July.
    assert scorecard.window(ALFONSIN, "Y") == (date(1983, 1, 1), date(1984, 1, 1), date(1989, 1, 1))
    # Semesters count only if they ended within the term.
    assert scorecard.window(FERNANDEZ, "S")[2] == date(2023, 1, 1)


def test_cell_measures_and_partial_terms():
    dates = [f"{y}-{m:02d}-01" for y in range(2019, 2024) for m in range(1, 13)]
    card = {"frequency": "M", "dates": dates, "variants": {"x": {"values": [100 * 1.01 ** i for i in range(len(dates))]}}}
    c = scorecard.cell(card, "x", "cum", FERNANDEZ, {})
    assert c["from"] == "2019-12-01" and c["to"] == "2023-12-01" and not c["partial"]
    assert abs(c["value"] - 100 * (1.01 ** 48 - 1)) < 1e-9
    # A series starting late in the term: blank if it covers less than half of it.
    late = {"frequency": "M", "dates": dates[-10:], "variants": {"x": {"values": [1.0] * 10}}}
    assert scorecard.cell(late, "x", "cum", FERNANDEZ, {}) is None
    mid = {"frequency": "M", "dates": dates[24:], "variants": {"x": {"values": [1.0] * (len(dates) - 24)}}}
    assert scorecard.cell(mid, "x", "se", FERNANDEZ, {})["partial"]
