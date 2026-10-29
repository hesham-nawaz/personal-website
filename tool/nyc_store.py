"""Keep every NYC screening repertory.nyc has posted, in data/nyc-screenings/.

Each daily run fetches every listed day from today up to the feed's horizon
(theaters post a few weeks to a couple of months ahead) and merges the result
into monthly JSON Lines files, data/nyc-screenings/YYYY-MM.jsonl. One line per
screening, sorted by date, time and venue, so a day's changes show up as a
small line diff in git.

Stored are all screenings, not just watchlist matches, so a film added to the
watchlist later still matches screenings that were posted earlier.

Sources: repertory.nyc (11 venues), the Museum of the Moving Image's own
calendar (nyc_momi.py), and Screen Slate for the venues neither covers
(nyc_screenslate.py). Each record names its source, and a source's run
only ever marks its own screenings as removed, so one source being down or
missing a day never touches the other's listings. If MoMI's calendar or
Screen Slate can't be reached, their stored screenings are left as they were;
only repertory.nyc being down stops the run.

Bookkeeping per screening:
  first_seen  the run date it first appeared in the feed
  removed     the run date it disappeared from a day the feed returned
              successfully (a cancellation or a correction); null while listed.
              It is cleared if the screening comes back.
There is deliberately no "last seen" date: it would change every line every
day, and the daily commit would rewrite the whole store instead of only the
screenings that changed.
Days before today are never re-fetched, so past screenings stay as they were
last seen.

Stdlib only.
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import nyc_momi
import nyc_screenslate
from nyc_repertory import API_URL, fetch_day, event_label, format_tag

HORIZON_DAYS = 120         # never look further ahead than this
STOP_AFTER_EMPTY_DAYS = 30  # the feed thins out; stop after a month of nothing
STOP_AFTER_FAILED_DAYS = 5  # the feed is down; don't spend the run retrying

FIELDS = ["id", "source", "date", "time", "title", "year", "director", "films", "theater",
          "theater_slug", "format", "event", "ticket_url",
          "first_seen", "removed"]
REPERTORY = "repertory.nyc"
MOMI = "momi"
SCREENSLATE = "screenslate"


def _source(r: dict) -> str:
    return r.get("source") or REPERTORY      # records stored before sources existed


def _record(entry: dict, run_day: str, source: str = REPERTORY) -> dict | None:
    """Compact stored form of one API row (drops posters, runtimes, ids of
    nested objects). Returns None for rows missing what we need."""
    sid = entry.get("id")
    title = (entry.get("film_title") or "").strip()
    if not sid or not title or not entry.get("date") or not entry.get("time"):
        return None
    year = entry.get("film_year")
    try:
        year = int(year) if year is not None else None
    except (TypeError, ValueError):
        year = None
    ticket = (entry.get("ticket_url") or "").strip() or None
    return {
        "id": sid,
        "source": source,
        "date": entry["date"],
        "time": entry["time"],
        "title": title,
        "year": year,
        "director": (entry.get("film_director") or "").strip() or None,
        # multi-film programs: [{title, year, director, format}, ...]
        "films": entry.get("films") or None,
        "theater": (entry.get("theater_name") or "").strip(),
        "theater_slug": entry.get("theater_slug"),
        # raw format as posted; display filtering (hiding DCP etc.) happens later
        "format": (str(entry["format"]).strip() if entry.get("format") else None),
        "event": event_label(entry.get("special_event")),
        "ticket_url": ticket,
        "first_seen": run_day,
        "removed": None,
    }


def _month_path(store: Path, iso_day: str) -> Path:
    return store / f"{iso_day[:7]}.jsonl"


def load_months(store: Path, months: set[str]) -> dict[str, dict]:
    """Records from the given YYYY-MM files, keyed by screening id."""
    out: dict[str, dict] = {}
    for m in sorted(months):
        p = store / f"{m}.jsonl"
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                r = json.loads(line)
                out[r["id"]] = r
    return out


def load_from(store: Path, start: date) -> list[dict]:
    """All stored records dated on or after `start`."""
    months = {p.stem for p in store.glob("*.jsonl") if p.stem >= start.isoformat()[:7]}
    return [r for r in load_months(store, months).values() if r["date"] >= start.isoformat()]


def _write_months(store: Path, records: dict[str, dict], months: set[str]) -> None:
    store.mkdir(parents=True, exist_ok=True)
    by_month: dict[str, list[dict]] = {m: [] for m in months}
    for r in records.values():
        m = r["date"][:7]
        if m in by_month:
            by_month[m].append(r)
    for m, rows in by_month.items():
        p = store / f"{m}.jsonl"
        if not rows:
            if p.exists():
                p.unlink()
            continue
        rows.sort(key=lambda r: (r["date"], r["time"], r["theater"].lower(), r["title"].lower(), r["id"]))
        text = "".join(json.dumps({k: r.get(k) for k in FIELDS}, ensure_ascii=False,
                                  separators=(",", ":")) + "\n" for r in rows)
        tmp = p.with_suffix(".jsonl.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(p)


def _merge(records: dict[str, dict], fetched: dict[str, list[dict]], source: str,
           run_day: str, counts: dict) -> None:
    """Merge one source's listings for the days it returned. Screenings of
    that source stored on those days but no longer listed are marked
    removed; other sources' screenings are left alone."""
    for iso, rows in fetched.items():
        listed = {}
        for entry in rows:
            rec = _record(entry, run_day, source)
            if rec:
                listed[rec["id"]] = rec
        for sid, rec in listed.items():
            old = records.get(sid)
            if old is None:
                records[sid] = rec
                counts["added"] += 1
                continue
            if old.get("removed"):
                counts["restored"] += 1
            rec["first_seen"] = old.get("first_seen") or run_day
            if any(old.get(k) != rec.get(k) for k in FIELDS if k not in ("first_seen", "removed", "source")):
                counts["updated"] += 1
            records[sid] = rec
        for sid, old in records.items():
            if (old["date"] == iso and _source(old) == source and sid not in listed
                    and not old.get("removed")):
                old["removed"] = run_day
                counts["removed"] += 1
        # A screening can move to another day under the same id; listed wins.


