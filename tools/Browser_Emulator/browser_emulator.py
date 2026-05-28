#!/usr/bin/env python3
"""
Playwright Browser — Real headless browser for web research.
Hybrid architecture: ddgs for search + Playwright Chromium for rendering and extraction.
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse, quote_plus

from ddgs import DDGS
from playwright.sync_api import sync_playwright, Page, Browser, BrowserContext


def clean_text(text: str, max_length: int = 50000) -> str:
    """Collapse whitespace, remove boilerplate, truncate."""
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    text = "\n".join(lines)
    if len(text) > max_length:
        text = text[:max_length] + "\n... [truncated]"
    return text.strip()


class PlaywrightBrowser:
    """Headless Chromium browser with research utilities."""

    CHROME_UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )

    def __init__(
        self,
        headless: bool = True,
        viewport_width: int = 1920,
        viewport_height: int = 1080,
    ):
        self._pw = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self.headless = headless
        self.viewport = (viewport_width, viewport_height)
        self.history: list[str] = []
        self.started = False
        self._ddgs = DDGS()

    # -- lifecycle ---------------------------------------------------------

    def launch(self) -> "PlaywrightBrowser":
        """Start browser (idempotent)."""
        if self.started:
            return self
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=self.headless,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        self._context = self._browser.new_context(
            user_agent=self.CHROME_UA,
            viewport={"width": self.viewport[0], "height": self.viewport[1]},
            locale="en-US",
            timezone_id="America/Denver",
        )
        # Hide webdriver fingerprint
        self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        self._page = self._context.new_page()
        self.started = True
        return self

    @property
    def page(self) -> Page:
        if not self.started:
            self.launch()
        return self._page

    def close(self):
        """Shut down browser."""
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()
        self.started = False

    def __enter__(self):
        return self.launch()

    def __exit__(self, *args):
        self.close()

    # -- core actions (chainable) ------------------------------------------

    def goto(
        self, url: str, wait_until: str = "domcontentloaded", timeout: int = 30000
    ) -> "PlaywrightBrowser":
        self.page.goto(url, wait_until=wait_until, timeout=timeout)
        self.history.append(url)
        return self

    def wait_network_idle(self, timeout: int = 20000):
        """Wait until there are no network connections for 500ms."""
        self.page.wait_for_load_state("networkidle", timeout=timeout)
        return self

    def wait_for_selector(self, selector: str, timeout: int = 15000):
        return self

    def sleep(self, secs: float):
        time.sleep(secs)
        return self

    # -- extraction -------------------------------------------------------

    def title(self) -> str:
        return self.page.title()

    def current_url(self) -> str:
        return self.page.url

    def text(self, selector: str = "body") -> str:
        """Return visible text under *selector*."""
        loc = self.page.locator(selector)
        count = loc.count()
        if count > 0:
            raw = loc.nth(0).inner_text(timeout=5000)
            return clean_text(raw)
        return ""

    def html(self, selector: str = "body") -> str:
        loc = self.page.locator(selector)
        count = loc.count()
        if count > 0:
            return loc.nth(0).inner_html(timeout=5000)
        return ""

    def evaluate(self, expr: str):
        """Run JS in the page context."""
        return self.page.evaluate(expr)

    # -- smart article extraction -----------------------------------------

    def scrape_article(
        self, max_length: int = 40000
    ) -> dict:
        """
        Attempt to extract the main article/content from the page.

        Returns dict with keys: title, url, text, length
        """
        # Try most specific selectors first; fall through to body
        for sel in [
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
        ]:
            loc = self.page.locator(sel)
            if loc.count() > 0:
                raw = loc.nth(0).inner_text(timeout=5000)
                if len(raw) > 300:
                    body_text = raw
                    break
        else:
            body_text = self.text("body")

        # Strip Wikipedia table of contents block
        body_text = re.sub(
            r'\n\s*Table of Contents[^:]*[:\s]*\n(?:\s*\d+[.\s].*\n?)*',
            '\n', body_text, flags=re.IGNORECASE,
        )

        cleaned = clean_text(body_text, max_length)
        return {
            "title": self.title(),
            "url": self.current_url(),
            "text": cleaned,
            "length": len(cleaned),
        }

    # -- search -----------------------------------------------------------

    def search(
        self, query: str, max_results: int = 10
    ) -> list[dict[str, str]]:
        """
        DuckDuckGo text search (via ddgs library).

        Returns list of dicts: {title, href, body}
        """
        raw = self._ddgs.text(query, max_results=max_results)
        results: list[dict[str, str]] = []
        for item in raw:
            results.append(
                {
                    "title": item.get("title", ""),
                    "href": item.get("href", ""),
                    "body": item.get("body", ""),
                }
            )
        return results

    def search_and_read(
        self, query: str, max_read: int = 3, max_chars: int = 15000
    ) -> list[dict]:
        """
        Search DuckDuckGo, then visit and scrape the top *max_read* results.

        Returns list of dicts with search result + scraped article or error.
        """
        results = self.search(query)
        readings: list[dict] = []
        for rank, res in enumerate(results[:max_read], start=1):
            url = res.get("href", "")
            if not url:
                readings.append(
                    {
                        "rank": rank,
                        "search_result": res,
                        "success": False,
                        "error": "no URL",
                    }
                )
                continue
            try:
                self.goto(
                    url, wait_until="domcontentloaded", timeout=25000
                ).wait_network_idle(timeout=15000)
                article = self.scrape_article(max_length=max_chars)
                readings.append(
                    {
                        "rank": rank,
                        "search_result": res,
                        "article": article,
                        "success": True,
                    }
                )
            except Exception as e:
                readings.append(
                    {
                        "rank": rank,
                        "search_result": res,
                        "success": False,
                        "error": str(e),
                    }
                )
        return readings

    # -- interactions ----------------------------------------------------

    def click(self, selector: str, timeout: int = 10000):
        self.page.locator(selector).first.click(timeout=timeout)
        return self

    def fill(self, selector: str, value: str):
        self.page.locator(selector).first.fill(value)
        return self

    def extract_links(self, selector: str = "body", max_links: int = 50) -> list[dict]:
        """Return [{text, href}, ...] from page."""
        return self.evaluate(
            f"""() => {{
                const links = Array.from(document.querySelectorAll('{selector} a[href]'));
                return links.slice(0, {max_links}).map(a => ({{
                    text: a.innerText.trim().substring(0, 200),
                    href: a.href,
                }}));
            }}"""
        )

    def screenshot(self, path: str, full_page: bool = False) -> str:
        self.page.screenshot(path=path, full_page=full_page)
        return path


# ---------------------------------------------------------------------------
# Convenience wrapper for research workflows
# ---------------------------------------------------------------------------

def search(query: str, max_results: int = 10) -> list[dict[str, str]]:
    """One-liner: search DuckDuckGo without launching a browser."""
    ddgs = DDGS()
    raw = ddgs.text(query, max_results=max_results)
    return [
        {"title": r.get("title", ""), "href": r.get("href", ""), "body": r.get("body", "")}
        for r in raw
    ]


def search_and_read(
    query: str, max_read: int = 3, max_chars: int = 15000
) -> list[dict]:
    """Search, then visit and scrape the top results."""
    with PlaywrightBrowser() as b:
        return b.search_and_read(query, max_read=max_read, max_chars=max_chars)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python3 browser_emulator.py <query>")
        print("       python3 browser_emulator.py --url <url>")
        sys.exit(0)

    if sys.argv[1] == "--url" and len(sys.argv) >= 3:
        target_url = sys.argv[2]
        with PlaywrightBrowser() as b:
            b.goto(target_url).wait_network_idle()
            article = b.scrape_article()
            print(f"Title: {article['title']}")
            print(f"URL:   {article['url']}")
            print(f"Len:   {article['length']} chars")
            print()
            print(article["text"])
    else:
        query = " ".join(sys.argv[1:])
        print(f"Searching for: {query}\n")
        results = search(query, max_results=5)
        for i, r in enumerate(results, 1):
            print(f"{i}. {r['title']}")
            print(f"   {r['href']}")
            body_snip = r["body"][:180]
            if body_snip:
                print(f"   {body_snip}")
            print()

        # Optionally read top result
        if results:
            top = results[0].get("href", "")
            if top:
                print("--- Reading top result ---\n")
                with PlaywrightBrowser() as b:
                    b.goto(top, timeout=30000).wait_network_idle()
                    article = b.scrape_article(max_length=8000)
                    print(article.get("text", "(nothing extracted)")[:4000])
