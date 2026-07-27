"""X11 RandR monitor geometry helpers."""
# SPDX-License-Identifier: MIT
import re
import shutil
import subprocess

from .deps import have


def _monitors_via_xlib():
    if not have("Xlib"):
        return None
    try:
        from Xlib import display as xdisplay
        from Xlib import error as xerror
    except ImportError:
        return None
    connection_errors = (xerror.DisplayError, xerror.ConnectionClosedError,
                         OSError)
    try:
        d = xdisplay.Display()
    except connection_errors:
        return None
    try:
        root = d.screen().root
        get_monitors = getattr(root, "xrandr_get_monitors", None)
        if get_monitors is None:
            return None  # server predates RandR 1.5
        monitors = get_monitors(is_active=True).monitors
        if not monitors:
            return None
        return tuple(
            (
                int(monitor.x), int(monitor.y),
                int(monitor.width_in_pixels),
                int(monitor.height_in_pixels),
                bool(monitor.primary),
            )
            for monitor in monitors
        )
    except (
            xerror.XError, *connection_errors, AttributeError, IndexError,
            TypeError, ValueError):
        return None
    finally:
        try:
            d.close()
        except Exception:
            pass


def _primary_via_xlib():
    """Primary monitor size via RandR GetMonitors, or None."""
    monitors = _monitors_via_xlib()
    if not monitors:
        return None
    primary = next(
        (monitor for monitor in monitors if monitor[4]), monitors[0])
    return str(primary[2]), str(primary[3])


_LIST_MONITOR = re.compile(
    r"^\s*\d+:\s+\+(\*)?\S+\s+"
    r"(\d+)/\d+x(\d+)/\d+([+-]\d+)([+-]\d+)")
_CONNECTED_MONITOR = re.compile(
    r"^\S+\s+connected(?:\s+(primary))?\s+"
    r"(\d+)x(\d+)([+-]\d+)([+-]\d+)")


def _parse_list_monitors(output):
    monitors = []
    for line in output.splitlines():
        match = _LIST_MONITOR.search(line)
        if match:
            monitors.append((
                int(match.group(4)), int(match.group(5)),
                int(match.group(2)), int(match.group(3)),
                bool(match.group(1)),
            ))
    return tuple(monitors)


def _parse_connected_monitors(output):
    monitors = []
    for line in output.splitlines():
        match = _CONNECTED_MONITOR.search(line)
        if match:
            monitors.append((
                int(match.group(4)), int(match.group(5)),
                int(match.group(2)), int(match.group(3)),
                bool(match.group(1)),
            ))
    return tuple(monitors)


def _xrandr_runner(runner=None):
    binary = shutil.which("xrandr")
    if not binary and runner is not None:
        binary = "xrandr"
    if not binary:
        return None
    runner = runner or subprocess.run

    def run(args):
        try:
            return runner([binary] + args, capture_output=True, text=True,
                          errors="replace", timeout=5, check=False)
        except (OSError, subprocess.SubprocessError):
            return None

    return run


def _monitors_via_xrandr_cli(runner=None):
    run = _xrandr_runner(runner)
    if run is None:
        return None
    for option in ("--listactivemonitors", "--listmonitors"):
        result = run([option])
        if result is not None and getattr(result, "returncode", 1) == 0:
            monitors = _parse_list_monitors(getattr(result, "stdout", ""))
            if monitors:
                return monitors
    result = run([])
    if result is None or getattr(result, "returncode", 1) != 0:
        return None
    return _parse_connected_monitors(getattr(result, "stdout", "")) or None


def _primary_via_xrandr_cli(runner=None):
    """Fallback: shell out to `xrandr` and parse its text output."""
    run = _xrandr_runner(runner)
    if run is None:
        return None
    result = run(["--listmonitors"])
    if result is not None and getattr(result, "returncode", 1) == 0:
        monitors = _parse_list_monitors(getattr(result, "stdout", ""))
        if monitors:
            primary = next(
                (monitor for monitor in monitors if monitor[4]), monitors[0])
            return str(primary[2]), str(primary[3])

    result = run([])
    if result is None:
        return None
    output = getattr(result, "stdout", "")
    monitors = _parse_connected_monitors(output)
    if monitors:
        primary = next(
            (monitor for monitor in monitors if monitor[4]), monitors[0])
        return str(primary[2]), str(primary[3])
    m = re.search(r"current\s+(\d+)\s+x\s+(\d+)", output)
    return (m.group(1), m.group(2)) if m else None


def monitor_geometries(runner=None):
    """Active monitor rectangles as (x, y, width, height) tuples."""
    monitors = (
        _monitors_via_xlib()
        or _monitors_via_xrandr_cli(runner)
        or ()
    )
    return tuple(monitor[:4] for monitor in monitors)


def primary_output_size(runner=None):
    """Primary monitor's (width, height) as strings, or None.

    Tries python-xlib's structured RandR GetMonitors reply first; falls back
    to parsing xrandr CLI text when python-xlib is missing, the X server
    predates RandR 1.5, or the connection fails. `runner` only affects the
    CLI fallback.
    """
    return _primary_via_xlib() or _primary_via_xrandr_cli(runner)
