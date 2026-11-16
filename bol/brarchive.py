"""Bedrock binary resource archive (.brarchive) support."""
# SPDX-License-Identifier: MIT

import os
import posixpath
import stat
import struct
import tempfile
from pathlib import Path, PurePosixPath
from typing import Dict, Iterator, List, Optional, Tuple, Union

MAGIC = b"\x7d\x27\x25\xb1\xa0\x52\x70\x26"
HEADER_SIZE = 16
ENTRY_SIZE = 256
MAX_NAME_LEN = 247
CURRENT_VERSION = 1


class BrArchiveError(ValueError):
    """Raised when parsing or manipulating a .brarchive fails."""


class BrArchive:
    """Reader, writer, and editor for Minecraft Bedrock .brarchive archives.

    Minecraft Bedrock compiles UI, animation, and logic assets into uncompressed,
    header-indexed binary archives (.brarchive) located in resource packs
    (e.g., ``data/resource_packs/vanilla/__brarchive/ui.brarchive``).
    """

    def __init__(self, entries: Optional[List[Tuple[str, bytes]]] = None, version: int = CURRENT_VERSION):
        self._entries: Dict[str, bytes] = {}
        self._order: List[str] = []
        self.version = version
        if entries:
            for name, data in entries:
                self.set(name, data)

    @classmethod
    def from_bytes(cls, data: bytes) -> "BrArchive":
        """Parse a .brarchive from raw bytes."""
        if len(data) < HEADER_SIZE:
            raise BrArchiveError("File too short to be a valid .brarchive")

        magic, count, version = struct.unpack("<8sII", data[:HEADER_SIZE])
        if magic != MAGIC:
            raise BrArchiveError(f"Invalid .brarchive magic: {magic!r}")

        table_size = count * ENTRY_SIZE
        data_start = HEADER_SIZE + table_size
        if len(data) < data_start:
            raise BrArchiveError(
                f"File truncated: expected at least {data_start} bytes for table, got {len(data)}"
            )

        entries: List[Tuple[str, bytes]] = []
        for i in range(count):
            entry_offset = HEADER_SIZE + i * ENTRY_SIZE
            entry_bytes = data[entry_offset : entry_offset + ENTRY_SIZE]

            name_len = entry_bytes[0]
            if name_len > MAX_NAME_LEN:
                raise BrArchiveError(f"Invalid entry name length {name_len} at index {i}")

            try:
                name = entry_bytes[1 : 1 + name_len].decode("utf-8")
            except UnicodeDecodeError as e:
                raise BrArchiveError(f"Invalid UTF-8 filename in entry {i}: {e}") from e

            file_offset, file_len = struct.unpack("<II", entry_bytes[248:256])
            abs_start = data_start + file_offset
            abs_end = abs_start + file_len

            if abs_end > len(data):
                raise BrArchiveError(
                    f"Entry {name!r} extends beyond file bounds (offset {abs_start}..{abs_end}, total {len(data)})"
                )

            entries.append((name, data[abs_start:abs_end]))

        archive = cls(entries=entries, version=version)
        return archive

    @classmethod
    def from_file(cls, path: Union[str, Path]) -> "BrArchive":
        """Read and parse a .brarchive from disk."""
        return cls.from_bytes(Path(path).read_bytes())

    def to_bytes(self) -> bytes:
        """Serialize the archive to binary format."""
        count = len(self._order)
        header = struct.pack("<8sII", MAGIC, count, self.version)

        table = bytearray()
        data_section = bytearray()
        current_offset = 0

        for name in self._order:
            file_bytes = self._entries[name]
            name_bytes = name.encode("utf-8")
            if len(name_bytes) > MAX_NAME_LEN:
                raise BrArchiveError(
                    f"Filename {name!r} ({len(name_bytes)} bytes) exceeds maximum length of {MAX_NAME_LEN}"
                )

            entry = bytearray(ENTRY_SIZE)
            entry[0] = len(name_bytes)
            entry[1 : 1 + len(name_bytes)] = name_bytes
            entry[248:252] = struct.pack("<I", current_offset)
            entry[252:256] = struct.pack("<I", len(file_bytes))

            table.extend(entry)
            data_section.extend(file_bytes)
            current_offset += len(file_bytes)

        return header + bytes(table) + bytes(data_section)

    def write(self, path: Union[str, Path]) -> None:
        """Atomically write the archive to disk.

        The archive rewritten in practice is the game's own user interface,
        and a game without it dies on its loading screen (#266). So the new
        bytes reach the disk before the rename puts them in place -- a power
        cut must leave the old archive or the new one, never a torn file --
        and the replacement keeps the permission bits of the file it replaces
        rather than mkstemp's owner-only 0600.
        """
        target = Path(path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        content = self.to_bytes()
        try:
            mode = stat.S_IMODE(target.stat().st_mode)
        except FileNotFoundError:
            mode = 0o644

        # Write to a sibling temp file in the same directory, then atomic rename
        fd, tmp_path = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp_path, mode)
            os.replace(tmp_path, target)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def names(self) -> List[str]:
        """Return the list of file names in the archive."""
        return list(self._order)

    def read_bytes(self, name: str) -> bytes:
        """Read the raw bytes of an entry."""
        if name not in self._entries:
            raise KeyError(f"Entry {name!r} not found in archive")
        return self._entries[name]

    def read_text(self, name: str, encoding: str = "utf-8", errors: str = "strict") -> str:
        """Read and decode the content of an entry as text."""
        return self.read_bytes(name).decode(encoding=encoding, errors=errors)

    def set(self, name: str, data: Union[str, bytes], encoding: str = "utf-8") -> None:
        """Add or update an entry in the archive."""
        if isinstance(data, str):
            data = data.encode(encoding)
        elif not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError(f"Data must be bytes or str, got {type(data).__name__}")

        if name not in self._entries:
            self._order.append(name)
        self._entries[name] = bytes(data)

    def delete(self, name: str) -> None:
        """Remove an entry from the archive."""
        if name not in self._entries:
            raise KeyError(f"Entry {name!r} not found in archive")
        del self._entries[name]
        self._order.remove(name)

    def extract(self, destination: Union[str, Path]) -> None:
        """Extract all files into destination directory, rejecting unsafe paths."""
        dest = Path(destination).resolve()
        dest.mkdir(parents=True, exist_ok=True)

        for name in self._order:
            raw_path = PurePosixPath(name)
            if not name or raw_path.is_absolute() or ".." in raw_path.parts:
                raise BrArchiveError(f"Unsafe path in archive entry: {name!r}")

            norm = posixpath.normpath(name)
            if norm in ("", ".", "..") or norm.startswith("../"):
                raise BrArchiveError(f"Unsafe path in archive entry: {name!r}")

            target = (dest / norm).resolve()
            if not str(target).startswith(str(dest)):
                raise BrArchiveError(f"Entry {name!r} extracts outside target destination")

            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(self._entries[name])

    def __contains__(self, name: str) -> bool:
        return name in self._entries

    def __getitem__(self, name: str) -> bytes:
        return self.read_bytes(name)

    def __setitem__(self, name: str, data: Union[str, bytes]) -> None:
        self.set(name, data)

    def __delitem__(self, name: str) -> None:
        self.delete(name)

    def __len__(self) -> int:
        return len(self._order)

    def __iter__(self) -> Iterator[str]:
        return iter(self._order)

