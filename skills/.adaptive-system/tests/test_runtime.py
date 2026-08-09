from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


SYSTEM_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = SYSTEM_ROOT.parent
RUNTIME_ROOT = SYSTEM_ROOT / "runtime"
PACKAGING_ROOT = SYSTEM_ROOT / "packaging"
sys.path.insert(0, str(RUNTIME_ROOT))
sys.path.insert(0, str(PACKAGING_ROOT))

from approval import (  # noqa: E402
    approval_hash,
    consume_approval,
    sign_approval,
    verify_approval,
)
from batch_router import BatchRouter, RouterLease, _path_token  # noqa: E402
from capability_resolver import CapabilityResolver  # noqa: E402
from control_cli import ControlSurface, _ControlMutationLock  # noqa: E402
from cutover_plan import build_cutover_plan  # noqa: E402
from export_plan import _trusted_validator, build_export_plan  # noqa: E402
from hook_dispatcher import dispatch_bytes  # noqa: E402
from process_lock import LocalProcessLock  # noqa: E402


UTC = timezone.utc


def dump_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class ContractSchemaTests(unittest.TestCase):
    def test_all_required_schemas_are_draft_2020_12_json(self) -> None:
        expected = {
            "invocation-envelope.schema.json",
            "skill-result.schema.json",
            "event-envelope.schema.json",
            "learning-signal.schema.json",
            "improvement-proposal.schema.json",
            "approval.schema.json",
            "experiment.schema.json",
        }
        actual = {
            path.name for path in (SYSTEM_ROOT / "contracts").glob("*.schema.json")
        }
        self.assertEqual(expected, actual)
        for name in expected:
            schema = json.loads(
                (SYSTEM_ROOT / "contracts" / name).read_text(encoding="utf-8")
            )
            self.assertEqual(
                "https://json-schema.org/draft/2020-12/schema",
                schema["$schema"],
            )
            self.assertFalse(schema.get("additionalProperties", True), name)
            self.assertTrue(schema.get("required"), name)


