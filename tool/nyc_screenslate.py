"""Screenings at NYC venues the other sources miss, from Screen Slate.

Screen Slate (https://www.screenslate.com) keeps hand-maintained listings
for ~30 NYC venues, including MoMA, the Paris Theater, Spectacle, Light
Industry, Maysles, DCTV's Firehouse and Scandinavia House.

Its listings are server-rendered, one page per day:

  /listings/YYYY-MM-DD
      <div class="venue"><h3><a href="/venues/<slug>">Venue</a></h3>
        <div class="listing">
          [<div class="series"><a>Series</a></div>]
          [<a class="display-title" href="...">Program title</a>]
          <div class="media-titles">
            <div class="media-title"> <a class="screening-link" href="ticket page">
              <span class="field--name-title">Title</span></a>   (or .media-title-label)
              <div class="media-title-info"><span>Director</span><span>Year</span>
                <span>Runtime</span><span>Format</span></div></div> ...
          <div class="showtimes"><span>1:00pm</span> <span class="has-note">6:30pm*</span>
          <div class="screening-notes"><div>* Open Captions</div></div>

Until Nov 2026 the page loaded these from a JSON API (/api/screenings/...).
Screen Slate retired it (it now answers 403, and robots.txt disallows /api/,
pointing crawlers to the listing pages instead), which is why this source
went quiet after Oct 15, 2026. The listing pages are allowed for crawlers;
Hesham uses them for this private, non-monetized page. One request per day,
paced.

Venues that repertory.nyc or the Museum of the Moving Image's calendar
already cover are skipped, so nothing is listed twice. For each new venue,
its Screen Slate page gives a street address, which sets its borough for
the theater filter and its map link; addresses are cached in
data/nyc-venues.json.

Rows come out in repertory.nyc's API shape (plus a "films" list for
multi-film programs), so nyc_store.py stores them alongside the others.
Stdlib only.
"""
from __future__ import annotations

import html
import json
import re
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

from htmldom import parse as parse_html

BASE = "https://www.screenslate.com"
USER_AGENT = "hesham-nawaz-personal-website/1.0 (private listings page; +https://heshamnawaz.me)"
PAUSE_SECONDS = 0.5   # between requests

# Venues covered by the other sources, by Screen Slate's names for them
# (normalized). Anything repertory.nyc lists is skipped too, by name.
COVERED = {
    "ifc center", "film at lincoln center", "metrograph", "film forum", "quad cinema",
    "bam", "bam rose cinemas", "anthology film archives", "roxy cinema", "roxy cinema new york",
    "new plaza cinema", "nitehawk cinema williamsburg", "nitehawk cinema prospect park",
    "nitehawk williamsburg", "nitehawk prospect park",
    "museum of the moving image",
}


def norm_venue(name: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(name or "").strip().lower())


