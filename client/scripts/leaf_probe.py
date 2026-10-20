#!/usr/bin/env python3
"""Bounded ELF PPU leaf regression probe. No memory, OS, or game execution.

Only the explicitly whitelisted GPR operations are accepted. Success establishes
agreement with an independent interpreter for the recorded inputs, not hardware
equivalence or whole-program compatibility. Upstream Python is trusted local code.
"""
import argparse
import contextlib
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import platform
import random
import re
import struct
import subprocess
import sys

MASK = (1 << 64) - 1
SEED = 0x5050554C454146
BLR = 0x4E800020


def read_code(data: bytes, start: int, end: int) -> bytes:
    """Return a <=64 byte range inside one executable file-backed PT_LOAD."""
    if type(start) is not int or type(end) is not int:
        raise ValueError('addresses must be integers')
    if not 0 <= start < end <= MASK or start % 4 or end % 4 or end-start > 64:
        raise ValueError('range must be aligned, nonempty, and at most 64 bytes')
    if len(data) < 64 or data[:7] != b'\x7fELF\x02\x02\x01':
        raise ValueError('expected ELF64 big-endian version 1, not SELF')
    h = struct.unpack_from('>HHIQQQIHHHHHH', data, 16)
    etype, machine, version, _, phoff, shoff, _, ehsize, phsize, phnum, shsize, shnum, shstr = h
    if (etype, machine, version, ehsize, phsize) != (2, 21, 1, 64, 56):
        raise ValueError('unsupported ELF header')
    if not phnum or phnum == 0xffff or phoff < 64 or phoff + phnum*56 > len(data):
        raise ValueError('invalid or truncated program header table')
    if shnum:
        if shsize != 64 or shoff < 64 or shoff + shnum*64 > len(data) or shstr >= shnum:
            raise ValueError('invalid or truncated section header table')
    elif shoff or shstr:
        raise ValueError('extended section numbering is not supported')
    matches = []
    for i in range(phnum):
        kind, flags, offset, va, _, filesz, memsz, align = struct.unpack_from('>IIQQQQQQ', data, phoff+i*56)
        if offset + filesz > len(data) or va + memsz > MASK+1:
            raise ValueError('truncated or overflowing program segment')
        if kind != 1:
            continue
        # This retail PS3 ELF contains a zero-sized placeholder PT_LOAD whose
        # file offset is not congruent with its unused virtual address.
        # It maps no bytes. Keep all checks on every nonempty mapping.
        if filesz == 0 and memsz == 0:
            continue
        if filesz > memsz or (align not in (0, 1) and (align & (align-1) or va % align != offset % align)):
            raise ValueError('malformed PT_LOAD')
        if va <= start and end <= va+filesz and flags & 1:
            at = offset+start-va
            matches.append(data[at:at+end-start])
    if len(matches) != 1:
        raise ValueError('range is not in exactly one executable file-backed PT_LOAD')
    return matches[0]


def interpret(words: list[int], gpr: list[int]) -> list[int]:
    """Independent PowerPC whitelist reference; never mutates its inputs."""
    if len(gpr) != 32 or any(type(x) is not int or not 0 <= x <= MASK for x in gpr):
        raise ValueError('expected 32 uint64 registers')
    if not 1 <= len(words) <= 16 or words[-1] != BLR:
        raise ValueError('expected 1..16 words ending with exact blr')
    out = list(gpr)
    for i, w in enumerate(words):
        if type(w) is not int or not 0 <= w <= 0xffffffff:
            raise ValueError('instruction is not uint32')
        if w == BLR:
            if i != len(words)-1:
                raise ValueError('early blr')
            continue
        op, rs, ra, rb, imm = w >> 26, (w >> 21) & 31, (w >> 16) & 31, (w >> 11) & 31, w & 0xffff
        if op in (14, 15):
            signed = imm if imm < 0x8000 else imm-0x10000
            out[rs] = ((out[ra] if ra else 0) + (signed << (16 if op == 15 else 0))) & MASK
        elif op in (24, 25, 26, 27):
            value = imm << (16 if op in (25, 27) else 0)
            out[ra] = (out[rs] | value if op in (24, 25) else out[rs] ^ value) & MASK
        elif op == 31 and (w >> 1) & 1023 == 444 and not w & 1:
            out[ra] = (out[rs] | out[rb]) & MASK
        else:
            raise ValueError(f'unsupported instruction {i}: 0x{w:08x}')
    return out


