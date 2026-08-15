#!/usr/bin/env python3
"""
Cloud Browser — remote browser automation via Browserbase (CDP).

Provides a CloudBrowser class that mirrors the BrowserSession interface,
allowing navigate_auto() to transparently route public URLs to a cloud
headless browser while keeping private/LAN URLs local.

Supported providers:
  - browserbase  (https://www.browserbase.com)

API key configuration:
  - Environment variable: BROWSERBASE_API_KEY
  - Or config file: ~/.config/browser_next/providers.json
    {"browserbase": {"api_key": "...", "project_id": "..."}}
"""

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Provider configuration
# ---------------------------------------------------------------------------

_PROVIDER_CONFIG_PATH = Path.home() / ".config" / "browser_next" / "providers.json"


def load_provider_config(provider: str) -> Dict[str, Any]:
    """Load provider credentials from config or environment."""
    config = {}

    if _PROVIDER_CONFIG_PATH.exists():
        with open(_PROVIDER_CONFIG_PATH) as f:
            data = json.load(f)
            config = data.get(provider, {})

    # Environment variables override config file
    if provider == "browserbase":
        api_key = config.get("api_key") or os.environ.get("BROWSERBASE_API_KEY")
        if api_key:
            config["api_key"] = api_key
        project_id = config.get("project_id") or os.environ.get("BROWSERBASE_PROJECT_ID")
        if project_id:
            config["project_id"] = project_id
        context_id = config.get("context_id") or os.environ.get("BROWSERBASE_CONTEXT_ID")
        if context_id:
            config["context_id"] = context_id

    return config


# ---------------------------------------------------------------------------
# Accessibility tree formatting
# ---------------------------------------------------------------------------

def _format_accessibility_node(
    node: Dict[str, Any],
    depth: int = 0,
    ref_counter: List[int] = None,
    interactive_only: bool = False,
) -> Tuple[str, Dict[str, str]]:
    """
    Format a playwright accessibility node as a text tree with @eN ref IDs.

    Returns (formatted_text, ref_map) where ref_map maps ref IDs to element
    descriptors for later click/fill operations.
    """
    if ref_counter is None:
        ref_counter = [0]

    lines = []
    indent = "  " * depth

    role = node.get("role", "unknown")
    name = node.get("name", "")
    value = node.get("value", "")
    description = node.get("description", "")
    children = node.get("children", [])

    # Determine if this node is interactive
    interactive_roles = {
        "button", "link", "checkbox", "radio", "textbox", "combobox",
        "listbox", "menuitem", "menuitemcheckbox", "menuitemradio",
        "option", "searchbox", "spinbutton", "switch", "togglebutton",
        "menu", "tab", "tablist", "treeitem", "gridcell",
    }
    is_interactive = role in interactive_roles

    if interactive_only and not is_interactive and not children:
        return "", {}

    ref_map = {}

    # Build node line
    parts = []
    if role != "generic" and role != "document":
        parts.append(role)
    if name:
        parts.append(f'"{name}"')
    if value and role in ("textbox", "searchbox", "combobox"):
        parts.append(f"[value={value[:30]}]")
    if description:
        parts.append(f"[{description[:50]}]")

    if is_interactive:
        ref_counter[0] += 1
        ref_id = f"@e{ref_counter[0]}"
        ref_map[ref_id] = {"role": role, "name": name}
        node_text = f"{indent}{ref_id} {' '.join(parts)}" if parts else f"{indent}{ref_id} {role}"
    else:
        node_text = f"{indent}{' '.join(parts)}" if parts else ""

    if node_text:
        lines.append(node_text)

    # Recurse into children
    for child in children:
        child_text, child_refs = _format_accessibility_node(
            child, depth + 1, ref_counter, interactive_only
        )
        if child_text:
            lines.append(child_text)
        ref_map.update(child_refs)

    return "\n".join(lines), ref_map


# ---------------------------------------------------------------------------
# Cloud Browser client
# ---------------------------------------------------------------------------

