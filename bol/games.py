"""bol.games — Minecraft version listing, download and selection."""
# SPDX-License-Identifier: MIT

import hashlib
import json
import re
import shutil
import zipfile
from pathlib import Path

from .config import CACHE, CONTENT, GAMES, GAME_ARCHIVE_REPO
from .log import BolError, die, info, ok, warn
from .util import (
    download,
    gh_releases,
    load_settings,
    path_exists,
    remove_path,
    save_settings,
)


_ASSET_METADATA = ".bedrock-on-linux-asset.json"


def list_mc_versions(include_beta=True):
    out = []
    for r in gh_releases(GAME_ARCHIVE_REPO, fetch_all=True):
        tag = r["tag_name"]
        m = re.search(r"(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?", tag)
        if not m:
            continue

        major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
        build = int(m.group(4)) if m.group(4) else 0

        is_beta = bool(r.get("prerelease"))
        if is_beta:
            if (major, minor, patch, build) < (1, 21, 120, 21):
                continue
            if not include_beta:
                continue
        else:
            if (major, minor, patch, build) < (1, 21, 120, 0):
                continue

        asset = next((
            candidate for candidate in r.get("assets", [])
            if str(candidate.get("name", "")).lower().endswith(".zip")
            and "minecraft" in str(candidate.get("name", "")).lower()
        ), None)
        if not asset or not asset.get("browser_download_url"):
            continue

        out.append({
            "tag": tag,
            "beta": is_beta,
            "url": asset["browser_download_url"],
            "name": asset["name"],
            "size": asset.get("size", 0),
            "asset_id": asset.get("id"),
            "asset_digest": asset.get("digest"),
            "asset_updated_at": asset.get("updated_at"),
        })
    return out


def _game_root(dest):
    """Folder of a complete extracted build (exe + appxmanifest), else None
    (a bare exe with no manifest means a truncated extract → reinstall)."""
    if not dest.exists():
        return None
    for exe in dest.rglob("Minecraft.Windows.exe"):
        if any((exe.parent / m).exists()
               for m in ("appxmanifest.xml", "AppxManifest.xml")):
            return exe.parent
    return None


def _asset_record(ver):
    """Stable release-asset identity, or None for legacy/custom callers."""

    digest = str(ver.get("asset_digest") or "").strip().lower()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        digest = ""
    asset_id = ver.get("asset_id")
    updated = str(ver.get("asset_updated_at") or "").strip()
    if asset_id is None and not digest and not updated:
        return None
    return {
        "schema": 1,
        "tag": str(ver.get("tag") or ""),
        "name": str(ver.get("name") or ""),
        "size": int(ver.get("size") or 0),
        "asset_id": str(asset_id) if asset_id is not None else "",
        "digest": digest,
        "updated_at": updated,
    }


