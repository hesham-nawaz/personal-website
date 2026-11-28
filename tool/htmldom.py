"""A tiny HTML tree for the scrapers (Screen Slate, BAM). Stdlib only.

parse(html) -> Node. Nodes support find_all(tag=None, cls=None, id=None),
find(...), text() and attribute access with .get(name). Good enough for
well-formed listing markup; it is not a full HTML5 parser.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
        "param", "source", "track", "wbr"}


class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: dict | None = None, parent: "Node | None" = None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children: list = []      # Node or str
        self.parent = parent

    def get(self, name: str, default=None):
        return self.attrs.get(name, default)

    @property
    def classes(self) -> list[str]:
        return (self.attrs.get("class") or "").split()

    def iter(self):
        for c in self.children:
            if isinstance(c, Node):
                yield c
                yield from c.iter()

    def matches(self, tag=None, cls=None, id=None) -> bool:
        return ((tag is None or self.tag == tag)
                and (cls is None or cls in self.classes)
                and (id is None or self.attrs.get("id") == id))

    def find_all(self, tag=None, cls=None, id=None) -> list["Node"]:
        return [n for n in self.iter() if n.matches(tag, cls, id)]

    def find(self, tag=None, cls=None, id=None) -> "Node | None":
        return next((n for n in self.iter() if n.matches(tag, cls, id)), None)

    def child_nodes(self, tag=None, cls=None) -> list["Node"]:
        return [c for c in self.children if isinstance(c, Node) and c.matches(tag, cls)]

    def text(self) -> str:
        parts = []

        def walk(n):
            for c in n.children:
                if isinstance(c, str):
                    parts.append(c)
                elif c.tag not in ("script", "style"):
                    walk(c)
                    if c.tag in ("br", "p", "div", "li"):
                        parts.append(" ")
        walk(self)
        return re.sub(r"\s+", " ", "".join(parts)).strip()


class _Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: (v or "") for k, v in attrs}, self.cur)
        self.cur.children.append(node)
        if tag not in VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(Node(tag, {k: (v or "") for k, v in attrs}, self.cur))

    def handle_endtag(self, tag):
        n = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.children.append(data)


def parse(html_text: str) -> Node:
    b = _Builder()
    b.feed(html_text)
    b.close()
    return b.root
