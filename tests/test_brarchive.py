"""Tests for BrArchive reader, writer, and editor."""
# SPDX-License-Identifier: MIT

import stat
import struct
import tempfile
import unittest
from pathlib import Path

from bol.brarchive import (
    BrArchive,
    BrArchiveError,
    ENTRY_SIZE,
    HEADER_SIZE,
    MAGIC,
)


class BrArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_roundtrip_empty(self):
        archive = BrArchive()
        raw = archive.to_bytes()
        self.assertEqual(len(raw), HEADER_SIZE)

        parsed = BrArchive.from_bytes(raw)
        self.assertEqual(len(parsed), 0)
        self.assertEqual(parsed.names(), [])

    def test_roundtrip_single_file(self):
        archive = BrArchive()
        archive["test.json"] = '{"hello": "world"}'

        raw = archive.to_bytes()
        self.assertEqual(len(raw), HEADER_SIZE + ENTRY_SIZE + len('{"hello": "world"}'))

        parsed = BrArchive.from_bytes(raw)
        self.assertEqual(len(parsed), 1)
        self.assertIn("test.json", parsed)
        self.assertEqual(parsed.read_text("test.json"), '{"hello": "world"}')

    def test_roundtrip_multiple_files_and_order(self):
        archive = BrArchive()
        archive["a.txt"] = b"content A"
        archive["b.json"] = b'{"b": 2}'
        archive["sub/c.png"] = b"\x89PNG\r\n\x1a\nfakeimage"

        raw = archive.to_bytes()
        parsed = BrArchive.from_bytes(raw)

        self.assertEqual(parsed.names(), ["a.txt", "b.json", "sub/c.png"])
        self.assertEqual(parsed["a.txt"], b"content A")
        self.assertEqual(parsed["b.json"], b'{"b": 2}')
        self.assertEqual(parsed["sub/c.png"], b"\x89PNG\r\n\x1a\nfakeimage")

    def test_modify_entry_content_resizes_offsets(self):
        archive = BrArchive()
        archive["first.txt"] = b"short"
        archive["second.txt"] = b"middle"
        archive["third.txt"] = b"last"

        # Expand first entry
        archive["first.txt"] = b"a much longer replacement string that shifts subsequent offsets"

        raw = archive.to_bytes()
        parsed = BrArchive.from_bytes(raw)

        self.assertEqual(
            parsed["first.txt"],
            b"a much longer replacement string that shifts subsequent offsets",
        )
        self.assertEqual(parsed["second.txt"], b"middle")
        self.assertEqual(parsed["third.txt"], b"last")

    def test_delete_entry(self):
        archive = BrArchive()
        archive["file1.txt"] = b"1"
        archive["file2.txt"] = b"2"
        archive["file3.txt"] = b"3"

        del archive["file2.txt"]
        self.assertEqual(archive.names(), ["file1.txt", "file3.txt"])

        parsed = BrArchive.from_bytes(archive.to_bytes())
        self.assertEqual(parsed.names(), ["file1.txt", "file3.txt"])
        self.assertEqual(parsed["file1.txt"], b"1")
        self.assertEqual(parsed["file3.txt"], b"3")

    def test_read_write_file_atomic(self):
        archive = BrArchive()
        archive["hello.txt"] = "world"

        target_file = self.tmp_path / "test.brarchive"
        archive.write(target_file)

        self.assertTrue(target_file.is_file())
        reloaded = BrArchive.from_file(target_file)
        self.assertEqual(reloaded.read_text("hello.txt"), "world")

    def test_write_keeps_the_permission_bits_of_the_file_it_replaces(self):
        target_file = self.tmp_path / "ui.brarchive"
        target_file.write_bytes(BrArchive().to_bytes())
        target_file.chmod(0o664)

        archive = BrArchive()
        archive["start_screen.json"] = "{}"
        archive.write(target_file)

        self.assertEqual(stat.S_IMODE(target_file.stat().st_mode), 0o664)
        self.assertEqual(BrArchive.from_file(target_file).names(),
                         ["start_screen.json"])

    def test_write_of_a_new_file_is_not_owner_only(self):
        target_file = self.tmp_path / "new.brarchive"
        BrArchive().write(target_file)
        self.assertEqual(stat.S_IMODE(target_file.stat().st_mode), 0o644)

    def test_write_leaves_no_temporary_file_behind(self):
        archive = BrArchive()
        archive["a.json"] = "{}"
        archive.write(self.tmp_path / "ui.brarchive")
        self.assertEqual(sorted(p.name for p in self.tmp_path.iterdir()),
                         ["ui.brarchive"])

    def test_extract_files(self):
        archive = BrArchive()
        archive["folder/file.json"] = '{"valid": true}'
        archive["root.txt"] = "hello"

        dest = self.tmp_path / "extracted"
        archive.extract(dest)

        self.assertEqual((dest / "root.txt").read_text(), "hello")
        self.assertEqual((dest / "folder" / "file.json").read_text(), '{"valid": true}')

    def test_extract_rejects_unsafe_paths(self):
        archive = BrArchive()
        archive["../escape.txt"] = "dangerous"
        dest = self.tmp_path / "extracted_unsafe"

        with self.assertRaises(BrArchiveError):
            archive.extract(dest)

    def test_invalid_magic(self):
        corrupt = b"BADMAGIC" + b"\x00" * 8
        with self.assertRaises(BrArchiveError):
            BrArchive.from_bytes(corrupt)

    def test_truncated_header(self):
        with self.assertRaises(BrArchiveError):
            BrArchive.from_bytes(b"short")

    def test_truncated_table(self):
        # Header says 2 entries, but only provides 1 entry table
        header = struct.pack("<8sII", MAGIC, 2, 1)
        fake_entry = b"\x00" * ENTRY_SIZE
        with self.assertRaises(BrArchiveError):
            BrArchive.from_bytes(header + fake_entry)

    def test_out_of_bounds_offset(self):
        header = struct.pack("<8sII", MAGIC, 1, 1)
        entry = bytearray(ENTRY_SIZE)
        entry[0] = 4
        entry[1:5] = b"test"
        # offset 9999, len 10
        entry[248:252] = struct.pack("<I", 9999)
        entry[252:256] = struct.pack("<I", 10)
        with self.assertRaises(BrArchiveError):
            BrArchive.from_bytes(header + bytes(entry))


if __name__ == "__main__":
    unittest.main()

