"""Multiple-account profile and shortcut regressions."""
# SPDX-License-Identifier: MIT

import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import pytest

from bol.log import BolError
from bol.profiles import (
    create_profile,
    launcher_executable,
    list_profiles,
    play_launch_command,
    profile_launch_command,
    profile_slug,
    require_profile_shortcuts_supported,
    require_shortcuts_supported,
    write_play_shortcut,
    write_profile_shortcut,
)

ROOT = Path(__file__).resolve().parents[1]


def test_profile_shares_large_assets_but_not_account_or_prefix(tmp_path):
    base = tmp_path / "main data"
    profile = create_profile("Player One", base)

    for name in ("games", "proton", "umu", "cache", "xodus-xcurl"):
        link = profile / name
        assert link.is_symlink()
        assert link.resolve(strict=False) == (base / name).resolve()
        assert (base / name).is_dir()
    assert not (profile / "msa").exists()
    assert not (profile / "winegdk-preauth").exists()
    assert not (profile / "compatdata").exists()
    assert not (profile / "settings.json").exists()
    assert profile.stat().st_mode & 0o777 == 0o700


def test_two_profiles_have_distinct_private_roots(tmp_path):
    base = tmp_path / "data"
    one = create_profile("Alice", base)
    two = create_profile("Bob", base)
    assert one != two
    assert one.parent == two.parent
    assert one / "compatdata" != two / "compatdata"
    assert one / "msa" != two / "msa"


def test_profile_creation_is_idempotent(tmp_path):
    first = create_profile("Deck User", tmp_path)
    second = create_profile("Deck User", tmp_path)
    assert first == second
    assert json.loads((first / "profile.json").read_text())["name"] == "Deck User"


def test_distinct_profiles_can_create_shared_targets_concurrently(
        tmp_path, monkeypatch):
    base = tmp_path / "data"
    original_mkdir = Path.mkdir
    games_barrier = threading.Barrier(2)

    def synchronized_mkdir(path, *args, **kwargs):
        if path == base / "games":
            games_barrier.wait(timeout=5)
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", synchronized_mkdir)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(create_profile, name, base)
            for name in ("Alice", "Bob")
        ]
        profiles = [future.result(timeout=5) for future in futures]

    assert {profile.name for profile in profiles} == {"alice", "bob"}
    assert (base / "games").is_dir()
    assert all((profile / "games").resolve() == (base / "games").resolve()
               for profile in profiles)


def test_concurrent_names_with_same_slug_cannot_overwrite_profile(tmp_path):
    base = tmp_path / "data"
    names = ("Foo Bar", "Foo-Bar")
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {
            pool.submit(create_profile, name, base): name
            for name in names
        }

    successes = []
    failures = []
    for future, name in futures.items():
        try:
            successes.append((name, future.result()))
        except BolError as exc:
            failures.append((name, str(exc)))

    assert len(successes) == 1
    assert len(failures) == 1
    winner, profile = successes[0]
    assert profile.name == "foo-bar"
    assert json.loads((profile / "profile.json").read_text())["name"] == winner
    assert "already used" in failures[0][1]


@pytest.mark.parametrize("name", ["", "../escape", "bad/name", "a" * 41])
def test_invalid_profile_name_is_rejected(name):
    with pytest.raises(BolError):
        profile_slug(name)


def test_profiles_are_listed_from_metadata(tmp_path):
    create_profile("Second", tmp_path)
    create_profile("First", tmp_path)
    items = list_profiles(tmp_path)
    assert {item["name"] for item in items} == {"First", "Second"}
    assert all(Path(item["path"]).is_dir() for item in items)


def test_profile_commands_from_managed_profile_use_sibling_root(tmp_path):
    base = tmp_path / "data"
    alice = create_profile("Alice", base)

    with mock.patch("bol.profiles.DATA", alice):
        bob = create_profile("Bob")
        items = list_profiles()

    assert bob == base / "profiles" / "bob"
    assert not (alice / "profiles").exists()
    assert {item["name"] for item in items} == {"Alice", "Bob"}


def test_bol_home_data_root_remains_profile_base(tmp_path):
    data_root = tmp_path / "direct BOL_HOME"

    with mock.patch("bol.profiles.DATA", data_root):
        profile = create_profile("Alice")
        items = list_profiles()

    assert profile == data_root / "profiles" / "alice"
    assert [item["name"] for item in items] == ["Alice"]


def test_explicit_profile_base_overrides_managed_profile_data(tmp_path):
    current = create_profile("Current", tmp_path / "current")
    explicit = tmp_path / "explicit"

    with mock.patch("bol.profiles.DATA", current):
        profile = create_profile("Other", explicit)

    assert profile == explicit / "profiles" / "other"


def test_shortcut_uses_isolated_bol_home_and_handles_spaces(tmp_path):
    base = tmp_path / "shared data"
    profile = create_profile("Family Two", base)
    apps = tmp_path / "desktop entries"
    launcher = tmp_path / "Bedrock Launcher"
    launcher.write_text("#!/bin/sh\n")

    entry = write_profile_shortcut(
        "Family Two", profile_dir=profile, applications_dir=apps,
        executable=launcher,
    )
    text = entry.read_text()
    assert "Name=BedrockOnLinux — Family Two" in text
    assert f'BOL_HOME="{profile}"' in text
    assert f'"{launcher.resolve()}" gui' in text
    assert "Icon=bedrock-on-linux" in text
    assert entry.stat().st_mode & 0o777 == 0o644


