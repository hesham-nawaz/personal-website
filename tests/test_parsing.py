"""Feed parsing, title matching and watchlist sync."""
from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from tests.helpers import WATCHLIST, FakeFeed, make_feed  # noqa: F401  (sets sys.path)

import json

import letterboxd_watchlist as lb
import nyc_momi
import nyc_screenslate
from tests.helpers import MOMI_EVENTS, SS_DETAILS
import nyc_repertory as nr
from watchlist import load_watchlist, title_variants


class FeedParsing(unittest.TestCase):
    def test_pages_through_a_whole_day(self):
        # The API returns at most 100 rows per request; a busy day needs several.
        day = date(2026, 10, 12)
        rows = [{"id": str(i), "film_title": f"F{i}", "date": day.isoformat(), "time": "12:00"}
                for i in range(237)]

        def fake_fetch_raw(url=nr.API_URL, timeout=30, query_date=None, limit=None, offset=0):
            return rows[offset:offset + min(limit or 50, 100)]

        with mock.patch.object(nr, "fetch_raw", fake_fetch_raw):
            self.assertEqual(len(nr.fetch_day(day)), 237)

    def test_format_tags(self):
        cases = {"DCP": None, "Digital": None, "35MM": "35mm", "16mm": "16mm", "4K DCP": "4K",
                 "4K RESTORATION": "4K restoration", "3D DCP": "3D", "100m": None,
                 "General Admission: $17": None, None: None}
        for raw, want in cases.items():
            self.assertEqual(nr.format_tag(raw), want, raw)

    def test_event_labels(self):
        self.assertEqual(nr.event_label({"event_type": "q_and_a", "description": "Introduced by X Read More ›"}),
                         "Introduced by X")
        self.assertEqual(nr.event_label({"event_type": "q_and_a", "description": ""}), "Q&A")
        self.assertIsNone(nr.event_label({"event_type": "other", "description": ""}))
        self.assertIsNone(nr.event_label(None))

    def test_ticket_links_follow_their_showtimes(self):
        recs = [{"id": "a", "title": "Naza", "year": 2026, "date": "2026-10-12", "time": "19:40",
                 "theater": "IFC Center", "format": "DCP", "ticket_url": "https://t/2"},
                {"id": "b", "title": "Naza", "year": 2026, "date": "2026-10-12", "time": "10:45",
                 "theater": "IFC Center", "format": "DCP", "ticket_url": "https://t/1"}]
        [s] = nr.screenings_from_records(recs)
        self.assertEqual(s.times, ["10:45a", "7:40p"])
        self.assertEqual(s.ticket_urls, ["https://t/1", "https://t/2"])
        self.assertIsNone(s.notes)


class MoMICalendar(unittest.TestCase):
    def test_parses_screenings_only(self):
        events = json.loads(MOMI_EVENTS.read_text())["events"]
        rows = [r for r in (nyc_momi.parse_event(e) for e in events) if r]
        self.assertEqual([r["id"] for r in rows], ["momi-101", "momi-102"])   # no exhibition, no talk
        a, b = rows
        self.assertEqual((a["film_title"], a["film_year"], a["film_director"], a["date"], a["time"], a["format"]),
                         ("In the Mouth of Madness", 1994, "John Carpenter", "2026-10-12", "15:30", "35mm"))
        self.assertIsNone(a["special_event"])
        self.assertEqual((b["film_year"], b["film_director"], b["format"], b["special_event"]),
                         (1987, "Wim Wenders", "4K restoration", "Q&A"))
        self.assertEqual(b["ticket_url"], "https://movingimage.org/event/wings-of-desire/")

    def test_follows_pages(self):
        pages = {"p1": {"events": [{"id": 1}], "next_rest_url": "p2"}, "p2": {"events": [{"id": 2}], "next_rest_url": None}}
        calls = []

        def get(url):
            calls.append(url)
            return pages["p1"] if len(calls) == 1 else pages[url]

        self.assertEqual([e["id"] for e in nyc_momi.fetch_events(date(2026, 10, 10), date(2026, 12, 1), get)], [1, 2])


