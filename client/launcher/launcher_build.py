"""Run the owned-backup builder (scripts/build_owned_backup.py) without blocking the UI.

The builder keeps every check: ELF identity, hash-verified PPU/SPU regeneration,
contained D: output, and resume only against identical recorded inputs. This
module only assembles its documented arguments, watches its checkpoint and logs,
and reports its own error text. It never edits the checkpoint or outputs.
"""
import json
import os
from pathlib import Path
import subprocess
import threading
import time

from launcher_core import BUILD_NAMES, interpreter_for_tools

REPO = Path(__file__).resolve().parent.parent
STEP_LABELS = {'regenerate-ppu': 'Regenerating PPU code from your EBOOT.elf',
               'regenerate-spu': 'Regenerating SPU jobs from your EBOOT.elf',
               'configure': 'Configuring the native build',
               'build': 'Compiling and linking FolkloreBoot.exe'}
GENERATION = ('regenerate-ppu', 'regenerate-spu')
COMPILE = ('configure', 'build')


def builder_paths(config):
    """The builder's explicit inputs; ELF comes from the setup paths."""
    b = config['build']
    paths = {'elf': config['paths']['elf'], **{k: b[k] for k in BUILD_NAMES}}
    if not paths['source']:
        paths['source'] = str(REPO)
    missing = [k for k, v in paths.items() if not v]
    if missing:
        raise ValueError('Build settings are incomplete: ' + ', '.join(m.replace('_', ' ') for m in missing) +
                         '. Fill them under Advanced > Build inputs, or import a build recipe.')
    for k, v in paths.items():
        if not Path(v).is_absolute():
            raise ValueError(f'Build input {k.replace("_", " ")} must be an absolute path.')
    return paths


def builder_command(config, prepare_only=False, python=None):
    paths = builder_paths(config)
    script = Path(paths['source']) / 'scripts' / 'build_owned_backup.py'
    if not script.is_file():
        raise ValueError(f'The builder script is missing: {script}')
    cmd = [python or interpreter_for_tools(), str(script)]
    for name, value in paths.items():
        cmd += ['--' + name.replace('_', '-'), value]
    resume = Path(paths['output']).exists()
    if resume:
        cmd.append('--resume')
    if prepare_only:
        cmd.append('--prepare-only')
    return cmd, paths, resume


def read_checkpoint(output):
    try:
        with (Path(output) / 'checkpoint.json').open('rb') as f:
            data = f.read(16 * 1024 * 1024 + 1)
        state = json.loads(data.decode('utf-8'))
        return state if isinstance(state, dict) else None
    except (OSError, ValueError, UnicodeError):
        return None


def planned_steps(start_state, prepare_only):
    """Steps the builder will run from a given checkpoint state (see build_owned_backup.execute)."""
    steps = list(GENERATION) if start_state in (None, 'starting') else []
    if not prepare_only:
        steps += COMPILE
    return steps


def last_line(path, limit=4096):
    try:
        with Path(path).open('rb') as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - limit))
            lines = [l for l in f.read().decode('utf-8', 'replace').splitlines() if l.strip()]
        return lines[-1].strip()[:200] if lines else ''
    except OSError:
        return ''


class BuildRun:
    """One builder process. poll() is cheap and safe to call from the UI thread."""

    def __init__(self, config, prepare_only=False, python=None, popen=subprocess.Popen):
        self.cmd, self.paths, self.resume = builder_command(config, prepare_only, python)
        self.output = Path(self.paths['output'])
        before = read_checkpoint(self.output) if self.resume else None
        self.baseline = len(before.get('steps', [])) if before else 0
        self.steps = planned_steps(before.get('state') if before else None, prepare_only)
        self.prepare_only = prepare_only
        self.started = time.monotonic()
        self.stdout, self.stderr = [], []
        self.cancelled = False
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        self.process = popen(self.cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                             shell=False, cwd=str(REPO), creationflags=flags)
        self._readers = [threading.Thread(target=self._drain, args=(s, b), daemon=True)
                         for s, b in ((self.process.stdout, self.stdout), (self.process.stderr, self.stderr))]
        for t in self._readers:
            t.start()

    @staticmethod
    def _drain(stream, sink):
        for raw in iter(stream.readline, b''):
            sink.append(raw.decode('utf-8', 'replace').rstrip())
        stream.close()

    def progress(self):
        state = read_checkpoint(self.output) or {}
        done = [s for s in state.get('steps', [])[self.baseline:] if isinstance(s, dict)]
        failed = next((s for s in done if s.get('exit_code')), None)
        index = min(len(done), len(self.steps))
        current = self.steps[index] if index < len(self.steps) else None
        log = self.output / f'{current}.log' if current else None
        return {'state': state.get('state'), 'done': index, 'total': len(self.steps), 'current': current,
                'label': STEP_LABELS.get(current, 'Checking inputs and recorded checkpoint' if not done else ''),
                'detail': last_line(log) if log else '', 'failed_step': failed and failed.get('name'),
                'elapsed': time.monotonic() - self.started}

    def poll(self):
        """None while running; otherwise a result dict."""
        code = self.process.poll()
        if code is None:
            return None
        for t in self._readers:
            t.join(timeout=2)
        result = {'exit_code': code, 'cancelled': self.cancelled, 'output': str(self.output)}
        for line in reversed(self.stdout):
            try:
                summary = json.loads(line)
            except ValueError:
                continue
            if isinstance(summary, dict):
                result['summary'] = summary
                break
        if code == 0 and 'summary' in result:
            binary = result['summary'].get('binary')
            result['message'] = ('Build finished. ' + (f'New executable: {binary}' if binary else
                                 'Generated code verified; no executable was compiled (verify only).'))
        elif self.cancelled:
            result['message'] = ('Build cancelled. A run cancelled during code generation cannot be resumed: '
                                 'choose a new output folder (the partial one was left in place for you to inspect or delete).')
        else:
            text = '\n'.join(l for l in self.stderr if l.strip()) or f'The builder exited with code {code}.'
            result['message'] = 'Build failed: ' + text[-2000:]
        return result

    def cancel(self):
        if self.process.poll() is not None:
            return
        self.cancelled = True
        if os.name == 'nt':
            # The builder starts regenerators, CMake and Ninja; stop the whole tree.
            subprocess.run(['taskkill', '/T', '/F', '/PID', str(self.process.pid)], capture_output=True,
                           shell=False, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            self.process.terminate()
