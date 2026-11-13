import json
import xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo

from pipeline import feed

BA = ZoneInfo("America/Argentina/Buenos_Aires")


def test_digest_is_due_on_monday_morning_once_per_week():
    mon_early, mon, tue, wed = (datetime(2026, 11, 16, 6, 30, tzinfo=BA), datetime(2026, 11, 16, 7, 30, tzinfo=BA),
                                datetime(2026, 11, 17, 7, 30, tzinfo=BA), datetime(2026, 11, 18, 7, 30, tzinfo=BA))
    assert not feed.due(mon_early, [])
    assert feed.due(mon, []) and feed.due(tue, [])          # Tuesday catches a missed Monday
    assert not feed.due(wed, [])
    assert not feed.due(mon, [{"week_start": "2026-11-09"}])
    a, b = feed.week_bounds(mon)
    assert (a.isoformat(), b.isoformat()) == ("2026-11-09", "2026-11-15")


def test_titles_and_numbers_in_both_languages():
    a, b = feed.week_bounds(datetime(2026, 11, 2, 8, tzinfo=BA))
    assert feed._span(a, b, "en") == "Week of Oct 26 to Nov 1, 2026"
    assert feed._span(a, b, "es") == "Semana del 26 de oct al 1 de nov de 2026"
    row = {"freq": "M", "mm": {"kind": "pct", "values": {"2026-07-01": [-2.81, 0], "2026-08-01": [0.066, 0], "2026-09-01": None}},
           "yy": {"kind": "pct", "values": {"2026-08-01": [-1.744, 0]}}}
    assert feed._changes(row, "en") == "+0.1% m/m, −1.7% y/y"
    assert feed._changes(row, "es") == "+0,1% m/m, −1,7% i.a."


def test_rss_is_well_formed():
    item = {"title": "A & B", "link": "https://x/?ref=rss#a", "guid": "g", "date": datetime(2026, 11, 16, 10, 30, tzinfo=BA),
            "html": "<p>x</p>"}
    root = ET.fromstring(feed._rss("T", "https://x/", "D", "https://x/feed.xml", "en", [item]).encode())
    assert root.find("channel/item/title").text == "A & B"
