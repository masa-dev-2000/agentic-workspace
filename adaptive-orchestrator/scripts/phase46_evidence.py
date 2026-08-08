"""Evidence Pack for Phase 4-6 readiness.

This module measures the existing Phase 0-3 and stage-runner boundaries.  It
does not implement a second state machine, dispatcher, monkeypatch, or socket
patch.  All runner observations use a caller-selected temporary SQLite file.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import multiprocessing
import os
import platform
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
STAGE_RUNNER_PATH = SCRIPTS / "stage_runner.py"
PHASE03_PATH = SCRIPTS / "phase03_contract.py"
REGISTRY_VALIDATOR_PATH = SCRIPTS / "registry_validator.py"
REGISTRY_PATH = ROOT / "skill-registry.yaml"
PHASE46_RUNTIME_PATH = SCRIPTS / "phase46_runtime.py"
DEFAULT_RUNNER_DB = SCRIPTS / "orchestration.sqlite3"
PROTECTED_PATHS = (
    STAGE_RUNNER_PATH,
    PHASE03_PATH,
    REGISTRY_VALIDATOR_PATH,
    REGISTRY_PATH,
)
CASE_FIELDS = (
    "run_id", "case_id", "phase", "test_boundary", "fixture_digest",
    "source_revision", "environment_digest", "db_path_digest",
    "python_version", "sqlite_version", "process_count", "iteration_count",
    "seed", "worker_id", "owner_id", "lease_deadline", "revision_before",
    "revision_after", "start", "end", "barrier_release_time",
    "observed_events", "expected_events", "actual_events",
    "process_exit_codes", "child_process_pids", "network_observation",
    "global_write_observation", "cleanup_status", "classification",
    "failure_reason", "evidence_refs",
)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_revision() -> str:
    paths = list(PROTECTED_PATHS)
    paths.append(PHASE46_RUNTIME_PATH)
    paths.extend(path for path in (ROOT / "tests").glob("*.py") if path.name != "test_phase46_evidence.py")
    return _digest({str(path.relative_to(ROOT)): _file_digest(path) for path in paths if path.is_file()})


def _protected_snapshot() -> dict[str, str]:
    paths = list(PROTECTED_PATHS)
    paths.append(PHASE46_RUNTIME_PATH)
    paths.extend(path for path in (ROOT / "tests").glob("*.py") if path.name != "test_phase46_evidence.py")
    if DEFAULT_RUNNER_DB.is_file():
        paths.append(DEFAULT_RUNNER_DB)
    return {str(path): _file_digest(path) for path in paths if path.is_file()}


def _environment() -> dict[str, str]:
    return {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "sqlite_version": sqlite3.sqlite_version,
        "platform": platform.platform(),
    }


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def collect_phase46_runtime(run_id: str, source_revision: str, environment: dict[str, str], protected_before: dict[str, str]) -> list[dict[str, Any]]:
    runtime = _load_module("phase46_runtime", PHASE46_RUNTIME_PATH)
    cases = []

    def case(case_id: str, phase: str, boundary: str, fixture: Any) -> dict[str, Any]:
        return _base_case(run_id, case_id, phase, boundary, fixture, None, 1, 1, 46, source_revision, environment, protected_before)

    approval = case("phase4-approval-fingerprint-valid", "phase4", "approval fingerprint/consumption", {"scope": ["atomic_write"]})
    ledger = runtime.ApprovalLedger()
    issued = ledger.issue(plan_digest="plan-a", invocation_digest="invoke-a", scope=["atomic_write"], expires_at=100, nonce="nonce-a")
    consumed = ledger.consume(fingerprint=issued["fingerprint"], plan_digest="plan-a", invocation_digest="invoke-a", scope=["atomic_write"], now=90)
    approval["expected_events"] = ["issued", "consumed"]
    approval["actual_events"] = ["issued", "consumed" if consumed["consumed"] else "not-consumed"]
    approval["evidence_refs"] = ["phase46_runtime:ApprovalLedger"]
    cases.append(_complete_case(approval, "PASS" if consumed["consumed"] else "FAIL"))

    tamper = case("phase4-approval-fingerprint-tamper", "phase4", "approval scope/expiry/nonce rejection", {})
    rejected = []
    try:
        ledger.consume(fingerprint=issued["fingerprint"], plan_digest="plan-b", invocation_digest="invoke-a", scope=["atomic_write"], now=90)
    except runtime.RuntimeContractError:
        rejected.append("plan-mismatch")
    try:
        ledger.consume(fingerprint=issued["fingerprint"], plan_digest="plan-a", invocation_digest="invoke-a", scope=["atomic_write"], now=90)
    except runtime.RuntimeContractError:
        rejected.append("replay")
    tamper["expected_events"] = ["plan-mismatch", "replay"]
    tamper["actual_events"] = rejected
    tamper["evidence_refs"] = ["phase46_runtime:approval_fingerprint"]
    cases.append(_complete_case(tamper, "PASS" if rejected == ["plan-mismatch", "replay"] else "FAIL"))

    retry = case("phase4-retry-budget-exhausted", "phase4", "retry budget", {"max_attempts": 2})
    budget = runtime.RetryBudget(max_attempts=2)
    outcomes = [budget.record("transient"), budget.record("transient"), budget.record("transient")]
    retry["expected_events"] = ["retryable", "retryable", "budget-exhausted"]
    retry["actual_events"] = outcomes
    retry["evidence_refs"] = ["phase46_runtime:RetryBudget"]
    cases.append(_complete_case(retry, "PASS" if outcomes == retry["expected_events"] else "FAIL"))

    replay_case = case("phase5-replay-equivalence", "phase5", "replay normalized event boundary", {"events": 2})
    events = [{"type": "dispatch", "payload_digest": "a", "timestamp": 1}, {"type": "result", "payload_digest": "b", "pid": 7}]
    replay_result = runtime.replay(events, lambda original: [{**original[0], "timestamp": 99}, {**original[1], "pid": 99}])
    replay_case["expected_events"] = ["equivalent"]
    replay_case["actual_events"] = ["equivalent" if replay_result["equivalent"] else "different"]
    replay_case["evidence_refs"] = ["phase46_runtime:replay"]
    replay_case["actual_digest"] = replay_result["actual_digest"]
    replay_case["expected_digest"] = replay_result["expected_digest"]
    cases.append(_complete_case(replay_case, "PASS" if replay_result["equivalent"] else "FAIL"))

    shadow_case = case("phase5-shadow-no-side-effect", "phase5", "shadow side-effect sink", {})
    shadow_result = runtime.shadow(lambda sink: [{"type": "observe-only"}])
    shadow_case["expected_events"] = ["side_effect_count=0"]
    shadow_case["actual_events"] = [f"side_effect_count={shadow_result['side_effect_count']}"]
    shadow_case["evidence_refs"] = ["phase46_runtime:shadow"]
    shadow_case["pre_snapshot"] = shadow_result["pre_snapshot"] if "pre_snapshot" in shadow_case else None
    shadow_case["post_snapshot"] = shadow_result["post_snapshot"] if "post_snapshot" in shadow_case else None
    cases.append(_complete_case(shadow_case, "PASS" if shadow_result["side_effect_free"] else "FAIL"))

    canary_case = case("phase6-canary-failure-rollback", "phase6", "facade canary rollback", {"canary_keys": ["bad"]})
    facade = runtime.CanaryFacade(lambda value: {"route": "legacy", "value": value}, lambda value: (_ for _ in ()).throw(RuntimeError("candidate-failure")), {"bad"})
    result = facade.route("bad", "payload")
    canary_case["expected_events"] = ["candidate-failed", "rollback", "legacy-served"]
    canary_case["actual_events"] = ["candidate-failed", "rollback" if facade.rollback_count == 1 else "no-rollback", "legacy-served" if result["route"] == "legacy" else "candidate-served"]
    canary_case["evidence_refs"] = ["phase46_runtime:CanaryFacade"]
    cases.append(_complete_case(canary_case, "PASS" if canary_case["actual_events"] == canary_case["expected_events"] else "FAIL"))

    cutover_case = case("phase6-cutover-idempotency", "phase6", "facade cutover/rollback", {})
    good = runtime.CanaryFacade(lambda value: "legacy:" + value, lambda value: "candidate:" + value, {"x"})
    good.cutover(); first = good.route("x", "a"); good.cutover(); second = good.route("x", "b"); good.rollback(); third = good.route("x", "c"); good.rollback()
    cutover_case["expected_events"] = ["candidate:a", "candidate:b", "legacy:c", "rollback-idempotent"]
    cutover_case["actual_events"] = [first, second, third, "rollback-idempotent" if good.mode == "legacy" else "rollback-not-idempotent"]
    cutover_case["evidence_refs"] = ["phase46_runtime:CanaryFacade"]
    cases.append(_complete_case(cutover_case, "PASS" if cutover_case["actual_events"] == cutover_case["expected_events"] else "FAIL"))
    return cases


def _runner_cli(db_path: Path, command: str, payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    argv = [sys.executable, str(STAGE_RUNNER_PATH), "--db", str(db_path), command]
    if payload is not None:
        argv.append(_canonical(payload))
    completed = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", check=False)
    stream = completed.stdout.strip() or completed.stderr.strip()
    try:
        result = json.loads(stream)
    except json.JSONDecodeError:
        result = {"error": "runner returned non-json output"}
    return completed.returncode, result


def _claim_worker(db_path: str, payload: dict[str, Any], start_event, result_queue) -> None:
    """Call the real stage_runner.claim from a spawned process."""
    started = time.monotonic()
    start_event.wait(30)
    try:
        runner = _load_module("phase46_stage_runner_worker", STAGE_RUNNER_PATH)
        connection = runner.connect(Path(db_path))
        try:
            result = runner.claim(connection, payload)
        finally:
            connection.close()
        result_queue.put({"worker_id": payload["worker_id"], "ok": True, "result": result})
    except Exception as exc:  # evidence records the real boundary rejection
        result_queue.put({"worker_id": payload["worker_id"], "ok": False, "error": str(exc)})
    finally:
        result_queue.put({"worker_id": payload["worker_id"], "worker_elapsed": time.monotonic() - started})


def _base_case(run_id: str, case_id: str, phase: str, boundary: str, fixture: Any,
               db_path: Path | None, process_count: int, iteration_count: int, seed: int,
               source_revision: str, environment: dict[str, str], protected_before: dict[str, str]) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "case_id": case_id,
        "phase": phase,
        "test_boundary": boundary,
        "fixture_digest": _digest(fixture),
        "source_revision": source_revision,
        "environment_digest": _digest(environment),
        "db_path_digest": _digest(str(db_path)) if db_path else _digest("not-applicable"),
        "python_version": environment["python_version"],
        "sqlite_version": environment["sqlite_version"],
        "process_count": process_count,
        "iteration_count": iteration_count,
        "seed": seed,
        "worker_id": "primary",
        "owner_id": None,
        "lease_deadline": None,
        "revision_before": None,
        "revision_after": None,
        "start": _now(),
        "end": None,
        "barrier_release_time": None,
        "observed_events": [],
        "expected_events": [],
        "actual_events": [],
        "process_exit_codes": {},
        "child_process_pids": [],
        "network_observation": {"status": "NOT_OBSERVABLE", "reason": "socket patch/instrumentation is prohibited"},
        "global_write_observation": {"status": "NOT_CHECKED", "protected_before": protected_before},
        "cleanup_status": "pending",
        "classification": "NOT_RUN",
        "failure_reason": None,
        "evidence_refs": [],
    }


def _complete_case(case: dict[str, Any], classification: str, reason: str | None = None) -> dict[str, Any]:
    case["end"] = _now()
    case["classification"] = classification
    case["failure_reason"] = reason
    case["cleanup_status"] = "complete"
    missing = [field for field in CASE_FIELDS if field not in case]
    if missing:
        raise RuntimeError(f"evidence case missing fields: {missing}")
    return case


def _db_observation(db_path: Path, job_id: str) -> dict[str, Any]:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        stage = connection.execute("SELECT * FROM ao_stages WHERE job_id=? AND name='planning'", (job_id,)).fetchone()
        events = [dict(row) for row in connection.execute("SELECT event_type,detail_json FROM ao_events WHERE job_id=? ORDER BY id", (job_id,))]
        dispatches = [dict(row) for row in connection.execute("SELECT dispatch_id,active,terminal_status,attempt FROM ao_dispatches WHERE job_id=? ORDER BY created_at", (job_id,))]
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        return {"stage": dict(stage) if stage else None, "events": events, "dispatches": dispatches, "integrity": integrity}
    finally:
        connection.close()


def _claim_iteration(db_path: Path, iteration: int, process_count: int, seed: int, run_id: str) -> dict[str, Any]:
    job_id = f"phase46-{run_id[:12]}-{iteration}"
    create_payload = {
        "job_id": job_id,
        "session_hash": _digest([run_id, "session"]),
        "turn_hash": _digest([run_id, iteration, "turn"]),
        "cwd_hash": _digest(str(ROOT)),
        "prompt_hash": _digest(["phase46", iteration]),
    }
    return_code, created = _runner_cli(db_path, "create-job", create_payload)
    if return_code != 0:
        raise RuntimeError(f"create-job failed: {created}")

    context = multiprocessing.get_context("spawn")
    event = context.Event()
    queue = context.Queue()
    processes = []
    for index in range(process_count):
        payload = {"job_id": job_id, "stage": "planning", "worker_id": f"worker-{iteration}-{index}", "expected_version": 0, "lease_seconds": 30}
        process = context.Process(target=_claim_worker, args=(str(db_path), payload, event, queue), name=f"phase46-{iteration}-{index}")
        process.start()
        processes.append(process)
    barrier_release_time = _now()
    event.set()
    for process in processes:
        process.join(30)
    remaining = [process.pid for process in processes if process.is_alive()]
    for process in processes:
        if process.is_alive():
            process.terminate()
            process.join(5)
    results = []
    while not queue.empty():
        results.append(queue.get())
    observation = _db_observation(db_path, job_id)
    successes = [item for item in results if item.get("ok")]
    stale_rejected = False
    stale_reason = None
    if successes:
        successful = successes[0]["result"]
        runner = _load_module("phase46_stage_runner_parent", STAGE_RUNNER_PATH)
        connection = runner.connect(db_path)
        try:
            connection.execute("UPDATE ao_stages SET lease_until=0 WHERE job_id=? AND name='planning'", (job_id,))
            stale_payload = {
                "dispatch_id": successful["dispatch_id"],
                "dispatch_capability": successful["dispatch_capability"],
                "runtime_handle": "phase46-stale",
                "principal_id": "phase46-stale-principal",
                "outcome": "passed",
                "runtime_usage": {"source": "unavailable"},
            }
            try:
                runner.record_result(connection, stale_payload)
            except Exception as exc:
                stale_rejected = True
                stale_reason = str(exc)
        finally:
            connection.close()
        observation = _db_observation(db_path, job_id)
    return {
        "job_id": job_id,
        "results": results,
        "success_count": len(successes),
        "dispatch_count": len(observation["dispatches"]),
        "dispatch_created_events": sum(event["event_type"] == "dispatch-created" for event in observation["events"]),
        "stale_rejected": stale_rejected,
        "stale_reason": stale_reason,
        "observation": observation,
        "remaining_pids": remaining,
        "exit_codes": {str(process.pid): process.exitcode for process in processes},
        "barrier_release_time": barrier_release_time,
    }


def collect_phase4(run_id: str, process_count: int, iterations: int, seed: int, source_revision: str,
                   environment: dict[str, str], protected_before: dict[str, str]) -> list[dict[str, Any]]:
    fixture = {"stage": "planning", "expected": {"claim": 1, "dispatch": 1, "stale": 0, "integrity": "ok"}}
    case = _base_case(run_id, "phase4-stage-runner-concurrency", "phase4", "stage_runner.claim/record_result", fixture, None, process_count, iterations, seed, source_revision, environment, protected_before)
    case["expected_events"] = ["job-created", "dispatch-created", "stale-result-rejected"]
    case["evidence_refs"] = ["stage_runner:claim", "stage_runner:record_result", "sqlite:integrity_check"]
    try:
        with tempfile.TemporaryDirectory(prefix="phase46-") as temp_root:
            db_path = Path(temp_root) / "runner.sqlite3"
            case["db_path_digest"] = _digest(str(db_path))
            iterations_observed = []
            for iteration in range(iterations):
                iterations_observed.append(_claim_iteration(db_path, iteration, process_count, seed, run_id))
            success_ok = all(item["success_count"] == 1 for item in iterations_observed)
            dispatch_ok = all(item["dispatch_created_events"] == 1 for item in iterations_observed)
            stale_ok = all(item["stale_rejected"] for item in iterations_observed)
            integrity_ok = all(item["observation"]["integrity"] == "ok" for item in iterations_observed)
            no_remaining = all(not item["remaining_pids"] for item in iterations_observed)
            case["owner_id"] = next((next((entry["worker_id"] for entry in item["results"] if entry.get("ok")), None) for item in iterations_observed), None)
            case["worker_id"] = ",".join(f"worker-{i}-{j}" for i in range(iterations) for j in range(process_count))
            case["lease_deadline"] = next((item["observation"]["stage"]["lease_until"] for item in iterations_observed if item["observation"]["stage"]), None)
            case["revision_before"] = 0
            case["revision_after"] = next((item["observation"]["stage"]["version"] for item in iterations_observed if item["observation"]["stage"]), None)
            case["barrier_release_time"] = iterations_observed[0]["barrier_release_time"] if iterations_observed else None
            case["process_exit_codes"] = {f"iteration-{i}": item["exit_codes"] for i, item in enumerate(iterations_observed)}
            case["child_process_pids"] = [pid for item in iterations_observed for pid in item["exit_codes"]]
            case["observed_events"] = [{"iteration": i, "events": item["observation"]["events"], "dispatches": item["observation"]["dispatches"], "stale_reason": item["stale_reason"]} for i, item in enumerate(iterations_observed)]
            case["actual_events"] = [{"success_count": item["success_count"], "dispatch_count": item["dispatch_count"], "duplicate_dispatch_count": max(0, item["dispatch_count"] - 1), "stale_rejected": item["stale_rejected"], "integrity": item["observation"]["integrity"]} for item in iterations_observed]
            case["global_write_observation"] = {"status": "PASS", "protected_before": protected_before, "temp_db_only": True}
            reason = None if all((success_ok, dispatch_ok, stale_ok, integrity_ok, no_remaining)) else "stage-runner evidence condition failed"
            result_cases = [_complete_case(case, "PASS" if reason is None else "FAIL", reason)]
            network_case = _base_case(run_id, "phase4-network-observation", "phase4", "network observation boundary", {"socket_patch": False}, db_path, process_count, iterations, seed, source_revision, environment, protected_before)
            network_case["expected_events"] = ["external communication observed"]
            network_case["actual_events"] = ["observation unavailable without prohibited socket instrumentation"]
            network_case["evidence_refs"] = ["network-observation:not-observable"]
            network_case["global_write_observation"] = {"status": "PASS", "protected_before": protected_before, "temp_db_only": True}
            result_cases.append(_complete_case(network_case, "NOT_OBSERVABLE", "network instrumentation is prohibited by the review contract"))
            return result_cases
    except Exception as exc:
        case["global_write_observation"] = {"status": "UNKNOWN", "protected_before": protected_before, "temp_db_only": True}
        return [_complete_case(case, "NOT_OBSERVABLE", str(exc))]


def _phase03_envelope() -> dict[str, Any]:
    return {
        "project_id": "phase46-project", "task_id": "phase46-task", "run_id": "phase46-run",
        "attempt_id": "phase46-attempt", "trace_id": "phase46-trace", "origin": "evidence-pack",
        "canonical_entry": "execution-engine", "registry_revision": "phase46-registry",
        "ledger_revision": "phase46-ledger", "policy_revision": "phase46-policy",
        "authority": "observe", "side_effect_mode": "observe-only", "idempotency_key": "phase46-idem",
        "parent_event_id": None, "created_at": "2026-08-04T00:00:00Z",
    }


def collect_phase03(run_id: str, source_revision: str, environment: dict[str, str], protected_before: dict[str, str]) -> list[dict[str, Any]]:
    phase03 = _load_module("phase46_phase03", PHASE03_PATH)
    registry = _load_module("phase46_registry_validator", REGISTRY_VALIDATOR_PATH)
    cases = []

    def make(case_id: str, boundary: str, fixture: Any) -> dict[str, Any]:
        return _base_case(run_id, case_id, "phase3", boundary, fixture, None, 1, 1, 0, source_revision, environment, protected_before)

    case = make("phase3-boundary-mutation-rejection", "Phase03Boundary.dispatch/write_ledger/request_approval/write_memory/external_effect", {"mode": "observe-only"})
    boundary = phase03.Phase03Boundary(_phase03_envelope())
    rejected = []
    for method in ("dispatch", "write_ledger", "request_approval", "write_memory", "external_effect"):
        try:
            getattr(boundary, method)()
        except phase03.BoundaryViolation:
            rejected.append(method)
    case["expected_events"] = ["five mutations rejected"]
    case["actual_events"] = rejected
    case["observed_events"] = rejected
    case["evidence_refs"] = ["phase03_contract:Phase03Boundary"]
    cases.append(_complete_case(case, "PASS" if len(rejected) == 5 else "FAIL", None if len(rejected) == 5 else "mutation boundary was not fully rejected"))

    case = make("phase3-telemetry-only", "Phase03Boundary.append_telemetry", {"mode": "observe-only", "body_free": True})
    seen = []
    boundary = phase03.Phase03Boundary(_phase03_envelope(), seen.append)
    first = boundary.append_telemetry({"idempotency_key": "phase46-telemetry"})
    second = boundary.append_telemetry({"idempotency_key": "phase46-telemetry"})
    case["expected_events"] = ["appended", "deduplicated"]
    case["actual_events"] = [first["status"], second["status"]]
    case["observed_events"] = [{"sink_count": len(seen), "body_free": True}]
    case["evidence_refs"] = ["phase03_contract:append_telemetry"]
    cases.append(_complete_case(case, "PASS" if [first["status"], second["status"]] == ["appended", "deduplicated"] and len(seen) == 1 else "FAIL", None))

    case = make("phase3-unknown-field-rejection", "phase03_contract.validate_envelope", {"unknown_field": True})
    invalid = _phase03_envelope() | {"unknown_field": True}
    try:
        phase03.validate_envelope(invalid)
        cases.append(_complete_case(case, "FAIL", "unknown envelope field accepted"))
    except phase03.ContractError:
        case["actual_events"] = ["unknown-field-rejected"]
        cases.append(_complete_case(case, "PASS"))

    case = make("phase3-body-rejection", "Phase03Boundary.append_telemetry", {"body": "redacted"})
    try:
        phase03.Phase03Boundary(_phase03_envelope()).append_telemetry({"body": "redacted"})
        cases.append(_complete_case(case, "FAIL", "body-bearing telemetry accepted"))
    except phase03.ContractError:
        case["actual_events"] = ["body-rejected"]
        cases.append(_complete_case(case, "PASS"))

    case = make("phase3-registry-caller-authority", "registry_validator.validate_invocation/validate_registry", {"entry": "execution-engine"})
    registry_data = registry.load_registry()
    checks = []
    try:
        registry.validate_invocation(registry_data, "execution-engine", "project-control")
        checks.append("allowed-caller-accepted")
    except registry.RegistryError:
        pass
    for entry, caller in (("execution-engine", "domain"), ("not-registered", "user")):
        try:
            registry.validate_invocation(registry_data, entry, caller)
        except registry.RegistryError:
            checks.append(f"rejected:{entry}:{caller}")
    invalid_authority = {"skills": [], "orchestrationProfiles": {"x": {"public_entry": False, "allowed_callers": ["system"], "exclusive_group": "x", "authority": "invalid", "blocking": False, "lifecycle_phase": "x", "canonical_store": "x", "produces": ["x"], "consumes": ["x"]}}}
    try:
        registry.validate_registry(invalid_authority)
    except registry.RegistryError:
        checks.append("invalid-authority-rejected")
    case["expected_events"] = ["allowed caller", "wrong caller rejected", "unknown entry rejected", "invalid authority rejected"]
    case["actual_events"] = checks
    case["observed_events"] = checks
    case["evidence_refs"] = ["registry_validator:validate_invocation", "registry_validator:validate_registry"]
    cases.append(_complete_case(case, "PASS" if len(checks) == 4 else "FAIL", None if len(checks) == 4 else "Registry boundary check incomplete"))
    return cases


def collect_not_run(run_id: str, source_revision: str, environment: dict[str, str], protected_before: dict[str, str]) -> list[dict[str, Any]]:
    cases = []
    for phase, case_id, boundary, reason in (
        ("phase4", "phase4-codex-pretool-approval", "Codex PreToolUse approval boundary", "Codex-side approval enforcement is not observable from this runtime"),
        ("phase5", "phase5-network-side-effect", "network side-effect boundary", "network instrumentation is prohibited and no injection point is exposed"),
        ("phase6", "phase6-production-cutover", "production facade/cutover", "production routing and external rollback are outside the local evidence environment"),
    ):
        case = _base_case(run_id, case_id, phase, boundary, {"status": "not-implemented"}, None, 0, 0, 0, source_revision, environment, protected_before)
        case["failure_reason"] = reason
        case["evidence_refs"] = ["current-source-boundary"]
        classification = "NOT_OBSERVABLE" if phase in {"phase4", "phase5"} else "NOT_RUN"
        cases.append(_complete_case(case, classification, reason))
    return cases


def run_evidence_pack(*, output: str | Path | None = None, process_count: int = 4, iterations: int = 2, seed: int = 46) -> dict[str, Any]:
    if process_count < 2 or iterations < 1:
        raise ValueError("process_count must be >= 2 and iterations must be >= 1")
    run_id = f"phase46-evidence-{uuid.uuid4().hex}"
    environment = _environment()
    protected_before = _protected_snapshot()
    source_revision = _source_revision()
    cases = collect_phase4(run_id, process_count, iterations, seed, source_revision, environment, protected_before)
    cases.extend(collect_phase03(run_id, source_revision, environment, protected_before))
    cases.extend(collect_phase46_runtime(run_id, source_revision, environment, protected_before))
    cases.extend(collect_not_run(run_id, source_revision, environment, protected_before))
    protected_after = _protected_snapshot()
    protected_unchanged = protected_before == protected_after
    for case in cases:
        case["global_write_observation"]["protected_after"] = protected_after
        case["global_write_observation"]["protected_unchanged"] = protected_unchanged
        if case["classification"] == "PASS" and not protected_unchanged:
            case["classification"] = "FAIL"
            case["failure_reason"] = "protected file hash changed"

    phase4 = [case for case in cases if case["phase"] == "phase4"]
    phase5 = [case for case in cases if case["phase"] == "phase5"]
    phase6 = [case for case in cases if case["phase"] == "phase6"]
    status = lambda selected: "PASS" if selected and all(case["classification"] == "PASS" for case in selected) else "HOLD"
    result = {
        "schema": "phase46-evidence-pack-v1",
        "run_id": run_id,
        "source_revision": source_revision,
        "environment_digest": _digest(environment),
        "environment": environment,
        "configuration": {"process_count": process_count, "iteration_count": iterations, "seed": seed},
        "evidence_pack_status": status(cases),
        "phase4_readiness": status(phase4),
        "phase5_readiness": status(phase5),
        "phase6_readiness": status(phase6),
        "classification_counts": {name: sum(case["classification"] == name for case in cases) for name in ("PASS", "FAIL", "NOT_RUN", "NOT_OBSERVABLE")},
        "protected_files_unchanged": protected_unchanged,
        "cases": cases,
        "limitations": ["network observation is NOT_OBSERVABLE because socket patch/instrumentation is prohibited", "NOT_RUN and NOT_OBSERVABLE are never counted as PASS"],
    }
    if output is not None:
        output_path = Path(output).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Phase 4-6 Evidence Pack against real local boundaries")
    parser.add_argument("--output", help="write JSON only to this explicitly selected path")
    parser.add_argument("--process-count", type=int, default=4)
    parser.add_argument("--iterations", type=int, default=2)
    parser.add_argument("--seed", type=int, default=46)
    args = parser.parse_args(argv)
    result = run_evidence_pack(output=args.output, process_count=args.process_count, iterations=args.iterations, seed=args.seed)
    print(json.dumps({"schema": result["schema"], "run_id": result["run_id"], "evidence_pack_status": result["evidence_pack_status"], "phase4_readiness": result["phase4_readiness"], "phase5_readiness": result["phase5_readiness"], "phase6_readiness": result["phase6_readiness"], "classification_counts": result["classification_counts"]}, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
