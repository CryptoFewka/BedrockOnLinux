"""Tests for bol.util's screen-geometry helper."""
# SPDX-License-Identifier: MIT

import unittest
from unittest import mock

from bol import util


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
