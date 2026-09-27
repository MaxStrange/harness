"""Turn HTML into readable text without extra dependencies."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser

_BLOCK_TAGS = {
    "p",
    "div",
    "br",
    "li",
    "ul",
    "ol",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "tr",
    "table",
    "section",
    "article",
    "header",
    "footer",
    "nav",
    "blockquote",
    "pre",
    "hr",
    "dd",
    "dt",
    "aside",
    "main",
    "figure",
    "figcaption",
    "summary",
    "details",
}
_SKIP_TAGS = {"script", "style", "noscript", "template", "svg"}


@dataclass
class ExtractedText:
    title: str = ""
    text: str = ""
    links: list[tuple[str, str]] = field(default_factory=list)


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._skip_depth = 0
        self._in_title = False
        self._current_href: str | None = None
        self._link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self._current_href = href
                self._link_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag == "title":
            self._in_title = False
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "a" and self._current_href is not None:
            text = " ".join("".join(self._link_text).split())
            if text:
                self.links.append((text, self._current_href))
            self._current_href = None

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
            return
        self.parts.append(data)
        if self._current_href is not None:
            self._link_text.append(data)


def html_to_text(html: str, max_links: int = 200) -> ExtractedText:
    parser = _Extractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 - malformed HTML must not stop the fetch
        pass
    raw = "".join(parser.parts)
    lines = [" ".join(line.split()) for line in raw.splitlines()]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    title = " ".join("".join(parser.title_parts).split())
    return ExtractedText(title=unescape(title), text=text, links=parser.links[:max_links])
