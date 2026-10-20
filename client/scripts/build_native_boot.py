"""Build the private diagnostic and bind its host/source state to the binary."""
import datetime
import argparse
import hashlib
import json
import os
import re
import stat
from pathlib import PurePosixPath
from pathlib import Path
import subprocess
import sys

GIT = r"C:\Program Files\Git\cmd\git.exe"
BIN = Path(r"D:\Tools\msys64\clang64\bin")
# A complete 256 KiB SPU image expands into several MiB of readable AOT C.
PRIVATE_NATIVE_JOB_FILE_MAX = 8 * 1024 * 1024
PRIVATE_NATIVE_JOB_TOTAL_MAX = 64 * 1024 * 1024
PRIVATE_NATIVE_JOB_FILE_COUNT_MAX = 1024


def host_config(platform=None, root=None, available=None, *, source_root=None,
                sdk_root=None, lift_root=None, dependency_root=None, input_root=None,
                firmware_root=None, private_jobs_root=None, output_root=None):
    platform = platform or os.name
    root = Path(root) if root is not None else (Path(r"Q:\folklore") if platform == "nt" else Path("/mnt/q/folklore"))
    if platform not in ("nt", "posix"):
        raise ValueError("unsupported diagnostic host")
    if platform == "nt":
        tools = {name: BIN / filename for name, filename in {
            "cc": "clang.exe", "cxx": "clang++.exe", "cmake": "cmake.exe", "ninja": "ninja.exe"}.items()}
        tools["git"] = Path(GIT)
    else:
        present = available or (lambda path: path.is_file() and os.access(path, os.X_OK))
        tools = {}
        for name, candidates in {"cc": ("/usr/local/bin/clang", "/usr/bin/clang"),
                                  "cxx": ("/usr/local/bin/clang++", "/usr/bin/clang++"),
                                  "cmake": ("/usr/bin/cmake", "/usr/local/bin/cmake"),
                                  "ninja": ("/usr/bin/ninja", "/usr/local/bin/ninja"),
                                  "git": ("/usr/bin/git", "/usr/local/bin/git")}.items():
            choice = next((Path(value) for value in candidates if present(Path(value))), None)
            if choice is None:
                raise ValueError(f"required supported POSIX tool missing: {name}")
            tools[name] = choice
    work = root / "work"
    source = Path(source_root) if source_root is not None else Path(__file__).resolve().parents[1]
    output = Path(output_root) if output_root is not None else work
    return {"platform": platform, "root": root, "work": work,
            "source_root": source, "sdk": Path(sdk_root) if sdk_root is not None else work / "ps3recomp-strict",
            "native": source / "native",
            "lift": Path(lift_root) if lift_root is not None else work / "ppu-full/strict-recompiled", "tools": tools,
            "input": Path(input_root) if input_root is not None else work / "input/candidate-usa",
            "firmware": Path(firmware_root) if firmware_root is not None else work / "tools/rpcs3-reference/dev_flash",
            "private_jobs": Path(private_jobs_root) if private_jobs_root is not None else work / "private-native/third-v1",
            "output_root": output, "isolated_outputs": output_root is not None,
            "build": output / ("build-folklore-gnu" if platform == "nt" else "build-folklore-linux"),
            "temporary": output / "tmp", "evidence": output / "evidence", "runs": output / "native-runs",
            "binary_name": "FolkloreBoot.exe" if platform == "nt" else "FolkloreBoot",
            "prefix": Path(dependency_root) if dependency_root is not None else work / "deps/install-linux"}


def overlaps(left, right):
    """Compare resolved ancestry and existing file identities (also drive aliases)."""
    left, right = Path(left).resolve(), Path(right).resolve()
    if left.is_relative_to(right) or right.is_relative_to(left):
        return True
    for leaf, ancestor in ((left, right), (right, left)):
        if ancestor.exists():
            for item in (leaf, *leaf.parents):
                if item.exists() and os.path.samefile(item, ancestor):
                    return True
    return False


