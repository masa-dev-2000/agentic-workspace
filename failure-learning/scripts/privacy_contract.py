from __future__ import annotations

import re
from typing import Iterator, NamedTuple


SECRET_FIELD_NAMES = (
    "authorization",
    "api_key",
    "api-key",
    "apikey",
    "access_token",
    "access-token",
    "accesstoken",
    "refresh_token",
    "refresh-token",
    "refreshtoken",
    "password",
    "passwd",
    "secret",
    "cookie",
)


def _key_character_pattern(character: str) -> str:
    codepoint = f"{ord(character):04x}"
    byte = f"{ord(character):02x}"
    # Multiple backslashes cover JSON nested inside escaped string layers
    # while the match offsets still refer to the original text.
    return rf"(?:{re.escape(character)}|\\+u{codepoint}|\\+x{byte})"


SECRET_FIELD_PATTERN = "(?:" + "|".join(
    "".join(_key_character_pattern(character) for character in name)
    for name in SECRET_FIELD_NAMES
) + ")"
SECRET_ASSIGNMENT_RE = re.compile(
    rf"""(?ix)
    (?<![A-Za-z0-9_])
    (?:\\*["'])?
    (?P<key>{SECRET_FIELD_PATTERN})
    (?:\\*["'])?
    (?:\s*\])?
    \s*[:=]\s*
    """
)
AUTH_SCHEME_VALUE_RE = re.compile(
    r"(?i)\b(?P<scheme>bearer|basic)\s+"
    r"(?P<value>[A-Za-z0-9._~+/=-]+)"
)
REDACTED_VALUES = {"<REDACTED>", "[REDACTED]"}
UNQUOTED_END_CHARS = frozenset(",;}]")
ASCII_KEY_ESCAPE_RE = re.compile(
    r"(?i)\\+(?:u(?P<unicode>[0-9a-f]{4})|x(?P<byte>[0-9a-f]{2}))"
)


class CredentialAssignment(NamedTuple):
    key: str
    value_start: int
    value_end: int
    replacement: str


def _quoted_value_span(
    text: str,
    start: int,
) -> tuple[int, str] | None:
    quote_index = start
    while quote_index < len(text) and text[quote_index] == "\\":
        quote_index += 1
    if quote_index >= len(text) or text[quote_index] not in {'"', "'"}:
        return None

    slash_count = quote_index - start
    quote = text[quote_index]
    cursor = quote_index + 1
    while cursor < len(text):
        if text[cursor] != quote:
            cursor += 1
            continue
        preceding_slashes = 0
        check = cursor - 1
        while check >= 0 and text[check] == "\\":
            preceding_slashes += 1
            check -= 1
        if (
            (slash_count == 0 and preceding_slashes % 2 == 0)
            or (slash_count > 0 and preceding_slashes == slash_count)
        ):
            delimiter = ("\\" * slash_count) + quote
            return cursor + 1, f"{delimiter}<REDACTED>{delimiter}"
        cursor += 1
    # An unterminated quoted credential is malformed but still sensitive.
    # Redact the remainder rather than leaking the tail after a delimiter guess.
    return len(text), "<REDACTED>"


def _balanced_container_end(text: str, start: int) -> int | None:
    pairs = {"{": "}", "[": "]"}
    opener = text[start] if start < len(text) else ""
    if opener not in pairs:
        return None
    stack = [pairs[opener]]
    cursor = start + 1
    while cursor < len(text):
        if text[cursor] in {'"', "'", "\\"}:
            quoted = _quoted_value_span(text, cursor)
            if quoted is not None:
                cursor = quoted[0]
                continue
        if text[cursor] in pairs:
            stack.append(pairs[text[cursor]])
        elif text[cursor] in {"}", "]"}:
            if not stack or text[cursor] != stack[-1]:
                return len(text)
            stack.pop()
            if not stack:
                return cursor + 1
        cursor += 1
    return len(text)


def _container_replacement(assignment_prefix: str) -> str:
    quoted_key = re.search(r"(?P<slashes>\\*)?(?P<quote>[\"'])", assignment_prefix)
    if quoted_key and quoted_key.group("slashes"):
        delimiter = quoted_key.group("slashes") + quoted_key.group("quote")
        return f"{delimiter}<REDACTED>{delimiter}"
    return '"<REDACTED>"'


def _unquoted_value_end(text: str, start: int, key: str) -> int:
    if key == "authorization":
        scheme = re.match(
            r"(?i)(?:bearer|basic)\s+[^\s,;}\]]+",
            text[start:],
        )
        if scheme:
            return start + scheme.end()
    cursor = start
    while (
        cursor < len(text)
        and not text[cursor].isspace()
        and text[cursor] not in UNQUOTED_END_CHARS
    ):
        cursor += 1
    return cursor


def _canonical_credential_key(value: str) -> str:
    def decode(match: re.Match[str]) -> str:
        encoded = match.group("unicode") or match.group("byte") or ""
        codepoint = int(encoded, 16)
        return chr(codepoint) if codepoint <= 0x7F else ""

    return ASCII_KEY_ESCAPE_RE.sub(decode, value).lower()


def iter_credential_assignments(text: str) -> Iterator[CredentialAssignment]:
    """Yield credential value spans for plain, JSON, and escaped-JSON assignments."""
    cursor = 0
    while cursor < len(text):
        match = SECRET_ASSIGNMENT_RE.search(text, cursor)
        if match is None:
            return
        value_start = match.end()
        if value_start >= len(text):
            return
        key = _canonical_credential_key(str(match.group("key")))
        quoted = _quoted_value_span(text, value_start)
        if quoted is not None:
            value_end, replacement = quoted
        else:
            container_end = _balanced_container_end(text, value_start)
            if container_end is not None:
                value_end = container_end
                replacement = _container_replacement(match.group(0))
            else:
                value_end = _unquoted_value_end(
                    text,
                    value_start,
                    key,
                )
                replacement = "<REDACTED>"
            if value_end <= value_start:
                cursor = max(match.end(), cursor + 1)
                continue
        yield CredentialAssignment(
            key,
            value_start,
            value_end,
            replacement,
        )
        cursor = max(value_end, match.end(), cursor + 1)


def _is_redacted_value(value: str) -> bool:
    normalized = value.replace("\\", "").strip().strip("\"'").upper()
    return normalized in REDACTED_VALUES


def redact_credential_assignments(text: str) -> str:
    assignments = list(iter_credential_assignments(text))
    if not assignments:
        return AUTH_SCHEME_VALUE_RE.sub(
            lambda match: f"{match.group('scheme').title()} <REDACTED>",
            text,
        )
    pieces: list[str] = []
    cursor = 0
    for assignment in assignments:
        pieces.append(text[cursor : assignment.value_start])
        pieces.append(assignment.replacement)
        cursor = assignment.value_end
    pieces.append(text[cursor:])
    redacted = "".join(pieces)
    return AUTH_SCHEME_VALUE_RE.sub(
        lambda match: f"{match.group('scheme').title()} <REDACTED>",
        redacted,
    )


def residual_credential_class(text: str) -> str | None:
    """Return a bounded reason when a credential value remains unredacted."""
    for assignment in iter_credential_assignments(text):
        candidate = text[assignment.value_start : assignment.value_end]
        if not _is_redacted_value(candidate):
            if assignment.key == "authorization":
                return "residual-authorization-value"
            return "residual-secret-value"
    if AUTH_SCHEME_VALUE_RE.search(text):
        return "residual-auth-scheme-value"
    return None
