from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from telemetry_store import TelemetryStore, iter_strings

TARGETS = [
    "failure-loop-guard",
    "skill-telemetry",
    "ai-project-manager:project-orchestrator",
]
VERIFY = re.compile(
    r"(?i)(\bpytest\b|\bunittest\b|\btest(?:s|ing)?\b|\bbuild\b|"
    r"integrity_check|doctor|validate|verification|検証|テスト)"
)
SUCCESS = re.compile(r"(?i)(exit code[: ]+0|\"exit_code\"\s*:\s*0|\bpassed\b|\bok\b|valid.{0,8}true)")
FAILURE = re.compile(
    r"(?i)(exit code[: ]+[1-9]\d*|\"exit_code\"\s*:\s*[1-9]\d*|"
    r"\bfailed\b|\berror\b|traceback|access denied|permission denied)"
)
DANGEROUS = re.compile(
    r"(?i)(deploy|publish|send[_ -]?mail|delete|remove-item|rm -rf|payment|purchase|"
    r"production|本番|削除|送信|支払)"
)


def load_rollouts(
    store: TelemetryStore, state: Path | None = None
) -> dict[str, tuple[str, Path]]:
    state = state or Path.home() / ".codex" / "state_5.sqlite"
    if not state.is_file():
        return {}
    try:
        db = sqlite3.connect(state.as_uri() + "?mode=ro", uri=True)
        try:
            rows = db.execute("SELECT id,rollout_path FROM threads").fetchall()
        finally:
            db.close()
    except (OSError, sqlite3.Error):
        return {}
    return {
        store.pseudonym_existing(thread_id, "session"): (thread_id, Path(path))
        for thread_id, path in rows
        if path and store.pseudonym_existing(thread_id, "session")
    }


def payload_text(payload: Any) -> str:
    return "\n".join(iter_strings(payload))


def turn_segment(store: TelemetryStore, path: Path, turn_hash: str) -> list[dict[str, Any]]:
    try:
        events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    except (OSError, json.JSONDecodeError):
        return []
    start = None
    for index, event in enumerate(events):
        payload = event.get("payload") or {}
        turn_id = payload.get("turn_id")
        if turn_id and store.pseudonym_existing(str(turn_id), "turn") == turn_hash:
            start = index
            break
    if start is None:
        return []
    end = len(events)
    for index in range(start, len(events)):
        payload = events[index].get("payload") or {}
        event_turn = payload.get("turn_id")
        if (
            event_turn
            and store.pseudonym_existing(str(event_turn), "turn") == turn_hash
            and payload.get("type") in {"task_complete", "turn_complete"}
        ):
            end = index + 1
            break
    return events[start:end]


def classify_segment(store: TelemetryStore, segment: list[dict[str, Any]], lifecycle: dict[str, Any]):
    if not segment:
        return "unverified", None, ["lifecycle"], "rollout-turn-not-found"
    completed = any(
        (event.get("payload") or {}).get("type") == "task_complete"
        for event in segment
    )
    calls: dict[str, str] = {}
    results: dict[str, str] = {}
    user_reaction = lifecycle.get("feedback_sentiment")
    dangerous = False
    for event in segment:
        payload = event.get("payload") or {}
        text = payload_text(payload)
        if payload.get("type") in {"function_call", "function_call_output", "custom_tool_call_output"}:
            dangerous = dangerous or bool(DANGEROUS.search(text))
        call_id = str(payload.get("call_id", ""))
        if call_id and payload.get("type") == "function_call":
            calls[call_id] = text
        elif call_id and payload.get("type") in {"function_call_output", "custom_tool_call_output"}:
            results[call_id] = text
    pairs = [calls.get(call_id, "") + "\n" + output for call_id, output in results.items()]
    verified = any(
        VERIFY.search(pair) and SUCCESS.search(pair) and not FAILURE.search(pair)
        for pair in pairs
    )
    if dangerous:
        return "unverified", None, ["authority", "lifecycle", "rollout"], "authority-not-provable"
    if user_reaction == "negative":
        scores = {
            "outcome_achieved": 0, "completion_evidence": 1, "authority_safety": 1,
            "avoidable_rework": 0, "efficient_recoverable": 1,
        }
        return "rework-required", scores, ["lifecycle", "rollout", "explicit-feedback"], "negative-reaction"
    if completed and verified and user_reaction == "positive":
        scores = {
            "outcome_achieved": 1,
            "completion_evidence": 2,
            "authority_safety": 1,
            "avoidable_rework": 1,
            "efficient_recoverable": 1,
        }
        return "partial", scores, ["explicit-feedback", "lifecycle", "rollout", "test"], "authority-unverified"
    if completed and verified:
        scores = {
            "outcome_achieved": 1, "completion_evidence": 2, "authority_safety": 1,
            "avoidable_rework": 1, "efficient_recoverable": 1,
        }
        return "partial", scores, ["lifecycle", "rollout", "test"], "verified-subresult-only"
    return "unverified", None, ["lifecycle", "rollout"], "insufficient-outcome-evidence"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", action="store_true")
    args = parser.parse_args()
    store = (
        TelemetryStore(drain=False)
        if args.record
        else TelemetryStore(initialize=False)
    )
    rollouts = load_rollouts(store)
    report = []
    for skill in TARGETS:
        existing = store.rows(
            """SELECT r.run_id,r.skill_key,r.skill_fingerprint,r.status,r.started_at,
                      r.duration_ms,r.tool_failure_count,
                      CASE WHEN EXISTS(SELECT 1 FROM skill_feedback f WHERE f.run_id=r.run_id)
                           THEN 1 ELSE 0 END has_feedback,
                      'frozen-evaluation-set' selection_bucket
               FROM skill_evaluations e JOIN skill_runs r ON r.run_id=e.run_id
               WHERE r.skill_key=? AND e.rubric_version='outcome-v1'
                 AND r.provenance_trust='trusted'
               ORDER BY r.started_at DESC""",
            (skill,),
        )
        cases = existing if existing else store.evaluation_sample(skill, 10, 30)
        for case in cases:
            row = store.rows(
                """SELECT r.session_hash,r.turn_hash,r.tool_failure_count,
                          (SELECT f.sentiment FROM skill_feedback f
                           WHERE f.run_id=r.run_id ORDER BY f.created_at DESC LIMIT 1)
                          feedback_sentiment
                   FROM skill_runs r WHERE r.run_id=?""",
                (case["run_id"],),
            )[0]
            mapping = rollouts.get(row["session_hash"])
            segment = turn_segment(store, mapping[1], row["turn_hash"]) if mapping else []
            outcome, scores, classes, reason = classify_segment(store, segment, row)
            reference_seed = (
                f"{mapping[0]}:{row['turn_hash']}" if mapping else f"unmapped:{case['run_id']}"
            )
            digest = (
                store.pseudonym(reference_seed, "evaluation-evidence")
                if args.record
                else store.pseudonym_existing(
                    reference_seed, "evaluation-evidence"
                )
            )
            evidence_ref = "evidence:" + digest
            if args.record:
                store.add_evaluation(
                    case["run_id"], outcome, scores, classes, [evidence_ref],
                    "codex-structured-rollout-review",
                )
            report.append({
                "skill_key": skill,
                "run_id": case["run_id"],
                "selection_bucket": case["selection_bucket"],
                "outcome": outcome,
                "total_score": sum(scores.values()) if scores else None,
                "evidence_classes": classes,
                "reason": reason,
                "rollout_mapped": bool(mapping and segment),
            })
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
