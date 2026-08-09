from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from capture_hook import sanitize_text

from failure_store import (
    DatabaseUnavailable,
    InvalidSpoolEnvelope,
    PrivacyMaintenancePending,
    SCHEMA_VERSION,
    _exclusive_privacy_maintenance_lock,
    add_learning_case,
    advice_cache_path,
    connect,
    connect_readonly,
    data_dir,
    db_path,
    disabled_path,
    ensure_private_dir,
    event_collector_version,
    exclusive_identity_key_lock,
    is_expected_control_flow,
    process_spool_envelope,
    privacy_readiness,
    provision_identity_key,
    repair_privacy_readiness,
    refresh_advice_cache,
    rebuild_patterns,
    require_privacy_ready,
    rows_to_dicts,
    set_event_review,
    utc_now,
)

SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
OPAQUE_REF_RE = re.compile(r"[a-z][a-z0-9-]{0,31}:[A-Za-z0-9._@-]{1,160}")
SCOPE_RE = re.compile(r"(?:global|(?:repo|skill|capability):[A-Za-z0-9._@-]{1,128})")
FINGERPRINT_RE = re.compile(
    r"(?:[a-z0-9][a-z0-9._-]{0,63}@[A-Za-z0-9._+-]{1,64}|sha256:[0-9a-f]{64})"
)
CASE_ID_RE = re.compile(r"case-[a-z0-9][a-z0-9-]{0,95}")
MAX_SPOOL_BYTES = 1_000_000
EXPORT_FORMAT_VERSION = 2
EXPORT_TABLES = (
    "meta",
    "events",
    "recovery_markers",
    "intervention_outcomes",
    "patterns",
    "pattern_events",
    "collector_health",
    "event_reviews",
    "spool_receipts",
    "learning_cases",
)


def emit(value: Any, as_json: bool = False) -> None:
    if as_json or isinstance(value, (dict, list)):
        print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(value)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone())


def _warn_database(state: str) -> None:
    print(f"failure-learning database: {state}", file=sys.stderr)


def cmd_doctor(args: argparse.Namespace) -> int:
    database_state = "ready"
    integrity: str | None = None
    journal: str | None = None
    stored_schema: int | None = None
    privacy: dict[str, Any] = {"ready": False}
    try:
        with connect_readonly(timeout=2.0) as conn:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
            if _table_exists(conn, "meta"):
                row = conn.execute(
                    "SELECT value FROM meta WHERE key='schema_version'"
                ).fetchone()
                stored_schema = int(row[0]) if row else None
                privacy = privacy_readiness(conn)
                if not privacy["ready"]:
                    database_state = "privacy-maintenance-pending"
    except DatabaseUnavailable as exc:
        database_state = str(exc)
    hooks_path = (Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "hooks.json").resolve()
    emit({
        "data_dir": str(data_dir()),
        "database": str(db_path()),
        "database_state": database_state,
        "database_integrity": integrity,
        "journal_mode": journal,
        "schema_version": stored_schema,
        "schema_current": (
            stored_schema == SCHEMA_VERSION and bool(privacy["ready"])
        ),
        "privacy": privacy,
        "retryable": database_state == "privacy-maintenance-pending",
        "collection_enabled": not disabled_path().exists(),
        "hooks_file": str(hooks_path),
        "hooks_file_exists": hooks_path.exists(),
        "advice_cache_exists": advice_cache_path().is_file(),
    })
    return 3 if database_state == "privacy-maintenance-pending" else 0


