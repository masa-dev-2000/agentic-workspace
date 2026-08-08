"""Backward-compatible Registry and Phase 0-3 entry validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover - environment error
    raise RuntimeError("registry validation requires PyYAML") from exc


METADATA_FIELDS = frozenset(
    {
        "public_entry",
        "allowed_callers",
        "exclusive_group",
        "authority",
        "blocking",
        "lifecycle_phase",
        "canonical_store",
        "produces",
        "consumes",
    }
)
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


class RegistryError(ValueError):
    """Invalid Registry or invocation."""


def default_registry_paths() -> tuple[Path, Path]:
    root = Path(__file__).resolve().parents[2]
    return root / "skill-registry.yaml", Path(__file__).resolve().parents[1] / "references" / "registry-orchestration.yaml"


def load_registry(
    registry_path: str | Path | None = None,
    overlay_path: str | Path | None = None,
) -> dict[str, Any]:
    default_registry, default_overlay = default_registry_paths()
    registry_file = Path(registry_path or default_registry)
    overlay_file = Path(overlay_path or default_overlay)
    registry = yaml.safe_load(registry_file.read_text(encoding="utf-8")) or {}
    overlay = yaml.safe_load(overlay_file.read_text(encoding="utf-8")) or {}
    if not isinstance(registry, dict) or not isinstance(overlay, dict):
        raise RegistryError("Registry documents must be mappings")
    result = dict(registry)
    result["orchestrationProfiles"] = overlay.get("profiles", {})
    validate_registry(result)
    return result


def _strings(value: Any, field: str, *, allow_empty: bool = False) -> None:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise RegistryError(f"{field} must be a non-empty list")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise RegistryError(f"{field} must contain non-empty strings")


def validate_registry(registry: dict[str, Any]) -> dict[str, Any]:
    skills = registry.get("skills", [])
    if not isinstance(skills, list):
        raise RegistryError("skills must be a list")
    keys: list[str] = []
    for item in skills:
        if not isinstance(item, dict) or not isinstance(item.get("key"), str) or not item["key"].strip():
            raise RegistryError("every skill must have a non-empty key")
        keys.append(item["key"])
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise RegistryError(f"duplicate Registry skill keys: {duplicates}")

    profiles = registry.get("orchestrationProfiles", {})
    if not isinstance(profiles, dict):
        raise RegistryError("orchestrationProfiles must be a mapping")
    for name, metadata in profiles.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(metadata, dict):
            raise RegistryError("invalid orchestration profile")
        unknown = set(metadata) - METADATA_FIELDS
        if unknown:
            raise RegistryError(f"unknown metadata fields for {name}: {sorted(unknown)}")
        required = METADATA_FIELDS - {"lifecycle_phase", "produces", "consumes"}
        missing = required - set(metadata)
        if missing:
            raise RegistryError(f"missing metadata fields for {name}: {sorted(missing)}")
        if not isinstance(metadata["public_entry"], bool) or not isinstance(metadata["blocking"], bool):
            raise RegistryError(f"public_entry and blocking must be boolean for {name}")
        _strings(metadata["allowed_callers"], f"allowed_callers for {name}")
        _strings(metadata["produces"], f"produces for {name}")
        _strings(metadata["consumes"], f"consumes for {name}")
        for field in ("exclusive_group", "canonical_store"):
            if not isinstance(metadata[field], str) or not metadata[field].strip():
                raise RegistryError(f"{field} must be a non-empty string for {name}")
        if metadata["authority"] not in AUTHORITIES:
            raise RegistryError(f"invalid authority for {name}: {metadata['authority']}")
        if not isinstance(metadata["lifecycle_phase"], str) or not metadata["lifecycle_phase"].strip():
            raise RegistryError(f"lifecycle_phase must be a non-empty string for {name}")
    return registry


def validate_invocation(registry: dict[str, Any], entry: str, caller: str) -> dict[str, Any]:
    if not isinstance(entry, str) or not entry.strip() or not isinstance(caller, str) or not caller.strip():
        raise RegistryError("entry and caller must be non-empty strings")
    profiles = registry.get("orchestrationProfiles", {})
    if entry in profiles:
        metadata = profiles[entry]
        if caller not in metadata["allowed_callers"]:
            raise RegistryError(f"caller {caller} is not allowed for {entry}")
        return {"entry": entry, "caller": caller, "kind": "orchestration-profile", "metadata": metadata}
    legacy = {item.get("key") for item in registry.get("skills", []) if isinstance(item, dict)}
    if entry in legacy:
        return {"entry": entry, "caller": caller, "kind": "legacy-registered", "direct_invocation_detected": caller == "user"}
    raise RegistryError(f"Registry-outside direct invocation: {entry}")
