"""bol.discord — show the play session on Discord (Rich Presence).

Discord's desktop client listens on a Unix socket in the user's runtime
directory and speaks a small framed protocol: a little-endian 32-bit opcode,
a little-endian 32-bit length, then a JSON payload. That is the entire
dependency — no library to install, no account of ours, no network call. When
Discord is not running the socket simply does not exist, which is the only
failure this module ever expects to meet, and it stays silent about it.

While Minecraft runs, friends see "Playing BedrockOnLinux" with the build
being played, how long for, and buttons back to the project: the launcher is
found by word of mouth, and this is the launcher saying its own name. Nothing
about the account, the world or the server is sent — Discord is told the
edition and version, and nothing else.

Discord clears a presence by itself the moment the socket closes, so a
launcher that is killed mid-game cannot leave anyone permanently "in game".
"""
# SPDX-License-Identifier: MIT

import json
import os
import select
import socket
import struct
import threading
import time
import uuid
from pathlib import Path

from .config import (
    DISCORD_APP_ID,
    DISCORD_INVITE,
    DISCORD_LARGE_IMAGE,
    DISCORD_SMALL_IMAGE,
    PRETTY,
    SITE_URL,
)

# Frame opcodes of Discord's local IPC.
_OP_HANDSHAKE = 0
_OP_FRAME = 1
_OP_CLOSE = 2
_OP_PING = 3
_OP_PONG = 4

# Discord numbers its socket per running client: the first one gets -0, a
# second client (Canary beside Stable, say) the next free index.
_SOCKET_NAMES = tuple(f"discord-ipc-{index}" for index in range(10))

# A sandboxed Discord keeps the same socket under its own runtime directory.
# The Flatpak and Snap builds are common enough on the distributions this
# launcher targets that skipping them would look like "it does not work".
_SOCKET_SUBDIRS = (
    "",
    "app/com.discordapp.Discord",
    "app/com.discordapp.DiscordCanary",
    "app/com.discordapp.DiscordPTB",
    "snap.discord",
    "snap.discord-canary",
)

_CONNECT_TIMEOUT = 2.0   # a local socket answers at once or not at all
_POLL_SECONDS = 1.0      # how often the session thread notices it must stop
_RETRY_SECONDS = 30.0    # Discord is often started after the game

_EDITION_NAMES = {
    "release": "Minecraft Bedrock",
    "preview": "Minecraft Preview",
}


def _runtime_dirs(environ):
    """The directories Discord may have put its socket in, best first."""
    dirs = []
    for var in ("XDG_RUNTIME_DIR", "TMPDIR", "TMP", "TEMP"):
        value = (environ.get(var) or "").strip()
        if value and value not in dirs:
            dirs.append(value)
    if "/tmp" not in dirs:
        dirs.append("/tmp")
    return dirs


def socket_candidates(environ=None):
    """Every path a running Discord could be listening on."""
    environ = os.environ if environ is None else environ
    paths = []
    for base in _runtime_dirs(environ):
        for subdir in _SOCKET_SUBDIRS:
            folder = Path(base, subdir) if subdir else Path(base)
            for name in _SOCKET_NAMES:
                paths.append(folder / name)
    return paths


def discord_socket(environ=None):
    """The socket of the running Discord, or None when none is running."""
    for path in socket_candidates(environ):
        try:
            if path.is_socket():
                return path
        except OSError:
            continue
    return None


def _read_exactly(sock, size):
    """Exactly ``size`` bytes, or None if Discord closed the connection."""
    chunks = b""
    while len(chunks) < size:
        block = sock.recv(size - len(chunks))
        if not block:
            return None
        chunks += block
    return chunks


def _send(sock, opcode, payload):
    body = json.dumps(payload).encode("utf-8")
    sock.sendall(struct.pack("<II", opcode, len(body)) + body)


def _recv(sock):
    """One frame as (opcode, payload), or None once the socket is done."""
    header = _read_exactly(sock, 8)
    if header is None:
        return None
    opcode, length = struct.unpack("<II", header)
    body = _read_exactly(sock, length) if length else b""
    if body is None:
        return None
    try:
        payload = json.loads(body.decode("utf-8")) if body else {}
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return opcode, payload


