"""Tests for generate_owned_pack (acceptance: owned-texture-pipeline).

Run: python -B -m unittest discover -s tools/textures -p test_generate_owned_pack.py
Uses only synthetic temp folders; no game files, model weights, Torch or GPU.
"""

import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import generate_owned_pack as gop  # noqa: E402
from generate_owned_pack import classify_textures, extract_textures, pack_io, upscale_pack  # noqa: E402

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

MAGIC = b"TESTFRP1\n"
ARCHIVES = {"a.fht": b"\x80\x20\x10A", "b.bin": b"\x10\x90\x30B", "terrain_data.fht": b"\x01\x02\x03D"}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tree_hashes(root):
    return {str(p.relative_to(root)): sha(p) for p in sorted(Path(root).rglob("*")) if p.is_file()}


class RealPackPipelineTest(unittest.TestCase):
    def test_synthetic_archives_real_pack_verification_and_corruption(self):
        # Exercise all production stages, replacing only neural inference.
        from test_extract_textures import dds, write_pair
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            inp, ws, model = root / 'input', root / 'output', root / 'model.pth'
            inp.mkdir(); model.write_bytes(b'synthetic model identity, never loaded')
            payload = b'\x00\xf8\xe0\x07\x00\x00\x00\x00' * 4
            write_pair(inp, 'etc', [('Artwork.dds_', dds(width=8, height=8, payload=payload))])
            write_pair(inp, 'model', [])
            write_pair(inp, 'mt', [])
            before = tree_hashes(inp)
            def infer(rgb):
                return rgb.resize((rgb.width * 4, rgb.height * 4), Image.Resampling.NEAREST)
            with mock.patch.object(upscale_pack, 'load_model', return_value=infer) as load, \
                    mock.patch.object(upscale_pack, 'set_cpu_limits', return_value=False):
                receipt = gop.run_pipeline(inp, ws, model, tile=8, pad=0)
                self.assertEqual(receipt['artwork_upscaled'], 1)
                self.assertEqual(receipt['count_preserved'], 0)
                self.assertEqual(json.loads((ws/'texture-packs/pack-index.json').read_text()), gop._index(True))
                packs = sorted((ws/'texture-packs').rglob('*.frp'))
                self.assertEqual(len(packs), 2)
                self.assertTrue(all(p.read_bytes().startswith(b'FTX4') for p in packs))
                hashes = {str(p): sha(p) for p in packs}
                gop.run_pipeline(inp, ws, model, tile=8, pad=0)
                self.assertEqual(load.call_count, 1)  # resume performed no inference
                self.assertEqual({str(p): sha(p) for p in packs}, hashes)
                packs[0].write_bytes(packs[0].read_bytes()[:-3])
                with self.assertRaises((gop.PipelineError, ValueError, RuntimeError)):
                    gop.run_pipeline(inp, ws, model, tile=8, pad=0)
                self.assertEqual(json.loads((ws/'texture-packs/pack-index.json').read_text()), gop._index(False))
                self.assertEqual(tree_hashes(inp), before)


