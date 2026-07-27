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
