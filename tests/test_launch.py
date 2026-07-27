"""Regression tests for launch-time engine selection."""
# SPDX-License-Identifier: MIT

import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

from bol import launch


class GraphicsEngineLaunchTests(unittest.TestCase):
    def _exercise_ready_launch(self, root, popen, arm, disarm,
                               prefix_idle=True, mark=None, preauth=True,
                               lock_fds=(), managed_engine=True):
        content = root / "content"
        logs = root / "logs"
        data = root / "data"
        content.mkdir()
        logs.mkdir()
        data.mkdir()
        (content / "Minecraft.Windows.exe").write_bytes(b"MZ")
        settings = {"game_dir": str(content)}
        patches = (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(launch, "CONTENT", content),
            mock.patch.object(launch, "LOGS", logs),
            mock.patch.object(launch, "DATA", data),
            mock.patch.object(launch, "load_settings", return_value=settings),
            mock.patch.object(launch, "proton_path",
                              return_value=Path("/tmp/fake-engine")),
            mock.patch.object(launch, "custom_proton",
                              return_value=not managed_engine),
            mock.patch.object(launch, "require_safe_graphics_session"),
            mock.patch.object(launch, "retire_idle_current_boot_marker"),
            mock.patch.object(launch, "_prepare_launch_engine"),
            mock.patch.object(
                launch, "msa_session_snapshot",
                return_value=({"refresh_token": "refresh"}, "a" * 32)),
            mock.patch.object(launch, "msa_refresh", return_value=None),
            mock.patch.object(launch, "msa_save_for_account_epoch",
                              return_value=True),
            mock.patch.object(launch, "account_epoch_is_current",
                              return_value=True),
            mock.patch.object(launch, "boot_prefix", return_value=True),
            mock.patch.object(launch, "wine_apply_winegdk_prereqs"),
            mock.patch.object(launch, "_install_cryptbase_in_prefix"),
            mock.patch.object(launch, "install_gameinput"),
            mock.patch.object(launch, "wine_reg_set_refresh_token",
                              return_value=True),
            mock.patch.object(launch, "ensure_login_deps"),
            mock.patch.object(launch, "xbl_preauth", return_value=preauth),
            mock.patch.object(launch, "bump_stack_reserve"),
            mock.patch.object(launch, "proton_umu_cmd",
                              return_value=(["fake-umu"], {})),
            mock.patch.object(launch, "patch_options"),
            mock.patch.object(launch, "diagnose", return_value=[]),
            mock.patch.object(launch, "_prefix_stably_idle_after_wrapper",
                              return_value=prefix_idle),
            mock.patch.object(launch, "arm_gpu_launch", side_effect=arm),
            mock.patch.object(
                launch, "mark_gpu_wrapper_returned",
                side_effect=mark or (lambda _token: True)),
            mock.patch.object(launch, "disarm_gpu_launch", side_effect=disarm),
            mock.patch.object(launch.subprocess, "Popen", side_effect=popen),
            mock.patch.object(launch, "info"),
            mock.patch.object(launch, "ok"),
            mock.patch.object(launch, "warn"),
        )
        with ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            return launch._launch_once(lock_fds=lock_fds)

    def test_managed_install_finishes_before_graphics_validation(self):
        calls = []
        with mock.patch.object(launch, "active_prefix",
                               return_value=Path("/tmp/bol-prefix")), \
                mock.patch.object(
                    launch, "prefix_processes",
                    side_effect=lambda _prefix: calls.append("idle") or []), \
                mock.patch.object(launch, "custom_proton", return_value=False), \
                mock.patch.object(launch, "ensure_winegdk",
                                  side_effect=lambda: calls.append("install")), \
                mock.patch.object(launch, "_prepare_graphics_engine",
                                  side_effect=lambda: calls.append("graphics")):
            launch._prepare_launch_engine()
        self.assertEqual(calls, ["idle", "install", "graphics"])

    def test_corrected_engine_is_installed_before_session_safety_check(self):
        calls = []

        def stop_after_safety():
            calls.append("safety")
            raise launch.BolError("stop after order check")

        with tempfile.TemporaryDirectory() as directory:
            content = Path(directory)
            (content / "Minecraft.Windows.exe").write_bytes(b"MZ")
            with mock.patch.object(
                    launch, "load_settings",
                    return_value={"game_dir": str(content)}), \
                    mock.patch.object(launch, "CONTENT", content), \
                    mock.patch.object(launch, "proton_path",
                                      return_value=Path("/tmp/engine")), \
                    mock.patch.object(
                        launch, "_prepare_launch_engine",
                        side_effect=lambda: calls.append("engine")), \
                    mock.patch.object(
                        launch, "retire_idle_current_boot_marker",
                        side_effect=lambda: calls.append("retire")), \
                    mock.patch.object(
                        launch, "require_safe_graphics_session",
                        side_effect=stop_after_safety):
                with self.assertRaisesRegex(launch.BolError, "order check"):
                    launch._launch_once()
        self.assertEqual(calls, ["engine", "retire", "safety"])

    def test_rejected_managed_install_never_reaches_graphics_or_wine(self):
        with mock.patch.object(launch, "active_prefix",
                               return_value=Path("/tmp/bol-prefix")), \
                mock.patch.object(launch, "prefix_processes",
                                  return_value=[]) as processes, \
                mock.patch.object(launch, "custom_proton", return_value=False), \
                mock.patch.object(launch, "ensure_winegdk",
                                  side_effect=launch.BolError("stale r10")), \
                mock.patch.object(launch, "_prepare_graphics_engine") as graphics, \
                mock.patch.object(launch, "patch_proton") as patch:
            with self.assertRaisesRegex(launch.BolError, "stale r10"):
                launch._prepare_launch_engine()
        processes.assert_called_once_with(Path("/tmp/bol-prefix"))
        graphics.assert_not_called()
        patch.assert_not_called()

    def test_running_prefix_is_refused_not_killed(self):
        with mock.patch.object(launch, "active_prefix",
                               return_value=Path("/tmp/bol-prefix")), \
                mock.patch.object(launch, "prefix_processes",
                                  return_value=[12, 34]), \
                mock.patch.object(launch, "ensure_winegdk") as install:
            with self.assertRaisesRegex(launch.BolError, "already active"):
                launch._prepare_launch_engine()
        install.assert_not_called()

    def test_managed_engine_activates_compatible_variant(self):
        engine = Path("/tmp/GDK-Proton-xuser")
        with mock.patch.object(launch, "custom_proton", return_value=False), \
                mock.patch.object(launch, "proton_path", return_value=engine), \
                mock.patch.object(launch, "prepare_universal_vkd3d",
                                  return_value=("nv-dgc", True)) as prepare, \
                mock.patch.object(launch, "info"):
            self.assertEqual(launch._prepare_graphics_engine(), "nv-dgc")
        prepare.assert_called_once_with(engine, launch.WINEGDK_BUILD_REV)

    def test_user_supplied_engine_is_never_rewritten(self):
        with mock.patch.object(launch, "custom_proton", return_value=True), \
                mock.patch.object(launch, "prepare_universal_vkd3d") as prepare:
            self.assertIsNone(launch._prepare_graphics_engine())
        prepare.assert_not_called()

    def test_managed_engine_validation_error_is_visible(self):
        with mock.patch.object(launch, "custom_proton", return_value=False), \
                mock.patch.object(launch, "proton_path",
                                  return_value=Path("/tmp/engine")), \
                mock.patch.object(launch, "prepare_universal_vkd3d",
                                  side_effect=launch.BolError(
                                      "engine revision mismatch")), \
                mock.patch.object(launch, "die",
                                  side_effect=launch.BolError) as die:
            with self.assertRaises(launch.BolError):
                launch._prepare_graphics_engine()
        die.assert_called_once_with("engine revision mismatch")

    def test_raw_va_workaround_preserves_user_vkd3d_options(self):
        env = {"VKD3D_CONFIG": "breadcrumbs;single_queue"}
        launch._require_vkd3d_config(env, "force_raw_va_cbv")
        self.assertEqual(
            env["VKD3D_CONFIG"],
            "breadcrumbs,single_queue,force_raw_va_cbv",
        )

    def test_raw_va_workaround_is_idempotent(self):
        env = {"VKD3D_CONFIG": "force_raw_va_cbv,breadcrumbs"}
        launch._require_vkd3d_config(env, "force_raw_va_cbv")
        self.assertEqual(env["VKD3D_CONFIG"],
                         "force_raw_va_cbv,breadcrumbs")

    def test_x11_neutralises_inherited_winewayland_and_enables_noopwr(self):
        env = {"PROTON_ENABLE_WAYLAND": "1"}
        launch._configure_runtime_compat(
            env, {}, "x11", True, host_env={"PROTON_ENABLE_WAYLAND": "1"},
            steam_deck=False,
        )
        self.assertEqual(env["PROTON_ENABLE_WAYLAND"], "0")
        self.assertEqual(env["WINE_DISABLE_VULKAN_OPWR"], "1")

    def test_native_x11_does_not_enable_wayland_presentation_workaround(self):
        env = {}
        launch._configure_runtime_compat(
            env, {}, "x11", False, host_env={}, steam_deck=False,
        )
        self.assertNotIn("WINE_DISABLE_VULKAN_OPWR", env)

    def test_wayland_backend_is_not_forced_back_to_x11(self):
        env = {}
        launch._configure_runtime_compat(
            env, {}, "wayland", True, host_env={}, steam_deck=False,
        )
        self.assertNotIn("PROTON_ENABLE_WAYLAND", env)

    def test_non_steam_launch_prefers_sdl_controller_mapping(self):
        env = {}
        launch._configure_runtime_compat(
            env, {}, "x11", False, host_env={}, steam_deck=False,
        )
        self.assertEqual(env["PROTON_PREFER_SDL"], "1")
        self.assertNotIn("PROTON_DISABLE_HIDRAW", env)

    def test_steam_launch_leaves_input_mapping_to_steam(self):
        env = {}
        launch._configure_runtime_compat(
            env, {}, "x11", False,
            host_env={
                "SteamGameId": "1234567890",
                "SteamVirtualGamepadInfo": "/run/user/1000/steam-input",
            },
            steam_deck=False,
        )
        self.assertNotIn("PROTON_PREFER_SDL", env)
        self.assertEqual(
            set(env["PROTON_DISABLE_HIDRAW"].split(",")),
            {
                "0x054C/0x05C4",
                "0x054C/0x09CC",
                "0x054C/0x0BA0",
                "0x054C/0x0CE6",
                "0x054C/0x0DF2",
            },
        )

    def test_steam_virtual_gamepad_without_app_id_keeps_steam_input(self):
        gamepad_info = "/run/user/1000/steam-virtual-gamepad-info"
        env = {"SteamVirtualGamepadInfo_Proton": gamepad_info}
        launch._configure_runtime_compat(
            env, {}, "x11", False, host_env=dict(env), steam_deck=False,
        )
        self.assertNotIn("PROTON_PREFER_SDL", env)
        self.assertIn("0x054C/0x0DF2", env["PROTON_DISABLE_HIDRAW"])
        self.assertEqual(env["SteamVirtualGamepadInfo_Proton"],
                         gamepad_info)

    def test_steam_sony_filter_replaces_global_value_and_spares_non_sony(self):
        env = {"PROTON_DISABLE_HIDRAW": "1"}
        launch._configure_runtime_compat(
            env, {}, "x11", False,
            host_env={
                "SteamGameId": "1234567890",
                "SteamVirtualGamepadInfo_Proton":
                    "/run/user/1000/steam-input",
                "PROTON_DISABLE_HIDRAW": "1",
            },
            steam_deck=False,
        )
        sony_ids = env["PROTON_DISABLE_HIDRAW"]
        self.assertIn("0x054C/0x0DF2", sony_ids)
        self.assertNotIn("0x045E/", sony_ids)  # Microsoft/Xbox
        self.assertNotIn("0x057E/", sony_ids)  # Nintendo
        self.assertNotEqual(sony_ids, "1")

    def test_empty_steam_markers_do_not_disable_standalone_sdl_input(self):
        env = {}
        launch._configure_runtime_compat(
            env, {}, "x11", False,
            host_env={
                "SteamGameId": "0",
                "SteamAppId": "",
                "SteamVirtualGamepadInfo": " ",
                "SteamVirtualGamepadInfo_Proton": "",
            },
            steam_deck=False,
        )
        self.assertEqual(env["PROTON_PREFER_SDL"], "1")

    def test_steam_app_id_without_virtual_gamepad_uses_sdl_fallback(self):
        env = {}
        launch._configure_runtime_compat(
            env, {}, "x11", False,
            host_env={
                "SteamGameId": "1234567890",
                "SteamAppId": "1234567890",
                "STEAM_COMPAT_CLIENT_INSTALL_PATH": "/opt/steam",
            },
            steam_deck=False,
        )
        self.assertEqual(env["PROTON_PREFER_SDL"], "1")
        self.assertNotIn("PROTON_DISABLE_HIDRAW", env)

    def test_winewayland_uses_sdl_even_with_virtual_steam_gamepad(self):
        env = {
            "SteamVirtualGamepadInfo_Proton":
                "/run/user/1000/steam-input",
            "PROTON_DISABLE_HIDRAW": "1",
            "PROTON_NO_STEAMINPUT": "0",
        }
        launch._configure_runtime_compat(
            env, {}, "wayland", True, host_env=dict(env), steam_deck=False,
        )
        self.assertEqual(env["PROTON_PREFER_SDL"], "1")
        self.assertNotIn("PROTON_DISABLE_HIDRAW", env)
        self.assertNotIn("PROTON_NO_STEAMINPUT", env)

    def test_steam_deck_disables_wine_window_decoration(self):
        env = {}
        launch._configure_runtime_compat(
            env, {}, "x11", True, host_env={"SteamDeck": "1"},
            steam_deck=True,
        )
        self.assertEqual(env["PROTON_NO_WM_DECORATION"], "1")

    def test_legacy_renderer_uses_supported_opengl_fallback(self):
        env = {}
        launch._configure_runtime_compat(
            env, {"renderer": "opengl"}, "x11", False, host_env={},
            steam_deck=False,
        )
        self.assertEqual(env["PROTON_USE_WINED3D"], "1")

    def test_normal_launch_does_not_enable_proton_debug_log(self):
        env = {
            "PROTON_LOG": "1",
            "PROTON_LOG_DIR": "/tmp/inherited-logs",
            "WINEDEBUG": "trace+all",
        }
        launch._configure_runtime_compat(
            env, {}, "x11", False, diagnostics=False,
            host_env=dict(env),
            steam_deck=False,
        )
        self.assertNotIn("PROTON_LOG", env)
        self.assertNotIn("PROTON_LOG_DIR", env)
        self.assertEqual(env["WINEDEBUG"], "-all")

    def test_diagnostics_keep_errors_without_hot_gdk_traces(self):
        env = {"WINEDEBUG": "trace+all"}
        with mock.patch.object(launch, "LOGS", Path("/tmp/bol-logs")):
            launch._configure_runtime_compat(
                env, {}, "x11", False, diagnostics=True,
                host_env=dict(env),
                steam_deck=False,
            )
        self.assertEqual(env["PROTON_LOG"], "1")
        self.assertEqual(env["PROTON_LOG_DIR"], "/tmp/bol-logs")
        self.assertIn("+gdkc", env["WINEDEBUG"])
        self.assertIn("trace-gdkc", env["WINEDEBUG"])
        self.assertIn("+xgameruntime", env["WINEDEBUG"])
        self.assertIn("trace-xgameruntime", env["WINEDEBUG"])
        self.assertNotIn("trace+gdkc", env["WINEDEBUG"])
        self.assertNotIn("trace+xgameruntime", env["WINEDEBUG"])
        self.assertNotIn("trace+all", env["WINEDEBUG"])

    def test_managed_graphics_cache_is_persistent_and_private(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / "profile"
            env = {}
            with mock.patch.object(launch, "DATA", data):
                launch._configure_graphics_cache(env, managed_engine=True)

            cache = data / "graphics-cache"
            self.assertTrue(cache.is_dir())
            self.assertEqual(cache.stat().st_mode & 0o777, 0o700)
            self.assertEqual(env["VKD3D_SHADER_CACHE_PATH"], str(cache))
            self.assertEqual(env["DXVK_SHADER_CACHE_PATH"], str(cache))

    def test_custom_engine_keeps_its_own_graphics_cache_settings(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / "profile"
            env = {
                "VKD3D_SHADER_CACHE_PATH": "/custom/vkd3d",
                "DXVK_SHADER_CACHE_PATH": "/custom/dxvk",
            }
            with mock.patch.object(launch, "DATA", data):
                launch._configure_graphics_cache(env, managed_engine=False)

            self.assertFalse((data / "graphics-cache").exists())
            self.assertEqual(
                env["VKD3D_SHADER_CACHE_PATH"], "/custom/vkd3d")
            self.assertEqual(env["DXVK_SHADER_CACHE_PATH"], "/custom/dxvk")

    def test_inherited_compat_values_cannot_disable_automatic_defaults(self):
        env = {
            "PROTON_ENABLE_WAYLAND": "1",
            "WINE_DISABLE_VULKAN_OPWR": "0",
            "PROTON_PREFER_SDL": "0",
            "PROTON_DISABLE_HIDRAW": "0x1234/0x5678",
            "PROTON_NO_WM_DECORATION": "0",
            "PROTON_USE_WINED3D": "0",
        }
        launch._configure_runtime_compat(
            env, {"renderer": "opengl"}, "x11", True,
            host_env=dict(env), steam_deck=True,
        )
        self.assertEqual(env["PROTON_ENABLE_WAYLAND"], "0")
        self.assertEqual(env["WINE_DISABLE_VULKAN_OPWR"], "1")
        self.assertEqual(env["PROTON_PREFER_SDL"], "1")
        self.assertNotIn("PROTON_DISABLE_HIDRAW", env)
        self.assertEqual(env["PROTON_NO_WM_DECORATION"], "1")
        self.assertEqual(env["PROTON_USE_WINED3D"], "1")

    def test_steam_input_removes_inherited_sdl_preference(self):
        env = {
            "PROTON_PREFER_SDL": "1",
            "PROTON_NO_STEAMINPUT": "1",
        }
        launch._configure_runtime_compat(
            env, {}, "x11", False,
            host_env={
                "SteamGameId": "1234567890",
                "SteamVirtualGamepadInfo_Proton":
                    "/run/user/1000/steam-input",
                "PROTON_PREFER_SDL": "1",
                "PROTON_NO_STEAMINPUT": "1",
            },
            steam_deck=False,
        )
        self.assertNotIn("PROTON_PREFER_SDL", env)
        self.assertNotIn("PROTON_NO_STEAMINPUT", env)

    def test_each_launch_discards_only_previous_proton_session_logs(self):
        with tempfile.TemporaryDirectory() as td:
            logs = Path(td)
            stale = (
                logs / "proton.log",
                logs / "steam-0.log",
                logs / "steam-umu-default.log",
            )
            for path in stale:
                path.write_text("stale crash")
            minecraft = logs / "minecraft.log"
            minecraft.write_text("keep until launch truncates it")
            unrelated = logs / "native-login.log"
            unrelated.write_text("keep")

            with mock.patch.object(launch, "LOGS", logs):
                launch._clear_previous_proton_logs()

            self.assertTrue(all(not path.exists() for path in stale))
            self.assertTrue(minecraft.exists())
            self.assertTrue(unrelated.exists())

    def test_custom_environment_still_has_final_precedence(self):
        with tempfile.TemporaryDirectory() as td:
            env = {}
            launch._configure_runtime_compat(
                env, {"renderer": "opengl"}, "x11", True, host_env={},
                steam_deck=True,
            )
            with mock.patch.object(launch, "DATA", Path(td)):
                launch._configure_graphics_cache(env, managed_engine=True)
            launch.apply_custom_env(
                env,
                "PROTON_ENABLE_WAYLAND=1 WINE_DISABLE_VULKAN_OPWR=0 "
                "PROTON_PREFER_SDL=0 PROTON_DISABLE_HIDRAW=0 "
                "PROTON_NO_WM_DECORATION=0 PROTON_USE_WINED3D=0 "
                "VKD3D_SHADER_CACHE_PATH=0 "
                "DXVK_SHADER_CACHE_PATH=/custom/dxvk",
            )
        self.assertEqual(env["PROTON_ENABLE_WAYLAND"], "1")
        self.assertEqual(env["WINE_DISABLE_VULKAN_OPWR"], "0")
        self.assertEqual(env["PROTON_PREFER_SDL"], "0")
        self.assertEqual(env["PROTON_DISABLE_HIDRAW"], "0")
        self.assertEqual(env["PROTON_NO_WM_DECORATION"], "0")
        self.assertEqual(env["PROTON_USE_WINED3D"], "0")
        self.assertEqual(env["VKD3D_SHADER_CACHE_PATH"], "0")
        self.assertEqual(env["DXVK_SHADER_CACHE_PATH"], "/custom/dxvk")

    def test_gpu_safety_failure_allows_engine_update_but_blocks_wine(self):
        with tempfile.TemporaryDirectory() as td:
            game = Path(td)
            (game / "Minecraft.Windows.exe").write_bytes(b"MZ")
            with mock.patch.object(launch, "load_settings",
                                   return_value={"game_dir": str(game)}), \
                    mock.patch.object(launch, "proton_path",
                                      return_value=Path("/tmp/engine")), \
                    mock.patch.object(
                        launch, "retire_idle_current_boot_marker"
                    ), \
                    mock.patch.object(
                        launch, "require_safe_graphics_session",
                        side_effect=launch.BolError("unsafe GPU")), \
                    mock.patch.object(launch, "_prepare_launch_engine") as prep, \
                    mock.patch.object(launch, "boot_prefix") as boot:
                with self.assertRaisesRegex(launch.BolError, "unsafe GPU"):
                    launch._launch_once()
            prep.assert_called_once_with()
            boot.assert_not_called()

    def test_gpu_marker_wraps_the_only_game_process_and_clears_on_return(self):
        calls = []

        class Process:
            @staticmethod
            def wait(timeout):
                calls.append(("wait", timeout))
                return 0

        def arm():
            calls.append(("arm",))
            return "owned-token"

        def popen(*_args, **_kwargs):
            calls.append(("popen",))
            return Process()

        def disarm(token):
            calls.append(("disarm", token))
            return True

        def mark(token):
            calls.append(("mark-returned", token))
            return True

        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(
                self._exercise_ready_launch(
                    Path(td), popen, arm, disarm, mark=mark), 0)
        self.assertEqual(calls, [
            ("arm",),
            ("popen",),
            ("wait", 1),
            ("mark-returned", "owned-token"),
            ("disarm", "owned-token"),
        ])

    def test_game_wrapper_inherits_both_launch_lock_descriptors(self):
        observed = {}

        class Process:
            @staticmethod
            def wait(timeout):
                return 0

        def popen(*_args, **kwargs):
            observed.update(kwargs)
            return Process()

        with tempfile.TemporaryDirectory() as td:
            self._exercise_ready_launch(
                Path(td),
                popen,
                lambda: "owned-token",
                lambda _token: True,
                lock_fds=(71, 72),
            )

        self.assertEqual(observed["pass_fds"], (71, 72))

    def test_managed_launch_passes_persistent_graphics_cache(self):
        observed = {}

        class Process:
            @staticmethod
            def wait(timeout):
                return 0

        def popen(*_args, **kwargs):
            observed.update(kwargs)
            return Process()

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._exercise_ready_launch(
                root,
                popen,
                lambda: "owned-token",
                lambda _token: True,
            )

            cache = root / "data" / "graphics-cache"
            self.assertEqual(
                observed["env"]["VKD3D_SHADER_CACHE_PATH"], str(cache))
            self.assertEqual(
                observed["env"]["DXVK_SHADER_CACHE_PATH"], str(cache))
            self.assertTrue(cache.is_dir())

    def test_xbox_preauth_failure_surfaces_sanitized_action_and_stage(self):
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(
                    launch, "xbl_preauth_error_message",
                    return_value="Verify the account age, then try again."), \
                mock.patch.object(
                    launch, "xbl_preauth_diagnostic",
                    return_value={"stage": "sisu-multiplayer",
                                  "category": "age"}):
            with self.assertRaisesRegex(
                    launch.BolError,
                    "stage: sisu-multiplayer.*Verify the account age"):
                self._exercise_ready_launch(
                    Path(td), mock.Mock(), mock.Mock(), mock.Mock(),
                    preauth=False,
                )

    def test_gpu_marker_is_cleared_when_process_spawn_fails(self):
        calls = []

        def arm():
            calls.append("arm")
            return "owned-token"

        def popen(*_args, **_kwargs):
            calls.append("popen")
            raise OSError("spawn interrupted")

        def disarm(_token):
            calls.append("disarm")
            return True

        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(OSError, "spawn interrupted"):
                self._exercise_ready_launch(Path(td), popen, arm, disarm)
        self.assertEqual(calls, ["arm", "popen", "disarm"])

    def test_gpu_marker_remains_when_wrapper_returns_with_live_children(self):
        calls = []

        class Process:
            @staticmethod
            def wait(timeout):
                calls.append(("wait", timeout))
                return 0

        def arm():
            calls.append(("arm",))
            return "owned-token"

        def popen(*_args, **_kwargs):
            calls.append(("popen",))
            return Process()

        def disarm(token):
            calls.append(("disarm", token))
            return True

        def mark(token):
            calls.append(("mark-returned", token))
            return True

        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(self._exercise_ready_launch(
                Path(td), popen, arm, disarm, prefix_idle=False, mark=mark), 0)
        self.assertEqual(calls, [
            ("arm",),
            ("popen",),
            ("wait", 1),
            ("mark-returned", "owned-token"),
        ])

    def test_wrapper_return_requires_three_idle_rescans_without_killing(self):
        scans = [[91], [91], [], [], []]
        with mock.patch.object(launch, "active_prefix",
                               return_value=Path("/tmp/prefix")), \
                mock.patch.object(launch, "prefix_processes",
                                  side_effect=scans) as processes, \
                mock.patch.object(launch.time, "monotonic",
                                  side_effect=[0, 0.1, 0.2, 0.3, 0.4]), \
                mock.patch.object(launch.time, "sleep"), \
                mock.patch.object(launch.os, "kill") as kill:
            self.assertTrue(launch._prefix_stably_idle_after_wrapper())
        self.assertEqual(processes.call_count, 5)
        kill.assert_not_called()


if __name__ == "__main__":
    unittest.main()
