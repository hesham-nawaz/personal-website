"""The daily job end to end: store refresh, matching and the built page."""
from __future__ import annotations

import json
import re
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from tests.helpers import WATCHLIST, FakeFeed, make_feed, scratch_site

import main_nyc
import nyc_store
import render

TODAY = date(2026, 10, 10)          # a Saturday


class DailyBuild(unittest.TestCase):
    def setUp(self):
        self.site = scratch_site()
        self.page = self.site / "screenings-nyc.html"
        self.store = Path(tempfile.mkdtemp()) / "store"
        self.feed = make_feed(TODAY)

    def run_day(self, today, feed=None):
        with mock.patch.object(nyc_store, "fetch_day", feed or FakeFeed(self.feed)):
            return main_nyc.run(str(self.page), str(WATCHLIST), str(self.store), today.isoformat(), today)

    def records(self):
        return {json.loads(l)["id"]: json.loads(l)
                for p in sorted(self.store.glob("*.jsonl")) for l in p.read_text().splitlines()}

    def test_store_is_sorted_one_line_per_screening(self):
        self.run_day(TODAY)
        for p in self.store.glob("*.jsonl"):
            recs = [json.loads(l) for l in p.read_text().splitlines()]
            self.assertEqual(recs, sorted(recs, key=lambda r: (r["date"], r["time"], r["theater"].lower(),
                                                               r["title"].lower(), r["id"])))
            self.assertTrue(all(set(r) == set(nyc_store.FIELDS) for r in recs))
        self.assertEqual(sorted(p.name for p in self.store.glob("*.jsonl")), ["2026-10.jsonl", "2026-11.jsonl"])

    def test_removals_moves_and_archive(self):
        self.run_day(TODAY)
        gone = self.feed[date(2026, 10, 14)].pop(0)
        moved = self.feed[date(2026, 10, 15)].pop(0)
        moved["date"] = "2026-10-16"
        self.feed[date(2026, 10, 16)].append(moved)
        self.run_day(date(2026, 10, 11))
        recs = self.records()
        self.assertEqual(recs[gone["id"]]["removed"], "2026-10-11")
        self.assertEqual(recs[moved["id"]]["date"], "2026-10-16")
        self.assertIsNone(recs[moved["id"]]["removed"])
        self.assertTrue(any(r["date"] == "2026-10-10" for r in recs.values()), "past days stay archived")
        self.assertNotIn('data-date="2026-10-10"', self.page.read_text())

    def test_feed_down_leaves_everything_alone(self):
        self.run_day(TODAY)
        before = (self.page.read_text(), {p.name: p.read_text() for p in self.store.glob("*.jsonl")})
        down = FakeFeed({}, fail_on=[date(2026, 10, 12) + __import__("datetime").timedelta(days=i) for i in range(10)])
        with self.assertRaises(RuntimeError):
            self.run_day(date(2026, 10, 12), feed=down)
        self.assertLessEqual(len(down.calls), nyc_store.STOP_AFTER_FAILED_DAYS)
        self.assertEqual(before, (self.page.read_text(), {p.name: p.read_text() for p in self.store.glob("*.jsonl")}))

    def test_one_failed_day_is_skipped(self):
        s = self.run_day(TODAY, feed=FakeFeed(self.feed, fail_on=[date(2026, 10, 13)]))
        self.assertEqual(s["days_failed"], 1)

    def test_page(self):
        self.run_day(TODAY)
        p = self.page.read_text()
        # this week by default, later weeks in the strip
        self.assertIn('<div class="day-group" data-date="2026-10-10">', p)
        self.assertIn('<div class="day-group" data-date="2026-10-12" hidden>', p)
        weeks = re.findall(r'class="strip-week-label" data-week="([^"]+)"[^>]*><span class="strip-week-name">([^<]+)<', p)
        self.assertEqual(weeks[0], ("2026-10-10", "This week"))
        self.assertEqual(weeks[1], ("2026-10-12", "Oct 12–18"))
        self.assertEqual(weeks[-1][0], "2026-11-16", "trailing empty weeks trimmed, far match kept")
        # one card per film per day; NAZA at two theaters is one card
        naza_cards = re.findall(r'<div class="day-group" data-date="2026-10-10">(.*?)</div>\n  <div class="day-group"', p, re.S)[0]
        self.assertEqual(naza_cards.count(">NAZA</a>"), 1)
        # links, tags and events
        self.assertIn('href="https://tickets.example.org/2026-10-10/0"', p)
        self.assertIn("google.com/maps/search/?api=1&amp;query=Nitehawk+Cinema+Williamsburg%2C+Brooklyn%2C+NY", p)
        self.assertNotIn(">DCP<", p)
        self.assertNotIn("General Admission", p)
        self.assertIn(">35mm<", p)
        self.assertIn("Q&amp;A with the editor", p)
        self.assertNotIn("Read More", p)
        self.assertIn('Listed as “Ghost in the Shell: 30th Anniversary Remaster”', p)
        self.assertNotIn(">The Wedding Banquet<", p)
        # controls
        self.assertIn('class="venue-filter"', p)
        self.assertIn('data-only="Brooklyn"', p)
        self.assertIn('class="weekend-toggle"', p)
        self.assertIn('data-updated="2026-10-10"', p)
        self.assertRegex(p, r'href="styles\.css\?v=[0-9a-f]{8}"')
        # the movie view lists each film once for the whole horizon
        movie = p.split('data-view="movie" hidden>', 1)[1]
        titles = re.findall(r'<div class="screening-title">(.*?)</div>', movie)
        self.assertEqual(len(titles), len(set(titles)))

    def test_la_page_still_renders(self):
        from match import MatchedScreening
        from revival_hub import Film, Screening
        from watchlist import WatchlistEntry
        la = [MatchedScreening(Screening(day=date(2026, 10, 12), weekday="Monday", times=["7:30p"],
                                         films=[Film("The Servant", 1963)], directors=["Joseph Losey"],
                                         theater="New Beverly", notes="35mm"),
                               [WatchlistEntry("The Servant", 1963, "https://boxd.it/x")])]
        frag = render.render_site_fragment(la, week_label="Oct 12 – 18, 2026", updated_iso="2026-10-12")
        self.assertNotIn('class="date-strip"', frag)
        self.assertNotIn("google.com/maps", frag)
        self.assertIn("showtime no-link", frag)


if __name__ == "__main__":
    unittest.main()
