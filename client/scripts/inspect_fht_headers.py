"""Read bounded entry prefixes from three validated local FHT/BIN pairs.

No extraction, decompression, payload execution, whole-container reads or hashes.
Raw prefix bytes belong only in the private evidence report, never repository docs.
"""
import argparse
from collections import Counter, defaultdict
import datetime
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fht_inventory import FormatError, inventory

MAX_HEADER = 64
MAX_TOTAL = 1024 * 1024
PAIR_NAMES = ("etc", "model", "mt")
DEFAULT_ASSETS = Path(r"Q:\folklore\work\input\candidate-usa\PS3_GAME\USRDIR")
PRIVATE_EVIDENCE = Path(r"Q:\folklore\work\evidence")


def classify(header):
    """Prefix observations only: these do not validate a complete file/stream."""
    if header.startswith(b"\x7fELF"):
        if len(header) >= 20 and header[4] in (1, 2) and header[5] in (1, 2):
            machine = int.from_bytes(header[18:20], "little" if header[5] == 1 else "big")
            return "ELF-prefix-EM_SPU" if machine == 23 else f"ELF-prefix-machine-{machine}"
        return "ELF-prefix-incomplete-or-invalid-identification"
    for magic, label in ((b"DDS ", "DDS-prefix"), (b"\x89PNG\r\n\x1a\n", "PNG-prefix"),
                         (b"\x1f\x8b\x08", "gzip-prefix-candidate"),
                         (b"\x28\xb5\x2f\xfd", "Zstandard-prefix-candidate"),
                         (b"\x04\x22\x4d\x18", "LZ4-frame-prefix-candidate"),
                         (b"PK\x03\x04", "ZIP-local-header-prefix-candidate")):
        if header.startswith(magic):
            return label
    if len(header) >= 2 and header[0] & 15 == 8 and header[0] >> 4 <= 7 and int.from_bytes(header[:2], "big") % 31 == 0:
        return "zlib-CMF-FLG-candidate"
    return "unrecognized-prefix"


def checked_path(root, relative):
    resolved = (root / relative).resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise FormatError("asset pair must contain regular files inside the supplied asset root")
    return resolved


def inspect_headers(asset_root, header_bytes=MAX_HEADER, total_limit=MAX_TOTAL):
    if type(header_bytes) is not int or not 1 <= header_bytes <= MAX_HEADER:
        raise FormatError("per-entry prefix limit must be 1..64 bytes")
    if type(total_limit) is not int or not 1 <= total_limit <= MAX_TOTAL:
        raise FormatError("total prefix budget must be 1..1048576 bytes")
    root = Path(asset_root).resolve(strict=True)
    pairs = []
    planned = 0
    for name in PAIR_NAMES:
        index = checked_path(root, Path("bin") / f"{name}.fht_")
        container = checked_path(root, Path("bin") / f"{name}.bin_")
        validated = inventory(index, container)
        planned += sum(min(header_bytes, entry["size"]) for entry in validated["entries"])
        pairs.append((name, container, validated))
    if planned > total_limit:
        raise FormatError("planned prefix reads exceed total budget; no container was opened")
    records = []
    suffix_groups = defaultdict(Counter)
    prefixes = defaultdict(Counter)
    actual = 0
    provenance = []
    for name, container, validated in pairs:
        with container.open("rb") as stream:
            before = os.fstat(stream.fileno())
            if before.st_size != validated["summary"]["container_size"]:
                raise FormatError("container size changed after range validation")
            for entry in validated["entries"]:
                wanted = min(header_bytes, entry["size"])
                stream.seek(entry["offset"])
                header = stream.read(wanted)
                actual += len(header)
                if len(header) != wanted:
                    raise FormatError("truncated entry prefix read")
                suffix = entry["type_hint"] or "<none>"
                observation = classify(header)
                suffix_groups[suffix][observation] += 1
                prefixes[suffix][header[:8].hex()] += 1
                records.append({"pair": name, "entry_index": entry["index"], "name": entry["name"],
                                "suffix": suffix, "offset": entry["offset"], "entry_size": entry["size"],
                                "bytes_read": len(header), "header_hex": header.hex(), "observation": observation})
            after = os.fstat(stream.fileno())
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise FormatError("container changed during prefix inspection")
        provenance.append({"pair": name, "index": validated["provenance"], "entries": len(validated["entries"]),
                           "payload_bytes_read": sum(min(header_bytes, e["size"]) for e in validated["entries"])})
    return {"schema": "folklore-fht-header-inspection-v1", "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "asset_root": str(root), "per_entry_limit": header_bytes, "total_limit": total_limit,
            "payload_bytes_read": actual, "entry_count": len(records), "pairs": provenance,
            "suffix_observations": {k: dict(v) for k, v in sorted(suffix_groups.items())},
            "private_first_eight_byte_groups": {k: dict(v) for k, v in sorted(prefixes.items())},
            "limitations": "Magic/prefix checks only, not validated formats, decompressibility, loadability or executable identity. No bytes beyond each bounded prefix were examined. Path/size/mtime are not a whole-container content identity.",
            "entries": records}


def private_report_path(path):
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(PRIVATE_EVIDENCE.resolve()):
        raise FormatError("raw header reports must stay inside the private work/evidence directory")
    return resolved


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--asset-root", type=Path, default=DEFAULT_ASSETS)
    cli.add_argument("--header-bytes", type=int, default=MAX_HEADER)
    cli.add_argument("--total-limit", type=int, default=MAX_TOTAL)
    cli.add_argument("--report", type=Path, default=PRIVATE_EVIDENCE / "fht-header-inspection.json")
    args = cli.parse_args(argv)
    try:
        report_path = private_report_path(args.report)
        if report_path.exists():
            raise FormatError("report already exists; overwrite forbidden")
        report = inspect_headers(args.asset_root, args.header_bytes, args.total_limit)
        with report_path.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2)
            stream.write("\n")
    except (OSError, ValueError) as error:
        print(f"fht_headers: {error}", file=sys.stderr)
        return 2
    print(f"Inspected {report['entry_count']} entries; {report['payload_bytes_read']} payload bytes; private report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
