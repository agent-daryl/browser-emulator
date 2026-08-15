# Browser Next — LLM-Native Browser Automation (v2)

Next-generation browser tool for opencode, built on `agent-browser` (Rust CLI by Vercel Labs, Apache-2.0).

## Architecture

```
opencode (Qwen)
  ↓ Python calls
tools/Browser_Next/browser_next.py   ← Session lifecycle, navigation flows, state
tools/Browser_Next/browser_next_utils.py  ← Hybrid routing, content extraction, anti-detection
  ↓ subprocess + evaluate_stdin()
agent-browser CLI (Rust)             ← Drives Chromium via CDP
  ↓ JS evaluation
Chromium (headless or connected)    ← DOM queries run as JavaScript
```

**Three layers:**
- **Python**: session lifecycle, navigation sequences, retry logic, hybrid routing, SPA/domain awareness, search integration
- **JavaScript (via `eval`)**: DOM queries, text extraction, table parsing, link filtering, scrolling, article extraction
- **agent-browser CLI**: the Rust binary driving headless Chrome (or CDP-connected Chrome)

## Installation

```bash
npm install -g agent-browser   # Rust CLI
agent-browser install          # Downloads Chrome
```

## Python API

### Core Navigation

```python
from browser_next import BrowserSession

with BrowserSession() as b:
    # Navigate — returns accessibility tree snapshot
    b.navigate("https://example.com")
    title = b.get_title()
    url = b.get_url()
```

### Ref-Based Interaction (from accessibility snapshot)

```python
# Get snapshot — each interactive element has @eN ref IDs
snap = b.snapshot(interactive=True)
# snap: "- link "Learn more" [ref=e2]\n- button "Submit" [ref=e5]"

b.click("@e2")           # Click by ref
b.fill("@e3", "email")   # Fill input
b.press("Enter")         # Keyboard
b.hover("@e4")           # Mouse hover
```

### JavaScript Page Operations

All DOM-level work runs as JavaScript inside the browser — not Python parsing accessibility text:

```python
# Text extraction
text = b.get_all_text(max_chars=5000)

# Table extraction
rows = b.get_table_data()                       # Last table on page
tables = b.get_all_tables()                     # All tables
client_list = b.find_table_by_header("Assigned IP")  # Find by header text

# Link discovery
links = b.get_internal_links()   # Same-domain links only, no external noise
all_links = b.get_all_links()    # Every link on the page
sidebar = b.get_sidebar_links()  # Navigation sidebar detection

# Text search in DOM
result = b.find_element_by_text("Sign In")       # First match
matches = b.find_elements_by_text("DHCP Client") # All matches

# Form field discovery
fields = b.get_form_fields()  # All inputs, selects, textareas with labels

# Scrolling (JS — instant, not 5 CLI calls)
b.scroll_to_bottom_js()
b.scroll_by_js(500)
b.scroll_to_top_js()
full_snap = b.scroll_and_capture()  # Scroll bottom → snapshot → scroll top
```

### High-Level Flows

```python
# Click by text (auto-finds ref, clicks, refreshes snapshot)
b.click_text("Advanced")
b.click_text("DHCP Server")

# Fill + click with auto-re-snapshot
b.fill_and_click("Password", "secret", "LOG IN")

# Auto-login (handles password-only, username+password, various submit texts)
b.login(password="mypass")

# SPA navigation
b.navigate_hash("#dhcpServerAdv")
hash = b.get_hash()
b.wait_for_hash("networkStatus")

# Domain awareness
b.navigate_safe("http://10.10.0.254/page")  # Raises if external domain

# Multi-action sequence (auto-re-snapshots between each step)
b.multi_action([
    {'type': 'click', 'text': 'Advanced', 'wait_ms': 2000},
    {'type': 'click', 'text': 'DHCP Server', 'wait_ms': 2000},
    {'type': 'js', 'script': 'document.querySelectorAll("table").length'},
    {'type': 'snapshot'},
])
```

