"""FTX4 v2 lossless texture-pack writer and verifier; no game files are changed."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import struct
import uuid
import zlib
from pathlib import Path

from PIL import Image


HEADER = struct.Struct("<4s7I32sQ32s")
MAGIC = b"FTX4"
VERSION = 2
RAW_LIMIT = 2 * 1024**3
SOURCE_LIMIT = 4096
OUTPUT_LIMIT = 16384
RESERVE = 1024**3
FORMATS = {"DXT1": 1, "BC1": 1, "DXT3": 2, "BC2": 2, "DXT5": 3, "BC3": 3}
READ_CHUNK = 1024 * 1024


def _is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def safe_root(root: Path) -> Path:
    root = Path(root).absolute()
    if ".." in root.parts or any(_is_link(path) for path in (root, *root.parents)):
        raise ValueError("pack root has traversal or a link/junction")
    return root


def item_info(item: dict) -> tuple[str, int, int, int]:
    base = item.get("base_payload_sha256")
    if not isinstance(base, str) or not re.fullmatch(r"[0-9a-f]{64}", base):
        raise ValueError("direct pack requires base_payload_sha256")
    fmt = FORMATS.get(str(item.get("format", "")).upper())
    if fmt is None:
        raise ValueError("direct pack supports only DXT1/DXT3/DXT5 or BC1/BC2/BC3")
    width, height = item.get("width"), item.get("height")
    if type(width) is not int or type(height) is not int or not 1 <= width <= SOURCE_LIMIT or not 1 <= height <= SOURCE_LIMIT:
        raise ValueError("source dimensions exceed 4096 or are invalid")
    return base, fmt, width, height


def relative_name(item: dict) -> str:
    base, fmt, width, height = item_info(item)
    return f"{base}-f{fmt}-{width}x{height}.frp"


def mip_sizes(width: int, height: int):
    sizes = []
    while True:
        sizes.append((width, height))
        if width == 1 and height == 1:
            break
        width, height = max(1, width // 2), max(1, height // 2)
    if len(sizes) > 16:
        raise ValueError("mip chain exceeds 16 levels")
    return sizes


def expected_layout(item: dict, scale: int) -> tuple[int, int, list, int]:
    _, _, width, height = item_info(item)
    if scale not in (2, 4):
        raise ValueError("pack scale must be 2 or 4")
    outw, outh = width * scale, height * scale
    if outw > OUTPUT_LIMIT or outh > OUTPUT_LIMIT:
        raise ValueError("output dimensions exceed 16384")
    sizes = mip_sizes(outw, outh)
    raw_length = sum(w * h * 4 for w, h in sizes)
    if raw_length >= RAW_LIMIT:
        raise ValueError("uncompressed mip body reaches 2 GiB")
    return outw, outh, sizes, raw_length


def check_space(root: Path, raw_length: int) -> None:
    free = shutil.disk_usage(root).free
    # zlib can expand incompressible data slightly; a second file may already exist.
    required = RESERVE + raw_length + raw_length // 100 + READ_CHUNK
    if free < required:
        raise OSError(f"pack output needs {required:,} free bytes including 1 GiB reserve; have {free:,}")


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(READ_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_temp(path: Path, image: Image.Image, item: dict, scale: int, sizes: list, raw_length: int) -> None:
    base, fmt, width, height = item_info(item)
    digest = hashlib.sha256()
    compressor = zlib.compressobj(level=1)
    count = 0
    with path.open("xb") as stream:
        stream.write(b"\0" * HEADER.size)
        current = image
        for index, (mipw, miph) in enumerate(sizes):
            if index:
                current = current.resize((mipw, miph), Image.Resampling.LANCZOS)
            for y in range(0, miph, 16):
                raw = current.crop((0, y, mipw, min(y + 16, miph))).tobytes()
                count += len(raw)
                digest.update(raw)
                stream.write(compressor.compress(raw))
        stream.write(compressor.flush())
        if count != raw_length:
            raise ValueError("mip body length mismatch")
        stream.seek(0)
        stream.write(HEADER.pack(MAGIC, VERSION, fmt, width, height, sizes[0][0], sizes[0][1],
                                 len(sizes), bytes.fromhex(base), count, digest.digest()))
        stream.flush()
        os.fsync(stream.fileno())


def verify_pack(path: Path, item: dict, scale: int) -> dict:
    path = Path(path)
    if not path.is_file() or _is_link(path):
        raise ValueError("pack file missing or linked")
    base, fmt, width, height = item_info(item)
    outw, outh, sizes, raw_length = expected_layout(item, scale)
    digest = hashlib.sha256()
    count = 0
    decoder = zlib.decompressobj()
    with path.open("rb") as stream:
        header = stream.read(HEADER.size)
        if len(header) != HEADER.size:
            raise ValueError("truncated FTX4 header")
        unpacked = HEADER.unpack(header)
        expected = (MAGIC, VERSION, fmt, width, height, outw, outh, len(sizes), bytes.fromhex(base), raw_length)
        if unpacked[:-1] != expected:
            raise ValueError("FTX4 header does not match the requested item")
        while True:
            block = stream.read(READ_CHUNK)
            if not block:
                break
            remaining = block
            while remaining:
                try:
                    raw = decoder.decompress(remaining, READ_CHUNK)
                except zlib.error as error:
                    raise ValueError(f"FTX4 compressed body is corrupt: {error}") from error
                count += len(raw)
                if count > raw_length:
                    raise ValueError("FTX4 body exceeds declared length")
                digest.update(raw)
                if decoder.eof:
                    if decoder.unused_data or stream.read(1):
                        raise ValueError("FTX4 has trailing bytes")
                    remaining = b""
                    break
                remaining = decoder.unconsumed_tail
                if not remaining:
                    break
        if not decoder.eof or count != raw_length or digest.digest() != unpacked[-1]:
            raise ValueError("FTX4 compressed body is truncated or corrupt")
    return {"path": str(path), "sha256": _sha_file(path), "width": outw, "height": outh,
            "mip_count": len(sizes), "raw_body_bytes": raw_length, "scale": scale,
            "format": fmt, "base_payload_sha256": base}


def _write_one(root: Path, tier: str, image: Image.Image, item: dict, scale: int) -> dict:
    outw, outh, sizes, raw_length = expected_layout(item, scale)
    if image.mode != "RGBA" or image.size != (outw, outh):
        raise ValueError(f"{tier} image must be RGBA {outw}x{outh}")
    directory = root / tier
    if _is_link(directory):
        raise ValueError("pack tier directory is linked")
    directory.mkdir(exist_ok=True)
    target = directory / relative_name(item)
    if _is_link(target):
        raise ValueError("pack output is linked")
    check_space(root, raw_length)
    temp = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        _write_temp(temp, image, item, scale, sizes, raw_length)
        candidate = verify_pack(temp, item, scale)
        if target.exists():
            existing = verify_pack(target, item, scale)
            if existing["sha256"] != candidate["sha256"]:
                raise ValueError(f"existing {tier} pack differs; choose a new pack root")
            return existing
        os.replace(temp, target)
        return verify_pack(target, item, scale)
    finally:
        temp.unlink(missing_ok=True)


def build_pair(original_rgba: Image.Image, high_rgba: Image.Image, item: dict, root: Path) -> dict:
    """Write verified 2x/4x lossless packs, without a bulk PNG intermediate."""
    root = safe_root(root)
    _, _, width, height = item_info(item)
    if original_rgba.mode != "RGBA" or original_rgba.size != (width, height):
        raise ValueError("original must match RGBA source dimensions")
    if high_rgba.mode != "RGBA" or high_rgba.size != (width * 4, height * 4):
        raise ValueError("high must be exact 4x RGBA")
    expected_alpha = original_rgba.getchannel("A").resize(high_rgba.size, Image.Resampling.BILINEAR)
    actual_alpha = high_rgba.getchannel("A")
    for y in range(0, high_rgba.height, 64):
        rect = (0, y, high_rgba.width, min(y + 64, high_rgba.height))
        if actual_alpha.crop(rect).tobytes() != expected_alpha.crop(rect).tobytes():
            raise ValueError("high alpha is not bilinear from original")
    del actual_alpha, expected_alpha
    root.mkdir(parents=True, exist_ok=True)
    medium_rgb = high_rgba.convert("RGB").resize((width * 2, height * 2), Image.Resampling.LANCZOS)
    medium_rgb.putalpha(original_rgba.getchannel("A").resize((width * 2, height * 2), Image.Resampling.BILINEAR))
    medium = _write_one(root, "medium", medium_rgb, item, 2)
    high = _write_one(root, "high", high_rgba, item, 4)
    return {"medium": medium, "high": high}