def checked_body(function) -> str:
    if getattr(function, 'fallthrough_to', None):
        raise ValueError('lifted function falls through')
    if getattr(function, 'calls', None):
        raise ValueError('lifted function calls external code')
    body = '\n'.join(function.body_lines)
    if re.search(r'TODO|unsupported|unknown', body, re.I):
        raise ValueError('unresolved upstream body')
    plain = re.sub(r'/\*.*?\*/|//[^\n]*', '', body, flags=re.S)
    if not re.search(r'\breturn\s*;\s*$', plain):
        raise ValueError('body has no terminal return')
    # Reject helpers, other fields, control flow, preprocessing, and identifiers.
    stripped = re.sub(r'ctx\s*->\s*gpr\s*\[\s*(?:[0-9]|[12][0-9]|3[01])\s*\]', 'REG', plain)
    stripped = re.sub(r'\b(?:0[xX][0-9a-fA-F]+|[0-9]+)[uUlL]*\b', '', stripped)
    names = set(re.findall(r'[A-Za-z_][A-Za-z_0-9]*', stripped))
    if names - {'REG', 'int64_t', 'uint64_t', 'int32_t', 'uint32_t', 'return'}:
        raise ValueError(f'unapproved body identifiers: {sorted(names)}')
    if re.search(r'[^\sA-Za-z_0-9()=;+|^<>-]', stripped) or re.search(r'REG\s*\(', stripped):
        raise ValueError('unapproved C body syntax')
    return body  # Actual upstream lines are inserted without rewriting.


def cases(count):
    edges = [0, MASK, 1, 0x7fff, 0x8000, 0xffff, 0x7fffffff, 0x80000000,
             0xffffffff, 0x100000000, (1 << 63)-1, 1 << 63, MASK-1,
             0xaaaaaaaaaaaaaaaa, 0x5555555555555555]
    structured = [[n]*32 for n in edges]
    structured += [[edges[(i+j) % len(edges)] for i in range(32)] for j in range(len(edges))]
    rng = random.Random(SEED)
    return (structured + [[rng.getrandbits(64) for _ in range(32)] for _ in range(max(0, count-len(structured)))])[:count]


def run_command(args, **kwargs):
    p = subprocess.run(args, text=True, capture_output=True, timeout=60, **kwargs)
    if p.returncode:
        raise ValueError(f'command failed ({p.returncode}): {p.stderr[-4000:]}')
    return p


