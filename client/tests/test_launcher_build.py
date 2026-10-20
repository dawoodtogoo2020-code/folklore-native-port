import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'launcher'))
import launcher_build as lb  # noqa: E402
import launcher_core as core  # noqa: E402

# Stand-in for scripts/build_owned_backup.py: same arguments, checkpoint and log
# layout, and failure style (message on stderr, exit 1). Never generates anything.
FAKE_BUILDER = r'''
import argparse, json, sys, time
from pathlib import Path
p = argparse.ArgumentParser()
for n in ('elf','sdk','metadata','ppu-manifest','spu-manifest','toolchain','prefix','shader-dir','output','source'):
    p.add_argument('--' + n)
p.add_argument('--resume', action='store_true'); p.add_argument('--prepare-only', action='store_true')
a = p.parse_args()
mode = Path(a.source, 'mode.txt').read_text().strip()
out = Path(a.output)
if mode == 'refuse':
    sys.exit('Resume inputs differ from recorded checkpoint')
out.mkdir()
state = {'state': 'starting', 'steps': []}
def save(): (out / 'checkpoint.json').write_text(json.dumps(state))
for name in ('regenerate-ppu', 'regenerate-spu', 'configure', 'build'):
    save()
    (out / (name + '.log')).write_text(f'{name}: working on unit 1 of 3\n')
    time.sleep(30 if mode == 'hang' else 0.4)
    if mode == 'fail' and name == 'regenerate-spu':
        state['steps'].append({'name': name, 'exit_code': 1}); save()
        sys.exit(f'{name} failed; see {out / (name + ".log")}')
    state['steps'].append({'name': name, 'exit_code': 0})
    if name == 'regenerate-spu':
        state['state'] = 'prepared'
        if a.prepare_only:
            save(); print(json.dumps({'state': 'prepared'})); sys.exit(0)
state.update(state='built', binary=str(out / 'native-build/FolkloreBoot.exe')); save()
print(json.dumps({'state': 'built', 'binary': state['binary'], 'runtime_tested': False}))
'''


class BuilderRunner(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.config = core.default_config()
        self.config['paths']['elf'] = str(self.root / 'EBOOT.elf')
        for k in core.BUILD_NAMES:
            self.config['build'][k] = str(self.root / k)
        (self.root / 'source' / 'scripts').mkdir(parents=True)
        (self.root / 'source' / 'scripts' / 'build_owned_backup.py').write_text(FAKE_BUILDER)

    def mode(self, text):
        (self.root / 'source' / 'mode.txt').write_text(text)

    def wait(self, run, seconds=30):
        seen = []
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            seen.append(run.progress())
            result = run.poll()
            if result is not None:
                return result, seen
            time.sleep(0.1)
        run.cancel()
        self.fail('builder did not finish')

    def test_command_uses_documented_interface(self):
        cmd, paths, resume = lb.builder_command(self.config, python='python.exe')
        self.assertEqual(cmd[:2], ['python.exe', str(self.root / 'source' / 'scripts' / 'build_owned_backup.py')])
        self.assertEqual(cmd[cmd.index('--ppu-manifest') + 1], str(self.root / 'ppu_manifest'))
        self.assertEqual(cmd[cmd.index('--elf') + 1], self.config['paths']['elf'])
        self.assertFalse(resume)
        self.assertNotIn('--resume', cmd)
        (self.root / 'output').mkdir()
        cmd, _, resume = lb.builder_command(self.config, prepare_only=True, python='python.exe')
        self.assertTrue(resume)
        self.assertEqual(cmd[-2:], ['--resume', '--prepare-only'])

    def test_incomplete_or_relative_inputs_are_explained(self):
        self.config['build']['shader_dir'] = ''
        with self.assertRaisesRegex(ValueError, 'shader dir'):
            lb.builder_command(self.config)
        self.config['build']['shader_dir'] = 'relative'
        with self.assertRaisesRegex(ValueError, 'absolute'):
            lb.builder_command(self.config)

    def test_planned_steps_follow_the_checkpoint(self):
        self.assertEqual(lb.planned_steps(None, False), ['regenerate-ppu', 'regenerate-spu', 'configure', 'build'])
        self.assertEqual(lb.planned_steps(None, True), ['regenerate-ppu', 'regenerate-spu'])
        self.assertEqual(lb.planned_steps('built', False), ['configure', 'build'])
        self.assertEqual(lb.planned_steps('prepared', True), [])

    def test_interpreter_is_console_python(self):
        with mock.patch.object(core.sys, 'executable', str(Path(sys.executable).with_name('pythonw.exe'))):
            chosen = core.interpreter_for_tools()
        if Path(sys.executable).with_name('python.exe').is_file():
            self.assertTrue(chosen.lower().endswith('python.exe'))

    def test_success_reports_progress_and_new_binary(self):
        self.mode('ok')
        run = lb.BuildRun(self.config, python=sys.executable)
        result, seen = self.wait(run)
        self.assertEqual(result['exit_code'], 0)
        self.assertIn('New executable', result['message'])
        self.assertEqual(result['summary']['binary'], str(self.root / 'output' / 'native-build/FolkloreBoot.exe'))
        steps = {p['current'] for p in seen if p['current']}
        self.assertTrue({'regenerate-ppu', 'configure', 'build'} <= steps, steps)
        self.assertTrue(any('unit 1 of 3' in p['detail'] for p in seen))
        self.assertEqual(seen[-1]['total'], 4)

    def test_failure_surfaces_builder_message(self):
        self.mode('fail')
        result, seen = self.wait(lb.BuildRun(self.config, python=sys.executable))
        self.assertEqual(result['exit_code'], 1)
        self.assertIn('regenerate-spu failed; see', result['message'])
        self.assertTrue(result['message'].startswith('Build failed'))
        self.assertEqual(seen[-1]['failed_step'], 'regenerate-spu')

    def test_resume_refusal_is_reported_not_hidden(self):
        (self.root / 'output').mkdir()
        self.mode('refuse')
        run = lb.BuildRun(self.config, python=sys.executable)
        self.assertIn('--resume', run.cmd)
        result, _ = self.wait(run)
        self.assertNotEqual(result['exit_code'], 0)
        self.assertIn('Resume inputs differ', result['message'])

    def test_cancel_stops_the_builder(self):
        self.mode('hang')
        run = lb.BuildRun(self.config, python=sys.executable)
        time.sleep(1)
        self.assertIsNone(run.poll())
        run.cancel()
        result, _ = self.wait(run, 20)
        self.assertTrue(result['cancelled'])
        self.assertIn('cannot be resumed', result['message'])

    def test_real_builder_refuses_output_off_d_drive(self):
        """The real scripts/build_owned_backup.py keeps its checks; its message reaches the UI."""
        config = core.default_config()
        config['paths']['elf'] = str(self.root / 'EBOOT.elf')
        for k in core.BUILD_NAMES:
            config['build'][k] = str(self.root / k)
        config['build']['source'] = str(REPO)
        config['build']['output'] = 'C:\\folklore-launcher-test-output-not-created'
        run = lb.BuildRun(config, python=sys.executable)
        result, _ = self.wait(run)
        self.assertEqual(result['exit_code'], 1)
        self.assertIn('Output must be on D:', result['message'])
        self.assertFalse(Path(config['build']['output']).exists())


if __name__ == '__main__':
    unittest.main()
