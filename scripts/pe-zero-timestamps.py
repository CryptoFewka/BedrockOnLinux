#!/usr/bin/env python3
"""Zero every PE TimeDateStamp + the optional-header CheckSum so MinGW DLLs are
byte reproducible regardless of the linker's (version-specific) timestamp
handling. Covers the COFF file header, the export directory, and each debug
directory entry."""
import struct
import sys


def u16(b, o):
    return struct.unpack_from("<H", b, o)[0]


def u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def rva_to_off(sections, rva):
    for va, vsz, praw, rawsz in sections:
        if va <= rva < va + max(vsz, rawsz):
            return praw + (rva - va)
    return None


def normalize(path):
    with open(path, "rb") as fh:
        b = bytearray(fh.read())
    e = u32(b, 0x3C)
    if b[e:e + 4] != b"PE\0\0":
        raise SystemExit(f"{path}: not a PE file")
    coff = e + 4
    struct.pack_into("<I", b, coff + 4, 0)          # FileHeader.TimeDateStamp
    num_sec = u16(b, coff + 2)
    opt_size = u16(b, coff + 16)
    opt = coff + 20
    magic = u16(b, opt)
    struct.pack_into("<I", b, opt + 64, 0)          # OptionalHeader.CheckSum
    ddir = opt + (112 if magic == 0x20B else 96)    # data directories
    sec_off = opt + opt_size
    sections = []
    for i in range(num_sec):
        s = sec_off + i * 40
        sections.append((u32(b, s + 12), u32(b, s + 8),
                         u32(b, s + 20), u32(b, s + 16)))
    exp_rva = u32(b, ddir + 0 * 8)                   # export dir TimeDateStamp
    if exp_rva:
        off = rva_to_off(sections, exp_rva)
        if off is not None:
            struct.pack_into("<I", b, off + 4, 0)
    dbg_rva = u32(b, ddir + 6 * 8)                   # debug dir entries
    dbg_sz = u32(b, ddir + 6 * 8 + 4)
    if dbg_rva:
        off = rva_to_off(sections, dbg_rva)
        if off is not None:
            for entry in range(0, dbg_sz, 28):
                struct.pack_into("<I", b, off + entry + 4, 0)
    with open(path, "wb") as fh:
        fh.write(b)


if __name__ == "__main__":
    for p in sys.argv[1:]:
        normalize(p)
        print("normalized", p)
