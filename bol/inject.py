"""bol.inject — inject a client .dll into the running Minecraft, natively.

Loading a client .dll (OderSo, Flarial, Horion, …) into Minecraft Bedrock is the
classic CreateRemoteThread + LoadLibraryW trick. BedrockOnLinux ships a tiny
injector (bol/injector.exe, built from src/injector.c) and runs it inside the
game's OWN Wine prefix — same engine, same WINEPREFIX, same ESYNC/FSYNC — so it
shares the game's wineserver and can see Minecraft.Windows.exe. No third-party
injector .exe needed; you just pick the .dll. This needs the game's wineserver
reachable from the host, so it works on the native / AppImage installs but NOT
inside the Flatpak sandbox.
"""
# SPDX-License-Identifier: MIT

import os
import pkgutil
import subprocess
from pathlib import Path

from .config import CACHE, LOGS
from .log import BolError
from .prefix import _mc_running, active_prefix
from .proton import proton_path


def _extract_injector():
    """Write the bundled injector.exe out as a real file Wine can run (works from
    a checkout, a .pyz zipapp, a .deb, an AppImage or a Flatpak alike)."""
    blob = pkgutil.get_data("bol", "injector.exe")
    if not blob:
        raise BolError("Bundled injector.exe is missing from this install.")
    CACHE.mkdir(parents=True, exist_ok=True)
    inj = CACHE / "injector.exe"
    if not inj.exists() or inj.stat().st_size != len(blob):
        inj.write_bytes(blob)
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
    if r.returncode != 0:
        raise BolError(f"Injection failed (code {r.returncode}). Check the .dll "
                       "is 64-bit and its dependencies are present — details in "
                       f"{LOGS / 'injector.log'}.")
    return dll.name
