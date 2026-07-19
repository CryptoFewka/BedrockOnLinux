"""bol.launch — launching Minecraft through Proton/umu."""
# SPDX-License-Identifier: MIT

import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path

from .auth import (
    account_epoch_is_current,
    msa_refresh,
    msa_save_for_account_epoch,
    msa_session_snapshot,
    wine_apply_winegdk_prereqs,
    wine_reg_set_refresh_token,
    xbl_preauth,
)
from .config import CONTENT, DATA, HOME, LOGS, WINEGDK_BUILD_REV
from .deps import ensure_login_deps
from .fixups import _install_cryptbase_in_prefix, bump_stack_reserve
from .gameinput import install_gameinput
from .gamesetup import diagnose
from .gpu_safety import (
    arm_gpu_launch,
    disarm_gpu_launch,
    mark_gpu_wrapper_returned,
    require_safe_graphics_session,
    retire_idle_current_boot_marker,
)
from .log import BolError, die, info, ok, warn
from .prefix import (
    active_prefix,
    boot_prefix,
    launch_lock,
    patch_options,
    prefix_processes,
    proton_umu_cmd,
)
from .proton import custom_proton, patch_proton, proton_path
from .util import _screen_wh, apply_custom_env, load_settings
from .vkd3d import prepare_universal_vkd3d
from .winegdk import ensure_winegdk


def _prepare_graphics_engine():
    """Activate the universal DGC pair without opening Vulkan in the launcher."""
    if custom_proton():
        return None
    try:
        variant, changed = prepare_universal_vkd3d(
            proton_path(), WINEGDK_BUILD_REV)
    except BolError as exc:
        # The CLI catches BolError, so emit the actionable manifest error here.
        die(str(exc))
    info(f"Graphics command path: {variant}"
         + (" (activated)." if changed else " (already active)."))
    return variant


def _require_vkd3d_config(env, option):
    """Add one vkd3d option without discarding user-provided options."""
    options = [item.strip() for item in
               env.get("VKD3D_CONFIG", "").replace(";", ",").split(",")
               if item.strip()]
    if option not in options:
        options.append(option)
    env["VKD3D_CONFIG"] = ",".join(options)


def _prefix_stably_idle_after_wrapper(timeout=10.0, interval=0.1,
                                      confirmations=3):
    """Confirm UMU did not detach a live Wine child when its wrapper returned."""

    prefix = active_prefix()
    deadline = time.monotonic() + max(0.0, timeout)
    empty_scans = 0
    while True:
        try:
            live = prefix_processes(prefix)
        except Exception:
            return False
        if live:
            empty_scans = 0
        else:
            empty_scans += 1
            if empty_scans >= max(1, confirmations):
                return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(max(0.0, interval), remaining))


def _prepare_launch_engine():
    """Make the selected engine safe before any Wine process is executed."""
    running = prefix_processes(active_prefix())
    if running:
        die("The BedrockOnLinux Wine prefix is already active "
            f"({len(running)} process(es)). Close Minecraft or use the "
            "explicit 'Force stop Minecraft' action before launching again.")
    managed_engine = not custom_proton()
    if managed_engine:
        ensure_winegdk()
    else:
        patch_proton(proton_path(), strict=False)
    return _prepare_graphics_engine()


