"""Tests for the read-only network diagnostics."""
# SPDX-License-Identifier: MIT

import socket
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

from bol import network


class NetworkDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        # Every probe in this module is injectable so the tests stay offline.
        # The Xbox social snapshot is the one that would otherwise reach Xbox
        # Live by itself, so it is stubbed for the whole class; the tests that
        # are about those lines pass their own.
        patch = mock.patch.object(
            network.presence, "social_snapshot",
            lambda *_a, **_k: network.presence.SocialSnapshot(
                state="Online", friends=3, in_session=1))
        patch.start()
        self.addCleanup(patch.stop)

    @staticmethod
    def _runner(argv, **_kwargs):
        if argv[0] == "timedatectl":
            return subprocess.CompletedProcess(
                argv, 0,
                stdout="NTPSynchronized=yes\nNTP=yes\nLocalRTC=no\n",
                stderr="",
            )
        if argv[:4] == ["ip", "-o", "link", "show"]:
            return subprocess.CompletedProcess(
                argv, 0,
                stdout=(
                    "2: eth0: <BROADCAST,UP> mtu 1500\n"
                    "7: tun0: <POINTOPOINT,UP> mtu 1500\n"
                    "8: docker0: <BROADCAST,UP> mtu 1500\n"
                ),
                stderr="",
            )
        if argv[:3] == ["ip", "route", "get"]:
            return subprocess.CompletedProcess(
                argv, 0,
                stdout=f"{argv[3]} dev eth0 src 192.168.1.40 uid 1000\n",
                stderr="",
            )
        raise AssertionError(f"unexpected command: {argv!r}")

    def test_default_playfab_probe_uses_service_host_not_xsts_audience(self):
        self.assertIn(
            ("PlayFab", "b980a380.playfabapi.com", 443),
            network.NETWORK_ENDPOINTS,
        )
        self.assertTrue(all(
            host != "b980a380.minecraft.playfabapi.com"
            for _label, host, _port in network.NETWORK_ENDPOINTS
        ))

    def test_success_returns_bool_and_display_ready_results(self):
        resolver_calls = []
        tls_calls = []

        def resolver(host, port, timeout):
            resolver_calls.append((host, port, timeout))
            return ("203.0.113.10",)

        def tls_probe(host, port, timeout):
            tls_calls.append((host, port, timeout))
            return "TLSv1.3"

        endpoints = (
            ("Xbox", "xbox.example", 443),
            ("PlayFab", "playfab.example", 443),
            ("Minecraft", "minecraft.example", 443),
        )
        result, checks = network.diagnose_network(
            "192.168.1.22",
            endpoints=endpoints,
            timeout=1,
            resolver=resolver,
            tls_probe=tls_probe,
            runner=self._runner,
        )

        self.assertTrue(result)
        self.assertEqual(
            {call[0] for call in resolver_calls},
            {endpoint[1] for endpoint in endpoints},
        )
        self.assertEqual(
            {call[0] for call in tls_calls},
            {endpoint[1] for endpoint in endpoints},
        )
        self.assertEqual(
            [check.kind for check in checks[:6]],
            ["dns", "tls", "dns", "tls", "dns", "tls"],
        )
        route = next(check for check in checks if check.kind == "route")
        self.assertTrue(route.ok)
        self.assertIn("interface=eth0", route.detail)
        self.assertIn("does not prove", route.detail)
        clock = next(check for check in checks if check.kind == "clock")
        self.assertIsNone(clock.ok)
        self.assertIn("NTP synchronized=yes", clock.detail)
        vpn = next(
            check for check in checks if check.kind == "virtual-interfaces"
        )
        self.assertIsNone(vpn.ok)
        self.assertIn("tun0", vpn.detail)
        self.assertIn("docker0", vpn.detail)
        self.assertIn("does not prove", vpn.detail)
        self.assertEqual(route.target, "192.168.1.22")

    def test_unsynchronized_clock_is_actionable_and_fails_report(self):
        def runner(argv, **kwargs):
            if argv[0] == "timedatectl":
                return subprocess.CompletedProcess(
                    argv, 0,
                    stdout=(
                        "NTPSynchronized=no\n"
                        "NTP=yes\n"
                        "LocalRTC=no\n"
                    ),
                    stderr="",
                )
            return self._runner(argv, **kwargs)

        result, checks = network.diagnose_network(
            endpoints=(("Xbox", "xbox.example", 443),),
            resolver=lambda *_args: ("203.0.113.10",),
            tls_probe=lambda *_args: "TLSv1.3",
            runner=runner,
        )

        self.assertFalse(result)
        clock = next(check for check in checks if check.kind == "clock")
        self.assertFalse(clock.ok)
        self.assertIn("not synchronized", clock.detail)
        self.assertIn("Xbox", clock.detail)

    @staticmethod
    def _timedatectl(stdout, returncode=0):
        def runner(argv, **_kwargs):
            assert argv[0] == "timedatectl"
            return subprocess.CompletedProcess(
                argv, returncode, stdout=stdout, stderr="")
        return runner

    def test_clock_is_unsynchronized_only_when_positively_reported(self):
        self.assertTrue(network.clock_is_unsynchronized(
            self._timedatectl("NTPSynchronized=no\nNTP=yes\nLocalRTC=no\n")))
        self.assertFalse(network.clock_is_unsynchronized(
            self._timedatectl("NTPSynchronized=yes\nNTP=yes\nLocalRTC=no\n")))

    def test_clock_is_unsynchronized_stays_false_when_unavailable(self):
        """No timedatectl (or a failing one) must never blame the clock."""

        self.assertFalse(network.clock_is_unsynchronized(
            self._timedatectl("", returncode=1)))

        def missing(*_args, **_kwargs):
            raise FileNotFoundError("timedatectl")

        self.assertFalse(network.clock_is_unsynchronized(missing))

    def test_endpoint_checks_are_run_in_parallel(self):
        barrier = threading.Barrier(3)

        def resolver(_host, _port, _timeout):
            barrier.wait(timeout=0.5)
            return ("203.0.113.10",)

        endpoints = tuple(
            (f"service-{index}", f"service-{index}.example", 443)
            for index in range(3)
        )
        result, checks = network.diagnose_network(
            endpoints=endpoints,
            timeout=0.75,
            resolver=resolver,
            tls_probe=lambda *_args: "TLSv1.3",
            runner=self._runner,
        )

        self.assertTrue(result)
        self.assertTrue(all(check.ok for check in checks[:6]))

    def test_dns_failure_skips_tls_and_fails_report(self):
        tls_calls = []

        def tls_probe(*args):
            tls_calls.append(args)
            return "TLSv1.3"

        result, checks = network.diagnose_network(
            endpoints=(("Xbox", "xbox.example", 443),),
            resolver=lambda *_args: (),
            tls_probe=tls_probe,
            runner=self._runner,
        )

        self.assertFalse(result)
        self.assertFalse(checks[0].ok)
        self.assertFalse(checks[1].ok)
        self.assertIn("DNS resolution failed", checks[1].detail)
        self.assertEqual(tls_calls, [])

    def test_tls_failure_is_not_reported_as_dns_failure(self):
        def fail_tls(*_args):
            raise OSError("certificate verify failed")

        result, checks = network.diagnose_network(
            endpoints=(("PlayFab", "playfab.example", 443),),
            resolver=lambda *_args: ("203.0.113.11",),
            tls_probe=fail_tls,
            runner=self._runner,
        )

        self.assertFalse(result)
        self.assertTrue(checks[0].ok)
        self.assertFalse(checks[1].ok)
        self.assertIn("certificate verify failed", checks[1].detail)

    def test_worker_deadline_is_reported(self):
        release = threading.Event()

        def stalled_resolver(*_args):
            release.wait(0.2)
            return ("203.0.113.12",)

        started = time.monotonic()
        try:
            result, checks = network.diagnose_network(
                endpoints=(("Xbox", "xbox.example", 443),),
                timeout=0.05,
                resolver=stalled_resolver,
                tls_probe=lambda *_args: "TLSv1.3",
                runner=self._runner,
            )
        finally:
            release.set()

        self.assertLess(time.monotonic() - started, 0.18)
        self.assertFalse(result)
        self.assertIn("timed out", checks[0].detail)
        self.assertIn("timed out", checks[1].detail)

    def test_default_resolver_runs_in_a_killable_bounded_child(self):
        completed = subprocess.CompletedProcess(
            [], 0, stdout='["2001:db8::2", "192.0.2.2"]\n', stderr="",
        )
        with mock.patch.object(
                network.subprocess, "run",
                return_value=completed) as run:
            addresses = network._resolved_addresses(
                "xbox.example", 443, 0.25)

        self.assertEqual(addresses, ("192.0.2.2", "2001:db8::2"))
        argv = run.call_args.args[0]
        self.assertEqual(argv[:3], [sys.executable, "-I", "-c"])
        self.assertEqual(argv[-2:], ["xbox.example", "443"])
        self.assertEqual(run.call_args.kwargs["timeout"], 0.25)

    def test_default_resolver_timeout_is_actionable(self):
        with mock.patch.object(
                network.subprocess, "run",
                side_effect=subprocess.TimeoutExpired(["python"], 0.05)):
            with self.assertRaisesRegex(TimeoutError, "DNS lookup timed out"):
                network._resolved_addresses("xbox.example", 443, 0.05)

    def test_tls_uses_resolved_ip_without_second_dns_lookup(self):
        events = []

        class Plain:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def settimeout(self, value):
                events.append(("timeout", value))

        class Secured:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def version(self):
                return "TLSv1.3"

        context = mock.Mock()
        context.wrap_socket.return_value = Secured()
        with mock.patch.object(
                network.socket, "create_connection",
                return_value=Plain()) as connect, \
                mock.patch.object(
                    network.ssl, "create_default_context",
                    return_value=context):
            version = network._tls_version(
                "xbox.example", 443, 1, ("192.0.2.7",))

        self.assertEqual(version, "TLSv1.3")
        self.assertEqual(
            context.minimum_version,
            network.ssl.TLSVersion.TLSv1_2,
        )
        connect.assert_called_once()
        self.assertEqual(connect.call_args.args[0], ("192.0.2.7", 443))
        context.wrap_socket.assert_called_once()
        self.assertEqual(
            context.wrap_socket.call_args.kwargs["server_hostname"],
            "xbox.example",
        )

    def test_tls_tries_next_address_after_first_address_times_out(self):
        clock = [10.0]
        connection_timeouts = []

        class Plain:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def settimeout(self, _value):
                return None

        class Secured:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def version(self):
                return "TLSv1.3"

        def connect(target, timeout):
            connection_timeouts.append((target, timeout))
            if len(connection_timeouts) == 1:
                clock[0] += timeout
                raise socket.timeout("first address timed out")
            return Plain()

        context = mock.Mock()
        context.wrap_socket.return_value = Secured()
        with mock.patch.object(
                network.time, "monotonic",
                side_effect=lambda: clock[0]), \
                mock.patch.object(
                    network.socket, "create_connection",
                    side_effect=connect), \
                mock.patch.object(
                    network.ssl, "create_default_context",
                    return_value=context):
            version = network._tls_version(
                "xbox.example", 443, 1,
                ("2001:db8::7", "192.0.2.7"),
            )

        self.assertEqual(version, "TLSv1.3")
        self.assertEqual(
            [call[0] for call in connection_timeouts],
            [("2001:db8::7", 443), ("192.0.2.7", 443)],
        )
        self.assertEqual(connection_timeouts[0][1], 0.5)
        self.assertLessEqual(connection_timeouts[1][1], 0.5)

    def test_tls_address_attempts_do_not_exceed_global_deadline(self):
        clock = [20.0]
        connection_timeouts = []

        def connect(target, timeout):
            connection_timeouts.append((target, timeout))
            clock[0] += timeout
            raise socket.timeout("address timed out")

        with mock.patch.object(
                network.time, "monotonic",
                side_effect=lambda: clock[0]), \
                mock.patch.object(
                    network.socket, "create_connection",
                    side_effect=connect), \
                mock.patch.object(
                    network.ssl, "create_default_context",
                    return_value=mock.Mock()):
            with self.assertRaises(socket.timeout):
                network._tls_version(
                    "xbox.example", 443, 0.9,
                    ("2001:db8::8", "192.0.2.8"),
                )

        self.assertEqual(len(connection_timeouts), 2)
        self.assertAlmostEqual(
            sum(timeout for _target, timeout in connection_timeouts),
            0.9,
        )
        self.assertAlmostEqual(clock[0], 20.9)

    def test_invalid_route_target_never_reaches_ip_command(self):
        commands = []

        def runner(argv, **kwargs):
            commands.append(argv)
            return self._runner(argv, **kwargs)

        result, checks = network.diagnose_network(
            "router.local; touch /tmp/not-run",
            endpoints=(),
            runner=runner,
        )

        self.assertFalse(result)
        route = next(check for check in checks if check.kind == "route")
        self.assertFalse(route.ok)
        self.assertIn("literal IPv4 or IPv6", route.detail)
        self.assertFalse(any(argv[:3] == ["ip", "route", "get"]
                             for argv in commands))

    def test_route_lookup_uses_an_argument_vector_and_no_udp_probe(self):
        commands = []

        def runner(argv, **kwargs):
            commands.append(argv)
            return self._runner(argv, **kwargs)

        result, _checks = network.diagnose_network(
            "2001:db8::8",
            endpoints=(),
            runner=runner,
        )

        self.assertTrue(result)
        self.assertIn(
            ["ip", "route", "get", "2001:db8::8"],
            commands,
        )
        flattened = " ".join(item for argv in commands for item in argv)
        self.assertNotIn("nc ", flattened)
        self.assertNotIn("nmap", flattened)
        self.assertNotIn("19132", flattened)


