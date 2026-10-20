import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
import os
import shutil
import subprocess

spec = importlib.util.spec_from_file_location('probe', Path(__file__).parents[1]/'scripts/prepare_timing_probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.src = self.root/'source'; self.src.mkdir()
        self.out = self.root/'output'
        self.data = ('#include "ppu_recomp.h"\r\n' + ''.join(f'loc_{x}:\r\n' for x in probe.SITES) +
            ''.join(f'ctx->lr = 0x{x}; func_0021CCB4(ctx); DRAIN_TRAMPOLINE(ctx);\r\n' for x in probe.RETURNS)).encode()
        (self.src/probe.NAME).write_bytes(self.data)
        (self.src/'unchanged.cpp').write_bytes(b'unchanged\r\n')
        old = probe.SOURCE_SHA
        probe.SOURCE_SHA = hashlib.sha256(self.data).hexdigest()
        self.addCleanup(setattr, probe, 'SOURCE_SHA', old)

    def test_injections_preserve_original_bytes(self):
        receipt = probe.prepare(self.src, self.out)
        data = (self.out/probe.NAME).read_bytes()
        data = data.replace(probe.HEADER.replace('\n','\r\n').encode(), b'')
        for x in probe.SITES:
            data = data.replace(f'        folklore_timing_probe(ctx, 0x{x});\r\n'.encode(), b'')
        for x in probe.RETURNS:
            data = data.replace(f' folklore_timing_probe(ctx, 0x{x});'.encode(), b'')
        self.assertEqual(data, self.data)
        self.assertEqual((self.src/probe.NAME).read_bytes(), self.data)
        self.assertEqual((self.out/'unchanged.cpp').read_bytes(), b'unchanged\r\n')
        self.assertTrue(receipt['guest_timebase_unchanged'])

    def test_unsupported_source_fails_before_output(self):
        (self.src/probe.NAME).write_bytes(self.data+b'x')
        with self.assertRaisesRegex(ValueError, 'hash'): probe.prepare(self.src, self.out)
        self.assertFalse(self.out.exists())

    def test_missing_site_fails_before_output(self):
        data = self.data.replace(b'loc_00181018:', b'loc_00181019:')
        (self.src/probe.NAME).write_bytes(data)
        probe.SOURCE_SHA = hashlib.sha256(data).hexdigest()
        with self.assertRaisesRegex(ValueError, 'label'): probe.prepare(self.src, self.out)
        self.assertFalse(self.out.exists())

    def test_existing_output_and_overlap_rejected(self):
        self.out.mkdir()
        for out in (self.out, self.src, self.src/'child', self.root):
            with self.assertRaises(ValueError): probe.prepare(self.src, out)

    def test_source_directory_rejected(self):
        (self.src/'nested').mkdir()
        with self.assertRaisesRegex(ValueError, 'flat'): probe.prepare(self.src, self.out)
        self.assertFalse(self.out.exists())

    def test_reserved_receipt_rejected(self):
        (self.src/'timing-probe-receipt.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'reserved'): probe.prepare(self.src, self.out)
        self.assertFalse(self.out.exists())

    def test_compiled_observer_preserves_context_and_float_status(self):
        compiler = shutil.which('clang++')
        if not compiler: self.skipTest('Clang++ required for native observer check')
        code = r'''
#include <cstdint>
#include <cstring>
#include <cfenv>
struct ppu_context { uint64_t lr; uint64_t gpr[32]; double fpr[32]; };
''' + probe.HEADER + r'''
int main() {
    ppu_context ctx{}, before{};
    uint64_t special = 0x7ff0000000000001ULL;
    std::memcpy(&ctx.fpr[31], &special, 8);
    ctx.gpr[27] = 3333; ctx.lr = 0x180F64;
    std::memcpy(&before, &ctx, sizeof ctx);
    std::feclearexcept(FE_ALL_EXCEPT);
    std::feraiseexcept(FE_DIVBYZERO);
    int flags = std::fetestexcept(FE_ALL_EXCEPT);
    for(unsigned i=0;i<40002;++i) folklore_timing_probe(&ctx, 0x181018);
    return std::memcmp(&ctx,&before,sizeof ctx) || std::fetestexcept(FE_ALL_EXCEPT)!=flags;
}
'''
        source = self.root/'observer.cpp'; source.write_text(code)
        binary = self.root/('observer.exe' if os.name=='nt' else 'observer')
        subprocess.run([compiler,'-std=c++20','-O1',str(source),'-o',str(binary)],check=True,capture_output=True)
        for enabled, expected in (('0',0),('1',40000)):
            log = self.root/('observer-'+enabled+'.log')
            with log.open('wb') as stream:
                subprocess.run([str(binary)],env=dict(os.environ,FOLKLORE_TIMING_PROBE=enabled),
                               stderr=stream,check=True)
            with log.open() as stream:
                self.assertEqual(sum('[timing-probe]' in line for line in stream),expected)

if __name__ == '__main__': unittest.main()
