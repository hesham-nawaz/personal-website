"""The page's script, in a real browser: dates, ranges, filters, views.

Needs Playwright (pip install playwright && python -m playwright install
chromium); skipped otherwise. The Tests workflow installs it.
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from tests.helpers import WATCHLIST, FakeFeed, make_feed, scratch_site

import main_nyc
import nyc_store

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

BUILT = date(2026, 10, 10)     # the day the page was built (a Saturday)

FIX_CLOCK = """
(() => { const D = Date; const fixed = new D('%sT14:00:00Z').getTime();
  window.Date = class extends D { constructor(...a) { super(...(a.length ? a : [fixed])); }
                                  static now() { return fixed; } }; })();
"""

STATE = """() => ({
  h1: document.querySelector('.title-card h1').textContent,
  q: location.search,
  summary: (document.querySelector('.screenings-summary:not([hidden])') ||
            document.querySelector('.screenings-empty')).innerText,
  days: [...document.querySelectorAll('.day-group:not([hidden])')].map(g => g.dataset.date),
  movieCards: document.querySelectorAll('[data-view=movie] .screening-card:not([hidden])').length,
  venues: [...new Set([...document.querySelectorAll(
      '.day-group:not([hidden]) .screening-card:not([hidden]) .screening-venue:not([hidden]) .venue-name')]
      .map(v => v.textContent.replace(' (map)', '').trim()))].sort(),
  stale: document.querySelector('.screenings-stale').hidden ? null : document.querySelector('.screenings-stale').innerText,
})"""


@unittest.skipIf(sync_playwright is None, "Playwright not installed")
class PageScript(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.site = scratch_site()
        store = Path(tempfile.mkdtemp()) / "store"
        with mock.patch.object(nyc_store, "fetch_day", FakeFeed(make_feed(BUILT))):
            main_nyc.run(str(cls.site / "screenings-nyc.html"), str(WATCHLIST), str(store), BUILT.isoformat(), BUILT)
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def open(self, query="", today=BUILT):
        ctx = self.browser.new_context(viewport={"width": 1100, "height": 900})
        self.addCleanup(ctx.close)
        page = ctx.new_page()
        page.route("http*://**", lambda r: r.abort())   # no network: fonts, icons
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        self.addCleanup(lambda: self.assertEqual(errors, []))
        page.add_init_script(FIX_CLOCK % today.isoformat())
        page.goto((self.site / "screenings-nyc.html").as_uri() + query)
        return page

    def state(self, page):
        return page.evaluate(STATE)

    def test_opens_on_this_week(self):
        s = self.state(self.open())
        self.assertEqual(s["h1"], "This Week in NYC")
        self.assertEqual(s["days"], ["2026-10-10", "2026-10-11"])
        self.assertIsNone(s["stale"])

    def test_day_then_range_then_reset(self):
        page = self.open()
        page.click('.strip-day[data-date="2026-10-14"]')
        s = self.state(page)
        self.assertEqual((s["days"], s["q"]), (["2026-10-14"], "?date=2026-10-14"))
        page.click('.strip-day[data-date="2026-10-20"]')
        s = self.state(page)
        self.assertEqual(s["days"][0], "2026-10-14")
        self.assertEqual(s["days"][-1], "2026-10-20")
        self.assertEqual(s["h1"], "Oct 14 – 20 in NYC")
        page.click("button[data-view=movie]")
        self.assertEqual(self.state(page)["movieCards"], 4)   # each film once across the range
        page.click(".strip-reset")
        self.assertEqual(self.state(page)["h1"], "This Week in NYC")

    def test_range_link(self):
        s = self.state(self.open("?from=2026-10-11&to=2026-10-25"))
        self.assertEqual(len(s["days"]), 15)
        self.assertTrue(s["summary"].endswith("Oct 11 – 25"))

    def test_weekends_only(self):
        page = self.open("?week=2026-10-12")
        page.click(".weekend-toggle")
        s = self.state(page)
        self.assertEqual(s["days"], ["2026-10-16", "2026-10-17", "2026-10-18"])
        self.assertEqual(s["h1"], "Weekend of Oct 16 in NYC")
        page.click('.strip-day[data-date="2026-10-14"]')
        self.assertIn("isn’t a weekend day", self.state(page)["summary"])

    def test_theater_filter(self):
        page = self.open("?week=2026-10-12")
        page.click(".venue-filter summary")
        page.click('.venue-shortcuts button[data-only="Brooklyn"]')
        s = self.state(page)
        self.assertEqual(s["venues"], ["BAM Rose Cinemas", "Nitehawk Cinema Williamsburg"])
        self.assertIn("skip=", s["q"])
        self.assertEqual(page.text_content(".venue-filter-label"), "2 of 5 theaters")
        self.assertEqual(page.text_content('.strip-day[data-date="2026-10-14"] .strip-count'), "2 films")
        # remembered on the next visit
        page.goto((self.site / "screenings-nyc.html").as_uri())
        self.assertEqual(self.state(page)["venues"], ["BAM Rose Cinemas", "Nitehawk Cinema Williamsburg"])

    def test_stale_notice(self):
        s = self.state(self.open(today=date(2026, 10, 14)))
        self.assertIn("4 days ago", s["stale"] or "")


if __name__ == "__main__":
    unittest.main()
