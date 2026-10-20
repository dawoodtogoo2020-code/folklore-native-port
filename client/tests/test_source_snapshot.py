import hashlib
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "source_snapshot.py"
_spec = importlib.util.spec_from_file_location("source_snapshot", SCRIPT)
snapshot = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(snapshot)

SnapshotError = snapshot.SourceSnapshotError
REPARSE = 0x400


def sha(data):
    return hashlib.sha256(data).hexdigest()


def norm(path):
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def tree_state(base):
    state = {}
    for dirpath, dirnames, filenames in os.walk(base):
        for name in dirnames + filenames:
            info = os.lstat(os.path.join(dirpath, name))
            state[os.path.join(dirpath, name)] = (info.st_size, info.st_mtime_ns)
    return state


class FakeStat:
    def __init__(self, real, **changes):
        for name in ("st_mode", "st_dev", "st_ino", "st_size", "st_mtime_ns"):
            setattr(self, name, getattr(real, name))
        self.st_file_attributes = getattr(real, "st_file_attributes", 0)
        for name, value in changes.items():
            setattr(self, name, value)


class SourceSnapshotTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        # realpath so platform temp links (e.g. macOS /var) are not rejected.
        self.base = Path(os.path.realpath(tmp.name))
        self.engine = self.base / "engine"
        self.tools = self.base / "tools"
        write(self.engine / "src" / "main.cpp", b"int main() { return 0; }\n")
        write(self.engine / ".clang-format", b"BasedOnStyle: LLVM\n")
        write(self.engine / "scripts" / "tool.py", b"print('hi')\n")
        write(self.engine / ".git" / "HEAD", b"ref: refs/heads/main\n")
        write(self.engine / "src" / "__pycache__" / "m.cpython-311.pyc", b"\0")
        write(self.engine / "stale.pyc", b"\0")
        write(self.tools / "README.md", b"# tools\n")
        self.roots = {"engine": self.engine, "tools": self.tools}

    def make_symlink(self, target, link, is_dir):
        try:
            os.symlink(target, link, target_is_directory=is_dir)
        except (OSError, NotImplementedError, AttributeError) as exc:
            self.skipTest(f"symlinks unavailable: {exc}")

    def test_inventory_hashes_sorted_relative_keys(self):
        result = snapshot.snapshot_sources(self.roots)
        self.assertEqual(result, {
            "engine/.clang-format": sha(b"BasedOnStyle: LLVM\n"),
            "engine/scripts/tool.py": sha(b"print('hi')\n"),
            "engine/src/main.cpp": sha(b"int main() { return 0; }\n"),
            "tools/README.md": sha(b"# tools\n"),
        })
        self.assertEqual(list(result), sorted(result))
        for key in result:
            self.assertNotIn("\\", key)
            self.assertNotIn(str(self.base), key)

    def test_deterministic_and_read_only(self):
        before = tree_state(self.base)
        first = snapshot.snapshot_sources(self.roots)
        second = snapshot.snapshot_sources(dict(reversed(list(self.roots.items()))))
        self.assertEqual(list(first.items()), list(second.items()))
        self.assertEqual(tree_state(self.base), before)

    def test_changes_alter_fingerprint(self):
        first = snapshot.snapshot_sources(self.roots)
        write(self.engine / "src" / "main.cpp", b"int main() { return 1; }\n")
        write(self.tools / "untracked.txt", b"new\n")
        second = snapshot.snapshot_sources(self.roots)
        self.assertNotEqual(first["engine/src/main.cpp"], second["engine/src/main.cpp"])
        self.assertEqual(second["tools/untracked.txt"], sha(b"new\n"))

    def test_sibling_roots_with_shared_prefix_allowed(self):
        write(self.base / "engine-tools" / "a.txt", b"a")
        result = snapshot.snapshot_sources(
            {"engine": self.engine, "extra": self.base / "engine-tools"})
        self.assertIn("extra/a.txt", result)

    def test_invalid_labels_rejected(self):
        for label in ["", ".", "..", "a/b", "a\\b", "c:x", 5]:
            with self.subTest(label=label):
                with self.assertRaises(SnapshotError):
                    snapshot.snapshot_sources({label: self.engine})
        with self.assertRaises(SnapshotError):
            snapshot.snapshot_sources({"Engine": self.engine, "engine": self.tools})

    def test_invalid_roots_rejected(self):
        cases = {
            "empty": {},
            "missing": {"x": self.base / "missing"},
            "file": {"x": self.tools / "README.md"},
            "duplicate": {"a": self.engine, "b": self.engine},
            "nested": {"a": self.engine, "b": self.engine / "src"},
            "parent": {"a": self.engine / "src", "b": self.engine},
        }
        for name, roots in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(SnapshotError):
                    snapshot.snapshot_sources(roots)

    def test_case_folded_name_collision_rejected(self):
        write(self.tools / "Readme.txt", b"a")
        write(self.tools / "README.txt", b"b")
        if len([n for n in os.listdir(self.tools) if n.lower() == "readme.txt"]) != 2:
            self.skipTest("case-insensitive file system")
        with self.assertRaises(SnapshotError):
            snapshot.snapshot_sources({"tools": self.tools})

    def test_symlinks_rejected(self):
        root_link = self.base / "engine-link"
        self.make_symlink(self.engine, root_link, True)
        with self.assertRaises(SnapshotError):
            snapshot.snapshot_sources({"e": root_link})
        with self.assertRaises(SnapshotError):
            snapshot.snapshot_sources({"e": root_link / "src"})
        os.unlink(root_link)

        dir_link = self.engine / "linked"
        self.make_symlink(self.tools, dir_link, True)
        with self.assertRaises(SnapshotError):
            snapshot.snapshot_sources({"engine": self.engine})
        os.unlink(dir_link)

        file_link = self.tools / "link.md"
        self.make_symlink(self.tools / "README.md", file_link, False)
        with self.assertRaises(SnapshotError):
            snapshot.snapshot_sources({"tools": self.tools})

    @unittest.skipUnless(hasattr(os, "mkfifo"), "mkfifo unavailable")
    def test_special_file_rejected(self):
        os.mkfifo(self.tools / "pipe")
        with self.assertRaises(SnapshotError):
            snapshot.snapshot_sources({"tools": self.tools})

    def run_with_reparse(self, marked, roots):
        real_lstat, real_scandir = os.lstat, os.scandir
        scanned = []

        def fake_lstat(path):
            info = real_lstat(path)
            if norm(path) == norm(marked):
                return FakeStat(info, st_file_attributes=REPARSE)
            return info

        def fake_scandir(path):
            scanned.append(norm(path))
            return real_scandir(path)

        with mock.patch.object(snapshot.os, "lstat", new=fake_lstat), \
                mock.patch.object(snapshot.os, "scandir", new=fake_scandir):
            with self.assertRaises(SnapshotError):
                snapshot.snapshot_sources(roots)
        return scanned

    def test_windows_reparse_directory_rejected_before_scandir(self):
        junction = self.engine / "junction"
        write(junction / "inner.txt", b"x")
        scanned = self.run_with_reparse(junction, {"engine": self.engine})
        self.assertNotIn(norm(junction), scanned)

    def test_windows_reparse_root_or_parent_rejected_before_scandir(self):
        self.assertEqual(self.run_with_reparse(self.engine, {"e": self.engine}), [])
        self.assertEqual(
            self.run_with_reparse(self.engine, {"e": self.engine / "src"}), [])

    def test_file_growing_during_hash_rejected(self):
        real_fstat = os.fstat
        calls = []

        def fake_fstat(fd):
            info = real_fstat(fd)
            calls.append(fd)
            if len(calls) >= 2:
                return FakeStat(info, st_size=info.st_size + 1)
            return info

        with mock.patch.object(snapshot.os, "fstat", new=fake_fstat):
            with self.assertRaises(SnapshotError):
                snapshot.snapshot_sources({"tools": self.tools})

    def test_file_replaced_during_hash_rejected(self):
        target = norm(self.tools / "README.md")
        real_lstat = os.lstat
        seen = []

        def fake_lstat(path):
            info = real_lstat(path)
            if norm(path) == target:
                seen.append(path)
                if len(seen) >= 2:
                    return FakeStat(info, st_ino=info.st_ino + 1)
            return info

        with mock.patch.object(snapshot.os, "lstat", new=fake_lstat):
            with self.assertRaises(SnapshotError):
                snapshot.snapshot_sources({"tools": self.tools})
        self.assertGreaterEqual(len(seen), 2)


if __name__ == "__main__":
    unittest.main()
