"""Run only the verified private Folklore diagnostic target with isolated state.

A process exit, even zero, is never reported as a playable-game success.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import time
from build_native_boot import (source_state, host_config, controlled_environment, git_command,
                               add_path_arguments, config_from_arguments, validate_paths,
                               path_bindings, file_identity)

ELF_SHA = "51a9eb16ceca3edfb7454b37a9e895f5a4e35bd515a40ae5b918f97c3a1b6c0e"


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def dialog_capture_complete(path):
    """Wait for this child to finish its bounded UI capture before input."""
    try:
        with path.open("rb") as image:
            header=image.read(14)
        size=path.stat().st_size
        return (len(header)==14 and header[:2]==b"BM" and
                54<=size<=16*1024*1024 and int.from_bytes(header[2:6],"little")==size)
    except OSError:
        return False


def normalize_capture_spans(spans, index=None):
    """Validate explicit diagnostic reads before inspecting the target files."""
    if index is not None and (type(index) is not int or not 0 <= index < 16):
        raise ValueError("span capture index must be an integer in 0..15")
    if spans is None:
        if index is not None:
            raise ValueError("span capture index requires explicit spans")
        return None
    if type(spans) not in (tuple, list) or not 1 <= len(spans) <= 2:
        raise ValueError("capture requires one or two explicit spans")
    result = []
    for span in spans:
        if (type(span) not in (tuple, list) or len(span) != 2 or
                type(span[0]) is not int or type(span[1]) is not int):
            raise ValueError("capture span requires integer EA and size")
        ea, size = span
        if not 0x10000 <= ea < (1 << 32) or not 1 <= size <= 131072 or ea + size > (1 << 32):
            raise ValueError("capture span is outside its checked bounds")
        if any(ea < other + length and other < ea + size for other, length in result):
            raise ValueError("capture spans overlap")
        result.append((ea, size))
    if sum(size for _, size in result) > 131072:
        raise ValueError("capture spans exceed the 128 KiB request budget")
    return tuple(result)


def launch_environment(config, temporary, desktop=False):
    env = controlled_environment(config, temporary)
    if config["platform"] == "posix" and desktop:
        # Native SDL/GTK and audio need the user's existing desktop sockets.
        # Keep experimental guest overrides and loader injection excluded.
        for key in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "XAUTHORITY",
                    "DBUS_SESSION_BUS_ADDRESS", "PULSE_SERVER"):
            if os.environ.get(key):
                env[key] = os.environ[key]
    return env


def rendering_evidence(log_text, *, graphics=False, vulkan=False):
    """Backend/API evidence only; this never establishes visible gameplay."""
    ready = graphics and "[D3D12] Backend ready:" in log_text
    last_count, extent = 0, None
    if vulkan:
        for line in log_text.splitlines():
            if re.fullmatch(r"\[RSX vulkan\] offscreen target [1-9][0-9]*x[1-9][0-9]* ready, depth format [0-9]+ \(.*\)", line):
                ready = True
            present = re.fullmatch(r"\[RSX vulkan\] window present ([1-9][0-9]*) accepted \(VkResult (?:0|1000001003)\)", line)
            if present:
                last_count = max(last_count, int(present[1]))
            swapchain = re.fullmatch(r"\[RSX vulkan\] window swapchain ([1-9][0-9]*)x([1-9][0-9]*), [1-9][0-9]* images, format [0-9]+", line)
            if swapchain:
                extent = [int(swapchain[1]), int(swapchain[2])]
    return {"rendering_enabled": bool(ready or last_count),
            "window_presentation_observed": last_count > 0,
            "vulkan_window_present_count_at_last_log": last_count,
            "vulkan_swapchain_extent_at_last_log": extent}


def run(timeout, allow_hdd_writes=False, config=None, graphics=False, trace_startup=False,
        exercise_startup_dialog=False, exercise_install_dialogs=False, capture_spu_job=False,
        capture_spu_aux_ea=None, native_dialogs=False, capture_spu_spans=None,
        capture_spu_span_index=None, plan=False, vulkan=False, render_scale=None, output_size=None,
        settings_path=None, save_settings=False, anisotropy=None, antialiasing=None, upscaler=None, ambient_occlusion=None):
    if type(timeout) is not int or not 1 <= timeout <= 60:
        raise ValueError("timeout must be 1..60 seconds")
    if render_scale is not None and (type(render_scale) is not int or not 1 <= render_scale <= 4):
        raise ValueError("render scale must be an integer from 1 to 4")
    if output_size is not None:
        output_size = parse_output_size(output_size)
    if (render_scale is not None or output_size is not None) and not vulkan:
        raise ValueError("resolution options require Vulkan")
    if anisotropy is not None:
        anisotropy = parse_anisotropy(anisotropy)
        if not vulkan:
            raise ValueError("anisotropy requires Vulkan")
    if antialiasing is not None:
        antialiasing = parse_antialiasing(antialiasing)
        if not vulkan:
            raise ValueError("anti-aliasing requires Vulkan")
    if upscaler is not None:
        upscaler = parse_upscaler(upscaler)
        if not vulkan:
            raise ValueError("upscaler requires Vulkan")
    if ambient_occlusion is not None:
        ambient_occlusion = parse_ambient_occlusion(ambient_occlusion)
        if not vulkan:
            raise ValueError("ambient occlusion requires Vulkan")
    if settings_path is not None:
        settings_path = Path(settings_path)
        if not settings_path.is_absolute():
            raise ValueError("settings path must be absolute")
    if ((settings_path is not None or save_settings) and not vulkan) or (save_settings and settings_path is None):
        raise ValueError("settings require Vulkan; saving also requires an explicit settings path")
    spans = normalize_capture_spans(capture_spu_spans, capture_spu_span_index)
    if spans is not None and (not capture_spu_job or capture_spu_aux_ea is not None):
        raise ValueError("span capture requires job capture and cannot mix with four-byte auxiliary capture")
    if capture_spu_aux_ea is not None and (not capture_spu_job or
            type(capture_spu_aux_ea) is not int or not 0x10000 <= capture_spu_aux_ea <= 0xFFFFFFFC):
        raise ValueError("auxiliary capture requires job capture and one checked four-byte 32-bit EA")
    config = config or host_config()
    if exercise_startup_dialog and exercise_install_dialogs:
        raise ValueError("dialog exercise modes are mutually exclusive")
    if exercise_install_dialogs:
        exercise_startup_dialog = True
    if graphics and vulkan:
        raise ValueError("D3D12 and Vulkan requests are mutually exclusive")
    if graphics and config["platform"] != "nt":
        raise ValueError("D3D12 diagnostic requires the Windows host")
    if native_dialogs and (graphics or vulkan):
        raise ValueError("native dialog diagnostic and renderer requests are mutually exclusive")
    if trace_startup and (config["platform"] != "nt" or not (graphics or vulkan or native_dialogs)):
        raise ValueError("startup tracing requires a Windows native presenter")
    if exercise_startup_dialog and (config["platform"]!="nt" or not graphics or
                                    not trace_startup or not allow_hdd_writes):
        raise ValueError("startup dialog exercise requires Windows D3D12, tracing and explicit contained writes")
    bindings = validate_paths(config, run_inputs=True)
    binary = config["build"] / config["binary_name"]
    vfs = config["input"]
    elf = vfs / "PS3_GAME" / "USRDIR" / "EBOOT.elf"
    flash, lifted = config["firmware"], config["lift"]
    def command_for(folder):
        command = [str(binary), str(elf), str(vfs), str(folder / "vfs/dev_hdd0"), str(flash)]
        for enabled, argument in ((allow_hdd_writes, "--contained-writes"), (graphics, "--d3d12"), (vulkan, "--vulkan"),
                                  (native_dialogs, "--native-dialogs"), (trace_startup, "--trace-startup")):
            if enabled:
                command.append(argument)
        if render_scale is not None:
            command.append(f"--render-scale={render_scale}")
        if output_size is not None:
            command.append(f"--output-size={output_size}")
        if anisotropy is not None:
            command.append(f"--anisotropy={anisotropy}")
        if antialiasing is not None:
            command.append(f"--anti-aliasing={antialiasing}")
        if upscaler is not None:
            command.append(f"--upscaler={upscaler}")
        if ambient_occlusion is not None:
            command.append(f"--ambient-occlusion={ambient_occlusion}")
        if settings_path is not None:
            command.append(f"--settings={settings_path}")
        if save_settings:
            command.append("--save-settings")
        return command
    if plan:
        return {"schema": "folklore-launch-plan-v1", "host_platform": config["platform"],
                "paths": bindings, "command": command_for(config["runs"] / "NEW-RUN-ID"),
                "run_directory_placeholder": "NEW-RUN-ID", "executes": False,
                "creates_directories": False, "source_binary_verified": False,
                "timeout_seconds": timeout, "playable_game": False}
    if sha(elf) != ELF_SHA:
        raise ValueError("ELF identity differs from the verified BCUS98147 input")
    if not binary.is_file() or not flash.is_dir():
        raise ValueError("native binary or isolated reference firmware tree missing")
    if config["platform"] == "posix" and not os.access(binary, os.X_OK):
        raise ValueError("native POSIX binary is not executable")
    build_manifest_path = binary.parent / "build-manifest.json"
    build_manifest = json.loads(build_manifest_path.read_text(encoding="utf-8"))
    if (build_manifest.get("schema") != "folklore-build-manifest-v1" or
            build_manifest.get("binary_sha256") != sha(binary) or
            build_manifest.get("sources") != source_state(config) or
            build_manifest.get("host_platform", "nt") != config["platform"] or
            ((config["isolated_outputs"] or "paths" in build_manifest) and
             build_manifest.get("paths") != bindings)):
        raise ValueError("binary/source build correspondence is missing or stale")
    chunks = sorted(lifted.glob("ppu_recomp_*.cpp"))
    if not chunks:
        raise ValueError("strict PPU output missing")
    strict_count = 0
    for chunk in chunks:
        source = chunk.read_bytes()
        if b"/* TODO:" in source or b"/* missing continuation" in source:
            raise ValueError("permissive generated markers remain")
        strict_count += source.count(b"strict unsupported instruction at")
    if strict_count != 8204:
        raise ValueError(f"strict opcode-stop census changed: {strict_count}")
    provenance = {file_identity(config, p): sha(p) for p in [binary, elf, lifted / "ppu_recomp.h", *chunks]}
    if trace_startup:
        for name in ("run_native_boot.py", "native_startup_trace.py"):
            trace_source = Path(__file__).with_name(name)
            provenance[file_identity(config, trace_source)] = sha(trace_source)
    if exercise_startup_dialog:
        scenario_source = Path(__file__).with_name("native_dialog_scenario.py")
        provenance[file_identity(config, scenario_source)] = sha(scenario_source)
    if capture_spu_job:
        for name in ("run_native_boot.py", "spu_job_capture.py"):
            capture_source = Path(__file__).with_name(name)
            provenance[file_identity(config, capture_source)] = sha(capture_source)
    runtime = config["sdk"]
    base = subprocess.run(git_command(config, ["rev-parse", "HEAD"]), cwd=runtime, capture_output=True, text=True, check=True).stdout.strip()
    diff = subprocess.run(git_command(config, ["diff", "--binary", "HEAD"]), cwd=runtime, capture_output=True, check=True).stdout
    for rel in ("runtime/ppu/ppu_strict_boot.h", "include/ps3emu/strict_boot.h", "tools/ppu_lifter.py"):
        item = runtime / rel
        if not item.is_file():
            raise ValueError(f"required strict source missing: {rel}")
        provenance[file_identity(config, item)] = sha(item)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    validate_paths(config, run_inputs=True)
    folder = config["runs"] / stamp
    folder.mkdir(parents=True, exist_ok=False)
    volumes = folder / "vfs"
    volumes.mkdir()
    hdd = volumes / "dev_hdd0"
    hdd.mkdir()
    hdd1 = volumes / "dev_hdd1"
    hdd1.mkdir()
    temporary = folder / "tmp"
    temporary.mkdir()
    # Start with OS essentials, not inherited experimental title overrides.
    env = launch_environment(config, temporary, desktop=vulkan or native_dialogs)
    capture_directory = None
    if capture_spu_job:
        capture_directory = folder / "spu-capture"
        capture_directory.mkdir(exist_ok=False)
        env["FOLKLORE_SPU_CAPTURE_DIR"] = str(capture_directory)
        if capture_spu_aux_ea is not None:
            env["FOLKLORE_SPU_CAPTURE_AUX_EA"] = f"0x{capture_spu_aux_ea:08X}"
        if spans is not None:
            env["FOLKLORE_SPU_CAPTURE_SPANS"] = ",".join(f"0x{ea:08X}:{size}" for ea, size in spans)
            if capture_spu_span_index is not None:
                env["FOLKLORE_SPU_CAPTURE_SPANS_INDEX"] = str(capture_spu_span_index)
    env.update({
                "PS3_STRICT_BOOT": "1", "PS3_STRICT_FS_READONLY": "1", "PS3_VFS_ROOT": str(vfs),
                "PS3_HDD0_ROOT": str(hdd), "PS3_DEV_FLASH": str(flash),
                "PS3_HDD1_ROOT": str(hdd1),
                "PS3_BOOT_PATH": "/dev_bdvd/PS3_GAME/USRDIR/EBOOT.BIN",
                "MSGDIALOG_NOBUTTON_WAITS": "1", "MSGDIALOG_AUTODISMISS_MS": "0"})
    command = command_for(folder)
    if allow_hdd_writes:
        env["PS3_STRICT_FS_READONLY"] = "0"
        env["PS3_STRICT_FS_WRITE_ROOT"] = str(volumes)
    if graphics:
        # Bounded diagnostic readback files stay inside this run's cwd.
        env["CELLMARK_DUMP"] = "4"
    (folder / "runtime-tracked.patch").write_bytes(diff)
    receipt = {"schema": "folklore-native-diagnostic-v1", "started_utc": stamp,
               "command": command, "cwd": str(folder), "environment": env,
               "runtime_base_commit": base, "runtime_patch_sha256": hashlib.sha256(diff).hexdigest(),
               "host_platform": config["platform"],
               "paths": bindings,
               "build_manifest_sha256": sha(build_manifest_path),
               "filesystem_mode": "contained VFS writes explicitly enabled" if allow_hdd_writes else "read-only diagnostic",
               "sha256": provenance, "strict_unsupported_stops": strict_count,
               "timeout_seconds": timeout, "playable_game": False,
               "rendering_requested": graphics or vulkan, "rendering_enabled": False,
               "renderer_requested": "Vulkan" if vulkan else "D3D12" if graphics else None,
               "render_scale_requested": render_scale,
               "anisotropy_requested": anisotropy,
               "antialiasing_requested": antialiasing,
               "upscaler_requested": upscaler,
               "ambient_occlusion_requested": ambient_occlusion,
               "output_size_requested": output_size,
               "settings_path_requested": None if settings_path is None else str(settings_path),
               "settings_save_requested": save_settings,
               "native_dialogs_requested": native_dialogs,
               "startup_trace_requested": trace_startup,
               "startup_dialog_input_requested": exercise_startup_dialog,
               "install_complete_dialog_input_requested": exercise_install_dialogs,
               "spu_job_capture_requested": capture_spu_job,
               "spu_auxiliary_capture_requested_ea": None if capture_spu_aux_ea is None else f"0x{capture_spu_aux_ea:08X}",
               "spu_span_capture_requested": None if spans is None else [{"ea": f"0x{ea:08X}", "size": size} for ea, size in spans],
               "spu_span_capture_attempt_index": capture_spu_span_index,
               "features_accepted": [], "status": "starting"}
    receipt_path = folder / "receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    started = time.monotonic()
    with (folder / "boot.log").open("wb") as log:
        process = None
        scenario = None
        completion_scenario = None
        try:
            process = subprocess.Popen(command, cwd=folder, env=env, stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            receipt["pid"] = process.pid
            if exercise_startup_dialog:
                from native_dialog_scenario import StartupDialogScenario
                scenario=StartupDialogScenario(process,folder)
            if trace_startup:
                from native_startup_trace import wait_with_trace
                if scenario is not None:
                    def exercise_after_capture():
                        nonlocal completion_scenario
                        if dialog_capture_complete(folder/"system-dialog.bmp"):
                            scenario.tick()
                        # Fresh runs start with generation1; only the known
                        # generation2 completion notice may receive a second input.
                        # Every other later message fails the exact-text check.
                        if (exercise_install_dialogs and scenario.status == "sent" and
                                dialog_capture_complete(folder/"system-dialog-2.bmp")):
                            if completion_scenario is None:
                                completion_scenario = StartupDialogScenario(process,folder,stage="install-complete")
                            completion_scenario.tick()
                    receipt["exit_code"]=wait_with_trace(process,timeout,folder,on_poll=exercise_after_capture)
                else:
                    receipt["exit_code"] = wait_with_trace(process, timeout, folder)
            else:
                receipt["exit_code"] = process.wait(timeout=timeout)
            receipt["status"] = "diagnostic-stopped" if receipt["exit_code"] == 86 else "process-exited"
        except subprocess.TimeoutExpired:
            process.kill()
            receipt["exit_code"] = process.wait(timeout=10)
            receipt["status"] = "time-budget-exhausted"
        except BaseException as exc:
            receipt["status"] = "interrupted-or-failed"
            receipt["error"] = type(exc).__name__
            raise
        finally:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            receipt["exit_code"] = process.returncode if process is not None else None
            if scenario is not None:
                receipt["startup_dialog_input"]=scenario.summary()
                action_file=folder/"startup-dialog-input.json"
                receipt["startup_dialog_input_sha256"]=sha(action_file) if action_file.is_file() else None
            if completion_scenario is not None:
                receipt["install_complete_dialog_input"] = completion_scenario.summary()
                action_file = folder/"install-complete-dialog-input.json"
                receipt["install_complete_dialog_input_sha256"] = sha(action_file) if action_file.is_file() else None
            receipt["elapsed_seconds"] = round(time.monotonic() - started, 3)
            if capture_directory is not None:
                from spu_job_capture import inspect_capture
                receipt["spu_job_capture"] = inspect_capture(capture_directory, expected_aux_ea=capture_spu_aux_ea,
                                                           expected_spans=spans, expected_span_index=capture_spu_span_index)
            receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    receipt["elapsed_seconds"] = round(time.monotonic() - started, 3)
    receipt["log_sha256"] = sha(folder / "boot.log")
    fault=folder/"native-fault.json"
    receipt["native_fault_sha256"]=sha(fault) if fault.is_file() else None
    log_text = (folder / "boot.log").read_text(encoding="utf-8", errors="replace")
    receipt.update(rendering_evidence(log_text, graphics=graphics, vulkan=vulkan))
    receipt["frame_captures"] = {str(p.relative_to(folder)): sha(p) for p in sorted(folder.glob("frame_*.bmp"))}
    receipt["system_dialog_captures"] = {str(p.relative_to(folder)): sha(p) for p in sorted(folder.glob("system-dialog*.bmp"))}
    if trace_startup:
        trace = folder / "startup-memory.jsonl"
        receipt["startup_memory_sha256"] = sha(trace) if trace.is_file() else None
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(str(receipt_path))
    return 1  # A diagnostic run cannot satisfy a game acceptance gate.


def parse_auxiliary_ea(value):
    if not re.fullmatch(r'0x[0-9a-fA-F]{1,8}', value) or not 0x10000 <= int(value, 16) <= 0xFFFFFFFC:
        raise argparse.ArgumentTypeError("EA must be 0x-prefixed hex with a four-byte span within 0x10000..0xFFFFFFFF")
    return int(value, 16)


def parse_capture_span(value):
    match = re.fullmatch(r'(0x[0-9a-fA-F]{1,8}):([1-9][0-9]{0,5})', value)
    if not match:
        raise argparse.ArgumentTypeError("span must be 0xEA:SIZE with a decimal byte count")
    try:
        return normalize_capture_spans([(int(match[1], 16), int(match[2], 10))])[0]
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def parse_capture_span_index(value):
    if not re.fullmatch(r'0|[1-9][0-9]?', value) or int(value) >= 16:
        raise argparse.ArgumentTypeError("capture attempt index must be 0..15")
    return int(value)


def parse_ambient_occlusion(value):
    if not isinstance(value, str) or value not in ("off", "cacao"):
        raise ValueError("ambient occlusion must be off or cacao")
    return value


def parse_upscaler(value):
    if type(value) is not str or value not in ("off", "fsr1"):
        raise ValueError("upscaler must be off or fsr1")
    return value


def parse_antialiasing(value):
    if type(value) is not str or value not in ("off", "fxaa"):
        raise ValueError("anti-aliasing must be off or fxaa")
    return value


def parse_anisotropy(value):
    if type(value) is not str or value not in ("auto", "0", "1", "2", "4", "8", "16"):
        raise ValueError("anisotropy must be auto, 0, 1, 2, 4, 8 or 16")
    return "auto" if value == "0" else value


def parse_output_size(value):
    parts = value.split("x") if isinstance(value, str) else []
    if (len(parts) != 2 or any(not part or not part.isascii() or not part.isdecimal()
                              or len(part) > 10 for part in parts)):
        raise ValueError("output size must be WIDTHxHEIGHT with dimensions 1..8192")
    width, height = map(int, parts)
    if not (1 <= width <= 8192 and 1 <= height <= 8192):
        raise ValueError("output size dimensions must be 1..8192")
    return f"{width}x{height}"


class UniqueResolutionOption(argparse.Action):
    def __call__(self, parser, namespace, value, option_string=None):
        if getattr(namespace, self.dest) is not None:
            raise argparse.ArgumentError(self, "option may only be specified once")
        setattr(namespace, self.dest, value)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    add_path_arguments(parser)
    parser.add_argument("--timeout", type=int, default=30, choices=range(1, 61))
    parser.add_argument("--allow-hdd-writes", action="store_true",
                        help="allow native guarded writes inside this new run's private VFS volumes")
    parser.add_argument("--d3d12", action="store_true", help="request the experimental Windows renderer and FIFO service")
    parser.add_argument("--vulkan", action="store_true", help="request the native Vulkan renderer and FIFO service on Windows or Linux")
    parser.add_argument("--render-scale", type=int, choices=range(1, 5), action=UniqueResolutionOption,
                        help="internal Vulkan raster scale, integer 1..4; independent of output size")
    parser.add_argument("--output-size", type=parse_output_size, action=UniqueResolutionOption,
                        help="requested Vulkan window size WIDTHxHEIGHT, dimensions 1..8192")
    parser.add_argument("--anisotropy", type=parse_anisotropy, action=UniqueResolutionOption,
                        help="Vulkan anisotropy: auto (guest), 1 (off), 2, 4, 8 or 16; capped by hardware")
    parser.add_argument("--upscaler", type=parse_upscaler, action=UniqueResolutionOption,
                        help="Vulkan spatial upscaler: off or fsr1 (EASU + RCAS)")
    parser.add_argument("--ambient-occlusion", type=parse_ambient_occlusion, action=UniqueResolutionOption,
                        help="optional Vulkan ambient occlusion: off or cacao; requires a CACAO-enabled build")
    parser.add_argument("--anti-aliasing", dest="antialiasing", type=parse_antialiasing, action=UniqueResolutionOption,
                        help="optional Vulkan postprocess anti-aliasing: off or fxaa")
    parser.add_argument("--settings", action=UniqueResolutionOption,
                        help="absolute graphics settings path; invalid files retain defaults and are not overwritten")
    parser.add_argument("--save-settings", action="store_true",
                        help="save effective graphics settings after successful renderer initialization; requires --settings")
    parser.add_argument("--native-dialogs", action="store_true", help="present platform system dialogs without starting the RSX renderer")
    parser.add_argument("--trace-startup", action="store_true", help="passively observe this Windows diagnostic's startup predicates")
    parser.add_argument("--capture-spu-job", action="store_true", help="capture bounded private SPU job snapshots before strict missing-workload rejection")
    parser.add_argument("--capture-spu-aux-ea", type=parse_auxiliary_ea,
                        help="capture exactly four checked private bytes at this explicit EA; requires --capture-spu-job")
    parser.add_argument("--capture-spu-span", type=parse_capture_span, action="append",
                        help="capture one nominated EA:SIZE span; repeat at most twice, total at most 128 KiB")
    parser.add_argument("--capture-spu-span-index", type=parse_capture_span_index,
                        help="read nominated spans only on this global capture attempt ordinal (0..15)")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--exercise-startup-dialog",action="store_true",
                        help="one recorded real Cancel control click on the exact owned startup notice; requires tracing/graphics/contained writes")
    modes.add_argument("--exercise-install-dialogs",action="store_true",
                       help="record real Cancel clicks on the exact startup and installation-completion notices; requires tracing/graphics/contained writes")
    args = parser.parse_args(argv)
    try:
        result = run(args.timeout, args.allow_hdd_writes, config=config_from_arguments(args),
                   graphics=args.d3d12, trace_startup=args.trace_startup,
                   exercise_startup_dialog=args.exercise_startup_dialog,
                   exercise_install_dialogs=args.exercise_install_dialogs,
                   capture_spu_job=args.capture_spu_job, capture_spu_aux_ea=args.capture_spu_aux_ea,
                   native_dialogs=args.native_dialogs, capture_spu_spans=args.capture_spu_span,
                   capture_spu_span_index=args.capture_spu_span_index, plan=not args.execute, vulkan=args.vulkan,
                   render_scale=args.render_scale, output_size=args.output_size,
                   settings_path=args.settings, save_settings=args.save_settings, anisotropy=args.anisotropy,
                   antialiasing=args.antialiasing, upscaler=args.upscaler, ambient_occlusion=args.ambient_occlusion)
        if not args.execute:
            print(json.dumps(result, indent=2))
            return 0
        return result
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"native diagnostic failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
