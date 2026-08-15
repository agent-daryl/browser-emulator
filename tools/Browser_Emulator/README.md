# Playwright Browser

Real headless Chromium browser for web research. Hybrid architecture: `ddgs` library for DuckDuckGo search + Playwright for full-page rendering and content extraction.

## Why This Exists

The old browser_emulator was a `requests` session with rotated headers — it couldn't render JavaScript, got blocked by DDG, and couldn't extract structured content. This replaces it with an actual Chromium instance.

## What It Does

- DuckDuckGo search (no API key needed)
- Full JavaScript rendering (SPAs, client-side apps, modern sites)
- Smart article extraction (finds main content, skips nav/ads/TOC)
- `search_and_read` — query then scrape top results in one call
- CLI interface for quick research from bash

## Usage

### CLI

```bash
# Search + read top result
python3 browser_emulator.py "Flutter Firebase multiplayer"

# Read a specific URL
python3 browser_emulator.py --url "https://en.wikipedia.org/wiki/Whist"
```

### Python API

```python
from browser_emulator import search, search_and_read, PlaywrightBrowser

# Search only (no browser launched)
results = search("your query", max_results=5)

# Search + scrape top results
readings = search_and_read("your query", max_read=3, max_chars=12000)

# Manual control
with PlaywrightBrowser() as b:
    b.goto("https://example.com").wait_network_idle()
    print(b.title())
    print(b.text()[:200])
    article = b.scrape_article()
    print(article["text"])
```

## Dependencies

```
playwright>=1.60.0
ddgs
```

Chromium is bundled by Playwright.

## What Changed

| Old | New |
|---|----|
| `requests` with rotated headers | Real Chromium via Playwright |
| No JavaScript rendering | Full JS/SPA support |
| Blocked by DuckDuckGo `/html/` | Uses `ddgs` library for search |
| Couldn't extract structured content | Smart article extraction with Wikipedia support |
| Fake delays pretending to be human | Actual load-state waits, network-idle detection |

## Current Version (2026-07-24+)

The current version adds proper CLI argument parsing (query first, flags after),
`--engine searxng` (70+ engines via the local SearXNG client), `--text-only` (strip
nav/footer/ads), and `--search-only`. Full CLI reference, quality rules, and
anti-patterns: see **`SKILL.md`** in this directory — that is the model-facing
context file.