### Raw JavaScript

```python
# Single expression
count = b.evaluate("document.querySelectorAll('a').length")

# Multi-line script
result = b.evaluate_stdin("""
(() => {
  return document.title;
})()
""")
```

### Session State

```python
# Navigation history
urls = b.visited_urls()       # ['https://example.com', ...]
b.is_new_url("https://...")   # True/False

# Domain/SPA awareness
domain = b.get_domain()       # "example.com"
is_spa = b.is_spa()           # True if hash-based SPA
```

### Screenshot & Vision

```python
# Screenshot (creates /tmp/browser_next_*.png)
path = b.screenshot("/tmp/page.png", full_page=True, annotate=True)

# Vision analysis via Qwen multimodal weights
from browser_next_vision import analyze_screenshot
answer = analyze_screenshot(b, "What does this chart show?")
```

---

## v2 Features

### Hybrid Routing — Auto-detect LAN vs Public URLs

```python
from browser_next_utils import is_private_url, route_url

is_private_url("http://10.10.0.254/")     # True — LAN
is_private_url("http://10.10.1.1/")      # True — LAN
is_private_url("https://github.com/")       # False — public
is_private_url("http://router.local/")      # True — .local TLD

route = route_url("http://10.10.0.254/", cloud_provider="browserbase")
# {"mode": "local", "provider": "agent-browser", "reason": "private URL"}

route = route_url("https://github.com/", cloud_provider="browserbase")
# {"mode": "cloud", "provider": "browserbase", "reason": "public URL"}

# Auto-routing in navigate:
with BrowserSession(cloud_provider="browserbase", auto_private_routing=True) as b:
    b.navigate_auto("http://10.10.0.254/")   # Goes to local browser
    b.navigate_auto("https://github.com/")    # Goes to cloud provider
```

### CDP Connect — Attach to Running Chrome/Edge

```python
from browser_next_utils import find_cdp_endpoint, launch_chrome_cdp

# Find running Chrome with --remote-debugging-port
endpoint = find_cdp_endpoint(port=9222)
# "ws://localhost:9222/devtools/browser/..."

# Launch Chrome with CDP enabled
endpoint = launch_chrome_cdp(port=9222, headless=False)
# Returns CDP WebSocket URL

# Connect browser session to running Chrome
with BrowserSession(cdp_endpoint=endpoint) as b:
    # You can see what the browser sees in real-time!
    b.navigate("https://example.com")
```

### Content Extraction — Pure Reading Mode

```python
# Read page content (strips ads, nav, footer — handles infinite scroll)
result = b.read_page(max_chars=15000, scroll=True)
# {"title": "...", "url": "...", "text": "clean article text", "length": 12345}

# Extract article with site-specific selectors (Wikipedia, Medium, Ghost, etc.)
result = b.extract_article(max_chars=50000)

# Handle infinite scroll / lazy loading
result = b.extract_with_scroll(max_scrolls=5, max_chars=50000)
```

### Session Persistence — Named Profiles

```python
# Create session with named profile
with BrowserSession(profile="my-workspace") as b:
    b.navigate("https://github.com/login")
    b.login(username="user", password="pass")
    b.close_and_save()  # Saves cookies + localStorage

# Later — restore session
with BrowserSession(profile="my-workspace") as b:
    b.load_session()  # Restores cookies
    b.navigate("https://github.com/")  # Already logged in!

# List saved profiles
from browser_next_utils import list_profiles
print(list_profiles())  # ["my-workspace", "personal", ...]
```

### Anti-Detection — Fingerprint Randomization

```python
# Enable full anti-detection
with BrowserSession(anti_detection=True) as b:
    # Randomized user agent, hidden webdriver, spoofed navigator, canvas noise
    b.navigate("https://example.com")  # Auto-applies anti-detection after page load

from browser_next_utils import get_random_ua, get_anti_detection_script
ua = get_random_ua()  # Random realistic Chrome/Edge UA
script = get_anti_detection_script()  # Full anti-detection init script
```

