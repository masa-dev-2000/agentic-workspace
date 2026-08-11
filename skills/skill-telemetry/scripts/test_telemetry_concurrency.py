from __future__ import annotations

import io
import json
import multiprocessing
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from itertools import permutations
from pathlib import Path
from unittest import mock

import telemetry_cli
import telemetry_store
from telemetry_store import TelemetryStore


HOOK = Path(__file__).with_name("capture_hook.py")
CLI = Path(__file__).with_name("telemetry_cli.py")


def _initialize_and_write(root: str, index: int, start) -> None:
    start.wait(10)
    store = TelemetryStore(Path(root), drain=False)
    run = store.start_manual(
        f"worker-{index}",
        session_id="shared-session",
        turn_id=f"turn-{index}",
    )
    if not store.finish_run(run, "returned"):
        raise RuntimeError("worker run was not finished")


class TelemetryConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.root = self.base / "state"
        self.skill = Path(__file__).resolve().parents[1]

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def event(self, **changes) -> dict:
        value = {
            "session_id": "session-private",
            "turn_id": "turn-private",
            "cwd": str(self.skill.parent),
            "model": "test-model",
            "hook_event_name": "PostToolUse",
            "tool_name": "shell",
            "tool_use_id": "call-private",
            "tool_input": {
                "command": f'python test.py "{self.skill / "SKILL.md"}"'
            },
            "tool_response": {"exit_code": 0},
        }
        value.update(changes)
        return value

    def run_hook(self, event: dict, root: Path | None = None) -> subprocess.CompletedProcess:
        env = dict(
            os.environ,
            CODEX_SKILL_TELEMETRY_HOME=str(root or self.root),
        )
        return subprocess.run(
            [sys.executable, "-X", "utf8", "-B", str(HOOK)],
            input=json.dumps(event, ensure_ascii=False),
            text=True,
            encoding="utf-8",
            capture_output=True,
            env=env,
            timeout=2,
        )

    @staticmethod
    def signed_at(
        store: TelemetryStore, record: dict, observed_at: str
    ) -> dict:
        record["observed_at"] = observed_at
        secret = store._existing_secret()
        assert secret is not None
        record["auth_tag"] = store._spool_auth_tag(record, secret)
        return record

    def test_fresh_hook_without_identity_key_drops_event_and_contains_no_body(self) -> None:
        canary = "RAW-PROMPT-CANARY-9c3a session-private turn-private"
        started = time.monotonic()
        result = self.run_hook(
            self.event(
                hook_event_name="UserPromptSubmit",
                prompt=f"違う。{canary}",
                tool_input={"command": canary},
                tool_response={"stderr": canary},
            )
        )
        elapsed = time.monotonic() - started
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertLess(elapsed, 2.0)
        self.assertFalse((self.root / "telemetry.sqlite3").exists())
        self.assertFalse((self.root / "secret.key").exists())
        queued = list((self.root / "spool").glob("*.json"))
        self.assertEqual([], queued)

        store = TelemetryStore(self.root)
        status = store.status()
        self.assertEqual(0, status["counts"]["runs"])
        self.assertEqual(0, status["spool"]["pending"])
        self.assertEqual(
            [],
            store.rows("SELECT status,detail_class FROM collector_health"),
        )

    def test_bootstrapped_hook_never_opens_db_and_reconcile_is_idempotent(self) -> None:
        bootstrap = TelemetryStore(self.root, drain=False)
        canary = "RAW-TOOL-CANARY-4bf0"
        event = self.event(
            tool_input={
                "command": f'python test.py "{self.skill / "SKILL.md"}" {canary}'
            },
            tool_response={"exit_code": 0, "stdout": canary},
        )
        before = bootstrap.status()["counts"]["runs"]
        db_mtime = bootstrap.db_path.stat().st_mtime_ns
        result = self.run_hook(event)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(before, bootstrap.status()["counts"]["runs"])
        self.assertEqual(db_mtime, bootstrap.db_path.stat().st_mtime_ns)
        queued = list((self.root / "spool").glob("*.json"))
        self.assertEqual(1, len(queued))
        spool_body = queued[0].read_text(encoding="utf-8")
        self.assertNotIn(canary, spool_body)

        duplicate = queued[0].with_name("duplicate.json")
        duplicate.write_bytes(queued[0].read_bytes())
        drained = bootstrap.drain_spool()
        self.assertEqual(1, drained["processed"])
        self.assertEqual(1, drained["duplicate"])
        self.assertEqual(1, bootstrap.status()["counts"]["runs"])
        self.assertEqual(0, bootstrap.status()["spool"]["pending"])
        self.assertNotIn(
            canary,
            bootstrap.db_path.read_bytes().decode("latin-1"),
        )

    def test_stop_then_late_post_is_returned_across_drain_batches(self) -> None:
        store = TelemetryStore(self.root, drain=False)
        post = store.sanitize_hook_event(
            self.event(tool_response={"exit_code": 1})
        )
        stop = store.sanitize_hook_event(
            self.event(hook_event_name="Stop")
        )
        self.assertIsNotNone(post)
        self.assertIsNotNone(stop)
        self.signed_at(store, post, "2026-07-30T00:00:00+00:00")
        self.signed_at(store, stop, "2026-07-30T00:00:01+00:00")
        store.spool_path.mkdir(parents=True, exist_ok=True)
        (store.spool_path / "first-stop.json").write_text(
            json.dumps(stop), encoding="utf-8"
        )
        first = store.drain_spool()
        self.assertEqual(1, first["processed"])
        self.assertEqual(0, store.status()["counts"]["runs"])

        (store.spool_path / "late-post.json").write_text(
            json.dumps(post), encoding="utf-8"
        )
        second = store.drain_spool()
        self.assertEqual(1, second["processed"])
        status = store.status()
        self.assertEqual(1, status["counts"]["runs"])
        self.assertEqual(0, status["counts"]["running"])
        self.assertEqual(1, status["counts"]["returned"])
        run = store.rows(
            """SELECT status,ended_at,duration_ms,tool_failure_count,
                      end_reason,duration_quality FROM skill_runs"""
        )[0]
        self.assertEqual("returned", run["status"])
        self.assertEqual(stop["observed_at"], run["ended_at"])
        self.assertEqual(1000, run["duration_ms"])
        self.assertEqual(1, run["tool_failure_count"])
        self.assertEqual("stop", run["end_reason"])
        self.assertEqual("exact", run["duration_quality"])
        self.assertEqual(
            2,
            store.rows("SELECT COUNT(*) count FROM spool_receipts")[0]["count"],
        )

    def test_stop_filename_before_post_is_order_independent_in_one_batch(self) -> None:
        store = TelemetryStore(self.root, drain=False)
        post = store.sanitize_hook_event(self.event())
        stop = store.sanitize_hook_event(
            self.event(hook_event_name="Stop")
        )
        self.assertIsNotNone(post)
        self.assertIsNotNone(stop)
        self.signed_at(store, post, "2026-07-30T00:00:00+00:00")
        self.signed_at(store, stop, "2026-07-30T00:00:01+00:00")
        store.spool_path.mkdir(parents=True, exist_ok=True)
        (store.spool_path / "000-stop.json").write_text(
            json.dumps(stop), encoding="utf-8"
        )
        (store.spool_path / "999-post.json").write_text(
            json.dumps(post), encoding="utf-8"
        )
        drained = store.drain_spool()
        self.assertEqual(2, drained["processed"])
        status = store.status()
        self.assertEqual(0, status["counts"]["running"])
        self.assertEqual(1, status["counts"]["returned"])

    def test_new_prompt_then_late_old_post_is_interrupted(self) -> None:
        store = TelemetryStore(self.root, drain=False)
        old_post = store.sanitize_hook_event(self.event(turn_id="old-turn"))
        new_prompt = store.sanitize_hook_event(
            self.event(
                hook_event_name="UserPromptSubmit",
                turn_id="new-turn",
                prompt="next task",
            )
        )
        self.assertIsNotNone(old_post)
        self.assertIsNotNone(new_prompt)
        self.signed_at(
            store, old_post, "2026-07-30T00:00:00+00:00"
        )
        self.signed_at(
            store, new_prompt, "2026-07-30T00:00:01+00:00"
        )
        store.spool_path.mkdir(parents=True, exist_ok=True)
        (store.spool_path / "new-prompt.json").write_text(
            json.dumps(new_prompt), encoding="utf-8"
        )
        self.assertEqual(1, store.drain_spool()["processed"])
        (store.spool_path / "late-old-post.json").write_text(
            json.dumps(old_post), encoding="utf-8"
        )
        self.assertEqual(1, store.drain_spool()["processed"])
        status = store.status()
        self.assertEqual(0, status["counts"]["running"])
        self.assertEqual(1, status["counts"]["interrupted"])
        run = store.rows(
            "SELECT status,ended_at,duration_ms FROM skill_runs"
        )[0]
        self.assertEqual("interrupted", run["status"])
        self.assertEqual(new_prompt["observed_at"], run["ended_at"])
        self.assertEqual(1000, run["duration_ms"])

    def test_stop_prompt_post_permutations_converge_by_event_time(self) -> None:
        scenarios = [
            (
                "prompt-first",
                "2026-07-30T00:00:02+00:00",
                "2026-07-30T00:00:01+00:00",
                "interrupted",
                "2026-07-30T00:00:01+00:00",
                1000,
            ),
            (
                "stop-first",
                "2026-07-30T00:00:01+00:00",
                "2026-07-30T00:00:02+00:00",
                "returned",
                "2026-07-30T00:00:01+00:00",
                1000,
            ),
        ]
        for (
            scenario,
            stopped_at,
            prompt_at,
            expected_status,
            expected_end,
            expected_duration,
        ) in scenarios:
            for order in permutations(("post", "stop", "prompt")):
                with self.subTest(scenario=scenario, order=order):
                    root = self.base / (
                        scenario + "-" + "-".join(order)
                    )
                    store = TelemetryStore(root, drain=False)
                    records = {
                        "post": store.sanitize_hook_event(
                            self.event(turn_id="old-turn")
                        ),
                        "stop": store.sanitize_hook_event(
                            self.event(
                                hook_event_name="Stop",
                                turn_id="old-turn",
                            )
                        ),
                        "prompt": store.sanitize_hook_event(
                            self.event(
                                hook_event_name="UserPromptSubmit",
                                turn_id="new-turn",
                                prompt="next task",
                            )
                        ),
                    }
                    self.assertTrue(all(records.values()))
                    self.signed_at(
                        store,
                        records["post"],
                        "2026-07-30T00:00:00+00:00",
                    )
                    self.signed_at(store, records["stop"], stopped_at)
                    self.signed_at(store, records["prompt"], prompt_at)
                    store.spool_path.mkdir(parents=True, exist_ok=True)
                    for index, name in enumerate(order):
                        (store.spool_path / f"{index}-{name}.json").write_text(
                            json.dumps(records[name]), encoding="utf-8"
                        )
                        self.assertEqual(
                            1, store.drain_spool()["processed"]
                        )
                    row = store.rows(
                        """SELECT status,ended_at,duration_ms,provenance_trust,
                                  end_reason,duration_quality
                           FROM skill_runs"""
                    )[0]
                    self.assertEqual(expected_status, row["status"])
                    self.assertEqual(expected_end, row["ended_at"])
                    self.assertEqual(expected_duration, row["duration_ms"])
                    self.assertEqual("trusted", row["provenance_trust"])
                    self.assertEqual(
                        "stop" if expected_status == "returned" else "superseded",
                        row["end_reason"],
                    )
                    self.assertEqual("exact", row["duration_quality"])

    def test_late_older_prompt_does_not_interrupt_future_run(self) -> None:
        for order in (("post", "prompt"), ("prompt", "post")):
            with self.subTest(order=order):
                root = self.base / ("future-" + "-".join(order))
                store = TelemetryStore(root, drain=False)
                records = {
                    "post": store.sanitize_hook_event(
                        self.event(turn_id="future-turn")
                    ),
                    "prompt": store.sanitize_hook_event(
                        self.event(
                            hook_event_name="UserPromptSubmit",
                            turn_id="older-prompt-turn",
                            prompt="next task",
                        )
                    ),
                }
                self.assertTrue(all(records.values()))
                self.signed_at(
                    store,
                    records["post"],
                    "2026-07-30T00:00:02+00:00",
                )
                self.signed_at(
                    store,
                    records["prompt"],
                    "2026-07-30T00:00:01+00:00",
                )
                store.spool_path.mkdir(parents=True, exist_ok=True)
                for index, name in enumerate(order):
                    (store.spool_path / f"{index}-{name}.json").write_text(
                        json.dumps(records[name]), encoding="utf-8"
                    )
                    self.assertEqual(1, store.drain_spool()["processed"])
                row = store.rows(
                    "SELECT status,ended_at FROM skill_runs"
                )[0]
                self.assertEqual("running", row["status"])
                self.assertIsNone(row["ended_at"])

    def test_locked_database_defers_quickly_then_drains(self) -> None:
        store = TelemetryStore(self.root, drain=False)
        lock = sqlite3.connect(store.db_path, timeout=0.1)
        try:
            lock.execute("BEGIN IMMEDIATE")
            started = time.monotonic()
            result = self.run_hook(self.event())
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertLess(time.monotonic() - started, 2.0)
            started = time.monotonic()
            deferred = store.drain_spool(max_seconds=0.6)
            elapsed = time.monotonic() - started
            self.assertEqual(1, deferred["deferred"])
            self.assertLess(elapsed, 1.5)
            self.assertEqual(1, store.spool_status()["pending"])
        finally:
            lock.rollback()
            lock.close()
        applied = store.drain_spool()
        self.assertEqual(1, applied["processed"])
        self.assertEqual(1, store.status()["counts"]["runs"])

    def test_schema_initialization_and_writes_are_multiprocess_safe(self) -> None:
        context = multiprocessing.get_context("spawn")
        start = context.Event()
        processes = [
            context.Process(
                target=_initialize_and_write,
                args=(str(self.root), index, start),
            )
            for index in range(6)
        ]
        for process in processes:
            process.start()
        start.set()
        for process in processes:
            process.join(15)
            self.assertEqual(0, process.exitcode)
        store = TelemetryStore(self.root, drain=False)
        status = store.status()
        self.assertEqual("ok", status["integrity"])
        self.assertEqual(6, status["counts"]["runs"])
        self.assertEqual(6, status["counts"]["returned"])
        self.assertEqual(
            1,
            store.rows(
                "SELECT COUNT(DISTINCT session_hash) count FROM skill_runs"
            )[0]["count"],
        )
        self.assertEqual(
                str(telemetry_store.SCHEMA_VERSION),
            store.rows(
                "SELECT value FROM meta WHERE key='schema_version'"
            )[0]["value"],
        )
        self.assertEqual(
            1,
            store.rows(
                "SELECT COUNT(*) count FROM sqlite_master "
                "WHERE type='table' AND name='spool_receipts'"
            )[0]["count"],
        )

    def test_status_cli_is_read_only_for_an_existing_database(self) -> None:
        store = TelemetryStore(self.root, drain=False)
        store.start_manual("read-only-check")
        before = store.db_path.stat().st_mtime_ns
        TelemetryStore(self.root, drain=False)
        self.assertEqual(before, store.db_path.stat().st_mtime_ns)
        env = dict(
            os.environ,
            CODEX_SKILL_TELEMETRY_HOME=str(self.root),
        )
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", str(CLI), "status"],
            text=True,
            encoding="utf-8",
            capture_output=True,
            env=env,
            timeout=5,
        )
        after = store.db_path.stat().st_mtime_ns
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(json.loads(result.stdout)["initialized"])
        self.assertEqual(before, after)

    def test_readonly_falls_back_to_immutable_only_without_sidecars(self) -> None:
        TelemetryStore(self.root, drain=False).start_manual("immutable-check")
        self.assertFalse(Path(str(self.root / "telemetry.sqlite3") + "-wal").exists())
        self.assertFalse(Path(str(self.root / "telemetry.sqlite3") + "-shm").exists())
        original_connect = sqlite3.connect
        attempts: list[str] = []

        def fail_standard_readonly(target, *args, **kwargs):
            rendered = str(target)
            attempts.append(rendered)
            if "mode=ro" in rendered and "immutable=1" not in rendered:
                raise sqlite3.OperationalError("unable to open database file")
            return original_connect(target, *args, **kwargs)

        with mock.patch.object(
            telemetry_store.sqlite3,
            "connect",
            side_effect=fail_standard_readonly,
        ):
            report = TelemetryStore(self.root, initialize=False).status()
        self.assertEqual("ok", report["integrity"])
        self.assertEqual("immutable", report["read_mode"])
        self.assertTrue(any("mode=ro&immutable=1" in item for item in attempts))

    def test_post_connect_schema_read_failure_falls_back_to_immutable(self) -> None:
        TelemetryStore(self.root, drain=False).start_manual(
            "post-connect-immutable-check"
        )
        original_probe = TelemetryStore._probe_read_connection
        attempts: list[bool] = []

        def fail_standard_probe(db, *, immutable):
            attempts.append(immutable)
            if not immutable:
                raise sqlite3.OperationalError("unable to open database file")
            return original_probe(db, immutable=immutable)

        with mock.patch.object(
            TelemetryStore,
            "_probe_read_connection",
            new=staticmethod(fail_standard_probe),
        ):
            report = TelemetryStore(self.root, initialize=False).status()
        self.assertEqual("ok", report["integrity"])
        self.assertEqual("immutable", report["read_mode"])
        self.assertEqual([False, True], attempts)

    def test_doctor_returns_unavailable_json_and_refuses_immutable_with_wal(self) -> None:
        store = TelemetryStore(self.root, drain=False)
        sidecar = Path(str(store.db_path) + "-wal")
        sidecar.write_bytes(b"simulated-active-wal")
        original_probe = TelemetryStore._probe_read_connection
        attempts: list[bool] = []

        def fail_standard_probe(db, *, immutable):
            attempts.append(immutable)
            if not immutable:
                raise sqlite3.OperationalError("unable to open database file")
            return original_probe(db, immutable=immutable)

        output = io.StringIO()
        with (
            mock.patch.object(
                TelemetryStore,
                "_probe_read_connection",
                new=staticmethod(fail_standard_probe),
            ),
            mock.patch.dict(
                os.environ,
                {"CODEX_SKILL_TELEMETRY_HOME": str(self.root)},
            ),
            mock.patch.object(sys, "argv", [str(CLI), "doctor"]),
            redirect_stdout(output),
        ):
            self.assertEqual(0, telemetry_cli.main())
        report = json.loads(output.getvalue())
        self.assertEqual("unavailable", report["integrity"])
        self.assertEqual("schema-or-io", report["read_error_class"])
        self.assertIsNone(report["journal_mode"])
        self.assertEqual([False, False], attempts)

    def test_doctor_does_not_bootstrap_an_uninitialized_root(self) -> None:
        untouched = self.base / "doctor-read-only"
        env = dict(
            os.environ,
            CODEX_SKILL_TELEMETRY_HOME=str(untouched),
        )
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", str(CLI), "doctor"],
            text=True,
            encoding="utf-8",
            capture_output=True,
            env=env,
            timeout=5,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        report = json.loads(result.stdout)
        self.assertFalse(report["initialized"])
        self.assertIsNone(report["journal_mode"])
        self.assertFalse(untouched.exists())

    def test_hook_fails_open_when_spool_root_is_unwritable_shape(self) -> None:
        invalid_root = self.base / "not-a-directory"
        invalid_root.write_text("occupied", encoding="utf-8")
        started = time.monotonic()
        result = self.run_hook(self.event(), invalid_root)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertLess(time.monotonic() - started, 2.0)

    def test_untrusted_spool_body_fields_are_quarantined_before_persistence(self) -> None:
        store = TelemetryStore(self.root, drain=False)
        valid = store.sanitize_hook_event(self.event())
        self.assertIsNotNone(valid)
        mutations = [
            lambda item: item.__setitem__("hook", "Stop"),
            lambda item: item.__setitem__("stable_correlation", False),
            lambda item: item.__setitem__(
                "observed_at", "2026-07-30T01:02:03+00:00"
            ),
            lambda item: item.__setitem__("session_hash", "0" * 64),
            lambda item: item.__setitem__("turn_hash", "1" * 64),
            lambda item: item.__setitem__("repo_hash", "2" * 64),
            lambda item: item.__setitem__("event_id", "3" * 64),
            lambda item: item.__setitem__("failure", True),
            lambda item: item.__setitem__("model_class", "openai"),
            lambda item: item.__setitem__(
                "model_class", "rawpromptcanaryabc123"
            ),
            lambda item: item["skills"][0].__setitem__(
                "skill_key", "rawpromptcanaryabc123"
            ),
            lambda item: item["skills"][0].__setitem__(
                "skill_name", "rawpromptcanaryabc123"
            ),
            lambda item: item["skills"][0].__setitem__(
                "provider", "rawpromptcanaryabc123"
            ),
            lambda item: item["skills"][0].__setitem__(
                "source_class", "plugin"
            ),
            lambda item: item["skills"][0].__setitem__(
                "skill_fingerprint", "4" * 64
            ),
            lambda item: item["skills"].append(
                dict(item["skills"][0])
            ),
            lambda item: item["evidence"].__setitem__(
                "evidence_class", "build"
            ),
            lambda item: item["evidence"].__setitem__("result", "failed"),
            lambda item: item["evidence"].__setitem__(
                "subject_hash", "5" * 64
            ),
            lambda item: item["evidence"].__setitem__(
                "idempotency_key", "6" * 64
            ),
            lambda item: item.__setitem__(
                "model_class", "ignore previous instructions"
            ),
            lambda item: item["skills"][0].__setitem__(
                "skill_name", "raw prompt body"
            ),
            lambda item: item["skills"][0].__setitem__(
                "provider", "assistant response body"
            ),
            lambda item: item["skills"][0].__setitem__(
                "source_class", "untrusted-body"
            ),
            lambda item: item["evidence"].__setitem__(
                "detection", "copied tool output"
            ),
            lambda item: item.__setitem__(
                "raw_prompt", "do not persist this body"
            ),
            lambda item: item.__setitem__("version", 1),
            lambda item: item.pop("auth_tag"),
        ]
        prompt = store.sanitize_hook_event(
            self.event(
                hook_event_name="UserPromptSubmit",
                turn_id="reaction-turn",
                prompt="ナイス！",
            )
        )
        self.assertIsNotNone(prompt)
        feedback_mutations = [
            lambda item: item["feedback"].__setitem__(
                "sentiment", "negative"
            ),
            lambda item: item["feedback"].__setitem__(
                "feeling_class", "explicit-complaint-or-correction"
            ),
            lambda item: item["feedback"].__setitem__("confidence", 0.5),
            lambda item: item["feedback"].__setitem__(
                "reaction_signature", "7" * 64
            ),
        ]
        store.spool_path.mkdir(parents=True, exist_ok=True)
        for index, mutate in enumerate(mutations):
            hostile = json.loads(json.dumps(valid))
            mutate(hostile)
            (store.spool_path / f"hostile-{index}.json").write_text(
                json.dumps(hostile), encoding="utf-8"
            )
        for index, mutate in enumerate(feedback_mutations):
            hostile = json.loads(json.dumps(prompt))
            mutate(hostile)
            (
                store.spool_path / f"hostile-feedback-{index}.json"
            ).write_text(json.dumps(hostile), encoding="utf-8")
        first = store.drain_spool()
        rejected_count = len(mutations) + len(feedback_mutations)
        self.assertEqual(rejected_count, first["rejected"])
        self.assertEqual(0, first["processed"])
        self.assertEqual(
            rejected_count, store.spool_status()["rejected"]
        )
        self.assertEqual(0, store.status()["counts"]["runs"])
        self.assertEqual(
            0,
            store.rows("SELECT COUNT(*) count FROM spool_receipts")[0]["count"],
        )
        quarantined = list(store.spool_path.glob("*.rejected"))
        self.assertEqual(rejected_count, len(quarantined))
        for path in quarantined:
            body = path.read_text(encoding="ascii")
            self.assertNotIn("ignore previous instructions", body)
            self.assertNotIn("raw prompt body", body)
            self.assertNotIn("assistant response body", body)
            self.assertNotIn("copied tool output", body)
            self.assertNotIn("do not persist this body", body)
            self.assertNotIn("rawpromptcanaryabc123", body)
            metadata = json.loads(body)
            self.assertEqual(
                {
                    "version",
                    "rejected_at",
                    "reason_class",
                    "size_bytes",
                    "content_sha256",
                    "source_name_sha256",
                },
                set(metadata),
            )
            self.assertEqual("invalid-envelope", metadata["reason_class"])
        second = store.drain_spool()
        self.assertEqual(
            {"processed": 0, "duplicate": 0, "deferred": 0, "rejected": 0},
            second,
        )

    def test_malformed_and_oversize_spool_are_quarantined_once(self) -> None:
        store = TelemetryStore(self.root, drain=False)
        store.spool_path.mkdir(parents=True, exist_ok=True)
        (store.spool_path / "malformed.json").write_text(
            '{"version":', encoding="utf-8"
        )
        (store.spool_path / "oversize.json").write_bytes(
            b"x" * (telemetry_store.SPOOL_RECORD_LIMIT + 1)
        )
        first = store.drain_spool()
        self.assertEqual(2, first["rejected"])
        self.assertEqual(
            {"pending": 0, "rejected": 2},
            store.spool_status(),
        )
        quarantined = [
            json.loads(path.read_text(encoding="ascii"))
            for path in store.spool_path.glob("*.rejected")
        ]
        self.assertEqual(
            {"malformed-json", "oversized-record"},
            {item["reason_class"] for item in quarantined},
        )
        self.assertTrue(
            all(item["size_bytes"] > 0 for item in quarantined)
        )
        self.assertTrue(
            all(
                path.stat().st_size < 1024
                for path in store.spool_path.glob("*.rejected")
            )
        )
        second = store.drain_spool()
        self.assertEqual(
            {"processed": 0, "duplicate": 0, "deferred": 0, "rejected": 0},
            second,
        )


if __name__ == "__main__":
    unittest.main()
