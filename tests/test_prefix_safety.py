"""Regression tests for prefix shutdown and operation serialization."""
# SPDX-License-Identifier: MIT

import os
import tempfile
import subprocess
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from bol import fixups, gameinput, gamesetup, prefix
from bol.log import BolError


def _make_ready_prefix(path):
    (path / "drive_c/windows/system32").mkdir(parents=True, exist_ok=True)
    for hive in ("system.reg", "user.reg"):
        (path / hive).write_bytes(b"WINE REGISTRY Version 2\n")


class PrefixShutdownTests(unittest.TestCase):
    def test_prefix_process_match_requires_exact_environment_name(self):
        pfx = Path("/data/prefix")
        self.assertTrue(prefix._environ_uses_prefix(
            b"USER=test\0WINEPREFIX=/data/prefix\0", pfx
        ))
        self.assertFalse(prefix._environ_uses_prefix(
            b"NOT_WINEPREFIX=/data/prefix\0", pfx
        ))
        self.assertFalse(prefix._environ_uses_prefix(
            b"WINEPREFIX=/data/prefix-extra\0", pfx
        ))

    def test_prefix_ready_requires_system32_and_valid_registry_hives(self):
        with tempfile.TemporaryDirectory() as td:
            pfx = Path(td) / "pfx"
            (pfx / "drive_c/windows/system32").mkdir(parents=True)
            self.assertFalse(prefix.prefix_ready(pfx))

            (pfx / "system.reg").write_bytes(b"not a Wine registry\n")
            (pfx / "user.reg").write_bytes(b"WINE REGISTRY Version 2\n")
            self.assertFalse(prefix.prefix_ready(pfx))

            (pfx / "system.reg").write_bytes(b"WINE REGISTRY Version 2\n")
            self.assertTrue(prefix.prefix_ready(pfx))

    def test_unsafe_fresh_prefix_is_rejected_before_umu_or_wine(self):
        with tempfile.TemporaryDirectory() as td:
            pfx = Path(td) / "fresh-prefix"
            with mock.patch(
                    "bol.gpu_safety.require_safe_graphics_session",
                    side_effect=BolError("unsafe graphics")) as safety, \
                    mock.patch.object(prefix, "proton_umu_cmd") as umu, \
                    mock.patch.object(prefix.subprocess, "run") as run:
                with self.assertRaisesRegex(BolError, "unsafe graphics"):
                    prefix.boot_prefix(pfx)
            safety.assert_called_once_with()
            umu.assert_not_called()
            run.assert_not_called()

    def test_partial_prefix_retries_wineboot(self):
        with tempfile.TemporaryDirectory() as td:
            pfx = Path(td) / "partial-prefix"
            (pfx / "drive_c/windows/system32").mkdir(parents=True)

            def finish_prefix(*_args, **_kwargs):
                _make_ready_prefix(pfx)
                return SimpleNamespace(returncode=0)

            with mock.patch.object(prefix, "LOGS", Path(td) / "logs"), \
                    mock.patch(
                        "bol.gpu_safety.require_safe_graphics_session"), \
                    mock.patch.object(
                        prefix, "proton_umu_cmd",
                        return_value=(["umu", "wineboot"], {})) as umu, \
                    mock.patch.object(
                        prefix, "seed_managed_bootstrap_cryptbase",
                        return_value=False), \
                    mock.patch.object(
                        prefix.subprocess, "run",
                        side_effect=finish_prefix) as run, \
                    mock.patch.object(prefix, "stop_prefix_procs"):
                self.assertTrue(prefix.boot_prefix(pfx))

            umu.assert_called_once_with("wineboot", prefix=pfx)
            run.assert_called_once()

    def test_complete_prefix_skips_wineboot(self):
        with tempfile.TemporaryDirectory() as td:
            pfx = Path(td) / "ready-prefix"
            _make_ready_prefix(pfx)
            with mock.patch(
                    "bol.gpu_safety.require_safe_graphics_session") as safety, \
                    mock.patch.object(
                        prefix, "repair_managed_prefix_user32") as repair, \
                    mock.patch.object(prefix, "proton_umu_cmd") as umu, \
                    mock.patch.object(prefix.subprocess, "run") as run:
                self.assertTrue(prefix.boot_prefix(pfx))
            safety.assert_not_called()
            repair.assert_called_once_with(pfx)
            umu.assert_not_called()
            run.assert_not_called()

    def test_nonzero_wineboot_is_logged_and_fails(self):
        with tempfile.TemporaryDirectory() as td:
            logs = Path(td) / "logs"
            with mock.patch.object(prefix, "LOGS", logs), \
                    mock.patch(
                        "bol.gpu_safety.require_safe_graphics_session"), \
                    mock.patch.object(
                        prefix, "proton_umu_cmd",
                        return_value=(["umu", "wineboot"], {})), \
                    mock.patch.object(
                        prefix, "seed_managed_bootstrap_cryptbase",
                        return_value=False), \
                    mock.patch.object(
                        prefix.subprocess, "run",
                        return_value=SimpleNamespace(returncode=42)), \
                    mock.patch.object(prefix, "stop_prefix_procs"), \
                    mock.patch.object(prefix, "warn") as warn:
                self.assertFalse(prefix.boot_prefix(Path(td) / "pfx"))

            self.assertIn(
                "wineboot exited with status 42",
                (logs / "native-login.log").read_text(),
            )
            self.assertIn(
                str(logs / "native-login.log"), warn.call_args.args[0])

    def test_wineboot_timeout_is_logged_and_fails(self):
        with tempfile.TemporaryDirectory() as td:
            logs = Path(td) / "logs"
            with mock.patch.object(prefix, "LOGS", logs), \
                    mock.patch(
                        "bol.gpu_safety.require_safe_graphics_session"), \
                    mock.patch.object(
                        prefix, "proton_umu_cmd",
                        return_value=(["umu", "wineboot"], {})), \
                    mock.patch.object(
                        prefix, "seed_managed_bootstrap_cryptbase",
                        return_value=False), \
                    mock.patch.object(
                        prefix.subprocess, "run",
                        side_effect=prefix.subprocess.TimeoutExpired(
                            ["umu", "wineboot"], 300)), \
                    mock.patch.object(prefix, "stop_prefix_procs"), \
                    mock.patch.object(prefix, "warn") as warn:
                self.assertFalse(prefix.boot_prefix(Path(td) / "pfx"))

            self.assertIn(
                "wineboot timed out after 300 seconds",
                (logs / "native-login.log").read_text(),
            )
            self.assertIn("timed out", warn.call_args.args[0])

    def test_wineboot_exception_is_logged_and_fails(self):
        with tempfile.TemporaryDirectory() as td:
            logs = Path(td) / "logs"
            with mock.patch.object(prefix, "LOGS", logs), \
                    mock.patch(
                        "bol.gpu_safety.require_safe_graphics_session"), \
                    mock.patch.object(
                        prefix, "proton_umu_cmd",
                        return_value=(["umu", "wineboot"], {})), \
                    mock.patch.object(
                        prefix, "seed_managed_bootstrap_cryptbase",
                        return_value=False), \
                    mock.patch.object(
                        prefix.subprocess, "run",
                        side_effect=OSError("cannot execute")), \
                    mock.patch.object(prefix, "stop_prefix_procs"), \
                    mock.patch.object(prefix, "warn") as warn:
                self.assertFalse(prefix.boot_prefix(Path(td) / "pfx"))

            self.assertIn(
                "wineboot raised OSError: cannot execute",
                (logs / "native-login.log").read_text(),
            )
            self.assertIn("OSError", warn.call_args.args[0])

    def test_shutdown_rescans_and_terminates_new_children(self):
        scans = [[10], [10, 11], []]
        with mock.patch.object(
                prefix, "prefix_processes", side_effect=scans), \
                mock.patch.object(prefix.os, "kill") as kill, \
                mock.patch.object(prefix.time, "sleep"):
            stopped, forced = prefix.stop_prefix_procs(
                Path("/tmp/bol-prefix"), grace=5)

        self.assertEqual((stopped, forced), (2, 0))
        self.assertEqual(
            kill.call_args_list,
            [mock.call(10, 15), mock.call(11, 15)],
        )

    def test_shutdown_fails_if_process_survives_sigkill(self):
        with mock.patch.object(prefix, "prefix_processes",
                               return_value=[10]), \
                mock.patch.object(prefix.os, "kill") as kill:
            with self.assertRaisesRegex(
                    BolError, "refusing unsafe offline changes"):
                prefix.stop_prefix_procs(
                    Path("/tmp/bol-prefix"), grace=0, kill_grace=0)

        self.assertIn(mock.call(10, 15), kill.call_args_list)
        self.assertIn(mock.call(10, 9), kill.call_args_list)

    def test_wineboot_propagates_shutdown_failure(self):
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(prefix, "LOGS", Path(td) / "logs"), \
                mock.patch.object(
                    prefix, "proton_umu_cmd",
                    return_value=(["umu", "wineboot"], {})), \
                mock.patch("bol.gpu_safety.require_safe_graphics_session"), \
                mock.patch.object(prefix.subprocess, "run"), \
                mock.patch.object(
                    prefix, "stop_prefix_procs",
                    side_effect=BolError("wineserver survived")):
            with self.assertRaisesRegex(BolError, "wineserver survived"):
                prefix.boot_prefix(Path(td) / "pfx")


