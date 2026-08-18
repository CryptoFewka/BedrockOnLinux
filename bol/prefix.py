"""bol.prefix — Wine prefix and umu lifecycle: boot, kill, reset, options."""
# SPDX-License-Identifier: MIT

import hashlib
import fcntl
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from .archive import safe_extract_tar
from .config import (
    CACHE,
    COMPAT,
    DATA,
    GAMES,
    HOME,
    LOGS,
    PFX,
    UMU_ARCHIVE_SHA256,
    UMU_ASSET,
    UMU_DIR,
    UMU_REPO,
    UMU_RUN_SHA256,
    UMU_VERSION,
    WINEGDK_BUILD_REV,
    WINEGDK_OUT,
)
from .log import BolError, die, info, ok, warn
from .proton import proton_path
from .util import download, sha256_file

# Records the engine revision a managed prefix was last built/refreshed with,
# so an engine upgrade can refresh the prefix's cached Windows system DLLs
# instead of running the new runtime against a stale, mixed prefix.
ENGINE_REV_MARKER = ".bol-engine-rev"

# Engine DLL directory -> prefix system directory it populates.
_MANAGED_RUNTIME_ARCH_DIRS = (
    ("files/lib/wine/x86_64-windows", "drive_c/windows/system32"),
    ("files/lib/wine/i386-windows", "drive_c/windows/syswow64"),
)

def ensure_umu(force=False):
    binp = UMU_DIR / "umu-run"
    if binp.is_file() and not force:
        try:
            if hashlib.sha256(binp.read_bytes()).hexdigest() == UMU_RUN_SHA256:
                return binp
        except OSError:
            pass
        warn("Installed umu-launcher is stale or modified; repairing it.")
    url = (f"https://github.com/{UMU_REPO}/releases/download/"
           f"{UMU_VERSION}/{UMU_ASSET}")
    pkg = CACHE / UMU_ASSET
    expected_archive_hash = UMU_ARCHIVE_SHA256.lower()
    actual_archive_hash = None
    if pkg.is_file():
        try:
            actual_archive_hash = hashlib.sha256(pkg.read_bytes()).hexdigest()
        except OSError:
            pass
    if actual_archive_hash != expected_archive_hash:
        pkg.unlink(missing_ok=True)
        info("Downloading umu-launcher …")
        download(url, pkg, "umu-launcher")
        actual_archive_hash = hashlib.sha256(pkg.read_bytes()).hexdigest()
    if actual_archive_hash != expected_archive_hash:
        pkg.unlink(missing_ok=True)
        raise ValueError(
            "umu-launcher archive SHA-256 mismatch (expected %s, got %s)" %
            (expected_archive_hash, actual_archive_hash))
    UMU_DIR.mkdir(parents=True, exist_ok=True)
    staging = None
    tmp_fd, tmp_name = tempfile.mkstemp(prefix=".umu-run-", dir=UMU_DIR)
    os.close(tmp_fd)
    tmp_bin = Path(tmp_name)
    try:
        staging = Path(tempfile.mkdtemp(
            prefix=".umu-extract-", dir=UMU_DIR.parent))
        with tarfile.open(pkg) as archive:
            safe_extract_tar(archive, staging)
        source = next((p for p in staging.rglob("umu-run")
                       if p.is_file()), None)
        if not source:
            die("umu-run missing from the package.")
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        if source_hash != UMU_RUN_SHA256:
            raise ValueError(
                "umu-run SHA-256 mismatch (expected %s, got %s)" %
                (UMU_RUN_SHA256, source_hash))
        shutil.copy2(source, tmp_bin)
        os.chmod(tmp_bin, 0o755)
        tmp_bin.replace(binp)
    finally:
        tmp_bin.unlink(missing_ok=True)
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
    ok("umu-launcher ready")
    return binp


def active_prefix():
    """Return the isolated app prefix or an explicit ``BOL_WINEPREFIX``."""
    override = os.environ.get("BOL_WINEPREFIX", "").strip()
    return Path(override).expanduser() if override else PFX