class ScreenSlateListings(unittest.TestCase):
    def setUp(self):
        self.details = {d["nid"]: d for d in json.loads(SS_DETAILS.read_text())}

    def test_parses_single_and_multi_film_screenings(self):
        self.assertEqual(nyc_screenslate.parse_films(self.details["201"]),
                         [{"title": "Sebastiane", "year": 1976, "director": "Derek Jarman, Paul Humfress", "format": "DCP"}])
        multi = nyc_screenslate.parse_films(self.details["203"])
        self.assertEqual([(f["title"], f["year"], f["director"]) for f in multi],
                         [("Sylph", 2021, "slimebubble, Almeda Morgan"), ("Wings of Desire", 1987, "Wim Wenders")])

    def test_rows_skip_covered_venues(self):
        showtimes = [{"nid": n, "field_timestamp": "2026-10-12T19:00:00", "field_note": "Q&amp;A" if n == "203" else ""}
                     for n in ("201", "202", "203", "204")]
        rows = nyc_screenslate.to_rows(showtimes, self.details, set(nyc_screenslate.COVERED))
        self.assertEqual([r["theater_name"] for r in rows], ["Paris Theater", "Museum of Modern Art", "Spectacle"])
        double = rows[2]
        self.assertEqual(double["film_title"], "Sylph / Wings of Desire")
        self.assertEqual(len(double["films"]), 2)
        self.assertEqual(double["special_event"], "Q&A")
        self.assertEqual(rows[0]["id"], "ss-201-20261012T1900")

    def test_venue_addresses_and_boroughs(self):
        info = nyc_screenslate.parse_venue_page("<p>Address 36-01 35th Avenue, Astoria, NY 11106</p>")
        self.assertEqual(info, {"address": "36-01 35th Avenue, Astoria, NY 11106", "borough": "Queens"})
        self.assertEqual(nyc_screenslate.borough_for_zip("11249"), "Brooklyn")
        self.assertEqual(nyc_screenslate.borough_for_zip("10019"), "Manhattan")
        self.assertIsNone(nyc_screenslate.parse_venue_page("<p>no address</p>")["borough"])

    def test_multi_film_record_matches_each_film(self):
        import nyc_repertory
        from watchlist import load_watchlist
        rec = {"id": "x", "title": "Sylph / Wings of Desire", "date": "2026-10-12", "time": "21:30",
               "theater": "Spectacle", "films": [{"title": "Sylph", "year": 2021, "director": "slimebubble"},
                                                 {"title": "Wings of Desire", "year": 1987, "director": "Wim Wenders"}]}
        [s] = nyc_repertory.screenings_from_records([rec])
        wl = load_watchlist(WATCHLIST)
        self.assertEqual([wl.match(f.title, f.year) is not None for f in s.films], [False, True])


class Matching(unittest.TestCase):
    def setUp(self):
        self.wl = load_watchlist(WATCHLIST)

    def test_decorated_titles_match(self):
        for title, year, want in [("Ghost in the Shell: 30th Anniversary Remaster", 1995, "Ghost in the Shell"),
                                  ("RoboCop - The Director's Cut", 1987, "RoboCop"),
                                  ("Wings of Desire (Open Captioning)", 1987, "Wings of Desire"),
                                  ("NAZA", None, "NAZA")]:
            hit = self.wl.match(title, year)
            self.assertIsNotNone(hit, title)
            self.assertEqual(hit.title, want)

    def test_same_title_different_film_does_not_match(self):
        self.assertIsNone(self.wl.match("The Wedding Banquet", 1993))   # watchlist has the 2025 remake

    def test_variants_strip_only_decorations(self):
        self.assertEqual(title_variants("Schindler's List"), [])
        self.assertIn("Lakeview Terrace", title_variants("Lakeview Terrace - 35MM + Q&A"))


class WatchlistSync(unittest.TestCase):
    PAGE = ("<div data-item-name=\"Molly&#039;s Game (2017)\" data-item-slug=\"mollys-game\" "
            "data-postered-identifier='{&quot;lid&quot;:&quot;dSl8&quot;}'></div>"
            "<div data-item-name=\"Untitled\" data-item-slug=\"untitled\"></div>")

    def test_parses_posters(self):
        rows = lb.parse_page(self.PAGE)
        self.assertEqual([(r["Name"], r["Year"], r["Letterboxd URI"]) for r in rows],
                         [("Molly's Game", "2017", "https://boxd.it/dSl8"),
                          ("Untitled", "", "https://letterboxd.com/film/untitled/")])

    def test_keeps_original_added_dates(self):
        rows = lb.parse_page(self.PAGE)
        merged = lb.merge_dates(rows, [{"Date": "2022-01-07", "Name": "Molly's Game", "Year": "2017",
                                        "Letterboxd URI": "https://boxd.it/dSl8"}], "2026-10-10")
        self.assertEqual({r["Name"]: r["Date"] for r in merged}, {"Molly's Game": "2022-01-07", "Untitled": "2026-10-10"})

    def test_refuses_short_or_failed_downloads(self):
        out = Path(tempfile.mkdtemp()) / "w.csv"
        lb.write_csv([{"Date": "2020-01-01", "Name": f"F{i}", "Year": "2000", "Letterboxd URI": f"u{i}"}
                      for i in range(100)], out)
        argv = ["x", "--user", "u", "--out", str(out)]
        with mock.patch("sys.argv", argv), mock.patch.object(lb, "fetch_watchlist", lambda u: lb.parse_page(self.PAGE)):
            self.assertEqual(lb.main(), 1)
        with mock.patch("sys.argv", argv), mock.patch.object(lb, "fetch_watchlist", mock.Mock(side_effect=OSError)):
            self.assertEqual(lb.main(), 1)
        self.assertEqual(len(lb._read_existing(out)), 100)


if __name__ == "__main__":
    unittest.main()
