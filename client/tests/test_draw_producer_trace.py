"""CPU-only preparation checks against the reviewed local generated inputs."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('drawtrace', ROOT/'scripts/prepare_draw_producer_trace.py')
trace = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trace)
INPUT = ROOT.parent/'owned-build/ppu/overlay'

class PreparationTests(unittest.TestCase):
    def test_exact_sources_preserved(self):
        for name, metadata in trace.SOURCES.items():
            with self.subTest(name=name):
                source = INPUT/name
                original = source.read_bytes()
                derived = trace.transform(name, original)
                self.assertEqual(derived.count(trace.MARKER), 1)
                extras=trace._controller_blocks(name)
                self.assertEqual(derived.count(b'folklore_draw_producer_trace::entry('), 2 if extras else 1)
                restored=derived
                for _,block in extras: restored=restored.replace(block,b'',1)
                self.assertEqual(restored.replace(trace._include_block(), b'').replace(
                    trace._hook_block(metadata['address']), b''), original)
                with tempfile.TemporaryDirectory() as temporary:
                    output = Path(temporary)/name
                    first = trace.prepare(str(source), str(output))
                    self.assertEqual(output.read_bytes(), derived)
                    self.assertEqual(first, trace.prepare(str(source), str(output)))
                self.assertEqual(source.read_bytes(), original)

    def test_source_drift_rejected_without_output_mutation(self):
        name = 'ppu_recomp_008.cpp'
        original = (INPUT/name).read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)/name
            output = Path(temporary)/'output.cpp'
            output.write_bytes(b'keep-existing-output')
            for invalid in (original+b' ', original.replace(trace.INCLUDE_LINE, b'broken', 1),
                            original+trace.INCLUDE_LINE+trace.CRLF):
                source.write_bytes(invalid)
                with self.assertRaises(trace.TransformError):
                    trace.prepare(str(source), str(output))
                self.assertEqual(output.read_bytes(), b'keep-existing-output')

    def test_anchor_guards_independent_of_hash_guard(self):
        name = 'ppu_recomp_008.cpp'
        original = (INPUT/name).read_bytes()
        metadata = trace.SOURCES[name]
        saved_hash = metadata['sha256']
        try:
            for invalid in (original.replace(trace.INCLUDE_LINE, b'broken', 1),
                            original+trace.INCLUDE_LINE+trace.CRLF,
                            original+trace.MARKER):
                metadata['sha256'] = hashlib.sha256(invalid).hexdigest()
                with self.assertRaises(trace.TransformError): trace.transform(name, invalid)
        finally:
            metadata['sha256'] = saved_hash

    def test_unknown_name_and_same_file_rejected(self):
        source = INPUT/'ppu_recomp_008.cpp'
        original = source.read_bytes()
        with self.assertRaises(trace.TransformError): trace.transform('other.cpp', original)
        with self.assertRaises(trace.TransformError): trace.prepare(str(source), str(source))
        self.assertEqual(source.read_bytes(), original)

if __name__ == '__main__': unittest.main()