def test_profile_launch_command_is_copyable_for_steam(tmp_path):
    profile = tmp_path / "profile with spaces"
    launcher = tmp_path / "launcher with spaces"
    command = profile_launch_command(profile, launcher)
    assert "BOL_HOME='" in command
    assert "' gui" in command


def test_shortcut_created_inside_appimage_uses_persistent_appimage(tmp_path):
    appimage = tmp_path / "Bedrock On Linux.AppImage"
    appimage.write_bytes(b"AppImage")
    mounted_launcher = tmp_path / ".mount_bol/usr/bin/bedrock-on-linux"
    mounted_launcher.parent.mkdir(parents=True)
    mounted_launcher.write_text("#!/bin/sh\n")

    with mock.patch.dict(
            os.environ, {"APPIMAGE": str(appimage)}, clear=True), \
            mock.patch("bol.profiles.shutil.which",
                       return_value=str(mounted_launcher)):
        assert launcher_executable() == str(appimage.resolve())


def test_fresh_profile_can_run_real_mkdirs_without_dangling_links(tmp_path):
    base = tmp_path / "fresh base"
    profile = create_profile("Fresh", base)
    env = dict(os.environ)
    env.update({
        "BOL_HOME": str(profile),
        "PYTHONPATH": str(ROOT),
    })

    subprocess.run(
        [
            sys.executable,
            "-c",
            "from bol.util import mkdirs; mkdirs()",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    for name in ("games", "proton", "umu", "cache"):
        assert (profile / name).is_dir()


def test_desktop_exec_escapes_literal_percent_field_codes(tmp_path):
    base = tmp_path / "base"
    profile = create_profile("Percent", base)
    launcher = tmp_path / "Bedrock%20Linux.AppImage"
    launcher.write_bytes(b"AppImage")

    entry = write_profile_shortcut(
        "Percent",
        profile_dir=profile,
        applications_dir=tmp_path / "applications",
        executable=launcher,
    )

    assert "Bedrock%%20Linux.AppImage" in entry.read_text()


def test_flatpak_profile_shortcut_is_rejected_before_private_desktop_write(
        tmp_path):
    with pytest.raises(BolError, match="cannot be installed from the Flatpak"):
        require_profile_shortcuts_supported(
            {"FLATPAK_ID": "io.github.wyze3306.BedrockOnLinux"},
            tmp_path / "not-flatpak-info",
        )


def test_play_shortcut_launches_the_game_without_the_launcher_window(
        tmp_path):
    apps = tmp_path / "desktop entries"
    launcher = tmp_path / "Bedrock Launcher"
    launcher.write_text("#!/bin/sh\n")

    entry = write_play_shortcut(
        applications_dir=apps, executable=launcher,
    )

    text = entry.read_text()
    assert entry.name == "bedrock-on-linux-play.desktop"
    assert "Name=Minecraft Bedrock\n" in text
    assert f'Exec="{launcher.resolve()}" play\n' in text
    # The default installation is not profile-scoped.
    assert "BOL_HOME" not in text
    assert "Terminal=false" in text
    assert entry.stat().st_mode & 0o777 == 0o644


def test_play_shortcut_for_a_profile_keeps_its_isolated_home(tmp_path):
    base = tmp_path / "shared data"
    profile = create_profile("Family Two", base)
    apps = tmp_path / "desktop entries"
    launcher = tmp_path / "Bedrock Launcher"
    launcher.write_text("#!/bin/sh\n")

    entry = write_play_shortcut(
        profile_name="Family Two", profile_dir=profile,
        applications_dir=apps, executable=launcher,
    )

    text = entry.read_text()
    assert entry.name == "bedrock-on-linux-play-family-two.desktop"
    assert "Name=Minecraft Bedrock — Family Two\n" in text
    assert f'Exec=env BOL_HOME="{profile}" "{launcher.resolve()}" play\n' \
        in text


def test_play_shortcut_does_not_collide_with_the_launcher_shortcut(tmp_path):
    base = tmp_path / "shared data"
    profile = create_profile("Family Two", base)
    apps = tmp_path / "desktop entries"
    launcher = tmp_path / "launcher"
    launcher.write_text("#!/bin/sh\n")

    windowed = write_profile_shortcut(
        "Family Two", profile_dir=profile, applications_dir=apps,
        executable=launcher,
    )
    direct = write_play_shortcut(
        profile_name="Family Two", profile_dir=profile,
        applications_dir=apps, executable=launcher,
    )

    assert windowed != direct
    assert windowed.read_text().endswith("Categories=Game;\n")
    assert " gui\n" in windowed.read_text()
    assert " play\n" in direct.read_text()


def test_play_launch_command_is_copyable_for_steam(tmp_path):
    launcher = tmp_path / "launcher with spaces"

    assert play_launch_command(executable=launcher).endswith("' play")
    assert play_launch_command(
        tmp_path / "profile with spaces", launcher).startswith("BOL_HOME='")


def test_flatpak_play_shortcut_is_refused_with_a_runnable_alternative(
        tmp_path):
    with pytest.raises(BolError) as refusal:
        require_shortcuts_supported(
            {"FLATPAK_ID": "io.github.wyze3306.BedrockOnLinux"},
            tmp_path / "not-flatpak-info",
        )

    message = str(refusal.value)
    assert "cannot be written from the Flatpak sandbox" in message
    # A refusal with no way forward is what sends users to the issue tracker.
    assert "flatpak run io.github.wyze3306.BedrockOnLinux play" in message
