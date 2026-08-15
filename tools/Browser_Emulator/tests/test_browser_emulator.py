#!/usr/bin/env python3

import unittest
import sys
import os
import unittest.mock as mock

# Add parent directory so we can import browser_emulator directly
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from browser_emulator import clean_text


class TestCleanText(unittest.TestCase):
    """Test text cleaning and truncation."""

    def test_empty_returns_empty(self):
        self.assertEqual(clean_text(""), "")

    def test_none_returns_empty(self):
        self.assertEqual(clean_text(None), "")

    def test_collapsed_whitespace(self):
        result = clean_text("hello   world\n\n  newlines  everywhere  ")
        self.assertNotIn("  ", result)
        self.assertIn("hello", result)

    def test_truncation(self):
        long_text = "word " * 3000
        result = clean_text(long_text, max_length=100)
        self.assertIn("[truncated]", result)

    def test_no_truncation_when_short(self):
        short = "a small amount of text"
        result = clean_text(short, max_length=1000)
        self.assertEqual(result, short)

    def test_strips_lines(self):
        multi = "\n\nline1\n   \nline2\n\n"
        result = clean_text(multi)
        self.assertNotIn("   ", result)


if __name__ == "__main__":
    unittest.main()
