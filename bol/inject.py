"""Inject client DLLs through the game's Wine prefix.

Flatpak is unsupported because its sandbox isolates the game's wineserver.
"""
# SPDX-License-Identifier: MIT

import os
import pkgutil
import subprocess
import tempfile
import time
from pathlib import Path

from .config import CACHE, LOGS
from .log import BolError
from .prefix import _mc_running, active_prefix, prefix_processes
from .proton import proton_path


def _cmdline_has_winedbg(cmdline):
    """Match Wine's debugger as an argv entry, not a parent-folder substring."""
    for argument in cmdline.lower().split(b"\0"):
        normalized = argument.replace(b"\\", b"/").rstrip(b"/")
        basename = normalized.rsplit(b"/", 1)[-1]
        if basename in (b"winedbg", b"winedbg.exe"):
            return True
    return False


def _wine_debugger_running():
    """Return whether this prefix has entered Wine's crash debugger."""
    for pid in prefix_processes(active_prefix()):
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            continue
        if _cmdline_has_winedbg(cmdline):
            return True
    return False


def _post_injection_failure(timeout=3.0, interval=0.1):
    """Observe asynchronous DLL startup long enough to catch an immediate crash.

    LoadLibrary returning successfully only proves that Windows mapped the DLL.
    Client initialization commonly continues on another thread, so a bad
    game/client version can still enter winedbg just after the injector exits.
    """
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        if _wine_debugger_running():
            return (
                "Minecraft entered Wine's crash debugger immediately after "
                "the DLL was loaded (winedbg)"
            )
        if not _mc_running():
            return "Minecraft exited immediately after the DLL was loaded"
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        time.sleep(min(max(0.01, interval), remaining))


def _extract_injector():
    """Materialize the bundled injector.exe for Wine."""
    blob = pkgutil.get_data("bol", "injector.exe")
    if not blob:
        raise BolError("Bundled injector.exe is missing from this install.")
    CACHE.mkdir(parents=True, exist_ok=True)
    inj = CACHE / "injector.exe"
    try:
        current = inj.read_bytes() if inj.is_file() else None
    except OSError:
        current = None
    if current != blob:
        fd, name = tempfile.mkstemp(
            prefix=".injector-", suffix=".tmp", dir=CACHE
        )
        staged = Path(name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(blob)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(staged, inj)
        finally:
            staged.unlink(missing_ok=True)
    return inj


def run_injector(dll_path):
    """Inject the given client .dll into the running Minecraft and wait for the
    outcome. The game must already be running (main menu reached). Returns the
    .dll's file name on success; raises BolError with a user-facing reason."""
    dll = Path(dll_path).expanduser()
    if not dll.is_file():
        raise BolError(f"DLL not found: {dll}")
    if dll.suffix.lower() != ".dll":
        raise BolError(f"Not a .dll: {dll.name}")
    if not _mc_running():
        raise BolError("Start Minecraft first and wait for the main menu, then "
                       "inject.")
    if _wine_debugger_running():
        raise BolError(
            "Minecraft is already stopped in Wine's crash debugger. Close the "
            "debugger, restart Minecraft, and do not inject the same DLL again."
        )
    pp = proton_path()
    if not pp:
        raise BolError("GDK-Proton engine missing — run Install / Update first.")
    wine = pp / "files/bin/wine"
    if not wine.exists():
        raise BolError(f"Engine wine binary not found ({wine}).")
    inj = _extract_injector()
    # The DLL path as Wine sees it: a host /a/b.dll maps to Z:\a\b.dll.
    wpath = "Z:" + str(dll.resolve()).replace("/", "\\")
    env = dict(os.environ)
    env.update({"WINEESYNC": "1", "WINEFSYNC": "1",
                "WINEPREFIX": str(active_prefix()),
                "WINEDEBUG": os.environ.get("WINEDEBUG", "-all")})
    LOGS.mkdir(parents=True, exist_ok=True)
    with open(LOGS / "injector.log", "a") as log:
        log.write(f"\n--- inject {dll} ---\n")
        log.flush()
        try:
            r = subprocess.run(
                [str(wine), str(inj), wpath, "Minecraft.Windows.exe"],
                env=env, stdout=log, stderr=subprocess.STDOUT, timeout=60)
        except subprocess.TimeoutExpired:
            raise BolError("Injection timed out — is the game still running?")
    if r.returncode == 8:
        raise BolError(
            "Injection timed out while the DLL was being loaded. Minecraft "
            "was left running and the remote path buffer was retained safely; "
            f"details are in {LOGS / 'injector.log'}."
        )
    if r.returncode == 7:
        raise BolError(
            "Injection failed because LoadLibrary could not load the DLL "
            "(code 7). Check that it is 64-bit and its dependencies are "
            f"present — details in {LOGS / 'injector.log'}."
        )
    if r.returncode != 0:
        raise BolError(
            f"Injection infrastructure failed (code {r.returncode}) while "
            "accessing Minecraft or starting the remote loader. The game may "
            f"have exited; details are in {LOGS / 'injector.log'}."
        )
    post_failure = _post_injection_failure()
    if post_failure:
        with open(LOGS / "injector.log", "a") as log:
            log.write(f"ERR post-injection: {post_failure}\n")
        raise BolError(
            f"{dll.name} was loaded, but {post_failure}. Use a client release "
            "made for the exact Minecraft version shown in the launcher; a "
            "client's GitHub 'latest' release may target an older game line. "
            f"Details: {LOGS / 'injector.log'}."
        )
    return dll.name
