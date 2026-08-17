"""End-to-end runner for the NYC pipeline.

Fetches this week's repertory screenings from https://repertory.nyc/api/screenings,
matches against Hesham's Letterboxd watchlist, splices results into a site
page (screenings-nyc.html), and optionally writes a standalone HTML report.

Much simpler than main.py (LA) — no email, no file inputs, no IMAP.
Just:  HTTP → JSON → match → HTML → splice.

Usage:
    python main_nyc.py --site-page screenings-nyc.html --updated 2026-05-04
    python main_nyc.py --site-page ../screenings-nyc.html --report ../reports/nyc-2026-05-04.html
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from watchlist import load_watchlist
from nyc_repertory import fetch_screenings_for_week, _week_bounds
from match import match_screenings
from render import write_report, write_site_page


DEFAULT_WATCHLIST = str(Path(__file__).resolve().parent.parent / "data" / "watchlist.csv")


def run(watchlist_path: str = DEFAULT_WATCHLIST,
        site_page: str | None = None,
        report_path: str | None = None,
        updated_iso: str | None = None,
        reference_date: date | None = None) -> dict:
    screenings = fetch_screenings_for_week(reference=reference_date)
    monday, sunday = _week_bounds(reference_date)

    wl = load_watchlist(watchlist_path)
    matches = match_screenings(screenings, wl)

    # Week label like "May 4 – May 10, 2026" (matches LA report style).
    week_label = f"{monday.strftime('%b %-d')} – {sunday.strftime('%b %-d, %Y')}"

    report_out = None
    if report_path:
        report_out = write_report(matches, report_path, week_label=week_label)

    site_out = None
    if site_page:
        site_out = write_site_page(matches, site_page,
                                   week_label=week_label,
                                   updated_iso=updated_iso)

    return {
        "week_monday": monday.isoformat(),
        "week_sunday": sunday.isoformat(),
        "week_label": week_label,
        "screenings_fetched": len(screenings),
        "screenings_matched": len(matches),
        "watchlist_entries": len(wl.entries),
        "report_path": str(report_out) if report_out else None,
        "site_page_path": str(site_out) if site_out else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Match repertory.nyc screenings against Letterboxd watchlist.")
    ap.add_argument("--watchlist", default=DEFAULT_WATCHLIST, help="Letterboxd watchlist CSV")
    ap.add_argument("--site-page", default=None,
                    help="Path to a site page (e.g. screenings-nyc.html) to splice updated content into.")
    ap.add_argument("--report", default=None,
                    help="Optional path to write a standalone HTML report to.")
    ap.add_argument("--updated", default=None,
                    help="Optional 'last updated' date for the site page (e.g. 2026-05-04).")
    ap.add_argument("--reference-date", default=None,
                    help="Override the reference date (YYYY-MM-DD). Defaults to today.")
    args = ap.parse_args()

    ref = None
    if args.reference_date:
        from datetime import datetime as _dt
        ref = _dt.strptime(args.reference_date, "%Y-%m-%d").date()

    summary = run(
        watchlist_path=args.watchlist,
        site_page=args.site_page,
        report_path=args.report,
        updated_iso=args.updated,
        reference_date=ref,
    )
    print(
        f"Week {summary['week_label']}: "
        f"fetched {summary['screenings_fetched']} screenings, "
        f"matched {summary['screenings_matched']} against "
        f"{summary['watchlist_entries']}-entry watchlist."
    )
    if summary["report_path"]:
        print(f"Report: {summary['report_path']}")
    if summary["site_page_path"]:
        print(f"Site page: {summary['site_page_path']}")


if __name__ == "__main__":
    main()
