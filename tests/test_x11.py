"""Tests for bol.x11's primary-monitor size lookup."""
# SPDX-License-Identifier: MIT

import subprocess
import sys
import types
import unittest
from types import SimpleNamespace
from unittest import mock

from bol import x11


def result(stdout="", returncode=0):
    return SimpleNamespace(stdout=stdout, returncode=returncode)


DUAL_MONITOR_PRIMARY_SECOND = (
    "Monitors: 2\n"
    " 0: +HDMI-A-0 1920/530x1080/300+1920+0  HDMI-A-0\n"
    " 1: +*HDMI-A-1 1920/530x1080/300+0+0  HDMI-A-1\n"
)

SINGLE_MONITOR = (
    "Monitors: 1\n"
    " 0: +*eDP-1 1920/340x1080/190+0+0  eDP-1\n"
)

NO_PRIMARY_FLAGGED = (
    "Monitors: 2\n"
    " 0: +HDMI-A-0 1920/530x1080/300+0+0  HDMI-A-0\n"
    " 1: +HDMI-A-1 2560/597x1440/336+1920+0  HDMI-A-1\n"
)

BARE_XRANDR_COMBINED = (
    "Screen 0: minimum 320 x 200, current 3840 x 1080, maximum 16384 x "
    "16384\n"
)


class XrandrCliFallbackTests(unittest.TestCase):
    """Exercises _primary_via_xrandr_cli directly, and through
    primary_output_size() with the python-xlib path forced off (it isn't
    installed on the test host either way)."""

    def setUp(self):
        patcher = mock.patch.object(x11.shutil, "which", return_value="xrandr")
        patcher.start()
        self.addCleanup(patcher.stop)
        have_patcher = mock.patch.object(x11, "have", return_value=False)
        have_patcher.start()
        self.addCleanup(have_patcher.stop)

    def test_dual_monitor_uses_primary_not_combined_root(self):
        def runner(args, **_kwargs):
            self.assertIn("--listmonitors", args)
            return result(DUAL_MONITOR_PRIMARY_SECOND)

        self.assertEqual(x11.primary_output_size(runner=runner),
                          ("1920", "1080"))

    def test_single_monitor(self):
        def runner(args, **_kwargs):
            return result(SINGLE_MONITOR)

        self.assertEqual(x11.primary_output_size(runner=runner),
                          ("1920", "1080"))

    def test_no_primary_flagged_falls_back_to_first_listed_monitor(self):
        def runner(args, **_kwargs):
            return result(NO_PRIMARY_FLAGGED)

        self.assertEqual(x11.primary_output_size(runner=runner),
                          ("1920", "1080"))

    def test_listmonitors_unsupported_falls_back_to_combined_root(self):
        def runner(args, **_kwargs):
            if "--listmonitors" in args:
                return result(returncode=1)
            return result(BARE_XRANDR_COMBINED)

        self.assertEqual(x11.primary_output_size(runner=runner),
                          ("3840", "1080"))

    def test_xrandr_missing_returns_none(self):
        with mock.patch.object(x11.shutil, "which", return_value=None):
            self.assertIsNone(x11.primary_output_size())

    def test_xrandr_timeout_returns_none(self):
        def runner(args, **_kwargs):
            raise subprocess.TimeoutExpired("xrandr", 5)

        self.assertIsNone(x11.primary_output_size(runner=runner))

    def test_unparseable_output_returns_none(self):
        def runner(args, **_kwargs):
            return result("nothing useful here\n")

        self.assertIsNone(x11.primary_output_size(runner=runner))


class FakeMonitor:
    def __init__(self, primary, width, height):
        self.primary = primary
        self.width_in_pixels = width
        self.height_in_pixels = height


class FakeMonitorsReply:
    def __init__(self, monitors):
        self.monitors = monitors


class FakeRoot:
    def __init__(self, monitors=None, supports_get_monitors=True,
                 get_monitors_raises=None):
        if supports_get_monitors:
            self._monitors = monitors or []
            self._get_monitors_raises = get_monitors_raises
            self.xrandr_get_monitors = self._get_monitors

    def _get_monitors(self, is_active=True):
        if self._get_monitors_raises is not None:
            raise self._get_monitors_raises
        return FakeMonitorsReply(self._monitors)


class FakeScreen:
    def __init__(self, root):
        self.root = root


class FakeDisplay:
    fail_to_connect = None
    close_raises = None
    root = None

    def __init__(self):
        if type(self).fail_to_connect is not None:
            raise type(self).fail_to_connect
        self.closed = False

    def screen(self):
        return FakeScreen(type(self).root)

    def close(self):
        self.closed = True
        if type(self).close_raises is not None:
            raise type(self).close_raises


