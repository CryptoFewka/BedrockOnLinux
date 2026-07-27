"""Isolated account/prefix profiles with optional desktop shortcuts."""
# SPDX-License-Identifier: MIT

import fcntl
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

from .config import APP, DATA, PRETTY, XDG_DATA_HOME
from .log import BolError


_PROFILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,39}")
_SHARED_DIRS = ("games", "proton", "umu", "cache", "xodus-xcurl")


def profile_slug(name):
    display = str(name).strip()
    if not _PROFILE_NAME.fullmatch(display) or ".." in display:
        raise BolError(
            "Profile names must be 1–40 characters and use only letters, "
            "numbers, spaces, '.', '_' or '-'."
        )
    slug = re.sub(r"[^a-z0-9]+", "-", display.lower()).strip("-")
    if not slug:
        raise BolError("The profile name does not contain a usable identifier.")
    return slug


def _metadata_path(profile_dir):
    return Path(profile_dir) / "profile.json"


def _profile_base(base_data=None):
    base = Path(DATA if base_data is None else base_data).expanduser().resolve()
    if base_data is not None or base.parent.name != "profiles":
        return base
    try:
        metadata = json.loads(_metadata_path(base).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return base
    if metadata.get("name") and metadata.get("slug") == base.name:
        return base.parent.parent
    return base


def profiles_root(base_data=None):
    return _profile_base(base_data) / "profiles"


def _ensure_shared_link(profile_dir, base_data, name):
    target_path = Path(base_data) / name
    if target_path.is_symlink():
        if not target_path.is_dir():
            raise BolError(f"Shared profile target is a dangling link: {target_path}")
    elif target_path.exists():
        if not target_path.is_dir():
            raise BolError(f"Shared profile target is not a directory: {target_path}")
    else:
        # Another profile process may win this shared-link creation race.
        target_path.mkdir(parents=True, exist_ok=True)
    target = target_path.resolve()
    link = Path(profile_dir) / name
    if link.is_symlink():
        if not link.is_dir():
            raise BolError(f"Profile shared path is a dangling link: {link}")
        if link.resolve() != target:
            raise BolError(f"Profile shared path points elsewhere: {link}")
        return
    if link.exists():
        raise BolError(f"Profile shared path is not a symlink: {link}")
    link.symlink_to(target, target_is_directory=True)


def create_profile(name, base_data=None):
    """Create an account/prefix-isolated profile while sharing large assets."""
    display = str(name).strip()
    slug = profile_slug(display)
    base = _profile_base(base_data)
    root = profiles_root(base)
    profile_dir = root / slug
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / f".{slug}.create.lock"
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            os.chmod(lock_path, 0o600)
        except OSError:
            pass
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        profile_dir.mkdir(mode=0o700, exist_ok=True)
        try:
            os.chmod(profile_dir, 0o700)
        except OSError:
            pass

        metadata_path = _metadata_path(profile_dir)
        if metadata_path.exists():
            try:
                existing = json.loads(
                    metadata_path.read_text(encoding="utf-8")
                )
            except Exception as exc:
                raise BolError(
                    f"Profile metadata is unreadable: {metadata_path}"
                ) from exc
            if existing.get("name") != display:
                raise BolError(
                    f"Profile identifier '{slug}' is already used by "
                    f"'{existing.get('name', 'another profile')}'."
                )

        for directory in _SHARED_DIRS:
            _ensure_shared_link(profile_dir, base, directory)

        fd, staged_name = tempfile.mkstemp(
            prefix=".profile-", suffix=".json", dir=profile_dir
        )
        staged = Path(staged_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"name": display, "slug": slug}, stream, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(staged, 0o600)
            os.replace(staged, metadata_path)
        finally:
            staged.unlink(missing_ok=True)
        return profile_dir
    finally:
        os.close(lock_fd)


def list_profiles(base_data=None):
    root = profiles_root(base_data)
    if not root.is_dir():
        return []
    found = []
    for metadata in sorted(root.glob("*/profile.json")):
        try:
            item = json.loads(metadata.read_text(encoding="utf-8"))
            item["path"] = str(metadata.parent)
            if item.get("name") and item.get("slug"):
                found.append(item)
        except Exception:
            continue
    return found


def _desktop_quote(value):
    escaped = str(value)
    # Desktop Exec fields require doubled percent signs for literal values.
    escaped = escaped.replace("%", "%%")
    for old, new in (("\\", "\\\\"), ('"', '\\"'), ("`", "\\`"),
                     ("$", "\\$")):
        escaped = escaped.replace(old, new)
    return f'"{escaped}"'


def profile_shortcuts_supported(environ=None, info_path=Path("/.flatpak-info")):
    """Whether this package can install host-visible profile shortcuts."""
    source = os.environ if environ is None else environ
    return not (
        source.get("FLATPAK_ID") or Path(info_path).is_file()
    )


def require_profile_shortcuts_supported(
        environ=None, info_path=Path("/.flatpak-info")):
    if not profile_shortcuts_supported(environ, info_path):
        raise BolError(
            "Isolated profile shortcuts cannot be installed from the Flatpak "
            "sandbox. Use the AppImage, .deb or native package for the "
            "multi-profile Steam shortcut workflow."
        )


def launcher_executable(explicit=None):
    if explicit:
        return str(Path(explicit).expanduser().resolve())
    # AppImage shortcuts must target APPIMAGE, not the temporary mount.
    appimage = os.environ.get("APPIMAGE", "").strip()
    if appimage:
        candidate = Path(appimage).expanduser()
        if candidate.is_file():
            return str(candidate.resolve())
    installed = shutil.which(APP)
    if installed:
        return installed
    return str(Path(sys.argv[0]).expanduser().resolve())


def write_profile_shortcut(
        name, profile_dir=None, base_data=None, applications_dir=None,
        executable=None):
    """Write a desktop entry Steam can add as a distinct non-Steam game."""
    # Host desktop and Steam cannot see Flatpak's private applications path.
    if applications_dir is None:
        require_profile_shortcuts_supported()
    display = str(name).strip()
    slug = profile_slug(display)
    directory = Path(profile_dir or create_profile(display, base_data))
    apps = Path(applications_dir or (XDG_DATA_HOME / "applications"))
    apps.mkdir(parents=True, exist_ok=True)
    entry = apps / f"{APP}-profile-{slug}.desktop"
    command = (
        "env BOL_HOME="
        + _desktop_quote(directory)
        + " "
        + _desktop_quote(launcher_executable(executable))
        + " gui"
    )
    safe_name = display.replace("\n", " ").replace("\r", " ")
    entry.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={PRETTY} — {safe_name}\n"
        f"Comment=Isolated Xbox profile: {safe_name}\n"
        f"Exec={command}\n"
        "Icon=bedrock-on-linux\n"
        "Terminal=false\n"
        "Categories=Game;\n",
        encoding="utf-8",
    )
    os.chmod(entry, 0o644)
    return entry


def profile_launch_command(profile_dir, executable=None):
    """Shell-display form for adding a profile directly to Steam."""
    import shlex
    return (
        f"BOL_HOME={shlex.quote(str(Path(profile_dir).resolve()))} "
        f"{shlex.quote(launcher_executable(executable))} gui"
    )
