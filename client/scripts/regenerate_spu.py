"""Extract nine hash-bound SPU images from an owned, decrypted USA ELF.

Offsets, hashes and captured descriptor field metadata contain no translated
instructions. Output is private. Existing strict source generators do the lift.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys

sys.dont_write_bytecode = True

ELF_SHA = '51a9eb16ceca3edfb7454b37a9e895f5a4e35bd515a40ae5b918f97c3a1b6c0e'
# Metadata order reproduces the tested registry registration order.
JOBS = [
    ('image03',6254592,36448,'3eee051c8a43fb010503792f592fc820d9caaaa7d57ce0de8662d2a75a6b42fd','e85625f42f2f9a1d8bbb83aaf62b4fd4adb92e1d05098c919fc2b1ab05672700',1),
    ('image_7cef6224b4ea',6200832,53648,'7cef6224b4ea2a4b36ea288e50c39fc591045f82c1b545b89ae2af3cf3c2fef4','5d73db5e74fe8564af6fb4efea1d7d8e5826f895b201df4c9b0f0807c7623a13',1),
    ('image_73654c450d84',6367232,9424,'73654c450d84b0fdca52bdbbc50b6a14c552c6c1533b8a2a1412bc8258ab44f0','f14b5d938b016a269e5671c8cedd6d8564c6ada03ce4695e5c69b66035b0e018',2),
    ('image_ddc5ac0796e3',6309120,13824,'ddc5ac0796e31a6e3af4bdeeef9b227e8b1ff00196de26bba5689902b17182cc','71088d4d6832feada312ec5946cffeef890d4c6511d76048593196697805c3c1',2),
    ('image_ff5a4cf926de',6348032,19088,'ff5a4cf926dee3aa9b4ce511c821b1091c1bc9162744d621550cef8ecfdeaaa2','4018f7d629471f296fe6342199b45479d288e9e4d83051b40a78133d60063138',2),
    ('image_eb11fdd13a47',6376704,17184,'eb11fdd13a4799ba22cce7b8ed05e5b9351c31c5bca58932113ae0ce323cbffc','2ec3373f41e74a4cebf0d628f09b6e6fad1881c95f66a86ac34be24d9426b325',2),
    ('image_bc31db8876d7',6323072,24832,'bc31db8876d70b2f08d8d0c4fa0e932903c6eb9570e8a2854b5bcc987e3ff986','9b509c28d032055db0cf6513bc1b637594539a7fcc2a163be00d61a472234a19',2),
    ('image_7e21b4ee0db6',6393984,65664,'7e21b4ee0db6180fe6086bf7d19f7c4717bae470ba256257952890426ead8399','7bb7f0bc18e4865dbc5590a95416d102ee27f75dac069caaac22e2c4040390ba',2),
    ('image_26159d89362d',6291072,18000,'26159d89362dcf9969db6685fffea2cb184642dded9889004e39d0d60095abb1','8415186dc8504faac6dd21fda4a5fab7c386c789072381d28d520026d7911dcf',2),
]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def virtual_address(elf, offset, size):
    if elf[:6] != b'\x7fELF\x02\x02':
        raise ValueError('Expected a big-endian ELF64')
    phoff = struct.unpack_from('>Q', elf, 32)[0]
    entsize, count = struct.unpack_from('>HH', elf, 54)
    matches = []
    for index in range(count):
        kind, _, start, address, _, length, _, _ = struct.unpack_from('>IIQQQQQQ', elf, phoff + index * entsize)
        if kind == 1 and start <= offset and offset + size <= start + length:
            matches.append(address + offset - start)
    if len(matches) != 1:
        raise ValueError('Image does not map to exactly one ELF load segment')
    return matches[0]


def descriptor(address, size, argument_kind):
    """Recreate captured test configuration, not a live guest descriptor.

    The binary address comes from the ELF load map. Remaining fields are
    documented capture metadata: 16 KiB stack, 16-byte argument record,
    workload class 3, argument variant 1/2 and captured parameter EA.
    Guest-DMA registry resolution reads live descriptor arguments at runtime.
    """
    if size <= 0 or size % 16 or size // 16 > 65535 or argument_kind not in (1, 2):
        raise ValueError('Unsupported descriptor metadata')
    data = bytearray(128)
    struct.pack_into('>Q', data, 0, address)
    struct.pack_into('>H', data, 8, size // 16)
    struct.pack_into('>H', data, 28, 0x4000 // 16)
    struct.pack_into('>I', data, 52, 16)
    struct.pack_into('>HH', data, 68, 3, argument_kind)
    struct.pack_into('>Q', data, 72, 0x4501B900)
    return bytes(data)


def execute(args):
    elf_path, sdk, output, manifest_path = [Path(x).resolve() for x in
                                           (args.elf, args.sdk, args.output, args.manifest)]
    from regenerate_ppu import reject_overlap
    reject_overlap(output, [elf_path, sdk, manifest_path, Path(__file__).parent])
    elf = elf_path.read_bytes()
    if digest(elf) != ELF_SHA:
        raise ValueError('Unsupported ELF SHA256')
    expected = json.loads(manifest_path.read_text(encoding='utf-8-sig'))['files']
    expected = {n: h for n, h in expected.items() if n.startswith('cases/')}
    if len(expected) != 27:
        raise ValueError('Expected hashes for exactly nine generated SPU cases')
    for name in expected:
        if '\\' in name or Path(name).is_absolute() or '..' in Path(name).parts:
            raise ValueError('Unsafe manifest filename')
    output.mkdir(parents=True)
    receipt = {'state': 'running', 'elf_sha256': ELF_SHA, 'images': [],
               'descriptor_provenance': 'Captured test field metadata reconstructed with ELF virtual addresses; no firmware bytes',
               'manifest_sha256': digest(manifest_path.read_bytes()),
               'sdk_tools_sha256': {name: digest((sdk / 'tools' / name).read_bytes())
                                    for name in ('spu_lifter.py', 'spu_disasm.py')},
               'generators_sha256': {name: digest((Path(__file__).parent / name).read_bytes())
                                     for name in ('prepare_startup_aot.py', 'native-lane/recover_image03.py')}}
    try:
        capture = output / 'capture'
        capture.mkdir()
        for case, offset, size, image_sha, desc_sha, variant in JOBS:
            raw = elf[offset:offset + size]
            address = virtual_address(elf, offset, size)
            desc = descriptor(address, size, variant)
            if digest(raw) != image_sha or digest(desc) != desc_sha:
                raise ValueError(f'Image or descriptor mismatch: {case}')
            stem = 'spurs-job-45017080-78B93C8168610ECC-03' if case == 'image03' else case
            (capture / (stem + '.image.bin')).write_bytes(raw)
            (capture / (stem + '.desc.bin')).write_bytes(desc)
            receipt['images'].append({'case': case, 'offset': offset, 'size': size,
                                      'elf_virtual_address': address, 'sha256': image_sha,
                                      'descriptor_sha256': desc_sha})
        sys.path[:0] = [str(Path(__file__).parent / 'native-lane'), str(sdk / 'tools')]
        # image03 was lifted before FPSCR instrumentation; later extra jobs
        # used the current translator. Preserve that tested source provenance.
        revision = 'd055c40ec4d38dab2b14906bd1a5ffa4d53437d5'
        from pinned_tools import read_pinned_tool
        old_lifter = read_pinned_tool(sdk, revision, 'spu_lifter.py')
        old_hash = 'c14f224c3bb44fadea1a26493c3a36ef2a57d41389e6078e4bbec988a49c5bc2'
        if digest(old_lifter) != old_hash:
            raise ValueError('Historical SPU lifter hash mismatch')
        old_path = output / 'image03_lifter.py'
        old_path.write_bytes(old_lifter)
        spec = importlib.util.spec_from_file_location('spu_lifter', old_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules['spu_lifter'] = module
        spec.loader.exec_module(module)
        receipt['image03_lifter'] = {'revision': revision, 'sha256': old_hash}
        from recover_image03 import resolve_image03_tables, walk, generate_blocks
        from spu_disasm import spu_decode
        from spu_lifter import SPULifter
        raw = (capture / 'spurs-job-45017080-78B93C8168610ECC-03.image.bin').read_bytes()
        tables = resolve_image03_tables(raw, spu_decode)
        seeds = list(dict.fromkeys([0] + [t for table in tables for t in table['targets']]))
        seen, cfg = walk(raw, spu_decode, SPULifter._branch_target, seeds)
        recovery = output / 'recovery'
        generation = generate_blocks(raw, seen, cfg, recovery, 0x8000)
        (recovery / 'generation.private.json').write_text(json.dumps(generation), encoding='utf-8')
        # The existing generic-only generator discards this empty previous
        # profile; it receives no pretranslated source or protected payload.
        previous = output / 'empty-previous'
        previous.mkdir()
        placeholder = 'cases/b8000/private_jobs.c'
        (previous / placeholder).parent.mkdir(parents=True)
        (previous / placeholder).write_bytes(b'')
        (previous / 'profile-manifest.json').write_text(json.dumps({
            'files': {placeholder: digest(b'')}, 'native_translation_unit': placeholder}), encoding='utf-8')
        command = [sys.executable, str(Path(__file__).with_name('prepare_startup_aot.py')),
                   '--sdk', str(sdk), '--previous', str(previous), '--recovery', str(recovery),
                   '--capture', str(capture), '--out', str(output / 'profile'), '--generic-only']
        for case, *_ in JOBS[1:]:
            command += ['--extra-capture', str(capture / (case + '.image.bin'))]
        receipt['command'] = command
        with (output / 'generation.log').open('wb') as log:
            subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT, cwd=output)
        rows = []
        for name, target_sha in expected.items():
            path = output / 'profile' / name
            actual = digest(path.read_bytes()) if path.is_file() else None
            rows.append({'name': name, 'expected': target_sha, 'actual': actual, 'match': actual == target_sha})
        receipt['files'] = rows
        if not all(row['match'] for row in rows):
            raise ValueError('Generated SPU source differs from tested profile')
        receipt['state'] = 'verified'
        receipt['profile'] = str(output / 'profile')
    except Exception as error:
        receipt.update(state='failed', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        (output / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('elf', 'sdk', 'output', 'manifest'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        result = execute(args)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, str(error) + '\n')
    print(json.dumps({'state': result['state'], 'profile': result['profile']}))


if __name__ == '__main__':
    main()
