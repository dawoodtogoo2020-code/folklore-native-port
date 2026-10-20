import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from classify_textures import classify_manifest, make_contact_sheets


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class ClassificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs = self.root / "inputs"
        self.inputs.mkdir()
        self.manifest = self.inputs / "manifest.json"
        self.entries = []

    def add(self, name, alias, *, width=16, height=16, payload=None):
        path = self.inputs / f"{name}.png"
        Image.new("RGBA", (width, height), "#326496").save(path)
        entry = {"id": digest(name), "source_png": path.name, "width": width, "height": height,
                 "format": "BC3", "aliases": [alias]}
        if payload:
            entry["base_payload_sha256"] = payload
        self.entries.append(entry)
        return entry

    def classify(self):
        self.manifest.write_text(json.dumps({"textures": self.entries}), encoding="utf-8")
        return classify_manifest(self.manifest)

    def test_normal_spec_tone_lightmap_and_tiny(self):
        self.add("normal", {"archive": "mt", "entry": "a", "child_name": "Wall_N.dds"})
        self.add("spec", {"archive": "mt", "entry": "a", "child_name": "WallS.dds"})
        self.add("tone", {"archive": "mt", "entry": "a", "child_name": "tone_map.dds"})
        self.add("camel", {"archive": "mt", "entry": "a", "child_name": "WallNormal.dds"})
        self.add("light", {"archive": "mt", "entry": "a", "child_name": "LM103.dds"})
        self.add("tiny", {"archive": "mt", "entry": "a", "child_name": "Canvas.dds"}, width=4)
        result = self.classify()
        self.assertEqual(result["summary"]["counts"]["data"], 6)
        reasons = [entry["classification_reason"] for entry in result["textures"]]
        self.assertTrue(any("suffix heuristic" in reason for reason in reasons))
        self.assertTrue(any("LM lightmap" in reason for reason in reasons))

    def test_exact_suffix_case_and_ordinary_color(self):
        self.add("ordinary", {"archive": "menu.arc", "entry": "title.dds_", "child_name": ""})
        self.add("model", {"archive": "model.arc", "entry": "a", "child_name": "StoneWall.dds"})
        self.add("lower", {"archive": "mt.arc", "entry": "a", "child_name": "cairn.dds"})
        result = self.classify()
        self.assertEqual(result["summary"]["counts"]["color"], 3)
        self.assertTrue(all(entry["classification_inferred"] for entry in result["textures"]))

    def test_unknown_and_vfx_review(self):
        self.add("unknown", {"archive": "packed.arc", "entry": "1234567890abcdef", "child_name": ""})
        self.add("vfx", {"archive": "vfx.arc", "entry": "sparks", "child_name": "Smoke.dds"})
        result = self.classify()
        self.assertEqual(result["summary"]["counts"]["review"], 2)
        self.assertEqual(len(result["review_items"]), 2)

    def test_payload_collision_consolidates_aliases_and_ids(self):
        payload = digest("same base pixels")
        first = self.add("first", {"archive": "mt", "entry": "x", "child_name": "Rock.dds"}, payload=payload)
        second = self.add("second", {"archive": "model", "entry": "x", "child_name": "Rock_N.dds"}, payload=payload)
        result = self.classify()
        self.assertEqual(result["summary"]["input_entries"], 2)
        self.assertEqual(result["summary"]["consolidated_entries"], 1)
        item = result["textures"][0]
        self.assertEqual(item["kind"], "data")
        self.assertEqual(item["original_ids"], sorted([first["id"], second["id"]]))
        self.assertEqual(len(item["aliases"]), 2)
        self.assertEqual(len(item["id"]), 64)
        self.assertEqual(self.classify()["textures"][0]["id"], item["id"])

    def test_different_dimensions_do_not_consolidate(self):
        payload = digest("same")
        self.add("one", {"child_name": "A.dds"}, payload=payload)
        self.add("two", {"child_name": "A.dds"}, width=32, payload=payload)
        self.assertEqual(self.classify()["summary"]["consolidated_entries"], 2)

    def test_path_escape_and_contact_sheet(self):
        self.add("art", {"entry": "art.dds_", "child_name": ""})
        result = self.classify()
        made = make_contact_sheets(result, self.manifest, self.root / "sheets")
        self.assertEqual(len(made), 1)
        with Image.open(made[0]) as image:
            self.assertEqual(image.size, (800, 140))
        with self.assertRaises(ValueError):
            make_contact_sheets(result, self.manifest, self.inputs / "sheets")
        self.entries[0]["source_png"] = "../outside.png"
        with self.assertRaises(ValueError):
            self.classify()


if __name__ == "__main__":
    unittest.main()
