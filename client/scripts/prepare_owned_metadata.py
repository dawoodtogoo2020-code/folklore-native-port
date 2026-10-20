"""Recreate accepted PPU metadata from the owner's supported ELF.

No instructions, game assets, firmware or private metadata are bundled.
Output uses the historical byte encoding so existing acceptance hashes remain
meaningful. A successful receipt proves regeneration, not gameplay.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

from build_owned_backup import plain_output
from regenerate_ppu import ELF_SHA, META_HASHES, reject_overlap, sha


def bounded_functions(loader, elf):
    ranges = loader.code_ranges(elf)
    address, size = loader.find_opd(elf, elf.elf_header.e_entry)
    if address is None:
        raise ValueError("Missing ELF function descriptors")
    entries, _ = loader.parse_opd(elf, address, size, ranges)
    starts = sorted({entry["func"] for entry in entries})
    toc = {entry["func"]: entry["toc"] for entry in entries}
    opd = {entry["func"]: entry["opd"] for entry in entries}
    sections = [(s.sh_addr, s.sh_addr + s.sh_size)
                for s in elf.section_headers if s.sh_flags & 4 and s.sh_size]
    result = []
    for index, start in enumerate(starts):
        segment_ends = [hi for lo, hi in ranges if lo <= start < hi]
        section_ends = [hi for lo, hi in sections if lo <= start < hi]
        if len(segment_ends) != 1 or len(section_ends) != 1:
            raise ValueError("Function has ambiguous or missing executable bounds")
        end = min(segment_ends[0], section_ends[0])
        if index + 1 < len(starts):
            end = min(end, starts[index + 1])
        if start % 4 or end % 4 or end <= start:
            raise ValueError("Invalid function boundary")
        result.append(dict(start=hex(start), end=hex(end),
                           toc=hex(toc[start]), opd=hex(opd[start])))
    if not result:
        raise ValueError("No function descriptors")
    return result


def encode_metadata(value, indent):
    return json.dumps(value, indent=indent).replace("\n", "\r\n").encode("ascii")


def execute(args):
    for path in (args.elf, args.sdk, args.output):
        if not path.is_absolute():
            raise ValueError("Use absolute input and output paths")
    plain_output(args.output)
    elf_path, sdk, output = (p.resolve() for p in (args.elf, args.sdk, args.output))
    reject_overlap(output, [elf_path, sdk, Path(__file__).parent])
    if sha(elf_path) != ELF_SHA:
        raise ValueError("Unsupported ELF SHA256; requires USA BCUS98147 1.00")
    tools = sdk / "tools"
    sys.path.insert(0, str(tools))
    spec = importlib.util.spec_from_file_location("owned_ppu_loader", tools / "ppu_loader.py")
    loader = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(loader)
        elf = loader.ELFFile(str(elf_path))
        elf.load()
        functions = bounded_functions(loader, elf)
        imports = loader.parse_imports(elf)
    finally:
        sys.path.remove(str(tools))
    encoded = {
        "EBOOT.functions.bounded.json": encode_metadata(functions, 2),
        "EBOOT.imports.json": encode_metadata(imports, 1),
    }
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in encoded.items()}
    if hashes != META_HASHES or sha(elf_path) != ELF_SHA:
        raise ValueError("Regenerated metadata differs from the accepted build")
    # All identities pass before creating output. Never overwrite an old run.
    plain_output(args.output)
    output.mkdir(parents=True, exist_ok=False)
    for name, data in encoded.items():
        (output / name).write_bytes(data)
    receipt = {
        "state": "verified", "elf_sha256": ELF_SHA, "files": hashes,
        "functions": len(functions), "imports": len(imports),
        "tools": {name: sha(tools / name) for name in ("ppu_loader.py", "elf_parser.py")},
        "runtime_tested": False,
    }
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("elf", "sdk", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    try:
        print(json.dumps(execute(parser.parse_args())))
    except (ValueError, OSError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
