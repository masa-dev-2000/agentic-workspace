from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = ROOT / "skill-registry.yaml"
DEFAULT_COMPONENTS = ROOT / "component-registry.yaml"
VALIDATOR_VERSION = "2.2.0"

SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
REQUIRED_SKILL_FIELDS = {
    "key",
    "capability",
    "source",
    "path",
    "exposure",
    "responsibility",
    "nonGoals",
    "triggers",
    "dependencies",
    "interfaces",
    "authority",
    "data",
    "completion",
    "failure",
    "telemetry",
    "evals",
    "maturity",
    "version",
    "contractFingerprint",
    "contractContentDigest",
    "primaryCategory",
    "primaryResponsibility",
    "entrypoint",
    "invocationPolicy",
    "delegatesTo",
    "stateOwner",
    "secondaryCapabilities",
}
REQUIRED_COMPONENT_FIELDS = {
    "id",
    "kind",
    "status",
    "responsibility",
    "implementation",
    "dependencies",
    "authority",
    "data",
    "version",
}
SURFACES = {"public", "internal", "project-local"}
INVOCATION_MODES = {"implicit", "explicit-only", "orchestrator-only"}
PRIMARY_CATEGORIES = {
    "decision-planning-governance",
    "execution-delegation-routing",
    "observation-verification-failure-control",
    "learning-memory-improvement",
    "artifact-communication",
    "product-development-environment",
    "session-human-interface",
}
ENTRYPOINTS = {"hook", "project", "planning", "explicit", "adaptive", "human"}
MATURITY_STAGES = {
    "experimental",
    "alpha",
    "beta",
    "stable",
    "deprecated",
    "quarantined",
}
COMPONENT_STATUSES = {"active", "legacy-external", "planned", "deprecated"}
DATA_WRITE_MODES = {"append", "mutate", "replace", "artifact"}
WRITE_MODE_EFFECTS = {
    "append": "state-append",
    "mutate": "state-mutate",
    "replace": "state-mutate",
}
CONTRACT_CONTENT_FIELDS = (
    "capability",
    "exposure",
    "responsibility",
    "nonGoals",
    "triggers",
    "dependencies",
    "interfaces",
    "authority",
    "data",
    "completion",
    "failure",
    "telemetry",
    "evals",
    "maturity",
    "version",
    "primaryCategory",
    "primaryResponsibility",
    "entrypoint",
    "invocationPolicy",
    "delegatesTo",
    "stateOwner",
    "secondaryCapabilities",
)


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    skills: int = 0
    components: int = 0
    discovered: int = 0

    @property
    def valid(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "schemaVersion": 2,
            "validatorVersion": VALIDATOR_VERSION,
            "skills": self.skills,
            "components": self.components,
            "discovered": self.discovered,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def _load_yaml_bytes(content: bytes, label: Path | str) -> dict[str, Any]:
    loaded = yaml.safe_load(content.decode("utf-8-sig"))
    if not isinstance(loaded, dict):
        raise ValueError(f"{label}: root must be a mapping")
    return loaded


def _load_yaml(path: Path) -> dict[str, Any]:
    return _load_yaml_bytes(path.read_bytes(), path)


def contract_content_digest(skill: dict[str, Any]) -> str:
    contract = {field: skill.get(field) for field in CONTRACT_CONTENT_FIELDS}
    rendered = json.dumps(
        contract,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(rendered).hexdigest()}"


def _expand_path(raw: str, base: Path) -> Path:
    expanded = os.path.expandvars(raw)
    path = Path(expanded)
    return path if path.is_absolute() else (base / path).resolve()


def _frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8-sig")
    if not text.startswith("---"):
        raise ValueError("missing YAML frontmatter")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ValueError("unterminated YAML frontmatter")
    loaded = yaml.safe_load(parts[1])
    if not isinstance(loaded, dict):
        raise ValueError("frontmatter must be a mapping")
    return loaded


def _openai_implicit_policy(skill_dir: Path) -> tuple[bool, str]:
    metadata_path = skill_dir / "agents" / "openai.yaml"
    if not metadata_path.exists():
        return True, "default-no-openai-yaml"
    loaded = _load_yaml(metadata_path)
    policy = loaded.get("policy", {})
    if not isinstance(policy, dict) or "allow_implicit_invocation" not in policy:
        return True, "default-policy-omitted"
    value = policy["allow_implicit_invocation"]
    if not isinstance(value, bool):
        raise ValueError("policy.allow_implicit_invocation must be boolean")
    return value, "explicit-policy"


def _find_cycle(graph: dict[str, list[str]]) -> list[str] | None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str, trail: list[str]) -> list[str] | None:
        if node in visiting:
            start = trail.index(node) if node in trail else 0
            return trail[start:] + [node]
        if node in visited:
            return None
        visiting.add(node)
        for child in graph.get(node, []):
            cycle = visit(child, trail + [node])
            if cycle:
                return cycle
        visiting.remove(node)
        visited.add(node)
        return None

    for node in graph:
        cycle = visit(node, [])
        if cycle:
            return cycle
    return None


