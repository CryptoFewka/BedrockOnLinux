"""Tests for bol.util's screen-geometry helper."""
# SPDX-License-Identifier: MIT

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bol import util


class HttpJsonNoCredentialTests(unittest.TestCase):
    """http_json fetches public endpoints (GitHub releases, Minecraft feedback)
    from more than one host, so it must never attach a credential, even when
    GITHUB_TOKEN is present in the environment."""

    def _captured_headers(self, url):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured.update({k.lower(): v for k, v in req.header_items()})
            return io.BytesIO(json.dumps({"ok": True}).encode())

        with mock.patch.dict("os.environ", {"GITHUB_TOKEN": "SENTINEL_SECRET"}), \
                mock.patch("urllib.request.urlopen", fake_urlopen):
            util.http_json(url)
        return captured

    def test_no_authorization_for_github(self):
        headers = self._captured_headers("https://api.github.com/repos/x/y/releases")
        self.assertNotIn("authorization", headers)

    def test_no_authorization_for_non_github(self):
        headers = self._captured_headers(
            "https://feedback.minecraft.net/api/v2/help_center/en-us/sections/1/articles.json")
        self.assertNotIn("authorization", headers)


class ScreenWHTests(unittest.TestCase):
    def test_delegates_to_x11_primary_output_size(self):
        with mock.patch("bol.x11.primary_output_size",
                         return_value=("1920", "1080")) as mocked:
            self.assertEqual(util._screen_wh(), ("1920", "1080"))
        mocked.assert_called_once_with(None)

    def test_passes_runner_through(self):
        runner = object()
        with mock.patch("bol.x11.primary_output_size",
                         return_value=None) as mocked:
            self.assertIsNone(util._screen_wh(runner=runner))
        mocked.assert_called_once_with(runner)


class LauncherCommandTests(unittest.TestCase):
    """Every packaging must print an invocation the user can actually run."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.missing_flatpak_info = self.root / "no-flatpak-info"

    def command(self, *arguments, **kwargs):
        kwargs.setdefault("info_path", self.missing_flatpak_info)
        kwargs.setdefault("which", lambda _name: None)
        kwargs.setdefault("argv", [""])
        return util.launcher_command(*arguments, **kwargs)

    def test_flatpak_uses_flatpak_run_with_the_sandbox_identity(self):
        self.assertEqual(
            self.command(
                "doctor", "--acknowledge-gpu-crash",
                environ={"FLATPAK_ID": "io.github.wyze3306.BedrockOnLinux"},
            ),
            "flatpak run io.github.wyze3306.BedrockOnLinux doctor "
            "--acknowledge-gpu-crash",
        )

    def test_flatpak_info_file_alone_selects_the_published_app_id(self):
        info = self.root / "flatpak-info"
        info.write_text("[Application]\n", encoding="utf-8")
        self.assertEqual(
            self.command("doctor", environ={}, info_path=info),
            "flatpak run io.github.wyze3306.BedrockOnLinux doctor",
        )

    def test_appimage_uses_its_persistent_file_not_the_temporary_mount(self):
        appimage = self.root / "BedrockOnLinux-2.1.1-x86_64.AppImage"
        appimage.write_text("ELF", encoding="utf-8")
        self.assertEqual(
            self.command(
                "doctor", "--acknowledge-gpu-crash",
                environ={"APPIMAGE": str(appimage)},
                argv=["/tmp/.mount_Bedroc123/AppRun"],
            ),
            f"{appimage} doctor --acknowledge-gpu-crash",
        )

    def test_installed_package_uses_the_program_name_on_path(self):
        self.assertEqual(
            self.command(
                "doctor", environ={},
                which=lambda name: f"/usr/bin/{name}",
            ),
            "bedrock-on-linux doctor",
        )

    def test_portable_zipapp_uses_its_own_path(self):
        zipapp = self.root / "bedrock-on-linux-2.1.1.pyz"
        zipapp.write_text("PK", encoding="utf-8")
        self.assertEqual(
            self.command("doctor", environ={}, argv=[str(zipapp)]),
            f"{zipapp} doctor",
        )

    def test_source_checkout_falls_back_to_the_program_name(self):
        module = self.root / "__main__.py"
        module.write_text("", encoding="utf-8")
        self.assertEqual(
            self.command("doctor", environ={}, argv=[str(module)]),
            "bedrock-on-linux doctor",
        )

    def test_paths_with_spaces_stay_a_single_argument(self):
        appimage = self.root / "My Games" / "BedrockOnLinux.AppImage"
        appimage.parent.mkdir()
        appimage.write_text("ELF", encoding="utf-8")
        self.assertEqual(
            self.command("doctor", environ={"APPIMAGE": str(appimage)}),
            f"'{appimage}' doctor",
        )


if __name__ == "__main__":
    unittest.main()
