#!/usr/bin/env python3
"""
Browser Next CLI — Command-line interface for opencode's browser automation.

Usage:
    # Single commands (session starts/stops per invocation)
    python3 cli.py navigate "https://example.com"
    python3 cli.py snapshot --interactive
    python3 cli.py click "@e1"

    # Search + read (uses SearXNG + browser)
    python3 cli.py search "OpenShift AI installation" --max-read 3

    # Interactive REPL (persistent session)
    python3 cli.py interactive

    # Read specific URL
    python3 cli.py --url "https://example.com"
"""

import argparse
import json
import sys
import os
import tempfile
import time
from pathlib import Path
from typing import List

# Ensure this directory is on path for imports
_module_dir = Path(__file__).parent
if str(_module_dir) not in sys.path:
    sys.path.insert(0, str(_module_dir))

# Ensure tools/ parent is on path for search imports
_tools_dir = Path(__file__).parent.parent.parent
if str(_tools_dir) not in sys.path:
    sys.path.insert(0, str(_tools_dir))

from browser_next import BrowserSession, AGENT_BROWSER
from browser_next_utils import (
    is_private_url, route_url, find_cdp_endpoint, launch_chrome_cdp,
    get_random_ua, list_profiles, search as utils_search,
    save_cookies, load_cookies, save_storage, load_storage,
    clean_article_text, extract_article_js, extract_with_scroll_js,
)


# ---------------------------------------------------------------------------
# Search integration (reuses SearXNG from Web_Search)
# ---------------------------------------------------------------------------

def search(query: str, max_results: int = 5) -> List[dict]:
    """Search via SearXNG (primary) with DDG fallback."""
    search_script = _tools_dir / "Web_Search" / "search.py"
    if search_script.exists():
        try:
            result = os.popen(
                f"python3 {search_script} '{query}' 2>&1"
            ).read()
            return _parse_search_output(result, max_results)
        except Exception:
            pass

    from ddgs import DDGS
    raw = DDGS().text(query, max_results=max_results)
    return [
        {"title": r.get("title", ""), "href": r.get("href", ""), "body": r.get("body", "")}
        for r in raw
    ]


def _parse_search_output(text: str, max_results: int) -> List[dict]:
    """Parse SearXNG search.py output into structured results."""
    results = []
    lines = text.split("\n")
    current = {}
    in_result = False
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("=") or stripped.startswith("Searching"):
            if current and "title" in current:
                results.append(current)
                current = {}
                if len(results) >= max_results:
                    break
            in_result = False
            continue

        if not in_result and stripped and stripped[0].isdigit() and "." in stripped[:4]:
            in_result = True
            current = {"title": stripped.split(".", 1)[1].strip() if "." in stripped else stripped}
        elif in_result:
            if stripped.startswith("URL: "):
                current["href"] = stripped[5:].strip()
            elif stripped.startswith("Engine:"):
                pass
            elif stripped and "..." in stripped and "body" not in current:
                current["body"] = stripped
    if current and "title" in current:
        results.append(current)
    return results[:max_results]


def search_and_read(query: str, max_read: int = 3) -> List[dict]:
    """Search + read top results with browser."""
    results = search(query, max_results=max_read)
    readings = []
    with BrowserSession() as browser:
        for i, res in enumerate(results[:max_read]):
            url = res.get("href", "")
            if not url:
                readings.append({
                    "rank": i + 1,
                    "search_result": res,
                    "success": False,
                    "error": "no URL",
                })
                continue
            try:
                browser.navigate(url, wait_network_idle=True, wait_ms=2000)
                # Use article extraction for readable content
                article = browser.read_page(max_chars=12000)
                readings.append({
                    "rank": i + 1,
                    "search_result": res,
                    "title": article.get("title", browser.get_title()),
                    "snapshot": article.get("text", ""),
                    "success": True,
                })
            except Exception as e:
                readings.append({
                    "rank": i + 1,
                    "search_result": res,
                    "success": False,
                    "error": str(e),
                })
    return readings


# ---------------------------------------------------------------------------
# REPL mode
# ---------------------------------------------------------------------------