class Fakes:
    """Stand-ins for extraction/classification/inference that check their own inputs."""

    def __init__(self):
        self.mode = "good"
        self.index_seen = []
        self.extract_calls = 0
        self.process_calls = 0
        self.process_kwargs = None
        self.last_counts = None
        self.verify_sha = None

    def extract(self, input_root, output):
        input_root, output = Path(input_root), Path(output)
        assert not gop._inside(output, input_root) and not gop._inside(input_root, output)
        self.extract_calls += 1
        index = output.parent / "texture-packs" / "pack-index.json"
        self.index_seen.append(json.loads(index.read_text()) if index.exists() else None)
        (output / "originals").mkdir(parents=True, exist_ok=True)
        textures = []
        for arc in sorted((input_root / "etc" / "model" / "mt").iterdir()):
            data = arc.read_bytes()
            tid = hashlib.sha256(arc.name.encode()).hexdigest()
            Image.new("RGBA", (4, 4), tuple(data[:3]) + (255,)).save(output / "originals" / f"{tid}.png")
            textures.append({"id": tid, "png": f"originals/{tid}.png", "archive": arc.name,
                             "payload": hashlib.sha256(b"payload:" + arc.name.encode()).hexdigest()})
        upscale_pack.atomic_json(output / "manifest.json", {"textures": textures})
        return {"textures": len(textures)}

    def classify(self, manifest):
        data = json.loads(Path(manifest).read_text())
        recs = [{"id": t["id"], "source_png": t["png"], "kind": "data" if "data" in t["archive"] else "color",
                 "format": "DXT5", "width": 4, "height": 4, "base_payload_sha256": t["payload"],
                 "aliases": [t["archive"]]} for t in data["textures"]]
        return {"textures": recs, "summary": {"total": len(recs)}}

    def process(self, manifest, output, model, **kwargs):
        self.process_calls += 1
        self.process_kwargs = kwargs
        manifest, output, pack_root = Path(manifest), Path(output), Path(kwargs["pack_root"])
        records = [r for r in json.loads(manifest.read_text())["textures"] if r["kind"] == "color"]
        if self.mode == "raise":
            raise RuntimeError("simulated inference failure")
        if self.mode == "failed":
            return {"complete": len(records) - 1, "failed": 1}
        if self.mode == "noop":
            return {"complete": len(records), "failed": 0}
        ledger_path = output / "ledger.json"
        ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {"items": {}}
        counts = {"complete": 0, "failed": 0, "skipped": 0}
        for rec in records:
            src_sha = sha(manifest.parent / rec["source_png"])
            entry = ledger["items"].get(rec["id"])
            if entry and entry["input_sha256"] == src_sha and all(
                    (pack_root / p["path"]).is_file() and sha(pack_root / p["path"]) == p["sha256"]
                    for p in entry["packs"].values()):
                counts["skipped"] += 1
                continue
            packs = {}
            for tier, scale in gop.TIERS.items():
                rel = f"{tier}/{pack_io.relative_name(rec)}"
                dest = pack_root / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(MAGIC + json.dumps({"id": rec["id"], "scale": scale, "src": src_sha}).encode())
                packs[tier] = {"path": rel, "sha256": sha(dest), "scale": scale}
            ledger["items"][rec["id"]] = {"status": "complete", "input_sha256": src_sha, "packs": packs}
            counts["complete"] += 1
        upscale_pack.atomic_json(ledger_path, ledger)
        self.last_counts = counts
        return counts

    def verify(self, path, item, scale):
        raw = Path(path).read_bytes()
        if not raw.startswith(MAGIC):
            raise ValueError("bad pack layout")
        meta = json.loads(raw[len(MAGIC):])
        if meta["id"] != item["id"] or meta["scale"] != scale:
            raise ValueError("pack does not belong to this item/scale")
        return {"sha256": self.verify_sha or sha(path), "path": str(path),
                "width": item["width"] * scale, "height": item["height"] * scale}


