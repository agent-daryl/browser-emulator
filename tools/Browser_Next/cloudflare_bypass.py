#!/usr/bin/env python3
"""
Cloudflare bypass module for Browser_Next.

Uses Playwright with proper init scripts to bypass Cloudflare challenges.
This is a fallback when agent-browser gets blocked by Cloudflare's managed tier.

Key difference from agent-browser: Playwright's add_init_script() runs BEFORE
any page JavaScript executes, so navigator.webdriver is already undefined when
Cloudflare's challenge runs.
"""

import re
import time
from typing import Any, Dict, Optional


# Cloudflare challenge detection patterns
_CLOUDFLARE_TITLES = ["just a moment", "attention required!", "access denied", "checking your browser"]
_CLOUDFLARE_URLS = ["cf-chl", "cdn-cgi/challenge-platform", "security/verify"]
_CLOUDFLARE_SELECTORS = [
    "#challenge-running", "#challenge-error-title", "#challenge-body-text",
    ".cf-browser-verification", "#turnstile-wrapper", "#cf-challenge-running"
]


def is_cloudflare_challenge(title: str, url: str, body_text: Optional[str] = None) -> bool:
    """
    Detect if current page is a Cloudflare challenge/bot check.
    """
    title_lower = title.lower().strip() if title else ""
    url_lower = url.lower() if url else ""
    body_lower = (body_text or "").lower()[:1000]

    # Check title
    for pattern in _CLOUDFLARE_TITLES:
        if pattern in title_lower:
            return True

    # Check URL
    for pattern in _CLOUDFLARE_URLS:
        if pattern in url_lower:
            return True

    # Check body text for challenge indicators
    if any(p in body_lower for p in ["performing security verification", "ray id", "please wait while we check", "checking your browser"]):
        return True

    return False


class PlaywrightCloudflareBrowser:
    """
    Playwright browser configured to bypass Cloudflare challenges.
    
    Key techniques:
    1. add_init_script() runs BEFORE page JS - hides navigator.webdriver
    2. Proper viewport, locale, timezone matching real browser
    3. Full browser features (WebGL, WebRTC, etc.)
    4. No automation flags in TLS fingerprint
    """

    CHROME_UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )

    # Complete anti-detection init script (runs BEFORE page JS)
    INIT_SCRIPT = """
    // Hide webdriver (CRITICAL - runs before page JS)
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

    // Spoof hardware concurrency
    Object.defineProperty(navigator, 'hardwareConcurrency', {
        get: () => 8
    });

    // Spoof device memory
    Object.defineProperty(navigator, 'deviceMemory', {
        get: () => 8
    });

    // Hide webkitGetUserMedia
    Object.defineProperty(navigator, 'webkitGetUserMedia', {get: () => undefined});
    Object.defineProperty(navigator, 'mediaDevices', {get: () => ({})});

    // Spoof window.chrome
    if (!window.chrome) {
        window.chrome = {
            runtime: {},
            loadTimes: function() {},
            csi: function() {},
            app: {}
        };
    }

    // Fix maxTouchPoints
    Object.defineProperty(navigator, 'maxTouchPoints', {
        get: () => 1
    });
    """

    def __init__(
        self,
        headless: bool = True,
        viewport_width: int = 1920,
        viewport_height: int = 1080,
        locale: str = "en-US",
        timezone_id: str = "America/Denver",
        ignore_https_errors: bool = False,
        wait_for_cf: int = 30,  # Max seconds to wait for Cloudflare challenge
    ):
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self.headless = headless
        self.viewport = (viewport_width, viewport_height)
        self.locale = locale
        self.timezone_id = timezone_id
        self.ignore_https_errors = ignore_https_errors
        self.wait_for_cf = wait_for_cf
        self.history = []
        self.started = False

    def launch(self) -> "PlaywrightCloudflareBrowser":
        """Start browser with anti-detection configuration."""
        if self.started:
            return self

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise ImportError("Playwright is required for Cloudflare bypass. Install with: python3.12 -m pip install playwright && playwright install chromium")

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=self.headless,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        self._context = self._browser.new_context(
            user_agent=self.CHROME_UA,
            viewport={"width": self.viewport[0], "height": self.viewport[1]},
            locale=self.locale,
            timezone_id=self.timezone_id,
            ignore_https_errors=self.ignore_https_errors,
        )
        # CRITICAL: Run anti-detection BEFORE any page JS executes
        self._context.add_init_script(self.INIT_SCRIPT)
        self._page = self._context.new_page()
        self.started = True
        return self

    @property
    def page(self):
        if not self.started:
            self.launch()
        return self._page

    def navigate(self, url: str, wait_for_cf: Optional[int] = None) -> Dict[str, Any]:
        """
        Navigate to URL with Cloudflare challenge handling.
        
        Args:
            url: URL to navigate to
            wait_for_cf: Max seconds to wait for Cloudflare challenge (overrides instance setting)
        """
        wait_time = wait_for_cf if wait_for_cf is not None else self.wait_for_cf
        
        self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        self.history.append(url)
        
        # Check for Cloudflare challenge and wait for it to resolve
        if wait_time > 0:
            start_time = time.time()
            while time.time() - start_time < wait_time:
                title = self.page.title()
                current_url = self.page.url
                
                if not is_cloudflare_challenge(title, current_url):
                    # Challenge resolved or not a CF page
                    break
                
                # Wait and check again
                time.sleep(2)
                try:
                    # Try to wait for the challenge to resolve
                    self.page.wait_for_load_state("networkidle", timeout=3000)
                except Exception:
                    pass
        
        # Wait for network idle
        try:
            self.page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        
        return {
            "title": self.page.title(),
            "url": self.page.url,
            "success": True,
        }

    def get_text(self, selector: str = "body", max_length: int = 50000) -> str:
        """Extract text from page element."""
        try:
            loc = self.page.locator(selector)
            if loc.count() > 0:
                return loc.nth(0).inner_text(timeout=5000)
        except Exception:
            pass
        return self.page.evaluate("document.body.innerText || ''")[:max_length]

    def get_title(self) -> str:
        return self.page.title()

    def get_url(self) -> str:
        return self.page.url

    def evaluate(self, expr: str) -> Any:
        """Run JavaScript in page context."""
        return self.page.evaluate(expr)

    def screenshot(self, path: str, full_page: bool = False) -> str:
        """Take screenshot."""
        self.page.screenshot(path=path, full_page=full_page)
        return path

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


def try_cloudflare_bypass(url: str, max_wait: int = 30) -> Optional[Dict[str, Any]]:
    """
    One-shot Cloudflare bypass attempt.
    
    Returns dict with title, url, text, length if successful.
    Returns None if Playwright is not available or bypass fails.
    """
    try:
        with PlaywrightCloudflareBrowser() as browser:
            browser.navigate(url, wait_for_cf=max_wait)
            
            # Check if we got through
            title = browser.get_title()
            current_url = browser.get_url()
            
            if is_cloudflare_challenge(title, current_url):
                return None  # Still blocked
            
            # Extract content
            text = browser.get_text(max_length=50000)
            
            return {
                "title": title,
                "url": current_url,
                "text": text,
                "length": len(text),
                "bypassed_cf": True,
            }
    except ImportError:
        return None
    except Exception as e:
        return None
