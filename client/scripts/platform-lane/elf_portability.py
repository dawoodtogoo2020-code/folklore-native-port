"""Read-only ELF64 target/page-geometry preflight. Never loads or runs input.

Passing these necessary header checks does not verify libc, symbols, packaging,
runtime behavior, device compatibility or gameplay. No third-party modules.
"""
import argparse
import json
import os
from pathlib import Path
import stat
import struct
import sys


TARGETS = {"linux-x64": (62, 4096), "android-arm64": (183, 16384)}
ELF_HEADER = struct.Struct("<16sHHIQQQIHHHHHH")
PROGRAM_HEADER = struct.Struct("<IIQQQQQQ")
PT_LOAD, PT_INTERP, PT_GNU_RELRO = 1, 3, 0x6474E552
U64_LIMIT = 1 << 64


class ElfError(ValueError):
    """Malformed or deliberately unsupported ELF input."""


def read_at(stream, offset, size):
    stream.seek(offset)
    value = stream.read(size)
    if len(value) != size:
        raise ElfError("truncated ELF metadata")
    return value


def inspect_stream(stream, file_size, target):
    """Inspect a seekable, size-bound stream, reading only ELF metadata."""
    machine_expected, page_size = TARGETS[target]
    if file_size < ELF_HEADER.size:
        raise ElfError("truncated ELF header")
    fields = ELF_HEADER.unpack(read_at(stream, 0, ELF_HEADER.size))
    ident, elf_type, machine, version, _, phoff, _, _, ehsize, phsize, phnum, *_ = fields
    if ident[:4] != b"\x7fELF":
        raise ElfError("not an ELF file")
    if ident[4:7] != b"\x02\x01\x01" or version != 1:
        raise ElfError("requires ELF64 little-endian version 1")
    if ehsize != ELF_HEADER.size or phsize != PROGRAM_HEADER.size:
        raise ElfError("unsupported ELF/program-header size")
    if not 1 <= phnum <= 1024:
        raise ElfError("requires 1..1024 program headers; extended numbering unsupported")
    if phoff < ehsize or phoff + phsize * phnum > file_size:
        raise ElfError("program-header table outside file")

    issues, warnings, loads, relro, interpreter = [], [], [], [], None
    if machine != machine_expected:
        issues.append(f"machine {machine} differs from target machine {machine_expected}")
    if elf_type not in (2, 3):
        issues.append("requires executable/shared ELF, not a relocatable object")
    if target == "android-arm64" and elf_type != 3:
        issues.append("Android check expects ET_DYN (shared library or PIE)")
    for index in range(phnum):
        kind, flags, offset, address, _, filesz, memsz, align = PROGRAM_HEADER.unpack(
            read_at(stream, phoff + index * phsize, phsize))
        if kind == 0:
            continue
        if filesz and (offset > file_size or filesz > file_size - offset):
            raise ElfError(f"segment {index} file range outside file")
        if address + memsz > U64_LIMIT:
            raise ElfError(f"segment {index} virtual range overflows ELF64")
        if kind == PT_LOAD:
            if filesz > memsz:
                raise ElfError(f"LOAD {index} file size exceeds memory size")
            if align not in (0, 1) and (align & (align - 1)):
                raise ElfError(f"LOAD {index} alignment is not a power of two")
            if align > 1 and (address - offset) % align:
                raise ElfError(f"LOAD {index} address/offset incongruent with p_align")
            if loads and address < loads[-1]["virtual_address"]:
                raise ElfError("LOAD entries not ordered by virtual address")
            if (address - offset) % page_size:
                issues.append(f"LOAD {index} address/offset incongruent at {page_size} bytes")
            if target == "android-arm64" and align < page_size:
                issues.append(f"LOAD {index} p_align {align} is below 16384 bytes")
            loads.append({"index": index, "offset": offset, "virtual_address": address,
                          "file_size": filesz, "memory_size": memsz,
                          "alignment": align, "flags": flags})
        elif kind == PT_INTERP:
            if interpreter is not None or not 2 <= filesz <= 4096:
                raise ElfError("duplicate or invalid interpreter segment")
            raw = read_at(stream, offset, filesz)
            if raw[-1:] != b"\0" or b"\0" in raw[:-1]:
                raise ElfError("interpreter must be one NUL-terminated path")
            try:
                interpreter = raw[:-1].decode("ascii")
            except UnicodeError as error:
                raise ElfError("interpreter is not ASCII") from error
        elif kind == PT_GNU_RELRO:
            relro.append({"index": index, "virtual_address": address, "memory_size": memsz,
                          "rounded_start": address // page_size * page_size,
                          "rounded_end": (address + memsz + page_size - 1) // page_size * page_size,
                          "mutable_data_ownership_verified": False})
    if not loads:
        raise ElfError("no LOAD segments")
    if target == "android-arm64" and interpreter not in (None, "/system/bin/linker64"):
        issues.append("interpreter is not Android /system/bin/linker64")
    for region in relro:
        raw_start, raw_end = region["virtual_address"], region["virtual_address"] + region["memory_size"]
        extra = ((region["rounded_start"], raw_start), (raw_end, region["rounded_end"]))
        region["extra_protection_writable_loads"] = [load["index"] for load in loads
            if load["flags"] & 2 and any(start < end and
                start < load["virtual_address"] + load["memory_size"] and
                load["virtual_address"] < end for start, end in extra)]
        if region["rounded_start"] != raw_start or region["rounded_end"] != raw_end:
            warnings.append(f"RELRO {region['index']} protection rounds to host pages; "
                            "mutable-data ownership in the extra range is unverified")
    return {"schema": "folklore-elf-header-preflight-v1", "target": target,
            "file_size": file_size, "machine": machine, "elf_type": elf_type,
            "page_size_checked": page_size, "interpreter": interpreter,
            "load_segments": loads, "relro_segments": relro, "issues": issues, "warnings": warnings,
            "header_checks_passed": not issues, "operating_system_abi_verified": False,
            "relro_runtime_safety_verified": False,
            "runtime_tested": False, "packaging_verified": False, "gameplay_verified": False}


def inspect_file(path, target):
    path = Path(path)
    if not stat.S_ISREG(path.stat().st_mode):
        raise ElfError("input must be a regular file")
    with path.open("rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ElfError("input must be a regular file")
        result = inspect_stream(stream, info.st_size, target)
    result["path"] = str(path.resolve())
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--target", required=True, choices=TARGETS)
    args = parser.parse_args(argv)
    try:
        result = inspect_file(args.path, args.target)
    except (OSError, ElfError) as error:
        print(json.dumps({"header_checks_passed": False, "error": str(error)}))
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result["header_checks_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
