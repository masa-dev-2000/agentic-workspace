from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sqlite3
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from spool_contract import (
    MAX_SPOOL_BYTES,
    InvalidSpoolEnvelope,
    external_runtime_lock_path,
    existing_key,
    purge_tombstone_path,
    state_root_fingerprint,
    validate_record,
)

TYPES = {"complaint", "request", "correction", "preference"}
IMPACTS = {"low", "medium", "high"}
EXPLICITNESS = {"explicit", "inferred"}
CAPTURE_MODES = {"hook", "model", "manual", "import"}
DIRECT_CAPTURE_MODES = {"model", "manual", "import"}
OUTCOME_STATUS = {"implemented", "partial", "rejected", "deferred", "unknown"}
VERIFICATION = {"user-confirmed", "observed", "not-verified"}
SATISFACTION = {"improved", "unchanged", "worse", "unknown"}
SOURCE_KINDS = {"user", "third-party", "system"}
SUBJECT_KINDS = {"user", "skill", "workflow", "project", "product", "organization", "unknown"}
VALENCES = {"positive", "negative", "mixed", "neutral", "unknown"}
PRIVACY_CLASSES = {"private", "restricted", "confidential", "public"}
CONSENT_BASES = {"direct-user", "user-provided", "authorized-import", "none"}
DIRECTNESS = {"direct", "reported", "inferred"}
RELIABILITY = {"low", "medium", "high", "unknown"}
EVIDENCE_ROLES = {"support", "counter", "boundary"}
SURFACES = {"observation", "dictionary", "agents", "existing-skill", "skill-edge", "hook", "runtime", "new-skill"}
EXPERIMENT_OUTCOMES = {"improved", "unchanged", "worse", "inconclusive"}
EXPERIMENT_VERIFICATION = {"user-confirmed", "verified", "observed", "not-verified"}
VERIFICATION_EVIDENCE_CLASSES = {"artifact", "external-state", "human-confirmation"}
VERIFICATION_CLASS_BY_LEVEL = {
    "verified": {"artifact", "external-state"},
    "user-confirmed": {"human-confirmation"},
}
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
OPAQUE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#-]{0,255}$")
SCHEMA_VERSION = 4
PRIVACY_REPAIR_VERSION = "3"
PRIVACY_REPAIR_PENDING = "pending-v3"
PROVENANCE_TRUST = {"trusted", "legacy-unverified"}
STATE_MARKER_NAME = ".feedback-learning-state.json"
STATE_MARKER_VERSION = 1
PURGE_CONFIRMATION = "DELETE-FEEDBACK-LEARNING-DATA"

SECRET_RE = re.compile(r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[:=]\s*[^\s,;]+")
JSON_SECRET_RE = re.compile(
    r"""(?ix)
    (?P<key>["'](?:api[_-]?key|access[_-]?token|refresh[_-]?token|
      client[_-]?secret|private[_-]?key|token|secret|password|authorization)["'])
    \s*:\s*
    (?P<value>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^,\s}\]]+)
    """
)
BEARER_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+")
URL_QUERY_RE = re.compile(r"(https?://[^\s?#]+)[?#][^\s]+", re.I)
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
WIN_HOME_RE = re.compile(r"(?i)\b[A-Z]:\\Users\\[^\\\s]+")
POSIX_HOME_RE = re.compile(r"(?<!\w)/(?:home|Users)/[^/\s]+")
UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f-]{27,}\b", re.I)
LONG_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_+/=-]{28,}\b")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
SLUG_RE = re.compile(r"[^a-z0-9-]+")


