from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import yaml

SCHEMA_VERSION = 8
SPOOL_SCHEMA_VERSION = 3
COMPONENT_VERSION = "1.9.0"
PRIVACY_REPAIR_VERSION = "3"
PRIVACY_REPAIR_PENDING = "pending-v3"
PRIVACY_REPAIR_LOGICAL_VERSION = "2"
PRIVACY_REPAIR_LEGACY_PENDING = "pending-v2"
FINAL_STATES = {"returned", "failed", "interrupted"}
RUN_STATUSES = FINAL_STATES | {"running"}
SENTIMENTS = {"positive", "negative", "mixed"}
EVALUATION_OUTCOMES = {"verified-success", "partial", "rework-required", "rejected", "unverified"}
EVIDENCE_CLASSES = {
    "test", "build", "validate", "artifact", "pm-verified-task", "browser-qa",
    "authority", "domain-verdict", "explicit-feedback",
}
EVIDENCE_RESULTS = {"passed", "failed", "partial", "ambiguous"}
COMPLETION_EVIDENCE_CLASSES = {
    "test",
    "build",
    "validate",
    "artifact",
    "pm-verified-task",
    "browser-qa",
}
HOOK_SOURCE_CLASSES = {"custom", "plugin", "system", "external"}
# Skill *invocation* vs. Skill.md *read* are different tools on some hosts
# (Claude Code: Skill tool vs. Read tool) and the same tool on others (Codex:
# opening SKILL.md IS how a skill is invoked). The classification below is
# decided purely by tool_name, never by guessing which host is running:
#   - tool_name == "Skill"   -> "invocation" (the tool's own argument names
#                                the skill directly; see skill_identities()).
#   - any other tool_name    -> "read" (a SKILL.md path was merely seen in
#                                that tool's input; on Codex this coincides
#                                with invocation, but the class name records
#                                only the detection method, not an inferred
#                                intent, so downstream consumers can choose
#                                how to weight it per host).
SKILL_DETECTION_CLASSES = {"invocation", "read"}
PERSISTED_SKILL_DETECTION_CLASSES = SKILL_DETECTION_CLASSES | {"legacy-unknown"}
HOOK_FEELING_CLASSES = {
    "explicit-approval",
    "explicit-complaint-or-correction",
    "explicit-mixed-reaction",
}
FEELING_CLASSES = set(HOOK_FEELING_CLASSES)
MODEL_CLASSES = {"", "openai", "anthropic", "google", "local", "test", "unknown"}
RUN_DETECTIONS = {"hook-inferred", "explicit-manual"}
EVIDENCE_DETECTIONS = {
    "hook-inferred",
    "explicit-manual",
    "hook-inferred-explicit-language",
}
FEEDBACK_SOURCES = {"explicit-manual", "hook-inferred-explicit-language"}
EVALUATION_EVIDENCE_CLASSES = EVIDENCE_CLASSES | {"lifecycle", "rollout"}
EVALUATORS = {
    "codex",
    "unit-test",
    "codex-structured-evidence",
    "codex-structured-rollout-review",
}
RUBRIC_VERSIONS = {"outcome-v1", "outcome-v2"}
END_REASONS = {
    "running",
    "stop",
    "superseded",
    "manual-returned",
    "manual-failed",
    "manual-interrupted",
    "proven-orphan",
    "stale-timeout",
    "legacy-unknown",
}
DURATION_QUALITIES = {"pending", "exact", "bounded", "unknown"}
VERIFIED_SUCCESS_END_REASONS = {"stop", "manual-returned"}
HEALTH_HOOKS = {"PostToolUse", "Stop", "UserPromptSubmit", "collector"}
HEALTH_STATUSES = {"ok", "degraded", "error"}
LEGACY_REDACTED = "legacy-redacted"
TRUSTED_PROVENANCE = "trusted"
LEGACY_PROVENANCE = "legacy-unverified"
PROVENANCE_TRUST = {TRUSTED_PROVENANCE, LEGACY_PROVENANCE}
PERSISTED_SOURCE_CLASSES = HOOK_SOURCE_CLASSES | {"manual", LEGACY_REDACTED}
PERSISTED_MODEL_CLASSES = MODEL_CLASSES | {LEGACY_REDACTED}
PERSISTED_RUN_DETECTIONS = RUN_DETECTIONS | {LEGACY_REDACTED}
PERSISTED_EVIDENCE_DETECTIONS = EVIDENCE_DETECTIONS | {LEGACY_REDACTED}
PERSISTED_FEELING_CLASSES = FEELING_CLASSES | {LEGACY_REDACTED}
PERSISTED_FEEDBACK_SOURCES = FEEDBACK_SOURCES | {LEGACY_REDACTED}
PERSISTED_EVALUATION_CLASSES = EVALUATION_EVIDENCE_CLASSES | {LEGACY_REDACTED}
PERSISTED_EVALUATORS = EVALUATORS | {LEGACY_REDACTED}
PERSISTED_HEALTH_HOOKS = HEALTH_HOOKS | {LEGACY_REDACTED}
PERSISTED_HEALTH_STATUSES = HEALTH_STATUSES | {LEGACY_REDACTED}
HOOK_RECORD_KEYS = {
    "version",
    "hook",
    "observed_at",
    "stable_correlation",
    "session_hash",
    "turn_hash",
    "repo_hash",
    "model_class",
    "skills",
    "failure",
    "event_id",
    "auth_tag",
}
HOOK_IDENTITY_RE = re.compile(r"[a-z0-9][a-z0-9._:@+-]*")
OPAQUE_REFERENCE_RE = re.compile(
    r"(?:evidence|artifact|sha256|run|test|build|validate|browser-qa|"
    r"pm-verified-task|domain-verdict|legacy):"
    r"[A-Za-z0-9][A-Za-z0-9._-]{2,127}"
)
LEGACY_RUBRIC_RE = re.compile(r"legacy-[0-9a-f]{32}")
RUN_ID_RE = re.compile(r"skillrun_[0-9a-f]{32}")
HEALTH_DETAIL_RE = re.compile(
    r"(?:spool-v3;stable-secret-unavailable|"
    r"spool-v3;skills:(?:[0-9]|1[0-9]|20);evidence:[01]|legacy-redacted)"
)
LEGACY_TIMESTAMP = "1970-01-01T00:00:00+00:00"
TIME_META_KEYS = {
    "outcome_v2_cycle_start",
    "outcome_v2_pre_release_start",
}
FIXED_META_KEYS = {
    "schema_version",
    "spool_schema_version",
    "component_version",
    "privacy_repair_version",
} | TIME_META_KEYS
RUBRIC_CRITERIA = {
    "outcome_achieved",
    "completion_evidence",
    "authority_safety",
    "avoidable_rework",
    "efficient_recoverable",
}
BUSY_DELAYS = (0.0, 0.025, 0.05, 0.1)
SQLITE_BUSY_TIMEOUT_MS = 75
INIT_LOCK_TIMEOUT_SECONDS = 1.5
SPOOL_RECORD_LIMIT = 128_000
PLUGIN_MANIFEST_LIMIT = 128_000


class PrivacyRepairPendingError(RuntimeError):
    """A legacy privacy repair is committed but WAL cleanup is still blocked."""


class DrainBudgetExceeded(RuntimeError):
    """A spool record was rolled back because its bounded drain budget expired."""


def is_busy_error(error: BaseException) -> bool:
    return isinstance(error, sqlite3.OperationalError) and any(
        marker in str(error).lower() for marker in ("locked", "busy")
    )


def retry_busy(call: Callable[[], Any]) -> Any:
    last: BaseException | None = None
    for delay in BUSY_DELAYS:
        if delay:
            time.sleep(delay)
        try:
            return call()
        except sqlite3.OperationalError as error:
            if not is_busy_error(error):
                raise
            last = error
    assert last is not None
    raise last


class RetryingConnection(sqlite3.Connection):
    """Apply one short, bounded busy retry policy to every SQLite statement."""

    def execute(self, sql, parameters=(), /):
        parent = super().execute
        return retry_busy(lambda: parent(sql, parameters))

    def executemany(self, sql, seq_of_parameters, /):
        parent = super().executemany
        return retry_busy(lambda: parent(sql, seq_of_parameters))

    def executescript(self, sql_script, /):
        parent = super().executescript
        return retry_busy(lambda: parent(sql_script))