def _handshake(sock, app_id):
    """Introduce the application and wait for Discord's READY."""
    _send(sock, _OP_HANDSHAKE, {"v": 1, "client_id": str(app_id)})
    while True:
        frame = _recv(sock)
        if frame is None:
            return False
        opcode, payload = frame
        if opcode == _OP_PING:
            _send(sock, _OP_PONG, payload)
            continue
        if opcode == _OP_CLOSE:
            return False
        if opcode != _OP_FRAME:
            continue
        event = payload.get("evt")
        if event == "READY":
            return True
        if event == "ERROR":
            # An application id Discord does not know ends here. Nothing to
            # report: the player configured no presence, or a fork did.
            return False


def _set_activity(sock, activity, pid):
    """Ask Discord to show (activity) or drop (None) the play session."""
    args = {"pid": int(pid)}
    if activity is not None:
        args["activity"] = activity
    _send(sock, _OP_FRAME, {
        "cmd": "SET_ACTIVITY",
        "nonce": str(uuid.uuid4()),
        "args": args,
    })


def _artwork():
    """The ``assets`` block, with only the images that are actually set.

    An empty value has to be left out rather than sent as "": Discord reads it
    as an asset key, finds nothing behind it, and shows a blank frame where
    the image should be. Sending no assets at all is what gets the plain card.
    """
    assets = {}
    if DISCORD_LARGE_IMAGE:
        assets["large_image"] = DISCORD_LARGE_IMAGE
        assets["large_text"] = PRETTY
    if DISCORD_SMALL_IMAGE:
        assets["small_image"] = DISCORD_SMALL_IMAGE
        assets["small_text"] = "Linux"
    return {"assets": assets} if assets else {}


def session_activity(settings=None, started_at=None):
    """What Discord is asked to display for one play session.

    The heading is the application's own name — "BedrockOnLinux" — so these
    two lines say what is being played inside it, and the timestamp turns
    into the session clock Discord counts up on its own.
    """
    settings = settings or {}
    edition = str(settings.get("mc_edition") or "release").strip().lower()
    title = _EDITION_NAMES.get(edition, _EDITION_NAMES["release"])
    version = str(settings.get("mc_version") or "").strip()
    return {
        "details": f"{title} {version}".strip(),
        "state": "Playing on Linux",
        "timestamps": {"start": int(started_at or time.time())},
        **_artwork(),
        # The advertisement proper: whoever sees the session can get the
        # launcher from it. Discord allows two, 32 characters each.
        "buttons": [
            {"label": "Play it on Linux", "url": SITE_URL},
            {"label": "Join the Discord", "url": DISCORD_INVITE},
        ],
        "instance": False,
    }


def presence_app_id(environ=None):
    """The Discord application to appear as, if one is configured."""
    environ = os.environ if environ is None else environ
    return (environ.get("BOL_DISCORD_APP_ID") or DISCORD_APP_ID or "").strip()


def presence_enabled(settings=None, environ=None):
    """Whether the play session may be shown on Discord.

    On by default — it is the point of the feature, it is what every other
    game launcher does, and it says only what is being played. The switch in
    Settings turns it off for good; BOL_DISCORD_PRESENCE overrides both for
    one run.
    """
    environ = os.environ if environ is None else environ
    forced = (environ.get("BOL_DISCORD_PRESENCE") or "").strip().lower()
    if forced in {"0", "false", "no", "off"}:
        return False
    if forced in {"1", "true", "yes", "on"}:
        return True
    return bool((settings or {}).get("discord_presence", True))


