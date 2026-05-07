"""Match Revival Hub screenings against a Letterboxd watchlist."""
from __future__ import annotations

from dataclasses import dataclass, field

from revival_hub import Screening, Film
from watchlist import Watchlist, WatchlistEntry


@dataclass
class MatchedScreening:
    screening: Screening
    # Per-film matches (one slot per film in the screening; None if no match).
    matches: list[WatchlistEntry | None] = field(default_factory=list)

    @property
    def any_match(self) -> bool:
        return any(m is not None for m in self.matches)

    @property
    def matched_films(self) -> list[tuple[Film, WatchlistEntry]]:
        out = []
        for f, m in zip(self.screening.films, self.matches):
            if m is not None:
                out.append((f, m))
        return out


def match_screenings(screenings: list[Screening], watchlist: Watchlist) -> list[MatchedScreening]:
    """Return one MatchedScreening per input screening. Only screenings where at
    least one film matched the watchlist are returned."""
    results: list[MatchedScreening] = []
    for scr in screenings:
        slots: list[WatchlistEntry | None] = []
        for film in scr.films:
            hit = watchlist.match(film.title, film.year)
            slots.append(hit)
        ms = MatchedScreening(screening=scr, matches=slots)
        if ms.any_match:
            results.append(ms)
    return results


if __name__ == "__main__":
    from watchlist import load_watchlist
    from revival_hub import parse_revival_hub_pdf

    wl = load_watchlist("/sessions/zealous-inspiring-turing/mnt/movies/data/watchlist.csv")
    scrs = parse_revival_hub_pdf(
        "/sessions/zealous-inspiring-turing/mnt/movies/reference-docs/Gmail - Playing This Week in LA.pdf",
        reference_year=2026,
    )

    matches = match_screenings(scrs, wl)
    print(f"Screenings parsed: {len(scrs)}")
    print(f"Screenings matched to watchlist: {len(matches)}")
    print()
    print("Sample matches:")
    for ms in matches[:15]:
        films = ", ".join(f"{f.title} ({f.year})" for f in ms.screening.films)
        matched = ", ".join(m.title for m in ms.matches if m is not None)
        print(f"  {ms.screening.day} {ms.screening.times[0]} | {films} @ {ms.screening.theater}")
        print(f"    -> matched: {matched}")
