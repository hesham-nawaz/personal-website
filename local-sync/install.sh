#!/bin/bash
# One-time setup on Hesham's Mac. In Terminal:
#   bash ~/Desktop/Website/screenings-sync/install.sh
# Copies run.sh to ~/screenings-sync/ (outside Desktop, which background
# jobs can't read), schedules it with launchd, and runs it once now.
set -e
SRC="$(cd "$(dirname "$0")" && pwd)"
BASE="$HOME/screenings-sync"
LABEL=me.heshamnawaz.screenings-sync
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

mkdir -p "$BASE" "$HOME/Library/LaunchAgents"
cp "$SRC/run.sh" "$BASE/run.sh"
chmod +x "$BASE/run.sh"
cp "$SRC/$LABEL.plist" "$PLIST"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Scheduled: every morning at 6:23 (or on wake)."
echo "Running the first sync now; this takes 3-5 minutes..."
bash "$BASE/run.sh" || true
tail -n 40 "$BASE/sync.log" | tee "$SRC/first-run.log"
