"""Regenerate the private, tested Folklore PPU overlay from an owned ELF.

No translated instructions are shipped here. Pinned public source supplies the
original translator; the current translator supplies the one recovered switch.
Success means every output SHA256 matches the supplied acceptance manifest.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from pinned_tools import read_pinned_tool

ELF_SHA = '51a9eb16ceca3edfb7454b37a9e895f5a4e35bd515a40ae5b918f97c3a1b6c0e'
BASE_REV = 'b199831f81be0b34fcc02f7f5d8434fefcc4ca94'
BASE_TOOLS = {
    'ppu_lifter.py': 'af1391ff20aeb47237ff304f63563cd63d285c78a4aaa9424f3e260e34a1d28f',
    'ppu_disasm.py': '1fd70ab34d22b2c55d49a779c9f9fb4a625ef4469c89314fc2e8bae00253d35e',
    'elf_parser.py': '4430264c27410598d3714c21707b9cd7c65ba23a85c3b6c26a2b1f11089903eb',
}
META_HASHES = {
    'EBOOT.functions.bounded.json': '4c054877dfefbf43b1aab2678057e85f22708098690df1c946d7719cc03cee04',
    'EBOOT.imports.json': 'c1759c70308a991f99c75023f0e68ac9dd24f56726c7d942a55d313f0eac374b',
}
PATTERN = re.compile(r'(?ms)^void func_0034D268\(ppu_context\* ctx\).*?(?=^void func_|^/\* Function table \*/|\Z)')


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def reject_overlap(output, inputs):
    output = output.resolve()
    for item in inputs:
        item = Path(item).resolve()
        if output == item or output in item.parents or item in output.parents:
            raise ValueError(f'Output overlaps input: {item}')
    if output.exists():
        raise ValueError('Output must be a new directory')


def normalized(source):
    source = re.sub(r'(?m)^loc_[0-9A-F]+:\n', '', source)
    return re.sub(r'(?m)^        switch \(\(uint32_t\)ctx->ctr\).*$',
                  '        ps3_indirect_call(ctx); return;', source).strip()


def splice(original, recovered):
    old, new = PATTERN.search(original), PATTERN.search(recovered)
    if not old or not new:
        raise ValueError('Expected recovery function missing')
    if 'case 0x0034DDF8u: goto loc_0034DDF8;' not in new[0]:
        raise ValueError('Recovered switch target missing')
    if normalized(old[0]) != normalized(new[0]):
        raise ValueError('Recovery changes translated instructions')
    return original[:old.start()] + new[0] + original[old.end():]


def verify(directory, manifest, field):
    rows = []
    expected = set()
    for record in manifest['files']:
        name = record['name']
        if Path(name).name != name or '/' in name or '\\' in name or name in expected:
            raise ValueError('Unsafe or duplicate manifest filename')
        expected.add(name)
        path = directory / name
        actual = sha(path) if path.is_file() else None
        rows.append({'name': name, 'expected': record[field], 'actual': actual,
                     'match': actual == record[field]})
    extra = sorted(p.name for p in directory.iterdir() if p.is_file() and p.name not in expected)
    return rows, extra


def execute(args):
    elf, sdk, metadata, output, manifest_path = map(
        lambda p: Path(p).resolve(), (args.elf, args.sdk, args.metadata, args.output, args.manifest))
    reject_overlap(output, [elf, sdk, metadata, manifest_path])
    if sha(elf) != ELF_SHA:
        raise ValueError('Unsupported ELF SHA256; only the tested USA 1.00 ELF is accepted')
    for name, expected in META_HASHES.items():
        if sha(metadata / name) != expected:
            raise ValueError(f'Unsupported metadata SHA256: {name}')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    if manifest.get('elf_sha256') != ELF_SHA or len(manifest.get('files', [])) != 26:
        raise ValueError('Unsupported acceptance manifest')
    # Validate manifest paths before creating anything.
    for row in manifest['files']:
        if Path(row['name']).name != row['name'] or '/' in row['name'] or '\\' in row['name']:
            raise ValueError('Unsafe manifest filename')
    for name in BASE_TOOLS:
        if not (sdk / 'tools' / name).is_file():
            raise ValueError(f'SDK tool missing: {name}')
    output.mkdir(parents=True)
    receipt = {'state': 'running', 'elf_sha256': ELF_SHA, 'baseline_revision': BASE_REV,
               'metadata_sha256': META_HASHES, 'manifest_sha256': sha(manifest_path),
               'commands': [], 'sdk_tool_sha256': {n: sha(sdk / 'tools' / n) for n in BASE_TOOLS}}
    try:
        tools = output / 'baseline-tools'
        tools.mkdir()
        for name, expected in BASE_TOOLS.items():
            data = read_pinned_tool(sdk, BASE_REV, name)
            if hashlib.sha256(data).hexdigest() != expected:
                raise ValueError(f'Baseline SDK tool hash mismatch: {name}')
            (tools / name).write_bytes(data)
        baseline = output / 'overlay'
        recovery = output / 'recovery'
        functions = output / 'recovery-functions.json'
        functions.write_text(json.dumps([{'start': '0x34d268', 'end': '0x34e5e8',
                                         'toc': '0x56eb00', 'opd': '0x55e9b0'}]), encoding='utf-8')
        commands = [
            [sys.executable, str(tools / 'ppu_lifter.py'), str(elf), '--functions',
             str(metadata / 'EBOOT.functions.bounded.json'), '--hle-stubs',
             str(metadata / 'EBOOT.imports.json'), '--chunk-lines', '60000',
             '--strict-unsupported', '--jobs', str(args.jobs), '--output', str(baseline)],
            [sys.executable, str(sdk / 'tools/ppu_lifter.py'), str(elf), '--functions',
             str(functions), '--single-file', '--strict-unsupported', '--jobs', '1',
             '--output', str(recovery)],
        ]
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')
        for index, command in enumerate(commands):
            receipt['commands'].append(command)
            with (output / f'lift-{index}.log').open('wb') as log:
                subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, env=env, check=True,
                               cwd=output)
        # The tested artifacts were emitted on Windows in cp1252 text mode.
        # Fix encoding/newlines explicitly so Linux produces identical bytes.
        for path in baseline.iterdir():
            text = path.read_text(encoding='utf-8').replace('\r\n', '\n')
            path.write_bytes(text.replace('\n', '\r\n').encode('cp1252'))
        rows, extra = verify(baseline, manifest, 'original_sha256')
        receipt['baseline_files'] = rows
        if extra or not all(r['match'] for r in rows):
            receipt['extra_files'] = extra
            raise ValueError('Baseline regeneration differs from tested source')
        unit = baseline / 'ppu_recomp_015.cpp'
        patched = splice(unit.read_text(encoding='cp1252'),
                         (recovery / 'ppu_recomp.c').read_text(encoding='utf-8'))
        # Historical preparation used Windows text-mode CRLF for this one unit.
        unit.write_bytes(patched.replace('\r\n', '\n').replace('\n', '\r\n').encode('cp1252'))
        rows, extra = verify(baseline, manifest, 'overlay_sha256')
        receipt['overlay_files'] = rows
        receipt['extra_files'] = extra
        if extra or not all(r['match'] for r in rows):
            raise ValueError('Overlay regeneration differs from tested source')
        receipt['state'] = 'verified'
        receipt['overlay'] = str(baseline)
    except Exception as error:
        receipt.update(state='failed', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        (output / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('elf', 'sdk', 'metadata', 'output', 'manifest'):
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--jobs', type=int, default=4)
    args = parser.parse_args()
    if not 1 <= args.jobs <= 32:
        parser.error('--jobs must be between 1 and 32')
    try:
        result = execute(args)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'{error}\n')
    print(json.dumps({'state': result['state'], 'overlay': result['overlay']}))


if __name__ == '__main__':
    main()