class CloudBrowser:
    """
    Cloud-based browser session using Browserbase + Playwright CDP.

    Mirrors the BrowserSession interface for transparent routing in
    navigate_auto().
    """

    def __init__(
        self,
        provider: str = "browserbase",
        project_id: Optional[str] = None,
        context_id: Optional[str] = None,
        timeout: int = 30000,
        viewport: Tuple[int, int] = (1280, 720),
        user_agent: Optional[str] = None,
        ignore_https_errors: bool = False,
    ):
        self.provider = provider
        self.timeout = timeout
        self.viewport = viewport
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        )
        self.ignore_https_errors = ignore_https_errors

        # Load provider config
        self._config = load_provider_config(provider)
        if project_id:
            self._config["project_id"] = project_id
        if context_id:
            self._config["context_id"] = context_id

        # Runtime state
        self._session_id: Optional[str] = None
        self._connect_url: Optional[str] = None
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._started = False
        self._closed = False
        self._url: Optional[str] = None
        self._ref_map: Dict[str, Dict[str, str]] = {}

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> "CloudBrowser":
        """Start the cloud browser session (idempotent)."""
        if self._started:
            return self

        if self.provider == "browserbase":
            self._start_browserbase()
        else:
            raise ValueError(f"Unsupported cloud provider: {self.provider}")

        self._started = True
        self._closed = False
        return self

    def _start_browserbase(self) -> None:
        """Create a Browserbase session and connect via CDP."""
        api_key = self._config.get("api_key")
        if not api_key:
            raise RuntimeError(
                "BROWSERBASE_API_KEY not set. "
                "Set environment variable or add to "
                f"{_PROVIDER_CONFIG_PATH}"
            )

        # Create session via Browserbase SDK
        from browserbase import Browserbase

        browser_settings = {
            "viewportWidth": self.viewport[0],
            "viewportHeight": self.viewport[1],
            "disableScreenshots": True,
        }

        create_kwargs: Dict[str, Any] = {"browser_settings": browser_settings}
        if self._config.get("project_id"):
            create_kwargs["project_id"] = self._config["project_id"]
        if self._config.get("context_id"):
            create_kwargs["context_id"] = self._config["context_id"]
        create_kwargs["keep_alive"] = True

        client = Browserbase(api_key=api_key)
        session = client.sessions.create(**create_kwargs)
        self._session_id = session.id
        self._connect_url = session.connect_url

        # Connect via Playwright CDP
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.connect_over_cdp(
            self._connect_url
        )

        # Get the default context and page created by Browserbase
        contexts = self._browser.contexts
        if contexts:
            self._context = contexts[0]
        else:
            self._context = self._browser.new_context(
                viewport={"width": self.viewport[0], "height": self.viewport[1]},
                user_agent=self.user_agent,
                ignore_https_errors=self.ignore_https_errors,
            )

        pages = self._context.pages
        if pages:
            self._page = pages[0]
        else:
            self._page = self._context.new_page()

        self._page.set_default_timeout(self.timeout)

    def close(self) -> None:
        """Close the cloud browser session."""
        if not self._started or self._closed:
            return

        try:
            if self._page:
                self._page.close()
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass

        # Release Browserbase session
        if self._session_id and self.provider == "browserbase":
            try:
                api_key = self._config.get("api_key")
                if api_key:
                    from browserbase import Browserbase
                    client = Browserbase(api_key=api_key)
                    client.sessions.update(
                        self._session_id, status="REQUEST_RELEASE"
                    )
            except Exception:
                pass

        self._closed = True
        self._started = False

    def __enter__(self):
        return self.start()

    def __exit__(self, *args):
        self.close()

    # -- ensure page is ready ------------------------------------------------

    def _ensure_page(self) -> None:
        """Ensure the browser session is started and page is available."""
        if not self._started:
            self.start()
        if not self._page:
            self._page = self._context.new_page()
            self._page.set_default_timeout(self.timeout)

    # -- navigation ----------------------------------------------------------

    def navigate(self, url: str, wait_network_idle: bool = False,
                 wait_ms: int = 0) -> str:
        """Navigate to URL and return accessibility tree snapshot."""
        self._ensure_page()

        wait_until = "networkidle" if wait_network_idle else "domcontentloaded"
        self._page.goto(url, wait_until=wait_until, timeout=self.timeout)
        if wait_ms > 0:
            time.sleep(wait_ms / 1000)

        self._url = self._page.url
        return self.snapshot(interactive=True)

    def navigate_auto(self, url: str, **kwargs) -> str:
        """Cloud browser handles all navigation (public URLs by design)."""
        return self.navigate(url, **kwargs)

    # -- accessibility snapshot -----------------------------------------------

    def snapshot(self, interactive: bool = False, full: bool = False,
                 depth: Optional[int] = None, selector: Optional[str] = None) -> str:
        """Get accessibility tree snapshot formatted as text."""
        self._ensure_page()

        try:
            tree = self._page.accessibility.snapshot(
                interesting_only=not full
            )
        except Exception:
            # Fallback: return page title and text
            return f"Page: {self.get_title()}\nURL: {self.get_url()}"

        if not tree:
            return f"Page: {self.get_title()}"

        self._ref_map = {}
        text, self._ref_map = _format_accessibility_node(
            tree, interactive_only=interactive and not full
        )
        return text if text else f"Page: {self.get_title()}"

    # -- interaction ---------------------------------------------------------

    def click(self, ref: str, new_tab: bool = False, wait_ms: int = 0) -> str:
        """Click element by ref ID or CSS selector."""
        self._ensure_page()

        if ref.startswith("@e") and ref in self._ref_map:
            info = self._ref_map[ref]
            role = info.get("role", "")
            name = info.get("name", "")
            if role == "link" and name:
                self._page.get_by_role("link", name=name).click()
            elif role == "button" and name:
                self._page.get_by_role("button", name=name).click()
            elif name:
                self._page.get_by_role(role, name=name).click()
            else:
                self._page.locator(f"[[role={role}]]").first.click()
        else:
            self._page.locator(ref).click()

        if wait_ms > 0:
            time.sleep(wait_ms / 1000)
        return self.snapshot(interactive=True)

    def fill(self, ref: str, text: str) -> None:
        """Clear and fill input field."""
        self._ensure_page()

        if ref.startswith("@e") and ref in self._ref_map:
            info = self._ref_map[ref]
            name = info.get("name", "")
            if name:
                self._page.get_by_label(name).fill(text)
            else:
                self._page.locator("input").first.fill(text)
        else:
            self._page.locator(ref).fill(text)

    def press(self, key: str) -> str:
        """Press keyboard key."""
        self._ensure_page()
        self._page.keyboard.press(key)
        return self.snapshot(interactive=True)

    def evaluate(self, expression: str) -> Any:
        """Evaluate JavaScript expression on the page."""
        self._ensure_page()
        return self._page.evaluate(expression)

    def evaluate_stdin(self, script: str) -> Any:
        """Evaluate a multi-line JavaScript script."""
        self._ensure_page()
        wrapped = f"(() => {{{script}}})()"
        return self._page.evaluate(wrapped)

    # -- page state ----------------------------------------------------------

    def get_url(self) -> str:
        """Get current page URL."""
        self._ensure_page()
        self._url = self._page.url
        return self._url

    def get_title(self) -> str:
        """Get current page title."""
        self._ensure_page()
        return self._page.title()

    def get_domain(self) -> str:
        """Get current page domain."""
        url = self.get_url()
        match = re.match(r"https?://([^/]+)", url)
        return match.group(1) if match else url

    def get_all_text(self, max_chars: int = 15000) -> str:
        """Extract all visible text from the page."""
        self._ensure_page()
        text = self._page.evaluate("""
            (() => {
                const walker = document.createTreeWalker(
                    document.body, NodeFilter.SHOW_TEXT, null, false
                );
                const texts = [];
                let node;
                while (node = walker.nextNode()) {
                    const t = node.textContent.trim();
                    if (t) texts.push(t);
                }
                return texts.join('\\n').replace(/\\n{3,}/g, '\\n\\n');
            })()
        """)
        return text[:max_chars] if len(text) > max_chars else text

    def screenshot(self, path: str = "/tmp/browser_next_cloud.png",
                   full_page: bool = False) -> str:
        """Take a screenshot and save to path."""
        self._ensure_page()
        self._page.screenshot(path=path, full_page=full_page)
        return path

    def get_hash(self) -> str:
        """Get the URL hash fragment."""
        self._ensure_page()
        return self._page.evaluate("window.location.hash || ''")

    # -- content extraction (mirrors BrowserSession v2) ----------------------

    def extract_article(self, max_chars: int = 50000) -> Dict[str, Any]:
        """Extract main article content from page."""
        self._ensure_page()

        result = self._page.evaluate("""
            (() => {
                const selectors = [
                    'article', 'main', '[role=main]',
                    '.post-content', '.article-body', '.entry-content',
                    '.prose', '[class*=article]', '[class*=post]',
                ];
                let el = null;
                for (const sel of selectors) {
                    el = document.querySelector(sel);
                    if (el && el.textContent.length > 100) break;
                }
                if (!el) el = document.body;
                const text = el.innerText || el.textContent;
                return text.trim().replace(/\\n{3,}/g, '\\n\\n');
            })()
        """)
        text = result[:max_chars] if len(result) > max_chars else result
        return {
            "title": self.get_title(),
            "url": self.get_url(),
            "text": text,
            "length": len(text),
        }

    def read_page(self, max_chars: int = 15000, scroll: bool = False) -> Dict[str, Any]:
        """Read page content, stripping ads/nav, handling scroll."""
        if scroll:
            try:
                self._page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(0.5)
            except Exception:
                pass

        text = self.get_all_text(max_chars)
        return {
            "title": self.get_title(),
            "url": self.get_url(),
            "text": text,
            "length": len(text),
        }