@contextmanager
def process_lock(path: Path, timeout: float = INIT_LOCK_TIMEOUT_SECONDS):
    """Take a local-filesystem advisory lock on Windows or POSIX."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    acquired = False
    deadline = time.monotonic() + max(0.05, timeout)
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            while not acquired:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    acquired = True
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("telemetry schema lock timeout")
                    time.sleep(0.025)
        else:
            import fcntl

            while not acquired:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except (BlockingIOError, OSError):
                    if time.monotonic() >= deadline:
                        raise TimeoutError("telemetry schema lock timeout")
                    time.sleep(0.025)
        yield
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


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def canonical_utc_timestamp(value: Any) -> tuple[datetime, str]:
    """Return a canonical UTC timestamp and its persisted precision."""
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("invalid timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError("invalid timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    normalized = parsed.astimezone(timezone.utc)
    canonical_microseconds = normalized.isoformat(timespec="microseconds")
    canonical_seconds = normalized.replace(microsecond=0).isoformat(
        timespec="seconds"
    )
    if value == canonical_microseconds:
        return normalized, "microseconds"
    if value == canonical_seconds:
        return normalized, "seconds"
    raise ValueError("timestamp must be canonical UTC")


def timestamp_strictly_precedes(earlier: str, later: str) -> bool:
    """Prove order without guessing inside a legacy second-precision interval."""
    earlier_time, earlier_precision = canonical_utc_timestamp(earlier)
    later_time, later_precision = canonical_utc_timestamp(later)
    if earlier_time.replace(microsecond=0) == later_time.replace(microsecond=0):
        if "seconds" in {earlier_precision, later_precision}:
            return False
    return earlier_time < later_time


def default_root() -> Path:
    override = os.environ.get("CODEX_SKILL_TELEMETRY_HOME")
    if override:
        return Path(override).expanduser().resolve()
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    return (codex_home / "skill-telemetry").resolve()


def iter_strings(value: Any, depth: int = 0) -> Iterable[str]:
    if depth > 5:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from iter_strings(child, depth + 1)
    elif isinstance(value, list):
        for child in value[:50]:
            yield from iter_strings(child, depth + 1)


class TelemetryStore:
    def __init__(
        self,
        root: Path | None = None,
        *,
        initialize: bool = True,
        drain: bool = False,
    ):
        if drain:
            raise ValueError(
                "constructor draining is disabled; call drain_spool explicitly"
            )
        self.root = (root or default_root()).resolve()
        self.db_path = self.root / "telemetry.sqlite3"
        self.secret_path = self.root / "secret.key"
        self.spool_path = self.root / "spool"
        self.lock_path = self.root / "schema.lock"
        self._initialized = False
        self._last_read_mode: str | None = None
        if initialize:
            self.root.mkdir(parents=True, exist_ok=True)
            self._ensure_schema()
            self._secret()

    def _existing_secret(self) -> bytes | None:
        try:
            value = self.secret_path.read_bytes()
            return value if len(value) >= 32 else None
        except OSError:
            return None

    def _secret(self) -> bytes:
        existing = self._existing_secret()
        if existing is not None:
            return existing
        self.root.mkdir(parents=True, exist_ok=True)
        for _ in range(20):
            try:
                descriptor = os.open(
                    self.secret_path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
            except FileExistsError:
                descriptor = None
            if descriptor is not None:
                try:
                    os.write(descriptor, secrets.token_bytes(32))
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            try:
                value = self.secret_path.read_bytes()
                if len(value) >= 32:
                    return value
            except FileNotFoundError:
                pass
            time.sleep(0.01)
        raise OSError("telemetry secret is unavailable")

    def pseudonym(self, value: str, domain: str) -> str:
        if not value:
            return ""
        return hmac.new(self._secret(), f"{domain}:{value}".encode(), hashlib.sha256).hexdigest()

    def pseudonym_existing(self, value: str, domain: str) -> str:
        """Pseudonymize without provisioning state, for read-only report paths."""
        if not value:
            return ""
        secret = self._existing_secret()
        if secret is None:
            return ""
        return hmac.new(
            secret, f"{domain}:{value}".encode(), hashlib.sha256
        ).hexdigest()

    @staticmethod
    def _require_identity(
        value: Any,
        label: str,
        *,
        limit: int,
        allow_empty: bool = False,
    ) -> str:
        if not isinstance(value, str):
            raise ValueError(f"invalid {label}")
        if value == "" and allow_empty:
            return value
        if (
            not value
            or len(value) > limit
            or not HOOK_IDENTITY_RE.fullmatch(value)
        ):
            raise ValueError(f"invalid {label}")
        return value

    @classmethod
    def _validate_skill_identity(
        cls,
        skill_key: Any,
        skill_name: Any,
        provider: Any,
        source_class: Any,
    ) -> tuple[str, str, str, str]:
        key = cls._require_identity(skill_key, "skill key", limit=160)
        name = cls._require_identity(skill_name, "skill name", limit=120)
        provider_value = cls._require_identity(provider, "provider", limit=80)
        if source_class not in PERSISTED_SOURCE_CLASSES:
            raise ValueError("invalid skill source class")
        return key, name, provider_value, str(source_class)

    @staticmethod
    def _classify_model(value: Any) -> str:
        """Reduce runtime model identifiers to a bounded provider class."""
        if value is None:
            return ""
        rendered = str(value).strip().lower()
        if not rendered:
            return ""
        if rendered in MODEL_CLASSES:
            return rendered
        if rendered.startswith(("gpt", "o1", "o3", "o4", "o5", "codex", "openai")):
            return "openai"
        if rendered.startswith("claude") or "anthropic" in rendered:
            return "anthropic"
        if rendered.startswith("gemini") or "google" in rendered:
            return "google"
        if rendered.startswith(("ollama", "local", "llama.cpp")):
            return "local"
        if rendered.startswith("test"):
            return "test"
        return "unknown"

    @staticmethod
    def _require_model_class(value: Any) -> str:
        if not isinstance(value, str) or value not in MODEL_CLASSES:
            raise ValueError("invalid model class")
        return value

    @staticmethod
    def _require_detection(value: Any, allowed: set[str], label: str) -> str:
        if not isinstance(value, str) or value not in allowed:
            raise ValueError(f"invalid {label}")
        return value

    @staticmethod
    def _require_health_values(
        hook: Any, status: Any, detail: Any
    ) -> tuple[str, str, str]:
        if not isinstance(hook, str) or hook not in HEALTH_HOOKS:
            raise ValueError("invalid health hook")
        if not isinstance(status, str) or status not in HEALTH_STATUSES:
            raise ValueError("invalid health status")
        if not isinstance(detail, str) or not HEALTH_DETAIL_RE.fullmatch(detail):
            raise ValueError("invalid health detail class")
        return hook, status, detail

    @staticmethod
    def _require_run_id(value: Any) -> str:
        if not isinstance(value, str) or not RUN_ID_RE.fullmatch(value):
            raise ValueError("invalid run_id")
        return value

    @staticmethod
    def _valid_opaque_reference(value: Any) -> bool:
        return (
            isinstance(value, str)
            and len(value) <= 160
            and bool(OPAQUE_REFERENCE_RE.fullmatch(value))
        )

    @classmethod
    def validate_evaluation_contract(
        cls,
        outcome: Any,
        scores: Any,
        evidence_classes: Any,
        evidence_refs: Any,
    ) -> tuple[str, dict[str, int] | None, list[str], list[str]]:
        """Validate cross-field outcome, score, and evidence invariants."""
        if (
            not isinstance(outcome, str)
            or outcome not in EVALUATION_OUTCOMES
        ):
            raise ValueError("invalid evaluation outcome")
        if (
            not isinstance(evidence_classes, list)
            or any(
                not isinstance(item, str)
                or item not in EVALUATION_EVIDENCE_CLASSES
                for item in evidence_classes
            )
        ):
            raise ValueError("invalid evaluation evidence class")
        if (
            not isinstance(evidence_refs, list)
            or any(not cls._valid_opaque_reference(item) for item in evidence_refs)
        ):
            raise ValueError("evidence refs must be strict opaque references")
        classes = sorted(set(evidence_classes))
        refs = sorted(set(evidence_refs))

        if outcome == "unverified":
            if scores is not None:
                raise ValueError("unverified evaluations must not include scores")
            return outcome, None, classes, refs
        if not classes or not refs:
            raise ValueError(
                "non-unverified evaluations require evidence classes and refs"
            )
        if not isinstance(scores, dict):
            raise ValueError("non-unverified evaluations require rubric scores")
        unknown = set(scores) - RUBRIC_CRITERIA
        if unknown or set(scores) != RUBRIC_CRITERIA or any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value <= 2
            for value in scores.values()
        ):
            raise ValueError(
                "all rubric scores must be integers from 0 to 2"
            )
        completion_present = bool(
            COMPLETION_EVIDENCE_CLASSES.intersection(classes)
        )
        domain_verdict_present = "domain-verdict" in classes
        if (
            scores["outcome_achieved"] == 2
            or scores["completion_evidence"] == 2
        ) and not completion_present:
            raise ValueError(
                "score 2 for outcome/completion requires completion evidence"
            )
        if (
            scores["outcome_achieved"] == 2
            and not domain_verdict_present
        ):
            raise ValueError(
                "outcome score 2 requires explicit domain-verdict evidence"
            )
        if scores["authority_safety"] == 2 and "authority" not in classes:
            raise ValueError("authority score 2 requires authority evidence")
        if (
            scores["avoidable_rework"] > 1
            or scores["efficient_recoverable"] > 1
        ):
            raise ValueError(
                "rework and efficiency scores are capped at 1 without "
                "independent evidence classes"
            )
        if outcome == "verified-success" and (
            scores["outcome_achieved"] != 2
            or scores["completion_evidence"] != 2
            or scores["authority_safety"] != 2
            or not domain_verdict_present
            or not completion_present
            or "authority" not in classes
        ):
            raise ValueError(
                "verified-success requires explicit domain verdict, "
                "completion evidence, authority evidence, and verified scores"
            )
        all_verified = all(
            scores[name] == 2
            for name in (
                "outcome_achieved",
                "completion_evidence",
                "authority_safety",
            )
        )
        if outcome != "verified-success" and all_verified:
            raise ValueError(
                "fully verified scores require verified-success outcome"
            )
        if outcome == "partial" and (
            scores["outcome_achieved"] != 1
            or scores["completion_evidence"] < 1
        ):
            raise ValueError(
                "partial requires partial outcome and completion evidence"
            )
        if outcome == "rework-required" and (
            scores["outcome_achieved"] > 1
            or scores["completion_evidence"] > 1
        ):
            raise ValueError(
                "rework-required cannot claim verified outcome or completion"
            )
        if outcome == "rejected" and (
            scores["outcome_achieved"] != 0
            or scores["authority_safety"] != 0
        ):
            raise ValueError(
                "rejected requires failed outcome and authority scores"
            )
        return outcome, dict(scores), classes, refs

    def _pseudonymize_evidence_reference(
        self,
        value: Any,
        *,
        strict: bool,
        secret: bytes | None = None,
    ) -> str:
        rendered = str(value)
        if strict:
            if not self._valid_opaque_reference(rendered):
                raise ValueError("invalid evidence reference")
            scheme, token = rendered.split(":", 1)
        else:
            match = re.fullmatch(
                r"(evidence|artifact|sha256|run|test|build|validate|"
                r"browser-qa|pm-verified-task|domain-verdict|legacy):(.*)",
                rendered,
                re.DOTALL,
            )
            if match:
                scheme, token = match.group(1), match.group(2)
            else:
                scheme, token = "legacy", rendered
        key = secret or self._secret()
        digest = hmac.new(
            key,
            f"evaluation-ref:{scheme}:{token}".encode("utf-8", "replace"),
            hashlib.sha256,
        ).hexdigest()
        return f"{scheme}:{digest}"

    @staticmethod
    def _valid_rubric_version(value: Any, *, persisted: bool = False) -> bool:
        if not isinstance(value, str):
            return False
        if value in RUBRIC_VERSIONS:
            return True
        return persisted and bool(LEGACY_RUBRIC_RE.fullmatch(value))

    def _legacy_token(self, domain: str, value: Any, length: int = 32) -> str:
        # Legacy identities can contain guessable Skill or rubric names.  A plain
        # digest permits offline dictionary recovery, so migration aliases must
        # use the same installation-local secret as every other pseudonym.
        digest = self._legacy_hmac(self._secret(), domain, value)
        return f"legacy-{digest[:length]}"

    @staticmethod
    def _canonical_timestamp_or_legacy(
        value: Any, *, allow_none: bool = False
    ) -> str | None:
        if value is None and allow_none:
            return None
        try:
            canonical_utc_timestamp(value)
        except ValueError:
            return None if allow_none else LEGACY_TIMESTAMP
        return str(value)

    @staticmethod
    def _legacy_hmac(secret: bytes, domain: str, value: Any) -> str:
        return hmac.new(
            secret,
            f"{domain}:{value!s}".encode("utf-8", "replace"),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _path_key(path: Path) -> str:
        return os.path.normcase(str(path.resolve()))

    @classmethod
    def _plugins_root(cls) -> Path:
        return Path(__file__).resolve().parents[3] / "plugins"

    @classmethod
    def _canonical_local_sources(
        cls,
    ) -> dict[str, tuple[str, str, str, str]]:
        """Resolve local Skills only from the canonical registry key/path pair."""
        root = Path(__file__).resolve().parents[2]
        registry = root / "skill-registry.yaml"
        sources: dict[str, tuple[str, str, str, str]] = {}
        try:
            data = yaml.safe_load(registry.read_text(encoding="utf-8-sig"))
            entries = data.get("skills") if isinstance(data, dict) else None
            if not isinstance(entries, list):
                return {}
            for item in entries:
                if (
                    not isinstance(item, dict)
                    or item.get("source") != "local"
                    or not isinstance(item.get("key"), str)
                    or not isinstance(item.get("path"), str)
                ):
                    continue
                key = item["key"]
                name = key.rsplit(":", 1)[-1]
                try:
                    key, name, provider, source_class = (
                        cls._validate_skill_identity(
                            key, name, "local", "custom"
                        )
                    )
                except ValueError:
                    continue
                relative = Path(item["path"])
                if relative.is_absolute():
                    continue
                candidate = root / relative
                skill_file = (
                    candidate
                    if candidate.name.lower() == "skill.md"
                    else candidate / "SKILL.md"
                ).resolve()
                if (
                    not skill_file.is_relative_to(root)
                    or not skill_file.is_file()
                ):
                    continue
                sources[cls._path_key(skill_file)] = (
                    key,
                    name,
                    provider,
                    source_class,
                )
        except (OSError, yaml.YAMLError):
            return {}
        return sources

    @classmethod
    def _known_skill_identities(cls) -> dict[str, set[tuple[str, str, str]]]:
        """Return canonical identities used to retain safe legacy labels."""
        known: dict[str, set[tuple[str, str, str]]] = {}
        for key, name, provider, source_class in (
            cls._canonical_local_sources().values()
        ):
            known.setdefault(key, set()).add(
                (name, provider, source_class)
            )
        for root in (
            Path(__file__).resolve().parents[2] / ".system",
            Path.home() / ".agents" / "skills",
        ):
            try:
                for path in root.glob("*/SKILL.md"):
                    name = path.parent.name.lower()
                    try:
                        cls._require_identity(
                            name, "skill name", limit=120
                        )
                    except ValueError:
                        continue
                    provider = (
                        "openai-system"
                        if root.name == ".system"
                        else "agents"
                    )
                    source_class = (
                        "system" if provider == "openai-system" else "external"
                    )
                    known.setdefault(name, set()).add(
                        (name, provider, source_class)
                    )
            except OSError:
                continue
        # Legacy Plugin labels are conservatively redacted. Reconstructing them
        # would require recursively scanning every cached package during each
        # database initialization; live events instead authorize the one
        # referenced path through its package manifest in `_plugin_identity`.
        return known

    @staticmethod
    def _spool_auth_tag(record: dict[str, Any], secret: bytes) -> str:
        unsigned = {key: value for key, value in record.items() if key != "auth_tag"}
        canonical = json.dumps(
            unsigned,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hmac.new(
            secret,
            b"skill-telemetry-spool-v2\0" + canonical,
            hashlib.sha256,
        ).hexdigest()

    def _repair_legacy_privacy(self, db: sqlite3.Connection) -> None:
        """Redact or pseudonymize every legacy field that could carry free text."""
        secret = self._secret()
        known_skill_identities = self._known_skill_identities()
        db.execute("PRAGMA defer_foreign_keys=ON")

        def safe_hash(value: Any, domain: str) -> str:
            # Every row reaching this repair boundary is legacy-untrusted.
            # Re-key even shape-valid hashes so a secret chosen to look like an
            # internal digest cannot survive migration verbatim. The domain
            # keeps same-field historical correlation without cross-field joins.
            return self._legacy_hmac(secret, domain, value)

        id_specs = (
            ("skill_runs", "run_id", "skillrun_"),
            ("skill_feedback", "feedback_id", "skillfb_"),
            (
                "skill_evaluations",
                "evaluation_id",
                "skilleval_",
            ),
            (
                "skill_evidence",
                "evidence_id",
                "skillevidence_",
            ),
        )
        for table, column, prefix in id_specs:
            for row in db.execute(
                f"SELECT rowid,{column} FROM {table}"
            ).fetchall():
                old_id = row[column]
                new_id = prefix + self._legacy_hmac(
                    secret, f"legacy-{column}", old_id
                )[:32]
                if table == "skill_runs":
                    for child in (
                        "skill_feedback",
                        "skill_evaluations",
                        "skill_run_evidence",
                    ):
                        db.execute(
                            f"UPDATE {child} SET run_id=? WHERE run_id=?",
                            (new_id, old_id),
                        )
                elif table == "skill_evidence":
                    db.execute(
                        """UPDATE skill_run_evidence
                           SET evidence_id=? WHERE evidence_id=?""",
                        (new_id, old_id),
                    )
                db.execute(
                    f"UPDATE {table} SET {column}=? WHERE rowid=?",
                    (new_id, row["rowid"]),
                )

        for row in db.execute(
            """SELECT rowid,idempotency_key,skill_key,skill_name,provider,
                      source_class,skill_fingerprint,session_hash,turn_hash,
                      repo_hash,model_class,detection,status,started_at,
                      ended_at,duration_ms,tool_failure_count
               FROM skill_runs"""
        ).fetchall():
            skill_key = row["skill_key"]
            skill_name = row["skill_name"]
            provider = row["provider"]
            source_class = row["source_class"]
            keep_named_identity = (
                row["detection"] == "hook-inferred"
                and (
                    skill_name,
                    provider,
                    source_class,
                )
                in known_skill_identities.get(str(skill_key), set())
            )
            try:
                skill_key, skill_name, provider, source_class = (
                    self._validate_skill_identity(
                        skill_key, skill_name, provider, source_class
                    )
                )
                if not keep_named_identity:
                    raise ValueError("untrusted legacy identity")
            except ValueError:
                identity_seed = (
                    f"{row['skill_key']}|{row['skill_name']}|{row['provider']}"
                )
                skill_key = self._legacy_token("skill-key", identity_seed)
                skill_name = self._legacy_token("skill-name", identity_seed)
                provider = LEGACY_REDACTED
                source_class = LEGACY_REDACTED
            fingerprint = safe_hash(
                row["skill_fingerprint"], "legacy-skill-fingerprint"
            )
            model = row["model_class"]
            if model not in PERSISTED_MODEL_CLASSES:
                model = self._classify_model(model)
            detection = row["detection"]
            if detection not in PERSISTED_RUN_DETECTIONS:
                detection = LEGACY_REDACTED
            status = row["status"] if row["status"] in RUN_STATUSES else "interrupted"
            started_at = self._canonical_timestamp_or_legacy(row["started_at"])
            if status == "running":
                ended_at = None
                duration_ms = None
                end_reason = "running"
                duration_quality = "pending"
            else:
                ended_at = self._canonical_timestamp_or_legacy(
                    row["ended_at"], allow_none=True
                )
                if ended_at is None:
                    ended_at = started_at
                duration_ms = row["duration_ms"]
                if (
                    isinstance(duration_ms, bool)
                    or not isinstance(duration_ms, int)
                    or duration_ms < 0
                ):
                    duration_ms = 0
                end_reason = "legacy-unknown"
                duration_quality = "unknown"
            failures = row["tool_failure_count"]
            if (
                isinstance(failures, bool)
                or not isinstance(failures, int)
                or failures < 0
            ):
                failures = 0
            db.execute(
                """UPDATE skill_runs
                   SET skill_key=?,skill_name=?,provider=?,source_class=?,
                       skill_fingerprint=?,idempotency_key=?,session_hash=?,
                       turn_hash=?,repo_hash=?,model_class=?,detection=?,status=?,
                       started_at=?,ended_at=?,duration_ms=?,
                       tool_failure_count=?,provenance_trust=?,end_reason=?,
                       duration_quality=?
                   WHERE rowid=?""",
                (
                    skill_key,
                    skill_name,
                    provider,
                    source_class,
                    fingerprint,
                    safe_hash(
                        row["idempotency_key"],
                        "legacy-run-idempotency",
                    ),
                    safe_hash(row["session_hash"], "legacy-session"),
                    safe_hash(row["turn_hash"], "legacy-turn"),
                    safe_hash(row["repo_hash"], "legacy-repo"),
                    model,
                    detection,
                    status,
                    started_at,
                    ended_at,
                    duration_ms,
                    failures,
                    LEGACY_PROVENANCE,
                    end_reason,
                    duration_quality,
                    row["rowid"],
                ),
            )

        for row in db.execute(
            """SELECT rowid,sentiment,rating,feeling_class,source,confidence,
                      reaction_signature,created_at FROM skill_feedback"""
        ).fetchall():
            sentiment = row["sentiment"] if row["sentiment"] in SENTIMENTS else "mixed"
            rating = row["rating"]
            if (
                rating is not None
                and (
                    isinstance(rating, bool)
                    or not isinstance(rating, int)
                    or not 1 <= rating <= 5
                )
            ):
                rating = None
            feeling = (
                row["feeling_class"]
                if row["feeling_class"] in PERSISTED_FEELING_CLASSES
                else LEGACY_REDACTED
            )
            source = (
                row["source"]
                if row["source"] in PERSISTED_FEEDBACK_SOURCES
                else LEGACY_REDACTED
            )
            confidence = row["confidence"]
            if (
                isinstance(confidence, bool)
                or not isinstance(confidence, (int, float))
                or not 0.0 <= confidence <= 1.0
            ):
                confidence = 0.0
            signature = self._legacy_hmac(
                secret, "legacy-reaction", row["reaction_signature"]
            )
            db.execute(
                """UPDATE skill_feedback
                   SET sentiment=?,rating=?,feeling_class=?,source=?,
                       confidence=?,reaction_signature=?,created_at=?
                   WHERE rowid=?""",
                (
                    sentiment,
                    rating,
                    feeling,
                    source,
                    confidence,
                    signature,
                    self._canonical_timestamp_or_legacy(row["created_at"]),
                    row["rowid"],
                ),
            )

        for row in db.execute(
            """SELECT rowid,skill_fingerprint,rubric_version,outcome,
                      outcome_achieved,completion_evidence,authority_safety,
                      avoidable_rework,efficient_recoverable,total_score,
                      evidence_classes,evidence_refs,evaluator,reviewed_at
               FROM skill_evaluations"""
        ).fetchall():
            rubric = row["rubric_version"]
            if not self._valid_rubric_version(rubric, persisted=True):
                rubric = self._legacy_token("rubric", rubric)
            invalid_class = False
            try:
                raw_classes = json.loads(row["evidence_classes"])
            except (TypeError, json.JSONDecodeError):
                raw_classes = []
                invalid_class = True
            if not isinstance(raw_classes, list):
                raw_classes = [raw_classes]
                invalid_class = True
            classes: set[str] = set()
            for item in raw_classes:
                if (
                    isinstance(item, str)
                    and item in PERSISTED_EVALUATION_CLASSES
                ):
                    classes.add(item)
                else:
                    invalid_class = True
            if invalid_class:
                classes.add(LEGACY_REDACTED)
            normalized_classes = sorted(classes)
            try:
                raw_refs = json.loads(row["evidence_refs"])
            except (TypeError, json.JSONDecodeError):
                raw_refs = [row["evidence_refs"]]
            if not isinstance(raw_refs, list):
                raw_refs = [raw_refs]
            refs = sorted(
                {
                    self._pseudonymize_evidence_reference(
                        item, strict=False, secret=secret
                    )
                    for item in raw_refs
                }
            )
            evaluator = (
                row["evaluator"]
                if row["evaluator"] in PERSISTED_EVALUATORS
                else LEGACY_REDACTED
            )
            outcome = (
                row["outcome"]
                if row["outcome"] in EVALUATION_OUTCOMES
                else "unverified"
            )
            score_names = sorted(RUBRIC_CRITERIA)
            scores: dict[str, int] | None = {
                name: row[name] for name in score_names
            }
            if outcome == "unverified" or any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= 2
                for value in scores.values()
            ):
                outcome = "unverified"
                scores = None
            elif not normalized_classes or not refs:
                outcome = "unverified"
                scores = None
            else:
                scores["avoidable_rework"] = min(
                    scores["avoidable_rework"], 1
                )
                scores["efficient_recoverable"] = min(
                    scores["efficient_recoverable"], 1
                )
                completion_present = bool(
                    set(normalized_classes) & COMPLETION_EVIDENCE_CLASSES
                )
                if (
                    scores["outcome_achieved"] == 2
                    or scores["completion_evidence"] == 2
                ) and not completion_present:
                    scores["outcome_achieved"] = min(
                        scores["outcome_achieved"], 1
                    )
                    scores["completion_evidence"] = min(
                        scores["completion_evidence"], 1
                    )
                if (
                    scores["authority_safety"] == 2
                    and "authority" not in normalized_classes
                ):
                    scores["authority_safety"] = 1
                domain_verdict_present = (
                    "domain-verdict" in normalized_classes
                )
                if (
                    scores["outcome_achieved"] == 2
                    and not domain_verdict_present
                ):
                    outcome = "unverified"
                    scores = None
                if outcome == "verified-success" and (
                    scores is None
                    or not domain_verdict_present
                    or scores["outcome_achieved"] != 2
                    or scores["completion_evidence"] != 2
                    or scores["authority_safety"] != 2
                ):
                    outcome = "unverified"
                    scores = None
                elif outcome != "verified-success" and all(
                    scores[name] == 2
                    for name in (
                        "outcome_achieved",
                        "completion_evidence",
                        "authority_safety",
                    )
                ):
                    outcome = "unverified"
                    scores = None
                elif outcome == "partial" and (
                    scores["outcome_achieved"] != 1
                    or scores["completion_evidence"] < 1
                ):
                    outcome = "unverified"
                    scores = None
                elif outcome == "rework-required" and (
                    scores["outcome_achieved"] > 1
                    or scores["completion_evidence"] > 1
                ):
                    outcome = "unverified"
                    scores = None
                elif outcome == "rejected" and (
                    scores["outcome_achieved"] != 0
                    or scores["authority_safety"] != 0
                ):
                    outcome = "unverified"
                    scores = None
            score_values = (
                {name: None for name in score_names}
                if scores is None
                else scores
            )
            total = None if scores is None else sum(scores.values())
            db.execute(
                """UPDATE skill_evaluations
                   SET skill_fingerprint=?,rubric_version=?,outcome=?,
                       outcome_achieved=?,completion_evidence=?,
                       authority_safety=?,avoidable_rework=?,
                       efficient_recoverable=?,total_score=?,
                       evidence_classes=?,evidence_refs=?,evaluator=?,
                       reviewed_at=? WHERE rowid=?""",
                (
                    safe_hash(
                        row["skill_fingerprint"],
                        "legacy-skill-fingerprint",
                    ),
                    rubric,
                    outcome,
                    score_values["outcome_achieved"],
                    score_values["completion_evidence"],
                    score_values["authority_safety"],
                    score_values["avoidable_rework"],
                    score_values["efficient_recoverable"],
                    total,
                    json.dumps(normalized_classes, separators=(",", ":")),
                    json.dumps(refs, separators=(",", ":")),
                    evaluator,
                    self._canonical_timestamp_or_legacy(row["reviewed_at"]),
                    row["rowid"],
                ),
            )

        for row in db.execute(
            """SELECT rowid,idempotency_key,session_hash,turn_hash,repo_hash,
                      evidence_class,result,subject_hash,detection,observed_at
               FROM skill_evidence"""
        ).fetchall():
            detection = (
                row["detection"]
                if row["detection"] in PERSISTED_EVIDENCE_DETECTIONS
                else LEGACY_REDACTED
            )
            subject_hash = self._legacy_hmac(
                secret, "legacy-evidence-subject", row["subject_hash"]
            )
            evidence_class = (
                row["evidence_class"]
                if row["evidence_class"]
                in EVIDENCE_CLASSES | {LEGACY_REDACTED}
                else LEGACY_REDACTED
            )
            result = (
                row["result"]
                if row["result"] in EVIDENCE_RESULTS
                else "ambiguous"
            )
            db.execute(
                """UPDATE skill_evidence
                   SET idempotency_key=?,session_hash=?,turn_hash=?,repo_hash=?,
                       evidence_class=?,result=?,subject_hash=?,detection=?,
                       observed_at=?,provenance_trust=? WHERE rowid=?""",
                (
                    safe_hash(
                        row["idempotency_key"],
                        "legacy-evidence-idempotency",
                    ),
                    safe_hash(row["session_hash"], "legacy-session"),
                    safe_hash(row["turn_hash"], "legacy-turn"),
                    safe_hash(row["repo_hash"], "legacy-repo"),
                    evidence_class,
                    result,
                    subject_hash,
                    detection,
                    self._canonical_timestamp_or_legacy(row["observed_at"]),
                    LEGACY_PROVENANCE,
                    row["rowid"],
                ),
            )

        for row in db.execute(
            """SELECT rowid,observed_at,hook_name,status,detail_class
               FROM collector_health"""
        ).fetchall():
            hook = (
                row["hook_name"]
                if row["hook_name"] in PERSISTED_HEALTH_HOOKS
                else LEGACY_REDACTED
            )
            status = (
                row["status"]
                if row["status"] in PERSISTED_HEALTH_STATUSES
                else LEGACY_REDACTED
            )
            detail = (
                row["detail_class"]
                if isinstance(row["detail_class"], str)
                and HEALTH_DETAIL_RE.fullmatch(row["detail_class"])
                else LEGACY_REDACTED
            )
            db.execute(
                """UPDATE collector_health
                   SET observed_at=?,hook_name=?,status=?,detail_class=?
                   WHERE rowid=?""",
                (
                    self._canonical_timestamp_or_legacy(row["observed_at"]),
                    hook,
                    status,
                    detail,
                    row["rowid"],
                ),
            )

        for row in db.execute(
            "SELECT rowid,linked_at FROM skill_run_evidence"
        ).fetchall():
            db.execute(
                "UPDATE skill_run_evidence SET linked_at=? WHERE rowid=?",
                (
                    self._canonical_timestamp_or_legacy(row["linked_at"]),
                    row["rowid"],
                ),
            )

        for row in db.execute(
            "SELECT rowid,event_id,processed_at FROM spool_receipts"
        ).fetchall():
            event_id = safe_hash(row["event_id"], "legacy-spool-event")
            db.execute(
                """UPDATE spool_receipts
                   SET event_id=?,processed_at=? WHERE rowid=?""",
                (
                    event_id,
                    self._canonical_timestamp_or_legacy(row["processed_at"]),
                    row["rowid"],
                ),
            )

        for row in db.execute(
            """SELECT rowid,session_hash,turn_hash,prompt_started_at,stopped_at
               FROM turn_lifecycle"""
        ).fetchall():
            db.execute(
                """UPDATE turn_lifecycle
                   SET session_hash=?,turn_hash=?,prompt_started_at=?,
                       stopped_at=? WHERE rowid=?""",
                (
                    safe_hash(row["session_hash"], "legacy-session"),
                    safe_hash(row["turn_hash"], "legacy-turn"),
                    self._canonical_timestamp_or_legacy(
                        row["prompt_started_at"], allow_none=True
                    ),
                    self._canonical_timestamp_or_legacy(
                        row["stopped_at"], allow_none=True
                    ),
                    row["rowid"],
                ),
            )

        fixed_values = {
            "schema_version": str(SCHEMA_VERSION),
            "spool_schema_version": str(SPOOL_SCHEMA_VERSION),
            "component_version": COMPONENT_VERSION,
            "privacy_repair_version": PRIVACY_REPAIR_PENDING,
        }
        for row in db.execute("SELECT rowid,key,value FROM meta").fetchall():
            key = row["key"]
            if key in fixed_values:
                safe_key, safe_value = key, fixed_values[key]
            elif key in TIME_META_KEYS:
                safe_key = key
                safe_value = self._canonical_timestamp_or_legacy(row["value"])
            else:
                safe_key = "legacy-" + self._legacy_hmac(
                    secret, "legacy-meta-key", key
                )
                safe_value = LEGACY_REDACTED
            db.execute(
                "UPDATE meta SET key=?,value=? WHERE rowid=?",
                (safe_key, safe_value, row["rowid"]),
            )

    def _sidecars_present(self) -> bool:
        try:
            return any(
                Path(str(self.db_path) + suffix).exists()
                for suffix in ("-wal", "-shm")
            )
        except OSError:
            # If sidecar state cannot be established, immutable reads are unsafe.
            return True

    def _open_read_candidate(
        self, *, immutable: bool
    ) -> sqlite3.Connection:
        suffix = "?mode=ro&immutable=1" if immutable else "?mode=ro"
        target = self.db_path.as_uri() + suffix
        db: sqlite3.Connection | None = None
        try:
            db = sqlite3.connect(
                target,
                timeout=SQLITE_BUSY_TIMEOUT_MS / 1000,
                uri=True,
                factory=RetryingConnection,
            )
            db.row_factory = sqlite3.Row
            db.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
            db.execute("PRAGMA query_only=ON")
            self._probe_read_connection(db, immutable=immutable)
            return db
        except Exception:
            if db is not None:
                db.close()
            raise

    @staticmethod
    def _probe_read_connection(
        db: sqlite3.Connection, *, immutable: bool
    ) -> None:
        # sqlite3.connect and connection-local PRAGMAs may succeed even when the
        # first schema read cannot access directory sidecars.
        db.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()

    def _open(self, *, read_only: bool) -> sqlite3.Connection:
        if read_only:
            self._last_read_mode = None
            try:
                db = self._open_read_candidate(immutable=False)
                self._last_read_mode = "read-only"
                return db
            except sqlite3.Error as primary_error:
                if self._sidecars_present():
                    raise
                try:
                    db = self._open_read_candidate(immutable=True)
                except sqlite3.Error:
                    raise primary_error
                # Never ignore a WAL that appeared while the fallback opened.
                if self._sidecars_present():
                    db.close()
                    raise primary_error
                self._last_read_mode = "immutable"
                return db
        db = sqlite3.connect(
            self.db_path,
            timeout=SQLITE_BUSY_TIMEOUT_MS / 1000,
            factory=RetryingConnection,
        )
        db.row_factory = sqlite3.Row
        db.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def connect(self) -> sqlite3.Connection:
        self._ensure_schema()
        return self._open(read_only=False)

    @contextmanager
    def connection(self):
        db = self.connect()
        try:
            yield db
            retry_busy(db.commit)
        except Exception:
            try:
                db.rollback()
            except sqlite3.Error:
                pass
            raise
        finally:
            db.close()

    def record_selection_audit(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Persist a body-free, conservative selection audit in the telemetry DB."""
        if not isinstance(payload, dict):
            raise ValueError("selection audit must be an object")
        audit_id = payload.get("audit_id", "")
        if audit_id:
            audit_id = self._require_identity(audit_id, "audit id", limit=160)
        job_id = self._require_identity(payload.get("job_id"), "job id", limit=160)
        session_id = self._require_identity(payload.get("session_id"), "session id", limit=160)
        turn_id = self._require_identity(payload.get("turn_id"), "turn id", limit=160)
        registry_revision = self._require_identity(payload.get("registry_revision"), "registry revision", limit=160)
        taxonomy_version = self._require_identity(payload.get("taxonomy_version"), "taxonomy version", limit=160)
        observation_state = payload.get("observation_state")
        telemetry_health = payload.get("telemetry_health")
        if observation_state not in {"complete", "incomplete", "failed"}:
            raise ValueError("invalid observation state")
        if telemetry_health not in {"complete", "degraded", "failed"}:
            raise ValueError("invalid telemetry health")
        booleans = ("observation_window_closed", "runner_terminal", "cardinality_ok")
        if any(not isinstance(payload.get(name), bool) for name in booleans):
            raise ValueError("selection audit completeness flags must be boolean")
        spool_pending = payload.get("spool_pending")
        if isinstance(spool_pending, bool) or not isinstance(spool_pending, int) or spool_pending < 0:
            raise ValueError("invalid spool_pending")
        candidates = payload.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise ValueError("selection audit candidates are required")
        normalized: list[dict[str, Any]] = []
        for item in candidates:
            if not isinstance(item, dict):
                raise ValueError("invalid candidate")
            skill_key = self._require_identity(item.get("skill_key"), "skill key", limit=160)
            source = item.get("source")
            provenance = item.get("provenance")
            coverage = item.get("coverage")
            if source not in {"registry_profile", "planner_candidate", "unfiltered_baseline"}:
                raise ValueError("invalid candidate source")
            if provenance not in {"registry", "planner", "baseline"}:
                raise ValueError("invalid candidate provenance")
            if coverage not in {"known", "unknown"}:
                raise ValueError("invalid candidate coverage")
            source_revision = self._require_identity(item.get("source_revision"), "source revision", limit=160)
            source_digest = item.get("source_digest")
            if not isinstance(source_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", source_digest):
                raise ValueError("invalid source digest")
            classification = item.get("classification")
            if classification not in {"selected", "not_observable", "not_comparable", "candidate_coverage_unknown"}:
                raise ValueError("invalid selection classification")
            reason_code = self._require_identity(item.get("reason_code"), "reason code", limit=80)
            source_json = json.dumps({"source": source, "provenance": provenance}, sort_keys=True, separators=(",", ":"))
            candidate_digest = hashlib.sha256(
                f"{skill_key}|{source_json}|{source_revision}|{source_digest}|{coverage}|{classification}|{reason_code}".encode("utf-8")
            ).hexdigest()
            normalized.append({
                "skill_key": skill_key,
                "source_json": source_json,
                "source_revision": source_revision,
                "source_digest": source_digest,
                "provenance": provenance,
                "coverage": coverage,
                "classification": classification,
                "reason_code": reason_code,
                "candidate_digest": candidate_digest,
            })
        session_hash = self.pseudonym(session_id, "session")
        turn_hash = self.pseudonym(turn_id, "turn")
        job_hash = self.pseudonym(job_id, "job")
        if not audit_id:
            audit_id = "selectionaudit_" + hmac.new(
                self._secret(), f"audit:{session_hash}:{turn_hash}".encode("utf-8"), hashlib.sha256
            ).hexdigest()[:32]
        candidate_digest = hashlib.sha256(
            json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        with self.connection() as db:
            db.execute(
                """INSERT OR IGNORE INTO skill_selection_audits
                   (audit_id,job_hash,session_hash,turn_hash,registry_revision,
                    taxonomy_version,observation_state,observation_window_closed,
                    telemetry_health,runner_terminal,spool_pending,cardinality_ok,
                    candidate_digest,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (audit_id, job_hash, session_hash, turn_hash, registry_revision,
                 taxonomy_version, observation_state, int(payload["observation_window_closed"]),
                 telemetry_health, int(payload["runner_terminal"]), spool_pending,
                 int(payload["cardinality_ok"]), candidate_digest, utc_now()),
            )
            for item in normalized:
                db.execute(
                    """INSERT OR IGNORE INTO skill_selection_candidates
                       (audit_id,skill_key,source_json,source_revision,source_digest,
                        provenance,coverage,classification,reason_code,candidate_digest)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (audit_id, item["skill_key"], item["source_json"], item["source_revision"],
                     item["source_digest"], item["provenance"], item["coverage"],
                     item["classification"], item["reason_code"], item["candidate_digest"]),
                )
        return {"audit_id": audit_id, "candidate_digest": candidate_digest, "persisted": True}

    def record_selection_audit_v2(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Persist the strict v2 selection-audit contract atomically.

        The v2 adapter owns input normalization and classification. This method
        owns the final storage boundary, including idempotency for one
        session/turn and the canonical candidate-array digest check.
        """
        allowed = {
            "audit_id", "job_id", "session_id", "turn_id", "registry_revision",
            "taxonomy_version", "observation_state", "observation_window_closed",
            "telemetry_health", "runner_terminal", "spool_pending", "cardinality_ok",
            "candidate_digest", "candidates",
        }
        if not isinstance(payload, dict) or set(payload) != allowed:
            raise ValueError("invalid selection audit v2 fields")
        audit_id = self._require_identity(payload["audit_id"], "audit id", limit=160)
        job_id = self._require_identity(payload["job_id"], "job id", limit=160)
        session_id = self._require_identity(payload["session_id"], "session id", limit=160)
        turn_id = self._require_identity(payload["turn_id"], "turn id", limit=160)
        registry_revision = self._require_identity(
            payload["registry_revision"], "registry revision", limit=160
        )
        taxonomy_version = self._require_identity(
            payload["taxonomy_version"], "taxonomy version", limit=160
        )
        if payload["observation_state"] not in {"complete", "incomplete", "failed"}:
            raise ValueError("invalid observation state")
        if payload["telemetry_health"] not in {"complete", "degraded", "failed"}:
            raise ValueError("invalid telemetry health")
        for name in ("observation_window_closed", "runner_terminal", "cardinality_ok"):
            if not isinstance(payload[name], bool):
                raise ValueError("selection audit v2 completeness flags must be boolean")
        spool_pending = payload["spool_pending"]
        if isinstance(spool_pending, bool) or not isinstance(spool_pending, int) or spool_pending < 0:
            raise ValueError("invalid spool_pending")
        candidate_digest = payload["candidate_digest"]
        if not isinstance(candidate_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", candidate_digest):
            raise ValueError("invalid candidate digest")
        candidates = payload["candidates"]
        if not isinstance(candidates, list) or not candidates:
            raise ValueError("selection audit v2 candidates are required")
        candidate_keys = {
            "skill_key", "source", "source_revision", "source_digest", "coverage",
            "provenance", "classification", "reason_code",
        }
        normalized: list[dict[str, Any]] = []
        canonical: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in candidates:
            if not isinstance(item, dict) or set(item) != candidate_keys:
                raise ValueError("invalid selection audit v2 candidate fields")
            skill_key = self._require_identity(item["skill_key"], "skill key", limit=160)
            if skill_key in seen:
                raise ValueError("duplicate candidate skill key")
            seen.add(skill_key)
            if item["source"] not in {"registry_profile", "planner_candidate", "unfiltered_baseline"}:
                raise ValueError("invalid candidate source")
            if item["provenance"] not in {"registry", "planner", "baseline"}:
                raise ValueError("invalid candidate provenance")
            source_revision = self._require_identity(
                item["source_revision"], "source revision", limit=160
            )
            source_digest = item["source_digest"]
            if not isinstance(source_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", source_digest):
                raise ValueError("invalid source digest")
            if item["coverage"] not in {"known", "unknown"}:
                raise ValueError("invalid candidate coverage")
            if item["classification"] not in {
                "selected", "not_observable", "not_comparable", "candidate_coverage_unknown",
            }:
                raise ValueError("invalid selection classification")
            reason_code = self._require_identity(item["reason_code"], "reason code", limit=80)
            canonical_item = {
                "skill_key": skill_key,
                "source": item["source"],
                "source_revision": source_revision,
                "source_digest": source_digest,
                "coverage": item["coverage"],
            }
            canonical.append(canonical_item)
            normalized.append({
                **canonical_item,
                "provenance": item["provenance"],
                "classification": item["classification"],
                "reason_code": reason_code,
            })
        computed_digest = hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        ).hexdigest()
        if not hmac.compare_digest(candidate_digest, computed_digest):
            raise ValueError("candidate digest mismatch")
        session_hash = self.pseudonym(session_id, "session")
        turn_hash = self.pseudonym(turn_id, "turn")
        job_hash = self.pseudonym(job_id, "job")
        with self.connection() as db:
            existing = db.execute(
                """SELECT audit_id,candidate_digest FROM skill_selection_audits
                   WHERE session_hash=? AND turn_hash=?""",
                (session_hash, turn_hash),
            ).fetchone()
            if existing:
                if existing["audit_id"] != audit_id or existing["candidate_digest"] != candidate_digest:
                    raise ValueError("selection audit payload conflicts with existing audit")
                return {"audit_id": audit_id, "candidate_digest": candidate_digest, "persisted": True}
            db.execute(
                """INSERT INTO skill_selection_audits
                   (audit_id,job_hash,session_hash,turn_hash,registry_revision,
                    taxonomy_version,observation_state,observation_window_closed,
                    telemetry_health,runner_terminal,spool_pending,cardinality_ok,
                    candidate_digest,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    audit_id, job_hash, session_hash, turn_hash, registry_revision,
                    taxonomy_version, payload["observation_state"],
                    int(payload["observation_window_closed"]), payload["telemetry_health"],
                    int(payload["runner_terminal"]), spool_pending,
                    int(payload["cardinality_ok"]), candidate_digest, utc_now(),
                ),
            )
            for item in normalized:
                source_json = json.dumps(
                    {"source": item["source"], "provenance": item["provenance"]},
                    sort_keys=True,
                    separators=(",", ":"),
                )
                item_digest = hashlib.sha256(
                    json.dumps(
                        {key: item[key] for key in ("skill_key", "source", "source_revision", "source_digest", "coverage")},
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                    ).encode("utf-8")
                ).hexdigest()
                db.execute(
                    """INSERT INTO skill_selection_candidates
                       (audit_id,skill_key,source_json,source_revision,source_digest,
                        provenance,coverage,classification,reason_code,candidate_digest)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        audit_id, item["skill_key"], source_json, item["source_revision"],
                        item["source_digest"], item["provenance"], item["coverage"],
                        item["classification"], item["reason_code"], item_digest,
                    ),
                )
        return {"audit_id": audit_id, "candidate_digest": candidate_digest, "persisted": True}

    @contextmanager
    def read_connection(self):
        if not self.db_path.is_file():
            raise FileNotFoundError(self.db_path)
        db = self._open(read_only=True)
        try:
            yield db
        finally:
            db.close()

    def _schema_current(self) -> bool:
        if not self.db_path.is_file():
            return False
        try:
            with self.read_connection() as db:
                meta = {
                    row["key"]: row["value"]
                    for row in db.execute(
                        """SELECT key,value FROM meta
                           WHERE key IN (
                             'schema_version','privacy_repair_version',
                             'component_version'
                           )"""
                    ).fetchall()
                }
                expected_tables = {
                    "skill_runs",
                    "skill_feedback",
                    "collector_health",
                    "skill_evaluations",
                    "skill_evidence",
                    "skill_run_evidence",
                    "spool_receipts",
                    "turn_lifecycle",
                    "skill_selection_audits",
                    "skill_selection_candidates",
                    "meta",
                }
                tables = {
                    item["name"]
                    for item in db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                run_columns = {
                    row["name"]
                    for row in db.execute(
                        "PRAGMA table_info(skill_runs)"
                    ).fetchall()
                }
                evidence_columns = {
                    row["name"]
                    for row in db.execute(
                        "PRAGMA table_info(skill_evidence)"
                    ).fetchall()
                }
                indexes = {
                    row["name"]
                    for row in db.execute(
                        "SELECT name FROM sqlite_master WHERE type='index'"
                    ).fetchall()
                }
            return bool(
                meta.get("schema_version") == str(SCHEMA_VERSION)
                and meta.get("privacy_repair_version")
                == PRIVACY_REPAIR_VERSION
                and meta.get("component_version") == COMPONENT_VERSION
                and expected_tables <= tables
                and "provenance_trust" in run_columns
                and "end_reason" in run_columns
                and "duration_quality" in run_columns
                and "detection_class" in run_columns
                and "provenance_trust" in evidence_columns
                and "idx_runs_session_time" in indexes
            )
        except (OSError, sqlite3.Error):
            return False

    @staticmethod
    def _privacy_repair_value(db: sqlite3.Connection) -> str | None:
        try:
            row = db.execute(
                """SELECT value FROM meta
                   WHERE key='privacy_repair_version'"""
            ).fetchone()
        except sqlite3.Error:
            return None
        return str(row[0]) if row else None

    @staticmethod
    def _checkpoint_pending_privacy_repair(
        db: sqlite3.Connection,
    ) -> None:
        if db.in_transaction:
            raise RuntimeError(
                "privacy repair checkpoint requires no active transaction"
            )
        try:
            checkpoint = db.execute(
                "PRAGMA wal_checkpoint(TRUNCATE)"
            ).fetchone()
        except sqlite3.OperationalError as error:
            if is_busy_error(error):
                raise PrivacyRepairPendingError(
                    "privacy-repair-pending"
                ) from error
            raise
        if checkpoint is None or int(checkpoint[0]) != 0:
            raise PrivacyRepairPendingError("privacy-repair-pending")

    @staticmethod
    def _mark_privacy_repair_final(db: sqlite3.Connection) -> None:
        db.execute(
            """INSERT OR REPLACE INTO meta
               VALUES('privacy_repair_version',?)""",
            (PRIVACY_REPAIR_VERSION,),
        )
        retry_busy(db.commit)

    def _ensure_schema(self) -> None:
        if self._initialized:
            return
        if self._schema_current():
            self._initialized = True
            return
        self.root.mkdir(parents=True, exist_ok=True)
        with process_lock(self.lock_path):
            if self._schema_current():
                self._initialized = True
                return
            db = self._open(read_only=False)
            try:
                # Legacy identifier repair may need to rewrite a parent key and
                # its children together. Disable immediate enforcement only for
                # this migration transaction, then explicitly verify all
                # constraints before committing.
                db.execute("PRAGMA foreign_keys=OFF")
                db.execute("PRAGMA secure_delete=ON")
                db.executescript(
                    """
                    PRAGMA journal_mode=WAL;
                    CREATE TABLE IF NOT EXISTS skill_runs(
                      run_id TEXT PRIMARY KEY,
                      idempotency_key TEXT NOT NULL UNIQUE,
                      skill_key TEXT NOT NULL,
                      skill_name TEXT NOT NULL,
                      provider TEXT NOT NULL,
                      source_class TEXT NOT NULL,
                      skill_fingerprint TEXT NOT NULL,
                      session_hash TEXT NOT NULL,
                      turn_hash TEXT NOT NULL,
                      repo_hash TEXT NOT NULL,
                      model_class TEXT NOT NULL,
                      detection TEXT NOT NULL,
                      status TEXT NOT NULL,
                      started_at TEXT NOT NULL,
                      ended_at TEXT,
                      duration_ms INTEGER,
                      tool_failure_count INTEGER NOT NULL DEFAULT 0,
                      provenance_trust TEXT NOT NULL DEFAULT 'legacy-unverified',
                      end_reason TEXT NOT NULL DEFAULT 'legacy-unknown',
                      duration_quality TEXT NOT NULL DEFAULT 'unknown',
                      detection_class TEXT NOT NULL DEFAULT 'legacy-unknown'
                    );
                    CREATE INDEX IF NOT EXISTS idx_runs_session_turn
                      ON skill_runs(session_hash,turn_hash,status);
                    CREATE INDEX IF NOT EXISTS idx_runs_skill_time
                      ON skill_runs(skill_key,started_at);
                    CREATE INDEX IF NOT EXISTS idx_runs_session_time
                      ON skill_runs(session_hash,started_at,status);
                    CREATE TABLE IF NOT EXISTS skill_feedback(
                      feedback_id TEXT PRIMARY KEY,
                      run_id TEXT NOT NULL REFERENCES skill_runs(run_id),
                      sentiment TEXT NOT NULL,
                      rating INTEGER,
                      feeling_class TEXT NOT NULL,
                      source TEXT NOT NULL,
                      confidence REAL NOT NULL,
                      reaction_signature TEXT NOT NULL,
                      created_at TEXT NOT NULL,
                      UNIQUE(run_id,reaction_signature)
                    );
                    CREATE TABLE IF NOT EXISTS collector_health(
                      observed_at TEXT NOT NULL,
                      hook_name TEXT NOT NULL,
                      status TEXT NOT NULL,
                      detail_class TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS skill_evaluations(
                      evaluation_id TEXT PRIMARY KEY,
                      run_id TEXT NOT NULL REFERENCES skill_runs(run_id),
                      skill_fingerprint TEXT NOT NULL,
                      rubric_version TEXT NOT NULL,
                      outcome TEXT NOT NULL,
                      outcome_achieved INTEGER,
                      completion_evidence INTEGER,
                      authority_safety INTEGER,
                      avoidable_rework INTEGER,
                      efficient_recoverable INTEGER,
                      total_score INTEGER,
                      evidence_classes TEXT NOT NULL,
                      evidence_refs TEXT NOT NULL,
                      evaluator TEXT NOT NULL,
                      reviewed_at TEXT NOT NULL,
                      UNIQUE(run_id,rubric_version)
                    );
                    CREATE INDEX IF NOT EXISTS idx_evaluations_skill
                      ON skill_evaluations(skill_fingerprint,reviewed_at);
                    CREATE TABLE IF NOT EXISTS skill_evidence(
                      evidence_id TEXT PRIMARY KEY,
                      idempotency_key TEXT NOT NULL UNIQUE,
                      session_hash TEXT NOT NULL,
                      turn_hash TEXT NOT NULL,
                      repo_hash TEXT NOT NULL,
                      evidence_class TEXT NOT NULL,
                      result TEXT NOT NULL,
                      subject_hash TEXT NOT NULL,
                      detection TEXT NOT NULL,
                      observed_at TEXT NOT NULL,
                      provenance_trust TEXT NOT NULL DEFAULT 'legacy-unverified'
                    );
                    CREATE INDEX IF NOT EXISTS idx_evidence_session_turn
                      ON skill_evidence(session_hash,turn_hash,observed_at);
                    CREATE TABLE IF NOT EXISTS skill_run_evidence(
                      run_id TEXT NOT NULL REFERENCES skill_runs(run_id) ON DELETE CASCADE,
                      evidence_id TEXT NOT NULL REFERENCES skill_evidence(evidence_id) ON DELETE CASCADE,
                      linked_at TEXT NOT NULL,
                      PRIMARY KEY(run_id,evidence_id)
                    );
                    CREATE INDEX IF NOT EXISTS idx_run_evidence_evidence
                      ON skill_run_evidence(evidence_id,run_id);
                    CREATE TABLE IF NOT EXISTS spool_receipts(
                      event_id TEXT PRIMARY KEY,
                      processed_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS turn_lifecycle(
                      session_hash TEXT NOT NULL,
                      turn_hash TEXT NOT NULL,
                      prompt_started_at TEXT,
                      stopped_at TEXT,
                      PRIMARY KEY(session_hash,turn_hash)
                    );
                    CREATE INDEX IF NOT EXISTS idx_turn_lifecycle_session_prompt
                      ON turn_lifecycle(session_hash,prompt_started_at);
                    CREATE TABLE IF NOT EXISTS skill_selection_audits(
                      audit_id TEXT PRIMARY KEY,
                      job_hash TEXT NOT NULL,
                      session_hash TEXT NOT NULL,
                      turn_hash TEXT NOT NULL,
                      registry_revision TEXT NOT NULL,
                      taxonomy_version TEXT NOT NULL,
                      observation_state TEXT NOT NULL,
                      observation_window_closed INTEGER NOT NULL CHECK(observation_window_closed IN (0,1)),
                      telemetry_health TEXT NOT NULL,
                      runner_terminal INTEGER NOT NULL CHECK(runner_terminal IN (0,1)),
                      spool_pending INTEGER NOT NULL CHECK(spool_pending >= 0),
                      cardinality_ok INTEGER NOT NULL CHECK(cardinality_ok IN (0,1)),
                      candidate_digest TEXT NOT NULL,
                      created_at TEXT NOT NULL,
                      UNIQUE(session_hash,turn_hash,candidate_digest)
                    );
                    CREATE TABLE IF NOT EXISTS skill_selection_candidates(
                      audit_id TEXT NOT NULL REFERENCES skill_selection_audits(audit_id) ON DELETE CASCADE,
                      skill_key TEXT NOT NULL,
                      source_json TEXT NOT NULL,
                      source_revision TEXT NOT NULL,
                      source_digest TEXT NOT NULL,
                      provenance TEXT NOT NULL,
                      coverage TEXT NOT NULL,
                      classification TEXT NOT NULL CHECK(classification IN ('selected','not_observable','not_comparable','candidate_coverage_unknown')),
                      reason_code TEXT NOT NULL,
                      candidate_digest TEXT NOT NULL,
                      PRIMARY KEY(audit_id,skill_key)
                    );
                    CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                    """
                )
                run_columns = {
                    row[1]
                    for row in db.execute(
                        "PRAGMA table_info(skill_runs)"
                    ).fetchall()
                }
                lifecycle_columns_added = False
                if "provenance_trust" not in run_columns:
                    db.execute(
                        "ALTER TABLE skill_runs ADD COLUMN provenance_trust "
                        "TEXT NOT NULL DEFAULT 'legacy-unverified'"
                    )
                if "end_reason" not in run_columns:
                    db.execute(
                        "ALTER TABLE skill_runs ADD COLUMN end_reason "
                        "TEXT NOT NULL DEFAULT 'legacy-unknown'"
                    )
                    lifecycle_columns_added = True
                if "duration_quality" not in run_columns:
                    db.execute(
                        "ALTER TABLE skill_runs ADD COLUMN duration_quality "
                        "TEXT NOT NULL DEFAULT 'unknown'"
                    )
                    lifecycle_columns_added = True
                if "detection_class" not in run_columns:
                    # Pre-fix rows cannot be retroactively told apart into
                    # invocation vs. read (that is the bug being fixed), so
                    # they stay labeled 'legacy-unknown' rather than being
                    # silently reclassified as either.
                    db.execute(
                        "ALTER TABLE skill_runs ADD COLUMN detection_class "
                        "TEXT NOT NULL DEFAULT 'legacy-unknown'"
                    )
                evidence_columns = {
                    row[1]
                    for row in db.execute(
                        "PRAGMA table_info(skill_evidence)"
                    ).fetchall()
                }
                if "provenance_trust" not in evidence_columns:
                    db.execute(
                        "ALTER TABLE skill_evidence ADD COLUMN "
                        "provenance_trust TEXT NOT NULL "
                        "DEFAULT 'legacy-unverified'"
                    )
                if lifecycle_columns_added:
                    # Pre-v6 final durations have no persisted reason, so retain
                    # their numeric value but do not upgrade its certainty.
                    db.execute(
                        """UPDATE skill_runs
                           SET end_reason=CASE
                                 WHEN status='running' THEN 'running'
                                 ELSE 'legacy-unknown'
                               END,
                               duration_quality=CASE
                                 WHEN status='running' THEN 'pending'
                                 ELSE 'unknown'
                               END"""
                    )
                privacy_value = self._privacy_repair_value(db)
                schema_row = db.execute(
                    "SELECT value FROM meta WHERE key='schema_version'"
                ).fetchone()
                prior_schema = str(schema_row[0]) if schema_row else None
                if (
                    privacy_value == PRIVACY_REPAIR_VERSION
                    and prior_schema != str(SCHEMA_VERSION)
                    and not lifecycle_columns_added
                ):
                    # Normalize a partial or hand-built pre-v6 shape rather
                    # than accepting arbitrary lifecycle labels as trusted.
                    db.execute(
                        """UPDATE skill_runs
                           SET end_reason=CASE
                                 WHEN status='running' THEN 'running'
                                 ELSE 'legacy-unknown'
                               END,
                               duration_quality=CASE
                                 WHEN status='running' THEN 'pending'
                                 ELSE 'unknown'
                               END"""
                    )
                if privacy_value == PRIVACY_REPAIR_VERSION:
                    # A v5 database has already crossed the privacy boundary.
                    # Add only the v6 lifecycle contract; never re-key trusted
                    # identifiers or downgrade their provenance.
                    db.execute(
                        """INSERT OR REPLACE INTO meta
                           VALUES('schema_version',?)""",
                        (str(SCHEMA_VERSION),),
                    )
                    db.execute(
                        """INSERT OR REPLACE INTO meta
                           VALUES('spool_schema_version',?)""",
                        (str(SPOOL_SCHEMA_VERSION),),
                    )
                    db.execute(
                        """INSERT OR REPLACE INTO meta
                           VALUES('component_version',?)""",
                        (COMPONENT_VERSION,),
                    )
                    retry_busy(db.commit)
                    self._initialized = True
                    return
                if privacy_value in {
                    PRIVACY_REPAIR_LOGICAL_VERSION,
                    PRIVACY_REPAIR_LEGACY_PENDING,
                    PRIVACY_REPAIR_PENDING,
                }:
                    # Logical v2 repair has already transformed identifiers.
                    # Upgrade only the cleanup protocol marker; never HMAC rows
                    # again or downgrade trusted provenance.
                    db.execute(
                        """INSERT OR REPLACE INTO meta
                           VALUES('schema_version',?)""",
                        (str(SCHEMA_VERSION),),
                    )
                    db.execute(
                        """INSERT OR REPLACE INTO meta
                           VALUES('spool_schema_version',?)""",
                        (str(SPOOL_SCHEMA_VERSION),),
                    )
                    db.execute(
                        """INSERT OR REPLACE INTO meta
                           VALUES('component_version',?)""",
                        (COMPONENT_VERSION,),
                    )
                    if privacy_value != PRIVACY_REPAIR_PENDING:
                        db.execute(
                            """INSERT OR REPLACE INTO meta
                               VALUES('privacy_repair_version',?)""",
                            (PRIVACY_REPAIR_PENDING,),
                        )
                    retry_busy(db.commit)
                    self._checkpoint_pending_privacy_repair(db)
                    self._mark_privacy_repair_final(db)
                    self._initialized = True
                    return
                self._repair_legacy_privacy(db)
                db.execute(
                    "INSERT OR REPLACE INTO meta VALUES('schema_version',?)",
                    (str(SCHEMA_VERSION),),
                )
                db.execute(
                    "INSERT OR REPLACE INTO meta VALUES('spool_schema_version',?)",
                    (str(SPOOL_SCHEMA_VERSION),),
                )
                db.execute(
                    "INSERT OR REPLACE INTO meta VALUES('component_version',?)",
                    (COMPONENT_VERSION,),
                )
                db.execute(
                    """INSERT OR REPLACE INTO meta
                       VALUES('privacy_repair_version',?)""",
                    (PRIVACY_REPAIR_PENDING,),
                )
                violations = db.execute("PRAGMA foreign_key_check").fetchall()
                if violations:
                    raise sqlite3.IntegrityError(
                        "legacy repair left foreign-key violations"
                    )
                # Phase A: transformed rows and pending-v3 are committed
                # atomically. Final v3 is deliberately absent while any reader
                # can still keep pre-repair pages alive in the WAL.
                retry_busy(db.commit)
                self._checkpoint_pending_privacy_repair(db)
                # Phase C: only a successful transaction-free truncate
                # checkpoint permits the non-secret final marker.
                self._mark_privacy_repair_final(db)
            except Exception:
                try:
                    db.rollback()
                except sqlite3.Error:
                    pass
                raise
            finally:
                db.close()
        self._initialized = True

    @classmethod
    def _plugin_identity(
        cls, resolved: Path
    ) -> tuple[str, str, str, str] | None:
        """Authorize a Plugin Skill through its installed root and manifest."""
        cache_root = (cls._plugins_root() / "cache").resolve()
        try:
            if not resolved.is_relative_to(cache_root):
                return None
        except (OSError, ValueError):
            return None

        # A cached package is exactly:
        #   cache/<source>/<plugin>/<version>/skills/<skill>/SKILL.md
        # Calculate the package root from that fixed layout instead of trusting
        # the nearest ancestor manifest (which could live in a backup tree).
        package_root = resolved.parents[2]
        try:
            package_parts = package_root.relative_to(cache_root).parts
        except (OSError, ValueError):
            return None
        if len(package_parts) != 3:
            return None
        _, plugin_name, version_name = package_parts
        manifest_path = package_root / ".codex-plugin" / "plugin.json"
        try:
            with manifest_path.open("rb") as handle:
                manifest_bytes = handle.read(PLUGIN_MANIFEST_LIMIT + 1)
            if len(manifest_bytes) > PLUGIN_MANIFEST_LIMIT:
                return None
            manifest = json.loads(manifest_bytes.decode("utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(manifest, dict):
            return None
        provider = manifest.get("name")
        manifest_version = manifest.get("version")
        skills_entry = manifest.get("skills")
        if (
            not isinstance(provider, str)
            or provider != plugin_name
            or not isinstance(manifest_version, str)
            or manifest_version != version_name
            or not isinstance(skills_entry, str)
        ):
            return None
        skills_relative = Path(skills_entry)
        if skills_relative.is_absolute():
            return None
        try:
            skills_root = (package_root / skills_relative).resolve()
            if (
                not skills_root.is_relative_to(package_root)
                or resolved.parent.parent != skills_root
                or resolved.name != "SKILL.md"
            ):
                return None
        except (OSError, ValueError):
            return None
        name = resolved.parent.name.lower()
        key = f"{provider.lower()}:{name}"
        try:
            return cls._validate_skill_identity(
                key, name, provider.lower(), "plugin"
            )
        except ValueError:
            return None

    @classmethod
    def _identity(
        cls,
        path: Path,
        *,
        local_sources: dict[str, tuple[str, str, str, str]] | None = None,
    ) -> tuple[str, str, str, str, str]:
        """Derive identity only from an authorized registry/root/manifest."""
        resolved = path.resolve()
        if not resolved.is_file() or resolved.name != "SKILL.md":
            raise ValueError("Skill source is not a canonical SKILL.md file")

        sources = (
            local_sources
            if local_sources is not None
            else cls._canonical_local_sources()
        )
        identity = sources.get(cls._path_key(resolved))
        skills_root = Path(__file__).resolve().parents[2]
        if identity is None:
            system_root = (skills_root / ".system").resolve()
            agents_root = (Path.home() / ".agents" / "skills").resolve()
            if resolved.parent.parent == system_root:
                name = resolved.parent.name.lower()
                identity = cls._validate_skill_identity(
                    name, name, "openai-system", "system"
                )
            elif resolved.parent.parent == agents_root:
                name = resolved.parent.name.lower()
                identity = cls._validate_skill_identity(
                    name, name, "agents", "external"
                )
            else:
                identity = cls._plugin_identity(resolved)
        if identity is None:
            raise ValueError("Skill source is not authorized")
        fingerprint = hashlib.sha256(resolved.read_bytes()).hexdigest()
        return (*identity, fingerprint)

    @classmethod
    def _skill_source_candidates_for_name(
        cls,
        name: str,
        local_sources: dict[str, tuple[str, str, str, str]],
    ) -> list[Path]:
        """Build candidate SKILL.md paths for a Skill-tool invocation name.

        Mirrors the same authorized roots `_identity` already checks (local
        registry, system, agents, plugin cache) but resolves from a bare or
        plugin-qualified skill *name* instead of a path found in tool_input.
        """
        skills_root = Path(__file__).resolve().parents[2]
        system_root = (skills_root / ".system").resolve()
        agents_root = (Path.home() / ".agents" / "skills").resolve()
        if ":" in name:
            plugin_name, _, skill_name = name.partition(":")
        else:
            plugin_name, skill_name = None, name
        skill_name = skill_name.lower()
        if not skill_name or not HOOK_IDENTITY_RE.fullmatch(skill_name):
            return []
        candidates: list[Path] = [
            Path(path_str)
            for path_str, identity in local_sources.items()
            if identity[1] == skill_name
        ]
        candidates.append(system_root / skill_name / "SKILL.md")
        candidates.append(agents_root / skill_name / "SKILL.md")
        if plugin_name:
            cache_root = (cls._plugins_root() / "cache").resolve()
            try:
                candidates.extend(
                    cache_root.glob(
                        f"*/{plugin_name}/*/skills/{skill_name}/SKILL.md"
                    )
                )
            except OSError:
                pass
        return candidates

    def _skill_identities_from_invocation(
        self, tool_input: Any
    ) -> list[tuple[Path, tuple[str, str, str, str, str]]]:
        """Resolve the skill named by a Skill-tool call's own argument.

        The Skill tool's JSONSchema names its skill argument `skill` (see the
        tool definition: `{"skill": {...}, "args": {...}}`, required=["skill"]);
        that is the ground truth used here rather than any path heuristic.
        """
        if not isinstance(tool_input, dict):
            return []
        name = tool_input.get("skill")
        if not isinstance(name, str) or not name:
            return []
        local_sources = self._canonical_local_sources()
        for candidate in self._skill_source_candidates_for_name(
            name, local_sources
        ):
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if not resolved.is_file() or resolved.name.lower() != "skill.md":
                continue
            try:
                identity = self._identity(resolved, local_sources=local_sources)
            except (OSError, ValueError):
                continue
            return [(resolved, identity)]
        return []

    def skill_identities(
        self, tool_input: Any, *, tool_name: str | None = None
    ) -> list[tuple[Path, tuple[str, str, str, str, str]]]:
        if tool_name == "Skill":
            return self._skill_identities_from_invocation(tool_input)
        candidates: dict[str, Path] = {}
        raw_seen: set[str] = set()
        pattern = re.compile(
            r"(?i)([A-Z]:[\\/][^\"'\r\n|;<>]*?[\\/]SKILL\.md|/(?:[^/\"'\s]+/)*SKILL\.md)"
        )
        for text in iter_strings(tool_input):
            for match in pattern.finditer(text):
                raw = match.group(1).strip()
                raw_key = os.path.normcase(raw)
                if raw_key in raw_seen:
                    continue
                raw_seen.add(raw_key)
                path = Path(raw)
                try:
                    resolved = path.resolve()
                except OSError:
                    continue
                key = self._path_key(resolved)
                if key in candidates:
                    continue
                if resolved.is_file() and resolved.name.lower() == "skill.md":
                    candidates[key] = resolved
        if not candidates:
            return []
        local_sources = self._canonical_local_sources()
        identities: list[
            tuple[Path, tuple[str, str, str, str, str]]
        ] = []
        for path in candidates.values():
            try:
                identity = self._identity(
                    path, local_sources=local_sources
                )
            except (OSError, ValueError):
                continue
            identities.append((path, identity))
        return identities

    def skill_paths(self, tool_input: Any) -> list[Path]:
        return [path for path, _ in self.skill_identities(tool_input)]

    @staticmethod
    def _compact_model(value: Any) -> str:
        return TelemetryStore._classify_model(value)

    @staticmethod
    def _valid_hash(value: Any, *, allow_empty: bool = True) -> bool:
        if value == "" and allow_empty:
            return True
        return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{64}", value))

    def sanitize_hook_event(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Reduce a hook payload to a bounded record with no prompt or tool bodies."""
        hook = str(payload.get("hook_event_name", ""))
        if hook not in {"PostToolUse", "Stop", "UserPromptSubmit"}:
            return None
        secret = self._existing_secret()
        if secret is None:
            # An unsigned envelope cannot establish canonical identity. Hooks fail
            # open and drop it instead of carrying attacker-controlled fields.
            return None
        hook_secret = secret

        def digest(value: str, domain: str) -> str:
            if not value:
                return ""
            return hmac.new(
                hook_secret, f"{domain}:{value}".encode(), hashlib.sha256
            ).hexdigest()

        session_raw = str(payload.get("session_id", ""))
        turn_raw = str(payload.get("turn_id", ""))
        session = digest(session_raw, "session")
        turn = digest(turn_raw, "turn")
        repo = digest(str(payload.get("cwd", "")), "repo")
        observed_at = utc_now()
        record: dict[str, Any] = {
            "version": SPOOL_SCHEMA_VERSION,
            "hook": hook,
            "observed_at": observed_at,
            "stable_correlation": True,
            "session_hash": session,
            "turn_hash": turn,
            "repo_hash": repo,
            "model_class": self._compact_model(payload.get("model", "")),
            "skills": [],
            "failure": False,
        }
        discriminator = str(
            payload.get(
                "tool_use_id",
                payload.get("tool_call_id", payload.get("call_id", "")),
            )
        )
        if hook == "PostToolUse":
            tool_name = payload.get("tool_name")
            tool_name_str = tool_name if isinstance(tool_name, str) else None
            # See the SKILL_DETECTION_CLASSES comment: classified by tool_name
            # alone, never by platform guessing.
            detection_class = (
                "invocation" if tool_name_str == "Skill" else "read"
            )
            identities = []
            for _, identity in self.skill_identities(
                payload.get("tool_input"), tool_name=tool_name_str
            )[:20]:
                skill_key, name, provider, source_class, fingerprint = (
                    identity
                )
                identities.append(
                    {
                        "skill_key": skill_key,
                        "skill_name": name,
                        "provider": provider,
                        "source_class": source_class,
                        "skill_fingerprint": fingerprint,
                        "detection_class": detection_class,
                    }
                )
            record["skills"] = identities
            record["failure"] = looks_like_failure(payload.get("tool_response"))
            classified = self.classify_tool_evidence(payload)
            if classified:
                evidence_class, result, subject = classified
                subject_hash = digest(subject, "evidence-subject")
                evidence_idem = hashlib.sha256(
                    f"{session}|{turn}|{evidence_class}|{result}|"
                    f"{digest(discriminator, 'tool-call') or subject_hash}".encode()
                ).hexdigest()
                record["evidence"] = {
                    "evidence_class": evidence_class,
                    "result": result,
                    "subject_hash": subject_hash,
                    "idempotency_key": evidence_idem,
                    "detection": "hook-inferred",
                }
            if not discriminator:
                discriminator = uuid.uuid4().hex
        elif hook == "UserPromptSubmit":
            prompt = payload.get("prompt")
            if isinstance(prompt, str):
                classified = self.classify_sentiment(prompt)
                if classified:
                    sentiment, feeling, confidence = classified
                    signature = hmac.new(
                        hook_secret, prompt.strip().encode(), hashlib.sha256
                    ).hexdigest()
                    record["feedback"] = {
                        "sentiment": sentiment,
                        "feeling_class": feeling,
                        "confidence": confidence,
                        "reaction_signature": signature,
                    }
                    discriminator = signature
        event_source = (
            f"{hook}|{session_raw}|{turn_raw}|{discriminator or hook}|"
            f"{record.get('repo_hash', '')}"
        )
        record["event_id"] = hmac.new(
            hook_secret, f"spool:{event_source}".encode(), hashlib.sha256
        ).hexdigest()
        record["auth_tag"] = self._spool_auth_tag(record, hook_secret)
        return record

    def spool_hook_event(self, payload: dict[str, Any]) -> Path | None:
        """Atomically write exactly one privacy-safe event without opening SQLite."""
        record = self.sanitize_hook_event(payload)
        if not record:
            return None
        encoded = json.dumps(
            record, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        if len(encoded) > SPOOL_RECORD_LIMIT:
            return None
        self.spool_path.mkdir(parents=True, exist_ok=True)
        nonce = uuid.uuid4().hex
        final = self.spool_path / f"{time.time_ns():020d}-{nonce}.json"
        temp = self.spool_path / f".{nonce}.{os.getpid()}.tmp"
        try:
            with temp.open("xb") as handle:
                handle.write(encoded)
                handle.flush()
            os.replace(temp, final)
        finally:
            if temp.exists():
                try:
                    temp.unlink()
                except OSError:
                    pass
        return final

    @staticmethod
    def _record_time(record: dict[str, Any]) -> datetime:
        value = record.get("observed_at")
        try:
            result, _ = canonical_utc_timestamp(value)
        except ValueError as error:
            raise ValueError("observed_at must be canonical UTC") from error
        return result

    def _validate_spool_record(self, record: Any) -> dict[str, Any]:
        if not isinstance(record, dict) or record.get("version") != SPOOL_SCHEMA_VERSION:
            raise ValueError("invalid spool version")
        auth_tag = record.get("auth_tag")
        secret = self._existing_secret()
        if (
            secret is None
            or not self._valid_hash(auth_tag, allow_empty=False)
            or not hmac.compare_digest(
                str(auth_tag), self._spool_auth_tag(record, secret)
            )
        ):
            raise ValueError("invalid spool authentication")
        hook = record.get("hook")
        if hook not in {"PostToolUse", "Stop", "UserPromptSubmit"}:
            raise ValueError("invalid spool hook")
        optional = (
            {"evidence"}
            if hook == "PostToolUse"
            else {"feedback"}
            if hook == "UserPromptSubmit"
            else set()
        )
        if not HOOK_RECORD_KEYS <= set(record) or set(record) - HOOK_RECORD_KEYS - optional:
            raise ValueError("invalid spool keys")
        if record.get("stable_correlation") is not True:
            raise ValueError("invalid correlation marker")
        if not self._valid_hash(record.get("event_id"), allow_empty=False):
            raise ValueError("invalid spool event id")
        for key in ("session_hash", "turn_hash", "repo_hash"):
            if not self._valid_hash(record.get(key)):
                raise ValueError(f"invalid {key}")
        self._record_time(record)
        model = record.get("model_class")
        if (
            not isinstance(model, str)
            or len(model) > 80
            or self._compact_model(model) != model
        ):
            raise ValueError("invalid model class")
        if not isinstance(record.get("failure"), bool):
            raise ValueError("invalid failure marker")
        skills = record.get("skills")
        if not isinstance(skills, list):
            raise ValueError("invalid skills")
        if len(skills) > 20:
            raise ValueError("too many skills")
        if hook != "PostToolUse" and (skills or record["failure"]):
            raise ValueError("unexpected non-tool fields")
        for skill in skills:
            if not isinstance(skill, dict) or set(skill) != {
                "skill_key",
                "skill_name",
                "provider",
                "source_class",
                "skill_fingerprint",
                "detection_class",
            }:
                raise ValueError("invalid skill identity")
            for key, limit in (
                ("skill_key", 160),
                ("skill_name", 120),
                ("provider", 80),
            ):
                value = skill.get(key)
                if (
                    not isinstance(value, str)
                    or not value
                    or len(value) > limit
                    or not HOOK_IDENTITY_RE.fullmatch(value)
                ):
                    raise ValueError(f"invalid skill {key}")
            if skill.get("source_class") not in HOOK_SOURCE_CLASSES:
                raise ValueError("invalid skill source class")
            if not self._valid_hash(skill.get("skill_fingerprint")):
                raise ValueError("invalid skill fingerprint")
            # Freshly spooled events always carry a live detection class;
            # "legacy-unknown" is reserved for pre-fix DB rows only (see
            # PERSISTED_SKILL_DETECTION_CLASSES) and must never appear here.
            if skill.get("detection_class") not in SKILL_DETECTION_CLASSES:
                raise ValueError("invalid skill detection class")
        evidence = record.get("evidence")
        if evidence is not None:
            if (
                hook != "PostToolUse"
                or not isinstance(evidence, dict)
                or set(evidence)
                != {
                    "evidence_class",
                    "result",
                    "subject_hash",
                    "idempotency_key",
                    "detection",
                }
                or evidence.get("evidence_class") not in EVIDENCE_CLASSES
                or evidence.get("evidence_class") == "domain-verdict"
                or evidence.get("result") not in EVIDENCE_RESULTS
                or not self._valid_hash(evidence.get("subject_hash"), allow_empty=False)
                or not self._valid_hash(evidence.get("idempotency_key"), allow_empty=False)
                or evidence.get("detection") != "hook-inferred"
            ):
                raise ValueError("invalid sanitized evidence")
        feedback = record.get("feedback")
        if feedback is not None:
            confidence = feedback.get("confidence") if isinstance(feedback, dict) else None
            if (
                hook != "UserPromptSubmit"
                or not isinstance(feedback, dict)
                or set(feedback)
                != {
                    "sentiment",
                    "feeling_class",
                    "confidence",
                    "reaction_signature",
                }
                or feedback.get("sentiment") not in SENTIMENTS
                or feedback.get("feeling_class") not in HOOK_FEELING_CLASSES
                or isinstance(confidence, bool)
                or not isinstance(confidence, (int, float))
                or not 0.0 <= confidence <= 1.0
                or not self._valid_hash(
                    feedback.get("reaction_signature"), allow_empty=False
                )
            ):
                raise ValueError("invalid sanitized feedback")
        return record

    @staticmethod
    def _insert_sanitized_evidence(
        db: sqlite3.Connection,
        record: dict[str, Any],
        evidence: dict[str, Any],
    ) -> int:
        evidence_class = evidence.get("evidence_class")
        result = evidence.get("result")
        subject_hash = evidence.get("subject_hash")
        idem = evidence.get("idempotency_key")
        detection = evidence.get("detection")
        if (
            evidence_class not in EVIDENCE_CLASSES
            or result not in EVIDENCE_RESULTS
            or not TelemetryStore._valid_hash(subject_hash, allow_empty=False)
            or not TelemetryStore._valid_hash(idem, allow_empty=False)
            or detection not in EVIDENCE_DETECTIONS
        ):
            raise ValueError("invalid sanitized evidence")
        if evidence_class == "domain-verdict":
            raise ValueError(
                "domain-verdict evidence must be explicit-manual"
            )
        proposed_id = "skillevidence_" + uuid.uuid4().hex
        observed_at = str(record["observed_at"])
        db.execute(
            """INSERT OR IGNORE INTO skill_evidence
               (evidence_id,idempotency_key,session_hash,turn_hash,repo_hash,
                evidence_class,result,subject_hash,detection,observed_at,
                provenance_trust)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                proposed_id,
                idem,
                record["session_hash"],
                record["turn_hash"],
                record["repo_hash"],
                evidence_class,
                result,
                subject_hash,
                detection,
                observed_at,
                TRUSTED_PROVENANCE,
            ),
        )
        row = db.execute(
            "SELECT evidence_id FROM skill_evidence WHERE idempotency_key=?", (idem,)
        ).fetchone()
        evidence_id = str(row["evidence_id"])
        session = str(record["session_hash"])
        turn = str(record["turn_hash"])
        repo = str(record["repo_hash"])
        runs = db.execute(
            """SELECT run_id,started_at FROM skill_runs
               WHERE session_hash=? AND turn_hash=?""",
            (session, turn),
        ).fetchall()
        if not runs and session:
            cutoff = (
                datetime.fromisoformat(observed_at) - timedelta(minutes=30)
            ).isoformat(timespec="microseconds")
            fallback_sql = """
                SELECT r.run_id,r.started_at
                FROM skill_runs r
                WHERE r.session_hash=?
                  AND r.started_at>=?
                  AND r.started_at<=?
            """
            params: list[Any] = [session, cutoff, observed_at]
            if repo:
                fallback_sql += " AND r.repo_hash=?"
                params.append(repo)
            fallback_sql += """
                  AND NOT EXISTS (
                    SELECT 1 FROM skill_runs newer
                    WHERE newer.session_hash=r.session_hash
                      AND newer.skill_key=r.skill_key
                      AND newer.started_at>=?
                      AND newer.started_at<=?
                      AND (
                        newer.started_at>r.started_at
                        OR (newer.started_at=r.started_at AND newer.run_id>r.run_id)
                      )
            """
            params.extend([cutoff, observed_at])
            if repo:
                fallback_sql += " AND newer.repo_hash=?"
                params.append(repo)
            fallback_sql += ")"
            runs = db.execute(fallback_sql, tuple(params)).fetchall()
            runs = [
                run
                for run in runs
                if timestamp_strictly_precedes(
                    str(run["started_at"]), observed_at
                )
            ]
        # Tool evidence has no trusted target Skill identifier. Credit it only
        # when the same-turn/fallback candidate is unique; otherwise retain the
        # evidence row as ambiguous and unlinked.
        linkable_runs = runs if len(runs) == 1 else []
        if linkable_runs:
            existing_links = db.execute(
                """SELECT run_id FROM skill_run_evidence
                   WHERE evidence_id=?""",
                (evidence_id,),
            ).fetchall()
            target = str(linkable_runs[0]["run_id"])
            if any(str(item["run_id"]) != target for item in existing_links):
                linkable_runs = []
        linked = 0
        for run in linkable_runs:
            cur = db.execute(
                """INSERT OR IGNORE INTO skill_run_evidence
                   (run_id,evidence_id,linked_at) VALUES(?,?,?)""",
                (run["run_id"], evidence_id, utc_now()),
            )
            linked += cur.rowcount
        return linked

    @staticmethod
    def _record_turn_lifecycle(
        db: sqlite3.Connection,
        session: str,
        turn: str,
        *,
        prompt_started_at: str | None = None,
        stopped_at: str | None = None,
    ) -> None:
        if not session or not turn:
            return
        db.execute(
            """
            INSERT INTO turn_lifecycle(
              session_hash,turn_hash,prompt_started_at,stopped_at
            ) VALUES(?,?,?,?)
            ON CONFLICT(session_hash,turn_hash) DO UPDATE SET
              prompt_started_at=CASE
                WHEN excluded.prompt_started_at IS NULL
                  THEN turn_lifecycle.prompt_started_at
                WHEN turn_lifecycle.prompt_started_at IS NULL
                  OR excluded.prompt_started_at<turn_lifecycle.prompt_started_at
                  THEN excluded.prompt_started_at
                ELSE turn_lifecycle.prompt_started_at
              END,
              stopped_at=CASE
                WHEN excluded.stopped_at IS NULL
                  THEN turn_lifecycle.stopped_at
                WHEN turn_lifecycle.stopped_at IS NULL
                  OR excluded.stopped_at<turn_lifecycle.stopped_at
                  THEN excluded.stopped_at
                ELSE turn_lifecycle.stopped_at
              END
            """,
            (session, turn, prompt_started_at, stopped_at),
        )

    @staticmethod
    def _duration_between(started_at: str, ended_at: str) -> int:
        return max(
            0,
            int(
                (
                    datetime.fromisoformat(ended_at)
                    - datetime.fromisoformat(started_at)
                ).total_seconds()
                * 1000
            ),
        )

    @classmethod
    def _terminal_state_for_run(
        cls,
        db: sqlite3.Connection,
        session: str,
        turn: str,
        started_at: str,
    ) -> tuple[str, str, str, str] | None:
        if not session or not turn:
            return None
        lifecycle = db.execute(
            """
            SELECT stopped_at AS ended_at
            FROM turn_lifecycle
            WHERE session_hash=? AND turn_hash=?
            """,
            (session, turn),
        ).fetchone()
        stopped_at = (
            str(lifecycle["ended_at"])
            if lifecycle and lifecycle["ended_at"]
            else None
        )
        successor_rows = db.execute(
            """
            SELECT prompt_started_at AS ended_at
            FROM turn_lifecycle
            WHERE session_hash=?
              AND turn_hash<>?
            """,
            (session, turn),
        ).fetchall()
        prompt_candidates = [
            str(row["ended_at"])
            for row in successor_rows
            if row["ended_at"]
            and timestamp_strictly_precedes(
                started_at, str(row["ended_at"])
            )
        ]
        prompt_at = (
            min(
                prompt_candidates,
                key=lambda value: canonical_utc_timestamp(value)[0],
            )
            if prompt_candidates
            else None
        )
        return cls._resolve_terminal(started_at, stopped_at, prompt_at)

    @staticmethod
    def _resolve_terminal(
        started_at: str,
        stopped_at: str | None,
        prompt_at: str | None,
    ) -> tuple[str, str, str, str] | None:
        if stopped_at is not None and not timestamp_strictly_precedes(
            started_at, stopped_at
        ):
            stopped_at = None
        if prompt_at is not None and not timestamp_strictly_precedes(
            started_at, prompt_at
        ):
            prompt_at = None
        if stopped_at is not None and prompt_at is not None:
            if timestamp_strictly_precedes(stopped_at, prompt_at):
                return "returned", stopped_at, "stop", "exact"
            if timestamp_strictly_precedes(prompt_at, stopped_at):
                return "interrupted", prompt_at, "superseded", "exact"
            # Equal microsecond instants and legacy same-second intervals do not
            # prove which terminal event happened first.
            return None
        if stopped_at is not None:
            return "returned", stopped_at, "stop", "exact"
        if prompt_at is not None:
            return "interrupted", prompt_at, "superseded", "exact"
        return None

    @classmethod
    def _terminal_from_lifecycle_index(
        cls,
        turn: str,
        started_at: str,
        stops: dict[str, str],
        prompts: list[tuple[str, str]],
    ) -> tuple[str, str, str, str] | None:
        stopped_at = stops.get(turn)
        prompt_candidates = [
            candidate_at
            for candidate_at, candidate_turn in prompts
            if candidate_turn != turn
            and timestamp_strictly_precedes(started_at, candidate_at)
        ]
        prompt_at = (
            min(
                prompt_candidates,
                key=lambda value: canonical_utc_timestamp(value)[0],
            )
            if prompt_candidates
            else None
        )
        return cls._resolve_terminal(started_at, stopped_at, prompt_at)

    @classmethod
    def _reconcile_session_lifecycle(
        cls,
        db: sqlite3.Connection,
        session: str,
        *,
        turn: str | None = None,
        successor_turn: str | None = None,
        successor_at: str | None = None,
        deadline: float | None = None,
    ) -> int:
        """Recompute only runs affected by one lifecycle event."""
        if not session:
            return 0
        if deadline is not None and time.monotonic() >= deadline:
            raise DrainBudgetExceeded("drain budget exhausted")
        changed = 0
        sql = """
            SELECT run_id,turn_hash,status,started_at,ended_at,duration_ms,
                   end_reason,duration_quality
            FROM skill_runs
            WHERE session_hash=? AND status<>'failed'
        """
        params: list[Any] = [session]
        if turn is not None:
            sql += " AND turn_hash=?"
            params.append(turn)
        elif successor_at is not None:
            sql += " AND turn_hash<>? AND started_at<=?"
            params.extend([successor_turn or "", successor_at])
        rows = db.execute(sql, tuple(params)).fetchall()
        if not rows:
            return 0
        lifecycle_rows = db.execute(
            """SELECT turn_hash,prompt_started_at,stopped_at
               FROM turn_lifecycle WHERE session_hash=?""",
            (session,),
        ).fetchall()
        stops = {
            str(item["turn_hash"]): str(item["stopped_at"])
            for item in lifecycle_rows
            if item["stopped_at"]
        }
        prompts = sorted(
            (
                str(item["prompt_started_at"]),
                str(item["turn_hash"]),
            )
            for item in lifecycle_rows
            if item["prompt_started_at"]
        )
        for index, row in enumerate(rows):
            if (
                deadline is not None
                and index % 32 == 0
                and time.monotonic() >= deadline
            ):
                raise DrainBudgetExceeded("drain budget exhausted")
            terminal = cls._terminal_from_lifecycle_index(
                str(row["turn_hash"]),
                str(row["started_at"]),
                stops,
                prompts,
            )
            if terminal is None:
                continue
            status, ended_at, end_reason, duration_quality = terminal
            duration = cls._duration_between(
                str(row["started_at"]), ended_at
            )
            if (
                row["status"] == status
                and row["ended_at"] == ended_at
                and row["duration_ms"] == duration
                and row["end_reason"] == end_reason
                and row["duration_quality"] == duration_quality
            ):
                continue
            db.execute(
                """UPDATE skill_runs
                   SET status=?,ended_at=?,duration_ms=?,end_reason=?,
                       duration_quality=? WHERE run_id=?""",
                (
                    status,
                    ended_at,
                    duration,
                    end_reason,
                    duration_quality,
                    row["run_id"],
                ),
            )
            changed += 1
        return changed

    @classmethod
    def _feedback_target_runs(
        cls,
        db: sqlite3.Connection,
        session: str,
        current_turn: str,
        observed_at: str,
    ) -> list[sqlite3.Row]:
        """Choose one causally prior turn, or hold feedback when order is tied."""
        rows = db.execute(
            """SELECT run_id,turn_hash,started_at,ended_at,end_reason
               FROM skill_runs
               WHERE session_hash=? AND turn_hash<>?
                 AND status<>'running' AND ended_at IS NOT NULL""",
            (session, current_turn),
        ).fetchall()
        by_turn: dict[str, dict[str, Any]] = {}
        for row in rows:
            ended_at = str(row["ended_at"])
            started_at = str(row["started_at"])
            marker: str | None = None
            if timestamp_strictly_precedes(ended_at, observed_at):
                marker = ended_at
            elif (
                row["end_reason"] == "superseded"
                and ended_at == observed_at
                and timestamp_strictly_precedes(started_at, observed_at)
            ):
                # This prompt may have just finalized an earlier running turn.
                marker = started_at
            if marker is None:
                continue
            turn = str(row["turn_hash"])
            group = by_turn.setdefault(
                turn, {"marker": marker, "rows": []}
            )
            group["rows"].append(row)
            if timestamp_strictly_precedes(
                str(group["marker"]), marker
            ):
                group["marker"] = marker
        if not by_turn:
            return []
        chosen_turn: str | None = None
        chosen_marker: str | None = None
        for turn, group in by_turn.items():
            marker = str(group["marker"])
            if chosen_marker is None:
                chosen_turn, chosen_marker = turn, marker
                continue
            if timestamp_strictly_precedes(chosen_marker, marker):
                chosen_turn, chosen_marker = turn, marker
                continue
            if timestamp_strictly_precedes(marker, chosen_marker):
                continue
            # Equal microsecond instants and overlapping legacy-second
            # intervals cannot identify which turn received the reaction.
            return []
        return list(by_turn[chosen_turn]["rows"]) if chosen_turn else []

    def _apply_spool_record(
        self,
        db: sqlite3.Connection,
        record: dict[str, Any],
        *,
        deadline: float | None = None,
    ) -> None:
        if deadline is not None and time.monotonic() >= deadline:
            raise DrainBudgetExceeded("drain budget exhausted")
        event_id = str(record["event_id"])
        if db.execute(
            "SELECT 1 FROM spool_receipts WHERE event_id=?", (event_id,)
        ).fetchone():
            return
        hook = str(record["hook"])
        observed = self._record_time(record)
        session = str(record["session_hash"])
        turn = str(record["turn_hash"])
        repo = str(record["repo_hash"])
        model = self._compact_model(record.get("model_class", ""))
        if hook == "PostToolUse":
            terminal = self._terminal_state_for_run(
                db, session, turn, str(record["observed_at"])
            )
            terminal_status = terminal[0] if terminal else "running"
            terminal_at = terminal[1] if terminal else None
            terminal_reason = terminal[2] if terminal else "running"
            terminal_quality = terminal[3] if terminal else "pending"
            terminal_duration = None
            if terminal_at is not None:
                terminal_duration = self._duration_between(
                    str(record["observed_at"]), terminal_at
                )
            for skill in record.get("skills", []):
                if not isinstance(skill, dict):
                    raise ValueError("invalid skill identity")
                fingerprint = str(skill.get("skill_fingerprint", ""))
                if fingerprint and not self._valid_hash(
                    fingerprint, allow_empty=False
                ):
                    raise ValueError("invalid skill fingerprint")
                skill_key, skill_name, provider, source_class = (
                    self._validate_skill_identity(
                        skill.get("skill_key"),
                        skill.get("skill_name"),
                        skill.get("provider"),
                        skill.get("source_class"),
                    )
                )
                detection_class = skill.get("detection_class")
                if detection_class not in SKILL_DETECTION_CLASSES:
                    raise ValueError("invalid skill detection class")
                idem = hashlib.sha256(
                    f"{session}|{turn}|{skill_key}|{fingerprint}".encode()
                ).hexdigest()
                db.execute(
                    """INSERT OR IGNORE INTO skill_runs
                       (run_id,idempotency_key,skill_key,skill_name,provider,source_class,
                        skill_fingerprint,session_hash,turn_hash,repo_hash,model_class,
                        detection,status,started_at,ended_at,duration_ms,
                        provenance_trust,end_reason,duration_quality,detection_class)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        "skillrun_" + uuid.uuid4().hex,
                        idem,
                        skill_key,
                        skill_name,
                        provider,
                        source_class,
                        fingerprint,
                        session,
                        turn,
                        repo,
                        model,
                        "hook-inferred",
                        terminal_status,
                        str(record["observed_at"]),
                        terminal_at,
                        terminal_duration,
                        TRUSTED_PROVENANCE,
                        terminal_reason,
                        terminal_quality,
                        detection_class,
                    ),
                )
            evidence = record.get("evidence")
            if isinstance(evidence, dict):
                self._insert_sanitized_evidence(db, record, evidence)
            if record.get("failure") is True:
                if terminal:
                    db.execute(
                        """UPDATE skill_runs
                           SET tool_failure_count=tool_failure_count+1
                           WHERE session_hash=? AND turn_hash=?""",
                        (session, turn),
                    )
                else:
                    db.execute(
                        """UPDATE skill_runs
                           SET tool_failure_count=tool_failure_count+1
                           WHERE session_hash=? AND turn_hash=? AND status='running'""",
                        (session, turn),
                    )
        elif hook == "Stop":
            self._record_turn_lifecycle(
                db,
                session,
                turn,
                stopped_at=str(record["observed_at"]),
            )
            self._reconcile_session_lifecycle(
                db,
                session,
                turn=turn,
                deadline=deadline,
            )
        elif hook == "UserPromptSubmit":
            self._record_turn_lifecycle(
                db,
                session,
                turn,
                prompt_started_at=str(record["observed_at"]),
            )
            self._reconcile_session_lifecycle(
                db,
                session,
                successor_turn=turn,
                successor_at=str(record["observed_at"]),
                deadline=deadline,
            )
            feedback = record.get("feedback")
            if isinstance(feedback, dict):
                runs = self._feedback_target_runs(
                    db, session, turn, str(record["observed_at"])
                )
                if runs:
                    signature = str(feedback.get("reaction_signature", ""))
                    sentiment = str(feedback.get("sentiment", ""))
                    if (
                        sentiment not in SENTIMENTS
                        or not self._valid_hash(signature, allow_empty=False)
                    ):
                        raise ValueError("invalid sanitized feedback")
                    for row in runs:
                        db.execute(
                            """INSERT OR IGNORE INTO skill_feedback
                               VALUES(?,?,?,?,?,?,?,?,?)""",
                            (
                                "skillfb_" + uuid.uuid4().hex,
                                row["run_id"],
                                sentiment,
                                None,
                                str(feedback.get("feeling_class", "")),
                                "hook-inferred-explicit-language",
                                float(feedback.get("confidence", 0.0)),
                                signature,
                                str(record["observed_at"]),
                            ),
                        )
                    if runs:
                        result = {
                            "positive": "passed",
                            "negative": "failed",
                            "mixed": "ambiguous",
                        }[sentiment]
                        subject_hash = self.pseudonym(
                            signature, "evidence-subject"
                        )
                        idem = hashlib.sha256(
                            f"{session}|{turn}|explicit-feedback|{result}|"
                            f"{signature}".encode()
                        ).hexdigest()
                        self._insert_sanitized_evidence(
                            db,
                            record,
                            {
                                "evidence_class": "explicit-feedback",
                                "result": result,
                                "subject_hash": subject_hash,
                                "idempotency_key": idem,
                                "detection": "hook-inferred-explicit-language",
                            },
                        )
        detail = (
            f"spool-v{SPOOL_SCHEMA_VERSION};skills:"
            f"{len(record.get('skills', []))};"
            f"evidence:{1 if isinstance(record.get('evidence'), dict) else 0}"
        )
        health_hook, health_status, health_detail = self._require_health_values(
            hook, "ok", detail
        )
        db.execute(
            "INSERT INTO collector_health VALUES(?,?,?,?)",
            (
                str(record["observed_at"]),
                health_hook,
                health_status,
                health_detail,
            ),
        )
        db.execute(
            """DELETE FROM collector_health WHERE rowid NOT IN
               (SELECT rowid FROM collector_health
                ORDER BY observed_at DESC LIMIT 200)"""
        )
        db.execute(
            "INSERT INTO spool_receipts(event_id,processed_at) VALUES(?,?)",
            (event_id, utc_now()),
        )

    def _quarantine_spool_record(
        self,
        path: Path,
        *,
        reason_class: str,
        size_bytes: int,
        raw: bytes | None = None,
    ) -> Path | None:
        """Replace an untrusted record with body-free rejection metadata."""
        nonce = uuid.uuid4().hex
        metadata = {
            "version": 1,
            "rejected_at": utc_now(),
            "reason_class": reason_class,
            "size_bytes": max(0, int(size_bytes)),
            "content_sha256": hashlib.sha256(raw).hexdigest() if raw is not None else None,
            "source_name_sha256": hashlib.sha256(
                path.name.encode("utf-8", "replace")
            ).hexdigest(),
        }
        encoded = json.dumps(
            metadata, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode("ascii")
        temp = self.spool_path / f".rejected-{nonce}.{os.getpid()}.tmp"
        rejected = self.spool_path / f"rejected-{nonce}.rejected"
        try:
            with temp.open("xb") as handle:
                handle.write(encoded)
                handle.flush()
            # Remove the untrusted body before giving the file its quarantine name.
            # If the second replace fails, only sanitized metadata remains as pending.
            os.replace(temp, path)
            os.replace(path, rejected)
        except OSError:
            return None
        finally:
            if temp.exists():
                try:
                    temp.unlink()
                except OSError:
                    pass
        return rejected

    def drain_spool(
        self, *, limit: int = 500, max_seconds: float = 1.0
    ) -> dict[str, int]:
        """Apply queued records transactionally; leave busy records for a later pass."""
        self._ensure_schema()
        result = {"processed": 0, "duplicate": 0, "deferred": 0, "rejected": 0}
        if not self.spool_path.is_dir():
            return result
        deadline = time.monotonic() + max(0.05, max_seconds)
        files = sorted(self.spool_path.glob("*.json"))[: max(1, min(limit, 5000))]
        for path in files:
            if time.monotonic() >= deadline:
                result["deferred"] += 1
                break
            try:
                size = path.stat().st_size
                if size > SPOOL_RECORD_LIMIT:
                    rejected = self._quarantine_spool_record(
                        path,
                        reason_class="oversized-record",
                        size_bytes=size,
                    )
                    if rejected is None:
                        result["deferred"] += 1
                        break
                    result["rejected"] += 1
                    continue
                with path.open("rb") as handle:
                    raw = handle.read(SPOOL_RECORD_LIMIT + 1)
                if len(raw) > SPOOL_RECORD_LIMIT:
                    rejected = self._quarantine_spool_record(
                        path,
                        reason_class="oversized-record",
                        size_bytes=max(size, len(raw)),
                    )
                    if rejected is None:
                        result["deferred"] += 1
                        break
                    result["rejected"] += 1
                    continue
                record = self._validate_spool_record(json.loads(raw.decode("utf-8")))
                with self.connection() as db:
                    duplicate = bool(
                        db.execute(
                            "SELECT 1 FROM spool_receipts WHERE event_id=?",
                            (record["event_id"],),
                        ).fetchone()
                    )
                    if not duplicate:
                        self._apply_spool_record(
                            db, record, deadline=deadline
                        )
                path.unlink()
                result["duplicate" if duplicate else "processed"] += 1
            except DrainBudgetExceeded:
                # The per-record transaction has rolled back, no receipt was
                # issued, and the original spool file remains retryable.
                result["deferred"] += 1
                break
            except json.JSONDecodeError:
                rejected = self._quarantine_spool_record(
                    path,
                    reason_class="malformed-json",
                    size_bytes=len(raw),
                    raw=raw,
                )
                result["rejected" if rejected is not None else "deferred"] += 1
                if rejected is None:
                    break
            except UnicodeDecodeError:
                rejected = self._quarantine_spool_record(
                    path,
                    reason_class="invalid-utf8",
                    size_bytes=len(raw),
                    raw=raw,
                )
                result["rejected" if rejected is not None else "deferred"] += 1
                if rejected is None:
                    break
            except (ValueError, TypeError):
                rejected = self._quarantine_spool_record(
                    path,
                    reason_class="invalid-envelope",
                    size_bytes=len(raw),
                    raw=raw,
                )
                result["rejected" if rejected is not None else "deferred"] += 1
                if rejected is None:
                    break
            except sqlite3.Error as error:
                if is_busy_error(error):
                    result["deferred"] += 1
                    break
                result["deferred"] += 1
                break
            except OSError:
                result["deferred"] += 1
                break
        return result

    def spool_status(self) -> dict[str, int]:
        try:
            if not self.spool_path.is_dir():
                return {"pending": 0, "rejected": 0}
            return {
                "pending": sum(1 for _ in self.spool_path.glob("*.json")),
                "rejected": sum(1 for _ in self.spool_path.glob("*.rejected")),
            }
        except OSError:
            return {"pending": 0, "rejected": 0, "unavailable": 1}

    def start_from_path(self, path: Path, payload: dict[str, Any], detection: str = "hook-inferred") -> str:
        skill_key, name, provider, source_class, fingerprint = self._identity(path)
        skill_key, name, provider, source_class = self._validate_skill_identity(
            skill_key, name, provider, source_class
        )
        detection = self._require_detection(
            detection, RUN_DETECTIONS, "run detection"
        )
        model = self._classify_model(payload.get("model", ""))
        session = self.pseudonym(str(payload.get("session_id", "")), "session")
        turn = self.pseudonym(str(payload.get("turn_id", "")), "turn")
        repo = self.pseudonym(str(payload.get("cwd", "")), "repo")
        idem = hashlib.sha256(f"{session}|{turn}|{skill_key}|{fingerprint}".encode()).hexdigest()
        run_id = "skillrun_" + uuid.uuid4().hex
        with self.connection() as db:
            db.execute(
                """INSERT OR IGNORE INTO skill_runs
                   (run_id,idempotency_key,skill_key,skill_name,provider,source_class,
                    skill_fingerprint,session_hash,turn_hash,repo_hash,model_class,detection,
                    status,started_at,provenance_trust,end_reason,
                    duration_quality)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id, idem, skill_key, name, provider, source_class, fingerprint,
                    session, turn, repo, model,
                    detection, "running", utc_now(), TRUSTED_PROVENANCE,
                    "running", "pending",
                ),
            )
            row = db.execute("SELECT run_id FROM skill_runs WHERE idempotency_key=?", (idem,)).fetchone()
        return str(row["run_id"])

    def start_manual(
        self,
        skill_name: str,
        session_id: str = "",
        turn_id: str = "",
        cwd: str = "",
        model: str = "",
    ) -> str:
        skill_name = self._require_identity(
            skill_name, "skill name", limit=120
        )
        model = self._require_model_class(model)
        now = utc_now()
        nonce = uuid.uuid4().hex
        run_id = "skillrun_" + nonce
        idem = hashlib.sha256(f"manual|{skill_name}|{nonce}".encode()).hexdigest()
        session = self.pseudonym(session_id, "session")
        turn = self.pseudonym(turn_id, "turn")
        repo = self.pseudonym(cwd, "repo")
        with self.connection() as db:
            db.execute(
                """INSERT INTO skill_runs
                   (run_id,idempotency_key,skill_key,skill_name,provider,source_class,
                    skill_fingerprint,session_hash,turn_hash,repo_hash,model_class,detection,
                    status,started_at,provenance_trust,end_reason,
                    duration_quality)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (run_id, idem, skill_name, skill_name, "manual", "manual", "", session, turn, repo,
                 model, "explicit-manual", "running", now, TRUSTED_PROVENANCE,
                 "running", "pending"),
            )
        return run_id

    def finish_run(self, run_id: str, status: str) -> bool:
        run_id = self._require_run_id(run_id)
        if not isinstance(status, str) or status not in FINAL_STATES:
            raise ValueError("invalid status")
        ended_at = utc_now()
        end_reason = {
            "returned": "manual-returned",
            "failed": "manual-failed",
            "interrupted": "manual-interrupted",
        }[status]
        with self.connection() as db:
            row = db.execute("SELECT started_at,status FROM skill_runs WHERE run_id=?", (run_id,)).fetchone()
            if not row:
                return False
            duration = self._duration_between(
                str(row["started_at"]), ended_at
            )
            cur = db.execute(
                """UPDATE skill_runs
                   SET status=?,ended_at=?,duration_ms=?,end_reason=?,
                       duration_quality='exact'
                   WHERE run_id=? AND status='running'""",
                (status, ended_at, duration, end_reason, run_id),
            )
        return cur.rowcount > 0

    def finish_turn(self, payload: dict[str, Any]) -> int:
        session = self.pseudonym(str(payload.get("session_id", "")), "session")
        turn = self.pseudonym(str(payload.get("turn_id", "")), "turn")
        ended_at = utc_now()
        with self.connection() as db:
            self._record_turn_lifecycle(
                db, session, turn, stopped_at=ended_at
            )
            return self._reconcile_session_lifecycle(
                db, session, turn=turn
            )

    def increment_failures(self, payload: dict[str, Any]) -> int:
        session = self.pseudonym(str(payload.get("session_id", "")), "session")
        turn = self.pseudonym(str(payload.get("turn_id", "")), "turn")
        with self.connection() as db:
            cur = db.execute(
                """UPDATE skill_runs SET tool_failure_count=tool_failure_count+1
                   WHERE session_hash=? AND turn_hash=? AND status='running'""",
                (session, turn),
            )
        return cur.rowcount

    def add_evidence(
        self,
        payload: dict[str, Any],
        evidence_class: str,
        result: str,
        subject: str,
        detection: str = "hook-inferred",
        skill_key: str = "",
        idempotency_hint: str = "",
    ) -> dict[str, Any]:
        """Persist only coarse evidence and pseudonyms, then link same-turn Skill runs."""
        if (
            not isinstance(evidence_class, str)
            or evidence_class not in EVIDENCE_CLASSES
        ):
            raise ValueError("invalid evidence class")
        if not isinstance(result, str) or result not in EVIDENCE_RESULTS:
            raise ValueError("invalid evidence result")
        detection = self._require_detection(
            detection, EVIDENCE_DETECTIONS, "evidence detection"
        )
        if (
            evidence_class == "domain-verdict"
            and detection != "explicit-manual"
        ):
            raise ValueError(
                "domain-verdict evidence must be explicit-manual"
            )
        if not isinstance(subject, str):
            raise ValueError("invalid evidence subject")
        if skill_key:
            skill_key = self._require_identity(
                skill_key, "skill key", limit=160
            )
        session = self.pseudonym(str(payload.get("session_id", "")), "session")
        turn = self.pseudonym(str(payload.get("turn_id", "")), "turn")
        repo = self.pseudonym(str(payload.get("cwd", "")), "repo")
        subject_hash = self.pseudonym(subject[:160], "evidence-subject")
        tool_call = str(
            payload.get("tool_use_id", payload.get("tool_call_id", payload.get("call_id", "")))
        )
        stable_hint = idempotency_hint or tool_call or subject_hash
        idem = hashlib.sha256(
            f"{session}|{turn}|{skill_key or '*'}|{evidence_class}|"
            f"{result}|{stable_hint}".encode()
        ).hexdigest()
        proposed_id = "skillevidence_" + uuid.uuid4().hex
        observed_at = utc_now()
        with self.connection() as db:
            db.execute(
                """INSERT OR IGNORE INTO skill_evidence
                   (evidence_id,idempotency_key,session_hash,turn_hash,repo_hash,
                    evidence_class,result,subject_hash,detection,observed_at,
                    provenance_trust)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    proposed_id, idem, session, turn, repo, evidence_class, result,
                    subject_hash, detection, observed_at, TRUSTED_PROVENANCE,
                ),
            )
            row = db.execute(
                "SELECT evidence_id FROM skill_evidence WHERE idempotency_key=?", (idem,)
            ).fetchone()
            evidence_id = str(row["evidence_id"])
            sql = (
                "SELECT run_id,started_at FROM skill_runs "
                "WHERE session_hash=? AND turn_hash=?"
            )
            params: list[Any] = [session, turn]
            if skill_key:
                sql += " AND skill_key=?"
                params.append(skill_key)
            runs = db.execute(sql, tuple(params)).fetchall()
            if not runs and session:
                cutoff = (
                    datetime.fromisoformat(observed_at) - timedelta(minutes=30)
                ).isoformat(timespec="microseconds")
                fallback_sql = """
                    SELECT r.run_id,r.started_at
                    FROM skill_runs r
                    WHERE r.session_hash=?
                      AND r.started_at>=?
                      AND r.started_at<=?
                """
                fallback_params: list[Any] = [session, cutoff, observed_at]
                if repo:
                    fallback_sql += " AND r.repo_hash=?"
                    fallback_params.append(repo)
                if skill_key:
                    fallback_sql += " AND r.skill_key=?"
                    fallback_params.append(skill_key)
                fallback_sql += """
                      AND NOT EXISTS (
                        SELECT 1 FROM skill_runs newer
                        WHERE newer.session_hash=r.session_hash
                          AND newer.skill_key=r.skill_key
                          AND newer.started_at>=?
                          AND newer.started_at<=?
                          AND (
                            newer.started_at>r.started_at
                            OR (newer.started_at=r.started_at AND newer.run_id>r.run_id)
                          )
                """
                fallback_params.extend([cutoff, observed_at])
                if repo:
                    fallback_sql += " AND newer.repo_hash=?"
                    fallback_params.append(repo)
                fallback_sql += ")"
                runs = db.execute(fallback_sql, tuple(fallback_params)).fetchall()
                runs = [
                    run
                    for run in runs
                    if timestamp_strictly_precedes(
                        str(run["started_at"]), observed_at
                    )
                ]
            if len(runs) != 1:
                runs = []
            if runs:
                existing_links = db.execute(
                    """SELECT run_id FROM skill_run_evidence
                       WHERE evidence_id=?""",
                    (evidence_id,),
                ).fetchall()
                target = str(runs[0]["run_id"])
                if any(str(item["run_id"]) != target for item in existing_links):
                    runs = []
            linked = 0
            for run in runs:
                cur = db.execute(
                    """INSERT OR IGNORE INTO skill_run_evidence
                       (run_id,evidence_id,linked_at) VALUES(?,?,?)""",
                    (run["run_id"], evidence_id, utc_now()),
                )
                linked += cur.rowcount
        return {"evidence_id": evidence_id, "linked_runs": linked}

    @staticmethod
    def classify_tool_evidence(payload: dict[str, Any]) -> tuple[str, str, str] | None:
        """Inspect tool data transiently and return only a coarse, body-free tuple."""
        name = str(payload.get("tool_name", payload.get("tool", ""))).lower()
        input_text = " ".join(iter_strings(payload.get("tool_input"))).lower()[:12000]
        response = payload.get("tool_response")
        response_text = " ".join(iter_strings(response)).lower()[:12000]
        failed = looks_like_failure(response)
        succeeded = looks_like_success(response)
        result = "failed" if failed else "passed" if succeeded else "ambiguous"
        combined = f"{name} {input_text}"

        if re.search(r"(approval|permission|sandbox|authority)", combined):
            if failed or re.search(r"(denied|rejected|not approved|approval required)", response_text):
                return "authority", "failed", "authority-boundary"
            if re.search(r"(approved|granted|allowed)", response_text):
                return "authority", "passed", "authority-boundary"
            return "authority", result, "authority-boundary"
        if re.search(r"(playwright|browser|screenshot|viewport|visual.?qa)", combined):
            # A successful browser/screenshot tool call proves acquisition,
            # not that a human or domain validator accepted the rendered UI.
            browser_result = "failed" if failed else "ambiguous"
            return "browser-qa", browser_result, "browser-check"
        if re.search(r"(progress-verifier|pm[_ -]?verified|verification.*(?:done|pass)|task.*verified)", combined):
            return "pm-verified-task", result, "pm-verification"
        if re.search(r"(\btest\b|pytest|unittest|vitest|jest|cargo test|go test|npm(?: .+)? run test)", combined):
            return "test", result, "test-command"
        if re.search(r"(\bbuild\b|tsc\b|cargo build|go build|npm(?: .+)? run build)", combined):
            return "build", result, "build-command"
        if re.search(r"(validate|validator|lint|typecheck|integrity_check|doctor)", combined):
            return "validate", result, "validation-command"
        return None

    def capture_tool_evidence(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        classified = self.classify_tool_evidence(payload)
        if not classified:
            return None
        evidence_class, result, subject = classified
        return self.add_evidence(payload, evidence_class, result, subject)

    def recover_stale(self, hours: int = 12) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).replace(microsecond=0).isoformat()
        now = utc_now()
        count = 0
        with self.connection() as db:
            rows = db.execute(
                """SELECT run_id,started_at FROM skill_runs
                   WHERE status='running' AND started_at<?""",
                (cutoff,),
            ).fetchall()
            for row in rows:
                duration = self._duration_between(
                    str(row["started_at"]), now
                )
                cur = db.execute(
                    """UPDATE skill_runs
                       SET status='interrupted',ended_at=?,duration_ms=?,
                           end_reason='stale-timeout',
                           duration_quality='bounded'
                       WHERE run_id=? AND status='running'""",
                    (now, duration, row["run_id"]),
                )
                count += cur.rowcount
        return count

    def recover_superseded(self, payload: dict[str, Any]) -> int:
        """Interrupt prior turns when a new turn begins in the same session."""
        session = self.pseudonym(str(payload.get("session_id", "")), "session")
        turn = self.pseudonym(str(payload.get("turn_id", "")), "turn")
        if not session or not turn:
            return 0
        prompt_started_at = utc_now()
        with self.connection() as db:
            self._record_turn_lifecycle(
                db,
                session,
                turn,
                prompt_started_at=prompt_started_at,
            )
            return self._reconcile_session_lifecycle(
                db,
                session,
                successor_turn=turn,
                successor_at=prompt_started_at,
            )

    def recover_proven_orphans(self) -> int:
        """Interrupt running rows whose session has already completed later work."""
        count = 0
        with self.connection() as db:
            rows = db.execute(
                """SELECT r.run_id,r.started_at,later.started_at AS evidence_at
                   FROM skill_runs r
                   JOIN skill_runs later
                     ON later.session_hash=r.session_hash
                    AND later.turn_hash<>r.turn_hash
                    AND later.status<>'running'
                   WHERE r.status='running' AND r.session_hash<>''"""
            ).fetchall()
            earliest: dict[str, tuple[str, str]] = {}
            for row in rows:
                started_at = str(row["started_at"])
                evidence_at = str(row["evidence_at"])
                if not timestamp_strictly_precedes(
                    started_at, evidence_at
                ):
                    continue
                run_id = str(row["run_id"])
                current = earliest.get(run_id)
                if current is None or timestamp_strictly_precedes(
                    evidence_at, current[1]
                ):
                    earliest[run_id] = (started_at, evidence_at)
            for run_id, (started_at, evidence_at) in earliest.items():
                duration = self._duration_between(
                    started_at, evidence_at
                )
                cur = db.execute(
                    """UPDATE skill_runs
                       SET status='interrupted',ended_at=?,duration_ms=?,
                           end_reason='proven-orphan',
                           duration_quality='bounded'
                       WHERE run_id=? AND status='running'""",
                    (evidence_at, duration, run_id),
                )
                count += cur.rowcount
        return count

    @staticmethod
    def classify_sentiment(prompt: str) -> tuple[str, str, float] | None:
        text = re.sub(r"\s+", " ", prompt.strip())[:240]
        if not text:
            return None
        positive = bool(re.search(r"(?i)(^|[\s。、,!！])(?:いいね|良いね|ナイス|ないす|助かった|便利|最高|完璧|信頼した|ok|okay|大丈夫)(?:$|[\s。、,!！])", text))
        negative = bool(re.search(r"(?i)(^|[\s。、,!！])(?:違う|ちがう|だめ|ダメ|使えない|動いてない|面倒|不満|失敗|やめて|直して|修正して)(?:$|[\s。、,!！])", text))
        if not positive and not negative:
            return None
        if positive and negative:
            return "mixed", "explicit-mixed-reaction", 0.85
        if positive:
            return "positive", "explicit-approval", 0.9
        return "negative", "explicit-complaint-or-correction", 0.9

    def attach_reaction(self, payload: dict[str, Any]) -> int:
        prompt = payload.get("prompt")
        if not isinstance(prompt, str):
            return 0
        classified = self.classify_sentiment(prompt)
        if not classified:
            return 0
        sentiment, feeling, confidence = classified
        session = self.pseudonym(str(payload.get("session_id", "")), "session")
        current_turn = self.pseudonym(str(payload.get("turn_id", "")), "turn")
        signature = hmac.new(self._secret(), prompt.strip().encode(), hashlib.sha256).hexdigest()
        observed_at = utc_now()
        with self.connection() as db:
            runs = self._feedback_target_runs(
                db, session, current_turn, observed_at
            )
            if not runs:
                return 0
            count = 0
            for row in runs:
                cur = db.execute(
                    """INSERT OR IGNORE INTO skill_feedback
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (
                        "skillfb_" + uuid.uuid4().hex, row["run_id"], sentiment, None,
                        feeling, "hook-inferred-explicit-language", confidence, signature, observed_at,
                    ),
                )
                count += cur.rowcount
            if runs:
                evidence_result = {
                    "positive": "passed",
                    "negative": "failed",
                    "mixed": "ambiguous",
                }[sentiment]
                subject_hash = self.pseudonym(signature, "evidence-subject")
                idem = hashlib.sha256(
                    f"{session}|{current_turn}|explicit-feedback|{evidence_result}|{signature}".encode()
                ).hexdigest()
                proposed_id = "skillevidence_" + uuid.uuid4().hex
                db.execute(
                    """INSERT OR IGNORE INTO skill_evidence
                       (evidence_id,idempotency_key,session_hash,turn_hash,repo_hash,
                        evidence_class,result,subject_hash,detection,observed_at,
                        provenance_trust)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        proposed_id, idem, session, current_turn,
                        self.pseudonym(str(payload.get("cwd", "")), "repo"),
                        "explicit-feedback", evidence_result, subject_hash,
                        "hook-inferred-explicit-language", utc_now(),
                        TRUSTED_PROVENANCE,
                    ),
                )
                evidence = db.execute(
                    "SELECT evidence_id FROM skill_evidence WHERE idempotency_key=?", (idem,)
                ).fetchone()
                for run in runs:
                    db.execute(
                        """INSERT OR IGNORE INTO skill_run_evidence
                           (run_id,evidence_id,linked_at) VALUES(?,?,?)""",
                        (run["run_id"], evidence["evidence_id"], utc_now()),
                    )
        return count

    def add_feedback(self, run_id: str, sentiment: str, feeling: str, rating: int | None = None) -> str:
        run_id = self._require_run_id(run_id)
        if not isinstance(sentiment, str) or sentiment not in SENTIMENTS:
            raise ValueError("invalid sentiment")
        if not isinstance(feeling, str) or feeling not in FEELING_CLASSES:
            raise ValueError("invalid feeling class")
        if (
            rating is not None
            and (
                isinstance(rating, bool)
                or not isinstance(rating, int)
                or not 1 <= rating <= 5
            )
        ):
            raise ValueError("rating must be 1..5")
        signature = hashlib.sha256(
            f"{run_id}|{sentiment}|{rating}|{feeling}|{uuid.uuid4()}".encode()
        ).hexdigest()
        feedback_id = "skillfb_" + uuid.uuid4().hex
        with self.connection() as db:
            db.execute(
                "INSERT INTO skill_feedback VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    feedback_id,
                    run_id,
                    sentiment,
                    rating,
                    feeling,
                    "explicit-manual",
                    1.0,
                    signature,
                    utc_now(),
                ),
            )
        return feedback_id

    def evaluation_sample(self, skill_key: str, limit: int = 10, days: int = 30) -> list[dict[str, Any]]:
        """Select a reproducible, non-cherry-picked mix without reading conversation bodies."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, days))).replace(
            microsecond=0
        ).isoformat()
        rows = self.rows(
            """SELECT r.run_id,r.skill_key,r.skill_fingerprint,r.status,r.started_at,
                      r.duration_ms,r.duration_quality,r.end_reason,
                      r.provenance_trust,r.tool_failure_count,
                      CASE WHEN EXISTS(
                        SELECT 1 FROM skill_feedback f WHERE f.run_id=r.run_id
                      ) THEN 1 ELSE 0 END has_feedback
               FROM skill_runs r
               WHERE r.skill_key=? AND r.started_at>=? AND r.status<>'running'
                 AND r.provenance_trust=?
               ORDER BY r.started_at DESC,r.run_id""",
            (skill_key, cutoff, TRUSTED_PROVENANCE),
        )
        chosen: list[dict[str, Any]] = []
        seen: set[str] = set()

        def take(bucket: str, predicate, count: int, reverse: bool = False) -> None:
            source = list(reversed(rows)) if reverse else rows
            for row in source:
                if len([item for item in chosen if item["selection_bucket"] == bucket]) >= count:
                    break
                if row["run_id"] in seen or not predicate(row):
                    continue
                item = dict(row)
                item["selection_bucket"] = bucket
                chosen.append(item)
                seen.add(row["run_id"])

        take("recent-returned", lambda r: r["status"] == "returned", 4)
        take("interrupted", lambda r: r["status"] == "interrupted", 2)
        take("tool-failure", lambda r: r["tool_failure_count"] > 0, 2)
        take("explicit-feedback", lambda r: r["has_feedback"] == 1, 1)
        take("oldest-or-version-boundary", lambda r: True, 1, reverse=True)
        take("deterministic-fill", lambda r: True, max(0, limit - len(chosen)))
        return chosen[: max(1, min(limit, 100))]

    def add_evaluation(
        self,
        run_id: str,
        outcome: str,
        scores: dict[str, int] | None,
        evidence_classes: list[str],
        evidence_refs: list[str],
        evaluator: str,
        rubric_version: str = "outcome-v1",
    ) -> str:
        run_id = self._require_run_id(run_id)
        outcome, scores, compact_classes, raw_refs = (
            self.validate_evaluation_contract(
                outcome, scores, evidence_classes, evidence_refs
            )
        )
        if not isinstance(evaluator, str) or evaluator not in EVALUATORS:
            raise ValueError("invalid evaluator")
        if not self._valid_rubric_version(rubric_version):
            raise ValueError("invalid rubric version")
        compact_refs = sorted(
            {
                self._pseudonymize_evidence_reference(
                    item, strict=True
                )
                for item in raw_refs
            }
        )
        with self.connection() as db:
            run = db.execute(
                """SELECT skill_fingerprint,provenance_trust,status,ended_at,
                          end_reason,duration_quality
                   FROM skill_runs WHERE run_id=?""",
                (run_id,),
            ).fetchone()
            if not run:
                raise ValueError("unknown run_id")
            if run["provenance_trust"] != TRUSTED_PROVENANCE:
                raise ValueError("run provenance is not trusted")
            if outcome == "verified-success":
                if (
                    run["status"] != "returned"
                    or run["ended_at"] is None
                    or run["end_reason"] not in VERIFIED_SUCCESS_END_REASONS
                    or run["duration_quality"] != "exact"
                ):
                    raise ValueError(
                        "verified-success requires a trusted returned run "
                        "with a verified terminal reason and exact duration"
                    )
                if any(
                    not reference.startswith("evidence:")
                    for reference in raw_refs
                ):
                    raise ValueError(
                        "verified-success accepts only evidence:EVIDENCE_ID "
                        "references"
                    )
                evidence_ids = sorted(
                    {
                        reference.split(":", 1)[1]
                        for reference in raw_refs
                    }
                )
                linked: list[sqlite3.Row] = []
                if evidence_ids:
                    placeholders = ",".join("?" for _ in evidence_ids)
                    linked = db.execute(
                        f"""SELECT e.evidence_id,e.evidence_class,e.result,
                                   e.detection,e.provenance_trust,
                                   (SELECT COUNT(*)
                                      FROM skill_run_evidence all_links
                                     WHERE all_links.evidence_id=e.evidence_id)
                                     AS total_link_count,
                                   (SELECT COUNT(*)
                                      FROM skill_run_evidence target_link
                                     WHERE target_link.evidence_id=e.evidence_id
                                       AND target_link.run_id=?)
                                     AS target_link_count
                             FROM skill_evidence e
                            WHERE e.evidence_id IN ({placeholders})""",
                        (run_id, *evidence_ids),
                    ).fetchall()
                if len(linked) != len(evidence_ids):
                    raise ValueError(
                        "verified-success evidence must exist, be trusted, "
                        "be passed, and be linked to the evaluated run only"
                    )
                invalid = [
                    row
                    for row in linked
                    if row["result"] != "passed"
                    or row["provenance_trust"] != TRUSTED_PROVENANCE
                    or int(row["total_link_count"]) != 1
                    or int(row["target_link_count"]) != 1
                ]
                if invalid:
                    raise ValueError(
                        "verified-success evidence must exist, be trusted, "
                        "be passed, and be linked to the evaluated run only"
                    )
                observed_classes = {
                    str(row["evidence_class"]) for row in linked
                }
                claimed_classes = set(compact_classes)
                if (
                    not claimed_classes <= EVIDENCE_CLASSES
                    or claimed_classes != observed_classes
                ):
                    raise ValueError(
                        "verified-success evidence classes must exactly match "
                        "the linked evidence references"
                    )
                if not COMPLETION_EVIDENCE_CLASSES.intersection(
                    observed_classes
                ):
                    raise ValueError(
                        "verified-success requires linked passed completion evidence"
                    )
                for evidence_class in ("domain-verdict", "authority"):
                    rows = [
                        row
                        for row in linked
                        if row["evidence_class"] == evidence_class
                    ]
                    if not rows or any(
                        row["detection"] != "explicit-manual" for row in rows
                    ):
                        raise ValueError(
                            "verified-success requires linked explicit-manual "
                            f"{evidence_class} evidence"
                        )
            evaluation_id = "skilleval_" + uuid.uuid4().hex
            values = [(scores or {}).get(name) for name in (
                "outcome_achieved", "completion_evidence", "authority_safety",
                "avoidable_rework", "efficient_recoverable",
            )]
            total = sum(scores.values()) if scores else None
            db.execute(
                """INSERT OR REPLACE INTO skill_evaluations
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    evaluation_id, run_id, run["skill_fingerprint"], rubric_version, outcome,
                    *values, total, json.dumps(compact_classes), json.dumps(compact_refs),
                    evaluator, utc_now(),
                ),
            )
        return evaluation_id

    def health(self, hook: str, status: str, detail: str) -> None:
        hook, status, detail = self._require_health_values(
            hook, status, detail
        )
        try:
            with self.connection() as db:
                db.execute(
                    "INSERT INTO collector_health VALUES(?,?,?,?)",
                    (utc_now(), hook, status, detail),
                )
                db.execute(
                    """DELETE FROM collector_health WHERE rowid NOT IN
                       (SELECT rowid FROM collector_health ORDER BY observed_at DESC LIMIT 200)"""
                )
        except sqlite3.Error:
            pass

    def freshness(self) -> dict[str, Any]:
        """Describe how current a read-only report is and what it excludes."""
        spool = self.spool_status()
        result: dict[str, Any] = {
            "reported_at": utc_now(),
            "schema_version": None,
            "latest_receipt_at": None,
            "latest_run_started_at": None,
            "spool_pending": int(spool.get("pending", 0)),
            "spool_rejected": int(spool.get("rejected", 0)),
            "pending_events_excluded": bool(spool.get("pending", 0)),
            "read_mode": None,
        }
        if spool.get("unavailable"):
            result["spool_status"] = "unavailable"
        if not self.db_path.is_file():
            result["database"] = "uninitialized"
            return result
        try:
            with self.read_connection() as db:
                schema = db.execute(
                    "SELECT value FROM meta WHERE key='schema_version'"
                ).fetchone()
                result["schema_version"] = str(schema[0]) if schema else None
                result["latest_receipt_at"] = db.execute(
                    "SELECT MAX(processed_at) FROM spool_receipts"
                ).fetchone()[0]
                result["latest_run_started_at"] = db.execute(
                    "SELECT MAX(started_at) FROM skill_runs"
                ).fetchone()[0]
                result["read_mode"] = self._last_read_mode
        except (OSError, sqlite3.Error) as error:
            result["database"] = "unavailable"
            result["read_mode"] = self._last_read_mode
            result["read_error_class"] = (
                "busy" if is_busy_error(error) else "schema-or-io"
            )
        return result

    def status(self) -> dict[str, Any]:
        counts = {
            "runs": 0,
            "running": 0,
            "returned": 0,
            "failed": 0,
            "interrupted": 0,
            "feedback": 0,
            "evaluations": 0,
            "evidence": 0,
            "evidence_links": 0,
            "skills_seen": 0,
            "trusted_runs": 0,
            "legacy_unverified_runs": 0,
            "trusted_evidence": 0,
            "legacy_unverified_evidence": 0,
        }
        result: dict[str, Any] = {
            "root": str(self.root),
            "initialized": self.db_path.is_file(),
            "integrity": "uninitialized",
            "privacy_repair": "uninitialized",
            "counts": counts,
            "latest_run": None,
            "spool": self.spool_status(),
            "read_mode": None,
        }
        if not self.db_path.is_file():
            return result
        try:
            with self.read_connection() as db:
                result["integrity"] = db.execute(
                    "PRAGMA integrity_check"
                ).fetchone()[0]
                privacy_value = self._privacy_repair_value(db)
                result["privacy_repair"] = (
                    privacy_value
                    if privacy_value is not None
                    else "required"
                )
                counts.update(
                    {
                        "runs": db.execute(
                            "SELECT COUNT(*) FROM skill_runs"
                        ).fetchone()[0],
                        "running": db.execute(
                            "SELECT COUNT(*) FROM skill_runs "
                            "WHERE status='running'"
                        ).fetchone()[0],
                        "returned": db.execute(
                            "SELECT COUNT(*) FROM skill_runs "
                            "WHERE status='returned'"
                        ).fetchone()[0],
                        "failed": db.execute(
                            "SELECT COUNT(*) FROM skill_runs "
                            "WHERE status='failed'"
                        ).fetchone()[0],
                        "interrupted": db.execute(
                            "SELECT COUNT(*) FROM skill_runs "
                            "WHERE status='interrupted'"
                        ).fetchone()[0],
                        "feedback": db.execute(
                            "SELECT COUNT(*) FROM skill_feedback"
                        ).fetchone()[0],
                        "evaluations": db.execute(
                            "SELECT COUNT(*) FROM skill_evaluations"
                        ).fetchone()[0],
                        "evidence": db.execute(
                            "SELECT COUNT(*) FROM skill_evidence"
                        ).fetchone()[0],
                        "evidence_links": db.execute(
                            "SELECT COUNT(*) FROM skill_run_evidence"
                        ).fetchone()[0],
                        "skills_seen": db.execute(
                            "SELECT COUNT(DISTINCT skill_key) FROM skill_runs"
                        ).fetchone()[0],
                        "trusted_runs": db.execute(
                            "SELECT COUNT(*) FROM skill_runs "
                            "WHERE provenance_trust='trusted'"
                        ).fetchone()[0],
                        "legacy_unverified_runs": db.execute(
                            "SELECT COUNT(*) FROM skill_runs "
                            "WHERE provenance_trust='legacy-unverified'"
                        ).fetchone()[0],
                        "trusted_evidence": db.execute(
                            "SELECT COUNT(*) FROM skill_evidence "
                            "WHERE provenance_trust='trusted'"
                        ).fetchone()[0],
                        "legacy_unverified_evidence": db.execute(
                            "SELECT COUNT(*) FROM skill_evidence "
                            "WHERE provenance_trust='legacy-unverified'"
                        ).fetchone()[0],
                    }
                )
                result["latest_run"] = db.execute(
                    "SELECT MAX(started_at) FROM skill_runs"
                ).fetchone()[0]
                result["read_mode"] = self._last_read_mode
        except (OSError, sqlite3.Error) as error:
            result["integrity"] = "unavailable"
            result["read_mode"] = self._last_read_mode
            result["read_error_class"] = (
                "busy" if is_busy_error(error) else "schema-or-io"
            )
        return result

    def rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if not self.db_path.is_file():
            return []
        with self.read_connection() as db:
            return [dict(row) for row in db.execute(sql, params).fetchall()]

    def journal_mode(self) -> str | None:
        try:
            rows = self.rows("PRAGMA journal_mode")
            return str(rows[0]["journal_mode"]) if rows else None
        except (OSError, sqlite3.Error):
            return None


def looks_like_failure(response: Any) -> bool:
    for text in iter_strings(response):
        lowered = text.lower()
        if re.search(r"\b(exit code[: ]+[1-9]\d*|access denied|permission denied|timed? out|traceback)\b", lowered):
            return True
    if isinstance(response, dict):
        if response.get("success") is False or response.get("is_error") is True:
            return True
        code = response.get("exit_code", response.get("exitCode"))
        if isinstance(code, int) and code != 0:
            return True
    return False


def looks_like_success(response: Any, depth: int = 0) -> bool:
    """Recognize only explicit structured success markers."""
    if depth > 4:
        return False
    if response is True:
        return True
    if isinstance(response, dict):
        for key in ("exit_code", "exitCode", "returncode"):
            value = response.get(key)
            if (
                isinstance(value, int)
                and not isinstance(value, bool)
                and value == 0
            ):
                return True
        for key in ("success", "ok", "passed", "valid"):
            if response.get(key) is True:
                return True
        status = response.get("status")
        if isinstance(status, str) and status.lower() in {
            "passed",
            "success",
            "succeeded",
            "ok",
            "completed",
        }:
            return True
        return any(
            looks_like_success(value, depth + 1)
            for key, value in response.items()
            if key in {"result", "response", "output", "data"}
        )
    if isinstance(response, list):
        return any(
            looks_like_success(item, depth + 1) for item in response[:50]
        )
    return False
