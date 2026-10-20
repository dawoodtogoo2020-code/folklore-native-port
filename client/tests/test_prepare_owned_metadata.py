import argparse
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import prepare_owned_metadata as metadata


class MetadataTests(unittest.TestCase):
    def fixture(self):
        entries = [
            dict(func=0x100, toc=0x500, opd=0x600),
            dict(func=0x120, toc=0x500, opd=0x608),
            dict(func=0x140, toc=0x500, opd=0x610),
            # Last descriptor for an aliased function is authoritative.
            dict(func=0x100, toc=0x500, opd=0x618),
        ]
        loader = NS(code_ranges=lambda elf: [(0x100, 0x200)],
                    find_opd=lambda elf, entry: (0x600, 32),
                    parse_opd=lambda *args: (entries, 0x500))
        elf = NS(elf_header=NS(e_entry=0x600), section_headers=[
            NS(sh_addr=0x100, sh_size=0x30, sh_flags=4),
            NS(sh_addr=0x140, sh_size=0x10, sh_flags=4),
            NS(sh_addr=0x150, sh_size=0xB0, sh_flags=2)])
        return loader, elf

    def test_section_end_clamps_last_function_before_data(self):
        loader, elf = self.fixture()
        self.assertEqual(metadata.bounded_functions(loader, elf), [
            dict(start="0x100", end="0x120", toc="0x500", opd="0x618"),
            dict(start="0x120", end="0x130", toc="0x500", opd="0x608"),
            dict(start="0x140", end="0x150", toc="0x500", opd="0x610")])

    def test_missing_section_is_rejected(self):
        loader, elf = self.fixture()
        elf.section_headers = elf.section_headers[:1]
        with self.assertRaisesRegex(ValueError, "bounds"):
            metadata.bounded_functions(loader, elf)

    def test_overlapping_executable_sections_rejected(self):
        loader, elf = self.fixture()
        elf.section_headers.append(elf.section_headers[0])
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            metadata.bounded_functions(loader, elf)

    def test_historical_json_is_exact_crlf_without_final_newline(self):
        encoded = metadata.encode_metadata([dict(start="0x100")], 1)
        self.assertEqual(encoded, b'[\r\n {\r\n  "start": "0x100"\r\n }\r\n]')
        self.assertEqual(json.loads(encoded), [dict(start="0x100")])

    def test_wrong_elf_has_no_output_and_no_parser_dependency(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "owner.elf").write_bytes(b"synthetic unsupported ELF")
            args = argparse.Namespace(elf=root / "owner.elf",
                                      sdk=root / "absent-sdk", output=root / "out")
            with self.assertRaisesRegex(ValueError, "Unsupported ELF"):
                metadata.execute(args)
            self.assertFalse(args.output.exists())


if __name__ == "__main__":
    unittest.main()
