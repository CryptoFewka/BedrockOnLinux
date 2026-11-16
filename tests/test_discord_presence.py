"""Tests for the Discord Rich Presence session.

The fake Discord below frames its side of the conversation by hand rather
than reusing bol.discord's own reader and writer: the wire format is the
thing being checked, and a test that spoke it through the code under test
would agree with that code however wrong both of them were.
"""
# SPDX-License-Identifier: MIT

import json
import socket
import struct
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from bol import discord

_HANDSHAKE = 0
_FRAME = 1
_TIMEOUT = 10


def _read_exactly(conn, size):
    chunks = b""
    while len(chunks) < size:
        block = conn.recv(size - len(chunks))
        if not block:
            return None
        chunks += block
    return chunks


def _read_frame(conn):
    header = _read_exactly(conn, 8)
    if header is None:
        return None
    opcode, length = struct.unpack("<II", header)
    body = _read_exactly(conn, length) if length else b"{}"
    if body is None:
        return None
    return opcode, json.loads(body.decode("utf-8"))


def _write_frame(conn, opcode, payload):
    body = json.dumps(payload).encode("utf-8")
    conn.sendall(struct.pack("<II", opcode, len(body)) + body)


class FakeDiscord(threading.Thread):
    """A Unix socket that answers a handshake and records what it is told."""

    def __init__(self, folder):
        super().__init__(daemon=True)
        self.path = Path(folder) / "discord-ipc-0"
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(str(self.path))
        self._server.listen(1)
        self._server.settimeout(_TIMEOUT)
        self.handshake = None
        self.activity = None
        self.announced = threading.Event()
        self.cleared = threading.Event()
        self.hung_up = threading.Event()

    def run(self):
        try:
            conn, _ = self._server.accept()
        except OSError:
            return
        with conn:
            conn.settimeout(_TIMEOUT)
            while True:
                try:
                    frame = _read_frame(conn)
                except OSError:
                    break
                if frame is None:
                    break
                opcode, payload = frame
                if opcode == _HANDSHAKE:
                    self.handshake = payload
                    _write_frame(conn, _FRAME, {
                        "cmd": "DISPATCH", "evt": "READY", "data": {}})
                    continue
                if payload.get("cmd") != "SET_ACTIVITY":
                    continue
                args = payload.get("args") or {}
                if "activity" in args and args["activity"] is not None:
                    self.activity = args["activity"]
                    self.announced.set()
                else:
                    self.cleared.set()
        self.hung_up.set()

    def close(self):
        try:
            self._server.close()
        except OSError:
            pass


class SocketDiscoveryTests(unittest.TestCase):
    def test_the_runtime_directory_is_searched_before_tmp(self):
        paths = discord.socket_candidates(
            {"XDG_RUNTIME_DIR": "/run/user/1000"})
        self.assertEqual(paths[0], Path("/run/user/1000/discord-ipc-0"))
        self.assertIn(Path("/tmp/discord-ipc-0"), paths)

    def test_sandboxed_discord_builds_are_searched_too(self):
        paths = set(discord.socket_candidates({"XDG_RUNTIME_DIR": "/run/u"}))
        # A Flatpak or Snap Discord keeps the same socket under a runtime
        # directory of its own; missing these reads as "it does not work".
        self.assertIn(
            Path("/run/u/app/com.discordapp.Discord/discord-ipc-0"), paths)
        self.assertIn(Path("/run/u/snap.discord/discord-ipc-0"), paths)

    def test_a_second_client_gets_a_later_index(self):
        paths = set(discord.socket_candidates({"XDG_RUNTIME_DIR": "/run/u"}))
        self.assertIn(Path("/run/u/discord-ipc-9"), paths)