def _fetch_days(fetch, today: date, url, label: str):
    """Fetch day by day from today until a month of empty days or the
    horizon. Returns (rows by iso day for the days that worked, failed days,
    whether the source looks down)."""
    fetched: dict[str, list[dict]] = {}   # iso day -> rows (only successful days)
    failed: list[str] = []
    empty_streak = fail_streak = 0
    for i in range(HORIZON_DAYS + 1):
        day = today + timedelta(days=i)
        try:
            rows = fetch(day, url)
        except Exception as e:  # one bad day shouldn't sink the run
            print(f"[nyc_store] WARNING: {label} fetch failed for {day}: {e}", file=sys.stderr)
            failed.append(day.isoformat())
            empty_streak = 0
            fail_streak += 1
            if fail_streak >= STOP_AFTER_FAILED_DAYS:
                break
            continue
        fail_streak = 0
        rows = [r for r in rows if r.get("date") == day.isoformat()]
        fetched[day.isoformat()] = rows
        empty_streak = 0 if rows else empty_streak + 1
        if empty_streak >= STOP_AFTER_EMPTY_DAYS:
            break
    return fetched, failed, (not fetched or fail_streak >= STOP_AFTER_FAILED_DAYS)


def refresh(store: Path, today: date | None = None, url: str = API_URL,
            fetch=None, momi_get_json=None, screenslate=None) -> dict:
    """Fetch today..horizon from every source and merge into the store.
    Returns a summary. Raises RuntimeError, leaving the store untouched, if
    repertory.nyc (the main source) is down."""
    fetch = fetch or fetch_day
    today = today or date.today()
    run_day = today.isoformat()

    fetched, failed, down = _fetch_days(fetch, today, url, "repertory.nyc")
    last_checked = max(fetched) if fetched else run_day
    if down:
        raise RuntimeError(f"feed unavailable ({len(failed)} failed requests, "
                           f"{len(fetched)} days fetched); store left unchanged")

    # MoMI: one query for the whole window. Every day in the window counts
    # as checked, so a screening the museum drops is marked removed.
    horizon_end = today + timedelta(days=HORIZON_DAYS)
    momi_fetched: dict[str, list[dict]] | None = None
    try:
        rows = nyc_momi.fetch_screenings(today, horizon_end, **({"get_json": momi_get_json} if momi_get_json else {}))
        momi_fetched = {(today + timedelta(days=i)).isoformat(): [] for i in range(HORIZON_DAYS + 1)}
        for r in rows:
            if r["date"] in momi_fetched:
                momi_fetched[r["date"]].append(r)
    except Exception as e:  # MoMI down: keep what we have from it
        print(f"[nyc_store] WARNING: Museum of the Moving Image calendar unavailable ({e}); "
              f"keeping its stored screenings", file=sys.stderr)

    # Screen Slate, for the venues the other two don't cover.
    seen = {r.get("theater_name") for rows in fetched.values() for r in rows}
    ss = screenslate or nyc_screenslate.ScreenSlate(seen)
    ss_fetched, _, ss_down = _fetch_days(ss.fetch_day, today, None, "Screen Slate")
    if ss_down:
        print("[nyc_store] WARNING: Screen Slate unavailable; keeping its stored screenings", file=sys.stderr)
        ss_fetched = None
    else:
        nyc_screenslate.update_venues(store.parent / "nyc-venues.json",
                                      [r for rows in ss_fetched.values() for r in rows],
                                      **({"get": ss.get_page} if hasattr(ss, "get_page") else {}))

    # Every month the checked window touches.
    months = set()
    d = today
    while d <= horizon_end:
        months.add(d.isoformat()[:7])
        d += timedelta(days=28)
    months.add(horizon_end.isoformat()[:7])
    records = load_months(store, months)

    counts = {"added": 0, "updated": 0, "removed": 0, "restored": 0}
    _merge(records, fetched, REPERTORY, run_day, counts)
    if momi_fetched is not None:
        _merge(records, momi_fetched, MOMI, run_day, counts)
    if ss_fetched is not None:
        _merge(records, ss_fetched, SCREENSLATE, run_day, counts)

    _write_months(store, records, months)
    listed_now = sum(1 for r in records.values()
                     if r["date"] >= run_day and not r.get("removed"))
    momi_listed = sum(1 for r in records.values()
                      if _source(r) == MOMI and r["date"] >= run_day and not r.get("removed"))
    return {"days_checked": len(fetched), "days_failed": len(failed),
            "through": last_checked, **counts, "listed_from_today": listed_now,
            "momi": "unavailable" if momi_fetched is None else momi_listed,
            "screenslate": "unavailable" if ss_fetched is None else sum(
                1 for r in records.values()
                if _source(r) == SCREENSLATE and r["date"] >= run_day and not r.get("removed"))}


