"""Render a list of matched screenings as a single-file HTML report."""
from __future__ import annotations

import hashlib
import html
import re
from collections import defaultdict
from dataclasses import dataclass
from urllib.parse import quote_plus
from datetime import date, timedelta
from pathlib import Path

from match import MatchedScreening
from watchlist import normalize_title


CSS = """
:root {
  --fg: #1a1a1a;
  --muted: #6b6b6b;
  --accent: #0a6b38;
  --card-bg: #fafafa;
  --border: #e2e2e2;
  --highlight: #fff7cc;
}
* { box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  color: var(--fg);
  max-width: 900px;
  margin: 2rem auto;
  padding: 0 1rem;
  line-height: 1.45;
}
h1 { font-size: 1.7rem; margin-bottom: 0.2rem; }
.summary { color: var(--muted); margin-bottom: 2rem; }
h2 {
  font-size: 1.2rem;
  margin-top: 2.2rem;
  margin-bottom: 0.6rem;
  padding-bottom: 0.3rem;
  border-bottom: 2px solid var(--accent);
}
.screening {
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0.8rem 1rem;
  margin-bottom: 0.7rem;
}
.screening .title { font-weight: 600; font-size: 1.05rem; }
.screening .title a { color: var(--accent); text-decoration: none; }
.screening .title a:hover { text-decoration: underline; }
.screening .meta { color: var(--muted); font-size: 0.92rem; margin-top: 0.2rem; }
.screening .times { margin-top: 0.25rem; font-variant-numeric: tabular-nums; }
.screening .notes {
  margin-top: 0.35rem;
  font-size: 0.88rem;
  background: var(--highlight);
  padding: 0.35rem 0.55rem;
  border-radius: 4px;
  display: inline-block;
}
.screening.double-feature .title { display: block; }
.screening .unmatched { opacity: 0.5; font-weight: 400; }
footer { color: var(--muted); font-size: 0.85rem; margin-top: 3rem; text-align: center; }
"""


def _film_title_html(film, matched, extra_films) -> str:
    """Render a single film title, linked to Letterboxd if matched."""
    title = html.escape(film.title)
    year = f" ({film.year})" if film.year else ""
    if matched is not None:
        url = html.escape(matched.letterboxd_url)
        return f'<a href="{url}" target="_blank" rel="noopener">{title}</a>{html.escape(year)}'
    return f'<span class="unmatched">{title}{html.escape(year)}</span>'


def render_report(matches: list[MatchedScreening], week_label: str | None = None) -> str:
    """Render the matched screenings as HTML."""
    # Group by day
    by_day: dict[date, list[MatchedScreening]] = defaultdict(list)
    for ms in matches:
        by_day[ms.screening.day].append(ms)
    for day in by_day:
        by_day[day].sort(key=lambda m: _sort_key(m.screening.times))

    days = sorted(by_day.keys())

    total = len(matches)
    movie_count = sum(len(ms.matched_films) for ms in matches)

    if week_label is None and days:
        week_label = f"{days[0].strftime('%b %-d')} – {days[-1].strftime('%b %-d, %Y')}"

    parts: list[str] = []
    parts.append("<!doctype html>")
    parts.append('<html lang="en"><head>')
    parts.append('<meta charset="utf-8">')
    parts.append(f"<title>Watchlist screenings — {html.escape(week_label or '')}</title>")
    parts.append(f"<style>{CSS}</style>")
    parts.append("</head><body>")
    parts.append(f"<h1>Your watchlist, playing this week</h1>")
    parts.append(
        f'<div class="summary">{total} screenings '
        f'({movie_count} matched title{"s" if movie_count != 1 else ""}) '
        f'— {html.escape(week_label or "")}</div>'
    )

    for day in days:
        header = day.strftime("%A, %B %-d")
        parts.append(f"<h2>{html.escape(header)}</h2>")
        for ms in by_day[day]:
            parts.append(_render_screening(ms))

    parts.append('<footer>Generated from Revival Hub LA weekly email &times; your Letterboxd watchlist.</footer>')
    parts.append("</body></html>")
    return "\n".join(parts)


def _sort_key(times: list[str]) -> tuple:
    # Sort "All Day" first, then by first numeric time.
    t = times[0] if times else ""
    if t == "All Day":
        return (0, 0, 0)
    # Parse "H:MMa" or "H:MMp"
    try:
        period = t[-1]
        hm = t[:-1]
        h, m = hm.split(":")
        h = int(h)
        m = int(m)
        if period == "p" and h != 12:
            h += 12
        if period == "a" and h == 12:
            h = 0
        return (1, h, m)
    except Exception:
        return (2, 0, 0)


