"""CPU-only guards for the PCM producer instrumentation preparation."""
import hashlib
import importlib.util
import os
import re
from pathlib import Path
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location(
    "producer_preparation", SCRIPTS / "prepare_audio_producer_trace.py")
prep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prep)


@unittest.skipUnless(os.environ.get("FOLKLORE_TRACE_SOURCE"),
                     "set FOLKLORE_TRACE_SOURCE to the original generated TU")
class PreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path(os.environ["FOLKLORE_TRACE_SOURCE"])
        cls.original = cls.source.read_bytes()

    def test_shape_and_disabled_guards(self):
        self.assertEqual(hashlib.sha256(self.original).hexdigest(), prep.ORIGINAL_SHA256)
        output = prep.transform(self.original)
        self.assertEqual(bytes(v for v in output if v >= 128),
                         bytes(v for v in self.original if v >= 128))
        s = output.decode("latin-1")
        self.assertEqual(s.count(prep.MARKER), 1)
        self.assertEqual(s.count("folklore_audio_producer_trace::emit("), 4)
        self.assertEqual(s.count("ctx->thread_id == 9 && folklore_audio_producer_trace::active()"), 4)
        self.assertEqual(s.count("[](uint32_t ea) { return vm_read32(ea); }"), 13)
        self.assertEqual(s.count("folklore_audio_producer_trace::phase_event("), 11)
        self.assertEqual(s.count("folklore_audio_producer_trace::nested_phase_event("), 12)
        self.assertEqual(s.count("func_002A4D24(ctx);"), 1)
        self.assertEqual(s.count("lv2_syscall(ctx);"),
                         self.original.decode("latin-1").count("lv2_syscall(ctx);"))
        self.assertNotIn("\n", s.replace("\r\n", ""))

    def test_original_control_flow_and_arguments_byte_identical(self):
        s = prep.transform(self.original).decode('latin-1').replace('\r\n', '\n')
        # Every added call is enclosed in its own feature guard. Removing only
        # those blocks and the one marker must recover every original byte.
        stripped = re.sub(r'#if defined\(FOLKLORE_AUDIO_PRODUCER_TRACE\) && FOLKLORE_AUDIO_PRODUCER_TRACE\n.*?#endif\n',
                          '', s, flags=re.S)
        stripped = stripped.replace(f'/* {prep.MARKER}: opt-in, prepared source copy only. */\n', '')
        self.assertEqual(stripped.replace('\n', '\r\n').encode('latin-1'), self.original)
        for phase in range(1, 6):
            self.assertEqual(s.count(f'phase_event({phase}u, 1u,'), 1)
            self.assertEqual(s.count(f'phase_event({phase}u, 2u,'), 1)
        self.assertEqual(s.count('phase_event(6u, 1u,'), 1)
        for phase in range(7, 13):
            self.assertEqual(s.count(f'nested_phase_event({phase}u, 1u,'), 1)
            self.assertEqual(s.count(f'nested_phase_event({phase}u, 2u,'), 1)

    def test_repeatable_and_rejects_drift(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            receipt = prep.prepare(self.source, out, True)
            self.assertEqual(receipt, prep.prepare(self.source, out, True))
            self.assertEqual(hashlib.sha256((out / prep.SOURCE_NAME).read_bytes()).hexdigest(),
                             receipt["prepared_sha256"])
            self.assertEqual((out / prep.HEADER_NAME).read_bytes(),
                             (SCRIPTS / prep.HEADER_NAME).read_bytes())
            before = (out / prep.SOURCE_NAME).read_bytes()
            (out / prep.HEADER_NAME).write_bytes(b"drift")
            with self.assertRaisesRegex(ValueError, "refusing to overwrite"):
                prep.prepare(self.source, out, True)
            self.assertEqual((out / prep.SOURCE_NAME).read_bytes(), before)
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            prep.transform(self.original + b"\r\n")


if __name__ == "__main__":
    unittest.main()
