"""Guarded local transformation of owned generated source; contains no game code.

Folklore's main loop (func_00180E0C) measures each frame's real elapsed time on
the 79.8 MHz timebase in 10 us units and hands it to the simulation as the
frame delta. At loc_00181340 it sleeps 10000 us at a time until more than 3000
units (30 ms) have passed since the previous frame: that wait alone caps the
game at about 30 frames per second.

This rewrites only those two literals at that one site into calls to
native/frame_limiter.h. With FOLKLORE_FRAME_RATE unset or "30" the calls return
the original 3000 and 10000, so the default build behaves exactly like the
original code. The original generated source is preserved; CMake compiles this
replacement translation unit only after its exact input digest and unique
anchors pass.
"""
import argparse
import hashlib
import json
from pathlib import Path

SOURCE_SHA256 = '7984d7291c63e39d36c8f1d59062ec1785d075f522278432daf7b1530c0bd5d0'
UNIT = 'ppu_recomp_007.cpp'
FUNCTION = b'void func_00180E0C(ppu_context* ctx) {'
SITE = (b'loc_00181340:\n'
        b'        ctx->lr = 0x00181344; func_0021CCB4(ctx); DRAIN_TRAMPOLINE(ctx);\n'
        b'        /* nop */;\n'
        b'        ctx->gpr[11] = (int64_t)(int32_t)(0x8D);\n'
        b'        { int64_t a = (int32_t)ctx->gpr[3]; int64_t b = (int64_t)0xBB8; '
        b'uint32_t cr_val = (a < b) ? 8 : (a > b) ? 4 : 2; ctx->cr = (ctx->cr & ~(0xFu << 0)) | (cr_val << 0); }\n'
        b'        ctx->gpr[3] = (int64_t)(int32_t)(0x2710);\n')
REPLACEMENT = (b'loc_00181340:\n'
               b'        ctx->lr = 0x00181344; func_0021CCB4(ctx); DRAIN_TRAMPOLINE(ctx);\n'
               b'        /* nop */;\n'
               b'        ctx->gpr[11] = (int64_t)(int32_t)(0x8D);\n'
               b'        { int64_t a = (int32_t)ctx->gpr[3]; int64_t b = (int64_t)folklore_frame_floor_units(); '
               b'uint32_t cr_val = (a < b) ? 8 : (a > b) ? 4 : 2; ctx->cr = (ctx->cr & ~(0xFu << 0)) | (cr_val << 0); }\n'
               b'        ctx->gpr[3] = (int64_t)(int32_t)(folklore_frame_sleep_us());\n')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def transform(data):
    if digest(data) != SOURCE_SHA256:
        raise ValueError('Frame limiter source digest differs from the reviewed owned translation')
    eol = b'\r\n' if b'\r\n' in data else b'\n'
    site, replacement = SITE.replace(b'\n', eol), REPLACEMENT.replace(b'\n', eol)
    if data.count(b'#include "ppu_recomp.h"') != 1:
        raise ValueError('Include anchor differs')
    if data.count(FUNCTION) != 1:
        raise ValueError('Main loop function anchor differs')
    start = data.index(FUNCTION)
    end = data.index(b'\nvoid func_', start + 1)
    region = data[start:end]
    if region.count(site) != 1 or data.count(site) != 1:
        raise ValueError('Frame limiter site differs')
    text = data[:start] + region.replace(site, replacement, 1) + data[end:]
    return text.replace(b'#include "ppu_recomp.h"', b'#include "ppu_recomp.h"' + eol + b'#include "frame_limiter.h"', 1)


def prepare(source, output):
    source, output = Path(source).resolve(), Path(output).absolute()
    if output.resolve() == source or output.name != UNIT or source.name != UNIT:
        raise ValueError('Use a separate build output with the matching translation unit name')
    for path in (output, *output.parents):
        if path.exists() and (path.is_symlink() or getattr(path.lstat(), 'st_file_attributes', 0) & 0x400):
            raise ValueError('Frame limiter output cannot traverse a reparse point')
    data = source.read_bytes()
    changed = transform(data)
    if output.exists() and output.read_bytes() != changed:
        raise ValueError('Existing frame limiter bridge differs; use a fresh build directory')
    output.parent.mkdir(parents=True, exist_ok=True)
    if not output.exists():
        output.write_bytes(changed)
    receipt = {'source': str(source), 'source_sha256': digest(data), 'output': str(output),
               'output_sha256': digest(changed), 'function': '00180E0C', 'site': '00181340',
               'original_constants': {'floor_units_10us': 3000, 'sleep_us': 10000},
               'original_preserved': True, 'default_behavior': 'original 30 FPS floor'}
    (output.parent / (UNIT + '-limiter-receipt.json')).write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.output)))
