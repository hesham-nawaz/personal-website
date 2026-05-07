"""End-to-end runner: Revival Hub source -> Letterboxd match -> HTML report
and (optionally) splice updated content into a site page.

Usage:
    python main.py <source_path> <output_html> [--site-page PATH] [--updated DATE]

Where <source_path> is either a .pdf (Revival Hub email printed to PDF) or a
.txt/.html file containing the email body text.

When run inside a GitHub Actions workflow against the personal-website repo,
the workflow itself handles git add/commit/push — this script only writes
files. (Earlier versions also did the publish step via a scratch-clone hack
needed to work around the Cowork sandbox; that's no longer needed.)
"""
from __future__ import annotations

import argparse
from pathlib import Path

from watchlist import load_watchlist
from revival_hub import parse_revival_hub_pdf, parse_revival_hub_text
from match import match_screenings
from render import write_report, write_site_page


DEFAULT_WATCHLIST = str(Path(__file__).resolve().parent.parent / "data" / "watchlist.csv")


def run(source: str, output: str, watchlist_path: str = DEFAULT_WATCHLIST,
        reference_year: int | None = None,
        site_page: str | None = None,
        updated_iso: str | None = None) -> dict:
    src = Path(source)
    if src.suffix.lower() == ".pdf":
        screenings = parse_revival_hub_pdf(str(src), reference_year=reference_year)
    else:
        text = src.read_text(encoding="utf-8")
        screenings = parse_revival_hub_text(text, reference_year=reference_year)

    wl = load_watchlist(watchlist_path)
    matches = match_screenings(screenings, wl)
    out_path = write_report(matches, output)

    site_path = None
    if site_page:
        site_path = write_site_page(matches, site_page, updated_iso=updated_iso)

    return {
        "screenings_parsed": len(screenings),
        "screenings_matched": len(matches),
        "watchlist_entries": len(wl.entries),
        "output_path": str(out_path),
        "site_page_path": str(site_path) if site_path else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Match Revival Hub weekly screenings against Letterboxd watchlist.")
    ap.add_argument("source", help="Path to Revival Hub email (PDF) or text file")
    ap.add_argument("output", help="Output HTML report path")
    ap.add_argument("--watchlist", default=DEFAULT_WATCHLIST, help="Letterboxd watchlist CSV")
    ap.add_argument("--year", type=int, default=None, help="Calendar year for the week (default: current year)")
    ap.add_argument("--site-page", default=None,
                    help="Path to a site page (e.g. screenings.html) to splice updated content into.")
    ap.add_argument("--updated", default=None,
                    help="Optional 'last updated' date to inject into the site page (e.g. 2026-05-04).")
    args = ap.parse_args()

    summary = run(args.source, args.output, watchlist_path=args.watchlist,
                  reference_year=args.year, site_page=args.site_page,
                  updated_iso=args.updated)
    print(
        f"Parsed {summary['screenings_parsed']} screenings, "
        f"matched {summary['screenings_matched']} against {summary['watchlist_entries']}-entry watchlist."
    )
    print(f"Report: {summary['output_path']}")
    if summary["site_page_path"]:
        print(f"Site page: {summary['site_page_path']}")


if __name__ == "__main__":
    main()
