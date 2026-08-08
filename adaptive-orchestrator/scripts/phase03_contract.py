"""Phase 0-3, side-effect-free execution contract.

This module intentionally does not call the stage runner, write the PM ledger,
dispatch agents, request approval, persist memory, or perform external work.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Iterable


ENVELOPE_FIELDS = frozenset(
    {
        "project_id",
        "task_id",
        "run_id",
        "attempt_id",
        "trace_id",
        "origin",
        "canonical_entry",
        "registry_revision",
        "ledger_revision",
        "policy_revision",
        "authority",
        "side_effect_mode",
        "idempotency_key",
        "parent_event_id",
        "created_at",
    }
)
REQUIRED_ENVELOPE_FIELDS = ENVELOPE_FIELDS
AUTHORITIES = frozenset(
    {
        "observe",
        "recommend",
        "write_local",
        "execute_reversible",
        "execute_external",
        "approval_required",
    }
)
SIDE_EFFECT_MODES = frozenset({"observe-only", "plan-only", "telemetry-only"})
FORBIDDEN_TELEMETRY_KEYS = frozenset(
    {"body", "content", "prompt", "response", "tool_input", "tool_output"}
)


class ContractError(ValueError):
    """Invalid contract input."""


class BoundaryViolation(PermissionError):
    """An operation is outside the Phase 0-3 side-effect boundary."""


def _nonempty_string(value: Any, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{field} must be a non-empty string")


def validate_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(envelope, dict):
        raise ContractError("envelope must be an object")
    unknown = set(envelope) - ENVELOPE_FIELDS
    missing = REQUIRED_ENVELOPE_FIELDS - set(envelope)
    if unknown:
        raise ContractError(f"unknown envelope fields: {sorted(unknown)}")
    if missing:
        raise ContractError(f"missing envelope fields: {sorted(missing)}")
    for field in ENVELOPE_FIELDS - {"parent_event_id", "authority", "side_effect_mode"}:
        _nonempty_string(envelope[field], field)
    if envelope["parent_event_id"] is not None:
        _nonempty_string(envelope["parent_event_id"], "parent_event_id")
    if envelope["authority"] not in AUTHORITIES:
        raise ContractError(f"invalid authority: {envelope['authority']}")
    if envelope["side_effect_mode"] not in SIDE_EFFECT_MODES:
        raise ContractError(f"invalid side_effect_mode: {envelope['side_effect_mode']}")
    if envelope["side_effect_mode"] == "observe-only" and envelope["authority"] != "observe":
        raise ContractError("observe-only requires authority=observe")
    if envelope["side_effect_mode"] == "plan-only" and envelope["authority"] != "recommend":
        raise ContractError("plan-only requires authority=recommend")
    return deepcopy(envelope)


class Phase03Boundary:
    """Allow observation/planning and metadata telemetry only."""

    def __init__(
        self,
        envelope: dict[str, Any],
        telemetry_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.envelope = validate_envelope(envelope)
        self.telemetry_sink = telemetry_sink
        self._telemetry_keys: set[str] = set()

    @property
    def mode(self) -> str:
        return self.envelope["side_effect_mode"]

    def observe(self, subject: str) -> dict[str, Any]:
        if self.mode != "observe-only":
            raise BoundaryViolation("observe is available only in observe-only mode")
        _nonempty_string(subject, "subject")
        return {"mode": self.mode, "subject": subject, "canonical_entry": self.envelope["canonical_entry"]}

    def plan(self, steps: Iterable[str]) -> dict[str, Any]:
        if self.mode != "plan-only":
            raise BoundaryViolation("plan is available only in plan-only mode")
        values = list(steps)
        if not values or any(not isinstance(step, str) or not step.strip() for step in values):
            raise ContractError("plan steps must contain non-empty strings")
        return {"mode": self.mode, "steps": values, "canonical_entry": self.envelope["canonical_entry"]}

    def append_telemetry(self, event: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(event, dict):
            raise ContractError("telemetry event must be an object")
        forbidden = FORBIDDEN_TELEMETRY_KEYS & set(event)
        if forbidden:
            raise ContractError(f"body-bearing telemetry fields are forbidden: {sorted(forbidden)}")
        key = event.get("idempotency_key", self.envelope["idempotency_key"])
        _nonempty_string(key, "idempotency_key")
        if key in self._telemetry_keys:
            return {"status": "deduplicated", "idempotency_key": key}
        record = {"event_type": "phase03-observation", "mode": self.mode, "idempotency_key": key}
        record.update({k: event[k] for k in event})
        self._telemetry_keys.add(key)
        if self.telemetry_sink is not None:
            self.telemetry_sink(deepcopy(record))
        return {"status": "appended", "event": record}

    def _reject(self, operation: str) -> None:
        raise BoundaryViolation(f"{operation} is forbidden in Phase 0-3 {self.mode}")

    def dispatch(self, *_args: Any, **_kwargs: Any) -> None:
        self._reject("dispatch")

    def write_ledger(self, *_args: Any, **_kwargs: Any) -> None:
        self._reject("Ledger write")

    def request_approval(self, *_args: Any, **_kwargs: Any) -> None:
        self._reject("approval")

    def write_memory(self, *_args: Any, **_kwargs: Any) -> None:
        self._reject("Memory write")

    def external_effect(self, *_args: Any, **_kwargs: Any) -> None:
        self._reject("external side effect")
