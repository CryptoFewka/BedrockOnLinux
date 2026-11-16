"""Tests for bol.gui's pre-QApplication stale-XWayland-display recovery.

Qt's xcb platform plugin aborts the process natively on a failed display
connection, so unlike the old Tk-based GUI there is no catchable exception to
retry on. bol.gui._resolve_gui_display therefore probes candidate X11 sockets
*before* QApplication is ever constructed, repointing environ['DISPLAY'] at a
live, user-owned socket when the current one is stale.
"""
# SPDX-License-Identifier: MIT

import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bol import gui


class _FakeSocketDir:
    """A temp dir of AF_UNIX sockets standing in for /tmp/.X11-unix."""

    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name)
        self._listeners = []

    def close(self):
        for sock in self._listeners:
            try:
                sock.close()
            except OSError:
                pass
        self._tmp.cleanup()

    def bind_socket(self, number, listen=True):
        """Create X<number> under the dir; listen() makes it a live socket."""
        path = self.path / f"X{number}"
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(path))
        if listen:
            sock.listen(1)
            self._listeners.append(sock)
        else:
            sock.close()
        return path

    def touch_stale_socket_file(self, number):
        """A socket-mode file with nothing listening on it (orphaned)."""
        return self.bind_socket(number, listen=False)


class ResolveGuiDisplayTests(unittest.TestCase):
    def setUp(self):
        self.dir = _FakeSocketDir()
        self.addCleanup(self.dir.close)

    def test_no_wayland_display_leaves_display_untouched(self):
        environ = {"DISPLAY": ":0"}
        result = gui._resolve_gui_display(
            environ, socket_dir=self.dir.path, uid=os.getuid())
        self.assertEqual(result, ":0")
        self.assertEqual(environ["DISPLAY"], ":0")

    def test_live_current_display_is_kept(self):
        self.dir.bind_socket(0)
        environ = {"WAYLAND_DISPLAY": "wayland-1", "DISPLAY": ":0"}
        result = gui._resolve_gui_display(
            environ, socket_dir=self.dir.path, uid=os.getuid())
        self.assertEqual(result, ":0")
        self.assertEqual(environ["DISPLAY"], ":0")

    def test_stale_display_recovers_to_owned_xwayland_socket(self):
        self.dir.touch_stale_socket_file(0)
        self.dir.bind_socket(4)
        environ = {"WAYLAND_DISPLAY": "wayland-1", "DISPLAY": ":0"}
        result = gui._resolve_gui_display(
            environ, socket_dir=self.dir.path, uid=os.getuid())
        self.assertEqual(result, ":4")
        self.assertEqual(environ["DISPLAY"], ":4")

    def test_missing_display_can_recover_without_inventing_a_number(self):
        self.dir.bind_socket(4)
        environ = {"WAYLAND_DISPLAY": "wayland-1"}
        result = gui._resolve_gui_display(
            environ, socket_dir=self.dir.path, uid=os.getuid())
        self.assertEqual(result, ":4")
        self.assertEqual(environ["DISPLAY"], ":4")

    def test_no_live_socket_leaves_display_unchanged(self):
        self.dir.touch_stale_socket_file(0)
        environ = {"WAYLAND_DISPLAY": "wayland-1", "DISPLAY": ":0"}
        result = gui._resolve_gui_display(
            environ, socket_dir=self.dir.path, uid=os.getuid())
        self.assertEqual(result, ":0")
        self.assertEqual(environ["DISPLAY"], ":0")

    def test_present_owned_display_preserves_authentication_failure(self):
        # A socket that is live and owned by us is never bypassed even if it
        # would otherwise refuse the connection for a different reason (e.g.
        # Xauthority) — only an actually-dead socket triggers recovery.
        self.dir.bind_socket(0)
        self.dir.bind_socket(4)
        environ = {"WAYLAND_DISPLAY": "wayland-1", "DISPLAY": ":0"}
        result = gui._resolve_gui_display(
            environ, socket_dir=self.dir.path, uid=os.getuid())
        self.assertEqual(result, ":0")
        self.assertEqual(environ["DISPLAY"], ":0")

    def test_socket_owned_by_another_uid_is_skipped(self):
        self.dir.touch_stale_socket_file(0)
        live_path = self.dir.bind_socket(4)
        with mock.patch.object(gui, "_owned_x11_socket_displays",
                                return_value=()):
            environ = {"WAYLAND_DISPLAY": "wayland-1", "DISPLAY": ":0"}
            result = gui._resolve_gui_display(
                environ, socket_dir=self.dir.path, uid=os.getuid())
        self.assertEqual(result, ":0")
        self.assertEqual(environ["DISPLAY"], ":0")
        self.assertTrue(live_path.exists())

    def test_attempted_list_records_every_candidate_tried(self):
        self.dir.touch_stale_socket_file(0)
        self.dir.bind_socket(4)
        environ = {"WAYLAND_DISPLAY": "wayland-1", "DISPLAY": ":0"}
        attempted = []
        gui._resolve_gui_display(
            environ, socket_dir=self.dir.path, uid=os.getuid(),
            attempted=attempted)
        self.assertIn(":0", attempted)
        self.assertIn(":4", attempted)


class OwnedX11SocketDisplaysTests(unittest.TestCase):
    def setUp(self):
        self.dir = _FakeSocketDir()
        self.addCleanup(self.dir.close)

    def test_lists_only_sockets_owned_by_uid_sorted_numerically(self):
        self.dir.bind_socket(10)
        self.dir.bind_socket(2)
        self.dir.bind_socket(0)
        result = gui._owned_x11_socket_displays(
            self.dir.path, uid=os.getuid())
        self.assertEqual(result, (":0", ":2", ":10"))

    def test_ignores_non_socket_files(self):
        (self.dir.path / "X99").write_text("not a socket")
        result = gui._owned_x11_socket_displays(
            self.dir.path, uid=os.getuid())
        self.assertEqual(result, ())

    def test_ignores_sockets_owned_by_other_uid(self):
        self.dir.bind_socket(0)
        result = gui._owned_x11_socket_displays(
            self.dir.path, uid=os.getuid() + 12345)
        self.assertEqual(result, ())

    def test_missing_directory_returns_empty(self):
        result = gui._owned_x11_socket_displays(
            self.dir.path / "does-not-exist", uid=os.getuid())
        self.assertEqual(result, ())


class X11SocketIsLiveTests(unittest.TestCase):
    def setUp(self):
        self.dir = _FakeSocketDir()
        self.addCleanup(self.dir.close)

    def test_true_for_a_listening_socket(self):
        self.dir.bind_socket(0)
        self.assertTrue(gui._x11_socket_is_live(self.dir.path, ":0"))

    def test_false_for_an_orphaned_socket_file(self):
        self.dir.touch_stale_socket_file(0)
        self.assertFalse(gui._x11_socket_is_live(self.dir.path, ":0"))

    def test_false_for_a_missing_socket(self):
        self.assertFalse(gui._x11_socket_is_live(self.dir.path, ":7"))

    def test_false_for_malformed_display_strings(self):
        self.dir.bind_socket(0)
        for bad in ("", "not-a-display", "0", None):
            with self.subTest(bad=bad):
                self.assertFalse(
                    gui._x11_socket_is_live(self.dir.path, bad))

    def test_handles_display_with_screen_suffix(self):
        self.dir.bind_socket(0)
        self.assertTrue(gui._x11_socket_is_live(self.dir.path, ":0.0"))


if __name__ == "__main__":
    unittest.main()