def _validate_interfaces(label: str, interfaces: Any, result: ValidationResult) -> None:
    if not isinstance(interfaces, dict):
        result.errors.append(f"{label}: interfaces must be a mapping")
        return
    for direction in ("inputs", "outputs"):
        values = interfaces.get(direction)
        if not isinstance(values, list) or not values:
            result.errors.append(
                f"{label}: interfaces.{direction} must be a non-empty list"
            )
            continue
        for index, value in enumerate(values):
            if (
                not isinstance(value, dict)
                or not value.get("name")
                or not value.get("type")
            ):
                result.errors.append(
                    f"{label}: interfaces.{direction}[{index}] requires name and type"
                )


def _validate_authority(
    label: str, authority: Any, allowed_effects: set[str], result: ValidationResult
) -> set[str]:
    if not isinstance(authority, dict):
        result.errors.append(f"{label}: authority must be a mapping")
        return set()
    if authority.get("default") != "deny":
        result.errors.append(f"{label}: authority.default must be deny")
    effects = authority.get("effects")
    if not isinstance(effects, list):
        result.errors.append(f"{label}: authority.effects must be a list")
        return set()
    effect_set = set(effects)
    unknown = sorted(effect_set - allowed_effects)
    if unknown:
        result.errors.append(f"{label}: unknown authority effects {unknown}")
    return effect_set


def _validate_data(
    label: str, data: Any, effects: set[str], result: ValidationResult
) -> list[str]:
    if not isinstance(data, dict):
        result.errors.append(f"{label}: data must be a mapping")
        return []
    for field_name in ("reads", "writes", "ownerOf"):
        if not isinstance(data.get(field_name), list):
            result.errors.append(f"{label}: data.{field_name} must be a list")
    writes = data.get("writes", [])
    write_domains: set[str] = set()
    for index, write in enumerate(writes if isinstance(writes, list) else []):
        if not isinstance(write, dict):
            result.errors.append(f"{label}: data.writes[{index}] must be a mapping")
            continue
        domain = write.get("domain")
        mode = write.get("mode")
        if not domain or mode not in DATA_WRITE_MODES:
            result.errors.append(
                f"{label}: data.writes[{index}] requires domain and a valid mode"
            )
        else:
            write_domains.add(domain)
            required_effect = WRITE_MODE_EFFECTS.get(mode)
            if required_effect and required_effect not in effects:
                result.errors.append(
                    f"{label}: data.writes[{index}] mode {mode!r} "
                    f"requires authority effect {required_effect!r}"
                )
    owners = data.get("ownerOf", [])
    if isinstance(owners, list):
        for domain in owners:
            if domain not in write_domains:
                result.errors.append(
                    f"{label}: owner domain {domain!r} is not in data.writes"
                )
    if "state-mutate" in effects and not owners:
        result.errors.append(
            f"{label}: state-mutate requires at least one data.ownerOf domain"
        )
    return owners if isinstance(owners, list) else []