@unittest.skipIf(Image is None, "Pillow is required")
class OwnedTexturePipelineTest(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="owned-pack-")).resolve()
        self.addCleanup(shutil.rmtree, self.base, True)
        self.inp = self.make_input("input", ARCHIVES)
        self.model = self.base / "models" / "model.pth"
        self.model.parent.mkdir()
        self.model.write_bytes(b"trusted synthetic model")
        (self.base / "out").mkdir()
        self.sentinel = self.base / "out" / "keep.txt"
        self.sentinel.write_text("unrelated user file")
        self.ws = self.base / "out" / "ws"
        self.fakes = Fakes()
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        for target, name, fn in ((extract_textures, "extract", self.fakes.extract),
                                 (classify_textures, "classify_manifest", self.fakes.classify),
                                 (upscale_pack, "process_pack", self.fakes.process),
                                 (pack_io, "verify_pack", self.fakes.verify)):
            stack.enter_context(mock.patch.object(target, name, fn))
        self.source_before = tree_hashes(self.inp)
        self.model_before = sha(self.model)

    def make_input(self, name, archives):
        root = self.base / name
        (root / "etc" / "model" / "mt").mkdir(parents=True)
        for arc, data in archives.items():
            (root / "etc" / "model" / "mt" / arc).write_bytes(data)
        return root

    def run_ws(self, ws=None, **kwargs):
        return gop.run_pipeline(kwargs.pop("input_root", self.inp), ws or self.ws,
                                kwargs.pop("model", self.model), **kwargs)

    def cli_args(self, ws):
        return ["--input-root", str(self.inp), "--workspace", str(ws), "--model", str(self.model)]

    def index(self, ws):
        return json.loads((ws / "texture-packs" / "pack-index.json").read_text())

    def assert_index(self, ws, complete):
        self.assertEqual(self.index(ws), {"schema_version": 1, "tiers": {
            "medium": {"scale": 2, "path": "medium", "complete": complete},
            "high": {"scale": 4, "path": "high", "complete": complete}}})

    def assert_preserved(self):
        self.assertEqual(tree_hashes(self.inp), self.source_before)
        self.assertEqual(sha(self.model), self.model_before)
        self.assertEqual(self.sentinel.read_text(), "unrelated user file")

    def ledger(self, ws):
        return json.loads((ws / "batch" / "ledger.json").read_text())

    def first_pack(self, ws):
        ledger = self.ledger(ws)
        tid = sorted(ledger["items"])[0]
        return ledger, tid, ws / "texture-packs" / ledger["items"][tid]["packs"]["medium"]["path"]

    def make_dir_link(self, link, target):
        try:
            os.symlink(target, link, target_is_directory=True)
        except (OSError, NotImplementedError, AttributeError):
            try:
                import _winapi
                _winapi.CreateJunction(str(target), str(link))
            except Exception:
                return False
        self.addCleanup(self.remove_link, link)
        return True

    @staticmethod
    def remove_link(link):
        try:
            os.unlink(link)
        except OSError:
            with contextlib.suppress(OSError):
                os.rmdir(link)

    def test_cli_help_defaults_and_exit_codes(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as cm:
            gop.main(["--help"])
        self.assertEqual(cm.exception.code, 0)
        self.assertIn("--workspace", out.getvalue())
        self.assertNotIn("--limit", out.getvalue())
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
            gop.main(["--input-root", str(self.inp)])
        self.assertEqual(cm.exception.code, 2)
        args = gop.build_parser().parse_args(self.cli_args(self.ws))
        self.assertEqual((args.device, args.precision, args.tile, args.pad, args.threads),
                         ("cpu", "float32", 128, 32, 2))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = gop.main(self.cli_args(self.inp / "ws"))
        self.assertEqual(code, 1)
        self.assertIn("error:", err.getvalue())
        self.assertFalse((self.inp / "ws").exists())
        self.assert_preserved()

    def test_full_success_and_idempotent_resume(self):
        receipt = self.run_ws()
        self.assert_index(self.ws, True)
        self.assertEqual(self.fakes.index_seen, [gop._index(False)])
        self.assertEqual(receipt["status"], "completed")
        self.assertFalse(receipt["runtime_tested"])
        self.assertTrue(receipt["artwork_upscaled"] and receipt["count_preserved"])
        self.assertEqual(receipt["model_sha256"], self.model_before)
        self.assertEqual(receipt["classified_manifest_sha256"],
                         sha(self.ws / "catalog" / "manifest.classified.json"))
        self.assertIn("color subset", receipt["scope"])
        self.assertEqual(receipt["counts"]["color_selected"], 2)
        self.assertEqual(receipt["counts"]["by_kind"], {"color": 2, "data": 1})
        self.assertEqual(json.loads((self.ws / "receipt.json").read_text()), receipt)
        kw = self.fakes.process_kwargs
        self.assertEqual((kw["device"], kw["precision"], kw["tile"], kw["pad"], kw["threads"], kw["strength"]),
                         ("cpu", "float32", 128, 32, 2, 0.4))
        self.assertEqual(Path(kw["pack_root"]), self.ws / "texture-packs")
        classified = json.loads((self.ws / "catalog" / "manifest.classified.json").read_text())
        for rec in classified["textures"]:
            for tier in gop.TIERS:
                pack = self.ws / "texture-packs" / tier / str(pack_io.relative_name(rec))
                self.assertEqual(pack.is_file(), rec["kind"] == "color", rec["aliases"])
            self.assertTrue((self.ws / "catalog" / rec["source_png"]).is_file())
        for tier in gop.TIERS:
            self.assertEqual(len([p for p in (self.ws / "texture-packs" / tier).iterdir()]), 2)

        packs_before = tree_hashes(self.ws / "texture-packs" / "high")
        self.fakes.index_seen.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(gop.main(self.cli_args(self.ws)), 0)
        self.assertEqual(self.fakes.last_counts["skipped"], 2)
        self.assertFalse(any(t["complete"] for t in self.fakes.index_seen[0]["tiers"].values()))
        self.assert_index(self.ws, True)
        self.assertEqual(tree_hashes(self.ws / "texture-packs" / "high"), packs_before)
        self.assert_preserved()

    def test_refuses_mismatched_resume(self):
        self.run_ws()
        marker = (self.ws / gop.MARKER).read_bytes()
        other_input = self.make_input("input2", ARCHIVES)
        original_model = self.model.read_bytes()
        self.model.write_bytes(b"different model")
        with self.assertRaisesRegex(gop.PipelineError, "model"):
            self.run_ws()
        self.model.write_bytes(original_model)
        for kwargs in ({"tile": 64}, {"pad": 8}, {"device": "cuda"}, {"precision": "float16"}, {"threads": 4}):
            with self.subTest(kwargs), self.assertRaisesRegex(gop.PipelineError, "recipe|requires CUDA"):
                self.run_ws(**kwargs)
        with self.assertRaisesRegex(gop.PipelineError, "input root"):
            self.run_ws(input_root=other_input)
        self.assertEqual((self.ws / gop.MARKER).read_bytes(), marker)
        self.assertEqual(self.fakes.extract_calls, 1)
        self.assert_index(self.ws, True)
        self.assert_preserved()

    def test_rejects_unsafe_paths(self):
        mws = self.base / "mws"
        mws.mkdir()
        inner_model = mws / "m.pth"
        inner_model.write_bytes(b"model")
        cases = [("inside input", self.inp / "ws", self.model),
                 ("lexical inside input", self.inp / "etc" / ".." / "nested", self.model),
                 ("deep inside input", self.inp / "etc" / "model" / "mt", self.model),
                 ("contains input", self.base, self.model),
                 ("filesystem root", Path(self.base.anchor), self.model),
                 ("model inside workspace", mws, inner_model)]
        for name, ws, model in cases:
            with self.subTest(name), self.assertRaises(gop.PipelineError):
                self.run_ws(ws, model=model)
        cwd = os.getcwd()
        os.chdir(self.base)
        try:
            with self.assertRaises(gop.PipelineError):
                gop.run_pipeline("input", os.path.join("input", "rel-ws"), str(self.model))
        finally:
            os.chdir(cwd)
        self.assertEqual(self.fakes.extract_calls, 0)
        self.assertEqual(sorted(os.listdir(mws)), ["m.pth"])
        self.assertFalse(list(self.base.rglob(gop.MARKER)))
        self.assert_preserved()

    def test_rejects_unrelated_or_unrecognized_workspace(self):
        self.ws.mkdir()
        (self.ws / "notes.txt").write_text("mine")
        with self.assertRaisesRegex(gop.PipelineError, "not created by this tool"):
            self.run_ws()
        self.assertEqual(os.listdir(self.ws), ["notes.txt"])
        self.assertEqual((self.ws / "notes.txt").read_text(), "mine")
        empty = self.base / "out" / "empty"
        empty.mkdir()
        with self.assertRaises(gop.PipelineError):
            self.run_ws(empty)
        self.assertEqual(os.listdir(empty), [])
        forged = self.base / "out" / "forged"
        forged.mkdir()
        (forged / gop.MARKER).write_text(json.dumps({"schema": "something-else"}))
        with self.assertRaisesRegex(gop.PipelineError, "unrecognized"):
            self.run_ws(forged)
        valid = self.base / "out" / "valid"
        self.run_ws(valid)
        (valid / "extra.txt").write_text("user data")
        with self.assertRaisesRegex(gop.PipelineError, "unrelated"):
            self.run_ws(valid)
        self.assertEqual((valid / "extra.txt").read_text(), "user data")
        self.assertEqual(self.fakes.extract_calls, 1)
        self.assert_preserved()

    def test_rejects_linked_workspace_paths(self):
        real = self.base / "real"
        real.mkdir()
        if not self.make_dir_link(self.base / "link", real):
            self.skipTest("directory links are not supported here")
        with self.assertRaisesRegex(gop.PipelineError, "link"):
            self.run_ws(self.base / "link" / "ws")
        self.assertEqual(os.listdir(real), [])
        self.run_ws()
        moved = self.base / "batch-moved"
        os.rename(self.ws / "batch", moved)
        if not self.make_dir_link(self.ws / "batch", moved):
            self.skipTest("directory links are not supported here")
        ledger_before = sha(moved / "ledger.json")
        with self.assertRaisesRegex(gop.PipelineError, "link"):
            self.run_ws()
        self.assertEqual(sha(moved / "ledger.json"), ledger_before)
        self.assert_preserved()

    def test_corrupt_stale_or_missing_outputs_keep_index_disabled(self):
        def corrupt_pack(ws):
            path = self.first_pack(ws)[2]
            path.write_bytes(path.read_bytes() + b"x")

        def missing_pack(ws):
            self.first_pack(ws)[2].unlink()

        def tamper_ledger(ws):
            ledger, tid, _ = self.first_pack(ws)
            ledger["items"][tid]["packs"]["high"]["sha256"] = "0" * 64
            (ws / "batch" / "ledger.json").write_text(json.dumps(ledger))

        def wrong_path(ws):
            ledger, tid, path = self.first_pack(ws)
            shutil.copyfile(path, path.parent / "other.frp")
            ledger["items"][tid]["packs"]["medium"]["path"] = "medium/other.frp"
            (ws / "batch" / "ledger.json").write_text(json.dumps(ledger))

        def missing_ledger(ws):
            (ws / "batch" / "ledger.json").unlink()

        def incomplete_entry(ws):
            ledger, tid, _ = self.first_pack(ws)
            ledger["items"][tid]["status"] = "failed"
            (ws / "batch" / "ledger.json").write_text(json.dumps(ledger))

        def verifier_disagrees(ws):
            self.fakes.verify_sha = "f" * 64

        def stale_source(ws):
            (self.inp / "etc" / "model" / "mt" / "a.fht").write_bytes(b"\x00\xff\x00A")

        scenarios = [corrupt_pack, missing_pack, tamper_ledger, wrong_path, missing_ledger,
                     incomplete_entry, verifier_disagrees, stale_source]
        original = ARCHIVES["a.fht"]
        for mutate in scenarios:
            with self.subTest(mutate.__name__):
                ws = self.base / "out" / f"ws-{mutate.__name__}"
                self.fakes.mode, self.fakes.verify_sha = "good", None
                self.run_ws(ws)
                self.assert_index(ws, True)
                try:
                    mutate(ws)
                    self.fakes.mode = "noop"
                    with self.assertRaises(gop.PipelineError):
                        self.run_ws(ws)
                finally:
                    (self.inp / "etc" / "model" / "mt" / "a.fht").write_bytes(original)
                    self.fakes.verify_sha = None
                self.assert_index(ws, False)
                self.assertNotEqual(json.loads((ws / "receipt.json").read_text())["status"], "completed")
        self.assert_preserved()

    def test_zero_color_and_process_errors_keep_index_disabled(self):
        data_only = self.make_input("data-input", {"x_data.fht": b"\x01\x01\x01", "y_data.bin": b"\x02\x02\x02"})
        zero_ws = self.base / "out" / "zero"
        with self.assertRaisesRegex(gop.PipelineError, "no color"):
            self.run_ws(zero_ws, input_root=data_only)
        self.assertEqual(self.fakes.process_calls, 0)
        self.assert_index(zero_ws, False)

        self.fakes.mode = "failed"
        with self.assertRaisesRegex(gop.PipelineError, "failed"):
            self.run_ws()
        self.assert_index(self.ws, False)
        self.assertFalse((self.ws / "receipt.json").exists())

        self.fakes.mode = "raise"
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(gop.main(self.cli_args(self.ws)), 1)
        self.assertIn("simulated inference failure", err.getvalue())
        self.assert_index(self.ws, False)

        self.fakes.mode = "good"
        self.run_ws()
        self.assert_index(self.ws, True)
        self.fakes.mode = "failed"
        with self.assertRaises(gop.PipelineError):
            self.run_ws()
        self.assert_index(self.ws, False)
        self.assertEqual(json.loads((self.ws / "receipt.json").read_text())["status"], "invalidated")
        self.assert_preserved()


if __name__ == "__main__":
    unittest.main()
