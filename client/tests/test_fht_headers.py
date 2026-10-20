import contextlib
import importlib.util
import io
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location("inspect_fht_headers", Path(__file__).parents[1] / "scripts" / "inspect_fht_headers.py")
headers = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(headers)


class HeaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parents[1])
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "bin").mkdir()
        for name in headers.PAIR_NAMES:
            (self.root / "bin" / f"{name}.fht_").write_bytes(b"a.dds_".ljust(32, b"\0") + struct.pack(">II", 80, 8))
            (self.root / "bin" / f"{name}.bin_").write_bytes(b"unused!!" + b"DDS " + b"A" * 76)

    def test_bounded_measured_reads_without_suffix_assumption(self):
        result = headers.inspect_headers(self.root)
        self.assertEqual(result["payload_bytes_read"], 192)
        self.assertEqual(result["entry_count"], 3)
        self.assertEqual(result["suffix_observations"], {".dds_": {"DDS-prefix": 3}})
        self.assertTrue(all(row["bytes_read"] == 64 and len(row["header_hex"]) == 128 for row in result["entries"]))
        self.assertEqual(headers.classify(b"wrapper data"), "unrecognized-prefix")

    def test_limits_reject_before_container_open(self):
        original = Path.open
        opened = []
        def tracked(path, *args, **kwargs):
            opened.append(path.name)
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, "open", tracked), self.assertRaises(headers.FormatError):
            headers.inspect_headers(self.root, total_limit=191)
        self.assertFalse(any(name.endswith(".bin_") for name in opened))
        for value in (0, 65, -1, True):
            with self.assertRaises(headers.FormatError):
                headers.inspect_headers(self.root, header_bytes=value)
        for value in (0, headers.MAX_TOTAL + 1, True):
            with self.assertRaises(headers.FormatError):
                headers.inspect_headers(self.root, total_limit=value)

    def test_invalid_range_rejected(self):
        (self.root / "bin" / "etc.bin_").write_bytes(b"too short")
        with self.assertRaises(headers.FormatError):
            headers.inspect_headers(self.root)

    def test_truncated_stream_rejected(self):
        original = Path.open
        class ShortReader:
            def __init__(self, stream): self.stream = stream
            def __enter__(self): return self
            def __exit__(self, *args): self.stream.close()
            def fileno(self): return self.stream.fileno()
            def seek(self, offset): return self.stream.seek(offset)
            def read(self, size): return self.stream.read(size - 1)
        def short(path, *args, **kwargs):
            stream = original(path, *args, **kwargs)
            return ShortReader(stream) if path.suffix == ".bin_" else stream
        with mock.patch.object(Path, "open", short), self.assertRaisesRegex(headers.FormatError, "truncated"):
            headers.inspect_headers(self.root)

    def test_short_entry_never_reads_neighbor(self):
        for name in headers.PAIR_NAMES:
            (self.root / "bin" / f"{name}.fht_").write_bytes(b"a".ljust(32, b"\0") + struct.pack(">II", 2, 8))
        result = headers.inspect_headers(self.root)
        self.assertEqual(result["payload_bytes_read"], 6)
        self.assertTrue(all(row["bytes_read"] == 2 for row in result["entries"]))

    def test_path_escape_and_public_report_rejected(self):
        with self.assertRaises(headers.FormatError):
            headers.checked_path(self.root.resolve(), Path("..") / "tests" / "test_fht_headers.py")
        with self.assertRaises(headers.FormatError):
            headers.private_report_path(Path(__file__).parents[1] / "public.json")

    def test_elf_machine_classification_and_compression_candidates(self):
        for endian in ("big", "little"):
            header = bytearray(64)
            header[:6] = b"\x7fELF\x01" + bytes([2 if endian == "big" else 1])
            header[18:20] = (23).to_bytes(2, endian)
            self.assertEqual(headers.classify(header), "ELF-prefix-EM_SPU")
        self.assertEqual(headers.classify(b"\x78\x9c"), "zlib-CMF-FLG-candidate")
        self.assertEqual(headers.classify(b"\x78\x9d"), "unrecognized-prefix")
        self.assertEqual(headers.classify(b"\x1f\x8b\x08"), "gzip-prefix-candidate")


if __name__ == "__main__":
    unittest.main()
