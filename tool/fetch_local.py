"""Fetch the sources that refuse GitHub's servers, from Hesham's Mac.

Screen Slate, Regal and AMC answer 403 to requests from GitHub Actions
(Nov 2026) but load normally from a home connection. A launchd job on the
Mac (see local-sync/ in this repo) runs this script every morning and pushes
what it fetched to data/local-sources/; the daily workflow then uses those
snapshots instead of fetching these sources itself (nyc_store.load_snapshot),
as long as they are fresh.

Each snapshot is JSON Lines: a first line {"meta": {...}} with the source
name, when it was fetched and the days it fully checked, then one row per
screening in repertory.nyc's API row shape. A source that fails keeps its
previous snapshot. For Screen Slate, venues new to data/nyc-venues.json get
their address looked up here too, since GitHub can't reach Screen Slate.

Usage (from the repo root):
    python3 tool/fetch_local.py --out data/local-sources
    python3 tool/fetch_local.py --out /tmp/snapshots --only regal
Stdlib only; runs on macOS's own python3 (3.9+).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import nyc_screenslate  # noqa: E402
import nyc_sources  # noqa: E402
from nyc_store import HORIZON_DAYS, _fetch_days  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def write_snapshot(out_dir: Path, name: str, days: dict, now: datetime) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = sorted((r for rs in days.values() for r in rs),
                  key=lambda r: (r["date"], r["time"], r.get("theater_name") or "", r["film_title"], r["id"]))
    meta = {"source": name, "fetched_at": now.isoformat(timespec="seconds"),
            "checked_days": sorted(days)}
    text = json.dumps({"meta": meta}) + "\n" + "".join(
        json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows)
    tmp = out_dir / f"{name}.jsonl.tmp"
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(out_dir / f"{name}.jsonl")
    return len(rows)


def fetch_screenslate(today: date, venues_path: Path | None) -> dict:
    ss = nyc_screenslate.ScreenSlate(set())
    days, failed, down = _fetch_days(ss.fetch_day, today, None, "Screen Slate")
    if down:
        raise RuntimeError(f"Screen Slate unavailable ({len(failed)} failed days)")
    if venues_path is not None:
        nyc_screenslate.update_venues(venues_path, [r for rs in days.values() for r in rs], get=ss.get_page)
    return days


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True, help="Directory for the snapshots (data/local-sources)")
    ap.add_argument("--venues", default=str(ROOT / "data" / "nyc-venues.json"),
                    help="Venue registry to add new Screen Slate venues to ('' to skip)")
    ap.add_argument("--only", action="append", choices=["screenslate", "regal", "amc"],
                    help="Fetch just these sources (repeatable)")
    ap.add_argument("--today", default=None, help="Treat this date (YYYY-MM-DD) as today")
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    today = date.fromisoformat(args.today) if args.today else datetime.now(nyc_sources.NY).date()
    end = today + timedelta(days=HORIZON_DAYS)
    out = Path(args.out)
    fetchers = {
        "screenslate": lambda: fetch_screenslate(today, Path(args.venues) if args.venues else None),
        "regal": lambda: nyc_sources.fetch_regal(today, end),
        "amc": lambda: nyc_sources.fetch_amc(today, end),
    }
    failures = 0
    for name in args.only or list(fetchers):
        try:
            n = write_snapshot(out, name, fetchers[name](), now)
            print(f"[fetch_local] {name}: {n} screenings")
        except Exception as e:
            failures += 1
            print(f"[fetch_local] {name} failed ({e}); keeping its previous snapshot", file=sys.stderr)
    return 1 if failures == len(args.only or fetchers) else 0


if __name__ == "__main__":
    sys.exit(main())
