"""Regression tests for Minecraft release-archive installation."""
# SPDX-License-Identifier: MIT

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from bol import games
from bol.log import BolError


def _write_game(root, marker):
    root.mkdir(parents=True, exist_ok=True)
    (root / "Minecraft.Windows.exe").write_bytes(marker)
    (root / "AppxManifest.xml").write_text("<Package />", encoding="utf-8")
    return root


def _write_archive(path, marker):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("payload/Minecraft.Windows.exe", marker)
        archive.writestr("payload/AppxManifest.xml", "<Package />")


class GameArchiveTests(unittest.TestCase):
    def _version(self):
        return {
            "tag": "1.26.33.1",
            "url": "https://example.invalid/minecraft.zip",
            "name": "Minecraft.zip",
            "size": 1024,
        }

    def test_release_listing_keeps_identity_needed_for_same_tag_refresh(self):
        release = {
            "tag_name": "1.26.33.1",
            "prerelease": False,
            "assets": [{
                "id": 12345,
                "name": "Microsoft.Minecraft_1.26.33.1.zip",
                "size": 4096,
                "digest": "sha256:" + "a" * 64,
                "updated_at": "2026-07-25T09:30:00Z",
                "browser_download_url": "https://example.invalid/game.zip",
            }],
        }
        with mock.patch.object(games, "gh_releases",
                               return_value=[release]):
            versions = games.list_mc_versions(include_beta=False)

        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0]["asset_id"], 12345)
        self.assertEqual(
            versions[0]["asset_digest"], "sha256:" + "a" * 64)
        self.assertEqual(
            versions[0]["asset_updated_at"], "2026-07-25T09:30:00Z")

    def test_changed_same_tag_asset_refreshes_automatically(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            games_dir = root / "games"
            cache_dir = root / "cache"
            target = games_dir / "1.26.33.1"
            _write_game(target / "payload", b"old-build")
            (target / games._ASSET_METADATA).write_text(json.dumps({
                "schema": 1,
                "tag": "1.26.33.1",
                "name": "Minecraft.zip",
                "size": 1024,
                "asset_id": "old-id",
                "digest": "",
                "updated_at": "2026-07-24T00:00:00Z",
            }))
            cached = cache_dir / "Minecraft.zip"
            _write_archive(cached, b"old-build")

            fresh_archive = root / "fresh.zip"
            _write_archive(fresh_archive, b"new-build")
            expected = hashlib.sha256(fresh_archive.read_bytes()).hexdigest()
            version = self._version() | {
                "asset_id": 54321,
                "asset_digest": "sha256:" + expected,
                "asset_updated_at": "2026-07-25T09:30:00Z",
            }

            def fresh_download(_url, destination, _label, _progress):
                Path(destination).write_bytes(fresh_archive.read_bytes())

            with mock.patch.object(games, "GAMES", games_dir), \
                    mock.patch.object(games, "CACHE", cache_dir), \
                    mock.patch.object(games, "download",
                                      side_effect=fresh_download) as download, \
                    mock.patch.object(games, "info"), \
                    mock.patch.object(games, "ok"):
                installed = games.download_game(version, force=False)

            download.assert_called_once()
            self.assertEqual(
                (installed / "Minecraft.Windows.exe").read_bytes(),
                b"new-build",
            )
            metadata = json.loads(
                (target / games._ASSET_METADATA).read_text())
            self.assertEqual(metadata["asset_id"], "54321")
            self.assertEqual(metadata["digest"], "sha256:" + expected)

    def test_digest_mismatch_never_replaces_working_same_tag_game(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            games_dir = root / "games"
            cache_dir = root / "cache"
            installed = _write_game(
                games_dir / "1.26.33.1" / "payload",
                b"working-build",
            )
            cached = cache_dir / "Minecraft.zip"
            _write_archive(cached, b"working-build")
            cached_before = cached.read_bytes()
            version = self._version() | {
                "asset_id": 54321,
                "asset_digest": "sha256:" + "0" * 64,
                "asset_updated_at": "2026-07-25T09:30:00Z",
            }

            def wrong_download(_url, destination, _label, _progress):
                _write_archive(Path(destination), b"wrong-build")

            with mock.patch.object(games, "GAMES", games_dir), \
                    mock.patch.object(games, "CACHE", cache_dir), \
                    mock.patch.object(games, "download",
                                      side_effect=wrong_download), \
                    mock.patch.object(games, "info"), \
                    self.assertRaisesRegex(BolError, "SHA-256 mismatch"):
                games.download_game(version, force=False)

            self.assertEqual(
                (installed / "Minecraft.Windows.exe").read_bytes(),
                b"working-build",
            )
            self.assertEqual(cached.read_bytes(), cached_before)

    def test_stale_same_name_cache_is_redownloaded_without_active_game(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            games_dir = root / "games"
            cache_dir = root / "cache"
            cached = cache_dir / "Minecraft.zip"
            _write_archive(cached, b"old-build")
            fresh_archive = root / "fresh.zip"
            _write_archive(fresh_archive, b"new-build")
            expected = hashlib.sha256(fresh_archive.read_bytes()).hexdigest()
            version = self._version() | {
                "asset_id": 54321,
                "asset_digest": "sha256:" + expected,
                "asset_updated_at": "2026-07-25T09:30:00Z",
            }

            def fresh_download(_url, destination, _label, _progress):
                Path(destination).write_bytes(fresh_archive.read_bytes())

            with mock.patch.object(games, "GAMES", games_dir), \
                    mock.patch.object(games, "CACHE", cache_dir), \
                    mock.patch.object(
                        games, "download", side_effect=fresh_download
                    ) as download, \
                    mock.patch.object(games, "info"), \
                    mock.patch.object(games, "ok"):
                installed = games.download_game(version, force=False)

            download.assert_called_once()
            self.assertEqual(cached.read_bytes(), fresh_archive.read_bytes())
            self.assertEqual(
                (installed / "Minecraft.Windows.exe").read_bytes(),
                b"new-build",
            )

    def test_bad_redownload_is_removed_so_next_retry_can_succeed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            games_dir = root / "games"
            cache_dir = root / "cache"
            cached = cache_dir / "Minecraft.zip"
            _write_archive(cached, b"old-build")
            fresh_archive = root / "fresh.zip"
            _write_archive(fresh_archive, b"new-build")
            wrong_archive = root / "wrong.zip"
            _write_archive(wrong_archive, b"wrong-build")
            expected = hashlib.sha256(fresh_archive.read_bytes()).hexdigest()
            version = self._version() | {
                "asset_id": 54321,
                "asset_digest": "sha256:" + expected,
                "asset_updated_at": "2026-07-25T09:30:00Z",
            }
            attempts = iter((wrong_archive, fresh_archive))

            def download_attempt(_url, destination, _label, _progress):
                Path(destination).write_bytes(next(attempts).read_bytes())

            with mock.patch.object(games, "GAMES", games_dir), \
                    mock.patch.object(games, "CACHE", cache_dir), \
                    mock.patch.object(
                        games, "download", side_effect=download_attempt
                    ) as download, \
                    mock.patch.object(games, "info"), \
                    mock.patch.object(games, "ok"):
                with self.assertRaisesRegex(BolError, "SHA-256 mismatch"):
                    games.download_game(version, force=False)
                self.assertFalse(cached.exists())
                installed = games.download_game(version, force=False)

            self.assertEqual(download.call_count, 2)
            self.assertEqual(cached.read_bytes(), fresh_archive.read_bytes())
            self.assertEqual(
                (installed / "Minecraft.Windows.exe").read_bytes(),
                b"new-build",
            )

    def test_force_refreshes_cached_archive_before_reinstall(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            games_dir = root / "games"
            cache_dir = root / "cache"
            _write_game(
                games_dir / "1.26.33.1" / "payload",
                b"old-build",
            )
            cached = cache_dir / "Minecraft.zip"
            _write_archive(cached, b"old-build")
            refresh_part = cache_dir / "Minecraft.zip.refresh.part"
            refresh_part.write_bytes(b"stale-part")

            def fresh_download(_url, destination, _label, _progress):
                destination = Path(destination)
                self.assertEqual(
                    destination,
                    cache_dir / "Minecraft.zip.refresh",
                )
                self.assertTrue(cached.exists())
                self.assertFalse(refresh_part.exists())
                _write_archive(destination, b"new-build")
                return destination

            with mock.patch.object(games, "GAMES", games_dir), \
                    mock.patch.object(games, "CACHE", cache_dir), \
                    mock.patch.object(
                        games, "download", side_effect=fresh_download
                    ) as download, \
                    mock.patch.object(games, "info"), \
                    mock.patch.object(games, "ok"):
                installed = games.download_game(
                    self._version(),
                    progress="progress-callback",
                    force=True,
                )

            download.assert_called_once_with(
                self._version()["url"],
                cache_dir / "Minecraft.zip.refresh",
                "Minecraft 1.26.33.1",
                "progress-callback",
            )
            self.assertEqual(
                (installed / "Minecraft.Windows.exe").read_bytes(),
                b"new-build",
            )
            self.assertFalse(
                (cache_dir / "Minecraft.zip.refresh").exists()
            )
            self.assertFalse(refresh_part.exists())
            self.assertFalse(
                (games_dir / ".1.26.33.1.refresh").exists()
            )
            self.assertFalse(
                (cache_dir / ".Minecraft.zip.rollback").exists()
            )
            self.assertFalse(
                (games_dir / ".1.26.33.1.rollback").exists()
            )
            with zipfile.ZipFile(cached) as archive:
                self.assertEqual(
                    archive.read("payload/Minecraft.Windows.exe"),
                    b"new-build",
                )

    def test_failed_force_refresh_keeps_cache_and_installed_game(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            games_dir = root / "games"
            cache_dir = root / "cache"
            game = _write_game(
                games_dir / "1.26.33.1" / "payload",
                b"working-build",
            )
            cached = cache_dir / "Minecraft.zip"
            _write_archive(cached, b"working-build")
            before = cached.read_bytes()

            def failed_download(_url, destination, _label, _progress):
                destination = Path(destination)
                destination.write_bytes(b"incomplete")
                destination.with_suffix(
                    destination.suffix + ".part"
                ).write_bytes(b"partial")
                raise BolError("network failed")

            with mock.patch.object(games, "GAMES", games_dir), \
                    mock.patch.object(games, "CACHE", cache_dir), \
                    mock.patch.object(
                        games, "download", side_effect=failed_download
                    ), \
                    mock.patch.object(games, "info"), \
                    self.assertRaisesRegex(BolError, "network failed"):
                games.download_game(self._version(), force=True)

            self.assertEqual(cached.read_bytes(), before)
            self.assertEqual(
                (game / "Minecraft.Windows.exe").read_bytes(),
                b"working-build",
            )
            self.assertFalse(
                (cache_dir / "Minecraft.zip.refresh").exists()
            )
            self.assertFalse(
                (cache_dir / "Minecraft.zip.refresh.part").exists()
            )

    def test_recovery_after_download_before_staging_keeps_active_game(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            games_dir = root / "games"
            cache_dir = root / "cache"
            game = _write_game(
                games_dir / "1.26.33.1" / "payload",
                b"working-build",
            )
            cached = cache_dir / "Minecraft.zip"
            _write_archive(cached, b"working-build")
            cached_before = cached.read_bytes()

            # Power loss immediately after download() has renamed its .part
            # file leaves refresh present but no extraction staging directory.
            _write_archive(
                cache_dir / "Minecraft.zip.refresh",
                b"unactivated-build",
            )

            with mock.patch.object(games, "GAMES", games_dir), \
                    mock.patch.object(games, "CACHE", cache_dir), \
                    mock.patch.object(
                        games,
                        "download",
                        side_effect=BolError("network failed on retry"),
                    ), \
                    mock.patch.object(games, "info"), \
                    self.assertRaisesRegex(
                        BolError, "network failed on retry"
                    ):
                games.download_game(self._version(), force=True)

            self.assertEqual(cached.read_bytes(), cached_before)
            self.assertEqual(
                (game / "Minecraft.Windows.exe").read_bytes(),
                b"working-build",
            )
            self.assertFalse(
                (cache_dir / "Minecraft.zip.refresh").exists()
            )

    def test_corrupt_force_refresh_preserves_cache_and_installed_game(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            games_dir = root / "games"
            cache_dir = root / "cache"
            game = _write_game(
                games_dir / "1.26.33.1" / "payload",
                b"working-build",
            )
            cached = cache_dir / "Minecraft.zip"
            _write_archive(cached, b"working-build")
            before = cached.read_bytes()

            def corrupt_download(_url, destination, _label, _progress):
                Path(destination).write_bytes(b"not-a-zip")
                return destination

            with mock.patch.object(games, "GAMES", games_dir), \
                    mock.patch.object(games, "CACHE", cache_dir), \
                    mock.patch.object(
                        games, "download", side_effect=corrupt_download
                    ), \
                    mock.patch.object(games, "info"), \
                    self.assertRaises(zipfile.BadZipFile):
                games.download_game(self._version(), force=True)

            self.assertEqual(cached.read_bytes(), before)
            self.assertEqual(
                (game / "Minecraft.Windows.exe").read_bytes(),
                b"working-build",
            )
            self.assertFalse(
                (cache_dir / "Minecraft.zip.refresh").exists()
            )
            self.assertFalse(
                (games_dir / ".1.26.33.1.refresh").exists()
            )
            self.assertFalse(
                (cache_dir / ".Minecraft.zip.rollback").exists()
            )
            self.assertFalse(
                (games_dir / ".1.26.33.1.rollback").exists()
            )

    def test_activation_failure_rolls_back_cache_and_installed_game(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            games_dir = root / "games"
            cache_dir = root / "cache"
            game = _write_game(
                games_dir / "1.26.33.1" / "payload",
                b"working-build",
            )
            cached = cache_dir / "Minecraft.zip"
            _write_archive(cached, b"working-build")
            before = cached.read_bytes()
            original_replace = Path.replace

            def fresh_download(_url, destination, _label, _progress):
                _write_archive(Path(destination), b"new-build")
                return destination

            def fail_archive_activation(source, target):
                source = Path(source)
                target = Path(target)
                if (source == cache_dir / "Minecraft.zip.refresh"
                        and target == cached):
                    self.assertEqual(
                        (games_dir / "1.26.33.1" / "payload"
                         / "Minecraft.Windows.exe").read_bytes(),
                        b"new-build",
                    )
                    self.assertTrue(
                        (cache_dir / ".Minecraft.zip.rollback").exists()
                    )
                    raise OSError("simulated archive activation failure")
                return original_replace(source, target)

            with mock.patch.object(games, "GAMES", games_dir), \
                    mock.patch.object(games, "CACHE", cache_dir), \
                    mock.patch.object(
                        games, "download", side_effect=fresh_download
                    ), \
                    mock.patch.object(Path, "replace",
                                      new=fail_archive_activation), \
                    mock.patch.object(games, "info"), \
                    self.assertRaisesRegex(
                        OSError, "simulated archive activation failure"
                    ):
                games.download_game(self._version(), force=True)

            self.assertEqual(cached.read_bytes(), before)
            self.assertEqual(
                (game / "Minecraft.Windows.exe").read_bytes(),
                b"working-build",
            )
            self.assertFalse(
                (cache_dir / "Minecraft.zip.refresh").exists()
            )
            self.assertFalse(
                (games_dir / ".1.26.33.1.refresh").exists()
            )
            self.assertFalse(
                (cache_dir / ".Minecraft.zip.rollback").exists()
            )
            self.assertFalse(
                (games_dir / ".1.26.33.1.rollback").exists()
            )

    def test_next_run_recovers_when_immediate_rollback_cannot_remove_game(
            self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            games_dir = root / "games"
            cache_dir = root / "cache"
            target = games_dir / "1.26.33.1"
            old_game = _write_game(target / "payload", b"old-build")
            archive = cache_dir / "Minecraft.zip"
            _write_archive(archive, b"old-build")
            original_replace = Path.replace
            original_remove = games._remove_path

            def fresh_download(_url, destination, _label, _progress):
                _write_archive(Path(destination), b"new-build")
                return destination

            def fail_archive_activation(source, destination):
                if (Path(source) == cache_dir / "Minecraft.zip.refresh"
                        and Path(destination) == archive):
                    raise OSError("simulated archive activation failure")
                return original_replace(source, destination)

            remove_failed = False

            def fail_first_game_rollback(path):
                nonlocal remove_failed
                if Path(path) == target and not remove_failed:
                    remove_failed = True
                    raise PermissionError("simulated transient EACCES")
                return original_remove(path)

            with mock.patch.object(games, "GAMES", games_dir), \
                    mock.patch.object(games, "CACHE", cache_dir), \
                    mock.patch.object(
                        games, "download", side_effect=fresh_download
                    ), \
                    mock.patch.object(
                        Path, "replace", new=fail_archive_activation
                    ), \
                    mock.patch.object(
                        games, "_remove_path",
                        side_effect=fail_first_game_rollback
                    ), \
                    mock.patch.object(games, "info"), \
                    self.assertRaisesRegex(
                        BolError, "rollback errors.*transient EACCES"
                    ):
                games.download_game(self._version(), force=True)

            refresh = cache_dir / "Minecraft.zip.refresh"
            game_backup = games_dir / ".1.26.33.1.rollback"
            self.assertTrue(refresh.exists())
            self.assertTrue(game_backup.exists())
            self.assertEqual(
                (target / "payload" / "Minecraft.Windows.exe").read_bytes(),
                b"new-build",
            )

            with mock.patch.object(games, "GAMES", games_dir), \
                    mock.patch.object(games, "CACHE", cache_dir), \
                    mock.patch.object(games, "info"), \
                    mock.patch.object(games, "download") as download:
                installed = games.download_game(
                    self._version(), force=False,
                )

            download.assert_not_called()
            self.assertEqual(installed, old_game)
            self.assertEqual(
                (installed / "Minecraft.Windows.exe").read_bytes(),
                b"old-build",
            )
            with zipfile.ZipFile(archive) as cached:
                self.assertEqual(
                    cached.read("payload/Minecraft.Windows.exe"),
                    b"old-build",
                )
            self.assertFalse(refresh.exists())
            self.assertFalse(game_backup.exists())

    def test_complete_rollback_without_old_cache_keeps_old_game_next_run(
            self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            games_dir = root / "games"
            cache_dir = root / "cache"
            old_game = _write_game(
                games_dir / "1.26.33.1" / "payload",
                b"old-build",
            )
            archive = cache_dir / "Minecraft.zip"
            original_replace = Path.replace

            def fresh_download(_url, destination, _label, _progress):
                _write_archive(Path(destination), b"new-build")
                return destination

            def fail_new_archive_activation(source, destination):
                if (Path(source) == cache_dir / "Minecraft.zip.refresh"
                        and Path(destination) == archive):
                    raise OSError("simulated first archive activation failure")
                return original_replace(source, destination)

            with mock.patch.object(games, "GAMES", games_dir), \
                    mock.patch.object(games, "CACHE", cache_dir), \
                    mock.patch.object(
                        games, "download", side_effect=fresh_download
                    ), \
                    mock.patch.object(
                        Path, "replace", new=fail_new_archive_activation
                    ), \
                    mock.patch.object(games, "info"), \
                    self.assertRaisesRegex(
                        OSError, "first archive activation failure"
                    ):
                games.download_game(self._version(), force=True)

            self.assertEqual(
                (old_game / "Minecraft.Windows.exe").read_bytes(),
                b"old-build",
            )
            self.assertFalse(
                (cache_dir / "Minecraft.zip.refresh").exists()
            )
            self.assertFalse(
                (games_dir / ".1.26.33.1.rollback").exists()
            )

            with mock.patch.object(games, "GAMES", games_dir), \
                    mock.patch.object(games, "CACHE", cache_dir), \
                    mock.patch.object(games, "info"), \
                    mock.patch.object(
                        games, "download",
                        side_effect=BolError("offline")
                    ) as download:
                installed = games.download_game(
                    self._version(), force=False,
                )

            download.assert_not_called()
            self.assertEqual(installed, old_game)
            self.assertEqual(
                (installed / "Minecraft.Windows.exe").read_bytes(),
                b"old-build",
            )

    def test_next_run_rolls_back_process_interrupted_between_game_and_cache(
            self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            games_dir = root / "games"
            cache_dir = root / "cache"
            target = games_dir / "1.26.33.1"
            _write_game(target / "payload", b"new-build")
            _write_game(
                games_dir / ".1.26.33.1.rollback" / "payload",
                b"old-build",
            )
            archive = cache_dir / "Minecraft.zip"
            refresh = cache_dir / "Minecraft.zip.refresh"
            _write_archive(archive, b"old-build")
            _write_archive(refresh, b"new-build")

            with mock.patch.object(games, "GAMES", games_dir), \
                    mock.patch.object(games, "CACHE", cache_dir), \
                    mock.patch.object(games, "info"), \
                    mock.patch.object(games, "download") as download:
                installed = games.download_game(
                    self._version(), force=False,
                )

            download.assert_not_called()
            self.assertEqual(
                (installed / "Minecraft.Windows.exe").read_bytes(),
                b"old-build",
            )
            with zipfile.ZipFile(archive) as cached:
                self.assertEqual(
                    cached.read("payload/Minecraft.Windows.exe"),
                    b"old-build",
                )
            self.assertFalse(refresh.exists())
            self.assertFalse(
                (games_dir / ".1.26.33.1.rollback").exists()
            )

    def test_next_run_commits_completed_activation_and_cleans_rollbacks(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            games_dir = root / "games"
            cache_dir = root / "cache"
            target = games_dir / "1.26.33.1"
            _write_game(target / "payload", b"new-build")
            game_backup = games_dir / ".1.26.33.1.rollback"
            _write_game(game_backup / "payload", b"old-build")
            archive = cache_dir / "Minecraft.zip"
            archive_backup = cache_dir / ".Minecraft.zip.rollback"
            _write_archive(archive, b"new-build")
            _write_archive(archive_backup, b"old-build")

            with mock.patch.object(games, "GAMES", games_dir), \
                    mock.patch.object(games, "CACHE", cache_dir), \
                    mock.patch.object(games, "info"), \
                    mock.patch.object(games, "download") as download:
                installed = games.download_game(
                    self._version(), force=False,
                )

            download.assert_not_called()
            self.assertEqual(
                (installed / "Minecraft.Windows.exe").read_bytes(),
                b"new-build",
            )
            with zipfile.ZipFile(archive) as cached:
                self.assertEqual(
                    cached.read("payload/Minecraft.Windows.exe"),
                    b"new-build",
                )
            self.assertFalse(archive_backup.exists())
            self.assertFalse(game_backup.exists())


if __name__ == "__main__":
    unittest.main()