def _write_asset_record(game_target, record):
    if record is None:
        return
    target = Path(game_target) / _ASSET_METADATA
    staged = target.with_name("." + target.name + ".tmp")
    try:
        staged.write_text(
            json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        staged.replace(target)
    finally:
        staged.unlink(missing_ok=True)


def _installed_asset_is_current(game_target, record):
    if record is None:
        return True
    path = Path(game_target) / _ASSET_METADATA
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return False
    return current == record


def _verify_asset_digest(path, record):
    expected = (record or {}).get("digest", "")
    if not expected:
        return
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual != expected.removeprefix("sha256:"):
        raise BolError(
            "Minecraft archive SHA-256 mismatch; the downloaded asset was "
            "not activated."
        )


def _recover_interrupted_game_refresh(archive, target, refresh, staging):
    """Resolve an interrupted two-path game/cache activation.

    A process can die between the game and cache renames. Candidate paths mean
    activation did not finish, so restore the rollback copies. With no
    candidates, two validated active paths mean activation finished and only
    rollback cleanup was interrupted.
    """
    archive_backup = archive.with_name("." + archive.name + ".rollback")
    game_backup = target.with_name("." + target.name + ".rollback")
    candidates_present = path_exists(refresh) or path_exists(staging)
    backups_present = (
        path_exists(archive_backup) or path_exists(game_backup)
    )
    if not candidates_present and not backups_present:
        return

    active_complete = (
        zipfile.is_zipfile(archive) and _game_root(target) is not None
    )
    if backups_present and not candidates_present and active_complete:
        # Both candidates reached their active names; only cleanup was cut
        # short. Keep the new pair and retire the old rollback paths.
        remove_path(archive_backup)
        remove_path(game_backup)
        return

    # In every other interrupted state, prefer the known-old rollback pair.
    # Without a backup, keep an active path: ``refresh`` can exist before
    # ``staging`` is created, so a missing staging name alone does not prove
    # that the game candidate was promoted. Deleting the active game in that
    # ambiguous download/activation window would lose a valid installation.
    if path_exists(archive_backup):
        remove_path(archive)
        archive_backup.replace(archive)

    if path_exists(game_backup):
        remove_path(target)
        game_backup.replace(target)

    remove_path(refresh)
    remove_path(staging)
    remove_path(archive_backup)
    remove_path(game_backup)


def _activate_game_refresh(staged_archive, archive, staged_game, target):
    """Activate a validated archive and game tree, restoring both on error."""
    archive_backup = archive.with_name("." + archive.name + ".rollback")
    game_backup = target.with_name("." + target.name + ".rollback")

    # Recover a path left between the two renames of an earlier swap. A backup
    # alongside a complete active path is only stale cleanup residue.
    for active, backup in (
            (archive, archive_backup), (target, game_backup)):
        if path_exists(backup):
            if path_exists(active):
                remove_path(backup)
            else:
                backup.replace(active)

    had_archive = path_exists(archive)
    had_game = path_exists(target)
    archive_backed_up = False
    game_backed_up = False
    try:
        # Keep both known-good paths until the candidates have been fully
        # downloaded, extracted and validated. The archive is activated last.
        if had_game:
            target.replace(game_backup)
            game_backed_up = True
        staged_game.replace(target)

        if had_archive:
            archive.replace(archive_backup)
            archive_backed_up = True
        staged_archive.replace(archive)
    except Exception as exc:
        rollback_errors = []
        for active, backup, had_active, backed_up in (
                (archive, archive_backup, had_archive, archive_backed_up),
                (target, game_backup, had_game, game_backed_up)):
            try:
                # If there was no old path, restore its exact absence. If the
                # old path was moved aside, discard any candidate before
                # putting the backup back.
                if backed_up or not had_active:
                    if path_exists(active):
                        remove_path(active)
                if backed_up:
                    if not path_exists(backup):
                        raise OSError(f"rollback backup missing: {backup}")
                    backup.replace(active)
            except Exception as rollback_exc:
                rollback_errors.append(f"{active}: {rollback_exc}")
        if rollback_errors:
            detail = "; ".join(rollback_errors)
            raise BolError(
                f"Minecraft archive activation failed: {exc}; "
                f"rollback errors: {detail}"
            ) from exc
        raise
    else:
        for backup in (archive_backup, game_backup):
            if path_exists(backup):
                try:
                    remove_path(backup)
                except OSError as exc:
                    # Both validated candidates are already active. Keep the
                    # harmless rollback for recovery instead of rejecting them.
                    warn(f"Could not remove Minecraft rollback {backup}: "
                         f"{exc}")


def download_game(ver, progress=None, force=False):
    """Install one release-archive entry.

    ``force`` refreshes the archive as well as the extracted game.  Microsoft
    can roll out a platform hotfix without changing the user-facing Bedrock
    version, so reusing a same-named cached archive forever can leave this
    client on an older internal build.  The refresh is downloaded alongside
    the existing cache entry and replaces it only after a complete download.
    """
    GAMES.mkdir(parents=True, exist_ok=True)
    dest = GAMES / ver["tag"]
    zp = CACHE / ver["name"]
    refresh = zp.with_name(zp.name + ".refresh")
    staging = dest.with_name("." + dest.name + ".refresh")
    _recover_interrupted_game_refresh(zp, dest, refresh, staging)
    root = _game_root(dest)
    asset_record = _asset_record(ver)
    if (root and not force
            and not _installed_asset_is_current(dest, asset_record)):
        # GitHub assets can be replaced while retaining the release tag. An
        # older install has no marker, so refresh it once and record the exact
        # asset thereafter.
        force = True
        info(
            f"Minecraft {ver['tag']} asset changed or predates verified "
            "asset tracking; refreshing automatically."
        )
    if root and not force:
        info(f"Minecraft {ver['tag']} already installed")
        return root
    if (not force and zp.exists()
            and (asset_record or {}).get("digest")):
        try:
            _verify_asset_digest(zp, asset_record)
        except BolError:
            # A release asset may be replaced without changing its filename.
            # With no complete active game there is nothing to preserve, and
            # retaining the now-invalid cache would make every retry fail
            # before download() gets a chance to fetch the replacement.
            remove_path(zp)
            info(
                f"Cached Minecraft {ver['tag']} archive no longer matches "
                "the published asset; downloading it again."
            )
    if force:
        refresh_part = refresh.with_suffix(refresh.suffix + ".part")
        remove_path(refresh)
        remove_path(refresh_part)
        remove_path(staging)
        preserve_transaction = False
        info(f"Refreshing Minecraft {ver['tag']} archive "
             f"(~{ver['size']>>20} Mio) …")
        try:
            download(
                ver["url"],
                refresh,
                f"Minecraft {ver['tag']}",
                progress,
            )
            _verify_asset_digest(refresh, asset_record)
            staging.mkdir(parents=True)
            with zipfile.ZipFile(refresh) as z:
                z.extractall(staging)
            staged_root = _game_root(staging)
            if not staged_root:
                die("Minecraft.Windows.exe missing from the archive.")
            relative_root = staged_root.relative_to(staging)
            _write_asset_record(staging, asset_record)
            info(f"{'Reinstalling' if root else 'Installing'} Minecraft "
                 f"{ver['tag']} …")
            # Once activation starts, a rollback failure must leave at least
            # one candidate path as a durable interrupted-transaction signal.
            # The next invocation can then prefer the known-old rollback pair
            # instead of mistaking two merely valid paths for a committed pair.
            preserve_transaction = True
            try:
                _activate_game_refresh(refresh, zp, staging, dest)
            except BolError:
                # _activate_game_refresh wraps an incomplete rollback in
                # BolError. Retain the candidate which tells recovery to roll
                # back rather than commit the syntactically valid mixed pair.
                raise
            except Exception:
                # Ordinary activation errors were rolled back completely by
                # _activate_game_refresh, so their candidates are disposable.
                preserve_transaction = False
                raise
            else:
                preserve_transaction = False
        finally:
            # Download/extract failures have not touched active data, so their
            # candidates are disposable. After an activation exception they
            # are recovery state and must survive until the next invocation.
            remove_path(refresh_part)
            if not preserve_transaction:
                remove_path(refresh)
                remove_path(staging)
        root = dest / relative_root
        ok(f"Minecraft {ver['tag']} installed")
        return root
    elif not zp.exists():
        info(f"Downloading Minecraft {ver['tag']} "
             f"(~{ver['size']>>20} Mio) …")
        download(ver["url"], zp, f"Minecraft {ver['tag']}", progress)
    try:
        _verify_asset_digest(zp, asset_record)
    except BolError:
        # This non-transactional path is used only when no complete game is
        # active. Never retain a freshly downloaded archive whose published
        # digest failed, otherwise all later retries would reuse it forever.
        remove_path(zp)
        raise
    info(f"{'Reinstalling' if root else 'Installing'} Minecraft "
         f"{ver['tag']} …")
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    with zipfile.ZipFile(zp) as z:
        z.extractall(dest)
    root = _game_root(dest)
    if not root:
        die("Minecraft.Windows.exe missing from the archive.")
    _write_asset_record(dest, asset_record)
    ok(f"Minecraft {ver['tag']} installed")
    return root


def use_game_dir(folder):
    folder = Path(folder).expanduser().resolve()
    if not (folder / "Minecraft.Windows.exe").exists():
        cands = list(folder.rglob("Minecraft.Windows.exe"))
        if not cands:
            die(f"Minecraft.Windows.exe not found in {folder} (nor in "
                f"its subfolders). Choose an extracted version folder, "
                f"or use '① Minecraft version'.")
        best = max(cands, key=lambda e: _vt(mc_version_str(e.parent) or "0"))
        folder = best.parent
        info(f"Minecraft found: {folder} "
             f"(version {mc_version_str(folder) or '?'})")
    if CONTENT.is_symlink() or CONTENT.exists():
        CONTENT.unlink() if CONTENT.is_symlink() else shutil.rmtree(CONTENT)
    CONTENT.symlink_to(folder)
    s = load_settings()
    s["game_dir"] = str(folder)
    # Remember the version actually selected so the picker and auto-select
    # default to the version you last played (else the latest) — never a stale
    # one. games/<tag>/ gives the exact release tag; fall back to the manifest.
    try:
        ver = folder.relative_to(GAMES.resolve()).parts[0]
    except ValueError:
        ver = None
    ver = ver or mc_version_str(folder)
    if ver:
        s["mc_version"] = ver
    save_settings(s)
    return folder


def mc_version_str(game_dir: Path):
    for nm in ("appxmanifest.xml", "AppxManifest.xml"):
        man = game_dir / nm
        if man.exists():
            m = re.search(r'Identity[^>]*Version="(\d+)\.(\d+)\.(\d+)\.\d+"',
                          man.read_text(errors="ignore"))
            if m:
                p = m.group(3)
                # Bedrock packs "<minor><patch>" into the Appx 3rd field, e.g.
                # 2004 -> "20.4", 3005 -> "30.5", 0301 -> "3.1" — split it back
                # so the result matches the release tags (e.g. 1.26.20.4).
                if len(p) >= 3:
                    return f"{m.group(1)}.{m.group(2)}.{int(p[:2])}.{int(p[2:])}"
                return f"{m.group(1)}.{m.group(2)}.{int(p)}"
    return None


def _vt(v):
    try:
        return tuple(int(x) for x in v.split("."))
    except Exception:
        return (0,)


def _auto_mc_version(s):
    vs = list_mc_versions(False) or list_mc_versions(True)
    if not vs:
        die("No Minecraft version available (check your network).")
    want = (s.get("mc_version") or "").strip()
    return next((v for v in vs if v["tag"] == want
                 or v["tag"].startswith(want + ".")), vs[0]) if want else vs[0]
