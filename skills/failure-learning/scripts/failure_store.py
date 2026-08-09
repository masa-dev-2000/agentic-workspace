from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

from privacy_contract import residual_credential_class


SCHEMA_VERSION = 8
COLLECTOR_VERSION = "0.7.0"
SANITIZER_VERSION = 3
NORMALIZER_VERSION = 4
FINGERPRINT_VERSION = 3
SPOOL_AUTH_VERSION = 1
_INITIALIZE_LOCK = threading.Lock()
ADVICE_CACHE_VERSION = 2
ADVICE_MAX_AGE_DAYS = 180
ADVICE_CACHE_MAX_PATTERNS = 1000
EXPECTED_CONTROL_FLOW_TOOLS = {
    "collaborationwait_agent",
    "collaborationlist_agents",
    "collaboration.wait_agent",
    "collaboration.list_agents",
}
HEALTH_STATUSES = {"ok", "degraded", "error"}
HEALTH_DETAIL_CLASSES = {
    "event-inserted",
    "duplicate-ignored",
    "advice-cache-refresh-failed",
    "hook-health",
}


class DatabaseUnavailable(RuntimeError):
    """Raised when an existing ledger cannot be opened without creating it."""


class InvalidSpoolEnvelope(ValueError):
    """Raised before persistence when a spool envelope violates its allowlist."""


class PrivacyMaintenancePending(RuntimeError):
    """Raised when privacy repair cannot safely complete and must be retried."""


