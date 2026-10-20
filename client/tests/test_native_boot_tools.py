"""Synthetic build/run control tests; never compiles or executes a game."""
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_native_boot as builder
import run_native_boot as runner

TEST_OUTPUT_ROOT = Path(os.environ.get("FOLKLORE_TEST_OUTPUT_ROOT", Path(__file__).parents[1]))


def fixture_host_config(platform, root, **kwargs):
    return builder.host_config(platform, root=root, source_root=Path(root) / "outputs/folklore-native", **kwargs)


class RenderingEvidenceTests(unittest.TestCase):
    def test_vulkan_presentation_records_api_evidence(self):
        log = ("[RSX vulkan] window swapchain 1280x720, 4 images, format 44\r\n"
               "[RSX vulkan] window present 1 accepted (VkResult 0)\r\n"
               "[RSX vulkan] window present 4 accepted (VkResult 1000001003)\r\n")
        evidence = runner.rendering_evidence(log, vulkan=True)
        self.assertTrue(evidence["rendering_enabled"])
        self.assertTrue(evidence["window_presentation_observed"])
        self.assertEqual(evidence["vulkan_window_present_count_at_last_log"], 4)
        self.assertEqual(evidence["vulkan_swapchain_extent_at_last_log"], [1280, 720])
        self.assertNotIn("playable_game", evidence)

    def test_swapchain_and_failed_present_do_not_prove_rendering(self):
        log = ("[RSX vulkan] window swapchain 1280x720, 4 images, format 44\n"
               "[RSX vulkan] window present 1 accepted (VkResult -4)\n"
               "interleaved [RSX vulkan] window present 2 accepted (VkResult 0)\n")
        evidence = runner.rendering_evidence(log, vulkan=True)
        self.assertFalse(evidence["rendering_enabled"] or evidence["window_presentation_observed"])

    def test_headless_initialization_is_separate_from_presentation(self):
        evidence = runner.rendering_evidence("[RSX vulkan] offscreen target 1280x720 ready, depth format 126 (Folklore native diagnostic)\n", vulkan=True)
        self.assertTrue(evidence["rendering_enabled"])
        self.assertFalse(evidence["window_presentation_observed"])
        self.assertIsNone(evidence["vulkan_swapchain_extent_at_last_log"])

    def test_renderer_request_scopes_evidence(self):
        log = "[RSX vulkan] window present 4 accepted (VkResult 0)\n[D3D12] Backend ready: test\n"
        self.assertFalse(runner.rendering_evidence(log)["rendering_enabled"])
        self.assertTrue(runner.rendering_evidence(log, graphics=True)["rendering_enabled"])
        self.assertFalse(runner.rendering_evidence(log, graphics=True)["window_presentation_observed"])


class Process:
    pid = 123
    def __init__(self, interruption=None):
        self.interruption, self.returncode, self.kills = interruption, None, 0
    def wait(self, timeout):
        if self.interruption:
            error, self.interruption = self.interruption, None
            raise error
        self.returncode = -9 if self.kills else 86
        return self.returncode
    def poll(self): return self.returncode
    def kill(self): self.kills += 1