def cmd_init(args: argparse.Namespace) -> int:
    identity = provision_identity_key()
    with connect() as conn:
        privacy = repair_privacy_readiness(conn)
        schema = conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()[0]
        events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    patterns = rebuild_patterns()
    emit({
        "initialized": True,
        "schema_version": int(schema),
        "privacy": privacy,
        "identity_key_exists": identity.is_file(),
        "events": events,
        "patterns": patterns,
        "advice_cache_exists": advice_cache_path().is_file(),
        "spool_drained": False,
    })
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    spool = data_dir() / "spool"
    counts = {
        "events": 0, "outcomes": 0, "patterns": 0, "cases": 0,
        "spooled": len(list(spool.glob("*.json"))) if spool.exists() else 0,
    }
    health: list[dict[str, Any]] = []
    database_state = "ready"
    privacy: dict[str, Any] = {"ready": False}
    try:
        with connect_readonly() as conn:
            if _table_exists(conn, "meta"):
                privacy = privacy_readiness(conn)
                if not privacy["ready"]:
                    database_state = "privacy-maintenance-pending"
            for key, table in (
                ("events", "events"),
                ("outcomes", "intervention_outcomes"),
                ("patterns", "patterns"),
                ("cases", "learning_cases"),
            ):
                if _table_exists(conn, table):
                    counts[key] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if _table_exists(conn, "collector_health"):
                health = rows_to_dicts(conn.execute(
                    "SELECT observed_at, status, detail_class "
                    "FROM collector_health ORDER BY health_id DESC LIMIT 5"
                ).fetchall())
    except DatabaseUnavailable as exc:
        database_state = str(exc)
    emit({
        "enabled": not disabled_path().exists(),
        "database_state": database_state,
        "privacy": privacy,
        "retryable": database_state == "privacy-maintenance-pending",
        "counts": counts,
        "recent_health": health,
        "advice_cache_exists": advice_cache_path().is_file(),
    })
    return 0


def cmd_events(args: argparse.Namespace) -> int:
    columns = (
        "event_id, observed_at, tool_name, operation_class, error_identity, "
        "capture_mode, capture_completeness, signature"
    )
    if args.include_message:
        columns += ", message_template"
    try:
        with connect_readonly() as conn:
            if args.include_message:
                require_privacy_ready(conn)
            rows = conn.execute(
                f"SELECT {columns} FROM events ORDER BY observed_at DESC LIMIT ?",
                (args.limit,),
            ).fetchall()
    except DatabaseUnavailable as exc:
        _warn_database(str(exc))
        emit([])
        return 3 if str(exc) == "privacy-maintenance-pending" else 0
    emit(rows_to_dicts(rows))
    return 0


def cmd_rebuild(args: argparse.Namespace) -> int:
    with _exclusive_drain_lock() as acquired:
        if not acquired:
            drain = _busy_drain_result()
            count = 0
        else:
            drain = _drain_spool(lock_held=True)
            count = rebuild_patterns()
    emit({"patterns_rebuilt": count, "drain": drain})
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    with _exclusive_drain_lock() as acquired:
        if not acquired:
            drain = _busy_drain_result()
            print(f"drain: {json.dumps(drain, sort_keys=True)}", file=sys.stderr)
            emit([])
            return 0
        else:
            drain = _drain_spool(lock_held=True)
            rebuild_patterns()
    with connect_readonly() as conn:
        rows = conn.execute(
            """
            SELECT p.pattern_id, p.repo_hash, p.tool_name, p.operation_class, p.error_identity,
                   p.incident_count, p.independent_sessions, p.first_seen, p.last_seen,
                   p.status, p.quality_status,
                   COUNT(o.outcome_id) outcome_count,
                   SUM(CASE WHEN o.status='success' THEN 1 ELSE 0 END) observed_successes,
                   SUM(CASE WHEN o.status='failure' THEN 1 ELSE 0 END) observed_failures
            FROM patterns p
            LEFT JOIN pattern_events pe ON pe.pattern_id=p.pattern_id
            LEFT JOIN intervention_outcomes o ON o.event_id=pe.event_id
            GROUP BY p.pattern_id
            ORDER BY p.incident_count DESC, p.last_seen DESC LIMIT ?
            """,
            (args.limit,),
        ).fetchall()
    if any(drain.values()):
        print(f"drain: {json.dumps(drain, sort_keys=True)}", file=sys.stderr)
    emit(rows_to_dicts(rows))
    return 0


