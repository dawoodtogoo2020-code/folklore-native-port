"""Offline, resumable extraction of supported DDS and PNG textures from FHT/BIN pairs."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import time

from PIL import Image

INDEX_LIMIT = 4 * 1024 * 1024
ENTRY_LIMIT = 128 * 1024 * 1024
MAX_EVENTS = 10000
PAIRS = ("etc", "model", "mt")
SCAN_SUFFIXES = {".dds_", ".bi5_", ".vtg_", ".png"}
BLOCK_BYTES = {b"DXT1": 8, b"DXT3": 16, b"DXT5": 16}
CHECKPOINT_EVERY = 100
UNSUPPORTED_REASONS = {
    ".sgd_": "audio or scene data; no texture parser",
    ".vpc_": "vertex program/container; no validated texture parser",
    ".bin_": "nested binary container; no validated child parser",
}


class FormatError(ValueError):
    pass


def _event(events: list[dict], **fields: object) -> None:
    if len(events) < MAX_EVENTS:
        events.append(fields)


def _safe_root(path: str | Path, *, existing: bool) -> Path:
    path = Path(path).absolute()
    if existing and not path.is_dir():
        raise FormatError(f"directory does not exist: {path}")
    # Resolve for containment only after checking every existing component.
    for part in (path, *path.parents):
        if part.is_symlink():
            raise FormatError(f"symlink path refused: {part}")
    return path.resolve(strict=False)


def _regular_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def _atomic_bytes(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".extract-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        for attempt in range(20):
            try:
                os.replace(tmp, path)
                break
            except PermissionError as error:
                if getattr(error, 'winerror', None) not in (5, 32, 33) or attempt == 19:
                    raise
                time.sleep(0.05)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_index(index: Path, container_size: int) -> list[tuple[str, int, int]]:
    if not _regular_file(index):
        raise FormatError(f"index missing or symlink: {index.name}")
    length = index.stat().st_size
    if length > INDEX_LIMIT or length % 40:
        raise FormatError(f"index length invalid: {index.name} ({length})")
    records = []
    with index.open("rb") as source:
        for number in range(length // 40):
            record = source.read(40)
            raw_name = record[:32].split(b"\0", 1)[0]
            if not raw_name or any(c < 32 or c > 126 for c in raw_name):
                raise FormatError(f"invalid ASCII entry name at record {number}")
            name = raw_name.decode("ascii")
            size, offset = struct.unpack(">II", record[32:])
            if offset > container_size or size > container_size - offset:
                raise FormatError(f"entry outside container: {name}")
            records.append((name, offset, size))
    return records


def _bi5_children(blob: bytes) -> list[tuple[int, int, str]]:
    if len(blob) < 20:
        raise FormatError("BI5 header truncated")
    total, count, offset_table, names_table, data_start = struct.unpack_from(">5I", blob)
    if total > len(blob) or total < 20 or count > 100000:
        raise FormatError("BI5 total or child count invalid")
    if offset_table < 20 or offset_table + 4 * (count + 1) > total:
        raise FormatError("BI5 offset table outside entry")
    if names_table + 24 * count > total or names_table < 20:
        raise FormatError("BI5 name table outside entry")
    if data_start > total:
        raise FormatError("BI5 data start outside entry")
    offsets = struct.unpack_from(f">{count + 1}I", blob, offset_table)
    if any(a > b for a, b in zip(offsets, offsets[1:])):
        raise FormatError("BI5 child offsets decrease")
    if offsets and (offsets[0] < data_start or offsets[-1] > total):
        raise FormatError("BI5 child offsets outside entry")
    children = []
    for i in range(count):
        raw = blob[names_table + i * 24:names_table + (i + 1) * 24].split(b"\0", 1)[0]
        name = raw.decode("ascii", errors="replace")
        # Names are manifest metadata only; never use them in paths.
        children.append((offsets[i], offsets[i + 1], name))
    return children


def _candidate_offsets(blob: bytes):
    seen = set()
    for signature in (b"DDS ", b" SDD"):
        start = 0
        while True:
            found = blob.find(signature, start)
            if found < 0:
                break
            if found not in seen:
                seen.add(found)
                yield found
            start = found + 1


def parse_dds(blob: bytes, offset: int, end: int) -> tuple[bytes, int, int, str, int]:
    if offset < 0 or end > len(blob) or offset + 128 > end:
        raise FormatError("DDS header truncated")
    header = blob[offset:offset + 128]
    if header[:4] == b" SDD":
        header = b"".join(header[i:i + 4][::-1] for i in range(0, 128, 4))
    if header[:4] != b"DDS ":
        raise FormatError("DDS signature invalid")
    size, flags, height, width, _, depth, mips = struct.unpack_from("<7I", header, 4)
    if flags & 0x1007 != 0x1007:
        raise FormatError("DDS required header flags missing")
    available = end - offset
    if size != 124:
        if size < 128 or size > available:
            raise FormatError("DDS header size invalid")
        header = header[:4] + struct.pack("<I", 124) + header[8:]
    if not (1 <= width <= 4096 and 1 <= height <= 4096):
        raise FormatError("DDS dimensions unsupported")
    mips = mips or 1
    if mips > 13:
        raise FormatError("DDS mip count unsupported")
    pf_size, pf_flags = struct.unpack_from("<2I", header, 76)
    if pf_size != 32 or not (pf_flags & 4):
        raise FormatError("DDS pixel format unsupported")
    fourcc = header[84:88]
    if fourcc not in BLOCK_BYTES:
        raise FormatError(f"DDS compression unsupported: {fourcc.hex()}")
    caps2 = struct.unpack_from("<I", header, 112)[0]
    if struct.unpack_from("<I", header, 108)[0] & 0x1000 == 0:
        raise FormatError("DDS texture capability missing")
    if (caps2 & (0x200 | 0x200000)) or (flags & 0x800000) or depth > 1:
        raise FormatError("DDS cubemap or volume unsupported")
    payload_size = 0
    for level in range(mips):
        w, h = max(1, width >> level), max(1, height >> level)
        payload_size += ((w + 3) // 4) * ((h + 3) // 4) * BLOCK_BYTES[fourcc]
    if 128 + payload_size > available:
        raise FormatError("DDS payload truncated")
    return header + blob[offset + 128:offset + 128 + payload_size], width, height, fourcc.decode(), mips


def _valid_existing_png(path: Path, expected: tuple[int, int], png_hash: str | None) -> bool:
    if not png_hash or not _regular_file(path):
        return False
    try:
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != png_hash:
            return False
        with Image.open(BytesIO(data)) as image:
            if image.size != expected or image.format != "PNG":
                return False
            image.load()
            return True
    except (OSError, ValueError):
        return False


def _decoded_dds(data: bytes, expected: tuple[int, int]) -> Image.Image:
    with Image.open(BytesIO(data)) as decoded:
        decoded.load()
        if decoded.size != expected:
            raise FormatError("decoded dimensions mismatch")
        return decoded.convert("RGBA")


def _adopt_existing_png(path: Path, expected: tuple[int, int], source_pixels: Image.Image) -> bytes | None:
    """Adopt interrupted output only after a fresh source decode matches pixels."""
    if not _regular_file(path) or path.stat().st_size > ENTRY_LIMIT:
        return None
    try:
        data = path.read_bytes()
        with Image.open(BytesIO(data)) as image:
            if image.format != "PNG" or image.size != expected:
                return None
            image.load()
            return data if image.convert("RGBA").tobytes() == source_pixels.tobytes() else None
    except (OSError, ValueError):
        return None


def _png_source(blob: bytes) -> tuple[Image.Image, int, int]:
    try:
        with Image.open(BytesIO(blob)) as image:
            if image.format != "PNG" or not (1 <= image.width <= 4096 and 1 <= image.height <= 4096):
                raise FormatError("PNG format or dimensions unsupported")
            image.load()
            return image.convert("RGBA"), image.width, image.height
    except (OSError, ValueError) as error:
        raise FormatError(f"PNG decode rejected: {error}") from error


def _manifest(textures, counts, holds, rejects, index_hashes, source_stamps,
              suffixes, unsupported, archive_index, next_entry, complete):
    return {"textures": list(textures.values()), "scan_counts": dict(counts),
            "holds": holds, "rejections": rejects,
            "input_index_hashes": index_hashes, "source_stamps": source_stamps,
            "skipped_suffix_counts": dict(suffixes),
            "unsupported_formats": unsupported,
            "progress": {"archive_index": archive_index, "next_entry": next_entry},
            "complete": complete,
            "scan_scope": "etc/model/mt FHT entries ending .dds_, .bi5_, .vtg_, .png; supported DXT1/3/5 and direct PNG; nested formats held; game coverage unverified"}


def _checkpoint(path: Path, manifest: dict) -> None:
    _atomic_bytes(path, (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def extract(input_root: str | Path, output: str | Path) -> dict:
    root = _safe_root(input_root, existing=True)
    out = _safe_root(output, existing=False)
    if out == root or root in out.parents or out in root.parents:
        raise FormatError("input and output directories must be separate")
    if out.exists() and not out.is_dir():
        raise FormatError("output is not a directory")
    out.mkdir(parents=True, exist_ok=True)
    originals = out / "originals"
    if originals.is_symlink():
        raise FormatError("symlink originals directory refused")
    originals.mkdir(exist_ok=True)
    manifest_path = out / "manifest.json"
    if manifest_path.is_symlink():
        raise FormatError("symlink manifest refused")

    pairs = []
    index_hashes, source_stamps = {}, {}
    for stem in PAIRS:
        index, container = root / f"{stem}.fht_", root / f"{stem}.bin_"
        if _regular_file(index) and index.stat().st_size <= INDEX_LIMIT:
            index_hashes[index.name] = hashlib.sha256(index.read_bytes()).hexdigest()
        else:
            index_hashes[index.name] = None
        if _regular_file(container):
            stat = container.stat()
            source_stamps[container.name] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
        else:
            source_stamps[container.name] = None
        pairs.append((stem, index, container))

    old = {}
    if manifest_path.exists():
        try:
            old = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as error:
            raise FormatError(f"existing manifest cannot be read: {error}") from error
        if not isinstance(old, dict) or not isinstance(old.get("textures"), list):
            raise FormatError("existing manifest structure invalid")
    old_hashes = {}
    for item in old.get("textures", []):
        if not isinstance(item, dict):
            raise FormatError("existing manifest texture invalid")
        old_hashes[item.get("id")] = item.get("png_sha256")

    resume = (old.get("complete") is False and
              old.get("input_index_hashes") == index_hashes and
              old.get("source_stamps") == source_stamps and
              isinstance(old.get("progress"), dict))
    if resume:
        progress = old["progress"]
        archive_start, entry_start = progress.get("archive_index"), progress.get("next_entry")
        if not (isinstance(archive_start, int) and 0 <= archive_start <= len(PAIRS)
                and isinstance(entry_start, int) and entry_start >= 0):
            raise FormatError("partial manifest progress invalid")
        textures = {item["id"]: item for item in old["textures"]}
        for item in textures.values():
            expected = (item.get("width"), item.get("height"))
            if not _valid_existing_png(originals / f"{item['id']}.png", expected, item.get("png_sha256")):
                raise FormatError(f"partial manifest PNG invalid: {item['id']}")
        holds = old.get("holds", [])
        rejects = old.get("rejections", [])
        counts = Counter(old.get("scan_counts", {}))
        suffixes = Counter(old.get("skipped_suffix_counts", {}))
        unsupported = old.get("unsupported_formats", {})
    else:
        archive_start = entry_start = 0
        textures, holds, rejects = {}, [], []
        counts, suffixes = Counter(), Counter()
        unsupported = {}

    for archive_i, (stem, index, container) in enumerate(pairs):
        if archive_i < archive_start:
            continue
        if not _regular_file(container):
            _event(holds, archive=stem, reason="container missing or symlink")
            _checkpoint(manifest_path, _manifest(textures, counts, holds, rejects, index_hashes,
                        source_stamps, suffixes, unsupported, archive_i + 1, 0, False))
            continue
        try:
            records = read_index(index, container.stat().st_size)
        except FormatError as error:
            _event(holds, archive=stem, reason=str(error))
            _checkpoint(manifest_path, _manifest(textures, counts, holds, rejects, index_hashes,
                        source_stamps, suffixes, unsupported, archive_i + 1, 0, False))
            continue
        start = entry_start if archive_i == archive_start else 0
        if start > len(records):
            raise FormatError("partial manifest entry position invalid")
        if start == 0:
            counts["indexed_entries"] += len(records)
        with container.open("rb") as source:
            for record_i in range(start, len(records)):
                name, entry_offset, size = records[record_i]
                suffix = Path(name).suffix.lower()
                if suffix not in SCAN_SUFFIXES:
                    suffix = suffix or "<none>"
                    suffixes[suffix] += 1
                    detail = unsupported.setdefault(suffix, {"count": 0,
                        "reason": UNSUPPORTED_REASONS.get(suffix, "unparsed format; texture content unverified"),
                        "example": name})
                    detail["count"] += 1
                elif size > ENTRY_LIMIT:
                    _event(holds, archive=stem, entry=name, reason="entry exceeds 128 MiB")
                    counts["oversize_entries"] += 1
                else:
                    source.seek(entry_offset)
                    blob = source.read(size)
                    if len(blob) != size:
                        _event(holds, archive=stem, entry=name, reason="entry read truncated")
                    else:
                        counts["scanned_entries"] += 1
                        counts["scanned_bytes"] += size
                        if suffix == ".png":
                            try:
                                pixels, width, height = _png_source(blob)
                                digest = hashlib.sha256(blob).hexdigest()
                                if digest not in textures:
                                    path = originals / f"{digest}.png"
                                    if path.is_symlink():
                                        raise FormatError("symlink output PNG refused")
                                    if path.exists():
                                        if _valid_existing_png(path, (width, height), old_hashes.get(digest)):
                                            png_bytes = path.read_bytes()
                                        else:
                                            png_bytes = _adopt_existing_png(path, (width, height), pixels)
                                            if png_bytes is None:
                                                raise FormatError("existing PNG pixel mismatch; preserved")
                                            counts["adopted_pngs"] += 1
                                    else:
                                        _atomic_bytes(path, blob)
                                        png_bytes = blob
                                    textures[digest] = {"id": digest, "source_png": f"originals/{digest}.png",
                                        "png_sha256": hashlib.sha256(png_bytes).hexdigest(),
                                        "base_payload_sha256": None, "base_payload_bytes": None,
                                        "format": "PNG", "width": width, "height": height,
                                        "kind": "review", "aliases": []}
                                textures[digest]["aliases"].append({"archive": stem, "entry": name,
                                    "child_name": None, "entry_offset": entry_offset,
                                    "dds_offset": None, "format": "PNG", "mips": 1})
                            except (FormatError, OSError, ValueError) as error:
                                _event(rejects, archive=stem, entry=name, reason=str(error))
                        else:
                            children = []
                            if suffix == ".bi5_":
                                try:
                                    children = _bi5_children(blob)
                                except FormatError as error:
                                    _event(holds, archive=stem, entry=name, reason=str(error))
                                else:
                                    counts["bi5_children"] += len(children)
                            for dds_offset in _candidate_offsets(blob):
                                counts["signatures"] += 1
                                if counts["signatures"] > 100000:
                                    _event(holds, archive=stem, entry=name, reason="signature scan limit reached")
                                    break
                                child_name, limit = None, size
                                if children:
                                    child = next(((a, b, n) for a, b, n in children if a <= dds_offset < b), None)
                                    if not child:
                                        _event(rejects, archive=stem, entry=name, dds_offset=dds_offset,
                                               reason="DDS signature outside BI5 child range")
                                        continue
                                    _, limit, child_name = child
                                try:
                                    normalized, width, height, fmt, mips = parse_dds(blob, dds_offset, limit)
                                    digest = hashlib.sha256(normalized).hexdigest()
                                    base_size = ((width + 3) // 4) * ((height + 3) // 4) * BLOCK_BYTES[fmt.encode("ascii")]
                                    base_payload = normalized[128:128 + base_size]
                                    if digest not in textures:
                                        path = originals / f"{digest}.png"
                                        if path.is_symlink():
                                            raise FormatError("symlink output PNG refused")
                                        if path.exists() and _valid_existing_png(path, (width, height), old_hashes.get(digest)):
                                            png_bytes = path.read_bytes()
                                        else:
                                            pixels = _decoded_dds(normalized, (width, height))
                                            if path.exists():
                                                png_bytes = _adopt_existing_png(path, (width, height), pixels)
                                                if png_bytes is None:
                                                    raise FormatError("existing PNG pixel mismatch; preserved")
                                                counts["adopted_pngs"] += 1
                                            else:
                                                buffer = BytesIO()
                                                pixels.save(buffer, format="PNG")
                                                png_bytes = buffer.getvalue()
                                                _atomic_bytes(path, png_bytes)
                                        textures[digest] = {"id": digest, "source_png": f"originals/{digest}.png",
                                            "png_sha256": hashlib.sha256(png_bytes).hexdigest(),
                                            "base_payload_sha256": hashlib.sha256(base_payload).hexdigest(),
                                            "base_payload_bytes": base_size, "format": fmt,
                                            "width": width, "height": height, "kind": "review", "aliases": []}
                                    textures[digest]["aliases"].append({"archive": stem, "entry": name,
                                        "child_name": child_name, "entry_offset": entry_offset,
                                        "dds_offset": dds_offset, "format": fmt, "mips": mips})
                                except (FormatError, OSError, ValueError) as error:
                                    _event(rejects, archive=stem, entry=name, dds_offset=dds_offset, reason=str(error))
                if (record_i + 1) % CHECKPOINT_EVERY == 0:
                    _checkpoint(manifest_path, _manifest(textures, counts, holds, rejects, index_hashes,
                                source_stamps, suffixes, unsupported, archive_i, record_i + 1, False))
        _checkpoint(manifest_path, _manifest(textures, counts, holds, rejects, index_hashes,
                    source_stamps, suffixes, unsupported, archive_i + 1, 0, False))
    final = _manifest(textures, counts, holds, rejects, index_hashes,
                      source_stamps, suffixes, unsupported, len(PAIRS), 0, True)
    _checkpoint(manifest_path, final)
    return final


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        result = extract(args.input_root, args.output)
    except FormatError as error:
        parser.error(str(error))
    print(json.dumps({"textures": len(result["textures"]), "scanned_entries": result["scan_counts"].get("scanned_entries", 0), "holds": len(result["holds"]), "rejections": len(result["rejections"])}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
