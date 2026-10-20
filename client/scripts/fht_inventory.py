"""Inventory the observed Folklore FHT profile without reading container payloads.

This is an index parser, not an extractor or payload-format decoder.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path, PurePosixPath
import struct
import sys

RECORD_SIZE = 40
MAX_INDEX_BYTES = 4 * 1024 * 1024
SCHEMA = "folklore-fht-inventory-v1"


class FormatError(ValueError):
    """Input does not satisfy the observed, bounds-checked index profile."""


def parse_index(data: bytes, container_size: int) -> dict:
    """Parse a bounded small index; container size comes from stat, not a read."""
    if not isinstance(container_size, int) or container_size < 0:
        raise FormatError("container size must be a nonnegative integer")
    if not data or len(data) > MAX_INDEX_BYTES or len(data) % RECORD_SIZE:
        raise FormatError("index must be nonempty, at most 4 MiB, and a multiple of 40 bytes")
    entries = []
    names = set()
    for position in range(0, len(data), RECORD_SIZE):
        record = data[position:position + RECORD_SIZE]
        name_field = record[:32]
        terminator = name_field.find(b"\0")
        if terminator <= 0 or any(name_field[terminator:]):
            raise FormatError(f"record {position // RECORD_SIZE}: invalid NUL-padded name")
        raw_name = name_field[:terminator]
        if any(value < 32 or value > 126 for value in raw_name):
            raise FormatError(f"record {position // RECORD_SIZE}: name is not printable ASCII")
        name = raw_name.decode("ascii")
        if name in names:
            raise FormatError(f"record {position // RECORD_SIZE}: duplicate name {name!r}")
        names.add(name)
        size, offset = struct.unpack_from(">II", record, 32)
        if size == 0:
            raise FormatError(f"record {position // RECORD_SIZE}: zero size is outside the observed profile")
        end = offset + size  # Python integers do not wrap at 32 bits.
        if end > container_size:
            raise FormatError(f"record {position // RECORD_SIZE}: range exceeds container size")
        entries.append({
            "index": position // RECORD_SIZE,
            "name": name,
            "type_hint": PurePosixPath(name).suffix or None,
            "offset": offset,
            "size": size,
            "end_exclusive": end,
        })
    ordered = sorted(entries, key=lambda entry: entry["offset"])
    previous_end = 0
    gap_bytes = 0
    for entry in ordered:
        if entry["offset"] < previous_end:
            raise FormatError(f"record {entry['index']}: overlapping container ranges")
        gap_bytes += entry["offset"] - previous_end
        previous_end = entry["end_exclusive"]
    trailing_bytes = container_size - previous_end
    contiguous = gap_bytes == 0 and trailing_bytes == 0
    return {
        "profile": {
            "record_bytes": RECORD_SIZE,
            "name": "32-byte NUL-padded printable ASCII",
            "integers": "big-endian unsigned 32-bit",
            "size_field_offset": 32,
            "container_offset_field_offset": 36,
            "confidence": "high for index layout; empirically inferred, not a published specification",
            "compression": "unknown; no payload bytes inspected",
            "payload_endianness": "unknown",
            "type_hints": "filename suffixes only; payload types unverified",
        },
        "summary": {
            "entry_count": len(entries),
            "container_size": container_size,
            "indexed_bytes": sum(entry["size"] for entry in entries),
            "gap_bytes": gap_bytes,
            "trailing_bytes": trailing_bytes,
            "complete_contiguous_coverage": contiguous,
            "record_order_matches_offset_order": entries == ordered,
            "type_hint_counts": dict(sorted(Counter(entry["type_hint"] or "<none>" for entry in entries).items())),
            "bounds_valid": True,
            "nonoverlap_valid": True,
        },
        "entries": entries,
    }


def inventory(index_path: Path, container_path: Path) -> dict:
    """Read at most MAX_INDEX_BYTES + 1; only stat the paired container."""
    if not index_path.is_file() or not container_path.is_file():
        raise FormatError("index and container must both be regular files")
    if index_path.resolve() == container_path.resolve():
        raise FormatError("index and container must be distinct files")
    with index_path.open("rb") as stream:
        data = stream.read(MAX_INDEX_BYTES + 1)
    result = parse_index(data, container_path.stat().st_size)
    result["provenance"] = {
        "index_path": str(index_path.resolve()),
        "index_bytes": len(data),
        "index_sha256": hashlib.sha256(data).hexdigest(),
        "container_path": str(container_path.resolve()),
        "container_content_read": False,
        "container_hash": None,
        "container_identity_limit": "path and size only; payload identity not verified",
    }
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair", nargs=2, action="append", required=True,
                        metavar=("INDEX", "CONTAINER"), help="repeat for each small FHT and paired BIN")
    args = parser.parse_args(argv)
    try:
        reports = [inventory(Path(index), Path(container)) for index, container in args.pair]
    except (OSError, FormatError) as error:
        print(f"fht_inventory: {error}", file=sys.stderr)
        return 2
    json.dump({"schema": SCHEMA, "inventories": reports}, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
