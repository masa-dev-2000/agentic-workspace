"""Small, deterministic local runtime boundaries used by the Phase 4-6 pack.

These components own no external resources. They make the safety contracts
executable without pretending that Codex's unavailable approval/network
runtime is observable.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


class RuntimeContractError(ValueError):
    pass


def approval_fingerprint(*, plan_digest: str, invocation_digest: str, scope: list[str], expires_at: int, nonce: str) -> str:
    if not all(isinstance(value, str) and value for value in (plan_digest, invocation_digest, nonce)):
        raise RuntimeContractError("approval identity fields are required")
    if not isinstance(scope, list) or any(not isinstance(value, str) or not value for value in scope):
        raise RuntimeContractError("approval scope must be a list of non-empty strings")
    if not isinstance(expires_at, int) or expires_at <= 0:
        raise RuntimeContractError("approval expiry must be a positive integer")
    return digest({"plan_digest": plan_digest, "invocation_digest": invocation_digest, "scope": sorted(scope), "expires_at": expires_at, "nonce": nonce})


@dataclass
class ApprovalLedger:
    approvals: dict[str, dict[str, Any]] = field(default_factory=dict)
    consumed_nonces: set[str] = field(default_factory=set)

    def issue(self, *, plan_digest: str, invocation_digest: str, scope: list[str], expires_at: int, nonce: str) -> dict[str, Any]:
        fingerprint = approval_fingerprint(plan_digest=plan_digest, invocation_digest=invocation_digest, scope=scope, expires_at=expires_at, nonce=nonce)
        if nonce in self.consumed_nonces or any(item["nonce"] == nonce for item in self.approvals.values()):
            raise RuntimeContractError("approval nonce replay")
        record = {"fingerprint": fingerprint, "plan_digest": plan_digest, "invocation_digest": invocation_digest, "scope": sorted(scope), "expires_at": expires_at, "nonce": nonce, "consumed": False}
        self.approvals[fingerprint] = record
        return dict(record)

    def consume(self, *, fingerprint: str, plan_digest: str, invocation_digest: str, scope: list[str], now: int) -> dict[str, Any]:
        record = self.approvals.get(fingerprint)
        if not record or record["consumed"]:
            raise RuntimeContractError("approval missing or already consumed")
        if record["expires_at"] < now:
            raise RuntimeContractError("approval expired")
        expected = approval_fingerprint(plan_digest=plan_digest, invocation_digest=invocation_digest, scope=scope, expires_at=record["expires_at"], nonce=record["nonce"])
        if expected != fingerprint or record["plan_digest"] != plan_digest or record["invocation_digest"] != invocation_digest or record["scope"] != sorted(scope):
            raise RuntimeContractError("approval scope or plan mismatch")
        record["consumed"] = True
        self.consumed_nonces.add(record["nonce"])
        return dict(record)


@dataclass
class RetryBudget:
    max_attempts: int
    attempts: int = 0
    retryable_failures: tuple[str, ...] = ("transient", "lease-expired", "repairable")

    def record(self, failure_class: str) -> str:
        if failure_class not in self.retryable_failures:
            return "terminal"
        if self.attempts >= self.max_attempts:
            return "budget-exhausted"
        self.attempts += 1
        return "retryable"


def normalized_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ignored = {"timestamp", "created_at", "pid", "runtime_handle", "nonce"}
    return [{key: value for key, value in event.items() if key not in ignored} for event in events]


def replay(events: list[dict[str, Any]], replay_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]]) -> dict[str, Any]:
    expected = normalized_events(events)
    actual = normalized_events(replay_fn(events))
    return {"equivalent": expected == actual, "expected_digest": digest(expected), "actual_digest": digest(actual), "expected": expected, "actual": actual}


@dataclass
class ShadowSink:
    writes: list[dict[str, Any]] = field(default_factory=list)

    def write(self, resource: str, value: Any) -> None:
        self.writes.append({"resource": resource, "value_digest": digest(value)})


def shadow(executor: Callable[[ShadowSink], list[dict[str, Any]]]) -> dict[str, Any]:
    before = digest([])
    sink = ShadowSink()
    events = executor(sink)
    after = digest(sink.writes)
    return {"events": events, "pre_snapshot": before, "post_snapshot": after, "side_effect_count": len(sink.writes), "side_effect_free": len(sink.writes) == 0}


class CanaryFacade:
    def __init__(self, legacy: Callable[[Any], Any], candidate: Callable[[Any], Any], canary_keys: set[str]):
        self.legacy = legacy
        self.candidate = candidate
        self.canary_keys = set(canary_keys)
        self.mode = "canary"
        self.rollback_count = 0
        self.routes: list[dict[str, Any]] = []

    def route(self, key: str, payload: Any) -> Any:
        use_candidate = self.mode in {"canary", "cutover"} and (self.mode == "cutover" or key in self.canary_keys)
        handler = self.candidate if use_candidate else self.legacy
        try:
            result = handler(payload)
        except Exception:
            if use_candidate:
                self.rollback()
                result = self.legacy(payload)
            else:
                raise
        self.routes.append({"key": key, "route": "candidate" if use_candidate else "legacy", "result_digest": digest(result)})
        return result

    def cutover(self) -> None:
        self.mode = "cutover"

    def rollback(self) -> None:
        self.mode = "legacy"
        self.rollback_count += 1