def _validate_completion(
    label: str,
    completion: Any,
    capabilities: set[str],
    components: set[str],
    result: ValidationResult,
) -> None:
    if not isinstance(completion, dict):
        result.errors.append(f"{label}: completion must be a mapping")
        return
    proof = completion.get("proof")
    verifier = completion.get("verifier")
    if not isinstance(proof, list) or not proof:
        result.errors.append(f"{label}: completion.proof must be a non-empty list")
    elif any(not isinstance(item, str) or not item.strip() for item in proof):
        result.errors.append(
            f"{label}: completion.proof entries must be non-empty strings"
        )
    if not isinstance(verifier, str) or not verifier:
        result.errors.append(f"{label}: completion.verifier is required")
    elif verifier in {"self", "human"}:
        pass
    elif verifier.startswith("capability:"):
        if verifier[11:] not in capabilities:
            result.errors.append(f"{label}: unknown completion verifier {verifier!r}")
    elif verifier.startswith("component:"):
        if verifier[10:] not in components:
            result.errors.append(f"{label}: unknown completion verifier {verifier!r}")
    else:
        result.errors.append(f"{label}: invalid completion verifier {verifier!r}")


def _validate_failure_and_telemetry(
    label: str, skill: dict[str, Any], result: ValidationResult
) -> None:
    failure = skill.get("failure")
    if not isinstance(failure, dict):
        result.errors.append(f"{label}: failure must be a mapping")
    else:
        max_attempts = failure.get("maxEquivalentAttempts")
        if not isinstance(max_attempts, int) or not 0 <= max_attempts <= 3:
            result.errors.append(
                f"{label}: failure.maxEquivalentAttempts must be an integer from 0 to 3"
            )
        if not failure.get("retry") or not failure.get("route"):
            result.errors.append(
                f"{label}: failure.retry and failure.route are required"
            )
    telemetry = skill.get("telemetry")
    if not isinstance(telemetry, dict):
        result.errors.append(f"{label}: telemetry must be a mapping")
    else:
        if telemetry.get("enabled") is not True:
            result.errors.append(f"{label}: telemetry.enabled must be true")
        if telemetry.get("contentPolicy") != "metadata-only":
            result.errors.append(
                f"{label}: telemetry contentPolicy must be metadata-only"
            )


