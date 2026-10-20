"""Synthetic format and safety checks for extract_textures."""

import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

import extract_textures as extractor

from extract_textures import FormatError, extract, parse_dds, read_index


def dds(fourcc=b"DXT1", width=4, height=4, mips=1, payload=None, header_size=124):
    header = bytearray(128)
    header[:4] = b"DDS "
    struct.pack_into("<7I", header, 4, header_size, 0x21007, height, width, 8, 0, mips)
    struct.pack_into("<2I", header, 76, 32, 4)
    header[84:88] = fourcc
    struct.pack_into("<I", header, 108, 0x1000)
    if payload is None:
        payload = b"\x00" * (8 if fourcc == b"DXT1" else 16)
    return bytes(header) + payload


def write_pair(root, stem, entries):
    index = bytearray()
    container = bytearray()
    for name, data in entries:
        index.extend(name.encode("ascii").ljust(32, b"\0"))
        index.extend(struct.pack(">II", len(data), len(container)))
        container.extend(data)
    (root / f"{stem}.fht_").write_bytes(index)
    (root / f"{stem}.bin_").write_bytes(container)


def bi5(children):
    n = len(children)
    offset_table = 48
    names_table = offset_table + 4 * (n + 1)
    data_start = names_table + 24 * n
    data = bytearray(data_start)
    positions = [data_start]
    for name, content in children:
        data.extend(content)
        positions.append(len(data))
    struct.pack_into(">5I", data, 0, len(data), n, offset_table, names_table, data_start)
    struct.pack_into(f">{n + 1}I", data, offset_table, *positions)
    for i, (name, _) in enumerate(children):
        data[names_table + 24 * i:names_table + 24 * (i + 1)] = name.encode("ascii").ljust(24, b"\0")
    return bytes(data), positions


class ExtractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "input"
        self.out = Path(self.temp.name) / "output"
        self.root.mkdir()

    def test_valid_duplicate_and_restart(self):
        texture = dds()
        write_pair(self.root, "etc", [("one.dds_", texture), ("two.vtg_", b"abc" + texture + b"padding"), ("sound.at3_", texture)])
        result = extract(self.root, self.out)
        self.assertEqual(len(result["textures"]), 1)
        item = result["textures"][0]
        self.assertEqual(len(item["aliases"]), 2)
        self.assertEqual(item["base_payload_bytes"], 8)
        self.assertEqual(item["base_payload_sha256"], hashlib.sha256(b"\x00" * 8).hexdigest())
        self.assertEqual(result["skipped_suffix_counts"], {".at3_": 1})
        self.assertEqual(result["scan_counts"]["scanned_entries"], 2)
        png = self.out / item["source_png"]
        self.assertTrue(png.is_file())
        self.assertEqual(result, extract(self.root, self.out))
        self.assertEqual(json.loads((self.out / "manifest.json").read_text()), result)

    def test_big_endian_header_and_stored_total(self):
        texture = dds(header_size=136)
        swapped = b"".join(texture[i:i + 4][::-1] for i in range(0, 128, 4)) + texture[128:]
        normal = parse_dds(texture, 0, len(texture))[0]
        self.assertEqual(parse_dds(swapped, 0, len(swapped))[0], normal)
        write_pair(self.root, "mt", [("swapped.dds_", swapped)])
        result = extract(self.root, self.out)
        self.assertEqual(len(result["textures"]), 1)

    def test_bad_index_bounds_and_name(self):
        write_pair(self.root, "etc", [("ok.dds_", dds())])
        index = self.root / "etc.fht_"
        index.write_bytes(b"bad.dds_".ljust(32, b"\0") + struct.pack(">II", 10000, 0))
        with self.assertRaisesRegex(FormatError, "outside container"):
            read_index(index, (self.root / "etc.bin_").stat().st_size)
        self.assertEqual(len(extract(self.root, self.out)["holds"]), 3)  # Invalid pair plus two absent pairs.
        index.write_bytes(b"\xff.dds_".ljust(32, b"\0") + struct.pack(">II", 1, 0))
        with self.assertRaisesRegex(FormatError, "ASCII"):
            read_index(index, 100)

    def test_truncated_and_unsupported(self):
        write_pair(self.root, "model", [("short.dds_", dds()[:-1]), ("other.dds_", dds(fourcc=b"DX10"))])
        result = extract(self.root, self.out)
        self.assertEqual(len(result["textures"]), 0)
        self.assertEqual(len(result["rejections"]), 2)
        self.assertIn("payload truncated", result["rejections"][0]["reason"])
        self.assertIn("compression unsupported", result["rejections"][1]["reason"])

    def test_bi5_names_zero_child_and_malicious_names(self):
        blob, offsets = bi5([("empty", b""), ("../unsafe", b"XYZ" + dds()), ("other", b"no texture")])
        write_pair(self.root, "etc", [("bundle.bi5_", blob), ("nothing.bi5_", bi5([])[0])])
        result = extract(self.root, self.out)
        self.assertEqual(len(result["textures"]), 1)
        alias = result["textures"][0]["aliases"][0]
        self.assertEqual(alias["child_name"], "../unsafe")
        self.assertEqual(alias["dds_offset"], offsets[1] + 3)
        self.assertEqual(list((self.out / "originals").iterdir())[0].name, result["textures"][0]["id"] + ".png")

    def test_no_texture_and_separate_roots(self):
        write_pair(self.root, "etc", [("empty.vtg_", b"zero"), ("music.ogg_", b"DDS ")])
        self.assertEqual(extract(self.root, self.out)["textures"], [])
        with self.assertRaisesRegex(FormatError, "separate"):
            extract(self.root, self.root / "inside")

    def test_cubemap_hold_and_unowned_png(self):
        texture = bytearray(dds())
        struct.pack_into("<I", texture, 112, 0x200)
        write_pair(self.root, "etc", [("cube.dds_", bytes(texture))])
        result = extract(self.root, self.out)
        self.assertIn("cubemap", result["rejections"][0]["reason"])
        write_pair(self.root, "etc", [("ordinary.dds_", dds())])
        digest = hashlib.sha256(dds()).hexdigest()
        (self.out / "originals" / f"{digest}.png").write_bytes(b"unowned")
        result = extract(self.root, self.out)
        self.assertIn("pixel mismatch", result["rejections"][0]["reason"])

    def test_adopt_only_identical_pixels_without_manifest(self):
        texture = dds()
        write_pair(self.root, "etc", [("one.dds_", texture)])
        first = extract(self.root, self.out)
        path = self.out / first["textures"][0]["source_png"]
        (self.out / "manifest.json").unlink()
        adopted = extract(self.root, self.out)
        self.assertEqual(adopted["scan_counts"]["adopted_pngs"], 1)
        self.assertEqual(len(adopted["textures"]), 1)
        self.assertEqual(adopted["textures"][0]["png_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_different_rgb_same_alpha_is_not_adopted(self):
        write_pair(self.root, "etc", [("one.dds_", dds())])
        first = extract(self.root, self.out)
        path = self.out / first["textures"][0]["source_png"]
        (self.out / "manifest.json").unlink()
        with Image.open(path) as old:
            changed = old.convert("RGBA")
        pixel = changed.getpixel((0, 0))
        changed.putpixel((0, 0), ((pixel[0] + 1) % 256, pixel[1], pixel[2], pixel[3]))
        changed.save(path)
        result = extract(self.root, self.out)
        self.assertEqual(result["textures"], [])
        self.assertIn("pixel mismatch", result["rejections"][0]["reason"])

    def test_partial_checkpoint_resumes(self):
        write_pair(self.root, "etc", [("one.dds_", dds()), ("two.vtg_", b"x" + dds())])
        real = extractor._checkpoint
        def interrupt(path, manifest):
            real(path, manifest)
            if manifest["progress"] == {"archive_index": 0, "next_entry": 1}:
                raise RuntimeError("synthetic interruption")
        with patch.object(extractor, "CHECKPOINT_EVERY", 1), patch.object(extractor, "_checkpoint", interrupt):
            with self.assertRaisesRegex(RuntimeError, "interruption"):
                extract(self.root, self.out)
        partial = json.loads((self.out / "manifest.json").read_text())
        self.assertFalse(partial["complete"])
        with patch.object(extractor, "CHECKPOINT_EVERY", 1):
            result = extract(self.root, self.out)
        self.assertTrue(result["complete"])
        self.assertEqual(result["scan_counts"]["scanned_entries"], 2)
        self.assertEqual(len(result["textures"][0]["aliases"]), 2)

    def test_direct_png_and_unsupported_inventory(self):
        from io import BytesIO
        buffer = BytesIO()
        Image.new("RGBA", (2, 3), (10, 20, 30, 255)).save(buffer, format="PNG")
        write_pair(self.root, "etc", [("cover.PNG", buffer.getvalue()),
                                       ("nested.bin_", b"LNK\0"),
                                       ("sound.sgd_", b"MYPA")])
        result = extract(self.root, self.out)
        self.assertEqual(len(result["textures"]), 1)
        self.assertEqual(result["textures"][0]["format"], "PNG")
        self.assertIsNone(result["textures"][0]["base_payload_sha256"])
        self.assertEqual(result["unsupported_formats"][".bin_"]["count"], 1)
        self.assertEqual(result["unsupported_formats"][".sgd_"]["count"], 1)


if __name__ == "__main__":
    unittest.main()
