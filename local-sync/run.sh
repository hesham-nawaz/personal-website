#!/bin/bash
# Daily on Hesham's Mac, run by launchd (me.heshamnawaz.screenings-sync).
# Fetches the sources that refuse GitHub's servers (Screen Slate, Regal,
# AMC) with tool/fetch_local.py and pushes them to data/local-sources/;
# the push starts the "Daily Screenings (NYC)" workflow, which rebuilds the
# page with them. See local-sync/README.md.
set -u
BASE="$HOME/screenings-sync"
REPO="$BASE/repo"
OUT="$BASE/out"
LOG="$BASE/sync.log"
PY=/usr/bin/python3
URL=https://github.com/hesham-nawaz/personal-website.git

mkdir -p "$BASE"
# Keep the log short (before redirecting into it).
if [ -f "$LOG" ] && [ "$(wc -l < "$LOG")" -gt 3000 ]; then
  tail -n 1500 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi
exec >> "$LOG" 2>&1
echo "=== $(date '+%Y-%m-%d %H:%M:%S') ==="

if [ ! -d "$REPO/.git" ]; then
  git clone -q --depth 1 "$URL" "$REPO" || { echo "clone failed"; exit 1; }
fi
cd "$REPO" || exit 1
git fetch -q --depth 1 origin main && git reset -q --hard origin/main || { echo "fetch failed"; exit 1; }

rm -rf "$OUT" && mkdir -p "$OUT"
cp data/nyc-venues.json "$OUT/nyc-venues.json"
"$PY" tool/fetch_local.py --out "$OUT/local-sources" --venues "$OUT/nyc-venues.json"
status=$?

for attempt in 1 2 3; do
  if ! { git fetch -q --depth 1 origin main && git reset -q --hard origin/main; }; then
    sleep 15; continue
  fi
  mkdir -p data/local-sources
  cp "$OUT"/local-sources/*.jsonl data/local-sources/ 2>/dev/null
  cp "$OUT/nyc-venues.json" data/nyc-venues.json
  git add data/local-sources data/nyc-venues.json
  if git diff --cached --quiet; then
    echo "nothing new to push"; exit $status
  fi
  git -c user.name="Hesham Nawaz" -c user.email="heshamnawaz2000@gmail.com" \
    commit -q -m "Mac snapshot: Screen Slate, Regal, AMC ($(date +%F))"
  if git push -q origin HEAD:main; then
    echo "pushed $(git rev-parse --short HEAD)"; exit $status
  fi
  echo "push attempt $attempt failed"; sleep 15
done
exit 1
