"""Prepare a guarded I-R3 PPU source copy; never edits the source/build tree."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ORIGINAL_SHA256 = "2714de0c15ae0c26df10969bb458c233117ce86e8d67c4689ecabc2345e58fbe"
SOURCE_NAME = "ppu_recomp_012.cpp"
HEADER_NAME = "folklore_audio_producer_trace.h"
MARKER = "FOLKLORE_AUDIO_PRODUCER_TRACE_I_R3"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise ValueError(f"{label}: expected exactly one anchor, found {count}")
    return source.replace(old, new, 1)


def phase_hook(phase: int, edge: int, target: str = "0u") -> str:
    return ('#if defined(FOLKLORE_AUDIO_PRODUCER_TRACE) && FOLKLORE_AUDIO_PRODUCER_TRACE\n'
            '        if (ctx->thread_id == 9)\n'
            f'            folklore_audio_producer_trace::phase_event({phase}u, {edge}u, {target},\n'
            '                [](uint32_t ea) { return vm_read32(ea); });\n'
            '#endif\n')


def prepare_phases(s: str) -> str:
    start = s.index('void func_002A4D24(ppu_context* ctx) {')
    end = s.index('\nvoid ', start + 1)
    body = s[start:end]
    for phase, anchor in ((1, '        ctx->lr = 0x002A4E34; func_00280AD0(ctx); DRAIN_TRAMPOLINE(ctx);\n'),
                          (2, '        ctx->lr = 0x002A4F98; func_002A4BAC(ctx); DRAIN_TRAMPOLINE(ctx);\n')):
        body = replace_once(body, anchor, phase_hook(phase, 1) + anchor +
                            phase_hook(phase, 2), f'phase {phase}')
    anchor = '        ps3_indirect_call(ctx); DRAIN_TRAMPOLINE(ctx);\n'
    pieces = body.split(anchor)
    if len(pieces) != 4:
        raise ValueError('producer callbacks: expected exactly three anchors')
    body = pieces[0]
    for phase, piece in zip((3, 4, 5), pieces[1:]):
        body += phase_hook(phase, 1, '(uint32_t)ctx->ctr') + anchor + \
                phase_hook(phase, 2) + piece
    body = replace_once(body, 'loc_002A5094:\n', 'loc_002A5094:\n' + phase_hook(6, 1),
                        'bookkeeping boundary')
    return s[:start] + body + s[end:]


def nested_hook(phase: int, edge: int, callback: str = "0u") -> str:
    result = '(int32_t)ctx->gpr[3]' if edge == 2 else '0'
    return ('#if defined(FOLKLORE_AUDIO_PRODUCER_TRACE) && FOLKLORE_AUDIO_PRODUCER_TRACE\n'
            '        if (ctx->thread_id == 9)\n'
            f'            folklore_audio_producer_trace::nested_phase_event({phase}u, {edge}u,\n'
            f'                (uint32_t)ctx->gpr[3], {result}, {callback});\n'
            '#endif\n')


def prepare_nested(s: str) -> str:
    def patch_function(source, name, edits):
        start = source.index(f'void func_{name}(ppu_context* ctx) {{')
        end = source.index('\nvoid ', start + 1)
        body = source[start:end]
        for phase, anchor, callback in edits:
            body = replace_once(body, anchor, nested_hook(phase, 1, callback) + anchor +
                                nested_hook(phase, 2), f'nested phase {phase}')
        return source[:start] + body + source[end:]
    s = patch_function(s, '002A4BAC', [
        (7, '        ctx->lr = 0x002A4BE4; func_002A8264(ctx); DRAIN_TRAMPOLINE(ctx);\n', '0u'),
        (8, '        ctx->lr = 0x002A4C00; func_002A4668(ctx); DRAIN_TRAMPOLINE(ctx);\n', '0u'),
        (9, '        ctx->lr = 0x002A4C10; func_002A9990(ctx); DRAIN_TRAMPOLINE(ctx);\n', '0u')])
    # The receive anchor is unique only in this exact function. Do not touch
    # the outer audio notification queue or any other guest thread's wait.
    return patch_function(s, '002A9990', [
        (10, '        ctx->lr = 0x002A9AA0; func_004EE984(ctx); DRAIN_TRAMPOLINE(ctx);\n', '0u'),
        (11, '        lv2_syscall(ctx);\n', '0u'),
        (12, '        ps3_indirect_call(ctx); DRAIN_TRAMPOLINE(ctx);\n', '(uint32_t)ctx->ctr')])


def transform(data: bytes) -> bytes:
    if sha(data) != ORIGINAL_SHA256:
        raise ValueError("I-R3 source SHA-256 mismatch; inspect drift before preparing")
    # The reviewed generated TU contains legacy comment bytes (including 0x97).
    # A byte-preserving single-byte decode keeps every untouched source byte.
    s = data.decode("latin-1")
    if b"\r\n" not in data or "\n" in s.replace("\r\n", ""):
        raise ValueError("unexpected I-R3 line endings")
    s = s.replace("\r\n", "\n")
    if MARKER in s:
        raise ValueError("source already contains producer trace marker")
    s = replace_once(
        s,
        '#include "ppu_recomp.h"\n#include <stdio.h>',
        '#include "ppu_recomp.h"\n'
        f'/* {MARKER}: opt-in, prepared source copy only. */\n'
        '#if defined(FOLKLORE_AUDIO_PRODUCER_TRACE) && FOLKLORE_AUDIO_PRODUCER_TRACE\n'
        f'#include "{HEADER_NAME}"\n'
        '#endif\n#include <stdio.h>',
        "header",
    )
    s = replace_once(
        s,
        '        ctx->gpr[11] = (int64_t)(int32_t)(0x82);\n'
        '        lv2_syscall(ctx);\n'
        '        ctx->gpr[3] = (int64_t)(int32_t)ctx->gpr[3];',
        '        ctx->gpr[11] = (int64_t)(int32_t)(0x82);\n'
        '#if defined(FOLKLORE_AUDIO_PRODUCER_TRACE) && FOLKLORE_AUDIO_PRODUCER_TRACE\n'
        '        if (ctx->thread_id == 9 && folklore_audio_producer_trace::active())\n'
        '            folklore_audio_producer_trace::emit(folklore_audio_producer_trace::Q_ENTER,\n'
        '                vm_read32(ctx->gpr[27] + 0x4), (uint32_t)ctx->gpr[31],\n'
        '                (uint32_t)ctx->gpr[3], 0);\n'
        '#endif\n'
        '        lv2_syscall(ctx);\n'
        '#if defined(FOLKLORE_AUDIO_PRODUCER_TRACE) && FOLKLORE_AUDIO_PRODUCER_TRACE\n'
        '        if (ctx->thread_id == 9 && folklore_audio_producer_trace::active())\n'
        '            folklore_audio_producer_trace::emit(folklore_audio_producer_trace::Q_RETURN,\n'
        '                vm_read32(ctx->gpr[27] + 0x4), (uint32_t)ctx->gpr[31],\n'
        '                3u, (int32_t)ctx->gpr[3]);\n'
        '#endif\n'
        '        ctx->gpr[3] = (int64_t)(int32_t)ctx->gpr[3];',
        "queue receive",
    )
    s = replace_once(
        s,
        '        ctx->lr = 0x002A86F8; func_002A4D24(ctx); DRAIN_TRAMPOLINE(ctx);\n'
        '        /* nop */;',
        '#if defined(FOLKLORE_AUDIO_PRODUCER_TRACE) && FOLKLORE_AUDIO_PRODUCER_TRACE\n'
        '        if (ctx->thread_id == 9 && folklore_audio_producer_trace::active())\n'
        '            folklore_audio_producer_trace::emit(folklore_audio_producer_trace::P_ENTER,\n'
        '                vm_read32(ctx->gpr[27] + 0x4), (uint32_t)ctx->gpr[31],\n'
        '                (uint32_t)ctx->gpr[28], 0, [](uint32_t ea) { return vm_read32(ea); });\n'
        '#endif\n'
        '        ctx->lr = 0x002A86F8; func_002A4D24(ctx); DRAIN_TRAMPOLINE(ctx);\n'
        '#if defined(FOLKLORE_AUDIO_PRODUCER_TRACE) && FOLKLORE_AUDIO_PRODUCER_TRACE\n'
        '        if (ctx->thread_id == 9 && folklore_audio_producer_trace::active())\n'
        '            folklore_audio_producer_trace::emit(folklore_audio_producer_trace::P_RETURN,\n'
        '                vm_read32(ctx->gpr[27] + 0x4), (uint32_t)ctx->gpr[31],\n'
        '                (uint32_t)ctx->gpr[28], 0, [](uint32_t ea) { return vm_read32(ea); });\n'
        '#endif\n'
        '        /* nop */;',
        "producer call",
    )
    s = prepare_phases(s)
    s = prepare_nested(s)
    return s.replace("\n", "\r\n").encode("latin-1")


def prepare(source: Path, output_dir: Path, write: bool) -> dict:
    source = source.resolve(strict=True)
    output_dir = output_dir.resolve()
    if source.name != SOURCE_NAME:
        raise ValueError("source must be ppu_recomp_012.cpp")
    if source.parent == output_dir:
        raise ValueError("output directory cannot be source directory")
    data = source.read_bytes()
    patched = transform(data)
    header_path = Path(__file__).with_name(HEADER_NAME)
    header = header_path.read_bytes()
    receipt = {
        "original_source": str(source),
        "original_sha256": sha(data),
        "prepared_sha256": sha(patched),
        "header_sha256": sha(header),
        "trace_define": "FOLKLORE_AUDIO_PRODUCER_TRACE=1",
        "output_dir": str(output_dir),
    }
    if write:
        output_dir.mkdir(parents=True, exist_ok=True)
        # Validate every existing target before writing any of them.
        for name, expected in ((SOURCE_NAME, patched), (HEADER_NAME, header)):
            target = output_dir / name
            if target.exists() and target.read_bytes() != expected:
                raise ValueError(f"refusing to overwrite differing artifact: {target}")
        receipt_path = output_dir / "preparation-receipt.json"
        payload = (json.dumps(receipt, indent=2) + "\n").encode("utf-8")
        if receipt_path.exists() and receipt_path.read_bytes() != payload:
            raise ValueError(f"refusing to overwrite differing receipt: {receipt_path}")
        for name, expected in ((SOURCE_NAME, patched), (HEADER_NAME, header)):
            target = output_dir / name
            if not target.exists():
                target.write_bytes(expected)
        if not receipt_path.exists():
            receipt_path.write_bytes(payload)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--prepare", action="store_true", help="write new prepared artifacts")
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.output_dir, args.prepare), indent=2))


if __name__ == "__main__":
    main()
