"""Tests for the portable launcher entry point and its PyInstaller spec.

Acceptance groups:
- portable_unit_tests: PortableEntryPathTests, PortableArgvTests,
  PortableMainTests, PortableImportTests
- portable_compile: PortableCompileTests
"""

import importlib
import os
import re
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER_DIR = REPO_ROOT / "launcher"
ENTRY_PATH = LAUNCHER_DIR / "portable_entry.py"
SPEC_PATH = REPO_ROOT / "packaging" / "FolkloreLauncher.spec"

if str(LAUNCHER_DIR) not in sys.path:
    sys.path.insert(0, str(LAUNCHER_DIR))

import portable_entry  # noqa: E402


def _fake_launcher(return_value=0):
    return types.SimpleNamespace(
        __name__="folklore_launcher", main=mock.Mock(return_value=return_value)
    )


class _CwdMixin:
    def enter_temp_cwd(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        previous = os.getcwd()
        os.chdir(temp.name)
        self.addCleanup(os.chdir, previous)
        return Path(temp.name)


class PortableEntryPathTests(_CwdMixin, unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.exe_dir = Path(temp.name) / "GameDrive" / "Folklore"
        self.executable = self.exe_dir / "FolkloreLauncher.exe"

    def test_frozen_default_is_beside_executable(self):
        path = portable_entry.default_config_path(
            frozen=True, executable=str(self.executable)
        )
        self.assertEqual(path, Path(os.path.abspath(self.exe_dir)) / "settings.json")
        self.assertTrue(path.is_absolute())

    def test_frozen_detection_uses_sys_executable(self):
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "executable", str(self.executable)):
            self.assertTrue(portable_entry.is_frozen())
            path = portable_entry.default_config_path()
        self.assertEqual(path.parent, Path(os.path.abspath(self.exe_dir)))

    def test_source_default_is_beside_entry_file(self):
        path = portable_entry.default_config_path(frozen=False)
        self.assertEqual(path, LAUNCHER_DIR / "settings.json")

    def test_source_default_with_explicit_entry_file(self):
        entry = self.exe_dir / "portable_entry.py"
        path = portable_entry.default_config_path(frozen=False, entry_file=str(entry))
        self.assertEqual(path, Path(os.path.abspath(self.exe_dir)) / "settings.json")

    def test_default_ignores_cwd(self):
        cwd = self.enter_temp_cwd()
        source = portable_entry.default_config_path(frozen=False)
        frozen = portable_entry.default_config_path(
            frozen=True, executable=str(self.executable)
        )
        for path in (source, frozen):
            self.assertNotEqual(Path(os.path.abspath(path)).parent, Path(os.path.abspath(cwd)))

    def test_default_ignores_meipass(self):
        extraction = tempfile.TemporaryDirectory()
        self.addCleanup(extraction.cleanup)
        with mock.patch.object(sys, "_MEIPASS", extraction.name, create=True):
            path = portable_entry.default_config_path(
                frozen=True, executable=str(self.executable)
            )
        self.assertNotEqual(path.parent, Path(os.path.abspath(extraction.name)))
        self.assertEqual(path.parent, Path(os.path.abspath(self.exe_dir)))


class PortableArgvTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.executable = str(Path(temp.name) / "FolkloreLauncher.exe")
        self.default = str(Path(os.path.abspath(temp.name)) / "settings.json")

    def normalize(self, argv):
        return portable_entry.normalize_argv(
            argv, frozen=True, executable=self.executable
        )

    def test_empty_argv_gets_default_config(self):
        self.assertEqual(self.normalize([]), ["--config", self.default])

    def test_explicit_config_separate_form_preserved(self):
        argv = ["--config", "X:/custom/settings.json", "--check"]
        self.assertEqual(self.normalize(argv), argv)

    def test_explicit_config_equals_form_preserved(self):
        argv = ["--launch", "--config=X:/custom/settings.json"]
        self.assertEqual(self.normalize(argv), argv)

    def test_other_arguments_preserved_in_order(self):
        argv = ["--build-recipe", "X:/recipes/build.json", "--check"]
        self.assertEqual(self.normalize(argv), ["--config", self.default] + argv)

    def test_input_list_not_mutated(self):
        argv = ["--check"]
        self.normalize(argv)
        self.assertEqual(argv, ["--check"])

    def test_similar_flag_is_not_config(self):
        self.assertFalse(portable_entry.has_explicit_config(["--configuration", "x"]))
        self.assertTrue(portable_entry.has_explicit_config(["--config="]))

    def test_config_after_double_dash_not_counted(self):
        self.assertFalse(portable_entry.has_explicit_config(["--", "--config", "x"]))


class PortableMainTests(_CwdMixin, unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.executable = str(Path(temp.name) / "FolkloreLauncher.exe")
        self.default = str(Path(os.path.abspath(temp.name)) / "settings.json")
        self.fake = _fake_launcher(return_value=7)
        patcher = mock.patch.object(
            portable_entry.importlib, "import_module", return_value=self.fake
        )
        self.import_module = patcher.start()
        self.addCleanup(patcher.stop)

    def frozen(self):
        return mock.patch.multiple(
            sys, create=True, frozen=True, executable=self.executable
        )

    def test_main_imports_launcher_lazily_and_passes_normalized_argv(self):
        with self.frozen():
            result = portable_entry.main(["--check"])
        self.import_module.assert_called_once_with("folklore_launcher")
        self.fake.main.assert_called_once_with(["--config", self.default, "--check"])
        self.assertEqual(result, 7)

    def test_main_preserves_explicit_config(self):
        argv = ["--config=X:/elsewhere.json", "--launch"]
        with self.frozen():
            portable_entry.main(argv)
        self.fake.main.assert_called_once_with(argv)

    def test_main_defaults_to_sys_argv(self):
        with self.frozen(), mock.patch.object(
            sys, "argv", [self.executable, "--build-recipe", "X:/r.json"]
        ):
            portable_entry.main()
        self.fake.main.assert_called_once_with(
            ["--config", self.default, "--build-recipe", "X:/r.json"]
        )

    def test_main_source_mode_uses_entry_directory(self):
        self.enter_temp_cwd()
        with mock.patch.object(sys, "frozen", False, create=True):
            portable_entry.main([])
        self.fake.main.assert_called_once_with(
            ["--config", str(LAUNCHER_DIR / "settings.json")]
        )

    def test_main_never_spawns_processes(self):
        guards = [
            mock.patch.object(subprocess, "Popen", side_effect=AssertionError("Popen")),
            mock.patch.object(subprocess, "run", side_effect=AssertionError("run")),
            mock.patch.object(subprocess, "call", side_effect=AssertionError("call")),
            mock.patch.object(os, "system", side_effect=AssertionError("system")),
            mock.patch.object(os, "execv", side_effect=AssertionError("execv")),
            mock.patch.object(os, "execl", side_effect=AssertionError("execl")),
        ]
        for guard in guards:
            guard.start()
            self.addCleanup(guard.stop)
        with self.frozen():
            portable_entry.main(["--check"])
        self.fake.main.assert_called_once()

    def test_reentrant_main_is_rejected(self):
        def reenter(argv):
            return portable_entry.main(argv)

        self.fake.main.side_effect = reenter
        with self.frozen():
            with self.assertRaises(RuntimeError):
                portable_entry.main(["--check"])
        self.assertFalse(portable_entry._ACTIVE)

    def test_self_import_is_rejected(self):
        self.import_module.return_value = portable_entry
        with self.frozen():
            with self.assertRaises(RuntimeError):
                portable_entry.main([])


class PortableImportTests(_CwdMixin, unittest.TestCase):
    def test_import_has_no_side_effects(self):
        cwd = self.enter_temp_cwd()
        before_launcher = sorted(os.listdir(LAUNCHER_DIR))
        before_cwd = sorted(os.listdir(cwd))
        with mock.patch.dict(sys.modules), \
                mock.patch.object(sys, "dont_write_bytecode", True):
            sys.modules.pop("folklore_launcher", None)
            importlib.reload(portable_entry)
            self.assertNotIn("folklore_launcher", sys.modules)
        self.assertEqual(sorted(os.listdir(LAUNCHER_DIR)), before_launcher)
        self.assertEqual(sorted(os.listdir(cwd)), before_cwd)


class PortableCompileTests(unittest.TestCase):
    def test_entry_compiles(self):
        source = ENTRY_PATH.read_text(encoding="utf-8")
        compile(source, str(ENTRY_PATH), "exec")

    def test_spec_compiles(self):
        source = SPEC_PATH.read_text(encoding="utf-8")
        compile(source, str(SPEC_PATH), "exec")

    def test_spec_settings(self):
        source = SPEC_PATH.read_text(encoding="utf-8")
        self.assertRegex(source, r"name\s*=\s*['\"]FolkloreLauncher['\"]")
        self.assertRegex(source, r"runtime_tmpdir\s*=\s*None")
        self.assertRegex(source, r"console\s*=\s*False")
        self.assertIn("SPECPATH", source)
        self.assertIn("portable_entry.py", source)
        for module in ("folklore_launcher", "launcher_core", "launcher_build",
                       "launcher_display", "launcher_theme", "backup_validation"):
            self.assertIn(module, source)
        self.assertIn("PIL.ImageTk", source)
        self.assertIn("tkinter", source)

    def test_spec_has_no_absolute_developer_paths(self):
        source = SPEC_PATH.read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"\b[A-Za-z]:[\\/]", source))
        self.assertNotRegex(source, r"/(home|Users)/")


if __name__ == "__main__":
    unittest.main()
