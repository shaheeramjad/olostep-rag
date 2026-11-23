"""Settings: paths, model names and tuning knobs, loaded from the env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_EMBEDDING_MODEL = "all-MiniLM-L6-v2"


class MissingKeyError(RuntimeError):
    """Raised when a required API key is not set."""


@dataclass(frozen=True)
class Settings:
    """Everything the pipeline needs to know, in one place."""

    data_dir: Path = Path("data")

    # LLM (Anthropic Claude) and local embeddings (sentence-transformers)
    model: str = DEFAULT_MODEL
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    max_answer_tokens: int = 8_000
    effort: str = "low"  # chat answers don't need deep thinking

    # Chunking
    chunk_max_chars: int = 2_000
    chunk_overlap: int = 200
    chunk_min_chars: int = 80

    # Retrieval
    top_k: int = 6
    collection: str = "docs"

    # Crawling
    retrieve_workers: int = 8
    poll_seconds: int = 5
    crawl_timeout_seconds: int = 1_800

    @property
    def pages_dir(self) -> Path:
        """Folder with one Markdown file per crawled page."""
        return self.data_dir / "pages"

    @property
    def chroma_dir(self) -> Path:
        """Folder of the persistent Chroma database."""
        return self.data_dir / "chroma"

    @property
    def crawl_file(self) -> Path:
        """JSON file with the last crawl id and its parameters."""
        return self.data_dir / "crawl.json"

    @property
    def failures_file(self) -> Path:
        """CSV log of pages that could not be fetched or indexed."""
        return self.data_dir / "failures.csv"

    @property
    def manifest_file(self) -> Path:
        """JSON map of URL -> content hash for everything indexed."""
        return self.data_dir / "manifest.json"


def load_settings() -> Settings:
    """Load ``.env`` and build Settings, honouring optional overrides."""
    load_dotenv()
    return Settings(
        data_dir=Path(os.getenv("DOCS_RAG_DATA_DIR", "data")),
        model=os.getenv("DOCS_RAG_MODEL", DEFAULT_MODEL),
        embedding_model=os.getenv(
            "DOCS_RAG_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL
        ),
    )


def require_key(name: str) -> str:
    """Return the env var ``name`` or raise a clear MissingKeyError."""
    load_dotenv()
    value = os.getenv(name, "").strip()
    if not value:
        raise MissingKeyError(
            f"{name} is not set. Copy .env.example to .env and add it."
        )
    return value
