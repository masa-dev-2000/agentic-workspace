from __future__ import annotations

import hashlib
import hmac
import sys
from pathlib import Path

import pytest

SKILL_ROOT = Path(__file__).resolve().parents[1]
TELEMETRY_ROOT = SKILL_ROOT.parent / "skill-telemetry"
sys.path.insert(0, str(SKILL_ROOT / "scripts"))
sys.path.insert(0, str(TELEMETRY_ROOT / "scripts"))

from selection_audit_v2 import SelectionAuditV2  # noqa: E402
from telemetry_store import TelemetryStore  # noqa: E402


def _payload(store: TelemetryStore, *, session="session-a", turn="turn-a"):
    session_hash = store.pseudonym(session, "session")
    turn_hash = store.pseudonym(turn, "turn")
    audit_id = "selectionaudit_" + hmac.new(
        store._secret(), f"audit:{session_hash}:{turn_hash}".encode(), hashlib.sha256
    ).hexdigest()[:32]
    return {
        "audit_id": audit_id,
        "job_id": "job-a",
        "session_id": session,
        "turn_id": turn,
        "registry_revision": "registry-1",
        "taxonomy_version": "taxonomy-1",
        "observation_window_closed": True,
        "telemetry_health": "complete",
        "runner_terminal": True,
        "spool_pending": 0,
        "cardinality_ok": True,
        "candidates": [
            {
                "skill_key": "skill-a",
                "source": "registry_profile",
                "source_revision": "rev-a",
                "source_digest": "a" * 64,
                "coverage": "known",
            },
            {
                "skill_key": "skill-b",
                "source": "planner_candidate",
                "source_revision": "rev-b",
                "source_digest": "b" * 64,
                "coverage": "known",
            },
        ],
    }


def _observe(store: TelemetryStore, session: str, turn: str, *skills: str):
    session_hash = store.pseudonym(session, "session")
    turn_hash = store.pseudonym(turn, "turn")
    for index, skill in enumerate(skills):
        with store.connection() as db:
            db.execute(
                """INSERT INTO skill_runs
                   (run_id,idempotency_key,skill_key,skill_name,provider,source_class,
                    skill_fingerprint,session_hash,turn_hash,repo_hash,model_class,
                    detection,status,started_at,ended_at,provenance_trust,end_reason,
                    duration_quality)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    f"skillrun_{index:032x}", f"idem-{index}", skill, skill, "test", "test",
                    f"fp-{index}", session_hash, turn_hash, "repo", "test", "explicit-manual",
                    "returned", "2026-08-04T00:00:00+00:00", "2026-08-04T00:00:01+00:00",
                    "trusted", "stop", "exact",
                ),
            )


def test_selected_and_not_comparable(tmp_path):
    store = TelemetryStore(tmp_path)
    _observe(store, "session-a", "turn-a", "skill-a")
    result = SelectionAuditV2(store).record(_payload(store))
    rows = store.rows(
        "SELECT skill_key,classification FROM skill_selection_candidates ORDER BY skill_key"
    )
    assert result["persisted"] is True
    assert rows == [
        {"skill_key": "skill-a", "classification": "selected"},
        {"skill_key": "skill-b", "classification": "not_comparable"},
    ]


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p["candidates"].append(dict(p["candidates"][0])),
        lambda p: p["candidates"][0].update(classification="missed_candidate"),
        lambda p: p["candidates"][0].update(coverage="invalid"),
        lambda p: p.update(prompt="raw"),
    ],
)
def test_rejects_duplicate_old_state_invalid_enum_and_raw_body(tmp_path, change):
    store = TelemetryStore(tmp_path)
    payload = _payload(store)
    change(payload)
    with pytest.raises(ValueError):
        SelectionAuditV2(store).record(payload)


def test_not_observable_and_unknown_coverage(tmp_path):
    store = TelemetryStore(tmp_path)
    payload = _payload(store)
    payload["observation_window_closed"] = False
    result = SelectionAuditV2(store).record(payload)
    assert result["persisted"] is True
    rows = store.rows("SELECT classification FROM skill_selection_candidates ORDER BY skill_key")
    assert [row["classification"] for row in rows] == ["not_observable", "not_observable"]

    store = TelemetryStore(tmp_path / "unknown")
    payload = _payload(store, session="session-b", turn="turn-b")
    payload["candidates"][1]["coverage"] = "unknown"
    _observe(store, "session-b", "turn-b", "skill-a")
    SelectionAuditV2(store).record(payload)
    rows = store.rows("SELECT skill_key,classification FROM skill_selection_candidates ORDER BY skill_key")
    assert rows[1]["classification"] == "candidate_coverage_unknown"


def test_multi_observed_conflict_and_hmac_mismatch(tmp_path):
    store = TelemetryStore(tmp_path)
    _observe(store, "session-a", "turn-a", "skill-a", "skill-b")
    with pytest.raises(ValueError, match="multi-observed"):
        SelectionAuditV2(store).record(_payload(store))
    payload = _payload(store, session="session-c", turn="turn-c")
    payload["audit_id"] = "selectionaudit_" + "0" * 32
    with pytest.raises(ValueError, match="HMAC"):
        SelectionAuditV2(store).record(payload)


def test_same_payload_is_idempotent_and_different_payload_conflicts(tmp_path):
    store = TelemetryStore(tmp_path)
    adapter = SelectionAuditV2(store)
    payload = _payload(store)
    first = adapter.record(payload)
    second = adapter.record(payload)
    assert first == second
    changed = _payload(store)
    changed["candidates"][1]["source_digest"] = "c" * 64
    with pytest.raises(ValueError, match="conflicts"):
        adapter.record(changed)

