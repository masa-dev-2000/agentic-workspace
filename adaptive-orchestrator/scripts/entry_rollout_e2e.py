"""Local-only cases for Skill entry Shadow/Canary/Rollback/Cutover."""
from __future__ import annotations

import json
from pathlib import Path
import importlib.util
import sys

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("entry_rollout", ROOT / "entry_rollout.py")
module = importlib.util.module_from_spec(spec)
assert spec.loader
sys.modules[spec.name] = module
spec.loader.exec_module(module)

SAFE = {
    "side_effect_classes": ["local-reversible"], "risk_level": "low",
    "external_io": False, "secret_access": False, "auth_state_access": False,
    "compensation_defined": True,
}


def main() -> int:
    state = module.initial_state()
    cases = []
    shadow = module.decide(state, "adaptive-orchestrator", SAFE)
    cases.append({"case_id": "shadow-no-execution", "classification": "PASS" if shadow["status"] == "shadow" and shadow["selected"] == "legacy" else "FAIL"})
    canary = module.transition(state, "canary", expected_state_version=0, route_digest="route-a", policy_digest="policy-a", evidence_ready=True, compatibility_ok=True)
    state = canary["state"]
    allowed = module.decide(state, "adaptive-orchestrator", SAFE)
    cases.append({"case_id": "canary-allowlist", "classification": "PASS" if allowed["status"] == "canary_allowed" and allowed["selected"] == "candidate" else "FAIL"})
    unsafe = dict(SAFE, external_io=True)
    fallback = module.decide(state, "adaptive-orchestrator", unsafe)
    cases.append({"case_id": "canary-unsafe-fallback", "classification": "PASS" if fallback["status"] == "canary_fallback" and fallback["selected"] == "legacy" else "FAIL"})
    unknown = module.decide(state, "adaptive-orchestrator", {"risk_level": "low"})
    cases.append({"case_id": "unknown-blocked", "classification": "PASS" if unknown["status"] == "blocked_unknown" and unknown["selected"] == "none" else "FAIL"})
    cutover = module.transition(state, "cutover", expected_state_version=1, route_digest="route-a", policy_digest="policy-a", evidence_ready=True, compatibility_ok=True)
    state = cutover["state"]
    cases.append({"case_id": "cutover-allowlist", "classification": "PASS" if module.decide(state, "adaptive-orchestrator", SAFE)["selected"] == "candidate" else "FAIL"})
    rollback = module.transition(state, "legacy", expected_state_version=2, route_digest="route-a", policy_digest="policy-a", compensation_complete=True, reason="canary-failure")
    again = module.transition(rollback["state"], "legacy", expected_state_version=3, compensation_complete=True, reason="duplicate-rollback")
    cases.append({"case_id": "rollback-idempotent", "classification": "PASS" if rollback["state"]["rollback_count"] == 1 and not again["changed"] and again["state"]["rollback_count"] == 1 else "FAIL"})
    result = {"schema": "skill-entry-rollout-e2e-v1", "cases": cases, "status": "PASS" if all(case["classification"] == "PASS" for case in cases) else "FAIL", "external_runtime": "NOT_OBSERVABLE"}
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
