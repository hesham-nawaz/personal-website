"""Parsers for theaters' own sites (nyc_sources.py) and the year fill-in.
The snippets follow markup captured from each site on Nov 27, 2026."""
from __future__ import annotations

import json
import re
import tempfile
import unittest
import urllib.error
from datetime import date
from pathlib import Path
from unittest import mock

from tests.helpers import WATCHLIST, FakeFeed, FakeScreenSlate, fake_momi, make_feed, scratch_site

import main_nyc
import nyc_momi
import nyc_screenslate
import nyc_sources as src
import nyc_store

TODAY = date(2026, 11, 27)

BAM_PAGE = """<html><body><h1>Club Kid</h1>
<p>Part of BAM Film 2026</p><p>Directed by Jordan Firstman (2026)</p>
<p>With Jordan Firstman, Cara Delevingne</p>
<h2 id="performanceHeader">UPCOMING Screenings</h2>
<ul id="performance-list">
<li><a href="https://commerce.bam.org/booking/production/59937"><span class="perfData">Fri, Nov 27 at 9:20PM</span></a></li>
<li><a href="https://commerce.bam.org/booking/production/59938"><span class="perfData">Sat, Nov 28 at 1:10PM</span></a> Open Captions</li>
<li><a href="https://commerce.bam.org/booking/production/60100"><span class="perfData">Sat, Jan 2 at 4:00PM</span></a></li>
</ul></body></html>"""

NITEHAWK_PAGE = """<html><head><script type="application/ld+json">{"@context":"https://schema.org","@graph":[
{"@type":"Movie","name":"Club Kid","url":"https://nitehawkcinema.com/williamsburg/movies/club-kid/",
 "dateCreated":"2026-01-01","director":[{"@type":"Person","name":"Jordan Firstman"}]},
{"@type":"ScreeningEvent","name":"Club Kid - 11/27/26 @ 9:40 pm","startDate":"2026-11-27T21:40:00-05:00",
 "eventStatus":"https://schema.org/EventScheduled",
 "offers":[{"@type":"Offer","url":"https://nitehawkcinema.com/williamsburg/purchase/25042639/"}]},
{"@type":"ScreeningEvent","name":"Club Kid - 11/28/26 @ 1:30 pm","startDate":"2026-11-28T13:30:00-05:00",
 "offers":[{"@type":"Offer","url":"https://nitehawkcinema.com/williamsburg/purchase/25042640/"}]},
{"@type":"MovieTheater","name":"Nitehawk Cinema - Williamsburg"}]}</script></head>
<body><p>Director: Jordan Firstman Run Time: 122 min. Format: DCP Rating: R Release Year: 2026</p></body></html>"""

ALAMO_SCHEDULE = {"data": {
    "presentations": [
        {"slug": "club-kid", "show": {"slug": "club-kid", "title": "Club Kid"}},
        {"slug": "the-mummy-1999", "show": {"slug": "the-mummy-1999", "title": "The Mummy"}},
    ],
    "sessions": [
        {"cinemaId": "2103", "sessionId": "1", "presentationSlug": "club-kid", "status": "ONSALE",
         "showTimeClt": "2026-11-28T19:15:00", "formatSlug": "2d-digital", "isHidden": False},
        {"cinemaId": "2101", "sessionId": "2", "presentationSlug": "club-kid", "status": "ONSALE",
         "showTimeClt": "2026-11-28T11:15:00", "formatSlug": "open-caption", "isHidden": False},
        {"cinemaId": "2101", "sessionId": "3", "presentationSlug": "the-mummy-1999", "status": "SOLDOUT",
         "showTimeClt": "2026-11-29T21:00:00", "formatSlug": "35mm", "isHidden": False},
        {"cinemaId": "2101", "sessionId": "4", "presentationSlug": "club-kid", "status": "PAST",
         "showTimeClt": "2026-11-27T10:00:00", "formatSlug": "2d-digital", "isHidden": False},
        {"cinemaId": "9999", "sessionId": "5", "presentationSlug": "club-kid", "status": "ONSALE",
         "showTimeClt": "2026-11-28T19:15:00", "formatSlug": "2d-digital", "isHidden": False},
    ]}}
