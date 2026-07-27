"""bol.log — console logging, the BolError exception and die()."""
# SPDX-License-Identifier: MIT

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


class BolError(Exception):
    pass


def die(m):
    err(m)
    exc = BolError(m)
    exc.reported = True
    raise exc
