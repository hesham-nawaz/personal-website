"""Screenings straight from theaters' own sites: BAM, the two Nitehawks,
and the AMC, Regal and Alamo Drafthouse locations in New York City.

Why: repertory.nyc carries BAM's and Nitehawk's repertory programs and
special events but not their regular new-release runs (in Nov 2026 it listed
Club Kid at BAM for one week while BAM itself kept showing it), and no
aggregator we use covers the chains at all.

Each source's fetch(today, end) returns {iso day: [rows]} for the days it
fully checked, in repertory.nyc's API row shape, so nyc_store.py stores and
dedupes them like the others. A source that can't be read raises, and the
store keeps that source's screenings as they were. Rows may carry a private
"_first_run" flag (see nyc_store.infer_years); it is not stored.

Where the data comes from (checked Nov 27, 2026; none needs a key):
  BAM       www.bam.org/film lists the film pages; each page's
            #performance-list has one link per screening ("Fri, Nov 27 at
            9:20PM") and the text "Directed by X (YEAR)".
  Nitehawk  nitehawkcinema.com/<location>/ (and its dated pages
            /<location>/YYYY-MM-DD/N/ and /coming-soon/) link the film pages;
            each film page carries schema.org JSON-LD: a Movie (name,
            director, dateCreated = release date) and one ScreeningEvent per
            showtime (startDate, ticket offer URL).
  Alamo     drafthouse.com/s/mother/v2/schedule/market/nyc: every NYC session
            (cinema, show time, format) and presentation (title). The market
            page feed (/s/mother/v1/page/market/main/nyc) says which films
            are repertory. robots.txt allows everything.
  Regal     www.regmovies.com/api/getShowtimes?theatres=<codes>&date=MM-DD-YYYY
            (the site's own showtimes call): performances per theatre, plus
            each movie's opening date and directors, and the dates with shows.
  AMC       www.amctheatres.com/movie-theatres/new-york-city/<theatre>/showtimes?date=YYYY-MM-DD
            The page embeds its data (React server payload): movies as
            {"name","slug","movieId"} and one {"showtimeId",...,"showDateTimeUtc"}
            per showtime, tagged with "<movie>-<theatre>-<format>". AMC's
            robots.txt disallows /showtimes/ (the booking pages), not these.
            AMC has no year or director, so its years come from the other
            sources (nyc_store.infer_years).

Requests are paced and identify the site. Stdlib only.
"""
from __future__ import annotations

import gzip
import json
import re
import sys
import time
import urllib.error
import urllib.request
import zlib
from datetime import date, datetime, timedelta, timezone

from htmldom import parse as parse_html

try:
    from zoneinfo import ZoneInfo
    NY = ZoneInfo("America/New_York")
except Exception:  # no tz database: fall back to the fixed winter offset
    NY = timezone(timedelta(hours=-5))

USER_AGENT = ("Mozilla/5.0 (compatible; hesham-nawaz-personal-website/1.0; "
              "private listings page; +https://heshamnawaz.me)")
PAUSE_SECONDS = 0.5


def http_get(url: str, timeout: int = 40, retries: int = 1) -> bytes:
    """GET with gzip, one retry, and a pause after each request."""
    last = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate",
            "Accept": "text/html,application/json;q=0.9,*/*;q=0.8"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
                enc = (resp.headers.get("Content-Encoding") or "").lower()
            if enc == "gzip":
                data = gzip.decompress(data)
            elif enc == "deflate":
                data = zlib.decompress(data)
            time.sleep(PAUSE_SECONDS)
            return data
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 404:
                raise
        except Exception as e:  # timeouts, resets
            last = e
        time.sleep(2 + 3 * attempt)
    raise last


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def _bucket(rows: list[dict], days: list[date]) -> dict[str, list[dict]]:
    """{iso: rows} for the checked days (rows on other days are dropped)."""
    out = {d.isoformat(): [] for d in days}
    for r in rows:
        if r["date"] in out:
            out[r["date"]].append(r)
    return out


def _days(today: date, end: date) -> list[date]:
    return [today + timedelta(days=i) for i in range((end - today).days + 1)]


