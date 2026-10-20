#!/usr/bin/env python3
"""Generate Folklore texture packs from textures extracted from a local owner's game copy.

Only the supported, heuristically classified "color" subset is upscaled; data and
review textures stay as the original game assets. This is not a game installer.
"""

import argparse
import collections
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import classify_textures  # noqa: E402
import extract_textures  # noqa: E402
import pack_io  # noqa: E402
import upscale_pack  # noqa: E402

TASK = "owned-texture-pipeline"
MARKER = "folklore-owned-pack.json"
MARKER_SCHEMA = "folklore.owned-texture-workspace"
RECIPE_VERSION = 1
STRENGTH = 0.4
TIERS = {"medium": 2, "high": 4}
FORMATS = {"DXT1", "DXT3", "DXT5"}
ALLOWED = {MARKER, "catalog", "batch", "texture-packs", "receipt.json"}
SCOPE = ("supported extracted heuristic color subset only; data and review textures are "
         "left as original game assets; counts do not claim full-game coverage")
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class PipelineError(Exception):
    pass


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _norm(path):
    return os.path.normcase(os.path.abspath(str(path)))


def _inside(child, parent):
    c, p = _norm(child), _norm(parent)
    return c == p or c.startswith(p.rstrip("\\/") + os.sep)


def _is_link(path):
    try:
        st = os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return False
    if stat.S_ISLNK(st.st_mode) or getattr(st, "st_file_attributes", 0) & _REPARSE:
        return True
    isjunction = getattr(os.path, "isjunction", None)
    return bool(isjunction and isjunction(path))


def _index(complete):
    return {"schema_version": 1,
            "tiers": {t: {"scale": s, "path": t, "complete": complete} for t, s in TIERS.items()}}


