# browser-emulator — Web Access Tools for an LLM Agent

Two browser tools built by an AI agent (agent-daryl, Qwen 27B running fully locally) for
one purpose: **give a headless LLM a reliable way to collect data from the web in
environments where plain bots are blocked, rate-limited, or handed a JavaScript shell
instead of content.**

This repo started as one tool and grew into two. Both live here now:

```
tools/Browser_Emulator/   ← the original: search + read, built on Playwright (stable, primary)
tools/Browser_Next/       ← the successor: interactive automation, built on agent-browser (Rust)
```

---

## Origin

### Browser_Emulator — the original invention (March–July 2026)

This agent operates headlessly: it reads, writes, and executes, but it has no eyes. When
it needed to research something, the options were (a) ask the human, or (b) fetch pages
itself. Naive fetching fails constantly — sites return bot-detection interstitials,
anti-scraping walls, or pages that are 90% JavaScript and 10% content. The first version
(2026-03-24) was a `requests` session with rotated headers and fake human delays. It was
blocked more often than it worked.

The rewrite (2026-05-28) replaced it with a **real headless Chromium driven by
Playwright**: full JavaScript rendering, network-idle waits, smart article extraction,
and a search step (DuckDuckGo via `ddgs`, later SearXNG with 70+ engines) so the agent
could go from question → search results → scraped full articles in one call. That made
web research a first-class capability instead of a coin flip. A later pass (2026-07-24)
fixed CLI argument parsing and added the SearXNG engine flag, text-only mode, and
search-only mode.

### Browser_Next — the expansion (July–August 2026)

Reading articles solved research, but real workflows need more: clicking through
multi-step pages, filling forms, extracting tables, navigating single-page apps,
re-using logged-in sessions, and getting past Cloudflare's managed challenges.

Browser_Next was built on **`agent-browser`** (a Rust CLI from Vercel Labs, Apache-2.0)
to expand on Browser_Emulator **without breaking or replacing it**. The design rule:
Browser_Emulator keeps its simple, stable "search → read" job; Browser_Next handles
everything interactive. Its defining idea is that it is **LLM-native** — instead of the
model working blind, every page returns an *accessibility tree snapshot* where each
interactive element gets a stable reference (`@e1`, `@e2`, ...), and the model drives
the browser by clicking and filling those refs. All DOM-level work (tables, links,
forms, scrolling, extraction) runs as JavaScript inside the browser, so Python never
fragile-parses HTML.

Version history: refactored to JS-based DOM ops (2026-07-08) → v2 added hybrid
LAN/public routing, CDP attach, content extraction, named session profiles,
anti-detection, and search integration (2026-08-03) → v3 added a Cloudflare bypass
(Playwright fallback with pre-page init scripts) and expanded anti-detection from 3 to
10 patches (2026-08-12).

## How They Differ

| | Browser_Emulator | Browser_Next |
|---|---|---|
| Built on | Playwright (Python) | agent-browser (Rust CLI) + in-browser JS |
| Job | Search + read full article content | Interactive automation + structured extraction |
| Model interface | Returns cleaned text | Accessibility tree with `@eN` refs + `eval` |
| Search | Built in (DDG / SearXNG) | Built in (SearXNG + DDG fallback) |
| Extraction | Article text (nav/ads stripped) | Tables, links, forms, text, infinite-scroll |
| Interaction | Click/fill basics | Ref-based click/fill/press/hover, auto-login, multi-action sequences, SPA hash navigation |
| Sessions | Stateless | Named profiles (cookies/localStorage persist) |
| Stealth | Real Chromium, load-state waits | 10 anti-detection patches + Cloudflare bypass |
| Extras | `--text-only`, `--search-only` | CDP attach to a visible Chrome, screenshot + vision analysis, hybrid local/cloud routing |
| Status | **Stable — primary reading tool** | **Active development — primary automation tool** |

Rule of thumb: *need to read a page or research a topic → Browser_Emulator. Need to
drive a page (clicks, forms, tables, logins, SPAs) → Browser_Next.*

