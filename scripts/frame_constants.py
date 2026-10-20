#!/usr/bin/env python3
"""Find 30/60 FPS timing constants in a decrypted PS3 PPU ELF and the code that loads them.

Read-only static scan of your own EBOOT.elf; nothing is executed or modified.
Results are leads for frame-rate dependent code such as garment and hair
dynamics, audio pacing or motion input. They do not prove that a function is
frame-rate dependent, and constants built at run time are not found.
"""
import argparse
from array import array
from bisect import bisect_right
import hashlib
import json
import math
from pathlib import Path
import struct
import sys

TARGETS = {
    '1/30 s': 1/30, '1/60 s': 1/60, '1/29.97 s': 1001/30000, '1/59.94 s': 1001/60000,
    '30 fps': 30.0, '60 fps': 60.0, '29.97 fps': 30000/1001, '59.94 fps': 60000/1001,
    '33.33 ms': 100/3, '16.67 ms': 50/3,
}
FLOAT_LOADS = {48: 'lfs', 49: 'lfsu', 50: 'lfd', 51: 'lfdu'}
# Primary opcodes that never write a GPR; everything untracked pops rt and ra.
NO_GPR_WRITE = {2, 3, 10, 11, 16, 36, 38, 44, 46, 48, 50, 52, 54, 59, 63}
BLR, NOP = 0x4E800020, 0x60000000
ADDR = 0xffffffff


def load_segments(data: bytes):
    """Return (entry, [(flags, offset, vaddr, filesz)]) for nonempty file-backed PT_LOADs."""
    if len(data) < 64 or data[:7] != b'\x7fELF\x02\x02\x01':
        raise ValueError('expected ELF64 big-endian version 1, not SELF; decrypt EBOOT.BIN first')
    etype, machine, version, entry, phoff, _, _, ehsize, phsize, phnum = struct.unpack_from('>HHIQQQIHHH', data, 16)
    if (etype, machine, version, ehsize, phsize) != (2, 21, 1, 64, 56):
        raise ValueError('unsupported ELF header; expected a PPC64 executable')
    if not phnum or phnum == 0xffff or phoff < 64 or phoff + phnum*56 > len(data):
        raise ValueError('invalid or truncated program header table')
    segs = []
    for i in range(phnum):
        kind, flags, offset, va, _, filesz, memsz, _ = struct.unpack_from('>IIQQQQQQ', data, phoff+i*56)
        if kind != 1 or not filesz:
            continue
        if offset + filesz > len(data) or filesz > memsz or va + memsz > 1 << 64:
            raise ValueError('truncated or malformed PT_LOAD')
        segs.append((flags, offset, va, filesz))
    if not any(flags & 1 for flags, *_ in segs):
        raise ValueError('no executable file-backed PT_LOAD')
    return entry, segs


def read(data, segs, va, size):
    for _, offset, base, filesz in segs:
        if base <= va and va + size <= base + filesz:
            return data[offset+va-base:offset+va-base+size]
    return None


