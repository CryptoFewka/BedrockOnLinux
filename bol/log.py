"""bol.log — console logging, the BolError exception and die()."""
# SPDX-License-Identifier: MIT

import shutil
import subprocess
import sys

IS_TTY = sys.stdout.isatty()
_LOG_SINK = None       # GUI hook: callable(str)

# The leading tag is the protocol consumed by the GUI log sink.
_LEVELS = {
    "::": ("info ", "\033[38;5;111m", "",               "#6ea8fe", "#aeb4bf"),
    "OK": ("ok   ", "\033[38;5;78m",  "",               "#5bc46a", "#aeb4bf"),
    "!!": ("warn ", "\033[38;5;179m", "\033[38;5;179m", "#e0b341", "#e6cd86"),
    "xx": ("error", "\033[38;5;167m", "\033[38;5;167m", "#e06c5b", "#f0a39a"),
}
_ANSI_RESET = "\033[0m"


def _emit(tag, m):
    if _LOG_SINK:
        try:
            _LOG_SINK(f"{tag} {m}")
        except Exception:
            pass
    lvl = _LEVELS.get(tag)
    if not lvl:
        print(f"{tag} {m}", flush=True)
        return
    label, alab, amsg, _, _ = lvl
    if IS_TTY:
        tail = f"{amsg}{m}{_ANSI_RESET}" if amsg else m
        print(f"{alab}{label}{_ANSI_RESET}  {tail}", flush=True)
    else:
        print(f"{label}  {m}", flush=True)


def info(m): _emit("::", m)
def ok(m):   _emit("OK", m)
def warn(m): _emit("!!", m)
def err(m):  _emit("xx", m)


def desktop_notify(message, summary=None):
    """Put a message on screen for runs started without a visible terminal.

    A desktop or Steam shortcut discards stdout, so an unreported failure is
    indistinguishable from the launcher doing nothing at all.
    """
    notifier = shutil.which("notify-send")
    if not notifier:
        return False
    try:
        subprocess.run(
            [notifier, "--app-name", "BedrockOnLinux",
             summary or "BedrockOnLinux", str(message)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return True


class BolError(Exception):
    pass


def die(m):
    err(m)
    exc = BolError(m)
    exc.reported = True
    raise exc
