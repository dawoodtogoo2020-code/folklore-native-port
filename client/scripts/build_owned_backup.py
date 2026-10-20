"""Development-only Windows builder for the tested owned USA Folklore ELF.

Prerequisites: Python, the public source archive, Clang/Clang++, CMake,
Ninja, installed SDL2/Vulkan/FFmpeg development dependencies and prebuilt CACAO
shaders. Supply all paths explicitly. No downloads, decryption or game launch.
Generated source and executable contain private game-derived code and must not
be redistributed. This tool is not a release installer or a gameplay test.
Only ELF identity is validated here. Disc PARAM.SFO title/version validation
and launcher-to-build integration remain outside this development entry point.
"""
import argparse
import hashlib
import json
import os
import stat
from pathlib import Path
from pathlib import PurePosixPath, PureWindowsPath
import subprocess
import sys

# Support both command-line use and path-based import by existing tests.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pinned_tools import pinned_fingerprints
from source_snapshot import snapshot_sources

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent


def plain_output(path):
    """Reject existing junction/symlink/reparse components before resolving."""
    path = Path(path)
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError(f'Output cannot use reparse points: {item}')


def validate_writable_children(output):
    """Inspect existing mutable paths without following directory links."""
    output = Path(output)
    plain_output(output)
    for name in ('checkpoint.json', 'regenerate-ppu.log', 'regenerate-spu.log',
                 'configure.log', 'build.log'):
        path = output / name
        plain_output(path)
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f'Writable output must be a regular file: {path}')
    for name in ('tmp', 'native-build'):
        pending = [output / name]
        while pending:
            directory = pending.pop()
            plain_output(directory)
            try:
                info = directory.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError(f'Writable output must be a directory: {directory}')
            with os.scandir(directory) as entries:
                for entry in entries:
                    # lstat first: no recursion or metadata access via a link.
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                        raise ValueError(f'Output cannot use reparse points: {entry.path}')
                    if stat.S_ISDIR(info.st_mode):
                        pending.append(Path(entry.path))


def safe_child(root, name, flat=False):
    if (not isinstance(name, str) or not name or '\\' in name or ':' in name or
            PurePosixPath(name).is_absolute() or PureWindowsPath(name).drive or
            any(p in ('', '.', '..') for p in name.split('/')) or
            (flat and '/' in name)):
        raise ValueError('Unsafe manifest or receipt path')
    path = Path(root) / name
    plain_output(path)
    if not path.resolve().is_relative_to(Path(root).resolve()):
        raise ValueError('Manifest path escapes output')
    return path


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            h.update(block)
    return h.hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def shader_inputs(root):
    manifest_path = safe_child(root, 'manifest.json')
    manifest = load(manifest_path)
    rows = manifest.get('shaders')
    if manifest.get('passed') is not True or not isinstance(rows, list) or len(rows) != 66:
        raise ValueError('CACAO requires a validated manifest with 66 shader headers')
    result = {'manifest.json': sha(manifest_path)}
    for row in rows:
        name = row.get('header')
        path = safe_child(root / 'PrecompiledShadersSPIRV', name, flat=True)
        key = 'PrecompiledShadersSPIRV/' + name
        if key in result or not path.is_file():
            raise ValueError('Duplicate or missing CACAO header')
        result[key] = sha(path)
        if result[key] != row.get('header_sha256'):
            raise ValueError('CACAO shader header hash differs: ' + name)
    return result


def validate(args):
    paths = {}
    for name in ('elf', 'sdk', 'metadata', 'ppu_manifest', 'spu_manifest', 'toolchain',
                 'prefix', 'shader_dir', 'output', 'source'):
        path = Path(getattr(args, name))
        if not path.is_absolute():
            raise ValueError(f'{name} must be an absolute path')
        if name == 'output':
            plain_output(path)
        paths[name] = path.resolve()
    output = paths['output']
    validate_writable_children(output)
    for name, path in paths.items():
        if name == 'output':
            continue
        if output == path or output in path.parents or path in output.parents:
            raise ValueError(f'Output overlaps protected input: {name}')
        if not path.exists():
            raise ValueError(f'Missing {name}: {path}')
    if not 1 <= args.jobs <= 32:
        raise ValueError('jobs must be between 1 and 32')
    for name in ('clang.exe', 'clang++.exe', 'cmake.exe', 'ninja.exe'):
        if not (paths['toolchain'] / name).is_file():
            raise ValueError(f'Missing development tool: {name}')
    for name in ('CMakeLists.txt',):
        if not (paths['source'] / 'native' / name).is_file():
            raise ValueError('Missing native CMake source')
    if not (paths['shader_dir'] / 'manifest.json').is_file():
        raise ValueError('Missing CACAO shader manifest')
    shader_inputs(paths['shader_dir'])
    if not (paths['metadata'] / 'EBOOT.functions.bounded.json').is_file():
        raise ValueError('Missing bounded PPU metadata')
    ppu, spu = load(paths['ppu_manifest']), load(paths['spu_manifest'])
    if len(ppu.get('files', [])) != 26 or len([n for n in spu.get('files', {}) if n.startswith('cases/')]) != 27:
        raise ValueError('Wrong PPU/SPU acceptance manifest shape')
    for row in ppu['files']:
        safe_child(output / 'ppu/overlay', row.get('name'), flat=True)
    for name in spu['files']:
        safe_child(output / 'spu/profile', name)
    return paths


