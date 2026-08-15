"""Browser Next — LLM-native browser automation for opencode."""

from browser_next import BrowserSession, AGENT_BROWSER
from browser_next_utils import (
    is_private_url,
    route_url,
    find_cdp_endpoint,
    launch_chrome_cdp,
    extract_article_js,
    extract_with_scroll_js,
    get_random_ua,
    get_anti_detection_script,
    save_cookies,
    load_cookies,
    save_storage,
    load_storage,
    list_profiles,
    search,
)
from cloudflare_bypass import (
    is_cloudflare_challenge,
    PlaywrightCloudflareBrowser,
    try_cloudflare_bypass,
)

__all__ = [
    "BrowserSession",
    "AGENT_BROWSER",
    "is_private_url",
    "route_url",
    "find_cdp_endpoint",
    "launch_chrome_cdp",
    "extract_article_js",
    "extract_with_scroll_js",
    "get_random_ua",
    "get_anti_detection_script",
    "save_cookies",
    "load_cookies",
    "save_storage",
    "load_storage",
    "list_profiles",
    "search",
    "is_cloudflare_challenge",
    "PlaywrightCloudflareBrowser",
    "try_cloudflare_bypass",
]
