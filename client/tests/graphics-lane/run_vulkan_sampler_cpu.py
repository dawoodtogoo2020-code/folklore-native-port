"""Extract only Vulkan sampler-cache source for an isolated CPU unit fixture.

Default mode prepares source only. Compile/run requires explicit --mode and
--compiler. Outputs must be fresh and contained in this test workspace.
No SDK imports or Vulkan linkage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys


CASE_NAMES = {
    "adjacent_min_lod", "adjacent_max_lod", "all_guest_lod_quantums",
    "every_filter_and_wrap_field", "repeat_and_interleaving", "padding_is_not_identity",
    "caller_mutation_preserves_snapshot", "effective_lod_clamps_are_preserved",
    "creation_failure_is_not_cached", "full_cache_retains_hits_and_fallback",
    "normalized_filter_and_wrap_aliases", "teardown_destroys_unique_successes_once",
}
BASELINE_FAILURES = {"adjacent_min_lod", "adjacent_max_lod", "all_guest_lod_quantums"}


def classify_transcript(stdout: str, exit_code: int | None, mode: str) -> dict:
    observed = re.findall(r"^case=(\w+) outcome=(PASS|FAIL)$", stdout, re.M)
    failures = [name for name, outcome in observed if outcome == "FAIL"]
    summaries = re.findall(r"^cases=(\d+) failed=(\d+) gpu=0 loader=0 renderer_init=0$", stdout, re.M)
    complete = (len(observed) == len(CASE_NAMES)
                and {name for name, _ in observed} == CASE_NAMES
                and len(re.findall(r"^case=", stdout, re.M)) == len(CASE_NAMES)
                and len(re.findall(r"^cases=", stdout, re.M)) == 1
                and len(summaries) == 1
                and tuple(map(int, summaries[0])) == (len(CASE_NAMES), len(failures)))
    passed = complete and exit_code == 0 and not failures
    reproduced = complete and exit_code == 1 and set(failures) == BASELINE_FAILURES
    return {"case_contract_complete": complete, "failed_cases": failures,
            "tests_passed": passed, "expected_defect_reproduced": reproduced,
            "accepted": reproduced if mode == "baseline" else passed}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def function(source: str, name: str) -> str:
    match = re.search(r"^static [^\n;]*\b" + re.escape(name) + r"\([^)]*\)\s*\{", source, re.M)
    if not match:
        raise ValueError(f"missing audited function: {name}")
    level = 1
    for index in range(match.end(), len(source)):
        level += (source[index] == "{") - (source[index] == "}")
        if level == 0:
            return source[match.start():index + 1]
    raise ValueError(f"unclosed function: {name}")


def extract(source: str, header: str) -> str:
    desc = re.search(r"typedef struct rsx_be_sampler_desc\s*\{[^}]*\}\s*rsx_be_sampler_desc;", header, re.S)
    cache = re.search(r"^static struct\s*\{[^}]*\}\s*s_esamp\[VK_ENG_SAMP_CACHE\];", source, re.M)
    limit = re.search(r"^#define VK_ENG_SAMP_CACHE\s+(\d+)\s*$", source, re.M)
    count = re.search(r"^static u32 s_esamp_count;", source, re.M)
    if not all((desc, cache, limit, count)):
        raise ValueError("cache/type shape changed; review extraction rather than inventing source")
    if limit.group(1) != "64":
        raise ValueError("cache capacity changed; review bounded fixture before execution")
    shutdown = function(source, "vk_eng2_shutdown")
    cleanup = re.search(r"for \(u32 i = 0; i < s_esamp_count; i\+\+\) "
                        r"pvkDestroySampler\(s_vk.device, s_esamp\[i\].s, NULL\);\s*"
                        r"s_esamp_count = 0;", shutdown)
    if not cleanup:
        raise ValueError("actual sampler teardown changed; review extraction")
    metadata = "#define FOLKLORE_SAMPLER_ANISOTROPY_FIELDS 1" if "anisotropy_code" in desc.group(0) else ""
    return "\n\n".join((metadata, desc.group(0), limit.group(0), cache.group(0), count.group(0),
                          function(source, "vk_gcm_wrap"), function(source, "vk_eng_sampler"),
                          "static void production_sampler_teardown(void)\n{\n" + cleanup.group(0) + "\n}")) + "\n"


def execute(command: list[str], cwd: Path, seconds: int) -> dict:
    try:
        completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=seconds)
        return {"argv": command, "exit_code": completed.returncode, "stdout": completed.stdout,
                "stderr": completed.stderr, "timed_out": False, "terminal_collected": True}
    except subprocess.TimeoutExpired as error:
        # subprocess.run kills and collects its owned child before raising.
        def decoded(value):
            return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""
        return {"argv": command, "exit_code": None, "stdout": decoded(error.stdout),
                "stderr": decoded(error.stderr), "timed_out": True, "terminal_collected": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk-root", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--mode", choices=("prepare", "baseline", "fixed"), default="prepare")
    parser.add_argument("--compiler", type=Path)
    args = parser.parse_args()
    root, run = args.sdk_root.resolve(strict=True), args.run_dir.resolve()
    lane = Path(__file__).resolve().parent
    if not run.is_relative_to(lane / "runs"):
        parser.error("run-dir must be a fresh child of this lane's tests/graphics-lane/runs")
    if args.mode != "prepare" and args.compiler is None:
        parser.error("compile/run requires an explicit compiler")
    source = root / "libs/video/rsx_vulkan_backend.c"
    header = root / "libs/video/rsx_draw_engine.h"
    fixture = lane / "vulkan_sampler_cache_cases.c"
    inputs = {str(path): sha(path) for path in (source, header, fixture, Path(__file__).resolve())}
    extracted = extract(source.read_text(encoding="utf-8"), header.read_text(encoding="utf-8"))
    run.mkdir(parents=True, exist_ok=False)
    include_path = run / "production_sampler.inc"
    include_path.write_text(extracted, encoding="utf-8", newline="\n")
    fixture_copy = run / "fixture.c"
    fixture_copy.write_bytes(fixture.read_bytes())
    runner_copy = run / "runner_snapshot.py"
    runner_copy.write_bytes(Path(__file__).read_bytes())
    report = {"schema": "folklore-vulkan-sampler-cpu-v1", "mode": args.mode,
              "source_hashes": inputs, "extracted_sha256": sha(include_path),
              "compiler_invoked": False, "gpu_executed": False, "loader_linked": False,
              "renderer_initialized": False, "commands": [], "tests_passed": None,
              "fixture_snapshot_sha256": sha(fixture_copy), "runner_snapshot_sha256": sha(runner_copy)}
    exit_code = 0
    if args.mode != "prepare":
        compiler = args.compiler.resolve(strict=True)
        executable = run / "sampler-cache-cpu.exe"
        command = [str(compiler), "-std=c11", "-O1", "-Wall", "-Wextra", "-Werror",
                   "-I", str(run), str(fixture_copy), "-o", str(executable)]
        compiled = execute(command, run, 60)
        report.update(compiler_invoked=True, compiler_sha256=sha(compiler))
        report["commands"].append(compiled)
        if compiled["exit_code"] != 0:
            exit_code = 1
        else:
            completed = execute([str(executable)], run, 15)
            report["commands"].append(completed)
            report["executable_sha256"] = sha(executable)
            report.update(classify_transcript(completed["stdout"], completed["exit_code"], args.mode))
            exit_code = 0 if report["accepted"] else 1
    unchanged = all(sha(Path(path)) == digest for path, digest in inputs.items())
    report["source_inputs_unchanged"] = unchanged
    exit_code = exit_code if unchanged else 1
    report["exit_code"] = exit_code
    (run / "receipt.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"receipt": str(run / "receipt.json"), "mode": args.mode,
                      "exit_code": exit_code, "compiler_invoked": report["compiler_invoked"]}))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
