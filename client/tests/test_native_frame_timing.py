"""Tests for native/frame_timing_probe.h and scripts/summarize_native_frame_timing.py.

Usage: python tests/test_native_frame_timing.py [--cxx PATH] [--temp-root DIR] [unittest args]
Without --cxx only the synthetic summarizer tests run."""
import argparse,csv,importlib.util,json,os,subprocess,sys,tempfile,unittest
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
NATIVE=ROOT/'native'
ENV_VAR='FOLKLORE_FRAME_TIMING_CSV'
CXX=None
TEMP_ROOT=HERE

_spec=importlib.util.spec_from_file_location('summarize_native_frame_timing',ROOT/'scripts'/'summarize_native_frame_timing.py')
summarizer=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(summarizer)

CSV_HEADERS={'legacy':['present_number','present_begin_qpc','present_end_qpc','qpc_frequency'],'generic':['present_number','present_begin_ticks','present_end_ticks','clock_frequency']}

def timeline(intervals_ms,first_end_ms=100):
    ends=[first_end_ms]
    for interval in intervals_ms: ends.append(ends[-1]+interval)
    return ends

def write_run(directory,*,frequency,ends_ms,windows_ms,schema='generic',marker_schema=None,duration_ms=2,mutate=None,marker_frequency=None):
    directory=Path(directory);scale=frequency//1000 if frequency>0 else 1
    rows=[[i+1,(end-duration_ms)*scale,end*scale,frequency] for i,end in enumerate(ends_ms)]
    if mutate: mutate(rows)
    with (directory/'frame-timing.csv').open('w',newline='') as out:
        writer=csv.writer(out);writer.writerow(CSV_HEADERS[schema]);writer.writerows(rows)
    marker_schema=marker_schema or schema
    marker_frequency=frequency if marker_frequency is None else marker_frequency
    windows=[]
    for index,(start,end) in enumerate(windows_ms):
        bounds=(start*scale,end*scale,(end-start)/1000)
        keys=('start_ticks','end_ticks','seconds') if marker_schema=='generic' else ('start_qpc','end_qpc','qpc_seconds')
        windows.append(dict(index=index,memory_before={},memory_after={},**dict(zip(keys,bounds))))
    calibration={'clock_frequency' if marker_schema=='generic' else 'qpc_frequency':marker_frequency}
    marker=dict(binary_sha256='0'*64,clock_calibration=calibration,warmup_seconds=5,windows=windows)
    (directory/'timing-windows.json').write_text(json.dumps(marker))
    return directory

class TempDirCase(unittest.TestCase):
    def setUp(self):
        self._tmp=tempfile.TemporaryDirectory(dir=TEMP_ROOT);self.addCleanup(self._tmp.cleanup)
        self.tmp=Path(self._tmp.name)
    def run_dir(self,name):
        path=self.tmp/name;path.mkdir();return path

