"""Offline, resumable CPU texture upscaler. Inputs and model are read only."""

from __future__ import annotations

import argparse
import csv
import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

from PIL import Image
import pack_io


class CudaGuardStop(RuntimeError):
    """A competing game or an unavailable process query stopped CUDA work."""


def guard_cuda_idle() -> None:
    control_path = os.environ.get("FOLKLORE_TEXTURE_BATCH_CONTROL")
    if control_path:
        try:
            control = json.loads(Path(control_path).read_text(encoding="utf-8"))
            if control.get("status") != "running":
                raise ValueError("batch paused by its controller")
        except (OSError, ValueError) as error:
            raise CudaGuardStop(str(error)) from error
    if os.name != "nt":
        raise CudaGuardStop("CUDA process guard requires Windows")
    try:
        completed = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True,
                                   text=True, timeout=10, check=True)
        rows = list(csv.reader(completed.stdout.splitlines()))
        if not rows or any(not row or len(row) < 2 for row in rows):
            raise ValueError("unexpected tasklist output")
        active = {row[0].strip().casefold() for row in rows}
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise CudaGuardStop(f"CUDA process query failed: {error}") from error
    competing = {name for name in active if name == "rpcs3.exe" or
                 (name.startswith("folkloreboot") and name.endswith(".exe"))}
    if competing:
        raise CudaGuardStop("CUDA paused because " + ", ".join(sorted(competing)) + " is running")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(30):
            try:
                os.replace(temp, path)
                break
            except PermissionError as error:
                if getattr(error, 'winerror', None) not in (5, 32, 33) or attempt == 29:
                    raise
                time.sleep(0.05)
    finally:
        temp.unlink(missing_ok=True)


def safe_source(manifest_dir: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("source_png must be a relative path")
    source = manifest_dir / relative
    if any(part in ("..", "") for part in Path(relative).parts):
        raise ValueError("source_png escapes manifest directory")
    if not source.resolve().is_relative_to(manifest_dir.resolve()):
        raise ValueError("source_png escapes manifest directory")
    cursor = source
    while cursor != manifest_dir:
        if cursor.is_symlink():
            raise ValueError("source_png contains a symlink")
        cursor = cursor.parent
    if not source.is_file():
        raise FileNotFoundError(source)
    return source


def validate_manifest(manifest: Path) -> list[dict]:
    raw = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("textures"), list):
        raise ValueError("manifest requires a textures list")
    found = {}
    for entry in raw["textures"]:
        if not isinstance(entry, dict):
            raise ValueError("texture must be an object")
        key = entry.get("id")
        if not isinstance(key, str) or len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise ValueError("texture id must be 64 lowercase hex characters")
        if entry.get("kind") not in ("color", "data", "review"):
            raise ValueError(f"invalid kind for {key}")
        if not isinstance(entry.get("width"), int) or not isinstance(entry.get("height"), int) or entry["width"] < 1 or entry["height"] < 1:
            raise ValueError(f"invalid dimensions for {key}")
        if not isinstance(entry.get("aliases"), list):
            raise ValueError(f"invalid aliases for {key}")
        safe_source(manifest.parent, entry.get("source_png"))
        if key in found and found[key] != entry:
            raise ValueError(f"conflicting definitions for {key}")
        found[key] = entry
    return list(found.values())


def ensure_output_isolated(manifest: Path, output: Path) -> None:
    root = manifest.parent.resolve()
    target = output.resolve()
    if target == root or target.is_relative_to(root):
        raise ValueError("output must be outside manifest/input directory")
    cursor = output
    while cursor != cursor.parent:
        if cursor.is_symlink():
            raise ValueError("output path contains a symlink")
        cursor = cursor.parent


def set_cpu_limits(threads: int) -> bool:
    import torch

    torch.set_num_threads(min(threads, 2))
    try:
        torch.set_num_interop_threads(1)
    except RuntimeError:
        pass
    if os.name != "nt":
        return False
    try:
        # BELOW_NORMAL_PRIORITY_CLASS, a best-effort process setting.
        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.SetPriorityClass.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
        kernel32.SetPriorityClass.restype = ctypes.c_int
        return bool(kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), 0x00004000))
    except (AttributeError, OSError):
        return False


