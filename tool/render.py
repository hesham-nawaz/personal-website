"""Render a list of matched screenings as a single-file HTML report."""
from __future__ import annotations

import html
from collections import defaultdict
from datetime import date
from pathlib import Path

from match import MatchedScreening


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


def render_site_fragment(matches: list[MatchedScreening],
                         week_label: str | None = None,
                         updated_iso: str | None = None) -> str:
    """Render screenings as a site-page fragment.

    Output uses CSS classes defined in personal-website/styles.css
    (.screenings-section, .day-group, .day-header, .screening-card, etc.) —
    no inline <style>, no <html>/<head>/<body>. Designed to be spliced
    into screenings.html between SCREENINGS:START / SCREENINGS:END markers.
    """
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
    parts.append('<section class="screenings-section">')

    if not matches:
        parts.append(
            '  <p class="screenings-empty">'
            'No matching screenings this week. Check back next week.'
            '</p>'
        )
        parts.append('</section>')
        return "\n".join(parts)

    parts.append(
        f'  <p class="screenings-summary">'
        f'<strong>{total}</strong> screening{"s" if total != 1 else ""} '
        f'({movie_count} matched title{"s" if movie_count != 1 else ""}) '
        f'— {html.escape(week_label or "")}'
        f'</p>'
    )
    if updated_iso:
        parts.append(
            f'  <p class="screenings-updated">'
            f'Last updated {html.escape(updated_iso)}'
            f'</p>'
        )

    for day in days:
        header = day.strftime("%A, %B %-d")
        parts.append('  <div class="day-group">')
        parts.append(f'    <h2 class="day-header">{html.escape(header)}</h2>')
        for ms in by_day[day]:
            parts.append(_render_site_screening(ms))
        parts.append('  </div>')

    parts.append('</section>')
    return "\n".join(parts)


def _render_site_screening(ms: MatchedScreening) -> str:
    scr = ms.screening

    film_html_parts = []
    for film, match in zip(scr.films, ms.matches):
        film_html_parts.append(_film_title_html(film, match, scr.films))
    title_html = " / ".join(film_html_parts)

    times = ", ".join(scr.times)

    meta_bits = []
    if scr.directors:
        label = "Dir." if len(scr.directors) == 1 else "Dirs."
        meta_bits.append(f"{label} {html.escape(', '.join(scr.directors))}")
    if scr.theater:
        meta_bits.append(html.escape(scr.theater))
    if scr.presenter:
        meta_bits.append(f"pres. by {html.escape(scr.presenter)}")
    meta = " &bull; ".join(meta_bits)

    klass = "screening-card"
    if len(scr.films) > 1:
        klass += " double-feature"

    lines = [f'    <div class="{klass}">']
    lines.append(f'      <div class="screening-title">{title_html}</div>')
    if meta:
        lines.append(f'      <div class="screening-meta">{meta}</div>')
    lines.append(f'      <div class="screening-times">{html.escape(times)}</div>')
    if scr.notes:
        lines.append(f'      <div class="screening-notes">{html.escape(scr.notes)}</div>')
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
    p.write_text(new_page, encoding="utf-8")
    return p


def write_site_page(matches: list[MatchedScreening],
                    page_path: str | Path,
                    week_label: str | None = None,
                    updated_iso: str | None = None) -> Path:
    """Render the site fragment and splice it into the given page file."""
    fragment = render_site_fragment(matches, week_label=week_label,
                                    updated_iso=updated_iso)
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
