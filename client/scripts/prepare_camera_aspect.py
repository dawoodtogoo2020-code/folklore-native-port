"""Guarded local transformation of owned generated source; contains no game code.

The original generated source is preserved. CMake compiles this one replacement
translation unit only after its exact input digest and unique anchor pass.
"""
import argparse
import hashlib
import json
from pathlib import Path

SOURCE_SHA256 = 'a98e40f9a2dd8907d603bb5385a396a391568fe8e010b976c9955009a78bf9fc'
SOURCE005_SHA256 = '61f393d644d4cc73116d2a43b2bbccf564ac66b80bc89cfbda52935a3bd41d65'
FUNCTION = 'void func_00149B28(ppu_context* ctx) {'
ANCHOR = '        { uint32_t tmp = vm_read32(ctx->gpr[2] + -0x3F64); float ftmp; memcpy(&ftmp, &tmp, 4); ctx->fpr[11] = ftmp; }'
CALL = '\n        ctx->fpr[11] = folklore_camera_aspect_override(ctx->fpr[11]);'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def transform(data, unit='ppu_recomp_006.cpp'):
    expected=SOURCE005_SHA256 if unit=='ppu_recomp_005.cpp' else SOURCE_SHA256
    functions=('00145AD4','00147D10') if unit=='ppu_recomp_005.cpp' else ('00149B28',)
    if digest(data) != expected:
        raise ValueError('Camera aspect source digest differs from the reviewed owned translation')
    text = data
    anchor=ANCHOR.encode('ascii')
    if text.count(b'#include "ppu_recomp.h"') != 1:raise ValueError('Include anchor differs')
    for fn in functions:
        function=f'void func_{fn}(ppu_context* ctx) {{'.encode('ascii')
        call=f'\n        ctx->fpr[11] = folklore_camera_aspect_override(ctx->fpr[11],0x{fn}u);'.encode('ascii')
        if text.count(function)!=1:raise ValueError('Camera function anchor differs')
        start=text.index(function);end=text.index(b'\nvoid func_',start+1)
        region=text[start:end]
        if region.count(anchor)!=1:raise ValueError('Camera aspect load anchor differs')
        text=text[:start]+region.replace(anchor,anchor+call,1)+text[end:]
    return text.replace(b'#include "ppu_recomp.h"',
                        b'#include "ppu_recomp.h"\n#include "camera_aspect.h"', 1)


def prepare(source, output):
    source, output = Path(source).resolve(), Path(output).absolute()
    unit=source.name if source.name=='ppu_recomp_005.cpp' else 'ppu_recomp_006.cpp'
    if output.resolve() == source or output.name != unit:
        raise ValueError('Use a separate build output with the matching translation unit name')
    for path in (output, *output.parents):
        if path.exists() and (path.is_symlink() or getattr(path.lstat(), 'st_file_attributes', 0) & 0x400):
            raise ValueError('Camera bridge output cannot traverse a reparse point')
    data = source.read_bytes()
    changed = transform(data,unit)
    # Refuse to replace unrelated or changed source, including an old prototype.
    if output.exists() and output.read_bytes() != changed:
        raise ValueError('Existing camera bridge differs; use a fresh build directory')
    output.parent.mkdir(parents=True, exist_ok=True)
    if not output.exists():
        output.write_bytes(changed)
    receipt = {'source': str(source), 'source_sha256': digest(data),
               'output': str(output), 'output_sha256': digest(changed),
               'functions': ['00145AD4','00147D10'] if unit=='ppu_recomp_005.cpp' else ['00149B28'], 'aspect_load': '0056AB9C',
               'original_preserved': True, 'default_behavior': 'original aspect'}
    (output.parent / (unit+'-aspect-receipt.json')).write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.output)))
