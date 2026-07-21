"""Fail-closed graphics checks which never open a GPU device.

The launcher must not call Vulkan/OpenGL merely to decide whether Vulkan is
safe: on a broken proprietary driver, that probe itself can panic the kernel.
Everything here is obtained from the existing X server, sysfs, or text logs.
"""
# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Mapping, Optional

from .config import APP, DATA, WINEGDK_BUILD_REV
from .log import BolError, die, warn


GPU_LAUNCH_MARKER = DATA / ".gpu-launch-in-progress.json"
GPU_SAFETY_ACK = DATA / ".gpu-safety-ack.json"
_STATE_VERSION = 2
_LEGACY_MARKER_VERSION = 1


def _truthy(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _x11_session(env: Mapping[str, str]) -> bool:
    session = (env.get("XDG_SESSION_TYPE") or "").strip().lower()
    if session:
        return session == "x11"
    return bool(env.get("DISPLAY")) and not bool(env.get("WAYLAND_DISPLAY"))


def _xrandr_provider_count(env: Mapping[str, str], runner=None) -> Optional[int]:
    binary = shutil.which("xrandr")
    # Supplying a runner is the unit-test seam; it must not depend on whether
    # the test host happens to install xrandr.
    if not binary and runner is not None:
        binary = "xrandr"
    if not binary:
        return None
    runner = runner or subprocess.run
    try:
        result = runner(
            [binary, "--listproviders"],
            env=dict(env),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=4,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if getattr(result, "returncode", 1) != 0:
        return None
    match = re.search(r"Providers:\s*number\s*:\s*(\d+)",
                      getattr(result, "stdout", ""), re.IGNORECASE)
    return int(match.group(1)) if match else None


def _boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return ""


def _sync_parent(path: Path) -> None:
    try:
        fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _read_state(path: Path) -> Optional[dict]:
    """Read a small regular JSON state file, rejecting links/oversized data."""

    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            return None
        with path.open("r", encoding="utf-8") as stream:
            raw = stream.read(65_537)
    except (OSError, UnicodeError):
        return None
    if len(raw) > 65_536:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _write_state_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    staged = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(staged, 0o600)
        os.replace(staged, path)
        _sync_parent(path)
    finally:
        staged.unlink(missing_ok=True)


def _pid_alive(pid) -> bool:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 1:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def interrupted_launch_problem(path: Optional[Path] = None) -> Optional[str]:
    """Describe a launch which never returned to the launcher.

    Presence is authoritative even if the JSON was torn by a power loss.  This
    is deliberately conservative: a stale marker is removed only by the
    explicit doctor acknowledgement after the driver has been repaired.
    """

    marker = Path(path) if path is not None else GPU_LAUNCH_MARKER
    command = f"{APP} doctor --acknowledge-gpu-crash"
    try:
        marker.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        return (
            "the launcher cannot inspect its persistent GPU safety marker; "
            f"repair the data-directory permissions, then run '{command}'"
        )
    state = _read_state(marker)
    if not state:
        return (
            "an interrupted Minecraft launch marker exists but is unreadable; "
            f"after repairing the graphics driver and rebooting, run '{command}'"
        )
    if state.get("version") == _LEGACY_MARKER_VERSION:
        # Schema 1 predates the durable wrapper-return phase. It cannot prove
        # whether Wine merely crashed in userspace (as in issue #31) or the GPU
        # session contributed to a hard lock, so never delete it implicitly.
        same_boot = bool(_boot_id() and state.get("boot_id") == _boot_id())
        if same_boot and _pid_alive(state.get("launcher_pid")):
            return (
                "a legacy Minecraft GPU session is still marked active in "
                "this launcher; close or force-stop it instead of starting a "
                "second session"
            )
        when = ("during this boot" if same_boot
                else "before the last reboot/power loss")
        return (
            f"a legacy Minecraft GPU session did not return cleanly {when}; "
            "the old marker cannot distinguish a Wine crash from a graphics-"
            "driver failure, so after checking the driver and rebooting run "
            f"'{command}' once to acknowledge it"
        )
    if state.get("version") != _STATE_VERSION:
        return (
            "an interrupted Minecraft launch marker has an unsupported format; "
            f"after repairing the graphics driver and rebooting, run '{command}'"
        )
    same_boot = bool(_boot_id() and state.get("boot_id") == _boot_id())
    if same_boot and _pid_alive(state.get("launcher_pid")):
        return (
            "a Minecraft GPU session is still marked active in this launcher; "
            "close or force-stop it instead of starting a second session"
        )
    when = "during this boot" if same_boot else "before the last reboot/power loss"
    return (
        f"the previous Minecraft GPU session did not return cleanly {when}; "
        "do not retry until the host graphics driver is repaired, then run "
        f"'{command}' to acknowledge the incident"
    )


def arm_gpu_launch(path: Optional[Path] = None) -> str:
    """Durably mark a GPU launch immediately before spawning its process."""

    marker = Path(path) if path is not None else GPU_LAUNCH_MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(16)
    payload = {
        "version": _STATE_VERSION,
        "engine_rev": WINEGDK_BUILD_REV,
        "phase": "running",
        "token": token,
        "boot_id": _boot_id(),
        "launcher_pid": os.getpid(),
        "created": int(time.time()),
    }
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(marker, flags, 0o600)
    except FileExistsError as exc:
        raise BolError(
            "A previous Minecraft GPU launch is still marked interrupted. "
            f"After repairing the driver and rebooting, run '{APP} doctor "
            "--acknowledge-gpu-crash'."
        ) from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(marker, 0o600)
        _sync_parent(marker)
    except Exception:
        marker.unlink(missing_ok=True)
        raise
    return token


def disarm_gpu_launch(token: str, path: Optional[Path] = None) -> bool:
    """Clear only the marker owned by a GPU process which returned normally."""

    marker = Path(path) if path is not None else GPU_LAUNCH_MARKER
    state = _read_state(marker)
    if not state or not secrets.compare_digest(str(state.get("token", "")), token):
        return False
    try:
        marker.unlink()
    except OSError:
        return False
    _sync_parent(marker)
    return True


def mark_gpu_wrapper_returned(
        token: str, path: Optional[Path] = None) -> bool:
    """Persist that UMU returned before checking Wine's final teardown."""

    marker = Path(path) if path is not None else GPU_LAUNCH_MARKER
    state = _read_state(marker)
    if (not state or state.get("version") != _STATE_VERSION
            or state.get("phase") != "running"
            or not secrets.compare_digest(str(state.get("token", "")), token)):
        return False
    payload = dict(state)
    payload["phase"] = "wrapper_returned"
    payload["wrapper_returned"] = int(time.time())
    try:
        _write_state_atomic(marker, payload)
    except OSError:
        return False
    return True


def retire_idle_current_boot_marker(path: Optional[Path] = None) -> bool:
    """Clear an orphan marker only after the caller proved the prefix idle.

    UMU can return just before Wine's last helper or wineserver finishes its
    normal shutdown.  If that exceeds the launcher's grace period, retaining
    the marker is correct while those processes are live but must not turn
    into a permanent same-boot block after the prefix has stopped. The launch
    lock and idle-prefix check are owned by the caller. Only a marker which
    durably records that the wrapper returned is eligible; a marker left while
    Minecraft was running, an old-boot marker, malformed state, and token
    races remain untouched. Kernel, journal, and display-provider checks still
    run immediately after this recovery.
    """

    marker = Path(path) if path is not None else GPU_LAUNCH_MARKER
    state = _read_state(marker)
    expected_fields = {
        "version", "engine_rev", "phase", "token", "boot_id",
        "launcher_pid", "created", "wrapper_returned",
    }
    if (not state or state.get("version") != _STATE_VERSION
            or state.get("phase") != "wrapper_returned"
            or set(state) != expected_fields):
        return False
    boot = _boot_id()
    if not boot or state.get("boot_id") != boot:
        return False
    pid = state.get("launcher_pid")
    created = state.get("created")
    wrapper_returned = state.get("wrapper_returned")
    engine_rev = state.get("engine_rev")
    token = state.get("token")
    if (isinstance(pid, bool) or not isinstance(pid, int) or pid <= 1
            or isinstance(created, bool) or not isinstance(created, int)
            or created < 0 or not isinstance(engine_rev, str)
            or isinstance(wrapper_returned, bool)
            or not isinstance(wrapper_returned, int)
            or wrapper_returned < created
            or not engine_rev or not isinstance(token, str)
            or not re.fullmatch(r"[0-9a-f]{32}", token)):
        return False
    if not disarm_gpu_launch(token, marker):
        return False
    warn(
        "Removed an orphaned current-boot GPU marker after confirming the "
        "Wine prefix is idle; kernel and display-server safety checks still "
        "apply."
    )
    return True


def acknowledge_gpu_safety_incident(
        marker_path: Optional[Path] = None,
        ack_path: Optional[Path] = None) -> bool:
    """Explicitly acknowledge a repaired interrupted launch.

    The caller must serialize this with PLAY (the doctor command uses the same
    launch lock). The acknowledgement suppresses only a fault from the
    *previous* boot during the current boot after that explicit confirmation.
    A current-boot GPU fault always remains blocking.
    """

    marker = Path(marker_path) if marker_path is not None else GPU_LAUNCH_MARKER
    ack = Path(ack_path) if ack_path is not None else GPU_SAFETY_ACK
    state = _read_state(marker)
    if (state and state.get("boot_id") == _boot_id()
            and _pid_alive(state.get("launcher_pid"))):
        raise BolError("The marked Minecraft launch is still active; stop it "
                       "before acknowledging a GPU safety incident.")
    _write_state_atomic(ack, {
        "version": _STATE_VERSION,
        "boot_id": _boot_id(),
        "acknowledged": int(time.time()),
    })
    existed = marker.exists() or marker.is_symlink()
    if existed:
        marker.unlink(missing_ok=True)
        _sync_parent(marker)
    return existed


def _previous_boot_fault_acknowledged(path: Optional[Path] = None) -> bool:
    ack = Path(path) if path is not None else GPU_SAFETY_ACK
    state = _read_state(ack)
    boot = _boot_id()
    return bool(boot and state and state.get("version") == _STATE_VERSION
                and state.get("boot_id") == boot)


def _xorg_software_fallback() -> bool:
    """Best-effort confirmation when RandR is unavailable.

    Require several independent markers to avoid rejecting a healthy session
    because an old log merely mentioned fbdev during driver discovery.
    """

    candidates = (
        Path.home() / ".local/share/xorg/Xorg.0.log",
        Path("/var/log/Xorg.0.log"),
    )
    for path in candidates:
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        if ("FBDEV(0):" in text
                and "DRISWRAST GL provider" in text
                and "Failed to open DRM device" in text):
            return True
    return False


def _nvidia_device_with_mesa_glx() -> bool:
    """Recognise Debian/Mint's particularly dangerous split-driver state."""

    nvidia = False
    for vendor_path in Path("/sys/class/drm").glob("card*/device/vendor"):
        try:
            vendor = vendor_path.read_text().strip().lower()
            driver = vendor_path.parent.joinpath("driver").resolve().name
        except OSError:
            continue
        if vendor == "0x10de" and driver == "nvidia":
            nvidia = True
            break
    if not nvidia:
        return False
    try:
        target = os.readlink("/etc/alternatives/glx")
    except OSError:
        return False
    return "mesa-diverted" in target


def _gpu_fault_in_text(text: str) -> bool:
    """Recognise a kernel GPU failure without conflating unrelated messages."""

    lines = text.lower().splitlines()
    direct = (
        re.compile(r"\bnvrm:.*gpu has fallen off the bus"),
        re.compile(r"\bnvrm:.*\bxid\b.*\b(?:79|119|120)\b"),
        re.compile(r"\bamdgpu\b.*(?:gpu reset begin|ring\s+\S+\s+timeout|"
                   r"asic reset failed|gpu fault)"),
        re.compile(r"\b(?:i915|xe)\b.*(?:gpu hang|wedged|reset.*failed)"),
    )
    if any(pattern.search(line) for line in lines for pattern in direct):
        return True

    # Kernel oops/lockup headers and the responsible module are frequently on
    # different stack-trace lines. Correlate them in a small neighbourhood;
    # seeing routine "amdgpu" boot chatter and an unrelated network-driver oops
    # somewhere else in a large journal must not blame the GPU.
    generic = re.compile(
        r"bug:\s*kernel null pointer dereference|"
        r"watchdog.*(?:hard|soft)\s+lockup|"
        r"kernel panic|general protection fault"
    )
    vendor = re.compile(r"\[nvidia\]|\bnvrm:|\bamdgpu\b|\bi915\b|\bxe\b")
    for index, line in enumerate(lines):
        if not generic.search(line):
            continue
        lo, hi = max(0, index - 40), min(len(lines), index + 41)
        if any(vendor.search(candidate) for candidate in lines[lo:hi]):
            return True
    return False


def _kernel_journal_text(binary: str, runner, boot: int) -> Optional[str]:
    args = [binary, "-k", "-b", str(boot), "--no-pager", "-o", "cat"]
    # Keep the tail for the whole selected boot. A current-boot fault must not
    # become launchable merely because the user waited fifteen minutes, and a
    # relative --since value would not refer to the selected previous boot.
    args += ["-n", "5000"]
    try:
        result = runner(
            args,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if getattr(result, "returncode", 1) != 0:
        return None
    return (getattr(result, "stdout", "") + "\n"
            + getattr(result, "stderr", ""))[-2_000_000:]


def _kernel_driver_fault_scope(runner=None) -> Optional[str]:
    """Return ``current``/``previous`` for an unacknowledged kernel fault."""

    binary = shutil.which("journalctl")
    if not binary and runner is not None:
        binary = "journalctl"
    if not binary:
        return None
    runner = runner or subprocess.run
    current = _kernel_journal_text(binary, runner, 0)
    if current is not None and _gpu_fault_in_text(current):
        return "current"
    if _previous_boot_fault_acknowledged():
        return None
    previous = _kernel_journal_text(binary, runner, -1)
    if previous is not None and _gpu_fault_in_text(previous):
        return "previous"
    return None


def graphics_safety_problem(
        environ: Optional[Mapping[str, str]] = None,
        xrandr_runner=None,
        journal_runner=None) -> Optional[str]:
    """Return an actionable reason to refuse launch, or ``None``.

    No Vulkan, OpenGL, ``nvidia-smi`` or DRM ioctl is performed here.
    """

    env = os.environ if environ is None else environ
    interrupted = interrupted_launch_problem()
    if interrupted:
        return interrupted
    fault_scope = _kernel_driver_fault_scope(journal_runner)
    if fault_scope == "current":
        return (
            "the graphics driver has already reported a fatal kernel fault "
            "during this boot; reboot before starting another GPU process"
        )
    if fault_scope == "previous":
        return (
            "the graphics driver reported a fatal kernel fault before the last "
            "reboot; after repairing/updating the driver, acknowledge it with "
            f"'{APP} doctor --acknowledge-gpu-crash'"
        )
    if _x11_session(env):
        providers = _xrandr_provider_count(env, xrandr_runner)
        if providers == 0:
            problem = (
                "the X11 session exposes zero RandR GPU providers and is "
                "running on a fallback framebuffer/software renderer"
            )
            if _nvidia_device_with_mesa_glx():
                problem += (
                    "; the NVIDIA kernel device is loaded while Debian's GLX "
                    "alternative points to mesa-diverted"
                )
            return problem
        if providers is None:
            if _xorg_software_fallback():
                problem = (
                    "Xorg failed to open its DRM device and fell back to "
                    "FBDEV/software rendering"
                )
                if _nvidia_device_with_mesa_glx():
                    problem += (
                        "; the NVIDIA kernel device is loaded while Debian's "
                        "GLX alternative points to mesa-diverted"
                    )
                return problem
            return (
                "the launcher could not verify any X11 hardware provider "
                "through RandR without opening the GPU"
            )
    return None


def require_safe_graphics_session(
        environ: Optional[Mapping[str, str]] = None) -> None:
    """Refuse a launch which could turn a known driver fault into a hard lock."""

    env = os.environ if environ is None else environ
    problem = graphics_safety_problem(env)
    if not problem:
        return
    if _truthy(env.get("BOL_ALLOW_UNSAFE_GPU")):
        warn("BOL_ALLOW_UNSAFE_GPU=1 bypasses the graphics safety block: "
             + problem + ".")
        return
    die("Unsafe graphics session: " + problem + ". BedrockOnLinux did not "
        "start Wine, Vulkan, or Minecraft. Repair/reinstall the host GPU "
        "driver, ensure the desktop uses the hardware DRM provider, then "
        "reboot. Advanced override (at your own risk): "
        "BOL_ALLOW_UNSAFE_GPU=1.")
