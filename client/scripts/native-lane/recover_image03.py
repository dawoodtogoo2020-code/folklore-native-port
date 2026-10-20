"""Offline, hash-bound structural recovery of the assigned captured SPU image.

No interpreter, guest execution, compilation, registration or subprocesses.
Decoded listings and generated code stay in the explicitly private output root.
The console summary contains structural counts and offsets, never opcode words.
"""

import argparse
from collections import Counter, deque
import hashlib
import json
from pathlib import Path
import re
import struct
import sys

sys.dont_write_bytecode = True
IMAGE_SHA = "3eee051c8a43fb010503792f592fc820d9caaaa7d57ce0de8662d2a75a6b42fd"
DESC_SHA = "e85625f42f2f9a1d8bbb83aaf62b4fd4adb92e1d05098c919fc2b1ab05672700"
TOOL_SHA = {
    "spu_disasm.py": "7e78a2b90167807b03b817575df2b9c335407a3482a54d8ce9e1d452f118328a",
    "spu_lifter.py": "15b378c72bacfc1c8e4101385b29f3969bf38c3f6ba9bb17d51674cc5261a862",
    "find_spu_functions.py": "15d29ad47fecbadf9659cb27937c4cf0db636db15b57a09bf986d28829d337e4",
}
DIRECT = {"br", "bra", "brsl", "brasl", "brz", "brnz", "brhz", "brhnz"}
UNCONDITIONAL = {"br", "bra"}
INDIRECT = {"bi", "bisl", "bisled", "biz", "binz", "bihz", "bihnz", "iret"}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as output:
        output.write(json.dumps(value, indent=2) + "\n")


def ranges(offsets):
    result = []
    for pc in sorted(offsets):
        if not result or pc != result[-1][1]:
            result.append([pc, pc + 4])
        else:
            result[-1][1] = pc + 4
    return result


def resolve_image03_tables(image, decode):
    """Recognize three bounded relative tables; no instruction interpretation.

    These are fixed source-identity pattern checks plus static target arithmetic,
    not computed branch outcomes. Both selector alternatives remain in the CFG.
    """
    def at(pc):
        return decode(struct.unpack_from(">I", image, pc)[0], pc)

    def require(pc, mnemonic, registers, immediate=None):
        ins = at(pc)
        regs = [int(value) for value in re.findall(r"\$r(\d+)", ins.operands)]
        if ins.mnemonic != mnemonic or regs != registers:
            raise ValueError(f"table provenance mismatch at offset {pc:X}")
        if immediate is not None:
            text = re.sub(r"\$r\d+", "", ins.operands).replace(",", " ").strip()
            if int(text, 0) != immediate:
                raise ValueError(f"table immediate mismatch at offset {pc:X}")

    for pc, mnemonic, regs, immediate in [
        (0x20, "ila", [2], 0x28), (0x24, "brsl", [79], 0x28),
        (0x38, "sf", [79, 2, 79], None), (0x50, "ai", [2, 6], -2),
        (0x58, "ila", [3], 0xA0), (0x60, "shli", [8, 2], 2),
        (0x64, "a", [3, 3, 79], None), (0x68, "clgti", [2, 2], 20),
        (0x6C, "lqx", [6, 3, 8], None), (0x7C, "rotqby", [4, 6, 8], None),
        (0x80, "a", [4, 4, 3], None), (0x84, "brz", [2], 0x90),
        (0x90, "bi", [4], None),
        (0x6D70, "ila", [2], 0x6D78), (0x6D74, "brsl", [126], 0x6D78),
        (0x6D94, "sf", [126, 2, 126], None),
        (0x6F48, "ila", [3], 0x6FC0), (0x6F94, "shli", [4, 2], 2),
        (0x6F98, "clgti", [2, 2], 14), (0x6F9C, "lqx", [5, 3, 4], None),
        (0x6FA4, "rotqby", [4, 5, 4], None), (0x6FA8, "a", [4, 4, 3], None),
        (0x6FAC, "brnz", [2], 0x70CC), (0x6FB4, "bi", [4], None),
        (0x46F0, "ila", [2], 0x46F8), (0x46F4, "brsl", [126], 0x46F8),
        (0x4714, "sf", [126, 2, 126], None),
        (0x47B0, "shli", [4, 82], 2), (0x47B4, "ila", [2], 0x47E0),
        (0x47B8, "clgti", [3, 82], 11), (0x47BC, "a", [2, 2, 126], None),
        (0x47C0, "lqx", [5, 2, 4], None), (0x47C4, "rotqby", [4, 5, 4], None),
        (0x47C8, "a", [4, 4, 2], None), (0x47CC, "brnz", [3], 0x485C),
        (0x47D0, "bi", [4], None),
    ]:
        require(pc, mnemonic, regs, immediate)
    tables = []
    for start, count, site in [(0xA0, 21, 0x90), (0x47E0, 12, 0x47D0), (0x6FC0, 15, 0x6FB4)]:
        end = start + 4 * count
        targets = [start + struct.unpack_from(">i", image, start + 4 * index)[0]
                   for index in range(count)]
        if any(target & 3 or not 0 <= target < len(image) for target in targets):
            raise ValueError("table target outside aligned image")
        if any(lo <= target < hi for target in targets
               for lo, hi in [(0xA0, 0xF4), (0x47E0, 0x4810), (0x6FC0, 0x6FFC)]):
            raise ValueError("table target points into a qualified table")
        tables.append({"start": start, "end": end, "count": count,
                       "dispatch_site": site, "sha256": digest(image[start:end]),
                       "targets": targets,
                       "assumption": "Pinned decoder semantics; call-next relocation delta and unsigned selector bounds."})
    return tables


