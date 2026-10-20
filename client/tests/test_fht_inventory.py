import contextlib
import importlib.util
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("fht_inventory", Path(__file__).parents[1] / "scripts" / "fht_inventory.py")
fht = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fht)


def record(name=b"folder/a.bi5_", size=16, offset=0, endian=">"):
    return name.ljust(32, b"\0") + struct.pack(endian + "II", size, offset)


class InventoryTests(unittest.TestCase):
    def test_contiguous_layout_and_suffix_only(self):
        result = fht.parse_index(record() + record(b"b.dds_", 8, 16), 24)
        self.assertTrue(result["summary"]["complete_contiguous_coverage"])
        self.assertEqual(result["entries"][1]["offset"], 16)
        self.assertEqual(result["entries"][1]["type_hint"], ".dds_")
        self.assertIn("unverified", result["profile"]["type_hints"])

    def test_gaps_and_unsorted_ranges_are_explicit(self):
        result = fht.parse_index(record(b"b", 4, 12) + record(b"a", 4, 0), 20)
        self.assertEqual(result["summary"]["gap_bytes"], 8)
        self.assertEqual(result["summary"]["trailing_bytes"], 4)
        self.assertFalse(result["summary"]["complete_contiguous_coverage"])
        self.assertFalse(result["summary"]["record_order_matches_offset_order"])

    def test_invalid_lengths(self):
        for value in (b"", b"x", record() + b"x", b"x" * (fht.MAX_INDEX_BYTES + 1)):
            with self.subTest(length=len(value)), self.assertRaises(fht.FormatError):
                fht.parse_index(value, 16)

    def test_bad_names(self):
        for value in (b"\0" * 32, b"a\0b".ljust(32, b"\0"), b"x" * 32,
                      b"a\nb".ljust(32, b"\0"), b"\xff".ljust(32, b"\0")):
            with self.subTest(value=value), self.assertRaises(fht.FormatError):
                fht.parse_index(value + struct.pack(">II", 16, 0), 16)

    def test_duplicate_names(self):
        with self.assertRaises(fht.FormatError):
            fht.parse_index(record() + record(offset=16), 32)

    def test_bounds_overlap_and_zero(self):
        for data, size in ((record(), 15), (record(size=0), 16),
                           (record() + record(b"b", 8, 15), 32),
                           (record(size=0xffffffff, offset=0xffffffff), 0xffffffff),
                           (record(endian="<"), 16)):
            with self.subTest(size=size), self.assertRaises(fht.FormatError):
                fht.parse_index(data, size)

    def test_negative_container_size(self):
        with self.assertRaises(fht.FormatError):
            fht.parse_index(record(), -1)

    def test_only_index_is_opened_and_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            index = Path(directory) / "a.fht_"
            container = Path(directory) / "a.bin_"
            index.write_bytes(record())
            container.write_bytes(b"x" * 16)
            original_open = Path.open
            opened = []

            def guarded_open(path, *args, **kwargs):
                opened.append(path)
                self.assertNotEqual(path, container)
                return original_open(path, *args, **kwargs)

            with patch.object(Path, "open", guarded_open):
                result = fht.inventory(index, container)
            self.assertEqual(opened, [index])
            self.assertEqual(len(result["provenance"]["index_sha256"]), 64)
            self.assertFalse(result["provenance"]["container_content_read"])
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(fht.main(["--pair", str(index), str(container)]), 0)
            self.assertEqual(json.loads(output.getvalue())["schema"], fht.SCHEMA)

    def test_cli_error_returns_no_partial_json(self):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            self.assertEqual(fht.main(["--pair", "missing.fht_", "missing.bin_"]), 2)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("fht_inventory:", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