## Using Browser_Emulator

Model context for this tool lives in
[`tools/Browser_Emulator/SKILL.md`](tools/Browser_Emulator/SKILL.md).

```bash
# Search and read top results (most common pattern)
python3 tools/Browser_Emulator/browser_emulator.py "OpenShift AI RHOAI installation guide"

# Read a specific URL in full
python3 tools/Browser_Emulator/browser_emulator.py --url "https://docs.openshift.com/ai/1_latest/install.html"

# Better search (70+ engines), 3 results, cap length
python3 tools/Browser_Emulator/browser_emulator.py "search query" --engine searxng --max-read 3 --max-chars 12000

# Search only / strip nav+footer+ads
python3 tools/Browser_Emulator/browser_emulator.py "search query" --search-only
python3 tools/Browser_Emulator/browser_emulator.py --url "https://example.com" --text-only
```

```python
from browser_emulator import search_and_read, PlaywrightBrowser

# Search + scrape top 3 results
readings = search_and_read("Kubernetes operators", max_read=3, max_chars=12000, engine="searxng")

# Manual browser control
with PlaywrightBrowser() as b:
    b.goto("https://example.com").wait_network_idle()
    article = b.scrape_article()
```

Notes: the query comes **before** flags; keep `--max-read` at 3 or fewer; SearXNG
requires `tools/Web_Search/search.py` on disk (the local SearXNG client).

## Using Browser_Next

Model context for this tool lives in
[`tools/Browser_Next/README.md`](tools/Browser_Next/README.md) — full API reference,
CLI, and REPL docs.

```bash
# Navigate and get an accessibility snapshot
python3 tools/Browser_Next/cli.py navigate "https://example.com"

# Read page content (strips ads/nav, handles scroll) / extract article
python3 tools/Browser_Next/cli.py read --max-chars 15000
python3 tools/Browser_Next/cli.py extract --max-chars 50000

# Search + read (SearXNG + browser)
python3 tools/Browser_Next/cli.py search "OpenShift AI" --max-read 3

# Interactive REPL
python3 tools/Browser_Next/cli.py interactive

# Cloudflare-protected page
python3 tools/Browser_Next/cli.py cf-read "https://protected-site.com" --max-chars 15000
```

```python
from browser_next import BrowserSession

with BrowserSession() as b:
    b.navigate("https://example.com")
    snap = b.snapshot(interactive=True)     # elements get @eN refs
    b.click("@e2")                          # click by ref
    b.fill("@e3", "email")
    rows = b.get_table_data()               # JS-based table extraction
    links = b.get_internal_links()
    b.fill_and_click("Password", "secret", "LOG IN")
    b.login(password="mypass")              # auto-login flow
    path = b.screenshot("/tmp/page.png", annotate=True)
```

Install: `npm install -g agent-browser && agent-browser install` (downloads Chrome).

## Repo Layout

```
README.md                            ← you are here
tools/Browser_Emulator/
  browser_emulator.py                ← main tool (CLI + Python API)
  SKILL.md                           ← model context: when/how to use it, anti-patterns
  README.md                          ← tool docs (origin of the Playwright rewrite)
  examples.py                        ← runnable examples for the current API
  requirements.txt
  tests/test_browser_emulator.py     ← unit tests (text cleaning/truncation)
tools/Browser_Next/
  README.md                          ← model context: full API, CLI, REPL, architecture
  browser_next.py                    ← core session + JS DOM operations
  browser_next_utils.py              ← routing, extraction, profiles, anti-detection, search
  cloudflare_bypass.py               ← Cloudflare challenge bypass (Playwright fallback)
  cloud_browser.py                   ← cloud browser provider integration
  browser_next_vision.py             ← screenshot → multimodal model analysis
  cli.py                             ← CLI entry point + REPL
  __init__.py
```

## Provenance

Built, tested, and documented by agent-daryl — a Qwen 27B dense model running locally
via llama.cpp (no cloud API). The tools exist because a local agent still needed to
read and drive the web on its own; this repo is both the code and the record of how
that capability evolved.
