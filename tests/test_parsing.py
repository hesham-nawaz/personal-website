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
from tests.helpers import MOMI_EVENTS, SS_LISTINGS
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


class SameScreeningTwice(unittest.TestCase):
    def rec(self, **kw):
        base = {"id": "x", "source": "repertory.nyc", "date": "2026-12-05", "time": "18:00",
                "title": "Full Metal Jacket", "year": None, "director": None, "films": None,
                "theater": "Museum of the Moving Image", "theater_slug": "momi", "format": None,
                "event": None, "ticket_url": None, "first_seen": "2026-10-20", "removed": None}
        return {**base, **kw}

    def test_venue_source_wins_and_borrows_missing_details(self):
        import nyc_store
        recs = [self.rec(id="sparse", first_seen="2026-10-23"),
                self.rec(id="full", year=1987, director="Stanley Kubrick", ticket_url="https://tix/fmj"),
                self.rec(id="momi-63613", source="momi", year=1987, format="35mm",
                         ticket_url="https://movingimage.org/event/full-metal-jacket-2026/"),
                self.rec(id="other-time", time="21:00")]
        out = {r["id"]: r for r in nyc_store.dedupe_records(recs)}
        self.assertEqual(sorted(out), ["momi-63613", "other-time"])
        fmj = out["momi-63613"]
        self.assertEqual((fmj["year"], fmj["director"], fmj["format"]), (1987, "Stanley Kubrick", "35mm"))
        self.assertEqual(fmj["ticket_url"], "https://movingimage.org/event/full-metal-jacket-2026/")
        [s] = [s for s in nr.screenings_from_records(list(out.values())) if s.times == ["6:00p"]]
        self.assertEqual(s.ticket_urls, ["https://movingimage.org/event/full-metal-jacket-2026/"])

    def test_titles_compare_normalized_and_newer_listing_wins(self):
        import nyc_store
        recs = [self.rec(id="old", title="Je t'aime, je t'aime", theater="Metrograph", year=1968,
                         director="François Truffaut", first_seen="2026-10-18"),
                self.rec(id="new", title="Je t’aime, je t’aime", theater="Metrograph", year=1968,
                         director="Alain Resnais", first_seen="2026-10-20")]
        [r] = nyc_store.dedupe_records(recs)
        self.assertEqual(r["director"], "Alain Resnais")


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
    def rows(self):
        return nyc_screenslate.parse_listings(SS_LISTINGS.read_text(), date(2026, 10, 12),
                                              set(nyc_screenslate.COVERED))

    def test_rows_skip_covered_venues_and_exhibitions(self):
        rows = self.rows()
        self.assertEqual([r["theater_name"] for r in rows], ["Museum of Modern Art", "Paris Theater", "Spectacle"])
        moma, paris, double = rows
        self.assertEqual((paris["film_title"], paris["film_year"], paris["film_director"], paris["format"]),
                         ("Sebastiane", 1976, "Derek Jarman, Paul Humfress", "DCP"))
        self.assertEqual((paris["date"], paris["time"], paris["theater_slug"]), ("2026-10-12", "19:00", "paris-theater"))
        self.assertEqual(paris["ticket_url"], "https://www.paristheaternyc.com/film/sebastiane-paris")
        self.assertEqual((moma["time"], moma["special_event"], moma["format"]), ("16:00", "Introduced by Dave Kehr", "35mm"))
        self.assertEqual(double["film_title"], "Sylph / Wings of Desire")
        self.assertEqual([(f["title"], f["year"], f["director"]) for f in double["films"]],
                         [("Sylph", 2021, "slimebubble, Almeda Morgan"), ("Wings of Desire", 1987, "Wim Wenders")])
        self.assertEqual((double["time"], double["special_event"]), ("21:30", "Q&A"))
        self.assertEqual(len({r["id"] for r in rows}), 3)

    def test_program_with_display_title_and_times(self):
        page = ('<div class="venue"><h3><a href="/venues/anthology-film-archives">Anthology Film Archives</a></h3>'
                '<div class="listing"><div class="listing-content"><div class="series"><a>Faces</a></div>'
                '<a class="display-title" href="https://anthology.example/62314">Self-Portraits</a>'
                '<div class="media-titles"><div class="media-title"><div class="media-title-label">'
                '<span class="field--name-title">Cinématon</span></div><div class="media-title-info">'
                '<span> Gérard Courant</span><span>1987</span><span>4M</span><span>Digital Video</span></div></div>'
                '<div class="media-title"><div class="media-title-label"><span class="field--name-title">Self-Portrait</span>'
                '</div><div class="media-title-info"><span> Shigeko Kubota</span><span>1970-71</span><span>5M</span></div></div></div>'
                '<div class="showtimes"><span>12:15am</span> <span>6:30pm**</span></div>'
                '<div class="screening-notes"><div>** Filmmaker in person!</div></div></div></div></div>')
        rows = nyc_screenslate.parse_listings(page, date(2026, 11, 29), set())
        self.assertEqual([(r["film_title"], r["time"], r["special_event"]) for r in rows],
                         [("Self-Portraits", "00:15", None), ("Self-Portraits", "18:30", "Filmmaker in person!")])
        self.assertEqual([f["year"] for f in rows[0]["films"]], [1987, 1970])
        self.assertEqual(rows[0]["films"][0]["format"], "Digital Video")
        self.assertEqual(rows[0]["ticket_url"], "https://anthology.example/62314")
        self.assertEqual(nyc_screenslate.to_24h("12:00pm"), "12:00")
        self.assertIsNone(nyc_screenslate.to_24h("TBA"))

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
