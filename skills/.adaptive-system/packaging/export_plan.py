from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any, Generator, Iterable, Mapping, Sequence


EXCLUDED_PARTS = {".git", "__pycache__", ".pytest_cache"}
TRUSTED_VALIDATOR_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "validate_skill_registry.py"
)


def _load_registry_snapshot(content: bytes) -> Mapping[str, Any]:
    text = content.decode("utf-8-sig")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as json_error:
        try:
            import yaml  # type: ignore[import-not-found]
        except ImportError:
            raise ValueError(
                "registry is not JSON; PyYAML is required for canonical YAML registries"
            ) from json_error
        value = yaml.safe_load(text)
    if not isinstance(value, dict) or not isinstance(value.get("skills"), list):
        raise ValueError("registry must contain a skills array")
    return value


def _local_relative_path(entry: Mapping[str, Any]) -> str | None:
    resolutions = entry.get("resolutions")
    if isinstance(resolutions, dict):
        local = resolutions.get("local")
        if isinstance(local, dict):
            relative = local.get("relativePath")
            if isinstance(relative, str) and relative:
                return relative
    if entry.get("provider") == "local":
        relative = entry.get("relativePath", entry.get("key"))
        if isinstance(relative, str) and relative:
            return relative
    if entry.get("source") == "local":
        relative = entry.get("path", entry.get("key"))
        if isinstance(relative, str) and relative:
            return relative
    return None


def _local_destination_key(entry: Mapping[str, Any], relative: str) -> str:
    resolutions = entry.get("resolutions")
    if isinstance(resolutions, dict):
        local = resolutions.get("local")
        if isinstance(local, dict):
            key = local.get("key")
            if isinstance(key, str) and key:
                return key
    return Path(relative).name


def _safe_path(root: Path, relative: str) -> Path:
    root = root.resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Skill source escapes skills root: {relative}") from exc
    return candidate


def _files(path: Path, allowed_root: Path) -> Iterable[Path]:
    allowed_root = allowed_root.resolve()
    for candidate in sorted(path.rglob("*")):
        if not candidate.is_file():
            continue
        if any(part in EXCLUDED_PARTS for part in candidate.parts):
            continue
        if candidate.suffix == ".pyc":
            continue
        try:
            candidate.resolve().relative_to(allowed_root)
        except ValueError as exc:
            raise ValueError(
                f"Skill source resolves outside skills root: {candidate}"
            ) from exc
        yield candidate


