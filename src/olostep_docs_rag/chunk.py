"""Split Markdown pages into chunks by heading, with a size fallback.

Each chunk keeps its source URL and heading path ("Security > OAuth2"),
so answers can cite exactly where a fact came from.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .utils import short_hash

HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")


@dataclass(frozen=True)
class Chunk:
    """One piece of a page, ready to embed."""

    id: str
    url: str
    title: str
    heading_path: str
    text: str

    @property
    def embed_text(self) -> str:
        """Text to embed: the heading path gives the chunk context."""
        if self.heading_path:
            return f"{self.heading_path}\n\n{self.text}"
        return self.text


@dataclass(frozen=True)
class Section:
    """Body text under one heading, plus the headings above it."""

    headings: tuple[str, ...]
    body: str


def parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    """Split ``---`` front matter from the body.

    >>> parse_front_matter("---\\nurl: https://x.dev\\n---\\nHi")
    ({'url': 'https://x.dev'}, 'Hi')
    """
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    meta: dict[str, str] = {}
    for line in text[4:end].splitlines():
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip()] = value.strip()
    body = text[end + len("\n---") :].lstrip("\n")
    return meta, body


def split_sections(markdown: str) -> list[Section]:
    """Split Markdown at headings, ignoring ``#`` lines inside code."""
    sections: list[Section] = []
    stack: list[tuple[int, str]] = []  # (level, heading text)
    lines: list[str] = []
    in_code = False

    def flush() -> None:
        body = "\n".join(lines).strip()
        if body:
            headings = tuple(text for _, text in stack)
            sections.append(Section(headings=headings, body=body))
        lines.clear()

    for line in markdown.splitlines():
        if FENCE.match(line):
            in_code = not in_code
        match = None if in_code else HEADING.match(line)
        if not match:
            lines.append(line)
            continue
        flush()
        level, text = len(match.group(1)), clean_heading(match.group(2))
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, text))
    flush()
    return sections


def strip_chrome(markdown: str) -> str:
    """Drop everything above the first ``# H1`` heading.

    Crawled pages start with site chrome: banners, language menus and
    the sidebar nav. The real content starts at the page's H1. Pages
    without an H1 are returned unchanged.
    """
    lines = markdown.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("# "):
            return "\n".join(lines[i:])
    return markdown


def clean_heading(text: str) -> str:
    """Strip Markdown links and anchor symbols from a heading.

    >>> clean_heading("[OAuth2](#oauth2) ¶")
    'OAuth2'
    """
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    return text.replace("¶", "").replace("​", "").strip(" #")


def split_by_size(text: str, max_chars: int, overlap: int) -> list[str]:
    """Split long text into pieces of at most ``max_chars``.

    Cuts prefer paragraph breaks, then line breaks, then spaces, and
    each piece repeats the last ``overlap`` chars of the one before.
    """
    if len(text) <= max_chars:
        return [text]
    pieces: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            end = _best_cut(text, start, end)
        pieces.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return [p for p in pieces if p]


def _best_cut(text: str, start: int, end: int) -> int:
    """Find a natural break in the second half of ``text[start:end]``."""
    floor = start + (end - start) // 2
    for sep in ("\n\n", "\n", " "):
        cut = text.rfind(sep, floor, end)
        if cut != -1:
            return cut + len(sep)
    return end


def chunk_page(
    text: str,
    max_chars: int = 2_000,
    overlap: int = 200,
    min_chars: int = 80,
) -> list[Chunk]:
    """Turn one saved page (front matter + Markdown) into chunks.

    Sections shorter than ``min_chars`` are merged into the next one,
    so tiny "intro" blurbs don't become useless chunks.
    """
    meta, body = parse_front_matter(text)
    body = strip_chrome(body)
    url = meta.get("url", "")
    title = clean_heading(meta.get("title", "")) or url

    chunks: list[Chunk] = []
    carry, path = "", ""
    for section in split_sections(body):
        merged = f"{carry}\n\n{section.body}" if carry else section.body
        path = " > ".join(section.headings)
        if len(merged) < min_chars:
            carry = merged
            continue
        carry = ""
        for piece in split_by_size(merged, max_chars, overlap):
            chunks.append(_make_chunk(url, title, path, piece, len(chunks)))
    if carry:  # a short last section: keep it rather than lose it
        chunks.append(_make_chunk(url, title, path, carry, len(chunks)))
    return chunks


def _make_chunk(
    url: str, title: str, path: str, text: str, position: int
) -> Chunk:
    chunk_id = f"{short_hash(url)}-{position:04d}"
    return Chunk(chunk_id, url, title, path, text)
