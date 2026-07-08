"""bol.launch — launching Minecraft through Proton/umu."""
# SPDX-License-Identifier: MIT

import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path

from .auth import (
    msa_load,
    msa_refresh,
    msa_save,
    wine_apply_winegdk_prereqs,
    wine_reg_set_refresh_token,
    xbl_preauth,
)
from .config import CONTENT, DATA, HOME, LOGS
from .deps import ensure_login_deps
from .fixups import _install_cryptbase_in_prefix, bump_stack_reserve
from .gameinput import install_gameinput
from .gamesetup import diagnose
from .log import die, info, ok, warn
from .prefix import (
    active_prefix,
    boot_prefix,
    kill_wine,
    patch_options,
    proton_umu_cmd,
    reset_prefix,
)
from .proton import custom_proton, patch_proton, proton_path
from .util import _screen_wh, load_settings

def launch(_pp=None, _repaired=False, _force_x11=False, _no_gamescope=False):
    s = load_settings()
    gd = s.get("game_dir")
    if not gd or not Path(gd, "Minecraft.Windows.exe").exists():
        die("No game — choose a Minecraft version first.")
    if not proton_path():
        die("GDK-Proton missing — run Install / Update.")
    patch_proton(proton_path(), strict=not custom_proton())

    tok = msa_load().get("refresh_token")
    if not tok:
        die("No Microsoft account linked — click 'Sign in' first.")
    try:
        fresh = msa_refresh(tok)
    except Exception as e:
        fresh = None
        warn(f"Token refresh skipped ({e}) — using cached token.")
    if fresh:
        msa_save({"refresh_token": fresh["refresh_token"],
                  "obtained": int(time.time())})
        tok = fresh["refresh_token"]
    wine_apply_winegdk_prereqs()
    boot_prefix()                  # ensure system32 exists before writing into it
    _install_cryptbase_in_prefix()
    # Heal prefixes from ≤ 1.0.9 where the native GameInput redist never
    # actually installed (the old check matched wineboot's pre-seeded builtin
    # gameinput.dll) — without it the in-game mouse/controller are dead.
    try:
        install_gameinput(active_prefix(), Path(gd))
    except Exception as e:
        warn(f"GameInput check failed ({e}) — continuing.")
    wine_reg_set_refresh_token(tok)
    # Bypass Wine GnuTLS for the JA3-fingerprinted /device/authenticate call:
    # do it from the host Python's OpenSSL stack and persist the token where
    # xgameruntime.dll's DeviceAuth_Initialize will read it instead of POSTing.
    # If this fails the C side falls back to the broken Wine path (still
    # better than crashing the launch).
    access = (fresh or {}).get("access_token") if fresh else None
    ensure_login_deps()                 # make sure `cryptography` is importable
    xbl_preauth(access or "")
    kill_wine()
    exe = str(CONTENT / "Minecraft.Windows.exe")
    bump_stack_reserve(Path(exe))    # issue #27: settings/pause stack overflow
    cmd, env = proton_umu_cmd(exe)
    env["PROTON_LOG"] = "1"          # always: proton.log feeds diagnose()
    env["PROTON_LOG_DIR"] = str(LOGS)
    # "Advanced diagnostics" — the Settings toggle (or BOL_DIAG=1): verbose
    # Wine/HTTP traces in proton.log plus the OpenSSL XCurl request log. Off by
    # default so a normal session stays quiet; turn it on to capture detail for
    # a bug report or to reverse-engineer the in-game social layer.
    diag = (s.get("diagnostics", False) or os.environ.get("BOL_DIAG") == "1")
    # +gdkc,+winhttp surface PlayFab/SISU HTTP statuses in proton.log (the
    # in-game LoginWithXbox flow runs over WinHTTP). fixme+all is verbose, so
    # keep it for diagnostics; otherwise keep errors but drop the fixme spam.
    #
    # CRUCIAL: `trace-gdkc` (disable *trace* on the gdkc channel; err/warn/fixme
    # stay). Plain `+gdkc` also turns on trace, and the game polls
    # XGameRuntimeIsFeatureAvailable in a tight init loop — each poll emits 4
    # gdkc TRACE lines. That floods the log (500k+ lines in a minute) and
    # bottlenecks the game on synchronous debug I/O so init never completes: no
    # window renders and menus crash. Diagnostics-on then looks like a totally
    # broken game (esp. on 1.26.32.x), while diagnostics-off is fine — which is
    # why only people who enabled Advanced Diagnostics hit it. The useful gdkc
    # signal (preauth/token/RP routing) is all at err level, so we lose nothing.
    env["WINEDEBUG"] = (os.environ.get("WINEDEBUG")
                        or ("+gdkc,trace-gdkc,+winhttp,fixme+all" if diag
                            else "fixme-all"))
    # The OpenSSL XCurl shim writes "DONE rc=.. http=.. url=.." per request to
    # <game>/xcurl.log when XCURL_LOG=1 (friends/profiles/realms ride XCurl,
    # invisible to WinHTTP traces). BOL_XCURL_LOG=1/0 overrides the toggle.
    xlog = os.environ.get("BOL_XCURL_LOG")
    if xlog == "1" or (xlog is None and diag):
        env["XCURL_LOG"] = "1"
    # cryptbase=n,b: prefer our cryptbase.dll RNG stub (installed in system32
    # by _install_cryptbase_in_prefix) so OpenSSL XCurl's RtlGenRandom forward
    # resolves, but FALL BACK to the GDK-Proton builtin when the stub is
    # absent — native-only would leave SystemFunction036 unresolvable and
    # every Wine service + explorer.exe aborts (looks like a broken prefix).
    # Minecraft Bedrock probes VR at startup (OpenVR, then OpenXR) but is not a
    # VR title on Linux. Proton wires up both runtimes, and leaving them enabled
    # crashes the launch (issue #14): the OpenVR/SteamVR shim asserts in
    # vrclient_main.c (and opens Steam); once that's disabled the game falls
    # through to OpenXR, whose Wine runtime (wineopenxr) can't hook Vulkan
    # ("get_native_VkDevice not found") so the game page-faults on the invalid
    # handle. Disable every VR runtime so both probes fail cleanly and the game
    # runs flat. (openxr_loader is left alone — the EXE may import it; we only
    # drop the runtime behind it.) Empty value == disabled in WINEDLLOVERRIDES;
    # host WINEDLLOVERRIDES are kept if the user set any.
    # amd_ags_x64: the game loads it unconditionally (even on Intel/NVIDIA), and
    # Wine's builtin recurses in get_ags_version_from_resource (agsInit → 100s of
    # nested calls, err 1812) until the stack overflows and the launch crashes a
    # few seconds in. AGS is optional AMD-GPU services the game runs fine without,
    # so disable it so the load fails cleanly instead.
    overrides = ["cryptbase=n,b", "vrclient=", "vrclient_x64=", "openvr_api=",
                 "wineopenxr=", "amd_ags_x64="]
    cur = os.environ.get("WINEDLLOVERRIDES", "")
    if cur:
        overrides.append(cur)
    env["WINEDLLOVERRIDES"] = ";".join(overrides)
    # WindowsAppRuntime's framework MSIX can never install under Wine, so its
    # two init paths (Bootstrap + DeploymentManager) always fail — mute their
    # "Install?" dialogs AND the Bootstrap failfast via the env gates each
    # DLL checks, so the game keeps running.
    env["MICROSOFT_WINDOWSAPPRUNTIME_BOOTSTRAP_INITIALIZE_SHOWUI"] = "0"
    env["MICROSOFT_WINDOWSAPPRUNTIME_BOOTSTRAP_INITIALIZE_FAILFAST"] = "0"
    env["MICROSOFT_WINDOWSAPPRUNTIME_DEPLOYMENT_INITIALIZE_ONERRORSHOWUI"] = "0"
    # libHttpClient overrides our HKLM DefaultSecureProtocols via
    # WinHttpSetOption, so drop TLS 1.3 at the GnuTLS level instead (Azure
    # rejects Wine's TLS 1.3 ClientHello → sign-in loops). %COMPAT loosens
    # cipher matching for the older Azure stack.
    prio = DATA / "etc" / "gnutls-no-tls13.cfg"
    if not prio.exists():
        prio.parent.mkdir(parents=True, exist_ok=True)
        prio.write_text("[priorities]\nSYSTEM = NORMAL:-VERS-TLS1.3:%COMPAT\n")
    env["GNUTLS_SYSTEM_PRIORITY_FILE"] = str(prio)
    env["GNUTLS_SYSTEM_PRIORITY_FAIL_ON_INVALID"] = "0"
    # Hand DeviceAuth_Initialize the host-side pre-auth blob (written by
    # xbl_preauth above) via Wine's Z: drive, so the C side skips the broken
    # Wine-side /device/authenticate POST.
    preauth = DATA / "winegdk-preauth" / "device.json"
    if preauth.exists():
        env["WINEGDK_PREAUTH_DEVICE"] = "Z:" + str(preauth).replace("/", "\\")
    # Tune the PlayFab XSTS relying party WITHOUT rebuilding WineGDK
    # (resolver honours WINEGDK_XSTS_RP_<HOST>). Iterate via
    # `config --xsts-rp <value>`.
    rp = s.get("xsts_rp")
    if rp:
        host = s.get("xsts_rp_host") or "b980a380.minecraft.playfabapi.com"
        san = "".join(c.upper() if c.isalnum() else "_" for c in host)
        env["WINEGDK_XSTS_RP_" + san] = rp
        info(f"XSTS relying party override [{host}] = {rp}")
    # Display + input backend. The in-game mouse goes through the NATIVE
    # Microsoft GameInput redist (install_gameinput) reading Win32 raw input,
    # which works on X11 and XWayland alike — the historic "mouse dead on
    # Wayland" was the Wine builtin gameinput.dll (no mouse backend) taking
    # over on prefixes where the redist never installed, not a display-server
    # issue. Default is X11/XWayland (starts everywhere); winewayland stays
    # an experiment via BOL_INPUT=wayland (auto-falls back if no window).
    wl = os.environ.get("WAYLAND_DISPLAY")
    backend = (os.environ.get("BOL_INPUT")
               or load_settings().get("input_backend") or "auto").lower()
    if backend == "auto":
        backend = "x11"               # never auto-pick wayland (see above)
    if _force_x11:
        backend = "x11"
    # gamescope wrapping is opt-in (BOL_GAMESCOPE=1, or a literal gamescope
    # option string). 1.0.9 auto-enabled it on Wayland hoping its nested
    # Xwayland would revive the mouse — field logs showed the mouse stayed
    # dead under gamescope too (the real culprit was the builtin GameInput,
    # above), so the auto is gone. The post-launch handler still retries
    # without it if it can't present the game.
    gs_opt = os.environ.get("BOL_GAMESCOPE")
    want_gamescope = bool(gs_opt) and \
        gs_opt.lower() not in ("0", "no", "off", "false")
    use_gamescope = (want_gamescope and not _no_gamescope
                     and bool(shutil.which("gamescope")))
    if use_gamescope:
        backend = "x11"
    elif want_gamescope and not shutil.which("gamescope"):
        warn("BOL_GAMESCOPE is set but gamescope isn't installed — ignored.")
    disp = os.environ.get("DISPLAY")
    if backend == "wayland" and wl:
        env["PROTON_ENABLE_WAYLAND"] = "1"
        env["WAYLAND_DISPLAY"] = wl
        xrd = os.environ.get("XDG_RUNTIME_DIR")
        if xrd:
            env["XDG_RUNTIME_DIR"] = xrd
        # winewayland only engages when DISPLAY is unset.
        env.pop("DISPLAY", None)
        # GE-Proton probes the primary monitor via xrandr, which fails with
        # no X server → no window; naming the output skips that probe.
        mon = (os.environ.get("BOL_WAYLAND_MONITOR")
               or os.environ.get("WAYLANDDRV_PRIMARY_MONITOR"))
        if mon:
            env["WAYLANDDRV_PRIMARY_MONITOR"] = mon
        warn("BOL_INPUT=wayland → winewayland (experimental). If it can't "
             "open a window the launcher falls back to XWayland automatically; "
             "to help winewayland connect first try BOL_WAYLAND_MONITOR=<output> "
             "(e.g. eDP-1).")
    else:
        if backend == "wayland":
            warn("BOL_INPUT=wayland but no WAYLAND_DISPLAY found — using X11.")
        if disp:
            env["DISPLAY"] = disp
            for cand in (os.environ.get("XAUTHORITY"), str(HOME / ".Xauthority"),
                         f"/run/user/{os.getuid()}/.mutter-Xwaylandauth.0"):
                if cand and Path(cand).exists():
                    env["XAUTHORITY"] = cand
                    break
            if shutil.which("xhost"):
                user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
                for arg in (f"+SI:localuser:{user}", "+local:"):
                    try:
                        subprocess.run(["xhost", arg], timeout=5,
                                       stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL)
                    except Exception:
                        pass
        elif wl:
            warn("Wayland session without X DISPLAY — install XWayland (or set "
                 "BOL_INPUT=wayland to use winewayland).")
    if use_gamescope:
        if gs_opt and gs_opt.strip().lower() not in ("1", "yes", "on", "true"):
            gs_argv = ["gamescope"] + shlex.split(gs_opt)      # explicit options
        else:
            gs_argv = ["gamescope", "-f"]
            wh = _screen_wh()                                  # native res, else -f only
            if wh:
                gs_argv += ["-W", wh[0], "-H", wh[1], "-w", wh[0], "-h", wh[1]]
        cmd = gs_argv + ["--"] + cmd
        info("Using gamescope (BOL_GAMESCOPE).")
    info("Starting Minecraft … sign in with Microsoft in-game, then "
         "join your server from the Servers tab.")
    glog = open(LOGS / "minecraft.log", "w")
    rc = None
    hits = []
    try:
        proc = subprocess.Popen(cmd, env=env, cwd=str(CONTENT), stdout=glog,
                                stderr=subprocess.STDOUT)
        # Once the game has survived its launch, flip the UI to a steady
        # "running" state instead of spinning "Starting…" for the whole
        # session — the launcher has nothing left to do until the game exits.
        started = time.time()
        announced = False
        while True:
            try:
                rc = proc.wait(timeout=1)
                break
            except subprocess.TimeoutExpired:
                if not announced and time.time() - started > 8:
                    announced = True
                    ok("Minecraft is running — close the game window to come "
                       "back here.")
    finally:
        glog.close()
        patch_options()
        # PROTON_LOG writes steam-<id>.log — keep the newest as proton.log
        # (the fixed name made us read stale logs after a crash).
        logs = sorted(LOGS.glob("steam-*.log"),
                      key=lambda p: p.stat().st_mtime if p.exists() else 0)
        if logs:
            logs[-1].replace(LOGS / "proton.log")
            for old in logs[:-1]:
                old.unlink(missing_ok=True)
        ok(f"Game closed (exit {rc}).")
        hits = diagnose()
    # Self-repair: a genuinely broken Wine prefix is reset + relaunched ONCE
    # (_repaired guards the loop). Skipped when the display is unavailable —
    # a reset can't fix that. The login survives: the token lives in
    # DATA/msa, not the prefix.
    broken = any("prefix broken" in h.lower() for h in hits)
    no_display = any("display unavailable" in h.lower() for h in hits)
    rng_abort = any("rng unresolved" in h.lower() for h in hits)
    wayland_attempt = env.get("PROTON_ENABLE_WAYLAND") == "1"
    # gamescope couldn't present the game (never reached umu, or its nested
    # display gave nodrv) → retry once without it so a launch is never lost.
    if use_gamescope and not _no_gamescope:
        ml = LOGS / "minecraft.log"
        ran = ml.exists() and "umu-launcher" in ml.read_text(errors="ignore")[:8000]
        if broken or not ran:
            warn("gamescope couldn't present the game — retrying without it.")
            return launch(_pp, _repaired=_repaired, _no_gamescope=True)
    # An RNG abort makes explorer fail to start, which only *looks* like a
    # broken prefix — a reset won't help; the cryptbase=n,b fallback fixes it
    # on relaunch.
    if rng_abort:
        warn("The window failure came from the cryptbase RNG abort, not a broken "
             "prefix or GPU — relaunch (builtin cryptbase now provides "
             "RtlGenRandom).")
    elif wayland_attempt and broken:
        # winewayland couldn't open a window — the Wayland backend didn't
        # connect, NOT a broken prefix: never reset for this. Relaunch ONCE
        # on XWayland when available (_force_x11 guards the loop).
        if not _force_x11 and os.environ.get("DISPLAY"):
            warn("winewayland couldn't open a window — falling back to XWayland "
                 "so the game still starts. Set BOL_INPUT=x11 to skip the "
                 "attempt next time, or BOL_WAYLAND_MONITOR=<output> to help "
                 "winewayland connect.")
            return launch(_pp, _repaired=_repaired, _force_x11=True)
        warn("winewayland couldn't open a window (BOL_INPUT=wayland) and no X "
             "DISPLAY is available to fall back to — unset BOL_INPUT (or set "
             "BOL_INPUT=x11) and make sure XWayland is running.")
    elif broken and not no_display:
        if not _repaired:
            warn("Broken Wine prefix — repairing and relaunching once…")
            reset_prefix()
            try:
                install_gameinput(active_prefix(),
                                  Path(load_settings()["game_dir"]))
            except Exception as e:
                warn(f"Re-bootstrap after repair failed ({e}).")
            return launch(_pp, _repaired=True, _force_x11=_force_x11)
        warn("Still failing after an automatic repair — likely a GPU/display "
             "issue, not the prefix.")
    return rc
