"""Private, hash-pinned read-only timing diagnostics. Never patches the ELF.

Copies the accepted generated overlay to a fresh directory and inserts calls
that observe registers only. No guest timers, waits, memory or registers change.
Generated output remains private game-derived code. Not an FPS patch.
"""
import argparse
import hashlib
import json
from pathlib import Path

SOURCE_SHA = '7984d7291c63e39d36c8f1d59062ec1785d075f522278432daf7b1530c0bd5d0'
NAME = 'ppu_recomp_007.cpp'
SITES = ('00180F60', '00181340', '00181018', '0018211C')
RETURNS = ('00180F64', '00181344', '00180F84', '00180F98',
           '001820A0', '001820B4', '00181424', '001814C8', '0018177C', '001819B8')
HEADER = r'''
#include <chrono>
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
static void folklore_timing_probe(const ppu_context* c, unsigned site) {
    static const bool active = [] { const char* v = std::getenv("FOLKLORE_TIMING_PROBE"); return v && v[0]=='1' && v[1]==0; }();
    if (!active) return;
    static std::atomic<unsigned> count{0};
    unsigned n = count.load(std::memory_order_relaxed);
    do { if (n >= 40000) return; }
    while (!count.compare_exchange_weak(n, n+1, std::memory_order_relaxed));
    auto ns = std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count();
    unsigned long long f29, f31;
    static_assert(sizeof(f29) == sizeof(c->fpr[29]));
    std::memcpy(&f29, &c->fpr[29], sizeof(f29));
    std::memcpy(&f31, &c->fpr[31], sizeof(f31));
    std::fprintf(stderr, "[timing-probe] n=%u ns=%lld site=%08X ctx=%p lr=%08llX r3=%lld r19=%lld r25=%lld r27=%lld r28=%lld r29=%lld f29bits=%016llX f31bits=%016llX\n",
        n, (long long)ns, site, (const void*)c, (unsigned long long)c->lr,
        (long long)c->gpr[3], (long long)c->gpr[19], (long long)c->gpr[25],
        (long long)c->gpr[27], (long long)c->gpr[28], (long long)c->gpr[29],
        f29, f31);
}
'''

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def prepare(source, output):
    source, output = Path(source).resolve(), Path(output).absolute()
    for parent in (output, *output.parents):
        if parent.is_symlink() or (parent.exists() and getattr(parent.lstat(), 'st_file_attributes', 0) & 0x400):
            raise ValueError('Output must not contain links or junctions')
    if output.exists() or source == output or source in output.parents or output in source.parents:
        raise ValueError('Output must be fresh and non-overlapping')
    files = list(source.iterdir())
    if any(not p.is_file() or p.is_symlink() or getattr(p.lstat(), 'st_file_attributes', 0) & 0x400 for p in files):
        raise ValueError('Source must contain regular flat files only')
    if (source / 'timing-probe-receipt.json').exists():
        raise ValueError('Source contains reserved receipt name')
    snapshots = {p.name: p.read_bytes() for p in files}
    data = snapshots[NAME]
    if hashlib.sha256(data).hexdigest() != SOURCE_SHA:
        raise ValueError('Unsupported generated source hash')
    newline = b'\r\n' if b'\r\n' in data else b'\n'
    for site in SITES:
        needle = f'loc_{site}:'.encode() + newline
        if data.count(needle) != 1:
            raise ValueError('Missing/duplicate timing label ' + site)
        data = data.replace(needle, needle + f'        folklore_timing_probe(ctx, 0x{site});'.encode() + newline)
    for site in RETURNS:
        needle = f'ctx->lr = 0x{site}; func_0021CCB4(ctx); DRAIN_TRAMPOLINE(ctx);'.encode()
        if data.count(needle) != 1:
            raise ValueError('Missing/duplicate elapsed return ' + site)
        data = data.replace(needle, needle + f' folklore_timing_probe(ctx, 0x{site});'.encode())
    # ppu_recomp.h supplies ppu_context before this diagnostic definition.
    needle = b'#include "ppu_recomp.h"' + newline
    if data.count(needle) != 1:
        raise ValueError('Missing generated header include')
    data = data.replace(needle, needle + HEADER.replace('\n', newline.decode()).encode())
    output.mkdir(parents=True)
    outputs = dict(snapshots)
    outputs[NAME] = data
    for name, content in outputs.items():
        (output / name).write_bytes(content)
    receipt = dict(kind='read_only_register_probe', guest_timebase_unchanged=True,
        source=str(source), output=str(output), sites=list(SITES), returns=list(RETURNS),
        files=[dict(name=name, input_sha256=hashlib.sha256(snapshots[name]).hexdigest(),
                    output_sha256=hashlib.sha256(content).hexdigest()) for name, content in outputs.items()])
    (output / 'timing-probe-receipt.json').write_text(json.dumps(receipt, indent=2))
    return receipt

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    receipt = prepare(a.source, a.output)
    print(json.dumps(dict(output=receipt['output'], sites=len(SITES)+len(RETURNS))))
