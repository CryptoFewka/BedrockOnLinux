"""Offline command-line dispatch regressions."""
# SPDX-License-Identifier: MIT

import contextlib
import io
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

from bol import cli
from bol.log import BolError
from bol.network import NetworkCheck


class CliTests(unittest.TestCase):
    def test_direct_bol_error_is_printed_before_cli_exit(self):
        output = io.StringIO()
        with mock.patch.object(
                sys, "argv",
                ["bedrock-on-linux", "profiles", "create", "../bad"]), \
                mock.patch.object(
                    cli, "require_profile_shortcuts_supported"), \
                mock.patch.object(
                    cli, "create_profile",
                    side_effect=BolError("invalid profile name")), \
                contextlib.redirect_stdout(output), \
                self.assertRaises(SystemExit) as exited:
            cli.main()

        self.assertEqual(exited.exception.code, 1)
        self.assertIn("invalid profile name", output.getvalue())

    def test_regular_setup_preserves_existing_dispatch(self):
        with mock.patch.object(
                sys, "argv", ["bedrock-on-linux", "setup", "--force"]), \
                mock.patch.object(cli, "do_setup") as setup, \
                mock.patch.object(cli, "ok"):
            cli.main()

        setup.assert_called_once_with(mc_ver=None, force=True)

    def test_profile_create_prints_profile_shortcut_and_command(self):
        profile = Path("/tmp/bol-profiles/family")
        shortcut = Path("/tmp/applications/bol-family.desktop")
        output = io.StringIO()
        with mock.patch.object(
                sys, "argv",
                ["bedrock-on-linux", "profiles", "create", "Family"]), \
                mock.patch.object(
                    cli, "create_profile", return_value=profile) as create, \
                mock.patch.object(
                    cli, "write_profile_shortcut",
                    return_value=shortcut) as write_shortcut, \
                mock.patch.object(
                    cli, "profile_launch_command",
                    return_value="BOL_HOME=/tmp/family bedrock-on-linux gui"
                ) as launch_command, \
                mock.patch.object(cli, "ok") as success, \
                contextlib.redirect_stdout(output):
            cli.main()

        create.assert_called_once_with("Family")
        write_shortcut.assert_called_once_with(
            "Family",
            profile_dir=profile,
        )
        launch_command.assert_called_once_with(profile)
        self.assertEqual(
            [call.args[0] for call in success.call_args_list],
            [f"Profile: {profile}", f"Desktop shortcut: {shortcut}"],
        )
        self.assertIn(
            "Steam command: BOL_HOME=/tmp/family bedrock-on-linux gui",
            output.getvalue(),
        )

    def test_shortcut_creates_a_direct_launch_entry_for_the_default_install(
            self):
        shortcut = Path("/tmp/applications/bedrock-on-linux-play.desktop")
        output = io.StringIO()
        with mock.patch.object(
                sys, "argv", ["bedrock-on-linux", "shortcut"]), \
                mock.patch.object(cli, "require_shortcuts_supported"), \
                mock.patch.object(cli, "create_profile") as create, \
                mock.patch.object(
                    cli, "write_play_shortcut",
                    return_value=shortcut) as write_shortcut, \
                mock.patch.object(
                    cli, "play_launch_command",
                    return_value="bedrock-on-linux play") as launch_command, \
                mock.patch.object(
                    cli, "direct_launch_readiness",
                    return_value=["Sign in from the launcher once."]), \
                mock.patch.object(cli, "warn") as warned, \
                mock.patch.object(cli, "info"), \
                mock.patch.object(cli, "ok") as success, \
                contextlib.redirect_stdout(output):
            cli.main()

        create.assert_not_called()
        write_shortcut.assert_called_once_with(
            profile_name=None, profile_dir=None)
        launch_command.assert_called_once_with(None)
        success.assert_called_once_with(f"Desktop shortcut: {shortcut}")
        self.assertIn("Steam command: bedrock-on-linux play",
                      output.getvalue())
        warned.assert_called_once_with("Sign in from the launcher once.")

    def test_profile_shortcut_reports_no_readiness_for_another_home(self):
        profile = Path("/tmp/bol-profiles/family")
        with mock.patch.object(
                sys, "argv",
                ["bedrock-on-linux", "shortcut", "--profile", "Family"]), \
                mock.patch.object(cli, "require_shortcuts_supported"), \
                mock.patch.object(
                    cli, "create_profile", return_value=profile), \
                mock.patch.object(
                    cli, "write_play_shortcut", return_value=Path("/tmp/e")), \
                mock.patch.object(
                    cli, "play_launch_command", return_value="cmd"), \
                mock.patch.object(
                    cli, "direct_launch_readiness") as readiness, \
                mock.patch.object(cli, "info"), \
                mock.patch.object(cli, "ok"), \
                contextlib.redirect_stdout(io.StringIO()):
            cli.main()

        # Settings under the current BOL_HOME say nothing about the profile.
        readiness.assert_not_called()

    def test_windowless_play_failure_reaches_the_desktop(self):
        with mock.patch.object(
                sys, "argv", ["bedrock-on-linux", "play"]), \
                mock.patch.object(cli, "IS_TTY", False), \
                mock.patch.object(
                    cli, "launch",
                    side_effect=BolError("No game — choose a version")), \
                mock.patch.object(cli, "desktop_notify") as notified, \
                mock.patch.object(cli, "err"), \
                self.assertRaises(SystemExit) as exited:
            cli.main()

        self.assertEqual(exited.exception.code, 1)
        self.assertIn("No game — choose a version", notified.call_args.args[0])

    def test_terminal_play_failure_is_not_duplicated_as_a_notification(self):
        with mock.patch.object(
                sys, "argv", ["bedrock-on-linux", "play"]), \
                mock.patch.object(cli, "IS_TTY", True), \
                mock.patch.object(
                    cli, "launch", side_effect=BolError("prefix busy")), \
                mock.patch.object(cli, "desktop_notify") as notified, \
                mock.patch.object(cli, "err"), \
                self.assertRaises(SystemExit):
            cli.main()

        notified.assert_not_called()

    def test_profile_list_prints_all_known_profiles(self):
        output = io.StringIO()
        profiles = [
            {
                "name": "Alice",
                "slug": "alice",
                "path": "/tmp/profiles/alice",
            },
            {
                "name": "Bob",
                "slug": "bob",
                "path": "/tmp/profiles/bob",
            },
        ]
        with mock.patch.object(
                sys, "argv",
                ["bedrock-on-linux", "profiles", "list"]), \
                mock.patch.object(
                    cli, "list_profiles", return_value=profiles) as listing, \
                contextlib.redirect_stdout(output):
            cli.main()

        listing.assert_called_once_with()
        self.assertIn("Alice (alice): /tmp/profiles/alice", output.getvalue())
        self.assertIn("Bob (bob): /tmp/profiles/bob", output.getvalue())

    def test_empty_profile_list_is_reported(self):
        with mock.patch.object(
                sys, "argv",
                ["bedrock-on-linux", "profiles", "list"]), \
                mock.patch.object(cli, "list_profiles", return_value=[]), \
                mock.patch.object(cli, "info") as informational:
            cli.main()

        informational.assert_called_once_with("No isolated profiles.")

    def test_doctor_network_combines_system_and_network_results(self):
        checks = [
            NetworkCheck("dns", "xbox.example", True, "resolved"),
            NetworkCheck("tls", "xbox.example", False, "timed out"),
            NetworkCheck("clock", "host", None, "status unavailable"),
        ]
        output = io.StringIO()
        with mock.patch.object(
                sys, "argv",
                ["bedrock-on-linux", "doctor", "--network"]), \
                mock.patch.object(
                    cli, "doctor", return_value=True) as system_doctor, \
                mock.patch.object(
                    cli, "diagnose_network",
                    return_value=(False, checks)) as network_doctor, \
                contextlib.redirect_stdout(output), \
                self.assertRaises(SystemExit) as exited:
            cli.main()

        self.assertEqual(exited.exception.code, 1)
        system_doctor.assert_called_once_with(False)
        network_doctor.assert_called_once_with(None)
        report = output.getvalue()
        self.assertIn("dns", report)
        self.assertIn("OK", report)
        self.assertIn("ÉCHEC", report)
        self.assertIn("INFO", report)

    def test_doctor_host_implies_network_checks(self):
        with mock.patch.object(
                sys, "argv",
                ["bedrock-on-linux", "doctor", "--host", "192.0.2.7"]), \
                mock.patch.object(cli, "doctor", return_value=True), \
                mock.patch.object(
                    cli, "diagnose_network",
                    return_value=(True, [])) as network_doctor, \
                self.assertRaises(SystemExit) as exited:
            cli.main()

        self.assertEqual(exited.exception.code, 0)
        network_doctor.assert_called_once_with("192.0.2.7")

    def test_plain_doctor_does_not_run_network_checks(self):
        with mock.patch.object(
                sys, "argv", ["bedrock-on-linux", "doctor"]), \
                mock.patch.object(
                    cli, "doctor", return_value=True) as system_doctor, \
                mock.patch.object(cli, "diagnose_network") as network_doctor, \
                self.assertRaises(SystemExit) as exited:
            cli.main()

        self.assertEqual(exited.exception.code, 0)
        system_doctor.assert_called_once_with(False)
        network_doctor.assert_not_called()

    def test_failed_system_doctor_is_not_masked_by_healthy_network(self):
        with mock.patch.object(
                sys, "argv",
                ["bedrock-on-linux", "doctor", "--network"]), \
                mock.patch.object(cli, "doctor", return_value=False), \
                mock.patch.object(
                    cli, "diagnose_network", return_value=(True, [])), \
                self.assertRaises(SystemExit) as exited:
            cli.main()

        self.assertEqual(exited.exception.code, 1)


