import importlib.util
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import dds_headers as dds


def fixture():
    h = bytearray(128)
    h[:4] = b"DDS "
    struct.pack_into("<I", h, 4, 124)
    struct.pack_into("<6I", h, 8, 0x81007, 128, 256, 32768, 0, 0)
    struct.pack_into("<II", h, 76, 32, 4)
    h[84:88] = b"DXT5"
    return bytes(h)


class DDSHeaderTests(unittest.TestCase):
    def test_standard_and_word_swapped_metadata_agree(self):
        header = fixture()
        swapped = b"".join(header[i:i + 4][::-1] for i in range(0, 128, 4))
        a, b = dds.parse_header(header, 32896), dds.parse_header(swapped, 32896)
        self.assertTrue(b.pop("header_word_swap"))
        self.assertFalse(a.pop("header_word_swap"))
        self.assertEqual(a, b)
        self.assertTrue(a["payload_size_matches"])
        self.assertFalse(a["payload_layout_or_decode_validated"])

    def test_mip_sizes_and_mismatch(self):
        h = bytearray(fixture())
        struct.pack_into("<I", h, 28, 2)
        self.assertEqual(dds.parse_header(h, 128 + 32768 + 8192)["expected_2d_bcn_bytes"], 40960)
        self.assertFalse(dds.parse_header(h, 129)["payload_size_matches"])

    def test_truncation_bad_header_and_mip_bounds(self):
        for size in (0, 64, 127):
            with self.assertRaises(ValueError):
                dds.parse_header(fixture()[:size], 32896)
        for offset, value in ((4, 0), (76, 0), (16, 0), (28, 16)):
            h = bytearray(fixture())
            struct.pack_into("<I", h, offset, value)
            with self.assertRaises(ValueError):
                dds.parse_header(h, 32896)

    def test_cubemap_and_unknown_format_not_validated_by_size(self):
        h = bytearray(fixture())
        struct.pack_into("<I", h, 112, 0x200)
        self.assertIsNone(dds.parse_header(h, 32896)["payload_size_matches"])
        h[84:88] = b"DX10"
        self.assertIsNone(dds.parse_header(h, 32896)["expected_2d_bcn_bytes"])
