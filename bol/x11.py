"""bol.x11 - primary-monitor size via the X11 RandR extension."""
# SPDX-License-Identifier: MIT
import re
import shutil
import subprocess

from .deps import have


def _primary_via_xlib():
    """(width, height) strings for the primary monitor via python-xlib's
    RandR GetMonitors, or None if python-xlib is unavailable/unusable."""
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
        primary = next((m for m in monitors if m.primary), monitors[0])
        return (str(primary.width_in_pixels), str(primary.height_in_pixels))
    except (xerror.XError, *connection_errors, AttributeError, IndexError):
        return None
    finally:
        try:
            d.close()
        except Exception:
            pass


def _primary_via_xrandr_cli(runner=None):
    """Fallback: shell out to `xrandr` and parse its text output."""
    binary = shutil.which("xrandr")
    # A supplied runner is the unit-test seam; it must not depend on whether
    # the test host happens to have xrandr installed.
    if not binary and runner is not None:
        binary = "xrandr"
    if not binary:
        return None
    runner = runner or subprocess.run

    def run(args):
        try:
            return runner([binary] + args, capture_output=True, text=True,
                          timeout=5, check=False)
        except (OSError, subprocess.SubprocessError):
            return None

    result = run(["--listmonitors"])
    if result is not None and getattr(result, "returncode", 1) == 0:
        first = primary = None
        for line in getattr(result, "stdout", "").splitlines():
            m = re.search(r"^\s*\d+:\s+\+(\*)?\S+\s+(\d+)/\d+x(\d+)/\d+\+",
                          line)
            if not m:
                continue
            wh = (m.group(2), m.group(3))
            first = first or wh
            if m.group(1):
                primary = wh
                break
        if primary or first:
            return primary or first

    result = run([])
    if result is None:
        return None
    m = re.search(r"current\s+(\d+)\s+x\s+(\d+)",
                  getattr(result, "stdout", ""))
    return (m.group(1), m.group(2)) if m else None


def primary_output_size(runner=None):
    """Primary monitor's (width, height) as strings, or None.

    Tries python-xlib's structured RandR GetMonitors reply first; falls back
    to parsing xrandr CLI text when python-xlib is missing, the X server
    predates RandR 1.5, or the connection fails. `runner` only affects the
    CLI fallback.
    """
    return _primary_via_xlib() or _primary_via_xrandr_cli(runner)
