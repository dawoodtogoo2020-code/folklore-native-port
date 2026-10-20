import hashlib
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import zlib

from PIL import Image

import pack_io


class PackIOTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "packs"
        self.item = {"base_payload_sha256": hashlib.sha256(b"compressed base").hexdigest(),
                     "format": "DXT5", "width": 3, "height": 2}
        self.original = Image.new("RGBA", (3, 2))
        self.original.putdata([(x * 39, y * 65, (x + y) * 24, x * 37 + y * 19)
                               for y in range(2) for x in range(3)])
        self.high = self.original.resize((12, 8), Image.Resampling.BICUBIC)
        self.high.putalpha(self.original.getchannel("A").resize((12, 8), Image.Resampling.BILINEAR))

    def test_exact_header_mips_and_alpha(self):
        metadata = pack_io.build_pair(self.original, self.high, self.item, self.root)
        for tier, scale in (("medium", 2), ("high", 4)):
            with self.subTest(tier=tier):
                path = Path(metadata[tier]["path"])
                blob = path.read_bytes()
                unpacked = pack_io.HEADER.unpack(blob[:104])
                sizes = pack_io.mip_sizes(3 * scale, 2 * scale)
                self.assertEqual(unpacked[:8], (b"FTX4", 2, 3, 3, 2, 3 * scale, 2 * scale, len(sizes)))
                self.assertEqual(unpacked[8], bytes.fromhex(self.item["base_payload_sha256"]))
                body = zlib.decompress(blob[104:])
                self.assertEqual(len(body), unpacked[9])
                self.assertEqual(hashlib.sha256(body).digest(), unpacked[10])
                first_size = sizes[0][0] * sizes[0][1] * 4
                base = Image.frombytes("RGBA", sizes[0], body[:first_size])
                expected_alpha = self.original.getchannel("A").resize(base.size, Image.Resampling.BILINEAR)
                self.assertEqual(base.getchannel("A").tobytes(), expected_alpha.tobytes())
                if tier == "high":
                    self.assertEqual(base.tobytes(), self.high.tobytes())
                else:
                    rgb = self.high.convert("RGB").resize(base.size, Image.Resampling.LANCZOS)
                    self.assertEqual(base.convert("RGB").tobytes(), rgb.tobytes())
                self.assertEqual(pack_io.verify_pack(path, self.item, scale)["sha256"],
                                 hashlib.sha256(blob).hexdigest())

    def test_corruption_truncation_trailing_and_mismatched_overwrite(self):
        metadata = pack_io.build_pair(self.original, self.high, self.item, self.root)
        path = Path(metadata["medium"]["path"])
        original = path.read_bytes()
        for damaged in (original[:-4], original + b"extra", original[:104] + b"broken"):
            path.write_bytes(damaged)
            with self.assertRaises(ValueError):
                pack_io.verify_pack(path, self.item, 2)
        path.write_bytes(original)
        changed = self.high.copy()
        changed.putpixel((0, 0), (200, 20, 30, changed.getpixel((0, 0))[3]))
        with self.assertRaisesRegex(ValueError, "differs"):
            pack_io.build_pair(self.original, changed, self.item, self.root)
        self.assertEqual(path.read_bytes(), original)

    def test_rejects_unsupported_format_bounds_and_unsafe_root(self):
        with self.assertRaisesRegex(ValueError, "only DXT"):
            pack_io.build_pair(self.original, self.high, {**self.item, "format": "R8"}, self.root)
        with self.assertRaisesRegex(ValueError, "4096"):
            pack_io.expected_layout({**self.item, "width": 4097}, 4)
        with self.assertRaisesRegex(ValueError, "traversal"):
            pack_io.safe_root(self.root / ".." / "other")
        self.root.mkdir()
        link = self.root.parent / "link"
        try:
            link.symlink_to(self.root, target_is_directory=True)
        except (OSError, NotImplementedError):
            pass
        else:
            with self.assertRaisesRegex(ValueError, "link"):
                pack_io.safe_root(link)

    def test_low_space_prevents_write(self):
        self.root.mkdir()
        fake = type("Disk", (), {"free": pack_io.RESERVE})()
        with patch.object(pack_io.shutil, "disk_usage", return_value=fake):
            with self.assertRaisesRegex(OSError, "free bytes"):
                pack_io.build_pair(self.original, self.high, self.item, self.root)
        self.assertEqual(list(self.root.rglob("*.frp")), [])


if __name__ == "__main__":
    unittest.main()
