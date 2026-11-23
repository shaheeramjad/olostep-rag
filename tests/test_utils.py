from pathlib import Path

from olostep_docs_rag.utils import (
    atomic_write_text,
    content_hash,
    format_duration,
    read_json,
    slugify,
    url_to_filename,
    write_json,
)


def test_slugify():
    assert (
        slugify("Tutorial: Security & OAuth2!") == "tutorial-security-oauth2"
    )
    assert slugify("  --  ") == ""


def test_url_to_filename_uses_path_segments():
    url = "https://fastapi.tiangolo.com/tutorial/security/"
    assert url_to_filename(url) == "tutorial__security.md"


def test_url_to_filename_root_is_index():
    assert url_to_filename("https://x.dev") == "index.md"
    assert url_to_filename("https://x.dev/") == "index.md"


def test_url_to_filename_is_deterministic_and_unique():
    a = url_to_filename("https://x.dev/search?q=1")
    b = url_to_filename("https://x.dev/search?q=2")
    assert a == url_to_filename("https://x.dev/search?q=1")
    assert a != b
    assert a.startswith("search-")


def test_url_to_filename_caps_long_paths():
    url = "https://x.dev/" + "/".join(["segment"] * 40)
    assert len(url_to_filename(url)) < 140


def test_content_hash_changes_with_content():
    assert content_hash("a") == content_hash("a")
    assert content_hash("a") != content_hash("b")


def test_content_hash_ignores_whitespace_only_changes():
    old = "# T\n\n```\ncode\n```\n"
    new = "# T\n\n```\n\ncode  \n```\n\n"
    assert content_hash(old) == content_hash(new)
    assert content_hash(old) != content_hash(old.replace("code", "c0de"))


def test_content_hash_ignores_link_targets():
    absolute = "## Defaults [¶](https://x.dev/a/#defaults)"
    relative = "## Defaults [¶](#defaults)"
    assert content_hash(absolute) == content_hash(relative)
    html_a = '<a href="https://x.dev/ai/">AI</a>'
    html_b = '<a href="../../ai/">AI</a>'
    assert content_hash(html_a) == content_hash(html_b)
    assert content_hash("[A](#a)") != content_hash("[B](#a)")


def test_atomic_write_and_json_roundtrip(tmp_path: Path):
    path = tmp_path / "nested" / "file.json"
    write_json(path, {"b": 1, "a": [1, 2]})
    assert read_json(path) == {"a": [1, 2], "b": 1}
    assert read_json(tmp_path / "missing.json", default={}) == {}
    atomic_write_text(path, "x")
    assert path.read_text() == "x"
    assert not list(path.parent.glob(".tmp-*"))


def test_format_duration():
    assert format_duration(4.25) == "4.2s"
    assert format_duration(174) == "2m 54s"
