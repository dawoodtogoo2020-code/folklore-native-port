import hashlib
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import pinned_tools as tools


class PinnedToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.file = self.root / "tools/pinned/revision/a.py"
        self.file.parent.mkdir(parents=True)
        self.file.write_bytes(b"synthetic\n")
        self.pins = {"revision": {"a.py": hashlib.sha256(b"synthetic\n").hexdigest()}}
        self.patcher = patch.object(tools, "PINS", self.pins)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_exact_bytes_and_fingerprint(self):
        self.assertEqual(tools.read_pinned_tool(self.root, "revision", "a.py"), b"synthetic\n")
        self.assertEqual(tools.pinned_fingerprints(self.root),
                         {"pinned:revision:tools/a.py": self.pins["revision"]["a.py"]})

    def test_unsupported_pairs_reject_before_file_access(self):
        with patch.object(Path, "lstat", side_effect=AssertionError("unexpected access")):
            for revision, name in [("../revision", "a.py"), ("revision", "../a.py"),
                                   ([], "a.py"), ("revision", {})]:
                with self.subTest(revision=revision, name=name), self.assertRaises(ValueError):
                    tools.read_pinned_tool(self.root, revision, name)

    def test_tampered_bytes_rejected(self):
        self.file.write_bytes(b"modified\n")
        with self.assertRaisesRegex(ValueError, "hash"):
            tools.read_pinned_tool(self.root, "revision", "a.py")

    def test_oversized_file_rejected_before_read(self):
        with patch.object(tools, "MAX_BYTES", 2), patch.object(Path, "open") as opening:
            with self.assertRaisesRegex(ValueError, "size"):
                tools.read_pinned_tool(self.root, "revision", "a.py")
            opening.assert_not_called()

    def test_directory_and_missing_file_rejected(self):
        self.file.unlink()
        with self.assertRaises(FileNotFoundError):
            tools.read_pinned_tool(self.root, "revision", "a.py")
        self.file.mkdir()
        with self.assertRaisesRegex(ValueError, "regular"):
            tools.read_pinned_tool(self.root, "revision", "a.py")

    def test_parent_reparse_rejected_before_read(self):
        original = Path.lstat
        def fake(path, *args, **kwargs):
            if path == self.root:
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
            return original(path, *args, **kwargs)
        with patch.object(Path, "lstat", fake), patch.object(Path, "open") as opening:
            with self.assertRaisesRegex(ValueError, "reparse"):
                tools.read_pinned_tool(self.root, "revision", "a.py")
            opening.assert_not_called()


if __name__ == "__main__":
    unittest.main()