def cmd_add_outcome(args: argparse.Namespace) -> int:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", args.action_class):
        print("--action-class must use lowercase letters, digits, and hyphens only.", file=sys.stderr)
        return 2
    notes, _ = sanitize_text(args.notes)
    with connect() as conn:
        exists = conn.execute("SELECT 1 FROM events WHERE event_id=?", (args.event_id,)).fetchone()
        if not exists:
            print(f"Unknown event_id: {args.event_id}", file=sys.stderr)
            return 2
        conn.execute(
            """
            INSERT INTO intervention_outcomes(
              outcome_id, event_id, observed_at, action_class, status,
              verification, risk_class, reversible, side_effects_checked,
              causal_strength, notes
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (str(uuid.uuid4()), args.event_id, utc_now(), args.action_class, args.status,
             args.verification, args.risk, int(args.reversible), int(args.side_effects_checked),
             args.causal_strength, notes),
        )
        conn.commit()
    try:
        refresh_advice_cache()
    except (DatabaseUnavailable, OSError, sqlite3.Error):
        pass
    emit({"recorded": True, "event_id": args.event_id})
    return 0


def _pending_spool_count() -> int:
    spool = data_dir() / "spool"
    try:
        return sum(1 for _ in spool.glob("*.json")) if spool.is_dir() else 0
    except OSError:
        return 0


def _empty_drain_result(*, busy: int = 0, deferred: int = 0) -> dict[str, int]:
    return {
        "inserted": 0,
        "duplicates": 0,
        "recoveries": 0,
        "unmatched_recoveries": 0,
        "health": 0,
        "rejected": 0,
        "invalid": 0,
        "busy": busy,
        "deferred": deferred,
    }


def _busy_drain_result() -> dict[str, int]:
    return _empty_drain_result(busy=1, deferred=_pending_spool_count())


@contextmanager
def _exclusive_drain_lock():
    """Acquire one non-blocking OS advisory lock for spool drain and rebuild."""
    root = data_dir()
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".drain.lock"
    handle = lock_path.open("a+b")
    acquired = False
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                acquired = True
            except OSError:
                acquired = False
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError:
                acquired = False
        yield acquired
    finally:
        if acquired:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()


def _rejection_reason(value: str) -> str:
    reason = re.sub(r"[^a-z0-9_-]+", "-", value.lower()).strip("-")[:96]
    return reason or "invalid-spool-envelope"


def _reject_spool_file(
    path: Path,
    spool: Path,
    *,
    raw: bytes,
    size_bytes: int,
    reason: str,
) -> bool:
    """Replace untrusted source bytes with a bounded opaque rejection receipt."""
    rejected_dir = spool / ".rejected"
    rejected_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(
        raw + b"\0" + str(size_bytes).encode("ascii", "strict")
    ).hexdigest()
    receipt = {
        "schema_version": 1,
        "rejected_at": utc_now(),
        "reason": _rejection_reason(reason),
        "size_bytes": max(0, int(size_bytes)),
        "opaque_fingerprint": f"sha256:{digest}",
    }
    final = rejected_dir / f"{digest}.json"
    if not final.exists():
        temp = rejected_dir / f".{digest}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
        temp.write_text(
            json.dumps(receipt, ensure_ascii=True, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temp, final)
    path.unlink()
    return True


def _read_spool_file(path: Path) -> tuple[bytes, int]:
    size = path.stat().st_size
    with path.open("rb") as handle:
        raw = handle.read(MAX_SPOOL_BYTES + 1)
    if size > MAX_SPOOL_BYTES or len(raw) > MAX_SPOOL_BYTES:
        raise InvalidSpoolEnvelope("spool-envelope-too-large")
    return raw, size


def _spool_order_key(candidate: dict[str, Any]) -> tuple[datetime, int, str, str]:
    envelope = candidate.get("envelope")
    if isinstance(envelope, dict):
        try:
            observed = datetime.fromisoformat(
                str(envelope.get("observed_at") or "").replace("Z", "+00:00")
            )
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=timezone.utc)
            observed = observed.astimezone(timezone.utc)
        except ValueError:
            observed = datetime.max.replace(tzinfo=timezone.utc)
        event_type = str(envelope.get("event_type") or "failure")
        type_order = {"failure": 0, "recovery": 1, "health": 2}.get(event_type, 3)
        event_id = str(envelope.get("event_id") or "")
    else:
        observed = datetime.max.replace(tzinfo=timezone.utc)
        type_order = 4
        event_id = ""
    return observed, type_order, event_id, str(candidate["path"].name)


def _drain_spool(*, lock_held: bool = False) -> dict[str, int]:
    if not lock_held:
        with _exclusive_drain_lock() as acquired:
            if not acquired:
                return _busy_drain_result()
            return _drain_spool(lock_held=True)

    spool = data_dir() / "spool"
    result = _empty_drain_result()
    if spool.exists():
        candidates: list[dict[str, Any]] = []
        for path in spool.glob("*.json"):
            raw = b""
            size_bytes = 0
            envelope: Any = None
            load_error: Exception | None = None
            try:
                try:
                    raw, size_bytes = _read_spool_file(path)
                    envelope = json.loads(raw.decode("utf-8"))
                except UnicodeError as exc:
                    raise InvalidSpoolEnvelope("spool-envelope-invalid-utf8") from exc
                except json.JSONDecodeError as exc:
                    raise InvalidSpoolEnvelope("spool-envelope-invalid-json") from exc
                if not isinstance(envelope, dict):
                    raise InvalidSpoolEnvelope("spool-envelope-not-object")
            except (InvalidSpoolEnvelope, OSError) as exc:
                load_error = exc
            candidates.append({
                "path": path,
                "raw": raw,
                "size_bytes": size_bytes,
                "envelope": envelope,
                "load_error": load_error,
            })

        for candidate in sorted(candidates, key=_spool_order_key):
            path = candidate["path"]
            raw = candidate["raw"]
            size_bytes = candidate["size_bytes"]
            envelope = candidate["envelope"]
            try:
                if candidate["load_error"] is not None:
                    raise candidate["load_error"]
                disposition = process_spool_envelope(envelope)
                if disposition == "inserted":
                    result["inserted"] += 1
                elif disposition == "recovery-recorded":
                    result["recoveries"] += 1
                elif disposition == "recovery-unmatched":
                    result["unmatched_recoveries"] += 1
                elif disposition == "health-recorded":
                    result["health"] += 1
                else:
                    result["duplicates"] += 1
                path.unlink()
            except InvalidSpoolEnvelope as exc:
                try:
                    if not raw:
                        with path.open("rb") as handle:
                            raw = handle.read(MAX_SPOOL_BYTES + 1)
                    if not size_bytes:
                        size_bytes = path.stat().st_size
                    _reject_spool_file(
                        path,
                        spool,
                        raw=raw,
                        size_bytes=size_bytes,
                        reason=str(exc),
                    )
                    result["rejected"] += 1
                except OSError:
                    result["deferred"] += 1
            except (OSError, sqlite3.Error):
                result["deferred"] += 1
            except (ValueError, KeyError, TypeError):
                result["invalid"] += 1
    return result


def cmd_drain(args: argparse.Namespace) -> int:
    with _exclusive_drain_lock() as acquired:
        if not acquired:
            result = _busy_drain_result()
            result["patterns_rebuilt"] = 0
        else:
            result = _drain_spool(lock_held=True)
            result["patterns_rebuilt"] = rebuild_patterns()
    emit(result)
    return 0


def _write_disabled_marker(reason: str) -> None:
    root = data_dir()
    ensure_private_dir(root)
    final = disabled_path()
    temp = root / f".disabled.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
    descriptor = os.open(
        temp,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(f"{reason}\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, final)
        try:
            final.chmod(0o600)
        except OSError:
            pass
    finally:
        temp.unlink(missing_ok=True)


def cmd_disable(args: argparse.Namespace) -> int:
    _write_disabled_marker("disabled by user")
    emit({"enabled": False})
    return 0


def cmd_enable(args: argparse.Namespace) -> int:
    disabled_path().unlink(missing_ok=True)
    emit({"enabled": True})
    return 0


def _full_export_snapshot() -> dict[str, Any]:
    with connect_readonly(timeout=2.0) as conn:
        conn.execute("BEGIN")
        try:
            integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
            if integrity.lower() != "ok":
                raise DatabaseUnavailable("database-integrity-check-failed")
            if not _table_exists(conn, "meta"):
                raise DatabaseUnavailable("export-table-missing-meta")
            require_privacy_ready(conn)

            tables: dict[str, list[dict[str, Any]]] = {}
            for table in EXPORT_TABLES:
                if not _table_exists(conn, table):
                    raise DatabaseUnavailable(f"export-table-missing-{table}")
                tables[table] = rows_to_dicts(
                    conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
                )
            return {
                "export_format_version": EXPORT_FORMAT_VERSION,
                "exported_at": utc_now(),
                "schema_version": SCHEMA_VERSION,
                "tables": tables,
            }
        finally:
            conn.rollback()


def _atomic_write_private_json(output: Path, payload: dict[str, Any]) -> None:
    parent = output.parent
    if not parent.is_dir():
        raise OSError("export-parent-directory-missing")
    temp = parent / f".{output.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
    descriptor = os.open(
        temp,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(
                payload,
                handle,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, output)
        try:
            output.chmod(0o600)
        except OSError:
            pass
    finally:
        temp.unlink(missing_ok=True)


def cmd_export(args: argparse.Namespace) -> int:
    output = Path(args.output).expanduser().resolve()
    try:
        snapshot = _full_export_snapshot()
        _atomic_write_private_json(output, snapshot)
    except (DatabaseUnavailable, OSError, sqlite3.Error, TypeError, ValueError) as exc:
        reason = (
            str(exc)
            if isinstance(exc, DatabaseUnavailable)
            else "export-failed"
        )
        emit({
            "exported": False,
            "output": str(output),
            "reason": reason,
            "retryable": reason == "privacy-maintenance-pending",
        })
        return 3
    emit({
        "exported": True,
        "output": str(output),
        "schema_version": snapshot["schema_version"],
        "table_counts": {
            name: len(rows) for name, rows in snapshot["tables"].items()
        },
    })
    return 0


def _successful_skill_content(message: str) -> bool:
    return bool(re.search(
        r"(?is)(?:^|\n)---\s*\nname:\s*[a-z0-9-]+\s*\ndescription:.*?\n---",
        message[:512],
    ))


def _auth_migration_counts(conn: sqlite3.Connection) -> dict[str, int]:
    event_columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(events)").fetchall()
    }
    auth_predicate = "COALESCE(e.auth_verified,0)=0" if "auth_verified" in event_columns else "1=1"
    reviews = _table_exists(conn, "event_reviews")
    join = "LEFT JOIN event_reviews er ON er.event_id=e.event_id" if reviews else ""
    accepted = "AND er.review_status='accepted'" if reviews else "AND 1=0"
    auto_quarantined = (
        "AND er.review_status='quarantined' "
        "AND er.reason_class='unsigned-legacy-envelope'"
        if reviews else "AND 1=0"
    )
    total = conn.execute(
        f"SELECT COUNT(*) FROM events e WHERE {auth_predicate}"
    ).fetchone()[0]
    accepted_count = conn.execute(
        f"SELECT COUNT(*) FROM events e {join} "
        f"WHERE {auth_predicate} {accepted}"
    ).fetchone()[0]
    quarantined_count = conn.execute(
        f"SELECT COUNT(*) FROM events e {join} "
        f"WHERE {auth_predicate} {auto_quarantined}"
    ).fetchone()[0]
    return {
        "unsigned_legacy_events": int(total),
        "accepted_overrides": int(accepted_count),
        "auto_quarantined": int(quarantined_count),
    }


def _repair_candidates(conn: sqlite3.Connection) -> tuple[list[dict[str, str]], dict[str, int]]:
    reviews = _table_exists(conn, "event_reviews")
    event_columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(events)").fetchall()
    }
    auth_column = "e.auth_verified" if "auth_verified" in event_columns else "0 AS auth_verified"
    review_join = (
        "LEFT JOIN event_reviews er ON er.event_id=e.event_id"
        if reviews else ""
    )
    review_column = "er.review_status" if reviews else "NULL AS review_status"
    rows = conn.execute(
        f"""
        SELECT e.event_id, e.tool_name, e.error_identity, e.message_template,
               e.event_json, {auth_column}, {review_column}
        FROM events e
        {review_join}
        """
    ).fetchall()
    candidates: list[dict[str, str]] = []
    counts = {
        "unsigned-legacy-envelope": 0,
        "legacy-untrusted": 0,
        "expected-control-flow": 0,
        "successful-skill-content": 0,
        "already-reviewed": 0,
    }
    for row in rows:
        event = dict(row)
        if event.get("review_status"):
            counts["already-reviewed"] += 1
            continue
        if event.get("auth_verified") not in {1, True}:
            reason, status = "unsigned-legacy-envelope", "quarantined"
        elif _successful_skill_content(str(event.get("message_template") or "")):
            reason, status = "successful-skill-content", "quarantined"
        elif is_expected_control_flow(event):
            reason, status = "expected-control-flow", "non-actionable"
        elif tuple(int(part) for part in (
            event_collector_version(event.get("event_json") or "{}").split(".")[:2]
        ) if part.isdigit()) < (0, 2):
            reason, status = "legacy-untrusted", "quarantined"
        else:
            continue
        counts[reason] += 1
        candidates.append({
            "event_id": str(event["event_id"]),
            "review_status": status,
            "reason_class": reason,
        })
    return candidates, counts


def cmd_repair(args: argparse.Namespace) -> int:
    drain: dict[str, int] | None = None
    if args.apply:
        with _exclusive_drain_lock() as acquired:
            if not acquired:
                emit({
                    "dry_run": False,
                    "deferred": True,
                    "applied": 0,
                    "candidates": {},
                    "patterns_rebuilt": 0,
                    "pattern_link_mismatches": None,
                    "drain": _busy_drain_result(),
                })
                return 0
            drain = _drain_spool(lock_held=True)
            with connect(timeout=2.0) as conn:
                candidates, counts = _repair_candidates(conn)
                auth_migration = _auth_migration_counts(conn)
                conn.executemany(
                    """
                    INSERT OR IGNORE INTO event_reviews(
                      event_id, review_status, reason_class, reviewed_at, review_source
                    ) VALUES(?,?,?,?,?)
                    """,
                    (
                        (
                            candidate["event_id"], candidate["review_status"],
                            candidate["reason_class"], utc_now(), "repair-v1",
                        )
                        for candidate in candidates
                    ),
                )
                conn.commit()
            patterns = rebuild_patterns()
            with connect_readonly() as conn:
                mismatch = conn.execute(
                    """
                    SELECT COUNT(*) FROM patterns p
                    WHERE p.incident_count != (
                      SELECT COUNT(*) FROM pattern_events pe WHERE pe.pattern_id=p.pattern_id
                    )
                    """
                ).fetchone()[0]
        emit({
            "dry_run": False,
            "applied": len(candidates),
            "candidates": counts,
            "auth_migration": auth_migration,
            "patterns_rebuilt": patterns,
            "pattern_link_mismatches": mismatch,
            "drain": drain,
        })
        return 0
    try:
        with connect_readonly(timeout=2.0) as conn:
            candidates, counts = _repair_candidates(conn)
            auth_migration = _auth_migration_counts(conn)
    except DatabaseUnavailable as exc:
        emit({
            "dry_run": True, "database_state": str(exc),
            "would_apply": 0, "candidates": {},
        })
        return 0
    emit({
        "dry_run": True,
        "would_apply": len(candidates),
        "candidates": counts,
        "auth_migration": auth_migration,
    })
    return 0


def cmd_review_event(args: argparse.Namespace) -> int:
    if not SLUG_RE.fullmatch(args.reason_class):
        print("--reason-class must be a lowercase slug.", file=sys.stderr)
        return 2
    if not set_event_review(
        args.event_id, args.status, args.reason_class, "explicit-cli-review"
    ):
        print(f"Unknown event_id: {args.event_id}", file=sys.stderr)
        return 2
    emit({
        "reviewed": True, "event_id": args.event_id,
        "status": args.status, "reason_class": args.reason_class,
    })
    return 0


def cmd_case_add(args: argparse.Namespace) -> int:
    slug_values = {
        "category": args.category,
        "root_cause_class": args.root_cause_class,
        "remediation_class": args.remediation_class,
    }
    for label, value in slug_values.items():
        if not SLUG_RE.fullmatch(value):
            print(f"--{label.replace('_', '-')} must be a lowercase slug.", file=sys.stderr)
            return 2
    if not SCOPE_RE.fullmatch(args.scope):
        print("--scope must be global or an opaque repo/skill/capability reference.", file=sys.stderr)
        return 2
    if not FINGERPRINT_RE.fullmatch(args.target_fingerprint):
        print("--target-fingerprint must be capability@version or sha256:<digest>.", file=sys.stderr)
        return 2
    if not args.evidence_ref or any(
        not OPAQUE_REF_RE.fullmatch(ref) for ref in args.evidence_ref
    ):
        print("--evidence-ref requires one or more opaque type:value references.", file=sys.stderr)
        return 2
    title, _ = sanitize_text(args.title)
    title = title.replace("\n", " ").strip()[:120]
    if not title:
        print("--title must not be empty.", file=sys.stderr)
        return 2
    case_id = args.case_id or f"case-{uuid.uuid4()}"
    if not CASE_ID_RE.fullmatch(case_id):
        print("--case-id must be an opaque lowercase case-* identifier.", file=sys.stderr)
        return 2
    case = {
        "case_id": case_id,
        "created_at": utc_now(),
        "title": title,
        "category": args.category,
        "scope": args.scope,
        "root_cause_class": args.root_cause_class,
        "remediation_class": args.remediation_class,
        "verification_status": args.verification_status,
        "evidence_refs": sorted(set(args.evidence_ref)),
        "target_fingerprint": args.target_fingerprint,
        "status": args.status,
    }
    try:
        add_learning_case(case)
    except sqlite3.IntegrityError:
        print(f"Duplicate case_id: {case_id}", file=sys.stderr)
        return 2
    emit({"recorded": True, "case_id": case_id})
    return 0


def cmd_cases(args: argparse.Namespace) -> int:
    try:
        with connect_readonly() as conn:
            require_privacy_ready(conn)
            if not _table_exists(conn, "learning_cases"):
                _warn_database("schema-upgrade-required")
                emit([])
                return 0
            rows = rows_to_dicts(conn.execute(
                """
                SELECT case_id, created_at, title, category, scope,
                       root_cause_class, remediation_class, verification_status,
                       evidence_refs, target_fingerprint, status
                FROM learning_cases
                ORDER BY created_at DESC LIMIT ?
                """,
                (args.limit,),
            ).fetchall())
    except DatabaseUnavailable as exc:
        _warn_database(str(exc))
        emit([])
        return 3 if str(exc) == "privacy-maintenance-pending" else 0
    for row in rows:
        row["evidence_refs"] = json.loads(row["evidence_refs"])
    emit(rows)
    return 0


PURGE_CONTROL_FILES = {
    "disabled",
    ".drain.lock",
    ".privacy-maintenance.lock",
    ".identity.lock",
}


def _purge_known_data(root: Path) -> list[str]:
    failures: list[str] = []
    targets = [
        db_path(),
        Path(str(db_path()) + "-wal"),
        Path(str(db_path()) + "-shm"),
        Path(str(db_path()) + "-journal"),
        root / "identity.key",
        advice_cache_path(),
    ]
    for target in targets:
        try:
            target.unlink(missing_ok=True)
        except OSError:
            failures.append(target.name)

    for pattern in (
        "identity.*.tmp",
        ".advice-cache.*.tmp",
        ".disabled.*.tmp",
    ):
        for target in root.glob(pattern):
            try:
                target.unlink(missing_ok=True)
            except OSError:
                failures.append(target.name)

    spool = root / "spool"
    if spool.exists():
        try:
            shutil.rmtree(spool)
        except OSError:
            failures.append("spool")
    return sorted(set(failures))


def _purge_residue(root: Path) -> list[str]:
    if not root.exists():
        return []
    residue: list[str] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if relative in PURGE_CONTROL_FILES:
            continue
        residue.append(relative)
    return sorted(residue)


def _purge_deferred(reason: str, root: Path) -> int:
    emit({
        "purged": False,
        "collection_enabled": False,
        "data_dir": str(root),
        "reason": reason,
        "retryable": True,
    })
    return 3


def cmd_purge(args: argparse.Namespace) -> int:
    if args.confirm != "DELETE-FAILURE-LEARNING-DATA":
        print("Refusing purge: confirmation token did not match.", file=sys.stderr)
        return 2
    root = data_dir()
    _write_disabled_marker("disabled by purge")

    with _exclusive_drain_lock() as drain_acquired:
        if not drain_acquired:
            return _purge_deferred("drain-lock-busy", root)
        with _exclusive_privacy_maintenance_lock() as privacy_acquired:
            if not privacy_acquired:
                return _purge_deferred(
                    "privacy-maintenance-lock-busy",
                    root,
                )
            with exclusive_identity_key_lock(blocking=False) as identity_acquired:
                if not identity_acquired:
                    return _purge_deferred("identity-key-lock-busy", root)
                failures = _purge_known_data(root)
                residue = _purge_residue(root)

    if failures or residue:
        emit({
            "purged": False,
            "collection_enabled": False,
            "data_dir": str(root),
            "delete_failures": failures,
            "residue": residue,
            "retryable": True,
        })
        return 3
    emit({
        "purged": True,
        "collection_enabled": False,
        "data_dir": str(root),
        "remaining_control_files": sorted(PURGE_CONTROL_FILES),
    })
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Inspect and administer the private failure-learning ledger.")
    sub = root.add_subparsers(dest="command", required=True)
    sub.add_parser("init").set_defaults(func=cmd_init)
    sub.add_parser("doctor").set_defaults(func=cmd_doctor)
    sub.add_parser("status").set_defaults(func=cmd_status)
    events = sub.add_parser("events")
    events.add_argument("--limit", type=int, default=20)
    events.add_argument("--include-message", action="store_true", help="Include redacted untrusted message templates.")
    events.set_defaults(func=cmd_events)
    sub.add_parser("rebuild").set_defaults(func=cmd_rebuild)
    review = sub.add_parser("review")
    review.add_argument("--limit", type=int, default=20)
    review.set_defaults(func=cmd_review)
    outcome = sub.add_parser("add-outcome")
    outcome.add_argument("event_id")
    outcome.add_argument("--action-class", required=True)
    outcome.add_argument("--status", choices=("success", "failure", "partial", "unknown"), required=True)
    outcome.add_argument("--verification", choices=("reproduced", "indirect", "not-verified"), default="not-verified")
    outcome.add_argument("--risk", choices=("low", "medium", "high"), default="low")
    outcome.add_argument("--causal-strength", choices=("none", "weak", "moderate", "strong"), default="none")
    outcome.add_argument("--reversible", action="store_true")
    outcome.add_argument("--side-effects-checked", action="store_true")
    outcome.add_argument("--notes", default="")
    outcome.set_defaults(func=cmd_add_outcome)
    sub.add_parser("drain").set_defaults(func=cmd_drain)
    repair = sub.add_parser(
        "repair",
        help="Analyze legacy false positives; dry-run unless --apply is supplied.",
    )
    repair.add_argument("--apply", action="store_true")
    repair.set_defaults(func=cmd_repair)
    review_event = sub.add_parser("review-event")
    review_event.add_argument("event_id")
    review_event.add_argument(
        "--status", choices=("accepted", "quarantined", "non-actionable"), required=True
    )
    review_event.add_argument("--reason-class", required=True)
    review_event.set_defaults(func=cmd_review_event)
    case_add = sub.add_parser("case-add")
    case_add.add_argument("--case-id")
    case_add.add_argument("--title", required=True)
    case_add.add_argument("--category", required=True)
    case_add.add_argument("--scope", default="global")
    case_add.add_argument("--root-cause-class", required=True)
    case_add.add_argument("--remediation-class", required=True)
    case_add.add_argument(
        "--verification-status",
        choices=("unverified", "tested", "validated"),
        default="unverified",
    )
    case_add.add_argument("--evidence-ref", action="append", default=[])
    case_add.add_argument("--target-fingerprint", required=True)
    case_add.add_argument(
        "--status", choices=("open", "verified", "archived"), default="open"
    )
    case_add.set_defaults(func=cmd_case_add)
    cases = sub.add_parser("cases")
    cases.add_argument("--limit", type=int, default=20)
    cases.set_defaults(func=cmd_cases)
    sub.add_parser("disable").set_defaults(func=cmd_disable)
    sub.add_parser("enable").set_defaults(func=cmd_enable)
    export = sub.add_parser("export")
    export.add_argument("output")
    export.set_defaults(func=cmd_export)
    purge = sub.add_parser("purge")
    purge.add_argument("--confirm", required=True)
    purge.set_defaults(func=cmd_purge)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        return int(args.func(args))
    except PrivacyMaintenancePending as exc:
        emit({
            "status": "privacy-maintenance-pending",
            "detail_class": str(exc),
            "retryable": True,
        })
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