def path_bindings(config):
    names = ("root", "source_root", "native", "sdk", "lift", "prefix", "input", "firmware",
             "private_jobs", "output_root", "build", "temporary", "evidence", "runs")
    return {name: str(Path(config[name]).resolve()) for name in names}


def validate_paths(config, run_inputs=False):
    """Read-only planning guard, not a concurrent-filesystem security sandbox."""
    bindings = path_bindings(config)
    for name in bindings:
        if not Path(config[name]).is_absolute():
            raise ValueError(f"{name} must be an absolute path")
    protected = [config[name] for name in
                 ("source_root", "native", "sdk", "lift", "prefix", "input", "firmware", "private_jobs")]
    outputs = [config[name] for name in ("build", "temporary", "evidence", "runs")]
    if config["output_root"].exists() and not config["output_root"].is_dir():
        raise ValueError("output root must be a directory")
    if config["isolated_outputs"]:
        for item in protected:
            if overlaps(config["output_root"], item):
                raise ValueError("output root overlaps a protected input")
        for shared in ("build-folklore-gnu", "build-folklore-linux", "tmp", "evidence", "native-runs"):
            if overlaps(config["output_root"], config["work"] / shared):
                raise ValueError("output root overlaps shared outputs")
    elif Path(config["source_root"]).resolve() != (config["root"] / "outputs/folklore-native").resolve():
        raise ValueError("isolated checkout requires an explicit output root")
    for index, output in enumerate(outputs):
        if not Path(output).resolve().is_relative_to(Path(config["output_root"]).resolve()):
            raise ValueError("mutable output escapes output root")
        if any(overlaps(output, item) for item in protected):
            raise ValueError("mutable output overlaps a protected input")
        if any(overlaps(output, other) for other in outputs[:index]):
            raise ValueError("mutable output directories overlap")
        if output.exists() and not output.is_dir():
            raise ValueError("mutable output must be a directory")
    for name in (config["binary_name"], "build-manifest.json", "CMakeCache.txt", "compile_commands.json"):
        leaf = config["build"] / name
        if leaf.exists() or leaf.is_symlink():
            info = leaf.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink > 1 or
                    getattr(info, "st_file_attributes", 0) & 0x400):
                raise ValueError("mutable build file is not an ordinary unaliased file")
    for name in ("source_root", "sdk", "lift") + (("prefix",) if config["platform"] == "posix" else ()):
        if not config[name].is_dir():
            raise ValueError(f"required input directory missing: {name}")
    for item in (config["native"] / "CMakeLists.txt", config["lift"] / "ppu_recomp.h"):
        if not item.is_file():
            raise ValueError("required native/lift input missing")
    if run_inputs and (not config["input"].is_dir() or not config["firmware"].is_dir()):
        raise ValueError("required game input or firmware directory missing")
    return bindings


def file_identity(config, path):
    path, root = Path(path).resolve(), Path(config["root"]).resolve()
    return str(path.relative_to(root)) if path.is_relative_to(root) else str(path)


def add_path_arguments(parser):
    parser.add_argument("--root", type=Path, help="task input workspace (legacy input defaults only)")
    for name in ("source", "sdk", "lift", "dependency", "input", "firmware", "private-jobs", "output"):
        parser.add_argument(f"--{name}-root", type=Path)
    parser.add_argument("--legacy-main", action="store_true",
                        help="deliberately select the main source and legacy shared output layout")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--plan", action="store_true", help="print a read-only plan (default)")
    modes.add_argument("--execute", action="store_true", help="execute; requires a separately admitted resource lease")


