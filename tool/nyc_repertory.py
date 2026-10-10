"""Fetch NYC repertory screenings from repertory.nyc's public JSON API.

repertory.nyc aggregates NYC arthouse/repertory theaters into a single feed at
https://repertory.nyc/api/screenings. As of Oct 2026 that is 11 venues: Film
Forum, Film at Lincoln Center, IFC Center, Metrograph, Quad, BAM Rose Cinemas,
Anthology Film Archives, Roxy, New Plaza and both Nitehawks. MoMA, Museum of
the Moving Image, Japan Society, the Paris Theater and Alamo are not in it.

The feed is far cleaner than any newsletter — no email/IMAP dependency, no
auth, structured JSON with title/year/director/theater/date/time/format.

This module returns list[Screening] using the same dataclass shape as
revival_hub.py, so match.py and render.py work unchanged.
"""
from __future__ import annotations

import json
import re
import urllib.request
from datetime import date, datetime, timedelta
from typing import Iterable

from revival_hub import Film, Screening


API_URL = "https://repertory.nyc/api/screenings"
USER_AGENT = "hesham-nawaz-personal-website/1.0 (+https://hesham-nawaz.com)"
DEFAULT_TIMEOUT = 30  # seconds


PAGE_SIZE = 100  # the API caps a single response; 100 is the largest page it honours
MAX_PAGES_PER_DAY = 20  # safety stop; a busy day is ~130 screenings (2 pages)


