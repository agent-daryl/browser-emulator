#!/usr/bin/env python3
"""
Browser Next — Hybrid routing, CDP connect, content extraction, session persistence, anti-detection.
Extended functionality built on agent-browser (Rust CLI) + Playwright fallback.
"""

import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Hybrid routing — auto-detect LAN vs public URLs
# ---------------------------------------------------------------------------

# RFC 1918 + loopback + link-local + .local/.lan/.internal
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]

_PRIVATE_TLD_SETS = {".local", ".lan", ".internal", ".home.arpa"}


def is_private_url(url: str) -> bool:
    """
    Check if a URL targets a private/loopback/LAN address.
    Returns True for localhost, 127.x, 10.x, 172.16-31.x, 192.168.x, *.local, *.lan, *.internal.
    """
    from urllib.parse import urlparse
    parsed = urlparse(url)
    hostname = parsed.hostname or ""

    # Check TLD-based private domains
    for tld in _PRIVATE_TLD_SETS:
        if hostname.endswith(tld):
            return True

    # Check IPv6 loopback
    if hostname == "::1":
        return True

    # Try parsing as IP
    try:
        addr = ipaddress.ip_address(hostname)
        for net in _PRIVATE_NETWORKS:
            if addr in net:
                return True
        return False
    except ValueError:
        pass

    # hostname is a domain name — resolve and check
    # (skip DNS for now, treat named hosts as public unless they match patterns)
    return False


def route_url(url: str, cloud_provider: Optional[str] = None) -> Dict[str, Any]:
    """
    Determine whether URL should go to local browser or cloud provider.
    Returns dict with 'mode' ('local' or 'cloud') and 'provider'.
    """
    if is_private_url(url):
        return {"mode": "local", "provider": "agent-browser", "reason": "private URL"}
    if cloud_provider:
        return {"mode": "cloud", "provider": cloud_provider, "reason": "public URL"}
    return {"mode": "local", "provider": "agent-browser", "reason": "no cloud provider configured"}


# ---------------------------------------------------------------------------
# CDP connect — attach to running Chrome/Edge instance
# ---------------------------------------------------------------------------

# Common CDP debug port ranges
_CDP_DEBUG_PORTS = range(9221, 9230)
_DEFAULT_CDP_PORT = 9222