ALAMO_FILMS = [{"slug": "club-kid", "isRepertory": False}, {"slug": "the-mummy-1999", "isRepertory": True}]

REGAL_PAYLOAD = {
    "shows": [{"TheatreCode": "1412", "Film": [
        {"Title": "Club Kid", "MasterMovieCode": "HO00023159", "Performances": [
            {"PerformanceId": 163616, "PerformanceAttributes": ["2D", "CC", "Recliner"],
             "CalendarShowTime": "2026-11-28T10:35:00"}]},
        {"Title": "Club Kid (Open Cap/Eng Sub)", "MasterMovieCode": "HO00023160", "Performances": [
            {"PerformanceId": 163617, "PerformanceAttributes": ["2D"], "CalendarShowTime": "2026-11-28T13:40:00"}]},
        {"Title": "Wicked: For Good", "MasterMovieCode": "HO1", "Performances": [
            {"PerformanceId": 1, "PerformanceAttributes": ["IMAX", "CC"], "CalendarShowTime": "2026-11-28T19:00:00"}]},
    ]}, {"TheatreCode": "0000", "Film": [{"Title": "Elsewhere", "Performances": [
        {"PerformanceId": 2, "CalendarShowTime": "2026-11-28T19:00:00"}]}]}],
    "movies": [
        {"MasterMovieCode": "HO00023159", "Title": "Club Kid", "OpeningDate": "2026-11-13T00:00:00",
         "Directors": ["Jordan Firstman"], "Studio": "A24"},
        {"MasterMovieCode": "HO00023160", "Title": "Club Kid (Open Cap/Eng Sub)",
         "OpeningDate": "2026-11-13T00:00:00", "Directors": ["Jordan Firstman"]},
        {"MasterMovieCode": "HO1", "Title": "Wicked: For Good", "OpeningDate": "2025-11-21T00:00:00",
         "Directors": ["Jon M. Chu"], "Studio": "Universal"},
    ],
    "datesWithShows": ["2026-11-27T00:00:00", "2026-11-28T00:00:00", "2026-11-30T00:00:00"],
}

# AMC embeds its data as an escaped React server payload.
AMC_PAGE = ('<script>self.__next_f.push([1,"' + json.dumps(
    '{"movies":[{"name":"Club Kid","slug":"club-kid-84493","movieId":84493},'
    '{"name":"The Mummy","slug":"the-mummy-80001","movieId":80001}]}'
    '["$","li","149178440",{"children":["$","$L13c",null,{"showtime":{"showtimeId":149178440,'
    '"policyCodes":[],"hasTrailers":true,"status":"Sellable","showDateTimeUtc":"2026-11-29T01:00:00.000Z",'
    '"display":{"time":"8:00","amPm":"pm"}},"selectTheatreOnClick":"$undefined",'
    '"aria-describedby":"club-kid-84493 club-kid-84493-amc-kips-bay-15 club-kid-84493-amc-kips-bay-15-digital '
    'club-kid-84493-amc-kips-bay-15-digital-0"}]}]'
    '["$","li","149178441",{"children":["$","$L13c",null,{"showtime":{"showtimeId":149178441,'
    '"policyCodes":[],"status":"AlmostFull","showDateTimeUtc":"2026-11-29T05:15:00.000Z",'
    '"display":{"time":"12:15","amPm":"am"}},"aria-describedby":"the-mummy-80001 the-mummy-80001-amc-kips-bay-15 '
    'the-mummy-80001-amc-kips-bay-15-imaxwithlaseratamc the-mummy-80001-amc-kips-bay-15-imaxwithlaseratamc-0"}]}]'
    '["$","li","149178440",{"children":["$","$L13c",null,{"showtime":{"showtimeId":149178440,'
    '"status":"Sellable","showDateTimeUtc":"2026-11-29T01:00:00.000Z"},"aria-describedby":"club-kid-84493"}]}]'
)[1:-1] + '"])</script>')