class Session:
    """One play session, kept on Discord by a thread of its own.

    Inert unless it was given an application id and something to show, so
    callers can always ``start()`` it and always ``stop()`` it. Nothing here
    may raise into a launch: the game must start, and go on running, with or
    without Discord.
    """

    def __init__(self, app_id="", activity=None, environ=None, pid=None):
        self._app_id = str(app_id or "")
        self._activity = activity
        self._environ = os.environ if environ is None else environ
        self._pid = os.getpid() if pid is None else pid
        self._stopped = threading.Event()
        self._thread = None
        # Set once Discord has accepted the presence; tests and `doctor` read
        # it, nothing else depends on it.
        self.announced = threading.Event()

    @property
    def active(self):
        """Whether anything is being sent at all."""
        return bool(self._app_id and self._activity)

    def start(self):
        """Begin announcing, in the background. Returns self."""
        if not self.active or self._thread is not None:
            return self
        self._thread = threading.Thread(
            target=self._run, name="discord-presence", daemon=True)
        self._thread.start()
        return self

    def stop(self, timeout=3.0):
        """Take the presence down and let the thread finish.

        The thread is a daemon and the socket close alone clears the session
        on Discord's side, so this waits briefly and then gives up rather
        than holding a launch teardown open.
        """
        self._stopped.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout)

    # -- the thread

    def _run(self):
        sock = None
        try:
            while not self._stopped.is_set():
                if sock is None:
                    sock = self._open()
                    if sock is None and self._stopped.wait(_RETRY_SECONDS):
                        break
                    continue
                if not self._serve(sock):
                    self._close(sock, clear=False)
                    sock = None
        except Exception:
            # Presence is decoration. A protocol surprise ends it quietly
            # instead of printing anything into a launch log.
            pass
        finally:
            if sock is not None:
                self._close(sock, clear=True)

    def _open(self):
        """A handshaken socket with the activity already set, or None."""
        for path in socket_candidates(self._environ):
            if self._stopped.is_set():
                return None
            try:
                if not path.is_socket():
                    continue
            except OSError:
                continue
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(_CONNECT_TIMEOUT)
            try:
                sock.connect(str(path))
                if _handshake(sock, self._app_id):
                    _set_activity(sock, self._activity, self._pid)
                    self.announced.set()
                    return sock
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        return None

    def _serve(self, sock):
        """Answer Discord for a moment. False once the connection is gone."""
        try:
            readable, _, _ = select.select([sock], [], [], _POLL_SECONDS)
            if not readable:
                return True
            frame = _recv(sock)
        except OSError:
            return False
        if frame is None:
            return False
        opcode, payload = frame
        if opcode == _OP_CLOSE:
            return False
        if opcode == _OP_PING:
            try:
                _send(sock, _OP_PONG, payload)
            except OSError:
                return False
        return True

    def _close(self, sock, clear):
        if clear:
            # Closing the socket is what actually clears the session; asking
            # first only makes it immediate rather than a moment later.
            try:
                _set_activity(sock, None, self._pid)
            except OSError:
                pass
        try:
            sock.close()
        except OSError:
            pass
        self.announced.clear()


def start_session(settings=None, environ=None, started_at=None):
    """Announce a play session on Discord for as long as it lasts.

    Always returns a :class:`Session` — an inert one when presence is off,
    when no Discord application is configured, or when Discord is not
    running — so a launch can stop it without asking whether it ever began.
    """
    environ = os.environ if environ is None else environ
    try:
        if not presence_enabled(settings, environ):
            return Session(environ=environ)
        app_id = presence_app_id(environ)
        if not app_id:
            return Session(environ=environ)
        activity = session_activity(settings, started_at=started_at)
        return Session(app_id, activity, environ=environ).start()
    except Exception:
        # A launch in progress is worth more than its decoration: a machine
        # that cannot even spare a thread still gets to play.
        return Session(environ=environ)


def presence_summary(settings=None, environ=None):
    """One line for `doctor`: what friends see while you play."""
    environ = os.environ if environ is None else environ
    if not presence_app_id(environ):
        return "unavailable (no Discord application is configured)"
    if not presence_enabled(settings, environ):
        return "off (Settings > General > Discord)"
    path = discord_socket(environ)
    if path is None:
        return "on, but Discord is not running (nothing is sent)"
    return f"on ({path})"
