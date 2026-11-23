"""Command line: ``crawl``, ``index``, ``ask`` and ``refresh``."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from .config import MissingKeyError, Settings, load_settings
from .crawl import (
    CrawlParams,
    CrawlSummary,
    load_crawl_record,
    parse_patterns,
    run_crawl,
)
from .index import IndexSummary, sync_index
from .rag import cited_sources, retrieve, stream_answer
from .utils import console, format_duration, setup_logging, timer

log = logging.getLogger(__name__)


# --- commands ---------------------------------------------------------------


def cmd_crawl(args: argparse.Namespace, settings: Settings) -> None:
    """Crawl a site (or resume the last crawl) and save Markdown pages."""
    if args.resume:
        summary = run_crawl(settings, resume=True)
    else:
        if not args.site:
            raise SystemExit("crawl: --site is required (or use --resume)")
        params = CrawlParams(
            site=args.site,
            include=parse_patterns(args.include),
            exclude=parse_patterns(args.exclude),
            max_pages=args.max_pages,
            max_depth=args.max_depth,
        )
        summary = run_crawl(settings, params)
    print_crawl_summary(summary, settings)


def cmd_index(args: argparse.Namespace, settings: Settings) -> None:
    """Chunk, embed and store saved pages (only what changed)."""
    with timer() as t:
        summary = sync_index(settings, rebuild=args.rebuild)
    print_index_summary(summary, t["seconds"])


def cmd_ask(args: argparse.Namespace, settings: Settings) -> None:
    """Answer one question from the terminal, with sources."""
    sources = retrieve(args.question, settings)
    text = ""
    with Live(console=console, refresh_per_second=12) as live:
        live.update(Panel("Thinking...", title=args.question))
        for piece in stream_answer(args.question, sources, settings):
            text += piece
            live.update(Panel(Markdown(text), title=args.question))
    cited = cited_sources(text, sources)
    if cited:
        console.print("[bold]Sources[/]")
        for s in cited:
            label = s.heading_path or s.title
            console.print(f"  [{s.number}] {label}\n      {s.url}")


def cmd_refresh(args: argparse.Namespace, settings: Settings) -> None:
    """Re-crawl with the saved settings and re-index changed pages."""
    _, params = load_crawl_record(settings)
    console.print(f"Re-crawling [bold]{params.site}[/] ...")
    crawl = run_crawl(settings, params)
    print_crawl_summary(crawl, settings)
    if args.prune:
        prune_pages(settings, keep=set(crawl.saved_files))
    with timer() as t:
        summary = sync_index(settings)
    print_index_summary(summary, t["seconds"])


def prune_pages(settings: Settings, keep: set[Path]) -> None:
    """Delete saved pages that the latest crawl did not return."""
    for path in settings.pages_dir.glob("*.md"):
        if path not in keep:
            log.info("Pruning %s", path.name)
            path.unlink()


# --- output -----------------------------------------------------------------


def print_crawl_summary(summary: CrawlSummary, settings: Settings) -> None:
    """Show crawl numbers in a small table."""
    total = summary.crawl_seconds + summary.retrieve_seconds
    rate = summary.pages_saved / (total / 60) if total else 0
    table = Table(title="Crawl summary", show_header=False)
    table.add_row("Crawl id", summary.crawl_id)
    table.add_row("Status", summary.status)
    table.add_row("Pages found", str(summary.pages_found))
    table.add_row("Pages saved", f"[green]{summary.pages_saved}[/]")
    table.add_row("Failures", _red_if(summary.failures))
    table.add_row("Crawl time", format_duration(summary.crawl_seconds))
    table.add_row("Download time", format_duration(summary.retrieve_seconds))
    table.add_row("Pages / minute", f"{rate:.0f}")
    table.add_row("Saved to", str(settings.pages_dir))
    console.print(table)
    if summary.failures:
        console.print(f"See {settings.failures_file} for failure reasons.")


def print_index_summary(summary: IndexSummary, seconds: float) -> None:
    """Show what changed in the index."""
    table = Table(title="Index summary", show_header=False)
    table.add_row("Added pages", f"[green]{len(summary.added)}[/]")
    table.add_row("Updated pages", f"[yellow]{len(summary.updated)}[/]")
    table.add_row("Removed pages", str(len(summary.removed)))
    table.add_row("Unchanged pages", str(summary.unchanged))
    table.add_row("Chunks written", str(summary.chunks_written))
    table.add_row("Failures", _red_if(summary.failures))
    table.add_row("Time", format_duration(seconds))
    console.print(table)
    for url in summary.updated:
        console.print(f"  [yellow]updated[/] {url}")


def _red_if(count: int) -> str:
    return f"[red]{count}[/]" if count else "0"


# --- argument parsing -------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Define the CLI."""
    parser = argparse.ArgumentParser(
        prog="olostep-docs-rag",
        description="Turn any docs site into a RAG chatbot.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    crawl = sub.add_parser("crawl", help="crawl a site into data/pages")
    crawl.add_argument("--site", help="start URL, e.g. https://x.dev")
    crawl.add_argument("--include", help='globs, e.g. "/docs/**"')
    crawl.add_argument("--exclude", help='globs, e.g. "/de/**,/es/**"')
    crawl.add_argument("--max-pages", type=int, default=100)
    crawl.add_argument("--max-depth", type=int, default=None)
    crawl.add_argument(
        "--resume",
        action="store_true",
        help="re-read the crawl saved in data/crawl.json",
    )
    crawl.set_defaults(func=cmd_crawl)

    index = sub.add_parser("index", help="embed pages into Chroma")
    index.add_argument(
        "--rebuild", action="store_true", help="re-embed every page"
    )
    index.set_defaults(func=cmd_index)

    ask = sub.add_parser("ask", help="ask a question from the terminal")
    ask.add_argument("question")
    ask.set_defaults(func=cmd_ask)

    refresh = sub.add_parser(
        "refresh", help="re-crawl and re-index only changed pages"
    )
    refresh.add_argument(
        "--prune",
        action="store_true",
        help="also drop pages the new crawl didn't return",
    )
    refresh.set_defaults(func=cmd_refresh)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Entry point for ``python -m olostep_docs_rag``."""
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)
    try:
        args.func(args, load_settings())
    except MissingKeyError as exc:
        console.print(f"[red]Error:[/] {exc}")
        sys.exit(1)
    except (FileNotFoundError, RuntimeError, TimeoutError) as exc:
        console.print(f"[red]Error:[/] {exc}")
        sys.exit(1)
    except KeyboardInterrupt:
        console.print("\nStopped.")
        sys.exit(130)