if __name__ == "__main__":
    unittest.main()


class XboxSocialLineTests(unittest.TestCase):
    """The three answers behind "I can't join my friend's world" (#243/#244).

    None of them may fail the report: an account nobody can see is a real
    problem but not a broken host, and an evening with no friend in a world
    is not a fault at all.
    """

    def _checks(self, **fields):
        return network._social_checks(network.presence.SocialSnapshot(**fields))

    def test_being_seen_as_offline_is_spelled_out_but_does_not_fail(self):
        checks = self._checks(state="Offline", friends=12, in_session=0)
        presence_line = next(c for c in checks if c.kind == "xbox presence")
        self.assertIsNone(presence_line.ok)
        self.assertIn("Offline", presence_line.detail)
        self.assertIn("nobody can join or invite", presence_line.detail)

    def test_being_seen_as_online_says_only_that(self):
        checks = self._checks(state="Online", friends=12, in_session=2)
        presence_line = next(c for c in checks if c.kind == "xbox presence")
        self.assertIn("Online", presence_line.detail)
        self.assertNotIn("nobody can join", presence_line.detail)

    def test_the_session_count_answers_the_report_directly(self):
        checks = self._checks(state="Online", friends=12, in_session=0)
        sessions = next(c for c in checks if c.kind == "xbox sessions")
        self.assertIsNone(sessions.ok)
        self.assertIn("0 of 12", sessions.detail)

    def test_a_readable_friends_list_is_a_pass(self):
        checks = self._checks(state="Online", friends=12, in_session=0)
        friends = next(c for c in checks if c.kind == "xbox friends")
        self.assertTrue(friends.ok)
        self.assertIn("12 friends", friends.detail)

    def test_an_unmeasurable_snapshot_says_so_once(self):
        checks = self._checks(error="no linked account to ask about")
        self.assertEqual(len(checks), 1)
        self.assertIsNone(checks[0].ok)
        self.assertIn("no linked account", checks[0].detail)

    def test_a_snapshot_that_raises_never_fails_the_report(self):
        def exploding():
            raise RuntimeError("boom")

        result, checks = network.diagnose_network(
            endpoints=(), runner=NetworkDiagnosticsTests._runner,
            social=exploding)

        self.assertTrue(result)
        social = next(c for c in checks if c.kind == "xbox social")
        self.assertIsNone(social.ok)
        self.assertIn("RuntimeError", social.detail)

    def test_the_lines_reach_the_report(self):
        result, checks = network.diagnose_network(
            endpoints=(), runner=NetworkDiagnosticsTests._runner,
            social=lambda: network.presence.SocialSnapshot(
                state="Online", friends=4, in_session=1))

        self.assertTrue(result)
        self.assertEqual(
            [c.kind for c in checks if c.kind.startswith("xbox")],
            ["xbox presence", "xbox friends", "xbox sessions"])