def config_from_arguments(args):
    root = args.root or (Path(r"Q:\folklore") if os.name == "nt" else Path("/mnt/q/folklore"))
    if args.legacy_main and (args.output_root is not None or args.source_root is not None):
        raise ValueError("legacy-main cannot mix with source/output overrides")
    if args.output_root is None and not args.legacy_main:
        raise ValueError("select an explicit output root or deliberate legacy-main mode")
    return host_config(root=root,
                       source_root=(root / "outputs/folklore-native" if args.legacy_main else
                                    args.source_root or Path(__file__).resolve().parents[1]),
                       sdk_root=args.sdk_root, lift_root=args.lift_root,
                       dependency_root=args.dependency_root, input_root=args.input_root,
                       firmware_root=args.firmware_root, private_jobs_root=args.private_jobs_root,
                       output_root=args.output_root)


def controlled_environment(config, temporary):
    if config["platform"] == "posix":
        return {"PATH": "/usr/local/bin:/usr/bin:/bin", "TMPDIR": str(temporary),
                "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}
    keys = ("SystemRoot", "WINDIR", "COMSPEC", "PATHEXT", "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS")
    env = {key: os.environ[key] for key in keys if key in os.environ}
    env.update({"PATH": str(BIN) + ";" + r"C:\Windows\System32",
                "TEMP": str(temporary), "TMP": str(temporary), "PYTHONDONTWRITEBYTECODE": "1"})
    return env


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def git_command(config, arguments):
    command = [str(config["tools"]["git"])]
    pointer = config["sdk"] / ".git"
    if config["platform"] == "posix" and pointer.is_file():
        text = pointer.read_text(encoding="utf-8").strip()
        if not text.startswith("gitdir: "):
            raise ValueError("unrecognized worktree metadata")
        target = text[8:].replace("\\", "/")
        if len(target) > 2 and target[0].isalpha() and target[1:3] == ":/":
            target = "/mnt/" + target[0].lower() + target[2:]
            command += ["--git-dir=" + target, "--work-tree=" + str(config["sdk"])]
    return command + list(arguments)


def private_native_job_inputs(config, profile_root=None):
    """Resolve one reviewed local source package, never a guest registration.

    Absence explicitly builds an empty native job registry. A present package
    must be complete and hash-bound; partial/stale packages cannot silently
    become an empty-registry build. No source or protected bytes are exported.
    """
    base = Path(config["work"]).resolve()
    root = Path(profile_root) if profile_root is not None else Path(config["private_jobs"])
    if not root.resolve().is_relative_to(base) and not root.resolve().is_relative_to(Path(config["source_root"]).resolve() / "work"):
        raise ValueError("private native job package must remain in the task workspace")
    if not root.exists() and not root.is_symlink():
        return None

    def ordinary(path):
        try:
            info = path.lstat()
        except OSError as error:
            raise ValueError("private native job input is unavailable") from error
        if (stat.S_ISLNK(info.st_mode) or
                getattr(info, "st_file_attributes", 0) & 0x400 or
                not path.resolve().is_relative_to(root.resolve())):
            raise ValueError("private native job inputs must be ordinary owned paths")
        return info

    if not stat.S_ISDIR(ordinary(root).st_mode):
        raise ValueError("private native job package is not a directory")
    manifest = root / "profile-manifest.json"
    info = ordinary(manifest)
    if not stat.S_ISREG(info.st_mode) or info.st_size > 256 * 1024:
        raise ValueError("invalid private native job manifest")

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate private native job manifest field")
            result[key] = value
        return result

    try:
        data = json.loads(manifest.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid private native job manifest") from error
    if not isinstance(data, dict) or data.get("schema") != "folklore-private-native-jobs-v1":
        raise ValueError("unsupported private native job manifest")
    listed = data.get("files")
    if not isinstance(listed, dict) or not 1 <= len(listed) <= PRIVATE_NATIVE_JOB_FILE_COUNT_MAX:
        raise ValueError("invalid private native job file set")
    folded = set()
    for name, digest in listed.items():
        if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_./-]+", name) or
                PurePosixPath(name).is_absolute() or PurePosixPath(name).as_posix() != name or
                any(part in (".", "..") for part in name.split("/")) or
                PurePosixPath(name).suffix not in (".c", ".h") or
                name.casefold() in folded or not isinstance(digest, str) or
                not re.fullmatch(r"[0-9a-fA-F]{64}", digest)):
            raise ValueError("invalid private native job file identity")
        folded.add(name.casefold())
    selected = data.get("native_translation_unit")
    if not isinstance(selected, str) or selected not in listed or PurePosixPath(selected).suffix != ".c":
        raise ValueError("private native job entry is not a bound C source")
    extra = data.get("extra_translation_units", [])
    if (not isinstance(extra, list) or len(extra) > 64 or any(not isinstance(name, str) for name in extra) or len(set(extra)) != len(extra) or
            any(not isinstance(name, str) or name not in listed or
                PurePosixPath(name).suffix != ".c" or name == selected for name in extra)):
        raise ValueError("additional native translation units must be distinct bound C sources")
    paths, pending, total, directories = set(), [root], 0, 0
    while pending:
        directory = pending.pop()
        directories += 1
        if directories > PRIVATE_NATIVE_JOB_FILE_COUNT_MAX:
            raise ValueError("private native job package has too many directories")
        for path in directory.iterdir():
            info = ordinary(path)
            if stat.S_ISDIR(info.st_mode):
                pending.append(path)
                continue
            if path == manifest:
                continue
            name = path.relative_to(root).as_posix()
            if not stat.S_ISREG(info.st_mode) or name not in listed:
                raise ValueError("undeclared private native job input")
            total += info.st_size
            if (info.st_size > PRIVATE_NATIVE_JOB_FILE_MAX or total > PRIVATE_NATIVE_JOB_TOTAL_MAX or
                    sha(path).lower() != listed[name].lower()):
                raise ValueError("private native job input size or hash differs")
            if name.casefold().startswith(("runtime/", "include/")):
                parts = PurePosixPath(name).parts
                current_header = Path(config["sdk"]) / PurePosixPath(parts[0].casefold(), *parts[1:])
                if not current_header.is_file() or sha(current_header).lower() != listed[name].lower():
                    raise ValueError("private native job SDK copy is stale")
            paths.add(path)
    if len(paths) != len(listed):
        raise ValueError("private native job input is missing")
    return {"manifest": manifest, "source": root / selected, "extra_sources": [root / name for name in extra], "files": paths | {manifest}}