class XlibDisplayError(Exception):
    pass


class XlibXError(Exception):
    pass


class XlibConnectionClosedError(Exception):
    pass


class XlibPathTests(unittest.TestCase):
    """Exercises _primary_via_xlib by injecting fake Xlib.display/Xlib.error
    modules. python-xlib is not installed on the test host, so this is the
    only way to cover the primary code path without a real X server."""

    def setUp(self):
        FakeDisplay.fail_to_connect = None
        FakeDisplay.close_raises = None
        FakeDisplay.root = None

        fake_xlib = types.ModuleType("Xlib")
        fake_display_mod = types.ModuleType("Xlib.display")
        fake_error_mod = types.ModuleType("Xlib.error")
        fake_display_mod.Display = FakeDisplay
        fake_error_mod.DisplayError = XlibDisplayError
        fake_error_mod.XError = XlibXError
        fake_error_mod.ConnectionClosedError = XlibConnectionClosedError
        fake_xlib.display = fake_display_mod
        fake_xlib.error = fake_error_mod

        modules_patcher = mock.patch.dict(sys.modules, {
            "Xlib": fake_xlib,
            "Xlib.display": fake_display_mod,
            "Xlib.error": fake_error_mod,
        })
        modules_patcher.start()
        self.addCleanup(modules_patcher.stop)

        have_patcher = mock.patch.object(x11, "have", return_value=True)
        have_patcher.start()
        self.addCleanup(have_patcher.stop)

    def test_primary_flagged_monitor_wins_over_first(self):
        FakeDisplay.root = FakeRoot(monitors=[
            FakeMonitor(False, "2560", "1440"),
            FakeMonitor(True, "1920", "1080"),
        ])
        self.assertEqual(x11._primary_via_xlib(), ("1920", "1080"))

    def test_no_primary_flagged_falls_back_to_first_monitor(self):
        FakeDisplay.root = FakeRoot(monitors=[
            FakeMonitor(False, "1920", "1080"),
            FakeMonitor(False, "2560", "1440"),
        ])
        self.assertEqual(x11._primary_via_xlib(), ("1920", "1080"))

    def test_empty_monitor_list_returns_none(self):
        FakeDisplay.root = FakeRoot(monitors=[])
        self.assertIsNone(x11._primary_via_xlib())

    def test_pre_randr_1_5_server_returns_none(self):
        FakeDisplay.root = FakeRoot(supports_get_monitors=False)
        self.assertIsNone(x11._primary_via_xlib())

    def test_connection_failure_returns_none(self):
        FakeDisplay.fail_to_connect = XlibDisplayError("no display")
        self.assertIsNone(x11._primary_via_xlib())

    def test_connection_dropped_mid_query_returns_none(self):
        # Issue: a ConnectionClosedError (dropped X connection, e.g. SSH
        # X-forwarding cut or the X server restarting mid-request) is
        # neither an XError nor an OSError; it must still be caught rather
        # than crash the caller.
        FakeDisplay.root = FakeRoot(
            get_monitors_raises=XlibConnectionClosedError("gone"))
        self.assertIsNone(x11._primary_via_xlib())

    def test_close_raising_does_not_mask_a_successful_result(self):
        FakeDisplay.root = FakeRoot(monitors=[FakeMonitor(True, "1920", "1080")])
        FakeDisplay.close_raises = XlibConnectionClosedError("gone on close")
        self.assertEqual(x11._primary_via_xlib(), ("1920", "1080"))

    def test_display_is_closed_after_use(self):
        FakeDisplay.root = FakeRoot(monitors=[FakeMonitor(True, "1920", "1080")])
        seen = {}

        real_init = FakeDisplay.__init__

        def tracking_init(self):
            real_init(self)
            seen["instance"] = self

        with mock.patch.object(FakeDisplay, "__init__", tracking_init):
            x11._primary_via_xlib()
        self.assertTrue(seen["instance"].closed)

    def test_xlib_path_wins_over_cli_fallback(self):
        FakeDisplay.root = FakeRoot(monitors=[FakeMonitor(True, "1920", "1080")])

        def must_not_run(*_args, **_kwargs):
            raise AssertionError("CLI fallback must not run when Xlib succeeds")

        with mock.patch.object(x11.shutil, "which", return_value="xrandr"):
            self.assertEqual(
                x11.primary_output_size(runner=must_not_run),
                ("1920", "1080"))


if __name__ == "__main__":
    unittest.main()
