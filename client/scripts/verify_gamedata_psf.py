"""Independent, read-only verifier for a game-data PARAM.SFO.

Usage: python verify-gamedata-psf.py ABSOLUTE/PARAM.SFO [--expect-title-id ID]
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import sys

MAGIC = b"\x00PSF"
VERSION = 0x00000101
HEADER = struct.Struct("<4sIIII")
ENTRY = struct.Struct("<HHIII")
FORMATS = {0x0204: "string", 0x0404: "integer", 0x0004: "binary"}
MAX_FILE = 1024 * 1024
MAX_ENTRIES = 512


class PSFError(ValueError):
    pass


def _fail(message: str) -> None:
    raise PSFError(message)


def _align4(value: int) -> int:
    return (value + 3) & ~3


def _separate(intervals: list[tuple[int, int]], label: str) -> None:
    intervals.sort()
    for (_, end), (next_start, _) in zip(intervals, intervals[1:]):
        if end > next_start:
            _fail(f"overlapping {label}")


def verify_bytes(raw: bytes, *, expect_title_id: str | None = None,
                 expect_title: str | None = None,
                 expect_version: str | None = None) -> dict:
    if len(raw) < HEADER.size or len(raw) > MAX_FILE:
        _fail("file size outside supported bounds")
    magic, version, key_start, data_start, count = HEADER.unpack_from(raw)
    if magic != MAGIC:
        _fail("bad PSF magic")
    if version != VERSION:
        _fail("unsupported PSF version")
    if not 1 <= count <= MAX_ENTRIES:
        _fail("entry count outside supported bounds")
    index_end = HEADER.size + count * ENTRY.size
    if index_end > len(raw) or not index_end <= key_start < data_start <= len(raw):
        _fail("entry, key, or data table bounds overlap or exceed file")
    if data_start % 4:
        _fail("data table is not 4-byte aligned")

    fields: dict[str, object] = {}
    key_intervals: list[tuple[int, int]] = []
    data_intervals: list[tuple[int, int]] = []
    for index in range(count):
        key_offset, fmt, length, capacity, data_offset = ENTRY.unpack_from(
            raw, HEADER.size + index * ENTRY.size)
        if fmt not in FORMATS:
            _fail(f"entry {index}: unsupported format 0x{fmt:04x}")
        if length > capacity or capacity == 0 or data_offset % 4:
            _fail(f"entry {index}: invalid length, capacity, or alignment")
        key_pos = key_start + key_offset
        if key_pos < key_start or key_pos >= data_start:
            _fail(f"entry {index}: key offset outside key table")
        key_end = raw.find(b"\0", key_pos, data_start)
        if key_end < 0 or key_end == key_pos:
            _fail(f"entry {index}: missing or empty NUL-terminated key")
        key_intervals.append((key_pos, key_end + 1))
        key_bytes = raw[key_pos:key_end]
        try:
            key = key_bytes.decode("ascii")
        except UnicodeDecodeError:
            _fail(f"entry {index}: key is not ASCII")
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            _fail(f"entry {index}: invalid key name")
        if key in fields:
            _fail(f"duplicate key: {key}")

        start = data_start + data_offset
        end = start + length
        capacity_end = start + _align4(capacity)
        if start < data_start or end > len(raw) or capacity_end > len(raw):
            _fail(f"entry {index}: data length or capacity outside data table")
        data_intervals.append((start, capacity_end))
        value_bytes = raw[start:end]
        kind = FORMATS[fmt]
        if kind == "string":
            if not value_bytes or value_bytes[-1] != 0 or b"\0" in value_bytes[:-1]:
                _fail(f"entry {index}: string is not exactly NUL-terminated")
            try:
                value: object = value_bytes[:-1].decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                _fail(f"entry {index}: string is not UTF-8")
        elif kind == "integer":
            if length != 4 or capacity != 4:
                _fail(f"entry {index}: integer must have four bytes")
            value = struct.unpack("<I", value_bytes)[0]
        else:
            value = {"format": "binary", "hex": value_bytes.hex(), "length": length}
        fields[key] = value
    _separate(key_intervals, "key strings")
    _separate(data_intervals, "data capacities")

    for key in ("CATEGORY", "TITLE_ID", "TITLE", "PARENTAL_LEVEL"):
        if key not in fields:
            _fail(f"missing required {key}")
    if fields["CATEGORY"] not in ("GD", "DG"):
        _fail("CATEGORY must be GD or DG")
    for key in ("TITLE_ID", "TITLE"):
        if not isinstance(fields[key], str) or not fields[key]:
            _fail(f"{key} must be a nonempty string")
    if not re.fullmatch(r"[A-Z0-9]{9}", fields["TITLE_ID"]):
        _fail("TITLE_ID must contain nine uppercase letters or digits")
    if not isinstance(fields["PARENTAL_LEVEL"], int):
        _fail("PARENTAL_LEVEL must be an integer")
    version_value = fields.get("APP_VER", fields.get("VERSION"))
    if not isinstance(version_value, str) or not version_value:
        _fail("missing or invalid APP_VER/VERSION")
    for key, value in fields.items():
        if re.fullmatch(r"TITLE_\d\d", key) and (not isinstance(value, str) or not value):
            _fail(f"{key} must be a nonempty localized title")

    if expect_title_id is not None and fields["TITLE_ID"] != expect_title_id:
        _fail("TITLE_ID does not match expectation")
    if expect_title is not None and fields["TITLE"] != expect_title:
        _fail("TITLE does not match expectation")
    if expect_version is not None and version_value != expect_version:
        _fail("APP_VER/VERSION does not match expectation")
    return {
        "structure_passed": True,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size": len(raw),
        "entry_count": count,
        "psf_version": f"0x{version:08x}",
        "version": version_value,
        "fields": fields,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path, help="absolute path to PARAM.SFO")
    parser.add_argument("--expect-title-id")
    parser.add_argument("--expect-title")
    parser.add_argument("--expect-version")
    args = parser.parse_args(argv)
    try:
        if not args.file.is_absolute() or not args.file.is_file():
            _fail("input must be an existing absolute file")
        if args.file.stat().st_size > MAX_FILE:
            _fail("file exceeds 1 MiB verification limit")
        result = verify_bytes(args.file.read_bytes(),
                              expect_title_id=args.expect_title_id,
                              expect_title=args.expect_title,
                              expect_version=args.expect_version)
        result["file"] = str(args.file)
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    except (PSFError, OSError) as exc:
        print(json.dumps({"structure_passed": False, "file": str(args.file),
                          "error": str(exc)}, ensure_ascii=False, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    sys.exit(main())