def words_of(data, offset, filesz):
    words = array('I')
    words.frombytes(data[offset:offset + filesz//4*4])
    if sys.byteorder == 'little':
        words.byteswap()
    return words


def toc_from_entry(data, segs, entry):
    """PS3 entry points name an 8-byte descriptor {u32 code, u32 toc}."""
    opd = read(data, segs, entry, 8)
    if opd is None:
        return None
    code, toc = struct.unpack('>II', opd)
    return toc if any(f & 1 and b <= code < b+n for f, _, b, n in segs) else None


def nearest(value, tolerance):
    name = min(TARGETS, key=lambda n: abs(value-TARGETS[n]) / TARGETS[n])
    return name if abs(value-TARGETS[name]) <= tolerance*TARGETS[name] else None


def bit_window(value, fmt, ifmt, shift):
    return struct.unpack(ifmt, struct.pack(fmt, value))[0] >> shift


def find_constants(data, segs, tolerance=1e-5):
    """Map address -> constant record for float and double encodings near TARGETS."""
    if not 0 < tolerance <= 0.01:
        raise ValueError('tolerance must be in (0, 0.01]')
    singles, highs = set(), set()
    for target in TARGETS.values():
        lo, hi = target*(1-tolerance), target*(1+tolerance)
        singles.update(range(bit_window(lo, '>f', '>I', 0)-1, bit_window(hi, '>f', '>I', 0)+2))
        highs.update(range(bit_window(lo, '>d', '>Q', 32)-1, bit_window(hi, '>d', '>Q', 32)+2))
    hits = {}
    for flags, offset, base, filesz in segs:
        words = words_of(data, offset, filesz)
        for i, w in enumerate(words):
            if w in singles:
                value = struct.unpack('>f', w.to_bytes(4, 'big'))[0]
                name = nearest(value, tolerance)
                if name:
                    hits[base+i*4] = dict(address=base+i*4, type='float', value=value, target=name, segment_flags=flags)
            if w in highs and i+1 < len(words):
                value = struct.unpack('>d', ((w << 32) | words[i+1]).to_bytes(8, 'big'))[0]
                name = math.isfinite(value) and nearest(value, tolerance)
                if name:
                    hits[base+i*4] = dict(address=base+i*4, type='double', value=value, target=name, segment_flags=flags)
    return hits


def function_starts(data, segs, toc):
    """Estimated starts: descriptor targets plus the first real word after each blr."""
    starts, sources = set(), {}
    for flags, offset, base, filesz in segs:
        words = words_of(data, offset, filesz)
        if flags & 1:
            starts.add(base)
            sources.setdefault(base, 'segment-start')
            for i, w in enumerate(words):
                if w == BLR:
                    j = i+1
                    while j < len(words) and words[j] in (0, NOP):
                        j += 1
                    if j < len(words):
                        starts.add(base+j*4)
                        sources.setdefault(base+j*4, 'after-blr')
        elif toc is not None:
            for i in range(len(words)-1):
                code = words[i]
                if words[i+1] == toc and not code & 3 and any(f & 1 and b <= code < b+n for f, _, b, n in segs):
                    starts.add(code)
                    sources[code] = 'descriptor'
    return sorted(starts), sources


def find_references(data, segs, hits, toc, window=16):
    """Track lis/addi/ori, TOC-relative and TOC-pointer address builds into lfs/lfd."""
    refs = []
    for flags, offset, base, filesz in segs:
        if not flags & 1:
            continue
        known = {}  # gpr -> (address, word index where it was set)
        for i, w in enumerate(words_of(data, offset, filesz)):
            op, rt, ra, d = w >> 26, (w >> 21) & 31, (w >> 16) & 31, w & 0xffff
            simm = d - 0x10000 if d & 0x8000 else d

            def reg(r):
                if r == 2:
                    return toc
                v = known.get(r)
                return v[0] if v and i - v[1] <= window else None
            if op in FLOAT_LOADS:
                b = reg(ra) if ra else None
                if b is not None and (b+simm) & ADDR in hits:
                    hit = hits[(b+simm) & ADDR]
                    refs.append(dict(address=base+i*4, word=f'{w:08x}', insn=FLOAT_LOADS[op],
                                     target_address=hit['address'], target=hit['target'], via='toc' if ra == 2 else 'register'))
                if op in (49, 51):
                    known.pop(ra, None)
            elif op in (14, 15):
                b = 0 if ra == 0 else reg(ra)
                if b is None or rt == 2:
                    known.pop(rt, None)
                else:
                    known[rt] = ((b + (simm << 16 if op == 15 else simm)) & ADDR, i)
            elif op == 24:  # ori ra, rs, ui
                s = reg(rt)
                if s is None or ra == 2:
                    known.pop(ra, None)
                else:
                    known[ra] = ((s | d) & ADDR, i)
            elif op == 32 or (op == 58 and not w & 3):  # lwz / ld
                b = reg(ra) if ra else None
                size, disp = (4, simm) if op == 32 else (8, simm & ~3)
                ptr = read(data, segs, (b+disp) & ADDR, size) if b is not None else None
                if ptr is None or rt == 2:
                    known.pop(rt, None)
                else:
                    known[rt] = (int.from_bytes(ptr, 'big') & ADDR, i)
            elif op in (17, 18) or (op == 19 and (w >> 1) & 1023 in (16, 528)):
                known.clear()
            elif op == 62:
                if w & 3 == 1:
                    known.pop(ra, None)
            elif op not in NO_GPR_WRITE:
                known.pop(rt, None)
                known.pop(ra, None)
    return refs


def scan(data, tolerance=1e-5, toc=None, window=16):
    entry, segs = load_segments(data)
    toc_source = 'argument'
    if toc is None:
        toc, toc_source = toc_from_entry(data, segs, entry), 'entry descriptor'
    hits = find_constants(data, segs, tolerance)
    refs = find_references(data, segs, hits, toc, window)
    starts, sources = function_starts(data, segs, toc)
    for ref in refs:
        k = bisect_right(starts, ref['address'])
        if k:
            ref['function'] = f'func_{starts[k-1]:08X}'
            ref['function_source'] = sources[starts[k-1]]
    return dict(schema_version=1, scope='static leads only; not proof of frame-rate dependence',
                sha256=hashlib.sha256(data).hexdigest(), toc=toc, toc_source=toc_source if toc is not None else None,
                tolerance=tolerance, constants=sorted(hits.values(), key=lambda h: h['address']), references=refs)


def text_report(result):
    lines = [f"sha256 {result['sha256']}"]
    toc = result['toc']
    lines.append(f"TOC base 0x{toc:08X} ({result['toc_source']})" if toc is not None
                 else 'TOC base unknown; TOC-relative loads skipped (pass --toc to set it)')
    lines.append(f"\nConstants found: {len(result['constants'])}")
    for c in result['constants']:
        flags = ''.join(ch for bit, ch in ((4, 'R'), (2, 'W'), (1, 'X')) if c['segment_flags'] & bit)
        lines.append(f"  0x{c['address']:08X}  {c['type']:<6}  {c['value']:<12.7g}  {c['target']:<10}  [{flags}]")
    lines.append(f"\nCode that loads them: {len(result['references'])}")
    for r in result['references']:
        where = f"{r.get('function', '?')} ({r.get('function_source', 'no estimate')})"
        lines.append(f"  0x{r['address']:08X}  {r['insn']:<5} <- 0x{r['target_address']:08X}  {r['target']:<10}  {where}")
    funcs = sorted({r['function'] for r in result['references'] if 'function' in r})
    lines.append(f"\nFunctions to inspect first ({len(funcs)}): " + (', '.join(funcs) if funcs else 'none'))
    lines.append('Function names are estimates; confirm each against the recompiler function list.')
    return '\n'.join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf', required=True, type=Path, help='your decrypted EBOOT.elf')
    p.add_argument('--toc', type=lambda x: int(x, 0), help='TOC base override, e.g. 0x8969a8')
    p.add_argument('--tolerance', type=float, default=1e-5, help='relative match tolerance, at most 0.01')
    p.add_argument('--window', type=int, default=16, help='instructions an address build stays valid')
    p.add_argument('--json', action='store_true', help='print JSON instead of text')
    args = p.parse_args(argv)
    try:
        if not 1 <= args.window <= 256:
            raise ValueError('window must be between 1 and 256')
        result = scan(args.elf.resolve(strict=True).read_bytes(), args.tolerance, args.toc, args.window)
    except (OSError, ValueError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2) if args.json else text_report(result))
    return 0


if __name__ == '__main__':
    sys.exit(main())