class ClosingConnection(sqlite3.Connection):
    """Commit or roll back, then actually release the Windows file handle."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class PrivacyRepairPendingError(RuntimeError):
    """Legacy prompt-like data was rewritten but WAL cleanup is not complete."""


class StateSafetyError(RuntimeError):
    error_class = "unsafe-feedback-state"


class InvalidHmacKeyError(StateSafetyError):
    error_class = "invalid-hmac-key"


class InvalidStateMarkerError(StateSafetyError):
    error_class = "invalid-state-marker"


class PurgedStateError(StateSafetyError):
    error_class = "state-purged"


class UnsafePurgeTargetError(StateSafetyError):
    error_class = "unsafe-purge-target"


@contextmanager
def nonblocking_process_lock(path: Path):
    """Acquire one local-filesystem drainer lock without waiting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    acquired = False
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                acquired = True
            except OSError:
                acquired = False
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except (BlockingIOError, OSError):
                acquired = False
        yield acquired
    finally:
        if acquired:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_time(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def data_home() -> Path:
    override = os.environ.get("CODEX_FEEDBACK_LEARNING_HOME")
    if override:
        return Path(override).expanduser().resolve()
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    return (codex_home / "feedback-learning").resolve()


def sanitize_text(value: Any, limit: int = 512) -> str:
    text = "" if value is None else str(value)
    text = CONTROL_RE.sub(" ", text)
    text = JSON_SECRET_RE.sub(
        lambda match: f'{match.group("key")}:"[REDACTED]"',
        text,
    )
    text = SECRET_RE.sub(lambda m: f"{m.group(1)}=[REDACTED]", text)
    text = BEARER_RE.sub("Bearer [REDACTED]", text)
    text = URL_QUERY_RE.sub(r"\1?[REDACTED]", text)
    text = EMAIL_RE.sub("[EMAIL]", text)
    text = WIN_HOME_RE.sub(r"C:\\Users\\[USER]", text)
    text = POSIX_HOME_RE.sub("/home/[USER]", text)
    text = UUID_RE.sub("[UUID]", text)
    text = LONG_TOKEN_RE.sub("[TOKEN]", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def slug(value: str, fallback: str = "general") -> str:
    clean = SLUG_RE.sub("-", sanitize_text(value, 80).lower()).strip("-")
    return clean[:64] or fallback


def normalize_target_hashes(value: dict[str, str]) -> dict[str, str]:
    if not value:
        raise ValueError("at least one target hash is required")
    normalized: dict[str, str] = {}
    for target, digest in value.items():
        clean_target = sanitize_text(target, 160)
        clean_digest = str(digest or "").strip().lower()
        if not clean_target or not HASH_RE.fullmatch(clean_digest):
            raise ValueError("target hashes must map non-empty target IDs to 64 lowercase hex characters")
        normalized[clean_target] = clean_digest
    return dict(sorted(normalized.items()))


def default_state_root() -> Path:
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    return (codex_home.expanduser().resolve() / "feedback-learning").resolve()


class FeedbackStore:
    def __init__(
        self,
        root: Path | None = None,
        governance_root: Path | None = None,
        *,
        test_purge_authority: bool = False,
    ):
        self.root = (root or data_home()).resolve()
        self.db_path = self.root / "feedback.sqlite3"
        self.key_path = self.root / "hmac.key"
        self.spool_path = self.root / "spool"
        self.drain_lock_path = external_runtime_lock_path(self.root)
        self.tombstone_path = purge_tombstone_path(self.root)
        self.state_marker_path = self.root / STATE_MARKER_NAME
        self.staging = self.root / "staging"
        self.test_purge_authority = bool(test_purge_authority)
        configured_governance = os.environ.get("CODEX_SKILL_GOVERNANCE_ROOT")
        self.governance_root = (
            governance_root
            or (Path(configured_governance).expanduser() if configured_governance else Path(__file__).resolve().parents[2])
        ).resolve()

    def _marker_scope(self) -> str:
        if self.root == default_state_root():
            return "production"
        if self.test_purge_authority:
            return "test-authorized"
        return "custom-no-purge"

    def _expected_state_marker(self) -> dict[str, Any]:
        return {
            "kind": "feedback-learning-state",
            "version": STATE_MARKER_VERSION,
            "root_fingerprint": state_root_fingerprint(self.root),
            "scope": self._marker_scope(),
        }

    def _validate_state_marker(self) -> dict[str, Any]:
        try:
            marker = json.loads(self.state_marker_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise InvalidStateMarkerError("state-marker-missing") from exc
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise InvalidStateMarkerError("state-marker-unreadable") from exc
        if marker != self._expected_state_marker():
            raise InvalidStateMarkerError("state-marker-mismatch")
        return marker

    def _ensure_state_marker(self) -> None:
        expected = self._expected_state_marker()
        if not self.state_marker_path.exists():
            try:
                with self.state_marker_path.open("x", encoding="utf-8") as handle:
                    json.dump(expected, handle, ensure_ascii=True, sort_keys=True)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            except FileExistsError:
                pass
        self._validate_state_marker()

    def _read_valid_key(self) -> bytes:
        try:
            encoded = self.key_path.read_text(encoding="ascii").strip()
        except FileNotFoundError as exc:
            raise InvalidHmacKeyError("hmac-key-missing") from exc
        except (OSError, UnicodeError) as exc:
            raise InvalidHmacKeyError("hmac-key-unreadable") from exc
        if not re.fullmatch(r"[0-9a-f]{64}", encoded):
            raise InvalidHmacKeyError("hmac-key-must-be-64-lowercase-hex")
        try:
            key = bytes.fromhex(encoded)
        except ValueError as exc:
            raise InvalidHmacKeyError("hmac-key-invalid-hex") from exc
        if len(key) != 32:
            raise InvalidHmacKeyError("hmac-key-must-decode-to-32-bytes")
        return key

    def initialize(self) -> None:
        if self.tombstone_path.exists():
            raise PurgedStateError("state-root-is-tombstoned")
        self.root.mkdir(parents=True, exist_ok=True)
        self._ensure_state_marker()
        if not self.key_path.exists():
            try:
                with self.key_path.open("x", encoding="ascii") as handle:
                    handle.write(secrets.token_hex(32))
                    handle.flush()
                    os.fsync(handle.fileno())
                try:
                    self.key_path.chmod(0o600)
                except OSError:
                    pass
            except FileExistsError:
                pass
        self._read_valid_key()
        self.staging.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(SCHEMA)
            self._migrate(db)
            db.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('enabled','1')")
            self._privacy_repair_v3(db)

    def _migrate(self, db: sqlite3.Connection) -> None:
        """Apply additive schema changes before the explicit privacy repair."""
        existing = {row[1] for row in db.execute("PRAGMA table_info(feedback_events)")}
        additions = {
            "source_kind": "TEXT NOT NULL DEFAULT 'user'",
            "speaker_hash": "TEXT NOT NULL DEFAULT ''",
            "channel": "TEXT NOT NULL DEFAULT 'conversation'",
            "subject_kind": "TEXT NOT NULL DEFAULT 'unknown'",
            "valence": "TEXT NOT NULL DEFAULT 'unknown'",
            "privacy_class": "TEXT NOT NULL DEFAULT 'private'",
            "consent_basis": "TEXT NOT NULL DEFAULT 'direct-user'",
            "directness": "TEXT NOT NULL DEFAULT 'direct'",
            "reliability": "TEXT NOT NULL DEFAULT 'high'",
            "raw_ref": "TEXT NOT NULL DEFAULT ''",
            "evidence_role": "TEXT NOT NULL DEFAULT 'support'",
            "persistence_requested": "INTEGER NOT NULL DEFAULT 0",
            "provenance_trust": "TEXT NOT NULL DEFAULT 'legacy-unverified'",
        }
        for name, definition in additions.items():
            if name not in existing:
                db.execute(f"ALTER TABLE feedback_events ADD COLUMN {name} {definition}")
        db.executescript(EVOLUTION_SCHEMA)
        approval_columns = {row[1] for row in db.execute("PRAGMA table_info(approvals)")}
        if "changeset_hash" not in approval_columns:
            db.execute("ALTER TABLE approvals ADD COLUMN changeset_hash TEXT NOT NULL DEFAULT ''")
        verification_columns = {
            row[1] for row in db.execute("PRAGMA table_info(verification_evidence)")
        }
        if (
            verification_columns
            and "provenance_trust" not in verification_columns
        ):
            db.execute(
                """ALTER TABLE verification_evidence
                   ADD COLUMN provenance_trust TEXT NOT NULL
                   DEFAULT 'legacy-unverified'"""
            )

    @staticmethod
    def _meta_value(db: sqlite3.Connection, key: str) -> str | None:
        row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return str(row[0]) if row else None

    @staticmethod
    def _set_meta(db: sqlite3.Connection, key: str, value: str) -> None:
        db.execute(
            """INSERT INTO meta(key,value) VALUES(?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (key, value),
        )

    @staticmethod
    def _checkpoint_truncate(db: sqlite3.Connection) -> bool:
        row = db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        return bool(row and int(row[0]) == 0)

    def _privacy_repair_v3(self, db: sqlite3.Connection) -> None:
        """Remove legacy Hook prompt-like summaries before publishing schema v3."""
        state = self._meta_value(db, "privacy_repair_version")
        if state == PRIVACY_REPAIR_VERSION:
            self._set_meta(db, "schema_version", str(SCHEMA_VERSION))
            return

        if state != PRIVACY_REPAIR_PENDING:
            db.execute("PRAGMA secure_delete=ON")
            secure = db.execute("PRAGMA secure_delete").fetchone()
            if not secure or int(secure[0]) != 1:
                raise RuntimeError("secure-delete-unavailable")
            db.execute("DROP TRIGGER IF EXISTS feedback_events_no_update")
            db.execute(
                """UPDATE feedback_events
                   SET expectation_template='', observed_template='', desired_template=''
                   WHERE capture_mode='hook'
                     AND (expectation_template<>'' OR observed_template<>'' OR desired_template<>'')"""
            )
            db.execute(
                """CREATE TRIGGER IF NOT EXISTS feedback_events_no_update
                   BEFORE UPDATE ON feedback_events
                   BEGIN SELECT RAISE(ABORT,'feedback_events are immutable'); END"""
            )
            self._set_meta(db, "privacy_repair_version", PRIVACY_REPAIR_PENDING)
            db.commit()
        else:
            # initialize() may have issued harmless INSERT OR IGNORE statements
            # before reaching the pending state. End that transaction before a
            # checkpoint so a retry can actually finalize the prior rewrite.
            db.commit()

        if not self._checkpoint_truncate(db):
            raise PrivacyRepairPendingError("privacy-repair-pending")

        self._set_meta(db, "privacy_repair_version", PRIVACY_REPAIR_VERSION)
        self._set_meta(db, "schema_version", str(SCHEMA_VERSION))
        db.commit()
        if not self._checkpoint_truncate(db):
            self._set_meta(db, "privacy_repair_version", PRIVACY_REPAIR_PENDING)
            db.commit()
            raise PrivacyRepairPendingError("privacy-repair-finalization-pending")

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=5, factory=ClosingConnection)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=5000")
        return db

    def digest(self, value: Any) -> str:
        key = self._read_valid_key()
        return hmac.new(key, str(value or "").encode("utf-8"), hashlib.sha256).hexdigest()

    def maturity_gate_status(self) -> str:
        gate_path = self.governance_root / "skill-maturity-gate.json"
        try:
            payload = json.loads(gate_path.read_text(encoding="utf-8"))
            return "open" if payload.get("status") == "open" else "frozen"
        except (OSError, ValueError, TypeError):
            return "frozen"

    def enabled(self) -> bool:
        self.initialize()
        if (self.root / "disabled").exists():
            return False
        with self.connect() as db:
            row = db.execute("SELECT value FROM meta WHERE key='enabled'").fetchone()
        return bool(row and row[0] == "1")

    def set_enabled(self, enabled: bool) -> None:
        self.initialize()
        marker = self.root / "disabled"
        if enabled:
            marker.unlink(missing_ok=True)
        else:
            marker.write_text("disabled by user\n", encoding="utf-8")
        with self.connect() as db:
            db.execute("INSERT INTO meta(key,value) VALUES('enabled',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("1" if enabled else "0",))

    def add_feedback(self, record: dict[str, Any]) -> tuple[str | None, bool]:
        self.initialize()
        if not self.enabled():
            return None, False
        kind = record.get("feedback_type", "request")
        impact = record.get("impact", "medium")
        explicitness = record.get("explicitness", "explicit")
        capture_mode = record.get("capture_mode", "manual")
        if (
            kind not in TYPES
            or impact not in IMPACTS
            or explicitness not in EXPLICITNESS
            or capture_mode not in DIRECT_CAPTURE_MODES
        ):
            raise ValueError("invalid feedback classification")
        source_kind = record.get("source_kind") or "user"
        subject_kind = record.get("subject_kind") or "unknown"
        valence = record.get("valence") or "unknown"
        privacy_class = record.get("privacy_class") or "private"
        consent_basis = record.get("consent_basis") or ("direct-user" if source_kind == "user" else "none")
        directness = record.get("directness") or ("direct" if source_kind == "user" else "reported")
        reliability = record.get("reliability") or ("high" if source_kind == "user" else "unknown")
        evidence_role = record.get("evidence_role") or "support"
        classifications = (
            (source_kind, SOURCE_KINDS, "source kind"),
            (subject_kind, SUBJECT_KINDS, "subject kind"),
            (valence, VALENCES, "valence"),
            (privacy_class, PRIVACY_CLASSES, "privacy class"),
            (consent_basis, CONSENT_BASES, "consent basis"),
            (directness, DIRECTNESS, "directness"),
            (reliability, RELIABILITY, "reliability"),
            (evidence_role, EVIDENCE_ROLES, "evidence role"),
        )
        for value, allowed, label in classifications:
            if value not in allowed:
                raise ValueError(f"invalid {label}")
        forbidden_raw = ("raw", "raw_text", "raw_feedback", "transcript", "prompt", "response")
        if source_kind == "third-party" and any(record.get(field) for field in forbidden_raw):
            raise ValueError("third-party raw content is not accepted; store only metadata and an authorized opaque raw_ref")
        subject = slug(record.get("subject_class", "general"))
        expectation = sanitize_text(record.get("expectation_template"), 320)
        observed = sanitize_text(record.get("observed_template"), 320)
        desired = sanitize_text(record.get("desired_template"), 320)
        if not any((expectation, observed, desired)):
            raise ValueError("at least one sanitized template is required")
        theme_key = slug(record.get("theme_key") or f"{kind}-{subject}")
        speaker_hash = self.digest(record.get("speaker_id")) if record.get("speaker_id") else ""
        raw_ref = sanitize_text(record.get("raw_ref"), 256)
        if raw_ref and not OPAQUE_REF_RE.fullmatch(raw_ref):
            raise ValueError("raw_ref must be an opaque identifier or URI without free text")
        if source_kind == "third-party" and raw_ref and consent_basis not in {"user-provided", "authorized-import"}:
            raise ValueError("third-party raw_ref requires user-provided or authorized-import consent")
        channel = slug(record.get("channel", "conversation"))
        persistence_requested = 1 if bool(record.get("persistence_requested")) else 0
        observed_at = parse_time(record.get("observed_at") or now()).isoformat(timespec="seconds")
        signature_material = canonical_json([
            kind,
            subject,
            theme_key,
            expectation,
            observed,
            desired,
            source_kind,
            speaker_hash,
            channel,
            subject_kind,
            valence,
            privacy_class,
            consent_basis,
            directness,
            reliability,
            raw_ref,
            evidence_role,
            persistence_requested,
        ])
        signature = self.digest(signature_material)
        turn_hash = self.digest(record.get("turn_id")) if record.get("turn_id") else ""
        idempotency_key = sanitize_text(record.get("idempotency_key"), 128) or self.digest(f"{turn_hash}:{signature}")
        feedback_id = "fb_" + uuid.uuid4().hex
        envelope = canonical_json({
            "type": kind,
            "subject": subject,
            "theme_key": theme_key,
            "impact": impact,
            "source_kind": source_kind,
            "channel": channel,
            "subject_kind": subject_kind,
            "valence": valence,
            "privacy_class": privacy_class,
            "consent_basis": consent_basis,
            "directness": directness,
            "reliability": reliability,
            "evidence_role": evidence_role,
            "persistence_requested": bool(persistence_requested),
            "raw_ref_present": bool(raw_ref),
        })
        values = (feedback_id, observed_at, idempotency_key, signature,
                  self.digest(record.get("session_id")) if record.get("session_id") else "",
                  turn_hash, self.digest(record.get("repo")) if record.get("repo") else "",
                  kind, subject, theme_key, impact, explicitness, capture_mode,
                  expectation, observed, desired, envelope, now(),
                  source_kind, speaker_hash, channel, subject_kind, valence, privacy_class,
                  consent_basis, directness, reliability, raw_ref, evidence_role,
                  persistence_requested, "trusted")
        with self.connect() as db:
            cur = db.execute("""INSERT OR IGNORE INTO feedback_events
                (feedback_id,observed_at,idempotency_key,signature,session_hash,turn_hash,repo_hash,
                 feedback_type,subject_class,theme_key,impact,explicitness,capture_mode,
                 expectation_template,observed_template,desired_template,event_json,created_at,
                 source_kind,speaker_hash,channel,subject_kind,valence,privacy_class,consent_basis,
                 directness,reliability,raw_ref,evidence_role,persistence_requested,provenance_trust)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", values)
            if cur.rowcount == 0:
                row = db.execute("SELECT feedback_id FROM feedback_events WHERE idempotency_key=?", (idempotency_key,)).fetchone()
                return (row[0] if row else None), False
        return feedback_id, True

    def _insert_hook_feedback(
        self,
        db: sqlite3.Connection,
        record: dict[str, Any],
    ) -> tuple[str, bool]:
        """Persist one already-authenticated, body-free Hook classification."""
        feedback_id = "fb_" + self.digest(record["event_id"])[:32]
        envelope = canonical_json({
            "type": record["feedback_type"],
            "subject": record["subject_class"],
            "theme_key": record["theme_key"],
            "impact": record["impact"],
            "source_kind": record["source_kind"],
            "subject_kind": record["subject_kind"],
            "valence": record["valence"],
            "evidence_role": record["evidence_role"],
            "persistence_requested": record["persistence_requested"],
            "body_free_hook": True,
        })
        signature = self.digest(canonical_json([
            record["feedback_type"],
            record["subject_class"],
            record["theme_key"],
            record["reaction_signature"],
        ]))
        values = (
            feedback_id,
            record["observed_at"],
            record["event_id"],
            signature,
            record["session_hash"],
            record["turn_hash"],
            record["repo_hash"],
            record["feedback_type"],
            record["subject_class"],
            record["theme_key"],
            record["impact"],
            record["explicitness"],
            "hook",
            "",
            "",
            "",
            envelope,
            now(),
            record["source_kind"],
            "",
            "conversation",
            record["subject_kind"],
            record["valence"],
            "private",
            "direct-user",
            "direct",
            "high",
            "",
            record["evidence_role"],
            int(record["persistence_requested"]),
            "trusted",
        )
        cursor = db.execute(
            """INSERT OR IGNORE INTO feedback_events(
                 feedback_id,observed_at,idempotency_key,signature,
                 session_hash,turn_hash,repo_hash,feedback_type,subject_class,
                 theme_key,impact,explicitness,capture_mode,
                 expectation_template,observed_template,desired_template,
                 event_json,created_at,source_kind,speaker_hash,channel,
                 subject_kind,valence,privacy_class,consent_basis,directness,
                 reliability,raw_ref,evidence_role,persistence_requested,
                 provenance_trust
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            values,
        )
        if cursor.rowcount:
            return feedback_id, True
        row = db.execute(
            "SELECT feedback_id FROM feedback_events WHERE idempotency_key=?",
            (record["event_id"],),
        ).fetchone()
        return (str(row[0]) if row else feedback_id), False

    def _quarantine_spool_file(
        self,
        path: Path,
        *,
        reason_class: str,
        size_bytes: int,
        raw: bytes | None = None,
    ) -> bool:
        """Replace an untrusted body with bounded rejection metadata."""
        nonce = uuid.uuid4().hex
        metadata = {
            "version": 1,
            "rejected_at": now(),
            "reason_class": slug(reason_class, "invalid-envelope"),
            "size_bytes": max(0, int(size_bytes)),
            "content_sha256": hashlib.sha256(raw).hexdigest() if raw is not None else None,
            "source_name_sha256": hashlib.sha256(
                path.name.encode("utf-8", "replace")
            ).hexdigest(),
        }
        encoded = json.dumps(
            metadata,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        temp = self.spool_path / f".rejected-{nonce}.{os.getpid()}.tmp"
        rejected = self.spool_path / f"rejected-{nonce}.rejected"
        try:
            with temp.open("xb") as handle:
                handle.write(encoded)
                handle.flush()
            os.replace(temp, path)
            os.replace(path, rejected)
            return True
        except OSError:
            return False
        finally:
            temp.unlink(missing_ok=True)

    def pending_spool_count(self) -> int:
        try:
            return (
                sum(1 for _ in self.spool_path.glob("*.json"))
                if self.spool_path.is_dir()
                else 0
            )
        except OSError:
            return 0

    def drain_spool(
        self,
        *,
        limit: int = 500,
        max_seconds: float = 1.0,
    ) -> dict[str, int]:
        """Authenticate and apply queued Hook envelopes under one drainer lock."""
        result = {
            "processed": 0,
            "duplicates": 0,
            "rejected": 0,
            "deferred": 0,
            "busy": 0,
        }
        with nonblocking_process_lock(self.drain_lock_path) as acquired:
            if not acquired:
                result["busy"] = 1
                result["deferred"] = self.pending_spool_count()
                return result
            self.initialize()
            if not self.spool_path.is_dir():
                return result
            key = existing_key(self.key_path)
            if key is None:
                result["deferred"] = self.pending_spool_count()
                return result
            deadline = time.monotonic() + max(0.05, max_seconds)
            files = sorted(self.spool_path.glob("*.json"))[: max(1, min(limit, 5000))]
            for index, path in enumerate(files):
                if time.monotonic() >= deadline:
                    result["deferred"] += len(files) - index
                    break
                raw = b""
                try:
                    size = path.stat().st_size
                    if size > MAX_SPOOL_BYTES:
                        if self._quarantine_spool_file(
                            path,
                            reason_class="oversized-record",
                            size_bytes=size,
                        ):
                            result["rejected"] += 1
                            continue
                        result["deferred"] += 1
                        break
                    raw = path.read_bytes()
                    if len(raw) > MAX_SPOOL_BYTES:
                        raise InvalidSpoolEnvelope("oversized-record")
                    record = validate_record(json.loads(raw.decode("utf-8")), key)
                    with self.connect() as db:
                        duplicate = bool(
                            db.execute(
                                "SELECT 1 FROM spool_receipts WHERE event_id=?",
                                (record["event_id"],),
                            ).fetchone()
                        )
                        if not duplicate:
                            feedback_id, _ = self._insert_hook_feedback(db, record)
                            db.execute(
                                """INSERT INTO spool_receipts(
                                     event_id,received_at,feedback_id
                                   ) VALUES(?,?,?)""",
                                (record["event_id"], now(), feedback_id),
                            )
                    path.unlink()
                    result["duplicates" if duplicate else "processed"] += 1
                except json.JSONDecodeError:
                    accepted = self._quarantine_spool_file(
                        path,
                        reason_class="malformed-json",
                        size_bytes=len(raw),
                        raw=raw,
                    )
                    result["rejected" if accepted else "deferred"] += 1
                    if not accepted:
                        break
                except UnicodeDecodeError:
                    accepted = self._quarantine_spool_file(
                        path,
                        reason_class="invalid-utf8",
                        size_bytes=len(raw),
                        raw=raw,
                    )
                    result["rejected" if accepted else "deferred"] += 1
                    if not accepted:
                        break
                except (InvalidSpoolEnvelope, TypeError, ValueError) as exc:
                    accepted = self._quarantine_spool_file(
                        path,
                        reason_class=str(exc),
                        size_bytes=len(raw),
                        raw=raw,
                    )
                    result["rejected" if accepted else "deferred"] += 1
                    if not accepted:
                        break
                except (OSError, sqlite3.Error):
                    result["deferred"] += 1
                    break
        return result

    def add_outcome(self, feedback_id: str, action_class: str, status: str, verification: str, satisfaction: str, notes: str = "") -> str:
        if status not in OUTCOME_STATUS or verification not in VERIFICATION or satisfaction not in SATISFACTION:
            raise ValueError("invalid outcome classification")
        self.initialize()
        outcome_id = "out_" + uuid.uuid4().hex
        with self.connect() as db:
            db.execute("INSERT INTO response_outcomes VALUES(?,?,?,?,?,?,?,?,?)", (outcome_id, feedback_id, now(), slug(action_class), status, verification, satisfaction, sanitize_text(notes, 320), now()))
        return outcome_id

    def rebuild(self) -> int:
        self.initialize()
        with self.connect() as db:
            db.execute("DELETE FROM theme_events")
            db.execute("DELETE FROM themes")
            rows = db.execute("""SELECT theme_key, subject_class, feedback_type, COUNT(*) incidents,
                COUNT(DISTINCT NULLIF(session_hash,'')) sessions, MIN(observed_at) first_seen,
                MAX(observed_at) last_seen,
                MAX(CASE impact WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END) severity
                FROM feedback_events WHERE provenance_trust='trusted'
                GROUP BY theme_key,subject_class,feedback_type""").fetchall()
            for row in rows:
                theme_id = "th_" + self.digest(f"{row['theme_key']}:{row['feedback_type']}")[:20]
                db.execute("INSERT INTO themes VALUES(?,?,?,?,?,?,?,?,?,?)", (theme_id, row["theme_key"], row["subject_class"], row["feedback_type"], row["incidents"], row["sessions"], row["first_seen"], row["last_seen"], row["severity"], "observed"))
                db.execute("""INSERT INTO theme_events
                    SELECT ?, feedback_id FROM feedback_events
                    WHERE theme_key=? AND feedback_type=? AND provenance_trust='trusted'""",
                    (theme_id, row["theme_key"], row["feedback_type"]))
        return len(rows)

    def sync_signals(self) -> int:
        """Materialize immutable LearningSignals from immutable feedback evidence."""
        self.initialize()
        inserted = 0
        with self.connect() as db:
            rows = db.execute("""SELECT * FROM feedback_events
                WHERE provenance_trust='trusted'
                  AND feedback_id NOT IN (SELECT feedback_id FROM learning_signals)
                ORDER BY observed_at,feedback_id""").fetchall()
            for row in rows:
                signal_id = "sig_" + self.digest(row["feedback_id"])[:20]
                refs = [f"feedback:{row['feedback_id']}"]
                if row["raw_ref"]:
                    refs.append(f"raw:{row['raw_ref']}")
                severity = {"low": 1, "medium": 2, "high": 3}[row["impact"]]
                cur = db.execute("""INSERT OR IGNORE INTO learning_signals(
                    signal_id,feedback_id,observed_at,signal_type,theme_key,subject_class,
                    session_hash,severity,evidence_role,persistence_requested,evidence_refs_json,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    signal_id,
                    row["feedback_id"],
                    row["observed_at"],
                    row["feedback_type"],
                    row["theme_key"],
                    row["subject_class"],
                    row["session_hash"],
                    severity,
                    row["evidence_role"],
                    row["persistence_requested"],
                    canonical_json(refs),
                    now(),
                ))
                inserted += max(0, cur.rowcount)
        return inserted

    def build_patterns(self, window_days: int = 90) -> list[dict[str, Any]]:
        """Build counter-aware patterns; frequency discovers candidates but never validates them."""
        if window_days < 1 or window_days > 3650:
            raise ValueError("window_days must be between 1 and 3650")
        self.sync_signals()
        cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
        with self.connect() as db:
            rows = db.execute("""SELECT s.*,f.expectation_template,f.observed_template,f.desired_template
                FROM learning_signals s JOIN feedback_events f ON f.feedback_id=s.feedback_id
                WHERE f.provenance_trust='trusted'
                ORDER BY s.observed_at,s.signal_id""").fetchall()
            grouped: dict[str, list[sqlite3.Row]] = {}
            for row in rows:
                grouped.setdefault(row["theme_key"], []).append(row)
            active_ids: set[str] = set()
            for pattern_key, evidence in grouped.items():
                pattern_id = "pat_" + self.digest(pattern_key)[:20]
                active_ids.add(pattern_id)
                supports = [row for row in evidence if row["evidence_role"] == "support"]
                counters = [row for row in evidence if row["evidence_role"] == "counter"]
                boundaries = [row for row in evidence if row["evidence_role"] == "boundary"]
                recent_supports = [row for row in supports if parse_time(row["observed_at"]) >= cutoff]
                sessions = {row["session_hash"] for row in recent_supports if row["session_hash"]}
                persistent = any(bool(row["persistence_requested"]) for row in supports)
                severity = max((row["severity"] for row in supports), default=1)
                if persistent or len(sessions) >= 2:
                    eligibility = "proposal-eligible"
                    reason = "explicit-persistence" if persistent else f"{len(sessions)}-independent-sessions-within-{window_days}-days"
                elif severity >= 3:
                    eligibility = "review-eligible"
                    reason = "high-severity-single-signal-review-only"
                else:
                    eligibility = "observed"
                    reason = "needs-explicit-persistence-or-two-independent-recent-sessions"
                prior = db.execute("SELECT status FROM improvement_patterns WHERE pattern_id=?", (pattern_id,)).fetchone()
                if counters:
                    status = "review-required"
                else:
                    status = prior["status"] if prior and prior["status"] == "validated" else "observed"
                support_refs = [f"signal:{row['signal_id']}" for row in supports]
                counter_refs = [f"signal:{row['signal_id']}" for row in counters]
                boundary_refs = [f"signal:{row['signal_id']}" for row in boundaries]
                subject = supports[0]["subject_class"] if supports else evidence[0]["subject_class"]
                db.execute("""INSERT INTO improvement_patterns(
                    pattern_id,pattern_key,title,status,eligibility,eligibility_reason,severity,
                    independent_sessions_90d,support_refs_json,counter_refs_json,boundary_refs_json,
                    first_seen,last_seen,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(pattern_id) DO UPDATE SET
                    title=excluded.title,
                    eligibility=excluded.eligibility,
                    eligibility_reason=excluded.eligibility_reason,
                    severity=excluded.severity,
                    independent_sessions_90d=excluded.independent_sessions_90d,
                    support_refs_json=excluded.support_refs_json,
                    counter_refs_json=excluded.counter_refs_json,
                    boundary_refs_json=excluded.boundary_refs_json,
                    first_seen=excluded.first_seen,
                    last_seen=excluded.last_seen,
                    updated_at=excluded.updated_at""", (
                    pattern_id,
                    pattern_key,
                    f"Improve {subject}",
                    status,
                    eligibility,
                    reason,
                    severity,
                    len(sessions),
                    canonical_json(support_refs),
                    canonical_json(counter_refs),
                    canonical_json(boundary_refs),
                    min(row["observed_at"] for row in evidence),
                    max(row["observed_at"] for row in evidence),
                    now(),
                    now(),
                ))
            if active_ids:
                placeholders = ",".join("?" for _ in active_ids)
                db.execute(f"DELETE FROM improvement_patterns WHERE pattern_id NOT IN ({placeholders}) AND pattern_id NOT IN (SELECT pattern_id FROM improvement_proposals)", tuple(active_ids))
        return self.rows("SELECT * FROM improvement_patterns ORDER BY last_seen DESC,pattern_id")

    def _route_surface(
        self,
        pattern: sqlite3.Row,
        requested_surface: str,
        target_ids: list[str] | None,
        capability_owner: str,
    ) -> tuple[str, list[str]]:
        if requested_surface != "auto" and requested_surface not in SURFACES:
            raise ValueError("invalid improvement surface")
        evidence = self.rows("""SELECT f.subject_class,f.expectation_template,f.observed_template,f.desired_template
            FROM learning_signals s JOIN feedback_events f ON f.feedback_id=s.feedback_id
            WHERE s.theme_key=?""", (pattern["pattern_key"],))
        routing_text = " ".join(
            str(value)
            for row in evidence
            for value in (row["subject_class"], row["expectation_template"], row["observed_template"], row["desired_template"])
        ).lower()
        if requested_surface == "auto":
            if any(token in routing_text for token in ("preparation", "準備", "事前", "draft", "final", "期限")):
                surface = "skill-edge"
                targets = ["project.plan", "human.request", "task.remind", "task.verify"]
            elif any(token in routing_text for token in ("hook", "capture", "収集", "入力時")):
                surface, targets = "hook", ["feedback.capture"]
            elif any(token in routing_text for token in ("queue", "scheduler", "worker", "台帳", "runtime")):
                surface, targets = "runtime", ["adaptive.runtime"]
            elif any(token in routing_text for token in ("用語", "dictionary", "辞書", "knowledge")):
                surface, targets = "dictionary", ["shared.dictionary"]
            elif any(token in routing_text for token in ("agents.md", "policy", "原則", "ルール")):
                surface, targets = "agents", ["agents.policy"]
            else:
                surface, targets = "observation", [pattern["pattern_id"]]
        else:
            surface = requested_surface
            targets = [sanitize_text(item, 160) for item in (target_ids or []) if sanitize_text(item, 160)]
            if not targets:
                targets = ["new-skill"] if surface == "new-skill" else [pattern["pattern_id"]]
        owner = sanitize_text(capability_owner, 160).lower()
        if surface == "new-skill" and (owner not in {"", "none"} or self.maturity_gate_status() != "open"):
            raise ValueError("new-skill is refused unless no capability owner exists and the maturity gate is open")
        return surface, targets

    def create_proposal(
        self,
        pattern_id: str,
        *,
        requested_surface: str = "auto",
        target_ids: list[str] | None = None,
        target_hashes: dict[str, str],
        capability_owner: str = "",
        title: str = "",
        change_summary: str = "",
    ) -> dict[str, Any]:
        """Stage a disabled ImprovementProposal and ChangeSet; never publish or apply it."""
        self.initialize()
        hashes = normalize_target_hashes(target_hashes)
        with self.connect() as db:
            pattern = db.execute("SELECT * FROM improvement_patterns WHERE pattern_id=?", (pattern_id,)).fetchone()
            if not pattern:
                raise ValueError("unknown pattern; run patterns first")
            if pattern["eligibility"] != "proposal-eligible":
                raise ValueError("pattern is review-only; explicit persistence or two recent independent sessions are required")
            surface, targets = self._route_surface(pattern, requested_surface, target_ids, capability_owner)
            if set(targets) != set(hashes):
                raise ValueError("target hashes must exactly match the routed ChangeSet targets")
        proposal_id = "prop_" + uuid.uuid4().hex
        changeset_id = "chg_" + uuid.uuid4().hex
        target = (self.staging / proposal_id).resolve()
        if self.staging not in target.parents:
            raise ValueError("unsafe staging path")
        target.mkdir(parents=True, exist_ok=False)
        proposal = {
            "proposal_id": proposal_id,
            "pattern_id": pattern_id,
            "title": sanitize_text(title, 160) or pattern["title"],
            "surface": surface,
            "capability_owner": sanitize_text(capability_owner, 160),
            "status": "disabled-staged",
            "target_hashes": hashes,
            "publication_allowed": False,
            "created_at": now(),
        }
        operations = {
            "surface": surface,
            "targets": list(hashes),
            "change_summary": sanitize_text(change_summary, 512),
            "apply": False,
        }
        changeset_hash = self.digest(canonical_json({"target_hashes": hashes, "operations": operations}))
        change_set = {
            "changeset_id": changeset_id,
            "proposal_id": proposal_id,
            "target_hashes": hashes,
            "operations": operations,
            "changeset_hash": changeset_hash,
            "status": "disabled-staged",
        }
        (target / "proposal.json.disabled").write_text(json.dumps(proposal, ensure_ascii=False, indent=2), encoding="utf-8")
        (target / "changeset.json.disabled").write_text(json.dumps(change_set, ensure_ascii=False, indent=2), encoding="utf-8")
        with self.connect() as db:
            db.execute("""INSERT INTO improvement_proposals(
                proposal_id,pattern_id,title,surface,capability_owner,status,staging_path,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?)""", (
                proposal_id,
                pattern_id,
                proposal["title"],
                surface,
                proposal["capability_owner"],
                "disabled-staged",
                str(target),
                proposal["created_at"],
                proposal["created_at"],
            ))
            db.execute("""INSERT INTO change_sets(
                changeset_id,proposal_id,target_hashes_json,operations_json,changeset_hash,status,created_at)
                VALUES(?,?,?,?,?,?,?)""", (
                changeset_id,
                proposal_id,
                canonical_json(hashes),
                canonical_json(operations),
                changeset_hash,
                "disabled-staged",
                proposal["created_at"],
            ))
        return proposal | {"changeset_id": changeset_id, "changeset_hash": changeset_hash, "staging_path": str(target)}

    @staticmethod
    def _read_disabled_json(path: Path) -> dict[str, Any]:
        if path.is_symlink() or not path.is_file():
            raise ValueError("staged artifact is missing or is not a regular file")
        try:
            if path.stat().st_size > 65_536:
                raise ValueError("staged artifact exceeds the size limit")
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("staged artifact is unreadable") from exc
        if not isinstance(value, dict):
            raise ValueError("staged artifact must be a JSON object")
        return value

    def _verify_staged_change(
        self,
        db: sqlite3.Connection,
        proposal_id: str,
    ) -> tuple[sqlite3.Row, sqlite3.Row, str]:
        proposal = db.execute(
            "SELECT * FROM improvement_proposals WHERE proposal_id=?",
            (proposal_id,),
        ).fetchone()
        change = db.execute(
            "SELECT * FROM change_sets WHERE proposal_id=?",
            (proposal_id,),
        ).fetchone()
        if not proposal or not change:
            raise ValueError("unknown proposal or ChangeSet")
        if proposal["status"] != "disabled-staged" or change["status"] != "disabled-staged":
            raise ValueError("proposal and ChangeSet must remain disabled-staged")

        staging_root = self.staging.resolve()
        expected_target = (staging_root / proposal_id).resolve()
        try:
            recorded_target = Path(proposal["staging_path"]).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError("staging path is unavailable") from exc
        if (
            recorded_target != expected_target
            or staging_root not in recorded_target.parents
            or not recorded_target.is_dir()
        ):
            raise ValueError("staging path escapes the governed proposal directory")

        try:
            target_hashes = json.loads(change["target_hashes_json"])
            operations = json.loads(change["operations_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("database ChangeSet JSON is invalid") from exc
        if not isinstance(target_hashes, dict) or not isinstance(operations, dict):
            raise ValueError("database ChangeSet structure is invalid")
        if operations.get("apply") is not False:
            raise ValueError("staged ChangeSet must remain non-applying")
        recomputed_hash = self.digest(
            canonical_json(
                {"target_hashes": target_hashes, "operations": operations}
            )
        )
        if change["changeset_hash"] != recomputed_hash:
            raise ValueError("staged ChangeSet integrity check failed")

        proposal_artifact = self._read_disabled_json(
            recorded_target / "proposal.json.disabled"
        )
        changeset_artifact = self._read_disabled_json(
            recorded_target / "changeset.json.disabled"
        )
        expected_proposal = {
            "proposal_id": proposal["proposal_id"],
            "pattern_id": proposal["pattern_id"],
            "title": proposal["title"],
            "surface": proposal["surface"],
            "capability_owner": proposal["capability_owner"],
            "status": proposal["status"],
            "target_hashes": target_hashes,
            "publication_allowed": False,
            "created_at": proposal["created_at"],
        }
        expected_change = {
            "changeset_id": change["changeset_id"],
            "proposal_id": change["proposal_id"],
            "target_hashes": target_hashes,
            "operations": operations,
            "changeset_hash": change["changeset_hash"],
            "status": change["status"],
        }
        if proposal_artifact != expected_proposal:
            raise ValueError("proposal.json.disabled does not match the ledger")
        if changeset_artifact != expected_change:
            raise ValueError("changeset.json.disabled does not match the ledger")
        return proposal, change, recomputed_hash

    def record_approval(
        self,
        proposal_id: str,
        target_hashes: dict[str, str],
        expires_at: str | datetime,
        approval_ref: str,
    ) -> dict[str, Any]:
        """Record an exact, expiring approval and return its one-time token."""
        self.initialize()
        hashes = normalize_target_hashes(target_hashes)
        expiry = parse_time(expires_at)
        current = datetime.now(timezone.utc)
        if expiry <= current:
            raise ValueError("approval expiry must be in the future")
        if expiry > current + timedelta(days=30):
            raise ValueError("approval expiry may not exceed 30 days")
        clean_ref = sanitize_text(approval_ref, 160)
        if not clean_ref:
            raise ValueError("an explicit human approval reference is required")
        with self.connect() as db:
            _, change, expected_changeset_hash = self._verify_staged_change(
                db,
                proposal_id,
            )
            expected = json.loads(change["target_hashes_json"])
            if hashes != expected:
                raise ValueError("approval target hashes are stale or do not match the staged ChangeSet")
        # argparse treats a value beginning with "-" as another option when the
        # common `--approval-token TOKEN` form is used.  Prefix the random token
        # so every newly issued one is both URL-safe and CLI-safe.
        token = "apt_" + secrets.token_urlsafe(24)
        approval_hash = self.digest(f"approval:{token}")
        approval_id = "apr_" + uuid.uuid4().hex
        with self.connect() as db:
            db.execute("""INSERT INTO approvals(
                approval_id,proposal_id,approval_hash,target_hashes_json,changeset_hash,approval_ref_hash,
                expires_at,status,created_at,used_at)
                VALUES(?,?,?,?,?,?,?,?,?,NULL)""", (
                approval_id,
                proposal_id,
                approval_hash,
                canonical_json(hashes),
                expected_changeset_hash,
                self.digest(clean_ref),
                expiry.isoformat(timespec="seconds"),
                "recorded",
                now(),
            ))
        return {
            "approval_id": approval_id,
            "approval_hash": approval_hash,
            "approval_token": token,
            "target_hashes": hashes,
            "expires_at": expiry.isoformat(timespec="seconds"),
            "single_use": True,
        }

    def start_experiment(
        self,
        proposal_id: str,
        approval_token: str,
        target_hashes: dict[str, str],
        hypothesis: str,
    ) -> dict[str, Any]:
        """Consume an approval atomically and record a bounded experiment; apply nothing."""
        self.initialize()
        hashes = normalize_target_hashes(target_hashes)
        token_hash = self.digest(f"approval:{approval_token}")
        clean_hypothesis = sanitize_text(hypothesis, 512)
        if not clean_hypothesis:
            raise ValueError("an experiment hypothesis is required")
        experiment_id = "exp_" + uuid.uuid4().hex
        with self.connect() as db:
            approval = db.execute("""SELECT * FROM approvals
                WHERE proposal_id=? AND approval_hash=?""", (proposal_id, token_hash)).fetchone()
            if not approval:
                raise ValueError("unknown approval token")
            if approval["status"] != "recorded" or approval["used_at"]:
                raise ValueError("approval token has already been used")
            if parse_time(approval["expires_at"]) <= datetime.now(timezone.utc):
                raise ValueError("approval token has expired")
            if hashes != json.loads(approval["target_hashes_json"]):
                raise ValueError("target hashes changed after approval")
            _, current_change, current_changeset_hash = self._verify_staged_change(
                db,
                proposal_id,
            )
            if hashes != json.loads(current_change["target_hashes_json"]):
                raise ValueError("staged ChangeSet changed after approval")
            if (
                current_change["changeset_hash"] != current_changeset_hash
                or approval["changeset_hash"] != current_changeset_hash
            ):
                raise ValueError("staged ChangeSet content changed after approval")
            consumed_at = now()
            cur = db.execute("""UPDATE approvals SET status='consumed',used_at=?
                WHERE approval_id=? AND status='recorded' AND used_at IS NULL""", (consumed_at, approval["approval_id"]))
            if cur.rowcount != 1:
                raise ValueError("approval token was consumed concurrently")
            db.execute("""INSERT INTO experiments(
                experiment_id,proposal_id,approval_id,hypothesis,target_hashes_json,status,started_at,completed_at)
                VALUES(?,?,?,?,?,'running',?,NULL)""", (
                experiment_id,
                proposal_id,
                approval["approval_id"],
                clean_hypothesis,
                canonical_json(hashes),
                consumed_at,
            ))
        return {"experiment_id": experiment_id, "proposal_id": proposal_id, "status": "running", "started_at": consumed_at}

    def record_verification_evidence(
        self,
        experiment_id: str,
        *,
        evidence_ref: str,
        outcome: str,
        verification: str,
        evidence_class: str,
    ) -> dict[str, Any]:
        """Bind trusted verification metadata to one running experiment."""
        if outcome not in EXPERIMENT_OUTCOMES:
            raise ValueError("invalid verification evidence outcome")
        allowed_classes = VERIFICATION_CLASS_BY_LEVEL.get(verification)
        if not allowed_classes or evidence_class not in allowed_classes:
            raise ValueError(
                "verification evidence class does not match the verification level"
            )
        clean_ref = sanitize_text(evidence_ref, 256)
        if not clean_ref or not OPAQUE_REF_RE.fullmatch(clean_ref):
            raise ValueError(
                "verification evidence_ref must be an opaque identifier or URI"
            )
        self.initialize()
        evidence_id = "vev_" + uuid.uuid4().hex
        recorded_at = now()
        with self.connect() as db:
            experiment = db.execute(
                "SELECT status FROM experiments WHERE experiment_id=?",
                (experiment_id,),
            ).fetchone()
            if not experiment:
                raise ValueError("unknown experiment")
            if experiment["status"] != "running":
                raise ValueError(
                    "verification evidence must be recorded before evaluation"
                )
            try:
                db.execute(
                    """INSERT INTO verification_evidence(
                         evidence_id,experiment_id,evidence_ref,outcome,
                         verification,evidence_class,provenance_trust,recorded_at
                       ) VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        evidence_id,
                        experiment_id,
                        clean_ref,
                        outcome,
                        verification,
                        evidence_class,
                        "trusted",
                        recorded_at,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(
                    "duplicate or invalid verification evidence"
                ) from exc
        return {
            "evidence_id": evidence_id,
            "experiment_id": experiment_id,
            "evidence_ref": clean_ref,
            "outcome": outcome,
            "verification": verification,
            "evidence_class": evidence_class,
            "provenance_trust": "trusted",
            "recorded_at": recorded_at,
        }

    def evaluate_experiment(
        self,
        experiment_id: str,
        *,
        outcome: str,
        verification: str,
        evidence_refs: list[str],
        notes: str = "",
    ) -> dict[str, Any]:
        """Append an outcome without inferring sole causality from correlation."""
        if outcome not in EXPERIMENT_OUTCOMES or verification not in EXPERIMENT_VERIFICATION:
            raise ValueError("invalid experiment evaluation")
        refs = list(
            dict.fromkeys(
                sanitize_text(ref, 256)
                for ref in evidence_refs
                if sanitize_text(ref, 256)
            )
        )
        if not refs:
            raise ValueError("at least one privacy-safe evidence reference is required")
        if any(not OPAQUE_REF_RE.fullmatch(ref) for ref in refs):
            raise ValueError("evidence references must be opaque identifiers or URIs without free text")
        self.initialize()
        outcome_id = "xout_" + uuid.uuid4().hex
        observed_at = now()
        with self.connect() as db:
            experiment = db.execute("""SELECT e.*,p.pattern_id FROM experiments e
                JOIN improvement_proposals p ON p.proposal_id=e.proposal_id
                WHERE e.experiment_id=?""", (experiment_id,)).fetchone()
            if not experiment:
                raise ValueError("unknown experiment")
            if experiment["status"] != "running":
                raise ValueError("experiment is already completed")
            verified_evidence = False
            if verification in VERIFICATION_CLASS_BY_LEVEL:
                placeholders = ",".join("?" for _ in refs)
                evidence = db.execute(
                    f"""SELECT evidence_ref,evidence_class
                        FROM verification_evidence
                        WHERE experiment_id=?
                          AND outcome=?
                          AND verification=?
                          AND provenance_trust='trusted'
                          AND evidence_ref IN ({placeholders})""",
                    (experiment_id, outcome, verification, *refs),
                ).fetchall()
                matched_refs = {
                    row["evidence_ref"]
                    for row in evidence
                    if row["evidence_class"]
                    in VERIFICATION_CLASS_BY_LEVEL[verification]
                }
                if matched_refs != set(refs):
                    raise ValueError(
                        "verified evaluation requires matching trusted ledger evidence "
                        "for this experiment, outcome, verification, and class"
                    )
                verified_evidence = True
            db.execute("""INSERT INTO experiment_outcomes(
                outcome_id,experiment_id,observed_at,outcome,verification,evidence_refs_json,
                notes,causal_claim,created_at)
                VALUES(?,?,?,?,?,?,?,?,?)""", (
                outcome_id,
                experiment_id,
                observed_at,
                outcome,
                verification,
                canonical_json(refs),
                sanitize_text(notes, 512),
                "not-established",
                observed_at,
            ))
            db.execute("UPDATE experiments SET status='completed',completed_at=? WHERE experiment_id=?", (observed_at, experiment_id))
            pattern = db.execute("SELECT * FROM improvement_patterns WHERE pattern_id=?", (experiment["pattern_id"],)).fetchone()
            validated = (
                outcome == "improved"
                and verification in {"verified", "user-confirmed"}
                and verified_evidence
                and pattern["independent_sessions_90d"] >= 2
                and not json.loads(pattern["counter_refs_json"])
            )
            if validated:
                db.execute("UPDATE improvement_patterns SET status='validated',updated_at=? WHERE pattern_id=?", (observed_at, pattern["pattern_id"]))
        return {
            "outcome_id": outcome_id,
            "experiment_id": experiment_id,
            "outcome": outcome,
            "verification": verification,
            "causal_claim": "not-established",
            "pattern_status": "validated" if validated else pattern["status"],
        }

    def rows(self, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        self.initialize()
        with self.connect() as db:
            return [dict(row) for row in db.execute(query, params).fetchall()]

    def status(self) -> dict[str, Any]:
        try:
            self.initialize()
        except StateSafetyError as exc:
            return {
                "root": str(self.root),
                "health": "error",
                "error_class": exc.error_class,
                "reason": str(exc),
                "key_valid": False
                if isinstance(exc, InvalidHmacKeyError)
                else None,
                "tombstoned": self.tombstone_path.exists(),
            }
        with self.connect() as db:
            counts = {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in (
                "feedback_events",
                "response_outcomes",
                "themes",
                "skill_candidates",
                "learning_signals",
                "improvement_patterns",
                "improvement_proposals",
                "change_sets",
                "approvals",
                "experiments",
                "experiment_outcomes",
                "verification_evidence",
            )}
            provenance = {
                row["provenance_trust"]: int(row["count"])
                for row in db.execute(
                    """SELECT provenance_trust,COUNT(*) count
                       FROM feedback_events GROUP BY provenance_trust"""
                ).fetchall()
            }
            schema_version = self._meta_value(db, "schema_version")
            privacy_repair_version = self._meta_value(db, "privacy_repair_version")
            ok = db.execute("PRAGMA integrity_check").fetchone()[0]
            enabled = db.execute(
                "SELECT value FROM meta WHERE key='enabled'"
            ).fetchone()
        return {
            "root": str(self.root),
            "health": "ok",
            "enabled": bool(
                enabled
                and enabled[0] == "1"
                and not (self.root / "disabled").exists()
            ),
            "key_valid": True,
            "tombstoned": False,
            "integrity": ok,
            "schema_version": schema_version,
            "privacy_repair_version": privacy_repair_version,
            "pending_spool": self.pending_spool_count(),
            "provenance": provenance,
            **counts,
        }

    def draft_skill(self, theme_id: str, name: str) -> dict[str, Any]:
        raise ValueError(
            "draft-skill is retired; build LearningSignals and ImprovementPatterns, then use "
            "propose with the surface router. new-skill remains refused unless no owner exists "
            "and the maturity gate is open"
        )

    def _assert_purge_authorized(self) -> None:
        if self.root == default_state_root():
            if self._marker_scope() != "production":
                raise UnsafePurgeTargetError("production-root-marker-scope-mismatch")
            return
        if self.test_purge_authority:
            temp_root = Path(tempfile.gettempdir()).resolve()
            if self.root != temp_root and temp_root in self.root.parents:
                if self._marker_scope() == "test-authorized":
                    return
        raise UnsafePurgeTargetError(
            "purge-refuses-noncanonical-root-without-explicit-temp-test-authority"
        )

    def _write_purge_tombstone(self) -> None:
        payload = {
            "kind": "feedback-learning-purge-tombstone",
            "version": 1,
            "root_fingerprint": state_root_fingerprint(self.root),
            "purged_at": now(),
        }
        self.tombstone_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.tombstone_path.parent / (
            f".{self.tombstone_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        try:
            with temp.open("x", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=True, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.tombstone_path)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    def purge(self, confirmation: str) -> dict[str, Any]:
        if confirmation != PURGE_CONFIRMATION:
            raise ValueError("exact confirmation token required")
        self._assert_purge_authorized()
        if self.tombstone_path.exists():
            if self.root.exists():
                raise UnsafePurgeTargetError(
                    "tombstoned-state-root-still-exists"
                )
            return {
                "deleted": str(self.root),
                "tombstone": str(self.tombstone_path),
                "residual": False,
                "already_purged": True,
            }

        self._validate_state_marker()
        self.set_enabled(False)
        with nonblocking_process_lock(self.drain_lock_path) as acquired:
            if not acquired:
                raise UnsafePurgeTargetError("purge-exclusive-lock-busy")
            self._assert_purge_authorized()
            self._validate_state_marker()
            if not (self.root / "disabled").is_file():
                raise UnsafePurgeTargetError("purge-disable-marker-missing")
            self._read_valid_key()
            self._write_purge_tombstone()
            shutil.rmtree(self.root)
            residual = self.root.exists()
            if residual:
                raise UnsafePurgeTargetError("purge-residual-state-root")
        return {
            "deleted": str(self.root),
            "tombstone": str(self.tombstone_path),
            "residual": False,
            "already_purged": False,
        }


SCHEMA = """
CREATE TABLE IF NOT EXISTS feedback_events(
 feedback_id TEXT PRIMARY KEY, observed_at TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
 signature TEXT NOT NULL, session_hash TEXT NOT NULL, turn_hash TEXT NOT NULL, repo_hash TEXT NOT NULL,
 feedback_type TEXT NOT NULL, subject_class TEXT NOT NULL, theme_key TEXT NOT NULL, impact TEXT NOT NULL,
 explicitness TEXT NOT NULL, capture_mode TEXT NOT NULL, expectation_template TEXT NOT NULL,
 observed_template TEXT NOT NULL, desired_template TEXT NOT NULL, event_json TEXT NOT NULL,
 created_at TEXT NOT NULL, provenance_trust TEXT NOT NULL DEFAULT 'trusted'
 CHECK(provenance_trust IN ('trusted','legacy-unverified')));
CREATE TRIGGER IF NOT EXISTS feedback_events_no_update BEFORE UPDATE ON feedback_events BEGIN SELECT RAISE(ABORT,'feedback_events are immutable'); END;
CREATE TABLE IF NOT EXISTS response_outcomes(
 outcome_id TEXT PRIMARY KEY, feedback_id TEXT NOT NULL REFERENCES feedback_events(feedback_id), observed_at TEXT NOT NULL,
 action_class TEXT NOT NULL, status TEXT NOT NULL, verification TEXT NOT NULL, satisfaction TEXT NOT NULL, notes TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS themes(
 theme_id TEXT PRIMARY KEY, theme_key TEXT NOT NULL, subject_class TEXT NOT NULL, feedback_type TEXT NOT NULL,
 incident_count INTEGER NOT NULL, independent_sessions INTEGER NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
 severity INTEGER NOT NULL, status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS theme_events(theme_id TEXT NOT NULL REFERENCES themes(theme_id) ON DELETE CASCADE, feedback_id TEXT NOT NULL REFERENCES feedback_events(feedback_id), PRIMARY KEY(theme_id,feedback_id));
CREATE TABLE IF NOT EXISTS skill_candidates(
 candidate_id TEXT PRIMARY KEY, theme_id TEXT NOT NULL, skill_name TEXT NOT NULL, status TEXT NOT NULL,
 staging_path TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS collector_health(name TEXT PRIMARY KEY, value INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS spool_receipts(
 event_id TEXT PRIMARY KEY,
 received_at TEXT NOT NULL,
 feedback_id TEXT NOT NULL REFERENCES feedback_events(feedback_id));
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


EVOLUTION_SCHEMA = """
CREATE TRIGGER IF NOT EXISTS feedback_events_no_delete
BEFORE DELETE ON feedback_events BEGIN SELECT RAISE(ABORT,'feedback_events are immutable'); END;

CREATE TABLE IF NOT EXISTS learning_signals(
 signal_id TEXT PRIMARY KEY,
 feedback_id TEXT NOT NULL UNIQUE REFERENCES feedback_events(feedback_id),
 observed_at TEXT NOT NULL,
 signal_type TEXT NOT NULL,
 theme_key TEXT NOT NULL,
 subject_class TEXT NOT NULL,
 session_hash TEXT NOT NULL,
 severity INTEGER NOT NULL,
 evidence_role TEXT NOT NULL,
 persistence_requested INTEGER NOT NULL,
 evidence_refs_json TEXT NOT NULL,
 created_at TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS learning_signals_no_update
BEFORE UPDATE ON learning_signals BEGIN SELECT RAISE(ABORT,'learning_signals are immutable'); END;
CREATE TRIGGER IF NOT EXISTS learning_signals_no_delete
BEFORE DELETE ON learning_signals BEGIN SELECT RAISE(ABORT,'learning_signals are immutable'); END;

CREATE TABLE IF NOT EXISTS improvement_patterns(
 pattern_id TEXT PRIMARY KEY,
 pattern_key TEXT NOT NULL UNIQUE,
 title TEXT NOT NULL,
 status TEXT NOT NULL,
 eligibility TEXT NOT NULL,
 eligibility_reason TEXT NOT NULL,
 severity INTEGER NOT NULL,
 independent_sessions_90d INTEGER NOT NULL,
 support_refs_json TEXT NOT NULL,
 counter_refs_json TEXT NOT NULL,
 boundary_refs_json TEXT NOT NULL,
 first_seen TEXT NOT NULL,
 last_seen TEXT NOT NULL,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS improvement_proposals(
 proposal_id TEXT PRIMARY KEY,
 pattern_id TEXT NOT NULL REFERENCES improvement_patterns(pattern_id),
 title TEXT NOT NULL,
 surface TEXT NOT NULL,
 capability_owner TEXT NOT NULL,
 status TEXT NOT NULL,
 staging_path TEXT NOT NULL,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS change_sets(
 changeset_id TEXT PRIMARY KEY,
 proposal_id TEXT NOT NULL UNIQUE REFERENCES improvement_proposals(proposal_id),
 target_hashes_json TEXT NOT NULL,
 operations_json TEXT NOT NULL,
 changeset_hash TEXT NOT NULL,
 status TEXT NOT NULL,
 created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS approvals(
 approval_id TEXT PRIMARY KEY,
 proposal_id TEXT NOT NULL REFERENCES improvement_proposals(proposal_id),
 approval_hash TEXT NOT NULL UNIQUE,
 target_hashes_json TEXT NOT NULL,
 changeset_hash TEXT NOT NULL,
 approval_ref_hash TEXT NOT NULL,
 expires_at TEXT NOT NULL,
 status TEXT NOT NULL,
 created_at TEXT NOT NULL,
 used_at TEXT);

CREATE TABLE IF NOT EXISTS experiments(
 experiment_id TEXT PRIMARY KEY,
 proposal_id TEXT NOT NULL REFERENCES improvement_proposals(proposal_id),
 approval_id TEXT NOT NULL UNIQUE REFERENCES approvals(approval_id),
 hypothesis TEXT NOT NULL,
 target_hashes_json TEXT NOT NULL,
 status TEXT NOT NULL,
 started_at TEXT NOT NULL,
 completed_at TEXT);

CREATE TABLE IF NOT EXISTS experiment_outcomes(
 outcome_id TEXT PRIMARY KEY,
 experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
 observed_at TEXT NOT NULL,
 outcome TEXT NOT NULL,
 verification TEXT NOT NULL,
 evidence_refs_json TEXT NOT NULL,
 notes TEXT NOT NULL,
 causal_claim TEXT NOT NULL,
 created_at TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS experiment_outcomes_no_update
BEFORE UPDATE ON experiment_outcomes BEGIN SELECT RAISE(ABORT,'experiment outcomes are append-only'); END;
CREATE TRIGGER IF NOT EXISTS experiment_outcomes_no_delete
BEFORE DELETE ON experiment_outcomes BEGIN SELECT RAISE(ABORT,'experiment outcomes are append-only'); END;

CREATE TABLE IF NOT EXISTS verification_evidence(
 evidence_id TEXT PRIMARY KEY,
 experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
 evidence_ref TEXT NOT NULL,
 outcome TEXT NOT NULL,
 verification TEXT NOT NULL,
 evidence_class TEXT NOT NULL,
 provenance_trust TEXT NOT NULL DEFAULT 'legacy-unverified',
 recorded_at TEXT NOT NULL,
 UNIQUE(experiment_id,evidence_ref,outcome,verification,evidence_class));
CREATE TRIGGER IF NOT EXISTS verification_evidence_no_update
BEFORE UPDATE ON verification_evidence BEGIN SELECT RAISE(ABORT,'verification evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS verification_evidence_no_delete
BEFORE DELETE ON verification_evidence BEGIN SELECT RAISE(ABORT,'verification evidence is immutable'); END;
"""