**Anti-detection patches (10 total):** `navigator.webdriver`, `navigator.plugins/languages`, `navigator.hardwareConcurrency/deviceMemory`, `window.chrome`, `navigator.maxTouchPoints`, `navigator.connection`, WebGL renderer/vendor spoof, `window.outerWidth/Height`, `permissions.query`, canvas fingerprint noise.

### Cloudflare Bypass — Playwright Fallback

When `agent-browser` gets blocked by Cloudflare's managed challenge tier, Browser_Next automatically falls back to Playwright, which uses `add_init_script()` to hide automation **before** page JavaScript runs.

```python
from browser_next import BrowserSession
from cloudflare_bypass import try_cloudflare_bypass, is_cloudflare_challenge

# Method 1: navigate_cf() — auto-detects Cloudflare and falls back to Playwright
with BrowserSession() as b:
    result = b.navigate_cf("https://arrowhead.zendesk.com/hc/en-us/articles/12345")
    # First tries agent-browser → if Cloudflare blocks → switches to Playwright

# Method 2: One-shot bypass
result = try_cloudflare_bypass("https://cloudflare-protected-site.com")
# Returns {"title": "...", "url": "...", "text": "...", "length": 1234, "bypassed_cf": True}

# Method 3: Direct Playwright browser
from cloudflare_bypass import PlaywrightCloudflareBrowser
with PlaywrightCloudflareBrowser() as cf:
    cf.navigate("https://protected-site.com", wait_for_cf=30)
    text = cf.get_text(max_length=50000)
    print(cf.get_title())
```

**Why Playwright works where agent-browser fails:**
| Factor | agent-browser | Playwright |
|---|---|---|
| `navigator.webdriver` | Set after page load (too late) | Set before page JS (via `add_init_script`) |
| TLS fingerprint | Standard headless Chrome | Same Chromium, but different init sequence |
| Cloudflare managed tier | Blocked | **Passes** |

### Search Integration — SearXNG + DDG

```python
from browser_next_utils import search

# Search (SearXNG primary, DDG fallback)
results = search("OpenShift AI installation", max_results=5)
# [{"title": "...", "href": "...", "body": "..."}, ...]

# Search + read top results (like original Browser_Emulator)
with BrowserSession() as b:
    readings = b.search_and_read(
        "OpenShift networking guide",
        max_read=3, max_chars=15000, engine="searxng"
    )
    for r in readings:
        if r["success"]:
            print(r["article"]["text"])
```

---

## CLI Usage

```bash
# Navigate and get snapshot
python3 tools/Browser_Next/cli.py navigate "https://example.com"

# Read page content (strips ads/nav, handles scroll)
python3 tools/Browser_Next/cli.py read --max-chars 15000

# Extract article content
python3 tools/Browser_Next/cli.py extract --max-chars 50000

# Extract with infinite scroll
python3 tools/Browser_Next/cli.py extract-scroll --max-scrolls 5

# Check if URL is private/LAN
python3 tools/Browser_Next/cli.py is-private "http://10.10.0.254/"

# Route URL to local or cloud
python3 tools/Browser_Next/cli.py route "https://github.com/" --cloud-provider browserbase

# Find running Chrome CDP endpoint
python3 tools/Browser_Next/cli.py cdp-find --port 9222

# List saved profiles
python3 tools/Browser_Next/cli.py profiles

# Search + read (SearXNG + browser)
python3 tools/Browser_Next/cli.py search "OpenShift AI" --max-read 3

# Interactive REPL
python3 tools/Browser_Next/cli.py interactive

# Cloudflare bypass — read Cloudflare-protected pages
python3 tools/Browser_Next/cli.py cf-read "https://protected-site.com" --max-chars 15000
```

## REPL Commands

