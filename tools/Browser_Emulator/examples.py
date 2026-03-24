#!/usr/bin/env python3
"""
Browser Emulator Examples - Demonstrates how to use the browser emulator
"""

from browser_emulator import BrowserEmulator

def example_basic_usage():
    """Basic usage example"""
    print("=== Example: Basic Usage ===")
    
    with BrowserEmulator() as browser:
        url = "https://example.com"
        print(f"Visiting: {url}")
        
        response = browser.get(url)
        
        if response:
            print(f"Status: {response.status_code}")
            print(f"Content length: {len(response.text)} chars")
        else:
            print("Failed to load page")

def example_search_engine():
    """Simulate searching through Google"""
    print("\n=== Example: Search Simulation ===")
    
    with BrowserEmulator() as browser:
        search_queries = [
            "latest stock market news",
            "iran conflict updates",
            "oil prices today",
        ]
        
        for query in search_queries:
            print(f"\nSearching for: '{query}'")
            url = f"https://www.google.com/search?q={query.replace(' ', '+')}"
            
            response = browser.get(url)
            
            if response:
                print(f"Search results page loaded: {len(response.text)} chars")
            time.sleep(1)  # Simulate reading results

def example_mimicking_human_behavior():
    """Demonstrates human-like behavior simulation"""
    print("\n=== Example: Human Behavior Simulation ===")
    
    with BrowserEmulator() as browser:
        url = "https://httpbin.org/html"
        print(f"Visiting: {url}")
        
        # Simulate various human behaviors
        print("Simulating scroll behavior...")
        browser.scroll_page(url, duration=3.0)
        
        print("Making request after browsing behavior...")
        response = browser.get(url)
        
        if response:
            print(f"Response status: {response.status_code}")

def example_form_interaction():
    """Simulate form filling"""
    print("\n=== Example: Form Interaction ===")
    
    with BrowserEmulator() as browser:
        url = "https://httpbin.org/forms/post"
        print(f"Simulating form fill on: {url}")
        
        fill_time = browser.form_fill(url)
        print(f"Form fill simulation took: {fill_time:.2f} seconds")
        
        response = browser.get(url)
        if response:
            print(f"Form page loaded: {response.status_code}")

def example_link_clicking():
    """Simulate clicking links on a page"""
    print("\n=== Example: Link Clicking ===")
    
    with BrowserEmulator() as browser:
        base_url = "https://example.com"
        print(f"Starting at: {base_url}")
        
        response = browser.get(base_url)
        
        if response and response.status_code == 200:
            # Simulate clicking a link
            print("Simulating link click behavior...")
            response = browser.click_link(base_url, base_url)
            
            if response:
                print(f"Successfully navigated to: {response.url}")

def example_rate_limiting():
    """Demonstrates rate limiting and pacing"""
    print("\n=== Example: Rate Limiting ===")
    
    with BrowserEmulator() as browser:
        browser.min_delay = 1.0
        browser.max_delay = 4.0
        
        urls = [
            "https://example.com/page1",
            "https://example.com/page2", 
            "https://example.com/page3",
        ]
        
        for url in urls:
            print(f"Requesting: {url}")
            start_time = time.time()
            
            response = browser.get(url)
            
            elapsed = time.time() - start_time
            print(f"Request took: {elapsed:.2f} seconds")

def example_with_custom_headers():
    """Demonstrates setting custom headers"""
    print("\n=== Example: Custom Headers ===")
    
    browser = BrowserEmulator()
    browser.session.headers.update({
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    })
    
    with browser:
        url = "https://httpbin.org/headers"
        print(f"Requesting: {url}")
        
        response = browser.get(url)
        
        if response:
            print("Response received")

if __name__ == "__main__":
    import time
    
    print("Browser Emulator Examples")
    print("=" * 50)
    
    example_basic_usage()
    # example_search_engine()  # May need API key or have rate limits
    example_mimicking_human_behavior()
    example_form_interaction()
    example_link_clicking()
    example_rate_limiting()
    example_with_custom_headers()
    
    print("\n" + "=" * 50)
    print("All examples completed!")
