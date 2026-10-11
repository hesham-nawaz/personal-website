"""TMDB posters: lookups, caching within TMDB's 6-month limit, and the page."""
from __future__ import annotations

import json
import tempfile
import unittest
import urllib.parse
from datetime import date, timedelta
from pathlib import Path

from tests.helpers import WATCHLIST  # noqa: F401  (sets sys.path)

import render
import tmdb_posters
from match import MatchedScreening
from revival_hub import Film, Screening
from watchlist import WatchlistEntry

CONFIG = {"images": {"secure_base_url": "https://image.tmdb.org/t/p/", "poster_sizes": ["w92", "w154", "original"]}}
RESULTS = {
    "Wings of Desire": [{"id": 1, "title": "Wings of Desire", "original_title": "Der Himmel über Berlin",
                         "release_date": "1987-10-29", "poster_path": "/wings.jpg"}],
    "The Wedding Banquet": [{"id": 2, "title": "The Wedding Banquet", "release_date": "2025-04-18", "poster_path": "/new.jpg"},
                            {"id": 3, "title": "The Wedding Banquet", "release_date": "1993-08-04", "poster_path": "/old.jpg"}],
    "Obscure Short": [],
}


class FakeTMDB(tmdb_posters.TMDB):
    def __init__(self):
        super().__init__("token", opener=self.open)
        self.searches = []

    def open(self, url):
        if url.endswith("/configuration"):
            return CONFIG
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.searches.append((q["query"][0], q.get("year", [None])[0]))
        return {"results": RESULTS.get(q["query"][0], [])}


def matched(title, year, url, day=date(2026, 10, 12)):
    return MatchedScreening(Screening(day=day, weekday="Monday", times=["7:00p"], films=[Film(title, year)],
                                      directors=[], theater="Film Forum"),
                            [WatchlistEntry(title, year, url)])


class Posters(unittest.TestCase):
    def setUp(self):
        self.cache = Path(tempfile.mkdtemp()) / "tmdb-posters.json"
        self.films = [matched("Wings of Desire", 1987, "u/wings"), matched("The Wedding Banquet", 1993, "u/banquet"),
                      matched("Obscure Short", 2001, "u/short")]

    def test_skipped_without_a_token(self):
        out = tmdb_posters.attach_posters(self.films, self.cache, token="")
        self.assertIn("skipped", out["posters"])
        self.assertFalse(any(hasattr(m, "poster_url") for m in self.films))
        self.assertFalse(self.cache.exists())

    def test_picks_the_right_film_and_builds_urls(self):
        tmdb = FakeTMDB()
        tmdb_posters.attach_posters(self.films, self.cache, date(2026, 10, 11), tmdb=tmdb)
        self.assertEqual(self.films[0].poster_url, "https://image.tmdb.org/t/p/w154/wings.jpg")
        self.assertEqual(self.films[1].poster_url, "https://image.tmdb.org/t/p/w154/old.jpg")   # 1993, not the remake
        self.assertFalse(hasattr(self.films[2], "poster_url"))

    def test_lookups_are_reused_then_refreshed_within_six_months(self):
        tmdb = FakeTMDB()
        tmdb_posters.attach_posters(self.films, self.cache, date(2026, 10, 11), tmdb=tmdb)
        first = len(tmdb.searches)
        tmdb_posters.attach_posters(self.films, self.cache, date(2026, 12, 1), tmdb=tmdb)
        self.assertEqual(len(tmdb.searches), first, "cached lookups reused")
        later = date(2026, 10, 11) + timedelta(days=tmdb_posters.REFRESH_DAYS + 1)
        tmdb_posters.attach_posters(self.films, self.cache, later, tmdb=tmdb)
        self.assertGreater(len(tmdb.searches), first, "stale lookups redone")
        self.assertLess(tmdb_posters.REFRESH_DAYS, 183)
        self.assertTrue(all(v["checked"] == later.isoformat() for v in json.loads(self.cache.read_text()).values()))

    def test_unused_old_lookups_are_dropped(self):
        tmdb = FakeTMDB()
        tmdb_posters.attach_posters(self.films, self.cache, date(2026, 10, 11), tmdb=tmdb)
        later = date(2026, 10, 11) + timedelta(days=tmdb_posters.REFRESH_DAYS + 1)
        tmdb_posters.attach_posters(self.films[:1], self.cache, later, tmdb=tmdb)
        self.assertEqual(sorted(json.loads(self.cache.read_text())), ["u/wings"])

    def test_page_shows_posters_and_tmdb_credit(self):
        tmdb_posters.attach_posters(self.films, self.cache, date(2026, 10, 11), tmdb=FakeTMDB())
        frag = render.render_site_fragment(self.films)
        self.assertIn('class="screening-card has-poster"', frag)
        self.assertIn('<img class="card-poster" src="https://image.tmdb.org/t/p/w154/wings.jpg"', frag)
        self.assertIn("not endorsed, certified, or otherwise approved by TMDB", frag)
        self.assertIn('src="images/tmdb-logo.svg"', frag)
        plain = render.render_site_fragment([matched("Obscure Short", 2001, "u/short")])
        self.assertNotIn("tmdb-credit", plain)


if __name__ == "__main__":
    unittest.main()
