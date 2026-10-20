import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import upscale_pack
from upscale_pack import CudaGuardStop, dry_run, make_output, process_pack, safe_source, upscale_tiled


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs = self.root / "inputs"
        self.inputs.mkdir()
        self.output = self.root / "output"
        self.model = self.root / "model.pth"
        self.model.write_bytes(b"synthetic model identity")
        self.image = self.inputs / "sample.png"
        image = Image.new("RGBA", (5, 3))
        image.putdata([(x * 31, y * 61, (x + y) * 23, (x * 37 + y * 19) % 256)
                       for y in range(3) for x in range(5)])
        image.save(self.image)
        self.key = hashlib.sha256(b"texture").hexdigest()
        self.manifest = self.inputs / "manifest.json"
        self.entries = [{"id": self.key, "source_png": "sample.png", "width": 5,
                         "height": 3, "kind": "color", "aliases": []}]
        self.write_manifest()
        self.calls = 0

    def write_manifest(self):
        self.manifest.write_text(json.dumps({"textures": self.entries}), encoding="utf-8")

    def infer(self, image):
        self.calls += 1
        return image.resize((image.width * 4, image.height * 4), Image.Resampling.NEAREST)

    def run_pack(self, **kwargs):
        return process_pack(self.manifest, self.output, self.model, infer_callback=self.infer,
                            tile=2, pad=1, **kwargs)

    def test_tiled_odd_dimensions_matches_whole_nearest(self):
        with Image.open(self.image) as source:
            rgb = source.convert("RGB")
        tiled = upscale_tiled(rgb, self.infer, 2, 1)
        expected = rgb.resize((20, 12), Image.Resampling.NEAREST)
        self.assertEqual(tiled.tobytes(), expected.tobytes())
        self.assertEqual(self.calls, 6)

    def test_blend_and_alpha(self):
        with Image.open(self.image) as source:
            actual = make_output(source, self.infer, 2, 1, 0.4)
            expected_alpha = source.getchannel("A").resize((20, 12), Image.Resampling.BILINEAR)
            ordinary = source.convert("RGB").resize((20, 12), Image.Resampling.BICUBIC)
            predicted = source.convert("RGB").resize((20, 12), Image.Resampling.NEAREST)
        self.assertEqual(actual.size, (20, 12))
        self.assertEqual(actual.getchannel("A").tobytes(), expected_alpha.tobytes())
        self.assertEqual(actual.convert("RGB").tobytes(), Image.blend(ordinary, predicted, 0.4).tobytes())

    def test_resume_tamper_input_model_and_parameters(self):
        self.assertEqual(self.run_pack()["complete"], 1)
        initial = self.calls
        self.assertEqual(self.run_pack()["resumed"], 1)
        self.assertEqual(self.calls, initial)
        destination = self.output / "textures" / (self.key + ".png")
        destination.write_bytes(b"corrupt")
        self.assertEqual(self.run_pack()["complete"], 1)
        self.assertGreater(self.calls, initial)
        initial = self.calls
        with Image.open(self.image) as original:
            altered = original.copy()
        altered.putpixel((0, 0), (1, 2, 3, 4))
        altered.save(self.image)
        self.assertEqual(self.run_pack()["complete"], 1)
        self.assertGreater(self.calls, initial)
        self.model.write_bytes(b"changed model identity")
        self.assertEqual(self.run_pack()["complete"], 1)
        ledger = json.loads((self.output / "ledger.json").read_text(encoding="utf-8"))
        self.assertEqual(ledger["items"][self.key]["status"], "complete")
        self.assertEqual(ledger["items"][self.key]["dimensions"], [20, 12])

    def test_data_and_review_held(self):
        second = dict(self.entries[0], id=hashlib.sha256(b"second").hexdigest(), kind="data")
        third = dict(self.entries[0], id=hashlib.sha256(b"third").hexdigest(), kind="review")
        self.entries.extend([second, third])
        self.write_manifest()
        counts = self.run_pack()
        self.assertEqual(counts["held"], 2)
        self.assertEqual(counts["failed"], 0)
        self.assertEqual(len(list((self.output / "textures").glob("*.png"))), 1)
        ledger = json.loads((self.output / "ledger.json").read_text(encoding="utf-8"))
        self.assertEqual(ledger["items"][second["id"]]["status"], "held")
        self.assertEqual(ledger["items"][third["id"]]["status"], "held")

    def test_path_escape_and_output_isolation(self):
        with self.assertRaises(ValueError):
            safe_source(self.inputs, "../model.pth")
        self.entries[0]["source_png"] = "../model.pth"
        self.write_manifest()
        with self.assertRaises(ValueError):
            self.run_pack()
        self.entries[0]["source_png"] = "sample.png"
        self.write_manifest()
        with self.assertRaises(ValueError):
            process_pack(self.manifest, self.inputs / "results", self.model, infer_callback=self.infer)

    def test_conflicting_duplicate_and_limit(self):
        conflict = dict(self.entries[0], kind="review")
        self.entries.append(conflict)
        self.write_manifest()
        with self.assertRaises(ValueError):
            self.run_pack()
        self.entries[-1] = dict(self.entries[0])
        self.write_manifest()
        self.assertEqual(self.run_pack(limit=0)["complete"], 0)
        self.assertEqual(self.run_pack(limit=1)["complete"], 1)

    def test_item_failure_keeps_checkpoint(self):
        def wrong_size(_image):
            return Image.new("RGB", (1, 1))
        counts = process_pack(self.manifest, self.output, self.model, infer_callback=wrong_size)
        self.assertEqual(counts["failed"], 1)
        ledger = json.loads((self.output / "ledger.json").read_text(encoding="utf-8"))
        self.assertEqual(ledger["items"][self.key]["status"], "failed")
        self.assertEqual(self.run_pack()["complete"], 1)

    def test_dry_run_counts_without_writes(self):
        summary = dry_run(self.manifest, self.output, self.model, tile=2, pad=1)
        self.assertEqual(summary["source_pixels"], 15)
        self.assertEqual(summary["output_rgba_bytes"], 960)
        self.assertEqual(summary["resume_ready"], 0)
        self.assertFalse(self.output.exists())
        self.run_pack()
        self.assertEqual(dry_run(self.manifest, self.output, self.model, tile=2, pad=1)["resume_ready"], 1)

    def test_cuda_guard_runs_for_every_tile_and_device_binds_resume(self):
        self.assertEqual(self.run_pack()["complete"], 1)
        self.assertEqual(dry_run(self.manifest, self.output, self.model, tile=2, pad=1, device="cuda")["resume_ready"], 0)
        checks = []
        counts = self.run_pack(device="cuda", guard_callback=lambda: checks.append(True))
        self.assertEqual(counts["complete"], 1)
        self.assertEqual(len(checks), 7)  # Six tiles plus pre-publish check.
        self.assertEqual(self.run_pack(device="cuda", guard_callback=lambda: checks.append(True))["resumed"], 1)
        self.assertEqual(len(checks), 7)

    def test_guard_stop_preserves_completed_ledger(self):
        other = dict(self.entries[0], id=hashlib.sha256(b"other").hexdigest())
        self.entries.append(other)
        self.write_manifest()
        calls = []

        def guard():
            calls.append(1)
            if len(calls) == 8:
                raise CudaGuardStop("game started")

        with self.assertRaises(CudaGuardStop):
            self.run_pack(device="cuda", guard_callback=guard)
        ledger = json.loads((self.output / "ledger.json").read_text(encoding="utf-8"))
        self.assertEqual(ledger["items"][self.key]["status"], "complete")
        self.assertNotIn(other["id"], ledger["items"])
        self.assertEqual(list((self.output / "textures").glob("*.png")),
                         [self.output / "textures" / (self.key + ".png")])

    def test_windows_process_query_fails_closed(self):
        class Completed:
            returncode = 0
            stdout = '"FolkloreBoot.exe","123","Console","1","123 K"\n'

        with patch.object(upscale_pack.os, "name", "nt"), patch.object(upscale_pack.subprocess, "run", return_value=Completed()):
            with self.assertRaisesRegex(CudaGuardStop, "folkloreboot"):
                upscale_pack.guard_cuda_idle()
        Completed.stdout = "unexpected output"
        with patch.object(upscale_pack.os, "name", "nt"), patch.object(upscale_pack.subprocess, "run", return_value=Completed()):
            with self.assertRaisesRegex(CudaGuardStop, "query failed"):
                upscale_pack.guard_cuda_idle()

    def test_direct_pack_resume_and_corruption_repair(self):
        base = hashlib.sha256(b"compressed base").hexdigest()
        self.entries[0].update(format="DXT1", base_payload_sha256=base)
        self.write_manifest()
        pack_root = self.root / "packs"
        self.assertEqual(self.run_pack(pack_root=pack_root)["complete"], 1)
        self.assertFalse((self.output / "textures").exists())
        ledger = json.loads((self.output / "ledger.json").read_text(encoding="utf-8"))
        record = ledger["items"][self.key]
        self.assertEqual(set(record["packs"]), {"medium", "high"})
        self.assertEqual(record["packs"]["high"]["width"], 20)
        self.assertEqual(record["packs"]["medium"]["width"], 10)
        calls = self.calls
        self.assertEqual(self.run_pack(pack_root=pack_root)["resumed"], 1)
        self.assertEqual(self.calls, calls)
        self.assertEqual(upscale_pack.dry_run(self.manifest, self.output, self.model, tile=2, pad=1,
                                              pack_root=pack_root)["resume_ready"], 1)
        # A damaged existing pack must never be silently overwritten.
        high = Path(record["packs"]["high"]["path"])
        high.write_bytes(b"damage")
        self.assertEqual(self.run_pack(pack_root=pack_root)["failed"], 1)
        self.assertEqual(high.read_bytes(), b"damage")

    def test_direct_pack_interrupt_preserves_completed_item(self):
        base = hashlib.sha256(b"compressed base").hexdigest()
        self.entries[0].update(format="BC3", base_payload_sha256=base)
        second = dict(self.entries[0], id=hashlib.sha256(b"second pack").hexdigest(),
                      base_payload_sha256=hashlib.sha256(b"second base").hexdigest())
        self.entries.append(second)
        self.write_manifest()
        pack_root = self.root / "packs"
        calls = []

        def guard():
            calls.append(1)
            if len(calls) == 8:
                raise CudaGuardStop("game started")

        with self.assertRaises(CudaGuardStop):
            self.run_pack(device="cuda", pack_root=pack_root, guard_callback=guard)
        ledger = json.loads((self.output / "ledger.json").read_text(encoding="utf-8"))
        self.assertEqual(ledger["items"][self.key]["status"], "complete")
        self.assertNotIn(second["id"], ledger["items"])
        self.assertEqual(len(list(pack_root.rglob("*.frp"))), 2)

    def test_precision_requires_cuda_and_binds_resume(self):
        with self.assertRaisesRegex(ValueError, "float16 requires CUDA"):
            self.run_pack(precision="float16")
        with self.assertRaisesRegex(ValueError, "float16 requires CUDA"):
            upscale_pack.dry_run(self.manifest, self.output, self.model, precision="float16")
        checks = []
        self.assertEqual(self.run_pack(device="cuda", precision="float32",
                                       guard_callback=lambda: checks.append(1))["complete"], 1)
        before = self.calls
        self.assertEqual(self.run_pack(device="cuda", precision="float32",
                                       guard_callback=lambda: checks.append(1))["resumed"], 1)
        self.assertEqual(self.calls, before)
        self.assertEqual(upscale_pack.dry_run(self.manifest, self.output, self.model,
                                              device="cuda", precision="float16", tile=2, pad=1)["resume_ready"], 0)
        self.assertEqual(self.run_pack(device="cuda", precision="float16",
                                       guard_callback=lambda: checks.append(1))["complete"], 1)
        self.assertGreater(self.calls, before)
        ledger = json.loads((self.output / "ledger.json").read_text(encoding="utf-8"))
        self.assertEqual(ledger["items"][self.key]["parameters"]["precision"], "float16")


if __name__ == "__main__":
    unittest.main()
