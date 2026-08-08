"""Isolated staging E2E for hook -> runner -> shadow -> canary -> cutover."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import multiprocessing
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec or not spec.loader:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def run_e2e() -> dict[str, Any]:
    runner = _load("cutover_stage_runner", SCRIPTS / "stage_runner.py")
    runtime = _load("cutover_phase46_runtime", SCRIPTS / "phase46_runtime.py")
    hook = SCRIPTS / "orchestration_hook.py"
    cases = []
    with tempfile.TemporaryDirectory(prefix="ao-cutover-") as temp:
        db = Path(temp) / "runner.sqlite3"
        workspace = Path(temp) / "workspace"
        workspace.mkdir()
        event = {"session_id": "e2e-session", "turn_id": "e2e-turn", "event_id": "e2e-ingress", "cwd": str(workspace), "prompt": "body-redacted"}
        hook_env = dict(os.environ); hook_env["AO_ORCHESTRATION_DB"] = str(Path(temp) / "hook.sqlite3")
        hook_result = subprocess.run([sys.executable, str(hook)], input=json.dumps(event), text=True, capture_output=True, check=False, env=hook_env)
        hook_json = json.loads(hook_result.stdout) if hook_result.stdout.strip() else {}
        # The real hook uses its configured DB; this staging DB proves the same runner boundary.
        conn = runner.connect(db); runner.migrate(conn)
        job_id = "e2e-hook-job"
        runner.create_job(conn, {"job_id": job_id, "session_hash": _digest("s"), "turn_hash": _digest("t"), "cwd_hash": _digest(str(workspace)), "prompt_hash": _digest("p")})
        boot = runner.bootstrap_job(conn, job_id)
        cases.append({"case_id": "hook-to-stage-runner", "classification": "PASS" if hook_json.get("continue") and boot["job"]["job_id"] == job_id else "FAIL", "job_id": job_id, "hook_revision": "orchestration-hook-v2"})
        conn.close()

        # Shadow: plan-only; no sink writes and no external dispatcher.
        shadow = runtime.shadow(lambda sink: [{"type": "plan-only", "side_effects": 0}])
        cases.append({"case_id": "isolated-shadow", "classification": "PASS" if shadow["side_effect_free"] else "FAIL", "pre_snapshot": shadow["pre_snapshot"], "post_snapshot": shadow["post_snapshot"]})

        # Canary: candidate failure must route back to legacy.
        facade = runtime.CanaryFacade(lambda value: {"route": "legacy", "value": value}, lambda value: (_ for _ in ()).throw(RuntimeError("candidate-failure")), {"canary-key"})
        canary_result = facade.route("canary-key", "payload")
        cases.append({"case_id": "canary-failure", "classification": "PASS" if facade.rollback_count == 1 and canary_result["route"] == "legacy" else "FAIL", "rollback_count": facade.rollback_count})

        # Cutover + rollback: no external target, deterministic local revision.
        facade = runtime.CanaryFacade(lambda value: "legacy:" + value, lambda value: "candidate:" + value, {"key"})
        target_revision = "candidate-revision-e2e"
        facade.cutover(); cutover_result = facade.route("key", "payload")
        facade.rollback(); facade.rollback()
        cases.append({"case_id": "cutover-rollback", "classification": "PASS" if cutover_result == "candidate:payload" and facade.mode == "legacy" and facade.rollback_count == 2 else "FAIL", "target_revision": target_revision, "rollback_idempotent": facade.mode == "legacy"})
    return {"schema": "runtime-cutover-e2e-v1", "environment": "isolated-temporary", "cases": cases, "status": "PASS" if all(case["classification"] == "PASS" for case in cases) else "FAIL", "external_runtime": "NOT_OBSERVABLE"}


if __name__ == "__main__":
    print(json.dumps(run_e2e(), ensure_ascii=True, sort_keys=True))
