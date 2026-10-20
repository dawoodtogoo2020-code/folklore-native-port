"""Synthetic tests for owned-ELF extraction and descriptor reconstruction."""
import argparse
import importlib.util
from pathlib import Path
import struct
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('regenerate_spu', SCRIPTS / 'regenerate_spu.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class SpuSafety(unittest.TestCase):
    def test_descriptor_has_explicit_size_stack_and_argument_fields(self):
        data = builder.descriptor(0x10000000, 160, 2)
        self.assertEqual(len(data), 128)
        self.assertEqual(struct.unpack_from('>Q', data)[0], 0x10000000)
        self.assertEqual(struct.unpack_from('>H', data, 8)[0], 10)
        self.assertEqual(struct.unpack_from('>H', data, 28)[0] * 16, 0x4000)
        self.assertEqual(struct.unpack_from('>HH', data, 68), (3, 2))

    def test_invalid_descriptor_metadata_rejected(self):
        for size, variant in [(0, 1), (17, 1), (1048576, 1), (160, 3)]:
            with self.subTest(size=size, variant=variant), self.assertRaises(ValueError):
                builder.descriptor(0x10000000, size, variant)

    def test_elf_mapping_requires_complete_extent(self):
        elf = bytearray(512)
        elf[:6] = b'\x7fELF\x02\x02'
        struct.pack_into('>Q', elf, 32, 64)
        struct.pack_into('>HH', elf, 54, 56, 1)
        struct.pack_into('>IIQQQQQQ', elf, 64, 1, 6, 200, 0x1000, 0, 100, 100, 16)
        self.assertEqual(builder.virtual_address(elf, 220, 16), 0x1014)
        with self.assertRaises(ValueError):
            builder.virtual_address(elf, 290, 16)

    def test_wrong_elf_rejected_before_output_creation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            elf = root / 'elf'
            elf.write_bytes(b'wrong')
            args = argparse.Namespace(elf=elf, sdk=root/'sdk', output=root/'output', manifest=root/'hashes.json')
            with self.assertRaisesRegex(ValueError, 'Unsupported ELF'):
                builder.execute(args)
            self.assertFalse(args.output.exists())

    def test_metadata_is_nine_unique_disjoint_images(self):
        self.assertEqual(len(builder.JOBS), 9)
        self.assertEqual(len({r[3] for r in builder.JOBS}), 9)
        ordered = sorted(builder.JOBS, key=lambda row: row[1])
        for a, b in zip(ordered, ordered[1:]):
            self.assertLessEqual(a[1] + a[2], b[1])


if __name__ == '__main__':
    unittest.main()