def _validate_components(
    data: dict[str, Any], base: Path, result: ValidationResult
) -> tuple[set[str], set[str]]:
    if data.get("schemaVersion") != 1:
        result.errors.append("component-registry: schemaVersion must be 1")
    components = data.get("components", [])
    if not isinstance(components, list):
        result.errors.append("component-registry: components must be a list")
        return set(), set()
    result.components = len(components)
    allowed_effects = set(data.get("authorityEffects", []))
    ids = [
        component.get("id") for component in components if isinstance(component, dict)
    ]
    known = {item for item in ids if isinstance(item, str)}
    if len(ids) != len(known):
        result.errors.append("component-registry: duplicate component id")
    graph: dict[str, list[str]] = {}
    active_ids: set[str] = set()
    for index, component in enumerate(components):
        if not isinstance(component, dict):
            result.errors.append(f"components[{index}]: must be a mapping")
            continue
        label = component.get("id") or f"components[{index}]"
        missing = sorted(REQUIRED_COMPONENT_FIELDS - set(component))
        if missing:
            result.errors.append(f"{label}: missing {', '.join(missing)}")
        version = component.get("version")
        if not isinstance(version, str) or not SEMVER_RE.fullmatch(version):
            result.errors.append(f"{label}: invalid SemVer {version!r}")
        status = component.get("status")
        if status not in COMPONENT_STATUSES:
            result.errors.append(f"{label}: unknown status {status!r}")
        if status in {"active", "legacy-external"}:
            active_ids.add(label)
        implementation = component.get("implementation")
        if status in {"active", "legacy-external"}:
            if not isinstance(implementation, str) or not implementation:
                result.errors.append(
                    f"{label}: active component requires implementation"
                )
            elif not _expand_path(implementation, base).exists():
                result.errors.append(
                    f"{label}: implementation does not exist: {implementation}"
                )
        dependencies = component.get("dependencies")
        if not isinstance(dependencies, list):
            result.errors.append(f"{label}: dependencies must be a list")
            dependencies = []
        unknown_dependencies = sorted(set(dependencies) - known)
        if unknown_dependencies:
            result.errors.append(
                f"{label}: unknown component dependencies {unknown_dependencies}"
            )
        graph[label] = [
            dependency for dependency in dependencies if dependency in known
        ]
        effects = _validate_authority(
            label, component.get("authority"), allowed_effects, result
        )
        data_contract = component.get("data")
        if not isinstance(data_contract, dict):
            result.errors.append(f"{label}: data must be a mapping")
        else:
            reads = data_contract.get("reads")
            writes = data_contract.get("writes")
            if not isinstance(reads, list):
                result.errors.append(f"{label}: data.reads must be a list")
            if not isinstance(writes, list):
                result.errors.append(f"{label}: data.writes must be a list")
            else:
                for write_index, write in enumerate(writes):
                    if not isinstance(write, dict):
                        result.errors.append(
                            f"{label}: data.writes[{write_index}] must be a mapping"
                        )
                        continue
                    domain = write.get("domain")
                    mode = write.get("mode")
                    if not domain or mode not in DATA_WRITE_MODES:
                        result.errors.append(
                            f"{label}: data.writes[{write_index}] requires "
                            "domain and a valid mode"
                        )
                        continue
                    required_effect = WRITE_MODE_EFFECTS.get(mode)
                    if required_effect and required_effect not in effects:
                        result.errors.append(
                            f"{label}: data.writes[{write_index}] mode {mode!r} "
                            f"requires authority effect {required_effect!r}"
                        )
    cycle = _find_cycle(graph)
    if cycle:
        result.errors.append("component dependency cycle: " + " -> ".join(cycle))
    return known, active_ids