def source_state(config=None):
    config = config or host_config()
    root, sdk, native, lifted = (config[key] for key in ("root", "sdk", "native", "lift"))
    names = subprocess.run(git_command(config, ["ls-files", "-z", "--cached", "--others", "--exclude-standard"]),
                           cwd=sdk, capture_output=True, check=True).stdout.decode("utf-8").split("\0")
    suffixes = {".c", ".cpp", ".h", ".hpp", ".inc", ".cmake", ".py", ".in"}
    paths = {sdk / name for name in names if name and (Path(name).suffix.lower() in suffixes or name.endswith("CMakeLists.txt"))}
    paths.update(native.glob("*.cpp"))
    paths.update(native.glob("*.h"))
    paths.update(native.glob("*.hpp"))
    paths.add(native / "CMakeLists.txt")
    paths.update(lifted.glob("ppu_recomp_*.cpp"))
    paths.add(lifted / "ppu_recomp.h")
    private = private_native_job_inputs(config)
    if private is not None:
        paths.update(private["files"])
    for name in ("build_native_boot.py", "run_native_boot.py", "spu_job_capture.py"):
        for folder in (config["source_root"] / "scripts", Path(__file__).resolve().parent):
            recipe = folder / name
            if recipe.is_file():
                paths.add(recipe)
    return {file_identity(config, path): sha(path) for path in sorted(paths)}


