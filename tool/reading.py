"""Sync the Blogpost/Paper Review Google Doc into reading.html.

Pipeline:  Doc tabs (HTML export) + tags sheet (CSV) → entries → HTML → splice.

The Doc, the tags sheet and the Professional Reading sheet are all shared
"anyone with the link can view", so plain export URLs work with no
credentials — the same trick the NYC screenings job relies on. Stdlib only.

Where each piece of an entry comes from:
  - Title, company, link and the review itself: the Doc. Each review starts
    with a "(Company) Title" Heading 2, and its sections are Heading 3s.
  - Tags, year and type: the "Tags" tab of the tags sheet, matched on title.
    The fetched sheet is snapshotted to data/reading-tags.csv, and that
    snapshot is used if Google can't be reached.
  - Reviews not yet in the tags sheet fall back to the Doc's own "Tags"
    section for tags, and to the Professional Reading sheet for year/type.

The Doc is exported as a zip (HTML plus an images/ folder) rather than as
Markdown, because the Markdown export flattens lists and paragraphs inside
table cells into one run-on line.

The page is only rewritten when the rendered content changes, so "last
updated" means the content changed that day, and a run with no edits makes
no commit.

Usage:
    python reading.py --page ../reading.html --img-dir ../reading/img --data-dir ../data
    python reading.py ... --offline            # use the CSV snapshots, don't fetch sheets
    python reading.py ... --doc-zip a.zip b.zip  # use local exports instead of the Doc
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import html
import io
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

DOC_ID = "1MoHxxzKPypEcNDErs5WYdY1MbCm3iPHucDArgeVohhw"

# Tabs to publish, newest first: entries from earlier tabs are listed first.
# To publish another tab, open it in the Doc and copy the "tab=t.…" part of
# the URL.
TABS = [
    ("t.g3qwplq2deug", "Applications (Part 2)"),
    ("t.hxbm1vl61my8", "Applications"),
]

TAGS_SHEET_ID = "1SdU8kIUH_GjB6xETjkL2gycRju665JJRZiiag0ax7V4"
TAGS_SHEET_TABS = {"tags": "Tags", "definitions": "Tag definitions"}

READING_SHEET_ID = "1l1c__X_FdnW8i28yu3s7XoVcGAqCQt9RtOIzM2_q75U"
READING_SHEET_TAB = "Blogposts and Papers"

START_MARKER = "<!-- READING:START -->"
END_MARKER = "<!-- READING:END -->"

# Template prompts that sit in every new review; unanswered, they're noise.
TEMPLATE_PROMPTS = {
    "how would i convince a business leader that this project was worth doing?",
    "how would you improve this system if you were building it today?",
}
# Words from the review template that don't count as content on their own.
TEMPLATE_WORDS = re.compile(
    r"\b(data|architecture|evaluation|metric\(s\)|results|deployment|post-deployment|"
    r"context|problem|solution|constraints|tradeoffs|question|answer|tag_name)\b",
    re.I,
)


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------

def fetch(url: str, expect: str, attempts: int = 3) -> bytes:
    """GET a Google export URL; `expect` is a substring of the content type.

    A file that isn't link-shared comes back as a 200 sign-in page, so the
    content type is the only reliable signal that the export worked.
    """
    last: Exception | None = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "reading-sync"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                ctype = resp.headers.get("Content-Type", "")
                body = resp.read()
            if expect not in ctype:
                raise RuntimeError(f"expected {expect}, got {ctype!r} (is the file link-shared?)")
            return body
        except urllib.error.HTTPError as e:
            if e.code < 500:  # not shared, or a bad ID: retrying won't help
                raise RuntimeError(f"fetch failed for {url}: {e}") from e
            last = e
            time.sleep(2 * (i + 1))
        except (urllib.error.URLError, RuntimeError, TimeoutError) as e:
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"fetch failed for {url}: {last}")


def fetch_tab_zip(tab_id: str) -> bytes:
    return fetch(f"https://docs.google.com/document/d/{DOC_ID}/export?format=zip&tab={tab_id}", "zip")


# --------------------------------------------------------------------------
# Company logos
# --------------------------------------------------------------------------
#
# Each company's icon is fetched once from its own site and committed under
# reading/logos/, so the page never calls a third-party logo service. The
# company → domain map lives in data/reading-companies.csv; a new company
# gets a row guessed from its review's link. A "Logo URL" in that file
# overrides whatever the site publishes, and "none" means no logo (the card
# shows the company's initial). Only roughly square icons are used. Delete a
# logo file to refetch it.

# Hosts that publish other people's posts, so their icon isn't the company's.
SHARED_HOSTS = {"medium.com", "arxiv.org", "linkedin.com", "github.com", "github.io", "substack.com",
                "youtube.com", "youtu.be", "amazonaws.com", "google.com", "notion.site", "x.com",
                "twitter.com", "openreview.net", "aclanthology.org", "acm.org", "sciencedirect.com",
                "researchgate.net", "huggingface.co"}
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
IMAGE_EXTS = {"image/png": "png", "image/svg+xml": "svg", "image/x-icon": "ico",
              "image/vnd.microsoft.icon": "ico", "image/jpeg": "jpg", "image/webp": "webp"}


def site_domain(url: str) -> str:
    """'https://build.forus.com/post' → 'forus.com'; '' for shared hosts."""
    parts = (urllib.parse.urlparse(url).hostname or "").lower().split(".")
    domain = ".".join(parts[-2:]) if len(parts) >= 2 else ""
    return "" if domain in SHARED_HOSTS else domain


def get_once(url: str) -> tuple[bytes, str, str]:
    req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read(), resp.headers.get("Content-Type", ""), resp.geturl()


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def image_size(data: bytes, ext: str) -> tuple[int, int] | None:
    """(width, height) of a PNG, the largest ICO frame, or an SVG's viewBox."""
    if data[:8] == PNG_SIGNATURE:
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if ext == "ico" and len(data) >= 22:
        frames = [(data[6 + 16 * i] or 256, data[7 + 16 * i] or 256)
                  for i in range(int.from_bytes(data[4:6], "little")) if 8 + 16 * i <= len(data)]
        return max(frames, default=None)
    if ext == "svg":
        head = data[:4000].decode("utf-8", "replace")
        box = re.search(r'viewBox=["\']\s*[-\d.]+[\s,]+[-\d.]+[\s,]+([\d.]+)[\s,]+([\d.]+)', head)
        if box:
            return round(float(box.group(1))), round(float(box.group(2)))
        w = re.search(r'<svg\b[^>]*\bwidth=["\']([\d.]+)', head)
        h = re.search(r'<svg\b[^>]*\bheight=["\']([\d.]+)', head)
        return (round(float(w.group(1))), round(float(h.group(1)))) if w and h else None
    return None


