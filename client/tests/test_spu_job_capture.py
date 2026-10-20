"""Private capture receipt verification; no game bytes or target execution."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import spu_job_capture as capture
import run_native_boot as launcher


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parents[1])
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.image = bytes(range(16))
        self.desc = bytearray(128)
        self.desc[:8] = (0x10010000).to_bytes(8, 'big')
        self.desc[8:10] = (1).to_bytes(2, 'big')
        fp = capture.runtime_fingerprint(self.image)
        self.prefix = f'spurs-job-45017080-{fp:016X}-00'
        self.meta = {'schema_version': 1, 'job_ea': '0x45017080', 'descriptor_size': 128,
            'raw_binary_ea': '0x0000000010010000', 'binary_flags': 0, 'binary_ea': '0x10010000',
            'image_size': 16, 'runtime_fingerprint': f'0x{fp:016X}', 'fingerprint_seed': str(capture.SEED),
            'descriptor_file': self.prefix+'.desc.bin', 'image_file': self.prefix+'.image.bin',
            'snapshot_consistency': 'separate checked copies; guest not quiesced'}

    def write(self):
        (self.directory/self.meta['descriptor_file']).write_bytes(self.desc)
        (self.directory/self.meta['image_file']).write_bytes(self.image)
        self.write_meta()

    def write_meta(self):
        (self.directory/(self.prefix+'.json')).write_text(json.dumps(self.meta))

    def add_auxiliary(self, data=b'\x01\x02\x00\x00'):
        self.meta.update(schema_version=2, auxiliary_ea='0x4501B900', auxiliary_size=4,
                         auxiliary_file=self.prefix+'.aux.bin',
                         auxiliary_consistency='separate checked copy; guest not quiesced')
        (self.directory/self.meta['auxiliary_file']).write_bytes(data)
        self.write_meta()

    def test_auxiliary_four_bytes_have_independent_hash_and_explicit_request(self):
        self.write()
        self.add_auxiliary()
        result = capture.inspect_capture(self.directory, expected_aux_ea=0x4501B900)
        self.assertEqual(result['status'], 'verified', result)
        record = result['records'][0]
        self.assertEqual(record['auxiliary_hex'], '01020000')
        self.assertEqual(record['auxiliary_sha256'], hashlib.sha256(bytes.fromhex('01020000')).hexdigest())
        self.assertEqual(record['auxiliary_ea'], '0x4501B900')

    def test_requested_auxiliary_cannot_be_missing_or_differ(self):
        self.assertEqual(capture.inspect_capture(self.directory, 0x4501B900)['status'], 'invalid')
        self.write()
        self.assertEqual(capture.inspect_capture(self.directory, 0x4501B900)['status'], 'invalid')
        self.add_auxiliary()
        self.assertEqual(capture.inspect_capture(self.directory, 0x4501B904)['status'], 'invalid')

    def test_auxiliary_metadata_rejects_unsafe_spans_paths_sizes_and_versions(self):
        self.write()
        self.add_auxiliary()
        valid = dict(self.meta)
        for key, value in [('auxiliary_ea', '0xFFFFFFFF'), ('auxiliary_ea', '0x0000FFFF'),
                           ('auxiliary_ea', '0x14501B900'), ('auxiliary_ea', '4501B900'),
                           ('auxiliary_size', True), ('auxiliary_size', 3),
                           ('auxiliary_file', '../outside.bin'), ('auxiliary_consistency', ''),
                           ('schema_version', 1), ('schema_version', 3)]:
            with self.subTest(key=key, value=value):
                self.meta = dict(valid)
                self.meta[key] = value
                self.write_meta()
                self.assertEqual(capture.inspect_capture(self.directory)['status'], 'invalid')
        for key in ('auxiliary_ea', 'auxiliary_size', 'auxiliary_file', 'auxiliary_consistency'):
            with self.subTest(missing=key):
                self.meta = dict(valid)
                del self.meta[key]
                self.write_meta()
                self.assertEqual(capture.inspect_capture(self.directory)['status'], 'invalid')

    def test_auxiliary_payload_must_be_exactly_four_bytes(self):
        self.write()
        for data in (b'', b'\x01\x02\x00', b'\x01\x02\x00\x00\xFF'):
            with self.subTest(size=len(data)):
                self.add_auxiliary(data)
                self.assertEqual(capture.inspect_capture(self.directory)['status'], 'invalid')

    def test_auxiliary_cli_and_run_validation_happen_before_target_access(self):
        self.assertEqual(launcher.parse_auxiliary_ea('0x4501b900'), 0x4501B900)
        for value in ('4501B900', '0X4501B900', '0x', '0x100000000', '0xFFFF', '0xFFFFFFFD', '0x10000\n'):
            with self.subTest(value=value):
                with self.assertRaises(launcher.argparse.ArgumentTypeError):
                    launcher.parse_auxiliary_ea(value)
        for value in (True, -1, 0x100000000, 0xFFFFFFFD):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    launcher.run(1, config={}, capture_spu_job=True, capture_spu_aux_ea=value)
        with self.assertRaises(ValueError):
            launcher.run(1, config={}, capture_spu_aux_ea=0x4501B900)

    def test_empty_directory_is_no_capture(self):
        self.assertEqual(capture.inspect_capture(self.directory)['status'], 'none')

    def test_checked_capture_verifies_bytes_sizes_and_sha256(self):
        self.write()
        result = capture.inspect_capture(self.directory)
        self.assertEqual(result['status'], 'verified', result)
        self.assertEqual(len(result['records']), 1)
        self.assertEqual(result['records'][0]['image_sha256'], hashlib.sha256(self.image).hexdigest())
        self.assertEqual(result['records'][0]['descriptor_sha256'], hashlib.sha256(self.desc).hexdigest())

    def test_payload_tampering_is_rejected(self):
        self.write()
        (self.directory/self.meta['image_file']).write_bytes(b'x'*16)
        self.assertEqual(capture.inspect_capture(self.directory)['status'], 'invalid')

    def test_declared_size_type_cannot_be_boolean(self):
        self.write()
        self.meta['image_size'] = True
        self.write_meta()
        self.assertEqual(capture.inspect_capture(self.directory)['status'], 'invalid')

    def test_metadata_cannot_redirect_to_other_paths(self):
        self.write()
        self.meta['image_file'] = '../outside.bin'
        self.write_meta()
        self.assertEqual(capture.inspect_capture(self.directory)['status'], 'invalid')

    def test_descriptor_flags_or_high_ea_cannot_be_truncated(self):
        self.write()
        for raw in (0x10010001, 0x110010000):
            with self.subTest(raw=raw):
                self.desc[:8] = raw.to_bytes(8, 'big')
                (self.directory/self.meta['descriptor_file']).write_bytes(self.desc)
                self.assertEqual(capture.inspect_capture(self.directory)['status'], 'invalid')

    def test_standard_fnv_seed_cannot_replace_runtime_seed(self):
        self.write()
        self.meta['fingerprint_seed'] = '14695981039346656037'
        self.write_meta()
        self.assertEqual(capture.inspect_capture(self.directory)['status'], 'invalid')

    def test_partial_capture_or_unreferenced_file_is_rejected(self):
        self.write()
        (self.directory/'extra.bin').write_bytes(b'owned unrelated canary')
        self.assertEqual(capture.inspect_capture(self.directory)['status'], 'invalid')
        (self.directory/'extra.bin').unlink()
        (self.directory/self.meta['image_file']).unlink()
        self.assertEqual(capture.inspect_capture(self.directory)['status'], 'invalid')

    def test_hardlinked_payload_is_rejected_without_changing_it(self):
        self.write()
        alias = self.directory/'image-alias.bin'
        try:
            os.link(self.directory/self.meta['image_file'], alias)
        except OSError as error:
            self.skipTest(str(error))
        self.assertEqual(capture.inspect_capture(self.directory)['status'], 'invalid')
        self.assertEqual(alias.read_bytes(), self.image)

    def test_malformed_or_nonobject_json_is_reported(self):
        self.write()
        for raw in (b'[]', b'{', b'null', b'\xff'):
            with self.subTest(raw=raw):
                (self.directory/(self.prefix+'.json')).write_bytes(raw)
                self.assertEqual(capture.inspect_capture(self.directory)['status'], 'invalid')

    def test_oversized_payload_rejected_before_hashing(self):
        self.write()
        (self.directory/self.meta['image_file']).write_bytes(bytes(capture.MAX_IMAGE+1))
        self.assertEqual(capture.inspect_capture(self.directory)['status'], 'invalid')

    def test_capture_requires_explicit_consistency_boundary(self):
        self.write()
        self.meta.pop('snapshot_consistency')
        self.write_meta()
        self.assertEqual(capture.inspect_capture(self.directory)['status'], 'invalid')


if __name__ == '__main__':
    unittest.main()
