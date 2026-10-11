"""Shared test helpers: import path, a simulated repertory.nyc feed, and a
scratch copy of the site page."""
from __future__ import annotations

import shutil
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tool"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
if str(TOOL) not in sys.path:
    sys.path.insert(0, str(TOOL))

WATCHLIST = FIXTURES / "watchlist.csv"
MOMI_EVENTS = FIXTURES / "momi_events.json"

# (title, year, director, theater, HH:MM, format, special_event)
BASE_DAY = [
    ("Naza", 2026, "Yuval Abraham, Rachel Szor", "IFC Center", "10:45", "DCP", None),
    ("Naza", 2026, "Yuval Abraham, Rachel Szor", "IFC Center", "19:40", "DCP", None),
    ("NAZA", None, None, "BAM Rose Cinemas", "21:00", "DCP", None),
    ("Wings of Desire", 1987, "Wim Wenders", "Film Forum", "20:15", "35MM", None),
    ("Ghost in the Shell: 30th Anniversary Remaster", 1995, "Mamoru Oshii", "Metrograph", "21:30", "4K DCP", None),
    ("RoboCop - The Director's Cut", 1987, "Paul Verhoeven", "Nitehawk Cinema Williamsburg", "22:00", None,
     {"id": "e", "event_type": "q_and_a", "description": "Q&A with the editor Read More ›", "guests": []}),
    ("The Wedding Banquet", 1993, "Ang Lee", "Quad Cinema", "18:00", "DCP", None),   # remake on watchlist: no match
    ("Not On The Watchlist", 2020, None, "IFC Center", "12:00", "General Admission: $17", None),
]


def row(d: date, i: int, t) -> dict:
    title, year, director, theater, hhmm, fmt, event = t
    return {"id": f"{d.isoformat()}-{i}", "film_title": title, "film_year": year,
            "film_director": director, "theater_name": theater, "theater_slug": "x",
            "date": d.isoformat(), "time": hhmm, "format": fmt,
            "ticket_url": f"https://tickets.example.org/{d.isoformat()}/{i} ",
            "special_event": event}


def make_feed(start: date, days: int = 16) -> dict[date, list[dict]]:
    """Two weeks of the base day, then one far-off listing."""
    feed: dict[date, list[dict]] = {}
    for k in range(days):
        d = start + timedelta(days=k)
        feed[d] = [row(d, i, t) for i, t in enumerate(BASE_DAY)]
    far = start + timedelta(days=40)
    feed[far] = [row(far, 99, ("High and Low", 1963, "Akira Kurosawa", "Film Forum", "19:00", "35mm", None))]
    return feed


class FakeFeed:
    """Stands in for nyc_repertory.fetch_day; can fail on chosen days."""

    def __init__(self, feed: dict[date, list[dict]], fail_on=()):
        self.feed, self.fail_on, self.calls = feed, set(fail_on), []

    def __call__(self, day: date, url=None) -> list[dict]:
        self.calls.append(day)
        if day in self.fail_on:
            raise OSError("simulated network error")
        return [dict(r) for r in self.feed.get(day, [])]


def scratch_site() -> Path:
    """A temp dir holding the site page and stylesheet, as the job sees them."""
    d = Path(tempfile.mkdtemp())
    shutil.copy(ROOT / "screenings-nyc.html", d / "screenings-nyc.html")
    shutil.copy(ROOT / "styles.css", d / "styles.css")
    return d


def momi_rows(events_file: Path = None) -> list[dict]:
    """The fixture MoMI calendar, parsed as the daily job would."""
    import json
    import nyc_momi
    data = json.loads((events_file or MOMI_EVENTS).read_text())
    return [r for r in (nyc_momi.parse_event(e) for e in data["events"]) if r]


def fake_momi(rows=None, fail=False):
    """Stands in for nyc_momi.fetch_screenings."""
    def fetch(start, end, **kw):
        if fail:
            raise OSError("simulated MoMI outage")
        return [dict(r) for r in (momi_rows() if rows is None else rows) if start.isoformat() <= r["date"] <= end.isoformat()]
    return fetch


SS_DETAILS = FIXTURES / "screenslate_details.json"
SS_VENUE_PAGES = {
    "paris-theater": "<div>Address 4 West 58th Street, New York, NY 10019</div>",
    "museum-modern-art": "<div>Address 11 West 53rd Street, New York, NY 10019</div>",
    "spectacle": "<div>Address 124 S. 3rd Street, Brooklyn, NY 11249</div>",
}


class FakeScreenSlate:
    """Stands in for nyc_screenslate.ScreenSlate: the fixture's screenings
    every day (Paris, MoMA, a Spectacle double feature, and a Film Forum
    listing that must be skipped as already covered)."""

    def __init__(self, fail=False):
        import json
        self.fail = fail
        self.details = {d["nid"]: d for d in json.loads(SS_DETAILS.read_text())}
        self.pages = []

    def fetch_day(self, day, url=None):
        import nyc_screenslate
        if self.fail:
            raise OSError("simulated Screen Slate outage")
        if day > date(2026, 10, 25):              # two weeks of listings, like make_feed
            return []
        ts = day.isoformat()
        showtimes = [{"nid": "201", "field_timestamp": f"{ts}T19:00:00", "field_note": ""},
                     {"nid": "202", "field_timestamp": f"{ts}T16:00:00", "field_note": "Introduced by Dave Kehr"},
                     {"nid": "203", "field_timestamp": f"{ts}T21:30:00", "field_note": "Q&amp;A"},
                     {"nid": "204", "field_timestamp": f"{ts}T20:15:00", "field_note": ""}]
        return nyc_screenslate.to_rows(showtimes, self.details, set(nyc_screenslate.COVERED))

    def get_page(self, path):
        self.pages.append(path)
        return SS_VENUE_PAGES[path.rsplit("/", 1)[1]].encode()
