"""Tests for the frame-rate constant scanner; fixtures are synthetic ELF records."""
import contextlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / 'scripts'))
import frame_constants as fc  # type: ignore

BLR, NOP = 0x4E800020, 0x60000000
CODE_VA, DATA_VA, TOC = 0x10000, 0x20000, 0x20300


def dform(op, rt, ra, imm):
    return (op << 26) | (rt << 21) | (ra << 16) | (imm & 0xFFFF)


def branch(offset):
    return (18 << 26) | (offset & 0x3FFFFFC)


def elf(code_words, data, entry=DATA_VA + 0x100, sce=False):
    """ELF64 BE with an R+X code segment at CODE_VA and an R+W data segment at DATA_VA."""
    code = b''.join(struct.pack('>I', w) for w in code_words)
    ident = b'\x7fELF' + bytes((2, 2, 1, 0, 0)) + bytes(7)
    header = struct.pack('>16sHHIQQQIHHHHHH', ident, 2, 21, 1, entry, 64, 0, 0, 64, 56, 2, 64, 0, 0)
    phdrs = (struct.pack('>IIQQQQQQ', 1, 5, 0x200, CODE_VA, 0, len(code), len(code), 0x100)
             + struct.pack('>IIQQQQQQ', 1, 6, 0x1000, DATA_VA, 0, len(data), len(data), 0x100))
    out = bytearray(0x1000 + len(data))
    out[:64], out[64:64 + len(phdrs)] = header, phdrs
    out[0x200:0x200 + len(code)] = code
    out[0x1000:] = data
    if sce:
        out[:4] = b'SCE\0'
    return bytes(out)


def data_segment(extra=b''):
    data = bytearray(0x400)
    struct.pack_into('>f', data, 0x000, 1 / 30)       # float 1/30
    struct.pack_into('>d', data, 0x008, 1 / 60)       # double 1/60
    struct.pack_into('>f', data, 0x010, 30.0)         # float 30
    struct.pack_into('>f', data, 0x014, 0.03333)      # near 1/30 only at loose tolerance
    struct.pack_into('>f', data, 0x018, 0.25)         # unrelated
    struct.pack_into('>II', data, 0x100, CODE_VA, TOC)  # entry descriptor {code, toc}
    struct.pack_into('>I', data, 0x300, DATA_VA + 8)  # TOC slot 0 points at the double
    data[0x380:0x380 + len(extra)] = extra
    return bytes(data)


CODE = [
    dform(15, 9, 0, 2),          # 0x10000 lis r9, 2         -> 0x20000
    dform(48, 1, 9, 0),          # 0x10004 lfs f1, 0(r9)     -> 1/30
    dform(32, 10, 2, 0),         # 0x10008 lwz r10, 0(r2)    -> pointer 0x20008
    dform(50, 2, 10, 0),         # 0x1000C lfd f2, 0(r10)    -> 1/60
    dform(48, 3, 2, 0x10 - 0x300),  # 0x10010 lfs f3, -0x2F0(r2) -> 30 fps
    BLR,                         # 0x10014
    NOP,                         # 0x10018 padding
    dform(15, 9, 0, 2),          # 0x1001C lis r9, 2
    dform(14, 9, 0, 5),          # 0x10020 li r9, 5 overwrites the address
    dform(48, 1, 9, 0),          # 0x10024 lfs f1, 0(r9)     -> no reference
    dform(15, 11, 0, 2),         # 0x10028 lis r11, 2
    branch(8),                   # 0x1002C b +8 clears tracking
    dform(48, 1, 11, 0),         # 0x10030 lfs f1, 0(r11)    -> no reference
    BLR,                         # 0x10034
]


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.data = elf(CODE, data_segment())

    def test_finds_float_and_double_constants_at_default_tolerance(self):
        hits = fc.find_constants(self.data, fc.load_segments(self.data)[1])
        found = {a: (h['type'], h['target']) for a, h in hits.items() if a >= DATA_VA}
        self.assertEqual(found, {0x20000: ('float', '1/30 s'), 0x20008: ('double', '1/60 s'),
                                 0x20010: ('float', '30 fps')})

    def test_loose_tolerance_matches_rounded_literal(self):
        hits = fc.find_constants(self.data, fc.load_segments(self.data)[1], tolerance=2e-3)
        self.assertEqual(hits[0x20014]['target'], '1/30 s')

    def test_rejects_out_of_range_tolerance(self):
        segs = fc.load_segments(self.data)[1]
        for tol in (0, -1, 0.5):
            with self.subTest(tol=tol), self.assertRaises(ValueError):
                fc.find_constants(self.data, segs, tolerance=tol)

    def test_reads_toc_from_entry_descriptor(self):
        result = fc.scan(self.data)
        self.assertEqual((result['toc'], result['toc_source']), (TOC, 'entry descriptor'))

    def test_references_via_lis_toc_pointer_and_toc_offset(self):
        refs = {r['address']: (r['insn'], r['target'], r['via'], r['function']) for r in fc.scan(self.data)['references']}
        self.assertEqual(refs, {0x10004: ('lfs', '1/30 s', 'register', 'func_00010000'),
                                0x1000C: ('lfd', '1/60 s', 'register', 'func_00010000'),
                                0x10010: ('lfs', '30 fps', 'toc', 'func_00010000')})

    def test_overwrite_branch_and_window_expiry_drop_tracking(self):
        code = [dform(15, 9, 0, 2)] + [NOP] * 20 + [dform(48, 1, 9, 0), BLR]
        data = elf(code, data_segment())
        self.assertEqual(fc.scan(data)['references'], [])
        self.assertEqual(len(fc.scan(data, window=32)['references']), 1)

    def test_unknown_toc_skips_toc_loads_but_keeps_lis_loads(self):
        data = elf(CODE, data_segment(), entry=0x90000)
        result = fc.scan(data)
        self.assertIsNone(result['toc'])
        self.assertEqual([r['address'] for r in result['references']], [0x10004])
        self.assertEqual(len(fc.scan(data, toc=TOC)['references']), 3)

    def test_function_estimate_after_blr(self):
        code = CODE[:7] + [dform(15, 9, 0, 2), dform(48, 1, 9, 0), BLR]
        refs = fc.scan(elf(code, data_segment()))['references']
        self.assertEqual((refs[-1]['address'], refs[-1]['function'], refs[-1]['function_source']),
                         (0x10020, 'func_0001001C', 'after-blr'))

    def test_rejects_self_and_non_ppc_inputs(self):
        bad = bytearray(self.data)
        struct.pack_into('>H', bad, 18, 3)
        for data in (elf(CODE, data_segment(), sce=True), bytes(bad), b'\x7fELF'):
            with self.subTest(head=data[:4]), self.assertRaises(ValueError):
                fc.load_segments(data)


class CliTests(unittest.TestCase):
    def run_main(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = fc.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_json_and_text_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'EBOOT.elf'
            path.write_bytes(elf(CODE, data_segment()))
            code, out, _ = self.run_main('--elf', str(path), '--json')
            self.assertEqual(code, 0)
            self.assertEqual(len(json.loads(out)['references']), 3)
            code, out, _ = self.run_main('--elf', str(path))
            self.assertEqual(code, 0)
            self.assertIn('Functions to inspect first (1): func_00010000', out)

    def test_missing_file_reports_error(self):
        code, out, err = self.run_main('--elf', str(ROOT / 'missing-EBOOT.elf'))
        self.assertEqual((code, out), (1, ''))
        self.assertIn('error:', err)


if __name__ == '__main__':
    unittest.main()
