"""Museum of the Moving Image (Astoria) screenings, from its events calendar.

repertory.nyc doesn't cover MoMI, but the museum's site runs The Events
Calendar plugin for WordPress, whose public REST API lists every event:
https://movingimage.org/wp-json/tribe/events/v1/events
(start_date / end_date / per_page / page; pages link to the next one).

Film screenings are the events in a "Screening..." category ("Screening",
"Screening + Q&A", "Screening + Live Accompaniment", ...). Their
descriptions follow the museum's house style, e.g.
    "Dir. Lotte Reiniger. 1926, 80 mins. Germany. 35mm."
which gives the director, year and format. Tickets are sold from each
event's page, so that is the ticket link.

Rows come out in the same shape as repertory.nyc's API, so nyc_store.py
can store them alongside it. Stdlib only.
"""
from __future__ import annotations

import html
import json
import re
import urllib.request
from datetime import date, datetime

API_URL = "https://movingimage.org/wp-json/tribe/events/v1/events"
VENUE = "Museum of the Moving Image"
SLUG = "momi"
USER_AGENT = "hesham-nawaz-personal-website/1.0 (+https://heshamnawaz.me)"
PER_PAGE = 50
MAX_PAGES = 20

_EVENT_NAMES = {
    "q&a": "Q&A",
    "intro": "Introduction",
    "introduction": "Introduction",
    "discussion": "Discussion",
    "live accompaniment": "Live accompaniment",
    "conversation": "Conversation",
}


def _text(h: str | None) -> str:
    """Plain text from an HTML fragment."""
    t = re.sub(r"<[^>]+>", " ", h or "")
    return re.sub(r"\s+", " ", html.unescape(t)).strip()


def _get_json(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def fetch_events(start: date, end: date, get_json=_get_json) -> list[dict]:
    """Every calendar event from start to end (inclusive)."""
    url = f"{API_URL}?per_page={PER_PAGE}&start_date={start.isoformat()}&end_date={end.isoformat()}"
    events: list[dict] = []
    for _ in range(MAX_PAGES):
        page = get_json(url)
        events.extend(page.get("events") or [])
        url = page.get("next_rest_url")
        if not url:
            break
    return events


def _event_label(categories: list[str]) -> str | None:
    """'Screening + Q&A' -> 'Q&A'; plain 'Screening' -> None."""
    for c in categories:
        m = re.match(r"^screening\s*\+\s*(.+)$", c.strip(), re.IGNORECASE)
        if m:
            extra = m.group(1).strip()
            return _EVENT_NAMES.get(extra.lower(), extra[:1].upper() + extra[1:])
    return None


def parse_event(e: dict) -> dict | None:
    """One screening as a repertory.nyc-shaped row, or None if the event
    isn't a film screening (exhibitions, talks, workshops)."""
    cats = [_text(c.get("name")) for c in (e.get("categories") or [])]
    if not any(re.match(r"^screening", c, re.IGNORECASE) for c in cats):
        return None
    start = e.get("start_date") or ""
    try:
        when = datetime.strptime(start, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    if e.get("all_day"):
        return None
    desc = _text(e.get("description"))
    director = year = None
    m = re.search(r"\bDirs?\.\s+(.+?)\.\s+(\d{4})\b", desc)
    if m and len(m.group(1)) <= 120:
        director, year = m.group(1).strip(), int(m.group(2))
    fmt = None
    tail = desc[m.end():m.end() + 160] if m else desc
    f = re.search(r"\b(35mm|16mm|70mm|Super ?8|VHS|4K(?: restoration)?|DCP|digital)\b", tail, re.IGNORECASE)
    if f:
        fmt = f.group(1)
    return {
        "id": f"momi-{e.get('id')}",
        "film_title": _text(e.get("title")),
        "film_year": year,
        "film_director": director,
        "theater_name": VENUE,
        "theater_slug": SLUG,
        "date": when.date().isoformat(),
        "time": when.strftime("%H:%M"),
        "format": fmt,
        "ticket_url": (e.get("url") or "").strip() or None,
        "special_event": _event_label(cats),
    }


def fetch_screenings(start: date, end: date, get_json=_get_json) -> list[dict]:
    rows = [parse_event(e) for e in fetch_events(start, end, get_json)]
    return [r for r in rows if r is not None]