# When two records describe the same screening, the venue's own calendar
# wins over an aggregator (it has the format, and its links are canonical).
_SOURCE_RANK = {MOMI: 0, REPERTORY: 1, SCREENSLATE: 2}
# Fields a winning record may be missing and borrow from the duplicates.
_FILLABLE = ("year", "director", "films", "format", "event", "ticket_url", "theater_slug")
_DETAIL = ("year", "director", "format", "event", "ticket_url")


def _posted(r: dict) -> int:
    try:
        return date.fromisoformat(r.get("first_seen") or "").toordinal()
    except ValueError:
        return 0


def dedupe_records(records: list[dict]) -> list[dict]:
    """Collapse records that are the same screening: same day, same time,
    same venue, same title once normalized ("The\\xa0Vourdalak" and "The
    Vourdalak" are one film). This happens two ways: repertory.nyc started
    covering the Museum of the Moving Image (2026-10-19) on top of our own
    MoMI source, and repertory.nyc sometimes posts one screening twice under
    different ids (a stale row next to a corrected one).

    The store keeps every source's rows as posted (so each source can still
    mark only its own screenings removed); this runs when the page is built.
    One record per screening is kept: the venue's own source first, then the
    record with the most details, then the most recently posted. Details it
    lacks (year, director, format, ticket link...) are taken from the rest.
    """
    from watchlist import normalize_title

    groups: dict[tuple, list[dict]] = {}
    for r in records:
        key = (r["date"], r["time"], (r.get("theater") or "").strip().casefold(),
               normalize_title(r.get("title") or ""))
        groups.setdefault(key, []).append(r)

    out = []
    for rows in groups.values():
        if len(rows) == 1:
            out.append(rows[0])
            continue
        rows = sorted(rows, key=lambda r: (
            _SOURCE_RANK.get(_source(r), 1),
            -sum(r.get(k) is not None for k in _DETAIL),
            -_posted(r),
            r["id"]))
        best = dict(rows[0])
        for k in _FILLABLE:
            if best.get(k) is None:
                best[k] = next((r[k] for r in rows[1:] if r.get(k) is not None), None)
        out.append(best)
    return out


def listed_records(store: Path, today: date) -> list[dict]:
    """Screenings from today on that the feed still lists, one record per
    screening (see dedupe_records)."""
    return dedupe_records([r for r in load_from(store, today) if not r.get("removed")])


def show_format(raw: str | None) -> str | None:
    return format_tag(raw)
