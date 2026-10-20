"""Contract tests for the small, deliberately strict PPC64 leaf probe.

Fixtures are synthetic ELF records; no executable or game data is used.
"""
from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import leaf_probe  # type: ignore


MASK = (1 << 64) - 1


def dform(op: int, rt: int, ra: int, imm: int) -> int:
    return (op << 26) | (rt << 21) | (ra << 16) | (imm & 0xFFFF)


def xform_or(rs: int, ra: int, rb: int, rc: int = 0) -> int:
    return (31 << 26) | (rs << 21) | (ra << 16) | (rb << 11) | (444 << 1) | rc


BLR = 0x4E800020


def elf64(code: bytes, *, filesz: int | None = None, memsz: int | None = None,
          p_type: int = 1, flags: int = 5, machine: int = 21,
          etype: int = 2, phoff: int = 64, phentsize: int = 56,
          phnum: int = 1, truncate: int | None = None,
          sce_marker: bool = False) -> bytes:
    """Build one ELF64 BE program header with code at file offset 0x100."""
    fs = len(code) if filesz is None else filesz
    ms = fs if memsz is None else memsz
    ident = b"\x7fELF" + bytes((2, 2, 1, 0, 0)) + bytes(7)
    header = struct.pack(">16sHHIQQQIHHHHHH", ident, etype, machine, 1, 0,
                         phoff, 0, 0, 64, phentsize, phnum, 64, 0, 0)
    phdr = struct.pack(">IIQQQQQQ", p_type, flags, 0x100, 0x1000, 0,
                       fs, ms, 0x100)
    data = bytearray(max(0x100 + len(code), phoff + len(phdr)))
    data[:len(header)] = header
    data[phoff:phoff + len(phdr)] = phdr
    data[0x100:0x100 + len(code)] = code
    if sce_marker:
        data[:4] = b"SCE\0"
    return bytes(data if truncate is None else data[:truncate])


def code_bytes(*words: int) -> bytes:
    return b"".join(struct.pack(">I", w & 0xFFFFFFFF) for w in words)


class ReadCodeTests(unittest.TestCase):
    def test_ignores_empty_placeholder_but_not_nonempty_misalignment(self):
        data = bytearray(elf64(code_bytes(BLR), phnum=2))
        empty = struct.pack('>IIQQQQQQ', 1, 6, 0x80, 0, 0, 0, 0, 0x10000)
        data[120:176] = empty
        self.assertEqual(leaf_probe.read_code(bytes(data), 0x1000, 0x1004), code_bytes(BLR))
        struct.pack_into('>Q', data, 120 + 40, 4)
        with self.assertRaises(ValueError):
            leaf_probe.read_code(bytes(data), 0x1000, 0x1004)

    def test_reads_exact_aligned_file_backed_range_end_exclusive(self):
        code = code_bytes(dform(14, 3, 0, 7), BLR)
        data = elf64(code)
        self.assertEqual(leaf_probe.read_code(data, 0x1000, 0x1008), code)

    def test_accepts_maximum_64_byte_range(self):
        code = code_bytes(*([dform(24, 1, 2, 3)] * 15 + [BLR]))
        self.assertEqual(len(leaf_probe.read_code(elf64(code), 0x1000, 0x1040)), 64)

    def test_rejects_empty_oversize_and_unaligned_ranges(self):
        data = elf64(code_bytes(BLR) * 17)
        for start, end in ((0x1000, 0x1000), (0x1000, 0x1044),
                           (0x1002, 0x1006), (0x1000, 0x1006)):
            with self.subTest(start=start, end=end), self.assertRaises(Exception):
                leaf_probe.read_code(data, start, end)

    def test_rejects_range_outside_executable_segment(self):
        data = elf64(code_bytes(BLR), flags=4)
        with self.assertRaises(Exception):
            leaf_probe.read_code(data, 0x1000, 0x1004)

    def test_rejects_bss_even_when_inside_memory_size(self):
        data = elf64(code_bytes(BLR), filesz=4, memsz=16)
        with self.assertRaises(Exception):
            leaf_probe.read_code(data, 0x1004, 0x1008)

    def test_rejects_truncated_header_and_program_header(self):
        good = elf64(code_bytes(BLR))
        for data in (good[:20], good[:64], good[:100]):
            with self.subTest(length=len(data)), self.assertRaises(Exception):
                leaf_probe.read_code(data, 0x1000, 0x1004)

    def test_rejects_non_elf_wrong_class_endian_machine_and_type(self):
        base = elf64(code_bytes(BLR))
        variants = []
        bad = bytearray(base); bad[0] = 0; variants.append(bytes(bad))
        bad = bytearray(base); bad[4] = 1; variants.append(bytes(bad))
        bad = bytearray(base); bad[5] = 1; variants.append(bytes(bad))
        bad = bytearray(base); struct.pack_into(">H", bad, 18, 3); variants.append(bytes(bad))
        bad = bytearray(base); struct.pack_into(">H", bad, 16, 3); variants.append(bytes(bad))
        for data in variants:
            with self.subTest(header=data[:20]), self.assertRaises(Exception):
                leaf_probe.read_code(data, 0x1000, 0x1004)

    def test_rejects_self_and_non_load_segment(self):
        code = code_bytes(BLR)
        for data in (elf64(code, sce_marker=True), elf64(code, p_type=0)):
            with self.assertRaises(Exception):
                leaf_probe.read_code(data, 0x1000, 0x1004)

    def test_rejects_invalid_segment_size_or_missing_program_header(self):
        data = bytearray(elf64(code_bytes(BLR)))
        struct.pack_into(">Q", data, 64 + 32, 8)  # p_filesz exceeds p_memsz below
        struct.pack_into(">Q", data, 64 + 40, 4)
        malformed = bytes(data)
        no_ph = bytearray(elf64(code_bytes(BLR)))
        struct.pack_into(">H", no_ph, 56, 0)
        for bad in (malformed, bytes(no_ph)):
            with self.assertRaises(Exception):
                leaf_probe.read_code(bad, 0x1000, 0x1004)