def load_model(path: Path, device: str = "cpu", precision: str = "float32", guard=None):
    if precision not in ("float32", "float16") or (precision == "float16" and device != "cuda"):
        raise ValueError("float16 precision requires CUDA; choose float32 for CPU")
    if device == "cuda":
        (guard or guard_cuda_idle)()  # Before any CUDA availability check or model load.
    import numpy as np
    import torch
    from spandrel import ModelLoader

    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable")
    target = torch.device(device)
    descriptor = ModelLoader(device=target).load_from_file(path).to(target)
    if precision == "float16":
        if not descriptor.supports_half:
            raise ValueError("selected model does not support float16")
        descriptor = descriptor.half().eval()
    else:
        descriptor = descriptor.float().eval()
    if descriptor.scale != 4:
        raise ValueError(f"model native scale must be 4, got {descriptor.scale}")
    if any(parameter.device.type != device for parameter in descriptor.model.parameters()):
        raise ValueError(f"model has parameters outside {device}")

    def infer(rgb: Image.Image) -> Image.Image:
        array = np.asarray(rgb.convert("RGB"), dtype=np.float32) / 255.0
        dtype = torch.float16 if precision == "float16" else torch.float32
        tensor = torch.from_numpy(array.transpose(2, 0, 1).copy()).unsqueeze(0).to(device=target, dtype=dtype)
        with torch.inference_mode():
            result = descriptor(tensor).clamp(0, 1)[0].permute(1, 2, 0).cpu().numpy()
        return Image.fromarray(np.rint(result * 255).astype(np.uint8), "RGB")

    return infer


def upscale_tiled(rgb: Image.Image, infer, tile: int, pad: int, guard=None) -> Image.Image:
    width, height = rgb.size
    result = Image.new("RGB", (width * 4, height * 4))
    for y in range(0, height, tile):
        for x in range(0, width, tile):
            if guard is not None:
                guard()
            right, bottom = min(x + tile, width), min(y + tile, height)
            left_halo, top_halo = max(0, x - pad), max(0, y - pad)
            right_halo, bottom_halo = min(width, right + pad), min(height, bottom + pad)
            source = rgb.crop((left_halo, top_halo, right_halo, bottom_halo))
            predicted = infer(source)
            expected = (source.width * 4, source.height * 4)
            if not isinstance(predicted, Image.Image) or predicted.size != expected:
                raise ValueError(f"model returned {getattr(predicted, 'size', None)}, expected {expected}")
            crop = predicted.convert("RGB").crop(((x - left_halo) * 4, (y - top_halo) * 4,
                                                   (right - left_halo) * 4, (bottom - top_halo) * 4))
            result.paste(crop, (x * 4, y * 4))
    return result


def make_output(original: Image.Image, infer, tile: int, pad: int, strength: float, guard=None) -> Image.Image:
    rgba = original.convert("RGBA")
    rgb = rgba.convert("RGB")
    predicted = upscale_tiled(rgb, infer, tile, pad, guard)
    ordinary = rgb.resize(predicted.size, Image.Resampling.BICUBIC)
    blended = Image.blend(ordinary, predicted, strength)
    alpha = rgba.getchannel("A").resize(predicted.size, Image.Resampling.BILINEAR)
    blended.putalpha(alpha)
    return blended


def valid_resume(record: dict | None, binding: dict, output_file: Path) -> bool:
    if not isinstance(record, dict) or record.get("status") != "complete":
        return False
    if any(record.get(key) != value for key, value in binding.items()):
        return False
    if not output_file.is_file() or output_file.is_symlink():
        return False
    try:
        if sha256(output_file) != record.get("output_sha256"):
            return False
        with Image.open(output_file) as image:
            image.verify()
        with Image.open(output_file) as image:
            return image.size == tuple(record.get("dimensions", ())) and image.mode == "RGBA"
    except (OSError, ValueError, TypeError):
        return False


