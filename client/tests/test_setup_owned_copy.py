"""Synthetic tests for setup_owned_copy.py: no game data, compilers or GPU."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "setup_owned_copy.py"
_spec = importlib.util.spec_from_file_location("setup_owned_copy", SCRIPT)
setup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(setup)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fake_snapshot(roots):
    return {label: {p.relative_to(root).as_posix(): _sha(p) for p in sorted(Path(root).rglob("*")) if p.is_file()}
            for label, root in roots.items()}


def fake_plain_output(path):
    path = Path(path)
    for item in (path, *path.parents):
        if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
            raise ValueError(f"reparse point {item}")
    return path


def fake_validate_backup(disc, elf):
    if not (Path(disc) / "PARAM.SFO").is_file():
        raise ValueError("missing PARAM.SFO")
    return {"title_id": "SYNTH0001", "region": "USA", "version": "01.00", "elf_sha256": _sha(elf)}


def fake_default_config():
    return {"schema": 8, "paths": {"binary": "", "elf": "", "disc": "", "hdd0": "", "dev_flash": ""},
            "build": {}, "texture_pack_root": ""}


def fake_validate_config(config, check_paths=False):
    if config.get("schema") != 8 or not isinstance(config.get("paths"), dict):
        raise ValueError("invalid launcher schema")
    if check_paths and not Path(config["paths"]["hdd0"]).is_dir():
        raise ValueError("save profile is absent")
    return config


def _module(name, **attrs):
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    return module


def _opt(args, flag):
    return args[args.index(flag) + 1]


class FakeRunner:
    def __init__(self, build_exit=0, forge=None):
        self.calls, self.build_exit, self.forge = [], build_exit, forge or {}

    def __call__(self, args, **kwargs):
        self.calls.append((list(args), kwargs))
        kwargs["stdout"].write("synthetic log\n")
        out = Path(_opt(args, "--output"))
        if Path(args[2]).name == "prepare_owned_metadata.py":
            out.mkdir()
            (out / "symbols.json").write_text("{}", encoding="utf-8")
            receipt = {"state": "verified", "files": {"symbols.json": _sha(out / "symbols.json")}}
            (out / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
            return subprocess.CompletedProcess(args, 0)
        out.mkdir(exist_ok=True)
        checkpoint = {"state": "prepared"}
        if "--prepare-only" not in args:
            binary = out / "native-build" / "FolkloreBoot.exe"
            binary.parent.mkdir(exist_ok=True)
            binary.write_bytes(b"synthetic binary")
            checkpoint = {"state": "built", "binary": str(binary), "binary_sha256": _sha(binary)}
        checkpoint["build_source_snapshot"] = fake_snapshot(
            {"engine": Path(_opt(args, "--sdk")), "client": Path(_opt(args, "--source"))})
        checkpoint.update(self.forge)
        (out / "checkpoint.json").write_text(json.dumps(checkpoint), encoding="utf-8")
        return subprocess.CompletedProcess(args, self.build_exit)


class SetupOwnedCopyTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(os.path.realpath(tempfile.mkdtemp(prefix="owner's setup ")))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        root = self.base / "o'wner files"
        self.repo = root / "repo"
        for rel in ("engine/include/core.h", "client/scripts/prepare_owned_metadata.py",
                    "client/scripts/build_owned_backup.py", "client/launcher/portable_entry.py",
                    "client/build-support/ppu-acceptance.json", "client/build-support/spu-acceptance.json"):
            self.write(self.repo / rel, "synthetic\n")
        capabilities = SCRIPT.parents[1] / "native" / "launcher-capabilities.json"
        self.write(self.repo / "client/native/launcher-capabilities.json", capabilities.read_text(encoding="utf-8"))
        self.disc = root / "disc dir"
        self.write(self.disc / "PARAM.SFO", "synthetic")
        self.elf = root / "EBOOT synthetic.elf"
        self.write(self.elf, "synthetic elf")
        self.dev_flash = root / "dev_flash"
        self.write(self.dev_flash / "sys" / "marker.txt", "synthetic")
        self.prefix = root / "clang64"
        for tool in setup.TOOLS:
            self.write(self.prefix / "bin" / tool, "")
        self.shaders = root / "shaders"
        self.write(self.shaders / "manifest.json", json.dumps({"passed": True}))
        self.write(self.shaders / "PrecompiledShadersSPIRV" / "a.h", "// synthetic\n")
        self.workspace = self.base / "work space's"
        real_core = setup._module("launcher_core")
        modules = {
            "build_owned_backup": _module("build_owned_backup", plain_output=fake_plain_output),
            "source_snapshot": _module("source_snapshot", snapshot_sources=fake_snapshot),
            "backup_validation": _module("backup_validation", validate_backup=fake_validate_backup),
            "launcher_core": _module("launcher_core", default_config=fake_default_config, validate_config=fake_validate_config,
                                     Capabilities=real_core.Capabilities,
                                     parse_capability_record=real_core.parse_capability_record,
                                     check_profile_length=real_core.check_profile_length),
        }
        patcher = mock.patch.dict(sys.modules, modules)
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def write(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def invoke(self, *flags, runner=None, platform="win32", **overrides):
        runner = runner or FakeRunner()
        values = {"disc": self.disc, "elf": self.elf, "dev-flash": self.dev_flash,
                  "workspace": self.workspace, "prefix": self.prefix, "shaders": self.shaders}
        values.update({key.replace("_", "-"): value for key, value in overrides.items()})
        argv = [item for key, value in values.items() for item in (f"--{key}", str(value))]
        argv += ["--jobs", "3", *flags]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = setup.main(argv, repo=self.repo, platform=platform, run=runner)
        return code, out.getvalue(), err.getvalue(), runner

    def state(self, ws=None):
        return json.loads(((ws or self.workspace) / "setup-state.json").read_text(encoding="utf-8"))

    def outside(self):
        result = {}
        for path in self.base.rglob("*"):
            if path == self.workspace or self.workspace in path.parents:
                continue
            result[str(path)] = _sha(path) if path.is_file() else "dir"
        return result

    def assert_rejected(self, expected, **kwargs):
        before = self.outside()
        code, _, err, runner = self.invoke(**kwargs)
        self.assertEqual(code, 1)
        self.assertIn(expected, err)
        self.assertFalse(os.path.lexists(kwargs.get("workspace", self.workspace)))
        self.assertEqual(self.outside(), before)
        self.assertEqual(runner.calls, [])

    def test_install_uses_exact_argument_lists(self):
        code, out, err, runner = self.invoke()
        self.assertEqual(code, 0, err)
        ws = self.workspace
        result = json.loads(out)
        self.assertEqual(result, {"phase": "installed-local-untested",
                                  "binary": str(ws / "build" / "native-build" / "FolkloreBoot.exe"),
                                  "launcher": str(ws / "Launch.py"), "settings": str(ws / "settings.json"),
                                  "runtime_tested": False})
        scripts, support = self.repo / "client" / "scripts", self.repo / "client" / "build-support"
        self.assertEqual(runner.calls[0][0], [
            sys.executable, "-B", str(scripts / "prepare_owned_metadata.py"), "--elf", str(self.elf),
            "--sdk", str(self.repo / "engine"), "--output", str(ws / "metadata")])
        self.assertEqual(runner.calls[1][0], [
            sys.executable, "-B", str(scripts / "build_owned_backup.py"), "--elf", str(self.elf),
            "--sdk", str(self.repo / "engine"), "--metadata", str(ws / "metadata"),
            "--ppu-manifest", str(support / "ppu-acceptance.json"),
            "--spu-manifest", str(support / "spu-acceptance.json"), "--toolchain", str(self.prefix / "bin"),
            "--prefix", str(self.prefix), "--shader-dir", str(self.shaders), "--source", str(self.repo / "client"),
            "--output", str(ws / "build"), "--jobs", "3"])
        for _, kwargs in runner.calls:
            self.assertIs(kwargs["shell"], False)
            self.assertEqual(kwargs["cwd"], str(ws))
            env = kwargs["env"]
            self.assertEqual((env["TEMP"], env["TMP"]), (str(ws / "tmp"), str(ws / "tmp")))
            self.assertTrue(env["PATH"].startswith(str(self.prefix / "bin") + os.pathsep))
            self.assertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")
        settings = json.loads((ws / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(settings["schema"], 8)
        self.assertEqual(settings["paths"], {"binary": result["binary"], "elf": str(self.elf),
                                             "disc": str(self.disc), "hdd0": str(ws / "profile" / "dev_hdd0"),
                                             "dev_flash": str(self.dev_flash)})
        self.assertTrue((ws / "profile" / "dev_hdd0").is_dir())
        state = self.state()
        self.assertEqual(state["bindings"]["repo"], str(self.repo))
        self.assertEqual([c["exit_code"] for c in state["commands"]], [0, 0])
        self.assertIs(state["runtime_tested"], False)

    def test_python_launcher_preserves_paths_without_shell(self):
        import runpy
        entry = self.repo / "client/launcher/portable_entry.py"
        settings = self.workspace / "settings.json"
        script = setup.python_launch_script(self.prefix / "bin", entry, settings)
        with mock.patch.object(runpy, "run_path") as run, mock.patch.object(sys, "argv", []), \
             mock.patch.dict(os.environ), mock.patch.object(sys, "dont_write_bytecode", False):
            exec(compile(script, "Launch.py", "exec"), {})
            self.assertEqual(sys.argv, [str(entry), "--config", str(settings)])
            self.assertTrue(os.environ["PATH"].startswith(str(self.prefix / "bin") + os.pathsep))
            self.assertTrue(sys.dont_write_bytecode)
            run.assert_called_once_with(str(entry), run_name="__main__")

    def test_launch_script_escaping(self):
        q = setup.ps_quote
        self.assertEqual(q("C:\\a b\\o'k\u2019"), "'C:\\a b\\o''k\u2019\u2019'")
        self.assertEqual(q("$env:X; `x"), "'$env:X; `x'")
        self.assertRaises(setup.SetupError, q, "a\nb")
        code, _, err, _ = self.invoke()
        self.assertEqual(code, 0, err)
        text = (self.workspace / "Launch.ps1").read_text(encoding="utf-8-sig")
        entry = self.repo / "client" / "launcher" / "portable_entry.py"
        command = [sys.executable, "-B", entry, "--config", self.workspace / "settings.json"]
        self.assertIn("& " + " ".join(q(part) for part in command), text.splitlines())
        self.assertIn(f"$env:PATH = {q(self.prefix / 'bin')} + ", text)
        self.assertIn("o''wner files", text)
        self.assertNotIn("Invoke-Expression", text)

    def test_rejected_inputs_write_nothing(self):
        self.assert_rejected("Windows", platform="linux")
        self.assert_rejected("absolute", elf="EBOOT.elf")
        self.assert_rejected("overlaps", workspace=self.prefix / "ws")
        self.assert_rejected("overlaps", workspace=self.repo / "client" / "ws")
        self.assert_rejected("--disc", disc=self.elf)
        tool = self.prefix / "bin" / "ninja.exe"
        tool.unlink()
        self.assert_rejected("ninja.exe")
        tool.write_bytes(b"")
        manifest = self.shaders / "manifest.json"
        manifest.write_text(json.dumps({"passed": False}), encoding="utf-8")
        self.assert_rejected("passed")
        manifest.write_text(json.dumps({"passed": True}), encoding="utf-8")
        shutil.rmtree(self.dev_flash / "sys")
        self.assert_rejected("--dev-flash")
        self.write(self.dev_flash / "sys" / "marker.txt", "synthetic")
        (self.disc / "PARAM.SFO").unlink()
        self.assert_rejected("PARAM.SFO")

    def test_profile_length_rejected_before_build_or_writes(self):
        capability_path = self.repo / "client/native/launcher-capabilities.json"
        caps = json.loads(capability_path.read_text(encoding="utf-8"))
        current = len(str((self.workspace / "profile/dev_hdd0").resolve()))
        caps["limits"]["profile_path_max"] = current - 1
        self.write(capability_path, json.dumps(caps))
        self.assert_rejected("shorter new folder")
        caps["limits"]["profile_path_max"] = current
        self.write(capability_path, json.dumps(caps))
        code, _, err, runner = self.invoke()
        self.assertEqual(code, 0, err)
        self.assertEqual(len(runner.calls), 2)

    def test_invalid_source_capabilities_fail_before_writes(self):
        self.write(self.repo / "client/native/launcher-capabilities.json", "{}")
        self.assert_rejected("cannot use this installation folder")

    def test_existing_unrelated_workspace_untouched(self):
        self.workspace.mkdir()
        self.write(self.workspace / "keep.txt", "mine")
        for flags in ((), ("--resume",)):
            code, _, err, runner = self.invoke(*flags)
            self.assertEqual(code, 1)
            self.assertEqual(runner.calls, [])
            self.assertEqual(os.listdir(self.workspace), ["keep.txt"])
            self.assertEqual((self.workspace / "keep.txt").read_text(encoding="utf-8"), "mine")

    def test_wrong_process_exit_never_installs(self):
        code, out, err, _ = self.invoke(runner=FakeRunner(build_exit=2))
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("exited with code 2", err)
        self.assertFalse((self.workspace / "settings.json").exists())
        self.assertFalse((self.workspace / "Launch.ps1").exists())
        state = self.state()
        self.assertEqual(state["phase"], "metadata-verified")
        self.assertEqual(state["commands"][-1]["exit_code"], 2)
        self.assertIn("error", state)

    def test_forged_checkpoint_rejected(self):
        forgeries = [({"binary_sha256": "0" * 64}, "SHA-256"),
                     ({"binary": str(self.base / "elsewhere.exe")}, "refusing"),
                     ({"build_source_snapshot": {}}, "build_source_snapshot"),
                     ({"state": "configured"}, "'built'")]
        for index, (forge, expected) in enumerate(forgeries):
            with self.subTest(forge=forge):
                ws = self.base / f"forged {index}"
                code, _, err, _ = self.invoke(runner=FakeRunner(forge=forge), workspace=ws)
                self.assertEqual(code, 1)
                self.assertIn(expected, err)
                self.assertFalse((ws / "settings.json").exists())
                self.assertNotEqual(self.state(ws)["phase"], "installed-local-untested")

    def test_prepare_only_then_resume_installs(self):
        code, out, err, _ = self.invoke("--prepare-only")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), {"phase": "prepared", "binary": None, "launcher": None,
                                           "settings": None, "runtime_tested": False})
        self.assertFalse((self.workspace / "settings.json").exists())
        self.assertFalse((self.workspace / "Launch.ps1").exists())
        first = self.state()["commands"]
        self.assertEqual(self.state()["phase"], "prepared")
        code, out, err, runner = self.invoke("--resume")
        self.assertEqual(code, 0, err)
        self.assertEqual(len(runner.calls), 1)
        self.assertIn("--resume", runner.calls[0][0])
        self.assertNotIn("--prepare-only", runner.calls[0][0])
        state = self.state()
        self.assertEqual(state["commands"][:len(first)], first)
        self.assertEqual(state["phase"], "installed-local-untested")

    def test_resume_rejects_changed_source_and_inputs(self):
        self.assertEqual(self.invoke("--prepare-only")[0], 0)
        receipt = (self.workspace / "setup-state.json").read_text(encoding="utf-8")
        core = self.repo / "engine" / "include" / "core.h"
        original = core.read_text(encoding="utf-8")
        core.write_text("changed\n", encoding="utf-8")
        code, _, err, runner = self.invoke("--resume")
        self.assertEqual((code, runner.calls), (1, []))
        self.assertIn("source_snapshot", err)
        core.write_text(original, encoding="utf-8")
        elf_bytes = self.elf.read_bytes()
        self.elf.write_bytes(b"different synthetic elf")
        code, _, err, runner = self.invoke("--resume")
        self.assertEqual((code, runner.calls), (1, []))
        self.assertIn("identity", err)
        self.elf.write_bytes(elf_bytes)
        other_disc = self.disc.with_name("disc copy")
        shutil.copytree(self.disc, other_disc)
        code, _, err, runner = self.invoke("--resume", disc=other_disc)
        self.assertEqual((code, runner.calls), (1, []))
        self.assertIn("bindings", err)
        self.assertEqual((self.workspace / "setup-state.json").read_text(encoding="utf-8"), receipt)
        self.assertEqual(self.invoke("--resume")[0], 0)

    def test_edited_settings_preserved_on_resume(self):
        self.assertEqual(self.invoke()[0], 0)
        settings = self.workspace / "settings.json"
        edited = json.loads(settings.read_text(encoding="utf-8"))
        edited["texture_pack_root"] = "D:\\my packs"
        settings.write_text(json.dumps(edited), encoding="utf-8")
        text = settings.read_text(encoding="utf-8")
        code, _, err, _ = self.invoke("--resume")
        self.assertEqual(code, 0, err)
        self.assertEqual(settings.read_text(encoding="utf-8"), text)
        self.assertIs(self.state()["settings_reused"], True)

    def test_resume_rejects_settings_for_another_binary_without_overwriting(self):
        self.assertEqual(self.invoke()[0], 0)
        path = self.workspace / "settings.json"
        settings = json.loads(path.read_text(encoding="utf-8"))
        settings["paths"]["binary"] = str(self.base / "unrelated.exe")
        path.write_text(json.dumps(settings), encoding="utf-8")
        before = path.read_bytes()
        code, _, err, _ = self.invoke("--resume")
        self.assertEqual(code, 1)
        self.assertIn("different inputs", err)
        self.assertEqual(path.read_bytes(), before)

    def test_resume_preserves_save_contents(self):
        self.assertEqual(self.invoke()[0], 0)
        save = self.workspace / "profile" / "dev_hdd0" / "save.dat"
        save.write_bytes(b"user save")
        self.assertEqual(self.invoke("--resume")[0], 0)
        self.assertEqual(save.read_bytes(), b"user save")

    def test_metadata_corruption_fails_resume(self):
        self.assertEqual(self.invoke("--prepare-only")[0], 0)
        metadata = self.workspace / "metadata"
        (metadata / "symbols.json").write_text("corrupt", encoding="utf-8")
        code, _, err, runner = self.invoke("--resume")
        self.assertEqual((code, runner.calls), (1, []))
        self.assertIn("does not match", err)
        (metadata / "receipt.json").unlink()
        code, _, err, runner = self.invoke("--resume")
        self.assertEqual((code, runner.calls), (1, []))
        self.assertIn("not a verified metadata receipt", err)
        self.assertEqual(self.state()["phase"], "prepared")

    def test_logs_and_receipt_only_inside_workspace(self):
        before = self.outside()
        code, _, err, _ = self.invoke()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.outside(), before)
        self.assertEqual(set(os.listdir(self.workspace)), {
            "setup-state.json", "settings.json", "Launch.py", "Launch.ps1", "logs", "tmp", "metadata", "build", "profile"})
        for command in self.state()["commands"]:
            log = Path(command["log"])
            self.assertEqual(log.parent, self.workspace / "logs")
            self.assertIn("synthetic log", log.read_text(encoding="utf-8"))

    def test_linked_workspace_child_rejected(self):
        self.assertEqual(self.invoke("--prepare-only")[0], 0)
        target = self.base / "outside"
        target.mkdir()
        try:
            os.symlink(target, self.workspace / "build" / "link", target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are not permitted here")
        code, _, err, runner = self.invoke("--resume")
        self.assertEqual((code, runner.calls), (1, []))
        self.assertIn("linked", err)
        self.assertEqual(os.listdir(target), [])


if __name__ == "__main__":
    unittest.main()