def build_commands(config):
    tools = config["tools"]
    private = private_native_job_inputs(config)
    command = [str(tools["cmake"]), "-S", str(config["native"]), "-B", str(config["build"]), "-G", "Ninja",
               f"-DCMAKE_C_COMPILER={tools['cc']}", f"-DCMAKE_CXX_COMPILER={tools['cxx']}",
               "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
               "-DPS3RECOMP_FFMPEG_RUNTIME=ON",
               f"-DPython3_EXECUTABLE={Path(sys.executable).as_posix()}",
               f"-DPS3RECOMP_DIR={config['sdk'].as_posix()}", f"-DRECOMP_DIR={config['lift'].as_posix()}",
               "-DFOLKLORE_PRIVATE_NATIVE_JOB_SOURCE=" + (private["source"].as_posix() if private else "")]
    command += ["-DFOLKLORE_PRIVATE_NATIVE_EXTRA_SOURCES=" +
                (";".join(path.as_posix() for path in private["extra_sources"]) if private else "")]
    if config.get("vulkan_build"):
        command += ["-DPS3RECOMP_RSX_VULKAN=ON"]
        if config["platform"] == "nt":
            command += ["-Dglslang_DIR=" + (BIN.parent / "lib/cmake/glslang").as_posix()]
    if config["platform"] == "nt":
        command += ["-DPS3RECOMP_FFMPEG_INCLUDE_DIR=" + (BIN.parent / "include").as_posix()]
        command += ["-DPS3RECOMP_PAD_USE_SDL2=ON"]
    if config["platform"] == "posix":
        command += [f"-DCMAKE_PREFIX_PATH={config['prefix']}", f"-DCMAKE_MAKE_PROGRAM={tools['ninja']}"]
    return [command, [str(tools["cmake"]), "--build", str(config["build"]), "--parallel", "2"]]


def build_plan(config):
    return {"schema": "folklore-build-plan-v1", "host_platform": config["platform"],
            "paths": validate_paths(config), "commands": build_commands(config),
            "executes": False, "creates_directories": False, "playable_game": False}


def build(config):
    validate_paths(config)
    before = source_state(config)
    commands = build_commands(config)
    build = config["build"]
    for path in (config["temporary"], config["evidence"]):
        path.mkdir(parents=True, exist_ok=True)
    validate_paths(config)
    env = controlled_environment(config, config["temporary"])
    (build / "build-manifest.json").unlink(missing_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    logs = []
    for stage, command in zip(("configure", "build"), commands):
        path = config["evidence"] / f"folklore-{stage}-{stamp}.txt"
        logs.append(str(path))
        with path.open("wb") as output:
            result = subprocess.run(command, env=env, cwd=config["source_root"], stdout=output, stderr=subprocess.STDOUT)
        if result.returncode:
            print(f"{stage} failed ({result.returncode}): {path}", file=sys.stderr)
            return 1
    after = source_state(config)
    if before != after:
        print("source changed during build; no build manifest accepted", file=sys.stderr)
        return 1
    binary = build / config["binary_name"]
    manifest = {"schema": "folklore-build-manifest-v1", "started_utc": stamp,
                "finished_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "commands": commands, "logs": logs, "sources": after,
                "host_platform": config["platform"], "environment": env,
                "paths": path_bindings(config),
                "tools": {str(path): sha(path) for path in config["tools"].values()},
                "binary": str(binary), "binary_sha256": sha(binary),
                "compile_commands_sha256": sha(build / "compile_commands.json"),
                "cmake_cache_sha256": sha(build / "CMakeCache.txt"),
                "playable_game": False}
    manifest_path = build / "build-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(str(manifest_path))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    add_path_arguments(parser)
    parser.add_argument("--vulkan-build", action="store_true", help="build the native Vulkan backend")
    args = parser.parse_args(argv)
    try:
        config = config_from_arguments(args)
        config["vulkan_build"] = args.vulkan_build
        if not args.execute:
            print(json.dumps(build_plan(config), indent=2))
            return 0
        return build(config)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"native build failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
