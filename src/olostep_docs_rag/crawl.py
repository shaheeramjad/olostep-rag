"""Crawl a docs site with Olostep and save every page as Markdown.

Flow (all through the official ``olostep`` SDK):

1. ``client.crawls.create(...)``       start the crawl, get a crawl id
2. ``client.crawls.info(crawl_id)``    poll status + page count
3. ``client.crawls.pages(crawl_id)``   list pages (url + retrieve_id)
4. ``client.retrieve.get(id, ...)``    fetch each page's Markdown
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from olostep import AsyncOlostep, Olostep
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

from .chunk import clean_heading
from .config import Settings, require_key
from .utils import (
    atomic_write_text,
    console,
    read_json,
    url_to_filename,
    write_json,
)

log = logging.getLogger(__name__)

TERMINAL_STATUSES = {"completed", "failed"}
FAILURE_COLUMNS = ["time", "stage", "url", "reason"]


@dataclass(frozen=True)
class CrawlParams:
    """What to crawl. Saved to data/crawl.json so refresh can reuse it."""

    site: str
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    max_pages: int = 100
    max_depth: int | None = None


@dataclass(frozen=True)
class PageRef:
    """A crawled page: its URL and the id used to fetch its content."""

    url: str
    retrieve_id: str


@dataclass
class CrawlSummary:
    """Numbers shown at the end of a crawl."""

    crawl_id: str
    status: str = ""
    pages_found: int = 0
    pages_saved: int = 0
    failures: int = 0
    crawl_seconds: float = 0.0
    retrieve_seconds: float = 0.0
    saved_files: list[Path] = field(default_factory=list)


# --- Olostep client ---------------------------------------------------------


def make_client() -> Olostep:
    """Create the Olostep client, failing fast if the key is missing."""
    return Olostep(api_key=require_key("OLOSTEP_API_KEY"))


def parse_patterns(value: str | None) -> list[str]:
    """Split a comma-separated glob list: ``"/de/**,/es/**"``."""
    if not value:
        return []
    return [p.strip() for p in value.split(",") if p.strip()]


# --- 1. start ---------------------------------------------------------------


def start_crawl(
    client: Olostep, params: CrawlParams, settings: Settings
) -> str:
    """Start a crawl, save its id to data/crawl.json, return the id."""
    crawl = client.crawls.create(
        start_url=params.site,
        max_pages=params.max_pages,
        include_urls=params.include or None,
        exclude_urls=params.exclude or None,
        max_depth=params.max_depth,
        include_external=False,
    )
    save_crawl_record(settings, crawl.id, params)
    log.info("Started crawl %s for %s", crawl.id, params.site)
    return crawl.id


def save_crawl_record(
    settings: Settings, crawl_id: str, params: CrawlParams, **extra: Any
) -> None:
    """Write the crawl id + params (+ any stats) to data/crawl.json."""
    record = read_json(settings.crawl_file, default={})
    if record.get("crawl_id") != crawl_id:
        record = {
            "crawl_id": crawl_id,
            "started_at": _now(),
            "params": params.__dict__,
        }
    record.update(extra)
    write_json(settings.crawl_file, record)


def load_crawl_record(settings: Settings) -> tuple[str, CrawlParams]:
    """Read the last crawl id and params from data/crawl.json."""
    record = read_json(settings.crawl_file)
    if not record:
        raise FileNotFoundError(
            f"No crawl found at {settings.crawl_file}. Run `crawl` first."
        )
    return record["crawl_id"], CrawlParams(**record["params"])


# --- 2. wait ----------------------------------------------------------------


def wait_for_crawl(
    client: Olostep, crawl_id: str, params: CrawlParams, settings: Settings
) -> tuple[str, int]:
    """Poll the crawl until it finishes. Returns (status, pages_count).

    We poll ``crawls.info`` ourselves instead of using the SDK's
    ``wait_till_done`` so we can draw a live progress bar.
    """
    columns = (
        SpinnerColumn(),
        TextColumn("[bold]Crawling[/] {task.fields[status]}"),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn("pages · depth {task.fields[depth]}"),
        TimeElapsedColumn(),
    )
    deadline = time.monotonic() + settings.crawl_timeout_seconds
    with Progress(*columns, console=console) as progress:
        task = progress.add_task(
            "crawl", total=params.max_pages, status="starting", depth="-"
        )
        while True:
            info = client.crawls.info(crawl_id)
            progress.update(
                task,
                completed=info.pages_count,
                status=info.status,
                depth=info.current_depth or 0,
            )
            if info.status in TERMINAL_STATUSES:
                return info.status, info.pages_count
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"Crawl {crawl_id} still running after "
                    f"{settings.crawl_timeout_seconds}s. "
                    "Re-run `crawl --resume` later to pick it up."
                )
            time.sleep(settings.poll_seconds)


# --- 3. list ----------------------------------------------------------------


def list_pages(crawl_id: str) -> list[PageRef]:
    """List every internal page of a finished crawl."""
    # olostep 1.2.3: the sync `client.crawls.pages(id)` raises
    # "TypeError: object async_generator can't be used in 'await'
    # expression", so we page through with the SDK's async client.
    return asyncio.run(_list_pages_async(crawl_id))


async def _list_pages_async(crawl_id: str) -> list[PageRef]:
    refs: dict[str, PageRef] = {}
    api_key = require_key("OLOSTEP_API_KEY")
    async with AsyncOlostep(api_key=api_key) as client:
        # The crawl is already finished, so skip the SDK's built-in
        # wait (it has a fixed 10 minute timeout).
        pages = client.crawls.pages(crawl_id, wait_for_completion=False)
        async for page in pages:
            if not page.is_external:
                refs[page.url] = PageRef(page.url, page.retrieve_id)
    return list(refs.values())


# --- 4. retrieve + save -----------------------------------------------------


def fetch_markdown(client: Olostep, page: PageRef) -> str:
    """Fetch one page's Markdown. Raises ValueError if there is none."""
    result = client.retrieve.get(page.retrieve_id, formats=["markdown"])
    markdown = getattr(result, "markdown_content", None)
    if getattr(result, "size_exceeded", False):
        raise ValueError("page larger than 6MB (size_exceeded)")
    if not markdown or not markdown.strip():
        raise ValueError("empty markdown_content")
    return markdown


