"""Small helpers: logging, slugs, filenames, hashes, atomic writes, timers."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import time
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from rich.console import Console
from rich.logging import RichHandler

console = Console()

MAX_FILENAME_LENGTH = 120
_NON_SLUG_CHARS = re.compile(r"[^a-z0-9]+")


def setup_logging(verbose: bool = False) -> None:
    """Log our own messages through rich; only warnings from libraries."""
    logging.basicConfig(
        level=logging.WARNING,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, show_path=False)],
        force=True,
    )
    level = logging.DEBUG if verbose else logging.INFO
    logging.getLogger("olostep_docs_rag").setLevel(level)
    # Hugging Face nags about HF_TOKEN; the model is public.
    logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
    # olostep 1.2.3 warns on every call when the API returns fields the
    # SDK doesn't model yet (e.g. cost_usd). Harmless, so hide them.
    logging.getLogger("olostep").setLevel(logging.ERROR)
    warnings.filterwarnings("ignore", category=UserWarning, module="olostep")


def slugify(text: str) -> str:
    """Turn text into a lowercase, filesystem-safe slug.

    >>> slugify("Tutorial: Security & OAuth2!")
    'tutorial-security-oauth2'
    """
    return _NON_SLUG_CHARS.sub("-", text.lower()).strip("-")


def url_to_filename(url: str) -> str:
    """Map a URL to a deterministic Markdown filename.

    The path segments become the name, so files are easy to find:
    ``https://x.dev/tutorial/security/`` -> ``tutorial__security.md``.
    Query strings or very long paths get a short hash suffix so two
    URLs never share a file.

    >>> url_to_filename("https://x.dev/")
    'index.md'
    """
    parts = urlsplit(url)
    segments = [slugify(unquote(s)) for s in parts.path.split("/")]
    name = "__".join(s for s in segments if s) or "index"
    if parts.query or len(name) > MAX_FILENAME_LENGTH:
        name = f"{name[:MAX_FILENAME_LENGTH]}-{short_hash(url)}"
    return f"{name}.md"


def short_hash(text: str, length: int = 8) -> str:
    """Return the first ``length`` hex chars of the SHA-1 of ``text``."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:length]


_LINK_TARGET = re.compile(r"\]\([^)]*\)|href=\"[^\"]*\"")


def content_hash(text: str) -> str:
    """SHA-256 of the *visible* text, used to detect changed pages.

    Two crawls of an unchanged page can differ in ways a reader never
    sees: blank lines inside code blocks, or links written as absolute
    URLs one time and relative ones the next. We drop blank lines,
    trailing spaces and link targets before hashing, so only real text
    changes trigger re-embedding.
    """
    text = _LINK_TARGET.sub("]", text)
    lines = (line.rstrip() for line in text.splitlines())
    normalized = "\n".join(line for line in lines if line)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` via a temp file + rename.

    A crash mid-write never leaves a half-written file behind.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def write_json(path: Path, data: Any) -> None:
    """Write ``data`` as pretty JSON, atomically."""
    atomic_write_text(path, json.dumps(data, indent=2, sort_keys=True))


def read_json(path: Path, default: Any = None) -> Any:
    """Read JSON from ``path``, or return ``default`` if it doesn't exist."""
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def format_duration(seconds: float) -> str:
    """Format seconds for humans: ``41.3s`` or ``2m 54s``."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, secs = divmod(round(seconds), 60)
    return f"{minutes}m {secs:02d}s"


@contextmanager
def timer() -> Iterator[dict[str, float]]:
    """Time a block. Read ``t["seconds"]`` after the block ends.

    >>> with timer() as t:
    ...     pass
    >>> t["seconds"] >= 0
    True
    """
    result = {"seconds": 0.0}
    start = time.perf_counter()
    try:
        yield result
    finally:
        result["seconds"] = time.perf_counter() - start
