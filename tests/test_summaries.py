from datetime import date

import pandas as pd

from pipeline import registry, summaries as S


def payload(freq="M", tf="mom", real=False, n=30):
    idx = pd.date_range("2024-01-01", periods=n, freq="MS" if freq == "M" else "QS")
    vals = [100 * 1.02 ** i for i in range(n)]
    p = {"id": "x", "kind": "variants", "title": "Test index", "short_title": "Test", "units": "Index",
         "measure": "index", "frequency": freq, "source_label": "INDEC", "dates": [d.strftime("%Y-%m-%d") for d in idx],
         "variants": {"a": {"label": "A", "values": vals}}, "default": {"variant": "a", "transform": tf, "real": real},
         "next_release": {"date": "2026-11-12"}}
    if real:
        p["deflator"] = {"values": [1.0] * n}
    return p


def test_sentence_monthly_change():
    s = S.sentence(payload(), "en", lambda x: x)
    assert s.startswith("Test: 2.0% m/m in June 2026 (2.0% in May 2026). Source: INDEC.")
    assert s.endswith("Next release: 12 November 2026.")


def test_sentence_quarterly_is_q_on_q_and_spanish_format():
    s = S.sentence(payload(freq="Q", n=10), "es", lambda x: x)
    assert "% trimestral en T2 2026" in s and "2,0%" in s and "Próximo dato: 12 de noviembre de 2026" in s


def test_sentence_level_with_units():
    s = S.sentence(payload(tf="level"), "en", lambda x: x)
    assert s.startswith("Test: 177.6 Index in June 2026 (174.1 in May 2026)")


def test_non_variant_cards_have_no_sentence():
    assert S.sentence({"kind": "table", "id": "t"}, "en", lambda x: x) is None


def test_calendar_marks_missing_releases():
    reg = registry.load()
    html = S.calendar_html(reg, {}, "en", {}, "../", today=date(2026, 10, 18))
    assert 'id="upcoming"' in html and 'id="recent"' in html
    assert "Not yet in the Monitor" in html      # empty status: nothing is in
