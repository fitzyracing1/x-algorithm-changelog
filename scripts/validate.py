"""Sanity-check generated output: feed is well-formed Atom, every page parses, internal links resolve.

Usage: python scripts/validate.py site
Uses only the standard library. If `feedparser` is installed it is used as an extra check.
"""
from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

ATOM = "{http://www.w3.org/2005/Atom}"


class LinkCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[str] = []
        self.stack: list[str] = []
        self.void = {"meta", "link", "br", "img", "hr", "input", "source", "wbr"}

    def handle_starttag(self, tag, attrs):
        if tag not in self.void:
            self.stack.append(tag)
        for k, v in attrs:
            if k in ("href", "src") and v:
                self.links.append(v)

    def handle_endtag(self, tag):
        if tag in self.void:
            return
        if not self.stack or self.stack[-1] != tag:
            raise ValueError(f"mismatched </{tag}> (open: {self.stack[-3:]})")
        self.stack.pop()


def main(site: str) -> int:
    errors: list[str] = []
    feed = os.path.join(site, "feed.xml")
    root = ET.parse(feed).getroot()
    assert root.tag == f"{ATOM}feed", "feed root is not <feed>"
    for req in ("id", "title", "updated"):
        if root.find(f"{ATOM}{req}") is None:
            errors.append(f"feed missing <{req}>")
    entries = root.findall(f"{ATOM}entry")
    ids = set()
    for e in entries:
        for req in ("id", "title", "updated", "link", "content"):
            if e.find(f"{ATOM}{req}") is None:
                errors.append(f"entry missing <{req}>")
        ids.add(e.findtext(f"{ATOM}id"))
    if len(ids) != len(entries):
        errors.append("duplicate entry ids")
    print(f"feed.xml: well-formed Atom, {len(entries)} entries")
    try:
        import feedparser  # type: ignore
        parsed = feedparser.parse(feed)
        if parsed.bozo:
            errors.append(f"feedparser: {parsed.bozo_exception}")
        print(f"feedparser: version={parsed.version} entries={len(parsed.entries)} bozo={parsed.bozo}")
    except ImportError:
        pass

    pages = 0
    for dirpath, _, files in os.walk(site):
        for f in files:
            if not f.endswith(".html"):
                continue
            path = os.path.join(dirpath, f)
            p = LinkCollector()
            try:
                p.feed(open(path, encoding="utf-8").read())
                p.close()
                if p.stack:
                    errors.append(f"{path}: unclosed tags {p.stack}")
            except ValueError as exc:
                errors.append(f"{path}: {exc}")
            for link in p.links:
                if link.startswith(("http://", "https://", "#", "mailto:")):
                    continue
                target = os.path.normpath(os.path.join(dirpath, link.split("#")[0]))
                if not os.path.exists(target):
                    errors.append(f"{path}: broken link {link}")
            pages += 1
    print(f"html: {pages} pages checked")
    for e in errors[:50]:
        print("ERROR", e)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "site"))
