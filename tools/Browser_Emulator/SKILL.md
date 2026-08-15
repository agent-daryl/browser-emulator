# SKILL: Browser Emulator

## Purpose
Full headless Chromium browser for JavaScript rendering, article scraping, and deep web research. Uses Playwright + DuckDuckGo search (or SearXNG via `--engine searxng`).

## When to Use
- Reading full article content from URLs (not just snippets)
- Researching topics that require JavaScript-rendered pages
- Extracting structured content from complex web pages
- When search snippets are insufficient and you need the full text

## Correct Usage

```bash
# Search and read top results (most common pattern)
python3 tools/Browser_Emulator/browser_emulator.py "OpenShift AI RHOAI installation guide"

# Read a specific URL in full
python3 tools/Browser_Emulator/browser_emulator.py --url "https://docs.openshift.com/ai/1_latest/install.html"

# Custom: read 3 results, max 12000 chars each, use SearXNG for better results
python3 tools/Browser_Emulator/browser_emulator.py "search query" --max-read 3 --max-chars 12000 --engine searxng

# Search only, don't read results
python3 tools/Browser_Emulator/browser_emulator.py "search query" --search-only

# Read a URL and strip nav/footer/ads
python3 tools/Browser_Emulator/browser_emulator.py --url "https://example.com" --text-only
```

```python
# Python API
from browser_emulator import search_and_read, PlaywrightBrowser

# Search + scrape top 3 results
readings = search_and_read("Kubernetes operators", max_read=3, max_chars=12000)

# Use SearXNG for better search results
readings = search_and_read("Kubernetes operators", max_read=3, max_chars=12000, engine="searxng")

# Manual browser control for complex interactions
with PlaywrightBrowser() as b:
    b.goto("https://example.com").wait_network_idle()
    text = b.text()[:2000]
    article = b.scrape_article()
```

## CLI Arguments (Fixed 2026-07-24)
| Flag | Default | Description |
|------|---------|-------------|
| `query` | required (positional) | Search query text |
| `--url` | — | Read a specific URL directly |
| `--max-read` | 3 | Number of search results to read |
| `--max-chars`, `-m` | 12000 | Max characters per article |
| `--engine` | `ddgs` | Search engine: `ddgs` (DuckDuckGo) or `searxng` (70+ engines, better results) |
| `--text-only` | — | Strip nav/footer/ads, return only text |
| `--search-only` | — | Only show search results, don't read pages |

**IMPORTANT:** The query must come BEFORE flags. Use quotes for multi-word queries.
```bash
# CORRECT:
python3 browser_emulator.py "search query" --max-read 3 --engine searxng

# WRONG (flags get swallowed into query):
python3 browser_emulator.py --max-read 3 "search query"
```

## Anti-Patterns

| Anti-Pattern | Problem | Fix |
|---|---|---|
| Using webfetch tool instead | Can't render JavaScript, gets blocked | Use Browser_Emulator for JS-heavy sites |
| Reading 10+ pages per query | Wastes time and tokens, most are noise | Limit to `--max-read 3`, pick the best results |
| Not checking output quality | Extracted text may be nav/ads, not content | Verify output has substantive content before using |
| Scraping login-protected pages | Will fail or get captcha | Only use for publicly accessible content |
| Not setting `--max-chars` | Can return enormous outputs that waste tokens | Always set `--max-chars 12000` |
| Not using `--engine searxng` | DDG returns poor results for technical queries | Use `--engine searxng` for better search quality |

## Quality Rules
1. Use `--engine searxng` for better search results (70+ engines vs DDG)
2. Use `--url` flag when you already know the target URL
3. Always set `--max-read 3` maximum — quality over quantity
4. Set `--max-chars 12000` to control output size
5. Use PlaywrightBrowser context manager for complex, multi-step browsing
6. Call `.wait_network_idle()` before scraping to ensure full page load

## Dependencies
- Playwright (Chromium bundled)
- ddgs library for DuckDuckGo search
- `tools/Web_Search/search.py` for SearXNG engine option

## Recent Changes (2026-07-24)
- **Fixed CLI argument parsing** — flags no longer get swallowed into the search query
- **Added `--engine searxng`** — use SearXNG (70+ engines) instead of DDG for better results
- **Added `--text-only`** — strip nav/footer/ads for cleaner output
- **Added `--search-only`** — search without reading pages
- **Added SearXNG fallback** — Python API accepts `engine="searxng"` parameter
