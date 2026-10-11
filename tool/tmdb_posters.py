"""Film posters for the screenings page, from TMDB (The Movie Database).

For each film on the page, look up its TMDB entry by title and year and use
TMDB's poster image (served from image.tmdb.org; nothing is downloaded or
committed). Needs a TMDB API Read Access Token in the TMDB_TOKEN environment
variable (a GitHub Actions secret); without one, the page simply has no
posters.

TMDB's API terms (https://www.themoviedb.org/api-terms-of-use), as they
shape this module:
  - non-commercial use only (this page is private and unmonetized);
  - the page must show TMDB's logo and the notice "This website uses TMDB
    and the TMDB APIs but is not endorsed, certified, or otherwise approved
    by TMDB." (render.py adds both when there are posters);
  - nothing from TMDB may be cached for more than 6 months: lookups are
    kept in data/tmdb-posters.json with the date they were made, redone
    after REFRESH_DAYS, and dropped once that old if no longer needed.

Stdlib only.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

API = "https://api.themoviedb.org/3"
LOGO_URL = ("https://www.themoviedb.org/assets/v4/logos/v2/"
            "blue_short-8e7b30f73a4020692ccca9c88bafe5dcb6f8a62a4c6bc55cd9ba82bb2cd95f6c.svg")
REFRESH_DAYS = 150          # well inside TMDB's 6-month caching limit
POSTER_SIZE = "w154"
MAX_LOOKUPS_PER_RUN = 200   # a first run looks up every film on the page
PAUSE_SECONDS = 0.05


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = re.sub(r"^(the|a|an)\s+", "", t)
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


class TMDB:
    def __init__(self, token: str, opener=None):
        self.token = token
        self.opener = opener or self._open

    def _open(self, url: str):
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.token}",
                                                   "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
        time.sleep(PAUSE_SECONDS)
        return data

    def image_base(self) -> str:
        cfg = self.opener(f"{API}/configuration")
        images = cfg.get("images") or {}
        base = images.get("secure_base_url") or "https://image.tmdb.org/t/p/"
        sizes = images.get("poster_sizes") or []
        size = POSTER_SIZE if (not sizes or POSTER_SIZE in sizes) else sizes[min(1, len(sizes) - 1)]
        return base + size

    def find(self, title: str, year: int | None) -> dict | None:
        """Best TMDB match for a film: same title (ignoring case, accents and
        a leading article) and a release year within one of `year`; failing
        that, the top result if its year fits."""
        q = {"query": title, "include_adult": "false"}
        if year:
            q["year"] = str(year)
        results = (self.opener(f"{API}/search/movie?{urllib.parse.urlencode(q)}") or {}).get("results") or []
        if not results and year:
            q.pop("year")
            results = (self.opener(f"{API}/search/movie?{urllib.parse.urlencode(q)}") or {}).get("results") or []

        def year_ok(r):
            y = (r.get("release_date") or "")[:4]
            return not year or (y.isdigit() and abs(int(y) - year) <= 1)

        want = _norm(title)
        for r in results:
            if year_ok(r) and want in (_norm(r.get("title")), _norm(r.get("original_title"))):
                return r
        for r in results[:1]:
            if year and year_ok(r):
                return r
        return None


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def attach_posters(matches, cache_path: Path, today: date | None = None,
                   token: str | None = None, tmdb: TMDB | None = None) -> dict:
    """Set `poster_url` on each matched screening whose (first matched) film
    has a TMDB poster. Returns a summary. Does nothing without a token."""
    token = token if token is not None else os.environ.get("TMDB_TOKEN", "").strip()
    if not token and tmdb is None:
        return {"posters": "no TMDB_TOKEN; skipped"}
    tmdb = tmdb or TMDB(token)
    today = today or date.today()
    cache = _load(cache_path)
    stale_before = (today - timedelta(days=REFRESH_DAYS)).isoformat()

    films: dict[str, tuple[str, int | None]] = {}
    for ms in matches:
        for f, m in zip(ms.screening.films, ms.matches):
            if m is not None and m.letterboxd_url:
                films.setdefault(m.letterboxd_url, (m.title, m.year or f.year))
                break

    looked_up = failed = 0
    try:
        base = tmdb.image_base()
    except Exception as e:
        print(f"[tmdb] configuration unavailable ({e}); posters skipped", file=sys.stderr)
        return {"posters": "TMDB unavailable"}
    for key, (title, year) in sorted(films.items()):
        entry = cache.get(key)
        if entry and entry.get("checked", "") >= stale_before:
            continue
        if looked_up >= MAX_LOOKUPS_PER_RUN:
            break
        try:
            r = tmdb.find(title, year)
        except Exception as e:
            print(f"[tmdb] lookup failed for {title} ({year}): {e}", file=sys.stderr)
            failed += 1
            continue
        looked_up += 1
        cache[key] = {"title": title, "year": year, "checked": today.isoformat(),
                      "tmdb_id": r.get("id") if r else None,
                      "poster_path": r.get("poster_path") if r else None}

    # Drop lookups that are both unused and too old to keep.
    for key in [k for k, v in cache.items() if k not in films and v.get("checked", "") < stale_before]:
        del cache[key]
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, indent=1, sort_keys=True, ensure_ascii=False) + "\n")

    with_poster = 0
    for ms in matches:
        for f, m in zip(ms.screening.films, ms.matches):
            if m is not None:
                path = (cache.get(m.letterboxd_url) or {}).get("poster_path")
                if path:
                    ms.poster_url = base + path
                    with_poster += 1
                break
    return {"posters": f"{sum(1 for k in films if (cache.get(k) or {}).get('poster_path'))} of "
                       f"{len(films)} films ({looked_up} looked up, {failed} failed)"}


def ensure_logo(path: Path, opener=None) -> bool:
    """Download TMDB's logo once (from its logos page) for the credit line."""
    if path.exists():
        return True
    try:
        if opener:
            data = opener(LOGO_URL)
        else:
            with urllib.request.urlopen(urllib.request.Request(LOGO_URL, headers={"User-Agent": "Mozilla/5.0"}),
                                        timeout=20) as resp:
                data = resp.read()
        if b"<svg" not in data[:2000]:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return True
    except Exception as e:
        print(f"[tmdb] logo download failed: {e}", file=sys.stderr)
        return False
