"""Read exact, bundled translator source without Git or network access."""
import hashlib
from pathlib import Path
import stat

PPU_REV = "b199831f81be0b34fcc02f7f5d8434fefcc4ca94"
SPU_REV = "d055c40ec4d38dab2b14906bd1a5ffa4d53437d5"
PINS = {
    PPU_REV: {
        "ppu_lifter.py": "af1391ff20aeb47237ff304f63563cd63d285c78a4aaa9424f3e260e34a1d28f",
        "ppu_disasm.py": "1fd70ab34d22b2c55d49a779c9f9fb4a625ef4469c89314fc2e8bae00253d35e",
        "elf_parser.py": "4430264c27410598d3714c21707b9cd7c65ba23a85c3b6c26a2b1f11089903eb",
    },
    SPU_REV: {
        "spu_lifter.py": "c14f224c3bb44fadea1a26493c3a36ef2a57d41389e6078e4bbec988a49c5bc2",
    },
}
MAX_BYTES = 2 * 1024 * 1024


def read_pinned_tool(sdk, revision, name):
    try:
        expected = PINS[revision][name]
    except (KeyError, TypeError) as error:
        raise ValueError("Unsupported pinned translator") from error
    path = Path(sdk) / "tools" / "pinned" / revision / name
    for component in reversed((path, *path.parents)):
        info = component.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Pinned translator path contains a link or reparse point")
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
        raise ValueError("Pinned translator is not a regular file within size limit")
    with path.open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES or hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("Pinned translator hash mismatch")
    return data


def pinned_fingerprints(sdk):
    return {
        f"pinned:{revision}:tools/{name}":
            hashlib.sha256(read_pinned_tool(sdk, revision, name)).hexdigest()
        for revision in sorted(PINS) for name in sorted(PINS[revision])
    }