def _load_json(path, label):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PipelineError(f"cannot read {label} {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise PipelineError(f"{label} {path} is not a JSON object")
    return data


def _validate_paths(input_root, workspace, model):
    inp, ws, mdl = (Path(os.path.abspath(str(p))) for p in (input_root, workspace, model))
    if ws.parent == ws:
        raise PipelineError("workspace must not be a filesystem root")
    for path in (ws, *ws.parents):
        if _is_link(path):
            raise PipelineError(f"workspace path contains a link or reparse point: {path}")
    for label, path in (("input root", inp), ("model", mdl)):
        if any(_is_link(part) for part in (path, *path.parents)):
            raise PipelineError(f"{label} must not contain a link or reparse point: {path}")
    if not inp.is_dir():
        raise PipelineError(f"input root is not a directory: {inp}")
    if not mdl.is_file():
        raise PipelineError(f"model is not a file: {mdl}")
    ws_r, inp_r, mdl_r = ws.resolve(), inp.resolve(), mdl.resolve()
    if ws_r.parent == ws_r:
        raise PipelineError("workspace must not be a filesystem root")
    for w, i in ((ws, inp), (ws_r, inp_r)):
        if _inside(w, i) or _inside(i, w):
            raise PipelineError("workspace and input root must not overlap")
    for m in (mdl, mdl_r):
        if _inside(m, ws) or _inside(m, ws_r):
            raise PipelineError("model must be outside the workspace")
    return inp_r, ws, mdl_r


def _owned_paths(ws):
    catalog, batch, packs = ws / "catalog", ws / "batch", ws / "texture-packs"
    return [ws, ws / MARKER, ws / "receipt.json", catalog, catalog / "manifest.json",
            catalog / "originals", catalog / "manifest.classified.json", batch,
            batch / "ledger.json", packs, packs / "pack-index.json", *(packs / t for t in TIERS)]


def _check_owned(ws):
    for path in (*ws.parents, *_owned_paths(ws)):
        if _is_link(path):
            raise PipelineError(f"refusing to write through link or reparse point: {path}")


def _create_workspace(ws, marker):
    if not ws.parent.is_dir():
        raise PipelineError(f"workspace parent does not exist: {ws.parent}")
    tmp = ws.parent / f".{ws.name}.creating-{os.getpid()}"
    try:
        tmp.mkdir()
    except OSError as exc:
        raise PipelineError(f"cannot create workspace staging folder {tmp}: {exc}") from exc
    try:
        upscale_pack.atomic_json(tmp / MARKER, marker)
        if os.path.lexists(ws):
            raise PipelineError(f"workspace appeared during creation: {ws}")
        os.rename(tmp, ws)
    except BaseException:
        try:
            (tmp / MARKER).unlink()
            tmp.rmdir()
        except OSError:
            pass
        raise


def _open_existing(ws, marker):
    if _is_link(ws) or not ws.is_dir():
        raise PipelineError(f"workspace exists and is not a plain directory: {ws}")
    marker_path = ws / MARKER
    if _is_link(marker_path) or not marker_path.is_file():
        raise PipelineError(f"existing folder was not created by this tool; choose a new workspace: {ws}")
    found = _load_json(marker_path, "workspace marker")
    if found.get("schema") != MARKER_SCHEMA or found.get("task") != TASK:
        raise PipelineError(f"unrecognized workspace marker: {marker_path}")
    for key in ("schema_version", "input_root", "model_sha256", "recipe"):
        value = found.get(key)
        if key == "input_root":
            same = isinstance(value, str) and os.path.normcase(value) == os.path.normcase(marker[key])
        else:
            same = value == marker[key]
        if not same:
            raise PipelineError(f"workspace was created for a different {key.replace('_', ' ')}; refusing to resume")
    extra = sorted(entry.name for entry in ws.iterdir() if entry.name not in ALLOWED)
    if extra:
        raise PipelineError(f"workspace contains unrelated content {extra}; refusing to resume")


def _select(records):
    selected, ids = [], set()
    for rec in records:
        if not isinstance(rec, dict) or rec.get("kind") != "color":
            continue
        tid = rec.get("id")
        if not isinstance(tid, str) or not re.fullmatch("[0-9a-f]{64}", tid) or tid in ids:
            raise PipelineError(f"invalid or duplicate color texture id: {tid!r}")
        if rec.get("format") not in FORMATS:
            raise PipelineError(f"unsupported color texture format {rec.get('format')!r} for {tid}")
        ids.add(tid)
        selected.append(rec)
    if not selected:
        raise PipelineError("no color textures were classified; nothing eligible to generate")
    return selected


def _within(root, rel, label):
    if not isinstance(rel, str) or not rel or Path(rel).is_absolute() or not _inside(root / rel, root):
        raise PipelineError(f"{label} path escapes {root}: {rel!r}")
    if any(_is_link(part) for part in (root / rel, *(root / rel).parents)):
        raise PipelineError(f"{label} path contains a link or reparse point: {rel!r}")
    return root / rel


def _same_path(raw, expected, packs, ws):
    if not isinstance(raw, str) or not raw:
        return False
    path = Path(raw)
    candidates = [path] if path.is_absolute() else [packs / path, expected.parent / path, ws / path]
    return any(_norm(c) == _norm(expected) for c in candidates)


def _verify(ws, selected):
    catalog, packs = ws / "catalog", ws / "texture-packs"
    ledger = _load_json(ws / "batch" / "ledger.json", "ledger")
    items = ledger["items"] if isinstance(ledger.get("items"), dict) else ledger
    seen = {tier: set() for tier in TIERS}
    for item in selected:
        tid = item["id"]
        entry = items.get(tid)
        if not isinstance(entry, dict) or entry.get("status") != "complete":
            raise PipelineError(f"ledger has no complete entry for {tid}")
        src = _within(catalog, item.get("source_png"), "source png")
        if _is_link(src) or not src.is_file():
            raise PipelineError(f"source png missing for {tid}: {src}")
        if entry.get("input_sha256") != _sha256(src):
            raise PipelineError(f"ledger entry for {tid} is stale: source png changed")
        recorded = entry.get("packs") if isinstance(entry.get("packs"), dict) else {}
        for tier, scale in TIERS.items():
            pack = recorded.get(tier)
            if not isinstance(pack, dict):
                raise PipelineError(f"ledger has no {tier} pack for {tid}")
            expected = packs / tier / str(pack_io.relative_name(item))
            if not _same_path(pack.get("path"), expected, packs, ws):
                raise PipelineError(f"{tier} pack path for {tid} is not the expected {expected}")
            if pack.get("scale", scale) != scale:
                raise PipelineError(f"{tier} pack for {tid} records scale {pack.get('scale')}, expected {scale}")
            if _is_link(expected) or not expected.is_file():
                raise PipelineError(f"{tier} pack missing for {tid}: {expected}")
            digest = _sha256(expected)
            if digest != pack.get("sha256"):
                raise PipelineError(f"{tier} pack for {tid} does not match its ledger SHA-256")
            try:
                result = pack_io.verify_pack(expected, item, scale)
            except Exception as exc:
                raise PipelineError(f"{tier} pack for {tid} failed verification: {exc}") from exc
            if not isinstance(result, dict) or result.get("sha256") != digest:
                raise PipelineError(f"{tier} pack verification for {tid} disagrees with ledger SHA-256")
            for key in ("width", "height"):
                if key in result and isinstance(item.get(key), int) and result[key] != item[key] * scale:
                    raise PipelineError(f"{tier} pack for {tid} has wrong {key}")
            if _norm(expected) in seen[tier]:
                raise PipelineError(f"duplicate {tier} pack path {expected}")
            seen[tier].add(_norm(expected))
    return {tier: len(paths) for tier, paths in seen.items()}


def run_pipeline(input_root, workspace, model, device="cpu", precision="float32", tile=128, pad=32, threads=2):
    if device not in ("cpu", "cuda"):
        raise PipelineError(f"unsupported device {device!r}")
    if precision not in ("float32", "float16"):
        raise PipelineError(f"unsupported precision {precision!r}")
    if precision == "float16" and device != "cuda":
        raise PipelineError("float16 precision requires CUDA")
    for name, value, low in (("tile", tile, 1), ("pad", pad, 0), ("threads", threads, 1)):
        if isinstance(value, bool) or not isinstance(value, int) or value < low:
            raise PipelineError(f"{name} must be an integer >= {low}")
    inp, ws, mdl = _validate_paths(input_root, workspace, model)
    recipe = {"recipe_version": RECIPE_VERSION, "strength": STRENGTH, "device": device,
              "precision": precision, "tile": tile, "pad": pad, "threads": threads, "tiers": dict(TIERS)}
    marker = {"schema": MARKER_SCHEMA, "schema_version": 1, "task": TASK, "input_root": str(inp),
              "model_sha256": _sha256(mdl), "recipe": recipe}
    if os.path.lexists(ws):
        _open_existing(ws, marker)
    else:
        _create_workspace(ws, marker)

    catalog, batch, packs = ws / "catalog", ws / "batch", ws / "texture-packs"
    _check_owned(ws)
    for folder in (packs, *(packs / t for t in TIERS)):
        folder.mkdir(exist_ok=True)
    upscale_pack.atomic_json(packs / "pack-index.json", _index(False))
    if (ws / "receipt.json").exists():
        upscale_pack.atomic_json(ws / "receipt.json", {"status": "invalidated", "reason": "revalidation in progress"})
    for folder in (catalog, batch):
        folder.mkdir(exist_ok=True)

    _check_owned(ws)
    extract_textures.extract(inp, catalog)
    _check_owned(ws)
    classified = classify_textures.classify_manifest(catalog / "manifest.json")
    if not isinstance(classified, dict) or not isinstance(classified.get("textures"), list):
        raise PipelineError("classification did not return a texture list")
    classified_path = catalog / "manifest.classified.json"
    upscale_pack.atomic_json(classified_path, classified)
    classified_sha = _sha256(classified_path)
    selected = _select(classified["textures"])

    _check_owned(ws)
    counts = upscale_pack.process_pack(classified_path, batch, mdl, threads=threads, strength=STRENGTH,
                                       tile=tile, pad=pad, device=device, precision=precision, pack_root=packs)
    if not isinstance(counts, dict):
        raise PipelineError("texture processing returned no counts")
    if counts.get("failed"):
        raise PipelineError(f"texture processing reported {counts.get('failed')} failed item(s)")

    _check_owned(ws)
    if _sha256(classified_path) != classified_sha:
        raise PipelineError("classified manifest changed during processing")
    if _sha256(mdl) != marker["model_sha256"]:
        raise PipelineError("model file changed during processing")
    tiers = _verify(ws, selected)
    if any(n != len(selected) for n in tiers.values()):
        raise PipelineError("pack counts do not match the selected color textures")
    kinds = collections.Counter(r.get("kind") for r in classified["textures"] if isinstance(r, dict))
    receipt = {"schema_version": 1, "task": TASK, "status": "completed", "artwork_upscaled": len(selected),
               "count_preserved": len(classified["textures"]) - len(selected), "model_sha256": marker["model_sha256"],
               "classified_manifest_sha256": classified_sha, "recipe": recipe, "runtime_tested": False,
               "scope": SCOPE, "counts": {"color_selected": len(selected), "by_kind": dict(kinds), "packs": tiers}}
    _check_owned(ws)
    upscale_pack.atomic_json(ws / "receipt.json", receipt)
    upscale_pack.atomic_json(packs / "pack-index.json", _index(True))
    return receipt


def _positive(text):
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return value


def _non_negative(text):
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return value


def build_parser():
    parser = argparse.ArgumentParser(
        description="Generate medium/high Folklore texture packs from the supported color subset of "
                    "textures extracted from your own game copy. Does not install anything into the game.")
    parser.add_argument("--input-root", required=True, help="folder containing etc/model/mt .fht_/.bin_ archives (read only)")
    parser.add_argument("--workspace", required=True, help="new or resumable output folder outside the input tree")
    parser.add_argument("--model", required=True, help="trusted local upscaling model file (read only)")
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--precision", choices=("float32", "float16"), default="float32")
    parser.add_argument("--tile", type=_positive, default=128)
    parser.add_argument("--pad", type=_non_negative, default=32)
    parser.add_argument("--threads", type=_positive, default=2)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        receipt = run_pipeline(args.input_root, args.workspace, args.model, device=args.device,
                               precision=args.precision, tile=args.tile, pad=args.pad, threads=args.threads)
    except KeyboardInterrupt:
        print("error: interrupted; generation did not complete", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("generation did not complete; inspect the last workspace receipt", file=sys.stderr)
        return 1
    print(f"completed: {receipt['counts']['color_selected']} color textures verified in medium and high tiers "
          f"({SCOPE})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