# Open-caption and subtitle variants are listed as separate titles by the chains.
_VARIANT = re.compile(r"\s*[\(\[]\s*(open cap[^)\]]*|oc|eng sub[^)\]]*|subtitled|"
                      r"open caption[^)\]]*|dubbed[^)\]]*|spanish[^)\]]*)\s*[\)\]]\s*$", re.I)


def clean_title(title: str) -> tuple[str, str | None]:
    """('Club Kid', 'Open captions') from 'Club Kid (Open Cap/Eng Sub)'."""
    t = re.sub(r"\s+", " ", title or "").strip()
    m = _VARIANT.search(t)
    if not m:
        return t, None
    label = m.group(1).lower()
    note = "Open captions" if ("cap" in label or label == "oc") else m.group(1).strip()
    return t[:m.start()].strip(), note


def chain_format(token: str) -> tuple[str | None, str | None]:
    """(format, note) from a chain's format label: IMAX, Dolby Cinema, 3D,
    70mm, 35mm, 4DX, ScreenX are worth showing; plain digital is not."""
    t = re.sub(r"[\s_-]+", "", (token or "").lower())
    note = "Open captions" if ("opencap" in t or t in ("oc",)) else None
    for key, name in (("70mm", "70mm"), ("35mm", "35mm"), ("imax", "IMAX"), ("dolby", "Dolby Cinema"),
                      ("4dx", "4DX"), ("screenx", "ScreenX"), ("3d", "3D")):
        if key in t:
            return name, note
    return None, note


# ---- BAM -------------------------------------------------------------------

BAM_BASE = "https://www.bam.org"
BAM_NAME = "BAM Rose Cinemas"
_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug",
                                       "sep", "oct", "nov", "dec"], 1)}


def _bam_when(label: str, today: date) -> tuple[date, str] | None:
    """'Fri, Nov 27 at 9:20PM' -> (date, '21:20'), choosing the year that puts
    the date nearest after today."""
    m = re.search(r"([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2})\s+at\s+(\d{1,2}):(\d{2})\s*([AP])M", label, re.I)
    if not m or m.group(1).lower()[:3] not in _MONTHS:
        return None
    mo, dd = _MONTHS[m.group(1).lower()[:3]], int(m.group(2))
    h = int(m.group(3)) % 12 + (12 if m.group(5).upper() == "P" else 0)
    for y in (today.year, today.year + 1):
        try:
            d = date(y, mo, dd)
        except ValueError:
            return None
        if d >= today - timedelta(days=7):
            return d, f"{h:02d}:{int(m.group(4)):02d}"
    return None


def parse_bam_film(page_html: str, today: date, path: str = "") -> list[dict]:
    root = parse_html(page_html)
    h1 = root.find("h1")
    title = h1.text() if h1 else ""
    ul = root.find(id="performance-list")
    if not title or ul is None:
        return []
    text = root.text()
    m = re.search(r"Directed by (.+?) \((\d{4})\)", text)
    director = m.group(1).strip() if m else None
    year = int(m.group(2)) if m else None
    # "Ken Russell's The Devils": BAM credits the director in the title
    if director:
        pm = re.match(r"(.+?)[’']s\s+(.+)$", title)
        if pm and pm.group(1).strip().lower() in director.lower():
            title = pm.group(2).strip()
    rows = []
    for li in ul.child_nodes("li"):
        a = li.find("a")
        span = li.find(cls="perfData")
        label = span.text() if span else li.text()
        when = _bam_when(label, today)
        if not when:
            continue
        d, hhmm = when
        extra = li.text().replace(label, "").strip(" -–·|")
        href = (a.get("href") if a else "") or ""
        pid = re.search(r"(\d+)\s*$", href)
        rows.append({
            "id": f"bam-{pid.group(1) if pid else _slug(title) + '-' + d.isoformat() + '-' + hhmm}",
            "film_title": title, "film_year": year, "film_director": director, "films": None,
            "theater_name": BAM_NAME, "theater_slug": "bam",
            "date": d.isoformat(), "time": hhmm, "format": None,
            "ticket_url": href or (BAM_BASE + path if path else None),
            "special_event": extra or None,
        })
    return rows


