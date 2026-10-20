"""Bounded, loopback-only RPCS3 guest snapshot. No application launch or writes.

Imports an explicitly supplied MIT rsp.py; tightens its permissive decoding,
checksum handling and fallback layout. Snapshot requires a stopped target.
"""
import argparse
import datetime
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import sys
import time
import xml.etree.ElementTree as ET


HOST = "127.0.0.1"
LEGACY_SOURCE_SHA256 = "5a80276db6d79868e0871e5f7377b41421124e6b0f12085a95fec3886175e867"
LEGACY_REGISTERS = [(f"r{i}", 8) for i in range(32)] + [(f"f{i}", 8) for i in range(32)] + [
    ("pc", 8), ("msr", 8), ("cr", 4), ("lr", 8), ("ctr", 8), ("xer", 4), ("fpscr", 4)]
LEGACY_UNAVAILABLE = {"msr", "xer", "fpscr"}


def verify_legacy_source(path):
    resolved = path.resolve(strict=True)
    digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
    if digest != LEGACY_SOURCE_SHA256:
        raise ValueError("legacy RPCS3 source SHA256 does not match pinned GDB.cpp")
    return {"path": str(resolved), "sha256": digest,
            "authority": "RPCS3 4d5def58 GDB.cpp get_reg/get_reg_size/read_all: PowerPC rs6000 IDs 0..70, big-endian hex, 556 bytes; msr/xer/fpscr unavailable. select_thread and qfThreadInfo enumerate PPU threads only; Hg selects general_ops_thread_id; qC verifies selected_thread. XML is unsupported."}


def memory_range(value):
    try:
        address, size = (int(part, 0) for part in value.split(":"))
    except (ValueError, TypeError):
        raise argparse.ArgumentTypeError("memory must be ADDRESS:SIZE (decimal or 0x hex)")
    if not 0 <= address <= 0xffffffff or not 1 <= size <= 256 or address + size > 0x100000000:
        raise argparse.ArgumentTypeError("memory range must fit guest 32-bit space and contain 1..256 bytes")
    return address, size


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--upstream", required=True, type=Path, help="approved upstream rsp.py")
    result.add_argument("--rpcs3-legacy-source", type=Path, help="explicit source-hash-pinned RPCS3 legacy register layout; no XML fallback")
    result.add_argument("--port", type=int, default=23491)
    result.add_argument("--timeout", type=float, default=10, help="total network operation budget, 0 < seconds <= 30")
    result.add_argument("--report", type=Path, required=True, help="new JSON file, never overwritten")
    result.add_argument("--memory", action="append", type=memory_range, default=[])
    result.add_argument("--interrupt", action="store_true")
    result.add_argument("--resume", action="store_true", help="explicitly continue after successful capture")
    return result


def load_upstream(path):
    path = path.resolve(strict=True)
    if path.name != "rsp.py" or not path.is_file():
        raise ValueError("--upstream must identify the approved rsp.py file")
    source = path.read_bytes()
    digest = hashlib.sha256(source).hexdigest()
    # Execute exactly the bytes hashed, avoiding a separate loader reread.
    spec = importlib.util.spec_from_loader("snapshot_upstream_rsp", loader=None)
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(path)
    exec(compile(source, str(path), "exec"), module.__dict__)
    return module, digest, path