def find_cdp_endpoint(port: Optional[int] = None) -> Optional[str]:
    """
    Find a running Chrome/Edge instance with remote debugging enabled.
    Returns CDP WebSocket endpoint (e.g. ws://localhost:9222/...).
    """
    check_ports = [port] if port else [_DEFAULT_CDP_PORT] + list(_CDP_DEBUG_PORTS)
    for p in check_ports:
        try:
            result = subprocess.run(
                ["curl", "-s", f"http://localhost:{p}/json/version"],
                capture_output=True, text=True, timeout=3,
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                websocket_url = data.get("webSocketDebuggerUrl")
                if websocket_url:
                    return websocket_url
        except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
            continue
    return None


def launch_chrome_cdp(
    port: int = _DEFAULT_CDP_PORT,
    headless: bool = False,
    user_data_dir: Optional[str] = None,
) -> str:
    """
    Launch Chrome/Edge with remote debugging enabled.
    Returns CDP WebSocket endpoint.
    """
    # Find Chrome binary
    chrome_paths = [
        # Linux
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        # Windows
        os.path.expandvars(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        # macOS
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        # Edge
        "/usr/bin/microsoft-edge",
        os.path.expandvars(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    ]
    chrome_bin = None
    for p in chrome_paths:
        if os.path.exists(p):
            chrome_bin = p
            break

    if not chrome_bin:
        # Try `which`
        for name in ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge"]:
            found = shutil.which(name)
            if found:
                chrome_bin = found
                break

    if not chrome_bin:
        raise RuntimeError(
            "No Chrome/Edge binary found. Install Chrome or pass an explicit path."
        )

    args = [chrome_bin, f"--remote-debugging-port={port}", "--no-first-run", "--no-default-browser-check"]
    if headless:
        args.append("--headless=new")
    if user_data_dir:
        args.extend(["--user-data-dir", user_data_dir])
    else:
        tmpdir = f"/tmp/browser_next_cdp_{uuid.uuid4().hex[:8]}"
        os.makedirs(tmpdir, exist_ok=True)
        args.extend(["--user-data-dir", tmpdir])

    # Launch in background
    import subprocess
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Wait for CDP endpoint
    for _ in range(30):
        time.sleep(0.3)
        endpoint = find_cdp_endpoint(port)
        if endpoint:
            return endpoint
    raise RuntimeError(f"Chrome did not start with CDP on port {port} within 10s.")


# ---------------------------------------------------------------------------
# Content extraction — SPA, infinite scroll, lazy load, paywalls, ads
# ---------------------------------------------------------------------------

# Site-specific article selectors (from original Browser_Emulator, expanded)
_ARTICLE_SELECTORS = [
    "[itemprop='articleBody']",
    "div.mw-parser-output",           # Wikipedia
    "#mw-content-text",               # Wikipedia wrapper
    "article",
    ".post-content",
    ".entry-content",
    ".post-content-body",             # Medium
    "#post-content",                  # Medium
    ".ProseMirror",                   # Ghost / Substack
    "main",
    "#content",
    ".article-body",
    ".article__body",
    ".article-content",
    "#article-content",
    ".markdown-body",                 # GitHub
    ".show-container",                # Hacker News
]

# Ad/nav/footer selectors to strip
_STRIP_SELECTORS = [
    "#google_ads_frame",
    ".ad",
    ".advertisement",
    ".adsbygoogle",
    "#sidebar",
    ".sidebar",
    "nav",
    "header",
    "footer",
    ".footer",
    "#footer",
    ".nav",
    ".navbar",
    ".menu",
    "#menu",
    ".cookie-notice",
    ".cookie-banner",
    ".social-share",
    ".related-posts",
    ".comments",
    "#comments",
]

# Infinite scroll sentinel patterns
_INFINITE_SCROLL_PATTERNS = [
    "Load more",
    "Show more",
    "Continue reading",
    "View more",
    "More stories",
    "infinite-scroll",
    "lazy-load",
]


def strip_ads_and_nav(html: str) -> str:
    """
    Remove ads, navigation, and footer elements from HTML string.
    Uses regex-based removal (no DOM parser needed).
    """
    # Remove script, style, noscript, iframe tags
    html = re.sub(r'<(script|style|noscript|iframe)[^>]*>.*?</\1>', '', html, flags=re.DOTALL | re.IGNORECASE)

    # Remove common ad/nav/footer elements
    for selector in _STRIP_SELECTORS:
        tag = selector.lstrip('#.')
        attr = ''
        if selector.startswith('#'):
            attr = f' id="{selector[1:]}"'
        elif selector.startswith('.'):
            attr = f' class="{selector[1:]}"'
        # Simple tag removal (not perfect but good enough for text extraction)
        pattern = rf'<{tag}[^>]*{attr}[^>]*>.*?</{tag}>'
        html = re.sub(pattern, '', html, flags=re.DOTALL | re.IGNORECASE)

    # Remove remaining tags, keep text
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def clean_article_text(text: str, max_length: int = 50000) -> str:
    """Collapse whitespace, remove boilerplate, truncate."""
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    text = "\n".join(lines)
    # Strip Wikipedia TOC
    text = re.sub(
        r'\n\s*Table of Contents[^:]*[:\s]*\n(?:\s*\d+[.\s].*\n?)*',
        '\n', text, flags=re.IGNORECASE,
    )
    if len(text) > max_length:
        text = text[:max_length] + "\n... [truncated]"
    return text.strip()


def extract_article_js() -> str:
    """
    JavaScript to extract main article content from page.
    Tries site-specific selectors, falls back to body.
    Returns a JS string to be evaluated in the page.
    """
    return r"""
    (() => {
        // Article body selectors to try
        const selectors = [
            "[itemprop='articleBody']",
            "div.mw-parser-output",
            "#mw-content-text",
            "article",
            ".post-content",
            ".entry-content",
            ".post-content-body",
            "#post-content",
            ".ProseMirror",
            "main",
            "#content",
            ".article-body",
            ".article__body",
            ".article-content",
            "#article-content",
            ".markdown-body",
            ".show-container",
        ];

        let target = null;
        for (const sel of selectors) {
            const el = document.querySelector(sel);
            if (el && el.textContent && el.textContent.trim().length > 300) {
                target = el;
                break;
            }
        }
        if (!target) target = document.body;

        // Remove ad/nav/script elements from a clone
        const clone = target.cloneNode(true);
        clone.querySelectorAll('script, style, noscript, iframe, .ad, .adsbygoogle, .advertisement, .cookie-notice, .cookie-banner').forEach(e => e.remove());

        let text = clone.innerText || '';
        text = text.replace(/\n{3,}/g, '\n\n').trim();
        return text.substring(0, 50000);
    })()
    """


def extract_with_scroll_js(max_scrolls: int = 5) -> str:
    """
    JavaScript to handle infinite scroll / lazy loading.
    Scrolls down, waits for new content, repeats.
    Then extracts clean text.
    """
    return rf"""
    (() => {{
        let prevHeight = 0;
        let scrolls = 0;
        const maxScrolls = {max_scrolls};

        // Scroll and wait for new content to load
        while (scrolls < maxScrolls) {{
            window.scrollTo(0, document.body.scrollHeight);
            const newHeight = document.body.scrollHeight;
            if (newHeight === prevHeight && prevHeight > 0) {{
                break; // No more content loaded
            }}
            prevHeight = newHeight;
            scrolls++;
        }}

        // Scroll back to top
        window.scrollTo(0, 0);

        // Extract text using article selectors
        const selectors = [
            "[itemprop='articleBody']", "div.mw-parser-output", "#mw-content-text",
            "article", ".post-content", ".entry-content", ".post-content-body",
            "#post-content", ".ProseMirror", "main", "#content",
        ];
        let target = null;
        for (const sel of selectors) {{
            const el = document.querySelector(sel);
            if (el && el.textContent && el.textContent.trim().length > 300) {{
                target = el;
                break;
            }}
        }}
        if (!target) target = document.body;

        const clone = target.cloneNode(true);
        clone.querySelectorAll('script, style, noscript, iframe, .ad, .adsbygoogle, .advertisement, .cookie-notice').forEach(e => e.remove());

        let text = clone.innerText || '';
        text = text.replace(/\\n{{3,}}/g, '\\n\\n').trim();
        return {{ text: text.substring(0, 50000), scrolls: scrolls }};
    }})()
    """


# ---------------------------------------------------------------------------
# Session persistence — named profiles with cookie storage
# ---------------------------------------------------------------------------

PROFILE_DIR = Path.home() / ".browser_next" / "profiles"


def get_profile_path(profile_name: str) -> Path:
    """Get path to a named profile directory."""
    profile = PROFILE_DIR / profile_name
    profile.mkdir(parents=True, exist_ok=True)
    return profile


def save_cookies(profile_name: str, cookies: List[Dict[str, Any]]) -> None:
    """Save cookies from browser session to profile."""
    profile = get_profile_path(profile_name)
    cookie_file = profile / "cookies.json"
    cookie_file.write_text(json.dumps(cookies, indent=2))


def load_cookies(profile_name: str) -> List[Dict[str, Any]]:
    """Load cookies from profile."""
    cookie_file = get_profile_path(profile_name) / "cookies.json"
    if cookie_file.exists():
        return json.loads(cookie_file.read_text())
    return []


def save_storage(profile_name: str, storage: Dict[str, Dict[str, str]]) -> None:
    """Save localStorage to profile."""
    profile = get_profile_path(profile_name)
    storage_file = profile / "storage.json"
    storage_file.write_text(json.dumps(storage, indent=2))


def load_storage(profile_name: str) -> Dict[str, Dict[str, str]]:
    """Load localStorage from profile."""
    storage_file = get_profile_path(profile_name) / "storage.json"
    if storage_file.exists():
        return json.loads(storage_file.read_text())
    return {}


def list_profiles() -> List[str]:
    """List all available profiles."""
    if not PROFILE_DIR.exists():
        return []
    return sorted([d.name for d in PROFILE_DIR.iterdir() if d.is_dir()])


# ---------------------------------------------------------------------------
# Anti-detection — fingerprint randomization
# ---------------------------------------------------------------------------

# Realistic user agent pool (Chrome/Edge 131+ on various platforms)
_USER_AGENTS = [
    # Windows Chrome
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    # Windows Edge
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 Edg/132.0.0.0",
    # macOS Chrome
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    # Linux Chrome
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
]

# WebRTC leak prevention script
_WEBRTC_HIDE = """
Object.defineProperty(navigator, 'webkitGetUserMedia', {get: () => undefined});
Object.defineProperty(navigator, 'mediaDevices', {get: () => ({})});
"""

# window.chrome spoof — headless Chrome has an incomplete/missing chrome object
_WINDOW_CHROME = """
// Spoof window.chrome (headless Chrome detection)
if (!window.chrome) {
    window.chrome = {
        runtime: {},
        loadTimes: function() {},
        csi: function() {},
        app: {}
    };
}
// Additional properties Cloudflare checks
Object.defineProperty(window.chrome, 'runtime', {
    get: () => ({
        onMessage: { addListener: function() {}, removeListener: function() {} },
        connect: function() {}
    })
});
"""

# Touch points — headless Chrome reports 0, real Chrome reports 1
_TOUCH_PATCH = """
Object.defineProperty(navigator, 'maxTouchPoints', {
    get: () => 1
});
"""

# Network Information API — headless Chrome often lacks this
_CONNECTION_PATCH = """
if (!navigator.connection) {
    Object.defineProperty(navigator, 'connection', {
        get: () => ({
            effectiveType: '4g',
            rtt: 50,
            downlink: 5,
            saveData: false,
            addEventListener: function() {},
            removeEventListener: function() {}
        })
    });
}
"""

# WebGL renderer/vendor spoof — headless Chrome exposes "SwiftShader" or "Mozilla"
_WEBGL_PATCH = """
const getParameter = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(parameter) {
    if (parameter === 37445) {
        return 'Google Inc. (NVIDIA)';  // UNMASKED_VENDOR_WEBGL
    }
    if (parameter === 37446) {
        return 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3080/ntpo, DirectX 12)';  // UNMASKED_RENDERER_WEBGL
    }
    return getParameter.apply(this, arguments);
};
if (typeof WebGL2RenderingContext !== 'undefined') {
    const getParameter2 = WebGL2RenderingContext.prototype.getParameter;
    WebGL2RenderingContext.prototype.getParameter = function(parameter) {
        if (parameter === 37445) {
            return 'Google Inc. (NVIDIA)';
        }
        if (parameter === 37446) {
            return 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3080/ntpo, DirectX 12)';
        }
        return getParameter2.apply(this, arguments);
    };
}
"""

# Window dimensions — headless Chrome has outerWidth/outerHeight = 0
_OUTER_DIMS_PATCH = """
// Fix window.outerWidth/outerHeight (0 in headless, should match inner)
Object.defineProperty(window, 'outerWidth', {
    get: () => window.innerWidth + 16
});
Object.defineProperty(window, 'outerHeight', {
    get: () => window.innerHeight + 38
});
// window.screen also needs consistency
Object.defineProperty(window.screen, 'availWidth', {
    get: () => 1904
});
Object.defineProperty(window.screen, 'availHeight', {
    get: () => 1011
});
Object.defineProperty(window.screen, 'width', {
    get: () => 1920
});
Object.defineProperty(window.screen, 'height', {
    get: () => 1080
});
"""

# Permissions API — headless Chrome behaves differently
_PERMISSIONS_PATCH = """
// Override permissions.query to avoid detection
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => {
    return originalQuery.call(navigator, parameters).then(result => {
        if (parameters.name === 'notifications') {
            return { state: Notification.permission };
        }
        return result;
    });
};
"""

# Source URL obfuscation — removes tool names from JS error traces
_SOURCE_URL_PATCH = """
// Remove sourceURL references that leak tool identity
Object.defineProperty(Error, 'prepareStackTrace', {
    value: undefined,
    configurable: true,
    writable: true
});
"""

# Navigator property overrides
_NAV_PATCH = """
// Hide webdriver
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});

// Spoof plugins
Object.defineProperty(navigator, 'plugins', {
    get: () => [
        {0: 'Portable Document Format', description: 'Portable Document Format', filename: 'internal-pdf-viewer', length: 1, name: 'internal-pdf-viewer'},
        {0: 'Chrome PDF Viewer', description: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', length: 1, name: 'Chrome PDF Viewer'},
    ]
});

// Spoof languages
Object.defineProperty(navigator, 'languages', {
    get: () => ['en-US', 'en']
});

// Spoof hardware concurrency (realistic: 4-16)
Object.defineProperty(navigator, 'hardwareConcurrency', {
    get: () => { const v = [4, 8, 12, 16][Math.floor(Math.random() * 4)]; return v; }
});

// Spoof device memory
Object.defineProperty(navigator, 'deviceMemory', {
    get: () => { const v = [4, 8, 16][Math.floor(Math.random() * 3)]; return v; }
});
"""

# Canvas fingerprint noise
_CANVAS_NOISE = """
// Add noise to canvas fingerprinting
const originalToBlob = HTMLCanvasElement.prototype.toBlob;
HTMLCanvasElement.prototype.toBlob = function(callback, type, quality) {
    const ctx = this.getContext('2d');
    if (ctx) {
        // Subtle pixel noise
        const imageData = ctx.getImageData(0, 0, this.width, this.height);
        const data = imageData.data;
        for (let i = 0; i < data.length; i += 100) {
            data[i] = Math.max(0, Math.min(255, data[i] + (Math.random() * 2 - 1)));
        }
        ctx.putImageData(imageData, 0, 0);
    }
    return originalToBlob.call(this, callback, type, quality);
};
"""


def get_random_ua() -> str:
    """Get a random realistic user agent."""
    import random
    return random.choice(_USER_AGENTS)


def get_anti_detection_script() -> str:
    """Get complete anti-detection initialization script."""
    return (
        _NAV_PATCH
        + _WEBRTC_HIDE
        + _WINDOW_CHROME
        + _TOUCH_PATCH
        + _CONNECTION_PATCH
        + _WEBGL_PATCH
        + _OUTER_DIMS_PATCH
        + _PERMISSIONS_PATCH
        + _SOURCE_URL_PATCH
        + _CANVAS_NOISE
    )


def get_random_timezone() -> str:
    """Get a random realistic timezone."""
    import random
    timezones = [
        "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles",
        "America/Phoenix", "America/Anchorage", "Europe/London", "Europe/Berlin",
        "Europe/Paris", "Asia/Tokyo", "Asia/Shanghai", "Australia/Sydney",
    ]
    return random.choice(timezones)


def get_random_locale() -> str:
    """Get a random realistic locale."""
    import random
    locales = ["en-US", "en-GB", "en-CA", "en-AU", "fr-FR", "de-DE", "ja-JP", "zh-CN"]
    return random.choice(locales)


# ---------------------------------------------------------------------------
# Search integration (SearXNG + DDG) — from original Browser_Emulator
# ---------------------------------------------------------------------------

def search(query: str, max_results: int = 5, engine: str = "searxng") -> List[Dict[str, str]]:
    """
    Search using SearXNG (primary) or DDG (fallback).
    Returns list of {title, href, body}.
    """
    results = []

    # Try SearXNG first
    if engine in ("searxng", "auto"):
        try:
            result = subprocess.run(
                [sys.executable, "tools/Web_Search/search.py", query],
                capture_output=True, text=True, timeout=30,
                cwd=str(Path.home() / "Documents" / "ai_workloads"),
            )
            lines = result.stdout.split("\n")
            current = {"title": "", "href": "", "body": ""}
            for line in lines:
                stripped = line.strip()
                if not stripped or stripped.startswith("===") or stripped.startswith("Searching") or stripped.startswith("Source:") or stripped.startswith("Timestamp:") or stripped.startswith("Results Found"):
                    continue
                num_match = re.match(r'^(\d+)\.\s+(.+)$', stripped)
                if num_match:
                    if current.get("title"):
                        results.append(current)
                    current = {"title": num_match.group(2), "href": "", "body": ""}
                elif stripped.startswith("URL:"):
                    current["href"] = stripped[4:].strip()
                elif stripped.startswith("Engine:"):
                    continue
                elif current.get("title") and not current.get("body"):
                    current["body"] = stripped
                elif current.get("title") and current.get("body"):
                    current["body"] += " " + stripped
            if current.get("title"):
                results.append(current)
            if results:
                return results[:max_results]
        except Exception:
            pass

    # Fallback to DDG
    try:
        from ddgs import DDGS
        ddgs = DDGS()
        raw = ddgs.text(query, max_results=max_results)
        for item in raw:
            results.append({
                "title": item.get("title", ""),
                "href": item.get("href", ""),
                "body": item.get("body", ""),
            })
    except Exception:
        pass

    return results[:max_results]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    """Standalone CLI for testing individual features."""
    import argparse
    parser = argparse.ArgumentParser(description="Browser Next utilities")
    sub = parser.add_subparsers(dest="command")

    # is-private check
    p = sub.add_parser("is-private", help="Check if URL is private/LAN")
    p.add_argument("url")

    # route
    p = sub.add_parser("route", help="Route URL to local or cloud")
    p.add_argument("url")
    p.add_argument("--cloud-provider", default=None)

    # find-cdp
    p = sub.add_parser("find-cdp", help="Find running Chrome CDP endpoint")
    p.add_argument("--port", type=int, default=None)

    # random-ua
    sub.add_parser("random-ua", help="Get random user agent")

    # list-profiles
    sub.add_parser("list-profiles", help="List saved browser profiles")

    # search
    p = sub.add_parser("search", help="Search the web")
    p.add_argument("query")
    p.add_argument("--max-results", type=int, default=5)
    p.add_argument("--engine", default="searxng")

    args = parser.parse_args()

    if args.command == "is-private":
        print(f"{'Private' if is_private_url(args.url) else 'Public'}: {args.url}")
    elif args.command == "route":
        result = route_url(args.url, args.cloud_provider)
        print(json.dumps(result, indent=2))
    elif args.command == "find-cdp":
        endpoint = find_cdp_endpoint(args.port)
        print(f"CDP endpoint: {endpoint or 'Not found'}")
    elif args.command == "random-ua":
        print(get_random_ua())
    elif args.command == "list-profiles":
        profiles = list_profiles()
        print(f"Profiles: {profiles or '(none)'}")
    elif args.command == "search":
        results = search(args.query, args.max_results, args.engine)
        for i, r in enumerate(results, 1):
            print(f"{i}. {r['title']}")
            print(f"   {r['href']}")
            print()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
