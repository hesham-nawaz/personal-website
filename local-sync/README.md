# Mac sync for the NYC screenings page

Screen Slate, Regal and AMC refuse requests from GitHub's servers (403,
Nov 2026), so the daily workflow can't fetch them. A launchd job on
Hesham's Mac fetches them from his home connection each morning with
`tool/fetch_local.py` and pushes the results to `data/local-sources/`. That
push starts the "Daily Screenings (NYC)" workflow, which uses any snapshot
up to 36 hours old in place of fetching that source itself.

Files:
- `run.sh`: the job (keeps its own shallow clone in `~/screenings-sync/repo`,
  logs to `~/screenings-sync/sync.log`).
- `me.heshamnawaz.screenings-sync.plist`: the launchd schedule (6:23 AM daily;
  runs on wake if the Mac was asleep).
- `install.sh`: one-time setup; also runs the job once.

Install (copies of these files live in `~/Desktop/Website/screenings-sync/`):

    bash ~/Desktop/Website/screenings-sync/install.sh

Check on it: `tail ~/screenings-sync/sync.log`. Run it by hand:
`launchctl kickstart gui/$(id -u)/me.heshamnawaz.screenings-sync`.
Remove it: `launchctl bootout gui/$(id -u)/me.heshamnawaz.screenings-sync`
and delete `~/Library/LaunchAgents/me.heshamnawaz.screenings-sync.plist`.

Pushing uses the Mac's own git credentials (macOS keychain).