def walk(image, decode, target_of, seeds):
    pending, seen, outside = deque(seeds), {}, set()
    edges, calls, indirect, unknown, terminals, refs, channels = [], [], [], [], [], [], []
    while pending:
        pc = pending.popleft()
        if pc in seen:
            continue
        if pc < 0 or pc & 3 or pc + 4 > len(image):
            outside.add(pc)
            continue
        ins = decode(struct.unpack_from(">I", image, pc)[0], pc)
        seen[pc] = ins
        mnemonic = ins.mnemonic
        if mnemonic in (".word", "???"):
            unknown.append(pc)
            continue
        if mnemonic in DIRECT:
            target = target_of(ins)
            if target is None:
                unknown.append(pc)
                continue
            edges.append({"site": pc, "target": target, "kind": mnemonic})
            pending.append(target)
            if mnemonic in ("brsl", "brasl"):
                calls.append({"site": pc, "target": target,
                              "link_register": ins.raw & 127, "call_next": target == pc + 4})
            if mnemonic not in UNCONDITIONAL:
                pending.append(pc + 4)
        elif mnemonic in INDIRECT:
            indirect.append({"site": pc, "kind": mnemonic,
                             "registers": [int(x) for x in re.findall(r"\$r(\d+)", ins.operands)]})
            if mnemonic not in ("bi", "iret"):
                pending.append(pc + 4)
        elif mnemonic in ("stop", "stopd"):
            terminals.append({"site": pc, "kind": mnemonic})
        else:
            pending.append(pc + 4)
        if mnemonic in ("lqa", "stqa", "lqr", "stqr"):
            numbers = re.findall(r"0x([0-9A-Fa-f]+)", ins.operands)
            if numbers:
                refs.append({"site": pc, "target": int(numbers[-1], 16), "kind": mnemonic})
        if mnemonic in ("rdch", "wrch", "rchcnt"):
            channel = (ins.raw >> 7) & 127
            channels.append({"site": pc, "kind": mnemonic, "channel": channel,
                             "register": ins.raw & 127})
    return seen, {"seeds": list(seeds), "instruction_count": len(seen),
                  "ranges_half_open": ranges(seen),
                  "mnemonics": dict(sorted(Counter(i.mnemonic for i in seen.values()).items())),
                  "direct_edges": edges, "calls": calls, "indirect": indirect,
                  "unknown_decode_sites": unknown, "outside_image_targets": sorted(outside),
                  "terminals": terminals, "static_ls_refs": refs, "channels": channels}


def block_partition(seen, cfg):
    """Split reached code at control-flow entries, never through known data gaps."""
    leaders = {first for first, _ in ranges(seen)}
    leaders.update(pc for pc in cfg["seeds"] if pc in seen)
    for edge in cfg["direct_edges"]:
        if edge["target"] in seen:
            leaders.add(edge["target"])
        if edge["kind"] not in UNCONDITIONAL and edge["site"] + 4 in seen:
            leaders.add(edge["site"] + 4)
    for item in cfg["indirect"]:
        if item["site"] + 4 in seen:
            leaders.add(item["site"] + 4)
    blocks = []
    for pc in sorted(seen):
        if not blocks or pc in leaders or pc != blocks[-1][1]:
            blocks.append([pc, pc + 4])
        else:
            blocks[-1][1] = pc + 4
    return blocks


