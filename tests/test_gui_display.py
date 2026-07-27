"""Deterministic tests for Wayland/XWayland GUI display recovery."""
# SPDX-License-Identifier: MIT

import os
import socket
import tempfile
import unittest
from pathlib import Path

from bol import gui


class FakeTclError(Exception):
    pass


class FakeTk:
    TclError = FakeTclError


class FakeCtk:
    def __init__(self, environ, outcomes):
        self.environ = environ
        self.outcomes = outcomes
        self.calls = []

    def CTk(self, **kwargs):
        display = self.environ.get("DISPLAY")
        self.calls.append((display, kwargs))
        outcome = self.outcomes.get(display)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class XwaylandDisplayRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.socket_dir = Path(self.tempdir.name)
        self.sockets = []

    def bind_socket(self, display, listen=False):
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(self.socket_dir / f"X{display}"))
        if listen:
            server.listen(1)
        self.sockets.append(server)
        self.addCleanup(server.close)
        return server

    def test_owned_socket_displays_are_numeric_sorted_and_socket_only(self):
        self.bind_socket(10)
        self.bind_socket(2)
        (self.socket_dir / "X1").write_text("not a socket")
        (self.socket_dir / "X3_").write_text("wrong name")

        self.assertEqual(
            gui._owned_x11_socket_displays(
                self.socket_dir, uid=os.getuid()),
            (":2", ":10"),
        )
        self.assertEqual(
            gui._owned_x11_socket_displays(
                self.socket_dir, uid=os.getuid() + 1),
            (),
        )

    def test_stale_display_recovers_to_owned_xwayland_socket(self):
        self.bind_socket(1)
        environ = {"WAYLAND_DISPLAY": "wayland-1", "DISPLAY": ":0"}
        first = FakeTclError("couldn't connect to display \":0\"")
        root = object()
        ctk = FakeCtk(environ, {":0": first, ":1": root})
        attempted = []

        result = gui._create_gui_root(
            ctk, FakeTk, environ=environ, socket_dir=self.socket_dir,
            attempted=attempted,
        )

        self.assertIs(result, root)
        self.assertEqual(attempted, [":0", ":1"])
        self.assertEqual(environ["DISPLAY"], ":1")

    def test_failed_retries_restore_original_display_and_error(self):
        self.bind_socket(1)
        environ = {"WAYLAND_DISPLAY": "wayland-1", "DISPLAY": ":0"}
        first = FakeTclError("couldn't connect to display \":0\"")
        retry = FakeTclError("couldn't connect to display \":1\"")
        ctk = FakeCtk(environ, {":0": first, ":1": retry})
        attempted = []

        with self.assertRaises(FakeTclError) as raised:
            gui._create_gui_root(
                ctk, FakeTk, environ=environ, socket_dir=self.socket_dir,
                attempted=attempted,
            )

        self.assertIs(raised.exception, first)
        self.assertEqual(attempted, [":0", ":1"])
        self.assertEqual(environ["DISPLAY"], ":0")

    def test_present_owned_display_preserves_authentication_failure(self):
        self.bind_socket(0, listen=True)
        self.bind_socket(1)
        environ = {"WAYLAND_DISPLAY": "wayland-1", "DISPLAY": ":0"}
        first = FakeTclError("couldn't connect to display \":0\"")
        ctk = FakeCtk(environ, {":0": first, ":1": object()})
        attempted = []

        with self.assertRaises(FakeTclError) as raised:
            gui._create_gui_root(
                ctk, FakeTk, environ=environ, socket_dir=self.socket_dir,
                attempted=attempted,
            )

        self.assertIs(raised.exception, first)
        self.assertEqual(ctk.calls, [
            (":0", {"className": gui.PRETTY}),
        ])
        self.assertEqual(attempted, [":0"])
        self.assertEqual(environ["DISPLAY"], ":0")

    def test_orphaned_original_socket_can_recover_to_another_display(self):
        orphan = self.bind_socket(0)
        orphan.close()
        self.bind_socket(1)
        environ = {"WAYLAND_DISPLAY": "wayland-1", "DISPLAY": ":0"}
        first = FakeTclError("couldn't connect to display \":0\"")
        root = object()
        ctk = FakeCtk(environ, {":0": first, ":1": root})
        attempted = []

        result = gui._create_gui_root(
            ctk, FakeTk, environ=environ, socket_dir=self.socket_dir,
            attempted=attempted,
        )

        self.assertIs(result, root)
        self.assertEqual(attempted, [":0", ":1"])
        self.assertEqual(environ["DISPLAY"], ":1")

    def test_non_wayland_session_does_not_probe_other_displays(self):
        self.bind_socket(1)
        environ = {"DISPLAY": ":0"}
        first = FakeTclError("couldn't connect to display \":0\"")
        ctk = FakeCtk(environ, {":0": first, ":1": object()})

        with self.assertRaises(FakeTclError):
            gui._create_gui_root(
                ctk, FakeTk, environ=environ, socket_dir=self.socket_dir,
            )

        self.assertEqual(len(ctk.calls), 1)
        self.assertEqual(environ["DISPLAY"], ":0")

    def test_missing_display_can_recover_without_inventing_a_number(self):
        self.bind_socket(4)
        environ = {"WAYLAND_DISPLAY": "wayland-1"}
        first = FakeTclError(
            "no display name and no $DISPLAY environment variable")
        root = object()
        ctk = FakeCtk(environ, {None: first, ":4": root})
        attempted = []

        result = gui._create_gui_root(
            ctk, FakeTk, environ=environ, socket_dir=self.socket_dir,
            attempted=attempted,
        )

        self.assertIs(result, root)
        self.assertEqual(attempted, ["<unset>", ":4"])
        self.assertEqual(environ["DISPLAY"], ":4")


if __name__ == "__main__":
    unittest.main()