def valid_pack_resume(record: dict | None, binding: dict, pack_root: Path, item: dict) -> bool:
    if not isinstance(record, dict) or record.get("status") != "complete":
        return False
    if any(record.get(key) != value for key, value in binding.items()):
        return False
    saved = record.get("packs")
    if not isinstance(saved, dict) or set(saved) != {"medium", "high"}:
        return False
    try:
        filename = pack_io.relative_name(item)
        for tier, scale in (("medium", 2), ("high", 4)):
            path = pack_root / tier / filename
            if not isinstance(saved[tier], dict) or saved[tier] != pack_io.verify_pack(path, item, scale):
                return False
        return True
    except (OSError, ValueError, TypeError):
        return False


def check_space(output: Path, width: int, height: int) -> None:
    # Destination RGBA, model RGB, bicubic RGB, and a conservative PNG buffer.
    required = 1024**3 + width * height * 16 * 4 * 20
    free = shutil.disk_usage(output).free
    if free < required:
        raise OSError(f"insufficient free space: need {required:,} bytes including 1 GiB reserve, have {free:,}")


def estimate(entries: list[dict]) -> dict:
    color = [entry for entry in entries if entry["kind"] == "color"]
    pixels = sum(entry["width"] * entry["height"] for entry in color)
    output_bytes = pixels * 16 * 4
    return {"entries": len(entries), "color": len(color), "held": len(entries) - len(color),
            "source_pixels": pixels, "output_rgba_bytes": output_bytes,
            "conservative_work_bytes": output_bytes * 20}


def dry_run(manifest: Path, output: Path, model: Path, *, limit: int | None = None,
            strength: float = 0.4, tile: int = 128, pad: int = 32, device: str = "cpu",
            precision: str = "float32", pack_root: Path | None = None) -> dict:
    manifest, output, model = Path(manifest).absolute(), Path(output).absolute(), Path(model).absolute()
    if device not in ("cpu", "cuda") or precision not in ("float32", "float16") or \
            (precision == "float16" and device != "cuda") or (limit is not None and limit < 0):
        raise ValueError("invalid device, precision, or limit; float16 requires CUDA")
    entries = validate_manifest(manifest)
    ensure_output_isolated(manifest, output)
    pack_root = pack_io.safe_root(pack_root) if pack_root is not None else None
    if pack_root is not None:
        ensure_output_isolated(manifest, pack_root)
    if not model.is_file():
        raise FileNotFoundError(model)
    selected = entries if limit is None else entries[:limit]
    summary = estimate(selected)
    if pack_root is not None:
        summary["pack_raw_body_bytes"] = sum(
            pack_io.expected_layout(entry, scale)[3]
            for entry in selected if entry["kind"] == "color" for scale in (2, 4))
    summary["resume_ready"] = 0
    ledger_file = output / "ledger.json"
    if ledger_file.is_symlink() or (output / "textures").is_symlink():
        raise ValueError("output contains a symlink")
    if ledger_file.exists():
        ledger = json.loads(ledger_file.read_text(encoding="utf-8"))
        if ledger.get("version") != 1 or not isinstance(ledger.get("items"), dict):
            raise ValueError("unsupported ledger")
        model_hash = sha256(model)
        parameters = {"strength": strength, "tile": tile, "pad": pad, "scale": 4,
                      "alpha": "bilinear", "base": "bicubic", "device": device, "precision": precision}
        if pack_root is not None:
            parameters.update(output_mode="frp-v2", pack_root=str(pack_root))
        for entry in selected:
            if entry["kind"] != "color":
                continue
            binding = {"input_sha256": sha256(safe_source(manifest.parent, entry["source_png"])),
                       "model_sha256": model_hash, "parameters": parameters,
                       "source_png": entry["source_png"], "kind": entry["kind"]}
            if pack_root is None:
                destination = output / "textures" / (entry["id"] + ".png")
                ready = valid_resume(ledger["items"].get(entry["id"]), binding, destination)
            else:
                base, fmt, _, _ = pack_io.item_info(entry)
                binding.update(base_payload_sha256=base, pack_format=fmt)
                ready = valid_pack_resume(ledger["items"].get(entry["id"]), binding, pack_root, entry)
            summary["resume_ready"] += int(ready)
    return summary


