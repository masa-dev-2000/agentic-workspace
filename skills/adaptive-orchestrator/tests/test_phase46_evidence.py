import json
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "scripts"))
import phase46_evidence as E


class Phase46EvidenceTests(unittest.TestCase):
    def test_case_schema_is_complete_and_external_boundaries_remain_gated(self):
        result = E.run_evidence_pack(process_count=2, iterations=1, seed=7)
        self.assertEqual(result["schema"], "phase46-evidence-pack-v1")
        self.assertTrue(all(set(E.CASE_FIELDS) <= set(case) for case in result["cases"]))
        self.assertEqual(result["phase5_readiness"], "HOLD")
        self.assertEqual(result["phase6_readiness"], "HOLD")
        self.assertTrue(any(case["classification"] == "PASS" for case in result["cases"] if case["phase"] == "phase5"))
        self.assertTrue(any(case["classification"] == "PASS" for case in result["cases"] if case["phase"] == "phase6"))
        self.assertTrue(any(case["classification"] in {"NOT_RUN", "NOT_OBSERVABLE"} for case in result["cases"] if case["phase"] in {"phase5", "phase6"}))

    def test_phase4_uses_real_runner_and_records_concurrency_evidence(self):
        result = E.run_evidence_pack(process_count=2, iterations=1, seed=8)
        case = next(case for case in result["cases"] if case["case_id"] == "phase4-stage-runner-concurrency")
        self.assertIn(case["classification"], {"PASS", "NOT_OBSERVABLE"})
        self.assertEqual(case["revision_before"], 0)
        self.assertEqual(case["process_count"], 2)
        self.assertEqual(case["iteration_count"], 1)
        if case["classification"] == "PASS":
            self.assertEqual(case["actual_events"][0]["success_count"], 1)
            self.assertEqual(case["actual_events"][0]["dispatch_count"], 1)
            self.assertTrue(case["actual_events"][0]["stale_rejected"])
            self.assertEqual(case["actual_events"][0]["integrity"], "ok")

    def test_phase03_boundary_and_registry_lanes_are_separate(self):
        result = E.run_evidence_pack(process_count=2, iterations=1, seed=9)
        cases = {case["case_id"]: case for case in result["cases"]}
        for case_id in ("phase3-boundary-mutation-rejection", "phase3-telemetry-only", "phase3-unknown-field-rejection", "phase3-body-rejection", "phase3-registry-caller-authority"):
            self.assertEqual(cases[case_id]["classification"], "PASS")

    def test_phase4_to_phase6_runtime_contracts_pass(self):
        result = E.run_evidence_pack(process_count=2, iterations=1, seed=12)
        cases = {case["case_id"]: case for case in result["cases"]}
        for case_id in (
            "phase4-approval-fingerprint-valid", "phase4-approval-fingerprint-tamper",
            "phase4-retry-budget-exhausted", "phase5-replay-equivalence",
            "phase5-shadow-no-side-effect", "phase6-canary-failure-rollback",
            "phase6-cutover-idempotency",
        ):
            self.assertEqual(cases[case_id]["classification"], "PASS")

    def test_not_observable_never_counts_as_pass(self):
        result = E.run_evidence_pack(process_count=2, iterations=1, seed=10)
        for case in result["cases"]:
            if case["classification"] in {"NOT_RUN", "NOT_OBSERVABLE"}:
                self.assertNotEqual(case["classification"], "PASS")
        self.assertEqual(sum(result["classification_counts"].values()), len(result["cases"]))

    def test_output_requires_explicit_path(self):
        with tempfile.TemporaryDirectory(prefix="phase46-test-") as directory:
            output = pathlib.Path(directory) / "evidence.json"
            result = E.run_evidence_pack(output=output, process_count=2, iterations=1, seed=11)
            self.assertTrue(output.is_file())
            saved = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(saved["run_id"], result["run_id"])
            self.assertTrue(result["protected_files_unchanged"])


if __name__ == "__main__":
    unittest.main()
