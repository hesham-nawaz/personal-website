"""Parse the Revival Hub "Playing This Week in LA" email body.

Source can be plain text extracted from the email HTML body, or text from the
printed PDF. Both end up as a block of text lines where each screening looks
like:

    TIME • TITLE (YEAR), dir. NAME • THEATER {optional notes} (pres. by X)

Variants handled:
  - Multi-showtime: "10:00a, 1:30p, 5:00p, 8:30p • ..."
  - "All Day • ..."
  - dirs. NAME1, NAME2   (multi-director)
  - Double features: "TITLE A (YEAR) / TITLE B (YEAR), dir. X / dir. Y"
  - Optional year (some programmed shorts/events have no year)
  - Notes in {curly braces}, presenter in "(pres. by ...)"
  - Day headers on their own line: "Monday, April 20"
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date


# ---- Regexes ---------------------------------------------------------------

DAY_HEADER_RE = re.compile(
    r"^(?:\*+\s*)?"  # tolerate leading "** " from mailchimp-style headings
    r"(?P<weekday>Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+"
    r"(?P<month>January|February|March|April|May|June|July|August|September|October|November|December)\s+"
    r"(?P<day>\d{1,2})\s*$"
)

# A "highlights" row starts with a weekday and then a time-bullet, e.g.
#   "Monday, April 20 • 2:30p • The Servant (1963), dir. ..."
# We detect and drop these because every highlight also appears in the
# day-grouped body below.
HIGHLIGHT_LINE_RE = re.compile(
    r"^\s*(?:\*+\s*)?(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+"
    r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+"
    r"\d{1,2}\s*•"
)

# Footer/boilerplate markers that signal "stop parsing — everything below is
# Revival Hub's email footer, not screenings." We stop on the FIRST match so
# trailing lines don't get appended to the last entry's theater field.
FOOTER_MARKERS = (
    "Spread the word",
    "See you at the movies",
    "Copyright (C)",
    "You are receiving this email",
    "update your preferences",
)

# A time token like "2:30p", "10:00a", "All Day"
TIME_TOKEN = r"(?:\d{1,2}:\d{2}[ap]|All Day)"
# A full "time prefix" on an entry line: one or more comma-separated time tokens
# followed by a bullet (possibly with spaces). The bullet character is "•".
ENTRY_START_RE = re.compile(
    rf"^(?P<times>{TIME_TOKEN}(?:\s*,\s*{TIME_TOKEN})*)\s*•\s*(?P<rest>.+)$"
)

# Title-with-optional-year, non-greedy: capture title up to " (YYYY)" or up to
# the first ", dir" or " •"
TITLE_YEAR_RE = re.compile(r"^(?P<title>.+?)\s*\((?P<year>\d{4})\)$")

MONTH_TO_NUM = {
    "January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
    "July": 7, "August": 8, "September": 9, "October": 10, "November": 11, "December": 12,
}


# ---- Data model ------------------------------------------------------------

@dataclass
class Film:
    title: str
    year: int | None


@dataclass
class Screening:
    day: date              # calendar date of the screening
    weekday: str           # "Monday"
    times: list[str]       # ["2:30p"] or ["10:00a", "1:30p", ...] or ["All Day"]
    films: list[Film]      # one entry for single feature, multiple for double feature
    directors: list[str]   # flat list across all films
    theater: str
    notes: str | None = None
    presenter: str | None = None
    raw: str = ""          # original joined line, for debugging


# ---- PDF / text extraction -------------------------------------------------

def extract_text_from_pdf(pdf_path: str) -> str:
    """Extract all text from a PDF as a single string, preserving line breaks."""
    import pdfplumber
    parts: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                parts.append(t)
    return "\n".join(parts)


# ---- Line reassembly -------------------------------------------------------

def _clean_plaintext(text: str) -> str:
    """Strip Gmail-plaintext-specific artifacts so the parser sees a uniform
    shape regardless of whether input was PDF text or email body.

    - Remove inline URLs in parens: "Title (1963) (https://...)" -> "Title (1963)"
    - Convert left/right smart quotes to straight quotes (helps downstream
      normalization).
    - Normalize non-breaking spaces.
    """
    # Drop URLs wrapped in parens (possibly preceded by whitespace).
    text = re.sub(r"\s*\(https?://[^)]+\)", "", text)
    # Normalize whitespace variants to plain spaces (but keep newlines).
    text = text.replace("\u00a0", " ")
    return text


def reassemble_entries(text: str) -> list[tuple[str, str]]:
    """Walk the text line-by-line, joining wrapped/blank-separated lines into
    one logical entry per screening.

    Returns a list of (kind, content) tuples where kind is one of:
      "day"   — content is "Monday, April 20"
      "entry" — content is the full joined entry line
    Non-matching preamble/footer lines are skipped.

    An entry starts when a line matches ENTRY_START_RE (a time/"All Day"
    bullet). It continues until the NEXT entry-start line, a day header, or
    end of input. Blank lines WITHIN an entry are preserved as spaces (this
    is necessary for the Gmail plaintext format where theater names can wrap
    across a blank line).
    """
    text = _clean_plaintext(text)

    out: list[tuple[str, str]] = []
    lines = [ln.rstrip() for ln in text.split("\n")]

    current_day_seen = False
    cur_entry: list[str] | None = None

    def flush():
        nonlocal cur_entry
        if cur_entry is not None:
            joined = " ".join(s.strip() for s in cur_entry if s.strip())
            joined = re.sub(r"\s+", " ", joined).strip()
            if joined:
                out.append(("entry", joined))
            cur_entry = None

    for raw in lines:
        line = raw.strip()

        # Footer detected — flush any in-flight entry and stop parsing
        # entirely. Without this, trailing newsletter boilerplate gets
        # appended to the last entry's theater field.
        if current_day_seen and any(marker in line for marker in FOOTER_MARKERS):
            flush()
            break

        # Day headers terminate any in-flight entry.
        m = DAY_HEADER_RE.match(line)
        if m:
            flush()
            # Normalize to stripped form for downstream parser.
            out.append(("day", f"{m.group('weekday')}, {m.group('month')} {m.group('day')}"))
            current_day_seen = True
            continue

        # Skip "highlights" rows that inline day + time prefix; they duplicate
        # entries that also appear in the day-grouped body.
        if HIGHLIGHT_LINE_RE.match(line):
            flush()
            continue

        # New entry-start line: flush whatever was being accumulated and start
        # a new entry.
        if ENTRY_START_RE.match(line):
            flush()
            cur_entry = [line]
            continue

        # Blank line: don't flush -- wait for the next entry-start or day
        # header. (Email bodies often have blank lines between entries AND
        # within entries.)
        if not line:
            continue

        # Continuation of the current entry (wrapped line / paragraph break).
        if cur_entry is not None and current_day_seen:
            cur_entry.append(line)
            continue

        # Otherwise: preamble / footer / sponsor block -- drop.

    flush()
    return out


# ---- Entry parsing ---------------------------------------------------------

def _strip_presenter(s: str) -> tuple[str, str | None]:
    """Split off a trailing '(pres. by X)' if present."""
    m = re.search(r"\s*\(pres\. by\s+(?P<name>[^)]+)\)\s*$", s)
    if not m:
        return s, None
    return s[:m.start()].rstrip(), m.group("name").strip()


def _extract_notes(s: str) -> tuple[str, str | None]:
    """Pull out the first {...} block. Returns (s_without_braces, note_or_None).

    We assume at most one top-level braces block per entry; the sample data
    supports this.
    """
    # Find outermost balanced braces (simple: first { to its matching }, naive)
    i = s.find("{")
    if i < 0:
        return s, None
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                note = s[i + 1: j].strip()
                cleaned = (s[:i] + s[j + 1:]).replace("  ", " ").strip()
                # Also remove dangling "  •" artifacts
                cleaned = re.sub(r"\s+•\s*$", "", cleaned)
                return cleaned, note
    # Unbalanced; bail
    return s, None


def _parse_films_and_directors(body: str) -> tuple[list[Film], list[str]]:
    """Given the 'Title (Year), dir. Name' portion, parse films + directors.

    Handles double features with '/' separators and 'dirs. A, B' multi-director.
    """
    # Split off director segment. We look for ", dir." or ", dirs." as the
    # boundary. The films portion is everything before; directors portion is
    # everything from 'dir' onward.
    # But directors can contain commas ("dirs. Lilly Wachowski, Lana Wachowski")
    # so we split only on the FIRST occurrence of the dir/dirs keyword.
    m = re.search(r",\s+(dirs?\.\s*)", body)
    if m:
        films_part = body[:m.start()].strip()
        dirs_part = body[m.start() + 1:].strip()  # "dir. X" or "dirs. X, Y / dir. Z"
    else:
        # No director info (e.g. programmed shorts events). Treat whole body as
        # the title.
        films_part = body.strip()
        dirs_part = ""

    # Films: split on " / " for double features
    film_chunks = [c.strip() for c in films_part.split(" / ")]
    films: list[Film] = []
    for chunk in film_chunks:
        tm = TITLE_YEAR_RE.match(chunk)
        if tm:
            films.append(Film(title=tm.group("title").strip(), year=int(tm.group("year"))))
        else:
            films.append(Film(title=chunk.strip(), year=None))

    # Directors: split on " / " first (for different directors per film),
    # then within each chunk, strip "dir." or "dirs." and split on "," for
    # multi-directors.
    directors: list[str] = []
    if dirs_part:
        for chunk in dirs_part.split(" / "):
            chunk = chunk.strip()
            chunk = re.sub(r"^dirs?\.\s*", "", chunk)
            for name in chunk.split(","):
                name = name.strip()
                if name:
                    directors.append(name)

    return films, directors


def parse_entry(weekday: str, day: date, line: str) -> Screening | None:
    m = ENTRY_START_RE.match(line)
    if not m:
        return None
    times_raw = m.group("times")
    times = [t.strip() for t in times_raw.split(",")]
    rest = m.group("rest").strip()

    # Strip presenter suffix
    rest, presenter = _strip_presenter(rest)
    # Strip notes
    rest, notes = _extract_notes(rest)

    # The remaining string should be:
    #   "TITLE (YEAR), dir. NAME • THEATER"
    # Split on the LAST " • " -- theater is the final bullet-delimited segment.
    # (Titles don't contain " • " so this is safe.)
    if " • " not in rest:
        # Could be an entry that has {notes} as the theater replacement (e.g.
        # open screen events). Treat the whole thing as the title/theater
        # unknown.
        body = rest
        theater = ""
    else:
        idx = rest.rfind(" • ")
        body = rest[:idx].strip()
        theater = rest[idx + 3:].strip()

    # Clean up dangling punctuation/commas on body
    body = body.rstrip(",").strip()

    films, directors = _parse_films_and_directors(body)

    return Screening(
        day=day,
        weekday=weekday,
        times=times,
        films=films,
        directors=directors,
        theater=theater,
        notes=notes,
        presenter=presenter,
        raw=line,
    )


# ---- Top-level --------------------------------------------------------------

def parse_revival_hub_text(text: str, reference_year: int | None = None) -> list[Screening]:
    """Parse the full email text into a list of Screening records.

    reference_year: the calendar year for the week (defaults to current year).
    The email doesn't include the year in day headers, only the month/day.
    """
    if reference_year is None:
        reference_year = date.today().year

    chunks = reassemble_entries(text)

    screenings: list[Screening] = []
    current_weekday: str | None = None
    current_date: date | None = None

    for kind, content in chunks:
        if kind == "day":
            m = DAY_HEADER_RE.match(content)
            if m:
                current_weekday = m.group("weekday")
                month = MONTH_TO_NUM[m.group("month")]
                dd = int(m.group("day"))
                current_date = date(reference_year, month, dd)
        elif kind == "entry":
            if current_date is None or current_weekday is None:
                continue
            scr = parse_entry(current_weekday, current_date, content)
            if scr is not None and scr.films:
                screenings.append(scr)
    return screenings


def parse_revival_hub_pdf(pdf_path: str, reference_year: int | None = None) -> list[Screening]:
    text = extract_text_from_pdf(pdf_path)
    return parse_revival_hub_text(text, reference_year=reference_year)


# ---- CLI self-test ---------------------------------------------------------

if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else \
        "/sessions/zealous-inspiring-turing/mnt/movies/reference-docs/Gmail - Playing This Week in LA.pdf"
    screenings = parse_revival_hub_pdf(path, reference_year=2026)
    print(f"Parsed {len(screenings)} screenings")

    # Distribution by day
    from collections import Counter
    by_day = Counter(s.weekday for s in screenings)
    for weekday, count in by_day.items():
        print(f"  {weekday}: {count}")

    print("\nFirst 3 entries:")
    for s in screenings[:3]:
        print(f"  {s.day} {s.times} | {[(f.title, f.year) for f in s.films]} | dirs={s.directors} | theater={s.theater!r} | notes={s.notes!r} | presenter={s.presenter!r}")

    print("\nSample double feature:")
    for s in screenings:
        if len(s.films) > 1:
            print(f"  {s.raw}")
            print(f"  -> films={[(f.title, f.year) for f in s.films]} dirs={s.directors}")
            break