def _render_screening(ms: MatchedScreening) -> str:
    scr = ms.screening

    # Title block
    film_html_parts = []
    for film, match in zip(scr.films, ms.matches):
        film_html_parts.append(_film_title_html(film, match, scr.films))
    title_html = " / ".join(film_html_parts)

    times = ", ".join(scr.times)

    # Director/theater meta
    meta_bits = []
    if scr.directors:
        label = "Dir." if len(scr.directors) == 1 else "Dirs."
        meta_bits.append(f"{label} {html.escape(', '.join(scr.directors))}")
    if scr.theater:
        meta_bits.append(html.escape(scr.theater))
    if scr.presenter:
        meta_bits.append(f"pres. by {html.escape(scr.presenter)}")
    meta = " &bull; ".join(meta_bits)

    klass = "screening"
    if len(scr.films) > 1:
        klass += " double-feature"

    lines = [f'<div class="{klass}">']
    lines.append(f'  <div class="title">{title_html}</div>')
    if meta:
        lines.append(f'  <div class="meta">{meta}</div>')
    lines.append(f'  <div class="times">{html.escape(times)}</div>')
    if scr.notes:
        lines.append(f'  <div class="notes">{html.escape(scr.notes)}</div>')
    lines.append("</div>")
    return "\n".join(lines)


