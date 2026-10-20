"""Synthetic failure gates for the real leaf-probe pipeline; no native tools."""
import contextlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / 'scripts'))
import leaf_probe as probe


class BodyGates(unittest.TestCase):
    def function(self, **changes):
        fields = dict(name='func_00001000', body_lines=['return;'],
                      calls=[], fallthrough_to=0)
        fields.update(changes)
        return SimpleNamespace(**fields)

    def test_realistic_body_and_comments_accepted_unchanged(self):
        lines = ['ctx->gpr[3] = ctx->gpr[13] | 0x0;',
                 'return; /* terminal instruction */']
        self.assertEqual(probe.checked_body(self.function(body_lines=lines)), '\n'.join(lines))

    def test_unresolved_bodies_rejected(self):
        for marker in ('TODO', 'unsupported', 'unknown'):
            with self.subTest(marker=marker), self.assertRaises(ValueError):
                probe.checked_body(self.function(body_lines=[f'/* {marker} */', 'return;']))

    def test_external_calls_rejected(self):
        with self.assertRaises(ValueError):
            probe.checked_body(self.function(calls=[0x2000]))

    def test_nonzero_fallthrough_rejected(self):
        with self.assertRaises(ValueError):
            probe.checked_body(self.function(fallthrough_to=0x1008))

    def test_non_gpr_writes_and_helpers_rejected(self):
        for statement in ('ctx->lr = 0;', 'ctx->cr = 0;',
                          'helper(ctx);', 'ctx->gpr[0] = helper();'):
            with self.subTest(statement=statement), self.assertRaises(ValueError):
                probe.checked_body(self.function(body_lines=[statement, 'return;']))


class PipelineGates(unittest.TestCase):
    def run_pipeline(self, mode):
        # Place synthetic temporary data beside the test repository, not on C:.
        with tempfile.TemporaryDirectory(prefix='.leaf-test-', dir=ROOT) as td:
            root = Path(td)
            elf = root / 'synthetic.elf'
            upstream = root / 'upstream'
            tools = upstream / 'tools'
            tools.mkdir(parents=True)
            compiler = root / 'fake-compiler'
            compiler.write_text('synthetic placeholder', encoding='utf-8')
            for module in ('ppu_disasm', 'ppu_lifter'):
                (tools / (module + '.py')).write_text('# synthetic module\n', encoding='utf-8')
            data = bytearray(132)
            data[:16] = b'\x7fELF\x02\x02\x01' + b'\x00' * 9
            struct.pack_into('>HHIQQQIHHHHHH', data, 16,
                             2, 21, 1, 0, 64, 0, 0, 64, 56, 1, 0, 0, 0)
            struct.pack_into('>IIQQQQQQ', data, 64, 1, 5, 128, 0x1000, 0, 4, 4, 1)
            struct.pack_into('>I', data, 128, probe.BLR)
            elf.write_bytes(data)
            report = root / 'report.json'
            function = SimpleNamespace(name='func_00001000', body_lines=['return;'],
                                       calls=[], fallthrough_to=0)
            disasm = SimpleNamespace(__file__=str(tools / 'ppu_disasm.py'),
                                     disassemble_bytes=lambda *args: [])
            lifter = SimpleNamespace(__file__=str(tools / 'ppu_lifter.py'),
                                     PPULifter=lambda: SimpleNamespace(lift_function=lambda *args: function))
            modules = {'ppu_disasm': disasm, 'ppu_lifter': lifter}
            phases = []

            def run_command(command, **kwargs):
                if '--version' in command:
                    phases.append('version')
                    return SimpleNamespace(stdout='synthetic compiler 1')
                if '-std=c11' in command:
                    phases.append('compile')
                    if mode == 'compile_failure':
                        raise ValueError('synthetic compile failure')
                    Path(command[command.index('-o') + 1]).write_bytes(b'synthetic executable, never launched')
                    return SimpleNamespace(stdout='')
                if command[0] == 'git':
                    phases.append('git')
                    return SimpleNamespace(stdout='' if 'status' in command else 'a' * 40)
                phases.append('run')
                output = kwargs['input']  # blr-only function preserves every GPR.
                if mode == 'short_output':
                    output = '\n'.join(output.splitlines()[:-1]) + '\n'
                elif mode == 'malformed_output':
                    rows = output.splitlines()
                    rows[0] = 'not-hex ' + ' '.join(rows[0].split()[1:])
                    output = '\n'.join(rows) + '\n'
                elif mode == 'gpr_mismatch':
                    rows = output.splitlines()
                    tokens = rows[0].split()
                    tokens[31] = '0000000000000001'
                    rows[0] = ' '.join(tokens)
                    output = '\n'.join(rows) + '\n'
                return SimpleNamespace(stdout=output)

            argv = ['leaf_probe.py', '--elf', str(elf), '--upstream', str(upstream),
                    '--start', '0x1000', '--end', '0x1004', '--compiler', str(compiler),
                    '--work-dir', str(root / 'generated'), '--report', str(report), '--cases', '32']
            stdout = io.StringIO()
            with mock.patch.object(sys, 'argv', argv), \
                 mock.patch.object(sys, 'path', list(sys.path)), \
                 mock.patch.object(sys, 'dont_write_bytecode', sys.dont_write_bytecode), \
                 mock.patch.dict(sys.modules, clear=False), \
                 mock.patch.object(probe.importlib, 'import_module', side_effect=modules.__getitem__), \
                 mock.patch.object(probe, 'run_command', side_effect=run_command), \
                 contextlib.redirect_stdout(stdout):
                # The production gate intentionally rejects already loaded modules.
                sys.modules.pop('ppu_disasm', None)
                sys.modules.pop('ppu_lifter', None)
                result = probe.main()
            summary = json.loads(stdout.getvalue())
            report_exists = report.exists()
            saved = json.loads(report.read_text(encoding='utf-8')) if report_exists else None
            self.assertEqual(elf.read_bytes(), bytes(data), 'ELF input was modified')
            return result, summary, report_exists, saved, phases

    def assert_failure(self, mode, expected_error):
        result, summary, exists, _, phases = self.run_pipeline(mode)
        self.assertNotEqual(result, 0)
        self.assertIs(summary['success'], False)
        self.assertIn(expected_error, summary['error'])
        self.assertFalse(exists, 'failed pipeline wrote a success report')
        self.assertIn('compile', phases)
        if mode != 'compile_failure':
            self.assertIn('run', phases)

    def test_compile_failure_has_no_success_report(self):
        self.assert_failure('compile_failure', 'synthetic compile failure')

    def test_short_output_has_no_success_report(self):
        self.assert_failure('short_output', 'wrong output case count')

    def test_malformed_output_has_no_success_report(self):
        self.assert_failure('malformed_output', 'malformed output')

    def test_last_gpr_mismatch_has_no_success_report(self):
        self.assert_failure('gpr_mismatch', 'gpr=31')

    def test_synthetic_success_control_reaches_report(self):
        result, summary, exists, saved, phases = self.run_pipeline('success')
        self.assertEqual(result, 0)
        self.assertIs(summary['success'], True)
        self.assertTrue(exists)
        self.assertEqual(saved['cases'], 32)
        self.assertIn('run', phases)


if __name__ == '__main__':
    unittest.main()