def _launch_once():
    s = load_settings()
    gd = s.get("game_dir")
    if not gd or not Path(gd, "Minecraft.Windows.exe").exists():
        die("No game — choose a Minecraft version first.")
    if not proton_path():
        die("GDK-Proton missing — run Install / Update.")
    # Installing, hashing, and wiring the managed engine does not open a GPU.
    # Do it before graphics validation so a corrected engine can migrate state
    # left by the previous revision without bypassing kernel/display checks.
    _prepare_launch_engine()
    # launch() holds the launch lock, and preparation just proved that no
    # process owns the prefix. Retire only a same-boot marker whose wrapper
    # completion was durably recorded; the full safety gate runs immediately.
    retire_idle_current_boot_marker()
    require_safe_graphics_session()

    account, account_epoch = msa_session_snapshot()
    tok = account.get("refresh_token")
    if not tok:
        die("No Microsoft account linked — click 'Sign in' first.")
    try:
        fresh = msa_refresh(tok)
    except Exception as e:
        fresh = None
        warn(f"Token refresh skipped ({e}) — using cached token.")
    if fresh:
        if not msa_save_for_account_epoch(
                {"refresh_token": fresh["refresh_token"],
                 "obtained": int(time.time())}, account_epoch):
            die("The Microsoft account changed during launch; no stale token "
                "was stored. Click PLAY again after signing in.")
        tok = fresh["refresh_token"]
    if not boot_prefix():
        die("Could not initialise the managed Wine prefix safely.")
    wine_apply_winegdk_prereqs()
    _install_cryptbase_in_prefix()
    try:
        install_gameinput(active_prefix(), Path(gd))
    except Exception as e:
        warn(f"GameInput check failed ({e}) — continuing.")
    if not wine_reg_set_refresh_token(tok):
        die("Could not write the Microsoft login token into the Wine prefix. "
            "The offline registry was left unchanged; use Repair and try "
            "again.")
    access = (fresh or {}).get("access_token")
    ensure_login_deps()
    if not xbl_preauth(access or "", account_epoch):
        die("Could not prepare a complete Xbox Live multiplayer session. "
            "Check the Microsoft account/network connection and try again.")
    exe = str(CONTENT / "Minecraft.Windows.exe")
    bump_stack_reserve(Path(exe))
    cmd, env = proton_umu_cmd(exe)
    env["PROTON_LOG"] = "1"
    env["PROTON_LOG_DIR"] = str(LOGS)
    # Required by the menu's indirect root-CBV updates (#27/#29/#30).
    _require_vkd3d_config(env, "force_raw_va_cbv")
    diag = (s.get("diagnostics", False) or os.environ.get("BOL_DIAG") == "1")
    # Keep diagnostics focused on the native GDK contracts. Raw WinHTTP trace
    # includes Authorization headers and must never be enabled automatically.
    env["WINEDEBUG"] = (os.environ.get("WINEDEBUG")
                        or ("trace+gdkc,trace+xgameruntime,fixme-all" if diag
                            else "fixme-all"))
    xlog = os.environ.get("BOL_XCURL_LOG")
    if xlog == "1" or (xlog is None and diag):
        env["XCURL_LOG"] = "1"
    # VR runtimes crash the non-VR game; Wine's AMD AGS builtin recurses until
    # stack overflow. cryptbase keeps the native RNG stub with builtin fallback.
    overrides = ["cryptbase=n,b", "vrclient=", "vrclient_x64=", "openvr_api=",
                 "wineopenxr=", "amd_ags_x64="]
    cur = os.environ.get("WINEDLLOVERRIDES", "")
    if cur:
        overrides.append(cur)
    env["WINEDLLOVERRIDES"] = ";".join(overrides)
    # WindowsAppRuntime framework MSIX cannot install under Wine.
    env["MICROSOFT_WINDOWSAPPRUNTIME_BOOTSTRAP_INITIALIZE_SHOWUI"] = "0"
    env["MICROSOFT_WINDOWSAPPRUNTIME_BOOTSTRAP_INITIALIZE_FAILFAST"] = "0"
    env["MICROSOFT_WINDOWSAPPRUNTIME_DEPLOYMENT_INITIALIZE_ONERRORSHOWUI"] = "0"
    # Azure rejects Wine's TLS 1.3 ClientHello; force TLS 1.2 for this process.
    prio = DATA / "etc" / "gnutls-no-tls13.cfg"
    if not prio.exists():
        prio.parent.mkdir(parents=True, exist_ok=True)
        prio.write_text("[priorities]\nSYSTEM = NORMAL:-VERS-TLS1.3:%COMPAT\n")
    env["GNUTLS_SYSTEM_PRIORITY_FILE"] = str(prio)
    env["GNUTLS_SYSTEM_PRIORITY_FAIL_ON_INVALID"] = "0"
    preauth = DATA / "winegdk-preauth" / "device.json"
    if preauth.exists():
        env["WINEGDK_PREAUTH_DEVICE"] = "Z:" + str(preauth).replace("/", "\\")
    rp = s.get("xsts_rp")
    if rp:
        host = s.get("xsts_rp_host") or "b980a380.minecraft.playfabapi.com"
        san = "".join(c.upper() if c.isalnum() else "_" for c in host)
        env["WINEGDK_XSTS_RP_" + san] = rp
        info(f"XSTS relying party override [{host}] = {rp}")
    # Also protects non-GUI callers from a concurrent account change.
    if not account_epoch_is_current(account_epoch):
        die("The Microsoft account changed during launch. Minecraft was not "
            "started; click PLAY again with the current account.")
    wl = os.environ.get("WAYLAND_DISPLAY")
    backend = (os.environ.get("BOL_INPUT")
               or s.get("input_backend") or "auto").lower()
    if backend == "auto":
        backend = "x11"
    gs_opt = s.get("gamescope") or os.environ.get("BOL_GAMESCOPE")
    want_gamescope = bool(gs_opt) and \
        gs_opt.lower() not in ("0", "no", "off", "false")
    use_gamescope = want_gamescope and bool(shutil.which("gamescope"))
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
        env.pop("DISPLAY", None)
        mon = (os.environ.get("BOL_WAYLAND_MONITOR")
               or os.environ.get("WAYLANDDRV_PRIMARY_MONITOR"))
        if mon:
            env["WAYLANDDRV_PRIMARY_MONITOR"] = mon
        warn("BOL_INPUT=wayland → winewayland (experimental). If it can't "
             "open a window no automatic GPU relaunch is attempted; "
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
        elif wl:
            warn("Wayland session without X DISPLAY — install XWayland (or set "
                 "BOL_INPUT=wayland to use winewayland).")
    if use_gamescope:
        if gs_opt and gs_opt.strip().lower() not in ("1", "yes", "on", "true"):
            gs_argv = ["gamescope"] + shlex.split(gs_opt)
        else:
            gs_argv = ["gamescope", "-f"]
            wh = _screen_wh()
            if wh:
                gs_argv += ["-W", wh[0], "-H", wh[1], "-w", wh[0], "-h", wh[1]]
        cmd = gs_argv + ["--"] + cmd
        info("Using gamescope (BOL_GAMESCOPE).")
    if not account_epoch_is_current(account_epoch):
        die("The Microsoft account changed before the game process started. "
            "Minecraft was not started; click PLAY again.")
    apply_custom_env(env, s.get("custom_env") or "")
    info("Starting Minecraft … sign in with Microsoft in-game, then "
         "join your server from the Servers tab.")
    glog = open(LOGS / "minecraft.log", "w")
    rc = None
    hits = []
    gpu_marker_token = None
    game_returned = False
    try:
        # A hard reboot leaves this marker so the next launch fails closed.
        gpu_marker_token = arm_gpu_launch()
        try:
            proc = subprocess.Popen(cmd, env=env, cwd=str(CONTENT), stdout=glog,
                                    stderr=subprocess.STDOUT)
        except Exception:
            # No process handle means the GPU launch never began.
            try:
                if not disarm_gpu_launch(gpu_marker_token):
                    warn("The game process could not be started and its GPU "
                         "safety marker could not be cleared. Close the "
                         "launcher, then inspect the marker with Doctor.")
            except Exception as marker_error:
                warn("The game process could not be started and clearing its "
                     "GPU safety marker failed (%s)." %
                     type(marker_error).__name__)
            raise
        started = time.time()
        announced = False
        while True:
            try:
                rc = proc.wait(timeout=1)
                game_returned = True
                break
            except subprocess.TimeoutExpired:
                if not announced and time.time() - started > 8:
                    announced = True
                    ok("Minecraft is running — close the game window to come "
                       "back here.")
    finally:
        if game_returned and gpu_marker_token:
            try:
                wrapper_returned_recorded = mark_gpu_wrapper_returned(
                    gpu_marker_token)
            except Exception as marker_error:
                wrapper_returned_recorded = False
                warn("Minecraft returned, but recording its GPU-marker phase "
                     "failed (%s)." % type(marker_error).__name__)
            if not wrapper_returned_recorded:
                warn("Minecraft returned, but its GPU marker could not record "
                     "the completed wrapper phase. A failed teardown will "
                     "require explicit Doctor acknowledgement.")
            if not _prefix_stably_idle_after_wrapper():
                warn("The UMU wrapper returned while Wine/Minecraft processes "
                     "still appear live. The GPU safety marker was retained; "
                     "force-stop the remaining processes and inspect the "
                     "driver before acknowledging the incident.")
            elif not disarm_gpu_launch(gpu_marker_token):
                warn("Minecraft returned, but its GPU safety marker could not "
                     "be cleared. Run 'bedrock-on-linux doctor "
                     "--acknowledge-gpu-crash' after checking the driver.")
        glog.close()
        patch_options()
        logs = sorted(LOGS.glob("steam-*.log"),
                      key=lambda p: p.stat().st_mtime if p.exists() else 0)
        if logs:
            logs[-1].replace(LOGS / "proton.log")
            for old in logs[:-1]:
                old.unlink(missing_ok=True)
        ok(f"Game closed (exit {rc}).")
        hits = diagnose()
    # Diagnose only; never reset or relaunch a GPU process automatically.
    broken = any("prefix broken" in h.lower() for h in hits)
    no_display = any("display unavailable" in h.lower() for h in hits)
    rng_abort = any("rng unresolved" in h.lower() for h in hits)
    wayland_attempt = env.get("PROTON_ENABLE_WAYLAND") == "1"
    if use_gamescope:
        ml = LOGS / "minecraft.log"
        ran = ml.exists() and "umu-launcher" in ml.read_text(errors="ignore")[:8000]
        if broken or not ran:
            warn("gamescope could not present the game. Automatic relaunch is "
                 "disabled for GPU safety; turn off BOL_GAMESCOPE and click "
                 "PLAY once after checking the logs.")
    if rng_abort:
        warn("The window failure came from the cryptbase RNG abort, not a broken "
             "prefix or GPU — relaunch (builtin cryptbase now provides "
             "RtlGenRandom).")
    elif wayland_attempt and broken:
        warn("winewayland could not open a window. Automatic XWayland relaunch "
             "is disabled for GPU safety; set BOL_INPUT=x11, then click PLAY "
             "once after checking the display.")
    elif broken and not no_display:
        warn("The Wine prefix may be broken. Automatic reset/relaunch is "
             "disabled for GPU safety; use the explicit Repair action, then "
             "click PLAY once.")
    return rc


def launch():
    """Run exactly one guarded launch for each user action."""
    with launch_lock():
        return _launch_once()