def generation_inputs(paths):
    files = [paths[n] for n in ('elf', 'ppu_manifest', 'spu_manifest')]
    files += [paths['metadata'] / n for n in ('EBOOT.functions.bounded.json', 'EBOOT.imports.json')]
    scripts = HERE
    files += [scripts / n for n in ('build_owned_backup.py', 'regenerate_ppu.py',
              'regenerate_spu.py', 'prepare_startup_aot.py', 'prepare_camera_aspect.py', 'prepare_frame_limiter.py',
              'native-lane/recover_image03.py', 'pinned_tools.py', 'source_snapshot.py')]
    files += [paths['sdk'] / 'tools' / n for n in ('ppu_lifter.py', 'ppu_disasm.py',
                                                  'elf_parser.py', 'spu_lifter.py', 'spu_disasm.py')]
    for directory in ('include/ps3emu', 'runtime/spu', 'runtime/platform'):
        files += list((paths['sdk'] / directory).glob('*.h'))
    result = {str(p): sha(p) for p in sorted(set(files))}
    result.update(pinned_fingerprints(paths['sdk']))
    result['python_executable'] = sha(sys.executable)
    result['python_version'] = sys.version
    return result


def build_source_snapshot(paths):
    return snapshot_sources({'engine': paths['sdk'], 'client': paths['source']})


def check_verified(root, kind):
    plain_output(root)
    # Detect nested links before reading receipt-selected output files.
    if root.exists():
        for path in root.rglob('*'):
            plain_output(path)
    receipt = load(root / 'receipt.json')
    if receipt.get('state') != 'verified':
        raise ValueError(f'{kind} generation has no verified checkpoint')
    if kind == 'ppu':
        for row in receipt['overlay_files']:
            safe_child(root / 'overlay', row.get('name'), flat=True)
        for row in receipt['overlay_files']:
            if not row['match'] or sha(safe_child(root / 'overlay', row['name'], flat=True)) != row['actual']:
                raise ValueError('PPU output changed after verification')
    else:
        manifest = load(root / 'profile/profile-manifest.json')
        for name in manifest['files']:
            safe_child(root / 'profile', name)
        for row in receipt['files']:
            safe_child(root / 'profile', row.get('name'))
        for name, expected in manifest['files'].items():
            if sha(safe_child(root / 'profile', name)) != expected:
                raise ValueError('SPU output changed after verification')
        for row in receipt['files']:
            if not row['match'] or sha(safe_child(root / 'profile', row['name'])) != row['actual']:
                raise ValueError('SPU acceptance output changed')


def configure_command(paths, python=sys.executable):
    ppu = paths['output'] / 'ppu/overlay'
    profile = paths['output'] / 'spu/profile'
    plain_output(profile / 'profile-manifest.json')
    manifest = load(profile / 'profile-manifest.json')
    private = safe_child(profile, manifest['native_translation_unit'])
    extras = ';'.join(str(safe_child(profile, n)) for n in manifest['extra_translation_units'])
    tools = paths['toolchain']
    return [str(tools / 'cmake.exe'), '-S', str(paths['source'] / 'native'),
            '-B', str(paths['output'] / 'native-build'), '-G', 'Ninja',
            '-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_C_COMPILER=' + str(tools / 'clang.exe'),
            '-DCMAKE_CXX_COMPILER=' + str(tools / 'clang++.exe'),
            '-DCMAKE_MAKE_PROGRAM=' + str(tools / 'ninja.exe'),
            '-DCMAKE_PREFIX_PATH=' + str(paths['prefix']), '-DPython3_EXECUTABLE=' + python,
            '-DPS3RECOMP_DIR=' + str(paths['sdk']), '-DRECOMP_DIR=' + str(ppu),
            '-DFOLKLORE_PRIVATE_NATIVE_JOB_SOURCE=' + str(private),
            '-DFOLKLORE_PRIVATE_NATIVE_EXTRA_SOURCES=' + extras,
            '-DPS3RECOMP_RSX_VULKAN=ON', '-DPS3RECOMP_PAD_USE_SDL2=ON',
            '-DPS3RECOMP_FFMPEG_RUNTIME=ON', '-DPS3RECOMP_RSX_CACAO=ON',
            '-DFOLKLORE_CAMERA_ASPECT_BRIDGE=ON', '-DFOLKLORE_FRAME_LIMITER_BRIDGE=ON',
            '-DPS3RECOMP_CACAO_SHADER_DIR=' + str(paths['shader_dir']), '-DFOLKLORE_LIFTED_OPT=-O1']