def fetch_raw(url: str = API_URL, timeout: int = DEFAULT_TIMEOUT,
              query_date: date | None = None,
              limit: int | None = None, offset: int = 0) -> list[dict]:
    """GET the JSON feed. Returns the list of screening dicts as-is.

    IMPORTANT: The bare `/api/screenings` endpoint returns a fixed/stale
    window of data (observed: only the first few days of May 2026 come back
    regardless of when you call it). To get current data you MUST pass
    `?date=YYYY-MM-DD` — that returns everything scheduled for that specific
    day. We iterate one call per day of the week in fetch_screenings_for_week.

    The API is paginated: without `limit` it returns at most 50 rows, sorted
    by time, so a single call silently drops every evening screening. Pass
    `limit`/`offset` and keep paging (see fetch_day) to get the whole day.
    """
    params = []
    if query_date is not None:
        params.append(f"date={query_date.isoformat()}")
    if limit is not None:
        params.append(f"limit={limit}")
    if offset:
        params.append(f"offset={offset}")
    full = url
    if params:
        sep = "&" if "?" in url else "?"
        full = f"{url}{sep}{'&'.join(params)}"
    req = urllib.request.Request(full, headers={
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = resp.read()
    return json.loads(payload)


def fetch_day(day: date, url: str = API_URL) -> list[dict]:
    """Every screening listed for `day`, following pagination until a page
    comes back empty. Rows are de-duplicated by id because consecutive pages
    can overlap by a row when the listing shifts between requests."""
    rows: dict[str, dict] = {}
    offset = 0
    for _ in range(MAX_PAGES_PER_DAY):
        page = fetch_raw(url, query_date=day, limit=PAGE_SIZE, offset=offset)
        if not isinstance(page, list):
            raise ValueError(f"unexpected API response type: {type(page).__name__}")
        if not page:
            break
        for entry in page:
            key = entry.get("id") or json.dumps(entry, sort_keys=True)
            rows[key] = entry
        offset += len(page)
    else:
        import sys
        print(f"[nyc_repertory] WARNING: stopped paging {day} after "
              f"{MAX_PAGES_PER_DAY} pages", file=sys.stderr)
    return list(rows.values())


def event_label(special) -> str | None:
    """special_event is null, a string, or an object like
    {"event_type": "q_and_a", "description": "Q&A with ...", "guests": [...]}.
    The venue's description is the useful part (event_type is often wrong:
    live scores and introductions get filed as "q_and_a"), so show that, and
    fall back to a label for the type."""
    if not special:
        return None
    if isinstance(special, str):
        return special.strip() or None
    if isinstance(special, dict):
        desc = (special.get("description") or "").strip()
        desc = re.sub(r"\s*Read More\s*›?\s*$", "", desc).strip()
        if desc:
            return desc
        names = {"q_and_a": "Q&A", "introduction": "Introduction", "intro": "Introduction",
                 "filmmaker_in_person": "Filmmaker in person",
                 "live_performance": "Live performance", "panel": "Panel"}
        etype = special.get("event_type") or ""
        if etype == "other":
            return None
        label = names.get(etype, etype.replace("_", " ").strip().capitalize())
        guests = [g if isinstance(g, str) else (g.get("name") if isinstance(g, dict) else None)
                  for g in (special.get("guests") or [])]
        guests = [g for g in guests if g]
        if label and guests:
            label = f"{label} with {', '.join(guests)}"
        return label or None
    return str(special)


# The feed's "format" field mixes projection formats with stray venue text
# (ticket prices, "first come first serve" notices). Only real formats that
# set a screening apart are shown; plain digital projection (DCP) is the norm
# and is hidden.
_FORMAT_NAMES = [
    (r"^(\d{2})\s*mm\b", lambda m: f"{m.group(1)}mm"),
    (r"^4k\s+restoration$", lambda m: "4K restoration"),
    (r"^4k(\s+dcp)?$", lambda m: "4K"),
    (r"^3d(\s+dcp)?$", lambda m: "3D"),
    (r"^(imax.*|vhs|nitrate.*|.*restoration)$", lambda m: m.group(0)[:1].upper() + m.group(0)[1:]),
]


def format_tag(raw) -> str | None:
    """Display tag for a format value, or None if it should not be shown."""
    if not raw:
        return None
    t = str(raw).strip()
    low = t.lower()
    if len(t) > 30 or low in ("dcp", "digital", "dcp digital", "digital projection"):
        return None
    for pat, name in _FORMAT_NAMES:
        m = re.match(pat, low)
        if m:
            return name(m)
    return None


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

    # Notes carry only display-worthy format tags (no plain DCP); special
    # events go in their own field.
    tag = format_tag(entry.get("format"))
    special = entry.get("special_event")
    event = special if (isinstance(special, str) or special is None) else event_label(special)
    ticket = (entry.get("ticket_url") or "").strip() or None

    return Screening(
        day=d,
        weekday=_weekday_name(d),
        times=[_time_24_to_12(time_str)],
        films=[Film(title=title, year=year)],
        directors=directors,
        theater=(entry.get("theater_name") or "").strip(),
        notes=tag,
        presenter=None,
        raw=json.dumps(entry, ensure_ascii=False),
        ticket_urls=[ticket],
        event=event,
    )


def screening_from_record(rec: dict) -> Screening | None:
    """Screening from a stored record (see nyc_store.py)."""
    entry = {
        "film_title": rec.get("title"), "film_year": rec.get("year"),
        "film_director": rec.get("director"), "theater_name": rec.get("theater"),
        "date": rec.get("date"), "time": rec.get("time"),
        "format": rec.get("format"), "special_event": rec.get("event"),
        "ticket_url": rec.get("ticket_url"), "id": rec.get("id"),
    }
    return _entry_to_screening(entry)


def screenings_from_records(records: list[dict]) -> list[Screening]:
    out = [s for s in (screening_from_record(r) for r in records) if s is not None]
    return _merge_showtimes(out)


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
            (s.event or "").lower(),
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
        combined_urls: list[str | None] = []
        seen_times: set[str] = set()
        for it in items_sorted:
            urls = list(it.ticket_urls) + [None] * (len(it.times) - len(it.ticket_urls))
            for t, u in zip(it.times, urls):
                if t not in seen_times:
                    combined_times.append(t)
                    combined_urls.append(u)
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
            ticket_urls=combined_urls,
            event=base.event,
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
    """Fetch all screenings for the Mon-Sun week containing `reference`,
    convert to Screening objects, and merge same-day same-film-same-theater
    entries by concatenating their times.

    Defaults to today's calendar week. Queries one day at a time because the
    bare API endpoint returns a stale/fixed window; only `?date=YYYY-MM-DD`
    returns current data. Each day is paged through in full (fetch_day).
    """
    monday, sunday = _week_bounds(reference)
    screenings: list[Screening] = []
    day = monday
    while day <= sunday:
        try:
            raw = fetch_day(day, url)
        except Exception as e:
            # Log and skip; a single-day failure shouldn't kill the whole week.
            import sys
            print(f"[nyc_repertory] WARNING: fetch failed for {day}: {e}",
                  file=sys.stderr)
            day += timedelta(days=1)
            continue
        for entry in raw:
            date_str = entry.get("date")
            if not date_str:
                continue
            # Trust the query date over the entry date to catch any mislabels
            # (defensive: if the API ever mixes days, we still bin correctly).
            try:
                entry_d = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                continue
            if entry_d != day:
                # Some endpoints return a small window; only keep the exact day.
                continue
            s = _entry_to_screening(entry)
            if s is not None:
                screenings.append(s)
        day += timedelta(days=1)
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