class CapabilityResolverTests(unittest.TestCase):
    def test_local_source_wins_without_hardcoded_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            skills = root / "portable" / "skills"
            local_skill = skills / "local-planner"
            local_skill.mkdir(parents=True)
            (local_skill / "SKILL.md").write_text(
                "---\nname: local-planner\n---\n", encoding="utf-8"
            )
            registry = root / "registry.json"
            dump_json(
                registry,
                {
                    "schemaVersion": 2,
                    "skills": [
                        {
                            "key": "adaptive:planner",
                            "provider": "personal-plugin",
                            "capabilities": ["project.plan"],
                            "resolutions": {
                                "local": {
                                    "key": "local-planner",
                                    "relativePath": "local-planner",
                                },
                                "plugin": {"key": "adaptive:planner"},
                            },
                        }
                    ],
                },
            )

            result = CapabilityResolver(registry, skills).resolve("project.plan")

            self.assertEqual("local", result.source)
            self.assertEqual("local-planner", result.invocation_key)
            self.assertEqual(local_skill.resolve(), result.skill_path)

    def test_missing_local_source_falls_back_to_plugin_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.json"
            dump_json(
                registry,
                {
                    "schemaVersion": 2,
                    "skills": [
                        {
                            "key": "adaptive:planner",
                            "provider": "personal-plugin",
                            "capabilities": ["project.plan"],
                            "resolutions": {
                                "local": {
                                    "key": "local-planner",
                                    "relativePath": "missing",
                                },
                                "plugin": {"key": "adaptive:planner"},
                            },
                        }
                    ],
                },
            )

            result = CapabilityResolver(registry, root / "skills").resolve(
                "project.plan"
            )

            self.assertEqual("plugin", result.source)
            self.assertEqual("adaptive:planner", result.invocation_key)
            self.assertIsNone(result.skill_path)

    def test_v2_yaml_source_and_path_resolve_as_local(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            skill = root / "planner"
            skill.mkdir()
            (skill / "SKILL.md").write_text(
                "---\nname: planner\n---\n", encoding="utf-8"
            )
            registry = root / "registry.yaml"
            registry.write_text(
                "\n".join(
                    [
                        "schemaVersion: 2",
                        "sources:",
                        "  local: {root: ., namespace: null}",
                        "skills:",
                        "  - key: planner",
                        "    capability: project.plan",
                        "    source: local",
                        "    path: planner",
                        "    version: 1.0.0",
                        "    contractFingerprint: contract-v2:project.plan@1.0.0",
                        "    contractContentDigest: sha256:" + ("a" * 64),
                    ]
                ),
                encoding="utf-8",
            )

            result = CapabilityResolver(registry, root).resolve("project.plan")

            self.assertEqual("local", result.source)
            self.assertEqual(skill.resolve(), result.skill_path)
            self.assertEqual(
                "sha256:" + ("a" * 64),
                result.contract_hash,
            )

    def test_v2_external_capability_resolves_provider_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            registry.write_text(
                "\n".join(
                    [
                        "schemaVersion: 2",
                        "externalCapabilities:",
                        "  - {id: image.generate, providerKey: imagegen}",
                        "skills: []",
                    ]
                ),
                encoding="utf-8",
            )

            result = CapabilityResolver(registry, root).resolve(
                "image.generate",
                prefer="plugin",
            )

            self.assertEqual("plugin", result.source)
            self.assertEqual("imagegen", result.invocation_key)


class HookDispatcherTests(unittest.TestCase):
    def test_invalid_input_fails_open_without_spooling(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = dispatch_bytes(b"{not-json", Path(temp))
            self.assertFalse(result.stored)
            self.assertEqual("invalid-input", result.status)
            self.assertEqual([], list(Path(temp).glob("*.json")))

    def test_duplicate_event_is_written_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            spool = Path(temp)
            event = {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "s-1",
                "turn_id": "t-1",
                "event_id": "evt-1",
            }
            raw = json.dumps(event).encode()

            first = dispatch_bytes(raw, spool)
            second = dispatch_bytes(raw, spool)

            self.assertTrue(first.stored)
            self.assertTrue(second.duplicate)
            self.assertEqual(1, len(list(spool.glob("*.json"))))

    def test_raw_bodies_are_never_persisted_but_content_refs_are(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            spool = Path(temp)
            secret = "RAW-SECRET-DO-NOT-STORE"
            event = {
                "hook_event_name": "Stop",
                "session_id": "s-2",
                "turn_id": "t-2",
                "event_id": "evt-2",
                "prompt": secret,
                "last_assistant_message": secret,
                "tool_input": {"command": secret},
                "tool_response": secret,
                "contentRef": "vault://opaque/abc1234567890xyz",
                "cwd": "C:/project",
            }

            result = dispatch_bytes(json.dumps(event).encode(), spool)
            persisted = result.path.read_text(encoding="utf-8")
            envelope = json.loads(persisted)

            self.assertNotIn(secret, persisted)
            self.assertEqual(
                ["vault://opaque/abc1234567890xyz"],
                envelope["contentRefs"],
            )
            self.assertFalse(envelope["privacy"]["rawContentStored"])
            self.assertNotIn("cwd", persisted)

    def test_content_ref_rejects_inline_and_free_text_uri(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            secret = "RAW-SECRET"
            event = {
                "hook_event_name": "Stop",
                "event_id": "evt-inline-ref",
                "contentRef": f"data://{secret}",
            }
            result = dispatch_bytes(json.dumps(event).encode(), Path(temp))
            persisted = result.path.read_text(encoding="utf-8")
            self.assertNotIn(secret, persisted)
            self.assertEqual([], json.loads(persisted)["contentRefs"])

    def test_untrusted_timestamp_and_windows_unsafe_id_are_not_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            spool = Path(temp)
            secret = "RAW-TIMESTAMP-SECRET"
            event = {
                "hook_event_name": "Custom_Hook",
                "event_id": "unsafe:event",
                "timestamp": secret,
            }

            result = dispatch_bytes(json.dumps(event).encode(), spool)
            persisted = result.path.read_text(encoding="utf-8")
            envelope = json.loads(persisted)

            self.assertTrue(result.stored)
            self.assertNotIn(secret, persisted)
            self.assertNotIn(":", result.path.name)
            self.assertEqual("hook.custom-hook", envelope["eventType"])

    def test_unidentified_same_length_events_are_not_false_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            spool = Path(temp)
            first = dispatch_bytes(
                json.dumps(
                    {"hook_event_name": "UserPromptSubmit", "prompt": "A"}
                ).encode(),
                spool,
            )
            second = dispatch_bytes(
                json.dumps(
                    {"hook_event_name": "UserPromptSubmit", "prompt": "B"}
                ).encode(),
                spool,
            )

            self.assertTrue(first.stored)
            self.assertTrue(second.stored)
            self.assertFalse(second.duplicate)
            self.assertEqual(2, len(list(spool.glob("*.json"))))

    def test_coarse_correlated_events_without_event_id_are_both_stored(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            spool = Path(temp)
            event = {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "same-session",
                "turn_id": "same-coarse-turn",
            }

            first = dispatch_bytes(json.dumps(event).encode(), spool)
            second = dispatch_bytes(json.dumps(event).encode(), spool)

            self.assertTrue(first.stored)
            self.assertTrue(second.stored)
            self.assertFalse(second.duplicate)
            self.assertNotEqual(first.path, second.path)

    def test_storage_crash_is_fail_open(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            not_a_directory = Path(temp) / "blocked"
            not_a_directory.write_text("file", encoding="utf-8")
            result = dispatch_bytes(
                json.dumps({"hook_event_name": "Stop", "event_id": "evt-3"}).encode(),
                not_a_directory,
            )
            self.assertFalse(result.stored)
            self.assertEqual("storage-error", result.status)

    def test_hook_cli_argument_error_is_fail_open(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(RUNTIME_ROOT / "hook_dispatcher.py"),
                "--unsupported-option",
            ],
            input=b"{}",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(0, completed.returncode)

    def test_large_raw_hook_payload_stays_within_timeout_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            secret = "x" * 2_000_000
            raw = json.dumps(
                {
                    "hook_event_name": "PostToolUse",
                    "event_id": "evt-large",
                    "tool_response": secret,
                }
            ).encode()
            started = time.perf_counter()
            result = dispatch_bytes(raw, Path(temp))
            elapsed = time.perf_counter() - started

            self.assertTrue(result.stored)
            self.assertLess(elapsed, 1.5)
            self.assertLess(result.path.stat().st_size, 4096)


class ApprovalTests(unittest.TestCase):
    TRUSTED_KEYS = {"human-key-v1": b"test-only-secret"}

    def _sign(self, approval: dict[str, object]) -> dict[str, object]:
        return sign_approval(
            approval,
            key_id="human-key-v1",
            secret=self.TRUSTED_KEYS["human-key-v1"],
        )

    def test_approval_is_bound_to_exact_proposal_hash(self) -> None:
        proposal = {
            "proposalId": "prop-1",
            "targetRef": "skill://planner",
            "changeRef": "artifact://candidate/1",
        }
        approval = self._sign(
            {
                "schemaVersion": "1.0",
                "approvalId": "approval-1",
                "proposalId": "prop-1",
                "decision": "approved",
                "approvedHash": approval_hash(proposal),
                "scope": ["activate-candidate"],
                "decidedBy": "human:masa",
                "decidedAt": "2026-07-30T00:00:00Z",
                "expiresAt": "2099-01-01T00:00:00Z",
            }
        )

        self.assertTrue(
            verify_approval(
                approval,
                proposal,
                "activate-candidate",
                trusted_keys=self.TRUSTED_KEYS,
            ).valid
        )
        proposal["changeRef"] = "artifact://candidate/2"
        result = verify_approval(
            approval,
            proposal,
            "activate-candidate",
            trusted_keys=self.TRUSTED_KEYS,
        )
        self.assertFalse(result.valid)
        self.assertEqual("hash-mismatch", result.reason)

    def test_expired_approval_is_rejected(self) -> None:
        proposal = {"proposalId": "prop-2"}
        approval = self._sign(
            {
                "schemaVersion": "1.0",
                "approvalId": "approval-2",
                "proposalId": "prop-2",
                "decision": "approved",
                "approvedHash": approval_hash(proposal),
                "scope": ["activate-candidate"],
                "decidedBy": "human:masa",
                "decidedAt": "2026-07-30T00:00:00Z",
                "expiresAt": "2000-01-01T00:00:00Z",
            }
        )
        result = verify_approval(
            approval,
            proposal,
            "activate-candidate",
            trusted_keys=self.TRUSTED_KEYS,
        )
        self.assertFalse(result.valid)
        self.assertEqual("expired", result.reason)

    def test_approval_consumption_is_atomic_and_single_use(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            proposal = {
                "proposalId": "prop-3",
                "candidateRef": "artifact://candidate/3",
            }
            approval = self._sign(
                {
                    "schemaVersion": "1.0",
                    "approvalId": "approval-3",
                    "proposalId": "prop-3",
                    "decision": "approved",
                    "approvedHash": approval_hash(proposal),
                    "scope": ["activate-candidate"],
                    "decidedBy": "human:masa",
                    "decidedAt": "2026-07-30T00:00:00Z",
                    "expiresAt": "2099-01-01T00:00:00Z",
                }
            )

            first = consume_approval(
                approval,
                proposal,
                "activate-candidate",
                Path(temp),
                trusted_keys=self.TRUSTED_KEYS,
            )
            second = consume_approval(
                approval,
                proposal,
                "activate-candidate",
                Path(temp),
                trusted_keys=self.TRUSTED_KEYS,
            )

            self.assertTrue(first.valid)
            self.assertFalse(second.valid)
            self.assertEqual("already-consumed", second.reason)
            receipt = json.loads(
                next(Path(temp).glob("*.json")).read_text(encoding="utf-8")
            )
            self.assertEqual("approval-3", receipt["approvalId"])
            self.assertNotIn("candidateRef", receipt)

    def test_unsigned_self_asserted_approval_is_rejected(self) -> None:
        proposal = {"proposalId": "prop-unsigned"}
        approval = {
            "schemaVersion": "1.0",
            "approvalId": "approval-unsigned",
            "proposalId": "prop-unsigned",
            "decision": "approved",
            "approvedHash": approval_hash(proposal),
            "scope": ["activate-candidate"],
            "decidedBy": "human:claimed",
            "decidedAt": "2026-07-30T00:00:00Z",
        }

        result = verify_approval(
            approval,
            proposal,
            "activate-candidate",
            trusted_keys=self.TRUSTED_KEYS,
        )

        self.assertFalse(result.valid)
        self.assertEqual("untrusted-issuer", result.reason)


class BatchRouterTests(unittest.TestCase):
    def _event(self, spool: Path, event_id: str = "evt-route") -> None:
        dump_json(
            spool / f"{event_id}.json",
            {
                "schemaVersion": "1.0",
                "eventId": event_id,
                "occurredAt": "2026-07-30T00:00:00Z",
                "eventType": "feedback.explicit",
                "source": {"kind": "hook", "name": "UserPromptSubmit"},
                "correlation": {},
                "contentRefs": [],
                "metadata": {},
                "privacy": {"rawContentStored": False},
            },
        )

    def test_live_lock_prevents_second_router(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            spool = state / "spool"
            spool.mkdir()
            self._event(spool)
            dump_json(
                state / "router.lock",
                {
                    "owner": "other",
                    "expiresAt": (datetime.now(UTC) + timedelta(minutes=5))
                    .isoformat()
                    .replace("+00:00", "Z"),
                },
            )
            router = BatchRouter(state, {"routes": {}})
            result = router.run()

            self.assertEqual("locked", result.status)
            self.assertTrue((spool / "evt-route.json").exists())

    def test_fresh_partial_lock_is_treated_as_live_not_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            spool = state / "spool"
            spool.mkdir()
            self._event(spool)
            (state / "router.lock").write_text("{partial", encoding="utf-8")

            result = BatchRouter(state, {"routes": {}}).run()

            self.assertEqual("locked", result.status)
            self.assertEqual(
                "{partial", (state / "router.lock").read_text(encoding="utf-8")
            )

    def test_lease_covers_batch_budget_and_longest_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            router = BatchRouter(
                Path(temp),
                {
                    "leaseSeconds": 1,
                    "budget": {"maxSeconds": 20},
                    "adapters": {"slow": {"command": ["unused"], "timeoutSeconds": 30}},
                },
            )
            self.assertGreaterEqual(router._effective_lease_seconds(), 55)

    def test_lease_refresh_extends_current_owner_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            current = [datetime(2026, 7, 30, tzinfo=UTC)]
            lease = RouterLease(Path(temp) / "router.lock", lambda: current[0], 10)
            self.assertTrue(lease.acquire())
            initial = json.loads(lease.path.read_text(encoding="utf-8"))["expiresAt"]
            current[0] += timedelta(seconds=5)

            self.assertTrue(lease.refresh())
            refreshed = json.loads(lease.path.read_text(encoding="utf-8"))["expiresAt"]
            self.assertGreater(refreshed, initial)
            lease.release()

    def test_expired_lease_cannot_displace_live_os_lock_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            current = [datetime(2026, 7, 30, tzinfo=UTC)]
            path = Path(temp) / "router.lock"
            first = RouterLease(path, lambda: current[0], 1)
            self.assertTrue(first.acquire())
            current[0] += timedelta(minutes=5)
            second = RouterLease(path, lambda: current[0], 1)

            self.assertFalse(second.acquire())
            first.release()
            self.assertTrue(second.acquire())
            second.release()

    def test_two_processes_have_one_stale_lease_reclaim_winner(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lock_path = root / "router.lock"
            dump_json(
                lock_path,
                {
                    "owner": "crashed",
                    "expiresAt": "2020-01-01T00:00:00Z",
                },
            )
            go = root / "go"
            code = (
                "import sys,time;"
                f"sys.path.insert(0,{str(RUNTIME_ROOT)!r});"
                "from datetime import datetime,timezone;"
                "from pathlib import Path;"
                "from batch_router import RouterLease;"
                "ready,result,go,lock=map(Path,sys.argv[1:]);"
                "ready.write_text('ready');"
                "\nwhile not go.exists(): time.sleep(0.005)"
                "\nlease=RouterLease(lock,lambda:datetime.now(timezone.utc),30);"
                "\nok=lease.acquire();result.write_text('1' if ok else '0');"
                "\nif ok: time.sleep(0.4);lease.release()"
            )
            processes = []
            results = []
            for index in range(2):
                ready = root / f"ready-{index}"
                result = root / f"result-{index}"
                results.append(result)
                processes.append(
                    subprocess.Popen(
                        [
                            sys.executable,
                            "-c",
                            code,
                            str(ready),
                            str(result),
                            str(go),
                            str(lock_path),
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                )
            try:
                deadline = time.monotonic() + 2
                while (
                    not all((root / f"ready-{index}").exists() for index in range(2))
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.01)
                self.assertTrue(
                    all((root / f"ready-{index}").exists() for index in range(2))
                )
                go.write_text("go", encoding="utf-8")
                for process in processes:
                    process.wait(timeout=2)
                self.assertEqual(
                    ["0", "1"], sorted(path.read_text() for path in results)
                )
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=2)

    def test_existing_receipt_deduplicates_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            spool = state / "spool"
            spool.mkdir()
            self._event(spool)
            dump_json(state / "receipts" / "evt-route.json", {"status": "processed"})

            result = BatchRouter(state, {"routes": {}}).run()

            self.assertEqual(1, result.deduplicated)
            self.assertFalse((spool / "evt-route.json").exists())

    def test_retry_limit_and_circuit_breaker_bound_adapter_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            spool = state / "spool"
            spool.mkdir()
            self._event(spool)
            config = {
                "routes": {"feedback.explicit": "always-fails"},
                "adapters": {
                    "always-fails": {
                        "enabled": True,
                        "command": [sys.executable, "-c", "raise SystemExit(7)"],
                        "timeoutSeconds": 2,
                    }
                },
                "maxRetries": 1,
                "budget": {"maxItems": 10, "maxAdapterRuns": 10, "maxSeconds": 5},
                "circuitBreaker": {"failureThreshold": 1, "cooldownSeconds": 60},
            }

            first = BatchRouter(state, config, execute_adapters=True).run()

            self.assertEqual(1, first.failed)
            self.assertTrue((state / "dead-letter" / "evt-route.json").exists())
            breaker = json.loads(
                (state / "router-state.json").read_text(encoding="utf-8")
            )
            self.assertEqual("open", breaker["circuits"]["always-fails"]["state"])

    def test_router_rejects_extra_raw_fields_before_adapter_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            spool = state / "spool"
            spool.mkdir()
            delivered = state / "delivered.txt"
            malicious = {
                "schemaVersion": "1.0",
                "eventId": "evt-malicious",
                "occurredAt": "2026-07-30T00:00:00Z",
                "eventType": "feedback.explicit",
                "source": {"kind": "hook", "name": "UserPromptSubmit"},
                "correlation": {},
                "contentRefs": [],
                "metadata": {},
                "privacy": {"rawContentStored": False},
                "prompt": "RAW-SECRET",
                "tool_response": "RAW-SECRET",
            }
            dump_json(spool / "evt-malicious.json", malicious)
            config = {
                "routes": {"feedback.explicit": "sink"},
                "adapters": {
                    "sink": {
                        "enabled": True,
                        "command": [
                            sys.executable,
                            "-c",
                            (
                                "import pathlib,sys; "
                                f"pathlib.Path({str(delivered)!r}).write_text(sys.stdin.read())"
                            ),
                        ],
                    }
                },
                "maxRetries": 1,
            }

            result = BatchRouter(state, config, execute_adapters=True).run()

            self.assertEqual(1, result.failed)
            self.assertFalse(delivered.exists())
            self.assertTrue((state / "dead-letter" / "evt-malicious.json").exists())

    def test_adapter_execution_requires_explicit_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            spool = state / "spool"
            spool.mkdir()
            self._event(spool)
            marker = state / "ran.txt"
            config = {
                "routes": {"feedback.explicit": "collector"},
                "adapters": {
                    "collector": {
                        "enabled": True,
                        "command": [
                            sys.executable,
                            "-c",
                            f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')",
                        ],
                    }
                },
            }

            result = BatchRouter(state, config, execute_adapters=False).run()

            self.assertEqual(1, result.deferred)
            self.assertFalse(marker.exists())

    def test_adapter_content_ref_contract_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            spool = state / "spool"
            spool.mkdir()
            self._event(spool, "evt-needs-content")
            marker = state / "ran.txt"
            config = {
                "routes": {"feedback.explicit": "collector"},
                "adapters": {
                    "collector": {
                        "enabled": True,
                        "inputContract": "EventEnvelope/1.0",
                        "requiresContentRef": True,
                        "command": [
                            sys.executable,
                            "-c",
                            f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')",
                        ],
                    }
                },
            }

            result = BatchRouter(state, config, execute_adapters=True).run()

            self.assertEqual(1, result.deferred)
            self.assertFalse(marker.exists())
            self.assertTrue((spool / "evt-needs-content.json").exists())

    def test_fanout_adapter_receipts_prevent_successful_adapter_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            spool = state / "spool"
            spool.mkdir()
            self._event(spool, "evt-fanout")
            first_marker = state / "first.txt"
            second_marker = state / "second.txt"
            config = {
                "routes": {"feedback.explicit": ["first", "second"]},
                "adapters": {
                    "first": {
                        "enabled": True,
                        "command": [
                            sys.executable,
                            "-c",
                            (
                                "from pathlib import Path; "
                                f"p=Path({str(first_marker)!r}); "
                                "p.write_text(p.read_text()+'1' if p.exists() else '1')"
                            ),
                        ],
                    },
                    "second": {
                        "enabled": True,
                        "command": [
                            sys.executable,
                            "-c",
                            f"from pathlib import Path; Path({str(second_marker)!r}).write_text('1')",
                        ],
                    },
                },
            }

            result = BatchRouter(state, config, execute_adapters=True).run()
            replay = BatchRouter(state, config, execute_adapters=True).run()

            self.assertEqual(1, result.processed)
            self.assertEqual("1", first_marker.read_text())
            self.assertEqual("1", second_marker.read_text())
            self.assertEqual(0, replay.processed)
            adapter_receipts = list(
                (state / "adapter-receipts" / "evt-fanout").glob("*.json")
            )
            self.assertEqual(2, len(adapter_receipts))

    def test_unsafe_adapter_names_cannot_collide_on_receipt_path(self) -> None:
        self.assertNotEqual(_path_token("a:b"), _path_token("a/b"))

    def test_adapter_without_explicit_enabled_never_executes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            spool = state / "spool"
            spool.mkdir()
            self._event(spool, "evt-disabled-default")
            marker = state / "ran.txt"
            config = {
                "routes": {"feedback.explicit": "collector"},
                "adapters": {
                    "collector": {
                        "command": [
                            sys.executable,
                            "-c",
                            f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')",
                        ]
                    }
                },
            }

            result = BatchRouter(state, config, execute_adapters=True).run()

            self.assertEqual(1, result.deferred)
            self.assertFalse(marker.exists())
            self.assertTrue((spool / "evt-disabled-default.json").exists())

    def test_pause_and_kill_switch_stop_router_until_explicit_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            spool = state / "spool"
            spool.mkdir()
            self._event(spool)
            control = ControlSurface(state)

            paused = control.pause("maintenance")
            paused_result = BatchRouter(state, {"routes": {}}).run()
            self.assertTrue(paused["control"]["paused"])
            self.assertEqual("paused", paused_result.status)
            self.assertTrue((spool / "evt-route.json").exists())

            resumed = control.resume()
            self.assertEqual("active", resumed["status"])
            control.set_kill_switch(True, "safety test")
            killed_result = BatchRouter(state, {"routes": {}}).run()
            self.assertEqual("kill-switch", killed_result.status)
            self.assertTrue((spool / "evt-route.json").exists())

            blocked_resume = control.resume()
            self.assertEqual("blocked", blocked_resume["status"])
            control.set_kill_switch(False, "verified safe")
            control.resume()
            completed = BatchRouter(state, {"routes": {}}).run()
            self.assertEqual("completed", completed.status)
            self.assertFalse((spool / "evt-route.json").exists())

    def test_kill_switch_terminates_running_adapter_and_preserves_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            spool = state / "spool"
            spool.mkdir()
            self._event(spool, "evt-running")
            started = state / "started.txt"
            completed = state / "completed.txt"
            descendant = state / "descendant.txt"
            child_code = (
                "from pathlib import Path; import time; "
                "time.sleep(0.8); "
                f"Path({str(descendant)!r}).write_text('escaped')"
            )
            config = {
                "routes": {"feedback.explicit": "slow"},
                "adapters": {
                    "slow": {
                        "enabled": True,
                        "command": [
                            sys.executable,
                            "-c",
                            (
                                "from pathlib import Path; import subprocess,sys,time; "
                                f"Path({str(started)!r}).write_text('started'); "
                                f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
                                "time.sleep(3); "
                                f"Path({str(completed)!r}).write_text('completed')"
                            ),
                        ],
                        "timeoutSeconds": 5,
                    }
                },
                "budget": {"maxSeconds": 10, "maxAdapterRuns": 2},
            }
            holder: dict[str, object] = {}
            thread = threading.Thread(
                target=lambda: holder.setdefault(
                    "result",
                    BatchRouter(state, config, execute_adapters=True).run(),
                )
            )
            thread.start()
            deadline = time.monotonic() + 2
            while not started.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(started.exists())

            ControlSurface(state).set_kill_switch(True, "test stop")
            thread.join(timeout=2)

            self.assertFalse(thread.is_alive())
            self.assertEqual("kill-switch", holder["result"].status)
            self.assertFalse(completed.exists())
            self.assertTrue((spool / "evt-running.json").exists())
            time.sleep(1)
            self.assertFalse(descendant.exists())

    def test_health_reports_queue_and_lock_without_event_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            spool = state / "spool"
            spool.mkdir()
            self._event(spool, "evt-health")
            control = ControlSurface(state)

            health = control.health()
            rendered = json.dumps(health)

            self.assertEqual(1, health["queues"]["spool"])
            self.assertEqual("absent", health["lease"]["status"])
            self.assertNotIn("contentRefs", rendered)
            self.assertNotIn("metadata", rendered)

    def test_corrupt_control_file_stops_router_and_preserves_queue(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            spool = state / "spool"
            spool.mkdir()
            self._event(spool, "evt-control-corrupt")
            (state / "control.json").write_text("{corrupt", encoding="utf-8")

            result = BatchRouter(state, {"routes": {}}).run()

            self.assertEqual("invalid-control", result.status)
            self.assertTrue((spool / "evt-control-corrupt.json").exists())

    def test_stale_control_write_cannot_clear_newer_kill_switch(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            surface = ControlSurface(Path(temp))
            stale = surface._load()
            surface.set_kill_switch(True, "new safety state")

            with self.assertRaises(RuntimeError):
                surface._save(stale)

            self.assertEqual("kill-switch", surface.status()["status"])

    def test_old_control_lock_file_cannot_be_stolen_from_live_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            lock_path = Path(temp) / "control.lock"
            first = _ControlMutationLock(lock_path, timeout_seconds=0.1)
            first.__enter__()
            try:
                old = time.time() - 60
                os.utime(lock_path, (old, old))
                second = _ControlMutationLock(lock_path, timeout_seconds=0.05)
                with self.assertRaises(RuntimeError):
                    second.__enter__()
            finally:
                first.__exit__(None, None, None)


class LocalProcessLockTests(unittest.TestCase):
    def test_lock_is_exclusive_across_processes_and_released_on_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lock_path = root / "process.lock"
            ready = root / "ready"
            code = (
                "import sys,time;"
                f"sys.path.insert(0,{str(RUNTIME_ROOT)!r});"
                "from pathlib import Path;"
                "from process_lock import LocalProcessLock;"
                "lock=LocalProcessLock(Path(sys.argv[1]));"
                "assert lock.acquire();"
                "Path(sys.argv[2]).write_text('ready');"
                "time.sleep(0.8)"
            )
            process = subprocess.Popen(
                [sys.executable, "-c", code, str(lock_path), str(ready)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                deadline = time.monotonic() + 2
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(ready.exists())
                contender = LocalProcessLock(lock_path, timeout_seconds=0.05)
                self.assertFalse(contender.acquire())
                process.wait(timeout=2)
                self.assertTrue(contender.acquire())
                contender.release()
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=2)


class ExportPlanTests(unittest.TestCase):
    def test_registry_snapshots_are_read_once_for_validation_and_planning(self) -> None:
        registry_path = (SKILLS_ROOT / "skill-registry.yaml").resolve()
        component_path = (SKILLS_ROOT / "component-registry.yaml").resolve()
        counts = {registry_path: 0, component_path: 0}
        original_read_bytes = Path.read_bytes

        def tracked_read_bytes(candidate: Path) -> bytes:
            resolved = candidate.resolve()
            if resolved in counts:
                counts[resolved] += 1
            return original_read_bytes(candidate)

        with patch.object(Path, "read_bytes", tracked_read_bytes):
            plan = build_export_plan(
                registry_path=registry_path,
                component_registry_path=component_path,
                skills_root=SKILLS_ROOT,
                plugin_name="snapshot-preview",
                selected_keys=["build-complete-app"],
            )

        self.assertTrue(plan["validation"]["valid"])
        self.assertEqual(1, counts[registry_path])
        self.assertEqual(1, counts[component_path])

    def test_export_plan_never_executes_validator_from_target_skills_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            untrusted_root = Path(temp)
            sentinel = untrusted_root / "executed.txt"
            scripts = untrusted_root / "scripts"
            scripts.mkdir()
            (scripts / "validate_skill_registry.py").write_text(
                "from pathlib import Path\n"
                f"Path({str(sentinel)!r}).write_text('executed')\n",
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                build_export_plan(
                    registry_path=SKILLS_ROOT / "skill-registry.yaml",
                    component_registry_path=SKILLS_ROOT / "component-registry.yaml",
                    skills_root=untrusted_root,
                    plugin_name="untrusted-preview",
                    selected_keys=["build-complete-app"],
                )

            self.assertFalse(sentinel.exists())

    def test_trusted_validator_is_digest_bound_and_not_cached(self) -> None:
        with _trusted_validator() as (first, first_digest):
            first_name = first.__name__
            with _trusted_validator() as (second, second_digest):
                second_name = second.__name__
                self.assertIsNot(first, second)
                self.assertNotEqual(first_name, second_name)
                self.assertEqual(first_digest, second_digest)

        self.assertNotIn(first_name, sys.modules)
        self.assertNotIn(second_name, sys.modules)

    def test_export_plan_is_portable_dry_run_only(self) -> None:
        plan = build_export_plan(
            registry_path=SKILLS_ROOT / "skill-registry.yaml",
            skills_root=SKILLS_ROOT,
            plugin_name="adaptive-execution",
            selected_keys=["build-complete-app"],
        )

        self.assertEqual("dry-run", plan["mode"])
        self.assertFalse(plan["actions"]["package"])
        self.assertFalse(plan["actions"]["install"])
        self.assertTrue(plan["validation"]["valid"])
        self.assertTrue(plan["validation"]["registryDigest"].startswith("sha256:"))
        self.assertTrue(
            plan["validation"]["componentRegistryDigest"].startswith("sha256:")
        )
        self.assertTrue(plan["sourceSnapshotDigest"].startswith("sha256:"))
        self.assertIn(
            "skills/build-complete-app/SKILL.md",
            {item["destination"] for item in plan["sourceMap"]},
        )
        self.assertNotIn(str(SKILLS_ROOT), json.dumps(plan))

    def test_cutover_is_a_non_mutating_proposal_and_preserves_external_clone(
        self,
    ) -> None:
        hooks = {
            "hooks": {
                "UserPromptSubmit": [
                    {
                        "hooks": [
                            {
                                "command": "python C:/portable/skill-telemetry/capture_hook.py"
                            }
                        ]
                    },
                    {
                        "hooks": [
                            {"command": "python C:/portable/excel_capability_guard.py"}
                        ]
                    },
                    {
                        "hooks": [
                            {
                                "command": "python C:/portable/failure-learning/scripts/advice_hook.py"
                            }
                        ]
                    },
                ]
            }
        }
        before = json.dumps(hooks, sort_keys=True)

        plan = build_cutover_plan(
            hooks,
            dispatcher_command=[
                "python",
                "-X",
                "utf8",
                "%CODEX_HOME%/skills/.adaptive-system/runtime/hook_dispatcher.py",
            ],
        )

        self.assertEqual(before, json.dumps(hooks, sort_keys=True))
        self.assertEqual("proposal-only", plan["mode"])
        self.assertFalse(plan["actions"]["writeHooksConfig"])
        self.assertFalse(plan["actions"]["modifyPlugin"])
        self.assertEqual(
            "preserve",
            plan["legacyExternalCollectors"][0]["action"],
        )
        self.assertEqual(1, len(plan["patchProposal"]["removeExact"]))
        self.assertEqual(2, len(plan["patchProposal"]["preserveExact"]))
        self.assertFalse(plan["readyToApply"])
        self.assertIn("content-vault-reference", plan["preconditions"])

    def test_export_plan_rejects_unvalidated_registry(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            registry = root / "registry.yaml"
            registry.write_text("schemaVersion: 2\nskills: []\n", encoding="utf-8")

            with self.assertRaises(ValueError):
                build_export_plan(
                    registry_path=registry,
                    skills_root=SKILLS_ROOT,
                    plugin_name="preview",
                    selected_keys=[],
                )

    def test_export_cli_cannot_overwrite_any_output_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            victim = Path(temp) / "plugin.json"
            victim.write_text("do-not-change", encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(PACKAGING_ROOT / "export_plan.py"),
                    "--registry",
                    str(Path(temp) / "missing.yaml"),
                    "--skills-root",
                    temp,
                    "--plugin-name",
                    "preview",
                    "--output",
                    str(victim),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertNotEqual(0, completed.returncode)
            self.assertEqual("do-not-change", victim.read_text(encoding="utf-8"))


class PortabilityTests(unittest.TestCase):
    def test_runtime_sources_do_not_embed_a_user_profile_path(self) -> None:
        for folder in ("runtime", "packaging"):
            for source in (SYSTEM_ROOT / folder).glob("*.py"):
                body = source.read_text(encoding="utf-8").lower()
                self.assertNotIn("c:\\users\\", body, source)
                self.assertNotIn("/users/", body, source)

    def test_router_fixture_is_json_and_adapters_are_disabled_by_default(self) -> None:
        fixture = json.loads(
            (SYSTEM_ROOT / "runtime" / "router-config.fixture.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertTrue(fixture["adapters"])
        self.assertTrue(
            all(adapter["enabled"] is False for adapter in fixture["adapters"].values())
        )


if __name__ == "__main__":
    unittest.main()
