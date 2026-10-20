"""Synthetic startup dialog gates. No native windows or child processes."""
from pathlib import Path
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import native_dialog_scenario as dialog


class FakeProcess:
    pid = 1234

    def __init__(self, running=True):
        self.running = running

    def poll(self):
        return None if self.running else 86


class FakeBackend:
    def __init__(self):
        self.identity = 42
        self.generation_value = 1
        self.windows = [10]
        self.owners = {10: 1234, 20: 1234, 30: 1234}
        self.classes = {10: dialog.WINDOW_CLASS, 20: "Button", 30: "Static"}
        self.texts = {10: dialog.WINDOW_TITLE, 20: "Cancel", 30: dialog.MESSAGE}
        self.parents = {20: 10, 30: 10}
        self.visible_items = {10, 20, 30}
        self.enabled_items = {10, 20, 30}
        self.clicks = []
        self.raise_on_click = False

    def creation_identity(self, process):
        return self.identity

    def windows_for_pid(self, pid):
        return list(self.windows)

    def pid(self, hwnd):
        return self.owners.get(hwnd, 0)

    def class_name(self, hwnd):
        return self.classes.get(hwnd, "")

    def text(self, hwnd):
        return self.texts.get(hwnd, "")

    child_text = text

    def item(self, hwnd, item_id):
        return {dialog.BUTTON_ID: 20, dialog.MESSAGE_ID: 30}.get(item_id, 0)

    def parent(self, hwnd):
        return self.parents.get(hwnd, 0)

    def generation(self, hwnd):
        return self.generation_value

    def visible(self, hwnd):
        return hwnd in self.visible_items

    def enabled(self, hwnd):
        return hwnd in self.enabled_items

    def click(self, hwnd):
        self.clicks.append(hwnd)
        if self.raise_on_click:
            raise TimeoutError("send timed out")
        return 0


class DialogScenarioTests(unittest.TestCase):
    def run_case(self, change=None, expected="rejected"):
        process, backend = FakeProcess(), FakeBackend()
        if change:
            change(process, backend)
        with tempfile.TemporaryDirectory() as folder:
            scenario = dialog.StartupDialogScenario(process, folder, backend)
            self.assertEqual(scenario.tick(), expected)
            self.assertEqual(scenario.tick(), expected)
            record = json.loads((Path(folder) / "startup-dialog-input.json").read_text(encoding="utf-8"))
            self.assertEqual(record["status"], expected)
            self.assertEqual(record["actions"][-1]["result"], "armed" if expected == "waiting" else expected)
            json.dumps(scenario.summary())
            return backend.clicks, record

    def test_valid_target_gets_exactly_one_send(self):
        clicks, record = self.run_case(expected="sent")
        self.assertEqual(clicks, [20])
        self.assertTrue(record["attempted"])
        self.assertEqual(record["actions"][-1]["detail"]["before_text"], dialog.MESSAGE)

    def test_stopped_child(self):
        clicks, _ = self.run_case(lambda p, b: setattr(p, "running", False), "stopped")
        self.assertFalse(clicks)

    def test_wrong_pid_is_ignored(self):
        clicks, record = self.run_case(lambda p, b: b.owners.__setitem__(10, 999), "waiting")
        self.assertFalse(clicks)
        self.assertEqual(record["actions"][-1]["result"], "armed")

    def test_wrong_class_is_ignored(self):
        clicks, _ = self.run_case(lambda p, b: b.classes.__setitem__(10, "Other"), "waiting")
        self.assertFalse(clicks)

    def test_wrong_title_rejected(self):
        clicks, _ = self.run_case(lambda p, b: b.texts.__setitem__(10, "Other"))
        self.assertFalse(clicks)

    def test_wrong_message_rejected(self):
        clicks, _ = self.run_case(lambda p, b: b.texts.__setitem__(30, dialog.MESSAGE + " "))
        self.assertFalse(clicks)

    def test_crlf_only_normalization(self):
        clicks, _ = self.run_case(lambda p, b: b.texts.__setitem__(30, dialog.MESSAGE.replace("\n", "\r\n")), "sent")
        self.assertEqual(clicks, [20])

    def test_hidden_button_rejected(self):
        clicks, _ = self.run_case(lambda p, b: b.visible_items.remove(20))
        self.assertFalse(clicks)

    def test_disabled_button_rejected(self):
        clicks, _ = self.run_case(lambda p, b: b.enabled_items.remove(20))
        self.assertFalse(clicks)

    def test_multiple_dialogs_rejected(self):
        def change(p, b):
            b.windows.append(11)
            b.owners[11] = 1234
            b.classes[11] = dialog.WINDOW_CLASS
        clicks, _ = self.run_case(change)
        self.assertFalse(clicks)

    def test_recycled_process_identity_rejected(self):
        process, backend = FakeProcess(), FakeBackend()
        with tempfile.TemporaryDirectory() as folder:
            scenario = dialog.StartupDialogScenario(process, folder, backend)
            backend.identity = 43
            self.assertEqual(scenario.tick(), "rejected")
            self.assertFalse(backend.clicks)

    def test_control_pid_and_parent_must_match(self):
        for change in (lambda p, b: b.owners.__setitem__(20, 999),
                       lambda p, b: b.parents.__setitem__(20, 99)):
            with self.subTest(change=change):
                clicks, _ = self.run_case(change)
                self.assertFalse(clicks)

    def test_timeout_unknown_never_retries(self):
        clicks, record = self.run_case(lambda p, b: setattr(b, "raise_on_click", True), "unknown")
        self.assertEqual(clicks, [20])
        self.assertTrue(record["attempted"])


if __name__ == "__main__":
    unittest.main()