class SummarizerTest(TempDirCase):
    KNOWN=[10]*99+[30]+[10]*99+[30]

    def known_run(self,name,**kwargs):
        ends=timeline(self.KNOWN)
        return write_run(self.run_dir(name),ends_ms=ends,windows_ms=[(ends[0],ends[-1])],**kwargs)

    def test_known_mean_and_one_percent_low(self):
        r=summarizer.summarize(self.known_run('known',frequency=1_000_000_000))
        pooled=r['pooled_completion_intervals']
        self.assertEqual(pooled['samples'],200)
        self.assertAlmostEqual(pooled['mean_ms'],10.2)
        self.assertAlmostEqual(pooled['median_ms'],10.0)
        self.assertAlmostEqual(pooled['p99_ms'],10.0)
        self.assertAlmostEqual(pooled['max_ms'],30.0)
        self.assertAlmostEqual(r['average_successful_present_rate_hz'],1000/10.2)
        self.assertEqual(r['one_percent_low_sample_count'],2)
        self.assertAlmostEqual(r['one_percent_low_hz'],1000/30)
        self.assertAlmostEqual(r['pooled_present_call_cpu_duration']['mean_ms'],2.0)
        self.assertIsNone(r['loading_time']);self.assertIsNone(r['gpu_execution_time'])
        self.assertTrue((self.tmp/'known'/'frame-timing-summary.json').is_file())

    def test_legacy_and_generic_schemas_equivalent(self):
        legacy=summarizer.summarize(self.known_run('legacy',frequency=10_000_000,schema='legacy'))
        generic=summarizer.summarize(self.known_run('generic',frequency=1_000_000_000,schema='generic'))
        mixed=summarizer.summarize(self.known_run('mixed',frequency=1_000_000_000,schema='generic',marker_schema='legacy'))
        self.assertEqual(legacy['csv_schema'],'legacy_qpc');self.assertEqual(generic['csv_schema'],'generic')
        for other in (generic,mixed):
            for key in ('pooled_completion_intervals','pooled_present_call_cpu_duration'):
                for stat,value in legacy[key].items(): self.assertAlmostEqual(value,other[key][stat],msg=f'{key}.{stat}')
            for key in ('average_successful_present_rate_hz','one_percent_low_hz'):
                self.assertAlmostEqual(legacy[key],other[key])
            self.assertAlmostEqual(legacy['windows'][0]['seconds'],other['windows'][0]['seconds'])

    def test_crossing_boundary_intervals_excluded(self):
        ends=timeline([10]*150+[500]+[10]*150)
        run=write_run(self.run_dir('cross'),frequency=1_000_000_000,ends_ms=ends,windows_ms=[(ends[0]+5,ends[150]),(ends[151],ends[301])])
        r=summarizer.summarize(run)
        self.assertEqual([w['completion_intervals']['samples'] for w in r['windows']],[149,150])
        self.assertEqual(r['pooled_completion_intervals']['samples'],299)
        self.assertAlmostEqual(r['pooled_completion_intervals']['max_ms'],10.0)
        self.assertAlmostEqual(r['one_percent_low_hz'],100.0)

    def assertRejected(self,name,**kwargs):
        ends=kwargs.pop('ends',timeline([10]*150))
        windows=kwargs.pop('windows',[(ends[0],ends[-1])])
        kwargs.setdefault('frequency',1_000_000_000)
        run=write_run(self.run_dir(name),ends_ms=ends,windows_ms=windows,**kwargs)
        with self.assertRaises(summarizer.TimingError): summarizer.summarize(run)

    def test_rejects_nonmonotonic_end(self):
        def mutate(rows): rows[50][2]=rows[49][2]
        self.assertRejected('end',mutate=mutate)

    def test_rejects_nonmonotonic_begin(self):
        def mutate(rows): rows[50][1]=rows[49][1]
        self.assertRejected('begin',mutate=mutate)

    def test_rejects_present_number_gap_and_repeat(self):
        def gap(rows):
            for row in rows[60:]: row[0]+=1
        def repeat(rows): rows[60][0]=rows[59][0]
        self.assertRejected('gap',mutate=gap);self.assertRejected('repeat',mutate=repeat)

    def test_rejects_end_before_begin(self):
        def mutate(rows): rows[20][1]=rows[20][2]+1
        self.assertRejected('negative',mutate=mutate)

    def test_rejects_overlapping_calls(self):
        def mutate(rows): rows[20][1]=rows[19][2]-1
        self.assertRejected('overlapping_calls',mutate=mutate)

    def test_rejects_inconsistent_window_seconds(self):
        run=self.known_run('wrong_seconds',frequency=1_000_000_000)
        path=run/'timing-windows.json';marker=json.loads(path.read_text())
        marker['windows'][0]['seconds']*=2;path.write_text(json.dumps(marker))
        with self.assertRaises(summarizer.TimingError): summarizer.summarize(run)

    def test_rejects_partial_window_recording(self):
        run=self.known_run('partial_window',frequency=1_000_000_000)
        path=run/'timing-windows.json';marker=json.loads(path.read_text())
        marker['windows'][0]['end_ticks']+=1_000_000_000
        marker['windows'][0]['seconds']+=1;path.write_text(json.dumps(marker))
        with self.assertRaises(summarizer.TimingError): summarizer.summarize(run)

    def test_rejects_invalid_frequency(self):
        self.assertRejected('zero',frequency=0)
        def mixed(rows): rows[10][3]+=1
        self.assertRejected('mixed',mutate=mixed)
        self.assertRejected('mismatch',marker_frequency=10_000_000)
        self.assertRejected('marker_zero',marker_frequency=0)

    def test_rejects_invalid_windows(self):
        ends=timeline([10]*300)
        self.assertRejected('reversed',ends=ends,windows=[(ends[200],ends[0])])
        self.assertRejected('overlap',ends=ends,windows=[(ends[0],ends[200]),(ends[100],ends[300])])
        self.assertRejected('short',ends=ends,windows=[(ends[0],ends[50])])

    def test_rejects_unknown_header(self):
        run=self.run_dir('header')
        (run/'frame-timing.csv').write_text('present_number,begin,end,frequency\n1,1,2,1000\n')
        with self.assertRaises(summarizer.TimingError): summarizer.load_rows(run/'frame-timing.csv')

