#!/usr/bin/env python3
"""
Browser Next — LLM-native browser automation for opencode.
Built on agent-browser (Rust CLI by Vercel Labs, Apache-2.0).

Architecture:
  - Python: session lifecycle, navigation flows, retry logic, coordination
  - JavaScript (via eval): DOM queries, text extraction, table parsing,
    link filtering, scroll operations, computed state
  - agent-browser CLI: the Rust binary driving headless Chrome
"""

import base64
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Import utility functions for content extraction
sys.path.insert(0, str(Path(__file__).parent))
from browser_next_utils import clean_article_text, save_cookies, load_cookies, save_storage


# ---------------------------------------------------------------------------
# Path to agent-browser binary
# ---------------------------------------------------------------------------

def _find_agent_browser() -> str:
    cli = shutil.which("agent-browser")
    if cli:
        return cli
    npm_global = Path.home() / ".npm-global" / "bin" / "agent-browser"
    if npm_global.exists():
        return str(npm_global)
    raise RuntimeError(
        "agent-browser CLI not found. Install with: npm install -g agent-browser"
    )


AGENT_BROWSER = _find_agent_browser()


# ---------------------------------------------------------------------------
# JavaScript snippets — run inside the browser for DOM operations
# ---------------------------------------------------------------------------

JS_GET_TABLE_DATA = r"""
(() => {
  const tables = document.querySelectorAll('table');
  const result = [];
  for (const table of tables) {
    const rows = [];
    const allRows = table.querySelectorAll('tr');
    for (const tr of allRows) {
      const cells = [];
      const allCells = tr.querySelectorAll('th, td');
      for (const cell of allCells) {
        // Get direct text nodes only (skip nested element text)
        let text = '';
        for (const node of cell.childNodes) {
          if (node.nodeType === Node.TEXT_NODE) {
            text += node.textContent;
          } else if (node.nodeType === Node.ELEMENT_NODE && node.tagName === 'SPAN') {
            // Include first-level span text
            text += (node.textContent || '');
          }
        }
        text = text.replace(/\s+/g, ' ').trim();
        cells.push(text);
      }
      if (cells.some(c => c)) {
        rows.push(cells);
      }
    }
    if (rows.length) {
      result.push(rows);
    }
  }
  return result;
})()
"""

JS_GET_ALL_TEXT = r"""
(() => {
  const maxLen = ${MAX_LEN};
  let text = (document.body && document.body.innerText) || '';
  text = text.replace(/\n{3,}/g, '\n\n').trim();
  return text.substring(0, maxLen);
})()
"""

JS_GET_INTERNAL_LINKS = r"""
(() => {
  const currentHost = window.location.hostname;
  const links = document.querySelectorAll('a');
  const result = [];
  for (const a of links) {
    const href = a.getAttribute('href') || '';
    let isInternal = false;
    try {
      if (!href || href === '#' || href.startsWith('#') || href.startsWith('/')) {
        isInternal = true;
      } else if (href.startsWith('javascript:')) {
        isInternal = true;
      } else {
        const url = new URL(href, window.location.href);
        if (url.hostname === currentHost) {
          isInternal = true;
        }
      }
    } catch (e) {
      isInternal = true;
    }
    if (isInternal) {
      const text = (a.textContent || '').replace(/\s+/g, ' ').trim();
      if (text || href) {
        result.push({ href, text, tagName: a.tagName, id: a.id || '' });
      }
    }
  }
  return result;
})()
"""

JS_GET_ALL_LINKS = r"""
(() => {
  const links = document.querySelectorAll('a');
  const result = [];
  for (const a of links) {
    const href = a.getAttribute('href') || '';
    const text = (a.textContent || '').replace(/\s+/g, ' ').trim();
    result.push({ href, text, tagName: a.tagName, id: a.id || '' });
  }
  return result;
})()
"""

JS_SCROLL_TO_BOTTOM = "window.scrollTo(0, document.body.scrollHeight);"

JS_SCROLL_TO_TOP = "window.scrollTo(0, 0);"

JS_SCROLL_STEP = "window.scrollBy(0, ${PIXELS});"

JS_FIND_ELEMENT_BY_TEXT = r"""
(() => {
  const searchText = ${SEARCH_TEXT_JSON};
  const roleFilter = ${ROLE_JSON};
  const allEls = document.querySelectorAll('*');
  for (const el of allEls) {
    if (!el.textContent || !el.textContent.includes(searchText)) continue;
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) continue;
    const tag = el.tagName.toLowerCase();
    if (roleFilter && tag !== roleFilter && el.role !== roleFilter) continue;
    const text = (el.textContent || '').replace(/\s+/g, ' ').trim();
    const selector = el.id ? '#' + el.id : tag + (el.className ? '.' + el.className.trim().split(/\s+/).join('.') : '');
    return { tag, text, selector, x: rect.x, y: rect.y, w: rect.width, h: rect.height };
  }
  return null;
})()
"""

JS_FIND_ELEMENTS_BY_TEXT = r"""
(() => {
  const searchText = ${SEARCH_TEXT_JSON};
  const allEls = document.querySelectorAll('*');
  const results = [];
  for (const el of allEls) {
    if (!el.textContent || !el.textContent.includes(searchText)) continue;
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) continue;
    const tag = el.tagName.toLowerCase();
    const text = (el.textContent || '').replace(/\s+/g, ' ').trim();
    results.push({ tag, text, id: el.id || '', href: el.href || '' });
    if (results.length >= 20) break;
  }
  return results;
})()
"""

