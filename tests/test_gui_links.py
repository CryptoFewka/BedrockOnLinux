"""The browser only ever receives http(s) links from downloaded content."""
# SPDX-License-Identifier: MIT

import unittest
from unittest import mock

from bol import gui


class OpenWebUrlTests(unittest.TestCase):
    def _open(self, url):
        with mock.patch.object(gui.subprocess, "Popen") as popen:
            opened = gui._open_web_url(url)
        return opened, popen

    def test_http_and_https_reach_xdg_open(self):
        for url in ("http://example.com/notes",
                    "https://github.com/Wyze3306/BedrockOnLinux/releases",
                    "HTTPS://example.com/upper-case-scheme"):
            with self.subTest(url=url):
                opened, popen = self._open(url)
                self.assertTrue(opened)
                popen.assert_called_once()
                self.assertEqual(popen.call_args[0][0], ["xdg-open", url])

    def test_other_schemes_are_refused(self):
        # A changelog body and the device-code response are network input, so a
        # link may name any scheme a desktop handler has registered.
        for url in ("file:///etc/passwd",
                    "smb://attacker/share",
                    "ssh://attacker/",
                    "javascript:alert(1)",
                    "steam://run/12345",
                    "data:text/html,<script>alert(1)</script>"):
            with self.subTest(url=url):
                opened, popen = self._open(url)
                self.assertFalse(opened)
                popen.assert_not_called()

    def test_empty_and_relative_links_are_refused(self):
        for url in ("", "   ", "example.com", "/tmp/local/file"):
            with self.subTest(url=url):
                opened, popen = self._open(url)
                self.assertFalse(opened)
                popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