def _get(path: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(BASE + path, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    time.sleep(PAUSE_SECONDS)
    return data


def _text(fragment: str | None) -> str:
    t = re.sub(r"<[^>]+>", " ", fragment or "")
    return re.sub(r"\s+", " ", html.unescape(t)).strip()


def to_24h(label: str) -> str | None:
    """'6:30pm*' -> '18:30'; None if it isn't a clock time."""
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*([ap])\.?m\.?\s*\**\s*", label or "", re.I)
    if not m:
        return None
    h, mi = int(m.group(1)) % 12, int(m.group(2))
    if m.group(3).lower() == "p":
        h += 12
    return f"{h:02d}:{mi:02d}"


def _film(media_title) -> dict | None:
    t = media_title.find(cls="field--name-title")
    title = t.text() if t else ""
    if not title:
        return None
    director = year = fmt = None
    info = media_title.find(cls="media-title-info")
    spans = [s.text() for s in info.child_nodes("span")] if info else []
    for i, s in enumerate(spans):
        if not s:
            continue
        if re.fullmatch(r"\d{4}(\s*[-–]\s*\d{2,4})?", s):
            year = int(s[:4])
        elif re.fullmatch(r"\d+\s*M(IN)?", s, re.I):
            continue
        elif i == len(spans) - 1 and i > 0:
            fmt = s
        elif director is None and i == 0:
            director = re.sub(r"\s*,\s*", ", ", s)
    return {"title": title, "year": year, "director": director, "format": fmt}


def parse_listings(page_html: str, day: date, skip_venues: set[str]) -> list[dict]:
    """repertory.nyc-shaped rows for one day's listing page."""
    root = parse_html(page_html)
    rows = []
    for venue in root.find_all(cls="venue"):
        h3 = venue.find("h3")
        name = h3.text() if h3 else ""
        if not name or norm_venue(name) in skip_venues:
            continue
        a = h3.find("a")
        m = re.search(r"/venues/([^/?#]+)", (a.get("href") if a else "") or "")
        slug = m.group(1) if m else None
        for n, listing in enumerate(venue.find_all(cls="listing")):
            films = [f for f in (_film(mt) for mt in listing.find_all(cls="media-title")) if f]
            display = listing.find(cls="display-title")
            if not films:
                if not display:
                    continue
                films = [{"title": display.text(), "year": None, "director": None, "format": None}]
            link = display if display else listing.find(cls="screening-link")
            ticket = (link.get("href") or "").strip() if link else ""
            notes = {}
            box = listing.find(cls="screening-notes")
            for d in (box.child_nodes("div") if box else []):
                nm = re.match(r"\s*(\*+)\s*(.*)", d.text())
                if nm and nm.group(2):
                    notes[len(nm.group(1))] = nm.group(2)
            formats = sorted({f["format"] for f in films if f["format"]})
            if display and len(films) > 1:
                title = display.text()
            else:
                title = films[0]["title"] if len(films) == 1 else " / ".join(f["title"] for f in films)
            times = listing.find(cls="showtimes")
            for span in (times.child_nodes("span") if times else []):
                label = span.text()
                hhmm = to_24h(label)
                if not hhmm:
                    continue
                stars = len(label) - len(label.rstrip("*"))
                rows.append({
                    "id": f"ss-{slug or norm_venue(name)}-{day.strftime('%Y%m%d')}-{hhmm.replace(':', '')}-"
                          f"{re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')[:40]}",
                    "film_title": title,
                    "film_year": films[0]["year"] if len(films) == 1 else None,
                    "film_director": films[0]["director"] if len(films) == 1 else None,
                    "films": films if len(films) > 1 else None,
                    "theater_name": name,
                    "theater_slug": slug,
                    "date": day.isoformat(),
                    "time": hhmm,
                    "format": formats[0] if len(formats) == 1 else None,
                    "ticket_url": ticket or None,
                    "special_event": notes.get(stars) if stars else None,
                })
    return rows


class ScreenSlate:
    """Fetches days from Screen Slate's listing pages."""

    def __init__(self, skip_venues: set[str], get=_get):
        self.skip = {norm_venue(v) for v in skip_venues} | COVERED
        self.get = get

    def fetch_day(self, day: date, url=None) -> list[dict]:
        page = self.get(f"/listings/{day.isoformat()}").decode("utf-8", "replace")
        if 'class="venue"' not in page and "listings" not in page:
            raise ValueError("unexpected page")
        return parse_listings(page, day, self.skip)

    def get_page(self, path: str) -> bytes:
        return self.get(path)


# ---- venue addresses -------------------------------------------------------

def borough_for_zip(zip_code: str | None) -> str | None:
    if not zip_code or not zip_code.isdigit():
        return None
    z = int(zip_code)
    if 10001 <= z <= 10282:
        return "Manhattan"
    if 10301 <= z <= 10314:
        return "Staten Island"
    if 10451 <= z <= 10475:
        return "Bronx"
    if 11201 <= z <= 11256:
        return "Brooklyn"
    if 11001 <= z <= 11109 or 11351 <= z <= 11697:
        return "Queens"
    return None


def parse_venue_page(page_html: str) -> dict:
    """{"address": ..., "borough": ...} from a Screen Slate venue page."""
    text = _text(page_html)
    m = re.search(r"Address\s+(.+?\b(?:NY|New York)\s+(\d{5}))", text)
    if not m:
        return {"address": None, "borough": None}
    return {"address": m.group(1).strip(), "borough": borough_for_zip(m.group(2))}


def update_venues(registry_path: Path, rows: list[dict], get=_get) -> dict:
    """Add any new venue in `rows` to the registry (data/nyc-venues.json),
    looking up its address once. Returns the registry {name: info}."""
    try:
        registry = json.loads(registry_path.read_text())
    except (OSError, ValueError):
        registry = {}
    for r in rows:
        name, slug = r.get("theater_name"), r.get("theater_slug")
        if not name or name in registry or not slug:
            continue
        try:
            info = parse_venue_page(get(f"/venues/{slug}").decode("utf-8", "replace"))
        except Exception as e:
            print(f"[screenslate] could not look up {name}: {e}", file=sys.stderr)
            continue
        registry[name] = {"slug": slug, **info}
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(json.dumps(registry, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    return registry