JS_GET_SIDEBAR_LINKS = r"""
(() => {
  const navTexts = new Set(['search', 'log out', 'update', 'back to top', 'tp-link id', 'support', 'save', 'ok', 'no', 'cancel', 'refresh', 'add', 'apply', 'close', 'submit', 'delete', 'edit', 'remove', 'new', 'retry', 'release', 'renew', 'login', 'sign in']);
  const navSelectors = ['#sidebar', '.sidebar', 'nav.sidebar', 'aside', 'nav[role="navigation"]'];
  for (const sel of navSelectors) {
    const el = document.querySelector(sel);
    if (el) {
      const links = el.querySelectorAll('a');
      if (links.length >= 3) {
        return Array.from(links).map(a => ({
          text: (a.textContent || '').replace(/\s+/g, ' ').trim(),
          href: a.getAttribute('href') || ''
        })).filter(l => !navTexts.has(l.text.toLowerCase()));
      }
    }
  }
  // Fallback: find list-based nav (ul/li with links) — common sidebar pattern
  const navLists = document.querySelectorAll('ul > li > a, ul li a');
  if (navLists.length >= 3) {
    const container = navLists[0].closest('ul');
    if (container) {
      const links = container.querySelectorAll('a');
      return Array.from(links).map(a => ({
        text: (a.textContent || '').replace(/\s+/g, ' ').trim(),
        href: a.getAttribute('href') || ''
      })).filter(l => l.text && !navTexts.has(l.text.toLowerCase()));
    }
  }
  return [];
})()
"""

JS_GET_FORM_FIELDS = r"""
(() => {
  const result = { inputs: [], selects: [], textareas: [] };
  for (const input of document.querySelectorAll('input, textarea, select')) {
    const label = (input.labels && input.labels[0])
      ? input.labels[0].textContent.trim()
      : (input.placeholder || input.name || input.id || '');
    const entry = {
      type: input.type || input.tagName.toLowerCase(),
      name: input.name || '',
      id: input.id || '',
      label,
      value: input.value || '',
      tagName: input.tagName.toLowerCase()
    };
    if (input.tagName === 'SELECT') {
      entry.options = Array.from(input.options).map(o => o.value);
      result.selects.push(entry);
    } else if (input.tagName === 'TEXTAREA') {
      result.textareas.push(entry);
    } else {
      result.inputs.push(entry);
    }
  }
  return result;
})()
"""


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