def generate_blocks(image, seen, cfg, output, base):
    """Use the unchanged strict lifter; these are chunks, not original symbols.

    All direct/table entries get native symbols so internal indirect destinations
    can be resolved. No generated instruction is edited and no runtime is called.
    """
    from spu_disasm import spu_decode
    from spu_lifter import SPULifter, compute_bi_r0_jumps, compute_link_returns
    if base < 0 or base & 1023 or base + len(image) > 0x40000:
        raise ValueError("generation base must be 1024-aligned and fit the whole image in LS")
    blocks = block_partition(seen, cfg)
    bounds = [(base + first, base + end) for first, end in blocks]
    instructions = [spu_decode(struct.unpack_from(">I", image, pc)[0], base + pc) for pc in sorted(seen)]
    lifter = SPULifter(prefix=f"image03_b{base:x}_", strict_unsupported=True)
    lifter.func_starts = {first for first, _ in bounds}
    lifter.bi_r0_jump = compute_bi_r0_jumps(instructions, bounds)
    lifter.link_return = compute_link_returns(instructions, bounds)
    body_failures = []
    for first, end in bounds:
        function = lifter.lift_function(instructions, first, end)
        for line in function.body_lines:
            if "spu_lift_strict_failure" in line:
                body_failures.append({"block_start": first, "reason": re.findall(r'"([^"\n]+)"', line)})
    missing = sorted((lifter.call_targets | lifter.branch_targets) - lifter.func_starts)
    header_name = "image03_generated.h"
    source_name = "image03_generated.c"
    generated = {header_name: lifter.emit_header(), source_name: lifter.emit_source(header_name)}
    output.mkdir(exist_ok=False)
    hashes = {}
    for name, content in generated.items():
        data = content.encode("utf-8")
        with (output / name).open("xb") as target:
            target.write(data)
        hashes[name] = {"sha256": digest(data), "bytes": len(data)}
    result = {"scope": "Complete reached-instruction chunk generation, not original function symbols or runtime qualification.",
              "base": base, "base_is_proposed_native_placement": True,
              "entry_relative": 0, "entry_absolute": base,
              "instruction_count": len(instructions), "chunk_count": len(bounds),
              "chunks_relative": blocks, "generated": hashes,
              "missing_direct_targets": missing, "strict_chunk_failure_sites": body_failures,
              "unsupported_mnemonics": lifter.unsupported,
              "computed_r0_sites": sorted(lifter.bi_r0_jump),
              "non_r0_return_sites": sorted(lifter.link_return),
              "dynamic_dispatch_sites_relative": cfg["indirect"],
              "limits": ["No compilation, registration, execution or symbol-boundary claim.",
                         "Cross-chunk continuation, nested calls, stack/ABI and dynamic dispatch need production-path CPU tests.",
                         "Native function lookup completeness and real input/DMA effects remain unqualified."]}
    write_json(output / "generation.private.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bindings", type=Path, required=True)
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", type=lambda value: int(value, 0), action="append", default=[])
    parser.add_argument("--resolve-tables", action="store_true",
                        help="require image03 source patterns and add all 48 bounded relative-table targets")
    parser.add_argument("--generate-base", type=lambda value: int(value, 0),
                        help="optional proposed fixed native LS base for strict offline chunk generation")
    args = parser.parse_args(argv)
    binding = json.loads(args.bindings.read_text(encoding="utf-8"))
    if binding.get("task_id") != "native-image-recovery-002":
        parser.error("unexpected binding task")
    inputs = {}
    for item in binding["inputs"]:
        path = Path(item["path"])
        data = path.read_bytes()
        if len(data) != item["bytes"] or digest(data) != item["sha256"]:
            parser.error("bound input drift: " + path.name)
        inputs[path.name] = data
    image = next(value for name, value in inputs.items() if name.endswith(".image.bin"))
    desc = next(value for name, value in inputs.items() if name.endswith(".desc.bin"))
    if len(image) != 36448 or digest(image) != IMAGE_SHA or len(desc) != 128 or digest(desc) != DESC_SHA:
        parser.error("capture identity mismatch")
    tool_bindings = []
    for name, expected in TOOL_SHA.items():
        path = args.sdk / "tools" / name
        actual = digest(path.read_bytes())
        if actual != expected:
            parser.error("pinned tool drift: " + name)
        tool_bindings.append({"path": str(path), "sha256": actual})
    sys.path.insert(0, str(args.sdk / "tools"))
    from spu_disasm import spu_decode
    from spu_lifter import SPULifter, LiftedFunction
    tables = resolve_image03_tables(image, spu_decode) if args.resolve_tables else []
    seeds = list(dict.fromkeys([0] + args.seed + [target for table in tables for target in table["targets"]]))
    seen, cfg = walk(image, spu_decode, SPULifter._branch_target, seeds)
    args.out.mkdir(parents=True, exist_ok=False)
    with (args.out / "reachable.private.disasm.txt").open("x", encoding="utf-8") as output:
        output.write("\n".join(str(seen[pc]) for pc in sorted(seen)) + "\n")
    lifter = SPULifter(strict_unsupported=True)
    # This whole-image container tests instruction translation only. It does not
    # assert real function boundaries, dynamic target closure or a valid ABI.
    dummy = LiftedFunction(name="inventory_only", start_addr=0, end_addr=len(image))
    unsupported, errors = [], []
    for pc, ins in sorted(seen.items()):
        try:
            translated = lifter._translate(ins, dummy)
            if "spu_lift_strict_failure" in translated:
                reasons = re.findall(r'"([^"\n]+)"', translated)
                unsupported.append({"site": pc, "mnemonic": ins.mnemonic, "reasons": reasons})
        except Exception as error:
            errors.append({"site": pc, "mnemonic": ins.mnemonic, "error_type": type(error).__name__})
    fingerprint = 1469598103934665603
    for byte in image:
        fingerprint = ((fingerprint ^ byte) * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    descriptor = {"binary_ea": f"0x{int.from_bytes(desc[:8], 'big') & ~15:08X}",
                  "binary_size": int.from_bytes(desc[8:10], "big") << 4,
                  "requested_stack_size": int.from_bytes(desc[28:30], "big") << 4,
                  "scratch_size": int.from_bytes(desc[30:32], "big") << 4,
                  "job_type": desc[44]}
    summary = {"schema": "folklore-image03-offline-structure-v1",
               "image_sha256": IMAGE_SHA, "image_size": len(image), "descriptor_sha256": DESC_SHA,
               "runtime_fingerprint_recomputed": f"0x{fingerprint:016X}",
               "elf_magic_at_zero": image[:4] == b"\x7fELF",
               "embedded_elf_magic_count": image.count(b"\x7fELF"), "descriptor": descriptor,
               "trailing_zero_bytes": len(image) - len(image.rstrip(b"\0")),
               "tool_bindings": tool_bindings, "cfg": cfg, "qualified_relative_tables": tables,
               "strict_translation_failures": unsupported, "translation_exceptions": errors,
               "entry_first_16_roles": [{"site": pc, "mnemonic": seen[pc].mnemonic,
                                          "registers": [int(x) for x in re.findall(r"\$r(\d+)", seen[pc].operands)]}
                                         for pc in sorted(seen) if pc < 64],
               "limits": ["Static direct-flow closure seeded at image offset zero; entry provenance is a separate obligation.",
                          "Both direct conditional outcomes and call continuations included; unresolved indirect targets stop closure.",
                          "Unreached bytes are unclassified, not automatically data.",
                          "Per-instruction translation coverage is not full native function generation or semantic equivalence.",
                          "No compiler, interpreter, guest execution, admission or runtime evidence."]}
    if args.generate_base is not None:
        if not args.resolve_tables:
            parser.error("chunk generation requires the checked table closure")
        summary["offline_generation"] = generate_blocks(image, seen, cfg, args.out / "generated", args.generate_base)
    write_json(args.out / "structure.private.json", summary)
    print(json.dumps({"out": str(args.out), "image_size": len(image),
                      "elf": summary["elf_magic_at_zero"], "descriptor": descriptor,
                      "fingerprint": summary["runtime_fingerprint_recomputed"],
                      "instructions": len(seen), "mnemonics": len(cfg["mnemonics"]),
                      "indirect_sites": cfg["indirect"], "direct_call_count": len(cfg["calls"]),
                      "qualified_tables": tables, "ranges_half_open": cfg["ranges_half_open"],
                      "unknown_decode": cfg["unknown_decode_sites"],
                      "outside_targets": cfg["outside_image_targets"],
                      "translation_failures": unsupported, "translation_exceptions": errors,
                      "offline_generation": None if "offline_generation" not in summary else {
                          key: value for key, value in summary["offline_generation"].items()
                          if key in ("base", "instruction_count", "chunk_count", "generated", "missing_direct_targets",
                                     "strict_chunk_failure_sites", "unsupported_mnemonics", "computed_r0_sites", "non_r0_return_sites")},
                      "entry_first_16_roles": summary["entry_first_16_roles"]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
