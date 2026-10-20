"""Hand-built PSF fixtures for the independent read-only verifier."""
import importlib.util
from pathlib import Path
import struct
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "verify_gamedata_psf.py"
SPEC = importlib.util.spec_from_file_location("verify_gamedata_psf", SOURCE)
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


def fixture(entries=None):
    if entries is None:
        entries = [
            ("CATEGORY", 0x0204, b"GD\0"),
            ("TITLE_ID", 0x0204, b"BCUS98147\0"),
            ("TITLE", 0x0204, "Synthetic Caf\u00e9".encode("utf-8") + b"\0"),
            ("APP_VER", 0x0204, b"01.00\0"),
            ("PARENTAL_LEVEL", 0x0404, struct.pack("<I", 5)),
            ("TITLE_00", 0x0204, b"Localized synthetic\0"),
            ("UNKNOWN_VENDOR", 0x0004, b"\x00\xff\x7f"),
        ]
    keys = bytearray()
    data = bytearray()
    index = bytearray()
    for key, fmt, value in entries:
        key_offset = len(keys)
        keys.extend(key.encode("ascii") + b"\0")
        data_offset = len(data)
        capacity = len(value)
        data.extend(value)
        data.extend(b"\0" * ((-len(data)) % 4))
        index.extend(struct.pack("<HHIII", key_offset, fmt, len(value), capacity, data_offset))
    key_start = 20 + len(index)
    data_start = (key_start + len(keys) + 3) & ~3
    header = struct.pack("<4sIIII", b"\x00PSF", 0x101, key_start,
                         data_start, len(entries))
    return bytearray(header + index + keys + b"\0" * (data_start - key_start - len(keys)) + data)


class VerificationTests(unittest.TestCase):
    def check_bad(self, raw):
        with self.assertRaises(verifier.PSFError):
            verifier.verify_bytes(bytes(raw))

    def test_valid_fixture_preserves_unknown_binary(self):
        result = verifier.verify_bytes(bytes(fixture()), expect_title_id="BCUS98147",
                                       expect_title="Synthetic Caf\u00e9", expect_version="01.00")
        self.assertTrue(result["structure_passed"])
        self.assertEqual(result["fields"]["UNKNOWN_VENDOR"],
                         {"format": "binary", "hex": "00ff7f", "length": 3})
        self.assertEqual(result["fields"]["PARENTAL_LEVEL"], 5)

    def test_bad_key_offset(self):
        raw = fixture()
        struct.pack_into("<H", raw, 20, 0xffff)
        self.check_bad(raw)

    def test_length_exceeds_capacity(self):
        raw = fixture()
        struct.pack_into("<I", raw, 20 + 4, 99)
        self.check_bad(raw)

    def test_integer_capacity_must_be_four(self):
        raw = fixture()
        struct.pack_into("<I", raw, 20 + 4 * 16 + 8, 8)
        self.check_bad(raw)

    def test_duplicate_key(self):
        raw = fixture()
        # Entry 1 points to entry 0's key, triggering the duplicate check.
        struct.pack_into("<H", raw, 20 + 16, 0)
        self.check_bad(raw)

    def test_overlapping_data_capacity(self):
        raw = fixture()
        struct.pack_into("<I", raw, 20 + 16 + 12, 0)
        self.check_bad(raw)

    def test_missing_key_nul(self):
        raw = fixture()
        key_start = struct.unpack_from("<I", raw, 8)[0]
        raw[key_start + len("CATEGORY")] = ord("X")
        self.check_bad(raw)

    def test_missing_string_nul(self):
        raw = fixture()
        data_start = struct.unpack_from("<I", raw, 12)[0]
        raw[data_start + 2] = ord("X")
        self.check_bad(raw)

    def test_bad_table_boundary(self):
        raw = fixture()
        struct.pack_into("<I", raw, 8, 20)
        self.check_bad(raw)

    def test_bad_magic_and_version(self):
        raw = fixture()
        raw[0] = ord("X")
        self.check_bad(raw)
        raw = fixture()
        struct.pack_into("<I", raw, 4, 0x100)
        self.check_bad(raw)

    def test_expectation_mismatch(self):
        with self.assertRaises(verifier.PSFError):
            verifier.verify_bytes(bytes(fixture()), expect_title_id="OTHER0000")


if __name__ == "__main__":
    unittest.main()
