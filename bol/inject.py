"""bol.inject — run a Windows DLL injector inside the game's Wine prefix.

Loading a client .dll (OderSo, Flarial, Horion, …) into Minecraft Bedrock needs
a standard Windows injector .exe. A plain `wine injector.exe` is useless here:
Wine spins up a SEPARATE per-process container, so the injector never sees the
running Minecraft.Windows.exe. The fix (community tutorial) is to launch the
injector through the SAME engine `wine` binary, the SAME WINEPREFIX and the same
ESYNC/FSYNC flags as the game — then it shares the game's wineserver and the
process is visible. This needs the game's wineserver reachable from the host, so
it works on the native / AppImage installs but NOT inside the Flatpak sandbox.
"""
# SPDX-License-Identifier: MIT

import os
import subprocess
from pathlib import Path

from .config import LOGS
from .log import BolError
from .prefix import _mc_running, active_prefix
from .proton import proton_path


def run_injector(injector_exe):
    """Launch a Windows DLL injector so it can see — and inject into — the
    running Minecraft. The game must already be running (main menu reached); the
    user picks the target .dll inside the injector's own window. Returns the
    injector's file name. Raises BolError with a user-facing reason on failure."""
    exe = Path(injector_exe).expanduser()
    if not exe.is_file():
        raise BolError(f"Injector not found: {exe}")
    if not _mc_running():
        raise BolError("Start Minecraft first and wait for the main menu, then "
                       "run the injector.")
    pp = proton_path()
    if not pp:
        raise BolError("GDK-Proton engine missing — run Install / Update first.")
    wine = pp / "files/bin/wine"
    if not wine.exists():
        raise BolError(f"Engine wine binary not found ({wine}).")
    # Same prefix + engine + sync flags as the game → injector shares its
    # wineserver and sees Minecraft.Windows.exe. active_prefix() (not a hard-coded
    # path) so it matches whichever prefix the game actually launched in.
    env = dict(os.environ)
    env.update({"WINEESYNC": "1", "WINEFSYNC": "1",
                "WINEPREFIX": str(active_prefix()),
                "WINEDEBUG": os.environ.get("WINEDEBUG", "-all")})
    LOGS.mkdir(parents=True, exist_ok=True)
    log = open(LOGS / "injector.log", "a")
    # Detached: the injector has its own window and outlives this call.
    subprocess.Popen([str(wine), str(exe)], env=env,
                     stdout=log, stderr=subprocess.STDOUT)
    return exe.name
