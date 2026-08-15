#!/usr/bin/env python3
"""
Browser Emulator Examples — current API (Playwright + search).

Run:  python3 examples.py
Each example is a standalone function; they launch a real headless Chromium,
so they need network access and a few seconds each.
"""

from browser_emulator import search, search_and_read, PlaywrightBrowser


def example_search_only():
    """Search without launching a browser (fast, cheap)."""
    print("=== Example: search only ===")
    results = search("OpenShift AI RHOAI installation", max_results=5, engine="searxng")
    for i, r in enumerate(results, 1):
        print(f"{i}. {r.get('title', '')}")
        print(f"   {r.get('href', '')}")
    return results


def example_search_and_read():
    """Search, then visit and scrape the top results in one call."""
    print("\n=== Example: search + read top results ===")
    readings = search_and_read(
        "Kubernetes operators explained",
        max_read=2,          # quality over quantity
        max_chars=12000,     # control output size
        engine="searxng",    # 70+ engines vs DDG
    )
    for r in readings:
        if r.get("success"):
            print(f"\n--- {r['article']['title']} ---")
            print(r["article"]["text"][:300], "...")
        else:
            print(f"failed: {r.get('url')} — {r.get('error')}")
    return readings


def example_manual_browser():
    """Manual control: navigate, wait for full load, extract content."""
    print("\n=== Example: manual browser control ===")
    with PlaywrightBrowser() as b:
        b.goto("https://en.wikipedia.org/wiki/Web_scraping").wait_network_idle()
        print("Title:", b.title())
        print("First 200 chars of body:", b.text()[:200])

        article = b.scrape_article(max_length=5000)
        print(f"\nScraped article: {article['length']} chars")
        print(article["text"][:300], "...")

        links = b.extract_links(max_links=5)
        print("\nSample links:")
        for l in links:
            print("  -", l["text"][:50], "->", l["href"][:60])
    return article


def example_interactions_and_screenshot():
    """Click, fill, and capture a screenshot of a simple public form."""
    print("\n=== Example: interactions + screenshot ===")
    with PlaywrightBrowser() as b:
        b.goto("https://example.com").wait_network_idle()
        print("Page title:", b.title())
        path = b.screenshot("/tmp/browser_emulator_example.png", full_page=True)
        print("Screenshot saved:", path)
    return path


if __name__ == "__main__":
    example_search_only()
    example_search_and_read()
    example_manual_browser()
    example_interactions_and_screenshot()
    print("\nAll examples complete.")