def fetch_bam(today: date, end: date, get=http_get) -> dict[str, list[dict]]:
    index = get(BAM_BASE + "/film").decode("utf-8", "replace")
    paths = sorted({p for p in re.findall(r'href="(?:https://www\.bam\.org)?(/film/20\d\d/[a-z0-9-]+)/?["#]', index)})
    if not paths:
        raise ValueError("no film pages linked from bam.org/film")
    rows = []
    for p in paths:
        rows += parse_bam_film(get(BAM_BASE + p).decode("utf-8", "replace"), today, p)
    return _bucket(rows, _days(today, end))


# ---- Nitehawk --------------------------------------------------------------

NITEHAWK_BASE = "https://nitehawkcinema.com"
NITEHAWK = {"williamsburg": "Nitehawk Cinema Williamsburg",
            "prospectpark": "Nitehawk Cinema Prospect Park"}
NITEHAWK_DATED_PAGES = 14


def _ld_objects(page_html: str) -> list[dict]:
    out = []
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page_html, re.S):
        try:
            j = json.loads(block)
        except ValueError:
            continue
        for obj in (j if isinstance(j, list) else [j]):
            out += obj.get("@graph", [obj]) if isinstance(obj, dict) else []
    return out


def parse_nitehawk_film(page_html: str, location: str) -> list[dict]:
    objs = _ld_objects(page_html)
    movie = next((o for o in objs if o.get("@type") == "Movie"), {})
    title = (movie.get("name") or "").strip()
    if not title:
        return []
    year = None
    m = re.match(r"(\d{4})", movie.get("dateCreated") or "")
    if m:
        year = int(m.group(1))
    ry = re.search(r"Release Year:\s*(\d{4})", page_html)
    if ry:
        year = int(ry.group(1))
    directors = movie.get("director") or []
    if isinstance(directors, dict):
        directors = [directors]
    director = ", ".join(d.get("name", "") for d in directors if isinstance(d, dict) and d.get("name")) or None
    fm = re.search(r"Format:\s*(?:</[^>]+>\s*)?([0-9A-Za-z ]{2,20}?)\s*(?:<|Rating:)", page_html)
    fmt = fm.group(1).strip() if fm else None
    rows = []
    for ev in objs:
        if ev.get("@type") != "ScreeningEvent":
            continue
        if "Cancelled" in (ev.get("eventStatus") or ""):
            continue
        sd = ev.get("startDate") or ""
        m = re.match(r"(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})", sd)
        if not m:
            continue
        offers = ev.get("offers") or []
        offers = offers if isinstance(offers, list) else [offers]
        url = next((o.get("url") for o in offers if isinstance(o, dict) and o.get("url")), None) or ev.get("url")
        pid = re.search(r"/purchase/(\d+)", url or "")
        rows.append({
            "id": f"nh-{location}-{pid.group(1) if pid else _slug(title) + '-' + m.group(1) + '-' + m.group(2)}",
            "film_title": title, "film_year": year, "film_director": director, "films": None,
            "theater_name": NITEHAWK[location], "theater_slug": f"nitehawk-{location}",
            "date": m.group(1), "time": m.group(2), "format": fmt,
            "ticket_url": url, "special_event": None,
        })
    return rows


def fetch_nitehawk(today: date, end: date, get=http_get) -> dict[str, list[dict]]:
    rows = []
    for loc in NITEHAWK:
        pages = [f"/{loc}/", f"/{loc}/coming-soon/"]
        pages += [f"/{loc}/{(today + timedelta(days=i)).isoformat()}/{i}/" for i in range(1, NITEHAWK_DATED_PAGES)]
        slugs: set[str] = set()
        for i, p in enumerate(pages):
            try:
                html_text = get(NITEHAWK_BASE + p).decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                if e.code == 404 and i == 1:
                    continue          # not every location has a coming-soon page
                if e.code == 404 and i >= 2:
                    break             # no more dated pages
                raise
            slugs |= set(re.findall(rf"/{loc}/movies/([a-z0-9-]+)/", html_text))
        if not slugs:
            raise ValueError(f"no films linked from Nitehawk {loc}")
        for s in sorted(slugs):
            rows += parse_nitehawk_film(get(f"{NITEHAWK_BASE}/{loc}/movies/{s}/").decode("utf-8", "replace"), loc)
    seen, unique = set(), []
    for r in rows:
        if r["id"] not in seen:
            seen.add(r["id"])
            unique.append(r)
    return _bucket(unique, _days(today, end))