@contextmanager
def prefix_operation_lock(operation="modify the Wine prefix"):
    """Serialize prefix mutations for the complete game session."""
    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / ".launch.lock"
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.chmod(path, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BolError(
                f"Cannot {operation}: another BedrockOnLinux setup, repair, "
                "or game session is already in progress. Close Minecraft or "
                "use 'Force stop Minecraft' before trying again."
            ) from exc
        yield fd
    finally:
        # Closing releases the lock when this is the last reference. PLAY
        # passes the descriptor to UMU, so an unexpected launcher exit does
        # not unlock setup/repair while the game wrapper is still alive.
        os.close(fd)


@contextmanager
def shared_assets_lock(operation, exclusive):
    """Coordinate shared game and engine assets across isolated profiles."""
    try:
        shared_root = GAMES.resolve(strict=False).parent
        shared_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BolError(
            f"Cannot locate the shared game-data root for {operation}: {exc}"
        ) from exc
    path = shared_root / ".shared-assets.lock"
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.chmod(path, 0o600)
        mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        try:
            fcntl.flock(fd, mode | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BolError(
                f"Cannot {operation}: shared Minecraft files are in use by "
                "another BedrockOnLinux profile or are being updated."
            ) from exc
        yield fd
    finally:
        # Do not issue LOCK_UN: an inherited descriptor held by the game
        # wrapper must keep this shared-assets lock alive if Python crashes.
        os.close(fd)


@contextmanager
def launch_lock():
    """Serialize PLAY with setup/repair and without stale crash locks."""
    with shared_assets_lock(
            "start Minecraft", exclusive=True) as shared_fd, \
            prefix_operation_lock("start Minecraft") as prefix_fd:
        yield shared_fd, prefix_fd


def steam_compat_dir():
    """Return a writable Steam compatibility directory for UMU/Proton.

    Flatpak cannot follow Steam Deck's host symlink, so sandboxed and unusable
    host paths fall back to app-owned storage.
    """
    if "FLATPAK_ID" in os.environ or Path("/.flatpak-info").exists():
        fallback = DATA / "steamcompat"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback
    steam = HOME / ".steam/steam"
    if steam.is_dir():                     # real Steam, or one we made earlier
        return steam
    try:
        steam.mkdir(parents=True, exist_ok=True)
        return steam
    except OSError:
        fallback = DATA / "steamcompat"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def _prepare_managed_prefix_layout(prefix):
    """Remove the obsolete managed-prefix symlink without following it."""
    if prefix != PFX:
        return
    COMPAT.mkdir(parents=True, exist_ok=True)
    if PFX.is_symlink():
        PFX.unlink()
        for junk in ("pfx.lock", "version", "tracked_files", "config_info"):
            (COMPAT / junk).unlink(missing_ok=True)


def proton_umu_cmd(exe, prefix=None):
    """Launch GDK-Proton through umu-launcher (Steam Linux Runtime). The GDK
    networking the LAN/server join needs only works inside that runtime, not
    with a bare `proton run`."""
    if prefix is None:
        prefix = active_prefix()
    if prefix == PFX:
        _prepare_managed_prefix_layout(prefix)
    else:
        info(f"Using existing GDK prefix: {prefix}")
    steam_compat = steam_compat_dir()
    env = dict(os.environ)
    env.update({"PROTONPATH": str(proton_path()),
                "PROTON_VERB": "run", "WINEPREFIX": str(prefix),
                "STEAM_COMPAT_CLIENT_INSTALL_PATH": str(steam_compat),
                # UMU appends "umu"; this resolves to the app-owned UMU_DIR.
                "UMU_FOLDERS_PATH": str(DATA),
                "UMU_RUNTIME_UPDATE": "0"})
    try:
        if Path(env["PROTONPATH"]).resolve(strict=False) == \
                WINEGDK_OUT.resolve(strict=False):
            env["PROTON_USE_WOW64"] = "1"
    except (OSError, RuntimeError):
        pass
    # GAMEID selects protonfixes, not the inherited Steam session identity.
    game_id = env.get("GAMEID", "").strip()
    env["GAMEID"] = game_id or "umu-default"
    return [sys.executable, str(ensure_umu()), exe], env


def prefix_ready(prefix: Path):
    """Return whether Wine completed the prefix, including both main hives."""
    prefix = Path(prefix)
    if not (prefix / "drive_c/windows/system32").is_dir():
        return False
    for name in ("system.reg", "user.reg"):
        try:
            with (prefix / name).open("rb") as hive:
                if not hive.read(64).startswith(b"WINE REGISTRY Version "):
                    return False
        except OSError:
            return False
    return True


def seed_managed_bootstrap_cryptbase(prefix: Path):
    """Install native cryptbase before managed-prefix services start.

    GDK-Proton's advapi32 forwards SystemFunction036 to cryptbase.  With the
    managed pure-WoW64 engine, forcing only Wine's builtin cryptbase during an
    engine upgrade can leave that forward unresolved and make every wineboot
    service repeatedly abort.  Never materialise or modify an explicitly
    supplied prefix or a prefix used with a custom engine.
    """
    if os.environ.get("BOL_WINEPREFIX", "").strip():
        return False
    pfx = Path(prefix)
    engine = proton_path()
    if engine is None:
        return False
    try:
        if pfx.resolve(strict=False) != PFX.resolve(strict=False) or \
                Path(engine).resolve(strict=False) != \
                WINEGDK_OUT.resolve(strict=False):
            return False
    except (OSError, RuntimeError):
        return False

    if pfx.is_symlink():
        raise BolError(
            "The managed Wine prefix is an unsafe symbolic link; "
            "run Install / Update to rebuild its local layout."
        )
    try:
        pfx.mkdir(parents=True, exist_ok=True)
        resolved_root = pfx.resolve(strict=True)
        current = pfx
        for component in ("drive_c", "windows", "system32"):
            child = current / component
            if child.is_symlink():
                raise BolError(
                    "The managed Wine prefix has an unsafe system32 layout; "
                    "run Install / Update to rebuild it."
                )
            child.mkdir(exist_ok=True)
            if not child.is_dir():
                raise BolError(
                    "The managed Wine prefix has an invalid system32 layout; "
                    "run Install / Update to rebuild it."
                )
            child.resolve(strict=True).relative_to(resolved_root)
            current = child
    except BolError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise BolError(
            "The managed Wine prefix has an unsafe system32 layout; "
            "run Install / Update to rebuild it."
        ) from exc

    # Import lazily: fixups imports active_prefix from this module.
    from .fixups import _install_cryptbase_in_prefix
    return _install_cryptbase_in_prefix(pfx)


def repair_bootstrap_cryptbase(prefix: Path):
    """Fetch the verified RNG payload when missing, then seed it again.

    Only used to recover a wineboot which already aborted on the unresolved
    ``advapi32.SystemFunction036`` forward: without a usable cryptbase.dll,
    every Wine service dies on its first RtlGenRandom call and the prefix can
    never complete.
    """
    from .fixups import ensure_openssl_xcurl_set
    try:
        ensure_openssl_xcurl_set()
    except Exception as exc:  # network/IO problems must not mask the retry
        warn(f"Could not refresh the verified RNG components ({exc}).")
    return seed_managed_bootstrap_cryptbase(prefix)


# Wine resolves advapi32.SystemFunction036 (RtlGenRandom) through cryptbase.
# Both spellings below mean the same thing: no usable cryptbase.dll, so every
# wineboot service aborts and the prefix initialisation can only time out.
_RNG_ABORT_SIGNATURE = re.compile(
    r"unimplemented function advapi32\.dll\.SystemFunction036|"
    r"(?:module|function) not found for forward "
    r"'cryptbase\.SystemFunction036'",
    re.IGNORECASE,
)


def _log_size(path: Path):
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _wineboot_hit_rng_abort(log_path: Path, offset=0):
    """Whether this attempt's own log section shows the cryptbase RNG abort.

    An aborting prefix repeats the same lines for its whole timeout, so only
    the beginning of this attempt's section is needed to recognise it.
    """
    try:
        with log_path.open("rb") as log:
            log.seek(max(0, offset))
            section = log.read(4 << 20)
    except OSError:
        return False
    return bool(_RNG_ABORT_SIGNATURE.search(
        section.decode("utf-8", "replace")))


def _run_wineboot(pfx: Path, log_path: Path, native_cryptbase):
    """Run one ``wineboot -u``; return None, or why it did not complete."""
    cmd, env = proton_umu_cmd("wineboot", prefix=pfx)
    cmd.append("-u")
    env = headless_setup_env(env, native_cryptbase=native_cryptbase)
    env.setdefault("WINEDEBUG", "-all")
    failure = None
    try:
        with log_path.open("a") as log:
            try:
                completed = subprocess.run(
                    cmd, env=env, stdout=log, stderr=subprocess.STDOUT,
                    timeout=300)
            except subprocess.TimeoutExpired:
                failure = "timed out after 300 seconds"
            except Exception as exc:
                failure = f"raised {type(exc).__name__}: {exc}"
            else:
                if completed.returncode != 0:
                    failure = f"exited with status {completed.returncode}"
            if failure:
                log.write(f"\nBedrockOnLinux: wineboot {failure}.\n")
                log.flush()
    except OSError as exc:
        failure = (f"could not write its diagnostic log "
                   f"({type(exc).__name__}: {exc})")
    finally:
        # Offline registry updates require wineboot services to be gone.
        stop_prefix_procs(pfx, grace=5)
    return failure


def boot_prefix(prefix=None):
    """Ensure Wine created system32 and its persistent registry hives."""
    pfx = Path(prefix or active_prefix())
    _prepare_managed_prefix_layout(pfx)
    if prefix_ready(pfx):
        if managed_prefix_engine_is_stale(pfx):
            # The engine changed since this prefix was built; its cached
            # Windows system DLLs no longer match the managed runtime.
            refresh_managed_prefix_runtime(pfx)
            _record_managed_engine_rev_if_managed(pfx)
        repair_managed_prefix_user32(pfx)
        return True
    # Refuse a known-bad graphics session before Wine can open a device.
    from .gpu_safety import require_safe_graphics_session
    require_safe_graphics_session()
    info("Initialising the Wine prefix (first run) …")
    native_cryptbase = seed_managed_bootstrap_cryptbase(pfx)
    LOGS.mkdir(parents=True, exist_ok=True)
    log_path = LOGS / "native-login.log"
    retried = False
    while True:
        attempt_offset = _log_size(log_path)
        failure = _run_wineboot(pfx, log_path, native_cryptbase)
        if not failure:
            end = time.time() + 30
            while time.time() < end and not prefix_ready(pfx):
                time.sleep(1)
            if prefix_ready(pfx):
                _record_managed_engine_rev_if_managed(pfx)
                repair_managed_prefix_user32(pfx)
                return True
        if retried or not _wineboot_hit_rng_abort(log_path, attempt_offset):
            break
        retried = True
        # The engine's first run creates the prefix from Proton's template and
        # can replace a file seeded before it existed, so re-seed and retry
        # once instead of leaving a prefix that can only time out.
        warn("Wine could not resolve its random-number generator "
             "(advapi32.SystemFunction036 → cryptbase). Reinstalling the "
             "verified RNG component and retrying the prefix once.")
        native_cryptbase = repair_bootstrap_cryptbase(pfx)
        if not native_cryptbase:
            warn("No verified cryptbase.dll is available for this prefix. "
                 "Connect to the network and re-run 'Install / Update' so the "
                 "online-login components can be downloaded.")
            break
    if failure:
        warn(f"Wine prefix initialisation failed: wineboot {failure}. "
             f"Details: {log_path}")
        return False
    warn("Wine prefix initialisation finished without valid system.reg, "
         f"user.reg, and system32 state. Details: {log_path}")
    return False


def _environ_uses_prefix(environ, prefix):
    """Match an exact NUL-delimited WINEPREFIX entry."""
    target = b"WINEPREFIX=" + os.fsencode(str(prefix))
    return target in environ.split(b"\0")


def prefix_processes(prefix: Path):
    """Return live PIDs carrying this exact ``WINEPREFIX`` environment."""
    found = []
    for pdir in Path("/proc").glob("[0-9]*"):
        try:
            if _environ_uses_prefix(
                    pdir.joinpath("environ").read_bytes(), prefix):
                pid = int(pdir.name)
                if pid != os.getpid():
                    found.append(pid)
        except Exception:
            continue
    return sorted(set(found))


def require_prefix_idle(prefix: Path, action="modify the Wine prefix"):
    """Fail before an offline mutation while wineserver still owns the hive."""
    live = prefix_processes(Path(prefix))
    if live:
        raise BolError(
            f"Cannot {action}: {len(live)} Wine/Proton process(es) still use "
            "this prefix. Close Minecraft or use 'Force stop Minecraft' first."
        )
    return True


def repair_managed_prefix_user32(prefix=None):
    """Restore the managed prefix's 64-bit user32 from the verified engine."""
    if os.environ.get("BOL_WINEPREFIX", "").strip():
        return False
    pfx = Path(prefix or PFX)
    engine = proton_path()
    if engine is None:
        return False
    try:
        if pfx.resolve(strict=False) != PFX.resolve(strict=False) \
                or Path(engine).resolve(strict=False) != \
                WINEGDK_OUT.resolve(strict=False):
            return False
    except (OSError, RuntimeError):
        return False

    source = (Path(engine) / "files/lib/wine/x86_64-windows/user32.dll")
    target = pfx / "drive_c/windows/system32/user32.dll"
    if not source.is_file() or source.is_symlink():
        raise BolError(
            "The verified managed engine has no usable 64-bit user32.dll; "
            "run Install / Update again."
        )
    if pfx.is_symlink():
        raise BolError(
            "The managed Wine prefix is an unsafe symbolic link; "
            "run Install / Update to rebuild its local layout."
        )
    try:
        resolved_root = pfx.resolve(strict=True)
        resolved_parent = target.parent.resolve(strict=True)
        resolved_parent.relative_to(resolved_root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise BolError(
            "The managed Wine prefix has an unsafe system32 layout; "
            "run Install / Update to rebuild it."
        ) from exc
    if target.parent.is_symlink():
        raise BolError(
            "The managed Wine prefix has an unsafe system32 link; "
            "run Install / Update to rebuild it."
        )

    source_hash = sha256_file(source)
    if target.is_file() and not target.is_symlink():
        try:
            if sha256_file(target) == source_hash:
                return False
        except OSError:
            pass

    require_prefix_idle(pfx, "repair the managed Wine runtime")
    backup = target.with_name(target.name + ".bol-managed-backup")
    if not (backup.exists() or backup.is_symlink()):
        if target.is_symlink():
            backup.symlink_to(os.readlink(target))
        elif target.exists():
            if not target.is_file():
                raise BolError(
                    "The managed prefix user32.dll path is not a regular file."
                )
            shutil.copy2(target, backup, follow_symlinks=False)

    fd, staged_name = tempfile.mkstemp(
        prefix=".user32.dll-", dir=target.parent)
    os.close(fd)
    staged = Path(staged_name)
    try:
        shutil.copy2(source, staged, follow_symlinks=False)
        if sha256_file(staged) != source_hash:
            raise BolError(
                "The managed user32.dll repair copy failed integrity checking."
            )
        os.replace(staged, target)
    finally:
        staged.unlink(missing_ok=True)
    ok("Managed Wine runtime repaired (user32.dll).")
    return True


def _managed_runtime_guards(pfx, engine):
    """True only for the managed prefix paired with the managed engine."""
    if os.environ.get("BOL_WINEPREFIX", "").strip():
        return False
    if engine is None:
        return False
    try:
        return Path(pfx).resolve(strict=False) == PFX.resolve(strict=False) \
            and Path(engine).resolve(strict=False) == \
            WINEGDK_OUT.resolve(strict=False)
    except (OSError, RuntimeError):
        return False


def _engine_rev_marker(pfx):
    return Path(pfx) / ENGINE_REV_MARKER


def read_managed_engine_rev(pfx):
    """Return the engine revision recorded in the prefix, or None."""
    try:
        return _engine_rev_marker(pfx).read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _write_managed_engine_rev(pfx, rev):
    try:
        _engine_rev_marker(pfx).write_text(rev + "\n", encoding="utf-8")
    except OSError:
        pass


def _record_managed_engine_rev_if_managed(pfx):
    if _managed_runtime_guards(pfx, proton_path()):
        _write_managed_engine_rev(pfx, WINEGDK_BUILD_REV)


def managed_prefix_engine_is_stale(prefix=None):
    """Whether the managed prefix was built by a different engine revision.

    A missing marker counts as stale so installs upgraded in place (whose
    prefix predates the marker) are refreshed once. Custom prefixes and custom
    engines are never considered stale and are left untouched.
    """
    pfx = Path(prefix or PFX)
    if not _managed_runtime_guards(pfx, proton_path()):
        return False
    return read_managed_engine_rev(pfx) != WINEGDK_BUILD_REV


def _dll_differs(source, target):
    if target.is_symlink() or not target.is_file():
        return True
    try:
        return sha256_file(target) != sha256_file(source)
    except OSError:
        return True


def _replace_managed_dll(source, target):
    """Atomically refresh one prefix DLL from the engine; return if changed."""
    source_hash = sha256_file(source)
    if target.is_file() and not target.is_symlink():
        try:
            if sha256_file(target) == source_hash:
                return False
        except OSError:
            pass
    backup = target.with_name(target.name + ".bol-runtime-backup")
    if not (backup.exists() or backup.is_symlink()):
        if target.is_symlink():
            backup.symlink_to(os.readlink(target))
        elif target.exists():
            if not target.is_file():
                raise BolError(
                    "The managed prefix has a non-regular runtime file: "
                    "%s" % target.name)
            shutil.copy2(target, backup, follow_symlinks=False)
    fd, staged_name = tempfile.mkstemp(
        prefix=".bol-runtime-", dir=target.parent)
    os.close(fd)
    staged = Path(staged_name)
    try:
        shutil.copy2(source, staged, follow_symlinks=False)
        if sha256_file(staged) != source_hash:
            raise BolError(
                "The managed runtime refresh failed integrity checking for "
                "%s." % target.name)
        os.replace(staged, target)
    finally:
        staged.unlink(missing_ok=True)
    return True


def refresh_managed_prefix_runtime(prefix=None):
    """Refresh the managed prefix's Windows system DLLs from the engine.

    An engine upgrade swaps the WineGDK tree but reuses the existing prefix,
    whose cached system32/syswow64 DLLs were populated by the previous engine.
    Running the new pure-WoW64 runtime against those stale DLLs faults
    Minecraft's account/menu path with an unhandled page fault (issue #135).
    Copy every DLL the current engine ships over the prefix's existing system
    DLLs, leaving the user profile (drive_c/users — worlds, settings, login)
    untouched. Only the managed prefix on the managed engine is modified.
    """
    engine = proton_path()
    pfx = Path(prefix or PFX)
    if not _managed_runtime_guards(pfx, engine):
        return False
    if pfx.is_symlink():
        raise BolError(
            "The managed Wine prefix is an unsafe symbolic link; "
            "run Install / Update to rebuild its local layout.")
    try:
        resolved_root = pfx.resolve(strict=True)
        operations = []
        for engine_rel, prefix_rel in _MANAGED_RUNTIME_ARCH_DIRS:
            src_dir = Path(engine) / engine_rel
            dst_dir = pfx / prefix_rel
            if not src_dir.is_dir() or not dst_dir.is_dir():
                continue
            if dst_dir.is_symlink():
                raise BolError(
                    "The managed Wine prefix has an unsafe system link; "
                    "run Install / Update to rebuild it.")
            dst_dir.resolve(strict=True).relative_to(resolved_root)
            # Deliberate boundary: only refresh DLLs the prefix ALREADY has.
            # The engine's windows dir holds Wine's full builtin set; a
            # prefix's system dir is the subset wineboot installed, and Wine
            # loads the rest straight from the engine dir. Injecting
            # engine-only DLLs here would disturb that redirection, so a DLL
            # the prefix lacks is intentionally left absent (issue #135).
            for source in sorted(src_dir.glob("*.dll")):
                target = dst_dir / source.name
                if (target.exists() or target.is_symlink()) \
                        and _dll_differs(source, target):
                    operations.append((source, target))
    except BolError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise BolError(
            "The managed Wine prefix has an unsafe system layout; "
            "run Install / Update to rebuild it.") from exc

    if not operations:
        return False
    require_prefix_idle(pfx, "refresh the managed Wine runtime")
    changed = False
    for source, target in operations:
        changed |= _replace_managed_dll(source, target)
    if changed:
        ok("Managed Wine runtime refreshed to %s." % WINEGDK_BUILD_REV)
    return changed


def stop_prefix_procs(prefix: Path, grace=5, kill_grace=2):
    """Stop a prefix, including children spawned during shutdown.

    A one-shot PID snapshot misses services which wineserver/explorer creates
    while their parents are exiting. Keep rescanning through both TERM and KILL
    phases, and do not let an offline registry writer proceed until the prefix
    is demonstrably idle.
    """
    prefix = Path(prefix)
    seen = set()
    term_sent = set()
    deadline = time.monotonic() + max(0, grace)

    while True:
        live = set(prefix_processes(prefix))
        if not live:
            return len(seen), 0
        seen.update(live)
        for pid in live - term_sent:
            try:
                os.kill(pid, 15)
            except (ProcessLookupError, PermissionError):
                pass
            term_sent.add(pid)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(0.1, remaining))

    forced = set()
    kill_deadline = time.monotonic() + max(0, kill_grace)
    while True:
        live = set(prefix_processes(prefix))
        if not live:
            return len(seen), len(forced)
        seen.update(live)
        for pid in live:
            try:
                os.kill(pid, 9)
            except (ProcessLookupError, PermissionError):
                pass
            else:
                forced.add(pid)
        remaining = kill_deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(0.1, remaining))

    live = prefix_processes(prefix)
    if live:
        raise BolError(
            f"Could not stop {len(live)} Wine/Proton process(es) for this "
            "prefix; refusing unsafe offline changes."
        )
    return len(seen), len(forced)


def headless_setup_env(env, native_cryptbase=False):
    """Prevent non-graphical Wine setup helpers from initialising a GPU."""
    result = dict(env)
    result.pop("DISPLAY", None)
    result.pop("WAYLAND_DISPLAY", None)
    result.pop("XAUTHORITY", None)
    result.pop("PROTON_ENABLE_WAYLAND", None)
    result["SDL_VIDEODRIVER"] = "dummy"
    setup_keys = {"cryptbase", "winevulkan", "dxgi", "d3d11", "d3d12"}
    current = [
        item for item in result.get("WINEDLLOVERRIDES", "").split(";")
        if item and item.partition("=")[0].strip().lower() not in setup_keys
    ]
    # Prefer only the verified, self-contained native RNG seeded above. Older
    # native implementations could recurse through advapi32, so retain the
    # builtin-only behavior when no safe DLL was staged.
    cryptbase = "cryptbase=n,b" if native_cryptbase else "cryptbase=b"
    overrides = [cryptbase, "winevulkan=", "dxgi=", "d3d11=", "d3d12="]
    result["WINEDLLOVERRIDES"] = ";".join(overrides + current)
    return result


def kill_wine():
    """Explicit GUI action: stop only this application's Wine prefix."""
    stopped, forced = stop_prefix_procs(active_prefix())
    if stopped:
        ok(f"Stopped {stopped} BedrockOnLinux process(es)"
           + (f" ({forced} forced)." if forced else "."))
    else:
        info("No BedrockOnLinux Wine process is running.")


def reset_prefix():
    # Repair must never delete an explicit third-party prefix.
    with prefix_operation_lock("repair the Wine prefix"):
        stop_prefix_procs(PFX)
        require_prefix_idle(PFX, "repair the Wine prefix")
        if COMPAT.is_symlink() or (
                COMPAT.exists() and not COMPAT.is_dir()):
            # Never follow a damaged or dangling compatibility-tree link.
            COMPAT.unlink()
        elif COMPAT.exists():
            shutil.rmtree(COMPAT)
        ok("Wine prefix reset — rebuilt on next launch.")


OPTIONS_REL = ("drive_c/users/steamuser/AppData/Roaming/Minecraft Bedrock/"
               "Users/Shared/games/com.mojang/minecraftpe/options.txt")


def patch_options():
    opt = PFX / OPTIONS_REL
    if not opt.exists():
        return
    kv, order = {}, []
    for l in opt.read_text(errors="ignore").splitlines():
        if ":" in l:
            k, _, v = l.partition(":")
            k = k.strip()
            if k not in kv:
                order.append(k)
            kv[k] = v.strip()
    if kv.get("do_not_show_multiplayer_online_safety_warning") == "1":
        return
    if "do_not_show_multiplayer_online_safety_warning" not in order:
        order.append("do_not_show_multiplayer_online_safety_warning")
    kv["do_not_show_multiplayer_online_safety_warning"] = "1"
    opt.write_text("\n".join(f"{k}:{kv[k]}" for k in order) + "\n")
    ok("Multiplayer warning disabled")


def _mc_running():
    for pid in prefix_processes(active_prefix()):
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
            if b"Minecraft.Windows.exe" in cmdline:
                return True
        except OSError:
            continue
    return False
