# Browser Emulator

A browser emulator that mimics human Chromium browser behavior to avoid bot detection.

## Features

- **Realistic User Agents**: Rotates through real Chromium browser user agents
- **Human-like Delays**: Simulates thinking and browsing delays between actions
- **Behavior Patterns**: Scrolls, form filling, and link clicking simulation
- **Header Spoofing**: Sets realistic browser headers including Accept, Accept-Language, Sec-Fetch
- **Session Management**: Maintains cookies and headers across requests
- **Redirect Following**: Handles redirects like a real browser

## Installation

```bash
pip install -r requirements.txt
```

## Usage

### Basic Example

```python
from browser_emulator import BrowserEmulator

with BrowserEmulator() as browser:
    response = browser.get("https://example.com")
    if response:
        print(response.status_code)
        print(response.text[:200])
```

### Human-like Behavior

```python
browser = BrowserEmulator()

# Simulate scrolling
browser.scroll_page(url, duration=5.0)

# Simulate form filling
fill_time = browser.form_fill(url)

# Click links realistically
response = browser.click_link(page_url, link_url)
```

### Custom Delay Settings

```python
browser = BrowserEmulator()
browser.min_delay = 1.0  # Minimum seconds between requests
browser.max_delay = 4.0  # Maximum seconds between requests
```

## Headers Added

The emulator automatically adds these headers to appear more human-like:

- User-Agent (rotates from real Chromium browsers)
- Accept (varied HTML/XHTML/XML options)
- Accept-Language (English variants)
- Accept-Encoding (modern compression)
- Sec-Fetch-* headers (browser security headers)
- Referer (randomized referrer)

## Files

- `browser_emulator.py` - Main emulator class
- `examples.py` - Usage examples
- `requirements.txt` - Dependencies

## Differences from Bot Detection

This emulator addresses common bot detection indicators:

1. **Timing**: Realistic delays between requests (not instant)
2. **Headers**: Complete, varied browser headers
3. **Behavior**: Simulates scrolling, hovering, and form filling
4. **User Agent**: Rotates through real Chromium browsers
5. **Sequence**: Follows realistic navigation patterns

## Notes

- Some websites may still detect and block requests
-Consider adding proxy rotation for high-volume scraping
- The emulator simulates behavior but doesn't render JavaScript
