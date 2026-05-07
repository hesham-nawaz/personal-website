"""Fetch the most recent Revival Hub LA weekly newsletter via Gmail IMAP.

Replaces the Cowork Gmail MCP for unattended runs (e.g. GitHub Actions).

Auth: Gmail App Password (not your account password). Generate one at
https://myaccount.google.com/apppasswords (requires 2FA). Pass via env vars:

    GMAIL_USER             — your Gmail address (e.g. heshamnawaz2000@gmail.com)
    GMAIL_APP_PASSWORD     — the 16-char app password (with or without spaces)

Usage:
    python fetch_email.py --output reports/.inbox-YYYY-MM-DD.txt --print-week-label

Behavior:
    - Connects to imap.gmail.com:993 over SSL
    - Searches All Mail for: FROM chandler@revivalhubla.com SUBJECT "Playing
      This Week in LA" within the last 14 days
    - Picks the most recent match
    - Extracts the text/plain body (decoded), writes it to --output
    - With --print-week-label, also prints the YYYY-MM-DD label of the Sunday
      that ends the email's week, on stdout (one line). The workflow uses this
      to name the inbox file and the report.

Exit codes:
    0  success
    1  no matching email found in the search window
    2  auth/connection error
    3  unexpected error
"""
from __future__ import annotations

import argparse
import email
import email.header
import email.utils
import imaplib
import os
import sys
from datetime import datetime, timedelta, timezone
from email.message import Message
from pathlib import Path


IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
SENDER = "chandler@revivalhubla.com"
SUBJECT = "Playing This Week in LA"
SEARCH_DAYS = 14


def _decode_header(value: str | None) -> str:
    if not value:
        return ""
    parts = email.header.decode_header(value)
    out = []
    for chunk, enc in parts:
        if isinstance(chunk, bytes):
            try:
                out.append(chunk.decode(enc or "utf-8", errors="replace"))
            except LookupError:
                out.append(chunk.decode("utf-8", errors="replace"))
        else:
            out.append(chunk)
    return "".join(out)


def _extract_text(msg: Message) -> str:
    """Return the text/plain body of msg, decoded."""
    if msg.is_multipart():
        # Prefer text/plain; fall back to first text/* part.
        plain = None
        any_text = None
        for part in msg.walk():
            ctype = part.get_content_type()
            if part.get("Content-Disposition", "").startswith("attachment"):
                continue
            if ctype == "text/plain" and plain is None:
                plain = part
            elif ctype.startswith("text/") and any_text is None:
                any_text = part
        target = plain or any_text
        if target is None:
            return ""
        payload = target.get_payload(decode=True) or b""
        charset = target.get_content_charset() or "utf-8"
    else:
        payload = msg.get_payload(decode=True) or b""
        charset = msg.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _week_label_from_date(d: datetime) -> str:
    """Return YYYY-MM-DD for the Sunday that ends the week containing d.

    Revival Hub goes out Mondays for that Mon–Sun week, so for a Monday email,
    Sunday is +6 days; for a Sunday email it's +0; etc.
    """
    # Monday = 0, Sunday = 6 in Python's weekday()
    days_to_sunday = (6 - d.weekday()) % 7
    sunday = d + timedelta(days=days_to_sunday)
    return sunday.strftime("%Y-%m-%d")


def fetch(output_path: Path, print_week_label: bool) -> None:
    user = os.environ.get("GMAIL_USER")
    pw = os.environ.get("GMAIL_APP_PASSWORD")
    if not user or not pw:
        print("ERROR: GMAIL_USER and GMAIL_APP_PASSWORD env vars required", file=sys.stderr)
        sys.exit(2)
    pw = pw.replace(" ", "")  # Google sometimes shows the password with spaces

    try:
        M = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    except Exception as e:
        print(f"ERROR: IMAP connect failed: {e}", file=sys.stderr)
        sys.exit(2)

    try:
        M.login(user, pw)
    except imaplib.IMAP4.error as e:
        print(f"ERROR: IMAP auth failed: {e}", file=sys.stderr)
        sys.exit(2)

    try:
        # Use the "All Mail" folder so we still find it if it was archived.
        # Gmail's special-use name is [Gmail]/All Mail; the X-GM-LABELS approach
        # would also work, but ALL is portable.
        status, _ = M.select('"[Gmail]/All Mail"', readonly=True)
        if status != "OK":
            # Fallback to INBOX if the All Mail folder name is localized.
            M.select("INBOX", readonly=True)

        since = (datetime.now(timezone.utc) - timedelta(days=SEARCH_DAYS)).strftime("%d-%b-%Y")
        # IMAP search criteria: FROM, SUBJECT (substring), SINCE date.
        status, data = M.search(None, "FROM", f'"{SENDER}"', "SUBJECT", f'"{SUBJECT}"', "SINCE", since)
        if status != "OK":
            print(f"ERROR: IMAP search failed: {status}", file=sys.stderr)
            sys.exit(3)
        ids = data[0].split() if data and data[0] else []
        if not ids:
            print("No matching Revival Hub email in the last "
                  f"{SEARCH_DAYS} days.", file=sys.stderr)
            sys.exit(1)

        # Most recent first. UIDs aren't strictly chronological but Gmail's
        # message sequence numbers in the All-Mail folder are by arrival order.
        latest_id = ids[-1]
        status, msg_data = M.fetch(latest_id, "(RFC822)")
        if status != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
            print(f"ERROR: IMAP fetch failed: {status}", file=sys.stderr)
            sys.exit(3)
        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)

        subject = _decode_header(msg.get("Subject"))
        date_hdr = msg.get("Date")
        try:
            email_date = email.utils.parsedate_to_datetime(date_hdr)
        except Exception:
            email_date = datetime.now(timezone.utc)
        if email_date.tzinfo is None:
            email_date = email_date.replace(tzinfo=timezone.utc)

        body = _extract_text(msg)
        if not body.strip():
            print("ERROR: matched email has empty body", file=sys.stderr)
            sys.exit(3)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(body, encoding="utf-8")

        week_label = _week_label_from_date(email_date)
        if print_week_label:
            print(week_label)
        # Send a status line to stderr so it shows in workflow logs but doesn't
        # pollute stdout (which is reserved for the week label).
        print(
            f"Saved {len(body)} chars from {SENDER} • "
            f"subject={subject!r} • date={email_date.isoformat()} • "
            f"week_label={week_label} -> {output_path}",
            file=sys.stderr,
        )
    finally:
        try:
            M.logout()
        except Exception:
            pass


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", required=True,
                    help="Path to write the plaintext email body to.")
    ap.add_argument("--print-week-label", action="store_true",
                    help="Print the YYYY-MM-DD week label (Sunday) on stdout.")
    args = ap.parse_args()

    fetch(Path(args.output), args.print_week_label)


if __name__ == "__main__":
    main()
