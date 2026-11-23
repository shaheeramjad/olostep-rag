from olostep_docs_rag.chunk import (
    chunk_page,
    clean_heading,
    parse_front_matter,
    split_by_size,
    split_sections,
)

PAGE = """---
url: https://x.dev/security/
title: Security
---

# Security

FastAPI gives you several tools to deal with security easily and quickly.

## OAuth2

OAuth2 is a specification that defines several ways to handle auth.

```python
# this is a comment, not a heading
token = get_token()
```

### Password flow

Use the password flow when you own both the API and the frontend app.

## API keys

API keys are a simple way to protect an API in the header of a request.
"""


def test_parse_front_matter():
    meta, body = parse_front_matter(PAGE)
    assert meta == {"url": "https://x.dev/security/", "title": "Security"}
    assert body.startswith("# Security")


def test_parse_front_matter_without_header():
    assert parse_front_matter("# Hi") == ({}, "# Hi")


def test_split_sections_builds_heading_paths():
    _, body = parse_front_matter(PAGE)
    paths = [s.headings for s in split_sections(body)]
    assert paths == [
        ("Security",),
        ("Security", "OAuth2"),
        ("Security", "OAuth2", "Password flow"),
        ("Security", "API keys"),
    ]


def test_hash_lines_in_code_blocks_are_not_headings():
    _, body = parse_front_matter(PAGE)
    oauth = split_sections(body)[1]
    assert "# this is a comment" in oauth.body


def test_clean_heading_removes_links_and_anchors():
    assert clean_heading("[OAuth2](#oauth2) ¶") == "OAuth2"


def test_chunk_page_keeps_url_and_path():
    chunks = chunk_page(PAGE, min_chars=10)
    assert len(chunks) == 4
    assert {c.url for c in chunks} == {"https://x.dev/security/"}
    assert chunks[2].heading_path == "Security > OAuth2 > Password flow"
    assert chunks[2].embed_text.startswith("Security > OAuth2")
    assert len({c.id for c in chunks}) == 4


def test_chunk_ids_are_deterministic():
    assert [c.id for c in chunk_page(PAGE)] == [c.id for c in chunk_page(PAGE)]


def test_short_sections_are_merged_forward():
    page = "# A\n\nshort\n\n# B\n\n" + "long text " * 20
    chunks = chunk_page(page, min_chars=50)
    assert len(chunks) == 1
    assert "short" in chunks[0].text and "long text" in chunks[0].text


def test_split_by_size_respects_limit_and_overlaps():
    text = " ".join(f"word{i}" for i in range(500))
    pieces = split_by_size(text, max_chars=300, overlap=50)
    assert len(pieces) > 1
    assert all(len(p) <= 300 for p in pieces)
    # Each piece starts with words from the end of the previous one.
    first_word_of_second = pieces[1].split()[0]
    assert first_word_of_second in pieces[0]


def test_split_by_size_short_text_is_untouched():
    assert split_by_size("hello", 100, 10) == ["hello"]


def test_page_without_headings_falls_back_to_size():
    page = "---\nurl: https://x.dev/\n---\n" + "para. " * 1000
    chunks = chunk_page(page, max_chars=500, overlap=50)
    assert len(chunks) > 5
    assert all(c.heading_path == "" for c in chunks)


def test_site_chrome_above_h1_is_dropped():
    page = (
        "---\nurl: https://x.dev/a\n"
        "title: Query [¶](https://x.dev/a#q)\n---\n"
        "- [Home](/)\n- [Learn](/learn)\n\n# Query\n\n" + "Real text. " * 20
    )
    chunks = chunk_page(page)
    assert chunks[0].title == "Query"
    assert "Learn" not in chunks[0].text
    assert chunks[0].heading_path == "Query"
