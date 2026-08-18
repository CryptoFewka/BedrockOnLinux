"""Policy checks for the application artifact build scripts."""
# SPDX-License-Identifier: MIT

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from bol import deps, gui


ROOT = Path(__file__).resolve().parents[1]


class ApplicationPackagingPolicyTests(unittest.TestCase):
    def test_appimage_is_relocatable_licensed_and_version_pinned(self):
        script = (ROOT / "scripts/build-appimage.sh").read_text(
            encoding="utf-8")
        self.assertIn("BOL_APPIMAGE_BUILD_CACHE", script)
        self.assertNotIn('CACHE="$OUT/.cache"', script)
        self.assertIn("--set-rpath '$ORIGIN'        \"$PYLIB/libtcl8.6.so\"",
                      script)
        self.assertIn("usr/share/licenses/bedrock-on-linux/LICENSE", script)
        self.assertIn("cat > \"$APPDIR/AppRun\" <<'EOF'\n#!/bin/sh\n", script)
        self.assertIn('/bin/sh -n "$APPDIR/AppRun"', script)
        self.assertIn('rm -f "$DYN"/_crypt.*.so', script)
        self.assertIn('"libcrypt.so.1", "libXss.so.1"', script)
        self.assertIn('runtime is not statically linked', script)
        # Wheels are hash-pinned, binary-only, no sdist builds; the pinned
        # closure lives in the requirements file the script installs from.
        self.assertIn("--require-hashes --only-binary=:all:", script)
        self.assertIn("third_party/requirements-appimage.txt", script)
        reqs = (ROOT / "third_party/requirements-appimage.txt").read_text(
            encoding="utf-8")
        for requirement in (
                "cryptography==43.0.3", "cffi==2.0.0", "pycparser==3.0",
                "customtkinter==5.2.2", "darkdetect==0.8.0",
                "packaging==26.2", "python-xlib==0.33"):
            self.assertIn(requirement, reqs)
        self.assertIn("--hash=sha256:", reqs)

    def test_deb_preserves_dependency_licenses_and_normalizes_modes(self):
        script = (ROOT / "scripts/build-deb.sh").read_text(encoding="utf-8")
        self.assertIn("--require-hashes --only-binary=:all:", script)
        self.assertIn("third_party/requirements-deb.txt", script)
        reqs = (ROOT / "third_party/requirements-deb.txt").read_text(
            encoding="utf-8")
        for requirement in (
                "customtkinter==5.2.2", "darkdetect==0.8.0",
                "packaging==26.2", "python-xlib==0.33"):
            self.assertIn(requirement, reqs)
        self.assertIn("--hash=sha256:", reqs)
        self.assertNotIn('*.dist-info', script)
        self.assertIn("usr/share/doc/bedrock-on-linux/copyright", script)
        self.assertIn("-iname 'LICENSE*'", script)
        self.assertIn("-name '.DS_Store' -delete", script)
        self.assertIn("-type d -exec chmod 0755", script)
        self.assertIn("-type f -exec chmod 0644", script)
        self.assertIn('SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-1782250551}"',
                      script)
        self.assertIn('touch -h -d "@$SOURCE_DATE_EPOCH"', script)

    def test_rpm_bundles_the_same_audited_payload_as_the_deb(self):
        # Fedora-based distributions (Nobara, Bazzite) asked for an .rpm; it
        # must carry the identical hash-pinned GUI stack the .deb does rather
        # than a second, quietly diverging closure.
        script = (ROOT / "scripts/build-rpm.sh").read_text(encoding="utf-8")
        self.assertIn("--require-hashes --only-binary=:all:", script)
        self.assertIn("third_party/requirements-deb.txt", script)
        self.assertIn("-iname 'LICENSE*'", script)
        self.assertIn("-name '.DS_Store' -delete", script)
        self.assertIn("-type d -exec chmod 0755", script)
        self.assertIn("-type f -exec chmod 0644", script)
        self.assertIn('SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-1782250551}"',
                      script)
        self.assertIn('touch -h -d "@$SOURCE_DATE_EPOCH"', script)
        self.assertIn("usr/share/licenses/bedrock-on-linux/LICENSE", script)
        # Generated requires would be satisfied by nothing on the target
        # distributions, so the spec declares every dependency by hand.
        self.assertIn("AutoReqProv:    no", script)
        for requirement in ("python3-tkinter", "python3-cryptography",
                            "/usr/bin/xrandr", "(curl or wget)"):
            self.assertIn(requirement, script)
        deb = (ROOT / "scripts/build-deb.sh").read_text(encoding="utf-8")
        for metadata in ("customtkinter-5.2.2.dist-info",
                         "darkdetect-0.8.0.dist-info",
                         "packaging-26.2.dist-info",
                         "python_xlib-0.33.dist-info",
                         "six-1.17.0.dist-info"):
            self.assertIn(metadata, deb)
            self.assertIn(metadata, script)

    def test_release_pipeline_builds_and_ships_the_rpm(self):
        build = (ROOT / "scripts/build-release.sh").read_text(encoding="utf-8")
        self.assertIn('bash "$SRC/scripts/build-rpm.sh"', build)
        self.assertIn('required_failures+=(".rpm")', build)
        workflow = (ROOT / ".github/workflows/build-app.yml").read_text(
            encoding="utf-8")
        self.assertIn("dpkg-dev rpm ", workflow)
        # Uploaded to the publish job, attested, and attached to the release.
        self.assertEqual(workflow.count("dist/bedrock-on-linux-*.rpm"), 3)

    def test_zipapp_embeds_license_and_bootstrap_pins_gui_stack(self):
        script = (ROOT / "scripts/build-release.sh").read_text(
            encoding="utf-8")
        self.assertIn('install -m644 "$SRC/LICENSE" "$STAGE/LICENSE"', script)
        self.assertIn(
            'install -Dm644 "$SRC/data/icon.png" "$STAGE/data/icon.png"',
            script,
        )
        self.assertIn('export SOURCE_DATE_EPOCH', script)
        self.assertIn('zipfile.ZipInfo(relative, date_time=date_time)', script)
        self.assertIn('sorted(stage.rglob("*")', script)
        self.assertEqual(
            deps.GUI_INSTALL_REQUIREMENTS,
            (
                "customtkinter==5.2.2",
                "darkdetect==0.8.0",
                "packaging==26.2",
                "python-xlib==0.33",
                "six==1.17.0",
            ),
        )

    def test_flatpak_installs_project_license(self):
        manifest = (ROOT / "flatpak/io.github.wyze3306.BedrockOnLinux.yml").read_text(
            encoding="utf-8")
        self.assertIn(
            "install -Dm644 LICENSE /app/share/licenses/bedrock-on-linux/LICENSE",
            manifest,
        )

    def test_flatpak_keeps_game_controller_device_access(self):
        manifest = (
            ROOT / "flatpak/io.github.wyze3306.BedrockOnLinux.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("- --device=all\n", manifest)

    def test_release_workflow_uses_updater_compatible_version_tags(self):
        workflow = (ROOT / ".github/workflows/build-app.yml").read_text(
            encoding="utf-8")
        self.assertIn('- "v[0-9]*"', workflow)
        self.assertIn('tag="v${ver}"', workflow)
        self.assertNotIn("app-v", workflow)
        self.assertIn("target_commitish: ${{ github.sha }}", workflow)
        self.assertIn(
            '[ "$GITHUB_REF_NAME" != "v${ver}" ]',
            workflow,
        )
        self.assertIn(
            "make_latest: ${{ env.RELEASE_PRERELEASE == 'false' }}",
            workflow,
        )
        for name in (
                "build-engine.yml", "build-winegdk.yml",
                "build-xcurl.yml", "build-vkd3d.yml"):
            component = (ROOT / ".github/workflows" / name).read_text(
                encoding="utf-8")
            self.assertIn("target_commitish: ${{ github.sha }}", component)
            self.assertIn("make_latest: false", component)

    def test_desktop_entries_launch_gui_and_match_window_class(self):
        for relative in (
                "data/bedrock-on-linux.desktop",
                "flatpak/io.github.wyze3306.BedrockOnLinux.desktop"):
            entry = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("Type=Application\n", entry)
            self.assertIn("Exec=bedrock-on-linux gui\n", entry)
            self.assertIn("Terminal=false\n", entry)
            self.assertIn("StartupWMClass=BedrockOnLinux\n", entry)

    def test_desktop_entries_offer_a_launcher_free_play_action(self):
        for relative in (
                "data/bedrock-on-linux.desktop",
                "flatpak/io.github.wyze3306.BedrockOnLinux.desktop"):
            entry = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("Actions=Play;\n", entry)
            self.assertIn("[Desktop Action Play]\n", entry)
            self.assertIn("Exec=bedrock-on-linux play\n", entry)
            # The action group must follow the main group it belongs to.
            self.assertLess(entry.index("[Desktop Entry]"),
                            entry.index("[Desktop Action Play]"))

    def test_packagers_rewrite_the_launcher_exec_without_the_play_action(self):
        # Both scripts rewrote every Exec= line, which would have redirected
        # the Play action back into the launcher window.
        installer = (ROOT / "scripts/install.sh").read_text(encoding="utf-8")
        self.assertIn(
            'sed "s|^Exec=bedrock-on-linux |Exec=$BIN/bedrock-on-linux |"',
            installer)
        appimage = (ROOT / "scripts/build-appimage.sh").read_text(
            encoding="utf-8")
        self.assertIn(
            "sed '0,/^Exec=/s|^Exec=.*|Exec=bedrock-on-linux gui|'", appimage)


class GuiStartupPolicyTests(unittest.TestCase):
    def test_pure_wayland_double_click_reports_xwayland_requirement(self):
        with mock.patch.dict(
                os.environ, {"WAYLAND_DISPLAY": "wayland-0"}, clear=True), \
                mock.patch.object(
                    gui, "_owned_x11_socket_displays", return_value=()), \
                mock.patch.object(gui, "_desktop_error") as error:
            gui.gui()
        error.assert_called_once()
        self.assertIn("XWayland", error.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