class HostTests(unittest.TestCase):
    def test_private_sdk_copy_must_match_current_sdk(self):
        with tempfile.TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as temp:
            config=fixture_host_config("posix",root=temp,available=lambda p: True)
            package=config["work"]/"private-native/third-v1"
            header=package/"runtime/spu/context.h";header.parent.mkdir(parents=True)
            header.write_bytes(b"original ABI")
            source=package/"entry.c";source.write_bytes(b"synthetic source")
            current=config["sdk"]/"runtime/spu/context.h";current.parent.mkdir(parents=True)
            current.write_bytes(header.read_bytes())
            (package/"profile-manifest.json").write_text(json.dumps({"schema":"folklore-private-native-jobs-v1","native_translation_unit":"entry.c","files":{"entry.c":builder.sha(source),"runtime/spu/context.h":builder.sha(header)}}))
            self.assertIsNotNone(builder.private_native_job_inputs(config))
            current.write_bytes(b"changed ABI")
            with self.assertRaises(ValueError):
                builder.private_native_job_inputs(config)

    def test_source_manifest_binds_build_and_launch_recipes(self):
        with tempfile.TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as temp:
            config=fixture_host_config("posix",root=temp,available=lambda p: True)
            config["native"].mkdir(parents=True);config["lift"].mkdir(parents=True)
            (config["native"]/"CMakeLists.txt").write_bytes(b"synthetic")
            (config["lift"]/"ppu_recomp.h").write_bytes(b"synthetic")
            recipes=config["root"]/"outputs/folklore-native/scripts";recipes.mkdir()
            for name in ("build_native_boot.py","run_native_boot.py","spu_job_capture.py"):
                (recipes/name).write_bytes(b"before")
            with mock.patch.object(builder.subprocess,"run",return_value=subprocess.CompletedProcess([],0,stdout=b"")):
                before=builder.source_state(config)
                (recipes/"run_native_boot.py").write_bytes(b"after")
                after=builder.source_state(config)
            for name in ("build_native_boot.py","run_native_boot.py","spu_job_capture.py"):
                self.assertIn(str((recipes/name).relative_to(config["root"])),before)
            key=str((recipes/"run_native_boot.py").relative_to(config["root"]))
            self.assertNotEqual(before[key],after[key])
            with mock.patch.object(builder.subprocess,"run",return_value=subprocess.CompletedProcess([],0,stdout=b"")):
                (recipes/"spu_job_capture.py").write_bytes(b"changed capture verifier")
                changed_capture=builder.source_state(config)
            capture_key=str((recipes/"spu_job_capture.py").relative_to(config["root"]))
            self.assertNotEqual(after[capture_key],changed_capture[capture_key])

    def test_case_variant_private_sdk_prefix_cannot_skip_abi_check(self):
        with tempfile.TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as temp:
            config=fixture_host_config("posix",root=temp,available=lambda p: True)
            package=config["work"]/"private-native/third-v1"
            header=package/"Runtime/spu/context.h";header.parent.mkdir(parents=True)
            header.write_bytes(b"stale ABI")
            source=package/"entry.c";source.write_bytes(b"synthetic source")
            current=config["sdk"]/"runtime/spu/context.h";current.parent.mkdir(parents=True)
            current.write_bytes(b"current ABI")
            (package/"profile-manifest.json").write_text(json.dumps({"schema":"folklore-private-native-jobs-v1","native_translation_unit":"entry.c","files":{"entry.c":builder.sha(source),"Runtime/spu/context.h":builder.sha(header)}}))
            with self.assertRaises(ValueError):
                builder.private_native_job_inputs(config)

    def test_dialog_input_waits_for_complete_bounded_native_bmp(self):
        with tempfile.TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as temp:
            image=Path(temp)/"system-dialog.bmp"
            self.assertFalse(runner.dialog_capture_complete(image))
            for raw in (b"BM",b"BM"+(54).to_bytes(4,"little")+bytes(14),bytes(54)):
                image.write_bytes(raw)
                self.assertFalse(runner.dialog_capture_complete(image))
            image.write_bytes(b"BM"+(54).to_bytes(4,"little")+bytes(48))
            self.assertTrue(runner.dialog_capture_complete(image))

    def test_native_header_change_invalidates_source_manifest(self):
        with tempfile.TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as temp:
            config=fixture_host_config("posix",root=temp,available=lambda p: True)
            config["native"].mkdir(parents=True)
            config["lift"].mkdir(parents=True)
            (config["native"]/"CMakeLists.txt").write_text("synthetic")
            (config["lift"]/"ppu_recomp.h").write_text("synthetic")
            header=config["native"]/"host.h"
            header.write_text("before")
            with mock.patch.object(builder.subprocess,"run",return_value=subprocess.CompletedProcess([],0,stdout=b"")):
                before=builder.source_state(config)
                header.write_text("after")
                after=builder.source_state(config)
            key=str(header.relative_to(config["root"]))
            self.assertIn(key,before)
            self.assertNotEqual(before[key],after[key])

    def test_posix_supported_tool_selection_and_configuration(self):
        available = {"/usr/local/bin/clang", "/usr/local/bin/clang++", "/usr/bin/cmake", "/usr/bin/ninja", "/usr/bin/git"}
        config = builder.host_config("posix", available=lambda p: str(p).replace("\\", "/") in available)
        self.assertEqual(config["binary_name"], "FolkloreBoot")
        command = builder.build_commands(config)[0]
        self.assertTrue(any("CMAKE_PREFIX_PATH=" in arg and "install-linux" in arg for arg in command))
        self.assertTrue(any("CMAKE_C_COMPILER=" in arg and "/usr/local/bin/clang" in arg.replace("\\", "/") for arg in command))
        with mock.patch.dict(os.environ, {"PS3_UNTRUSTED_OVERRIDE": "1", "LD_PRELOAD": "untrusted"}):
            env = builder.controlled_environment(config, Path("/mnt/q/folklore/work/tmp"))
        self.assertEqual(set(env), {"PATH", "TMPDIR", "LANG", "LC_ALL", "PYTHONDONTWRITEBYTECODE"})
        self.assertNotIn("LD_PRELOAD", env)

    def test_linux_desktop_environment_is_scoped_to_launch(self):
        config = builder.host_config("posix", available=lambda p: True)
        host = {"DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-0",
                "XDG_RUNTIME_DIR": "/run/user/1000", "XAUTHORITY": "/tmp/test-xauth",
                "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
                "PULSE_SERVER": "unix:/tmp/pulse", "LD_PRELOAD": "/tmp/inject.so",
                "PS3_UNTRUSTED_OVERRIDE": "1"}
        with mock.patch.dict(os.environ, host, clear=True):
            ui = runner.launch_environment(config, Path("/tmp/test"), desktop=True)
            cpu = runner.launch_environment(config, Path("/tmp/test"))
            build = builder.controlled_environment(config, Path("/tmp/test"))
        for key in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "XAUTHORITY",
                    "DBUS_SESSION_BUS_ADDRESS", "PULSE_SERVER"):
            self.assertEqual(ui[key], host[key])
            self.assertNotIn(key, cpu)
            self.assertNotIn(key, build)
        self.assertNotIn("LD_PRELOAD", ui)
        self.assertNotIn("PS3_UNTRUSTED_OVERRIDE", ui)

    def test_missing_posix_tool_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "tool missing"):
            builder.host_config("posix", available=lambda p: False)

    def test_windows_names_and_paths_preserved(self):
        config = builder.host_config("nt")
        self.assertEqual(config["binary_name"], "FolkloreBoot.exe")
        self.assertEqual(config["build"].name, "build-folklore-gnu")
        self.assertIn("clang.exe", str(config["tools"]["cc"]))
        with mock.patch.object(builder, "private_native_job_inputs", return_value=None):
            self.assertFalse(any("CMAKE_PREFIX_PATH=" in arg for arg in builder.build_commands(config)[0]))
            self.assertIn("-DPS3RECOMP_PAD_USE_SDL2=ON", builder.build_commands(config)[0])

    def test_extra_translation_units_are_hash_bound(self):
        with tempfile.TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as temp:
            config=fixture_host_config("nt",root=temp)
            package=config["work"]/"private-native/third-v1";package.mkdir(parents=True)
            for name in ("entry.c","extra.c"):(package/name).write_text("/* synthetic */")
            data={"schema":"folklore-private-native-jobs-v1","native_translation_unit":"entry.c",
                  "extra_translation_units":["extra.c"],"files":{n:builder.sha(package/n) for n in ("entry.c","extra.c")}}
            manifest=package/"profile-manifest.json";manifest.write_text(json.dumps(data))
            inputs=builder.private_native_job_inputs(config)
            self.assertEqual(inputs["extra_sources"],[package/"extra.c"])
            self.assertIn("-DFOLKLORE_PRIVATE_NATIVE_EXTRA_SOURCES="+(package/"extra.c").as_posix(),builder.build_commands(config)[0])
            for invalid in (["missing.c"],["entry.c"],["extra.c","extra.c"],[{}]):
                data["extra_translation_units"]=invalid;manifest.write_text(json.dumps(data))
                with self.assertRaises(ValueError):builder.private_native_job_inputs(config)

    def test_windows_build_environment_excludes_unrelated_private_values(self):
        with mock.patch.dict(os.environ, {"UNRELATED_PRIVATE_VALUE": "never-record", "PS3_UNTRUSTED_OVERRIDE": "1"}):
            env = builder.controlled_environment(builder.host_config("nt"), Path("Q:/folklore/work/tmp"))
        self.assertNotIn("UNRELATED_PRIVATE_VALUE", env)
        self.assertNotIn("PS3_UNTRUSTED_OVERRIDE", env)

    def test_wsl_resolves_windows_worktree_pointer_without_changing_it(self):
        with tempfile.TemporaryDirectory(dir=TEST_OUTPUT_ROOT) as temp:
            config = fixture_host_config("posix", root=temp, available=lambda p: True)
            config["sdk"].mkdir(parents=True)
            pointer = config["sdk"] / ".git"
            original = "gitdir: Q:/folklore/work/ps3recomp/.git/worktrees/ps3recomp-strict\n"
            pointer.write_text(original)
            command = builder.git_command(config, ["rev-parse", "HEAD"])
            self.assertIn("--git-dir=/mnt/q/folklore/work/ps3recomp/.git/worktrees/ps3recomp-strict", command)
            self.assertEqual(command[-2:], ["rev-parse", "HEAD"])
            self.assertEqual(pointer.read_text(), original)


