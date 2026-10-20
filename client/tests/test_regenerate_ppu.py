"""Builder safety checks use synthetic inputs, never game data."""
import argparse
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    'regenerate_ppu', Path(__file__).resolve().parents[1] / 'scripts/regenerate_ppu.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class RegenerationSafety(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_rejects_both_directions_of_overlap(self):
        sdk = self.root / 'sdk'
        sdk.mkdir()
        for output in (sdk, sdk / 'generated', self.root):
            with self.subTest(output=output), self.assertRaises(ValueError):
                builder.reject_overlap(output, [sdk])

    def test_rejects_existing_output(self):
        output = self.root / 'exists'
        output.mkdir()
        with self.assertRaisesRegex(ValueError, 'new directory'):
            builder.reject_overlap(output, [self.root / 'elf'])

    def test_wrong_elf_never_creates_output(self):
        elf = self.root / 'eboot.elf'
        elf.write_bytes(b'not the supported ELF')
        args = argparse.Namespace(elf=elf, sdk=self.root / 'sdk', metadata=self.root / 'metadata',
                                  manifest=self.root / 'manifest.json', output=self.root / 'out', jobs=1)
        with self.assertRaisesRegex(ValueError, 'Unsupported ELF'):
            builder.execute(args)
        self.assertFalse(args.output.exists())

    def test_missing_recovery_function_rejected(self):
        with self.assertRaisesRegex(ValueError, 'function missing'):
            builder.splice('void other() {}', 'void other() {}')

    def test_instruction_change_rejected(self):
        old = 'void func_0034D268(ppu_context* ctx) {\n        ctx->gpr[1] = 1;\n}\n'
        new = ('void func_0034D268(ppu_context* ctx) {\n        ctx->gpr[1] = 2;\n'
               '        switch ((uint32_t)ctx->ctr) {case 0x0034DDF8u: goto loc_0034DDF8;}\n}\n')
        with self.assertRaisesRegex(ValueError, 'translated instructions'):
            builder.splice(old, new)

    def test_traversal_manifest_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            builder.verify(self.root, {'files': [{'name': '../outside', 'overlay_sha256': '0'}]},
                           'overlay_sha256')

    def test_missing_extra_and_modified_files_detected(self):
        (self.root / 'unit.cpp').write_bytes(b'changed')
        (self.root / 'extra.cpp').write_bytes(b'extra')
        manifest = {'files': [{'name': 'unit.cpp', 'overlay_sha256': '0' * 64},
                              {'name': 'missing.h', 'overlay_sha256': '1' * 64}]}
        rows, extra = builder.verify(self.root, manifest, 'overlay_sha256')
        self.assertFalse(any(r['match'] for r in rows))
        self.assertEqual(extra, ['extra.cpp'])
        self.assertIsNone(rows[1]['actual'])


if __name__ == '__main__':
    unittest.main()