HEX_24_RE = re.compile(r"[0-9a-f]{24}")
HEX_32_RE = re.compile(r"[0-9a-f]{32}")
HEX_64_RE = re.compile(r"[0-9a-f]{64}")
SEMVER_RE = re.compile(r"\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?")
SAFE_NAME_RE = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
SAFE_CLASS_RE = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
SAFE_ENV_RE = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
OPAQUE_CASE_REF_RE = re.compile(
    r"([a-z][a-z0-9-]{0,31}):([A-Za-z0-9._@-]{1,160})"
)
CASE_PSEUDONYM_REF_RE = re.compile(
    r"[a-z][a-z0-9-]{0,31}:h1_[0-9a-f]{64}"
)
CASE_REF_PRIVACY_VERSION = "2"
CASE_REF_PRIVACY_STATE_KEY = "case_ref_privacy_state"
CASE_REF_PRIVACY_PENDING = "pending-v2"
CASE_REF_PRIVACY_COMPLETE = "complete-v2"
EVENT_PAYLOAD_PRIVACY_VERSION = "2"
EVENT_PAYLOAD_PRIVACY_STATE_KEY = "event_payload_privacy_state"
EVENT_PAYLOAD_PRIVACY_PENDING = "pending-v2"
EVENT_PAYLOAD_PRIVACY_COMPLETE = "complete-v2"
PRIVACY_READY_KEY = "privacy_ready"
PRIVACY_READY_VALUE = (
    f"schema-v{SCHEMA_VERSION}:case-v{CASE_REF_PRIVACY_VERSION}:"
    f"event-v{EVENT_PAYLOAD_PRIVACY_VERSION}"
)
# Compatibility aliases for callers that imported the former template-only names.
EVENT_TEMPLATE_PRIVACY_VERSION = EVENT_PAYLOAD_PRIVACY_VERSION
EVENT_TEMPLATE_PRIVACY_STATE_KEY = EVENT_PAYLOAD_PRIVACY_STATE_KEY
EVENT_TEMPLATE_PRIVACY_PENDING = EVENT_PAYLOAD_PRIVACY_PENDING
EVENT_TEMPLATE_PRIVACY_COMPLETE = EVENT_PAYLOAD_PRIVACY_COMPLETE
SAFE_ERROR_IDENTITY_RE = re.compile(
    r"(?:timeout|permission:denied|resource:not_found|invocation:invalid|"
    r"launcher-shim-unavailable|"
    r"win32:error_[0-9]{1,10}|process:exit_[0-9]{1,5}|message:[0-9a-f]{16}|"
    r"module:not_found:[0-9a-f]{16}|"
    r"exception:[a-z_][a-z0-9_.]*(?:error|exception):[0-9a-f]{16})"
)
RESIDUAL_WIN_DRIVE_PATH_RE = re.compile(
    r"""(?ix)
    (?<![A-Za-z0-9])
    [A-Z]:[\\/]
    [^\s<>:"'|?*,;)\]}]+
    """
)
RESIDUAL_WIN_UNC_PATH_RE = re.compile(
    r"""(?ix)
    (?<![A-Za-z0-9])
    \\\\
    [^\\\s<>:"'|?*,;)\]}]+
    \\
    [^\\\s<>:"'|?*,;)\]}]+
    (?:\\[^\\\s<>:"'|?*,;)\]}]+)*
    """
)
RESIDUAL_POSIX_PATH_RE = re.compile(
    r"""(?x)
    (?<![A-Za-z0-9_.:/-])
    /
    (?:[^/\s<>"']+/)*
    [^/\s<>"',;)\]}]+
    """
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def data_dir() -> Path:
    override = os.environ.get("CODEX_FAILURE_LEARNING_HOME")
    if override:
        return Path(override).expanduser().resolve()
    codex_home = os.environ.get("CODEX_HOME")
    base = Path(codex_home).expanduser() if codex_home else Path.home() / ".codex"
    return (base / "failure-learning").resolve()


def db_path() -> Path:
    return data_dir() / "failure-learning.db"


def disabled_path() -> Path:
    return data_dir() / "disabled"


def ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass


@contextmanager
def exclusive_identity_key_lock(*, blocking: bool = True):
    """Serialize identity-key creation and purge across local processes."""
    root = data_dir()
    ensure_private_dir(root)
    lock_path = root / ".identity.lock"
    handle = lock_path.open("a+b")
    acquired = False
    try:
        try:
            lock_path.chmod(0o600)
        except OSError:
            pass
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
            try:
                msvcrt.locking(handle.fileno(), mode, 1)
                acquired = True
            except OSError:
                acquired = False
        else:
            import fcntl

            flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
            try:
                fcntl.flock(handle.fileno(), flags)
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


def _read_identity_key(path: Path) -> bytes | None:
    try:
        key = path.read_bytes()
    except OSError:
        return None
    return key if len(key) == 32 else None


def _secret_key(create: bool = True) -> bytes | None:
    root = data_dir()
    path = root / "identity.key"
    if not create:
        return _read_identity_key(path)
    ensure_private_dir(root)
    with exclusive_identity_key_lock(blocking=True) as acquired:
        if not acquired:
            raise RuntimeError("identity-key-lock-unavailable")
        key = _read_identity_key(path)
        if key is not None:
            return key
        if path.exists():
            raise RuntimeError("identity-key-invalid")

        temp = root / f"identity.{os.getpid()}.{secrets.token_hex(4)}.tmp"
        descriptor = os.open(
            temp,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(secrets.token_bytes(32))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
            try:
                path.chmod(0o600)
            except OSError:
                pass
            try:
                directory = os.open(root, os.O_RDONLY)
            except OSError:
                directory = None
            if directory is not None:
                try:
                    os.fsync(directory)
                except OSError:
                    pass
                finally:
                    os.close(directory)
        finally:
            temp.unlink(missing_ok=True)

        key = _read_identity_key(path)
        if key is None:
            raise RuntimeError("identity-key-provision-failed")
        return key


def pseudonym(value: str | None, namespace: str) -> str | None:
    if not value:
        return None
    key = _secret_key()
    assert key is not None
    digest = hmac.new(key, f"{namespace}\0{value}".encode("utf-8", "replace"), hashlib.sha256)
    return digest.hexdigest()[:24]


def provision_identity_key() -> Path:
    key = _secret_key(create=True)
    assert key is not None
    return data_dir() / "identity.key"


def identity_key_readonly() -> bytes | None:
    """Return only an already-provisioned, valid identity key."""
    key = _secret_key(create=False)
    if key is None:
        return None
    return key


def pseudonym_readonly(value: str | None, namespace: str) -> str | None:
    """Pseudonymize without creating a key or directory."""
    if not value:
        return None
    key = identity_key_readonly()
    if key is None:
        return None
    digest = hmac.new(key, f"{namespace}\0{value}".encode("utf-8", "replace"), hashlib.sha256)
    return digest.hexdigest()[:24]


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()


def pseudonymize_case_evidence_ref(value: Any) -> str:
    """Keep the evidence class while replacing every caller token with HMAC."""
    rendered = str(value)
    match = OPAQUE_CASE_REF_RE.fullmatch(rendered)
    scheme = match.group(1) if match else "legacy"
    key = _secret_key()
    assert key is not None
    digest = hmac.new(
        key,
        f"learning-case-evidence\0{rendered}".encode("utf-8", "replace"),
        hashlib.sha256,
    ).hexdigest()
    return f"{scheme}:h1_{digest}"


def _spool_auth_bytes(envelope_without_tag: dict[str, Any]) -> bytes:
    try:
        rendered = json.dumps(
            envelope_without_tag,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise InvalidSpoolEnvelope("invalid-spool-auth-payload") from exc
    return rendered.encode("utf-8")


def authenticate_spool_envelope(
    envelope: dict[str, Any],
    key: bytes,
) -> dict[str, Any]:
    """Build the canonical authenticated wire envelope without reading or creating keys."""
    if not isinstance(key, bytes) or len(key) < 32:
        raise InvalidSpoolEnvelope("spool-auth-key-unavailable")
    body = canonicalize_spool_envelope(envelope)
    wire = dict(body)
    wire["auth_version"] = SPOOL_AUTH_VERSION
    wire["auth_tag"] = hmac.new(
        key,
        _spool_auth_bytes(wire),
        hashlib.sha256,
    ).hexdigest()
    return wire


def verify_spool_envelope_auth(envelope: dict[str, Any]) -> dict[str, Any]:
    """Verify the complete wire body before schema validation or persistence."""
    if not isinstance(envelope, dict):
        raise InvalidSpoolEnvelope("envelope-not-object")
    version = envelope.get("auth_version")
    if isinstance(version, bool) or version != SPOOL_AUTH_VERSION:
        if version is None:
            raise InvalidSpoolEnvelope("unsigned-spool-envelope")
        raise InvalidSpoolEnvelope("unsupported-spool-auth-version")
    supplied = envelope.get("auth_tag")
    if not isinstance(supplied, str) or not HEX_64_RE.fullmatch(supplied):
        raise InvalidSpoolEnvelope("invalid-spool-auth-tag")
    key = identity_key_readonly()
    if key is None:
        raise InvalidSpoolEnvelope("spool-auth-key-unavailable")
    signed = dict(envelope)
    signed.pop("auth_tag", None)
    expected = hmac.new(key, _spool_auth_bytes(signed), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, supplied):
        raise InvalidSpoolEnvelope("spool-auth-mismatch")
    signed.pop("auth_version", None)
    return signed


@contextmanager
def connect(timeout: float = 1.0):
    root = data_dir()
    ensure_private_dir(root)
    path = db_path()
    conn = sqlite3.connect(path, timeout=timeout)
    conn.row_factory = sqlite3.Row
    try:
        with _INITIALIZE_LOCK:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=1000")
            initialize(conn)
        yield conn
    finally:
        conn.close()
        try:
            path.chmod(0o600)
        except OSError:
            pass


@contextmanager
def connect_readonly(timeout: float = 1.0):
    """Open an existing ledger without mkdir, DDL, WAL changes, or chmod."""
    path = db_path()
    if not path.is_file():
        raise DatabaseUnavailable("database-not-initialized")

    def open_connection(*, immutable: bool) -> sqlite3.Connection:
        suffix = "?mode=ro&immutable=1" if immutable else "?mode=ro"
        uri = f"file:{quote(path.as_posix(), safe='/:')}{suffix}"
        connection = sqlite3.connect(uri, uri=True, timeout=timeout)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=1000")
            connection.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
            return connection
        except sqlite3.Error:
            connection.close()
            raise

    def sidecars_absent() -> bool:
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(path) + suffix)
            try:
                sidecar.stat()
                return False
            except FileNotFoundError:
                continue
            except OSError:
                return False
        return True

    try:
        conn = open_connection(immutable=False)
    except sqlite3.Error as first_error:
        if not sidecars_absent():
            raise DatabaseUnavailable("database-read-unavailable") from first_error
        try:
            conn = open_connection(immutable=True)
        except sqlite3.Error as fallback_error:
            raise DatabaseUnavailable("database-read-unavailable") from fallback_error
        # Refuse a stale immutable snapshot if a sidecar appeared during fallback.
        if not sidecars_absent():
            conn.close()
            raise DatabaseUnavailable("database-read-unavailable") from first_error
    try:
        yield conn
    except sqlite3.Error as exc:
        raise DatabaseUnavailable("database-read-unavailable") from exc
    finally:
        conn.close()


def advice_cache_path() -> Path:
    return data_dir() / "advice-cache.json"


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _ensure_column(conn: sqlite3.Connection, table: str, declaration: str) -> None:
    name = declaration.split()[0]
    if name not in _column_names(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {declaration}")


@contextmanager
def _exclusive_privacy_maintenance_lock():
    """Serialize destructive privacy maintenance across local processes."""
    root = data_dir()
    ensure_private_dir(root)
    handle = (root / ".privacy-maintenance.lock").open("a+b")
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


def _case_ref_privacy_status(
    conn: sqlite3.Connection,
) -> tuple[str | None, str | None]:
    values = {
        str(row["key"]): str(row["value"])
        for row in conn.execute(
            "SELECT key,value FROM meta "
            "WHERE key IN ('case_ref_privacy_version',?)",
            (CASE_REF_PRIVACY_STATE_KEY,),
        ).fetchall()
    }
    return (
        values.get("case_ref_privacy_version"),
        values.get(CASE_REF_PRIVACY_STATE_KEY),
    )


def _case_ref_privacy_complete(conn: sqlite3.Connection) -> bool:
    version, state = _case_ref_privacy_status(conn)
    return (
        version == CASE_REF_PRIVACY_VERSION
        and state == CASE_REF_PRIVACY_COMPLETE
    )


def _checkpoint_truncate_succeeded(conn: sqlite3.Connection) -> bool:
    try:
        row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    except sqlite3.Error:
        return False
    if row is None or len(row) < 3:
        return False
    return int(row[0]) == 0 and int(row[1]) == 0 and int(row[2]) == 0


def _set_case_privacy_pending(conn: sqlite3.Connection) -> None:
    conn.execute(
        "DELETE FROM meta WHERE key='case_ref_privacy_version'"
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
        (CASE_REF_PRIVACY_STATE_KEY, CASE_REF_PRIVACY_PENDING),
    )


def _case_ref_rows_satisfy_privacy(conn: sqlite3.Connection) -> bool:
    try:
        for row in conn.execute(
            "SELECT evidence_refs FROM learning_cases"
        ).fetchall():
            rendered = str(row["evidence_refs"])
            refs = json.loads(rendered)
            if (
                not isinstance(refs, list)
                or any(
                    not isinstance(ref, str)
                    or not CASE_PSEUDONYM_REF_RE.fullmatch(ref)
                    for ref in refs
                )
                or refs != sorted(set(refs))
                or rendered
                != json.dumps(
                    refs,
                    ensure_ascii=True,
                    separators=(",", ":"),
                )
            ):
                return False
    except (sqlite3.Error, TypeError, ValueError, json.JSONDecodeError):
        return False
    return True


def _rewrite_case_evidence_refs(conn: sqlite3.Connection) -> None:
    for row in conn.execute(
        "SELECT case_id,evidence_refs FROM learning_cases"
    ).fetchall():
        try:
            raw_refs = json.loads(row["evidence_refs"])
        except (TypeError, json.JSONDecodeError):
            raw_refs = [row["evidence_refs"]]
        if not isinstance(raw_refs, list):
            raw_refs = [raw_refs]
        safe_refs = sorted({
            pseudonymize_case_evidence_ref(item)
            for item in raw_refs
        })
        conn.execute(
            "UPDATE learning_cases SET evidence_refs=? WHERE case_id=?",
            (
                json.dumps(safe_refs, ensure_ascii=True, separators=(",", ":")),
                row["case_id"],
            ),
        )


def _run_case_ref_privacy_maintenance(conn: sqlite3.Connection) -> None:
    """Rewrite legacy refs and publish completion only after two clean truncations."""
    if (
        _case_ref_privacy_complete(conn)
        and _case_ref_rows_satisfy_privacy(conn)
    ):
        return
    with _exclusive_privacy_maintenance_lock() as acquired:
        if not acquired:
            raise PrivacyMaintenancePending("case-ref-privacy-maintenance-busy")
        if (
            _case_ref_privacy_complete(conn)
            and _case_ref_rows_satisfy_privacy(conn)
        ):
            return

        try:
            conn.execute("PRAGMA secure_delete=ON")
            secure_delete = conn.execute("PRAGMA secure_delete").fetchone()
            if not secure_delete or int(secure_delete[0]) != 1:
                raise PrivacyMaintenancePending("case-ref-secure-delete-unavailable")

            version, state = _case_ref_privacy_status(conn)
            rows_ready = _case_ref_rows_satisfy_privacy(conn)
            trusted_rows = rows_ready and (
                version in {"1", CASE_REF_PRIVACY_VERSION}
                or state == CASE_REF_PRIVACY_PENDING
            )
            if state != CASE_REF_PRIVACY_PENDING or not trusted_rows:
                conn.execute("BEGIN EXCLUSIVE")
                _set_case_privacy_pending(conn)
                if not trusted_rows:
                    _rewrite_case_evidence_refs(conn)
                if not _case_ref_rows_satisfy_privacy(conn):
                    raise sqlite3.IntegrityError(
                        "case reference privacy invariant failed"
                    )
                violations = conn.execute("PRAGMA foreign_key_check").fetchall()
                if violations:
                    raise sqlite3.IntegrityError(
                        "case reference privacy repair left foreign-key violations"
                    )
                conn.commit()

            if not _checkpoint_truncate_succeeded(conn):
                raise PrivacyMaintenancePending("case-ref-checkpoint-busy")

            if not _case_ref_rows_satisfy_privacy(conn):
                raise PrivacyMaintenancePending(
                    "case-ref-privacy-invariant-pending"
                )
            conn.execute("BEGIN EXCLUSIVE")
            conn.execute(
                "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
                ("case_ref_privacy_version", CASE_REF_PRIVACY_VERSION),
            )
            conn.execute(
                "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
                (CASE_REF_PRIVACY_STATE_KEY, CASE_REF_PRIVACY_COMPLETE),
            )
            conn.commit()

            if not _checkpoint_truncate_succeeded(conn):
                conn.execute("BEGIN EXCLUSIVE")
                _set_case_privacy_pending(conn)
                conn.commit()
                _checkpoint_truncate_succeeded(conn)
                raise PrivacyMaintenancePending(
                    "case-ref-completion-checkpoint-busy"
                )
        except PrivacyMaintenancePending:
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
            raise
        except (sqlite3.Error, TypeError, ValueError) as exc:
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
            raise PrivacyMaintenancePending("case-ref-privacy-maintenance-failed") from exc


def _event_payload_privacy_status(
    conn: sqlite3.Connection,
) -> tuple[str | None, str | None]:
    values = {
        str(row["key"]): str(row["value"])
        for row in conn.execute(
            "SELECT key,value FROM meta "
            "WHERE key IN ('event_payload_privacy_version',?)",
            (EVENT_PAYLOAD_PRIVACY_STATE_KEY,),
        ).fetchall()
    }
    return (
        values.get("event_payload_privacy_version"),
        values.get(EVENT_PAYLOAD_PRIVACY_STATE_KEY),
    )


def _event_payload_privacy_complete(conn: sqlite3.Connection) -> bool:
    version, state = _event_payload_privacy_status(conn)
    return (
        version == EVENT_PAYLOAD_PRIVACY_VERSION
        and state == EVENT_PAYLOAD_PRIVACY_COMPLETE
    )


def _set_event_payload_privacy_pending(conn: sqlite3.Connection) -> None:
    conn.execute(
        "DELETE FROM meta WHERE key IN "
        "('event_payload_privacy_version','event_template_privacy_version',"
        "'event_template_privacy_state')"
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
        (EVENT_PAYLOAD_PRIVACY_STATE_KEY, EVENT_PAYLOAD_PRIVACY_PENDING),
    )


def _create_events_immutable_trigger(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS events_are_immutable
        BEFORE UPDATE ON events
        BEGIN
            SELECT RAISE(ABORT, 'events are immutable');
        END
        """
    )


def _legacy_event_versions(payload: dict[str, Any]) -> dict[str, Any]:
    source = payload.get("versions")
    source = source if isinstance(source, dict) else {}
    schema = source.get("schema")
    collector = source.get("collector")
    versions: dict[str, Any] = {
        "schema": (
            schema
            if (
                isinstance(schema, int)
                and not isinstance(schema, bool)
                and 1 <= schema <= 100
            )
            else 1
        ),
        "collector": (
            collector
            if (
                isinstance(collector, str)
                and SEMVER_RE.fullmatch(collector)
            )
            else "0.0.0"
        ),
        "sanitizer": SANITIZER_VERSION,
    }
    for name in ("normalizer", "fingerprint"):
        value = source.get(name)
        versions[name] = (
            value
            if (
                isinstance(value, int)
                and not isinstance(value, bool)
                and 1 <= value <= 1000
            )
            else 1
        )
    return versions


def _legacy_event_environment(payload: dict[str, Any]) -> dict[str, str]:
    source = payload.get("environment")
    source = source if isinstance(source, dict) else {}
    environment: dict[str, str] = {}
    for name in ("os_family", "shell_family", "permission_mode"):
        value = source.get(name)
        environment[name] = (
            value
            if isinstance(value, str) and SAFE_ENV_RE.fullmatch(value)
            else "unknown"
        )
    return environment


def _iter_string_leaves(value: Any, depth: int = 0) -> Iterable[str]:
    if depth > 8:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _iter_string_leaves(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_string_leaves(child, depth + 1)


def _canonical_event_json(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    # Credential assignments must also be found through JSON escaping layers.
    # Absolute paths are checked on decoded leaves so JSON's own backslash
    # escaping cannot turn safe text into a false UNC/path match.
    residual = residual_credential_class(encoded)
    if residual is not None:
        raise sqlite3.IntegrityError(
            f"event-payload-privacy-{residual}"
        )
    for leaf in _iter_string_leaves(payload):
        residual = residual_sensitive_class(leaf)
        if residual is not None:
            raise sqlite3.IntegrityError(
                f"event-payload-privacy-{residual}"
            )
    return encoded


def _event_payload_rows_satisfy_privacy(conn: sqlite3.Connection) -> bool:
    relational_fields = (
        "event_id",
        "observed_at",
        "idempotency_key",
        "signature",
        "session_hash",
        "turn_hash",
        "tool_call_hash",
        "repo_hash",
        "tool_name",
        "tool_family",
        "operation_class",
        "outcome_class",
        "error_identity",
        "message_template",
        "capture_mode",
        "capture_completeness",
    )
    try:
        for row in conn.execute("SELECT * FROM events").fetchall():
            rendered = str(row["event_json"])
            payload = json.loads(rendered)
            if not isinstance(payload, dict):
                return False
            canonical = canonicalize_spool_envelope(
                payload,
                expected_type="failure",
            )
            if payload != canonical or rendered != _canonical_event_json(canonical):
                return False
            if any(
                canonical.get(field) != row[field]
                for field in relational_fields
            ):
                return False
    except (
        sqlite3.Error,
        TypeError,
        ValueError,
        KeyError,
        RecursionError,
        json.JSONDecodeError,
    ):
        return False
    return True


def _rewrite_event_payloads(conn: sqlite3.Connection) -> None:
    from capture_hook import sanitize_text

    conn.execute("DROP TRIGGER IF EXISTS events_are_immutable")
    for row in conn.execute(
        "SELECT * FROM events"
    ).fetchall():
        safe_message, _ = sanitize_text(str(row["message_template"] or ""))
        if not safe_message:
            safe_message = "redacted tool failure"
        if residual_sensitive_class(safe_message) is not None:
            raise sqlite3.IntegrityError(
                "event-template-privacy-residual-sensitive-content"
            )
        try:
            event_json = json.loads(str(row["event_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            event_json = {}
        if not isinstance(event_json, dict):
            event_json = {}
        # Legacy bodies are untrusted. Rebuild the exact current envelope
        # allowlist from relational columns and strictly validated nested
        # classes instead of retaining unknown keys or raw bodies.
        candidate = {
            "event_type": "failure",
            "event_id": str(row["event_id"]),
            "observed_at": str(row["observed_at"]),
            "idempotency_key": str(row["idempotency_key"]),
            "signature": str(row["signature"]),
            "session_hash": row["session_hash"],
            "turn_hash": row["turn_hash"],
            "tool_call_hash": row["tool_call_hash"],
            "repo_hash": row["repo_hash"],
            "tool_name": str(row["tool_name"]),
            "tool_family": str(row["tool_family"]),
            "operation_class": str(row["operation_class"]),
            "outcome_class": str(row["outcome_class"]),
            "error_identity": str(row["error_identity"]),
            "message_template": safe_message,
            "capture_mode": str(row["capture_mode"]),
            "capture_completeness": row["capture_completeness"],
            "environment": _legacy_event_environment(event_json),
            "versions": _legacy_event_versions(event_json),
            "safety": {
                "secret_scan": "best-effort-passed",
                "redaction_or_truncation_applied": True,
                "raw_input_stored": False,
                "raw_output_stored": False,
                "repo_correlation": (
                    "keyed-pseudonym"
                    if row["repo_hash"]
                    else "not-provided"
                ),
            },
        }
        rebuilt = canonicalize_spool_envelope(
            candidate,
            expected_type="failure",
        )
        canonical_payload = _canonical_event_json(rebuilt)
        conn.execute(
            "UPDATE events SET message_template=?, event_json=? WHERE event_id=?",
            (
                safe_message,
                canonical_payload,
                row["event_id"],
            ),
        )
    _create_events_immutable_trigger(conn)


def _run_event_payload_privacy_maintenance(conn: sqlite3.Connection) -> None:
    """Minimize legacy payloads and publish only after clean WAL truncation."""
    if (
        _event_payload_privacy_complete(conn)
        and _event_payload_rows_satisfy_privacy(conn)
    ):
        return
    with _exclusive_privacy_maintenance_lock() as acquired:
        if not acquired:
            raise PrivacyMaintenancePending(
                "event-payload-privacy-maintenance-busy"
            )
        if (
            _event_payload_privacy_complete(conn)
            and _event_payload_rows_satisfy_privacy(conn)
        ):
            return

        try:
            conn.execute("PRAGMA secure_delete=ON")
            secure_delete = conn.execute("PRAGMA secure_delete").fetchone()
            if not secure_delete or int(secure_delete[0]) != 1:
                raise PrivacyMaintenancePending(
                    "event-payload-secure-delete-unavailable"
                )

            _, state = _event_payload_privacy_status(conn)
            rows_ready = _event_payload_rows_satisfy_privacy(conn)
            if state != EVENT_PAYLOAD_PRIVACY_PENDING or not rows_ready:
                conn.execute("BEGIN EXCLUSIVE")
                _set_event_payload_privacy_pending(conn)
                # A version without its same-transaction completion state is
                # not evidence that the legacy payload was minimized.
                _rewrite_event_payloads(conn)
                if not _event_payload_rows_satisfy_privacy(conn):
                    raise sqlite3.IntegrityError(
                        "event payload privacy invariant failed"
                    )
                violations = conn.execute("PRAGMA foreign_key_check").fetchall()
                if violations:
                    raise sqlite3.IntegrityError(
                        "event payload privacy repair left foreign-key violations"
                    )
                conn.commit()

            if not _checkpoint_truncate_succeeded(conn):
                raise PrivacyMaintenancePending(
                    "event-payload-checkpoint-busy"
                )

            if not _event_payload_rows_satisfy_privacy(conn):
                raise PrivacyMaintenancePending(
                    "event-payload-privacy-invariant-pending"
                )
            conn.execute("BEGIN EXCLUSIVE")
            conn.execute(
                "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
                (
                    "event_payload_privacy_version",
                    EVENT_PAYLOAD_PRIVACY_VERSION,
                ),
            )
            conn.execute(
                "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
                (
                    EVENT_PAYLOAD_PRIVACY_STATE_KEY,
                    EVENT_PAYLOAD_PRIVACY_COMPLETE,
                ),
            )
            conn.commit()

            if not _checkpoint_truncate_succeeded(conn):
                conn.execute("BEGIN EXCLUSIVE")
                _set_event_payload_privacy_pending(conn)
                conn.commit()
                _checkpoint_truncate_succeeded(conn)
                raise PrivacyMaintenancePending(
                    "event-payload-completion-checkpoint-busy"
                )
        except PrivacyMaintenancePending:
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
            raise
        except (sqlite3.Error, TypeError, ValueError) as exc:
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
            _create_events_immutable_trigger(conn)
            raise PrivacyMaintenancePending(
                "event-payload-privacy-maintenance-failed"
            ) from exc


def privacy_readiness(
    conn: sqlite3.Connection,
    *,
    verify_rows: bool = True,
) -> dict[str, Any]:
    keys = (
        "schema_version",
        "case_ref_privacy_version",
        CASE_REF_PRIVACY_STATE_KEY,
        "event_payload_privacy_version",
        EVENT_PAYLOAD_PRIVACY_STATE_KEY,
        PRIVACY_READY_KEY,
    )
    placeholders = ",".join("?" for _ in keys)
    values = {
        str(row["key"]): str(row["value"])
        for row in conn.execute(
            f"SELECT key,value FROM meta WHERE key IN ({placeholders})",
            keys,
        ).fetchall()
    }
    case_ready = (
        values.get("case_ref_privacy_version") == CASE_REF_PRIVACY_VERSION
        and values.get(CASE_REF_PRIVACY_STATE_KEY)
        == CASE_REF_PRIVACY_COMPLETE
    )
    event_ready = (
        values.get("event_payload_privacy_version")
        == EVENT_PAYLOAD_PRIVACY_VERSION
        and values.get(EVENT_PAYLOAD_PRIVACY_STATE_KEY)
        == EVENT_PAYLOAD_PRIVACY_COMPLETE
    )
    schema_ready = values.get("schema_version") == str(SCHEMA_VERSION)
    aggregate_ready = values.get(PRIVACY_READY_KEY) == PRIVACY_READY_VALUE
    metadata_ready = bool(
        case_ready and event_ready and schema_ready and aggregate_ready
    )
    case_rows_ready: bool | None = False
    event_rows_ready: bool | None = False
    if metadata_ready and verify_rows:
        case_rows_ready = _case_ref_rows_satisfy_privacy(conn)
        event_rows_ready = _event_payload_rows_satisfy_privacy(conn)
    elif metadata_ready:
        case_rows_ready = None
        event_rows_ready = None
    return {
        "ready": bool(
            metadata_ready
            and (
                not verify_rows
                or (case_rows_ready and event_rows_ready)
            )
        ),
        "row_invariants_checked": verify_rows,
        "metadata_ready": metadata_ready,
        "case_ref_rows_ready": case_rows_ready,
        "event_payload_rows_ready": event_rows_ready,
        "schema_version": values.get("schema_version"),
        "case_ref_privacy_version": values.get("case_ref_privacy_version"),
        "case_ref_privacy_state": values.get(CASE_REF_PRIVACY_STATE_KEY),
        "event_payload_privacy_version": values.get(
            "event_payload_privacy_version"
        ),
        "event_payload_privacy_state": values.get(
            EVENT_PAYLOAD_PRIVACY_STATE_KEY
        ),
        "privacy_ready": values.get(PRIVACY_READY_KEY),
    }


def require_privacy_ready(conn: sqlite3.Connection) -> dict[str, Any]:
    status = privacy_readiness(conn)
    if not status["ready"]:
        raise DatabaseUnavailable("privacy-maintenance-pending")
    return status


def repair_privacy_readiness(conn: sqlite3.Connection) -> dict[str, Any]:
    """Explicitly repair row invariants before publishing strict readiness."""
    status = privacy_readiness(conn)
    if status["ready"]:
        return status
    conn.execute("DELETE FROM meta WHERE key=?", (PRIVACY_READY_KEY,))
    if status["schema_version"] == str(SCHEMA_VERSION):
        conn.execute("DELETE FROM meta WHERE key='schema_version'")
    conn.commit()
    _run_case_ref_privacy_maintenance(conn)
    _run_event_payload_privacy_maintenance(conn)
    _publish_privacy_ready(conn)
    status = privacy_readiness(conn)
    if not status["ready"]:
        raise PrivacyMaintenancePending("privacy-row-invariants-incomplete")
    return status


def _invalidate_privacy_publication(
    conn: sqlite3.Connection,
    *,
    verify_rows: bool = True,
) -> bool:
    status = privacy_readiness(conn, verify_rows=verify_rows)
    if status["ready"]:
        return True
    conn.execute("DELETE FROM meta WHERE key=?", (PRIVACY_READY_KEY,))
    if status["schema_version"] == str(SCHEMA_VERSION):
        conn.execute("DELETE FROM meta WHERE key='schema_version'")
    return False


def _publish_privacy_ready(conn: sqlite3.Connection) -> None:
    if privacy_readiness(conn)["ready"]:
        return
    with _exclusive_privacy_maintenance_lock() as acquired:
        if not acquired:
            raise PrivacyMaintenancePending(
                "privacy-ready-maintenance-busy"
            )
        if privacy_readiness(conn)["ready"]:
            return
        if not (
            _case_ref_privacy_complete(conn)
            and _event_payload_privacy_complete(conn)
        ):
            raise PrivacyMaintenancePending(
                "privacy-components-incomplete"
            )
        # Both secure-delete rewrites and component markers must be in the
        # main database before the single public readiness marker is written.
        if not _checkpoint_truncate_succeeded(conn):
            raise PrivacyMaintenancePending(
                "privacy-ready-checkpoint-busy"
            )
        conn.execute("BEGIN EXCLUSIVE")
        conn.execute(
            "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
            (PRIVACY_READY_KEY, PRIVACY_READY_VALUE),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta(key,value) "
            "VALUES('schema_version',?)",
            (str(SCHEMA_VERSION),),
        )
        conn.commit()


def initialize(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS events (
            event_id TEXT PRIMARY KEY,
            observed_at TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            signature TEXT NOT NULL,
            session_hash TEXT,
            turn_hash TEXT,
            tool_call_hash TEXT,
            repo_hash TEXT,
            tool_name TEXT NOT NULL,
            tool_family TEXT NOT NULL,
            operation_class TEXT NOT NULL,
            outcome_class TEXT NOT NULL,
            error_identity TEXT NOT NULL,
            message_template TEXT NOT NULL,
            capture_mode TEXT NOT NULL,
            capture_completeness REAL NOT NULL,
            auth_verified INTEGER NOT NULL DEFAULT 0
              CHECK(auth_verified IN (0,1)),
            event_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TRIGGER IF NOT EXISTS events_are_immutable
        BEFORE UPDATE ON events
        BEGIN
            SELECT RAISE(ABORT, 'events are immutable');
        END;

        CREATE TABLE IF NOT EXISTS recovery_markers (
            recovery_id TEXT PRIMARY KEY,
            observed_at TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            session_hash TEXT NOT NULL,
            repo_hash TEXT,
            tool_name TEXT NOT NULL,
            operation_class TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS intervention_outcomes (
            outcome_id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
            observed_at TEXT NOT NULL,
            action_class TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('success','failure','partial','unknown')),
            verification TEXT NOT NULL CHECK(verification IN ('reproduced','indirect','not-verified')),
            risk_class TEXT NOT NULL CHECK(risk_class IN ('low','medium','high')),
            reversible INTEGER NOT NULL,
            side_effects_checked INTEGER NOT NULL,
            causal_strength TEXT NOT NULL CHECK(causal_strength IN ('none','weak','moderate','strong')),
            notes TEXT NOT NULL DEFAULT '',
            source_recovery_id TEXT REFERENCES recovery_markers(recovery_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS patterns (
            pattern_id TEXT PRIMARY KEY,
            signature TEXT NOT NULL UNIQUE,
            event_signature TEXT,
            scope_key TEXT,
            repo_hash TEXT,
            tool_name TEXT NOT NULL,
            tool_family TEXT NOT NULL,
            operation_class TEXT NOT NULL,
            error_identity TEXT NOT NULL,
            incident_count INTEGER NOT NULL,
            independent_sessions INTEGER NOT NULL,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'observed',
            quality_status TEXT NOT NULL DEFAULT 'eligible',
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS pattern_events (
            pattern_id TEXT NOT NULL REFERENCES patterns(pattern_id) ON DELETE CASCADE,
            event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
            PRIMARY KEY(pattern_id, event_id)
        );

        CREATE TABLE IF NOT EXISTS collector_health (
            health_id INTEGER PRIMARY KEY AUTOINCREMENT,
            observed_at TEXT NOT NULL,
            status TEXT NOT NULL,
            detail_class TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS event_reviews (
            event_id TEXT PRIMARY KEY REFERENCES events(event_id) ON DELETE CASCADE,
            review_status TEXT NOT NULL
              CHECK(review_status IN ('accepted','quarantined','non-actionable')),
            reason_class TEXT NOT NULL,
            reviewed_at TEXT NOT NULL,
            review_source TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS spool_receipts (
            idempotency_key TEXT PRIMARY KEY,
            processed_at TEXT NOT NULL,
            event_type TEXT NOT NULL,
            disposition TEXT NOT NULL,
            reference_id TEXT
        );

        CREATE TABLE IF NOT EXISTS learning_cases (
            case_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            title TEXT NOT NULL,
            category TEXT NOT NULL,
            scope TEXT NOT NULL,
            root_cause_class TEXT NOT NULL,
            remediation_class TEXT NOT NULL,
            verification_status TEXT NOT NULL
              CHECK(verification_status IN ('unverified','tested','validated')),
            evidence_refs TEXT NOT NULL,
            target_fingerprint TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('open','verified','archived'))
        );

        CREATE INDEX IF NOT EXISTS idx_events_signature ON events(signature);
        CREATE INDEX IF NOT EXISTS idx_events_observed_at ON events(observed_at DESC);
        CREATE INDEX IF NOT EXISTS idx_outcomes_event_id ON intervention_outcomes(event_id);
        CREATE INDEX IF NOT EXISTS idx_recovery_markers_scope
          ON recovery_markers(session_hash, repo_hash, tool_name, operation_class, observed_at);
        CREATE INDEX IF NOT EXISTS idx_event_reviews_status ON event_reviews(review_status);
        CREATE INDEX IF NOT EXISTS idx_learning_cases_created_at
          ON learning_cases(created_at DESC);
        """
    )
    # Compatible migration for schema-v1 ledgers. Rebuilt patterns replace legacy rows.
    _ensure_column(conn, "patterns", "event_signature TEXT")
    _ensure_column(conn, "patterns", "scope_key TEXT")
    _ensure_column(conn, "patterns", "repo_hash TEXT")
    _ensure_column(conn, "patterns", "quality_status TEXT NOT NULL DEFAULT 'eligible'")
    _ensure_column(conn, "patterns", "updated_at TEXT")
    auth_column_was_present = "auth_verified" in _column_names(conn, "events")
    _ensure_column(
        conn,
        "events",
        "auth_verified INTEGER NOT NULL DEFAULT 0 CHECK(auth_verified IN (0,1))",
    )
    _ensure_column(
        conn,
        "intervention_outcomes",
        "source_recovery_id TEXT REFERENCES recovery_markers(recovery_id)",
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_patterns_scope "
        "ON patterns(repo_hash, tool_name, operation_class, last_seen DESC)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_outcomes_source_recovery "
        "ON intervention_outcomes(source_recovery_id) "
        "WHERE source_recovery_id IS NOT NULL"
    )
    quarantine_cursor = conn.execute(
        """
        INSERT OR IGNORE INTO event_reviews(
          event_id, review_status, reason_class, reviewed_at, review_source
        )
        SELECT e.event_id, 'quarantined', 'unsigned-legacy-envelope', ?, 'schema-v4-migration'
        FROM events e
        WHERE e.auth_verified=0
        """,
        (utc_now(),),
    )
    # Do not advertise the current schema until both destructive privacy
    # migrations have completed and their WAL frames have been truncated.
    # Ordinary authenticated writes trust the already-published metadata and
    # validate only the new envelope. Full row audits remain at explicit
    # maintenance and body/reference read gates, avoiding O(rows * spool).
    privacy_was_ready = _invalidate_privacy_publication(
        conn,
        verify_rows=False,
    )
    values = {
        "collector_version": COLLECTOR_VERSION,
        "sanitizer_version": str(SANITIZER_VERSION),
        "normalizer_version": str(NORMALIZER_VERSION),
    }
    conn.executemany("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", values.items())
    if not auth_column_was_present or quarantine_cursor.rowcount:
        _rebuild_patterns_conn(conn)
        try:
            advice_cache_path().unlink(missing_ok=True)
        except OSError:
            pass
    conn.commit()
    if privacy_was_ready:
        return
    _run_case_ref_privacy_maintenance(conn)
    _run_event_payload_privacy_maintenance(conn)
    _publish_privacy_ready(conn)


def _version_tuple(value: str | None) -> tuple[int, int, int]:
    parts: list[int] = []
    for part in str(value or "").split(".")[:3]:
        digits = "".join(ch for ch in part if ch.isdigit())
        parts.append(int(digits or 0))
    return tuple((parts + [0, 0, 0])[:3])  # type: ignore[return-value]


def event_collector_version(event_json: str | dict[str, Any]) -> str:
    try:
        payload = json.loads(event_json) if isinstance(event_json, str) else event_json
        versions = payload.get("versions") if isinstance(payload, dict) else None
        if isinstance(versions, dict):
            return str(versions.get("collector") or "0.0.0")
    except (TypeError, ValueError):
        pass
    return "0.0.0"


def is_expected_control_flow(event: dict[str, Any]) -> bool:
    return (
        str(event.get("error_identity") or "").lower() == "timeout"
        and str(event.get("tool_name") or "").lower() in EXPECTED_CONTROL_FLOW_TOOLS
    )


def is_actionable_event(event: dict[str, Any]) -> bool:
    review = str(event.get("review_status") or "")
    if review in {"quarantined", "non-actionable"}:
        return False
    if review == "accepted":
        return True
    if event.get("auth_verified") not in {1, True}:
        return False
    if is_expected_control_flow(event):
        return False
    return _version_tuple(event_collector_version(event.get("event_json") or {})) >= (0, 2, 0)


def _pattern_identity(event: dict[str, Any]) -> tuple[str, str, str]:
    exact = {
        "event_signature": str(event["signature"]),
        "repo_hash": event.get("repo_hash"),
        "tool_name": str(event["tool_name"]).lower(),
        "operation_class": str(event["operation_class"]),
        "error_identity": str(event["error_identity"]),
    }
    pattern_signature = stable_hash(json.dumps(exact, sort_keys=True, separators=(",", ":")))
    scope_key = stable_hash(json.dumps(
        {"repo_hash": event.get("repo_hash"), "tool_name": str(event["tool_name"]).lower()},
        sort_keys=True,
        separators=(",", ":"),
    ))
    return stable_hash(f"pattern-v3\0{pattern_signature}")[:24], pattern_signature, scope_key


def _replace_pattern_group(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    first = rows[0]
    pattern_id, pattern_signature, scope_key = _pattern_identity(first)
    conn.execute("DELETE FROM pattern_events WHERE pattern_id=?", (pattern_id,))
    conn.execute(
        "DELETE FROM patterns WHERE pattern_id=? OR signature=?",
        (pattern_id, pattern_signature),
    )
    sessions = {str(row["session_hash"]) for row in rows if row.get("session_hash")}
    status = "accepted" if any(row.get("review_status") == "accepted" for row in rows) else "observed"
    first_seen = min(str(row["observed_at"]) for row in rows)
    last_seen = max(str(row["observed_at"]) for row in rows)
    conn.execute(
        """
        INSERT INTO patterns(
          pattern_id, signature, event_signature, scope_key, repo_hash,
          tool_name, tool_family, operation_class, error_identity,
          incident_count, independent_sessions, first_seen, last_seen,
          status, quality_status, updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            pattern_id, pattern_signature, str(first["signature"]), scope_key,
            first.get("repo_hash"), str(first["tool_name"]), str(first["tool_family"]),
            str(first["operation_class"]), str(first["error_identity"]), len(rows),
            len(sessions), first_seen, last_seen, status, "eligible", utc_now(),
        ),
    )
    conn.executemany(
        "INSERT INTO pattern_events(pattern_id, event_id) VALUES(?,?)",
        ((pattern_id, str(row["event_id"])) for row in rows),
    )
    linked = conn.execute(
        "SELECT COUNT(*) FROM pattern_events WHERE pattern_id=?", (pattern_id,)
    ).fetchone()[0]
    if linked != len(rows):
        raise sqlite3.IntegrityError("pattern-link-count-mismatch")


def _matching_actionable_rows(
    conn: sqlite3.Connection, event: dict[str, Any]
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT e.*, er.review_status
        FROM events e
        LEFT JOIN event_reviews er ON er.event_id=e.event_id
        WHERE e.signature=?
          AND lower(e.tool_name)=lower(?)
          AND e.operation_class=?
          AND e.error_identity=?
          AND (e.repo_hash=? OR (? IS NULL AND e.repo_hash IS NULL))
        ORDER BY e.observed_at, e.event_id
        """,
        (
            event["signature"], event["tool_name"], event["operation_class"],
            event["error_identity"], event.get("repo_hash"), event.get("repo_hash"),
        ),
    ).fetchall()
    return [dict(row) for row in rows if is_actionable_event(dict(row))]


def _refresh_pattern_for_event(conn: sqlite3.Connection, event: dict[str, Any]) -> None:
    if not is_actionable_event(event):
        return
    _replace_pattern_group(conn, _matching_actionable_rows(conn, event))


def _insert_event_conn(
    conn: sqlite3.Connection,
    event: dict[str, Any],
    *,
    auth_verified: bool,
) -> bool:
    event = canonicalize_spool_envelope(event, expected_type="failure")
    serialized_event = _canonical_event_json(event)
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO events(
            event_id, observed_at, idempotency_key, signature,
            session_hash, turn_hash, tool_call_hash, repo_hash,
            tool_name, tool_family, operation_class, outcome_class,
            error_identity, message_template, capture_mode,
            capture_completeness, auth_verified, event_json, created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            event["event_id"], event["observed_at"], event["idempotency_key"], event["signature"],
            event.get("session_hash"), event.get("turn_hash"), event.get("tool_call_hash"),
            event.get("repo_hash"), event["tool_name"], event["tool_family"],
            event["operation_class"], event["outcome_class"], event["error_identity"],
            event["message_template"], event["capture_mode"], event["capture_completeness"],
            int(auth_verified), serialized_event, utc_now(),
        ),
    )
    if cursor.rowcount:
        if not auth_verified:
            conn.execute(
                """
                INSERT OR IGNORE INTO event_reviews(
                  event_id, review_status, reason_class, reviewed_at, review_source
                ) VALUES(?,?,?,?,?)
                """,
                (
                    event["event_id"],
                    "quarantined",
                    "unsigned-legacy-envelope",
                    utc_now(),
                    "unverified-direct-insert",
                ),
            )
        stored = dict(event)
        stored["event_json"] = serialized_event
        stored["review_status"] = "quarantined" if not auth_verified else None
        stored["auth_verified"] = int(auth_verified)
        _refresh_pattern_for_event(conn, stored)
        _reconcile_recovery_scope_conn(
            conn,
            session_hash=event.get("session_hash"),
            repo_hash=event.get("repo_hash"),
            tool_name=str(event["tool_name"]),
            operation_class=str(event["operation_class"]),
        )
    conn.execute(
        "INSERT INTO collector_health(observed_at, status, detail_class) VALUES(?,?,?)",
        (utc_now(), "ok", "event-inserted" if cursor.rowcount else "duplicate-ignored"),
    )
    return bool(cursor.rowcount)


def insert_event(event: dict[str, Any]) -> bool:
    """Retain a direct event as unverified evidence, excluded until explicitly accepted."""
    with connect() as conn:
        inserted = _insert_event_conn(conn, event, auth_verified=False)
        conn.commit()
        return inserted


def _parsed_observed_at(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _reconcile_recovery_scope_conn(
    conn: sqlite3.Connection,
    *,
    session_hash: str | None,
    repo_hash: str | None,
    tool_name: str,
    operation_class: str,
) -> int:
    if not session_hash:
        return 0
    marker_rows = conn.execute(
        """
        SELECT recovery_id, observed_at
        FROM recovery_markers
        WHERE session_hash=? AND lower(tool_name)=lower(?) AND operation_class=?
          AND (repo_hash=? OR (? IS NULL AND repo_hash IS NULL))
        """,
        (session_hash, tool_name, operation_class, repo_hash, repo_hash),
    ).fetchall()
    if not marker_rows:
        return 0
    conn.execute(
        """
        DELETE FROM intervention_outcomes
        WHERE source_recovery_id IN (
          SELECT recovery_id
          FROM recovery_markers
          WHERE session_hash=? AND lower(tool_name)=lower(?) AND operation_class=?
            AND (repo_hash=? OR (? IS NULL AND repo_hash IS NULL))
        )
        """,
        (session_hash, tool_name, operation_class, repo_hash, repo_hash),
    )
    failure_rows = conn.execute(
        """
        SELECT e.event_id, e.observed_at
        FROM events e
        WHERE e.session_hash=? AND lower(e.tool_name)=lower(?) AND e.operation_class=?
          AND (e.repo_hash=? OR (? IS NULL AND e.repo_hash IS NULL))
          AND NOT EXISTS (
            SELECT 1
            FROM event_reviews er
            WHERE er.event_id=e.event_id
              AND er.review_status IN ('quarantined','non-actionable')
          )
          AND (
            e.auth_verified=1
            OR EXISTS (
              SELECT 1
              FROM event_reviews er
              WHERE er.event_id=e.event_id AND er.review_status='accepted'
            )
          )
          AND NOT EXISTS (
            SELECT 1
            FROM intervention_outcomes o
            WHERE o.event_id=e.event_id
              AND o.source_recovery_id IS NULL
              AND o.status='success'
          )
        """,
        (session_hash, tool_name, operation_class, repo_hash, repo_hash),
    ).fetchall()
    if not failure_rows:
        return 0

    timeline: list[tuple[datetime, int, str, str, str]] = []
    timeline.extend(
        (
            _parsed_observed_at(str(row["observed_at"])),
            0,
            str(row["event_id"]),
            "failure",
            str(row["event_id"]),
        )
        for row in failure_rows
    )
    timeline.extend(
        (
            _parsed_observed_at(str(row["observed_at"])),
            1,
            str(row["recovery_id"]),
            "recovery",
            str(row["recovery_id"]),
        )
        for row in marker_rows
    )
    timeline.sort()
    unmatched_failures: list[str] = []
    matches: list[tuple[str, str, str]] = []
    marker_times = {
        str(row["recovery_id"]): str(row["observed_at"]) for row in marker_rows
    }
    for _, _, _, event_type, identifier in timeline:
        if event_type == "failure":
            unmatched_failures.append(identifier)
        elif unmatched_failures:
            matches.append(
                (unmatched_failures.pop(), identifier, marker_times[identifier])
            )

    conn.executemany(
        """
        INSERT INTO intervention_outcomes(
          outcome_id, event_id, observed_at, action_class, status,
          verification, risk_class, reversible, side_effects_checked,
          causal_strength, notes, source_recovery_id
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            (
                stable_hash(f"recovery-outcome\0{recovery_id}")[:32],
                event_id,
                recovery_at,
                "subsequent-retry",
                "success",
                "indirect",
                "low",
                1,
                0,
                "none",
                "A later explicit success matched the same session, repository, tool, and operation class.",
                recovery_id,
            )
            for event_id, recovery_id, recovery_at in matches
        ),
    )
    return len(matches)


def _record_recovery_conn(
    conn: sqlite3.Connection,
    *,
    recovery_id: str,
    observed_at: str,
    idempotency_key: str,
    session_hash: str | None,
    repo_hash: str | None,
    tool_name: str,
    operation_class: str,
) -> bool:
    if not session_hash:
        return False
    conn.execute(
        """
        INSERT OR IGNORE INTO recovery_markers(
          recovery_id, observed_at, idempotency_key, session_hash,
          repo_hash, tool_name, operation_class, created_at
        ) VALUES(?,?,?,?,?,?,?,?)
        """,
        (
            recovery_id,
            observed_at,
            idempotency_key,
            session_hash,
            repo_hash,
            tool_name,
            operation_class,
            utc_now(),
        ),
    )
    _reconcile_recovery_scope_conn(
        conn,
        session_hash=session_hash,
        repo_hash=repo_hash,
        tool_name=tool_name,
        operation_class=operation_class,
    )
    return bool(conn.execute(
        "SELECT 1 FROM intervention_outcomes WHERE source_recovery_id=?",
        (recovery_id,),
    ).fetchone())


def record_recovery(payload: dict[str, Any]) -> bool:
    """Reject unsigned direct recovery claims; authenticated spool processing is required."""
    return False


def record_health(status: str, detail_class: str) -> None:
    if status not in HEALTH_STATUSES or detail_class not in HEALTH_DETAIL_CLASSES:
        raise ValueError("invalid-health-class")
    with connect() as conn:
        conn.execute(
            "INSERT INTO collector_health(observed_at, status, detail_class) VALUES(?,?,?)",
            (utc_now(), status, detail_class),
        )
        conn.commit()


def _validate_timestamp(value: Any) -> None:
    if not isinstance(value, str) or len(value) > 40:
        raise InvalidSpoolEnvelope("invalid-observed-at")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidSpoolEnvelope("invalid-observed-at") from exc


def _validate_optional_hash(value: Any, name: str) -> None:
    if value is not None and (
        not isinstance(value, str) or not HEX_24_RE.fullmatch(value)
    ):
        raise InvalidSpoolEnvelope(f"invalid-{name}")


def _validate_versions(value: Any, full: bool) -> None:
    allowed = {"schema", "collector", "sanitizer", "normalizer", "fingerprint"}
    required = allowed if full else {"schema", "collector"}
    if not isinstance(value, dict) or set(value) - allowed or not required <= set(value):
        raise InvalidSpoolEnvelope("invalid-versions")
    if not isinstance(value.get("schema"), int) or not 1 <= value["schema"] <= 100:
        raise InvalidSpoolEnvelope("invalid-schema-version")
    if not isinstance(value.get("collector"), str) or not SEMVER_RE.fullmatch(
        value["collector"]
    ):
        raise InvalidSpoolEnvelope("invalid-collector-version")
    for key in {"sanitizer", "normalizer", "fingerprint"} & set(value):
        if not isinstance(value[key], int) or not 1 <= value[key] <= 1000:
            raise InvalidSpoolEnvelope(f"invalid-{key}-version")


def _validate_safety(value: Any, *, event_type: str) -> None:
    required_by_type = {
        "failure": {
            "secret_scan", "raw_input_stored", "raw_output_stored",
            "redaction_or_truncation_applied", "repo_correlation",
        },
        "recovery": {"secret_scan", "raw_input_stored", "raw_output_stored"},
        "health": {"raw_input_stored", "raw_output_stored"},
    }
    required = required_by_type[event_type]
    if not isinstance(value, dict) or set(value) != required:
        raise InvalidSpoolEnvelope("invalid-safety")
    if value.get("raw_input_stored") is not False:
        raise InvalidSpoolEnvelope("raw-input-not-false")
    if value.get("raw_output_stored") is not False:
        raise InvalidSpoolEnvelope("raw-output-not-false")
    if event_type == "failure":
        if value.get("secret_scan") != "best-effort-passed":
            raise InvalidSpoolEnvelope("invalid-secret-scan")
        if not isinstance(value.get("redaction_or_truncation_applied"), bool):
            raise InvalidSpoolEnvelope("invalid-redaction-flag")
        if value.get("repo_correlation") not in {
            "keyed-pseudonym", "unavailable-no-identity-key", "not-provided",
        }:
            raise InvalidSpoolEnvelope("invalid-repo-correlation")
    elif event_type == "recovery":
        if value.get("secret_scan") != "not-applicable-no-content-stored":
            raise InvalidSpoolEnvelope("invalid-secret-scan")


def residual_path_class(value: str) -> str | None:
    """Return a bounded reason when decoded text retains an absolute path."""
    if RESIDUAL_WIN_UNC_PATH_RE.search(value):
        return "residual-windows-unc-path"
    if RESIDUAL_WIN_DRIVE_PATH_RE.search(value):
        return "residual-windows-drive-path"
    if RESIDUAL_POSIX_PATH_RE.search(value):
        return "residual-posix-path"
    return None


def residual_sensitive_class(value: str) -> str | None:
    """Independently reject residual credentials and absolute paths at persistence."""
    credential = residual_credential_class(value)
    if credential is not None:
        return credential
    return residual_path_class(value)


def _validate_message_template(value: Any) -> None:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise InvalidSpoolEnvelope("invalid-message-template")
    residual = residual_sensitive_class(value)
    if residual is not None:
        raise InvalidSpoolEnvelope(residual)
    # The persistence boundary independently reruns the sanitizer. Any change means
    # the spool contained raw or non-canonical text and must be rejected, not repaired.
    from capture_hook import sanitize_text

    sanitized, _ = sanitize_text(value)
    if sanitized != value:
        raise InvalidSpoolEnvelope("unsafe-message-template")


def validate_spool_envelope(envelope: dict[str, Any]) -> str:
    """Strictly validate privacy-safe Hook output before any receipt or row write."""
    if not isinstance(envelope, dict):
        raise InvalidSpoolEnvelope("envelope-not-object")
    event_type = str(envelope.get("event_type") or "failure")
    common = {"event_type", "event_id", "observed_at", "idempotency_key", "versions", "safety"}
    failure_keys = common | {
        "signature", "session_hash", "turn_hash", "tool_call_hash", "repo_hash",
        "tool_name", "tool_family", "operation_class", "outcome_class",
        "error_identity", "message_template", "capture_mode",
        "capture_completeness", "environment",
    }
    recovery_keys = common | {
        "session_hash", "repo_hash", "tool_name", "operation_class",
    }
    health_keys = common | {"status", "detail_class"}
    allowed = {
        "failure": failure_keys,
        "recovery": recovery_keys,
        "health": health_keys,
    }.get(event_type)
    if allowed is None:
        raise InvalidSpoolEnvelope("unknown-event-type")
    unknown = set(envelope) - allowed
    if unknown:
        raise InvalidSpoolEnvelope("unknown-top-level-key")
    if not isinstance(envelope.get("idempotency_key"), str) or not HEX_64_RE.fullmatch(
        envelope["idempotency_key"]
    ):
        raise InvalidSpoolEnvelope("invalid-idempotency-key")
    _validate_timestamp(envelope.get("observed_at"))
    _validate_versions(envelope.get("versions"), full=event_type != "health")
    _validate_safety(envelope.get("safety"), event_type=event_type)
    if event_type in {"failure", "recovery"}:
        try:
            uuid.UUID(str(envelope.get("event_id")))
        except (ValueError, TypeError, AttributeError) as exc:
            raise InvalidSpoolEnvelope("invalid-event-id") from exc
        _validate_optional_hash(envelope.get("session_hash"), "session-hash")
        _validate_optional_hash(envelope.get("repo_hash"), "repo-hash")
        for key in ("tool_name", "operation_class"):
            value = envelope.get(key)
            if not isinstance(value, str) or not SAFE_NAME_RE.fullmatch(value):
                raise InvalidSpoolEnvelope(f"invalid-{key}")
    else:
        if not isinstance(envelope.get("event_id"), str) or not HEX_32_RE.fullmatch(
            envelope["event_id"]
        ):
            raise InvalidSpoolEnvelope("invalid-health-event-id")
        if str(envelope.get("status") or "") not in HEALTH_STATUSES:
            raise InvalidSpoolEnvelope("invalid-health-status")
        detail = envelope.get("detail_class")
        if not isinstance(detail, str) or detail not in HEALTH_DETAIL_CLASSES:
            raise InvalidSpoolEnvelope("invalid-health-detail")
    if event_type == "failure":
        required = failure_keys - {"event_type"}
        if not required <= set(envelope):
            raise InvalidSpoolEnvelope("missing-failure-field")
        for key in ("signature",):
            if not isinstance(envelope.get(key), str) or not HEX_64_RE.fullmatch(
                envelope[key]
            ):
                raise InvalidSpoolEnvelope(f"invalid-{key}")
        for key in ("turn_hash", "tool_call_hash"):
            _validate_optional_hash(envelope.get(key), key.replace("_", "-"))
        for key in (
            "tool_family", "outcome_class", "capture_mode",
        ):
            value = envelope.get(key)
            if not isinstance(value, str) or not SAFE_CLASS_RE.fullmatch(value):
                raise InvalidSpoolEnvelope(f"invalid-{key}")
        identity = envelope.get("error_identity")
        if not isinstance(identity, str) or not SAFE_ERROR_IDENTITY_RE.fullmatch(identity):
            raise InvalidSpoolEnvelope("invalid-error-identity")
        _validate_message_template(envelope.get("message_template"))
        completeness = envelope.get("capture_completeness")
        if (
            isinstance(completeness, bool)
            or not isinstance(completeness, (int, float))
            or not 0 <= completeness <= 1
        ):
            raise InvalidSpoolEnvelope("invalid-capture-completeness")
        environment = envelope.get("environment")
        if (
            not isinstance(environment, dict)
            or set(environment) != {"os_family", "shell_family", "permission_mode"}
            or any(
                not isinstance(item, str) or not SAFE_ENV_RE.fullmatch(item)
                for item in environment.values()
            )
        ):
            raise InvalidSpoolEnvelope("invalid-environment")
    return event_type


def canonicalize_spool_envelope(
    envelope: dict[str, Any],
    *,
    expected_type: str | None = None,
) -> dict[str, Any]:
    """Validate and rebuild an envelope from allowlisted scalar fields only."""
    event_type = validate_spool_envelope(envelope)
    if expected_type is not None and event_type != expected_type:
        raise InvalidSpoolEnvelope("unexpected-event-type")

    common = {
        "event_type": event_type,
        "event_id": str(envelope["event_id"]),
        "observed_at": str(envelope["observed_at"]),
        "idempotency_key": str(envelope["idempotency_key"]),
        "versions": {
            key: envelope["versions"][key]
            for key in ("schema", "collector", "sanitizer", "normalizer", "fingerprint")
            if key in envelope["versions"]
        },
        "safety": {
            key: envelope["safety"][key]
            for key in (
                "secret_scan", "raw_input_stored", "raw_output_stored",
                "redaction_or_truncation_applied", "repo_correlation",
            )
            if key in envelope["safety"]
        },
    }
    if event_type == "failure":
        common.update({
            "signature": str(envelope["signature"]),
            "session_hash": envelope.get("session_hash"),
            "turn_hash": envelope.get("turn_hash"),
            "tool_call_hash": envelope.get("tool_call_hash"),
            "repo_hash": envelope.get("repo_hash"),
            "tool_name": str(envelope["tool_name"]),
            "tool_family": str(envelope["tool_family"]),
            "operation_class": str(envelope["operation_class"]),
            "outcome_class": str(envelope["outcome_class"]),
            "error_identity": str(envelope["error_identity"]),
            "message_template": str(envelope["message_template"]),
            "capture_mode": str(envelope["capture_mode"]),
            "capture_completeness": envelope["capture_completeness"],
            "environment": {
                key: str(envelope["environment"][key])
                for key in ("os_family", "shell_family", "permission_mode")
            },
        })
    elif event_type == "recovery":
        common.update({
            "session_hash": envelope.get("session_hash"),
            "repo_hash": envelope.get("repo_hash"),
            "tool_name": str(envelope["tool_name"]),
            "operation_class": str(envelope["operation_class"]),
        })
    else:
        common.update({
            "status": str(envelope["status"]),
            "detail_class": str(envelope["detail_class"]),
        })
    return common


def process_spool_envelope(envelope: dict[str, Any]) -> str:
    """Authenticate, validate, and apply one Hook envelope exactly once."""
    envelope = canonicalize_spool_envelope(verify_spool_envelope_auth(envelope))
    event_type = str(envelope["event_type"])
    receipt = str(envelope.get("idempotency_key") or "")
    if not receipt:
        raise ValueError("spool-envelope-missing-idempotency-key")
    with connect(timeout=2.0) as conn:
        if conn.execute(
            "SELECT 1 FROM spool_receipts WHERE idempotency_key=?", (receipt,)
        ).fetchone():
            return "duplicate"
        reference_id: str | None = None
        if event_type == "failure":
            inserted = _insert_event_conn(conn, envelope, auth_verified=True)
            disposition = "inserted" if inserted else "duplicate"
            reference_id = str(envelope.get("event_id") or "") or None
        elif event_type == "recovery":
            recorded = _record_recovery_conn(
                conn,
                recovery_id=str(envelope["event_id"]),
                observed_at=str(envelope["observed_at"]),
                idempotency_key=receipt,
                session_hash=envelope.get("session_hash"),
                repo_hash=envelope.get("repo_hash"),
                tool_name=str(envelope.get("tool_name") or "")[:128],
                operation_class=str(envelope.get("operation_class") or "")[:128],
            )
            disposition = "recovery-recorded" if recorded else "recovery-unmatched"
            reference_id = str(envelope.get("event_id") or "") or None
        elif event_type == "health":
            conn.execute(
                "INSERT INTO collector_health(observed_at, status, detail_class) VALUES(?,?,?)",
                (
                    str(envelope.get("observed_at") or utc_now()),
                    str(envelope.get("status") or "degraded")[:32],
                    str(envelope.get("detail_class") or "hook-health")[:128],
                ),
            )
            disposition = "health-recorded"
        else:
            raise ValueError("spool-envelope-unknown-event-type")
        conn.execute(
            """
            INSERT INTO spool_receipts(
              idempotency_key, processed_at, event_type, disposition, reference_id
            ) VALUES(?,?,?,?,?)
            """,
            (receipt, utc_now(), event_type, disposition, reference_id),
        )
        conn.commit()
        return disposition


def spool_health_event(detail_class: str) -> None:
    """Best-effort, daily-deduplicated health event for fail-open hooks."""
    if detail_class not in HEALTH_DETAIL_CLASSES or disabled_path().exists():
        return
    day = datetime.now(timezone.utc).date().isoformat()
    detail = detail_class
    key = stable_hash(f"health\0{day}\0{detail}")
    envelope = {
        "event_type": "health",
        "event_id": key[:32],
        "observed_at": utc_now(),
        "idempotency_key": key,
        "status": "degraded",
        "detail_class": detail,
        "versions": {"schema": SCHEMA_VERSION, "collector": COLLECTOR_VERSION},
        "safety": {"raw_input_stored": False, "raw_output_stored": False},
    }
    key_bytes = identity_key_readonly()
    if key_bytes is None:
        return
    envelope = authenticate_spool_envelope(envelope, key_bytes)
    root = data_dir() / "spool"
    ensure_private_dir(root)
    final = root / f"health-{key[:24]}.json"
    if final.exists():
        return
    temp = root / f".health-{key[:24]}.{os.getpid()}.tmp"
    temp.write_text(json.dumps(envelope, sort_keys=True), encoding="utf-8")
    if disabled_path().exists():
        temp.unlink(missing_ok=True)
        return
    os.replace(temp, final)
    if disabled_path().exists():
        final.unlink(missing_ok=True)


def _rebuild_patterns_conn(conn: sqlite3.Connection) -> int:
    source = conn.execute(
        """
        SELECT e.*, er.review_status
        FROM events e
        LEFT JOIN event_reviews er ON er.event_id=e.event_id
        ORDER BY e.observed_at, e.event_id
        """
    ).fetchall()
    groups: dict[str, list[dict[str, Any]]] = {}
    for raw in source:
        event = dict(raw)
        if not is_actionable_event(event):
            continue
        _, key, _ = _pattern_identity(event)
        groups.setdefault(key, []).append(event)
    conn.execute("DELETE FROM pattern_events")
    conn.execute("DELETE FROM patterns")
    for rows in groups.values():
        _replace_pattern_group(conn, rows)
    mismatch = conn.execute(
        """
        SELECT COUNT(*) FROM patterns p
        WHERE p.incident_count != (
          SELECT COUNT(*) FROM pattern_events pe WHERE pe.pattern_id=p.pattern_id
        )
        """
    ).fetchone()[0]
    if mismatch:
        raise sqlite3.IntegrityError("pattern-link-count-mismatch")
    return len(groups)


def refresh_advice_cache() -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=ADVICE_MAX_AGE_DAYS)).isoformat(
        timespec="seconds"
    )
    with connect_readonly(timeout=2.0) as conn:
        rows = conn.execute(
            """
            SELECT p.pattern_id, p.repo_hash, p.tool_name, p.operation_class,
                   p.error_identity, p.incident_count, p.independent_sessions,
                   p.last_seen, p.status, p.quality_status,
                   SUM(CASE WHEN o.status='success' THEN 1 ELSE 0 END) recoveries
            FROM patterns p
            LEFT JOIN pattern_events pe ON pe.pattern_id=p.pattern_id
            LEFT JOIN intervention_outcomes o ON o.event_id=pe.event_id
            WHERE p.independent_sessions >= 2
              AND p.status IN ('observed','accepted')
              AND p.quality_status='eligible'
              AND p.last_seen >= ?
            GROUP BY p.pattern_id
            ORDER BY p.independent_sessions DESC, p.incident_count DESC, p.last_seen DESC
            LIMIT ?
            """,
            (cutoff, ADVICE_CACHE_MAX_PATTERNS),
        ).fetchall()
    cache = {
        "schema_version": ADVICE_CACHE_VERSION,
        "generated_at": utc_now(),
        "max_age_days": ADVICE_MAX_AGE_DAYS,
        "patterns": rows_to_dicts(rows),
    }
    root = data_dir()
    ensure_private_dir(root)
    final = advice_cache_path()
    temp = root / f".advice-cache.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    temp.write_text(json.dumps(cache, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(temp, final)
    try:
        final.chmod(0o600)
    except OSError:
        pass
    return len(cache["patterns"])


def rebuild_patterns() -> int:
    with connect(timeout=2.0) as conn:
        count = _rebuild_patterns_conn(conn)
        conn.commit()
    try:
        refresh_advice_cache()
    except (DatabaseUnavailable, OSError, sqlite3.Error, ValueError):
        try:
            record_health("degraded", "advice-cache-refresh-failed")
        except (OSError, sqlite3.Error):
            pass
    return count


def set_event_review(
    event_id: str, review_status: str, reason_class: str, review_source: str
) -> bool:
    if review_status not in {"accepted", "quarantined", "non-actionable"}:
        raise ValueError("invalid-review-status")
    with connect() as conn:
        event = conn.execute(
            """
            SELECT session_hash, repo_hash, tool_name, operation_class
            FROM events
            WHERE event_id=?
            """,
            (event_id,),
        ).fetchone()
        if not event:
            return False
        conn.execute(
            """
            INSERT INTO event_reviews(
              event_id, review_status, reason_class, reviewed_at, review_source
            ) VALUES(?,?,?,?,?)
            ON CONFLICT(event_id) DO UPDATE SET
              review_status=excluded.review_status,
              reason_class=excluded.reason_class,
              reviewed_at=excluded.reviewed_at,
              review_source=excluded.review_source
            """,
            (event_id, review_status, reason_class[:64], utc_now(), review_source[:64]),
        )
        _reconcile_recovery_scope_conn(
            conn,
            session_hash=event["session_hash"],
            repo_hash=event["repo_hash"],
            tool_name=str(event["tool_name"]),
            operation_class=str(event["operation_class"]),
        )
        _rebuild_patterns_conn(conn)
        conn.commit()
    refresh_advice_cache()
    return True


def add_learning_case(case: dict[str, Any]) -> None:
    safe_evidence_refs = sorted(
        {
            pseudonymize_case_evidence_ref(item)
            for item in case["evidence_refs"]
        }
    )
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO learning_cases(
              case_id, created_at, title, category, scope, root_cause_class,
              remediation_class, verification_status, evidence_refs,
              target_fingerprint, status
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                case["case_id"], case["created_at"], case["title"], case["category"],
                case["scope"], case["root_cause_class"], case["remediation_class"],
                case["verification_status"],
                json.dumps(
                    safe_evidence_refs,
                    ensure_ascii=True,
                    separators=(",", ":"),
                ),
                case["target_fingerprint"], case["status"],
            ),
        )
        conn.commit()


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]
