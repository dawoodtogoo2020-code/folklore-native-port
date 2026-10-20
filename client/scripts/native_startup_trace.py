"""Passive memory observations of this launcher's owned Windows diagnostic.

ReadProcessMemory does not commit missing guest pages or change guest state.
Samples are asynchronous observations, not instruction traces or atomic snapshots.
"""
import ctypes
from ctypes import wintypes
import json
import re
import time

VM_LIMIT = 0xE0000000
TOC = 0x56EB00
# Indirection slots recovered locally from the selected revision. No game bytes.
WATCHES = (
    ("cache_stop", TOC - 0xB20, 1),
    ("cache_state", TOC - 0xB54, 4),
    ("cache_last_state", TOC - 0x3A68, 1),
    ("dialog_wait", TOC - 0xB14, 1),
    ("dialog_phase", TOC - 0xB04, 4),
    ("dialog_result", TOC - 0xB0C, 4),
    ("gamedata_check_flag", TOC - 0x3784, 1),
    ("install_pending", TOC - 0x3730, 1),
    ("install_error", TOC - 0x3778, 1),
)


def guest_read(reader, base, address, size):
    if (type(base) is not int or type(address) is not int or type(size) is not int
            or base <= 0 or base + VM_LIMIT >= 1 << 63
            or address < 0 or size < 1 or size > 32 or address + size > VM_LIMIT):
        raise ValueError("invalid guest observation range")
    raw = reader.read(base + address, size)
    return int.from_bytes(raw, "big") if raw is not None and len(raw) == size else None


def sample(reader, base):
    values = {}
    for name, slot, size in WATCHES:
        pointer = guest_read(reader, base, slot, 4)
        values[name + "_address"] = pointer
        values[name] = (guest_read(reader, base, pointer, size)
                        if pointer and pointer + size <= VM_LIMIT else None)
    for name, address in (("callback_result", 0x2520000),
                          ("callback_is_new", 0x2520024),
                          ("callback_set_param", 0x2521468)):
        values[name] = guest_read(reader, base, address, 4)
    return values


class OwnedProcessReader:
    """Only the PID returned by Popen in run_native_boot is passed here."""
    def __init__(self, pid):
        if type(pid) is not int or pid <= 0:
            raise ValueError("invalid owned process id")
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.api.OpenProcess.restype = wintypes.HANDLE
        self.api.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                               ctypes.c_void_p, ctypes.c_size_t,
                                               ctypes.POINTER(ctypes.c_size_t)]
        self.api.ReadProcessMemory.restype = wintypes.BOOL
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.restype = wintypes.BOOL
        # PROCESS_VM_READ only. No write, allocation, thread or suspend rights.
        self.handle = self.api.OpenProcess(0x0010, False, pid)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())

    def read(self, address, size):
        output = (ctypes.c_ubyte * size)()
        count = ctypes.c_size_t()
        ok = self.api.ReadProcessMemory(self.handle, address, output, size,
                                       ctypes.byref(count))
        return bytes(output) if ok and count.value == size else None

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def wait_with_trace(process, timeout, folder, on_poll=None):
    """Preserve Popen timeout handling and retain every owned handle locally."""
    import subprocess
    started = time.monotonic()
    reader = OwnedProcessReader(process.pid)
    base = None
    last = None
    last_emitted = 0.0
    try:
        with (folder / "startup-memory.jsonl").open("w", encoding="utf-8") as output:
            while process.poll() is None:
                now = time.monotonic()
                if now - started >= timeout:
                    raise subprocess.TimeoutExpired(process.args, timeout)
                if on_poll is not None:
                    on_poll()
                if base is None:
                    text = (folder / "boot.log").read_text(encoding="utf-8", errors="replace")
                    found = re.search(r"\[startup-trace\] vm_base=0x([0-9A-Fa-f]+) limit=0xE0000000", text)
                    if found:
                        base = int(found[1], 16)
                if base is not None:
                    values = sample(reader, base)
                    if values != last or now - last_emitted >= 1.0:
                        output.write(json.dumps({"elapsed_seconds": round(now - started, 6),
                                                 "values": values}) + "\n")
                        output.flush()
                        last, last_emitted = values, now
                time.sleep(0.05)
        return process.returncode
    finally:
        reader.close()
