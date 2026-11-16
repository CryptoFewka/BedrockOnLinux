"""The browser only ever receives http(s) links from downloaded content."""
# SPDX-License-Identifier: MIT

import unittest
from unittest import mock

from bol import gui


class OpenWebLinkTests(unittest.TestCase):
    def _open(self, url):
        with mock.patch.object(gui.QDesktopServices, "openUrl",
                               return_value=True) as open_url, \
             mock.patch.object(gui, "warn"):
            opened = gui._open_web_link(url)
        return opened, open_url

    def test_http_and_https_reach_the_desktop(self):
        for url in ("http://example.com/notes",
                    "https://github.com/Wyze3306/BedrockOnLinux/releases",
                    "HTTPS://example.com/upper-case-scheme"):
            with self.subTest(url=url):
                opened, open_url = self._open(url)
                self.assertTrue(opened)
                open_url.assert_called_once_with(url)

    def test_other_schemes_are_refused(self):
        # Changelog bodies and mod descriptions are network input, so a link
        # may name any scheme a desktop handler has registered.
        for url in ("file:///etc/passwd",
                    "smb://attacker/share",
                    "ssh://attacker/",
                    "javascript:alert(1)",
                    "steam://run/12345",
                    "data:text/html,<script>alert(1)</script>"):
            with self.subTest(url=url):
                opened, open_url = self._open(url)
                self.assertFalse(opened)
                open_url.assert_not_called()

    def test_empty_and_relative_links_are_refused(self):
        for url in ("", "   ", "example.com", "/tmp/local/file", "https://"):
            with self.subTest(url=url):
                opened, open_url = self._open(url)
                self.assertFalse(opened)
                open_url.assert_not_called()


class HrefTests(unittest.TestCase):
    def test_web_links_are_attribute_escaped(self):
        self.assertEqual(gui._href('https://example.com/?a=1&b="x"'),
                         "https://example.com/?a=1&amp;b=&quot;x&quot;")

    def test_non_web_links_become_empty(self):
        for url in ("file:///etc/passwd", "javascript:alert(1)", "", None):
            with self.subTest(url=url):
                self.assertEqual(gui._href(url), "")


if __name__ == "__main__":
    unittest.main()
