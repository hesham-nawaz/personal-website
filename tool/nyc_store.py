"""Keep every NYC screening repertory.nyc has posted, in data/nyc-screenings/.

Each daily run fetches every listed day from today up to the feed's horizon
(theaters post a few weeks to a couple of months ahead) and merges the result
into monthly JSON Lines files, data/nyc-screenings/YYYY-MM.jsonl. One line per
screening, sorted by date, time and venue, so a day's changes show up as a
small line diff in git.

Stored are all screenings, not just watchlist matches, so a film added to the
watchlist later still matches screenings that were posted earlier.

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

from nyc_repertory import API_URL, fetch_day, event_label, format_tag

HORIZON_DAYS = 120         # never look further ahead than this
STOP_AFTER_EMPTY_DAYS = 30  # the feed thins out; stop after a month of nothing
STOP_AFTER_FAILED_DAYS = 5  # the feed is down; don't spend the run retrying

FIELDS = ["id", "date", "time", "title", "year", "director", "theater",
          "theater_slug", "format", "event", "ticket_url",
          "first_seen", "removed"]


def _record(entry: dict, run_day: str) -> dict | None:
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
        "date": entry["date"],
        "time": entry["time"],
        "title": title,
        "year": year,
        "director": (entry.get("film_director") or "").strip() or None,
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


def refresh(store: Path, today: date | None = None, url: str = API_URL,
            fetch=None) -> dict:
    """Fetch today..horizon and merge into the store. Returns a summary.
    Raises RuntimeError, leaving the store untouched, if the feed is down."""
    fetch = fetch or fetch_day
    today = today or date.today()
    run_day = today.isoformat()

    fetched: dict[str, list[dict]] = {}   # iso day -> rows (only successful days)
    failed: list[str] = []
    empty_streak = fail_streak = 0
    for i in range(HORIZON_DAYS + 1):
        day = today + timedelta(days=i)
        try:
            rows = fetch(day, url)
        except Exception as e:  # one bad day shouldn't sink the run
            print(f"[nyc_store] WARNING: fetch failed for {day}: {e}", file=sys.stderr)
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
    last_checked = max(fetched) if fetched else run_day

    if not fetched or fail_streak >= STOP_AFTER_FAILED_DAYS:
        raise RuntimeError(f"feed unavailable ({len(failed)} failed requests, "
                           f"{len(fetched)} days fetched); store left unchanged")

    # Every month the checked window touches, plus any stored months in it.
    months = set()
    d = today
    while d.isoformat() <= last_checked:
        months.add(d.isoformat()[:7])
        d += timedelta(days=28)
    months.add(last_checked[:7])
    records = load_months(store, months)

    added = updated = removed = restored = 0
    for iso, rows in fetched.items():
        listed = {}
        for entry in rows:
            rec = _record(entry, run_day)
            if rec:
                listed[rec["id"]] = rec
        for sid, rec in listed.items():
            old = records.get(sid)
            if old is None:
                records[sid] = rec
                added += 1
                continue
            if old.get("removed"):
                restored += 1
            rec["first_seen"] = old.get("first_seen") or run_day
            if any(old.get(k) != rec.get(k) for k in FIELDS if k not in ("first_seen", "removed")):
                updated += 1
            records[sid] = rec
        # Stored screenings on this day that the feed no longer lists.
        for sid, old in records.items():
            if old["date"] == iso and sid not in listed and not old.get("removed"):
                old["removed"] = run_day
                removed += 1
        # A screening can move to another day under the same id; listed wins.

    _write_months(store, records, months)
    listed_now = sum(1 for r in records.values()
                     if r["date"] >= run_day and not r.get("removed"))
    return {"days_checked": len(fetched), "days_failed": len(failed),
            "through": last_checked, "added": added, "updated": updated,
            "removed": removed, "restored": restored, "listed_from_today": listed_now}


def listed_records(store: Path, today: date) -> list[dict]:
    """Screenings from today on that the feed still lists."""
    return [r for r in load_from(store, today) if not r.get("removed")]


def show_format(raw: str | None) -> str | None:
    return format_tag(raw)
