#!/usr/bin/env python3
"""Prepare hash-guarded copies of reviewed PPU translation units with an
opt-in, read-only draw producer identity trace hook.

Original sources are never modified. Untouched bytes (including CRLF line
endings and legacy comments) are preserved exactly; all edits are bytes
insertions performed after full validation.
"""

import argparse
import hashlib
import json
import os
import sys
import tempfile

CRLF = b"\r\n"
GUARD = b"#if defined(FOLKLORE_DRAW_PRODUCER_TRACE) && FOLKLORE_DRAW_PRODUCER_TRACE"
ENDIF = b"#endif"
MARKER = b"FOLKLORE_DRAW_PRODUCER_TRACE_V1"
TRACE_HEADER = b"draw_producer_trace.h"
INCLUDE_LINE = b'#include "ppu_recomp.h"'

SOURCES = {
    "ppu_recomp_008.cpp": {
        "sha256": "87129ccb51868bf58eed288b061fdcb29687977e0a93e03619f0e593dc63d481",
        "function": "func_001DA62C",
        "address": "0x001DA62Cu",
    },
    "ppu_recomp_011.cpp": {
        "sha256": "ca9d30b960ebf93fae4c47942802b1e8209337272762440b112abb40537a9611",
        "function": "func_0027A5A4",
        "address": "0x0027A5A4u",
    },
}


class TransformError(ValueError):
    pass


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _include_block():
    return (GUARD + CRLF
            + b'#include "' + TRACE_HEADER + b'"' + CRLF
            + b"// " + MARKER + CRLF
            + ENDIF + CRLF)


def _hook_block(address):
    call = b"    folklore_draw_producer_trace::entry(ctx, " + address.encode("ascii") + b");"
    return GUARD + CRLF + call + CRLF + ENDIF + CRLF


def _controller_blocks(source_name):
    # Controller follow-up after the v1 artifact was integrated and verified.
    # Scope only associates calls within one invocation, never across frames.
    if source_name != "ppu_recomp_008.cpp":
        return []
    return [
        (b"void func_001C0884(ppu_context* ctx) {",
         GUARD + CRLF + b"    folklore_draw_producer_trace::Scope trace_scope(ctx, 0x001C0884u);" + CRLF + ENDIF + CRLF),
        (b"void func_001D8504(ppu_context* ctx) {", _hook_block("0x001D8504u")),
    ]


def _unique_line_end(data, anchor, label):
    """Return the offset just past anchor+CRLF; anchor must occur exactly once
    at the start of a line and be followed by CRLF."""
    count = data.count(anchor)
    if count != 1:
        raise TransformError("%s anchor must occur exactly once, found %d" % (label, count))
    pos = data.find(anchor)
    if pos > 0 and data[pos - 1:pos] != b"\n":
        raise TransformError("%s anchor is not at the start of a line" % label)
    end = pos + len(anchor)
    if data[end:end + 2] != CRLF:
        raise TransformError("%s anchor is not followed by CRLF" % label)
    return end + 2


def transform(source_name, data):
    """Validate a reviewed source and return the derived traced bytes."""
    if not isinstance(data, (bytes, bytearray)):
        raise TransformError("source data must be bytes")
    data = bytes(data)
    spec = SOURCES.get(source_name)
    if spec is None:
        raise TransformError("unsupported source name: %r" % (source_name,))
    actual = _sha256(data)
    if actual != spec["sha256"]:
        raise TransformError("source hash drift for %s: %s" % (source_name, actual))
    if MARKER in data or TRACE_HEADER in data:
        raise TransformError("trace marker or header already present")

    opener = b"void " + spec["function"].encode("ascii") + b"(ppu_context* ctx) {"
    inc_at = _unique_line_end(data, INCLUDE_LINE, "include")
    fn_at = _unique_line_end(data, opener, spec["function"])
    if fn_at <= inc_at:
        raise TransformError("function opener precedes ppu_recomp.h include")

    inc_block = _include_block()
    hook_block = _hook_block(spec["address"])
    out = (data[:inc_at] + inc_block + data[inc_at:fn_at]
           + hook_block + data[fn_at:])

    extras = _controller_blocks(source_name)
    for anchor, block in extras:
        _unique_line_end(data, anchor, "controller observation")
        out = out.replace(anchor + CRLF, anchor + CRLF + block, 1)

    # Self-check: exact hook count; removing only inserted blocks restores input.
    call = b"folklore_draw_producer_trace::entry("
    if out.count(MARKER) != 1 or out.count(call) != (2 if extras else 1):
        raise TransformError("derived output does not contain the exact hook count")
    restored = out
    for _, block in extras:
        restored = restored.replace(block, b"", 1)
    restored = restored.replace(inc_block, b"", 1).replace(hook_block, b"", 1)
    if restored != data:
        raise TransformError("derived output does not preserve source bytes")
    return out


def _real(path):
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def prepare(source, output):
    name = os.path.basename(source)
    if name not in SOURCES:
        raise TransformError("source basename must be one of %s" % sorted(SOURCES))
    if os.path.islink(source):
        raise TransformError("source must not be a symlink")
    if not os.path.isfile(source):
        raise TransformError("source is not a regular file")
    if os.path.islink(output):
        raise TransformError("output must not be a symlink")
    if os.path.isdir(output):
        raise TransformError("output must not be a directory")
    if _real(source) == _real(output):
        raise TransformError("source and output resolve to the same path")
    if os.path.exists(output) and os.path.samefile(source, output):
        raise TransformError("source and output are the same file")
    parent = os.path.dirname(os.path.abspath(output))
    if not os.path.isdir(parent):
        raise TransformError("output parent directory does not exist")

    with open(source, "rb") as fh:
        data = fh.read()
    out = transform(name, data)

    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(output) + ".",
                               suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(out)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, output)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    spec = SOURCES[name]
    return {
        "source": name,
        "function": spec["function"],
        "address": spec["address"],
        "source_sha256": _sha256(data),
        "output_sha256": _sha256(out),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        receipt = prepare(args.source, args.output)
    except (TransformError, OSError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    print(json.dumps(receipt, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
