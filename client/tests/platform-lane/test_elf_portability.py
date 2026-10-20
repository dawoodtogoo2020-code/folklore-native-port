"""Independent wire fixtures for target and page-geometry failure modes."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "platform_elf", ROOT / "scripts/platform-lane/elf_portability.py")
ELF = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ELF)


def fixture(machine=183, elf_type=3, align=16384, offset=0, address=0,
            filesz=256, memsz=256, extra=()):
    # Literal ELF wire layout, independent of the parser's struct definitions.
    headers = [(1, 5, offset, address, 0, filesz, memsz, align), *extra]
    data = bytearray(1024)
    data[:16] = b"\x7fELF\x02\x01\x01" + b"\x00" * 9
    struct.pack_into("<HHIQQQIHHHHHH", data, 16,
                     elf_type, machine, 1, 0, 64, 0, 0, 64, 56, len(headers), 0, 0, 0)
    for index, header in enumerate(headers):
        struct.pack_into("<IIQQQQQQ", data, 64 + 56 * index, *header)
    return data


class TargetHeaderTests(unittest.TestCase):
    def inspect(self, data, target="android-arm64"):
        return ELF.inspect_stream(io.BytesIO(data), len(data), target)

    def test_android_library_header_does_not_claim_execution_or_libc(self):
        result = self.inspect(fixture())
        self.assertTrue(result["header_checks_passed"])
        for key in ("runtime_tested", "gameplay_verified", "packaging_verified",
                    "operating_system_abi_verified"):
            self.assertIs(result[key], False)

    def test_linux_4k_header_is_not_android_artifact(self):
        data = fixture(machine=62, elf_type=2, align=4096)
        self.assertTrue(self.inspect(data, "linux-x64")["header_checks_passed"])
        result = self.inspect(data)
        self.assertFalse(result["header_checks_passed"])
        self.assertTrue(any("machine" in x for x in result["issues"]))
        self.assertTrue(any("p_align" in x for x in result["issues"]))

    def test_4k_arm64_alignment_fails_android_16k(self):
        result = self.inspect(fixture(align=4096))
        self.assertFalse(result["header_checks_passed"])

    def test_congruence_independent_of_declared_alignment(self):
        data = fixture(align=1, address=0x1000)
        result = self.inspect(data)
        self.assertFalse(result["header_checks_passed"])
        self.assertTrue(any("incongruent at 16384" in issue for issue in result["issues"]))
        linux = self.inspect(fixture(machine=62, align=1, address=0x1000), "linux-x64")
        self.assertTrue(linux["header_checks_passed"])

    def test_bss_only_load_does_not_require_file_backing(self):
        self.assertTrue(self.inspect(fixture(offset=16384, filesz=0, memsz=16384))[
            "header_checks_passed"])

    def test_review_padding_counterexample_reports_rounded_range_without_false_rejection(self):
        result = self.inspect(self.review_relro_fixture(0x8000))
        self.assertTrue(result["header_checks_passed"])
        region = result["relro_segments"][0]
        self.assertEqual((region["rounded_start"], region["rounded_end"]), (0x4000, 0x8000))
        self.assertEqual(region["extra_protection_writable_loads"], [1])
        self.assertTrue(result["warnings"])
        self.assertFalse(region["mutable_data_ownership_verified"])
        self.assertFalse(result["relro_runtime_safety_verified"])

    def test_rounding_can_touch_another_writable_load_without_proving_data_safety(self):
        result = self.inspect(self.review_relro_fixture(0x6000))
        self.assertTrue(result["header_checks_passed"])
        self.assertEqual(result["relro_segments"][0]["extra_protection_writable_loads"], [1, 2])
        self.assertTrue(result["warnings"])
        self.assertFalse(result["relro_runtime_safety_verified"])

    def review_relro_fixture(self, writable_start):
        # Review seq5: independent three-LOAD wire metadata, no runnable DSO claim.
        data = bytearray(49152)
        data[:16] = b"\x7fELF\x02\x01\x01" + b"\0" * 9
        struct.pack_into("<HHIQQQIHHHHHH", data, 16, 3, 183, 1, 0, 64, 0, 0, 64, 56, 4, 0, 0, 0)
        headers = [(1, 5, 0, 0, 0, 16384, 16384, 16384),
                   (1, 6, 16384, 16384, 0, 4096, 16384, 16384),
                   (1, 6, writable_start, writable_start, 0, 4096, 16384, 16384),
                   (0x6474E552, 4, 16384, 16384, 0, 4096, 4096, 1)]
        for index, header in enumerate(headers):
            struct.pack_into("<IIQQQQQQ", data, 64 + index * 56, *header)
        return data

    def test_android_executable_interpreter(self):
        for name, expected in ((b"/system/bin/linker64\0", True),
                               (b"/lib/ld-linux-aarch64.so.1\0", False)):
            data = fixture(extra=[(3, 4, 512, 0, 0, len(name), len(name), 1)])
            data[512:512 + len(name)] = name
            self.assertEqual(self.inspect(data)["header_checks_passed"], expected)

    def test_relocatable_is_not_accepted_as_app(self):
        self.assertFalse(self.inspect(fixture(elf_type=1))["header_checks_passed"])

    def test_malformed_or_foreign_header_rejected(self):
        cases = []
        data = fixture(); data[0] = 0; cases.append(data)
        data = fixture(); data[4] = 1; cases.append(data)
        data = fixture(); data[5] = 2; cases.append(data)
        data = fixture(); struct.pack_into("<Q", data, 32, 1000); cases.append(data)
        data = fixture(); struct.pack_into("<H", data, 54, 8); cases.append(data)
        data = fixture(); struct.pack_into("<H", data, 56, 65535); cases.append(data)
        cases.extend([b"", fixture()[:64], fixture(offset=16384), fixture(filesz=300, memsz=1),
                      fixture(align=3), fixture(address=1),
                      fixture(address=(1 << 64) - 16384, memsz=32768)])
        for index, data in enumerate(cases):
            with self.subTest(index=index), self.assertRaises(ELF.ElfError):
                self.inspect(data)

    def test_load_order_and_missing_load_rejected(self):
        data = fixture(address=32768, extra=[(1, 4, 0, 16384, 0, 0, 16384, 16384)])
        with self.assertRaises(ELF.ElfError):
            self.inspect(data)
        data = fixture(); struct.pack_into("<I", data, 64, 4)
        with self.assertRaises(ELF.ElfError):
            self.inspect(data)

    def test_interpreter_length_and_termination_rejected(self):
        for size, payload in ((0, b""), (4097, b""), (4, b"abcd"), (4, b"a\0b\0")):
            data = fixture(extra=[(3, 4, 512, 0, 0, size, size, 1)])
            data[512:512 + len(payload)] = payload
            with self.subTest(size=size), self.assertRaises(ELF.ElfError):
                self.inspect(data)

    def test_cli_exit_codes_read_only_and_d_storage(self):
        output = Path(os.environ.get("FOLKLORE_TEST_OUTPUT_ROOT", Path(__file__).parent))
        with tempfile.TemporaryDirectory(prefix=".elf-fixture-", dir=output) as name:
            path = Path(name) / "fixture.elf"
            for payload, expected in ((fixture(), 0), (fixture(align=4096), 1), (b"bad", 2)):
                path.write_bytes(payload)
                before = path.read_bytes()
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    code = ELF.main([str(path), "--target", "android-arm64"])
                self.assertEqual(code, expected)
                self.assertEqual(path.read_bytes(), before)
                self.assertIsInstance(json.loads(output.getvalue()), dict)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(ELF.main([str(path.parent / "absent"), "--target", "linux-x64"]), 2)
                self.assertEqual(ELF.main([str(path.parent), "--target", "linux-x64"]), 2)


if __name__ == "__main__":
    unittest.main()
