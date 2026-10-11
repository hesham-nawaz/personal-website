"""Screenings at NYC venues the other sources miss, from Screen Slate.

Screen Slate (https://www.screenslate.com) keeps hand-maintained listings
for ~30 NYC venues, including MoMA, the Paris Theater, Spectacle, Light
Industry, Maysles, DCTV's Firehouse and Scandinavia House. Its listings page
loads them from a JSON API:

  /api/screenings/date?_format=json&date=YYYYMMDD&field_city_target_id=10969
      -> one row per showtime: {nid, field_time, field_note, field_timestamp}
  /api/screenings/id/<nid>+<nid>+...?_format=json
      -> one row per screening: titles, "director / year / runtime / format"
         per film, venue link, and the venue's own page for the screening

The API is undocumented and not published for reuse; Hesham chose to use it
for this private, non-monetized page. Requests are kept few and paced.

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
from datetime import date, datetime
from pathlib import Path

BASE = "https://www.screenslate.com"
CITY_NYC = "10969"
USER_AGENT = "hesham-nawaz-personal-website/1.0 (private listings page; +https://heshamnawaz.me)"
BATCH = 60            # screening ids per detail request
PAUSE_SECONDS = 0.3   # between requests

# Venues covered by the other sources, by Screen Slate's names for them
# (normalized). Anything repertory.nyc lists is skipped too, by name.
COVERED = {
    "ifc center", "film at lincoln center", "metrograph", "film forum", "quad cinema",
    "bam", "bam rose cinemas", "anthology film archives", "roxy cinema", "roxy cinema new york",
    "new plaza cinema", "nitehawk cinema williamsburg", "nitehawk cinema prospect park",
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


def _get_json(path: str):
    return json.loads(_get(path))


def _text(fragment: str | None) -> str:
    t = re.sub(r"<[^>]+>", " ", fragment or "")
    return re.sub(r"\s+", " ", html.unescape(t)).strip()


def _spans(fragment: str | None) -> list[str]:
    return [_text(s) for s in re.findall(r"<span[^>]*>(.*?)</span>", fragment or "", re.S)]


def parse_films(detail: dict) -> list[dict]:
    """[{title, year, director, format}] for each film in a screening.
    Films are separated by '|' in the label, info and format fields; each
    info group is <span>director</span><span>year</span><span>runtime</span>
    <span>format</span>, any of which may be missing."""
    labels = [_text(p) for p in (detail.get("media_title_labels") or "").split("|")]
    infos = (detail.get("media_title_info") or "").split("|")
    fmts = [f.strip() for f in (detail.get("media_title_format") or "").split("|")]
    films = []
    for i, title in enumerate(labels):
        if not title:
            continue
        director = year = None
        for s in _spans(infos[i] if i < len(infos) else ""):
            if re.fullmatch(r"\d{4}", s):
                year = int(s)
            elif re.fullmatch(r"\d+\s*M(IN)?", s, re.I) or (i < len(fmts) and s == fmts[i]):
                continue
            elif director is None:
                director = re.sub(r"\s*,\s*", ", ", s)
        fmt = fmts[i] if i < len(fmts) and fmts[i] else None
        films.append({"title": title, "year": year, "director": director, "format": fmt})
    if not films:
        t = re.sub(r"\s+at\s+[^@]+$", "", _text(detail.get("title")))
        if t:
            films.append({"title": t, "year": None, "director": None, "format": None})
    return films


def venue_of(detail: dict) -> tuple[str, str | None]:
    """(venue name, Screen Slate venue slug)."""
    raw = detail.get("venue_title") or ""
    m = re.search(r'href="/venues/([^"]+)"', raw)
    return _text(raw), (m.group(1) if m else None)


def to_rows(showtimes: list[dict], details: dict[str, dict], skip_venues: set[str]) -> list[dict]:
    """repertory.nyc-shaped rows for one day's showtimes."""
    rows = []
    for s in showtimes:
        d = details.get(str(s.get("nid")))
        if not d:
            continue
        venue, slug = venue_of(d)
        if not venue or norm_venue(venue) in skip_venues:
            continue
        try:
            when = datetime.strptime((s.get("field_timestamp") or "")[:16], "%Y-%m-%dT%H:%M")
        except ValueError:
            continue
        films = parse_films(d)
        if not films:
            continue
        formats = sorted({f["format"] for f in films if f["format"]})
        note = _text(s.get("field_note"))
        first = films[0]
        rows.append({
            "id": f"ss-{s.get('nid')}-{when.strftime('%Y%m%dT%H%M')}",
            "film_title": first["title"] if len(films) == 1 else " / ".join(f["title"] for f in films),
            "film_year": first["year"] if len(films) == 1 else None,
            "film_director": first["director"] if len(films) == 1 else None,
            "films": films if len(films) > 1 else None,
            "theater_name": venue,
            "theater_slug": slug,
            "date": when.date().isoformat(),
            "time": when.strftime("%H:%M"),
            "format": formats[0] if len(formats) == 1 else None,
            "ticket_url": (d.get("field_url") or "").strip() or None,
            "special_event": note or None,
        })
    return rows


class ScreenSlate:
    """Fetches days from Screen Slate, caching screening details per run."""

    def __init__(self, skip_venues: set[str], get_json=_get_json):
        self.skip = {norm_venue(v) for v in skip_venues} | COVERED
        self.get_json = get_json
        self.details: dict[str, dict] = {}

    def fetch_day(self, day: date, url=None) -> list[dict]:
        showtimes = self.get_json(f"/api/screenings/date?_format=json&date={day.strftime('%Y%m%d')}"
                                  f"&field_city_target_id={CITY_NYC}")
        if not isinstance(showtimes, list):
            raise ValueError("unexpected response")
        missing = sorted({str(s.get("nid")) for s in showtimes} - set(self.details))
        for i in range(0, len(missing), BATCH):
            got = self.get_json(f"/api/screenings/id/{'+'.join(missing[i:i + BATCH])}?_format=json")
            for d in (got if isinstance(got, list) else list(got.values())):
                self.details[str(d.get("nid"))] = d
        return to_rows(showtimes, self.details, self.skip)


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
