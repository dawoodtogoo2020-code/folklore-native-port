"""Explicit span requests fail before target file access; no target execution."""
import argparse
from pathlib import Path
import sys
import unittest
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import run_native_boot as launcher

class SpanLauncherTests(unittest.TestCase):
    def test_observed_requests_and_inclusive_upper_boundary(self):
        self.assertEqual(launcher.normalize_capture_spans([(0x4501B900,32),(0x4501BE00,84048)],2),((0x4501B900,32),(0x4501BE00,84048)))
        self.assertEqual(launcher.normalize_capture_spans([(0xFFFFFFFF,1)],0),((0xFFFFFFFF,1),))
        self.assertIsNone(launcher.normalize_capture_spans(None))

    def test_bad_request_is_rejected_before_target_or_host_access(self):
        invalid=[[],[(0x10000,1)]*3,[(True,1)],[(0x10000,True)],[(0xFFFF,1)],[(0xFFFFFFFF,2)],[(0x10000,0)],[(0x10000,131073)],[(0x10000,100000),(0x40000,40000)],[(0x10000,32),(0x10010,32)],['0x10000:4'],'0x10000:4']
        with mock.patch.object(launcher,'host_config',side_effect=AssertionError('target configuration accessed')),mock.patch.object(launcher,'sha',side_effect=AssertionError('target file accessed')):
            for spans in invalid:
                with self.subTest(spans=spans),self.assertRaises(ValueError):
                    launcher.run(1,capture_spu_job=True,capture_spu_spans=spans)
            for index in (-1,16,True,'2',1.5):
                with self.subTest(index=index),self.assertRaises(ValueError):
                    launcher.run(1,capture_spu_job=True,capture_spu_spans=[(0x10000,4)],capture_spu_span_index=index)

    def test_missing_job_capture_mixed_modes_and_orphan_index(self):
        with mock.patch.object(launcher,'host_config',side_effect=AssertionError('target configuration accessed')):
            for args in ({'capture_spu_spans':[(0x10000,4)]},{'capture_spu_job':True,'capture_spu_spans':[(0x10000,4)],'capture_spu_aux_ea':0x10000},{'capture_spu_job':True,'capture_spu_span_index':2}):
                with self.subTest(args=args),self.assertRaises(ValueError):launcher.run(1,**args)

    def test_cli_span_is_canonical_bounded_and_not_truncated(self):
        self.assertEqual(launcher.parse_capture_span('0x4501b900:32'),(0x4501B900,32))
        for value in ('4501B900:32','0X4501B900:32','0x4501B900:032','0x4501B900:+32','0x4501B900:32\n','0x100000000:4','0xFFFF:4','0xFFFFFFFE:4','0x10000:131073','0x10000:0','0x10000:4:4'):
            with self.subTest(value=value),self.assertRaises(argparse.ArgumentTypeError):launcher.parse_capture_span(value)

    def test_cli_attempt_index_is_explicit_and_bounded(self):
        self.assertEqual(launcher.parse_capture_span_index('0'),0)
        self.assertEqual(launcher.parse_capture_span_index('15'),15)
        for value in ('02','-1','16','1\n','+1',' 1','true',''):
            with self.subTest(value=value),self.assertRaises(argparse.ArgumentTypeError):launcher.parse_capture_span_index(value)

if __name__=='__main__':unittest.main()