REPL_COMMANDS = {
    "navigate": lambda b, args: b.navigate(args),
    "snapshot": lambda b, args: b.snapshot(
        interactive="interactive" in args or "-i" in args,
        full="--full" in args,
    ),
    "click": lambda b, args: b.click(args),
    "dblclick": lambda b, args: b.dblclick(args),
    "fill": lambda b, args: (b.fill(args[0], " ".join(args[1:])) if len(args) > 1 else None),
    "type": lambda b, args: (b.type_text(args[0], " ".join(args[1:])) if len(args) > 1 else None),
    "press": lambda b, args: b.press(args),
    "scroll": lambda b, args: b.scroll(args[0] if args else "down", int(args[1]) if len(args) > 1 else 300),
    "back": lambda b, args: b.back(),
    "reload": lambda b, args: b.reload(),
    "title": lambda b, args: b.get_title(),
    "url": lambda b, args: b.get_url(),
    "text": lambda b, args: b.get_text(args),
    "eval": lambda b, args: b.evaluate(args),
    "console": lambda b, args: b.get_console("--clear" in args),
    "errors": lambda b, args: b.get_errors("--clear" in args),
    "screenshot": lambda b, args: b.screenshot(
        path=args[0] if args else None,
        full_page="--full" in args,
        annotate="--annotate" in args,
    ),
    "tabs": lambda b, args: b.list_tabs(),
    "cookies": lambda b, args: b.get_cookies(),
    "find": lambda b, args: b.find_and_click(args[0], args[1], "--exact" in args) if len(args) >= 2 else None,
    "search": lambda b, args: _repl_search(args),
    "wait": lambda b, args: (
        b.wait(milliseconds=int(args[0])) if args and args[0].isdigit()
        else b.wait(ref=args[0]) if args else None
    ),
    # New v2 commands
    "read": lambda b, args: b.read_page(max_chars=int(args[0]) if args and args[0].isdigit() else 15000),
    "extract": lambda b, args: b.extract_article(max_chars=int(args[0]) if args and args[0].isdigit() else 50000),
    "extract-scroll": lambda b, args: b.extract_with_scroll(max_scrolls=int(args[0]) if args and args[0].isdigit() else 5),
    "profile": lambda b, args: b.save_session() if args == "save" else b.load_session() if args == "load" else _repl_profiles(args),
    "is-private": lambda b, args: f"Private: {is_private_url(args)}" if args else None,
    "route": lambda b, args: json.dumps(route_url(args), indent=2) if args else None,
    "cdp-find": lambda b, args: f"CDP endpoint: {find_cdp_endpoint() or 'Not found'}",
    "random-ua": lambda b, args: get_random_ua(),
    "help": lambda b, args: _print_help(),
}


def _repl_search(args: str) -> str:
    query = " ".join(args) if isinstance(args, list) else args
    max_read = 2
    readings = search_and_read(query, max_read=max_read)
    lines = []
    for r in readings:
        if r.get("success"):
            lines.append(f"[{r['rank']}] {r.get('title', 'Untitled')}")
            snap = r.get("snapshot", "")[:2000]
            lines.append(snap)
            lines.append("")
        else:
            lines.append(f"[{r['rank']}] FAILED: {r.get('error', 'unknown')}")
    return "\n".join(lines)


def _repl_read(args: str) -> str:
    """Read current page content."""
    from browser_next import BrowserSession
    # Use global browser instance
    return ""


def _repl_profiles(args: str) -> str:
    profiles = list_profiles()
    return f"Saved profiles: {profiles or '(none)'}"


def _print_help() -> str:
    return """
Available commands:
  navigate <url>        Go to URL
  snapshot [-i|--full]  Page snapshot (interactive or full)
  click @e1             Click element
  dblclick @e1          Double-click
  fill @e1 text         Fill input field
  type @e1 text         Type without clearing
  press Key             Press key (Enter, Tab, etc.)
  scroll [dir] [px]     Scroll page
  back                  Go back
  reload                Reload page
  title                 Page title
  url                   Current URL
  text @e1              Get element text
  eval <js>             Evaluate JavaScript
  console [--clear]     Console messages
  errors [--clear]      Page errors
  screenshot [--full]   Take screenshot
  tabs                  List tabs
  cookies               Get cookies
  find <loc> <val>      Find and click by semantic locator
  search <query>        Search + read with browser
  wait [ms|@ref]        Wait for element or time

  NEW (v2):
  read [chars]          Read page content (strips ads/nav, handles scroll)
  extract [chars]       Extract article content (site-specific selectors)
  extract-scroll [N]    Extract with infinite scroll handling (N scrolls)
  profile save/load/list  Session persistence with named profiles
  is-private <url>      Check if URL is private/LAN
  route <url>           Route URL to local or cloud browser
  cdp-find              Find running Chrome CDP endpoint
  random-ua             Get random user agent

  help                  This message
  quit/exit             Close browser and exit
"""


