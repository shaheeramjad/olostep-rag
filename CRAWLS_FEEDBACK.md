# Crawls endpoint + Python SDK feedback

Notes from building and running `olostep-docs-rag` on 2026-11-23 with
`olostep==1.2.3` (Python 3.12, Linux). Target site:
`https://fastapi.tiangolo.com`, `--include "/**" --exclude
"/de/**,/es/**" --max-pages 30`. Three crawls were run (one `crawl`,
two `refresh`), about 90 credits in total.

Only things that actually happened are logged.

## Run metrics

| Metric | Value |
| --- | --- |
| Time to first working crawl | About 1 minute after adding the API key. The first attempt's crawl succeeded, but listing its pages hit an SDK bug (#1). After the fix, `crawl --resume` reused the crawl id with no new credits. |
| Crawl speed | 30 pages in about 21–25 s per crawl (latest: 21.4 s crawl, 4.5 s to retrieve all Markdown with 8 threads), so about 70 pages/min end to end |
| Failures | 0 of 90 pages failed to retrieve. No API errors. |
| Progress clarity | Good. `crawls.info()` returns `status`, `pages_count` and `current_depth`, enough for a live bar. The bar went from 0 to 30 quickly and the crawl finished at depth 1. |
| Error clarity | No API errors occurred. The one error we hit was a Python `TypeError` from inside the SDK (#1), which didn't say what to do. |
| Markdown quality | Body text, headings and code are well extracted, but each page also includes all the site chrome, and the format varies between crawls (#2, #3). |
| include/exclude globs | `/de/**` did **not** exclude the `/de` page itself (#5). Whether it excludes `/de/...` sub-pages wasn't tested: the crawl only reached depth 1. |

## Friction log

### 1. Sync `client.crawls.pages(crawl_id)` crashes

- **Issue:** Listing pages of a crawl by id with the sync client fails.
- **Example:**
  ```python
  client = Olostep(api_key=...)
  client.crawls.pages("crawl_jyur36npdd", wait_for_completion=False)
  # TypeError: object async_generator can't be used in 'await' expression
  #   olostep/clients/sync_client.py, line 770, in _inner
  ```
  `crawl.pages()` on a `Crawl` object returned by `create()` goes
  through a different wrapper, but after a restart you only have the
  crawl id, so you need the menu method.
- **Workaround used:** `async with AsyncOlostep(...) as c:` then
  `async for page in c.crawls.pages(id, wait_for_completion=False)`.
- **Suggestion:** Wrap `CrawlMenu.pages` like `Crawl.pages` in the sync
  client, and add a sync test for resuming from a crawl id.
- **Priority:** high

### 2. Markdown for the same unchanged page varies between crawls

- **Issue:** Crawling the same page twice, minutes apart, returned
  different Markdown. This breaks content-hash change detection (the
  "refresh only changed pages" use case): on the first `refresh`, 20 of
  30 pages looked "changed" although the docs hadn't changed.
- **Examples** (crawl `crawl_jyur36npdd` vs a later crawl, same URLs):
  - **Links:** absolute in one crawl, relative in the other, with the
    direction flipping per page:
    `## Defaults [¶](https://fastapi.tiangolo.com/tutorial/query-params/#defaults ...)`
    vs `## Defaults [¶](#defaults ...)`, and
    `<a href="https://fastapi.tiangolo.com/ai-development/">` vs
    `<a href="../../ai-development/">`.
  - **Whitespace:** an extra blank line right after every opening code
    fence (```` ``` ```` + empty line) in one crawl only.
  - **Converter style:** `*   item` vs `- item`, `* * *` vs `---`, and
    `<details><summary>` kept as HTML vs flattened to text.
  - **Content:** footer present in one crawl and missing in the other
    (`/about`: 36 vs 87 words).
- **Workaround used:** hash the text with blank lines, trailing spaces
  and link targets removed. That made 16 of 30 pages match. The other
  14 still differ because of #3 and the footer.
- **Suggestion:** Make the Markdown output deterministic (one converter,
  always-absolute links). A `content_hash` field on crawl pages would
  make "what changed?" trivial for users.
- **Priority:** high

### 3. Code blocks sometimes flattened into one line of inline code

- **Issue:** Some pages came back with multi-line code blocks collapsed
  into a single inline code span, losing newlines and in some cases the
  terminal output under them. The same page in another crawl had proper
  fenced blocks. For a docs RAG this is the worst failure, since code is
  what people ask about.
- **Example** (`/tutorial`, crawl `crawl_jyur36npdd`):
  ```
  `uv init awesome-project --bare cd awesome-project uv add "fastapi[standard]"`
  ```
  vs the next crawl:
  ````
  ```
  uv init awesome-project --bare
  cd awesome-project
  uv add "fastapi[standard]"
  ```
  ````
  In the latest crawl, 1 of 33 saved pages (`/fr`) has this style: 0
  code fences and `*   ` list markers.
- **Gate 0 run (crawls `crawl_tlp4fmjq2n` + `crawl_5rbbwg47rq`, FastAPI
  `/tutorial/**`):** 9 of 44 saved pages have no code fences at all,
  e.g. `tutorial/sql-databases` and `tutorial/query-params-str-validations`.
  4 more have fences but also lost some blocks to one-line inline code
  (e.g. `tutorial/schema-extra-example`, `tutorial/handling-errors`).
  A 5-page dry run just before (`crawl_8wml80xo7g`) had none flattened,
  so it varies from run to run.
- **Suggestion:** Always emit fenced code blocks and keep newlines in
  `<pre>`. Add the language tag when the HTML has one (`class="language-python"`).
  Fences currently have no language tag.
- **Priority:** high

### 4. No main-content-only option; every page includes the site chrome

- **Issue:** Every page starts with banners, sponsors, the language
  menu and the full sidebar nav, and ends with prev/next links and the
  footer. On `/learn`, the first `# H1` is at line 224 of 253, so almost
  all of it is navigation. Rotating sponsor banners also change between
  crawls.
- **Workaround used:** drop everything above the first H1 before
  chunking and hashing.
- **Suggestion:** A `main_content_only` / readability option on crawls
  (the docs mention `scrape_options.parser`, but the SDK's
  `crawls.create()` doesn't expose `scrape_options`, see #6).
- **Priority:** medium

### 5. `--exclude "/de/**"` doesn't exclude the `/de` page

- **Issue:** With `--exclude "/de/**,/es/**"`, the pages
  `https://fastapi.tiangolo.com/de` and `/es` were still crawled, in all
  three crawls. (No `/de/...` sub-pages appeared, but the crawl only
  reached depth 1, so that doesn't prove the glob excludes them.) Page
  URLs also came back without the trailing slash the site uses (`/de`
  while the site links to `/de/`), which may be why `/de/**` didn't
  match. Separately, the other language homepages (`/fr`, `/ja`, `/ko`,
  `/ru`, ...) used 9 of the 30-page budget at depth 1.
- **Seen again in the Gate 0 run:** `--exclude "/tutorial/security/**"`
  kept every security sub-page out, but `/tutorial/security` itself was
  crawled and indexed. So the sub-page half is now confirmed working,
  and `/x/**` doesn't match `/x`.
- **Example:** `data/crawl.json` → `urls` contains
  `https://fastapi.tiangolo.com/de` and `https://fastapi.tiangolo.com/es`.
- **Suggestion:** Document whether `/x/**` should match `/x` (most
  users will expect it to). An exclude-by-language or "stay in
  this locale" option would help docs sites a lot.
- **Priority:** medium

### 6. `crawls.create()` doesn't expose `scrape_options`, `storage` or `timeout`

- **Issue:** The docs (docs.olostep.com/features/crawls/crawls) list
  `scrape_options` (`formats`, `parser`), `storage.expires_in` and
  `timeout`. The SDK's `create()` has none of them, so you can't choose
  formats up front or keep content past the 7-day default.
- **Example:** `create(start_url, *, url, max_pages, include_urls,
  exclude_urls, max_depth, include_external, include_subdomain,
  search_query, top_n, webhook_url, follow_robots_txt,
  validate_request)` in `olostep/frontend/crawl_menu.py`.
- **Suggestion:** Add them, or a `**extra` pass-through for new fields.
- **Priority:** medium

### 7. SDK warns on every `info()` call about unknown response fields

- **Issue:** Each poll printed both a `UserWarning` and a log warning,
  which buries the progress bar in noise:
  ```
  UserWarning: The API response from 'Get Crawl Info' contains 3 extra
  field(s) not defined in the SDK model: cost_usd, credits_consumed,
  storage. ...
  WARNING  Extra fields detected in response from 'Get Crawl Info'
  ```
  It also means `credits_consumed` / `cost_usd` come back from the API
  but aren't available on `CrawlInfo`, so we can't show the crawl's cost.
- **Workaround used:** filter `UserWarning` from `olostep` and set the
  `olostep` logger to ERROR.
- **Suggestion:** Add `credits_consumed` and `cost_usd` to `CrawlInfo`,
  and warn about unknown fields once per process, at DEBUG.
- **Priority:** medium

### 8. `pages(wait_for_completion=True)` has a fixed 10-minute timeout

- **Issue:** `crawl.pages()` waits via `_wait_till_done_(caller,
  crawl_id)` with the defaults `timeout_seconds=600` and
  `check_every_n_secs=10`, which `pages()` can't change. A 500-page crawl
  (the README example) could raise `OlostepClientError_Timeout` here.
  Found while reading the SDK; our 30-page crawls finished in about 25 s, so
  we didn't hit it.
- **Workaround used:** poll `crawls.info()` ourselves, then call
  `pages(..., wait_for_completion=False)`.
- **Suggestion:** Forward `timeout_seconds` / `check_every_n_secs`.
- **Priority:** medium

### 9. `retrieve.get()` docstring says `formats` is unreliable

- **Issue:** The SDK docstring says *"Bug 2: The formats parameter
  behavior is unpredictable - consider leaving it blank"* and *"Invalid
  IDs ... return empty results instead of errors"*, while the docs use
  `page.retrieve(["markdown"])`. In our runs `formats=["markdown"]`
  worked on all 90 pages, so the warning looks stale.
- **Suggestion:** Remove the warning if it's fixed. Return an error for
  expired or invalid retrieve ids.
- **Priority:** low

### 10. Docs and SDK name the webhook differently

- **Issue:** The docs say `webhook`, the SDK argument is `webhook_url`.
- **Priority:** low

### 11. No progress callback; the sync `pages()` buffers everything

- **Issue:** A progress UI needs a hand-written polling loop around
  `info()`. The sync iterator also collects every page before yielding
  the first (`_wrap_async_iterator` → `_collect_all`).
- **Suggestion:** `wait_till_done(on_progress=...)`, and lazy paging in
  the sync client.
- **Priority:** low

## What worked really well

- **Fast and reliable:** 3 crawls × 30 pages, 0 failed retrievals, about 25 s
  per crawl including fetching every page's Markdown.
- **Resumable by id:** saving the crawl id and re-reading pages later
  worked without re-crawling or extra crawl credits.
- **Good extraction once you skip the chrome:** headings, prose,
  admonitions ("Tip", "Info") and, in most pages, code all came through
  well. The RAG answers were accurate and cited the right sections.
- **SDK source is easy to read:** clear docstrings with examples on
  every public method, and typed pydantic response models.
- **Friendly arguments:** `include_urls` / `exclude_urls` accept a string or a
  list, and `start_url` / `url` are both accepted.
