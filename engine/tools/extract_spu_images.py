#!/usr/bin/env python3
"""
Scan a PPU ELF / EBOOT for embedded SPU ELF images and extract each one.

PS3 games carry SPU programs as ELF blobs embedded in the PPU binary's data
section (often referenced by sys_spu_image_open via spu_image structs).  An
SPU ELF is identified by:

    bytes 0..3 : 7F 45 4C 46     ELF magic
    byte  5    : 02              EI_DATA = big-endian
    bytes 18-19: 00 17           e_machine = EM_SPU (23)

Size of each image = max(end of section table, end of last PT_LOAD), which is
the maximum file offset referenced by the ELF header tables.

Usage:
    python extract_spu_images.py <ppu_elf> [--output DIR]
"""

import argparse
import os
import struct
import sys

ELF_MAGIC = b"\x7FELF"
EM_SPU    = 23


def parse_spu_elf_size(buf: bytes, off: int) -> int | None:
    """Return the byte-size of the SPU ELF starting at `off`, or None if it
    doesn't look like a valid SPU ELF."""
    if off + 0x34 > len(buf):
        return None
    hdr = buf[off:off + 0x34]
    if hdr[:4] != ELF_MAGIC:
        return None

    ei_class = hdr[4]              # 1 = 32-bit, 2 = 64-bit
    ei_data  = hdr[5]              # 1 = LE, 2 = BE
    if ei_data != 2:
        return None
    e_machine = struct.unpack_from(">H", hdr, 0x12)[0]
    if e_machine != EM_SPU:
        return None

    # SPU images are 32-bit.  Defensive: still handle 64-bit if seen.
    if ei_class == 1:
        e_phoff,     = struct.unpack_from(">I", hdr, 0x1C)
        e_shoff,     = struct.unpack_from(">I", hdr, 0x20)
        e_phentsize, = struct.unpack_from(">H", hdr, 0x2A)
        e_phnum,     = struct.unpack_from(">H", hdr, 0x2C)
        e_shentsize, = struct.unpack_from(">H", hdr, 0x2E)
        e_shnum,     = struct.unpack_from(">H", hdr, 0x30)
    else:
        e_phoff,     = struct.unpack_from(">Q", hdr, 0x20)
        e_shoff,     = struct.unpack_from(">Q", hdr, 0x28)
        e_phentsize, = struct.unpack_from(">H", hdr, 0x36)
        e_phnum,     = struct.unpack_from(">H", hdr, 0x38)
        e_shentsize, = struct.unpack_from(">H", hdr, 0x3A)
        e_shnum,     = struct.unpack_from(">H", hdr, 0x3C)

    # Must agree EXACTLY with spu_elf_image_size() in runtime/spu/spu_workload.c:
    # that function decides how many bytes the dispatcher fingerprints when the
    # guest hands an image to cellSpurs, so an image extracted to a different
    # length gets a different FNV-1a-64 and never matches.
    #
    # Two things were off, both making the extract SHORT. It counted only
    # PT_LOAD extents, and it seeded `end` with the program-header table extent
    # instead of the section-header table's. Scott Pilgrim's SPU images have no
    # section headers and a non-PT_LOAD segment past the last PT_LOAD, so every
    # one came out 52 bytes short -- and all ten registered under fingerprints
    # nothing would ever dispatch.
    end = e_shoff + e_shnum * e_shentsize

    ph_off = off + e_phoff
    if ph_off + e_phnum * e_phentsize <= len(buf):
        for i in range(e_phnum):                       # every segment, not just PT_LOAD
            base = ph_off + i * e_phentsize
            if ei_class == 1:
                p_offset = struct.unpack_from(">I", buf, base + 4)[0]
                p_filesz = struct.unpack_from(">I", buf, base + 16)[0]
            else:
                p_offset = struct.unpack_from(">Q", buf, base + 8)[0]
                p_filesz = struct.unpack_from(">Q", buf, base + 32)[0]
            end = max(end, p_offset + p_filesz)

    sh_off = off + e_shoff
    if e_shnum and sh_off + e_shnum * e_shentsize <= len(buf):
        for i in range(e_shnum):                       # non-NOBITS section content
            base = sh_off + i * e_shentsize
            if ei_class == 1:
                sh_type   = struct.unpack_from(">I", buf, base + 4)[0]
                sh_offset = struct.unpack_from(">I", buf, base + 16)[0]
                sh_size   = struct.unpack_from(">I", buf, base + 20)[0]
            else:
                sh_type   = struct.unpack_from(">I", buf, base + 4)[0]
                sh_offset = struct.unpack_from(">Q", buf, base + 24)[0]
                sh_size   = struct.unpack_from(">Q", buf, base + 32)[0]
            if sh_type != 8:   # SHT_NOBITS occupies no file space
                end = max(end, sh_offset + sh_size)

    return end


def find_spu_images(buf: bytes):
    """Yield (offset, size) for each embedded SPU ELF."""
    pos = 0
    while True:
        i = buf.find(ELF_MAGIC, pos)
        if i < 0:
            return
        size = parse_spu_elf_size(buf, i)
        if size and i + size <= len(buf):
            yield i, size
            pos = i + size
        else:
            pos = i + 4


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="PPU ELF / EBOOT.elf to scan")
    ap.add_argument("--output", "-o", default="spu_images",
                    help="Output directory (default: ./spu_images)")
    args = ap.parse_args()

    with open(args.input, "rb") as f:
        buf = f.read()

    os.makedirs(args.output, exist_ok=True)
    count = 0
    total_bytes = 0
    print(f"Scanning {len(buf):,} bytes of {args.input}")
    for idx, (off, size) in enumerate(find_spu_images(buf)):
        out_path = os.path.join(args.output, f"spu_{idx:04d}_at_{off:08X}.elf")
        with open(out_path, "wb") as g:
            g.write(buf[off:off + size])
        print(f"  [{idx:3d}]  offset 0x{off:08X}  size {size:>8,} B  -> {out_path}")
        count += 1
        total_bytes += size

    if count == 0:
        print("No SPU images found.")
        return 1
    print(f"\nExtracted {count} SPU image(s), {total_bytes:,} bytes total.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