def run_repl():
    """Interactive REPL with persistent browser session."""
    print("Browser Next REPL — type 'help' for commands, 'quit' to exit")
    print()

    with BrowserSession() as browser:
        while True:
            try:
                cmd_input = input("browser> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nBye.")
                break

            if not cmd_input:
                continue
            if cmd_input.lower() in ("quit", "exit", "q"):
                print("Bye.")
                break

            parts = cmd_input.split(None, 1)
            command = parts[0].lower()
            args = parts[1].strip() if len(parts) > 1 else ""
            args_list = args.split() if args else []

            if command in REPL_COMMANDS:
                try:
                    result = REPL_COMMANDS[command](browser, args if args else args_list)
                    if result is not None:
                        print(result)
                except Exception as e:
                    print(f"Error: {e}")
            else:
                print(f"Unknown command: {command}. Type 'help' for commands.")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Browser Next — LLM-native browser automation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s navigate "https://example.com"
  %(prog)s snapshot --interactive
  %(prog)s click "@e1"
  %(prog)s --url "https://example.com"
  %(prog)s search "OpenShift installation" --max-read 3
  %(prog)s interactive
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    nav = subparsers.add_parser("navigate", help="Navigate to URL")
    nav.add_argument("url", help="URL to navigate to")
    nav.add_argument("--wait-idle", action="store_true", help="Wait for network idle")

    snap = subparsers.add_parser("snapshot", help="Page snapshot")
    snap.add_argument("--interactive", "-i", action="store_true", help="Interactive elements only")
    snap.add_argument("--full", action="store_true", help="Full accessibility tree")

    click_parser = subparsers.add_parser("click", help="Click element")
    click_parser.add_argument("ref", help="Element ref (e.g. @e1)")

    fill = subparsers.add_parser("fill", help="Fill input field")
    fill.add_argument("ref", help="Element ref")
    fill.add_argument("text", help="Text to fill")

    type_parser = subparsers.add_parser("type", help="Type text (without clearing)")
    type_parser.add_argument("ref", help="Element ref")
    type_parser.add_argument("text", help="Text to type")

    press = subparsers.add_parser("press", help="Press key")
    press.add_argument("key", help="Key name (Enter, Tab, etc.)")

    scroll_parser = subparsers.add_parser("scroll", help="Scroll page")
    scroll_parser.add_argument("direction", nargs="?", default="down", help="up/down/left/right")
    scroll_parser.add_argument("pixels", nargs="?", type=int, default=300)

    subparsers.add_parser("back", help="Go back")
    subparsers.add_parser("reload", help="Reload page")

    text_parser = subparsers.add_parser("text", help="Get element text")
    text_parser.add_argument("ref", help="Element ref")

    eval_parser = subparsers.add_parser("eval", help="Evaluate JavaScript")
    eval_parser.add_argument("expression", help="JS expression")

    console_parser = subparsers.add_parser("console", help="Console messages")
    console_parser.add_argument("--clear", action="store_true")

    screenshot_parser = subparsers.add_parser("screenshot", help="Take screenshot")
    screenshot_parser.add_argument("--full", action="store_true")
    screenshot_parser.add_argument("--annotate", action="store_true")
    screenshot_parser.add_argument("path", nargs="?", default=None)

    search_parser = subparsers.add_parser("search", help="Search + read")
    search_parser.add_argument("query", help="Search query")
    search_parser.add_argument("--max-read", type=int, default=3)

    # New v2 commands
    read_parser = subparsers.add_parser("read", help="Read page content (strips ads/nav)")
    read_parser.add_argument("--max-chars", type=int, default=15000)

    extract_parser = subparsers.add_parser("extract", help="Extract article content")
    extract_parser.add_argument("--max-chars", type=int, default=50000)

    extract_scroll = subparsers.add_parser("extract-scroll", help="Extract with infinite scroll")
    extract_scroll.add_argument("--max-scrolls", type=int, default=5)
    extract_scroll.add_argument("--max-chars", type=int, default=50000)

    cf_read_parser = subparsers.add_parser("cf-read", help="Read Cloudflare-protected page (Playwright fallback)")
    cf_read_parser.add_argument("url")
    cf_read_parser.add_argument("--max-chars", type=int, default=15000)

    navigate_cf_parser = subparsers.add_parser("navigate-cf", help="Navigate with Cloudflare fallback")
    navigate_cf_parser.add_argument("url")

    is_private = subparsers.add_parser("is-private", help="Check if URL is private/LAN")
    is_private.add_argument("url")

    route_parser = subparsers.add_parser("route", help="Route URL to local or cloud")
    route_parser.add_argument("url")
    route_parser.add_argument("--cloud-provider", default=None)

    cdp_find = subparsers.add_parser("cdp-find", help="Find running Chrome CDP endpoint")
    cdp_find.add_argument("--port", type=int, default=None)

    profiles_parser = subparsers.add_parser("profiles", help="List saved profiles")

    subparsers.add_parser("interactive", help="Start interactive REPL")

    parser.add_argument("--url", help="URL to read (shortcut)")
    parser.add_argument("--session", help="Session name")
    parser.add_argument("--max-chars", type=int, default=15000, help="Max chars for content extraction")
    parser.add_argument("--timeout", type=int, default=45000, help="Timeout in ms for page load (default: 45000)")

    args = parser.parse_args()

    if not args.command and not args.url:
        parser.print_help()
        sys.exit(0)

    if args.command == "interactive":
        run_repl()
        return

    # Utility commands that don't need a browser session
    if args.command == "is-private":
        print(f"{'Private' if is_private_url(args.url) else 'Public'}: {args.url}")
        return

    if args.command == "route":
        result = route_url(args.url, args.cloud_provider)
        print(json.dumps(result, indent=2))
        return

    if args.command == "cdp-find":
        endpoint = find_cdp_endpoint(args.port)
        print(f"CDP endpoint: {endpoint or 'Not found (no Chrome with --remote-debugging-port running)'}")
        return

    if args.command == "profiles":
        profiles = list_profiles()
        print(f"Saved profiles: {profiles or '(none)'}")
        return

    session_name = args.session
    timeout = args.timeout if hasattr(args, 'timeout') and args.timeout else 45000

    with BrowserSession(session=session_name, timeout=timeout) as browser:
        # Direct URL navigation (skip for cf-read/navigate-cf which handle it themselves)
        if args.url and not getattr(args, 'command', None) in ('cf-read', 'navigate-cf'):
            result = browser.navigate(args.url, wait_network_idle=True, wait_ms=3000)
            print(f"Title: {browser.get_title()}")
            print(f"URL:   {browser.get_url()}")
            # Auto-extract article content
            article = browser.read_page(max_chars=args.max_chars if hasattr(args, 'max_chars') else 15000)
            print(f"Len:   {article['length']} chars")
            print()
            print(article["text"])
            return

        if args.command == "navigate":
            result = browser.navigate(args.url, wait_network_idle=args.wait_idle)
            print(result)

        elif args.command == "snapshot":
            result = browser.snapshot(interactive=args.interactive, full=args.full)
            print(result)

        elif args.command == "click":
            result = browser.click(args.ref)
            print(result)

        elif args.command == "fill":
            browser.fill(args.ref, args.text)
            print("OK")

        elif args.command == "type":
            browser.type_text(args.ref, args.text)
            print("OK")

        elif args.command == "press":
            result = browser.press(args.key)
            print(result)

        elif args.command == "scroll":
            result = browser.scroll(args.direction, args.pixels)
            print(result)

        elif args.command == "back":
            result = browser.back()
            print(result)

        elif args.command == "reload":
            result = browser.reload()
            print(result)

        elif args.command == "text":
            print(browser.get_text(args.ref))

        elif args.command == "eval":
            result = browser.evaluate(args.expression)
            if isinstance(result, (dict, list)):
                print(json.dumps(result, indent=2))
            else:
                print(result)

        elif args.command == "console":
            print(browser.get_console(clear=args.clear))

        elif args.command == "screenshot":
            path = browser.screenshot(
                path=args.path,
                full_page=args.full,
                annotate=args.annotate,
            )
            print(f"Screenshot saved: {path}")

        elif args.command == "search":
            readings = search_and_read(args.query, max_read=args.max_read)
            for r in readings:
                if r.get("success"):
                    print(f"\n[{r['rank']}] {r.get('title', 'Untitled')}")
                    print(r.get("snapshot", ""))
                else:
                    print(f"\n[{r['rank']}] FAILED: {r.get('error', 'unknown')}")

        # New v2 commands
        elif args.command == "read":
            result = browser.read_page(max_chars=args.max_chars)
            print(f"Title: {result['title']}")
            print(f"URL:   {result['url']}")
            print(f"Len:   {result['length']} chars")
            print()
            print(result["text"])

        elif args.command == "extract":
            result = browser.extract_article(max_chars=args.max_chars)
            print(f"Title: {result['title']}")
            print(f"URL:   {result['url']}")
            print(f"Len:   {result['length']} chars")
            print()
            print(result["text"])

        elif args.command == "extract-scroll":
            result = browser.extract_with_scroll(
                max_scrolls=args.max_scrolls, max_chars=args.max_chars
            )
            print(f"Title: {result['title']}")
            print(f"URL:   {result['url']}")
            print(f"Len:   {result['length']} chars")
            print()
            print(result["text"])

        # Cloudflare bypass commands
        elif args.command == "cf-read":
            from cloudflare_bypass import try_cloudflare_bypass
            result = try_cloudflare_bypass(args.url, max_wait=30)
            if result:
                print(f"Title: {result['title']}")
                print(f"URL:   {result['url']}")
                print(f"Len:   {result['length']} chars")
                print()
                max_chars = args.max_chars if hasattr(args, 'max_chars') and args.max_chars else 15000
                print(result["text"][:max_chars])
            else:
                print("FAILED: Could not bypass Cloudflare. Try installing playwright.")
                sys.exit(1)

        elif args.command == "navigate-cf":
            result = browser.navigate_cf(args.url, max_wait=30)
            print(result)

        else:
            print(f"Unknown command: {args.command}")
            sys.exit(1)


if __name__ == "__main__":
    main()
