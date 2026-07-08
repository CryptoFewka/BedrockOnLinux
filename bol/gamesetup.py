"""bol.gamesetup — the do_setup() orchestration and post-mortem diagnose()."""
# SPDX-License-Identifier: MIT

import re
from pathlib import Path

from .auth import msa_signed_in
from .config import LOGS
from .deps import ensure_login_deps
from .fixups import fix_curl_ssl, hide_signin_button, install_gdk_xbox_dlls
from .gameinput import install_gameinput
from .games import _auto_mc_version, _game_root, download_game, use_game_dir
from .log import info, ok, warn
from .prefix import active_prefix, boot_prefix, ensure_umu
from .proton import ensure_proton
from .util import load_settings, mkdirs
from .winegdk import ensure_winegdk

def do_setup(game_dir=None, mc_ver=None, proton_tag=None, pp_tag=None,
             force=False, progress=None):
    mkdirs()
    s = load_settings()
    ensure_login_deps()                        # cryptography for native login
    if mc_ver:                                 # download chosen MC version
        use_game_dir(download_game(mc_ver, progress, force=force))
    elif game_dir and _game_root(Path(game_dir).expanduser()):
        use_game_dir(game_dir)
    # First run, or the configured folder was deleted → auto-(re)install
    # the last/newest version into games/, never anywhere else.
    cur = load_settings().get("game_dir")
    if not cur or not _game_root(Path(cur)):
        use_game_dir(download_game(_auto_mc_version(s), progress, force=force))
    gd = Path(load_settings()["game_dir"])
    if load_settings().get("proton_source") == "winegdk":
        ensure_winegdk(force, progress)
        install_gdk_xbox_dlls(gd)       # before fix_curl_ssl, which keeps XCurl
    else:
        ensure_proton(proton_tag, force, progress)  # user-supplied dir/url
    ensure_umu(force)
    fix_curl_ssl(gd)
    boot_prefix()                              # create the prefix (system32) first
    install_gameinput(active_prefix(), gd)
    hide_signin_button(gd)                     # cosmetic: drop the dead in-game Sign-in button
    ok("Setup complete — click PLAY, then sign in to Microsoft in-game.")


_DIAG_RULES = [
    (r"Unimplemented function combase\.dll\.RoOriginateError|"
     r"RoOriginateErrorW",
     "combase patch missing — re-run 'Install / Update'."),
    (r"NtQueryWnfStateData|Unimplemented function .*aborting",
     "ntdll patch missing — re-run 'Install / Update'."),
    (r"vkGetPhysicalDeviceSurfaceFormatsKHR|Can't open display|x11drv: Can't",
     "Display unavailable (no X/Wayland server)."),
    (r"VK_ERROR_DEVICE_LOST|device removed|DXGI_ERROR|vkd3d.*fatal|"
     r"VK_ERROR_OUT_OF_DEVICE_MEMORY",
     "GPU/Vulkan crash — update the driver or lower graphics."),
    (r"Cannot allocate memory|OutOfMemory|std::bad_alloc",
     "Out of memory (RAM/VRAM)."),
    (r"wineserver.*version mismatch|wineserver binary was not upgraded",
     "Broken WineGDK packaging (wine vs wineserver mismatch) — rebuild the "
     "engine: 'bedrock-on-linux setup --force'."),
    (r"Authentication failed|invalid_grant|login.*failed",
     "Microsoft sign-in failed in-game — sign in again "
     "(open the link, enter the code)."),
    # Must come BEFORE the nodrv_CreateWindow rule: when SystemFunction036 is
    # unresolved, every Wine service and explorer.exe abort on RtlGenRandom, and
    # the *symptom* is a nodrv_CreateWindow / "explorer failed to start". That is
    # NOT a broken prefix or a GPU fault, so resetting the prefix can't fix it.
    (r"unimplemented function advapi32\.dll\.SystemFunction036|"
     r"forward 'cryptbase\.SystemFunction036'|"
     r"module not found for forward 'cryptbase",
     "Wine RNG unresolved (cryptbase.SystemFunction036) — re-run "
     "'Install / Update'; the builtin-cryptbase fallback fixes this on relaunch."),
    (r"nodrv_CreateWindow|no driver could be loaded|"
     r"explorer process failed to start",
     "Wine prefix broken (Wine couldn't open a window)."),
    # Old GDK-Proton without the WineGDK XUser fork → xgameruntime stubs the
    # XUser calls. Require the xgameruntime/XUser context: a bare 0x80004001 /
    # E_NOTIMPL also appears in benign WindowsAppRuntime bootstrapper messages
    # ("Bootstrapper initialization failed looking for version 1.8"), and the
    # diagnose() guard below also drops this hit when the engine's own XUser
    # patches are in the log.
    (r"XUserAddAsync|QueryApiImpl.*unimpl|"
     r"xgameruntime:.*(?:unimpl|stub|not implemented|E_NOTIMPL|0x80004001)",
     "The GDK-Proton in use has no WineGDK XUser — reinstall the engine: "
     "'bedrock-on-linux setup --force'."),
]


def diagnose():
    """Scan game logs and surface a likely cause."""
    blobs = []
    for p in (LOGS / "proton.log", LOGS / "minecraft.log",
              LOGS / "winegdk-build.log"):
        if p.exists():
            try:
                blobs.append(p.read_text(errors="ignore")[-200000:])
            except Exception:
                pass
    text = "\n".join(blobs)
    hits = [msg for pat, msg in _DIAG_RULES if re.search(pat, text, re.I)]
    # Positive evidence wins: if the engine logged its XUser patches or a
    # successful pre-auth, WineGDK XUser IS present — drop any "no XUser" hit so
    # a benign HRESULT elsewhere can't tell the user to reinstall a working
    # engine.
    if re.search(r"InitializeApiImplEx2 patched|preauth: loaded user/XSTS", text):
        hits = [h for h in hits if "no WineGDK XUser" not in h]
    # Software-rendering fallback: if DXVK/vkd3d only found llvmpipe (Mesa's CPU
    # rasteriser) and no real GPU Vulkan device, the GPU driver isn't active in
    # the container. The game then runs on the CPU — slow, and the OreUI Play
    # screen renders as black panels (the simple main menu still shows, so it
    # looks baffling rather than obviously GPU-related). Usual cause: a GPU
    # driver left in a bad state (fixed by a reboot / driver reinstall) or a
    # missing Vulkan ICD. Only flag when EVERY device found is llvmpipe.
    devices = re.findall(r"Found device:\s*(.+)", text)
    if devices and all("llvmpipe" in d.lower() for d in devices):
        hits.append("Running on software rendering (llvmpipe) — your GPU's "
                    "Vulkan driver isn't active, so the game runs on the CPU "
                    "(slow, and the Play screen may render black). Reboot, or "
                    "(re)install/enable your GPU's Vulkan drivers.")
    if not msa_signed_in():
        hits.append("No Microsoft account linked — click 'Sign in' "
                    "before PLAY.")
    if hits:
        warn("Likely cause:")
        for h in dict.fromkeys(hits):
            warn("  • " + h)
    else:
        info(f"No known cause. Logs: {LOGS}")
    return hits