# ---- Alamo Drafthouse ------------------------------------------------------

ALAMO_BASE = "https://drafthouse.com"
ALAMO_CINEMAS = {"2103": "Alamo Drafthouse Lower Manhattan",
                 "2101": "Alamo Drafthouse Downtown Brooklyn",
                 "2102": "Alamo Drafthouse Staten Island"}


def parse_alamo(schedule: dict, films: list[dict] | None = None) -> list[dict]:
    data = schedule.get("data") or {}
    pres = {p.get("slug"): p for p in data.get("presentations") or []}
    repertory = {f.get("slug"): f.get("isRepertory") for f in (films or []) if f.get("slug")}
    rows = []
    for s in data.get("sessions") or []:
        if s.get("isHidden") or s.get("status") == "PAST":
            continue
        cinema = ALAMO_CINEMAS.get(str(s.get("cinemaId")))
        p = pres.get(s.get("presentationSlug")) or {}
        show = p.get("show") or {}
        title, note = clean_title(show.get("title") or "")
        when = s.get("showTimeClt") or ""
        if not cinema or not title or len(when) < 16:
            continue
        fmt, fnote = chain_format(s.get("formatSlug") or "")
        rep = repertory.get(show.get("slug"))
        rows.append({
            "id": f"alamo-{s.get('cinemaId')}-{s.get('sessionId')}",
            "film_title": title, "film_year": None, "film_director": None, "films": None,
            "theater_name": cinema, "theater_slug": f"alamo-{s.get('cinemaId')}",
            "date": when[:10], "time": when[11:16], "format": fmt,
            "ticket_url": f"{ALAMO_BASE}/nyc/show/{s.get('presentationSlug')}",
            "special_event": note or fnote,
            "_first_run": None if rep is None else not rep,
        })
    return rows


def fetch_alamo(today: date, end: date, get=http_get) -> dict[str, list[dict]]:
    schedule = json.loads(get(ALAMO_BASE + "/s/mother/v2/schedule/market/nyc"))
    if not (schedule.get("data") or {}).get("sessions"):
        raise ValueError("empty Alamo schedule")
    try:
        films = (json.loads(get(ALAMO_BASE + "/s/mother/v1/page/market/main/nyc")).get("data") or {}).get("films")
    except Exception as e:
        print(f"[nyc_sources] Alamo film list unavailable ({e}); repertory flags unknown", file=sys.stderr)
        films = None
    return _bucket(parse_alamo(schedule, films), _days(today, end))


# ---- Regal -----------------------------------------------------------------

REGAL_BASE = "https://www.regmovies.com"
# NYC theatres (from regmovies.com/theatres, Nov 2026): code -> (name, page path, borough)
REGAL_THEATRES = {
    "1412": ("Regal Essex Crossing", "regal-essex-crossing-1412", "Manhattan"),
    "1929": ("Regal Times Square", "regal-times-square-1929", "Manhattan"),
    "1335": ("Regal Battery Park", "regal-battery-park-1335", "Manhattan"),
    "1320": ("Regal Union Square", "regal-union-square-screenx-4dx-1320", "Manhattan"),
    "1159": ("Regal UA Sheepshead Bay", "regal-ua-sheepshead-bay-1159", "Brooklyn"),
    "1333": ("Regal UA Kaufman Astoria", "regal-ua-kaufman-astoria-rpx-1333", "Queens"),
    "1472": ("Regal Tangram", "regal-tangram-4dx-1472", "Queens"),
    "1486": ("Regal Concourse", "regal-concourse-1486", "Bronx"),
    "1419": ("Regal Bricktown Charleston", "regal-bricktown-charleston-1419", "Staten Island"),
}
REGAL_DAYS = 45


