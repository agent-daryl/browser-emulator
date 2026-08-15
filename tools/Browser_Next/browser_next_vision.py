#!/usr/bin/env python3
"""
Browser Vision — Screenshot + AI vision analysis for Browser Next.
Uses Qwen's multimodal weights via image_processor for on-device analysis.

Usage:
    from browser_next_vision import analyze_screenshot
    with BrowserSession() as b:
        b.navigate("https://example.com")
        result = analyze_screenshot(b, "What does this page show?")
"""

import base64
import json
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Optional

# Ensure sibling imports work
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from browser_next import BrowserSession


# ---------------------------------------------------------------------------
# Vision analysis via image_processor (Qwen multimodal)
# ---------------------------------------------------------------------------

def _find_image_processor() -> Path:
    """Find the image processor script."""
    tools_dir = Path(__file__).parent.parent.parent
    ip_path = tools_dir / "image_processor" / "image_processor.py"
    if ip_path.exists():
        return ip_path
    # Try alternate location
    alt_path = tools_dir / "tools" / "image_processor" / "image_processor.py"
    if alt_path.exists():
        return alt_path
    raise RuntimeError(
        "image_processor.py not found. Expected at tools/image_processor/image_processor.py"
    )


def analyze_screenshot(
    browser: BrowserSession,
    question: str,
    full_page: bool = False,
    annotate: bool = True,
) -> str:
    """
    Take a screenshot and analyze it with Qwen's vision model.

    Args:
        browser: Active BrowserSession.
        question: What to ask the vision model about the screenshot.
        full_page: Full page screenshot vs viewport.
        annotate: Add numbered labels on interactive elements.

    Returns:
        Vision model's text response.

    Note:
        GPU weight swap causes 1-3 min delay on first call.
    """
    ip_path = _find_image_processor()

    # Take screenshot
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        screenshot_path = tmp.name

    try:
        browser.screenshot(screenshot_path, full_page=full_page, annotate=annotate)

        # Run image processor
        result = subprocess.run(
            ["python3", str(ip_path), screenshot_path, question],
            capture_output=True,
            text=True,
            timeout=300,  # 5 min timeout (GPU swap can be slow)
        )

        if result.returncode != 0:
            stderr = result.stderr.strip()
            return f"Vision analysis failed: {stderr}"

        return result.stdout.strip()
    finally:
        # Cleanup temp screenshot
        Path(screenshot_path).unlink(missing_ok=True)


def analyze_url(
    url: str,
    question: str,
    full_page: bool = False,
    annotate: bool = True,
    wait_network_idle: bool = True,
) -> str:
    """
    Navigate to URL, screenshot, and analyze with vision.

    Convenience function that manages the full browser lifecycle.

    Args:
        url: URL to navigate to.
        question: What to ask the vision model.
        full_page: Full page screenshot.
        annotate: Add numbered labels.
        wait_network_idle: Wait for network idle before screenshot.

    Returns:
        Vision model's text response.
    """
    with BrowserSession() as browser:
        browser.navigate(url, wait_network_idle=wait_network_idle)
        return analyze_screenshot(browser, question, full_page=full_page, annotate=annotate)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Browser Vision — Screenshot + AI analysis")
    parser.add_argument("url", help="URL to analyze")
    parser.add_argument("question", help="Question to ask about the page", nargs="?")
    parser.add_argument("--full", action="store_true", help="Full page screenshot")
    args = parser.parse_args()

    if not args.question:
        args.question = "What does this page show?"

    print(f"Analyzing {args.url}...")
    print(f"Question: {args.question}")
    print("(Note: GPU weight swap may take 1-3 minutes on first call)")
    print()

    result = analyze_url(args.url, args.question, full_page=args.full)
    print(result)