class PrefixEnvironmentTests(unittest.TestCase):
    def test_flatpak_uses_app_owned_steam_compat_directory(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            host_steam = root / "home/.steam/steam"
            host_steam.mkdir(parents=True)
            with mock.patch.dict(
                    prefix.os.environ,
                    {"FLATPAK_ID": "io.github.bedrock_on_linux"},
                    clear=True), \
                    mock.patch.object(prefix, "HOME", root / "home"), \
                    mock.patch.object(prefix, "DATA", root / "data"):
                selected = prefix.steam_compat_dir()

            self.assertEqual(selected, root / "data/steamcompat")
            self.assertTrue(selected.is_dir())
            self.assertNotEqual(selected, host_steam)

    def test_flatpak_info_file_also_selects_app_owned_compat_directory(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with mock.patch.dict(prefix.os.environ, {}, clear=True), \
                    mock.patch.object(prefix, "HOME", root / "home"), \
                    mock.patch.object(prefix, "DATA", root / "data"), \
                    mock.patch.object(
                        prefix.Path, "exists", autospec=True,
                        side_effect=lambda path: str(path) == "/.flatpak-info"):
                selected = prefix.steam_compat_dir()

            self.assertEqual(selected, root / "data/steamcompat")
            self.assertTrue(selected.is_dir())

    def test_headless_setup_forces_builtin_cryptbase(self):
        env = {
            "DISPLAY": ":0",
            "WAYLAND_DISPLAY": "wayland-0",
            "PROTON_ENABLE_WAYLAND": "1",
            "WINEDLLOVERRIDES": "cryptbase=n,b;foo=n;dxgi=n",
        }
        result = prefix.headless_setup_env(env)
        overrides = result["WINEDLLOVERRIDES"].split(";")

        self.assertNotIn("DISPLAY", result)
        self.assertNotIn("WAYLAND_DISPLAY", result)
        self.assertNotIn("PROTON_ENABLE_WAYLAND", result)
        self.assertEqual(result["SDL_VIDEODRIVER"], "dummy")
        self.assertEqual(overrides.count("cryptbase=b"), 1)
        self.assertIn("foo=n", overrides)
        self.assertNotIn("cryptbase=n,b", overrides)
        self.assertNotIn("dxgi=n", overrides)

    def test_headless_setup_prefers_seeded_native_cryptbase(self):
        result = prefix.headless_setup_env(
            {"WINEDLLOVERRIDES": "cryptbase=b;foo=n"},
            native_cryptbase=True,
        )
        overrides = result["WINEDLLOVERRIDES"].split(";")

        self.assertEqual(overrides.count("cryptbase=n,b"), 1)
        self.assertNotIn("cryptbase=b", overrides)
        self.assertIn("foo=n", overrides)

    def test_bootstrap_cryptbase_is_seeded_into_fresh_managed_prefix(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pfx = root / "fresh-prefix"
            engine = root / "managed-engine"
            engine.mkdir()

            with mock.patch.dict(prefix.os.environ, {}, clear=True), \
                    mock.patch.object(prefix, "PFX", pfx), \
                    mock.patch.object(prefix, "WINEGDK_OUT", engine), \
                    mock.patch.object(
                        prefix, "proton_path", return_value=engine), \
                    mock.patch(
                        "bol.fixups._install_cryptbase_in_prefix",
                        return_value=True) as install:
                self.assertTrue(
                    prefix.seed_managed_bootstrap_cryptbase(pfx))

            self.assertTrue(
                (pfx / "drive_c/windows/system32").is_dir())
            install.assert_called_once_with(pfx)

    def test_bootstrap_cryptbase_never_mutates_explicit_prefix(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            managed = root / "managed-prefix"
            external = root / "external-prefix"
            engine = root / "managed-engine"
            with mock.patch.dict(
                    prefix.os.environ,
                    {"BOL_WINEPREFIX": str(external)}, clear=True), \
                    mock.patch.object(prefix, "PFX", managed), \
                    mock.patch.object(prefix, "WINEGDK_OUT", engine), \
                    mock.patch.object(
                        prefix, "proton_path", return_value=engine), \
                    mock.patch(
                        "bol.fixups._install_cryptbase_in_prefix") as install:
                self.assertFalse(
                    prefix.seed_managed_bootstrap_cryptbase(external))

            self.assertFalse(external.exists())
            install.assert_not_called()

    def test_bootstrap_cryptbase_never_mutates_custom_engine_prefix(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pfx = root / "managed-prefix"
            managed_engine = root / "managed-engine"
            custom_engine = root / "custom-engine"
            with mock.patch.dict(prefix.os.environ, {}, clear=True), \
                    mock.patch.object(prefix, "PFX", pfx), \
                    mock.patch.object(
                        prefix, "WINEGDK_OUT", managed_engine), \
                    mock.patch.object(
                        prefix, "proton_path", return_value=custom_engine), \
                    mock.patch(
                        "bol.fixups._install_cryptbase_in_prefix") as install:
                self.assertFalse(
                    prefix.seed_managed_bootstrap_cryptbase(pfx))

            self.assertFalse(pfx.exists())
            install.assert_not_called()

    def test_bootstrap_cryptbase_rejects_system32_symlink(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pfx = root / "managed-prefix"
            engine = root / "managed-engine"
            external = root / "external"
            (pfx / "drive_c/windows").mkdir(parents=True)
            engine.mkdir()
            external.mkdir()
            (pfx / "drive_c/windows/system32").symlink_to(
                external, target_is_directory=True)

            with mock.patch.dict(prefix.os.environ, {}, clear=True), \
                    mock.patch.object(prefix, "PFX", pfx), \
                    mock.patch.object(prefix, "WINEGDK_OUT", engine), \
                    mock.patch.object(
                        prefix, "proton_path", return_value=engine), \
                    mock.patch(
                        "bol.fixups._install_cryptbase_in_prefix") as install:
                with self.assertRaisesRegex(BolError, "unsafe system32"):
                    prefix.seed_managed_bootstrap_cryptbase(pfx)

            self.assertEqual(list(external.iterdir()), [])
            install.assert_not_called()

    def test_proton_umu_uses_neutral_gameid_without_rewriting_steam_identity(
            self):
        cases = [
            ({"GAMEID": "umu-existing"}, "umu-existing"),
            ({"GAMEID": "17"}, "17"),
            ({"GAMEID": "0", "SteamAppId": "123",
              "SteamGameId": "456"}, "0"),
            ({"SteamAppId": "invalid",
              "SteamGameId": "456"}, "umu-default"),
            ({"SteamAppId": "0", "SteamGameId": ""}, "umu-default"),
        ]
        for inherited, expected in cases:
            with self.subTest(inherited=inherited), \
                    mock.patch.dict(prefix.os.environ, inherited, clear=True), \
                    mock.patch.object(
                        prefix, "steam_compat_dir",
                        return_value=Path("/tmp/steam")), \
                    mock.patch.object(
                        prefix, "proton_path",
                        return_value=Path("/tmp/proton")), \
                    mock.patch.object(
                        prefix, "ensure_umu",
                        return_value=Path("/tmp/umu-run")), \
                    mock.patch.object(prefix, "info"):
                _cmd, env = prefix.proton_umu_cmd(
                    "Minecraft.Windows.exe", Path("/tmp/external-prefix"))

            self.assertEqual(env["GAMEID"], expected)
            self.assertEqual(env["UMU_FOLDERS_PATH"], str(prefix.DATA))
            for name in ("SteamAppId", "SteamGameId"):
                if name in inherited:
                    self.assertEqual(env[name], inherited[name])

    def test_managed_engine_forces_pure_wow64_runtime(self):
        managed = Path("/tmp/managed-winegdk")
        with mock.patch.dict(prefix.os.environ, {}, clear=True), \
                mock.patch.object(prefix, "WINEGDK_OUT", managed), \
                mock.patch.object(prefix, "steam_compat_dir",
                                  return_value=Path("/tmp/steam")), \
                mock.patch.object(prefix, "proton_path",
                                  return_value=managed), \
                mock.patch.object(prefix, "ensure_umu",
                                  return_value=Path("/tmp/umu-run")):
            _cmd, env = prefix.proton_umu_cmd(
                "Minecraft.Windows.exe", Path("/tmp/prefix"))

        self.assertEqual(env["PROTON_USE_WOW64"], "1")

    def test_custom_engine_does_not_force_pure_wow64_runtime(self):
        managed = Path("/tmp/managed-winegdk")
        custom = Path("/tmp/custom-engine")
        with mock.patch.dict(prefix.os.environ, {}, clear=True), \
                mock.patch.object(prefix, "WINEGDK_OUT", managed), \
                mock.patch.object(prefix, "steam_compat_dir",
                                  return_value=Path("/tmp/steam")), \
                mock.patch.object(prefix, "proton_path",
                                  return_value=custom), \
                mock.patch.object(prefix, "ensure_umu",
                                  return_value=Path("/tmp/umu-run")):
            _cmd, env = prefix.proton_umu_cmd(
                "Minecraft.Windows.exe", Path("/tmp/prefix"))

        self.assertNotIn("PROTON_USE_WOW64", env)


class CryptbaseInstallTests(unittest.TestCase):
    def test_install_is_atomic_and_preserves_existing_file_once(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source_dir = root / "openssl-set"
            source_dir.mkdir()
            (source_dir / "cryptbase.dll").write_bytes(b"verified-rng")
            pfx = root / "prefix"
            system32 = pfx / "drive_c/windows/system32"
            system32.mkdir(parents=True)
            destination = system32 / "cryptbase.dll"
            destination.write_bytes(b"old-placeholder")

            with mock.patch.object(
                    fixups, "OPENSSL_XCURL_SET", source_dir), \
                    mock.patch.object(fixups, "ok"):
                self.assertTrue(
                    fixups._install_cryptbase_in_prefix(pfx))
                self.assertTrue(
                    fixups._install_cryptbase_in_prefix(pfx))

            self.assertEqual(destination.read_bytes(), b"verified-rng")
            self.assertEqual(
                (system32 / "cryptbase.dll.bol-orig").read_bytes(),
                b"old-placeholder",
            )
            self.assertEqual(
                list(system32.glob(".cryptbase.dll-*")), [])

    def test_install_replaces_symlink_without_touching_its_target(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source_dir = root / "openssl-set"
            source_dir.mkdir()
            (source_dir / "cryptbase.dll").write_bytes(b"verified-rng")
            pfx = root / "prefix"
            system32 = pfx / "drive_c/windows/system32"
            system32.mkdir(parents=True)
            external = root / "external-cryptbase.dll"
            external.write_bytes(b"external")
            destination = system32 / "cryptbase.dll"
            destination.symlink_to(external)

            with mock.patch.object(
                    fixups, "OPENSSL_XCURL_SET", source_dir), \
                    mock.patch.object(fixups, "ok"):
                self.assertTrue(
                    fixups._install_cryptbase_in_prefix(pfx))

            self.assertFalse(destination.is_symlink())
            self.assertEqual(destination.read_bytes(), b"verified-rng")
            self.assertEqual(external.read_bytes(), b"external")
            backup = system32 / "cryptbase.dll.bol-orig"
            self.assertTrue(backup.is_symlink())
            self.assertEqual(os.readlink(backup), str(external))


class ManagedRuntimeRepairTests(unittest.TestCase):
    def _paths(self, root):
        engine = root / "engine"
        pfx = root / "prefix"
        source = engine / "files/lib/wine/x86_64-windows/user32.dll"
        target = pfx / "drive_c/windows/system32/user32.dll"
        source.parent.mkdir(parents=True)
        _make_ready_prefix(pfx)
        return engine, pfx, source, target

    def test_corrupt_managed_user32_is_backed_up_and_replaced(self):
        with tempfile.TemporaryDirectory() as td:
            engine, pfx, source, target = self._paths(Path(td))
            source.write_bytes(b"verified-user32")
            target.write_bytes(b"corrupt-user32")
            with mock.patch.dict(prefix.os.environ, {}, clear=True), \
                    mock.patch.object(prefix, "PFX", pfx), \
                    mock.patch.object(prefix, "WINEGDK_OUT", engine), \
                    mock.patch.object(prefix, "proton_path",
                                      return_value=engine), \
                    mock.patch.object(prefix, "require_prefix_idle") as idle, \
                    mock.patch.object(prefix, "ok"):
                changed = prefix.repair_managed_prefix_user32(pfx)

            self.assertTrue(changed)
            self.assertEqual(target.read_bytes(), b"verified-user32")
            self.assertEqual(
                target.with_name(
                    "user32.dll.bol-managed-backup").read_bytes(),
                b"corrupt-user32",
            )
            idle.assert_called_once_with(
                pfx, "repair the managed Wine runtime")
            self.assertEqual(list(target.parent.glob(".user32.dll-*")), [])

    def test_matching_managed_user32_is_untouched(self):
        with tempfile.TemporaryDirectory() as td:
            engine, pfx, source, target = self._paths(Path(td))
            source.write_bytes(b"verified-user32")
            target.write_bytes(b"verified-user32")
            with mock.patch.dict(prefix.os.environ, {}, clear=True), \
                    mock.patch.object(prefix, "PFX", pfx), \
                    mock.patch.object(prefix, "WINEGDK_OUT", engine), \
                    mock.patch.object(prefix, "proton_path",
                                      return_value=engine), \
                    mock.patch.object(prefix, "require_prefix_idle") as idle:
                changed = prefix.repair_managed_prefix_user32(pfx)

            self.assertFalse(changed)
            idle.assert_not_called()
            self.assertFalse(target.with_name(
                "user32.dll.bol-managed-backup").exists())

    def test_matching_managed_user32_symlink_is_replaced(self):
        with tempfile.TemporaryDirectory() as td:
            engine, pfx, source, target = self._paths(Path(td))
            source.write_bytes(b"verified-user32")
            target.symlink_to(source)
            with mock.patch.dict(prefix.os.environ, {}, clear=True), \
                    mock.patch.object(prefix, "PFX", pfx), \
                    mock.patch.object(prefix, "WINEGDK_OUT", engine), \
                    mock.patch.object(prefix, "proton_path",
                                      return_value=engine), \
                    mock.patch.object(prefix, "require_prefix_idle") as idle:
                changed = prefix.repair_managed_prefix_user32(pfx)

            self.assertTrue(changed)
            self.assertFalse(target.is_symlink())
            self.assertEqual(target.read_bytes(), b"verified-user32")
            backup = target.with_name("user32.dll.bol-managed-backup")
            self.assertTrue(backup.is_symlink())
            self.assertEqual(os.readlink(backup), str(source))
            idle.assert_called_once_with(
                pfx, "repair the managed Wine runtime")

    def test_external_system32_link_is_rejected_without_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            engine, pfx, source, target = self._paths(root)
            source.write_bytes(b"verified-user32")
            target.parent.rmdir()
            external = root / "external-system32"
            external.mkdir()
            external_target = external / "user32.dll"
            external_target.write_bytes(b"external-user32")
            target.parent.symlink_to(external, target_is_directory=True)

            with mock.patch.dict(prefix.os.environ, {}, clear=True), \
                    mock.patch.object(prefix, "PFX", pfx), \
                    mock.patch.object(prefix, "WINEGDK_OUT", engine), \
                    mock.patch.object(prefix, "proton_path",
                                      return_value=engine), \
                    mock.patch.object(prefix, "require_prefix_idle") as idle:
                with self.assertRaisesRegex(BolError, "unsafe system32"):
                    prefix.repair_managed_prefix_user32(pfx)

            self.assertEqual(external_target.read_bytes(), b"external-user32")
            self.assertFalse((external / (
                "user32.dll.bol-managed-backup")).exists())
            idle.assert_not_called()

    def test_custom_engine_and_external_prefix_are_never_repaired(self):
        with tempfile.TemporaryDirectory() as td:
            engine, pfx, source, target = self._paths(Path(td))
            custom = Path(td) / "custom-engine"
            external = Path(td) / "external-prefix"
            source.write_bytes(b"verified-user32")
            target.write_bytes(b"corrupt-user32")
            with mock.patch.dict(prefix.os.environ, {}, clear=True), \
                    mock.patch.object(prefix, "PFX", pfx), \
                    mock.patch.object(prefix, "WINEGDK_OUT", engine), \
                    mock.patch.object(prefix, "proton_path",
                                      return_value=custom):
                self.assertFalse(
                    prefix.repair_managed_prefix_user32(pfx))
            with mock.patch.dict(prefix.os.environ, {}, clear=True), \
                    mock.patch.object(prefix, "PFX", pfx), \
                    mock.patch.object(prefix, "WINEGDK_OUT", engine), \
                    mock.patch.object(prefix, "proton_path",
                                      return_value=engine):
                self.assertFalse(
                    prefix.repair_managed_prefix_user32(external))

            self.assertEqual(target.read_bytes(), b"corrupt-user32")


class PrefixSetupTests(unittest.TestCase):
    def test_setup_stops_before_gameinput_when_prefix_boot_fails(self):
        with tempfile.TemporaryDirectory() as td:
            settings = {
                "game_dir": td,
                "proton_source": "proton",
            }
            with mock.patch.object(gamesetup, "mkdirs"), \
                    mock.patch.object(gamesetup, "load_settings",
                                      return_value=settings), \
                    mock.patch.object(gamesetup, "ensure_login_deps"), \
                    mock.patch.object(gamesetup, "_game_root",
                                      return_value=True), \
                    mock.patch.object(gamesetup, "ensure_proton"), \
                    mock.patch.object(gamesetup, "ensure_umu"), \
                    mock.patch.object(gamesetup, "fix_curl_ssl"), \
                    mock.patch.object(gamesetup, "boot_prefix",
                                      return_value=False), \
                    mock.patch.object(
                        gamesetup, "install_gameinput") as install, \
                    mock.patch.object(
                        gamesetup, "hide_signin_button") as hide, \
                    mock.patch.object(gamesetup, "ok") as setup_ok:
                with self.assertRaisesRegex(
                        BolError, "Could not initialise the Wine prefix"):
                    gamesetup._do_setup()

            install.assert_not_called()
            hide.assert_not_called()
            setup_ok.assert_not_called()

    def test_gameinput_refuses_to_materialise_an_incomplete_prefix(self):
        with tempfile.TemporaryDirectory() as td:
            pfx = Path(td) / "missing-prefix"
            with self.assertRaisesRegex(BolError, "prefix is incomplete"):
                gameinput.install_gameinput(pfx, Path(td) / "game")
            self.assertFalse(pfx.exists())


class PrefixOperationLockTests(unittest.TestCase):
    def test_repair_removes_managed_tree_for_clean_next_setup(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            compat = root / "compat"
            pfx = compat / "pfx"
            pfx.mkdir(parents=True)
            (pfx / "broken-system.reg").write_text("partial")

            with mock.patch.object(prefix, "DATA", root / "data"), \
                    mock.patch.object(prefix, "COMPAT", compat), \
                    mock.patch.object(prefix, "PFX", pfx), \
                    mock.patch.object(
                        prefix, "stop_prefix_procs", return_value=(0, 0)
                    ) as stop, \
                    mock.patch.object(prefix, "require_prefix_idle") as idle, \
                    mock.patch.object(prefix, "ok") as repaired:
                prefix.reset_prefix()

            self.assertFalse(compat.exists())
            stop.assert_called_once_with(pfx)
            idle.assert_called_once_with(pfx, "repair the Wine prefix")
            repaired.assert_called_once()

    def test_repair_does_not_report_success_when_removal_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            compat = root / "compat"
            pfx = compat / "pfx"
            pfx.mkdir(parents=True)

            with mock.patch.object(prefix, "DATA", root / "data"), \
                    mock.patch.object(prefix, "COMPAT", compat), \
                    mock.patch.object(prefix, "PFX", pfx), \
                    mock.patch.object(
                        prefix, "stop_prefix_procs", return_value=(0, 0)
                    ), \
                    mock.patch.object(prefix, "require_prefix_idle"), \
                    mock.patch.object(
                        prefix.shutil,
                        "rmtree",
                        side_effect=PermissionError("read-only prefix"),
                    ), \
                    mock.patch.object(prefix, "ok") as repaired, \
                    self.assertRaisesRegex(
                        PermissionError, "read-only prefix"
                    ):
                prefix.reset_prefix()

            repaired.assert_not_called()

    def test_repair_unlinks_dangling_compat_symlink_without_following_it(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            compat = root / "compat"
            compat.symlink_to(root / "missing-target")
            pfx = compat / "pfx"

            with mock.patch.object(prefix, "DATA", root / "data"), \
                    mock.patch.object(prefix, "COMPAT", compat), \
                    mock.patch.object(prefix, "PFX", pfx), \
                    mock.patch.object(
                        prefix, "stop_prefix_procs", return_value=(0, 0)
                    ), \
                    mock.patch.object(prefix, "require_prefix_idle"), \
                    mock.patch.object(prefix.shutil, "rmtree") as rmtree, \
                    mock.patch.object(prefix, "ok") as repaired:
                prefix.reset_prefix()

            self.assertFalse(compat.is_symlink())
            rmtree.assert_not_called()
            repaired.assert_called_once()

    def test_repair_cannot_run_while_common_lock_is_held(self):
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(prefix, "DATA", Path(td)), \
                mock.patch.object(prefix, "stop_prefix_procs") as stop, \
                mock.patch.object(prefix.shutil, "rmtree") as rmtree:
            with prefix.prefix_operation_lock("test operation"):
                with self.assertRaisesRegex(BolError, "another BedrockOnLinux"):
                    prefix.reset_prefix()

        stop.assert_not_called()
        rmtree.assert_not_called()

    def test_setup_holds_the_same_operation_lock(self):
        events = []

        @contextmanager
        def locked(_operation):
            events.append("lock-enter")
            try:
                yield
            finally:
                events.append("lock-exit")

        @contextmanager
        def shared_locked(_operation, exclusive):
            events.append(f"shared-{'exclusive' if exclusive else 'shared'}-enter")
            try:
                yield
            finally:
                events.append("shared-exit")

        with mock.patch.object(gamesetup, "shared_assets_lock",
                               shared_locked), \
                mock.patch.object(gamesetup, "prefix_operation_lock", locked), \
                mock.patch.object(
                    gamesetup, "_do_setup",
                    side_effect=lambda *args: events.append("setup") or "done"):
            self.assertEqual(gamesetup.do_setup(), "done")

        self.assertEqual(
            events,
            [
                "shared-exclusive-enter",
                "lock-enter",
                "setup",
                "lock-exit",
                "shared-exit",
            ],
        )

    def test_launch_holds_shared_assets_lock_exclusively(self):
        events = []

        @contextmanager
        def shared_locked(operation, exclusive):
            events.append((operation, exclusive))
            yield

        @contextmanager
        def prefix_locked(operation):
            events.append((operation, "prefix"))
            yield

        with mock.patch.object(prefix, "shared_assets_lock", shared_locked), \
                mock.patch.object(
                    prefix, "prefix_operation_lock", prefix_locked
                ):
            with prefix.launch_lock():
                events.append(("launch", "body"))

        self.assertEqual(
            events,
            [
                ("start Minecraft", True),
                ("start Minecraft", "prefix"),
                ("launch", "body"),
            ],
        )

    def test_inherited_launch_descriptors_keep_locks_after_parent_closes(self):
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(prefix, "DATA", Path(td) / "profile"), \
                mock.patch.object(prefix, "GAMES", Path(td) / "shared/games"):
            child = None
            with prefix.launch_lock() as lock_fds:
                child = subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        (
                            "import sys,time;"
                            "sys.stdout.write('ready\\n');sys.stdout.flush();"
                            "time.sleep(10)"
                        ),
                    ],
                    stdout=subprocess.PIPE,
                    text=True,
                    pass_fds=lock_fds,
                )
                self.assertEqual(child.stdout.readline().strip(), "ready")
            try:
                with self.assertRaisesRegex(
                        BolError, "shared Minecraft files are in use"):
                    with prefix.launch_lock():
                        pass
            finally:
                child.terminate()
                child.wait(timeout=5)
                child.stdout.close()

            with prefix.launch_lock():
                pass

    def test_profiles_share_asset_lock_and_parallel_play_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "base"
            (base / "games").mkdir(parents=True)
            one = Path(td) / "one"
            two = Path(td) / "two"
            one.mkdir()
            two.mkdir()
            (one / "games").symlink_to(base / "games")
            (two / "games").symlink_to(base / "games")

            with mock.patch.object(prefix, "GAMES", one / "games"):
                with prefix.shared_assets_lock("play one", exclusive=True):
                    with mock.patch.object(prefix, "GAMES", two / "games"):
                        with self.assertRaisesRegex(
                                BolError, "shared Minecraft files are in use"):
                            with prefix.shared_assets_lock(
                                    "play two", exclusive=True):
                                pass
                        with self.assertRaisesRegex(
                                BolError, "shared Minecraft files are in use"):
                            with prefix.shared_assets_lock(
                                    "run setup", exclusive=True):
                                pass


if __name__ == "__main__":
    unittest.main()