SMOKE_SOURCE=r'''
#include "frame_timing_probe.h"
#include <cstdio>
#include <cstdlib>
#include <cstdint>

static int count_lines(const char* path) {
    std::FILE* f = std::fopen(path, "rb");
    if (!f) return -1;
    int lines = 0;
    for (int c; (c = std::fgetc(f)) != EOF;) if (c == '\n') ++lines;
    std::fclose(f);
    return lines;
}

int main(int argc, char** argv) {
    const int rows = argc > 1 ? std::atoi(argv[1]) : 0;
    bool active = false;
    {
        FrameTimingProbe probe;
        active = probe.active();
        if (active) {
            std::uint64_t previous = probe.tick();
            for (int i = 1; i <= rows; ++i) {
                std::uint64_t begin = probe.tick();
                for (long long spin = 0; begin <= previous; ++spin) { if (spin > 100000000LL) return 3; begin = probe.tick(); }
                std::uint64_t end = probe.tick();
                for (long long spin = 0; end <= begin; ++spin) { if (spin > 100000000LL) return 4; end = probe.tick(); }
                probe.record(static_cast<std::uint64_t>(i), begin, end);
                previous = end;
            }
        }
    }
    const char* path = std::getenv("FOLKLORE_FRAME_TIMING_CSV");
    std::printf("active=%d lines=%d\n", active ? 1 : 0, active && path ? count_lines(path) : -1);
    return 0;
}
'''
SMOKE_ROWS=125

class HeaderSmokeTest(TempDirCase):
    @classmethod
    def setUpClass(cls):
        if not CXX: raise unittest.SkipTest('pass --cxx PATH to compile the header smoke test')
        cls._build=tempfile.TemporaryDirectory(dir=TEMP_ROOT);cls.addClassCleanup(cls._build.cleanup)
        work=Path(cls._build.name);source=work/'smoke.cpp';source.write_text(SMOKE_SOURCE)
        cls.exe=work/('smoke.exe' if os.name=='nt' else 'smoke')
        built=subprocess.run([CXX,'-std=c++17','-O1','-D_CRT_SECURE_NO_WARNINGS','-I',str(NATIVE),str(source),'-o',str(cls.exe)],capture_output=True,text=True)
        if built.returncode: raise AssertionError(f'compile failed:\n{built.stdout}\n{built.stderr}')

    def launch(self,csv_value,cwd):
        env=os.environ.copy();env.pop(ENV_VAR,None)
        if csv_value is not None: env[ENV_VAR]=str(csv_value)
        done=subprocess.run([str(self.exe),str(SMOKE_ROWS)],env=env,cwd=cwd,capture_output=True,text=True,timeout=120)
        self.assertEqual(done.returncode,0,done.stderr)
        fields=dict(part.split('=') for part in done.stdout.split())
        return int(fields['active']),int(fields['lines']),done.stderr

    def test_disabled_creates_no_csv(self):
        for name,value in (('unset',None),('empty','')):
            out=self.run_dir(name)
            active,_,_=self.launch(value,out)
            self.assertEqual(active,0);self.assertEqual(list(out.iterdir()),[])

    def test_enabled_writes_monotonic_rows(self):
        out=self.run_dir('enabled');path=out/'frame-timing.csv'
        active,lines,stderr=self.launch(path,out)
        self.assertEqual(active,1);self.assertEqual(lines,SMOKE_ROWS+1)
        self.assertIn('[frame-timing]',stderr)
        schema,frequency,rows=summarizer.load_rows(path)
        self.assertEqual(schema,'generic');self.assertGreater(frequency,0)
        if sys.platform.startswith('linux'):
            self.assertEqual(frequency,1_000_000_000);self.assertIn('CLOCK_MONOTONIC',stderr);self.assertNotIn('QPC',stderr)
        self.assertEqual([r[0] for r in rows],list(range(1,SMOKE_ROWS+1)))

    def test_unavailable_path_stays_inactive(self):
        out=self.run_dir('unavailable');path=out/'missing'/'frame-timing.csv'
        active,_,_=self.launch(path,out)
        self.assertEqual(active,0);self.assertFalse(path.exists());self.assertFalse(path.parent.exists())

if __name__=='__main__':
    parser=argparse.ArgumentParser(add_help=False)
    parser.add_argument('--cxx');parser.add_argument('--temp-root',type=Path)
    args,rest=parser.parse_known_args()
    CXX=args.cxx
    if args.temp_root: TEMP_ROOT=args.temp_root.resolve();TEMP_ROOT.mkdir(parents=True,exist_ok=True)
    unittest.main(argv=[sys.argv[0]]+rest)