def parse_regal(payload: dict) -> list[dict]:
    movies = {m.get("MasterMovieCode"): m for m in payload.get("movies") or []}
    rows = []
    for show in payload.get("shows") or []:
        code = str(show.get("TheatreCode"))
        if code not in REGAL_THEATRES:
            continue
        name, path, _ = REGAL_THEATRES[code]
        for film in show.get("Film") or []:
            meta = movies.get(film.get("MasterMovieCode")) or {}
            title, note = clean_title(film.get("Title") or meta.get("Title") or "")
            if not title:
                continue
            year = None
            event_cinema = re.search(r"fathom|trafalgar|anniversary|re-?release",
                                     f"{meta.get('Studio') or ''} {title}", re.I)
            m = re.match(r"(\d{4})", meta.get("OpeningDate") or "")
            if m and not event_cinema:
                year = int(m.group(1))
            directors = meta.get("Directors") or []
            for perf in film.get("Performances") or []:
                when = perf.get("CalendarShowTime") or ""
                if len(when) < 16:
                    continue
                fmt = None
                for attr in perf.get("PerformanceAttributes") or []:
                    f, n = chain_format(attr)
                    fmt = fmt or f
                    note = note or n
                d = when[:10]
                rows.append({
                    "id": f"regal-{code}-{perf.get('PerformanceId')}",
                    "film_title": title, "film_year": year,
                    "film_director": ", ".join(directors) or None, "films": None,
                    "theater_name": name, "theater_slug": path,
                    "date": d, "time": when[11:16], "format": fmt,
                    "ticket_url": f"{REGAL_BASE}/theatres/{path}?date={d[5:7]}-{d[8:10]}-{d[:4]}",
                    "special_event": note,
                    "_first_run": None if event_cinema else True,
                })
    return rows


def fetch_regal(today: date, end: date, get=http_get) -> dict[str, list[dict]]:
    codes = ",".join(REGAL_THEATRES)

    def day(d: date) -> dict:
        url = (f"{REGAL_BASE}/api/getShowtimes?theatres={codes}&date={d.strftime('%m-%d-%Y')}"
               f"&hoCode=&ignoreCache=false&moviesOnly=false")
        return json.loads(get(url))

    first = day(today)
    if "shows" not in first:
        raise ValueError("unexpected Regal response")
    last = min(end, today + timedelta(days=REGAL_DAYS))
    dates = sorted({date.fromisoformat(x[:10]) for x in first.get("datesWithShows") or []})
    checked, rows = [today], parse_regal(first)
    for d in dates:
        if d <= today or d > last:
            continue
        try:
            rows += parse_regal(day(d))
            checked.append(d)
        except Exception as e:
            print(f"[nyc_sources] WARNING: Regal {d} failed: {e}", file=sys.stderr)
    # Days between listed dates had no shows at all: those count as checked too.
    listed = {d for d in dates}
    checked += [d for d in _days(today, last) if d not in listed and d not in checked]
    return _bucket(rows, sorted(set(checked)))


# ---- AMC -------------------------------------------------------------------

AMC_BASE = "https://www.amctheatres.com"
# Manhattan theatres in AMC's "new-york-city" market (Newport Centre, in Jersey
# City, is left out): slug -> name
AMC_THEATRES = {
    "amc-kips-bay-15": "AMC Kips Bay 15",
    "amc-19th-st-east-6": "AMC 19th St. East 6",
    "amc-village-7": "AMC Village 7",
    "amc-34th-street-14": "AMC 34th Street 14",
    "amc-empire-25": "AMC Empire 25",
    "amc-lincoln-square-13": "AMC Lincoln Square 13",
    "amc-orpheum-7": "AMC Orpheum 7",
    "amc-84th-street-6": "AMC 84th Street 6",
    "amc-magic-johnson-harlem-9": "AMC Magic Johnson Harlem 9",
}
AMC_DAYS = 14

