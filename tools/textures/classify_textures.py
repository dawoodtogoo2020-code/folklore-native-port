"""Classify extracted texture manifests without changing images or archives."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageDraw


DATA_TERMS = re.compile(r"(?:^|[^a-z0-9])(normal|bump|spec|specular|rough|roughness|metal|metallic|mask|depth|tone|lut|lookup)(?:$|[^a-z0-9])", re.I)
CAMEL_DATA = re.compile(r"Normal|Bump|Specular|Spec|Roughness|Rough|Metallic|Metal|Mask|Depth|Tone|LUT|Lookup")
LIGHTMAP = re.compile(r"^LM\d+", re.I)
OPAQUE = re.compile(r"^(?:[0-9a-f]{8,}|(?:tex|texture|image|img)[_\-]?\d+)$", re.I)
VFX = re.compile(r"(?:^|[/\\_.\-])(vfx|fx|effect|effects|particle|particles)(?:$|[/\\_.\-])", re.I)
DATA_CHANNEL = re.compile(r"(?:^|[_\-.])(?:n|s)\d{0,3}(?:$|[_\-.])", re.I)


def safe_source(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("invalid source_png relative path")
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("source_png escapes manifest directory")
    cursor = path
    while cursor != root:
        if cursor.is_symlink():
            raise ValueError("source_png contains symlink")
        cursor = cursor.parent
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def normal_name(alias: dict) -> str:
    name = alias.get("child_name")
    if not isinstance(name, str):
        entry = alias.get("entry", "")
        name = entry if isinstance(entry, str) and entry.lower().endswith(".dds_") else ""
    return Path(name.replace("\\", "/")).name.rsplit(".", 1)[0]


def classify_aliases(aliases: list[dict], width: int, height: int) -> tuple[str, str, bool]:
    if width < 8 or height < 8:
        return "data", "tiny lookup or constant candidate (<8 pixels on an axis)", True
    names = [normal_name(alias) for alias in aliases]
    for name in names:
        if DATA_TERMS.search(name) or CAMEL_DATA.search(name) or DATA_CHANNEL.search(name):
            return "data", f"data term in child name: {name}", True
        if LIGHTMAP.match(name):
            return "data", f"LM lightmap name: {name}", True
        # Exact uppercase suffix; lowercase n/s in ordinary names is not enough.
        if name.endswith(("N", "S")) and len(name) > 1:
            return "data", f"uppercase N/S suffix heuristic: {name}", True
    for alias in aliases:
        path = f"{alias.get('archive', '')}/{alias.get('entry', '')}/{alias.get('child_name', '')}"
        if VFX.search(path):
            return "review", "VFX alias requires visual review", False
    meaningful = [name for name in names if name and not OPAQUE.fullmatch(name)]
    if meaningful:
        return "color", "inferred color from meaningful child name", True
    # Loose .dds_ files often contain menu/tutorial art; names alone do not prove
    # color, but these are eligible for the representative visual audit.
    top_level = any(str(alias.get("entry", "")).lower().endswith(".dds_") and not alias.get("child_name")
                    for alias in aliases)
    if top_level:
        return "color", "inferred color from top-level DDS artwork", True
    return "review", "opaque container or no meaningful child name", False


def canonical_key(entry: dict) -> tuple:
    payload = entry.get("base_payload_sha256")
    if isinstance(payload, str) and re.fullmatch(r"[0-9a-f]{64}", payload):
        return ("payload", payload, str(entry["format"]).lower(), entry["width"], entry["height"])
    return ("id", entry["id"])


def classify_manifest(manifest: Path) -> dict:
    manifest = Path(manifest).absolute()
    raw = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("textures"), list):
        raise ValueError("manifest requires textures list")
    groups = defaultdict(list)
    id_definitions = {}
    for entry in raw["textures"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not re.fullmatch(r"[0-9a-f]{64}", entry["id"]):
            raise ValueError("each texture needs a 64-character lowercase hex id")
        if not isinstance(entry.get("width"), int) or not isinstance(entry.get("height"), int) or min(entry["width"], entry["height"]) < 1:
            raise ValueError("invalid texture dimensions")
        if not isinstance(entry.get("format"), str) or not isinstance(entry.get("aliases"), list):
            raise ValueError("texture format and aliases required")
        if not all(isinstance(alias, dict) for alias in entry["aliases"]):
            raise ValueError("aliases must be objects")
        safe_source(manifest.parent, entry.get("source_png"))
        old = id_definitions.setdefault(entry["id"], entry)
        if old != entry:
            raise ValueError(f"conflicting definitions for {entry['id']}")
        if old is entry:
            groups[canonical_key(entry)].append(entry)
    classified = []
    for key, members in sorted(groups.items(), key=lambda pair: repr(pair[0])):
        source = min(members, key=lambda item: item["source_png"])
        aliases = []
        seen_aliases = set()
        for member in members:
            for alias in member["aliases"]:
                encoded = json.dumps(alias, sort_keys=True, separators=(",", ":"))
                if encoded not in seen_aliases:
                    seen_aliases.add(encoded)
                    aliases.append(alias)
        aliases.sort(key=lambda alias: json.dumps(alias, sort_keys=True))
        kind, reason, inferred = classify_aliases(aliases, source["width"], source["height"])
        canonical_id = hashlib.sha256(json.dumps(key, separators=(",", ":")).encode("utf-8")).hexdigest() if key[0] == "payload" else source["id"]
        classified.append({"id": canonical_id, "original_ids": sorted({member["id"] for member in members}),
                           "base_payload_sha256": source.get("base_payload_sha256"),
                           "source_png": source["source_png"], "width": source["width"], "height": source["height"],
                           "format": source["format"], "aliases": aliases, "kind": kind,
                           "classification_reason": reason, "classification_inferred": inferred})
    classified.sort(key=lambda item: item["id"])
    counts = {kind: sum(item["kind"] == kind for item in classified) for kind in ("color", "data", "review")}
    pixels = {kind: sum(item["width"] * item["height"] for item in classified if item["kind"] == kind)
              for kind in ("color", "data", "review")}
    return {"textures": classified, "summary": {"input_entries": len(raw["textures"]),
                                                   "consolidated_entries": len(classified), "counts": counts,
                                                   "source_pixels": pixels},
            "review_items": [{"id": item["id"], "source_png": item["source_png"],
                              "reason": item["classification_reason"]} for item in classified if item["kind"] == "review"]}


def make_contact_sheets(classified: dict, manifest: Path, folder: Path) -> list[Path]:
    manifest, folder = Path(manifest).absolute(), Path(folder).absolute()
    if folder.resolve().is_relative_to(manifest.parent.resolve()):
        raise ValueError("contact-sheet folder must be outside input directory")
    cursor = folder
    while cursor != cursor.parent:
        if cursor.is_symlink():
            raise ValueError("contact-sheet folder contains symlink")
        cursor = cursor.parent
    folder.mkdir(parents=True, exist_ok=True)
    made = []
    for kind in ("color", "data", "review"):
        selected = [item for item in classified["textures"] if item["kind"] == kind][:40]
        if not selected:
            continue
        sheet = Image.new("RGB", (800, ((len(selected) + 4) // 5) * 140), "#303030")
        draw = ImageDraw.Draw(sheet)
        for index, item in enumerate(selected):
            source = safe_source(manifest.parent, item["source_png"])
            with Image.open(source) as image:
                preview = image.convert("RGB")
                preview.thumbnail((150, 108), Image.Resampling.LANCZOS)
                x, y = (index % 5) * 160, (index // 5) * 140
                sheet.paste(preview, (x + (160 - preview.width) // 2, y))
            names = [normal_name(alias) for alias in item["aliases"]]
            label = next((name for name in names if name), Path(item["source_png"]).stem)
            draw.text((x + 3, y + 110), label[:23], fill="white")
            draw.text((x + 3, y + 124), item["id"][:12], fill="#bbbbbb")
        destination = folder / f"{kind}.jpg"
        sheet.save(destination, format="JPEG", quality=88)
        made.append(destination)
    return made


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--contact-sheet", type=Path, metavar="FOLDER")
    args = parser.parse_args(argv)
    try:
        result = classify_manifest(args.manifest)
        output = args.manifest.with_name(args.manifest.stem + ".classified.json")
        output.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
        sheets = make_contact_sheets(result, args.manifest, args.contact_sheet) if args.contact_sheet else []
        print(json.dumps({"output": str(output), "summary": result["summary"],
                          "contact_sheets": [str(path) for path in sheets]}, sort_keys=True))
        return 0
    except (OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