class RunTests(unittest.TestCase):
    def test_capture_is_explicit_and_bound_to_new_private_run(self):
        source=self.root/'outputs/folklore-native/scripts/run_native_boot.py'
        self.write(source,b'synthetic launcher')
        self.write(source.with_name('spu_job_capture.py'),b'synthetic verifier')
        with mock.patch.object(runner,'__file__',str(source)):
            with contextlib.redirect_stdout(io.StringIO()):
                runner.run(1,config=self.config,capture_spu_job=True)
        receipt=self.receipt()
        self.assertTrue(receipt['spu_job_capture_requested'])
        self.assertEqual(receipt['spu_job_capture']['status'],'none')
        target=Path(receipt['cwd'])/'spu-capture'
        self.assertEqual(self.launch.call_args.kwargs['env']['FOLKLORE_SPU_CAPTURE_DIR'],str(target))
        self.assertTrue(target.is_dir())
        self.assertIn(str(source.with_name('spu_job_capture.py').relative_to(self.root)),receipt['sha256'])

    def test_default_run_does_not_inherit_capture_destination(self):
        with mock.patch.dict(os.environ,{'FOLKLORE_SPU_CAPTURE_DIR':'unrelated-private-path'}):
            with contextlib.redirect_stdout(io.StringIO()):
                runner.run(1,config=self.config)
        receipt=self.receipt()
        self.assertFalse(receipt['spu_job_capture_requested'])
        self.assertNotIn('FOLKLORE_SPU_CAPTURE_DIR',self.launch.call_args.kwargs['env'])
        self.assertFalse((Path(receipt['cwd'])/'spu-capture').exists())

    def test_install_exercise_requires_explicit_modes_before_launch(self):
        for host,graphics,trace,writes in (("posix",False,False,False),("nt",False,False,True),
                                         ("nt",True,False,True),("nt",True,True,False)):
            with self.subTest(host=host,graphics=graphics,trace=trace,writes=writes):
                with self.assertRaises(ValueError):
                    runner.run(1,config={**self.config,"platform":host},graphics=graphics,
                               trace_startup=trace,allow_hdd_writes=writes,exercise_install_dialogs=True)
        self.launch.assert_not_called()

    def test_dialog_exercise_modes_are_mutually_exclusive(self):
        with self.assertRaisesRegex(ValueError,"mutually exclusive"):
            runner.run(1,config=self.config,exercise_startup_dialog=True,exercise_install_dialogs=True)
        self.launch.assert_not_called()

    def test_dialog_exercise_requires_all_explicit_modes_before_launch(self):
        for host,graphics,trace,writes in (("posix",False,False,False),("nt",False,False,True),
                                            ("nt",True,False,True),("nt",True,True,False)):
            with self.subTest(host=host,graphics=graphics,trace=trace,writes=writes):
                with self.assertRaisesRegex(ValueError,"startup dialog exercise requires"):
                    runner.run(1,config={**self.config,"platform":host},graphics=graphics,
                               trace_startup=trace,allow_hdd_writes=writes,exercise_startup_dialog=True)
        self.launch.assert_not_called()

    def test_startup_trace_rejects_unsupported_host_or_missing_graphics(self):
        for platform in ("posix","nt"):
            with self.subTest(platform=platform), self.assertRaisesRegex(ValueError,"startup tracing requires"):
                runner.run(1,config={**self.config,"platform":platform},trace_startup=True)
        self.launch.assert_not_called()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=TEST_OUTPUT_ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = fixture_host_config("posix", root=self.root, available=lambda p: True)
        self.write(self.config["native"] / "CMakeLists.txt", b"synthetic native build")
        self.config["prefix"].mkdir(parents=True)
        work = self.config["work"]
        self.elf = work / "input/candidate-usa/PS3_GAME/USRDIR/EBOOT.elf"
        self.binary = self.config["build"] / self.config["binary_name"]
        self.manifest = self.binary.parent / "build-manifest.json"
        self.write(self.elf, b"synthetic ELF fixture")
        self.write(self.binary, b"synthetic binary fixture, never executed")
        (work / "tools/rpcs3-reference/dev_flash").mkdir(parents=True)
        self.write(self.config["lift"] / "ppu_recomp.h", b"synthetic header")
        self.write(self.config["lift"] / "ppu_recomp_000.cpp", b"strict unsupported instruction at\n" * 8204)
        for rel in ("runtime/ppu/ppu_strict_boot.h", "include/ps3emu/strict_boot.h", "tools/ppu_lifter.py"):
            self.write(self.config["sdk"] / rel, b"synthetic source")
        self.manifest.write_text(json.dumps({"schema": "folklore-build-manifest-v1", "host_platform": "posix",
                                           "binary_sha256": runner.sha(self.binary), "sources": {"fixture": "state"}}))
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(runner, "ELF_SHA", runner.sha(self.elf)).start()
        mock.patch.object(runner, "source_state", return_value={"fixture": "state"}).start()
        mock.patch.object(runner.os, "access", return_value=True).start()
        mock.patch.object(runner.subprocess, "run", side_effect=[
            subprocess.CompletedProcess([], 0, stdout="basecommit\n"),
            subprocess.CompletedProcess([], 0, stdout=b"synthetic patch")]).start()
        self.launch = mock.patch.object(runner.subprocess, "Popen").start()
        self.process = Process()
        self.launch.return_value = self.process

    def write(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def receipt(self):
        paths = list((self.config["work"] / "native-runs").glob("*/receipt.json"))
        self.assertEqual(len(paths), 1)
        return json.loads(paths[0].read_text())

    def execute(self, writes=False):
        with contextlib.redirect_stdout(io.StringIO()):
            return runner.run(1, allow_hdd_writes=writes, config=self.config)

    def test_default_remains_readonly_and_diagnostic_only(self):
        self.assertEqual(self.execute(), 1)
        command = self.launch.call_args.args[0]
        env = self.launch.call_args.kwargs["env"]
        self.assertEqual(len(command), 5)
        self.assertEqual(env["PS3_STRICT_FS_READONLY"], "1")
        self.assertNotIn("PS3_STRICT_FS_WRITE_ROOT", env)
        receipt = self.receipt()
        self.assertFalse(receipt["playable_game"] or receipt["rendering_enabled"])
        self.assertEqual(receipt["status"], "diagnostic-stopped")

    def test_explicit_writes_are_bound_to_exact_new_run(self):
        self.assertEqual(self.execute(writes=True), 1)
        command = self.launch.call_args.args[0]
        env = self.launch.call_args.kwargs["env"]
        self.assertEqual(len(command), 6)
        self.assertEqual(command[-1], "--contained-writes")
        self.assertEqual(env["PS3_STRICT_FS_READONLY"], "0")
        self.assertEqual(Path(env["PS3_STRICT_FS_WRITE_ROOT"]), Path(command[3]).parent)
        self.assertEqual(Path(command[3]).parent.parent, self.launch.call_args.kwargs["cwd"])
        self.assertEqual(Path(command[3]).name, "dev_hdd0")
        self.assertEqual(Path(env["PS3_HDD1_ROOT"]), Path(command[3]).parent / "dev_hdd1")
        self.assertTrue(Path(command[3]).is_dir())
        self.assertTrue(Path(env["PS3_HDD1_ROOT"]).is_dir())

    def test_wrong_elf_and_stale_binary_never_launch(self):
        self.elf.write_bytes(b"wrong input")
        with self.assertRaisesRegex(ValueError, "ELF identity"):
            self.execute()
        self.launch.assert_not_called()
        mock.patch.object(runner, "ELF_SHA", runner.sha(self.elf)).start()
        self.binary.write_bytes(b"changed binary")
        with self.assertRaisesRegex(ValueError, "stale"):
            self.execute()
        self.launch.assert_not_called()

    def test_changed_source_and_wrong_host_never_launch(self):
        with mock.patch.object(runner, "source_state", return_value={"fixture": "changed"}):
            with self.assertRaisesRegex(ValueError, "stale"):
                self.execute()
        manifest = json.loads(self.manifest.read_text())
        manifest["host_platform"] = "nt"
        self.manifest.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "stale"):
            self.execute()
        self.launch.assert_not_called()

    def test_no_execute_permission_never_launches(self):
        with mock.patch.object(runner.os, "access", return_value=False):
            with self.assertRaisesRegex(ValueError, "not executable"):
                self.execute()
        self.launch.assert_not_called()

    def test_d3d12_rejected_on_posix_before_launch(self):
        with self.assertRaisesRegex(ValueError, "Windows host"):
            runner.run(1, config=self.config, graphics=True)
        self.launch.assert_not_called()

    def test_native_dialogs_posix_request_is_not_acceptance(self):
        with contextlib.redirect_stdout(io.StringIO()):
            runner.run(1, config=self.config, native_dialogs=True)
        self.assertIn("--native-dialogs",self.launch.call_args.args[0])
        self.assertFalse(self.receipt()["rendering_enabled"] or self.receipt()["playable_game"])

    def test_vulkan_posix_request_preserves_strict_receipt(self):
        with contextlib.redirect_stdout(io.StringIO()):
            runner.run(1, config=self.config, vulkan=True)
        self.assertIn("--vulkan", self.launch.call_args.args[0])
        self.assertNotIn("--d3d12", self.launch.call_args.args[0])
        self.assertEqual(self.launch.call_args.kwargs["env"]["PS3_STRICT_BOOT"], "1")
        self.assertEqual(self.receipt()["renderer_requested"], "Vulkan")
        self.assertTrue(self.receipt()["rendering_requested"])
        self.assertFalse(self.receipt()["rendering_enabled"] or self.receipt()["playable_game"])

    def test_ambient_occlusion_command_records_request_only(self):
        with contextlib.redirect_stdout(io.StringIO()):
            runner.run(1, config=self.config, vulkan=True, ambient_occlusion="cacao")
        self.assertIn("--ambient-occlusion=cacao", self.launch.call_args.args[0])
        self.assertEqual(self.receipt()["ambient_occlusion_requested"], "cacao")
        self.assertFalse(self.receipt()["rendering_enabled"])

    def test_ambient_occlusion_off_plan_is_explicit(self):
        result = runner.run(1, config=self.config, vulkan=True, ambient_occlusion="off", plan=True)
        self.assertIn("--ambient-occlusion=off", result["command"])
        self.launch.assert_not_called()

    def test_ambient_occlusion_invalid_requests_do_not_launch(self):
        for value in (False, 1, "", "CACAO", "ssao", "cacao ", " off"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                runner.run(1, config=self.config, vulkan=True, ambient_occlusion=value)
        with self.assertRaises(ValueError):
            runner.run(1, config=self.config, ambient_occlusion="off")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            runner.main(["--ambient-occlusion=off", "--ambient-occlusion=cacao"])
        self.launch.assert_not_called()

    def test_upscaler_command_records_request_only(self):
        with contextlib.redirect_stdout(io.StringIO()):
            runner.run(1, config=self.config, vulkan=True, upscaler="fsr1", antialiasing="fxaa")
        self.assertIn("--upscaler=fsr1", self.launch.call_args.args[0])
        self.assertIn("--anti-aliasing=fxaa", self.launch.call_args.args[0])
        self.assertEqual(self.receipt()["upscaler_requested"], "fsr1")
        self.assertFalse(self.receipt()["rendering_enabled"])

    def test_upscaler_off_plan_is_explicit(self):
        result = runner.run(1, config=self.config, vulkan=True, upscaler="off", plan=True)
        self.assertIn("--upscaler=off", result["command"])
        self.launch.assert_not_called()

    def test_upscaler_invalid_requests_do_not_launch(self):
        for value in (False, 1, "", "FSR1", "fsr", "fsr2", "fsr1 ", " off"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                runner.run(1, config=self.config, vulkan=True, upscaler=value)
        with self.assertRaises(ValueError):
            runner.run(1, config=self.config, upscaler="off")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            runner.main(["--upscaler=off", "--upscaler=fsr1"])
        self.launch.assert_not_called()

    def test_antialiasing_command_records_request_only(self):
        with contextlib.redirect_stdout(io.StringIO()):
            runner.run(1, config=self.config, vulkan=True, antialiasing="fxaa")
        self.assertIn("--anti-aliasing=fxaa", self.launch.call_args.args[0])
        self.assertEqual(self.receipt()["antialiasing_requested"], "fxaa")
        self.assertFalse(self.receipt()["rendering_enabled"])

    def test_antialiasing_off_plan_is_explicit(self):
        result = runner.run(1, config=self.config, vulkan=True, antialiasing="off", plan=True)
        self.assertIn("--anti-aliasing=off", result["command"])
        self.launch.assert_not_called()

    def test_antialiasing_invalid_requests_do_not_launch(self):
        for value in (False, 1, "", "FXAA", "taa", "0", "fxaa ", " off"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                runner.run(1, config=self.config, vulkan=True, antialiasing=value)
        with self.assertRaises(ValueError):
            runner.run(1, config=self.config, antialiasing="off")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            runner.main(["--anti-aliasing=off", "--anti-aliasing=fxaa"])
        self.launch.assert_not_called()

    def test_anisotropy_command_records_request_without_rendering_claim(self):
        with contextlib.redirect_stdout(io.StringIO()):
            runner.run(1, config=self.config, vulkan=True, anisotropy="16")
        self.assertIn("--anisotropy=16", self.launch.call_args.args[0])
        self.assertEqual(self.receipt()["anisotropy_requested"], "16")
        self.assertFalse(self.receipt()["rendering_enabled"])

    def test_anisotropy_plan_canonicalizes_guest_alias(self):
        result = runner.run(1, config=self.config, vulkan=True, anisotropy="0", plan=True)
        self.assertIn("--anisotropy=auto", result["command"])
        self.assertFalse(result["executes"])
        self.launch.assert_not_called()

    def test_anisotropy_invalid_requests_do_not_launch(self):
        for value in (False, 16, 2.0, "", "3", "12", "016", " 2", "2 ", "AUTO", "+2", "4294967296"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                runner.run(1, config=self.config, vulkan=True, anisotropy=value)
        with self.assertRaises(ValueError):
            runner.run(1, config=self.config, anisotropy="auto")
        for arguments in (["--anisotropy=0", "--anisotropy=16"], ["--anisotropy=3"]):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as failure:
                runner.main(arguments)
            self.assertEqual(failure.exception.code, 2)
        self.launch.assert_not_called()

    def test_settings_file_command_records_request_only(self):
        settings = self.config["work"] / "graphics.ini"
        with contextlib.redirect_stdout(io.StringIO()):
            runner.run(1, config=self.config, vulkan=True, settings_path=settings, save_settings=True)
        self.assertIn(f"--settings={settings}", self.launch.call_args.args[0])
        self.assertIn("--save-settings", self.launch.call_args.args[0])
        self.assertEqual(self.receipt()["settings_path_requested"], str(settings))
        self.assertTrue(self.receipt()["settings_save_requested"])
        self.assertFalse(settings.exists())
        self.assertFalse(self.receipt()["rendering_enabled"])

    def test_invalid_settings_requests_never_launch(self):
        for options in ({"vulkan": True, "settings_path": "relative.ini"},
                        {"vulkan": True, "save_settings": True},
                        {"settings_path": self.config["work"] / "graphics.ini"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                runner.run(1, config=self.config, **options)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as failure:
            runner.main(["--settings=one.ini", "--settings=two.ini"])
        self.assertEqual(failure.exception.code, 2)
        self.launch.assert_not_called()

    def test_settings_plan_does_not_create_file(self):
        settings = self.config["work"] / "planned-settings.ini"
        result = runner.run(1, config=self.config, vulkan=True, settings_path=settings, save_settings=True, plan=True)
        self.assertIn(f"--settings={settings}", result["command"])
        self.assertIn("--save-settings", result["command"])
        self.assertFalse(result["executes"])
        self.assertFalse(settings.exists())
        self.launch.assert_not_called()

    def test_vulkan_conflicting_modes_never_launch(self):
        for other in ({"graphics":True},{"native_dialogs":True}):
            with self.subTest(other=other),self.assertRaisesRegex(ValueError,"mutually exclusive"):
                runner.run(1,config=self.config,vulkan=True,**other)
        self.launch.assert_not_called()

    def test_vulkan_resolution_reaches_command_and_receipt(self):
        with contextlib.redirect_stdout(io.StringIO()):
            runner.run(1, config=self.config, vulkan=True, render_scale=3, output_size="3840x2160")
        command = self.launch.call_args.args[0]
        self.assertIn("--render-scale=3", command)
        self.assertIn("--output-size=3840x2160", command)
        self.assertEqual(self.receipt()["render_scale_requested"], 3)
        self.assertEqual(self.receipt()["output_size_requested"], "3840x2160")
        self.assertFalse(self.receipt()["rendering_enabled"])

    def test_resolution_rejected_before_any_launch(self):
        invalid = [{"render_scale": x} for x in (0, 5, True, 1.5, "3")]
        invalid += [{"output_size": x} for x in (None, "", "3840", "0x720", "8193x2160",
                    "1x8193", "1x1x1", "3840X2160", "1x+1", "1x 1", "1x١", "000000000001x1") if x is not None]
        for options in invalid:
            with self.subTest(options=options), self.assertRaises(ValueError):
                runner.run(1, config=self.config, vulkan=True, **options)
        for options in ({"render_scale": 3}, {"output_size": "3840x2160"}):
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, "require Vulkan"):
                runner.run(1, config=self.config, **options)
        self.launch.assert_not_called()

    def test_duplicate_resolution_cli_never_launches(self):
        for arguments in (["--render-scale=2", "--render-scale=3"],
                          ["--output-size=1280x720", "--output-size=3840x2160"]):
            with self.subTest(arguments=arguments), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as failure:
                    runner.main(arguments)
                self.assertEqual(failure.exception.code, 2)
        self.launch.assert_not_called()

    def test_native_dialogs_and_renderer_are_exclusive_before_launch(self):
        self.config["platform"]="nt"
        with self.assertRaises(ValueError):
            runner.run(1, config=self.config, native_dialogs=True, graphics=True)
        self.launch.assert_not_called()

    def test_native_dialog_request_preserves_no_rendering_acceptance(self):
        self.config["platform"]="nt"
        manifest=json.loads(self.manifest.read_text());manifest["host_platform"]="nt"
        self.manifest.write_text(json.dumps(manifest))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runner.run(1, config=self.config, native_dialogs=True),1)
        command=self.launch.call_args.args[0]
        self.assertIn("--native-dialogs",command)
        self.assertNotIn("--d3d12",command)
        self.assertNotIn("CELLMARK_DUMP",self.launch.call_args.kwargs["env"])
        receipt=self.receipt()
        self.assertTrue(receipt["native_dialogs_requested"])
        self.assertFalse(receipt["rendering_requested"] or receipt["rendering_enabled"] or receipt["playable_game"])

    def test_renderer_request_is_not_rendered_frame_acceptance(self):
        self.config["platform"] = "nt"
        manifest = json.loads(self.manifest.read_text())
        manifest["host_platform"] = "nt"
        self.manifest.write_text(json.dumps(manifest))
        with contextlib.redirect_stdout(io.StringIO()):
            runner.run(1, config=self.config, graphics=True)
        self.assertIn("--d3d12", self.launch.call_args.args[0])
        receipt = self.receipt()
        self.assertTrue(receipt["rendering_requested"])
        self.assertFalse(receipt["rendering_enabled"] or receipt["playable_game"])
        self.assertEqual(receipt["frame_captures"], {})

    def test_timeout_kills_owned_process_and_records_receipt(self):
        self.process.interruption = subprocess.TimeoutExpired("synthetic", 1)
        self.execute()
        self.assertEqual(self.process.kills, 1)
        self.assertEqual(self.receipt()["status"], "time-budget-exhausted")

    def test_cancellation_always_cleans_up_and_records_receipt(self):
        self.process.interruption = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.execute()
        self.assertEqual(self.process.kills, 1)
        self.assertEqual(self.receipt()["status"], "interrupted-or-failed")

    def test_process_creation_failure_records_failure(self):
        self.launch.side_effect = OSError("synthetic exec failure")
        with self.assertRaises(OSError):
            self.execute()
        receipt = self.receipt()
        self.assertEqual(receipt["status"], "interrupted-or-failed")
        self.assertIsNone(receipt["exit_code"])

    def test_build_source_change_invalidates_old_manifest(self):
        with mock.patch.object(builder, "host_config", return_value=self.config), \
             mock.patch.object(builder, "source_state", side_effect=[{"before": "a"}, {"after": "b"}]), \
             mock.patch.object(builder.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(builder.build(self.config), 1)
        self.assertFalse(self.manifest.exists())
        self.launch.assert_not_called()

    def test_failed_build_does_not_leave_old_manifest_accepted(self):
        with mock.patch.object(builder, "host_config", return_value=self.config), \
             mock.patch.object(builder, "source_state", return_value={"source": "unchanged"}), \
             mock.patch.object(builder.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(builder.build(self.config), 1)
        self.assertFalse(self.manifest.exists())
        self.launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
