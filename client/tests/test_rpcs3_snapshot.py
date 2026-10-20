"""Synthetic RSP tests only. No game, provider, or native application access."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("rpcs3_snapshot", ROOT / "scripts" / "rpcs3_snapshot.py")
snapshot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(snapshot)
UPSTREAM = Path(os.environ.get("RSP_UPSTREAM", r"Q:\folklore\work\ps3recomp\tools\rpcs3_probe\rsp.py"))
LEGACY_SOURCE = Path(r"Q:\folklore\research\RPCS3-4d5def58-GDB.cpp")
LEGACY_DATA = b"".join((b"x" * (size * 2) if name in snapshot.LEGACY_UNAVAILABLE
                         else f"{index + 1:0{size * 2}x}".encode("ascii"))
                        for index, (name, size) in enumerate(snapshot.LEGACY_REGISTERS))
XML = b'<target><architecture>powerpc:common64</architecture><feature name="test"><reg name="r0" bitsize="64" regnum="0"/><reg name="pc" bitsize="32" regnum="1"/></feature></target>'


class FakeRSP:
    def __init__(self, registers=b"010203040506070800100000", memory=b"deadbeef", xml=XML,
                 corrupt=False, stall=False, noack=False, stop=b"T0506:00100000;thread:1;",
                 legacy=False, current=b"QC0000000001000001", threads=b"m0000000001000001,0000000001000002l"):
        self.registers, self.memory, self.xml = registers, memory, xml
        self.corrupt, self.stall, self.noack = corrupt, stall, noack
        self.stop = stop
        self.legacy, self.current, self.threads = legacy, current, threads
        self.commands = []
        self.error = None
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        self.listener.listen(1)
        self.listener.settimeout(2)
        self.thread = threading.Thread(target=self.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *unused):
        self.listener.close()
        self.thread.join(3)
        if self.thread.is_alive():
            raise AssertionError("fake RSP thread did not terminate")
        if self.error:
            raise self.error

    def run(self):
        try:
            with self.listener.accept()[0] as connection:
                connection.settimeout(2)
                buffer = b""
                while True:
                    part = connection.recv(4096)
                    if not part:
                        return
                    buffer += part
                    while buffer:
                        if buffer[:1] in (b"+", b"-"):
                            buffer = buffer[1:]
                            continue
                        if buffer[:1] == b"\x03":
                            self.commands.append("INTERRUPT")
                            buffer = buffer[1:]
                            self.respond(connection, b"T02thread:1;")
                            continue
                        if b"#" not in buffer or len(buffer) < buffer.index(b"#") + 3:
                            break
                        end = buffer.index(b"#")
                        command = buffer[1:end].decode("ascii")
                        buffer = buffer[end + 3:]
                        self.commands.append(command)
                        if not self.noack or command.startswith("qSupported") or command == "QStartNoAckMode":
                            connection.sendall(b"+")
                        if self.stall:
                            # bounded fixture delay to exercise total deadline
                            time.sleep(.15)
                            return
                        if command.startswith("qSupported"):
                            reply = b"PacketSize=1200" if self.legacy else b"qXfer:features:read+" + (b";QStartNoAckMode+" if self.noack else b"")
                        elif command == "qfThreadInfo":
                            reply = self.threads
                        elif command.startswith(("Hg", "Hc")):
                            reply = b"OK"
                        elif command == "qC":
                            reply = self.current
                        elif command == "QStartNoAckMode":
                            reply = b"OK"
                        elif command.startswith("qXfer:features:read:target.xml:"):
                            reply = b"l" + self.xml
                        elif command == "?":
                            reply = self.stop
                        elif command == "g":
                            reply = self.registers
                        elif command.startswith("m"):
                            reply = self.memory
                        elif command in ("c", "vCont;c"):
                            continue
                        else:
                            raise AssertionError("unexpected command: " + command)
                        self.respond(connection, reply)
        except (ConnectionResetError, BrokenPipeError):
            pass
        except Exception as error:
            self.error = error

    def respond(self, connection, reply):
        checksum = (sum(reply) + int(self.corrupt)) & 255
        connection.sendall(b"$" + reply + b"#" + f"{checksum:02x}".encode("ascii"))


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="snapshot-test-", dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.report = Path(self.temp.name) / "report.json"

    def invoke(self, server, *extra):
        args = ["--upstream", str(UPSTREAM), "--port", str(server.port), "--report", str(self.report), *extra]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return snapshot.main(args)

    def test_actual_data_decode_default_paused(self):
        with FakeRSP() as server:
            self.assertEqual(self.invoke(server, "--memory", "0x1000:4"), 0)
        result = json.loads(self.report.read_text())
        self.assertEqual(result["registers"][0]["value"], 0x0102030405060708)
        self.assertEqual(result["registers"][1]["value"], 0x00100000)
        self.assertEqual(result["memory"][0]["hex"], "deadbeef")
        self.assertEqual(result["host"], "127.0.0.1")
        self.assertEqual(len(result["upstream_sha256"]), 64)
        self.assertTrue(result["registers_complete"] and result["memory_complete"])
        self.assertFalse(result["resumed"] or result["interrupted"])
        self.assertNotIn("c", server.commands)
        self.assertNotIn("INTERRUPT", server.commands)

    def test_legacy_exact_layout_and_selected_ppu(self):
        self.assertEqual(len(snapshot.LEGACY_REGISTERS), 71)
        self.assertEqual(sum(size for _, size in snapshot.LEGACY_REGISTERS), 556)
        with FakeRSP(legacy=True, registers=LEGACY_DATA) as server:
            self.assertEqual(self.invoke(server, "--rpcs3-legacy-source", str(LEGACY_SOURCE)), 0)
        report = json.loads(self.report.read_text())
        self.assertEqual(len(report["registers"]), 71)
        self.assertEqual(report["registers"][64]["name"], "pc")
        self.assertEqual(report["registers"][64]["value"], 65)
        self.assertEqual(report["registers"][66]["size_bytes"], 4)
        self.assertEqual(report["registers"][67]["size_bytes"], 8)
        self.assertIsNone(report["registers"][65]["value"])
        self.assertFalse(report["registers_complete"])
        self.assertTrue(report["available_registers_complete"] and report["register_block_complete"])
        self.assertEqual(report["thread"]["selected_ppu_id"], "0x01000001")
        self.assertEqual(report["legacy_source"]["sha256"], snapshot.LEGACY_SOURCE_SHA256)
        self.assertEqual(server.commands, ["qSupported", "?", "qfThreadInfo", "Hg1000001", "qC", "g", "qC"])

    def test_legacy_short_or_malformed_register_block_suppressed(self):
        for registers in (LEGACY_DATA[:-2], LEGACY_DATA + b"00", LEGACY_DATA.replace(b"0000000000000001", b"xxxxxxxxxxxxxxxx", 1)):
            with self.subTest(size=len(registers)), FakeRSP(legacy=True, registers=registers) as server:
                self.assertEqual(self.invoke(server, "--rpcs3-legacy-source", str(LEGACY_SOURCE)), 1)
                self.assertFalse(self.report.exists())

    def test_legacy_wrong_source_hash_precedes_upstream_execution(self):
        bad_source = Path(self.temp.name) / "GDB.cpp"
        bad_source.write_text("untrusted source")
        args = ["--upstream", str(UPSTREAM), "--report", str(self.report), "--rpcs3-legacy-source", str(bad_source)]
        with mock.patch.object(snapshot, "load_upstream") as upstream, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(snapshot.main(args), 1)
            upstream.assert_not_called()
        self.assertFalse(self.report.exists())

    def test_legacy_invalid_thread_is_rejected_before_registers(self):
        cases = [{"current": b"QC0000000001000002"}, {"current": b""}, {"threads": b"ml"},
                 {"threads": b"m0000000000000000l"}, {"threads": b"m0000000001000001"}]
        for options in cases:
            with self.subTest(options=options), FakeRSP(legacy=True, registers=LEGACY_DATA, **options) as server:
                self.assertEqual(self.invoke(server, "--rpcs3-legacy-source", str(LEGACY_SOURCE)), 1)
                self.assertNotIn("g", server.commands)
                self.assertFalse(self.report.exists())

    def test_legacy_explicit_resume_uses_vendor_command(self):
        with FakeRSP(legacy=True, registers=LEGACY_DATA) as server:
            self.assertEqual(self.invoke(server, "--rpcs3-legacy-source", str(LEGACY_SOURCE), "--resume"), 0)
        self.assertEqual(server.commands[-2:], ["Hc1000001", "vCont;c"])
        self.assertTrue(json.loads(self.report.read_text())["resumed"])

    def test_interrupt_and_explicit_resume(self):
        with FakeRSP(noack=True) as server:
            self.assertEqual(self.invoke(server, "--interrupt", "--resume"), 0)
        result = json.loads(self.report.read_text())
        self.assertTrue(result["interrupted"] and result["resumed"])
        self.assertIn("INTERRUPT", server.commands)
        self.assertIn("c", server.commands)
        self.assertNotIn("?", server.commands)

    def test_register_failures_suppress_report(self):
        for registers in (b"00", b"xx" * 12, b"zz" * 12, b"00" * 13, b"E01"):
            with self.subTest(registers=registers), FakeRSP(registers=registers) as server:
                self.assertEqual(self.invoke(server), 1)
                self.assertFalse(self.report.exists())

    def test_memory_failures_suppress_report(self):
        for memory in (b"aa", b"00" * 5, b"xx" * 4, b"E01", b"abc", b"00 00 00 00"):
            with self.subTest(memory=memory), FakeRSP(memory=memory) as server:
                self.assertEqual(self.invoke(server, "--memory", "0:4"), 1)
                self.assertFalse(self.report.exists())

    def test_xml_failures_do_not_fallback(self):
        for xml in (b"<broken", b"<target/>", XML.replace(b'bitsize="64"', b'bitsize="7"'),
                    XML.replace(b'regnum="1"', b'regnum="3"')):
            with self.subTest(xml=xml), FakeRSP(xml=xml) as server:
                self.assertEqual(self.invoke(server), 1)
                self.assertFalse(self.report.exists())
                self.assertNotIn("g", server.commands)

    def test_bad_checksum_suppresses_report(self):
        with FakeRSP(corrupt=True) as server:
            self.assertEqual(self.invoke(server), 1)
        self.assertFalse(self.report.exists())

    def test_exited_or_malformed_stop_suppresses_report(self):
        for reply in (b"W00", b"X09", b"OK", b"T05garbage", b"S0z"):
            with self.subTest(reply=reply), FakeRSP(stop=reply) as server:
                self.assertEqual(self.invoke(server), 1)
                self.assertFalse(self.report.exists())
                self.assertNotIn("g", server.commands)

    def test_socket_destination_is_hardcoded_loopback(self):
        module, _, _ = snapshot.load_upstream(UPSTREAM)
        client = snapshot.strict_client(module, 23491, .1)
        with mock.patch.object(module.socket, "create_connection", side_effect=OSError("synthetic refusal")) as connection:
            with self.assertRaises(module.RSPError):
                client.connect(retries=1, delay=0)
            self.assertEqual(connection.call_count, 1)
            self.assertEqual(connection.call_args.args[0], ("127.0.0.1", 23491))

    def test_total_timeout_suppresses_report(self):
        start = time.monotonic()
        with FakeRSP(stall=True) as server:
            self.assertEqual(self.invoke(server, "--timeout", "0.05"), 1)
        self.assertLess(time.monotonic() - start, 1)
        self.assertFalse(self.report.exists())

    def test_range_limits(self):
        self.assertEqual(snapshot.memory_range("0xffffffff:1"), (0xffffffff, 1))
        self.assertEqual(snapshot.memory_range("0:256"), (0, 256))
        for value in ("-1:1", "0:0", "0:257", "0xffffffff:2", "0x100000000:1", "bad", "0:1:2"):
            with self.subTest(value=value), self.assertRaises(Exception):
                snapshot.memory_range(value)

    def test_invalid_arguments_do_not_connect(self):
        base = ["--upstream", str(UPSTREAM), "--report", str(self.report)]
        cases = [["--host", "example.com"], ["--port", "0"], ["--port", "65536"],
                 ["--timeout", "31"], ["--timeout", "nan"], ["--timeout", "0"],
                 ["--memory", "0:256"] * 5]
        for extra in cases:
            with self.subTest(extra=extra), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                snapshot.main(base + extra)
        self.assertFalse(self.report.exists())

    def test_existing_report_is_preserved_without_connection(self):
        self.report.write_text("original")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(snapshot.main(["--upstream", str(UPSTREAM), "--report", str(self.report)]), 1)
        self.assertEqual(self.report.read_text(), "original")


if __name__ == "__main__":
    unittest.main()