def execute(args):
    if os.name != 'nt':
        raise ValueError('This entry point currently supports Windows development builds only')
    paths = validate(args)
    inputs = generation_inputs(paths)
    output = paths['output']
    checkpoint = output / 'checkpoint.json'
    if output.exists():
        if not args.resume or not checkpoint.is_file():
            raise ValueError('Existing output requires --resume and its checkpoint')
        validate_writable_children(output)
        state = load(checkpoint)
        if state.get('inputs') != inputs or state.get('paths') != {k: str(v) for k, v in paths.items()}:
            raise ValueError('Resume inputs differ from recorded checkpoint')
        if state.get('state') not in ('prepared', 'configured', 'built'):
            raise ValueError('Only completed verified generation can resume; use a fresh output')
        check_verified(output / 'ppu', 'ppu')
        check_verified(output / 'spu', 'spu')
    else:
        output.mkdir(parents=True)
        state = {'state': 'starting', 'paths': {k: str(v) for k, v in paths.items()},
                 'inputs': inputs, 'steps': [], 'runtime_tested': False,
                 'scope': 'Private local development build; not redistributable'}
    validate_writable_children(output)
    (output / 'tmp').mkdir(exist_ok=True)
    env = dict(os.environ, PATH=str(paths['toolchain']) + os.pathsep + os.environ.get('PATH', ''),
               TEMP=str(output / 'tmp'), TMP=str(output / 'tmp'), PYTHONDONTWRITEBYTECODE='1')

    def save():
        validate_writable_children(output)
        checkpoint.write_text(json.dumps(state, indent=2) + '\n', encoding='utf-8')

    def run(name, command):
        save()
        with (output / (name + '.log')).open('wb') as log:
            result = subprocess.run(command, env=env, cwd=output, stdout=log, stderr=subprocess.STDOUT)
        state['steps'].append({'name': name, 'command': command, 'exit_code': result.returncode})
        save()
        if result.returncode:
            raise ValueError(f'{name} failed; see {output / (name + ".log")}')

    if state['state'] == 'starting':
        for kind in ('ppu', 'spu'):
            command = [sys.executable, str(HERE / ('regenerate_' + kind + '.py')),
                       '--elf', str(paths['elf']), '--sdk', str(paths['sdk']),
                       '--manifest', str(paths[kind + '_manifest']), '--output', str(output / kind)]
            if kind == 'ppu':
                command += ['--metadata', str(paths['metadata']), '--jobs', str(args.jobs)]
            run('regenerate-' + kind, command)
            check_verified(output / kind, kind)
        state['state'] = 'prepared'
        save()
    if args.prepare_only:
        print(json.dumps({'state': state['state'], 'checkpoint': str(checkpoint)}))
        return state
    command = configure_command(paths)
    state['build_source_snapshot'] = build_source_snapshot(paths)
    state['toolchain_sha256'] = {n: sha(paths['toolchain'] / n) for n in
                                 ('clang.exe', 'clang++.exe', 'cmake.exe', 'ninja.exe')}
    state['shader_inputs'] = shader_inputs(paths['shader_dir'])
    state['shader_manifest_sha256'] = state['shader_inputs']['manifest.json']
    run('configure', command)
    state['state'] = 'configured'
    save()
    run('build', [str(paths['toolchain'] / 'cmake.exe'), '--build', str(output / 'native-build'),
                  '--parallel', str(args.jobs)])
    if state['build_source_snapshot'] != build_source_snapshot(paths):
        raise ValueError('Source changed during compilation; rebuild from a stable source copy')
    if state['shader_inputs'] != shader_inputs(paths['shader_dir']):
        raise ValueError('CACAO shader inputs changed during compilation')
    binary = output / 'native-build/FolkloreBoot.exe'
    state.update(state='built', binary=str(binary), binary_sha256=sha(binary))
    save()
    print(json.dumps({'state': 'built', 'binary': str(binary), 'runtime_tested': False}))
    return state


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('elf', 'sdk', 'metadata', 'ppu-manifest', 'spu-manifest', 'toolchain',
                 'prefix', 'shader-dir', 'output'):
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--source', type=Path, default=HERE.parent)
    p.add_argument('--jobs', type=int, default=6)
    p.add_argument('--prepare-only', action='store_true')
    p.add_argument('--resume', action='store_true')
    return p


if __name__ == '__main__':
    p = parser()
    try:
        execute(p.parse_args())
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        p.exit(1, str(error) + '\n')
