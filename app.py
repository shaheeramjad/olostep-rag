"""Streamlit chat UI: ``streamlit run app.py``."""

from __future__ import annotations

import streamlit as st

from olostep_docs_rag.config import MissingKeyError, Settings, load_settings
from olostep_docs_rag.index import get_collection, get_embedder
from olostep_docs_rag.rag import (
    Source,
    cited_sources,
    retrieve,
    stream_answer,
)
from olostep_docs_rag.utils import read_json

st.set_page_config(page_title="Docs chat", page_icon="📚")


@st.cache_resource
def setup() -> Settings:
    """Load settings and warm up the embedding model once."""
    settings = load_settings()
    get_embedder(settings.embedding_model)
    return settings


def render_sources(sources: list[Source]) -> None:
    """Show clickable source links under an answer."""
    if not sources:
        return
    links = [
        f"{s.number}. [{s.heading_path or s.title}]({s.url})" for s in sources
    ]
    st.caption("Sources")
    st.markdown("\n".join(links))


def sidebar(settings: Settings) -> None:
    """What's indexed, and with which models."""
    crawl = read_json(settings.crawl_file, default={})
    with st.sidebar:
        st.header("Index")
        st.write(f"**Site:** {crawl.get('params', {}).get('site', '-')}")
        st.write(f"**Chunks:** {get_collection(settings).count()}")
        st.write(f"**LLM:** `{settings.model}`")
        st.write(f"**Embeddings:** `{settings.embedding_model}`")
        if st.button("Clear chat"):
            st.session_state.messages = []


settings = setup()
sidebar(settings)
st.title("📚 Chat with the docs")

if "messages" not in st.session_state:
    st.session_state.messages = []

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        render_sources(message.get("sources", []))

if question := st.chat_input("Ask a question about the docs"):
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        try:
            hits = retrieve(question, settings)
            text = st.write_stream(stream_answer(question, hits, settings))
        except (MissingKeyError, RuntimeError) as exc:
            st.error(str(exc))
            st.stop()
        sources = cited_sources(str(text), hits)
        render_sources(sources)

    st.session_state.messages.append(
        {"role": "assistant", "content": text, "sources": sources}
    )
