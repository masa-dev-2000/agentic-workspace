import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


C = load_module("phase03_contract", ROOT / "scripts" / "phase03_contract.py")
R = load_module("registry_validator", ROOT / "scripts" / "registry_validator.py")


def envelope(**changes):
    value = {
        "project_id": "p1",
        "task_id": "t1",
        "run_id": "r1",
        "attempt_id": "a1",
        "trace_id": "x1",
        "origin": "user",
        "canonical_entry": "execution-engine",
        "registry_revision": "registry-v2",
        "ledger_revision": "ledger-v1",
        "policy_revision": "policy-v1",
        "authority": "observe",
        "side_effect_mode": "observe-only",
        "idempotency_key": "idem-1",
        "parent_event_id": None,
        "created_at": "2026-08-04T00:00:00Z",
    }
    value.update(changes)
    return value


class Phase03ContractTests(unittest.TestCase):
    def test_envelope_accepts_required_schema(self):
        self.assertEqual(C.validate_envelope(envelope())["task_id"], "t1")

    def test_envelope_rejects_missing_unknown_and_invalid_enum(self):
        missing = envelope()
        del missing["trace_id"]
        with self.assertRaises(C.ContractError):
            C.validate_envelope(missing)
        with self.assertRaises(C.ContractError):
            C.validate_envelope(envelope(extra="x"))
        with self.assertRaises(C.ContractError):
            C.validate_envelope(envelope(authority="admin"))

    def test_boundary_rejects_all_mutating_operations(self):
        boundary = C.Phase03Boundary(envelope())
        for method in ("dispatch", "write_ledger", "request_approval", "write_memory", "external_effect"):
            with self.assertRaises(C.BoundaryViolation):
                getattr(boundary, method)()

    def test_plan_only_is_not_dispatch(self):
        boundary = C.Phase03Boundary(envelope(authority="recommend", side_effect_mode="plan-only"))
        self.assertEqual(boundary.plan(["inspect", "compare"])["steps"], ["inspect", "compare"])
        with self.assertRaises(C.BoundaryViolation):
            boundary.observe("registry")
        for method in ("dispatch", "write_ledger", "request_approval", "write_memory", "external_effect"):
            with self.assertRaises(C.BoundaryViolation):
                getattr(boundary, method)()

    def test_side_effect_mode_requires_matching_authority(self):
        with self.assertRaises(C.ContractError):
            C.validate_envelope(envelope(authority="observe", side_effect_mode="plan-only"))

    def test_telemetry_is_allowed_and_idempotent(self):
        seen = []
        boundary = C.Phase03Boundary(envelope(), seen.append)
        self.assertEqual(boundary.append_telemetry({"idempotency_key": "k1"})["status"], "appended")
        self.assertEqual(boundary.append_telemetry({"idempotency_key": "k1"})["status"], "deduplicated")
        self.assertEqual(len(seen), 1)
        with self.assertRaises(C.ContractError):
            boundary.append_telemetry({"body": "not allowed"})

    def test_registry_profiles_and_legacy_entry(self):
        registry = R.load_registry()
        self.assertEqual(R.validate_invocation(registry, "execution-engine", "project-control")["kind"], "orchestration-profile")
        legacy_entry = registry["skills"][0]["key"]
        self.assertEqual(R.validate_invocation(registry, legacy_entry, "user")["kind"], "legacy-registered")
        with self.assertRaises(R.RegistryError):
            R.validate_invocation(registry, "not-registered", "user")
        with self.assertRaises(R.RegistryError):
            R.validate_invocation(registry, "domain", "user")

    def test_registry_duplicate_and_unknown_metadata_fail(self):
        base = {"skills": [{"key": "one"}], "orchestrationProfiles": {}}
        duplicate = {"skills": [{"key": "one"}, {"key": "one"}], "orchestrationProfiles": {}}
        with self.assertRaises(R.RegistryError):
            R.validate_registry(duplicate)
        invalid = {"skills": [], "orchestrationProfiles": {"x": {"unknown": True}}}
        with self.assertRaises(R.RegistryError):
            R.validate_registry(invalid)
        self.assertIs(R.validate_registry(base), base)


if __name__ == "__main__":
    unittest.main()