class InterpretTests(unittest.TestCase):
    def run_words(self, words, regs=None):
        return leaf_probe.interpret(words, list(regs if regs is not None else [0] * 32))

    def test_addi_ra_zero_signed_immediate_and_addis(self):
        regs = [0] * 32
        result = self.run_words([dform(14, 3, 0, 0xFFFF),
                                 dform(15, 4, 0, 0x8000), BLR], regs)
        self.assertEqual(result[3], MASK)
        self.assertEqual(result[4], (-(32768 << 16)) & MASK)
        self.assertEqual(regs, [0] * 32)

    def test_dform_add_and_addis_wrap_to_uint64(self):
        regs = [0] * 32; regs[5] = MASK
        result = self.run_words([dform(14, 5, 5, 1), dform(15, 6, 5, 1), BLR], regs)
        self.assertEqual(result[5], 0)
        self.assertEqual(result[6], 0x10000)

    def test_logical_immediates_and_register_zero_is_ordinary(self):
        regs = [0] * 32; regs[0] = 0x123456789ABCDEF0
        regs[6] = 0x1111222233334444
        result = self.run_words([dform(24, 0, 1, 0x00FF),
                                 dform(25, 6, 2, 0xABCD),
                                 dform(26, 6, 3, 0xFFFF),
                                 dform(27, 6, 4, 0x8001), BLR], regs)
        self.assertEqual(result[1], 0x123456789ABCDEF0 | 0x00FF)
        self.assertEqual(result[2], 0x11112222BBFF4444)
        self.assertEqual(result[3], 0x1111222233334444 ^ 0xFFFF)
        self.assertEqual(result[4], 0x1111222233334444 ^ 0x80010000)

    def test_or_register_uses_rs_and_rb_and_does_not_mutate_input(self):
        regs = [0] * 32; regs[0] = 0x8000000000000001; regs[7] = 0xF0
        before = regs.copy()
        result = self.run_words([xform_or(0, 7, 7), BLR], regs)
        self.assertEqual(result[7], 0x80000000000000F1)
        self.assertEqual(regs, before)
        self.assertIsNot(result, regs)

    def test_requires_terminal_blr_and_rejects_early_or_missing_blr(self):
        for words in ([BLR, dform(14, 1, 0, 2)],
                      [dform(14, 1, 0, 2)],
                      [dform(14, 1, 0, 2), 0]):
            with self.subTest(words=words), self.assertRaises(Exception):
                self.run_words(list(words))

    def test_rejects_unsupported_opcode_xo_and_record_form(self):
        bad_words = [0, dform(16, 1, 0, 0),
                     (31 << 26) | (1 << 21) | (2 << 16) | (3 << 11) | (266 << 1),
                     xform_or(1, 2, 3, 1)]
        for bad in bad_words:
            with self.subTest(word=hex(bad)), self.assertRaises(Exception):
                self.run_words([bad, BLR])

    def test_rejects_malformed_program_and_register_inputs(self):
        cases = [([], [0] * 32), ([BLR], [0] * 31), ([BLR], [0] * 33),
                 ([True, BLR], [0] * 32), ([0x100000000, BLR], [0] * 32),
                 (None, [0] * 32), ([BLR], None)]
        for words, regs in cases:
            with self.subTest(words=words, regs_len=None if regs is None else len(regs)), self.assertRaises(Exception):
                leaf_probe.interpret(words, regs)


if __name__ == "__main__":
    unittest.main()