def write_report(matches: list[MatchedScreening], out_path: str | Path,
                 week_label: str | None = None) -> Path:
    html_text = render_report(matches, week_label=week_label)
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(html_text, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Site fragment rendering: HTML keyed to personal-website's styles.css
# ---------------------------------------------------------------------------

SITE_FRAGMENT_START = "<!-- SCREENINGS:START -->"
SITE_FRAGMENT_END = "<!-- SCREENINGS:END -->"


def _film_key(ms: MatchedScreening) -> tuple:
    """Identify the film(s) a screening shows, so listings of the same film
    at different venues (or under slightly different listing titles, like
    "Fjord" and "Fjord (Open Captioning)") land in one card. Matched films
    are keyed by their Letterboxd URL; unmatched ones by title and year."""
    key = []
    for film, match in zip(ms.screening.films, ms.matches):
        if match is not None and match.letterboxd_url:
            key.append(("lb", match.letterboxd_url))
        else:
            key.append(("t", normalize_title(film.title), film.year))
    return tuple(key)


def _group_by_film(day_matches: list[MatchedScreening]) -> list[list[MatchedScreening]]:
    """Group one day's screenings by film. Groups are ordered by their earliest
    showtime, and venues within a group likewise."""
    groups: dict[tuple, list[MatchedScreening]] = {}
    for ms in day_matches:
        groups.setdefault(_film_key(ms), []).append(ms)
    out = []
    for items in groups.values():
        items.sort(key=lambda m: (_sort_key(m.screening.times), m.screening.theater.lower()))
        out.append(items)
    out.sort(key=lambda g: _sort_key(g[0].screening.times))
    return out


def _canonical_title_html(ms: MatchedScreening) -> str:
    """Card heading: the Letterboxd title and year for matched films (clean and
    consistent across venues), the listing title for unmatched ones."""
    parts = []
    for film, match in zip(ms.screening.films, ms.matches):
        if match is not None:
            title = match.title
            year = match.year if match.year is not None else film.year
            url = html.escape(match.letterboxd_url)
            yr = f" ({year})" if year else ""
            parts.append(f'<a href="{url}" target="_blank" rel="noopener">'
                         f'{html.escape(title)}</a>{html.escape(yr)}')
        else:
            yr = f" ({film.year})" if film.year else ""
            parts.append(f'<span class="unmatched">{html.escape(film.title)}{html.escape(yr)}</span>')
    return " / ".join(parts)


def _listed_as(ms: MatchedScreening) -> str | None:
    """The venue's own listing title, when it carries more than the film's
    name (e.g. "Ghost in the Shell: 30th Anniversary Remaster", "... + Q&A")."""
    differs = False
    for film, match in zip(ms.screening.films, ms.matches):
        if match is not None and normalize_title(film.title) != normalize_title(match.title):
            differs = True
    if not differs:
        return None
    return " / ".join(f.title for f in ms.screening.films)


@dataclass
class SiteWeek:
    """One selectable week on a screenings page. The current week starts
    today; later weeks run Monday to Sunday."""
    start: date
    end: date
    matches: list[MatchedScreening]
    is_current: bool = False


def _range_label(start: date, end: date) -> str:
    """'Oct 12 – 18', 'Oct 26 – Nov 1', or 'Sun, Oct 11' for a single day."""
    if start == end:
        return start.strftime("%a, %b %-d")
    if start.month == end.month:
        return f"{start.strftime('%b %-d')} – {end.strftime('%-d')}"
    return f"{start.strftime('%b %-d')} – {end.strftime('%b %-d')}"


def render_site_fragment(matches: list[MatchedScreening],
                         week_label: str | None = None,
                         updated_iso: str | None = None,
                         weeks: list[SiteWeek] | None = None,
                         map_query=None) -> str:
    """Render screenings as a site-page fragment.

    Output uses CSS classes defined in personal-website/styles.css and is
    spliced into a page between SCREENINGS:START / SCREENINGS:END markers.

    With `weeks` (the NYC page), the page holds every listed day once, under
    a strip of day tiles grouped by week; the browser shows whatever the
    visitor picks (a week, a day, or a range of days from one tap to a
    second), opening on the first week. Without `weeks`, `matches` is shown
    as one block with no strip (the LA page).

    Two views of the selection: by day (default), one card per film per day
    with a row per venue; and by movie, one card per film for the whole
    selection. `map_query(venue)` returns a Google Maps search for a venue,
    which turns venue names into map links; showtimes link to their ticket
    pages when the data has them.
    """
    strip = weeks is not None
    if weeks is None:
        days = sorted({m.screening.day for m in matches})
        start = days[0] if days else date.today()
        weeks = [SiteWeek(start=start, end=days[-1] if days else start,
                          matches=matches, is_current=True)]
    all_matches = [m for w in weeks for m in w.matches]
    first = weeks[0]

    parts: list[str] = ['<section class="screenings-section">']
    if updated_iso:
        parts.append(f'  <p class="screenings-updated">Last updated {html.escape(updated_iso)}</p>')

    # Controls are hidden until the script runs; without JavaScript the page
    # shows the first week by day.
    parts.append('  <div class="screenings-controls" hidden>')
    if strip:
        parts.append(_render_date_strip(weeks))
        parts.append('    <p class="strip-selection" aria-live="polite"></p>')
    parts.append('    <div class="screenings-controls-row">')
    parts.append('      <div class="screenings-view-toggle" role="group" aria-label="Organize screenings">')
    parts.append('        <span class="view-toggle-label">Organize by</span>')
    parts.append('        <button type="button" data-view="day" aria-pressed="true">Day</button>')
    parts.append('        <button type="button" data-view="movie" aria-pressed="false">Movie</button>')
    parts.append('      </div>')
    if strip:
        parts.append('      <button type="button" class="weekend-toggle" aria-pressed="false">'
                     '<span class="weekend-box" aria-hidden="true"></span>Weekends only '
                     '<span class="weekend-days">(Fri–Sun)</span></button>')
    has_links = any(u for m in all_matches for u in m.screening.ticket_urls)
    if has_links or map_query:
        bits = []
        if has_links:
            bits.append('<span class="hint-item"><i class="fa-solid fa-ticket" aria-hidden="true"></i> '
                        'Pick a showtime for tickets</span>')
        if map_query:
            bits.append('<span class="hint-item"><i class="fa-solid fa-location-dot" aria-hidden="true"></i> '
                        'Pick a theater for directions</span>')
        sep = '<span class="hint-sep">·</span>'
        parts.append(f'      <p class="screenings-hint">{sep.join(bits)}</p>')
    parts.append('    </div>')
    parts.append('  </div>')

    # Summary of the first week, as served; the script rewrites it for
    # whatever is selected.
    label = week_label or f"{_range_label(first.start, first.end)}, {first.end.year}"
    if first.matches:
        parts.append(f'  <p class="screenings-summary">{_summary_html(*_counts(first.matches), label)}</p>')
        parts.append('  <p class="screenings-empty" hidden></p>')
    else:
        parts.append('  <p class="screenings-summary" hidden></p>')
        parts.append(f'  <p class="screenings-empty">{_empty_text(label)}</p>')

    by_day: dict[date, list[MatchedScreening]] = defaultdict(list)
    for ms in all_matches:
        by_day[ms.screening.day].append(ms)
    parts.append('  <div class="screenings-view" data-view="day">')
    for day in sorted(by_day):
        outside = not (first.start <= day <= first.end)
        parts.append(f'  <div class="day-group" data-date="{day.isoformat()}"{" hidden" if outside else ""}>')
        parts.append(f'    <h2 class="day-header">{html.escape(day.strftime("%A, %B %-d"))}</h2>')
        for group in _group_by_film(by_day[day]):
            parts.append(_render_site_film(group, map_query))
        parts.append('  </div>')
    parts.append('  </div>')

    parts.append('  <div class="screenings-view" data-view="movie" hidden>')
    for group in _group_by_film_for_week(all_matches):
        parts.append(_render_site_film_week(group, map_query))
    parts.append('  </div>')

    parts.append(_SCREENINGS_SCRIPT)
    parts.append('</section>')
    return "\n".join(parts)


def _counts(ms_list: list[MatchedScreening]) -> tuple[int, int, int]:
    return (len({_film_key(m) for m in ms_list}),
            sum(len(m.screening.times) for m in ms_list),
            len({m.screening.theater for m in ms_list if m.screening.theater}))


def _empty_text(label: str) -> str:
    return (f'No screenings from my watchlist are listed for {html.escape(label)} yet. '
            f'Theaters post their schedules a few weeks ahead, so later dates fill in over time.')


def _count_text(n: int) -> str:
    return f"{n} film{'s' if n != 1 else ''}" if n else "none"


def _render_date_strip(weeks: list[SiteWeek]) -> str:
    """A row of tiles, one per day, grouped under a label tile per week (the
    pattern Metrograph's and Fandango's showtime pages use). Each tile counts
    the watchlist films playing; days with none are greyed out."""
    today = weeks[0].start
    lines = ['    <div class="date-strip">',
             '      <button type="button" class="strip-scroll" data-dir="-1" aria-label="Earlier dates">'
             '<span aria-hidden="true">‹</span></button>',
             '      <div class="strip-track" role="group" aria-label="Choose dates">']
    prev_month = None
    for w in weeks:
        films_by_day: dict[date, set] = defaultdict(set)
        for ms in w.matches:
            films_by_day[ms.screening.day].add(_film_key(ms))
        week_films = len({_film_key(ms) for ms in w.matches})
        weekend_films = len({_film_key(ms) for ms in w.matches if ms.screening.day.weekday() >= 4})
        label = "This week" if w.is_current else _range_label(w.start, w.end)
        tile = "This week" if w.is_current else _range_label(w.start, w.end).replace(" – ", "–")
        lines.append(f'        <div class="strip-week" data-week="{w.start.isoformat()}" '
                     f'data-end="{w.end.isoformat()}" data-short="{html.escape(w.start.strftime("%b %-d"))}">')
        lines.append(f'          <button type="button" class="strip-week-label" data-week="{w.start.isoformat()}" '
                     f'aria-pressed="false" aria-label="{html.escape(label)}: {_count_text(week_films)}">'
                     f'<span class="strip-week-name">{html.escape(tile)}</span>'
                     f'<span class="strip-count" data-all="{_count_text(week_films)}" '
                     f'data-weekend="{_count_text(weekend_films)}">{_count_text(week_films)}</span></button>')
        d = w.start
        while d <= w.end:
            n = len(films_by_day.get(d, ()))
            dow = "Today" if d == today and w.is_current else d.strftime("%a")
            month = d.strftime("%b") if (d.month != prev_month or d.day == 1) else ""
            prev_month = d.month
            full = d.strftime("%A, %B %-d")
            lines.append(
                f'          <button type="button" class="strip-day{"" if n else " is-empty"}" '
                f'data-date="{d.isoformat()}" data-week="{w.start.isoformat()}" aria-pressed="false" '
                f'aria-label="{html.escape(full)}: {_count_text(n)}">'
                f'<span class="strip-dow">{dow}</span>'
                f'<span class="strip-num">{d.day}</span>'
                f'<span class="strip-month">{month}</span>'
                f'<span class="strip-count">{_count_text(n) if n else "–"}</span></button>')
            d += timedelta(days=1)
        lines.append('        </div>')
    lines += ['      </div>',
              '      <button type="button" class="strip-scroll" data-dir="1" aria-label="Later dates">'
              '<span aria-hidden="true">›</span></button>',
              '    </div>']
    return "\n".join(lines)


def _summary_html(films: int, showtimes: int, venues: int, when: str) -> str:
    return (f'<strong>{films}</strong> film{"s" if films != 1 else ""} from my watchlist, '
            f'<strong>{showtimes}</strong> showtime{"s" if showtimes != 1 else ""} '
            f'at {venues} venue{"s" if venues != 1 else ""} — {html.escape(when)}')


# Date strip, selection and Day / Movie switch.
#  - A selection is a week (its label tile), a day (one tap on a day tile),
#    or a range: after picking a day, tapping a later day selects everything
#    from the first through the second. Tapping an earlier day starts over
#    from it, and tapping the picked day again selects its week. "Back to
#    this week" resets.
#  - The page holds every listed day; the selection only shows and hides
#    them, and the summary line and the movie cards' counts are recomputed
#    from what is shown.
#  - The view is kept in the URL (?view=movie) and in localStorage, so links
#    and return visits open the same view. Dates are kept in the URL only
#    (?week=, ?date=, or ?from=&to=, all YYYY-MM-DD), so a plain visit opens
#    on this week.
#  - The page is rebuilt daily; in case a rebuild is late, days before today
#    (New York time) are dropped, and if this week has nothing left the next
#    week is shown instead.
#  - "Weekends only" narrows any selection to Fridays, Saturdays and Sundays
#    and dims the other days in the strip. Like the view, it is kept in the
#    URL (?weekends=1) and in localStorage.
#  - The heading follows the selection: "This Week in NYC", "Week of Oct 12
#    in NYC", "Today in NYC", "Wednesday, Oct 14 in NYC", "Oct 11 – 25 in NYC",
#    and with weekends only "This Weekend in NYC", "Weekend of Oct 16 in NYC",
#    "Weekends, Oct 11 – 25 in NYC".
_SCREENINGS_SCRIPT = """  <script>
  (function () {
    var section = document.currentScript.closest('.screenings-section');
    if (!section) return;
    function all(sel, root) { return Array.prototype.slice.call((root || section).querySelectorAll(sel)); }
    function plural(n, word) { return n + ' ' + word + (n === 1 ? '' : 's'); }
    var controls = section.querySelector('.screenings-controls');
    var track = section.querySelector('.strip-track');
    var viewButtons = all('.screenings-view-toggle button[data-view]');
    var KEY = 'screenings-view';

    function showView(view, remember) {
      if (view !== 'movie') view = 'day';
      all('.screenings-view').forEach(function (v) { v.hidden = v.getAttribute('data-view') !== view; });
      viewButtons.forEach(function (b) {
        b.setAttribute('aria-pressed', b.getAttribute('data-view') === view ? 'true' : 'false');
      });
      if (remember) {
        try { localStorage.setItem(KEY, view); } catch (e) {}
        try {
          var url = new URL(window.location.href);
          if (view === 'movie') url.searchParams.set('view', 'movie'); else url.searchParams.delete('view');
          history.replaceState(null, '', url);
        } catch (e) {}
      }
    }
    var params = null;
    try { params = new URL(window.location.href).searchParams; } catch (e) {}
    var startView = params && params.get('view');
    if (!startView) { try { startView = localStorage.getItem(KEY); } catch (e) {} }
    viewButtons.forEach(function (b) {
      b.addEventListener('click', function () { showView(b.getAttribute('data-view'), true); });
    });
    if (!track) { showView(startView, false); controls.hidden = false; return; }

    var heading = document.querySelector('.title-card h1');
    var headingText = heading ? heading.textContent : '';
    var summary = section.querySelector('.screenings-summary');
    var empty = section.querySelector('.screenings-empty');
    var selectionLine = section.querySelector('.strip-selection');
    var weekendButton = section.querySelector('.weekend-toggle');
    var WEEKEND_KEY = 'screenings-weekends';
    var weekendsOnly = false;
    function isWeekend(iso) { var g = dateObj(iso).getDay(); return g === 5 || g === 6 || g === 0; }
    var today;
    try {
      today = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York' }).format(new Date());
    } catch (e) { today = new Date().toISOString().slice(0, 10); }

    // Drop what has already happened (only matters if a daily rebuild is late).
    all('.strip-day').forEach(function (t) { if (t.getAttribute('data-date') < today) t.hidden = true; });
    var stripWeeks = all('.strip-week').filter(function (w) {
      if (w.getAttribute('data-end') >= today) return true;
      w.hidden = true;
      return false;
    });
    if (!stripWeeks.length) return;
    var lastDay = stripWeeks[stripWeeks.length - 1].getAttribute('data-end');

    function dateObj(iso) { return new Date(iso + 'T12:00:00'); }
    function fmt(iso, opts) { return dateObj(iso).toLocaleDateString('en-US', opts); }
    function rangeLabel(from, to) {
      if (from === to) return fmt(from, { weekday: 'long', month: 'long', day: 'numeric' });
      var a = dateObj(from), b = dateObj(to);
      return fmt(from, { month: 'short', day: 'numeric' }) + ' – ' +
        (a.getMonth() === b.getMonth() ? b.getDate() : fmt(to, { month: 'short', day: 'numeric' }));
    }
    function weekState(w) {
      var start = w.getAttribute('data-week');
      return { kind: 'week', week: start, from: start < today ? today : start, to: w.getAttribute('data-end') };
    }
    function weekOf(iso) {
      return stripWeeks.filter(function (w) {
        return w.getAttribute('data-week') <= iso && iso <= w.getAttribute('data-end');
      })[0];
    }
    var defaultState = weekState(stripWeeks[0]);
    var state = defaultState;

    function apply(remember) {
      var from = state.from, to = state.to;
      function inRange(el) {
        var d = el.getAttribute('data-date');
        return d >= today && d >= from && d <= to && (!weekendsOnly || isWeekend(d));
      }
      all('.day-group').forEach(function (g) { g.hidden = !inRange(g); });
      var films = 0;
      all('.screenings-view[data-view="movie"] .screening-card').forEach(function (card) {
        var rows = all('.screening-venue', card);
        var shown = 0, times = 0, dates = {};
        rows.forEach(function (r) {
          r.hidden = !inRange(r);
          if (!r.hidden) {
            shown++;
            times += r.querySelectorAll('.showtime').length;
            dates[r.getAttribute('data-date')] = true;
          }
        });
        card.hidden = !shown;
        if (shown) films++;
        var count = card.querySelector('.card-count');
        if (count) {
          count.hidden = state.kind === 'day';
          var sep = count.textContent.indexOf('•') >= 0 ? ' • ' : '';
          count.textContent = sep + plural(times, 'showtime') + ' on ' + plural(Object.keys(dates).length, 'day');
        }
      });
      var showtimes = 0, venues = {};
      all('.day-group:not([hidden]) .screening-venue').forEach(function (r) {
        showtimes += r.querySelectorAll('.showtime').length;
        var v = r.querySelector('.venue-name');
        if (v) venues[v.textContent.replace(' (map)', '').trim()] = true;
      });
      var label = rangeLabel(from, to);
      if (weekendsOnly && from !== to) label = 'Weekends, ' + label;
      if (films) {
        summary.innerHTML = '<strong>' + films + '</strong> ' + (films === 1 ? 'film' : 'films') +
          ' from my watchlist, <strong>' + showtimes + '</strong> ' + (showtimes === 1 ? 'showtime' : 'showtimes') +
          ' at ' + plural(Object.keys(venues).length, 'venue') + ' — ' + label;
        summary.hidden = false; empty.hidden = true;
      } else if (weekendsOnly && state.kind === 'day' && !isWeekend(from)) {
        empty.textContent = fmt(from, { weekday: 'long' }) + ' isn\u2019t a weekend day. ' +
          'Turn off \u201cWeekends only\u201d to see ' + label + '.';
        summary.hidden = true; empty.hidden = false;
      } else {
        empty.textContent = 'No screenings from my watchlist are listed for ' + label +
          ' yet. Theaters post their schedules a few weeks ahead, so later dates fill in over time.';
        summary.hidden = true; empty.hidden = false;
      }

      all('.strip-week-label').forEach(function (b) {
        b.setAttribute('aria-pressed', state.kind === 'week' && b.getAttribute('data-week') === state.week ? 'true' : 'false');
      });
      all('.strip-day').forEach(function (t) {
        var d = t.getAttribute('data-date');
        var end = state.kind !== 'week' && (d === from || d === to);
        t.setAttribute('aria-pressed', end ? 'true' : 'false');
        t.classList.toggle('in-selection', !end && d >= from && d <= to);
        t.classList.toggle('filtered-out', weekendsOnly && !isWeekend(d));
      });
      if (weekendButton) weekendButton.setAttribute('aria-pressed', weekendsOnly ? 'true' : 'false');
      all('.strip-week-label .strip-count').forEach(function (c) {
        c.textContent = c.getAttribute(weekendsOnly ? 'data-weekend' : 'data-all');
      });

      var isDefault = state.kind === 'week' && state.week === defaultState.week;
      if (heading && /^This Week/.test(headingText)) {
        var lead = weekendsOnly ? 'This Weekend' : 'This Week';
        if (state.kind === 'day') lead = from === today ? 'Today' : fmt(from, { weekday: 'long', month: 'short', day: 'numeric' });
        else if (state.kind === 'range') lead = (weekendsOnly ? 'Weekends, ' : '') + rangeLabel(from, to);
        else if (!isDefault && weekendsOnly) {
          var fri = dateObj(state.week); fri.setDate(fri.getDate() + ((5 - fri.getDay() + 7) % 7));
          lead = 'Weekend of ' + fri.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
        }
        else if (!isDefault) lead = 'Week of ' + weekOf(state.week).getAttribute('data-short');
        heading.textContent = headingText.replace(/^This Week/, lead);
      }
      var note = state.kind === 'day' ? 'Tap a later day to see a range from ' + fmt(from, { weekday: 'short', month: 'short', day: 'numeric' }) + '.'
               : isDefault ? 'Tap a day to see just that day, or two days to see a range.' : '';
      selectionLine.innerHTML = '';
      selectionLine.appendChild(document.createTextNode(note));
      if (!isDefault) {
        var back = document.createElement('button');
        back.type = 'button'; back.className = 'strip-reset'; back.textContent = 'Back to this week';
        back.addEventListener('click', function () { state = defaultState; apply(true); });
        selectionLine.appendChild(back);
      }

      if (remember) {
        try {
          var url = new URL(window.location.href);
          ['week', 'date', 'from', 'to', 'weekends'].forEach(function (k) { url.searchParams.delete(k); });
          if (weekendsOnly) url.searchParams.set('weekends', '1');
          if (state.kind === 'day') url.searchParams.set('date', from);
          else if (state.kind === 'range') { url.searchParams.set('from', from); url.searchParams.set('to', to); }
          else if (!isDefault) url.searchParams.set('week', state.week);
          history.replaceState(null, '', url);
        } catch (e) {}
      }
    }

    // Initial selection: from the URL, else this week (or next week if this
    // week has nothing left).
    function valid(iso) { return /^\\d{4}-\\d{2}-\\d{2}$/.test(iso || '') && iso <= lastDay; }
    var pDate = params && params.get('date'), pFrom = params && params.get('from'),
        pTo = params && params.get('to'), pWeek = params && params.get('week');
    if (valid(pDate) && pDate >= today) {
      state = { kind: 'day', from: pDate, to: pDate };
    } else if (valid(pTo) && /^\\d{4}-\\d{2}-\\d{2}$/.test(pFrom || '') && pFrom <= pTo && pTo >= today) {
      state = { kind: 'range', from: pFrom < today ? today : pFrom, to: pTo };
    } else if (pWeek && weekOf(pWeek) && weekOf(pWeek).getAttribute('data-week') === pWeek) {
      state = weekState(weekOf(pWeek));
    } else if (stripWeeks.length > 1 && !all('.day-group').some(function (g) {
                 var d = g.getAttribute('data-date'); return d >= today && d <= defaultState.to; })
               && all('.day-group').some(function (g) { return g.getAttribute('data-date') >= today; })) {
      defaultState = state = weekState(stripWeeks[1]);
    }
    var w0 = params && params.get('weekends');
    if (w0 === null || w0 === undefined) { try { w0 = localStorage.getItem(WEEKEND_KEY); } catch (e) {} }
    weekendsOnly = w0 === '1';
    apply(false);
    showView(startView, false);
    if (weekendButton) weekendButton.addEventListener('click', function () {
      weekendsOnly = !weekendsOnly;
      try { localStorage.setItem(WEEKEND_KEY, weekendsOnly ? '1' : '0'); } catch (e) {}
      apply(true);
    });

    all('.strip-week-label').forEach(function (b) {
      b.addEventListener('click', function () { state = weekState(weekOf(b.getAttribute('data-week'))); apply(true); });
    });
    all('.strip-day').forEach(function (t) {
      t.addEventListener('click', function () {
        var d = t.getAttribute('data-date');
        if (state.kind === 'day' && d === state.from) state = weekState(weekOf(d));
        else if (state.kind === 'day' && d > state.from) state = { kind: 'range', from: state.from, to: d };
        else state = { kind: 'day', from: d, to: d };
        apply(true);
      });
    });

    // Scroll arrows: page through the strip; disabled at either end.
    var arrows = all('.strip-scroll');
    function updateArrows() {
      arrows[0].disabled = track.scrollLeft <= 2;
      arrows[1].disabled = track.scrollLeft + track.clientWidth >= track.scrollWidth - 2;
    }
    arrows.forEach(function (a) {
      a.addEventListener('click', function () {
        track.scrollBy({ left: Number(a.getAttribute('data-dir')) * track.clientWidth * 0.8, behavior: 'smooth' });
      });
    });
    track.addEventListener('scroll', updateArrows, { passive: true });
    window.addEventListener('resize', updateArrows);
    controls.hidden = false;
    var sel = section.querySelector('.strip-day[aria-pressed="true"]')
           || section.querySelector('.strip-week-label[aria-pressed="true"]');
    if (sel && sel.offsetLeft + sel.offsetWidth > track.clientWidth) track.scrollLeft = sel.offsetLeft - 8;
    updateArrows();
  })();
  </script>"""


def _film_header_lines(group: list[MatchedScreening], extra_meta: str | None = None) -> list[str]:
    first = group[0]
    directors = next((m.screening.directors for m in group if m.screening.directors), [])
    klass = "screening-card"
    if len(first.screening.films) > 1:
        klass += " double-feature"
    lines = [f'    <div class="{klass}">']
    lines.append(f'      <div class="screening-title">{_canonical_title_html(first)}</div>')
    meta = []
    if directors:
        label = "Dir." if len(directors) == 1 else "Dirs."
        meta.append(f"{label} {html.escape(', '.join(directors))}")
    meta_html = " &bull; ".join(meta)
    if extra_meta:
        # The count is hidden when a single day is picked, bullet and all.
        meta_html += (f'<span class="card-count">{" &bull; " if meta else ""}'
                      f'{html.escape(extra_meta)}</span>')
    if meta_html:
        lines.append(f'      <div class="screening-meta">{meta_html}</div>')
    return lines


def _venue_html(theater: str, map_query) -> str:
    name = html.escape(theater or "Venue TBA")
    q = map_query(theater) if (map_query and theater) else None
    if not q:
        return f'<span class="venue-name">{name}</span>'
    url = "https://www.google.com/maps/search/?api=1&query=" + quote_plus(q)
    return (f'<a class="venue-name venue-map" href="{html.escape(url)}" target="_blank" rel="noopener" '
            f'title="{name} on Google Maps">'
            f'<i class="fa-solid fa-location-dot" aria-hidden="true"></i>{name}'
            f'<span class="visually-hidden"> (map)</span></a>')


def _showings_lines(ms: MatchedScreening, indent: str) -> list[str]:
    """Showtimes (each linking to its tickets when known), format tags, the
    special event if any, and any alternate listing title for one row."""
    scr = ms.screening
    tags = [t.strip() for t in (scr.notes or "").split("•") if t.strip()]
    if scr.presenter:
        tags.append(f"pres. by {scr.presenter}")
    listed = _listed_as(ms)
    urls = list(scr.ticket_urls) + [None] * (len(scr.times) - len(scr.ticket_urls))
    day = scr.day.strftime("%a, %b %-d")
    lines = [f'{indent}<span class="venue-showings">']
    times = []
    for t, u in zip(scr.times, urls):
        if u:
            label = f"Tickets for {t}, {day}, {scr.theater}"
            times.append(f'<a class="showtime" href="{html.escape(u)}" target="_blank" rel="noopener" '
                         f'title="{html.escape(label)}" aria-label="{html.escape(label)}">{html.escape(t)}</a>')
        else:
            times.append(f'<span class="showtime no-link">{html.escape(t)}</span>')
    lines.append(f'{indent}  <span class="venue-times">{"".join(times)}</span>')
    for t in tags:
        lines.append(f'{indent}  <span class="screening-tag">{html.escape(t)}</span>')
    if scr.event:
        lines.append(f'{indent}  <span class="screening-event">'
                     f'<i class="fa-solid fa-microphone-lines" aria-hidden="true"></i>'
                     f'{html.escape(scr.event)}</span>')
    if listed:
        lines.append(f'{indent}  <span class="venue-listed">Listed as “{html.escape(listed)}”</span>')
    lines.append(f'{indent}</span>')
    return lines


def _render_site_film(group: list[MatchedScreening], map_query=None) -> str:
    """By-day view: one card for one film on one day, with a row per venue."""
    lines = _film_header_lines(group)
    lines.append('      <ul class="screening-venues">')
    for ms in group:
        lines.append('        <li class="screening-venue">')
        lines.append(f'          {_venue_html(ms.screening.theater, map_query)}')
        lines.extend(_showings_lines(ms, '          '))
        lines.append('        </li>')
    lines.append('      </ul>')
    lines.append('    </div>')
    return "\n".join(lines)


def _title_sort_key(ms: MatchedScreening) -> str:
    """Alphabetical order for the by-movie view, ignoring a leading article
    and case ("The Age of Innocence" sorts under A)."""
    film, match = ms.screening.films[0], ms.matches[0]
    return normalize_title(match.title if match is not None else film.title)


def _group_by_film_for_week(matches: list[MatchedScreening]) -> list[list[MatchedScreening]]:
    """Group a week's screenings by film, films in alphabetical order, each
    film's screenings in date, time and venue order."""
    groups: dict[tuple, list[MatchedScreening]] = {}
    for ms in matches:
        groups.setdefault(_film_key(ms), []).append(ms)
    out = []
    for items in groups.values():
        items.sort(key=lambda m: (m.screening.day, _sort_key(m.screening.times),
                                  m.screening.theater.lower()))
        out.append(items)
    out.sort(key=lambda g: _title_sort_key(g[0]))
    return out


def _render_site_film_week(group: list[MatchedScreening], map_query=None) -> str:
    """By-movie view: one card for one film across the week, with a row per
    day and venue. The day label appears on the first row of each day."""
    showtimes = sum(len(m.screening.times) for m in group)
    days = len({m.screening.day for m in group})
    extra = (f'{showtimes} showtime{"s" if showtimes != 1 else ""} '
             f'on {days} day{"s" if days != 1 else ""}')
    lines = _film_header_lines(group, extra_meta=extra)
    lines.append('      <ul class="screening-venues screening-week">')
    prev_day = None
    for ms in group:
        day = ms.screening.day
        new_day = day != prev_day
        prev_day = day
        klass = "screening-venue" + (" new-day" if new_day else "")
        label = day.strftime("%a, %b %-d")
        lines.append(f'        <li class="{klass}" data-date="{day.isoformat()}">')
        # Repeat rows of the same day keep the date for screen readers but hide
        # it visually, so each day's rows read as one block.
        if new_day:
            lines.append(f'          <span class="venue-day">{html.escape(label)}</span>')
        else:
            lines.append(f'          <span class="venue-day"><span class="visually-hidden">'
                         f'{html.escape(label)}</span></span>')
        lines.append(f'          {_venue_html(ms.screening.theater, map_query)}')
        lines.extend(_showings_lines(ms, '          '))
        lines.append('        </li>')
    lines.append('      </ul>')
    lines.append('    </div>')
    return "\n".join(lines)


def splice_into_site_page(page_path: str | Path, fragment: str) -> Path:
    """Replace the content between SCREENINGS:START / SCREENINGS:END markers
    in the given page file with the new fragment. Page must already contain
    both markers.
    """
    p = Path(page_path)
    page = p.read_text(encoding="utf-8")
    if SITE_FRAGMENT_START not in page or SITE_FRAGMENT_END not in page:
        raise ValueError(
            f"{p} is missing required markers "
            f"({SITE_FRAGMENT_START!r} / {SITE_FRAGMENT_END!r})"
        )
    pre, _, rest = page.partition(SITE_FRAGMENT_START)
    _, _, post = rest.partition(SITE_FRAGMENT_END)
    new_page = (
        pre
        + SITE_FRAGMENT_START + "\n"
        + fragment + "\n"
        + SITE_FRAGMENT_END
        + post
    )
    new_page = _stamp_stylesheet(new_page, p.parent)
    p.write_text(new_page, encoding="utf-8")
    return p


def _stamp_stylesheet(page: str, site_root: Path) -> str:
    """Point the page's styles.css link at a content version (styles.css?v=<hash>).

    The site serves styles.css with a 4-hour browser cache, so without this a
    returning visitor can get freshly generated markup paired with a stale
    stylesheet that lacks its classes. Same scheme tool/reading.py uses for
    reading.html."""
    css = site_root / "styles.css"
    if not css.exists():
        return page
    version = hashlib.sha256(css.read_bytes()).hexdigest()[:8]
    return re.sub(r'\bhref="styles\.css(?:\?v=[0-9a-f]+)?"',
                  f'href="styles.css?v={version}"', page)


def write_site_page(matches: list[MatchedScreening],
                    page_path: str | Path,
                    week_label: str | None = None,
                    updated_iso: str | None = None,
                    weeks: list[SiteWeek] | None = None,
                    map_query=None) -> Path:
    """Render the site fragment and splice it into the given page file."""
    fragment = render_site_fragment(matches, week_label=week_label,
                                    updated_iso=updated_iso, weeks=weeks,
                                    map_query=map_query)
    return splice_into_site_page(page_path, fragment)


if __name__ == "__main__":
    from watchlist import load_watchlist
    from revival_hub import parse_revival_hub_pdf
    from match import match_screenings

    wl = load_watchlist("/sessions/zealous-inspiring-turing/mnt/movies/data/watchlist.csv")
    scrs = parse_revival_hub_pdf(
        "/sessions/zealous-inspiring-turing/mnt/movies/reference-docs/Gmail - Playing This Week in LA.pdf",
        reference_year=2026,
    )
    matches = match_screenings(scrs, wl)
    out = write_report(matches, "/sessions/zealous-inspiring-turing/movie-tool/report.html")
    print(f"Wrote {out} ({len(matches)} matched screenings)")
