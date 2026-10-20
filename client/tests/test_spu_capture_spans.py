"""Synthetic schema-3 capture contracts. No target/game bytes or execution."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import spu_job_capture as capture

WORK = Path(os.environ.get('FOLKLORE_TEST_WORK', str(Path(__file__).resolve().parents[3] / 'work')))


class SpanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='spu-span-test-', dir=WORK)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root / 'capture'
        self.directory.mkdir()

    def record(self, ordinal=0, spans=((0x60000, 4),), schema=3, job=0x30000):
        image = bytes(range(16))
        desc = bytearray(128)
        desc[:8] = (0x40000).to_bytes(8, 'big')
        desc[9] = 1
        fp = capture.runtime_fingerprint(image)
        prefix = f'spurs-job-{job:08X}-{fp:016X}-{ordinal:02d}'
        meta = dict(schema_version=schema, job_ea=f'0x{job:08X}', descriptor_size=128,
                    raw_binary_ea='0x0000000000040000', binary_ea='0x00040000', binary_flags=0,
                    image_size=16, runtime_fingerprint=f'0x{fp:016X}', fingerprint_seed=str(capture.SEED),
                    descriptor_file=prefix+'.desc.bin', image_file=prefix+'.image.bin',
                    snapshot_consistency='separate checked copies; guest not quiesced')
        (self.directory/meta['descriptor_file']).write_bytes(desc)
        (self.directory/meta['image_file']).write_bytes(image)
        if schema == 3:
            meta['capture_spans'] = []
            for index, (ea, size) in enumerate(spans):
                leaf = prefix+f'.span-{index:02d}.bin'
                payload = bytes((i * 37 + index) & 255 for i in range(size))
                (self.directory/leaf).write_bytes(payload)
                meta['capture_spans'].append(dict(ea=f'0x{ea:08X}', size=size, file=leaf,
                                                  consistency=capture.SPAN_CONSISTENCY))
        elif schema == 2:
            meta.update(auxiliary_ea='0x00060000', auxiliary_size=4, auxiliary_file=prefix+'.aux.bin',
                        auxiliary_consistency=capture.SPAN_CONSISTENCY)
            (self.directory/meta['auxiliary_file']).write_bytes(b'ABCD')
        self.save(prefix, meta)
        return prefix, meta

    def save(self, prefix, meta):
        (self.directory/(prefix+'.json')).write_text(json.dumps(meta), encoding='utf-8')

    def inspect(self, **kwargs):
        return capture.inspect_capture(self.directory, **kwargs)

    def invalid(self, **kwargs):
        result = self.inspect(**kwargs)
        self.assertEqual(result['status'], 'invalid', result)
        self.assertTrue(result['errors'], result)
        self.assertEqual(result['records'], [], result)

    def test_single_span_hash_hex_and_unrequested_validation(self):
        prefix, meta = self.record()
        for kwargs in ({}, {'expected_spans': [(0x60000, 4)]}):
            result = self.inspect(**kwargs)
            self.assertEqual(result['status'], 'verified', result)
            span = result['records'][0]['capture_spans'][0]
            data = (self.directory/meta['capture_spans'][0]['file']).read_bytes()
            self.assertEqual(span, dict(file=prefix+'.span-00.bin', ea='0x00060000', size=4,
                                      sha256=hashlib.sha256(data).hexdigest(), hex=data.hex(),
                                      consistency=capture.SPAN_CONSISTENCY))

    def test_full_command_and_state_capture_has_no_large_hex(self):
        self.record(spans=((0x60000, 32), (0x80000, 84048)))
        result = self.inspect(expected_spans=[(0x60000, 32), (0x80000, 84048)])
        self.assertEqual(result['status'], 'verified', result)
        spans = result['records'][0]['capture_spans']
        self.assertEqual(len(spans[0]['hex']), 64)
        self.assertNotIn('hex', spans[1])
        self.assertEqual(spans[1]['size'], 84048)

    def test_hex_boundary_and_maximum_span(self):
        for size in (64, 65, 131072):
            with self.subTest(size=size):
                prefix, meta = self.record(spans=((0x60000, size),))
                result = self.inspect()
                self.assertEqual(result['status'], 'verified', result)
                self.assertEqual('hex' in result['records'][0]['capture_spans'][0], size <= 64)

    def test_two_adjacent_spans_at_32bit_end(self):
        self.record(spans=((0xFFFFFFF8, 4), (0xFFFFFFFC, 4)))
        self.assertEqual(self.inspect()['status'], 'verified')

    def test_two_spans_exact_total_budget(self):
        self.record(spans=((0x60000, 65536), (0x70000, 65536)))
        result = self.inspect(expected_spans=[(0x60000, 65536), (0x70000, 65536)])
        self.assertEqual(result['status'], 'verified', result)
        self.assertTrue(all('hex' not in s for s in result['records'][0]['capture_spans']))

    def test_global_file_budget_and_extra_non_file(self):
        self.record()
        for index in range(78):
            (self.directory/f'extra-{index}').write_bytes(b'')
        self.invalid()

    def test_unexpected_directory_is_invalid(self):
        self.record()
        (self.directory/'nested').mkdir()
        self.invalid()

    def test_selected_index_two_mixed_schema1_and_schema3(self):
        self.record(0, schema=1)
        self.record(1, schema=1)
        self.record(2)
        self.assertEqual(self.inspect(expected_spans=[(0x60000, 4)], expected_span_index=2)['status'], 'verified')
        self.invalid(expected_spans=[(0x60000, 4)])
        self.invalid(expected_spans=[(0x60000, 4)], expected_span_index=1)
        self.invalid(expected_spans=[(0x60000, 4)], expected_span_index=3)

    def test_all_records_requested_without_index(self):
        self.record(0)
        self.record(1)
        self.assertEqual(self.inspect(expected_spans=[(0x60000, 4)])['status'], 'verified')
        self.invalid(expected_spans=[(0x60000, 4)], expected_span_index=0)

    def test_unselected_auxiliary_record_rejected(self):
        self.record(0, schema=2)
        self.record(2)
        self.invalid(expected_spans=[(0x60000, 4)], expected_span_index=2)

    def test_request_order_size_and_ea_must_match(self):
        self.record(spans=((0x60000, 4), (0x70000, 8)))
        for request in ([(0x70000, 8), (0x60000, 4)], [(0x60000, 4)],
                        [(0x60000, 8), (0x70000, 8)], [(0x60010, 4), (0x70000, 8)]):
            with self.subTest(request=request):
                self.invalid(expected_spans=request)

    def test_empty_request_behavior(self):
        self.assertEqual(self.inspect()['status'], 'none')
        self.invalid(expected_spans=[(0x60000, 4)])
        self.invalid(expected_spans=[(0x60000, 4)], expected_span_index=0)

    def test_request_validation_precedes_filesystem_access(self):
        bad_spans = ([], True, '0x10000:4', [(0x10000,)], [(True, 4)], [(0x10000, True)],
                     [(0xFFFF, 4)], [(0x10000, 0)], [(0x10000, 131073)], [(0xFFFFFFFF, 2)],
                     [(0x10000, 4), (0x10002, 4)], [(0x10000, 70000), (0x40000, 70000)],
                     [(0x10000, 1)] * 3)
        requests = [dict(expected_spans=s) for s in bad_spans]
        requests += [dict(expected_spans=[(0x10000, 4)], expected_span_index=i) for i in (True, -1, 16, '2')]
        requests += [dict(expected_span_index=0), dict(expected_aux_ea=0x10000, expected_spans=[(0x10000, 4)])]
        with mock.patch.object(Path, 'is_symlink', side_effect=AssertionError('filesystem accessed')):
            for kwargs in requests:
                with self.subTest(kwargs=kwargs):
                    self.invalid(**kwargs)

    def test_metadata_span_validation(self):
        prefix, valid = self.record()
        bad_values = [('ea', '0x0000FFFF'), ('ea', '0x000a0000'), ('ea', '0x100000000'),
                      ('ea', '0xFFFFFFFF'), ('ea', 0x60000), ('size', True), ('size', 0),
                      ('size', 131073), ('size', 4.0), ('file', '../outside.bin'),
                      ('file', '/outside.bin'), ('file', 'sub\\data.bin'),
                      ('file', prefix+'.span-01.bin'), ('consistency', 'coherent')]
        for key, value in bad_values:
            with self.subTest(key=key, value=value):
                meta = copy.deepcopy(valid)
                meta['capture_spans'][0][key] = value
                self.save(prefix, meta)
                self.invalid()
        for key in ('ea', 'size', 'file', 'consistency'):
            meta = copy.deepcopy(valid)
            del meta['capture_spans'][0][key]
            self.save(prefix, meta)
            self.invalid()
        meta = copy.deepcopy(valid)
        meta['capture_spans'][0]['extra'] = 1
        self.save(prefix, meta)
        self.invalid()

    def test_metadata_array_missing_empty_or_extra(self):
        prefix, valid = self.record()
        for spans in (None, [], {}, [valid['capture_spans'][0]] * 3):
            meta = copy.deepcopy(valid)
            meta['capture_spans'] = spans
            self.save(prefix, meta)
            self.invalid()
        del valid['capture_spans']
        self.save(prefix, valid)
        self.invalid()

    def test_overlap_and_combined_budget(self):
        prefix, meta = self.record(spans=((0x60000, 4), (0x70000, 4)))
        meta['capture_spans'][1]['ea'] = '0x00060002'
        self.save(prefix, meta)
        self.invalid()
        meta['capture_spans'][0]['size'] = 70000
        meta['capture_spans'][1].update(ea='0x00090000', size=70000)
        self.save(prefix, meta)
        self.invalid()

    def test_schema_versions_cannot_mix_fields(self):
        prefix, valid = self.record()
        for schema in (1, 2):
            meta = copy.deepcopy(valid)
            meta['schema_version'] = schema
            self.save(prefix, meta)
            self.invalid()
        for key in ('auxiliary_ea', 'auxiliary_size', 'auxiliary_file', 'auxiliary_consistency'):
            meta = copy.deepcopy(valid)
            meta[key] = 1
            self.save(prefix, meta)
            self.invalid()

    def test_old_schema2_changed_to3_is_invalid(self):
        prefix, meta = self.record(schema=2)
        self.assertEqual(self.inspect(expected_aux_ea=0x60000)['status'], 'verified')
        meta['schema_version'] = 3
        self.save(prefix, meta)
        self.invalid()

    def test_duplicate_json_keys_top_and_nested(self):
        prefix, meta = self.record()
        original = json.dumps(meta)
        for text in ('{"schema_version":3,'+original[1:],
                     original.replace('"size": 4', '"size": 4, "size": 4', 1)):
            (self.directory/(prefix+'.json')).write_text(text)
            self.invalid()

    def test_global_ordinal_unique_even_different_job(self):
        self.record(0, job=0x30000)
        self.record(0, job=0x30100)
        self.invalid()

    def test_ordinal_max_and_out_of_range(self):
        self.record(15)
        self.assertEqual(self.inspect(expected_spans=[(0x60000, 4)], expected_span_index=15)['status'], 'verified')
        self.record(16)
        self.invalid()

    def test_span_payload_missing_short_long_and_unreferenced(self):
        prefix, meta = self.record()
        path = self.directory/meta['capture_spans'][0]['file']
        path.unlink()
        self.invalid()
        for payload in (b'123', b'12345'):
            path.write_bytes(payload)
            self.invalid()
        path.write_bytes(b'1234')
        (self.directory/'unused.bin').write_bytes(b'x')
        self.invalid()

    def test_unreadable_payload_becomes_invalid_result(self):
        _, meta = self.record()
        blocked = self.directory/meta['capture_spans'][0]['file']
        real_open = Path.open
        def guarded(path, *args, **kwargs):
            if path == blocked:
                raise PermissionError('synthetic unreadable span')
            return real_open(path, *args, **kwargs)
        with mock.patch.object(Path, 'open', guarded):
            self.invalid()

    def test_partial_valid_record_not_returned_on_later_failure(self):
        self.record(0)
        prefix, meta = self.record(1)
        meta['capture_spans'][0]['size'] = True
        self.save(prefix, meta)
        self.invalid()

    def test_hardlinked_span_is_rejected(self):
        _, meta = self.record()
        path = self.directory/meta['capture_spans'][0]['file']
        try:
            os.link(path, self.root/'other-link')
        except OSError as error:
            self.skipTest(str(error))
        self.invalid()

    def test_symlinked_span_is_rejected(self):
        _, meta = self.record()
        path = self.directory/meta['capture_spans'][0]['file']
        outside = self.root/'outside.bin'
        outside.write_bytes(b'abcd')
        path.unlink()
        try:
            path.symlink_to(outside)
        except OSError as error:
            self.skipTest(str(error))
        self.invalid()

    def test_reparse_attribute_rejected(self):
        self.record()
        real_lstat = Path.lstat
        def reparse(path, *args, **kwargs):
            actual = real_lstat(path, *args, **kwargs)
            if path.name.endswith('.span-00.bin'):
                return type('Info', (), dict(st_mode=actual.st_mode, st_nlink=1, st_file_attributes=0x400))()
            return actual
        with mock.patch.object(Path, 'lstat', reparse):
            self.invalid()


if __name__ == '__main__':
    unittest.main()
