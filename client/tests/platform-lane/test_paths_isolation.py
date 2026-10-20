"""Synthetic filesystem/process contracts for independently rooted recipes."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import build_native_boot as builder
import run_native_boot as runner

OUTPUT_ROOT = Path(os.environ["FOLKLORE_TEST_OUTPUT_ROOT"])


def directory_alias(source, destination):
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(source), str(destination))
    else:
        destination.symlink_to(source, target_is_directory=True)


class PathIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="path-fixture-", dir=OUTPUT_ROOT)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        assert self.base.is_relative_to(OUTPUT_ROOT.resolve())
        self.config = builder.host_config("posix", root=self.base / "task", available=lambda p: True,
            source_root=self.base / "source", sdk_root=self.base / "sdk",
            lift_root=self.base / "lift", dependency_root=self.base / "dependencies",
            input_root=self.base / "disc", firmware_root=self.base / "firmware",
            output_root=self.base / "output")
        for name in ("root", "source_root", "sdk", "lift", "prefix", "input", "firmware"):
            self.config[name].mkdir(parents=True, exist_ok=True)
        self.write(self.config["native"] / "CMakeLists.txt", b"synthetic project")
        self.write(self.config["native"] / "host.cpp", b"source before")
        self.write(self.config["lift"] / "ppu_recomp.h", b"synthetic lift ABI")
        self.write(self.config["lift"] / "ppu_recomp_000.cpp", b"strict unsupported instruction at\n" * 8204)
        self.write(self.config["sdk"] / "runtime/core.c", b"synthetic runtime")
        for name in ("runtime/ppu/ppu_strict_boot.h", "include/ps3emu/strict_boot.h", "tools/ppu_lifter.py"):
            self.write(self.config["sdk"] / name, b"strict source")
        self.elf = self.config["input"] / "PS3_GAME/USRDIR/EBOOT.elf"
        self.write(self.elf, b"synthetic game input, never executed")

    def write(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def tree(self):
        return {str(p.relative_to(self.base)): p.read_bytes() for p in self.base.rglob("*") if p.is_file()}

    def test_build_and_launch_plans_are_read_only_and_use_independent_roots(self):
        before = self.tree()
        with mock.patch.object(subprocess, "run", side_effect=AssertionError("process invoked")), \
             mock.patch.object(subprocess, "Popen", side_effect=AssertionError("target invoked")), \
             mock.patch.object(Path, "mkdir", side_effect=AssertionError("directory created")):
            build = builder.build_plan(self.config)
            launch = runner.run(5, config=self.config, plan=True)
        self.assertEqual(self.tree(), before)
        self.assertFalse(self.config["output_root"].exists())
        command = build["commands"][0]
        self.assertEqual(command[command.index("-S") + 1], str(self.config["native"]))
        self.assertIn("-DPS3RECOMP_DIR=" + self.config["sdk"].as_posix(), command)
        self.assertIn("-DRECOMP_DIR=" + self.config["lift"].as_posix(), command)
        self.assertEqual(launch["command"][1], str(self.elf))
        self.assertEqual(launch["command"][4], str(self.config["firmware"]))
        self.assertTrue(Path(launch["command"][3]).is_relative_to(self.config["output_root"]))
        self.assertFalse(launch["source_binary_verified"])

    def test_help_and_bad_arguments_cannot_access_configuration_or_processes(self):
        for module in (builder, runner):
            with mock.patch.object(module, "config_from_arguments", side_effect=AssertionError("config accessed")), \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as result:
                    module.main(["--help"])
                self.assertEqual(result.exception.code, 0)
                with self.assertRaises(SystemExit) as result:
                    module.main(["--unknown-option"])
                self.assertEqual(result.exception.code, 2)

    def test_cli_defaults_to_plan_and_requires_deliberate_output_layout(self):
        args = ["--root", str(self.config["root"]), "--source-root", str(self.config["source_root"]),
                "--sdk-root", str(self.config["sdk"]), "--lift-root", str(self.config["lift"]),
                "--input-root", str(self.config["input"]), "--firmware-root", str(self.config["firmware"]),
                "--output-root", str(self.config["output_root"])]
        with mock.patch.object(subprocess, "run", side_effect=AssertionError("process invoked")), \
             mock.patch.object(subprocess, "Popen", side_effect=AssertionError("target invoked")):
            for module in (builder, runner):
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(module.main(args), 0)
                self.assertIs(json.loads(output.getvalue())["executes"], False)
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(module.main([]), 2)
        self.assertFalse(self.config["output_root"].exists())

    def test_protected_and_shared_outputs_reject_before_writes(self):
        candidates = [self.config["sdk"], self.config["source_root"] / "nested",
                      self.base, self.config["input"] / ".." / "disc",
                      self.config["work"] / "build-folklore-gnu",
                      self.config["work"] / "native-runs/new"]
        for candidate in candidates:
            config = dict(self.config, output_root=candidate)
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                builder.build_plan(config)

    def test_real_directory_aliases_to_input_and_escaped_tmp_reject(self):
        alias = self.config["output_root"]
        directory_alias(self.config["sdk"], alias)
        with self.assertRaises(ValueError):
            builder.build_plan(self.config)
        os.rmdir(alias) if os.name == "nt" else alias.unlink()
        alias.mkdir()
        temporary = self.config["temporary"]
        directory_alias(self.config["sdk"], temporary)
        with self.assertRaises(ValueError):
            builder.build_plan(self.config)
        os.rmdir(temporary) if os.name == "nt" else temporary.unlink()
        self.assertEqual((self.config["sdk"] / "runtime/core.c").read_bytes(), b"synthetic runtime")

    def test_mutable_binary_hardlink_rejected_with_input_preserved(self):
        binary = self.config["build"] / self.config["binary_name"]
        binary.parent.mkdir(parents=True)
        binary.hardlink_to(self.elf)
        with self.assertRaisesRegex(ValueError, "unaliased"):
            builder.build_plan(self.config)
        self.assertEqual(self.elf.read_bytes(), b"synthetic game input, never executed")

    def test_relative_missing_and_file_paths_reject(self):
        for name, value in (("output_root", Path("relative")), ("sdk", self.base / "absent"),
                            ("source_root", self.elf), ("output_root", self.elf)):
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                builder.build_plan(dict(self.config, **{name: value}))
        self.assertFalse(self.config["output_root"].exists())

    def test_source_manifest_binds_external_sdk_source_lift_and_live_recipes(self):
        with mock.patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout=b"runtime/core.c\0")):
            before = builder.source_state(self.config)
            changed = self.config["native"] / "host.cpp"
            changed.write_bytes(b"source after")
            after = builder.source_state(self.config)
        for path in (changed, self.config["sdk"] / "runtime/core.c", self.config["lift"] / "ppu_recomp.h",
                     Path(builder.__file__).resolve()):
            self.assertIn(str(path.resolve()), before)
        self.assertNotEqual(before[str(changed.resolve())], after[str(changed.resolve())])

    def test_mocked_build_uses_owned_outputs_and_binds_exact_paths(self):
        fake_tool = self.base / "fake-tool"
        fake_tool.write_bytes(b"tool hash fixture, never executed")
        self.config["tools"] = {name: fake_tool for name in ("cc", "cxx", "cmake", "ninja", "git")}
        calls = []
        def process(command, **kwargs):
            if "ls-files" in command:
                return subprocess.CompletedProcess(command, 0, stdout=b"runtime/core.c\0")
            calls.append((command, kwargs))
            self.write(self.config["build"] / self.config["binary_name"], b"synthetic binary")
            self.write(self.config["build"] / "compile_commands.json", b"[]")
            self.write(self.config["build"] / "CMakeCache.txt", b"synthetic cache")
            return subprocess.CompletedProcess(command, 0)
        with mock.patch.object(subprocess, "run", side_effect=process), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(builder.build(self.config), 0)
        manifest = json.loads((self.config["build"] / "build-manifest.json").read_text())
        self.assertEqual(manifest["paths"], builder.path_bindings(self.config))
        self.assertEqual(len(calls), 2)
        for _, options in calls:
            self.assertEqual(options["cwd"], self.config["source_root"])
            self.assertEqual(options["env"]["TMPDIR"], str(self.config["temporary"]))
        self.assertFalse((self.config["work"] / "evidence").exists())

    def test_explicit_launch_rejects_legacy_manifest_or_changed_path_binding(self):
        binary = self.config["build"] / self.config["binary_name"]
        self.write(binary, b"synthetic binary")
        manifest = {"schema": "folklore-build-manifest-v1", "host_platform": "posix",
                    "binary_sha256": runner.sha(binary), "sources": {"fixture": "bound"}}
        with mock.patch.object(runner, "ELF_SHA", runner.sha(self.elf)), \
             mock.patch.object(runner, "source_state", return_value={"fixture": "bound"}), \
             mock.patch.object(runner.os, "access", return_value=True), \
             mock.patch.object(subprocess, "Popen", side_effect=AssertionError("target invoked")):
            for paths in (None, {**builder.path_bindings(self.config), "sdk": "wrong"}):
                if paths is not None:
                    manifest["paths"] = paths
                self.write(binary.parent / "build-manifest.json", json.dumps(manifest).encode())
                with self.assertRaisesRegex(ValueError, "stale"):
                    runner.run(1, config=self.config)
        self.assertFalse(self.config["runs"].exists())

    def test_legacy_layout_requires_deliberate_source_selection(self):
        config = builder.host_config("nt", root=self.config["root"])
        self.assertEqual(config["source_root"], Path(builder.__file__).resolve().parents[1])
        with self.assertRaisesRegex(ValueError, "explicit output"):
            builder.build_plan(config)
        config = builder.host_config("nt", root=self.config["root"],
                                     source_root=self.config["root"] / "outputs/folklore-native")
        self.write(config["native"] / "CMakeLists.txt", b"legacy main fixture")
        self.write(config["lift"] / "ppu_recomp.h", b"legacy lift fixture")
        config["sdk"].mkdir(parents=True)
        plan = builder.build_plan(config)
        self.assertEqual(Path(plan["paths"]["build"]), config["work"] / "build-folklore-gnu")
        with self.assertRaisesRegex(ValueError, "explicit output"):
            builder.build_plan(dict(config, source_root=self.config["source_root"], native=self.config["native"]))

    def test_positive_isolated_mock_launch_uses_exact_sdk_inputs_and_run_outputs(self):
        binary = self.config["build"] / self.config["binary_name"]
        self.write(binary, b"synthetic binary, not executed")
        manifest = {"schema": "folklore-build-manifest-v1", "host_platform": "posix",
                    "binary_sha256": runner.sha(binary), "sources": {"fixture": "bound"},
                    "paths": builder.path_bindings(self.config)}
        self.write(binary.parent / "build-manifest.json", json.dumps(manifest).encode())
        class Process:
            pid = 123
            returncode = None
            def wait(self, timeout):
                self.returncode = 86
                return 86
            def poll(self):
                return self.returncode
        with mock.patch.object(runner, "ELF_SHA", runner.sha(self.elf)), \
             mock.patch.object(runner, "source_state", return_value={"fixture": "bound"}), \
             mock.patch.object(runner.os, "access", return_value=True), \
             mock.patch.object(subprocess, "run", side_effect=[
                 subprocess.CompletedProcess([], 0, stdout="sdk commit"),
                 subprocess.CompletedProcess([], 0, stdout=b"synthetic patch")]) as git, \
             mock.patch.object(subprocess, "Popen", return_value=Process()) as launch, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runner.run(1, config=self.config), 1)
        for call in git.call_args_list:
            self.assertEqual(call.kwargs["cwd"], self.config["sdk"])
        folder = launch.call_args.kwargs["cwd"]
        self.assertTrue(folder.is_relative_to(self.config["output_root"]))
        self.assertEqual(launch.call_args.args[0][1], str(self.elf))
        self.assertEqual(launch.call_args.args[0][4], str(self.config["firmware"]))
        receipt = json.loads((folder / "receipt.json").read_text())
        self.assertEqual(receipt["paths"], manifest["paths"])
        self.assertIn(str((self.config["sdk"] / "tools/ppu_lifter.py").resolve()), receipt["sha256"])
        self.assertFalse(receipt["playable_game"])
        self.assertEqual(launch.call_args.kwargs["env"]["PYTHONDONTWRITEBYTECODE"], "1")


if __name__ == "__main__":
    unittest.main()
