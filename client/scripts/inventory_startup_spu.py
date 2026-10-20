"""Inventory BCUS98147 1.00 raw startup SPU images from its original table.

Optional extraction stays in a caller-selected private directory. No game
bytes belong in the repository. This is static inventory, not execution proof.
"""
import argparse
import hashlib
import json
import struct
from pathlib import Path

ELF_SHA256 = '51a9eb16ceca3edfb7454b37a9e895f5a4e35bd515a40ae5b918f97c3a1b6c0e'
TABLE = 0x5B7058
COUNT = 9


def inventory(data):
    if hashlib.sha256(data).hexdigest() != ELF_SHA256:
        raise ValueError('EBOOT identity differs from verified BCUS98147 1.00 ELF')
    phoff = struct.unpack_from('>Q', data, 32)[0]
    phsize, phcount = struct.unpack_from('>HH', data, 54)
    segments = [struct.unpack_from('>IIQQQQQQ', data, phoff + n * phsize)
                for n in range(phcount)]
    shoff = struct.unpack_from('>Q', data, 40)[0]
    shsize, shcount = struct.unpack_from('>HH', data, 58)
    sections = [struct.unpack_from('>IIQQQQIIQQ', data, shoff + n * shsize)
                for n in range(shcount)]

    def offset(address, size):
        matches = [s[2] + address - s[3] for s in segments
                   if s[0] == 1 and s[3] <= address and address + size <= s[3] + s[5]]
        if len(matches) != 1:
            raise ValueError('image is not contained in one file-backed load segment')
        return matches[0]

    images = []
    for n in range(COUNT):
        record = TABLE + 12 * n
        name_address, address, size = struct.unpack_from('>III', data, record)
        if not size or size % 16 or size > 0x40000:
            raise ValueError('invalid source image extent')
        start = offset(address, size)
        matching = [s for s in sections if s[1] == 1 and s[3] == address
                    and s[4] == start and size <= s[5]]
        if len(matching) != 1:
            raise ValueError('image table and original section headers disagree')
        image = data[start:start + size]
        images.append({'index': n, 'table_record_offset': record,
                       'name_pointer': name_address, 'ppu_address': address,
                       'file_offset': start, 'size': size,
                       'section_size': matching[0][5],
                       'sha256': hashlib.sha256(image).hexdigest()})
    ordered = sorted(images, key=lambda x: x['file_offset'])
    if any(a['file_offset'] + a['size'] > b['file_offset']
           for a, b in zip(ordered, ordered[1:])):
        raise ValueError('image extents overlap')
    return {'schema': 'folklore-original-startup-spu-inventory-v1',
            'elf_sha256': ELF_SHA256, 'table_offset': TABLE, 'images': images,
            'scope': 'Nine raw startup images in this verified EBOOT table only. '
                     'Not proof of all images used by archives, modules or campaigns.',
            'runtime_tested': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('elf', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--extract-index', type=int, action='append', default=[])
    args = parser.parse_args()
    if args.out.exists():
        parser.error('output must be a new private directory')
    if any(n < 0 or n >= COUNT for n in args.extract_index):
        parser.error('extract index outside the source table')
    data = args.elf.read_bytes()
    report = inventory(data)
    args.out.mkdir(parents=True)
    for entry in report['images']:
        if entry['index'] in args.extract_index:
            target = args.out / ('image_' + entry['sha256'] + '.bin')
            target.write_bytes(data[entry['file_offset']:entry['file_offset'] + entry['size']])
            entry['private_image'] = str(target.resolve())
    (args.out / 'inventory.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'images': len(report['images']), 'extracted': len(set(args.extract_index)),
                      'inventory': str(args.out / 'inventory.json')}))


if __name__ == '__main__':
    main()