class LauncherStartTests(unittest.TestCase):
    """Starting the launcher opens the launcher — in Game Mode too."""

    def _run(self, argv, gamescope):
        with mock.patch.object(sys, "argv", argv), \
                mock.patch.dict(os.environ, {"DISPLAY": ":0"}), \
                mock.patch.object(cli, "launch") as launched, \
                mock.patch.object(cli, "gui") as window, \
                mock.patch.object(cli, "info"), \
                mock.patch("bol.gpu_safety.in_gamescope_session",
                           return_value=gamescope):
            cli.main()
        return launched, window

    def test_the_window_opens_in_every_session(self):
        # The 2.1.4 behaviour — Game Mode starts the game and never shows the
        # launcher — also fired on the reporter's desktop session (#127, #130).
        for gamescope in (True, False):
            for argv in (["bedrock-on-linux"], ["bedrock-on-linux", "gui"]):
                with self.subTest(argv=argv, gamescope=gamescope):
                    launched, window = self._run(argv, gamescope)
                    window.assert_called_once_with()
                    launched.assert_not_called()

    def test_play_stays_the_launcher_free_path(self):
        launched, window = self._run(["bedrock-on-linux", "play"], True)
        launched.assert_called_once_with()
        window.assert_not_called()

    def test_shortcut_launch_failure_reaches_the_desktop(self):
        # A shortcut leaves no terminal, so the notification is the only place
        # this can surface.
        with mock.patch.object(sys, "argv", ["bedrock-on-linux", "play"]), \
                mock.patch.object(cli, "IS_TTY", False), \
                mock.patch.object(
                    cli, "launch",
                    side_effect=BolError("No game — choose a version")), \
                mock.patch.object(cli, "desktop_notify") as notified, \
                mock.patch.object(cli, "err"), \
                self.assertRaises(SystemExit) as exited:
            cli.main()

        self.assertEqual(exited.exception.code, 1)
        self.assertIn("No game — choose a version", notified.call_args.args[0])

    def test_window_failure_is_not_reported_as_a_launch_failure(self):
        with mock.patch.object(sys, "argv", ["bedrock-on-linux", "gui"]), \
                mock.patch.dict(os.environ, {"DISPLAY": ":0"}), \
                mock.patch.object(cli, "IS_TTY", False), \
                mock.patch.object(cli, "gui",
                                  side_effect=BolError("Tk is missing")), \
                mock.patch.object(cli, "desktop_notify") as notified, \
                mock.patch.object(cli, "err"), \
                self.assertRaises(SystemExit):
            cli.main()

        notified.assert_not_called()


if __name__ == "__main__":
    unittest.main()
