"""This fork consumes only its own engine releases, never upstream's binaries."""
# SPDX-License-Identifier: MIT

import unittest

from bol import config, feed


class ForkPostureTests(unittest.TestCase):
    def test_downloads_come_from_this_fork(self):
        self.assertEqual(config.WINEGDK_PREBUILT_REPO, "CryptoFewka/BedrockOnLinux")
        # Update notices still track upstream releases.
        self.assertEqual(config.SELF_REPO, "Wyze3306/BedrockOnLinux")

    def test_feed_pins_cannot_redirect_downloads_to_upstream(self):
        upstream = ("https://github.com/Wyze3306/BedrockOnLinux/releases/download/"
                    "xodus-r-p15/xodus-cli-r-p15.tar.gz")
        ours = upstream.replace("Wyze3306", "CryptoFewka")
        self.assertIsNone(feed._ASSET_URL.match(upstream))
        self.assertIsNotNone(feed._ASSET_URL.match(ours))


if __name__ == "__main__":
    unittest.main()