def validate(
    registry_path: Path = DEFAULT_REGISTRY,
    component_path: Path = DEFAULT_COMPONENTS,
    *,
    discover: bool = True,
    registry_snapshot: bytes | None = None,
    component_snapshot: bytes | None = None,
) -> ValidationResult:
    result = ValidationResult()
    try:
        registry = (
            _load_yaml_bytes(registry_snapshot, registry_path)
            if registry_snapshot is not None
            else _load_yaml(registry_path)
        )
    except Exception as exc:
        result.errors.append(f"registry load failed: {exc}")
        return result
    try:
        component_registry = (
            _load_yaml_bytes(component_snapshot, component_path)
            if component_snapshot is not None
            else _load_yaml(component_path)
        )
    except Exception as exc:
        result.errors.append(f"component registry load failed: {exc}")
        return result

    if registry.get("schemaVersion") != 2:
        result.errors.append("skill-registry: schemaVersion must be 2")
    component_ids, _ = _validate_components(
        component_registry, component_path.parent, result
    )
    allowed_effects = set(registry.get("authorityEffects", []))
    sources = registry.get("sources", {})
    if not isinstance(sources, dict) or not sources:
        result.errors.append("skill-registry: sources must be a non-empty mapping")
        sources = {}
    external_capabilities = registry.get("externalCapabilities", [])
    external_ids = {
        item.get("id")
        for item in external_capabilities
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    skills = registry.get("skills", [])
    if not isinstance(skills, list):
        result.errors.append("skill-registry: skills must be a list")
        skills = []
    result.skills = len(skills)
    keys = [item.get("key") for item in skills if isinstance(item, dict)]
    capabilities = [item.get("capability") for item in skills if isinstance(item, dict)]
    known_keys = {item for item in keys if isinstance(item, str)}
    known_capabilities = {item for item in capabilities if isinstance(item, str)}
    if len(keys) != len(known_keys):
        result.errors.append("skill-registry: duplicate skill key")
    if len(capabilities) != len(known_capabilities):
        result.errors.append("skill-registry: duplicate capability id")

    discovered_entries: dict[tuple[str, str], Path] = {}
    if discover:
        for source_id, source in sources.items():
            if not isinstance(source, dict) or source.get("discover") is not True:
                continue
            raw_root = source.get("root")
            if not isinstance(raw_root, str):
                result.errors.append(f"source {source_id}: root is required")
                continue
            source_root = _expand_path(raw_root, registry_path.parent)
            if not source_root.is_dir():
                result.errors.append(
                    f"source {source_id}: root does not exist: {source_root}"
                )
                continue
            namespace = source.get("namespace")
            for child in sorted(source_root.iterdir()):
                if child.name.startswith(".") or not (child / "SKILL.md").is_file():
                    continue
                key = f"{namespace}:{child.name}" if namespace else child.name
                discovered_entries[(source_id, child.name)] = child
                if key not in known_keys:
                    result.errors.append(f"unregistered discovered skill: {key}")
        result.discovered = len(discovered_entries)

    graph: dict[str, list[str]] = {}
    state_owners: dict[str, str] = {}
    implicit_triggers: dict[str, list[str]] = {}
    for index, skill in enumerate(skills):
        if not isinstance(skill, dict):
            result.errors.append(f"skills[{index}]: must be a mapping")
            continue
        label = skill.get("key") or f"skills[{index}]"
        missing = sorted(REQUIRED_SKILL_FIELDS - set(skill))
        if missing:
            result.errors.append(f"{label}: missing {', '.join(missing)}")
        capability = skill.get("capability")
        version = skill.get("version")
        if not isinstance(version, str) or not SEMVER_RE.fullmatch(version):
            result.errors.append(f"{label}: invalid SemVer {version!r}")
        expected_fingerprint = f"contract-v2:{capability}@{version}"
        if skill.get("contractFingerprint") != expected_fingerprint:
            result.errors.append(
                f"{label}: contractFingerprint must be {expected_fingerprint!r}"
            )
        expected_content_digest = contract_content_digest(skill)
        if skill.get("contractContentDigest") != expected_content_digest:
            result.errors.append(
                f"{label}: contractContentDigest must be {expected_content_digest!r}"
            )
        if skill.get("maturity") not in MATURITY_STAGES:
            result.errors.append(f"{label}: unknown maturity {skill.get('maturity')!r}")
        if len(str(skill.get("responsibility", "")).strip()) < 20:
            result.errors.append(f"{label}: responsibility is not meaningful")
        if skill.get("primaryCategory") not in PRIMARY_CATEGORIES:
            result.errors.append(f"{label}: invalid primaryCategory")
        if len(str(skill.get("primaryResponsibility", "")).strip()) < 20:
            result.errors.append(f"{label}: primaryResponsibility is not meaningful")
        if skill.get("entrypoint") not in ENTRYPOINTS:
            result.errors.append(f"{label}: invalid entrypoint")
        invocation_policy = skill.get("invocationPolicy")
        if invocation_policy not in INVOCATION_MODES | {"deprecated"}:
            result.errors.append(f"{label}: invalid invocationPolicy")
        delegates_to = skill.get("delegatesTo")
        if not isinstance(delegates_to, list) or any(
            not isinstance(value, str) or not value for value in delegates_to
        ):
            result.errors.append(f"{label}: delegatesTo must be a list of Skill keys")
        else:
            for target in delegates_to:
                if target == label:
                    result.errors.append(f"{label}: delegatesTo may not self-reference")
                elif target not in known_keys:
                    result.errors.append(f"{label}: delegatesTo unknown Skill {target!r}")
        state_owner = skill.get("stateOwner")
        if not isinstance(state_owner, str) or not state_owner:
            result.errors.append(f"{label}: stateOwner must be a Skill key or none")
        elif state_owner != "none" and state_owner not in known_keys:
            result.errors.append(f"{label}: stateOwner unknown Skill {state_owner!r}")
        secondary = skill.get("secondaryCapabilities")
        if not isinstance(secondary, list) or any(
            not isinstance(value, str) or not value for value in secondary
        ):
            result.errors.append(f"{label}: secondaryCapabilities must be a list")
        if invocation_policy == "deprecated" and skill.get("maturity") != "deprecated":
            result.errors.append(f"{label}: deprecated invocationPolicy requires deprecated maturity")
        non_goals = skill.get("nonGoals")
        if not isinstance(non_goals, list) or not non_goals:
            result.errors.append(f"{label}: nonGoals must be a non-empty list")

        exposure = skill.get("exposure")
        if not isinstance(exposure, dict):
            result.errors.append(f"{label}: exposure must be a mapping")
            exposure = {}
        surface = exposure.get("surface")
        invocation = exposure.get("invocation")
        if surface not in SURFACES:
            result.errors.append(f"{label}: unknown exposure surface {surface!r}")
        if invocation not in INVOCATION_MODES:
            result.errors.append(f"{label}: unknown invocation mode {invocation!r}")
        if surface == "internal" and invocation == "implicit":
            result.errors.append(
                f"{label}: internal Skill may not be implicitly invoked"
            )

        triggers = skill.get("triggers")
        if not isinstance(triggers, dict):
            result.errors.append(f"{label}: triggers must be a mapping")
        else:
            for trigger_type in ("positive", "negative"):
                values = triggers.get(trigger_type)
                if not isinstance(values, list) or not values:
                    result.errors.append(
                        f"{label}: triggers.{trigger_type} must be a non-empty list"
                    )
            if surface == "public" and invocation == "implicit":
                positives = triggers.get("positive")
                if isinstance(positives, list):
                    for value in positives:
                        if not isinstance(value, str):
                            continue
                        normalized = re.sub(r"\W+", " ", value.casefold()).strip()
                        if normalized:
                            implicit_triggers.setdefault(normalized, []).append(
                                str(label)
                            )

        source_id = skill.get("source")
        relative_path = skill.get("path")
        source = sources.get(source_id)
        skill_dir: Path | None = None
        if not isinstance(source, dict):
            result.errors.append(f"{label}: unknown source {source_id!r}")
        elif not isinstance(relative_path, str):
            result.errors.append(f"{label}: path must be a string")
        else:
            skill_dir = (
                _expand_path(source["root"], registry_path.parent) / relative_path
            )
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.is_file():
                result.errors.append(
                    f"{label}: SKILL.md does not exist at {skill_file}"
                )
            else:
                try:
                    frontmatter = _frontmatter(skill_file)
                    declared_name = frontmatter.get("name")
                    if declared_name != skill_dir.name:
                        result.errors.append(
                            f"{label}: frontmatter name {declared_name!r} "
                            f"does not match directory {skill_dir.name!r}"
                        )
                    expected_key = (
                        f"{source.get('namespace')}:{declared_name}"
                        if source.get("namespace")
                        else declared_name
                    )
                    if label != expected_key:
                        result.errors.append(
                            f"{label}: key does not match source/frontmatter {expected_key!r}"
                        )
                    if not frontmatter.get("description"):
                        result.errors.append(
                            f"{label}: frontmatter description is empty"
                        )
                except Exception as exc:
                    result.errors.append(
                        f"{label}: invalid SKILL.md frontmatter: {exc}"
                    )
            try:
                actual_implicit, policy_source = _openai_implicit_policy(skill_dir)
                desired_implicit = invocation == "implicit"
                if actual_implicit != desired_implicit:
                    message = (
                        f"{label}: openai policy ({policy_source}, "
                        f"implicit={actual_implicit}) conflicts with registry "
                        f"invocation={invocation}"
                    )
                    if (
                        source_id != "local"
                        and exposure.get("migrationState") == "legacy-packaged"
                    ):
                        result.warnings.append(message)
                    else:
                        result.errors.append(message)
            except Exception as exc:
                result.errors.append(f"{label}: invalid agents/openai.yaml: {exc}")
            if discover and (source_id, relative_path) not in discovered_entries:
                result.errors.append(
                    f"{label}: registered path was not discovered in source {source_id}"
                )

        dependencies = skill.get("dependencies")
        capability_dependencies: list[str] = []
        if not isinstance(dependencies, dict):
            result.errors.append(f"{label}: dependencies must be a mapping")
        else:
            capability_dependencies = dependencies.get("capabilities", [])
            component_dependencies = dependencies.get("components", [])
            if not isinstance(capability_dependencies, list):
                result.errors.append(
                    f"{label}: dependencies.capabilities must be a list"
                )
                capability_dependencies = []
            if not isinstance(component_dependencies, list):
                result.errors.append(f"{label}: dependencies.components must be a list")
                component_dependencies = []
            unknown_capabilities = sorted(
                set(capability_dependencies) - known_capabilities - external_ids
            )
            if unknown_capabilities:
                result.errors.append(
                    f"{label}: unknown capability dependencies {unknown_capabilities}"
                )
            unknown_components = sorted(set(component_dependencies) - component_ids)
            if unknown_components:
                result.errors.append(
                    f"{label}: unknown component dependencies {unknown_components}"
                )
            for component_id in component_dependencies:
                component = next(
                    (
                        item
                        for item in component_registry.get("components", [])
                        if item.get("id") == component_id
                    ),
                    None,
                )
                if component and component.get("status") == "planned":
                    result.warnings.append(
                        f"{label}: depends on planned component {component_id}"
                    )
        graph[capability] = [
            dependency
            for dependency in capability_dependencies
            if dependency in known_capabilities
        ]
        _validate_interfaces(label, skill.get("interfaces"), result)
        effects = _validate_authority(
            label, skill.get("authority"), allowed_effects, result
        )
        for domain in _validate_data(label, skill.get("data"), effects, result):
            previous = state_owners.get(domain)
            if previous:
                result.errors.append(
                    f"state owner conflict for {domain!r}: {previous} and {label}"
                )
            else:
                state_owners[domain] = label
        _validate_completion(
            label, skill.get("completion"), known_capabilities, component_ids, result
        )
        _validate_failure_and_telemetry(label, skill, result)
        evals = skill.get("evals")
        if not isinstance(evals, list) or not evals:
            result.errors.append(f"{label}: evals must be a non-empty list")

    cycle = _find_cycle(graph)
    if cycle:
        result.errors.append("skill dependency cycle: " + " -> ".join(cycle))
    for trigger, owners in sorted(implicit_triggers.items()):
        distinct_owners = sorted(set(owners))
        if len(distinct_owners) > 1:
            result.warnings.append(
                "implicit trigger collision advisory "
                f"{trigger!r}: {', '.join(distinct_owners)}"
            )
    if discover:
        registered_pairs = {
            (skill.get("source"), skill.get("path"))
            for skill in skills
            if isinstance(skill, dict)
        }
        for source_and_path in registered_pairs - set(discovered_entries):
            result.errors.append(
                "registered Skill is outside discovered source set: "
                + "/".join(str(part) for part in source_and_path)
            )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Skill Registry v2 contracts")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--components", type=Path, default=DEFAULT_COMPONENTS)
    parser.add_argument(
        "--no-discovery",
        action="store_true",
        help="Skip filesystem completeness checks (intended only for isolated contract tests)",
    )
    args = parser.parse_args(argv)
    result = validate(
        args.registry.resolve(),
        args.components.resolve(),
        discover=not args.no_discovery,
    )
    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    return 0 if result.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
