"""Receipt trust checks with synthetic transcripts; no C or backend execution."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest

spec = importlib.util.spec_from_file_location("sampler_runner", Path(__file__).with_name("run_vulkan_sampler_cpu.py"))
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def transcript(failed=()):
    failed = set(failed)
    return "".join(f"case={name} outcome={'FAIL' if name in failed else 'PASS'}\n"
                   for name in sorted(runner.CASE_NAMES)) + (
        f"cases={len(runner.CASE_NAMES)} failed={len(failed)} gpu=0 loader=0 renderer_init=0\n")


class ReceiptTests(unittest.TestCase):
    def test_complete_pass(self):
        self.assertTrue(runner.classify_transcript(transcript(), 0, "fixed")["accepted"])

    def test_fail_record_with_zero_exit_is_rejected(self):
        result = runner.classify_transcript(transcript({"adjacent_min_lod"}), 0, "fixed")
        self.assertTrue(result["case_contract_complete"])
        self.assertFalse(result["tests_passed"])
        self.assertFalse(result["accepted"])

    def test_nonzero_or_absent_exit_is_rejected(self):
        for code in (1, 86, None):
            with self.subTest(exit_code=code):
                self.assertFalse(runner.classify_transcript(transcript(), code, "fixed")["accepted"])

    def test_exact_baseline_has_no_false_pass(self):
        result = runner.classify_transcript(transcript(runner.BASELINE_FAILURES), 1, "baseline")
        self.assertTrue(result["accepted"])
        self.assertTrue(result["expected_defect_reproduced"])
        self.assertFalse(result["tests_passed"])
        self.assertFalse(runner.classify_transcript(transcript(runner.BASELINE_FAILURES), 0, "baseline")["accepted"])

    def test_extra_or_missing_baseline_failure_rejected(self):
        for failures in (runner.BASELINE_FAILURES | {"padding_is_not_identity"}, {"adjacent_min_lod"}):
            self.assertFalse(runner.classify_transcript(transcript(failures), 1, "baseline")["accepted"])

    def test_missing_duplicate_unknown_and_malformed_records(self):
        good = transcript()
        lines = good.splitlines(keepends=True)
        for text in ("".join(lines[1:]), lines[0] + good,
                     good.replace(sorted(runner.CASE_NAMES)[0], "unrecognized_case"),
                     good.replace("outcome=PASS", "outcome=UNKNOWN", 1),
                     good + "case=unrecognized_case outcome=UNKNOWN\n"):
            with self.subTest(text=text[:60]):
                self.assertFalse(runner.classify_transcript(text, 0, "fixed")["accepted"])

    def test_summary_mismatch_missing_duplicate_and_driver_flag(self):
        good = transcript()
        for text in (good.replace("failed=0", "failed=1"),
                     "\n".join(good.splitlines()[:-1]),
                     good + good.splitlines()[-1] + "\n", good.replace("gpu=0", "gpu=1")):
            self.assertFalse(runner.classify_transcript(text, 0, "fixed")["accepted"])


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ReceiptTests))
    print(json.dumps({"schema": "folklore-sampler-receipt-tests-v1", "tests_run": result.testsRun,
                      "exit_code": 0 if result.wasSuccessful() else 1,
                      "gpu_executed": False, "compiler_invoked": False}))
    sys.exit(0 if result.wasSuccessful() else 1)