def _digest(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


@contextmanager
def _trusted_validator() -> Generator[tuple[ModuleType, str], None, None]:
    validator_path = TRUSTED_VALIDATOR_PATH
    if not validator_path.is_file():
        raise ValueError("canonical registry validator is unavailable")
    source = validator_path.read_bytes()
    digest = f"sha256:{hashlib.sha256(source).hexdigest()}"
    module_name = (
        "_adaptive_registry_validator_"
        + digest.removeprefix("sha256:")[:16]
        + "_"
        + uuid.uuid4().hex
    )
    module = ModuleType(module_name)
    module.__file__ = str(validator_path)
    module.__package__ = ""
    sys.modules[module_name] = module
    try:
        exec(compile(source, str(validator_path), "exec"), module.__dict__)
        yield module, digest
    except Exception:
        raise
    finally:
        sys.modules.pop(module_name, None)


def _validated_snapshot(
    registry_path: Path,
    component_path: Path,
) -> tuple[dict[str, Any], Mapping[str, Any]]:
    registry_snapshot = registry_path.read_bytes()
    component_snapshot = component_path.read_bytes()
    registry_digest = f"sha256:{hashlib.sha256(registry_snapshot).hexdigest()}"
    component_digest = f"sha256:{hashlib.sha256(component_snapshot).hexdigest()}"
    with _trusted_validator() as (validator, validator_digest):
        result = validator.validate(
            registry_path,
            component_path,
            discover=True,
            registry_snapshot=registry_snapshot,
            component_snapshot=component_snapshot,
        )
        validator_version = str(validator.VALIDATOR_VERSION)
    if not result.valid:
        raise ValueError("registry validation failed: " + "; ".join(result.errors[:5]))
    return (
        {
            "valid": True,
            "validatorVersion": validator_version,
            "validatorDigest": validator_digest,
            "registryDigest": registry_digest,
            "componentRegistryDigest": component_digest,
            "warnings": list(result.warnings),
        },
        _load_registry_snapshot(registry_snapshot),
    )


def build_export_plan(
    *,
    registry_path: Path | str,
    skills_root: Path | str,
    plugin_name: str,
    selected_keys: Sequence[str] | None = None,
    component_registry_path: Path | str | None = None,
) -> dict[str, Any]:
    registry_path = Path(registry_path).expanduser().resolve()
    skills_root = Path(skills_root).expanduser().resolve()
    component_path = (
        Path(component_registry_path).expanduser().resolve()
        if component_registry_path is not None
        else skills_root / "component-registry.yaml"
    )
    validation, registry = _validated_snapshot(registry_path, component_path)
    entries = {
        entry.get("key"): entry
        for entry in registry["skills"]
        if isinstance(entry, dict) and isinstance(entry.get("key"), str)
    }
    keys = list(selected_keys) if selected_keys is not None else sorted(entries)
    missing = sorted(set(keys) - set(entries))
    if missing:
        raise ValueError(f"Unknown Skill keys: {', '.join(missing)}")

    source_map: list[dict[str, str]] = []
    external_dependencies: list[dict[str, str]] = []
    for key in keys:
        entry = entries[key]
        relative = _local_relative_path(entry)
        if relative is None:
            external_dependencies.append(
                {
                    "key": key,
                    "provider": str(
                        entry.get("source", entry.get("provider", "external"))
                    ),
                }
            )
            continue
        skill_path = _safe_path(skills_root, relative)
        if not skill_path.is_dir() or not (skill_path / "SKILL.md").is_file():
            raise ValueError(f"Local Skill source is unavailable: {key}")
        destination_key = _local_destination_key(entry, relative)
        if (
            not destination_key
            or destination_key in {".", ".."}
            or any(character in destination_key for character in '<>:"/\\|?*')
        ):
            raise ValueError(f"Local Skill has an unsafe export key: {key}")
        normalized_source_root = skill_path.relative_to(skills_root).as_posix()
        for source in _files(skill_path, skills_root):
            within_skill = source.relative_to(skill_path).as_posix()
            source_map.append(
                {
                    "skillKey": key,
                    "source": f"{normalized_source_root}/{within_skill}",
                    "destination": f"skills/{destination_key}/{within_skill}",
                    "digest": _digest(source),
                }
            )

    source_map.sort(key=lambda item: (item["skillKey"], item["destination"]))
    source_snapshot_digest = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                source_map,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
    )
    return {
        "schemaVersion": "1.0",
        "mode": "dry-run",
        "manifestPreview": {
            "schemaVersion": "1.0",
            "name": plugin_name,
            "version": "0.0.0-local-export-preview",
            "description": "Generated preview only; not an installable artifact.",
            "skillKeys": keys,
        },
        "sourceMap": source_map,
        "sourceSnapshotDigest": source_snapshot_digest,
        "validation": validation,
        "externalDependencies": external_dependencies,
        "actions": {
            "writeSource": False,
            "package": False,
            "install": False,
            "activate": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Print a deterministic Plugin export preview without packaging or installing."
    )
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--skills-root", type=Path, required=True)
    parser.add_argument("--components", type=Path)
    parser.add_argument("--plugin-name", required=True)
    parser.add_argument("--skill", action="append", dest="skills")
    args = parser.parse_args()
    try:
        plan = build_export_plan(
            registry_path=args.registry,
            skills_root=args.skills_root,
            plugin_name=args.plugin_name,
            selected_keys=args.skills,
            component_registry_path=args.components,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "invalid", "reason": str(exc)}, ensure_ascii=False))
        return 2
    rendered = json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
