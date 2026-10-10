"""Refresh data/watchlist.csv from Hesham's public Letterboxd watchlist.

The weekly screening pages match against data/watchlist.csv. That file used
to be a one-off manual export, so films added to the watchlist afterwards
never matched. This script rebuilds it from the public watchlist pages
(https://letterboxd.com/<user>/watchlist/page/N/, 28 films per page) in the
same format as Letterboxd's own CSV export: Date, Name, Year, Letterboxd URI.

Safety: if the download fails, or returns far fewer films than the current
file holds, the existing CSV is left untouched and the script exits non-zero,
so the screenings run carries on with the last good copy.

Stdlib only.

Usage:
    python letterboxd_watchlist.py --user hnawaz --out ../data/watchlist.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import urllib.request
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

USER_AGENT = ("hesham-nawaz-personal-website/1.0 "
              "(weekly watchlist sync; +https://heshamnawaz.me)")
MAX_PAGES = 400          # 28 films/page -> room for ~11,000 films
MIN_KEEP_RATIO = 0.8     # refuse to shrink the list by more than 20% in one run
PAUSE_SECONDS = 0.5      # be gentle with Letterboxd


class _PosterParser(HTMLParser):
    """Collect one record per film poster on a watchlist page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.films: list[dict] = []
        self._seen: set[str] = set()

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        name = a.get("data-item-name")
        slug = a.get("data-item-slug")
        if not name or not slug or slug in self._seen:
            return
        self._seen.add(slug)
        lid = None
        ident = a.get("data-postered-identifier")
        if ident:
            try:
                lid = json.loads(ident).get("lid")
            except (ValueError, AttributeError):
                lid = None
        self.films.append({"name_with_year": name, "slug": slug, "lid": lid})


def _split_name_year(s: str) -> tuple[str, str]:
    """'Godzilla (1954)' -> ('Godzilla', '1954'); 'Untitled' -> ('Untitled', '')."""
    s = s.strip()
    if len(s) > 7 and s.endswith(")") and s[-6] == "(" and s[-5:-1].isdigit():
        return s[:-6].rstrip(), s[-5:-1]
    return s, ""


def parse_page(html_text: str) -> list[dict]:
    p = _PosterParser()
    p.feed(html_text)
    out = []
    for f in p.films:
        name, year = _split_name_year(f["name_with_year"])
        uri = (f"https://boxd.it/{f['lid']}" if f["lid"]
               else f"https://letterboxd.com/film/{f['slug']}/")
        out.append({"Name": name, "Year": year, "Letterboxd URI": uri,
                    "slug": f["slug"]})
    return out


def _get(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_watchlist(user: str) -> list[dict]:
    films: dict[str, dict] = {}
    for page in range(1, MAX_PAGES + 1):
        url = f"https://letterboxd.com/{user}/watchlist/page/{page}/"
        rows = parse_page(_get(url))
        if not rows:
            break
        for r in rows:
            films.setdefault(r["slug"], r)
        time.sleep(PAUSE_SECONDS)
    else:
        raise RuntimeError(f"stopped after {MAX_PAGES} pages; list may be truncated")
    return list(films.values())


def _read_existing(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def merge_dates(fresh: list[dict], existing: list[dict], today: str) -> list[dict]:
    """Keep each film's original 'Date' (when it was added) from the existing
    file; films new to the file get today's date as their first-seen date."""
    old_by_uri = {r.get("Letterboxd URI", ""): r for r in existing}
    old_by_title = {(r.get("Name", "").casefold(), r.get("Year", "")): r for r in existing}
    rows = []
    for f in fresh:
        old = (old_by_uri.get(f["Letterboxd URI"])
               or old_by_title.get((f["Name"].casefold(), f["Year"])))
        rows.append({
            "Date": (old or {}).get("Date") or today,
            "Name": f["Name"],
            "Year": f["Year"],
            "Letterboxd URI": f["Letterboxd URI"],
        })
    rows.sort(key=lambda r: (r["Date"], r["Name"].casefold()))
    return rows


def write_csv(rows: list[dict], path: Path) -> None:
    tmp = path.with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["Date", "Name", "Year", "Letterboxd URI"])
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--user", required=True, help="Letterboxd username")
    ap.add_argument("--out", required=True, help="Path to watchlist.csv to refresh")
    args = ap.parse_args()
    out = Path(args.out)

    existing = _read_existing(out)
    try:
        fresh = fetch_watchlist(args.user)
    except Exception as e:  # network errors, blocks, layout changes
        print(f"[letterboxd] could not download watchlist ({e}); "
              f"keeping existing {out} ({len(existing)} films)", file=sys.stderr)
        return 1

    if existing and len(fresh) < MIN_KEEP_RATIO * len(existing):
        print(f"[letterboxd] downloaded only {len(fresh)} films vs {len(existing)} "
              f"in {out}; refusing to overwrite (possible block or page change)",
              file=sys.stderr)
        return 1

    rows = merge_dates(fresh, existing, date.today().isoformat())
    write_csv(rows, out)
    old_uris = {r.get("Letterboxd URI") for r in existing}
    new_uris = {r["Letterboxd URI"] for r in rows}
    print(f"[letterboxd] watchlist: {len(rows)} films "
          f"({len(new_uris - old_uris)} added, {len(old_uris - new_uris)} removed since last sync)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