def render_page(url: str, markdown: str) -> str:
    """Prefix the Markdown with a small front matter block.

    The front matter keeps the source URL next to the content, so the
    chunker (and a human) always knows where a file came from.
    """
    title = find_title(markdown) or url
    header = f"---\nurl: {url}\ntitle: {title}\n---\n\n"
    return header + markdown.strip() + "\n"


def find_title(markdown: str) -> str | None:
    """Return the text of the first ``# H1`` heading, if any."""
    for line in markdown.splitlines():
        if line.startswith("# "):
            return clean_heading(line[2:]) or None
    return None


def save_page(settings: Settings, url: str, markdown: str) -> Path:
    """Write a page to data/pages/<path>.md and return the path."""
    path = settings.pages_dir / url_to_filename(url)
    atomic_write_text(path, render_page(url, markdown))
    return path


def download_pages(
    client: Olostep, pages: list[PageRef], settings: Settings
) -> tuple[list[Path], list[dict[str, str]]]:
    """Fetch and save pages in parallel. One failure never stops the run.

    Returns (saved paths, failure rows).
    """
    saved: list[Path] = []
    failures: list[dict[str, str]] = []
    columns = (
        SpinnerColumn(),
        TextColumn("[bold]Downloading Markdown"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
    )
    with (
        Progress(*columns, console=console) as progress,
        ThreadPoolExecutor(max_workers=settings.retrieve_workers) as pool,
    ):
        task = progress.add_task("retrieve", total=len(pages))
        futures = {
            pool.submit(fetch_markdown, client, page): page for page in pages
        }
        for future in as_completed(futures):
            page = futures[future]
            try:
                saved.append(save_page(settings, page.url, future.result()))
            except Exception as exc:  # one bad page must not stop the run
                log.warning("Failed %s: %s", page.url, exc)
                failures.append(failure_row("retrieve", page.url, exc))
            progress.advance(task)
    return saved, failures


# --- failures log -----------------------------------------------------------


def failure_row(stage: str, url: str, error: Exception | str) -> dict:
    """Build one row for data/failures.csv."""
    reason = error if isinstance(error, str) else _describe(error)
    return {"time": _now(), "stage": stage, "url": url, "reason": reason}


def record_failures(settings: Settings, rows: list[dict[str, str]]) -> None:
    """Append rows to data/failures.csv (rewritten atomically)."""
    if not rows:
        return
    existing = ""
    if settings.failures_file.exists():
        existing = settings.failures_file.read_text(encoding="utf-8")
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=FAILURE_COLUMNS)
    if not existing:
        writer.writeheader()
    writer.writerows(rows)
    atomic_write_text(settings.failures_file, existing + buffer.getvalue())


# --- orchestration ----------------------------------------------------------


def run_crawl(
    settings: Settings,
    params: CrawlParams | None = None,
    resume: bool = False,
) -> CrawlSummary:
    """Crawl (or resume the last crawl) and save all pages as Markdown."""
    client = make_client()
    if resume or params is None:
        crawl_id, params = load_crawl_record(settings)
        log.info("Resuming crawl %s", crawl_id)
    else:
        crawl_id = start_crawl(client, params, settings)

    summary = CrawlSummary(crawl_id=crawl_id)
    started = time.perf_counter()
    summary.status, _ = wait_for_crawl(client, crawl_id, params, settings)
    summary.crawl_seconds = time.perf_counter() - started

    pages = list_pages(crawl_id)
    summary.pages_found = len(pages)

    started = time.perf_counter()
    saved, failures = download_pages(client, pages, settings)
    summary.retrieve_seconds = time.perf_counter() - started

    summary.saved_files = saved
    summary.pages_saved = len(saved)
    summary.failures = len(failures)
    record_failures(settings, failures)
    save_crawl_record(
        settings,
        crawl_id,
        params,
        finished_at=_now(),
        status=summary.status,
        pages_found=summary.pages_found,
        pages_saved=summary.pages_saved,
        failures=summary.failures,
        crawl_seconds=round(summary.crawl_seconds, 1),
        retrieve_seconds=round(summary.retrieve_seconds, 1),
        urls=sorted(p.url for p in pages),
    )
    return summary


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _describe(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}".replace("\n", " ")[:300]
