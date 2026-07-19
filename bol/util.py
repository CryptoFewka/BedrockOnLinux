"""bol.util — small shared helpers: run, settings, HTTP, downloads, GitHub, screen/proc."""
# SPDX-License-Identifier: MIT

import http.client
import fcntl
import json
import os
import shlex
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .config import (
    APP,
    CACHE,
    DATA,
    GAMES,
    LOGS,
    PROTON_DIR,
    SETTINGS,
    UMU_DIR,
)
from .log import IS_TTY, die, warn

def run(cmd, **kw):
    kw.setdefault("check", True)
    return subprocess.run(cmd, **kw)


def mkdirs():
    for d in (DATA, PROTON_DIR, UMU_DIR, CACHE, LOGS, GAMES):
        d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(DATA, 0o700)
    except OSError:
        pass


def load_settings():
    s = {}
    if SETTINGS.exists():
        try:
            s = json.loads(SETTINGS.read_text())
        except Exception:
            s = {}
    if not s.get("proton_dir") and not s.get("proton_url"):
        s.setdefault("proton_source", "winegdk")
    return s


def save_settings(s):
    DATA.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(DATA, 0o700)
    except OSError:
        pass
    lock_path = DATA / ".settings.lock"
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    staged = None
    try:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        fd, name = tempfile.mkstemp(prefix=".settings-", suffix=".tmp",
                                    dir=DATA)
        staged = Path(name)
        with os.fdopen(fd, "w") as stream:
            json.dump(s, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(staged, 0o600)
        os.replace(staged, SETTINGS)
        staged = None
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


def apply_custom_env(env, custom_env):
    """Merge KEY=VALUE tokens from a space-separated string into env."""
    if not custom_env or not str(custom_env).strip():
        return
    for token in shlex.split(str(custom_env).strip()):
        if "=" not in token:
            continue
        key, _, value = token.partition("=")
        key = key.strip()
        if key:
            env[key] = value


def http_json(url):
    req = urllib.request.Request(
        url, headers={"User-Agent": APP, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def http_post_form(url, fields):
    """POST application/x-www-form-urlencoded → parsed JSON. OAuth endpoints
    return their error payload with a 4xx, so decode the body either way."""
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"User-Agent": APP, "Accept": "application/json",
                 "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            raise


# Network reads can stall mid-transfer (slow CDN, flaky Wi-Fi, captive proxy);
# a single failed read used to abort the whole setup with "read operation timed
# out". Retry transient failures and RESUME via HTTP Range so a large engine or
# DLL-set download survives a drop instead of restarting from zero.
_RETRYABLE = (urllib.error.URLError, TimeoutError, socket.timeout,
              ConnectionError, http.client.IncompleteRead,
              http.client.HTTPException)


def download(url, dest: Path, label=None, progress=None, attempts=5):
    label = label or dest.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    last_err = None
    for attempt in range(1, attempts + 1):
        have = tmp.stat().st_size if tmp.exists() else 0
        headers = {"User-Agent": APP}
        if have:
            headers["Range"] = f"bytes={have}-"      # resume where we stopped
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                resuming = have > 0 and getattr(r, "status", 200) == 206
                if have and not resuming:
                    have = 0                          # server ignored Range
                if resuming:
                    cr = r.headers.get("Content-Range", "")
                    total = int(cr.rsplit("/", 1)[-1]) if "/" in cr else 0
                else:
                    total = int(r.headers.get("Content-Length", 0))
                got = last = have
                with open(tmp, "ab" if resuming else "wb") as f:
                    while True:
                        chunk = r.read(1 << 16)
                        if not chunk:
                            break
                        f.write(chunk)
                        got += len(chunk)
                        if progress and total:
                            progress(got, total)
                        if IS_TTY and total and got - last > (1 << 21):
                            last = got
                            print(f"\r:: {label}: {got*100//total:3d}% "
                                  f"({got>>20}/{total>>20} MiB)", end="", flush=True)
                if total and got < total:             # short read → resume
                    raise http.client.IncompleteRead(b"", total - got)
            if IS_TTY:
                print()
            tmp.replace(dest)
            return dest
        except urllib.error.HTTPError as e:
            if e.code == 416 and tmp.exists():        # stale/complete .part
                tmp.unlink(missing_ok=True)
                last_err = e
            elif e.code < 500:                        # 4xx won't fix itself
                die(f"Download failed: {url}\n{e}")
            else:
                last_err = e                          # 5xx → retry
        except _RETRYABLE as e:
            last_err = e
        if attempt < attempts:
            wait = min(2 ** attempt, 15)
            warn(f"{label}: connection dropped ({last_err}); resuming in "
                 f"{wait}s [{attempt}/{attempts - 1}] …")
            time.sleep(wait)
    die(f"Download failed after {attempts} attempts: {url}\n{last_err}")


def gh_latest(repo):
    return http_json(f"https://api.github.com/repos/{repo}/releases/latest")


def gh_releases(repo, per_page=50):
    return http_json(
        f"https://api.github.com/repos/{repo}/releases?per_page={per_page}")


def asset_url(release, predicate):
    for a in release.get("assets", []):
        if predicate(a["name"]):
            return a["browser_download_url"], a["name"], a.get("size", 0)
    return None, None, 0


def _screen_wh(runner=None):
    """Primary screen WxH (for gamescope/Wine desktop sizing), or None. See
    bol.x11.primary_output_size for how the primary monitor is found."""
    from .x11 import primary_output_size
    return primary_output_size(runner)
