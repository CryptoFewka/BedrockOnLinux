"""Tests for hide_signin_button fixup."""
# SPDX-License-Identifier: MIT

import stat
import tempfile
import unittest
from pathlib import Path

from bol.brarchive import BrArchive
from bol.fixups import hide_signin_button


class HideSigninButtonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.game_dir = Path(self.tmp.name)
        self.vanilla = self.game_dir / "data" / "resource_packs" / "vanilla"
        self.brarchive_dir = self.vanilla / "__brarchive"
        self.brarchive_dir.mkdir(parents=True, exist_ok=True)
        self.archive = self.brarchive_dir / "ui.brarchive"
        self.backup = self.brarchive_dir / "ui.brarchive.bol-bak"
        self.orig_backup = self.brarchive_dir / "ui.brarchive.bol-orig"
        self.ui_dir = self.vanilla / "ui"

    def tearDown(self):
        try:
            # Restore write perms on brarchive_dir if any test revoked them
            self.brarchive_dir.chmod(0o755)
        except Exception:
            pass
        self.tmp.cleanup()

    def test_preserves_archive_when_start_screen_missing(self):
        """When start_screen.json is not in archive or loose, ui.brarchive must be untouched."""
        self.archive.write_bytes(b"compiled-ui-data")

        hide_signin_button(self.game_dir)

        self.assertTrue(self.archive.is_file())
        self.assertFalse(self.backup.exists())
        self.assertEqual(self.archive.read_bytes(), b"compiled-ui-data")

    def test_patches_brarchive_when_loose_start_screen_absent(self):
        """When loose files are absent (1.26.50+), ui.brarchive is patched directly."""
        sample_json = (
            '{"controls":[{"xbl_signin_button@start.xbl_signin_button":{}}],'
            '"bindings":[{"binding_name":"#sign_in_visible"}]}'
        )
        archive = BrArchive()
        archive["start_screen.json"] = sample_json
        archive["other_file.json"] = '{"other": 123}'
        archive.write(self.archive)

        hide_signin_button(self.game_dir)

        self.assertTrue(self.archive.is_file())
        self.assertFalse(self.backup.exists())
        self.assertTrue(self.orig_backup.is_file())

        # Verify original backup has original content
        orig_parsed = BrArchive.from_file(self.orig_backup)
        self.assertIn('"#sign_in_visible"', orig_parsed.read_text("start_screen.json"))

        # Verify target archive has modified content
        patched = BrArchive.from_file(self.archive)
        content = patched.read_text("start_screen.json")
        self.assertIn('"#edu_demo_only_ui_visible"', content)
        self.assertNotIn('"#sign_in_visible"', content)
        self.assertEqual(patched.read_text("other_file.json"), '{"other": 123}')

    def test_brarchive_patching_keeps_bytes_that_are_not_utf8(self):
        """The start screen is rewritten as bytes: nothing else in it may change."""
        original = (b'{"note":"\xff\xfe",'
                    b'"bindings":[{"binding_name":"#sign_in_visible"}]}')
        archive = BrArchive()
        archive["start_screen.json"] = original
        archive.write(self.archive)

        hide_signin_button(self.game_dir)

        patched = BrArchive.from_file(self.archive)["start_screen.json"]
        self.assertEqual(
            patched,
            original.replace(b'"#sign_in_visible"',
                             b'"#edu_demo_only_ui_visible"'))

    def test_brarchive_patching_keeps_the_archive_permissions(self):
        archive = BrArchive()
        archive["start_screen.json"] = '{"binding_name":"#sign_in_visible"}'
        archive.write(self.archive)
        self.archive.chmod(0o664)

        hide_signin_button(self.game_dir)

        self.assertEqual(stat.S_IMODE(self.archive.stat().st_mode), 0o664)

    def test_restores_the_pristine_archive_when_ours_no_longer_parses(self):
        """A torn rewrite must not leave the game without its user interface."""
        archive = BrArchive()
        archive["start_screen.json"] = '{"binding_name":"#sign_in_visible"}'
        archive.write(self.orig_backup)
        pristine = self.orig_backup.read_bytes()
        self.archive.write_bytes(pristine[:20])

        hide_signin_button(self.game_dir)
        self.assertEqual(self.archive.read_bytes(), pristine)

        # The next PLAY hides the button in the restored archive again.
        hide_signin_button(self.game_dir)
        self.assertIn(
            b'"#edu_demo_only_ui_visible"',
            BrArchive.from_file(self.archive)["start_screen.json"])
        self.assertEqual(self.orig_backup.read_bytes(), pristine)

    def test_leaves_an_unreadable_archive_it_never_rewrote_alone(self):
        """No pristine copy means the archive is not ours to replace."""
        self.archive.write_bytes(b"a-future-archive-format")

        hide_signin_button(self.game_dir)

        self.assertEqual(self.archive.read_bytes(), b"a-future-archive-format")
        self.assertFalse(self.orig_backup.exists())

    def test_brarchive_patching_is_idempotent(self):
        """Running hide_signin_button multiple times does not corrupt or re-backup archive."""
        sample_json = '{"bindings":[{"binding_name":"#sign_in_visible"}]}'
        archive = BrArchive()
        archive["start_screen.json"] = sample_json
        archive.write(self.archive)

        hide_signin_button(self.game_dir)
        first_modified_bytes = self.archive.read_bytes()
        first_orig_bytes = self.orig_backup.read_bytes()

        # Run second time
        hide_signin_button(self.game_dir)
        self.assertEqual(self.archive.read_bytes(), first_modified_bytes)
        self.assertEqual(self.orig_backup.read_bytes(), first_orig_bytes)

    def test_self_heals_missing_archive_from_backup(self):
        """If previous runs left ui.brarchive.bol-bak behind, restore it automatically."""
        self.backup.write_bytes(b"restored-ui-data")

        hide_signin_button(self.game_dir)

        self.assertTrue(self.archive.is_file())
        self.assertFalse(self.backup.exists())
        self.assertEqual(self.archive.read_bytes(), b"restored-ui-data")

    def test_cleans_stale_backup_if_archive_already_present(self):
        """If both ui.brarchive and the backup exist, clean up the backup."""
        self.archive.write_bytes(b"active-ui")
        self.backup.write_bytes(b"old-backup")

        hide_signin_button(self.game_dir)

        self.assertTrue(self.archive.is_file())
        self.assertFalse(self.backup.exists())
        self.assertEqual(self.archive.read_bytes(), b"active-ui")

    def test_self_heals_when_directory_was_read_only(self):
        """Self-healing succeeds even if the user ran chmod a-w on the directory."""
        self.backup.write_bytes(b"restored-ui-data")
        # Make directory read-only
        self.brarchive_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)

        hide_signin_button(self.game_dir)

        self.assertTrue(self.archive.is_file())
        self.assertFalse(self.backup.exists())
        self.assertEqual(self.archive.read_bytes(), b"restored-ui-data")

    def test_applies_fixup_when_start_screen_exists(self):
        """When loose start_screen.json exists (<1.26.50), the button binding is hidden."""
        self.archive.write_bytes(b"compiled-ui-data")
        self.ui_dir.mkdir(parents=True, exist_ok=True)
        start_screen = self.ui_dir / "start_screen.json"
        sample_json = (
            '{\n'
            '  "controls": [\n'
            '    { "xbl_signin_button@start.xbl_signin_button": {} }\n'
            '  ],\n'
            '  "bindings": [\n'
            '    {\n'
            '      "binding_name": "#sign_in_visible"\n'
            '    }\n'
            '  ]\n'
            '}'
        )
        start_screen.write_text(sample_json, encoding="utf-8")

        hide_signin_button(self.game_dir)

        self.assertFalse(self.archive.exists())
        self.assertTrue(self.backup.is_file())
        content = start_screen.read_text(encoding="utf-8")
        self.assertIn("#edu_demo_only_ui_visible", content)
        self.assertNotIn("#sign_in_visible", content)

    def test_never_raises_on_errors(self):
        """hide_signin_button is best-effort and must never raise exceptions."""
        try:
            hide_signin_button("/nonexistent/directory/that/does/not/exist")
            hide_signin_button(None)
        except Exception as e:
            self.fail(f"hide_signin_button raised unexpectedly: {e}")


if __name__ == "__main__":
    unittest.main()