_AMC_MOVIE = re.compile(r'\{"name":"((?:[^"\\]|\\.)*)","slug":"([a-z0-9-]+)","movieId":(\d+)\}')
_AMC_SHOWTIME = re.compile(r'"showtimeId":(\d+),(.{0,700}?)"aria-describedby":"([^"]+)"', re.S)


def parse_amc(page: str, theatre: str) -> list[dict]:
    """Showtimes from one AMC theatre-day page."""
    text = page.replace('\\"', '"')
    names = {}
    for m in _AMC_MOVIE.finditer(text):
        try:
            names[m.group(2)] = json.loads(f'"{m.group(1)}"')
        except ValueError:
            names[m.group(2)] = m.group(1)
    rows, seen = [], set()
    for m in _AMC_SHOWTIME.finditer(text):
        sid, body, aria = m.group(1), m.group(2), m.group(3).split()
        if sid in seen or not aria:
            continue
        status = re.search(r'"status":"(\w+)"', body)
        utc = re.search(r'"showDateTimeUtc":"([^"]+)"', body)
        if not utc or (status and status.group(1).lower() in ("canceled", "cancelled")):
            continue
        movie = aria[0]
        if movie not in names:
            continue
        seen.add(sid)
        prefix = f"{movie}-{theatre}-"
        token = next((a[len(prefix):] for a in aria if a.startswith(prefix)), "")
        token = re.sub(r"-\d+$", "", token)
        fmt, note = chain_format(token)
        title, tnote = clean_title(names[movie])
        when = datetime.strptime(utc.group(1)[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).astimezone(NY)
        rows.append({
            "id": f"amc-{sid}",
            "film_title": title, "film_year": None, "film_director": None, "films": None,
            "theater_name": AMC_THEATRES.get(theatre, theatre), "theater_slug": theatre,
            "date": when.date().isoformat(), "time": when.strftime("%H:%M"), "format": fmt,
            "ticket_url": f"{AMC_BASE}/showtimes/{sid}",
            "special_event": tnote or note,
            "_first_run": None,
        })
    return rows


def fetch_amc(today: date, end: date, get=http_get) -> dict[str, list[dict]]:
    last = min(end, today + timedelta(days=AMC_DAYS - 1))
    rows, checked = [], []
    empty_streak = {t: 0 for t in AMC_THEATRES}
    failures = 0
    for d in _days(today, last):
        ok = True
        for t in AMC_THEATRES:
            if empty_streak[t] >= 2:          # this theatre has nothing further out
                continue
            url = f"{AMC_BASE}/movie-theatres/new-york-city/{t}/showtimes?date={d.isoformat()}"
            try:
                got = parse_amc(get(url).decode("utf-8", "replace"), t)
            except Exception as e:
                print(f"[nyc_sources] WARNING: AMC {t} {d} failed: {e}", file=sys.stderr)
                ok = False
                failures += 1
                if failures >= 5 and not rows:
                    raise RuntimeError("AMC unreachable")
                continue
            empty_streak[t] = 0 if got else empty_streak[t] + 1
            rows += got
        if ok:
            checked.append(d)
    if not rows:
        raise ValueError("no AMC showtimes found")
    return _bucket(rows, checked)


# ---- registry --------------------------------------------------------------

# (store source name, fetch function); fetched in this order.
SOURCES = [
    ("bam", fetch_bam),
    ("nitehawk", fetch_nitehawk),
    ("regal", fetch_regal),
    ("alamo", fetch_alamo),
    ("amc", fetch_amc),
]

# Borough of each venue these sources add, for the page's theater filter.
VENUE_BOROUGHS = {
    **{name: borough for name, _, borough in REGAL_THEATRES.values()},
    **{name: "Manhattan" for name in AMC_THEATRES.values()},
    "Alamo Drafthouse Lower Manhattan": "Manhattan",
    "Alamo Drafthouse Downtown Brooklyn": "Brooklyn",
    "Alamo Drafthouse Staten Island": "Staten Island",
}
