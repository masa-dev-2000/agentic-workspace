from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

from spool_contract import (
    MAX_SPOOL_BYTES,
    SPOOL_VERSION,
    canonical_json,
    existing_key,
    keyed_hash,
    purge_tombstone_path,
    sign_record,
    utc_now,
)


MAX_STDIN_BYTES = 1_000_000
MAX_PROMPT_CHARS = 12_000

# Deliberately narrow. Classification may inspect the prompt transiently, but the
# prompt and all derived free text are discarded before the envelope is signed.
PATTERNS = {
    "complaint": [
        r"同じ.+繰り返.+やめ",
        r"毎回.+面倒",
        r"困(?:る|った)",
        r"不満",
        r"ダメじゃん",
        r"stop (?:doing|repeating)",
        r"frustrat(?:ed|ing)",
        r"every time.+annoy",
    ],
    "correction": [
        r"(?:ちがう|違う)[、, ]",
        r"そう(?:いう|じゃ)ない",
        r"訂正",
        r"I meant\b",
        r"that's not what I",
    ],
    "preference": [
        r"(?:デフォルト|今後|これから).+(?:して|にして)(?:ほしい|ください)",
        r"(?:prefer|always)\b",
    ],
    "request": [
        r"(?:仕組み|機能|場所|スキル).+(?:欲しい|ほしい|作って)",
        r"ためて(?:お|置)け",
        r"記録して",
        r"登録して",
        r"覚えておいて",
        r"please remember",
        r"keep track of",
        r"I want (?:a|this)",
    ],
}

SUBJECT_PATTERNS = [
    ("review", "workflow", r"review|レビュー|gan"),
    ("skill", "skill", r"skill|スキル"),
    ("workflow", "workflow", r"workflow|手順|仕組み|毎回|デフォルト|フック"),
    ("communication", "user", r"説明|返答|聞|ask|response"),
    ("memory", "workflow", r"記録|登録|覚え|ため|蓄積|memory"),
]

PERSISTENCE_RE = re.compile(
    r"今後|これから|常に|毎回|デフォルト|覚えておいて|記録して|登録して|"
    r"ためて(?:お|置)け|同じ.+繰り返|please remember|keep track of|\balways\b",
    re.I | re.S,
)


def data_home() -> Path:
    override = os.environ.get("CODEX_FEEDBACK_LEARNING_HOME")
    if override:
        return Path(override).expanduser().resolve()
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    return (codex_home / "feedback-learning").resolve()


def classify(prompt: str) -> str | None:
    for kind in ("complaint", "correction", "preference", "request"):
        if any(re.search(pattern, prompt, re.I | re.S) for pattern in PATTERNS[kind]):
            return kind
    return None


def subject(prompt: str) -> tuple[str, str]:
    for label, subject_kind, pattern in SUBJECT_PATTERNS:
        if re.search(pattern, prompt, re.I):
            return label, subject_kind
    return "general", "unknown"


def build_record(payload: dict, key: bytes) -> dict | None:
    if payload.get("hook_event_name") != "UserPromptSubmit":
        return None
    prompt = next(
        (
            payload.get(name)
            for name in ("prompt", "user_prompt", "message")
            if isinstance(payload.get(name), str)
        ),
        "",
    )
    if not prompt or len(prompt) > MAX_PROMPT_CHARS:
        return None
    kind = classify(prompt)
    if not kind:
        return None

    subject_class, subject_kind = subject(prompt)
    session_raw = str(payload.get("session_id") or "")
    turn_raw = str(payload.get("turn_id") or "")
    repo_raw = str(payload.get("cwd") or "")
    reaction_signature = keyed_hash(key, "reaction", prompt.strip())
    session_hash = keyed_hash(key, "session", session_raw)
    turn_hash = keyed_hash(key, "turn", turn_raw)
    repo_hash = keyed_hash(key, "repo", repo_raw)
    event_source = "|".join(
        ("feedback", session_hash, turn_hash, repo_hash, reaction_signature)
    )
    record = {
        "version": SPOOL_VERSION,
        "event_type": "feedback",
        "event_id": keyed_hash(key, "spool-event", event_source),
        "observed_at": utc_now(),
        "session_hash": session_hash,
        "turn_hash": turn_hash,
        "repo_hash": repo_hash,
        "feedback_type": kind,
        "subject_class": subject_class,
        "theme_key": f"{kind}-{subject_class}",
        "impact": "medium",
        "explicitness": "explicit",
        "source_kind": "user",
        "subject_kind": subject_kind,
        "valence": "negative" if kind in {"complaint", "correction"} else "neutral",
        "evidence_role": "support",
        "persistence_requested": bool(PERSISTENCE_RE.search(prompt)),
        "reaction_signature": reaction_signature,
    }
    return sign_record(record, key)


def spool_record(record: dict, root: Path) -> Path | None:
    tombstone = purge_tombstone_path(root)
    if tombstone.exists():
        return None
    encoded = canonical_json(record).encode("utf-8")
    if len(encoded) > MAX_SPOOL_BYTES:
        return None
    spool = root / "spool"
    spool.mkdir(parents=True, exist_ok=True)
    nonce = uuid.uuid4().hex
    final = spool / f"{time.time_ns():020d}-{nonce}.json"
    temp = spool / f".{nonce}.{os.getpid()}.tmp"
    try:
        if tombstone.exists():
            return None
        with temp.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
        if tombstone.exists():
            return None
        os.replace(temp, final)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
    return final


def main() -> int:
    try:
        root = data_home()
        if purge_tombstone_path(root).exists():
            return 0
        if (root / "disabled").exists():
            return 0
        key = existing_key(root / "hmac.key")
        if key is None:
            return 0
        raw = sys.stdin.buffer.read(MAX_STDIN_BYTES + 1)
        if len(raw) > MAX_STDIN_BYTES:
            return 0
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            return 0
        record = build_record(payload, key)
        if record is not None:
            spool_record(record, root)
    except Exception:
        # Lifecycle collection is fail-open and emits no model-visible output.
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