class ActivityTests(unittest.TestCase):
    def test_the_build_being_played_is_what_is_shown(self):
        activity = discord.session_activity(
            {"mc_edition": "release", "mc_version": "1.26.32.2"},
            started_at=1_700_000_000)
        self.assertEqual(activity["details"], "Minecraft Bedrock 1.26.32.2")
        self.assertEqual(activity["timestamps"]["start"], 1_700_000_000)

    def test_preview_is_named_as_preview(self):
        activity = discord.session_activity({"mc_edition": "preview",
                                             "mc_version": "1.26.40.1"})
        self.assertEqual(activity["details"], "Minecraft Preview 1.26.40.1")

    def test_an_empty_settings_file_still_produces_a_line(self):
        # Settings written before the version was recorded, or a game folder
        # from outside the managed tree: still a session, just a vaguer one.
        self.assertEqual(discord.session_activity({})["details"],
                         "Minecraft Bedrock")

    def test_the_session_carries_the_project_links(self):
        buttons = discord.session_activity({})["buttons"]
        self.assertLessEqual(len(buttons), 2)  # Discord accepts two
        for button in buttons:
            self.assertTrue(button["url"].startswith("https://"))
            self.assertLessEqual(len(button["label"]), 32)


class EnablementTests(unittest.TestCase):
    def test_presence_is_on_unless_it_was_turned_off(self):
        self.assertTrue(discord.presence_enabled({}, {}))
        self.assertTrue(discord.presence_enabled({"discord_presence": True},
                                                 {}))
        self.assertFalse(discord.presence_enabled({"discord_presence": False},
                                                  {}))

    def test_the_environment_overrides_the_switch_either_way(self):
        off = {"BOL_DISCORD_PRESENCE": "0"}
        on = {"BOL_DISCORD_PRESENCE": "1"}
        self.assertFalse(discord.presence_enabled({}, off))
        self.assertTrue(discord.presence_enabled({"discord_presence": False},
                                                 on))

    def test_a_build_without_an_application_announces_nothing(self):
        with mock.patch.object(discord, "DISCORD_APP_ID", ""):
            session = discord.start_session({}, {})
        self.addCleanup(session.stop)
        self.assertFalse(session.active)


class SessionTests(unittest.TestCase):
    def setUp(self):
        # Short path: a Unix socket address is limited to about 100 bytes.
        self._tmp = tempfile.TemporaryDirectory(prefix="bol-ipc-", dir="/tmp")
        self.addCleanup(self._tmp.cleanup)
        self.folder = self._tmp.name

    def test_a_play_session_is_announced_and_then_taken_down(self):
        fake = FakeDiscord(self.folder)
        self.addCleanup(fake.close)
        fake.start()

        session = discord.start_session(
            {"mc_edition": "release", "mc_version": "1.26.32.2"},
            environ={"XDG_RUNTIME_DIR": self.folder,
                     "BOL_DISCORD_APP_ID": "424242"},
            started_at=1_700_000_000)
        self.addCleanup(session.stop)

        self.assertTrue(fake.announced.wait(_TIMEOUT),
                        "Discord was never told about the play session")
        self.assertEqual(fake.handshake.get("v"), 1)
        self.assertEqual(fake.handshake.get("client_id"), "424242")
        self.assertEqual(fake.activity["details"],
                         "Minecraft Bedrock 1.26.32.2")
        self.assertEqual(fake.activity["timestamps"]["start"], 1_700_000_000)

        session.stop()
        self.assertTrue(fake.cleared.wait(_TIMEOUT),
                        "the presence was not cleared when the game closed")
        self.assertTrue(fake.hung_up.wait(_TIMEOUT),
                        "the socket was left open after the session")

    def test_a_session_with_no_discord_running_stops_at_once(self):
        # The retry between attempts is measured in tens of seconds; stopping
        # has to interrupt it, or every launch teardown would wait it out.
        with mock.patch.object(discord, "socket_candidates",
                               return_value=[Path(self.folder) / "absent"]):
            session = discord.start_session(
                {}, environ={"XDG_RUNTIME_DIR": self.folder,
                             "BOL_DISCORD_APP_ID": "424242"})
            self.addCleanup(session.stop)
            self.assertTrue(session.active)
            time.sleep(0.2)          # let the thread reach its retry wait
            began = time.monotonic()
            session.stop(timeout=_TIMEOUT)
            waited = time.monotonic() - began
        self.assertLess(waited, discord._RETRY_SECONDS)
        self.assertIsNone(session._thread)

    def test_the_switch_keeps_the_launcher_off_discord(self):
        fake = FakeDiscord(self.folder)
        self.addCleanup(fake.close)
        fake.start()

        session = discord.start_session(
            {"discord_presence": False},
            environ={"XDG_RUNTIME_DIR": self.folder,
                     "BOL_DISCORD_APP_ID": "424242"})
        self.addCleanup(session.stop)
        self.assertFalse(session.active)
        self.assertFalse(fake.announced.wait(1))
        self.assertIsNone(fake.handshake)


