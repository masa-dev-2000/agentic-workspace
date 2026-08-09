import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("entry_rollout", ROOT / "scripts" / "entry_rollout.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

SAFE = {
    "side_effect_classes": ["local-reversible"], "risk_level": "low",
    "external_io": False, "secret_access": False, "auth_state_access": False,
    "compensation_defined": True,
}


class EntryRolloutTests(unittest.TestCase):
    def test_canary_requires_deterministic_allowlist(self):
        state = MODULE.transition(MODULE.initial_state(), "canary", expected_state_version=0, route_digest="r", policy_digest="p", evidence_ready=True, compatibility_ok=True)["state"]
        self.assertEqual(MODULE.decide(state, "adaptive-orchestrator", SAFE)["status"], "canary_allowed")
        self.assertEqual(MODULE.decide(state, "adaptive-orchestrator", dict(SAFE, external_io=True))["status"], "canary_fallback")

    def test_unknown_classification_blocks(self):
        state = MODULE.transition(MODULE.initial_state(), "canary", expected_state_version=0, route_digest="r", policy_digest="p", evidence_ready=True, compatibility_ok=True)["state"]
        result = MODULE.decide(state, "adaptive-orchestrator", {"risk_level": "low"})
        self.assertEqual((result["selected"], result["status"]), ("none", "blocked_unknown"))

    def test_transition_requires_evidence_and_version(self):
        with self.assertRaises(MODULE.RolloutError):
            MODULE.transition(MODULE.initial_state(), "canary", expected_state_version=0)
        with self.assertRaises(MODULE.RolloutError):
            MODULE.transition(MODULE.initial_state(), "canary", expected_state_version=1, route_digest="r", policy_digest="p", evidence_ready=True, compatibility_ok=True)

    def test_cutover_requires_canary_predecessor(self):
        with self.assertRaises(MODULE.RolloutError):
            MODULE.transition(MODULE.initial_state(), "cutover", expected_state_version=0, route_digest="r", policy_digest="p", evidence_ready=True, compatibility_ok=True)

    def test_rollback_is_idempotent_and_requires_compensation(self):
        state = MODULE.transition(MODULE.initial_state(), "canary", expected_state_version=0, route_digest="r", policy_digest="p", evidence_ready=True, compatibility_ok=True)["state"]
        with self.assertRaises(MODULE.RolloutError):
            MODULE.transition(state, "legacy", expected_state_version=1, reason="failure")
        rolled = MODULE.transition(state, "legacy", expected_state_version=1, compensation_complete=True, reason="failure")["state"]
        again = MODULE.transition(rolled, "legacy", expected_state_version=2, compensation_complete=True, reason="duplicate")["state"]
        self.assertEqual((rolled["rollback_count"], again["rollback_count"], again["state_version"]), (1, 1, 2))


if __name__ == "__main__":
    unittest.main()