def png_pixels(data: bytes) -> tuple[int, int, list[list[tuple[int, int, int, int]]]] | None:
    """Decode an 8-bit, non-interlaced PNG to rows of RGBA pixels, or None
    for layouts this doesn't handle (the logo is then judged by size alone)."""
    pos, ihdr, idat, plte, trns = 8, b"", b"", b"", b""
    while pos + 8 <= len(data):
        n, kind = int.from_bytes(data[pos:pos + 4], "big"), data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + n]
        if kind == b"IHDR":
            ihdr = body
        elif kind == b"IDAT":
            idat += body
        elif kind == b"PLTE":
            plte = body
        elif kind == b"tRNS":
            trns = body
        elif kind == b"IEND":
            break
        pos += 12 + n
    if len(ihdr) < 13 or ihdr[8] != 8 or ihdr[12] != 0:
        return None
    w, h, color = int.from_bytes(ihdr[0:4], "big"), int.from_bytes(ihdr[4:8], "big"), ihdr[9]
    bpp = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color)
    if not bpp or (color == 3 and not plte):
        return None
    try:
        raw = zlib.decompress(idat)
    except zlib.error:
        return None
    stride, prev, rows = w * bpp, bytearray(w * bpp), []
    for y in range(h):
        start = y * (stride + 1)
        kind, row = raw[start], bytearray(raw[start + 1:start + 1 + stride])
        for i in range(stride):  # undo PNG's per-row filter
            a = row[i - bpp] if i >= bpp else 0
            b, c = prev[i], (prev[i - bpp] if i >= bpp else 0)
            if kind == 1:
                row[i] = (row[i] + a) & 255
            elif kind == 2:
                row[i] = (row[i] + b) & 255
            elif kind == 3:
                row[i] = (row[i] + (a + b) // 2) & 255
            elif kind == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                row[i] = (row[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        prev = row
        if color == 6:
            rows.append([tuple(row[i:i + 4]) for i in range(0, stride, 4)])
        elif color == 2:
            rows.append([(*row[i:i + 3], 255) for i in range(0, stride, 3)])
        elif color == 4:
            rows.append([(row[i], row[i], row[i], row[i + 1]) for i in range(0, stride, 2)])
        elif color == 0:
            rows.append([(v, v, v, 255) for v in row])
        else:
            rows.append([(*plte[3 * v:3 * v + 3], trns[v] if v < len(trns) else 255) for v in row])
    return w, h, rows


def ico_png_frame(data: bytes) -> bytes | None:
    """The largest frame of an ICO, if that frame is stored as a PNG."""
    best = None
    for i in range(int.from_bytes(data[4:6], "little")):
        e = 6 + 16 * i
        if e + 16 > len(data):
            break
        width = data[e] or 256
        size, offset = int.from_bytes(data[e + 8:e + 12], "little"), int.from_bytes(data[e + 12:e + 16], "little")
        if best is None or width > best[0]:
            best = (width, data[offset:offset + size])
    return best[1] if best and best[1][:8] == PNG_SIGNATURE else None


def logo_problem(data: bytes, ext: str, check_content: bool = True) -> str:
    """Why a logo wouldn't fit a small square on the page, or "" if it's fine.

    The canvas must be about square. Then, reading the pixels where possible:
    a colored tile (rounded square or circle) is fine as long as its mark sits
    inside it rather than running off the edge (a cropped wordmark does);
    otherwise the visible shape must itself be roughly square (a wordmark on a
    clear or white background isn't).
    """
    size = image_size(data, ext)
    if not size or not size[1] or not 0.8 <= size[0] / size[1] <= 1.25:
        return f"canvas isn't square ({size})"
    if not check_content:
        return ""
    png = data if ext == "png" else ico_png_frame(data) if ext == "ico" else None
    px = png_pixels(png) if png else None
    if not px:
        return ""
    w, h, rows = px

    def box(hit) -> tuple[int, int, int, int] | None:
        xs, ys = [], []
        for y, row in enumerate(rows):
            on = [x for x, p in enumerate(row) if hit(p)]
            if on:
                xs += [on[0], on[-1]]
                ys.append(y)
        return (min(xs), min(ys), max(xs), max(ys)) if xs else None

    shape = box(lambda p: p[3] > 24)
    if not shape:
        return "blank"
    x0, y0, x1, y1 = shape
    opaque = [p for row in rows[y0:y1 + 1] for p in row[x0:x1 + 1] if p[3] > 24]
    # A tile (rounded square, circle) fills most of its box; a mark on a clear
    # background, like a grid of dots, doesn't.
    if len(opaque) >= 0.75 * (x1 - x0 + 1) * (y1 - y0 + 1):
        bucket = lambda p: (p[0] // 32, p[1] // 32, p[2] // 32)
        counts = collections.Counter(map(bucket, opaque))
        common = counts.most_common(1)[0][0]
        same = [p for p in opaque if bucket(p) == common]
        bg = [sum(p[i] for p in same) / len(same) for i in range(3)]
        mark = box(lambda p: p[3] > 24 and sum(abs(p[i] - bg[i]) for i in range(3)) > 60)
        if min(bg) < 225:  # a colored tile is the visible square; its mark must sit inside it
            if not mark:
                return ""
            margin = max(1, (x1 - x0) // 64)
            if mark[0] - x0 < margin or mark[1] - y0 < margin or x1 - mark[2] < margin or y1 - mark[3] < margin:
                return "the mark runs off the edge of its tile (cut off)"
            return ""
        if not mark:  # a plain white square
            return "blank"
        x0, y0, x1, y1 = mark  # a white tile blends into the page; what shows is the mark
    ratio = (x1 - x0 + 1) / (y1 - y0 + 1)
    return "" if 0.4 <= ratio <= 2.5 else f"the visible shape isn't square (width/height {ratio:.1f})"


def logo_candidates(domain: str) -> list[str]:
    urls = []
    try:
        page, _, final = get_once(f"https://{domain}/")
        for tag in re.findall(r"<link\b[^>]*>", page.decode("utf-8", "replace"), re.I):
            rel = re.search(r'\brel=["\']?([^"\'>]+)', tag, re.I)
            href = re.search(r'\bhref=["\']?([^"\'\s>]+)', tag, re.I)
            if rel and href and "icon" in rel.group(1).lower() and "mask" not in rel.group(1).lower():
                urls.append(urllib.parse.urljoin(final, html.unescape(href.group(1))))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        pass
    urls += [f"https://{domain}/apple-touch-icon.png", f"https://{domain}/favicon.ico",
             f"https://www.google.com/s2/favicons?domain={domain}&sz=128"]
    return list(dict.fromkeys(urls))


def fetch_logo(domain: str, override: str = "") -> tuple[tuple[bytes, str] | None, bool]:
    """The sharpest icon that fits a square, from the site's declared icons and
    the usual fallbacks. Returns (icon or None, whether icons were found but
    none fit), so a temporary network failure isn't mistaken for "no logo"."""
    fetched = []
    for url in [override] if override else logo_candidates(domain):
        try:
            data, ctype, _ = get_once(url)
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            continue
        ext = IMAGE_EXTS.get(ctype.split(";")[0].strip().lower())
        if ext and len(data) >= 100:
            readable = ext == "png" or (ext == "ico" and ico_png_frame(data) is not None)
            fetched.append((data, ext, logo_problem(data, ext), readable))
    # If the site's artwork failed a pixel check, an SVG or old-style ICO of the
    # same artwork can't be trusted either, since those can't be checked.
    artwork_failed = any(problem and readable and "canvas" not in problem
                         for _, _, problem, readable in fetched)
    usable = [(d, e) for d, e, problem, readable in fetched if not problem and (readable or not artwork_failed)]
    if not usable:
        return None, bool(fetched)
    return max(usable, key=lambda c: 1024 if c[1] == "svg" else image_size(c[0], c[1])[0]), False


def fetch_sheet_tabs(sheet_id: str) -> dict[str, str]:
    """Every tab of a sheet as CSV text, keyed by tab name.

    Exported as .xlsx so tabs can be found by name (the CSV export wants a
    numeric tab id) and values come through exactly as typed.
    """
    data = fetch(f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=xlsx", "spreadsheetml")
    out = {}
    for name, rows in read_xlsx(data).items():
        buf = io.StringIO()
        csv.writer(buf, lineterminator="\n").writerows(rows)
        out[name] = buf.getvalue()
    return out


XLSX_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
           "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
           "rel": "http://schemas.openxmlformats.org/package/2006/relationships"}


def read_xlsx(data: bytes) -> dict[str, list[list[str]]]:
    m, ns = "{%s}" % XLSX_NS["m"], XLSX_NS

    def col_index(ref: str) -> int:
        n = 0
        for ch in re.match(r"[A-Z]+", ref).group(0):
            n = n * 26 + ord(ch) - 64
        return n - 1

    tabs: dict[str, list[list[str]]] = {}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns):
                shared.append("".join(t.text or "" for t in si.iter(m + "t")))
        rels = {r.get("Id"): r.get("Target")
                for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels")).findall("rel:Relationship", ns)}
        for sheet in ET.fromstring(z.read("xl/workbook.xml")).find("m:sheets", ns):
            target = rels[sheet.get("{%s}id" % ns["r"])]
            path = target.lstrip("/") if target.startswith("/") else f"xl/{target}"
            rows = []
            for row in ET.fromstring(z.read(path)).iter(m + "row"):
                cells = {}
                for c in row.findall("m:c", ns):
                    kind = c.get("t")
                    if kind == "s":
                        value = shared[int(c.findtext("m:v", "0", ns))]
                    elif kind == "inlineStr":
                        value = "".join(t.text or "" for t in c.iter(m + "t"))
                    else:
                        value = c.findtext("m:v", "", ns)
                        if re.fullmatch(r"-?\d+\.0", value):
                            value = value[:-2]
                    cells[col_index(c.get("r"))] = value
                if any(v.strip() for v in cells.values()):
                    rows.append([cells.get(i, "") for i in range(max(cells) + 1)])
            tabs[sheet.get("name")] = rows
    return tabs


# --------------------------------------------------------------------------
# Reading Google's HTML export
# --------------------------------------------------------------------------

class Node:
    __slots__ = ("tag", "attrs", "children")

    def __init__(self, tag: str, attrs: dict[str, str]):
        self.tag, self.attrs, self.children = tag, attrs, []

    def classes(self) -> list[str]:
        return self.attrs.get("class", "").split()


VOID = {"br", "img", "hr", "meta", "link", "col", "input"}


class TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("root", {})
        self.stack = [self.root]
        self.style = []

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: v or "" for k, v in attrs})
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        if self.stack[-1].tag == "style":
            self.style.append(data)
        elif self.stack[-1].tag not in ("head", "title", "script"):
            self.stack[-1].children.append(data)


def find(node: Node, tag: str) -> Node | None:
    for c in node.children:
        if isinstance(c, Node):
            if c.tag == tag:
                return c
            hit = find(c, tag)
            if hit:
                return hit
    return None


def walk(node: Node):
    for c in node.children:
        if isinstance(c, Node):
            yield c
            yield from walk(c)


def own_rows(table: Node) -> list[Node]:
    """A table's rows, not those of tables nested in its cells."""
    rows = []
    for c in table.children:
        if isinstance(c, Node):
            if c.tag == "tr":
                rows.append(c)
            elif c.tag in ("thead", "tbody", "tfoot"):
                rows += [r for r in c.children if isinstance(r, Node) and r.tag == "tr"]
    return rows


def text_of(node: Node | str) -> str:
    if isinstance(node, str):
        return node
    return "".join(text_of(c) for c in node.children)


def plain_text(node: Node | str) -> str:
    """Like text_of, but block elements are separated by spaces."""
    if isinstance(node, str):
        return node
    inner = "".join(plain_text(c) for c in node.children)
    return f" {inner} " if node.tag in ("p", "li", "br", "td", "th", "h1", "h2", "h3", "h4") else inner


def clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()


def parse_formats(css: str) -> dict[str, set[str]]:
    """Map Google's generated class names to the formatting worth keeping."""
    fmt: dict[str, set[str]] = {}
    for cls, body in re.findall(r"\.(c\d+)\{([^}]*)\}", css):
        flags = set()
        if re.search(r"font-weight:\s*(700|bold)", body):
            flags.add("strong")
        if "font-style:italic" in body:
            flags.add("em")
        if "line-through" in body:
            flags.add("s")
        if re.search(r'font-family:\s*"?(Courier|Consolas|Roboto Mono|Source Code)', body):
            flags.add("code")
        if "vertical-align:super" in body:
            flags.add("sup")
        if "vertical-align:sub" in body:
            flags.add("sub")
        if flags:
            fmt[cls] = flags
    return fmt


def unwrap_href(href: str) -> str:
    """Google routes every link through google.com/url?q=…; undo that."""
    if href.startswith("https://www.google.com/url"):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("q")
        if q:
            return q[0]
    return href


class Renderer:
    """Turns export nodes into plain, class-free HTML for the site."""

    def __init__(self, fmt: dict[str, set[str]], images: dict[str, str]):
        self.fmt, self.images = fmt, images

    def children(self, nodes: list) -> str:
        out, run = [], []
        for n in nodes + [None]:
            if isinstance(n, Node) and n.tag in ("ul", "ol"):
                run.append(n)
                continue
            if run:
                out.append(self.lists(run))
                run = []
            if n is not None:
                out.append(self.node(n))
        return "".join(out)

    def node(self, n) -> str:
        if isinstance(n, str):
            return html.escape(n.replace("\xa0", " "), quote=False)
        tag, inner = n.tag, None
        if tag == "span":
            inner = self.children(n.children)
            if not inner:
                return ""
            flags = set().union(*(self.fmt.get(c, set()) for c in n.classes()))
            for f in ("code", "strong", "em", "s", "sup", "sub"):
                if f in flags and inner.strip():
                    inner = f"<{f}>{inner}</{f}>"
            return inner
        if tag == "a":
            inner = self.children(n.children)
            href = unwrap_href(n.attrs.get("href", ""))
            if href.startswith("#cmnt") or n.attrs.get("id", "").startswith("cmnt"):
                return ""  # comment markers
            if not href.startswith(("http://", "https://", "mailto:")):
                return inner
            return f'<a href="{html.escape(href)}" target="_blank" rel="noopener">{inner}</a>'
        if tag == "br":
            return "<br>"
        if tag == "img":
            src = self.images.get(n.attrs.get("src", ""))
            return f'<img src="{src}" alt="Figure from the post" loading="lazy" decoding="async">' if src else ""
        if tag == "p":
            inner = self.children(n.children)
            return f"<p>{inner}</p>" if inner.strip() else ""
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            inner = clean_text(text_of(n))
            return f"<h4>{html.escape(inner)}</h4>" if inner else ""
        if tag == "table":
            return self.table(n)
        if tag in ("sup", "sub"):
            inner = self.children(n.children)
            return f"<{tag}>{inner}</{tag}>" if inner.strip() else ""
        if tag in ("hr", "div", "style", "head"):
            return ""
        return self.children(n.children)

    def lists(self, blocks: list[Node]) -> str:
        """Google exports nested lists as flat sibling lists whose class ends
        in -0, -1, … for the depth; rebuild the nesting."""
        roots: list[dict] = []
        stack: list[dict] = []
        for b in blocks:
            m = re.search(r"lst-kix_(\w+)-(\d+)", b.attrs.get("class", ""))
            list_id, level = (m.group(1), int(m.group(2))) if m else ("", 0)
            level = min(level, len(stack))
            del stack[level + 1:]
            current = stack[level] if level < len(stack) else None
            if not current or current["tag"] != b.tag or current["id"] != list_id:
                current = {"tag": b.tag, "id": list_id, "start": b.attrs.get("start", "1"), "items": []}
                parent = stack[level - 1]["items"][-1]["sub"] if level and stack[level - 1]["items"] else roots
                parent.append(current)
                stack[level:] = [current]
            for li in b.children:
                if isinstance(li, Node) and li.tag == "li":
                    current["items"].append({"html": self.children(li.children), "sub": []})

        def emit(lst: dict) -> str:
            items = []
            for it in lst["items"]:
                sub = "".join(emit(s) for s in it["sub"])
                label = clean_text(html.unescape(re.sub(r"<[^>]+>", "", it["html"]))).rstrip(":").strip()
                # Unfilled template bullets ("Architecture" with nothing under it).
                if not sub and (not label or TEMPLATE_WORDS.fullmatch(label)):
                    continue
                items.append(f"<li>{it['html']}{sub}</li>")
            items = "".join(items)
            if not items:
                return ""
            start = f' start="{lst["start"]}"' if lst["tag"] == "ol" and lst["start"] not in ("", "1") else ""
            return f"<{lst['tag']}{start}>{items}</{lst['tag']}>"

        return "".join(emit(r) for r in roots)

    def table(self, t: Node) -> str:
        rows = [[c for c in tr.children if isinstance(c, Node) and c.tag in ("td", "th")]
                for tr in own_rows(t)]
        rows = [r for r in rows if r]
        kept = []
        for r in rows:
            texts = [clean_text(text_of(c)) for c in r]
            has_img = any(x.tag == "img" for c in r for x in walk(c))
            if not any(texts) and not has_img:
                continue
            # A label or template prompt with nothing next to it.
            if len(r) == 2 and not texts[1] and not has_img and (
                    texts[0].lower() in TEMPLATE_PROMPTS or len(texts[0]) <= 28):
                continue
            kept.append((r, texts))
        if not kept or (len(kept) == 1 and all(TEMPLATE_WORDS.fullmatch(x) or not x for x in kept[0][1])):
            return ""

        two_col = all(len(r) == 2 for r, _ in kept)
        if two_col and max(len(x[0]) for _, x in kept) <= 28:
            kind, header = "kv-table", False
        else:
            kind = "qa-table" if two_col else "grid-table"
            header = len(kept) > 1 and all(len(x) <= 30 for x in kept[0][1])

        def cells(r, tag):
            out = []
            for c in r:
                span = "".join(f' {a}="{c.attrs[a]}"' for a in ("colspan", "rowspan")
                               if c.attrs.get(a, "1") not in ("", "1"))
                out.append(f"<{tag}{span}>{self.children(c.children)}</{tag}>")
            return "".join(out)

        body = kept[1:] if header else kept
        thead = f"<thead><tr>{cells(kept[0][0], 'th')}</tr></thead>" if header else ""
        tbody = "".join(f"<tr>{cells(r, 'td')}</tr>" for r, _ in body)
        return f'<div class="table-wrap"><table class="{kind}">{thead}<tbody>{tbody}</tbody></table></div>'


# --------------------------------------------------------------------------
# Splitting the Doc into entries
# --------------------------------------------------------------------------

@dataclass
class Entry:
    company: str
    title: str
    url: str | None
    body_html: str
    doc_tags: list[str] = field(default_factory=list)
    excerpt: str = ""
    tags: list[str] = field(default_factory=list)
    year: str = ""
    type: str = ""
    slug: str = ""
    logo: str = ""


def norm_title(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).lower().replace("\\", "")
    s = re.sub(r"[‘’“”\"']", "", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def slugify(s: str, limit: int = 80) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    if len(s) > limit:
        s = s[:limit].rsplit("-", 1)[0]
    return s


def is_empty_html(fragment: str) -> bool:
    if "<img" in fragment:
        return False
    text = html.unescape(re.sub(r"<[^>]+>", " ", fragment))
    text = re.sub(r"^\s*\{[^}]*\}\s*$", "", text)  # a lone "{to add}" placeholder
    text = TEMPLATE_WORDS.sub("", text)
    return not re.search(r"[A-Za-z0-9]", text)


def clean_heading(h: str) -> str:
    return re.sub(r"\s*\(~\s*\d*\s*paragraphs?\)", "", clean_text(h)).strip()


def kv_value(nodes: list[Node], label: str) -> str:
    """Text of the cell next to `label` in a summary table, if any."""
    for n in nodes:
        for tr in [r for t in [n, *walk(n)] if t.tag == "table" for r in own_rows(t)]:
            cells = [c for c in tr.children if isinstance(c, Node) and c.tag in ("td", "th")]
            if len(cells) == 2 and clean_text(text_of(cells[0])).lower() == label.lower():
                return clean_text(plain_text(cells[1]))
    return ""


def excerpt_of(nodes: list[Node], limit: int = 240) -> str:
    text = kv_value(nodes, "Problem") or kv_value(nodes, "Context")
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip(",;:—-") + "…"
    return text


def parse_doc_tags(text: str) -> list[str]:
    tags = [t.strip(" <>*") for t in re.split(r"[,;\n]", text)]
    return [t for t in tags if t and t.lower() != "tag_name"]


def parse_export(page: str, images: dict[str, str]) -> list[Entry]:
    builder = TreeBuilder()
    builder.feed(page)
    body = find(builder.root, "body") or builder.root
    r = Renderer(parse_formats("".join(builder.style)), images)

    # Group the flat body into entries (Heading 2) and sections (Heading 3).
    raw: list[dict] = []
    for n in body.children:
        if not isinstance(n, Node):
            continue
        if n.tag in ("h1", "h2"):
            raw.append({"heading": clean_text(text_of(n)), "pre": [], "sections": []})
            if n.tag == "h1":
                raw[-1]["heading"] = ""  # a tab-level title, not a review
        elif not raw:
            continue
        elif n.tag == "h3":
            raw[-1]["sections"].append({"heading": clean_heading(text_of(n)), "nodes": []})
        elif raw[-1]["sections"]:
            raw[-1]["sections"][-1]["nodes"].append(n)
        else:
            raw[-1]["pre"].append(n)

    entries = []
    for e in raw:
        heading = e["heading"]
        if not heading or "Template_" in heading:
            continue
        m = re.match(r"\((.+?)\)\s*(.+)$", heading)
        company, title = (m.group(1).strip(), m.group(2).strip()) if m else ("", heading)

        # The link to the original sits right under the heading. A bare link
        # line moves into the entry's header; labeled ones ("Paper: …") stay
        # in the notes and the first of them becomes the header link.
        pre = [n for n in e["pre"] if clean_text(text_of(n)) or any(x.tag == "img" for x in walk(n))]
        url = None
        links = [a for n in pre for a in walk(n) if a.tag == "a" and a.attrs.get("href")]
        if links:
            url = unwrap_href(links[0].attrs["href"])
            first = pre[0]
            if first.tag == "p" and clean_text(text_of(first)) == clean_text(text_of(links[0])):
                pre = pre[1:]

        parts = [r.children(pre)]
        doc_tags: list[str] = []
        all_nodes = list(pre)
        for s in e["sections"]:
            h, nodes = s["heading"], s["nodes"]
            all_nodes += nodes
            if re.fullmatch(r"tags:?", h, re.I):
                doc_tags = parse_doc_tags("\n".join(text_of(n) for n in nodes))
                continue
            lm = re.match(r"Level of understanding.*?:\s*(.*)$", h)
            if lm:
                after = clean_text(" ".join(text_of(n) for n in nodes))
                level = lm.group(1).strip() or (after if 0 < len(after) <= 8 else "")
                if level:
                    parts.append(f'<p class="reading-level"><strong>Level of understanding:</strong> {html.escape(level)}</p>')
                continue
            content = r.children(nodes)
            if is_empty_html(content):
                continue
            parts.append((f"<h3>{html.escape(h)}</h3>" if h else "") + content)

        entries.append(Entry(company=company, title=title, url=url,
                             body_html="\n".join(p for p in parts if p.strip()),
                             doc_tags=doc_tags, excerpt=excerpt_of(all_nodes)))
    return entries


def read_export_zip(data: bytes, rel_dir: str) -> tuple[list[Entry], dict[str, bytes]]:
    """Parse one tab's zip export; images are renamed by content hash so an
    unchanged image keeps its URL across syncs."""
    files: dict[str, bytes] = {}
    paths: dict[str, str] = {}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        page = next((z.read(n).decode("utf-8") for n in z.namelist() if n.endswith(".html")), "")
        for name in z.namelist():
            if name.startswith("images/") and not name.endswith("/"):
                blob = z.read(name)
                ext = name.rsplit(".", 1)[-1].lower()
                fname = f"{hashlib.sha256(blob).hexdigest()[:16]}.{ext}"
                files[fname] = blob
                paths[name] = f"{rel_dir}/{fname}"
    return parse_export(page, paths), files


# --------------------------------------------------------------------------
# Metadata
# --------------------------------------------------------------------------

def load_definitions(text: str) -> dict[str, dict]:
    defs = {}
    for row in csv.DictReader(io.StringIO(text)):
        tag = (row.get("Tag") or "").strip()
        if tag:
            defs[tag] = {"group": (row.get("Group") or "").strip() or "Other",
                         "definition": (row.get("Definition") or "").strip()}
    return defs


def split_tags(s: str) -> list[str]:
    return [t.strip() for t in re.split(r"[,;]", s or "") if t.strip()]


ALIASES = {"tns": "Trust & Safety", "t&s": "Trust & Safety", "dl": "Deep Learning",
           "machine learning": "ML", "gradient boosting": "Classical ML",
           "tabular data": "Classical ML", "recommendations": "RecSys", "evaluation": "Evals",
           "transformers": "Deep Learning"}


def canonical_tag(tag: str, defs: dict[str, dict]) -> str:
    key = tag.strip().lower()
    lookup = {k.lower(): k for k in defs}
    return lookup.get(key) or lookup.get(ALIASES.get(key, "").lower()) or tag.strip()


def apply_metadata(entries: list[Entry], tags_csv: str, reading_csv: str | None,
                   defs: dict[str, dict]) -> list[str]:
    """Fill tags/year/type in place; returns warnings worth printing."""
    sheet = {norm_title(r["Title"]): r for r in csv.DictReader(io.StringIO(tags_csv)) if r.get("Title")}
    reading = {}
    if reading_csv:
        reading = {norm_title(r["Title"]): r for r in csv.DictReader(io.StringIO(reading_csv)) if r.get("Title")}

    warnings, used = [], set()
    order = {t: i for i, t in enumerate(defs)}
    for e in entries:
        key = norm_title(e.title)
        row = sheet.get(key)
        if row is not None:
            used.add(key)
            tags = split_tags(row.get("Tags", ""))
            e.year, e.type = (row.get("Year") or "").strip(), (row.get("Type") or "").strip()
        else:
            ref = reading.get(key) or {}
            tags = e.doc_tags or split_tags(ref.get("Tags", ""))
            e.year, e.type = (ref.get("Year") or "").strip(), (ref.get("Type") or "").strip()
            warnings.append(f"not in tags sheet (tags from {'the Doc' if e.doc_tags else 'Professional Reading'}): "
                            f"({e.company}) {e.title}")
        canon: list[str] = []
        for t in tags:
            c = canonical_tag(t, defs)
            if c not in canon:
                canon.append(c)
        e.tags = sorted(canon, key=lambda t: (order.get(t, len(order)), t))
        warnings += [f"tag not in definitions: {t!r} on ({e.company}) {e.title}" for t in e.tags if t not in defs]
    warnings += [f"tags sheet row matches no review: ({row.get('Company')}) {row.get('Title')}"
                 for key, row in sheet.items() if key not in used]
    return warnings


def assign_logos(entries: list[Entry], companies_csv: Path, logo_dir: Path, rel_dir: str,
                 fetch_missing: bool) -> None:
    """Point each entry at its company's logo, fetching any that are missing."""
    fields = ["Company", "Domain", "Logo URL"]
    rows = list(csv.DictReader(io.StringIO(companies_csv.read_text(encoding="utf-8")))) if companies_csv.exists() else []
    known = {(r.get("Company") or "").strip(): r for r in rows if (r.get("Company") or "").strip()}
    logo_dir.mkdir(parents=True, exist_ok=True)
    files = {p.stem: p for p in logo_dir.iterdir() if p.is_file()}

    changed = False
    for company in sorted({e.company for e in entries if e.company}):
        row = known.get(company)
        if row is None:
            domain = next((d for e in entries if e.company == company and e.url
                           for d in [site_domain(e.url)] if d), "")
            row = known[company] = {"Company": company, "Domain": domain, "Logo URL": ""}
            changed = True
            print(f"New company {company!r}: guessed domain {domain or '(none)'}; "
                  f"check data/{companies_csv.name}")
        key = slugify(company)
        domain, override = (row.get("Domain") or "").strip(), (row.get("Logo URL") or "").strip()
        if override.lower() == "none":  # no logo that fits: the card shows the initial
            if key in files:
                files.pop(key).unlink()
            continue
        if key in files:
            problem = logo_problem(files[key].read_bytes(), files[key].suffix[1:])
            if problem:
                print(f"WARNING: removed {files[key].name}: {problem}", file=sys.stderr)
                files.pop(key).unlink()
                row["Logo URL"], changed = "none", True
                continue
        elif fetch_missing and (domain or override):
            got, none_fit = fetch_logo(domain, override)
            if got:
                files[key] = logo_dir / f"{key}.{got[1]}"
                files[key].write_bytes(got[0])
                print(f"Fetched logo for {company}")
            elif none_fit:
                print(f"WARNING: no logo for {company} fits a square; it shows its initial "
                      f"(set a Logo URL in data/{companies_csv.name} to override)", file=sys.stderr)
                row["Logo URL"], changed = "none", True
            else:
                print(f"WARNING: couldn't reach {domain or override} for {company}'s logo", file=sys.stderr)
        if key in files:
            # The content hash in the URL busts caches when a logo is replaced.
            version = hashlib.sha256(files[key].read_bytes()).hexdigest()[:8]
            for e in entries:
                if e.company == company:
                    e.logo = f"{rel_dir}/{files[key].name}?v={version}"

    if changed:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(sorted(known.values(), key=lambda r: r["Company"].lower()))
        companies_csv.write_text(buf.getvalue(), encoding="utf-8")


# --------------------------------------------------------------------------
# Rendering the page fragment
# --------------------------------------------------------------------------

def esc(s: str) -> str:
    return html.escape(s, quote=True)


def render_entry(e: Entry, order: int) -> str:
    if e.logo:
        mark = f'<img class="reading-logo" src="{esc(e.logo)}" alt="" width="22" height="22" decoding="async">'
    else:
        mark = f'<span class="reading-logo reading-logo-letter" aria-hidden="true">{esc(e.company[:1])}</span>'
    meta = [f'<button type="button" class="reading-company" data-company="{esc(e.company)}">{mark}'
            f'<span class="reading-company-text">{esc(e.company)}</span></button>'] if e.company else []
    meta += [esc(x) for x in (e.year, e.type) if x]
    if e.url:
        meta.append(f'<a class="reading-original" href="{esc(e.url)}" target="_blank" rel="noopener">original <span aria-hidden="true">↗</span></a>')
    tags = "".join(f'<li><button type="button" class="reading-tag" data-tag="{esc(t)}">{esc(t)}</button></li>' for t in e.tags)
    excerpt = f'\n    <p class="reading-excerpt">{esc(e.excerpt)}</p>' if e.excerpt else ""
    return f"""<article class="reading-entry" id="{e.slug}" data-company="{esc(e.company)}" data-tags="{esc('|'.join(e.tags))}" data-year="{esc(e.year)}" data-type="{esc(e.type)}" data-order="{order}">
  <div class="reading-head">
    <div class="reading-meta">{' <span aria-hidden="true">·</span> '.join(meta)}</div>
    <h2 class="reading-title"><a href="#{e.slug}">{esc(e.title)}</a></h2>{excerpt}
    <ul class="reading-tags">{tags}</ul>
  </div>
  <details class="reading-review">
    <summary>My notes</summary>
    <div class="reading-body">
{e.body_html}
    </div>
  </details>
</article>"""


def render_fragment(entries: list[Entry], defs: dict[str, dict], updated: str) -> str:
    articles = "\n".join(render_entry(e, i) for i, e in enumerate(entries))
    defs_json = json.dumps(defs, ensure_ascii=False).replace("</", "<\\/")
    content_hash = hashlib.sha256((articles + defs_json).encode()).hexdigest()[:16]
    return f"""{START_MARKER}
<section class="reading-section" data-content-hash="{content_hash}">
<script type="application/json" id="reading-tag-defs">{defs_json}</script>
<p class="reading-updated">Last updated {esc(updated)}</p>
<div class="reading-list">
{articles}
</div>
</section>
{END_MARKER}"""


def splice(page: str, fragment: str) -> str:
    start, end = page.find(START_MARKER), page.find(END_MARKER)
    if start < 0 or end < start:
        raise RuntimeError("reading page is missing the READING:START/END markers")
    return page[:start] + fragment + page[end + len(END_MARKER):]


def stamp_assets(page: str, root: Path) -> str:
    """Version the page's stylesheet and script links by their content, so a
    browser can't pair a new page with a cached old styles.css."""
    def stamp(m: re.Match) -> str:
        path = root / m.group(2)
        if not path.exists():
            return m.group(0)
        return f'{m.group(1)}="{m.group(2)}?v={hashlib.sha256(path.read_bytes()).hexdigest()[:8]}"'
    return re.sub(r'\b(href|src)="(styles\.css|js/reading\.js)(?:\?v=[0-9a-f]+)?"', stamp, page)


def content_hash_of(text: str) -> str | None:
    m = re.search(r'class="reading-section" data-content-hash="([0-9a-f]+)"', text)
    return m.group(1) if m else None


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------

def write_if_changed(path: Path, text: str) -> bool:
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.write_text(text, encoding="utf-8")
    return True


def run(page_path: Path, img_dir: Path, data_dir: Path, offline: bool = False,
        doc_zips: list[Path] | None = None, logo_dir: Path | None = None) -> int:
    tags_snapshot = data_dir / "reading-tags.csv"
    defs_snapshot = data_dir / "reading-tag-definitions.csv"

    # 1. The Doc. A failure here aborts: publishing a partial page is worse
    #    than leaving yesterday's up.
    zips = [p.read_bytes() for p in doc_zips] if doc_zips else [fetch_tab_zip(t) for t, _ in TABS]

    # 2. Tags. Fall back to the committed snapshot if the sheet can't be read.
    tags_csv = defs_csv = reading_csv = None
    if not offline:
        try:
            tabs = fetch_sheet_tabs(TAGS_SHEET_ID)
            missing = [t for t in TAGS_SHEET_TABS.values() if t not in tabs]
            if missing:
                raise RuntimeError(f"tags sheet has no tab named {missing}")
            tags_csv, defs_csv = tabs[TAGS_SHEET_TABS["tags"]], tabs[TAGS_SHEET_TABS["definitions"]]
            for path, text in ((tags_snapshot, tags_csv), (defs_snapshot, defs_csv)):
                if write_if_changed(path, text):
                    print(f"Updated snapshot {path.name}")
        except (RuntimeError, zipfile.BadZipFile, ET.ParseError, KeyError) as e:
            print(f"WARNING: tags sheet unavailable, using snapshot: {e}", file=sys.stderr)
            tags_csv = defs_csv = None
        try:
            reading_csv = fetch_sheet_tabs(READING_SHEET_ID).get(READING_SHEET_TAB)
        except (RuntimeError, zipfile.BadZipFile, ET.ParseError, KeyError) as e:
            print(f"WARNING: Professional Reading sheet unavailable: {e}", file=sys.stderr)
    tags_csv = tags_csv or tags_snapshot.read_text(encoding="utf-8")
    defs = load_definitions(defs_csv or defs_snapshot.read_text(encoding="utf-8"))

    # 3. Parse.
    rel_dir = img_dir.relative_to(page_path.parent).as_posix()
    entries: list[Entry] = []
    image_files: dict[str, bytes] = {}
    for data in zips:
        tab_entries, files = read_export_zip(data, rel_dir)
        entries += tab_entries
        image_files.update(files)

    titles: set[str] = set()
    slugs: set[str] = set()
    unique = []
    for e in entries:
        if norm_title(e.title) in titles:
            print(f"WARNING: duplicate review skipped: ({e.company}) {e.title}", file=sys.stderr)
            continue
        titles.add(norm_title(e.title))
        base = e.slug = slugify(f"{e.company} {e.title}")
        n = 2
        while e.slug in slugs:
            e.slug, n = f"{base}-{n}", n + 1
        slugs.add(e.slug)
        unique.append(e)
    entries = unique
    if not entries:
        raise RuntimeError("no reviews parsed from the Doc; refusing to publish an empty page")

    for w in apply_metadata(entries, tags_csv, reading_csv, defs):
        print(f"WARNING: {w}", file=sys.stderr)
    logo_dir = logo_dir or img_dir.parent / "logos"
    assign_logos(entries, data_dir / "reading-companies.csv", logo_dir,
                 logo_dir.relative_to(page_path.parent).as_posix(), fetch_missing=not offline)

    # 4. Render, and only touch the page when the content changed. Images no
    #    longer referenced by the page are deleted.
    original = page_path.read_text(encoding="utf-8")
    page = stamp_assets(original, page_path.parent)
    updated = datetime.now(ZoneInfo("America/New_York")).strftime("%B %-d, %Y")
    fragment = render_fragment(entries, defs, updated)
    used_images = {n for n in image_files if n in fragment}
    if content_hash_of(fragment) == content_hash_of(page):
        if page != original:
            page_path.write_text(page, encoding="utf-8")
            print("Updated stylesheet/script versions.")
        print(f"No content changes ({len(entries)} reviews).")
        return 0

    img_dir.mkdir(parents=True, exist_ok=True)
    for name in used_images:
        if not (img_dir / name).exists():
            (img_dir / name).write_bytes(image_files[name])
    for stale in img_dir.iterdir():
        if stale.is_file() and stale.name not in used_images:
            stale.unlink()
    page_path.write_text(splice(page, fragment), encoding="utf-8")
    print(f"Wrote {page_path.name}: {len(entries)} reviews, {len(used_images)} images.")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Sync the Blogpost/Paper Review Doc into reading.html.")
    ap.add_argument("--page", required=True, type=Path, help="Site page with READING markers.")
    ap.add_argument("--img-dir", required=True, type=Path, help="Where extracted images go.")
    ap.add_argument("--data-dir", required=True, type=Path, help="Where the tag CSV snapshots live.")
    ap.add_argument("--offline", action="store_true", help="Use the CSV snapshots instead of fetching sheets.")
    ap.add_argument("--doc-zip", nargs="*", type=Path, help="Local zip exports to use instead of the Doc.")
    ap.add_argument("--logo-dir", type=Path, help="Where company logos live (default: <img-dir>/../logos).")
    args = ap.parse_args()
    sys.exit(run(args.page.resolve(), args.img_dir.resolve(), args.data_dir.resolve(),
                 offline=args.offline, doc_zips=args.doc_zip,
                 logo_dir=args.logo_dir.resolve() if args.logo_dir else None))


if __name__ == "__main__":
    main()
