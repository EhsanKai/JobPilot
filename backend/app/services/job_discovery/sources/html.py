"""Deterministic HTML handling for job descriptions.

Job sources commonly deliver descriptions as HTML. JobPilot never stores or
renders source HTML (that would be an XSS risk in the frontend). Instead it
keeps plain text, plus the bullet lists found under recognizable section
headings ("Requirements", "What you'll do", "Dein Profil", ...).

No AI is involved: section detection is keyword matching on headings, and
anything not recognized simply stays in the description text.
"""

import html as html_lib
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Literal

_BLOCK_TAGS = frozenset(
    {"p", "div", "section", "article", "ul", "ol", "li", "br", "tr", "table", "blockquote",
     "h1", "h2", "h3", "h4", "h5", "h6", "header", "footer", "dl", "dt", "dd", "hr"}
)
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_BOLD_TAGS = frozenset({"strong", "b"})
_SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "head"})
# A bold-only paragraph longer than this is emphasis, not a heading.
_MAX_HEADING_CHARS = 90
MAX_LIST_ITEMS = 50
MAX_ITEM_CHARS = 1000

BlockKind = Literal["heading", "item", "paragraph"]


@dataclass
class _Block:
    kind: BlockKind
    text: str


@dataclass
class Section:
    heading: str | None
    items: list[str] = field(default_factory=list)
    paragraphs: list[str] = field(default_factory=list)


def _clean(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split())


class _BlockParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[_Block] = []
        self._buffer: list[str] = []
        self._kind: BlockKind = "paragraph"
        self._skip_depth = 0
        self._bold_depth = 0
        # Whether every non-whitespace character of the current block was bold.
        self._all_bold = True
        self._has_text = False

    def _flush(self) -> None:
        text = _clean("".join(self._buffer))
        if text:
            kind = self._kind
            if kind == "paragraph" and self._all_bold and len(text) <= _MAX_HEADING_CHARS:
                kind = "heading"
            self.blocks.append(_Block(kind, text))
        self._buffer = []
        self._kind = "paragraph"
        self._all_bold = True
        self._has_text = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag in _BLOCK_TAGS:
            self._flush()
            if tag in _HEADING_TAGS:
                self._kind = "heading"
            elif tag == "li":
                self._kind = "item"
        elif tag in _BOLD_TAGS:
            self._bold_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag in _BLOCK_TAGS:
            self._flush()
        elif tag in _BOLD_TAGS:
            self._bold_depth = max(0, self._bold_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if data.strip():
            self._has_text = True
            if self._bold_depth == 0:
                self._all_bold = False
        self._buffer.append(data)

    def close(self) -> None:
        super().close()
        self._flush()


def _looks_entity_encoded(value: str) -> bool:
    return "<" not in value and bool(re.search(r"&lt;\s*/?\s*[a-zA-Z]", value))


def _parse_blocks(value: str | None) -> list[_Block]:
    if not value:
        return []
    # Some sources (e.g. Greenhouse) entity-encode their HTML.
    for _ in range(2):
        if _looks_entity_encoded(value):
            value = html_lib.unescape(value)
    parser = _BlockParser()
    parser.feed(value)
    parser.close()
    return parser.blocks


def html_to_text(value: str | None) -> str | None:
    """Readable plain text: paragraphs separated by blank lines, list items
    as "• item" lines. Returns None for empty input."""
    blocks = _parse_blocks(value)
    lines: list[str] = []
    previous: BlockKind | None = None
    for block in blocks:
        if lines and not (block.kind == "item" and previous == "item"):
            lines.append("")
        lines.append(f"• {block.text}" if block.kind == "item" else block.text)
        previous = block.kind
    text = "\n".join(lines).strip()
    return text or None


def extract_sections(value: str | None) -> list[Section]:
    """Split HTML into sections at headings. Content before the first
    heading is a section with `heading=None`."""
    sections: list[Section] = []
    current = Section(heading=None)
    for block in _parse_blocks(value):
        if block.kind == "heading":
            if current.heading is not None or current.items or current.paragraphs:
                sections.append(current)
            current = Section(heading=block.text)
        elif block.kind == "item":
            current.items.append(block.text)
        else:
            current.paragraphs.append(block.text)
    if current.heading is not None or current.items or current.paragraphs:
        sections.append(current)
    return sections


def html_list_items(value: str | None) -> list[str]:
    """The `<li>` texts of an HTML fragment (e.g. Lever's `lists[].content`)."""
    return [b.text for b in _parse_blocks(value) if b.kind == "item"]


SectionKind = Literal["requirements", "responsibilities"]

# Matched against the casefolded heading. English and German, since JobPilot
# targets the German market too. Checked in order: unambiguous requirement
# phrases, then responsibilities, then broader requirement phrases -- so
# "What you will need" is a requirement while "What you will do" is a
# responsibility.
_STRONG_REQUIREMENT_PATTERNS = (
    r"requirement", r"qualification", r"what you('ll| will)? (need|bring)",
    r"what we('re| are) looking for", r"must.have", r"nice.to.have", r"anforderung",
    r"dein profil", r"ihr profil", r"qualifikation", r"mitbring",
)
_RESPONSIBILITY_PATTERNS = (
    r"responsibilit", r"what you('ll| will) do", r"what you('ll| will) be doing",
    r"your (role|tasks|mission|impact|day)", r"the role", r"in this role", r"you will",
    r"you('ll)? (own|work on|be responsible)", r"duties", r"key accountabilit",
    r"what the job involves", r"aufgaben", r"deine rolle", r"ihre rolle",
    r"das erwartet dich", r"das erwartet sie", r"was dich erwartet", r"was sie erwartet",
    r"verantwortlich",
)
_REQUIREMENT_PATTERNS = (
    r"you (have|are|should have)", r"about you", r"your (profile|skills|background|experience)",
    r"who you are", r"skills", r"is this you", r"experience",
)


def classify_heading(heading: str | None) -> SectionKind | None:
    if not heading:
        return None
    text = heading.casefold()
    if any(re.search(p, text) for p in _STRONG_REQUIREMENT_PATTERNS):
        return "requirements"
    if any(re.search(p, text) for p in _RESPONSIBILITY_PATTERNS):
        return "responsibilities"
    if any(re.search(p, text) for p in _REQUIREMENT_PATTERNS):
        return "requirements"
    return None


def classified_lists(sections: list[Section]) -> dict[SectionKind, list[str]]:
    """Collect requirement and responsibility items from recognized sections."""
    result: dict[SectionKind, list[str]] = {"requirements": [], "responsibilities": []}
    for section in sections:
        kind = classify_heading(section.heading)
        if kind is None:
            continue
        entries = section.items or section.paragraphs
        result[kind].extend(entries)
    return result