def contained(path, root):
    return path == root or root in path.parents


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('elf', 'upstream', 'compiler', 'work-dir', 'report'):
        p.add_argument('--'+name, required=True, type=Path)
    for name in ('start', 'end'):
        p.add_argument('--'+name, required=True, type=lambda x: int(x, 0))
    p.add_argument('--cases', type=int, default=1024)
    args = p.parse_args()
    report = None
    try:
        if not 32 <= args.cases <= 100000:
            raise ValueError('cases must be between 32 and 100000 to retain structured patterns')
        elf = args.elf.resolve(strict=True)
        upstream = args.upstream.resolve(strict=True)
        compiler = args.compiler.resolve(strict=True)
        work, report = args.work_dir.resolve(), args.report.resolve()
        if not elf.is_file() or not compiler.is_file() or not upstream.is_dir():
            raise ValueError('expected ELF/compiler files and upstream directory')
        if contained(work, upstream) or contained(upstream, work) or contained(elf, work):
            raise ValueError('work directory aliases protected input locations')
        if contained(report, upstream) or report in (elf, compiler) or report.exists():
            raise ValueError('report must be a new output outside protected inputs')
        if not report.parent.is_dir():
            raise ValueError('report parent must already exist')
        source = work/'leaf_driver.c'
        executable = work/('leaf_driver.exe' if os.name == 'nt' else 'leaf_driver')
        if source.exists() or executable.exists() or report in (source, executable):
            raise ValueError('generated outputs must be new and distinct')
        data = elf.read_bytes()
        code = read_code(data, args.start, args.end)
        words = list(struct.unpack('>'+str(len(code)//4)+'I', code))
        interpret(words, [0]*32)  # Validation precedes all upstream imports.
        tool_dir = (upstream/'tools').resolve(strict=True)
        head, tracked_clean = 'unknown', None
        try:
            head = run_command(['git', '-C', str(upstream), 'rev-parse', 'HEAD']).stdout.strip()
            state = run_command(['git', '-C', str(upstream), 'status', '--porcelain', '--untracked-files=no'])
            tracked_clean = not bool(state.stdout.strip())
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        if tracked_clean is False:
            raise ValueError('upstream has modified tracked files; pin or review them first')
        module_hashes = {name+'.py': hashlib.sha256((tool_dir/(name+'.py')).read_bytes()).hexdigest()
                         for name in ('ppu_disasm', 'ppu_lifter')}
        for name in ('ppu_disasm', 'ppu_lifter'):
            if name in sys.modules:
                raise ValueError('upstream module already loaded')
        sys.path.insert(0, str(tool_dir))
        # Keep bytecode out of the upstream input tree and diagnostics off stdout.
        sys.dont_write_bytecode = True
        with contextlib.redirect_stdout(io.StringIO()):
            disasm = importlib.import_module('ppu_disasm')
            lifter = importlib.import_module('ppu_lifter')
            for mod in (disasm, lifter):
                if Path(mod.__file__).resolve().parent != tool_dir:
                    raise ValueError('upstream module resolved outside explicit tools path')
            insns = disasm.disassemble_bytes(code, args.start, True)
            function = lifter.PPULifter().lift_function(insns, args.start, args.end)
        if function.name != f'func_{args.start:08X}':
            raise ValueError('unexpected upstream function identity')
        body = checked_body(function)
        driver = '''#include <stdint.h>
#include <stdio.h>
#include <inttypes.h>
typedef struct { uint64_t gpr[32]; } ppu_context;
static void lifted(ppu_context* ctx) {
''' + body + '''
}
int main(void) {
    ppu_context ctx;
    for (;;) {
        int n = scanf("%" SCNx64, &ctx.gpr[0]);
        if (n == EOF) return 0;
        if (n != 1) return 2;
        for (int i=1; i<32; ++i) if (scanf("%" SCNx64, &ctx.gpr[i]) != 1) return 3;
        lifted(&ctx);
        for (int i=0; i<32; ++i) printf("%016" PRIx64 "%c", ctx.gpr[i], i==31?'\\n':' ');
    }
}
'''
        version = run_command([str(compiler), '--version']).stdout.strip()
        work.mkdir(parents=True, exist_ok=True)
        with source.open('x', encoding='utf-8', newline='\n') as f:
            f.write(driver)
        compile_command = [str(compiler), '-std=c11', '-O2', str(source), '-o', str(executable)]
        run_command(compile_command, cwd=work)
        inputs = cases(args.cases)
        stdin = ''.join(' '.join(f'{n:016x}' for n in registers)+'\n' for registers in inputs)
        rows = run_command([str(executable)], input=stdin, cwd=work).stdout.splitlines()
        if len(rows) != len(inputs):
            raise ValueError(f'wrong output case count: {len(rows)}')
        for i, (row, registers) in enumerate(zip(rows, inputs)):
            tokens = row.split()
            if len(tokens) != 32 or any(not re.fullmatch(r'[0-9a-fA-F]{16}', t) for t in tokens):
                raise ValueError(f'malformed output at case {i}')
            actual, expected = [int(t, 16) for t in tokens], interpret(words, registers)
            for reg, (a, e) in enumerate(zip(actual, expected)):
                if a != e:
                    raise ValueError(f'mismatch case={i} gpr={reg} expected={e:016x} actual={a:016x}')
        for name, digest in module_hashes.items():
            if hashlib.sha256((tool_dir/name).read_bytes()).hexdigest() != digest:
                raise ValueError('upstream module changed during probe')
        result = dict(schema_version=2, success=True, scope='whitelisted GPR leaf differential only; no hardware or gameplay validation',
                      elf=str(elf), elf_sha256=hashlib.sha256(data).hexdigest(), code_sha256=hashlib.sha256(code).hexdigest(),
                      start=args.start, end=args.end, words=[f'{w:08x}' for w in words], cases=len(inputs), seed=SEED,
                      compiler=str(compiler), compiler_version=version, upstream=str(upstream), upstream_head=head,
                      machine=platform.machine(), source=str(source), executable=str(executable))
        result.update(host_platform=platform.platform(), compared_gprs=32,
                      compile_command=compile_command, invocation=sys.argv,
                      upstream_tracked_clean=tracked_clean, upstream_module_sha256=module_hashes,
                      probe_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                      source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                      executable_sha256=hashlib.sha256(executable.read_bytes()).hexdigest())
        with report.open('x', encoding='utf-8') as f:
            json.dump(result, f, indent=2)
            f.write('\n')
        print(json.dumps(dict(success=True, cases=len(inputs), report=str(report), scope=result['scope'])))
        return 0
    except Exception as exc:
        print(json.dumps(dict(success=False, error=f'{type(exc).__name__}: {exc}')))
        return 1


if __name__ == '__main__':
    sys.exit(main())
