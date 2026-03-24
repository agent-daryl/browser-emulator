#!/usr/bin/env python3
"""
Browser Emulator - Mimics human Chromium browser behavior to avoid bot detection
"""

import random
import time
import requests
from urllib.parse import urlparse
from typing import Optional, Dict, Any

class BrowserEmulator:
    """Simulates a human Chromium browser with realistic behavior patterns"""
    
    # Realistic user agents (Chromium-based browsers)
    USER_AGENTS = [
        # Chrome on Windows 11
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        # Chrome on macOS
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        # Chrome on Linux
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        # Edge on Windows
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edge/123.0.0.0",
    ]
    
    # Realistic accept headers
    ACCEPT_HEADERS = [
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    ]
    
    # Realistic accept-language headers
    ACCEPT_LANGUAGES = [
        "en-US,en;q=0.9",
        "en-GB,en;q=0.8,en-US;q=0.6",
        "en-US,en;q=0.9,es;q=0.8,pt;q=0.7",
    ]
    
    def __init__(self, headless: bool = False):
        self.session = requests.Session()
        self.headless = headless
        self.history: list = []
        self.last_request_time: float = 0
        self.min_delay = 0.5  # Minimum delay between requests
        self.max_delay = 3.0  # Maximum delay between requests
        
    def _get_random_headers(self, url: str) -> Dict[str, str]:
        """Generate realistic browser headers"""
        parsed = urlparse(url)
        
        headers = {
            "User-Agent": random.choice(self.USER_AGENTS),
            "Accept": random.choice(self.ACCEPT_HEADERS),
            "Accept-Language": random.choice(self.ACCEPT_LANGUAGES),
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Sec-Gpc": "1",
            "Cache-Control": "max-age=0",
            "Referer": random.choice([parsed.netloc, "https://www.google.com/", "https://www.bing.com/", ""]),
        }
        
        return headers
    
    def _human_delay(self):
        """Simulate human thinking and browsing delays"""
        # Exponential-ish delay pattern like human behavior
        base_delay = random.uniform(self.min_delay, self.max_delay)
        
        # Sometimes browse multiple pages (simulate following links)
        if random.random() < 0.3:
            follow_delay = random.uniform(2.0, 8.0)
            time.sleep(follow_delay)
        
        time.sleep(base_delay)
    
    def _simulate_browsing_behavior(self, url: str):
        """Simulate realistic browser behavior before making request"""
        parsed = urlparse(url)
        
        # Simulate scrolling (not actually done, but we track it)
        scroll_positions = [0, 25, 50, 75, 100]
        for _ in scroll_positions:
            time.sleep(random.uniform(0.1, 0.3))
        
        # Simulate form focus/typing if URL suggests interaction
        if any(keyword in url.lower() for keyword in ["search", "query", "login", "form"]):
            time.sleep(random.uniform(1.0, 3.0))
        
        self.history.append({
            "url": url,
            "timestamp": time.time(),
            "user_agent": self.session.headers.get("User-Agent"),
        })
    
    def get(self, url: str, timeout: int = 30) -> Optional[requests.Response]:
        """Perform a GET request with human-like behavior"""
        try:
            # Wait before making request
            time_since_last = time.time() - self.last_request_time
            if time_since_last < self.min_delay:
                time.sleep(random.uniform(self.min_delay, self.max_delay))
            
            # Set realistic headers
            headers = self._get_random_headers(url)
            self.session.headers.update(headers)
            
            # Simulate browsing behavior
            self._simulate_browsing_behavior(url)
            
            # Make request
            response = self.session.get(
                url,
                headers=headers,
                timeout=timeout,
                verify=True,
            )
            
            self.last_request_time = time.time()
            
            # Follow redirects realistically (like a browser would)
            if response.status_code in [301, 302, 303, 307, 308]:
                redirect_url = response.headers.get("Location", "")
                if redirect_url:
                    time.sleep(random.uniform(0.3, 1.0))
                    return self.get(redirect_url)
            
            return response
            
        except requests.exceptions.RequestException as e:
            print(f"Request failed: {e}")
            return None
    
    def click_link(self, url: str, link_url: str) -> Optional[requests.Response]:
        """Simulate clicking a link with realistic behavior"""
        # Simulate hovering over link
        time.sleep(random.uniform(0.2, 0.8))
        
        # Verify target
        time.sleep(random.uniform(0.1, 0.3))
        
        # Click with slight delay after hover
        time.sleep(random.uniform(0.3, 0.6))
        
        return self.get(link_url)
    
    def scroll_page(self, url: str, duration: float = 5.0):
        """Simulate scrolling through a page"""
        start_time = time.time()
        while time.time() - start_time < duration:
            scroll_time = random.uniform(0.5, 2.0)
            time.sleep(scroll_time)
        self._human_delay()
    
    def form_fill(self, url: str, input_delay: float = 0.3) -> float:
        """Simulate filling out a form with realistic typing delays"""
        total_time = 0
        # Simulate typing each character with variable delays
        char_count = random.randint(10, 50)
        for _ in range(char_count):
            time.sleep(input_delay + random.uniform(0.05, 0.15))
            total_time += input_delay
        return total_time
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
    
    def close(self):
        """Clean up session"""
        self.session.close()
