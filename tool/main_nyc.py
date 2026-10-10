"""End-to-end runner for the NYC screenings page.

Each (daily) run:
  1. refreshes the screenings store, data/nyc-screenings/, from
     https://repertory.nyc/api/screenings for today through the feed's horizon
     (see nyc_store.py);
  2. matches every screening still listed from today on against Hesham's
     Letterboxd watchlist;
  3. renders "This Week in NYC" (today through Sunday) plus each later week
     that has listings, and splices it into the site page (screenings-nyc.html).

Usage:
    python main_nyc.py --site-page ../screenings-nyc.html --updated 2026-10-10
    python main_nyc.py --site-page ../screenings-nyc.html --no-fetch      # rebuild from the store
    python main_nyc.py --site-page ../screenings-nyc.html --reference-date 2026-10-12
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from match import match_screenings
from nyc_repertory import screenings_from_records
from nyc_store import listed_records, refresh
from render import SiteWeek, write_report, write_site_page
from watchlist import load_watchlist

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WATCHLIST = str(ROOT / "data" / "watchlist.csv")
DEFAULT_STORE = str(ROOT / "data" / "nyc-screenings")

# Google Maps searches by venue name find the venue's own listing. The borough
# keeps a search from landing on a same-named place elsewhere.
_BROOKLYN = {"bam rose cinemas", "nitehawk cinema williamsburg", "nitehawk cinema prospect park"}


def map_query(venue: str) -> str:
    borough = "Brooklyn, NY" if venue.strip().lower() in _BROOKLYN else "New York, NY"
    return f"{venue}, {borough}"


def build_weeks(matches, listed_days: list[date], today: date) -> list[SiteWeek]:
    """This week (today through Sunday) and each following Monday-Sunday week
    through the last week with a watchlist match (always at least this week
    and next, so the picker has somewhere to go). Empty weeks in between stay,
    labelled as having no matches yet."""
    sunday = today + timedelta(days=6 - today.weekday())
    last = max(listed_days) if listed_days else sunday
    weeks = []
    start, end, is_current = today, sunday, True
    while start <= max(last, sunday):
        weeks.append(SiteWeek(
            start=start, end=end, is_current=is_current,
            matches=[m for m in matches if start <= m.screening.day <= end]))
        start, end, is_current = end + timedelta(days=1), end + timedelta(days=7), False
    while len(weeks) > 2 and not weeks[-1].matches:
        weeks.pop()
    return weeks


def run(site_page: str, watchlist_path: str = DEFAULT_WATCHLIST, store: str = DEFAULT_STORE,
        updated_iso: str | None = None, today: date | None = None,
        fetch: bool = True, report_path: str | None = None) -> dict:
    today = today or date.today()
    store_dir = Path(store)
    summary = refresh(store_dir, today=today) if fetch else {}

    records = listed_records(store_dir, today)
    screenings = screenings_from_records(records)
    wl = load_watchlist(watchlist_path)
    matches = match_screenings(screenings, wl)
    weeks = build_weeks(matches, sorted({s.day for s in screenings}), today)

    write_site_page(matches, site_page, updated_iso=updated_iso,
                    weeks=weeks, map_query=map_query)
    if report_path:
        write_report(weeks[0].matches, report_path)
    return {
        **summary,
        "screenings_listed": sum(len(s.times) for s in screenings),
        "matched_this_week": sum(len(m.screening.times) for m in weeks[0].matches),
        "matched_total": sum(len(m.screening.times) for m in matches),
        "weeks": len(weeks),
        "watchlist_entries": len(wl.entries),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the NYC screenings page from repertory.nyc and a Letterboxd watchlist.")
    ap.add_argument("--site-page", required=True, help="Page to splice into (screenings-nyc.html)")
    ap.add_argument("--watchlist", default=DEFAULT_WATCHLIST, help="Letterboxd watchlist CSV")
    ap.add_argument("--store", default=DEFAULT_STORE, help="Directory of stored screenings")
    ap.add_argument("--updated", default=None, help="'Last updated' date shown on the page")
    ap.add_argument("--reference-date", default=None, help="Treat this date (YYYY-MM-DD) as today")
    ap.add_argument("--no-fetch", action="store_true", help="Rebuild from the store without fetching")
    ap.add_argument("--report", default=None, help="Optional standalone HTML report of this week")
    args = ap.parse_args()

    today = datetime.strptime(args.reference_date, "%Y-%m-%d").date() if args.reference_date else None
    try:
        s = run(args.site_page, args.watchlist, args.store, args.updated, today,
                fetch=not args.no_fetch, report_path=args.report)
    except RuntimeError as e:
        print(f"[main_nyc] {e}; page left unchanged", file=sys.stderr)
        return 1
    if "days_checked" in s:
        print(f"Store: checked {s['days_checked']} days through {s['through']} "
              f"({s['days_failed']} failed); {s['added']} new, {s['updated']} changed, "
              f"{s['removed']} removed, {s['restored']} back.")
    print(f"Listed from today: {s['screenings_listed']} showtimes; matched {s['matched_total']} "
          f"({s['matched_this_week']} this week) against {s['watchlist_entries']}-entry watchlist; "
          f"{s['weeks']} weeks on the page.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
