from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


class CapabilityResolutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class CapabilityResolution:
    capability: str
    registry_key: str
    invocation_key: str
    source: str
    skill_path: Path | None
    version: str | None
    contract_hash: str | None

    def to_json(self) -> dict[str, Any]:
        value = asdict(self)
        value["skill_path"] = str(self.skill_path) if self.skill_path else None
        return value


def _load_registry(path: Path) -> Mapping[str, Any]:
    text = path.read_text(encoding="utf-8-sig")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as json_error:
        try:
            import yaml  # type: ignore[import-not-found]
        except ImportError:
            raise CapabilityResolutionError(
                f"{path} is not JSON; install PyYAML only if the canonical registry uses YAML"
            ) from json_error
        value = yaml.safe_load(text)
    if not isinstance(value, dict):
        raise CapabilityResolutionError("registry root must be an object")
    if not isinstance(value.get("skills"), list):
        raise CapabilityResolutionError("registry.skills must be an array")
    return value


def _capabilities(entry: Mapping[str, Any]) -> set[str]:
    result: set[str] = set()
    singular = entry.get("capability")
    if isinstance(singular, str):
        result.add(singular)
    plural = entry.get("capabilities")
    if isinstance(plural, list):
        result.update(value for value in plural if isinstance(value, str))
    key = entry.get("key")
    if isinstance(key, str):
        result.add(key)
    return result


def _safe_local_path(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    resolved_root = root.resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise CapabilityResolutionError(
            f"local resolution escapes the configured skills root: {relative}"
        ) from exc
    return candidate


class CapabilityResolver:
    def __init__(
        self,
        registry_path: Path | str,
        skills_root: Path | str | None = None,
    ) -> None:
        self.registry_path = Path(registry_path).expanduser().resolve()
        if skills_root is None:
            configured = os.environ.get("ADAPTIVE_SKILLS_ROOT")
            skills_root = Path(configured) if configured else self.registry_path.parent
        self.skills_root = Path(skills_root).expanduser().resolve()
        self.registry = _load_registry(self.registry_path)

    def _matching_entries(self, capability: str) -> Iterable[Mapping[str, Any]]:
        external = self.registry.get("externalCapabilities")
        if isinstance(external, list):
            for item in external:
                if (
                    isinstance(item, dict)
                    and item.get("id") == capability
                    and isinstance(item.get("providerKey"), str)
                ):
                    yield {
                        "key": item["providerKey"],
                        "capability": capability,
                        "source": "external",
                    }
        bindings = self.registry.get("capabilityBindings")
        binding = bindings.get(capability) if isinstance(bindings, dict) else None
        bound_keys: set[str] = set()
        if isinstance(binding, str):
            bound_keys.add(binding)
        elif isinstance(binding, list):
            bound_keys.update(item for item in binding if isinstance(item, str))
        elif isinstance(binding, dict):
            for key in ("key", "localKey", "pluginKey"):
                value = binding.get(key)
                if isinstance(value, str):
                    bound_keys.add(value)

        for raw in self.registry["skills"]:
            if not isinstance(raw, dict):
                continue
            key = raw.get("key")
            if capability in _capabilities(raw) or key in bound_keys:
                yield raw

    def resolve(
        self,
        capability: str,
        *,
        prefer: str = "local",
    ) -> CapabilityResolution:
        if prefer not in {"local", "plugin"}:
            raise ValueError("prefer must be 'local' or 'plugin'")
        entries = list(self._matching_entries(capability))
        if not entries:
            raise CapabilityResolutionError(
                f"no registry entry provides {capability!r}"
            )

        candidates: list[CapabilityResolution] = []
        for entry in entries:
            registry_key = entry.get("key")
            if not isinstance(registry_key, str) or not registry_key:
                continue
            resolutions = entry.get("resolutions")
            if not isinstance(resolutions, dict):
                resolutions = {}
            source_name = entry.get("source")
            is_local_source = source_name == "local" or (
                source_name is None and entry.get("provider") == "local"
            )

            local = resolutions.get("local")
            if not isinstance(local, dict) and is_local_source:
                local = {
                    "key": registry_key,
                    "relativePath": entry.get(
                        "path",
                        entry.get("relativePath", registry_key),
                    ),
                }
            if isinstance(local, dict):
                local_key = local.get("key", registry_key)
                relative = local.get("relativePath", local_key)
                if isinstance(local_key, str) and isinstance(relative, str):
                    path = _safe_local_path(self.skills_root, relative)
                    if path.is_dir() and (path / "SKILL.md").is_file():
                        candidates.append(
                            CapabilityResolution(
                                capability=capability,
                                registry_key=registry_key,
                                invocation_key=local_key,
                                source="local",
                                skill_path=path,
                                version=_optional_string(entry.get("version")),
                                contract_hash=_optional_string(
                                    entry.get(
                                        "contractContentDigest",
                                        entry.get(
                                            "contractHash",
                                            entry.get("contractFingerprint"),
                                        ),
                                    )
                                ),
                            )
                        )

            plugin = resolutions.get("plugin")
            if not isinstance(plugin, dict) and not is_local_source:
                plugin = {"key": registry_key}
            if isinstance(plugin, dict):
                plugin_key = plugin.get("key", registry_key)
                if isinstance(plugin_key, str) and plugin_key:
                    candidates.append(
                        CapabilityResolution(
                            capability=capability,
                            registry_key=registry_key,
                            invocation_key=plugin_key,
                            source="plugin",
                            skill_path=None,
                            version=_optional_string(entry.get("version")),
                            contract_hash=_optional_string(
                                entry.get(
                                    "contractContentDigest",
                                    entry.get(
                                        "contractHash",
                                        entry.get("contractFingerprint"),
                                    ),
                                )
                            ),
                        )
                    )

        order = (prefer, "plugin" if prefer == "local" else "local")
        for source in order:
            for candidate in candidates:
                if candidate.source == source:
                    return candidate
        raise CapabilityResolutionError(
            f"{capability!r} is registered but has no available local or plugin resolution"
        )


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resolve a capability to a local or plugin Skill key."
    )
    parser.add_argument("capability")
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--skills-root", type=Path)
    parser.add_argument("--prefer", choices=("local", "plugin"), default="local")
    args = parser.parse_args()
    try:
        resolution = CapabilityResolver(args.registry, args.skills_root).resolve(
            args.capability,
            prefer=args.prefer,
        )
    except (CapabilityResolutionError, OSError, ValueError) as exc:
        print(
            json.dumps({"status": "unresolved", "reason": str(exc)}, ensure_ascii=False)
        )
        return 2
    print(json.dumps(resolution.to_json(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
