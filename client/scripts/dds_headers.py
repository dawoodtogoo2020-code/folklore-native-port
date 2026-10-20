"""Validate bounded DDS metadata; never decode or modify a texture payload."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import struct
from fht_inventory import inventory


def parse_header(raw, entry_size):
    if len(raw) != 128 or entry_size < 128:
        raise ValueError("truncated 128-byte DDS header")
    swapped = raw[:4] == b" SDD"
    header = b"".join(raw[i:i + 4][::-1] for i in range(0, 128, 4)) if swapped else raw
    if header[:4] != b"DDS " or struct.unpack_from("<I", header, 4)[0] != 124:
        raise ValueError("DDS magic/header size mismatch")
    if struct.unpack_from("<I", header, 76)[0] != 32:
        raise ValueError("DDS pixel-format size mismatch")
    flags, height, width, pitch, depth, mips = struct.unpack_from("<6I", header, 8)
    pixel_flags = struct.unpack_from("<I", header, 80)[0]
    fourcc = header[84:88].decode("ascii", errors="backslashreplace")
    caps2 = struct.unpack_from("<I", header, 112)[0]
    if not 1 <= width <= 32768 or not 1 <= height <= 32768 or mips > 16:
        raise ValueError("invalid or unsupported dimension/mip bounds")
    if mips and mips > max(width, height).bit_length():
        raise ValueError("mip count exceeds dimensions")
    expected = None
    if pixel_flags & 4 and fourcc in ("DXT1", "DXT3", "DXT5") and caps2 == 0 and depth in (0, 1):
        block = 8 if fourcc == "DXT1" else 16
        expected = sum(((max(1, width >> level) + 3) // 4) *
                       ((max(1, height >> level) + 3) // 4) * block
                       for level in range(max(1, mips)))
    return {"header_word_swap": swapped, "width": width, "height": height,
            "flags": flags, "pitch_or_linear_size": pitch, "depth": depth,
            "mip_count_field": mips, "pixel_flags": pixel_flags, "fourcc": fourcc,
            "caps2": caps2, "payload_bytes": entry_size - 128,
            "expected_2d_bcn_bytes": expected,
            "payload_size_matches": None if expected is None else expected == entry_size - 128,
            "payload_layout_or_decode_validated": False}


def inspect(root):
    root = Path(root).resolve(strict=True)
    index = root / "bin" / "etc.fht_"
    container = root / "bin" / "etc.bin_"
    checked = inventory(index, container)
    entries = [e for e in checked["entries"] if e["type_hint"] == ".dds_"]
    if len(entries) > 4096:
        raise ValueError("DDS header read budget exceeded")
    rows = []
    with container.open("rb") as source:
        for entry in entries:
            source.seek(entry["offset"])
            raw = source.read(min(128, entry["size"]))
            row = {"name": entry["name"], "offset": entry["offset"],
                   "entry_size": entry["size"], "header_sha256": hashlib.sha256(raw).hexdigest()}
            try:
                row.update(parse_header(raw, entry["size"]))
                row["header_valid"] = True
            except ValueError as exc:
                row.update({"header_valid": False, "error": str(exc)})
            rows.append(row)
    return {"schema": "folklore-dds-headers-v1", "index_provenance": checked["provenance"],
            "entry_count": len(rows), "header_bytes_read": sum(min(128, e["size"]) for e in entries),
            "valid_headers": sum(r["header_valid"] for r in rows),
            "format_counts": dict(Counter(r.get("fourcc", "invalid") for r in rows)),
            "word_swapped_headers": sum(r.get("header_word_swap", False) for r in rows),
            "matching_payload_sizes": sum(r.get("payload_size_matches") is True for r in rows),
            "payload_decoded": False, "rows": rows}


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--asset-root", type=Path, default=Path(r"Q:\folklore\work\input\candidate-usa\PS3_GAME\USRDIR"))
    cli.add_argument("--report", type=Path, default=Path(r"Q:\folklore\work\evidence\dds-header-validation.json"))
    args = cli.parse_args()
    report = args.report.resolve()
    if not report.is_relative_to(Path(r"Q:\folklore\work\evidence").resolve()):
        cli.error("report must remain inside private work/evidence")
    result = inspect(args.asset_root)
    with report.open("x", encoding="utf-8") as output:
        json.dump(result, output, indent=2)
        output.write("\n")
    print(json.dumps({k: v for k, v in result.items() if k not in ("rows", "index_provenance")}))
    return 0 if result["valid_headers"] == result["entry_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
