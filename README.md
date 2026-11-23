# olostep-docs-rag

Turn any documentation site into a RAG chatbot with Python and
[Olostep](https://www.olostep.com) Crawls, with cited answers.

<!-- Demo video: replace with the YouTube link -->
> 📺 **Demo video:** _coming soon_ — "Turn Any Docs Site into a RAG
> Chatbot with Python and Olostep"

## Features

- **Crawl a whole docs site** with Olostep's Crawls endpoint, with
  include/exclude glob rules and a page limit. Every page is saved as
  clean Markdown in `data/pages/`.
- **Heading-aware chunking.** Pages are split by Markdown headings
  (code blocks are respected), with a size + overlap fallback. Each chunk
  keeps its URL and heading path (`Security > OAuth2 > Scopes`).
- **Local embeddings and vector DB.** Uses
  [sentence-transformers](https://www.sbert.net) (`all-MiniLM-L6-v2`)
  and a persistent [Chroma](https://www.trychroma.com) database, with no
  server and no extra API key.
- **Answers with sources.** Claude answers only from the retrieved
  excerpts, cites them as `[1]`, `[2]`, and says so when the docs don't
  cover the question.
- **Streamlit chat UI** with streamed answers and clickable sources.
- **Cheap refresh.** Re-crawl, then re-embed only the pages whose content
  hash changed.
- **Robust by default.** One bad page never stops a run (reasons go to
  `data/failures.csv`), files are written atomically, and the crawl id
  is saved so a crawl can be resumed.

## Quickstart

Requires Python 3.10+.

```bash
git clone https://github.com/olostep/olostep-docs-rag
cd olostep-docs-rag
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env   # add OLOSTEP_API_KEY and ANTHROPIC_API_KEY
```

Then crawl, index and chat:

```bash
python -m olostep_docs_rag crawl --site https://fastapi.tiangolo.com \
    --include "/**" --exclude "/de/**,/es/**" --max-pages 500
python -m olostep_docs_rag index
python -m olostep_docs_rag ask "How do I add authentication?"
streamlit run app.py
```

Get an Olostep API key at <https://www.olostep.com/dashboard> and an
Anthropic API key at <https://platform.claude.com>.

## CLI

| Command | What it does |
| --- | --- |
| `crawl --site URL [--include G] [--exclude G] [--max-pages N] [--max-depth N]` | Start an Olostep crawl, show progress, save every page to `data/pages/*.md` |
| `crawl --resume` | Re-read the crawl saved in `data/crawl.json` (no new crawl) |
| `index [--rebuild]` | Chunk + embed pages into Chroma; only new/changed pages are re-embedded |
| `ask "question"` | Answer in the terminal, with sources |
| `refresh [--prune]` | Re-crawl with the saved settings and re-index only changed pages |

`--include` and `--exclude` take comma-separated globs on the URL path,
such as `"/tutorial/**,/advanced/**"`. In our test, `/de/**` did not
exclude the `/de` page itself, so list `/de` as well if you want it gone.

Optional settings in `.env`:

| Variable | Default | |
| --- | --- | --- |
| `DOCS_RAG_MODEL` | `claude-sonnet-5-5` | e.g. `claude-haiku-5-5` for cheaper runs |
| `DOCS_RAG_EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | any sentence-transformers model |
| `DOCS_RAG_DATA_DIR` | `data` | where pages, the DB and logs live |

## How it works

```
 crawl            chunk              embed               chat
┌────────┐     ┌──────────┐     ┌──────────────┐     ┌──────────────┐
│Olostep │ md  │ split by │     │ MiniLM (CPU) │     │ top-k chunks │
│ Crawls ├────►│ headings ├────►│  -> Chroma   ├────►│ -> Claude    │
└────────┘     └──────────┘     └──────────────┘     │ -> [1] [2]   │
data/pages/*.md               data/chroma/           └──────────────┘
```

1. **Crawl** (`crawl.py`): `client.crawls.create(...)` starts the crawl.
   We poll `client.crawls.info(id)` for a live progress bar, list pages
   with `client.crawls.pages(id)`, then fetch each page's Markdown with
   `client.retrieve.get(retrieve_id, formats=["markdown"])` in parallel.
   Each page is saved as `data/pages/<url-path>.md` with a small front
   matter block holding its URL.
2. **Chunk** (`chunk.py`): split at `#`…`######` headings (ignoring `#`
   inside code blocks), merge tiny sections forward, and split long ones
   by size with overlap, preferring paragraph breaks. Site chrome
   above the page's first `# H1` (banners, menus, sidebar nav) is
   dropped.
3. **Embed** (`index.py`): embed `heading path + text` locally and
   upsert into a Chroma collection (cosine distance), storing `url`,
   `title` and `heading_path` as metadata.
4. **Chat** (`rag.py`, `app.py`): embed the question, take the top 6
   chunks, and ask Claude to answer only from them and cite `[n]`. The
   sources the answer actually cites are shown as links.

## Refresh: only re-embed what changed

`data/manifest.json` stores a SHA-256 hash of every indexed page's
Markdown. `refresh` re-runs the crawl with the parameters saved in
`data/crawl.json`, rewrites the pages, then compares hashes:

| Page | Action |
| --- | --- |
| new URL | chunk, embed, add |
| hash changed | delete its old chunks, add new ones |
| hash unchanged | skip (no embedding work) |
| file removed | delete its chunks |

Use `refresh --prune` to also drop pages the new crawl didn't return.
`index` uses the same logic, so running it twice is a no-op.

The hash covers only what is indexed: the page from its first `# H1`
down, ignoring blank lines, trailing spaces and link targets. Two
crawls of an unchanged page can differ in exactly those ways, such as
absolute vs relative links. Some pages can still come back formatted
differently and get re-embedded. That costs a few seconds of local CPU
and no API credits.

## Sample Q&A

From a real run: a 30-page crawl of `fastapi.tiangolo.com` (30 pages in
about 25 s, 502 chunks), answered by `claude-sonnet-5-5`. Answers are
abridged here, and the sources are exactly what the CLI printed.

**Q: How do I make a query parameter optional?**

> Give the query parameter a default value. To make it optional without
> choosing a specific value, set the default to `None` [5]:
>
> ```python
> @app.get("/items/{item_id}")
> async def read_item(item_id: str, q: str | None = None):
>     ...
> ```
>
> A type hint alone isn't enough: `str | None` allows `None` as a value
> but doesn't make the parameter optional. Without a default it is
> still required [2]. With `Annotated`, keep the default on the function
> parameter: `q: Annotated[str | None, Query(min_length=3)] = None` [2].
>
> **Sources**
> [2] [Query Parameters and String Validations > Required parameters > Required parameters and `None`](https://fastapi.tiangolo.com/tutorial/query-params-str-validations) ·
> [3] [Query Parameters > Optional parameters](https://fastapi.tiangolo.com/tutorial/query-params) ·
> [4] [Query Parameters > Defaults](https://fastapi.tiangolo.com/tutorial/query-params) ·
> [5] [Query Parameters > Required query parameters](https://fastapi.tiangolo.com/tutorial/query-params)

**Q: How do I declare an example for a request body in the generated docs?**

> Use the `openapi_examples` parameter on `Body()` (it's also available
> on `Path()`, `Query()`, `Header()`, `Cookie()`, `Form()` and `File()`) [6].
> The keys of the dict identify each example. Each value is a dict with
> `summary`, `description`, `value` or `externalValue` [6].
>
> **Sources**
> [6] [Declare Request Example Data > Using the `openapi_examples` Parameter](https://fastapi.tiangolo.com/tutorial/schema-extra-example)

**Q: How do I add authentication?** _(the security tutorial wasn't in
the 30 crawled pages)_

> The excerpts don't include step-by-step instructions for adding
> authentication, but they do describe what FastAPI supports: HTTP
> Basic, OAuth2 (also with JWT tokens), and API keys in headers, query
> parameters and cookies [1]. I couldn't find the actual implementation
> steps or code in the excerpts.
>
> **Sources**
> [1] [Features > FastAPI features > Security and authentication](https://fastapi.tiangolo.com/features)

The bot said what it couldn't find instead of making up an auth
tutorial. Crawl more pages (or `--include "/tutorial/**"`) and it
answers from the security docs.

## Use cases

- A support bot for your product docs, or an internal wiki.
- "Chat with the docs" for an open-source library you use every day.
- Onboarding: let new engineers ask questions about internal runbooks.
- Keeping an assistant in sync with docs that change weekly, using
  `refresh` on a schedule.

## Project layout

```
src/olostep_docs_rag/
  cli.py      crawl, index, ask, refresh commands
  config.py   Settings dataclass + env loading
  crawl.py    Olostep Crawls -> data/pages/*.md
  chunk.py    heading-based Markdown chunking
  index.py    embeddings + Chroma sync with content hashes
  rag.py      retrieve, prompt, answer with citations
  utils.py    logging, slugs, atomic writes, timers
app.py        Streamlit chat UI
```

## Development

```bash
ruff format . && ruff check .
pytest
```

## License

[MIT](LICENSE)