def process_pack(manifest: Path, output: Path, model: Path, *, limit: int | None = None,
                 threads: int = 2, strength: float = 0.4, tile: int = 128, pad: int = 32,
                 device: str = "cpu", precision: str = "float32", pack_root: Path | None = None,
                 infer_callback=None, guard_callback=None) -> dict:
    manifest, output, model = Path(manifest).absolute(), Path(output).absolute(), Path(model).absolute()
    if device not in ("cpu", "cuda") or precision not in ("float32", "float16") or \
            (precision == "float16" and device != "cuda") or not 0 <= strength <= 1 or \
            tile < 1 or pad < 0 or threads < 1 or (limit is not None and limit < 0):
        raise ValueError("invalid strength, tile, pad, threads, device, precision, or limit; float16 requires CUDA")
    entries = validate_manifest(manifest)
    ensure_output_isolated(manifest, output)
    pack_root = pack_io.safe_root(pack_root) if pack_root is not None else None
    if pack_root is not None:
        ensure_output_isolated(manifest, pack_root)
        for entry in entries:
            if entry["kind"] == "color":
                pack_io.item_info(entry)
                pack_io.expected_layout(entry, 4)
    if not model.is_file():
        raise FileNotFoundError(model)
    output.mkdir(parents=True, exist_ok=True)
    texture_dir = output / "textures"
    if pack_root is None:
        if texture_dir.is_symlink():
            raise ValueError("texture output directory is a symlink")
        texture_dir.mkdir(exist_ok=True)
    else:
        pack_root.mkdir(parents=True, exist_ok=True)
    ledger_file = output / "ledger.json"
    if ledger_file.is_symlink():
        raise ValueError("ledger is a symlink")
    ledger = json.loads(ledger_file.read_text(encoding="utf-8")) if ledger_file.exists() else {"version": 1, "items": {}}
    if ledger.get("version") != 1 or not isinstance(ledger.get("items"), dict):
        raise ValueError("unsupported ledger")
    model_hash = sha256(model)
    parameters = {"strength": strength, "tile": tile, "pad": pad, "scale": 4, "alpha": "bilinear",
                  "base": "bicubic", "device": device, "precision": precision}
    if pack_root is not None:
        parameters.update(output_mode="frp-v2", pack_root=str(pack_root))
    selected = entries if limit is None else entries[:limit]
    print("Pack estimate: " + json.dumps(estimate(selected), sort_keys=True), flush=True)
    counts = {"complete": 0, "resumed": 0, "held": 0, "failed": 0}
    infer = infer_callback
    guard = (guard_callback or guard_cuda_idle) if device == "cuda" else None
    for index, entry in enumerate(selected, 1):
        key = entry["id"]
        started = time.monotonic()
        source = safe_source(manifest.parent, entry["source_png"])
        input_hash = sha256(source)
        binding = {"input_sha256": input_hash, "model_sha256": model_hash, "parameters": parameters,
                   "source_png": entry["source_png"], "kind": entry["kind"]}
        destination = texture_dir / (key + ".png") if pack_root is None else None
        if destination is not None and destination.is_symlink():
            raise ValueError(f"output is a symlink: {destination}")
        if entry["kind"] != "color":
            ledger["items"][key] = {**binding, "status": "held", "reason": entry["kind"]}
            atomic_json(ledger_file, ledger)
            counts["held"] += 1
            print(f"[{index}/{len(selected)}] {key} held ({entry['kind']})", flush=True)
            continue
        if pack_root is not None:
            base, fmt, _, _ = pack_io.item_info(entry)
            binding.update(base_payload_sha256=base, pack_format=fmt)
        ready = (valid_resume(ledger["items"].get(key), binding, destination) if pack_root is None else
                 valid_pack_resume(ledger["items"].get(key), binding, pack_root, entry))
        if ready:
            counts["resumed"] += 1
            print(f"[{index}/{len(selected)}] {key} resumed", flush=True)
            continue
        temp = destination.with_name(destination.name + "." + uuid.uuid4().hex + ".tmp") if destination else None
        try:
            if pack_root is None:
                check_space(output, entry["width"], entry["height"])
            else:
                pair_bytes = sum(pack_io.expected_layout(entry, scale)[3] for scale in (2, 4))
                pack_io.check_space(pack_root, pair_bytes)
            with Image.open(source) as image:
                image.load()
                if image.size != (entry["width"], entry["height"]):
                    raise ValueError(f"source dimensions {image.size} differ from manifest")
                if infer is None:
                    infer = load_model(model, device=device, precision=precision, guard=guard)
                result = make_output(image, infer, tile, pad, strength, guard)
                if guard is not None:
                    guard()  # Check after inference before publishing either tier.
                if pack_root is not None:
                    packs = pack_io.build_pair(image.convert("RGBA"), result, entry, pack_root)
            if pack_root is None:
                result.save(temp, format="PNG")
                with Image.open(temp) as verify:
                    verify.load()
                    if verify.size != (entry["width"] * 4, entry["height"] * 4) or verify.mode != "RGBA":
                        raise ValueError("saved image validation failed")
                os.replace(temp, destination)
                record = {**binding, "status": "complete", "output_sha256": sha256(destination),
                          "dimensions": list(result.size), "seconds": round(time.monotonic() - started, 3)}
            else:
                record = {**binding, "status": "complete", "packs": packs,
                          "seconds": round(time.monotonic() - started, 3)}
            ledger["items"][key] = record
            atomic_json(ledger_file, ledger)
            counts["complete"] += 1
            print(f"[{index}/{len(selected)}] {key} complete {record['seconds']}s", flush=True)
        except KeyboardInterrupt:
            if temp is not None:
                temp.unlink(missing_ok=True)
            raise
        except CudaGuardStop:
            if temp is not None:
                temp.unlink(missing_ok=True)
            raise
        except Exception as error:
            if temp is not None:
                temp.unlink(missing_ok=True)
            ledger["items"][key] = {**binding, "status": "failed", "error": str(error),
                                     "seconds": round(time.monotonic() - started, 3)}
            atomic_json(ledger_file, ledger)
            counts["failed"] += 1
            print(f"[{index}/{len(selected)}] {key} failed: {error}", flush=True)
            if infer is None or (device == "cuda" and "out of memory" in str(error).lower()):
                raise RuntimeError(f"Inference cannot continue safely: {error}") from error
    return counts


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pack-root", type=Path, help="write verified 2x/4x FTX4 v2 packs instead of bulk PNGs")
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--precision", choices=("float32", "float16"), default="float32")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--strength", type=float, default=0.4)
    parser.add_argument("--tile", type=int, default=128)
    parser.add_argument("--pad", type=int, default=32)
    parser.add_argument("--dry-run", action="store_true", help="report workload and verified resume count without inference or writes")
    args = parser.parse_args(argv)
    try:
        if args.dry_run:
            print(json.dumps(dry_run(args.manifest, args.output, args.model, limit=args.limit,
                                     strength=args.strength, tile=args.tile, pad=args.pad, device=args.device,
                                     precision=args.precision, pack_root=args.pack_root), sort_keys=True))
            return 0
        priority = set_cpu_limits(args.threads)
        print(f"Device: {args.device}; precision: {args.precision}; Windows below-normal priority: {priority}", flush=True)
        counts = process_pack(args.manifest, args.output, args.model, limit=args.limit,
                              threads=args.threads, strength=args.strength, tile=args.tile, pad=args.pad,
                              device=args.device, precision=args.precision, pack_root=args.pack_root)
        print(json.dumps(counts, sort_keys=True), flush=True)
        return 1 if counts["failed"] else 0
    except KeyboardInterrupt:
        print("Interrupted after last atomic checkpoint", file=sys.stderr)
        return 130
    except CudaGuardStop as error:
        print(f"Stopped safely: {error}", file=sys.stderr)
        return 3
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