class Parsers(unittest.TestCase):
    def test_bam(self):
        rows = src.parse_bam_film(BAM_PAGE, TODAY, "/film/2026/club-kid")
        self.assertEqual([(r["date"], r["time"]) for r in rows],
                         [("2026-11-27", "21:20"), ("2026-11-28", "13:10"), ("2027-01-02", "16:00")])
        r = rows[1]
        self.assertEqual((r["film_title"], r["film_year"], r["film_director"], r["theater_name"]),
                         ("Club Kid", 2026, "Jordan Firstman", "BAM Rose Cinemas"))
        self.assertEqual(r["ticket_url"], "https://commerce.bam.org/booking/production/59938")
        self.assertEqual(r["special_event"], "Open Captions")
        self.assertEqual(r["id"], "bam-59938")
        # BAM puts the director in some titles; repertory.nyc doesn't
        page = BAM_PAGE.replace("<h1>Club Kid</h1>", "<h1>Jordan Firstman’s Club Kid</h1>")
        self.assertEqual(src.parse_bam_film(page, TODAY)[0]["film_title"], "Club Kid")
        # series pages have no performance list
        self.assertEqual(src.parse_bam_film("<h1>NewFest</h1><p>Directed by</p>", TODAY), [])

    def test_bam_fetch_follows_film_links(self):
        index = ('<a href="/film/2026/club-kid">Club Kid</a><a href="/film/2026/club-kid#performanceHeader">x</a>'
                 '<a href="/film/2026/newfest">NewFest</a><a href="/#Calendar">c</a>')
        pages = {"https://www.bam.org/film": index, "https://www.bam.org/film/2026/club-kid": BAM_PAGE,
                 "https://www.bam.org/film/2026/newfest": "<h1>NewFest</h1>"}
        got = src.fetch_bam(TODAY, date(2026, 12, 31), lambda u: pages[u].encode())
        self.assertEqual(len(got), 35)                      # every day in the window counts as checked
        self.assertEqual([r["time"] for r in got["2026-11-28"]], ["13:10"])
        self.assertNotIn("2027-01-02", got)                 # beyond the window

    def test_nitehawk(self):
        rows = src.parse_nitehawk_film(NITEHAWK_PAGE, "williamsburg")
        self.assertEqual([(r["date"], r["time"], r["id"]) for r in rows],
                         [("2026-11-27", "21:40", "nh-williamsburg-25042639"),
                          ("2026-11-28", "13:30", "nh-williamsburg-25042640")])
        r = rows[0]
        self.assertEqual((r["film_title"], r["film_year"], r["film_director"], r["format"], r["theater_name"]),
                         ("Club Kid", 2026, "Jordan Firstman", "DCP", "Nitehawk Cinema Williamsburg"))
        self.assertEqual(r["ticket_url"], "https://nitehawkcinema.com/williamsburg/purchase/25042639/")

    def test_nitehawk_fetch_collects_films_from_listing_pages(self):
        home = '<a href="https://nitehawkcinema.com/{loc}/movies/club-kid/?date=today">Club Kid</a>'

        def get(url):
            loc = "williamsburg" if "/williamsburg/" in url else "prospectpark"
            if "/movies/" in url:
                return NITEHAWK_PAGE.replace("williamsburg", loc).encode()
            if url.endswith(f"/{loc}/") or "coming-soon" in url:
                return home.format(loc=loc).encode()
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

        got = src.fetch_nitehawk(TODAY, date(2026, 12, 31), get)
        self.assertEqual(sorted(r["theater_name"] for r in got["2026-11-27"]),
                         ["Nitehawk Cinema Prospect Park", "Nitehawk Cinema Williamsburg"])

    def test_alamo(self):
        rows = src.parse_alamo(ALAMO_SCHEDULE, ALAMO_FILMS)
        self.assertEqual([(r["theater_name"], r["date"], r["time"], r["format"], r["special_event"]) for r in rows],
                         [("Alamo Drafthouse Lower Manhattan", "2026-11-28", "19:15", None, None),
                          ("Alamo Drafthouse Downtown Brooklyn", "2026-11-28", "11:15", None, "Open captions"),
                          ("Alamo Drafthouse Downtown Brooklyn", "2026-11-29", "21:00", "35mm", None)])
        self.assertEqual([r["_first_run"] for r in rows], [True, True, False])
        self.assertEqual(rows[0]["ticket_url"], "https://drafthouse.com/nyc/show/club-kid")
        self.assertIsNone(src.parse_alamo(ALAMO_SCHEDULE)[0]["_first_run"])     # film list unavailable

    def test_regal(self):
        rows = src.parse_regal(REGAL_PAYLOAD)
        self.assertEqual([(r["film_title"], r["time"], r["format"], r["special_event"], r["film_year"]) for r in rows],
                         [("Club Kid", "10:35", None, None, 2026),
                          ("Club Kid", "13:40", None, "Open captions", 2026),
                          ("Wicked: For Good", "19:00", "IMAX", None, 2025)])
        r = rows[0]
        self.assertEqual((r["theater_name"], r["film_director"], r["id"]),
                         ("Regal Essex Crossing", "Jordan Firstman", "regal-1412-163616"))
        self.assertEqual(r["ticket_url"], "https://www.regmovies.com/theatres/regal-essex-crossing-1412?date=11-28-2026")

    def test_regal_fetch_checks_listed_and_empty_days(self):
        calls = []

        def get(url):
            calls.append(url)
            if "date=11-30-2026" in url:
                raise OSError("timeout")
            m, d, y = re.search(r"date=(\d\d)-(\d\d)-(\d{4})", url).groups()
            return json.dumps(REGAL_PAYLOAD).replace('Time": "2026-11-28T', f'Time": "{y}-{m}-{d}T').encode()

        got = src.fetch_regal(TODAY, date(2026, 12, 2), get)
        self.assertEqual(sorted(got), ["2026-11-27", "2026-11-28", "2026-11-29", "2026-12-01", "2026-12-02"])
        self.assertEqual(len(got["2026-11-28"]), 3)
        self.assertEqual(len(got["2026-11-27"]), 3)
        self.assertEqual(len(calls), 3)     # today, Nov 28, Nov 30 (failed, so not checked)

    def test_amc(self):
        rows = src.parse_amc(AMC_PAGE, "amc-kips-bay-15")
        self.assertEqual([(r["id"], r["film_title"], r["date"], r["time"], r["format"]) for r in rows],
                         [("amc-149178440", "Club Kid", "2026-11-28", "20:00", None),
                          ("amc-149178441", "The Mummy", "2026-11-29", "00:15", "IMAX")])
        self.assertEqual(rows[0]["theater_name"], "AMC Kips Bay 15")
        self.assertEqual(rows[0]["ticket_url"], "https://www.amctheatres.com/showtimes/149178440")

    def test_amc_fetch_stops_theatres_with_nothing_further(self):
        calls = []

        def get(url):
            calls.append(url)
            return (AMC_PAGE if "2026-11-28" in url else "<html></html>").encode()

        with mock.patch.object(src, "AMC_THEATRES", {"amc-kips-bay-15": "AMC Kips Bay 15"}):
            got = src.fetch_amc(TODAY, date(2026, 12, 31), get)
        self.assertEqual([r["film_title"] for r in got["2026-11-28"]], ["Club Kid"])
        self.assertEqual(len(calls), 4)       # Nov 27 (empty), 28, 29 and 30 (empty twice: stop)

    def test_titles_and_formats(self):
        self.assertEqual(src.clean_title("Club Kid (Open Cap/Eng Sub)"), ("Club Kid", "Open captions"))
        self.assertEqual(src.clean_title("Wicked: For Good"), ("Wicked: For Good", None))
        self.assertEqual(src.chain_format("imaxwithlaseratamc"), ("IMAX", None))
        self.assertEqual(src.chain_format("dolbycinemaatamcprime"), ("Dolby Cinema", None))
        self.assertEqual(src.chain_format("reald3d"), ("3D", None))
        self.assertEqual(src.chain_format("opencaption"), (None, "Open captions"))
        self.assertEqual(src.chain_format("2d-digital"), (None, None))


