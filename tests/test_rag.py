from olostep_docs_rag.rag import Source, build_prompt, cited_sources


def _source(n: int, url: str) -> Source:
    return Source(n, url, "T", f"T > S{n}", f"text {n}", 0.5)


SOURCES = [_source(1, "https://x.dev/a"), _source(2, "https://x.dev/a")]
SOURCES += [_source(3, "https://x.dev/b")]


def test_cited_sources_match_numbers_in_answer():
    cited = cited_sources("Use X [3]. Also Y [1][3].", SOURCES)
    assert [s.number for s in cited] == [1, 3]


def test_cited_sources_ignores_unknown_numbers():
    assert cited_sources("Nothing here [9].", SOURCES) == []


def test_build_prompt_numbers_documents():
    prompt = build_prompt("How?", SOURCES)
    assert '<document index="3" url="https://x.dev/b"' in prompt
    assert prompt.endswith("Question: How?")