class BrowserSession:
    """
    Manages a named agent-browser session.

    Python handles: session lifecycle, navigation flows, retry logic, coordination.
    JavaScript (via evaluate/evaluate_stdin): DOM queries, text extraction,
    table parsing, link filtering, scroll operations.

    New features (v2):
    - Hybrid routing: auto-detects LAN vs public URLs
    - CDP connect: attach to running Chrome/Edge
    - Content extraction: SPA, infinite scroll, lazy load, paywalls, ads
    - Session persistence: named profiles with cookie/storage survival
    - Anti-detection: UA rotation, fingerprint randomization, webdriver hiding
    - Search: SearXNG + DDG integration for search-and-read workflows
    """

    def __init__(
        self,
        session: Optional[str] = None,
        headless: bool = True,
        viewport_width: int = 1920,
        viewport_height: int = 1080,
        user_agent: Optional[str] = None,
        ignore_https_errors: bool = False,
        timeout: int = 25000,
        # New: Anti-detection
        anti_detection: bool = False,
        # New: Session persistence
        profile: Optional[str] = None,
        # New: CDP connect to running browser
        cdp_endpoint: Optional[str] = None,
        # New: Hybrid routing
        cloud_provider: Optional[str] = None,
        auto_private_routing: bool = True,
    ):
        self.session = session or f"opencode-{secrets.token_hex(6)}"
        self.headless = headless
        self.viewport = (viewport_width, viewport_height)
        self.user_agent = user_agent
        self.ignore_https_errors = ignore_https_errors
        self.timeout = timeout
        self._started = False
        self._closed = False
        self._url: Optional[str] = None
        # History tracking
        self._history: List[Dict[str, Any]] = []
        self._tracked_urls: set = set()

        # New: Anti-detection settings
        self.anti_detection = anti_detection
        if anti_detection and not user_agent:
            from browser_next_utils import get_random_ua
            self.user_agent = get_random_ua()
        elif not user_agent:
            self.user_agent = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            )

        # New: Session persistence
        self.profile = profile

        # New: CDP connect
        self.cdp_endpoint = cdp_endpoint

        # New: Hybrid routing
        self.cloud_provider = cloud_provider
        self.auto_private_routing = auto_private_routing

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> "BrowserSession":
        """Start the browser session (idempotent)."""
        if self._started:
            return self
        try:
            self._run(["open", "about:blank"])
            self._started = True
            self._closed = False
            self.set_viewport(*self.viewport)
        except Exception:
            self._started = False
            raise
        return self

    def close(self) -> None:
        """Close the browser session."""
        if self._started and not self._closed:
            try:
                self._run(["close", "--all"])
            except subprocess.CalledProcessError:
                pass
            self._closed = True
            self._started = False

    def __enter__(self):
        return self.start()

    def __exit__(self, *args):
        self.close()

    # -- core commands -------------------------------------------------------

    def navigate(self, url: str, wait_network_idle: bool = False,
                 wait_ms: int = 0, apply_anti_detection: bool = None) -> str:
        """Navigate to URL. Returns interactive snapshot on success.

        Args:
            url: URL to navigate to.
            wait_network_idle: Wait for network idle after page load.
            wait_ms: Additional wait time in milliseconds.
            apply_anti_detection: Override anti_detection flag for this call.
                True to force, False to skip, None to use instance setting.
        """
        self.start()
        cmd = ["open", url]
        result = self._run(cmd)
        self._url = url

        if wait_network_idle:
            self._run(["wait", "--load", "networkidle"])
            time.sleep(0.5)
        if wait_ms > 0:
            time.sleep(wait_ms / 1000)

        self._track_url(self.get_url())

        # Auto-apply anti-detection after page load
        should_apply = self.anti_detection if apply_anti_detection is None else apply_anti_detection
        if should_apply:
            try:
                self.apply_anti_detection()
            except Exception:
                pass  # Don't fail navigation if anti-detection fails

        return self.snapshot(interactive=True)

    def snapshot(self, interactive: bool = False, full: bool = False,
                 depth: Optional[int] = None, selector: Optional[str] = None) -> str:
        """
        Get accessibility tree snapshot (for LLM consumption).

        Args:
            interactive: Only show interactive elements (recommended for LLM use).
            full: Full accessibility tree (not just interactive).
            depth: Limit tree depth.
            selector: Scope to CSS selector.
        """
        self.start()
        cmd = ["snapshot"]
        if interactive and not full:
            cmd.append("-i")
        if depth is not None:
            cmd.extend(["-d", str(depth)])
        if selector:
            cmd.extend(["-s", selector])
        return self._run(cmd).strip()

    def click(self, ref: str, new_tab: bool = False, wait_ms: int = 0) -> str:
        """Click element by ref ID (e.g. '@e1'). Returns new snapshot."""
        cmd = ["click", ref]
        if new_tab:
            cmd.append("--new-tab")
        self._run(cmd)
        if wait_ms > 0:
            time.sleep(wait_ms / 1000)
        self._track_url(self.get_url())
        return self.snapshot(interactive=True)

    def dblclick(self, ref: str) -> str:
        """Double-click element. Returns new snapshot."""
        self._run(["dblclick", ref])
        return self.snapshot(interactive=True)

    def fill(self, ref: str, text: str) -> None:
        """Clear and fill input field."""
        self._run(["fill", ref, text])

    def type_text(self, ref: str, text: str) -> None:
        """Type text without clearing field first."""
        self._run(["type", ref, text])

    def press(self, key: str) -> str:
        """Press keyboard key (Enter, Tab, Escape, ArrowDown, etc.)."""
        self._run(["press", key])
        return self.snapshot(interactive=True)

    def hover(self, ref: str) -> str:
        """Hover over element."""
        self._run(["hover", ref])
        return self.snapshot(interactive=True)

    def focus(self, ref: str) -> None:
        """Focus element."""
        self._run(["focus", ref])

    def check(self, ref: str) -> None:
        """Check checkbox."""
        self._run(["check", ref])

    def uncheck(self, ref: str) -> None:
        """Uncheck checkbox."""
        self._run(["uncheck", ref])

    def select_option(self, ref: str, *values: str) -> None:
        """Select dropdown option(s)."""
        self._run(["select", ref] + list(values))

    def scroll(self, direction: str = "down", pixels: int = 300) -> str:
        """Scroll page. Returns new snapshot."""
        self._run(["scroll", direction, str(pixels)])
        return self.snapshot(interactive=True)

    def scroll_into_view(self, ref: str) -> str:
        """Scroll element into view."""
        self._run(["scrollintoview", ref])
        return self.snapshot(interactive=True)

    def back(self) -> str:
        """Navigate back. Returns new snapshot."""
        self._run(["back"])
        return self.snapshot(interactive=True)

    def forward(self) -> str:
        """Navigate forward."""
        self._run(["forward"])
        return self.snapshot(interactive=True)

    def reload(self) -> str:
        """Reload page."""
        self._run(["reload"])
        time.sleep(1)
        return self.snapshot(interactive=True)

    def wait(self, milliseconds: Optional[int] = None, ref: Optional[str] = None,
             text: Optional[str] = None, url_pattern: Optional[str] = None) -> None:
        """Wait for condition."""
        if milliseconds:
            self._run(["wait", str(milliseconds)])
        elif ref:
            self._run(["wait", ref])
        elif text:
            self._run(["wait", "--text", text])
        elif url_pattern:
            self._run(["wait", "--url", url_pattern])

    # -- element info (CLI wrappers) -----------------------------------------

    def get_text(self, ref: str) -> str:
        """Get text content of element."""
        return self._run(["get", "text", ref]).strip()

    def get_html(self, ref: str) -> str:
        """Get innerHTML of element."""
        return self._run(["get", "html", ref]).strip()

    def get_value(self, ref: str) -> str:
        """Get input value."""
        return self._run(["get", "value", ref]).strip()

    def get_attr(self, ref: str, attr: str) -> str:
        """Get element attribute."""
        return self._run(["get", "attr", ref, attr]).strip()

    def get_title(self) -> str:
        """Get page title."""
        return self._run(["get", "title"]).strip()

    def get_url(self) -> str:
        """Get current URL."""
        return self._run(["get", "url"]).strip()

    def is_visible(self, ref: str) -> bool:
        """Check if element is visible."""
        try:
            self._run(["is", "visible", ref])
            return True
        except subprocess.CalledProcessError:
            return False

    def is_enabled(self, ref: str) -> bool:
        """Check if element is enabled."""
        try:
            self._run(["is", "enabled", ref])
            return True
        except subprocess.CalledProcessError:
            return False

    def is_checked(self, ref: str) -> bool:
        """Check if checkbox is checked."""
        try:
            self._run(["is", "checked", ref])
            return True
        except subprocess.CalledProcessError:
            return False

    # -- semantic find (CLI wrapper) -----------------------------------------

    def find_and_click(self, locator: str, value: str,
                       exact: bool = False) -> str:
        """Find element by semantic locator and click."""
        cmd = ["find", locator, value, "click"]
        if exact:
            cmd.append("--exact")
        self._run(cmd)
        return self.snapshot(interactive=True)

    # -- snapshot element lookup (for LLM-driven ref-based interaction) ------

    def find_ref_by_text(self, text: str, role: Optional[str] = None) -> Optional[str]:
        """
        Find an element's ref ID by its visible text.
        Searches the interactive accessibility snapshot.
        """
        snap = self.snapshot(interactive=True)
        for line in snap.splitlines():
            if text.lower() not in line.lower():
                continue
            if role and f'[{role}]' not in line.lower():
                continue
            ref_match = re.search(r'\[ref=(e\d+)\]', line)
            if ref_match:
                return f'@{ref_match.group(1)}'
        return None

    def find_refs_by_role(self, role: str) -> List[Tuple[str, str]]:
        """Find all elements of a given role in the accessibility snapshot."""
        snap = self.snapshot(interactive=True)
        results = []
        role_pattern = re.compile(rf'(?:^|\s)-\s+{re.escape(role)}\b', re.IGNORECASE)
        ref_pattern = re.compile(r'\[ref=(e\d+)\]')
        text_pattern = re.compile(r'"([^"]*)"')
        for line in snap.splitlines():
            if role_pattern.search(line):
                ref_m = ref_pattern.search(line)
                text_m = text_pattern.search(line)
                ref = f'@{ref_m.group(1)}' if ref_m else '@?'
                text = text_m.group(1) if text_m else ''
                results.append((ref, text))
        return results

    # -- JavaScript evaluation -----------------------------------------------

    def evaluate(self, expression: str) -> Any:
        """Evaluate JavaScript expression in page context."""
        encoded = base64.b64encode(expression.encode()).decode()
        output = self._run(["eval", "-b", encoded])
        if not output.strip():
            return None
        try:
            return json.loads(output.strip())
        except (json.JSONDecodeError, ValueError):
            return output.strip()

    def evaluate_stdin(self, script: str) -> Any:
        """Evaluate multi-line JavaScript via stdin (avoids shell escaping issues)."""
        output = self._run_stdin(script, ["eval", "--stdin"])
        if not output.strip():
            return None
        try:
            return json.loads(output.strip())
        except (json.JSONDecodeError, ValueError):
            return output.strip()

    # -- JavaScript-driven page operations -----------------------------------

    def get_table_data(self) -> List[List[str]]:
        """
        Extract table data from the page using JS DOM queries.
        Returns all tables as list of rows (list of cell texts).
        """
        result = self.evaluate_stdin(JS_GET_TABLE_DATA)
        if result and isinstance(result, list):
            # Return last table's rows (most likely the one of interest)
            return result[-1] if result else []
        return []

    def get_all_tables(self) -> List[List[List[str]]]:
        """
        Extract ALL tables from the page.
        Returns list of tables, each table is a list of rows.
        """
        result = self.evaluate_stdin(JS_GET_TABLE_DATA)
        return result if isinstance(result, list) else []

    def find_table_by_header(self, header_text: str) -> List[List[str]]:
        """
        Find a table by searching for header text, then return all its data rows.
        Handles nested table structures (headers in one <table>, data in sibling).
        """
        result = self.evaluate_stdin(r"""
        (() => {
          const searchHeader = ${HEADER_JSON};
          // Find any table or adjacent table containing this header text
          const allTables = document.querySelectorAll('table');
          for (let i = 0; i < allTables.length; i++) {
            const text = allTables[i].textContent;
            if (text.includes(searchHeader)) {
              // Collect all rows from this table and siblings
              const rows = [];
              let j = i;
              while (j < allTables.length) {
                const currentText = allTables[j].textContent;
                // Stop if we've moved past related tables
                if (j > i + 2 && !currentText.includes(searchHeader)) break;
                const trs = allTables[j].querySelectorAll('tr');
                for (const tr of trs) {
                  const cells = [];
                  tr.querySelectorAll('td, th').forEach(td => {
                    const text = td.textContent.trim();
                    if (text) cells.push(text);
                  });
                  if (cells.length) rows.push(cells);
                }
                j++;
              }
              return rows;
            }
          }
          return [];
        })()
        """.replace("${HEADER_JSON}", json.dumps(header_text)))
        return result if isinstance(result, list) else []

    def get_all_text(self, max_chars: int = 5000) -> str:
        """Get all visible text from the page using JS."""
        script = JS_GET_ALL_TEXT.replace("${MAX_LEN}", str(max_chars))
        return self.evaluate_stdin(script) or ''

    def get_internal_links(self) -> List[Dict[str, str]]:
        """
        Get only links that point to the same domain or are hash-based SPA nav.
        Uses JS DOM queries, not snapshot parsing.
        """
        return self.evaluate_stdin(JS_GET_INTERNAL_LINKS) or []

    def get_all_links(self) -> List[Dict[str, str]]:
        """Get ALL links on the page. Uses JS DOM queries."""
        return self.evaluate_stdin(JS_GET_ALL_LINKS) or []

    def get_sidebar_links(self) -> List[Dict[str, str]]:
        """
        Get links from the sidebar navigation area.
        Tries common sidebar selectors, falls back to container detection.
        """
        return self.evaluate_stdin(JS_GET_SIDEBAR_LINKS) or []

    def get_form_fields(self) -> Dict[str, List[Dict[str, str]]]:
        """Get all form inputs, selects, and textareas with their labels."""
        return self.evaluate_stdin(JS_GET_FORM_FIELDS) or {}

    def find_element_by_text(self, text: str,
                             tag: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Find first visible element containing the given text.
        Returns dict with tag, text, selector, and bounding box.
        """
        search_json = json.dumps(text)
        role_json = json.dumps(tag) if tag else 'null'
        script = JS_FIND_ELEMENT_BY_TEXT.replace(
            "${SEARCH_TEXT_JSON}", search_json
        ).replace("${ROLE_JSON}", role_json)
        return self.evaluate_stdin(script)

    def find_elements_by_text(self, text: str) -> List[Dict[str, str]]:
        """Find up to 20 visible elements containing the given text."""
        search_json = json.dumps(text)
        script = JS_FIND_ELEMENTS_BY_TEXT.replace(
            "${SEARCH_TEXT_JSON}", search_json
        )
        return self.evaluate_stdin(script) or []

    def scroll_to_bottom_js(self) -> None:
        """Scroll to bottom using JS (instant, no steps)."""
        self.evaluate_stdin(JS_SCROLL_TO_BOTTOM)

    def scroll_to_top_js(self) -> None:
        """Scroll to top using JS (instant)."""
        self.evaluate_stdin(JS_SCROLL_TO_TOP)

    def scroll_by_js(self, pixels: int) -> None:
        """Scroll by N pixels using JS."""
        script = JS_SCROLL_STEP.replace("${PIXELS}", str(pixels))
        self.evaluate_stdin(script)

    def scroll_and_capture(self, steps: int = 3) -> str:
        """
        Scroll to bottom to trigger lazy loading, capture full snapshot,
        then scroll back to top. Uses JS for instant scrolling.
        """
        # Scroll to bottom (one JS call, not 5 CLI calls)
        self.scroll_to_bottom_js()
        time.sleep(0.5)

        # Capture full snapshot (accessibility tree is scroll-independent)
        result = self.snapshot(full=True)

        # Scroll back to top
        self.scroll_to_top_js()
        return result

    # -- high-level Python orchestrators -------------------------------------

    def click_text(self, text: str, role: Optional[str] = None,
                   wait_ms: int = 1000) -> str:
        """
        Find element by text in snapshot and click it.
        Uses accessibility snapshot for LLM-driven interaction.
        """
        ref = self.find_ref_by_text(text, role)
        if not ref:
            raise RuntimeError(
                f"Could not find element with text: '{text}'"
                + (f" (role: {role})" if role else "")
            )
        self.click(ref)
        if wait_ms > 0:
            time.sleep(wait_ms / 1000)
        return self.snapshot(interactive=True)

    def fill_text(self, label: str, text: str) -> str:
        """Find input by its label text and fill it."""
        ref = self.find_ref_by_text(label, role='textbox')
        if not ref:
            raise RuntimeError(f"Could not find textbox for label: '{label}'")
        self.fill(ref, text)
        return self.snapshot(interactive=True)

    def fill_and_click(self, field_label: str, field_value: str,
                       button_text: str) -> str:
        """Fill a field and click a button, re-snapshotting in between."""
        ref = self.find_ref_by_text(field_label, role='textbox')
        if not ref:
            raise RuntimeError(f"Could not find textbox for: '{field_label}'")
        self.fill(ref, field_value)
        snap = self.snapshot(interactive=True)
        btn_ref = self.find_ref_by_text(button_text)
        if not btn_ref:
            raise RuntimeError(
                f"Could not find button with text: '{button_text}'"
            )
        return self.click(btn_ref)

    def click_and_refresh(self, text: str, role: Optional[str] = None,
                          wait_ms: int = 1500) -> str:
        """
        Click by text, return fresh snapshot so refs are valid.
        Safe default — never assumes refs persist across actions.
        """
        return self.click_text(text, role=role, wait_ms=wait_ms)

    def fill_and_refresh(self, ref: str, text: str) -> str:
        """Fill a field and return fresh snapshot (refs change after fill)."""
        self.fill(ref, text)
        return self.snapshot(interactive=True)

    def login(self, username: Optional[str] = None,
              password: Optional[str] = None,
              submit_text: str = "LOG IN",
              wait_ms: int = 2000) -> str:
        """
        Attempt login on the current page.
        Handles single password field, username+password, various submit texts.
        """
        textboxes = self.find_refs_by_role('textbox')

        if password:
            if username and len(textboxes) >= 2:
                self.fill(textboxes[0][0], username)
                self.snapshot(interactive=True)  # refresh refs
                self.fill(textboxes[1][0], password)
            elif len(textboxes) >= 1:
                self.fill(textboxes[-1][0], password)
            else:
                raise RuntimeError("No textbox found for login")

        self.snapshot(interactive=True)  # refresh refs

        submit_ref = (
            self.find_ref_by_text(submit_text) or
            self.find_ref_by_text("Login") or
            self.find_ref_by_text("Sign In") or
            self.find_ref_by_text("Submit") or
            self.find_ref_by_text("log in", role='link') or
            self.find_ref_by_text("login", role='link') or
            self.find_ref_by_text("sign in", role='link')
        )
        if not submit_ref:
            raise RuntimeError(
                f"Could not find submit button. Tried: '{submit_text}', "
                "Login, Sign In, Submit"
            )
        self.click(submit_ref)
        if wait_ms > 0:
            time.sleep(wait_ms / 1000)
        return self.snapshot(interactive=True)

    def multi_action(self, actions: List[Dict[str, Any]],
                     wait_ms: int = 1000) -> List[str]:
        """
        Execute a sequence of actions, re-snapshotting between each one.
        Each action is a dict with 'type' and params.

        Supported types: click, fill, scroll_down, scroll_up, wait, snapshot,
                         js (runs JavaScript via evaluate_stdin)
        """
        snapshots = []
        for action in actions:
            atype = action.get('type', '')
            try:
                if atype == 'click':
                    snap = self.click_and_refresh(
                        action['text'],
                        role=action.get('role'),
                        wait_ms=action.get('wait_ms', wait_ms),
                    )
                    snapshots.append(snap)
                elif atype == 'fill':
                    if 'ref' in action:
                        snap = self.fill_and_refresh(action['ref'], action['text'])
                    else:
                        ref = self.find_ref_by_text(action['label'], role='textbox')
                        if not ref:
                            raise RuntimeError(
                                f"No textbox for label '{action['label']}'"
                            )
                        snap = self.fill_and_refresh(ref, action['text'])
                    snapshots.append(snap)
                elif atype == 'scroll_down':
                    self.scroll_by_js(action.get('pixels', 500))
                    snapshots.append(self.snapshot(interactive=True))
                elif atype == 'scroll_up':
                    self.scroll_by_js(-action.get('pixels', 500))
                    snapshots.append(self.snapshot(interactive=True))
                elif atype == 'js':
                    result = self.evaluate_stdin(action['script'])
                    snapshots.append(json.dumps(result) if result else '')
                elif atype == 'wait':
                    time.sleep(action.get('ms', wait_ms) / 1000)
                    snapshots.append(self.snapshot(interactive=True))
                elif atype == 'snapshot':
                    snapshots.append(self.snapshot(interactive=True))
                else:
                    raise ValueError(f"Unknown action type: {atype}")
            except Exception as e:
                snapshots.append(f"ERROR: {e}")
        return snapshots

    # -- screenshot ----------------------------------------------------------

    def screenshot(self, path: Optional[str] = None, full_page: bool = False,
                   annotate: bool = False) -> str:
        """Take screenshot."""
        if path is None:
            path = f"/tmp/browser_next_{uuid.uuid4().hex[:8]}.png"
        cmd = ["screenshot"]
        if full_page:
            cmd.append("--full")
        if annotate:
            cmd.append("--annotate")
        cmd.append(path)
        self._run(cmd)
        return path

    # -- console & errors ----------------------------------------------------

    def get_console(self, clear: bool = False) -> str:
        """Get browser console messages (log/warn/error)."""
        cmd = ["console"]
        if clear:
            cmd.append("--clear")
        return self._run(cmd).strip()

    def get_errors(self, clear: bool = False) -> str:
        """Get page errors (JS exceptions, network errors)."""
        cmd = ["errors"]
        if clear:
            cmd.append("--clear")
        return self._run(cmd).strip()

    # -- cookies & storage ---------------------------------------------------

    def get_cookies(self) -> str:
        """Get all cookies."""
        return self._run(["cookies"]).strip()

    def set_cookie(self, name: str, value: str, url: Optional[str] = None) -> None:
        """Set a cookie."""
        cmd = ["cookies", "set", name, value]
        if url:
            cmd.extend(["--url", url])
        self._run(cmd)

    def clear_cookies(self) -> None:
        """Clear all cookies."""
        self._run(["cookies", "clear"])

    def get_local_storage(self) -> str:
        """Get all localStorage."""
        return self._run(["storage", "local"]).strip()

    def clear_local_storage(self) -> None:
        """Clear all localStorage."""
        self._run(["storage", "local", "clear"])

    # -- tabs ----------------------------------------------------------------

    def list_tabs(self) -> str:
        """List open tabs."""
        return self._run(["tab"]).strip()

    def new_tab(self, url: Optional[str] = None,
                label: Optional[str] = None) -> str:
        """Open new tab. Returns tab list."""
        cmd = ["tab", "new"]
        if label:
            cmd.extend(["--label", label])
        if url:
            cmd.append(url)
        self._run(cmd)
        return self.list_tabs()

    def switch_tab(self, tab_id: str) -> str:
        """Switch to tab by ID or label. Returns snapshot."""
        self._run(["tab", tab_id])
        return self.snapshot(interactive=True)

    def close_tab(self, tab_id: Optional[str] = None) -> str:
        """Close tab. If no ID, closes current tab."""
        if tab_id:
            self._run(["tab", "close", tab_id])
        else:
            self._run(["tab", "close"])
        return self.snapshot(interactive=True)

    # -- frames --------------------------------------------------------------

    def enter_frame(self, ref_or_selector: str) -> str:
        """Switch to iframe by ref or CSS selector. Returns snapshot."""
        self._run(["frame", ref_or_selector])
        return self.snapshot(interactive=True)

    def exit_frame(self) -> str:
        """Return to main frame. Returns snapshot."""
        self._run(["frame", "main"])
        return self.snapshot(interactive=True)

    # -- settings ------------------------------------------------------------

    def set_viewport(self, width: int, height: int) -> None:
        """Set viewport size."""
        self._run(["set", "viewport", str(width), str(height)])

    def set_offline(self, offline: bool) -> None:
        """Toggle offline mode."""
        self._run(["set", "offline", "on" if offline else "off"])

    # -- batch ---------------------------------------------------------------

    def batch(self, commands: List[List[str]],
              bail: bool = False) -> List[Dict[str, Any]]:
        """Execute multiple commands in a single call via stdin (JSON mode)."""
        cmd = ["batch", "--json"]
        if bail:
            cmd.append("--bail")
        stdin_json = json.dumps(commands)
        output = self._run_stdin(stdin_json, cmd)
        try:
            return json.loads(output.strip())
        except json.JSONDecodeError:
            return [{"success": False, "error": output}]

    # -- navigation history (Python state tracking) --------------------------

    def _track_url(self, url: str) -> bool:
        """Track a URL visit. Returns True if new."""
        if url in self._tracked_urls:
            return False
        self._tracked_urls.add(url)
        self._history.append({
            'url': url,
            'title': self.get_title(),
            'timestamp': time.time(),
        })
        return True

    def visited_urls(self) -> List[str]:
        """Return list of all visited URLs."""
        return [entry['url'] for entry in self._history]

    def is_new_url(self, url: str) -> bool:
        """Check if URL has been visited before in this session."""
        return url not in self._tracked_urls

    # -- domain awareness (Python state tracking) ----------------------------

    def get_domain(self) -> str:
        """Get the domain (hostname) of the current URL."""
        url = self.get_url()
        match = re.match(r'https?://([^/:]+)', url)
        return match.group(1) if match else ''

    def is_same_domain(self, url: str) -> bool:
        """Check if a URL is on the same domain as the current page."""
        current = self.get_domain()
        match = re.match(r'https?://([^/:]+)', url)
        target = match.group(1) if match else ''
        return current == target

    def navigate_safe(self, url: str) -> str:
        """
        Navigate to URL only if it's on the same domain.
        Raises RuntimeError if URL is external.
        """
        if not self.is_same_domain(url):
            current = self.get_domain()
            match = re.match(r'https?://([^/:]+)', url)
            target = match.group(1) if match else '?'
            raise RuntimeError(
                f"Refusing to navigate to external domain '{target}' "
                f"(current: '{current}')"
            )
        return self.navigate(url)

    # -- Cloudflare-aware navigation -----------------------------------------

    def navigate_cf(self, url: str, max_wait: int = 30) -> str:
        """
        Navigate to URL with automatic Cloudflare challenge handling.
        
        If Cloudflare blocks agent-browser, falls back to Playwright which
        has better anti-detection (add_init_script runs BEFORE page JS).
        
        Args:
            url: URL to navigate to
            max_wait: Max seconds to wait for Cloudflare challenge to resolve
            
        Returns:
            Interactive snapshot of the page content
        """
        from cloudflare_bypass import is_cloudflare_challenge, PlaywrightCloudflareBrowser
        
        # Try normal navigation first
        self.navigate(url)
        time.sleep(3)  # Give Cloudflare time to serve challenge
        
        title = self.get_title()
        current_url = self.get_url()
        
        if not is_cloudflare_challenge(title, current_url):
            return self.snapshot(interactive=True)
        
        # Cloudflare detected - wait for it to resolve
        start = time.time()
        while time.time() - start < max_wait:
            title = self.get_title()
            if not is_cloudflare_challenge(title, current_url):
                break
            time.sleep(2)
        
        # If still blocked after waiting, fall back to Playwright
        title = self.get_title()
        if is_cloudflare_challenge(title, current_url):
            try:
                cf_browser = PlaywrightCloudflareBrowser()
                cf_browser.launch()
                cf_browser.navigate(url, wait_for_cf=max_wait)
                
                cf_title = cf_browser.get_title()
                cf_url = cf_browser.get_url()
                
                if not is_cloudflare_challenge(cf_title, cf_url):
                    # Success! Extract content
                    text = cf_browser.get_text(max_length=50000)
                    cf_browser.close()
                    
                    # Return a formatted result
                    return f"[CLOUDFLARE BYPASS - Playwright]\nTitle: {cf_title}\nURL: {cf_url}\n\n{text[:50000]}"
            except Exception as e:
                print(f"Cloudflare bypass failed: {e}")
        
        return self.snapshot(interactive=True)

    # -- SPA awareness (Python state tracking) -------------------------------

    def get_hash(self) -> str:
        """Get the hash fragment of the current URL."""
        url = self.get_url()
        return url.split('#')[-1] if '#' in url else ''

    def is_spa(self) -> bool:
        """Detect if current page is a hash-based SPA."""
        return '#' in self.get_url()

    def navigate_hash(self, hash_fragment: str) -> str:
        """Navigate to a hash fragment on the current domain/path."""
        if not hash_fragment.startswith('#'):
            hash_fragment = f'#{hash_fragment}'
        url = self.get_url().split('#')[0]
        return self.navigate(f"{url}{hash_fragment}")

    def wait_for_hash(self, expected_hash: str,
                      timeout_ms: int = 5000) -> bool:
        """Wait for URL hash to change to expected value."""
        if not expected_hash.startswith('#'):
            expected_hash = f'#{expected_hash}'
        start = time.time()
        while time.time() - start < timeout_ms / 1000:
            current_hash = f'#{self.get_hash()}'
            if current_hash == expected_hash:
                return True
            time.sleep(0.2)
        return False

    # -- hybrid routing (v2) ---------------------------------------------------

    def route_url(self, url: str) -> Dict[str, Any]:
        """Determine if URL should go to local or cloud browser."""
        from browser_next_utils import route_url as _route_url
        return _route_url(url, self.cloud_provider)

    def navigate_auto(self, url: str, **kwargs) -> str:
        """
        Navigate with automatic hybrid routing.
        Private/LAN URLs go to local browser, public URLs go to cloud provider
        (if configured). Falls back to local browser for everything.
        """
        route = self.route_url(url)

        if route["mode"] == "cloud" and self.cloud_provider:
            try:
                from cloud_browser import CloudBrowser
                cloud = CloudBrowser(
                    provider=self.cloud_provider,
                    viewport=self.viewport,
                    timeout=self.timeout,
                    user_agent=self.user_agent,
                    ignore_https_errors=self.ignore_https_errors,
                )
                cloud.start()
                result = cloud.navigate(url, **kwargs)
                cloud.close()
                return result
            except Exception as e:
                print(
                    f"[Browser_Next] Cloud routing failed ({e}), "
                    f"falling back to local browser",
                    file=sys.stderr,
                )

        return self.navigate(url, **kwargs)

    # -- content extraction (v2) ----------------------------------------------

    def extract_article(self, max_chars: int = 50000) -> Dict[str, Any]:
        """
        Extract main article content from page.
        Tries site-specific selectors (Wikipedia, Medium, Ghost, etc.),
        falls back to body text. Returns dict with title, url, text, length.
        """
        from browser_next_utils import extract_article_js
        result = self.evaluate_stdin(extract_article_js())
        text = result if isinstance(result, str) else str(result)
        text = clean_article_text(text, max_chars)
        return {
            "title": self.get_title(),
            "url": self.get_url(),
            "text": text,
            "length": len(text),
        }

    def extract_with_scroll(self, max_scrolls: int = 5, max_chars: int = 50000) -> Dict[str, Any]:
        """
        Handle infinite scroll / lazy loading, then extract article content.
        Scrolls down repeatedly until no new content loads, then extracts clean text.
        """
        from browser_next_utils import extract_with_scroll_js
        result = self.evaluate_stdin(extract_with_scroll_js(max_scrolls))
        if isinstance(result, dict):
            text = result.get("text", "")
        else:
            text = str(result)
        text = clean_article_text(text, max_chars)
        return {
            "title": self.get_title(),
            "url": self.get_url(),
            "text": text,
            "length": len(text),
        }

    def read_page(self, max_chars: int = 15000, scroll: bool = True) -> Dict[str, Any]:
        """
        Read a page's content — the pure reading mode.
        Handles infinite scroll if scroll=True. Strips ads/nav/footer.
        Returns dict with title, url, text, length.
        """
        if scroll:
            return self.extract_with_scroll(max_chars=max_chars)
        return self.extract_article(max_chars=max_chars)

    # -- session persistence (v2) ---------------------------------------------

    def save_session(self) -> None:
        """Save cookies and localStorage to named profile."""
        if not self.profile:
            raise RuntimeError("No profile set. Initialize with profile='name'.")
        try:
            cookies_raw = self.get_cookies()
            cookies = json.loads(cookies_raw) if cookies_raw.strip() else []
            save_cookies(self.profile, cookies)
        except Exception:
            pass
        try:
            storage_raw = self.get_local_storage()
            if storage_raw.strip():
                storage = json.loads(storage_raw)
                save_storage(self.profile, storage)
        except Exception:
            pass

    def load_session(self) -> None:
        """Load cookies and localStorage from named profile."""
        if not self.profile:
            raise RuntimeError("No profile set. Initialize with profile='name'.")
        # Load cookies
        cookies = load_cookies(self.profile)
        for cookie in cookies:
            try:
                self.set_cookie(cookie.get("name", ""), cookie.get("value", ""), cookie.get("url"))
            except Exception:
                pass

    def close_and_save(self) -> None:
        """Save session state then close browser."""
        self.save_session()
        self.close()

    # -- anti-detection (v2) --------------------------------------------------

    def apply_anti_detection(self) -> None:
        """
        Apply anti-detection scripts to current page.
        Hides webdriver, spoofs navigator properties, adds canvas noise.
        Call after navigating to a page.
        """
        if not self.anti_detection:
            return
        from browser_next_utils import get_anti_detection_script
        self.evaluate_stdin(get_anti_detection_script())

    # -- search integration (v2) -----------------------------------------------

    def search_and_read(
        self, query: str, max_read: int = 3, max_chars: int = 15000, engine: str = "searxng"
    ) -> List[Dict[str, Any]]:
        """
        Search, then visit and scrape top results.
        Like the original Browser_Emulator search_and_read.
        """
        from browser_next_utils import search
        results = search(query, max_results=max_read, engine=engine)
        readings = []
        for rank, res in enumerate(results[:max_read], start=1):
            url = res.get("href", "")
            if not url:
                readings.append({
                    "rank": rank,
                    "search_result": res,
                    "success": False,
                    "error": "no URL",
                })
                continue
            try:
                self.navigate(url, wait_network_idle=True)
                article = self.read_page(max_chars=max_chars)
                readings.append({
                    "rank": rank,
                    "search_result": res,
                    "article": article,
                    "success": True,
                })
            except Exception as e:
                readings.append({
                    "rank": rank,
                    "search_result": res,
                    "success": False,
                    "error": str(e),
                })
        return readings

    # -- properties ----------------------------------------------------------

    def _build_env(self) -> Dict[str, str]:
        """Build environment for subprocess calls."""
        env = os.environ.copy()
        env["AGENT_BROWSER_SESSION"] = self.session
        if not self.headless:
            env["AGENT_BROWSER_HEADED"] = "true"
        if self.ignore_https_errors:
            env["AGENT_BROWSER_IGNORE_HTTPS_ERRORS"] = "true"

    @property
    def current_url(self) -> str:
        """Get current URL."""
        return self.get_url()

    @property
    def page_title(self) -> str:
        """Get page title."""
        return self.get_title()

    @property
    def is_running(self) -> bool:
        """Check if browser session is active."""
        return self._started and not self._closed

    # -- internal ------------------------------------------------------------

    def _build_env(self) -> Dict[str, str]:
        """Build environment for subprocess calls."""
        env = os.environ.copy()
        env["AGENT_BROWSER_SESSION"] = self.session
        if not self.headless:
            env["AGENT_BROWSER_HEADED"] = "true"
        if self.ignore_https_errors:
            env["AGENT_BROWSER_IGNORE_HTTPS_ERRORS"] = "true"
        return env

    def _run(self, args: List[str], timeout: Optional[int] = None) -> str:
        """Run agent-browser command, return stdout."""
        full_cmd = [AGENT_BROWSER] + args
        env = self._build_env()
        try:
            result = subprocess.run(
                full_cmd,
                capture_output=True,
                text=True,
                timeout=timeout or self.timeout,
                env=env,
            )
            if result.returncode != 0:
                stderr = result.stderr.strip()
                stdout = result.stdout.strip()
                error_msg = stderr or stdout
                if not error_msg:
                    error_msg = (
                        f"agent-browser exited with code {result.returncode}"
                    )
                raise RuntimeError(
                    f"Browser command failed: {' '.join(args)}\n{error_msg}"
                )
            return result.stdout
        except subprocess.TimeoutExpired:
            raise TimeoutError(
                f"Browser command timed out after "
                f"{timeout or self.timeout}ms: {' '.join(args)}"
            )

    def _run_stdin(self, stdin_text: str, args: List[str],
                   timeout: Optional[int] = None) -> str:
        """Run command with stdin input."""
        full_cmd = [AGENT_BROWSER] + args
        env = self._build_env()
        result = subprocess.run(
            full_cmd,
            input=stdin_text,
            capture_output=True,
            text=True,
            timeout=timeout or self.timeout,
            env=env,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Browser command failed: {' '.join(args)}\n{result.stderr}"
            )
        return result.stdout
