"""Retrieve relevant chunks and answer with Claude, citing sources."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any

import anthropic

from .config import Settings, require_key
from .index import embed, get_collection

if TYPE_CHECKING:
    from chromadb.api.models.Collection import Collection

NOT_FOUND = "I couldn't find the answer to that in the docs."

SYSTEM_PROMPT = f"""\
You answer questions about a documentation site, using ONLY the \
numbered excerpts provided in <documents>.

Rules:
- Base every statement on the excerpts. Do not use outside knowledge.
- Cite the excerpts you use with their number in square brackets, \
like [1] or [2][3], right after the sentence they support.
- If the excerpts don't contain the answer, reply exactly: "{NOT_FOUND}" \
You may add one sentence on what the excerpts do cover.
- Be concise. Use Markdown, and fenced code blocks for code.
"""

CITATION = re.compile(r"\[(\d+)\]")

FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_MODELS = {
    "claude-sonnet-5-5",
    "claude-opus-5-5",
    "claude-opus-5",
    "claude-fable-5-2",
    "claude-fable-5-1",
}


@dataclass(frozen=True)
class Source:
    """A retrieved chunk, numbered as the model sees it."""

    number: int
    url: str
    title: str
    heading_path: str
    text: str
    score: float


# --- retrieval --------------------------------------------------------------


def retrieve(
    question: str,
    settings: Settings,
    collection: Collection | None = None,
) -> list[Source]:
    """Return the ``top_k`` chunks most similar to the question."""
    collection = collection or get_collection(settings)
    if collection.count() == 0:
        raise RuntimeError("The index is empty. Run `crawl` then `index`.")
    result = collection.query(
        query_embeddings=embed([question], settings.embedding_model),
        n_results=settings.top_k,
        include=["documents", "metadatas", "distances"],
    )
    rows = zip(
        result["documents"][0],
        result["metadatas"][0],
        result["distances"][0],
        strict=True,
    )
    return [
        Source(
            number=i,
            url=str(meta["url"]),
            title=str(meta.get("title", "")),
            heading_path=str(meta.get("heading_path", "")),
            text=doc,
            score=round(1 - distance, 3),  # cosine similarity
        )
        for i, (doc, meta, distance) in enumerate(rows, start=1)
    ]


# --- prompt -----------------------------------------------------------------


def build_prompt(question: str, sources: list[Source]) -> str:
    """Put the excerpts and the question into one user message."""
    docs = "\n".join(
        f'<document index="{s.number}" url="{s.url}" '
        f'section="{s.heading_path}">\n{s.text}\n</document>'
        for s in sources
    )
    return f"<documents>\n{docs}\n</documents>\n\nQuestion: {question}"


# --- answering --------------------------------------------------------------


@lru_cache(maxsize=1)
def get_llm() -> anthropic.Anthropic:
    """Create the Anthropic client once, failing fast without a key."""
    return anthropic.Anthropic(api_key=require_key("ANTHROPIC_API_KEY"))


def fallback_options(model: str) -> dict[str, Any]:
    """Server-side refusal fallback, for models that support it.

    If a safety classifier declines a request (false positives happen on
    security-flavoured docs), the API retries on a fallback model in the
    same call. Haiku has no server-side fallback, so we skip it there.
    """
    if model not in FALLBACK_MODELS:
        return {}
    return {"betas": [FALLBACK_BETA], "fallbacks": "default"}


def stream_answer(
    question: str, sources: list[Source], settings: Settings
) -> Iterator[str]:
    """Stream the answer text from Claude, piece by piece."""
    with get_llm().beta.messages.stream(
        model=settings.model,
        max_tokens=settings.max_answer_tokens,
        system=SYSTEM_PROMPT,
        output_config={"effort": settings.effort},
        messages=[
            {"role": "user", "content": build_prompt(question, sources)}
        ],
        **fallback_options(settings.model),
    ) as stream:
        yield from stream.text_stream
        final = stream.get_final_message()
    if final.stop_reason == "refusal":
        yield "\n\n_The model declined to answer this question._"
    elif final.stop_reason == "max_tokens":
        yield "\n\n_(Answer cut off: max_answer_tokens reached.)_"


def cited_sources(answer: str, sources: list[Source]) -> list[Source]:
    """The sources the answer cites as [n], in numeric order."""
    numbers = {int(n) for n in CITATION.findall(answer)}
    return [s for s in sources if s.number in numbers]


def answer(question: str, settings: Settings) -> tuple[str, list[Source]]:
    """Answer a question (non-streaming). Returns (text, cited sources)."""
    sources = retrieve(question, settings)
    text = "".join(stream_answer(question, sources, settings))
    return text, cited_sources(text, sources)
