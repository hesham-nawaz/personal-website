"""Fetch NYC repertory screenings from repertory.nyc's public JSON API.

repertory.nyc aggregates 12+ NYC arthouse/repertory theaters (Film Forum,
Metrograph, IFC Center, Anthology, BAM, MoMA, MoMI, Nitehawk, Quad, Roxy,
New Plaza, Film at Lincoln Center) into a single feed at
https://repertory.nyc/api/screenings.

The feed is far cleaner than any newsletter — no email/IMAP dependency, no
auth, structured JSON with title/year/director/theater/date/time/format.

This module returns list[Screening] using the same dataclass shape as
revival_hub.py, so match.py and render.py work unchanged.
"""
from __future__ import annotations

import json
import urllib.request
from datetime import date, datetime, timedelta
from typing import Iterable

from revival_hub import Film, Screening


API_URL = "https://repertory.nyc/api/screenings"
USER_AGENT = "hesham-nawaz-personal-website/1.0 (+https://hesham-nawaz.com)"
DEFAULT_TIMEOUT = 30  # seconds


def fetch_raw(url: str = API_URL, timeout: int = DEFAULT_TIMEOUT) -> list[dict]:
    """GET the JSON feed. Returns the list of screening dicts as-is."""
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = resp.read()
    return json.loads(payload)


def _time_24_to_12(hhmm: str) -> str:
    """Convert '14:30' → '2:30p', '09:00' → '9:00a'. Falls back to the input."""
    try:
        h_s, m_s = hhmm.split(":")
        h, m = int(h_s), int(m_s)
        period = "a" if h < 12 else "p"
        h12 = h % 12 or 12
        return f"{h12}:{m:02d}{period}"
    except Exception:
        return hhmm


def _weekday_name(d: date) -> str:
    # Monday, Tuesday, ...
    return d.strftime("%A")


def _week_bounds(reference: date | None = None) -> tuple[date, date]:
    """Return (monday, sunday) of the ISO week containing `reference`.

    Defaults to today. Uses Python's weekday() so Monday=0, Sunday=6.
    """
    if reference is None:
        reference = date.today()
    monday = reference - timedelta(days=reference.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def _entry_to_screening(entry: dict) -> Screening | None:
    """Convert one API row into a Screening. Returns None if unparseable."""
    title = (entry.get("film_title") or "").strip()
    date_str = entry.get("date")
    time_str = entry.get("time")
    if not title or not date_str or not time_str:
        return None
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return None

    year = entry.get("film_year")  # may be None
    if year is not None:
        try:
            year = int(year)
        except (TypeError, ValueError):
            year = None

    director = entry.get("film_director")
    directors: list[str] = []
    if director:
        # The API sometimes returns "Name1, Name2" as a single string;
        # split conservatively so render.py's "Dir./Dirs." label works.
        directors = [d.strip() for d in director.split(",") if d.strip()]

    # Compose notes from format + special_event when present.
    note_bits: list[str] = []
    fmt = entry.get("format")
    if fmt:
        note_bits.append(str(fmt))
    special = entry.get("special_event")
    if special:
        note_bits.append(str(special))
    notes = " • ".join(note_bits) if note_bits else None

    return Screening(
        day=d,
        weekday=_weekday_name(d),
        times=[_time_24_to_12(time_str)],
        films=[Film(title=title, year=year)],
        directors=directors,
        theater=(entry.get("theater_name") or "").strip(),
        notes=notes,
        presenter=None,
        raw=json.dumps(entry, ensure_ascii=False),
    )


def _merge_showtimes(screenings: list[Screening]) -> list[Screening]:
    """Combine same-day same-title same-theater screenings by concatenating
    their times, so a film with four showtimes at IFC on Tuesday appears as
    one card with times "1:00p, 3:15p, 5:30p, 8:00p" instead of four cards.
    """
    def key(s: Screening) -> tuple:
        return (
            s.day.isoformat(),
            tuple((f.title.lower(), f.year) for f in s.films),
            s.theater.lower(),
            (s.notes or "").lower(),
        )

    from collections import defaultdict
    groups: dict[tuple, list[Screening]] = defaultdict(list)
    for s in screenings:
        groups[key(s)].append(s)

    merged: list[Screening] = []
    for k, items in groups.items():
        base = items[0]
        # Preserve original chronological order within the day
        items_sorted = sorted(items, key=lambda s: _time_sort_key(s.times[0]))
        combined_times: list[str] = []
        seen_times: set[str] = set()
        for it in items_sorted:
            for t in it.times:
                if t not in seen_times:
                    combined_times.append(t)
                    seen_times.add(t)
        merged.append(Screening(
            day=base.day,
            weekday=base.weekday,
            times=combined_times,
            films=base.films,
            directors=base.directors,
            theater=base.theater,
            notes=base.notes,
            presenter=base.presenter,
            raw=base.raw,
        ))
    return merged


def _time_sort_key(t: str) -> tuple[int, int]:
    """'2:30p' → (14, 30) for sorting."""
    try:
        period = t[-1].lower()
        h_s, m_s = t[:-1].split(":")
        h, m = int(h_s), int(m_s)
        if period == "p" and h != 12:
            h += 12
        elif period == "a" and h == 12:
            h = 0
        return (h, m)
    except Exception:
        return (99, 99)


def fetch_screenings_for_week(reference: date | None = None,
                              url: str = API_URL) -> list[Screening]:
    """Fetch all screenings, filter to the Mon-Sun week containing `reference`,
    convert to Screening objects, and merge same-day same-film-same-theater
    entries by concatenating their times.

    Defaults to today's calendar week.
    """
    monday, sunday = _week_bounds(reference)
    raw = fetch_raw(url)
    screenings: list[Screening] = []
    for entry in raw:
        date_str = entry.get("date")
        if not date_str:
            continue
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if not (monday <= d <= sunday):
            continue
        s = _entry_to_screening(entry)
        if s is not None:
            screenings.append(s)
    return _merge_showtimes(screenings)


if __name__ == "__main__":
    import sys
    scrs = fetch_screenings_for_week()
    monday, sunday = _week_bounds()
    print(f"[nyc_repertory] {len(scrs)} screenings for {monday} – {sunday}",
          file=sys.stderr)
    for s in scrs[:5]:
        films = ", ".join(f"{f.title} ({f.year})" for f in s.films)
        print(f"  {s.day} {s.times} | {films} @ {s.theater}")