class YearInference(unittest.TestCase):
    def test_years_come_from_current_listings_only(self):
        amc = {"2026-11-28": [
            {"film_title": "Club Kid", "film_year": None, "_first_run": None},
            {"film_title": "The Mummy", "film_year": None, "_first_run": None},
            {"film_title": "Wings of Desire", "film_year": None, "_first_run": None},
            {"film_title": "Club Kid", "film_year": None, "_first_run": False},
        ]}
        regal = {"2026-11-28": [{"film_title": "The Mummy", "film_year": 2026}]}
        known = [{"title": "Club Kid", "year": 2026}, {"title": "Wings of Desire", "year": 1987},
                 {"title": "The Mummy", "year": 1999}]
        filled = nyc_store.infer_years({"amc": amc, "regal": regal, "alamo": None}, known, TODAY)
        self.assertEqual([r["film_year"] for r in amc["2026-11-28"]], [2026, 2026, None, None])
        self.assertEqual(filled, 2)


class TheaterSites(unittest.TestCase):
    """End to end: a theater's own site adds screenings repertory.nyc lacks,
    duplicates of repertory.nyc's collapse, and an outage keeps what's stored."""

    def setUp(self):
        patcher = mock.patch.dict("os.environ", {"TMDB_TOKEN": ""})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.site = scratch_site()
        self.page = self.site / "screenings-nyc.html"
        self.store = Path(tempfile.mkdtemp()) / "store"
        self.today = date(2026, 10, 10)
        self.feed = make_feed(self.today)

    def fake_chain(self, fail=False):
        def fetch(today, end):
            if fail:
                raise OSError("simulated outage")
            d = today.isoformat()
            return {d: [
                # Naza is on the test watchlist; AMC gives no year
                {"id": "amc-1", "film_title": "Naza", "film_year": None, "film_director": None, "films": None,
                 "theater_name": "AMC Kips Bay 15", "theater_slug": "amc-kips-bay-15", "date": d, "time": "19:00",
                 "format": "imaxwithlaser", "ticket_url": "https://www.amctheatres.com/showtimes/1",
                 "special_event": None, "_first_run": None},
            ]}
        return fetch

    def run_day(self, today, chain):
        with mock.patch.object(nyc_store, "fetch_day", FakeFeed(self.feed)), \
             mock.patch.object(nyc_momi, "fetch_screenings", fake_momi()), \
             mock.patch.object(nyc_screenslate, "ScreenSlate", lambda skip: FakeScreenSlate()), \
             mock.patch.object(src, "SOURCES", [("amc", chain)]):
            return main_nyc.run(str(self.page), str(WATCHLIST), str(self.store), today.isoformat(), today)

    def test_chain_screenings_reach_the_page(self):
        s = self.run_day(self.today, self.fake_chain())
        self.assertEqual(s["sources"], {"amc": 1})
        self.assertEqual(s["years_filled"], 1)
        recs = [json.loads(l) for p in self.store.glob("*.jsonl") for l in p.read_text().splitlines()]
        amc = [r for r in recs if r["source"] == "amc"]
        self.assertEqual((amc[0]["year"], amc[0]["theater"]), (2026, "AMC Kips Bay 15"))
        self.assertNotIn("_first_run", amc[0])
        page = self.page.read_text()
        self.assertIn("AMC Kips Bay 15", page)
        self.assertIn("https://www.amctheatres.com/showtimes/1", page)
        self.assertEqual(main_nyc.venue_area("AMC Kips Bay 15"), "Manhattan")
        self.assertEqual(main_nyc.venue_area("Regal UA Kaufman Astoria"), "Queens")
        # the site is down the next day: its screening stays listed
        s2 = self.run_day(date(2026, 10, 10), self.fake_chain(fail=True))
        self.assertEqual(s2["sources"], {"amc": "unavailable"})
        self.assertIn("https://www.amctheatres.com/showtimes/1", self.page.read_text())


if __name__ == "__main__":
    unittest.main()
