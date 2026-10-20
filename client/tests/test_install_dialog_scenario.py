"""Synthetic opt-in install-complete dialog tests; never enumerate real UI."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from test_native_dialog_scenario import FakeBackend, FakeProcess
import native_dialog_scenario as dialog


COMPLETE = "Game data creation on the HDD complete."


class InstallCompleteDialogTests(unittest.TestCase):
    def scenario(self, folder, process=None, backend=None, stage="install-complete"):
        return dialog.StartupDialogScenario(
            process or FakeProcess(), folder, backend or FakeBackend(), stage=stage)

    def test_completion_exactly_one_send_and_distinct_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            backend = FakeBackend()
            backend.texts[30] = COMPLETE
            backend.generation_value = 2
            scenario = self.scenario(folder, backend=backend)
            self.assertEqual(scenario.tick(), "sent")
            self.assertEqual(scenario.tick(), "sent")
            self.assertEqual(backend.clicks, [20])
            path = Path(folder) / "install-complete-dialog-input.json"
            self.assertTrue(path.is_file())
            self.assertFalse((Path(folder) / "startup-dialog-input.json").exists())
            receipt = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(receipt["stage"], "install-complete")
            self.assertEqual(receipt["status"], "sent")
            self.assertEqual(receipt["actions"][-1]["detail"]["before_text"], COMPLETE)
            self.assertEqual(receipt["actions"][-1]["expected_before_text"], COMPLETE)
            json.dumps(scenario.summary())

    def test_default_startup_rejects_completion_notice(self):
        with tempfile.TemporaryDirectory() as folder:
            backend = FakeBackend()
            backend.texts[30] = COMPLETE
            backend.generation_value = 2
            scenario = self.scenario(folder, backend=backend, stage="startup")
            self.assertEqual(scenario.tick(), "rejected")
            self.assertFalse(backend.clicks)
            self.assertTrue((Path(folder) / "startup-dialog-input.json").is_file())

    def test_second_stage_rejects_initial_or_modified_message(self):
        for message in (dialog.MESSAGE, COMPLETE + " ", COMPLETE + "\n"):
            with self.subTest(message=message), tempfile.TemporaryDirectory() as folder:
                backend = FakeBackend()
                backend.texts[30] = message
                backend.generation_value = 2
                scenario = self.scenario(folder, backend=backend)
                self.assertEqual(scenario.tick(), "rejected")
                self.assertFalse(backend.clicks)

    def test_invalid_stage_cannot_arm(self):
        with tempfile.TemporaryDirectory() as folder:
            for stage in ("", "other", "install_complete", None):
                with self.subTest(stage=stage):
                    with self.assertRaises(ValueError):
                        self.scenario(folder, stage=stage)

    def test_stopped_or_recycled_child_never_sends(self):
        for stopped in (True, False):
            with self.subTest(stopped=stopped), tempfile.TemporaryDirectory() as folder:
                process, backend = FakeProcess(), FakeBackend()
                backend.texts[30] = COMPLETE
                backend.generation_value = 2
                scenario = self.scenario(folder, process, backend)
                if stopped:
                    process.running = False
                    self.assertEqual(scenario.tick(), "stopped")
                else:
                    backend.identity += 1
                    self.assertEqual(scenario.tick(), "rejected")
                self.assertFalse(backend.clicks)

    def test_same_message_on_later_generation_never_sends(self):
        with tempfile.TemporaryDirectory() as folder:
            backend = FakeBackend()
            backend.texts[30] = COMPLETE
            backend.generation_value = 3
            scenario = self.scenario(folder, backend=backend)
            self.assertEqual(scenario.tick(), "rejected")
            self.assertFalse(backend.clicks)

    def test_generation_change_between_target_checks_never_sends(self):
        with tempfile.TemporaryDirectory() as folder:
            backend = FakeBackend()
            backend.texts[30] = COMPLETE
            generations = iter((2, 3))
            backend.generation = lambda hwnd: next(generations)
            scenario = self.scenario(folder, backend=backend)
            self.assertEqual(scenario.tick(), "rejected")
            self.assertFalse(backend.clicks)

    def test_unknown_delivery_is_terminal_without_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            backend = FakeBackend()
            backend.texts[30] = COMPLETE
            backend.generation_value = 2
            backend.raise_on_click = True
            scenario = self.scenario(folder, backend=backend)
            self.assertEqual(scenario.tick(), "unknown")
            self.assertEqual(scenario.tick(), "unknown")
            self.assertEqual(backend.clicks, [20])
            receipt = json.loads((Path(folder) / "install-complete-dialog-input.json").read_text(encoding="utf-8"))
            self.assertTrue(receipt["attempted"])
            self.assertEqual(receipt["status"], "unknown")

    def test_two_explicit_stages_keep_independent_receipts(self):
        with tempfile.TemporaryDirectory() as folder:
            process, backend = FakeProcess(), FakeBackend()
            first = self.scenario(folder, process, backend, stage="startup")
            self.assertEqual(first.tick(), "sent")
            backend.texts[30] = COMPLETE
            backend.generation_value = 2
            second = self.scenario(folder, process, backend)
            self.assertEqual(second.tick(), "sent")
            self.assertEqual(backend.clicks, [20, 20])
            initial = json.loads((Path(folder) / "startup-dialog-input.json").read_text(encoding="utf-8"))
            final = json.loads((Path(folder) / "install-complete-dialog-input.json").read_text(encoding="utf-8"))
            self.assertEqual(initial["actions"][-1]["detail"]["before_text"], dialog.MESSAGE)
            self.assertEqual(final["actions"][-1]["detail"]["before_text"], COMPLETE)


if __name__ == "__main__":
    unittest.main()