def strict_client(module, port, timeout, legacy=None):
    class Client(module.RSPClient):
        def __init__(self):
            super().__init__(host=HOST, port=port, timeout=timeout)
            self.end = time.monotonic() + timeout
            self.xml_documents = {}
            self.thread_evidence = None

        def remaining(self):
            remaining = self.end - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("total snapshot network deadline exceeded")
            return remaining

        def _read_raw(self, n=4096):
            self.sock.settimeout(self.remaining())
            data = self.sock.recv(n)
            if not data:
                raise ValueError("RSP connection closed")
            return data

        def _send_packet(self, payload):
            self.sock.settimeout(self.remaining())
            return super()._send_packet(payload)

        def _recv_packet(self, deadline=None):
            # Do not use upstream's unchecked checksum / unbounded packet buffer.
            while b"$" not in self.buf:
                self.buf += self._read_raw()
                if len(self.buf) > 262144:
                    raise ValueError("oversized RSP preamble")
            self.buf = self.buf[self.buf.index(b"$"):]
            while b"#" not in self.buf or len(self.buf) < self.buf.index(b"#") + 3:
                self.buf += self._read_raw()
                if len(self.buf) > 262144:
                    raise ValueError("oversized RSP packet")
            marker = self.buf.index(b"#")
            payload, checksum = self.buf[1:marker], self.buf[marker + 1:marker + 3]
            self.buf = self.buf[marker + 3:]
            if not re.fullmatch(rb"[0-9a-fA-F]{2}", checksum) or int(checksum, 16) != sum(payload) & 255:
                raise ValueError("invalid RSP checksum")
            if not self.no_ack:
                self.sock.settimeout(self.remaining())
                self.sock.sendall(b"+")
            decoded = bytearray()
            i = 0
            while i < len(payload):
                c = payload[i]
                if c == 125:
                    if i + 1 >= len(payload):
                        raise ValueError("truncated RSP escape")
                    decoded.append(payload[i + 1] ^ 32)
                    i += 2
                elif c == 42:
                    if not decoded or i + 1 >= len(payload) or not 32 <= payload[i + 1] <= 126:
                        raise ValueError("invalid RSP repeat")
                    decoded.extend([decoded[-1]] * (payload[i + 1] - 29))
                    i += 2
                else:
                    decoded.append(c)
                    i += 1
                if len(decoded) > 262144:
                    raise ValueError("oversized decoded packet")
            return bytes(decoded)

        def qxfer_read(self, obj, annex=""):
            result = bytearray()
            while True:
                self._send_packet(f"qXfer:{obj}:read:{annex}:{len(result):x},1000")
                packet = self._recv_packet()
                if packet[:1] not in (b"l", b"m") or (packet[:1] == b"m" and len(packet) == 1):
                    raise ValueError("incomplete or unsupported target description transfer")
                result.extend(packet[1:])
                if len(result) > 1048576:
                    raise ValueError("target description too large")
                if packet[:1] == b"l":
                    return bytes(result)

        def handshake(self):
            if legacy is not None:
                self._send_packet("qSupported")
                features = self._recv_packet().decode("ascii")
                if features != "PacketSize=1200":
                    raise ValueError("unexpected pinned RPCS3 capabilities")
                offset = 0
                for name, size in LEGACY_REGISTERS:
                    self.reg_layout.append((name, offset, size))
                    self.reg_by_name[name] = (offset, size)
                    offset += size
                return features
            self._send_packet("qSupported:multiprocess+;xmlRegisters=powerpc;qXfer:features:read+")
            features = self._recv_packet().decode("ascii")
            if "QStartNoAckMode+" in features.split(";"):
                self._send_packet("QStartNoAckMode")
                if self._recv_packet() != b"OK":
                    raise ValueError("no-ack negotiation rejected")
                self.no_ack = True
            self._load_registers()
            return features

        def select_legacy_ppu(self):
            self._send_packet("qfThreadInfo")
            reply = self._recv_packet()
            # This exact vendor emits both the m prefix and l suffix in ONE packet.
            if not re.fullmatch(rb"m[0-9a-fA-F]{16}(?:,[0-9a-fA-F]{16})*l", reply):
                raise ValueError("invalid or empty pinned RPCS3 PPU thread list")
            threads = [int(value, 16) for value in reply[1:-1].split(b",")]
            if len(threads) != len(set(threads)) or any(not 0 < value <= 0xffffffff for value in threads):
                raise ValueError("invalid real PPU thread IDs")
            selected = threads[0]
            self._send_packet(f"Hg{selected:x}")
            if self._recv_packet() != b"OK":
                raise ValueError("RPCS3 rejected explicit PPU thread selection")
            self.thread_evidence = {"enumerated_ppu_ids": [f"0x{value:08x}" for value in threads],
                                    "selected_ppu_id": f"0x{selected:08x}",
                                    "selection": "first enumerated PPU; explicit Hg, verified qC before and after reads"}
            self.verify_legacy_thread()

        def verify_legacy_thread(self):
            self._send_packet("qC")
            reply = self._recv_packet()
            if not re.fullmatch(rb"QC[0-9a-fA-F]{16}", reply) or int(reply[2:], 16) != int(self.thread_evidence["selected_ppu_id"], 16):
                raise ValueError("selected PPU thread did not match qC")

        def _load_registers(self):
            registers = []
            seen = set()

            def visit(annex):
                if annex in seen or len(seen) >= 16 or not re.fullmatch(r"[A-Za-z0-9_.-]+", annex):
                    raise ValueError("cyclic, excessive or unsupported target XML include")
                seen.add(annex)
                raw = self.qxfer_read("features", annex)
                self.xml_documents[annex] = raw.decode("utf-8")
                root = ET.fromstring(raw)
                for item in root.iter():
                    tag = item.tag.rsplit("}", 1)[-1]
                    if tag == "include":
                        visit(item.attrib["href"])
                    elif tag == "architecture" and not (item.text or "").strip().lower().startswith("powerpc"):
                        raise ValueError("target architecture is not PowerPC")
                    elif tag == "reg":
                        name = item.attrib["name"]
                        bits = int(item.attrib["bitsize"])
                        number = int(item.get("regnum", str(len(registers))), 0)
                        if not name or any(name == r[0] for r in registers) or number != len(registers) or bits <= 0 or bits % 8 or bits > 4096:
                            raise ValueError("invalid or sparse target register layout")
                        registers.append((name, bits // 8))
            visit("target.xml")
            if not registers or sum(size for _, size in registers) > 65536:
                raise ValueError("missing or excessive target registers")
            offset = 0
            for name, size in registers:
                self.reg_layout.append((name, offset, size))
                self.reg_by_name[name] = (offset, size)
                offset += size

        def read_registers(self):
            self._send_packet("g")
            value = self._recv_packet()
            expected = sum(size for _, _, size in self.reg_layout)
            if legacy is not None:
                if len(value) != expected * 2:
                    raise ValueError("incomplete legacy guest register block")
                result = []
                for name, off, size in self.reg_layout:
                    data = value[off * 2:(off + size) * 2]
                    if name in LEGACY_UNAVAILABLE:
                        if data != b"x" * (size * 2):
                            raise ValueError("unexpected vendor unavailable register encoding")
                        result.append({"name": name, "size_bytes": size, "hex": None, "value": None,
                                       "available": False, "reason": "pinned vendor get_reg returns x placeholders"})
                    else:
                        if not re.fullmatch(rb"[0-9a-fA-F]+", data):
                            raise ValueError("malformed available legacy guest register")
                        raw = bytes.fromhex(data.decode("ascii"))
                        result.append({"name": name, "size_bytes": size, "hex": raw.hex(),
                                       "value": int.from_bytes(raw, "big"), "available": True})
                return result
            if len(value) != expected * 2 or not re.fullmatch(rb"[0-9a-fA-F]+", value):
                raise ValueError("malformed, unavailable or incomplete guest register block")
            raw = bytes.fromhex(value.decode("ascii"))
            return [{"name": name, "size_bytes": size, "hex": raw[off:off + size].hex(),
                     "value": int.from_bytes(raw[off:off + size], "big")}
                    for name, off, size in self.reg_layout]

        def read_mem(self, address, size):
            self._send_packet(f"m{address:x},{size:x}")
            value = self._recv_packet()
            if len(value) != size * 2 or not re.fullmatch(rb"[0-9a-fA-F]+", value):
                raise ValueError("malformed, truncated or excessive guest memory response")
            return bytes.fromhex(value.decode("ascii"))

    return Client()


def stopped(reply):
    if not re.fullmatch(rb"(?:S[0-9a-fA-F]{2}|T[0-9a-fA-F]{2}(?:[A-Za-z0-9_.-]+:[^;]*;)*)", reply):
        raise ValueError("target did not return a valid stopped status")
    return reply.decode("ascii")


def capture(args):
    if args.report.exists():
        raise ValueError("report already exists")
    legacy = verify_legacy_source(args.rpcs3_legacy_source) if args.rpcs3_legacy_source else None
    if legacy and args.interrupt:
        raise ValueError("pinned RPCS3 auto-pauses on connection; legacy --interrupt is unsupported, omit it")
    module, digest, path = load_upstream(args.upstream)
    client = strict_client(module, args.port, args.timeout, legacy=legacy)
    try:
        client.connect(retries=1, delay=0)
        features = client.handshake()
        if getattr(client, "_reg_err", None):
            raise ValueError("target XML register discovery failed: " + client._reg_err)
        if args.interrupt:
            client.interrupt()
            stop = stopped(client.wait_stop())
        else:
            client._send_packet("?")
            stop = stopped(client._recv_packet())
        if legacy:
            client.select_legacy_ppu()
        registers = client.read_registers()
        memory = []
        for address, size in args.memory:
            raw = client.read_mem(address, size)
            if len(raw) != size:
                raise ValueError("truncated or excessive guest memory response")
            memory.append({"address": f"0x{address:08x}", "size_bytes": size, "hex": raw.hex(), "complete": True})
        if legacy:
            client.verify_legacy_thread()
        report = {"schema": "rpcs3-guest-snapshot-v1", "captured_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  "host": HOST, "port": args.port, "upstream_path": str(path), "upstream_sha256": digest,
                  "features": features, "target_xml": client.xml_documents, "byte_order": "big",
                  "interrupted": args.interrupt, "stop_reply": stop, "resumed": False,
                  "registers": registers, "memory": memory, "registers_complete": True, "memory_complete": True,
                  "complete": True}
        if legacy:
            report.update({"register_layout_mode": "explicit-pinned-rpcs3-legacy", "legacy_source": legacy,
                           "thread": client.thread_evidence, "register_block_complete": True,
                           "registers_complete": False, "unavailable_registers": sorted(LEGACY_UNAVAILABLE),
                           "available_registers_complete": True, "register_block_bytes": 556,
                           "pause_evidence": "pinned source auto-pauses running emulator on GDB connection; ? returned stopped"})
        if args.resume:
            if legacy:
                selected = int(client.thread_evidence["selected_ppu_id"], 16)
                client._send_packet(f"Hc{selected:x}")
                if client._recv_packet() != b"OK":
                    raise ValueError("RPCS3 rejected continue-thread selection")
                client._send_packet("vCont;c")
            else:
                client.cont()
            report["resumed"] = True
            report["resume_evidence"] = "continue command sent; running state not independently observed"
        client.remaining()
        # Exclusive creation is the final operation; failures never publish success.
        try:
            with args.report.open("x", encoding="utf-8") as handle:
                json.dump(report, handle, indent=2)
                handle.write("\n")
        except FileExistsError:
            raise
        except Exception:
            args.report.unlink(missing_ok=True)
            raise
    finally:
        client.close()


def main(argv=None):
    cli = parser()
    args = cli.parse_args(argv)
    if not 1 <= args.port <= 65535:
        cli.error("port must be 1..65535")
    if not math.isfinite(args.timeout) or not 0 < args.timeout <= 30:
        cli.error("timeout must be finite and 0 < seconds <= 30")
    if sum(size for _, size in args.memory) > 1024:
        cli.error("total requested memory exceeds 1024 bytes")
    try:
        capture(args)
    except Exception as exc:
        print(f"snapshot failed: {exc}", file=sys.stderr)
        return 1
    print(str(args.report.resolve()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
