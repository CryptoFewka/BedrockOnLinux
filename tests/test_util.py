"""Tests for bol.util's screen-geometry helper."""
# SPDX-License-Identifier: MIT

import io
import json
import unittest
from unittest import mock

from bol import util


class HttpJsonNoCredentialTests(unittest.TestCase):
    """http_json fetches public endpoints (GitHub releases, Minecraft feedback)
    from more than one host, so it must never attach a credential, even when
    GITHUB_TOKEN is present in the environment."""

    def _captured_headers(self, url):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured.update({k.lower(): v for k, v in req.header_items()})
            return io.BytesIO(json.dumps({"ok": True}).encode())

        with mock.patch.dict("os.environ", {"GITHUB_TOKEN": "SENTINEL_SECRET"}), \
                mock.patch("urllib.request.urlopen", fake_urlopen):
            util.http_json(url)
        return captured

    def test_no_authorization_for_github(self):
        headers = self._captured_headers("https://api.github.com/repos/x/y/releases")
        self.assertNotIn("authorization", headers)

    def test_no_authorization_for_non_github(self):
        headers = self._captured_headers(
            "https://feedback.minecraft.net/api/v2/help_center/en-us/sections/1/articles.json")
        self.assertNotIn("authorization", headers)


class ScreenWHTests(unittest.TestCase):
    def test_delegates_to_x11_primary_output_size(self):
        with mock.patch("bol.x11.primary_output_size",
                         return_value=("1920", "1080")) as mocked:
            self.assertEqual(util._screen_wh(), ("1920", "1080"))
        mocked.assert_called_once_with(None)

    def test_passes_runner_through(self):
        runner = object()
        with mock.patch("bol.x11.primary_output_size",
                         return_value=None) as mocked:
            self.assertIsNone(util._screen_wh(runner=runner))
        mocked.assert_called_once_with(runner)


if __name__ == "__main__":
    unittest.main()
