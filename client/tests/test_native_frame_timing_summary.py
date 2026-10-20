"""Known arithmetic and window boundaries, not synthetic game performance."""
import csv,json,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from summarize_native_frame_timing import stats,summarize,TimingError

class TimingSummaryTest(unittest.TestCase):
    def test_nearest_rank(self):
        s=stats(list(range(1,101)))
        self.assertEqual((s['median_ms'],s['p95_ms'],s['p99_ms']),(50.5,95,99))

    def fixture(self,root,skip=None):
        with (root/'frame-timing.csv').open('w',newline='') as f:
            w=csv.writer(f);w.writerow(['present_number','present_begin_qpc','present_end_qpc','qpc_frequency'])
            for i in range(1,302):
                if i!=skip:w.writerow([i,10*i-2,10*i,10000])
        windows=[dict(index=1,qpc_seconds=.1,start_qpc=500,end_qpc=1500,memory_before={},memory_after={}),dict(index=2,qpc_seconds=.1,start_qpc=2000,end_qpc=3000,memory_before={},memory_after={})]
        (root/'timing-windows.json').write_text(json.dumps(dict(binary_sha256='fixture',clock_calibration={'qpc_frequency':10000},warmup_seconds=15,windows=windows)))

    def test_boundaries_and_low_definition(self):
        with tempfile.TemporaryDirectory(prefix='folklore-timing-') as d:
            root=Path(d);self.fixture(root);r=summarize(root)
            self.assertEqual([w['completion_intervals']['samples'] for w in r['windows']],[100,100])
            self.assertEqual(r['pooled_completion_intervals']['samples'],200)
            self.assertEqual(r['one_percent_low_hz'],1000)
            self.assertEqual(r['one_percent_low_sample_count'],2)
            self.assertAlmostEqual(r['pooled_present_call_cpu_duration']['median_ms'],.2)

    def test_missing_present_rejected(self):
        with tempfile.TemporaryDirectory(prefix='folklore-timing-') as d:
            root=Path(d);self.fixture(root,skip=75)
            with self.assertRaises(TimingError):summarize(root)

if __name__=='__main__':unittest.main()