class ArtworkTests(unittest.TestCase):
    """The image beside the session.

    Discord takes each of these as either an asset key uploaded to the
    application or a full URL it fetches itself. The distinction matters
    because an application can be configured and announcing sessions while its
    Art Assets are still empty -- which is what "it works but there is no
    logo" was: a key with no artwork behind it, sent and silently ignored.
    """

    def _assets(self, large, small):
        with mock.patch.object(discord, "DISCORD_LARGE_IMAGE", large), \
                mock.patch.object(discord, "DISCORD_SMALL_IMAGE", small):
            return discord.session_activity({}).get("assets")

    def test_the_shipped_default_is_a_url_not_a_bare_key(self):
        # The default has to work with no developer-portal step at all.
        large = discord.session_activity({})["assets"]["large_image"]
        self.assertTrue(large.startswith("https://"), large)

    def test_the_default_image_is_one_this_project_publishes(self):
        from bol.config import SITE_URL
        self.assertTrue(
            discord.session_activity({})["assets"]["large_image"]
            .startswith(SITE_URL))

    def test_an_asset_key_is_still_accepted(self):
        assets = self._assets("logo", "linux")
        self.assertEqual(assets["large_image"], "logo")
        self.assertEqual(assets["small_image"], "linux")

    def test_the_large_image_is_labelled_with_the_app_name(self):
        from bol.config import PRETTY
        self.assertEqual(self._assets("logo", "")["large_text"], PRETTY)

    def test_an_unset_small_image_is_left_out_entirely(self):
        # Not sent as "": Discord reads an empty string as a key, finds
        # nothing, and leaves a blank frame where the badge should be.
        assets = self._assets("logo", "")
        self.assertNotIn("small_image", assets)
        self.assertNotIn("small_text", assets)

    def test_no_artwork_at_all_sends_no_assets_block(self):
        self.assertIsNone(self._assets("", ""))

    def test_the_session_still_carries_its_other_fields_without_artwork(self):
        with mock.patch.object(discord, "DISCORD_LARGE_IMAGE", ""), \
                mock.patch.object(discord, "DISCORD_SMALL_IMAGE", ""):
            activity = discord.session_activity(
                {"mc_edition": "release", "mc_version": "26.32"})
        self.assertIn("26.32", activity["details"])
        self.assertEqual(len(activity["buttons"]), 2)


class DoctorSummaryTests(unittest.TestCase):
    def test_each_reason_for_seeing_nothing_reads_differently(self):
        no_app = discord.presence_summary({}, {})
        self.assertIn("no Discord application", no_app)
        env = {"BOL_DISCORD_APP_ID": "424242"}
        self.assertIn("off", discord.presence_summary(
            {"discord_presence": False}, env))
        with tempfile.TemporaryDirectory(prefix="bol-ipc-", dir="/tmp") as tmp:
            with mock.patch.object(discord, "socket_candidates",
                                   return_value=[Path(tmp) / "absent"]):
                self.assertIn("not running",
                              discord.presence_summary({}, env))


if __name__ == "__main__":
    unittest.main()
