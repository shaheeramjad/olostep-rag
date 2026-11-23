"""Embed chunks locally and keep a Chroma collection in sync with pages.

A manifest (data/manifest.json) stores a content hash per URL, so
re-indexing only touches pages that are new, changed or removed.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from .chunk import Chunk, chunk_page, parse_front_matter, strip_chrome
from .config import Settings
from .crawl import failure_row, record_failures
from .utils import content_hash, read_json, write_json

if TYPE_CHECKING:  # heavy imports, only needed for type hints
    from chromadb.api.models.Collection import Collection
    from sentence_transformers import SentenceTransformer

log = logging.getLogger(__name__)

BATCH_SIZE = 128


@dataclass
class IndexSummary:
    """What changed in the index during one run."""

    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    unchanged: int = 0
    chunks_written: int = 0
    failures: int = 0


@dataclass(frozen=True)
class Page:
    """A saved page on disk."""

    url: str
    path: Path
    text: str
    hash: str


# --- models + database ------------------------------------------------------


@lru_cache(maxsize=2)
def get_embedder(model_name: str) -> SentenceTransformer:
    """Load (once) a local sentence-transformers model."""
    # Hide Hugging Face download/progress noise in the terminal.
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
    from sentence_transformers import SentenceTransformer

    log.info("Loading embedding model %s", model_name)
    try:  # use the cached copy, no network calls
        return SentenceTransformer(model_name, local_files_only=True)
    except OSError:  # first run: download it (~90 MB)
        return SentenceTransformer(model_name)


def embed(texts: list[str], model_name: str) -> list[list[float]]:
    """Embed texts into normalised vectors (cosine-ready)."""
    model = get_embedder(model_name)
    vectors = model.encode(
        texts,
        batch_size=32,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return vectors.tolist()


def get_collection(settings: Settings) -> Collection:
    """Open (or create) the persistent Chroma collection."""
    import chromadb

    client = chromadb.PersistentClient(path=str(settings.chroma_dir))
    # We always pass our own embeddings, so no embedding function.
    return client.get_or_create_collection(
        name=settings.collection,
        embedding_function=None,
        metadata={"hnsw:space": "cosine"},
    )


# --- pages on disk ----------------------------------------------------------


def load_pages(settings: Settings) -> dict[str, Page]:
    """Read every saved page, keyed by URL."""
    pages: dict[str, Page] = {}
    for path in sorted(settings.pages_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        meta, body = parse_front_matter(text)
        url = meta.get("url")
        if not url:
            log.warning("Skipping %s: no url in front matter", path.name)
            continue
        # Hash only what we index: banners above the H1 often rotate.
        digest = content_hash(strip_chrome(body))
        pages[url] = Page(url, path, text, digest)
    return pages


# --- writing to Chroma ------------------------------------------------------


def delete_page(collection: Collection, url: str) -> None:
    """Remove every chunk that belongs to ``url``."""
    collection.delete(where={"url": url})


def add_chunks(
    collection: Collection, chunks: list[Chunk], model_name: str
) -> None:
    """Embed chunks and upsert them, in batches."""
    for start in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[start : start + BATCH_SIZE]
        collection.upsert(
            ids=[c.id for c in batch],
            documents=[c.text for c in batch],
            embeddings=embed([c.embed_text for c in batch], model_name),
            metadatas=[
                {
                    "url": c.url,
                    "title": c.title,
                    "heading_path": c.heading_path,
                }
                for c in batch
            ],
        )


def index_page(collection: Collection, page: Page, settings: Settings) -> int:
    """Replace a page's chunks in the index. Returns the chunk count."""
    chunks = chunk_page(
        page.text,
        max_chars=settings.chunk_max_chars,
        overlap=settings.chunk_overlap,
        min_chars=settings.chunk_min_chars,
    )
    delete_page(collection, page.url)
    if not chunks:
        raise ValueError("page produced no chunks")
    add_chunks(collection, chunks, settings.embedding_model)
    return len(chunks)


# --- sync -------------------------------------------------------------------


def sync_index(settings: Settings, rebuild: bool = False) -> IndexSummary:
    """Make the index match data/pages, touching only what changed.

    New page        -> chunk, embed, add
    Changed hash    -> delete old chunks, add new ones
    Same hash       -> skip
    File gone       -> delete its chunks
    """
    collection = get_collection(settings)
    manifest: dict[str, str] = (
        {} if rebuild else read_json(settings.manifest_file, default={})
    )
    if rebuild:
        _clear(collection)

    pages = load_pages(settings)
    summary = IndexSummary()
    failures: list[dict[str, str]] = []

    for url, page in pages.items():
        old_hash = manifest.get(url)
        if old_hash == page.hash:
            summary.unchanged += 1
            continue
        try:
            summary.chunks_written += index_page(collection, page, settings)
        except Exception as exc:  # one bad page must not stop the run
            log.warning("Could not index %s: %s", url, exc)
            failures.append(failure_row("index", url, exc))
            continue
        manifest[url] = page.hash
        (summary.updated if old_hash else summary.added).append(url)

    for url in sorted(set(manifest) - set(pages)):
        delete_page(collection, url)
        del manifest[url]
        summary.removed.append(url)

    write_json(settings.manifest_file, manifest)
    record_failures(settings, failures)
    summary.failures = len(failures)
    return summary


def _clear(collection: Collection) -> None:
    ids = collection.get(include=[])["ids"]
    for start in range(0, len(ids), 5_000):
        collection.delete(ids=ids[start : start + 5_000])
