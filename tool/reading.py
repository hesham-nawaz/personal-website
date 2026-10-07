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
import zipfile
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
TAGS_SHEET_GIDS = {"tags": "0", "definitions": "1"}

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


def fetch_sheet_csv(sheet_id: str, gid: str) -> str:
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"
    return fetch(url, "text/csv").decode("utf-8")


def fetch_reading_sheet_csv() -> str:
    url = (f"https://docs.google.com/spreadsheets/d/{READING_SHEET_ID}/gviz/tq?tqx=out:csv"
           f"&sheet={urllib.parse.quote(READING_SHEET_TAB)}")
    return fetch(url, "text/csv").decode("utf-8")


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


# --------------------------------------------------------------------------
# Rendering the page fragment
# --------------------------------------------------------------------------

def esc(s: str) -> str:
    return html.escape(s, quote=True)


def render_entry(e: Entry, order: int) -> str:
    meta = [f'<button type="button" class="reading-company" data-company="{esc(e.company)}">{esc(e.company)}</button>'] if e.company else []
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
        doc_zips: list[Path] | None = None) -> int:
    tags_snapshot = data_dir / "reading-tags.csv"
    defs_snapshot = data_dir / "reading-tag-definitions.csv"

    # 1. The Doc. A failure here aborts: publishing a partial page is worse
    #    than leaving yesterday's up.
    zips = [p.read_bytes() for p in doc_zips] if doc_zips else [fetch_tab_zip(t) for t, _ in TABS]

    # 2. Tags. Fall back to the committed snapshot if the sheet can't be read.
    tags_csv = defs_csv = reading_csv = None
    if not offline:
        try:
            tags_csv = fetch_sheet_csv(TAGS_SHEET_ID, TAGS_SHEET_GIDS["tags"])
            defs_csv = fetch_sheet_csv(TAGS_SHEET_ID, TAGS_SHEET_GIDS["definitions"])
            for path, text in ((tags_snapshot, tags_csv), (defs_snapshot, defs_csv)):
                if write_if_changed(path, text.replace("\r\n", "\n").rstrip("\n") + "\n"):
                    print(f"Updated snapshot {path.name}")
        except RuntimeError as e:
            print(f"WARNING: tags sheet unavailable, using snapshot: {e}", file=sys.stderr)
            tags_csv = defs_csv = None
        try:
            reading_csv = fetch_reading_sheet_csv()
        except RuntimeError as e:
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

    # 4. Render, and only touch the page when the content changed. Images no
    #    longer referenced by the page are deleted.
    page = page_path.read_text(encoding="utf-8")
    updated = datetime.now(ZoneInfo("America/New_York")).strftime("%B %-d, %Y")
    fragment = render_fragment(entries, defs, updated)
    used_images = {n for n in image_files if n in fragment}
    if content_hash_of(fragment) == content_hash_of(page):
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
    args = ap.parse_args()
    sys.exit(run(args.page.resolve(), args.img_dir.resolve(), args.data_dir.resolve(),
                 offline=args.offline, doc_zips=args.doc_zip))


if __name__ == "__main__":
    main()