```
browser> navigate https://example.com
browser> snapshot -i          # Interactive elements only
browser> click @e1            # Click by ref
browser> fill @e2 "text"      # Fill input
browser> press Enter          # Press key
browser> scroll down 500      # Scroll page
browser> eval "document.title"
browser> console              # JS console messages
browser> screenshot --annotate
browser> search "query"       # SearXNG + browser read
browser> read 15000           # Read page (strips ads/nav)
browser> extract 50000        # Extract article content
browser> profile save         # Save session to profile
browser> profile load         # Load session from profile
browser> profile              # List profiles
browser> is-private http://10.10.0.254/
browser> cdp-find             # Find Chrome CDP endpoint
browser> cf-read <url>        # Cloudflare bypass read
browser> navigate-cf <url>    # Navigate with CF fallback
```

## What Gets Created on Disk

**Per session (auto-cleaned on close):**
- `/tmp/agent-browser-chrome-{uuid}/` — ~5MB Chrome profile (cookies, cache). Deleted when session closes.

**Persistent (you control):**
- `/tmp/browser_next_*.png` — Screenshots, only created when `screenshot()` is called.
- `~/.browser_next/profiles/` — Named session profiles (cookies, localStorage).

**Nothing else.** No state, logs, HAR files, or traces unless explicitly enabled.

## Files

- `browser_next.py` — Core module. Python orchestrator + JS snippets for DOM operations.
- `browser_next_utils.py` — Hybrid routing, content extraction, session persistence, anti-detection, search.
- `cloudflare_bypass.py` — Cloudflare bypass via Playwright. Auto-detects CF challenges, falls back to Playwright with pre-page init scripts.
- `cli.py` — CLI entry point + SearXNG search integration.
- `browser_next_vision.py` — Vision analysis via Qwen multimodal weights.
- `__init__.py` — Clean imports.

## What Changed (v3 — 2026-08-12)

**New features:**
- **Cloudflare bypass** — Auto-detects Cloudflare challenges, falls back to Playwright. Uses `add_init_script()` to hide automation before page JS runs. Passes Cloudflare's managed tier that blocks agent-browser.
- **Anti-detection expanded** — Was 3 patches, now 10: added `window.chrome`, `maxTouchPoints`, `navigator.connection`, WebGL spoof, `outerWidth/Height`, `permissions.query`, source URL obfuscation.
- **Auto-apply anti-detection** — `navigate()` now auto-applies patches when `anti_detection=True` (was manual `apply_anti_detection()` call).
- **Bug fix** — `_WEBC_HIDE` → `_WEBRTC_HIDE` typo crash.

## What Changed (v2 — 2026-08-03)

**New features:**
- **Hybrid routing** — Auto-detects LAN vs public URLs. Routes `http://10.10.0.254/` to local browser, `https://github.com` to cloud provider.
- **CDP connect** — Attach to running Chrome/Edge instance. See what the browser sees in real-time.
- **Content extraction** — Pure reading mode. Handles SPAs, infinite scroll, lazy loading, paywalls, ad-stripping with dedicated JS strategies.
- **Session persistence** — Named profiles with cookie/storage survival across restarts.
- **Anti-detection** — Randomized fingerprints, user agent rotation, webdriver hiding, canvas noise, navigator spoofing.
- **Search integration** — SearXNG + DDG search, search-and-read workflow (like original Browser_Emulator).
- **Article extraction** — Site-specific selectors for Wikipedia, Medium, Ghost, Substack, GitHub, Hacker News, etc.

**What Changed (Refactored 2026-07-08):**
**Before:** Python parsed accessibility tree text to extract tables, links, regions, and content. Fragile, slow, unreliable when page structure changed.

**After:** All DOM-level operations run as JavaScript via `evaluate_stdin()`. Python handles session lifecycle, navigation flows, history tracking, and coordination. JS snippets handle table extraction, link filtering, sidebar detection, text search, scrolling, and form field discovery.
