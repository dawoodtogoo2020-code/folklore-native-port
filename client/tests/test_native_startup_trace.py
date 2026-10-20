"""Synthetic tests for passive startup observation; no process is opened."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import native_startup_trace as trace


class Memory:
    def __init__(self, contents=None):
        self.contents = contents or {}
        self.calls = []

    def read(self, address, size):
        self.calls.append((address, size))
        return self.contents.get((address, size))


class Process:
    pid = 123
    args = ["synthetic-diagnostic"]

    def __init__(self, polls, returncode=86):
        self.polls = iter(polls)
        self.returncode = returncode

    def poll(self):
        return next(self.polls)


class Reader:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class ObservationTests(unittest.TestCase):
    def test_big_endian_pointer_and_value_widths_and_fixed_callbacks(self):
        base = 0x100000000
        contents = {}
        for index, (name, slot, size) in enumerate(trace.WATCHES):
            pointer = 0x1000 + index * 0x20
            contents[(base + slot, 4)] = pointer.to_bytes(4, "big")
            contents[(base + pointer, size)] = (index + 1).to_bytes(size, "big")
        callbacks = {"callback_result": (0x2520000, 0x12345678),
                     "callback_is_new": (0x2520024, 0x00000102),
                     "callback_set_param": (0x2521468, 0xABCDEF01)}
        for address, value in callbacks.values():
            contents[(base + address, 4)] = value.to_bytes(4, "big")
        memory = Memory(contents)

        values = trace.sample(memory, base)

        for index, (name, slot, size) in enumerate(trace.WATCHES):
            pointer = 0x1000 + index * 0x20
            self.assertEqual(values[name + "_address"], pointer)
            self.assertEqual(values[name], index + 1)
            self.assertIn((base + slot, 4), memory.calls)
            self.assertIn((base + pointer, size), memory.calls)
        for name, (address, value) in callbacks.items():
            self.assertEqual(values[name], value)
            self.assertIn((base + address, 4), memory.calls)

    def test_missing_short_and_uncommitted_reads_are_unavailable(self):
        base = 0x200000000
        first = trace.WATCHES[0]
        second = trace.WATCHES[1]
        third = trace.WATCHES[2]
        contents = {
            (base + second[1], 4): b"\x00\x10",  # Partial pointer read.
            (base + third[1], 4): (0x3000).to_bytes(4, "big"),
            (base + 0x3000, third[2]): b"",  # Uncommitted value page.
        }
        memory = Memory(contents)

        values = trace.sample(memory, base)

        self.assertIsNone(values[first[0] + "_address"])
        self.assertIsNone(values[first[0]])
        self.assertIsNone(values[second[0] + "_address"])
        self.assertIsNone(values[second[0]])
        self.assertEqual(values[third[0] + "_address"], 0x3000)
        self.assertIsNone(values[third[0]])
        self.assertNotIn((base, first[2]), memory.calls)
        self.assertNotIn((base, second[2]), memory.calls)
        self.assertEqual(memory.calls.count((base + 0x3000, third[2])), 1)
        for name in ("callback_result", "callback_is_new", "callback_set_param"):
            self.assertIsNone(values[name])

    def test_null_and_out_of_range_indirections_are_not_followed(self):
        base = 0x100000000
        first, second = trace.WATCHES[:2]
        memory = Memory({
            (base + first[1], 4): bytes(4),
            (base + second[1], 4): (trace.VM_LIMIT - second[2] + 1).to_bytes(4, "big"),
        })

        values = trace.sample(memory, base)

        self.assertEqual(values[first[0] + "_address"], 0)
        self.assertEqual(values[second[0] + "_address"], trace.VM_LIMIT - second[2] + 1)
        self.assertIsNone(values[first[0]])
        self.assertIsNone(values[second[0]])
        self.assertNotIn((base, first[2]), memory.calls)
        self.assertNotIn((base + trace.VM_LIMIT - second[2] + 1, second[2]), memory.calls)

    def test_invalid_ranges_and_host_overflow_rejected_before_reader_call(self):
        memory = Memory()
        invalid = [
            (0, 0, 4), (-1, 0, 4), (1 << 63, 0, 4),
            (1, -1, 4), (1, trace.VM_LIMIT, 1),
            (1, trace.VM_LIMIT - 1, 2), (1, 0, 0), (1, 0, 33),
            (True, 0, 4), (1, True, 4), (1, 0, True),
        ]
        for base, address, size in invalid:
            with self.subTest(base=base, address=address, size=size):
                with self.assertRaisesRegex(ValueError, "invalid guest observation range"):
                    trace.guest_read(memory, base, address, size)
        self.assertEqual(memory.calls, [])


class SamplerTests(unittest.TestCase):
    def test_optional_input_hook_failure_still_closes_read_only_handle(self):
        process=Process([None])
        action=mock.Mock(side_effect=RuntimeError("synthetic action failure"))
        with mock.patch.object(trace,"OwnedProcessReader",return_value=self.reader), \
             mock.patch.object(trace.time,"monotonic",side_effect=[0,0]):
            with self.assertRaisesRegex(RuntimeError,"synthetic action failure"):
                trace.wait_with_trace(process,2,self.folder,on_poll=action)
        action.assert_called_once_with()
        self.assertTrue(self.reader.closed)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        (self.folder / "boot.log").write_text("[startup-trace] vm_base=0x100000000 limit=0xE0000000\n")
        self.reader = Reader()

    def run_sampler(self, process, times, samples):
        with mock.patch.object(trace, "OwnedProcessReader", return_value=self.reader) as factory, \
             mock.patch.object(trace, "sample", side_effect=samples) as sample, \
             mock.patch.object(trace.time, "monotonic", side_effect=times), \
             mock.patch.object(trace.time, "sleep") as sleep:
            result = trace.wait_with_trace(process, 2, self.folder)
        factory.assert_called_once_with(process.pid)
        self.assertTrue(self.reader.closed)
        self.assertEqual(sample.call_count, len(samples))
        self.assertEqual(sleep.call_count, len(samples))
        return result, [json.loads(line) for line in (self.folder / "startup-memory.jsonl").read_text().splitlines()]

    def test_changed_samples_and_one_second_heartbeat(self):
        process = Process([None, None, None, None, 86])
        result, records = self.run_sampler(process, [0, 0, .2, .4, 1.1],
                                           [{"state": 0}, {"state": 0},
                                            {"state": 1}, {"state": 1}])
        self.assertEqual(result, 86)
        self.assertEqual([r["elapsed_seconds"] for r in records], [0, .4])
        self.assertEqual([r["values"] for r in records], [{"state": 0}, {"state": 1}])

        process = Process([None, None, None, 86])
        _, records = self.run_sampler(process, [0, 0, .5, 1.01],
                                      [{"state": 1}] * 3)
        self.assertEqual([r["elapsed_seconds"] for r in records], [0, 1.01])

    def test_timeout_closes_owned_reader_without_real_wait(self):
        process = Process([None])
        with mock.patch.object(trace, "OwnedProcessReader", return_value=self.reader), \
             mock.patch.object(trace, "sample") as sample, \
             mock.patch.object(trace.time, "monotonic", side_effect=[0, 2]), \
             mock.patch.object(trace.time, "sleep") as sleep:
            with self.assertRaises(subprocess.TimeoutExpired):
                trace.wait_with_trace(process, 2, self.folder)
        self.assertTrue(self.reader.closed)
        sample.assert_not_called()
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
