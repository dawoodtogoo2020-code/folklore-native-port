"""Assemble a private ARM64 test APK from verified local inputs, using Linux SDK tools.

No downloads, game/firmware copying, device installation, or key generation.
The caller supplies the test keystore and its password through an environment variable.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def verify_identity(badging):
    if "package: name='org.folklore.port'" not in badging or "minSdkVersion:'26'" not in badging or "targetSdkVersion:'36'" not in badging:
        raise ValueError('Unexpected packaged identity or SDK range')


def build(args):
    if os.name != 'posix':
        raise ValueError('Run with Linux SDK tools, for example in WSL; keep output on D:')
    out = args.output.resolve()
    if out.exists():
        raise ValueError('Output directory must be new; preserve previous attempt receipts')
    if not out.parent.is_dir() or shutil.disk_usage(out.parent).free < 2 * 1024**3:
        raise ValueError('Existing output parent needs at least 2 GiB free')
    if not args.keystore.is_file() or not os.environ.get(args.password_env):
        raise ValueError('Supply an existing test keystore and password environment variable')
    source = args.source.resolve()
    android = args.sdk.resolve() / 'platforms/android-36/android.jar'
    tools = args.sdk.resolve() / 'build-tools/36.0.0'
    java = args.jdk.resolve() / 'bin'
    ndk = args.ndk.resolve() / 'toolchains/llvm/prebuilt/linux-x86_64/bin'
    sdl = args.sdl_source.resolve() / 'android-project/app/src/main/java/org/libsdl/app'
    closure = json.loads(args.native_closure.read_text())
    if not closure.get('passed') or closure.get('abi') != 'arm64-v8a' or closure.get('api') != 26:
        raise ValueError('A passed ARM64 API26 native closure receipt is required')
    required = {'libFolkloreBoot.so', 'libSDL2.so', 'libavcodec.so', 'libavutil.so'}
    if set(closure['libraries']) not in (required, required | {'libc++_shared.so'}):
        raise ValueError('Unexpected native library set')
    libraries = {}
    for name, row in closure['libraries'].items():
        path = Path(row['path'])
        if path.name != name or not path.is_file() or sha(path) != row['sha256']:
            raise ValueError('Native closure source changed: ' + name)
        libraries[name] = path
    app_sources = sorted((source / 'android/src').rglob('*.java'))
    sdl_sources = sorted(sdl.glob('*.java'))
    expected = {'FolkloreActivity.java', 'FolkloreLauncherActivity.java', 'FolklorePaths.java', 'FolkloreWritablePaths.java',
                'FolkloreDialogPresenter.java', 'FolkloreTouchState.java', 'FolkloreTouchView.java'}
    if {p.name for p in app_sources} != expected or len(app_sources) != len(expected) or not sdl_sources:
        raise ValueError('Expected launcher, dialog, touch controls and pinned SDL Java sources')
    manifest = source / 'android/AndroidManifest.xml'
    inputs = app_sources + sdl_sources + [manifest, android, args.native_closure]
    out.mkdir()
    classes, dex, lib = out / 'classes', out / 'dex', out / 'lib/arm64-v8a'
    for directory in (classes, dex, lib):
        directory.mkdir(parents=True)
    env = dict(os.environ, JAVA_HOME=str(args.jdk.resolve()),
               PATH=str(java) + ':' + os.environ.get('PATH', ''), TMPDIR=str(out))
    receipt = {'created_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
               'inputs': {str(p): sha(p) for p in inputs}, 'libraries': {}, 'steps': [],
               'scope': 'Private signed APK assembly and static validation; no device/runtime acceptance.',
               'passed': False, 'installed': False, 'device_tested': False}

    def run(name, command):
        p = subprocess.run([str(v) for v in command], cwd=out, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
        (out / (name + '.log')).write_bytes(p.stdout)
        receipt['steps'].append({'name': name, 'exit_code': p.returncode,
                                 'log': str(out / (name + '.log'))})
        (out / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
        print(json.dumps({'step': name, 'exit_code': p.returncode,
                          'tail': p.stdout.decode(errors='replace')[-1200:]}), flush=True)
        if p.returncode:
            raise RuntimeError(name + ' failed; see preserved log')
        return p.stdout.decode(errors='replace')

    # Compile unchanged upstream separately so application warnings stay errors.
    run('sdl-java', [java/'javac', '--release', '8', '-Xlint:-options',
                    '-classpath', android, '-d', classes, *sdl_sources])
    run('app-java', [java/'javac', '--release', '8', '-Xlint:all,-options', '-Werror',
                    '-classpath', str(android) + ':' + str(classes), '-d', classes, *app_sources])
    run('classes-jar', [java/'jar', '--create', '--file', out/'classes.jar', '-C', classes, '.'])
    run('dex', [tools/'d8', '--min-api', '26', '--lib', android, '--output', dex, out/'classes.jar'])
    run('manifest', [tools/'aapt2', 'link', '-I', android, '--manifest', manifest, '-o', out/'resources.apk'])
    for name, path in libraries.items():
        target = lib/name
        run('strip-' + name, [ndk/'llvm-strip', '--strip-debug', '-o', target, path])
        header = run('elf-' + name, [ndk/'llvm-readelf', '-h', '-lW', target])
        if 'AArch64' not in header:
            raise ValueError('Wrong ABI after strip: ' + name)
        for line in header.splitlines():
            fields = line.split()
            if fields and fields[0] == 'LOAD':
                if int(fields[-1], 16) < 16384 or (int(fields[2], 16)-int(fields[1], 16)) % 16384:
                    raise ValueError('Native load alignment changed: ' + name)
        receipt['libraries'][name] = {'source': str(path), 'source_sha256': sha(path),
                                      'packaged_sha256': sha(target), 'bytes': target.stat().st_size}
    raw = out/'unsigned.apk'
    with zipfile.ZipFile(out/'resources.apk') as resources, zipfile.ZipFile(raw, 'w', zipfile.ZIP_DEFLATED) as apk:
        for entry in resources.infolist():
            apk.writestr(entry, resources.read(entry))
        for path in sorted(dex.glob('classes*.dex')):
            apk.write(path, path.name)
        for path in sorted(lib.iterdir()):
            apk.write(path, 'lib/arm64-v8a/' + path.name)
    run('zipalign', [tools/'zipalign', '-P', '16', '4', raw, out/'aligned.apk'])
    final = out/'Folklore-arm64-test.apk'
    run('sign', [tools/'apksigner', 'sign', '--ks', args.keystore.resolve(),
                 '--ks-pass', 'env:' + args.password_env, '--key-pass', 'env:' + args.password_env,
                 '--v4-signing-enabled', 'false', '--out', final, out/'aligned.apk'])
    run('verify-signature', [tools/'apksigner', 'verify', '--verbose', '--print-certs', final])
    run('verify-alignment', [tools/'zipalign', '-c', '-P', '16', '4', final])
    badging = run('verify-manifest', [tools/'aapt2', 'dump', 'badging', final])
    verify_identity(badging)
    with zipfile.ZipFile(final) as apk:
        expected = {'AndroidManifest.xml', 'resources.arsc'} | {'lib/arm64-v8a/'+name for name in libraries}
        expected |= {p.name for p in dex.glob('classes*.dex')}
        actual = set(apk.namelist())
        if actual - expected - {n for n in actual if n.startswith('META-INF/')}:
            raise ValueError('Unexpected APK payload')
        if not expected <= actual:
            raise ValueError('Missing APK payload')
        with zipfile.ZipFile(out/'resources.apk') as resources:
            for name in ('AndroidManifest.xml', 'resources.arsc'):
                if apk.read(name) != resources.read(name):
                    raise ValueError('AAPT2 output changed during packaging: ' + name)
        for name, row in receipt['libraries'].items():
            if hashlib.sha256(apk.read('lib/arm64-v8a/'+name)).hexdigest() != row['packaged_sha256']:
                raise ValueError('Packaged native bytes differ: ' + name)
    for path, digest in receipt['inputs'].items():
        if sha(Path(path)) != digest:
            raise ValueError('Input changed during build: ' + path)
    for name,path in libraries.items():
        if sha(path) != closure['libraries'][name]['sha256']:
            raise ValueError('Native input changed during build: ' + name)
    receipt.update(passed=True, apk=str(final), apk_sha256=sha(final), bytes=final.stat().st_size)
    (out/'receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')
    print(json.dumps({'apk': str(final), 'bytes': receipt['bytes'], 'sha256': receipt['apk_sha256']}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'sdk', 'jdk', 'ndk', 'sdl-source', 'native-closure', 'keystore', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--password-env', default='FOLKLORE_TEST_KEY_PASSWORD')
    build(parser.parse_args())
