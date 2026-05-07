"""Parse the Letterboxd watchlist CSV and build a lookup index for matching."""
from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WatchlistEntry:
    title: str
    year: int | None
    letterboxd_url: str


def normalize_title(title: str) -> str:
    """Normalize a movie title for matching.

    - lowercase
    - strip accents (NFKD + drop combining marks)
    - move leading article to end: "The Servant" -> "servant the"
      and "Servant, The" -> "servant the"
    - collapse whitespace, drop punctuation
    """
    if title is None:
        return ""
    t = title.strip()

    # Strip accents
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))

    t = t.lower()

    # Smart quotes / apostrophes -> straight
    t = t.replace("\u2018", "'").replace("\u2019", "'")
    t = t.replace("\u201c", '"').replace("\u201d", '"')

    # Handle "Title, The" -> "The Title"
    m = re.match(r"^(.*),\s+(the|a|an|le|la|les|el|los|las|der|die|das)$", t)
    if m:
        t = f"{m.group(2)} {m.group(1)}"

    # Now normalize leading articles off entirely so "The X" and "X" both match.
    # We instead keep them but move to end so "the servant" == "servant the"? Simpler: just strip.
    t = re.sub(r"^(the|a|an|le|la|les|el|los|las|der|die|das)\s+", "", t)

    # Drop most punctuation (but keep letters/digits/spaces/&)
    t = re.sub(r"[^\w\s&]", " ", t)

    # Collapse whitespace
    t = re.sub(r"\s+", " ", t).strip()

    return t


@dataclass
class Watchlist:
    entries: list[WatchlistEntry]
    by_title: dict[str, list[WatchlistEntry]]
    by_title_year: dict[tuple[str, int], WatchlistEntry]

    def match(self, title: str, year: int | None) -> WatchlistEntry | None:
        norm = normalize_title(title)
        if not norm:
            return None
        candidates = self.by_title.get(norm, [])
        if not candidates:
            return None

        if year is not None:
            # Exact (title, year) hit wins.
            hit = self.by_title_year.get((norm, year))
            if hit is not None:
                return hit
            # ±1 year drift (festival vs release year quirks).
            for c in candidates:
                if c.year is not None and abs(c.year - year) <= 1:
                    return c
            # Candidates with no year at all -> accept if unambiguous.
            yearless = [c for c in candidates if c.year is None]
            if len(yearless) == 1 and len(candidates) == 1:
                return yearless[0]
            # Otherwise: year conflict. Don't match (avoids cases like
            # "The Room" 2003 vs "Room" 2015 colliding on normalized title).
            return None

        # No year on the screening side -> only match if unambiguous.
        if len(candidates) == 1:
            return candidates[0]
        return None


def load_watchlist(csv_path: str | Path) -> Watchlist:
    path = Path(csv_path)
    entries: list[WatchlistEntry] = []
    by_title: dict[str, list[WatchlistEntry]] = {}
    by_title_year: dict[tuple[str, int], WatchlistEntry] = {}

    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            title = (row.get("Name") or "").strip()
            if not title:
                continue
            year_raw = (row.get("Year") or "").strip()
            year: int | None = None
            if year_raw:
                try:
                    year = int(year_raw)
                except ValueError:
                    year = None
            url = (row.get("Letterboxd URI") or "").strip()

            entry = WatchlistEntry(title=title, year=year, letterboxd_url=url)
            entries.append(entry)

            norm = normalize_title(title)
            by_title.setdefault(norm, []).append(entry)
            if year is not None:
                # Last write wins for exact-dup (title, year); rare and benign.
                by_title_year[(norm, year)] = entry

    return Watchlist(entries=entries, by_title=by_title, by_title_year=by_title_year)


if __name__ == "__main__":
    wl = load_watchlist("/sessions/zealous-inspiring-turing/mnt/movies/data/watchlist.csv")
    print(f"Loaded {len(wl.entries)} entries")
    print(f"Unique normalized titles: {len(wl.by_title)}")
    # Spot checks
    for q in [("The Servant", 1963), ("Deliverance", 1972), ("Oppenheimer", 2023),
              ("(500) Days of Summer", 2009), ("City of God", 2002),
              ("Sherman's March", 1985), ("Nonexistent Movie XYZ", 2024)]:
        hit = wl.match(*q)
        print(f"  {q} -> {hit.title if hit else None}")
