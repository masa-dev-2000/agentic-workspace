from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from validate_skill_registry import (
    DEFAULT_COMPONENTS,
    DEFAULT_REGISTRY,
    contract_content_digest,
    validate,
)


def make_skill(
    name: str,
    capability: str,
    *,
    source: str = "local",
    invocation: str = "implicit",
    surface: str = "public",
    migration_state: str = "local-source",
) -> dict:
    version = "1.0.0"
    skill = {
        "key": name if source == "local" else f"{source}:{name}",
        "capability": capability,
        "source": source,
        "path": name,
        "exposure": {
            "surface": surface,
            "invocation": invocation,
            "migrationState": migration_state,
        },
        "responsibility": f"Perform the complete governed responsibility for {name}.",
        "nonGoals": ["Do not exceed the declared responsibility."],
        "triggers": {
            "positive": [f"use {name}"],
            "negative": [f"do not use {name}"],
        },
        "dependencies": {"capabilities": [], "components": []},
        "interfaces": {
            "inputs": [{"name": "request", "type": "intent-ref"}],
            "outputs": [{"name": "result", "type": "artifact-ref"}],
        },
        "authority": {"default": "deny", "effects": ["read"]},
        "data": {"reads": [], "writes": [], "ownerOf": []},
        "completion": {"proof": ["observable result"], "verifier": "self"},
        "failure": {
            "maxEquivalentAttempts": 1,
            "retry": "material-change-only",
            "route": "human-decision",
        },
        "telemetry": {
            "enabled": True,
            "lifecycle": True,
            "outcomeEvidence": True,
            "contentPolicy": "metadata-only",
        },
        "evals": ["contract-fixture"],
        "maturity": "beta",
        "version": version,
        "contractFingerprint": f"contract-v2:{capability}@{version}",
        "primaryCategory": "execution-delegation-routing",
        "primaryResponsibility": f"Own the single governed responsibility for {name}.",
        "entrypoint": "adaptive",
        "invocationPolicy": invocation,
        "delegatesTo": [],
        "stateOwner": "none",
        "secondaryCapabilities": [],
    }
    skill["contractContentDigest"] = contract_content_digest(skill)
    return skill


class RegistryFixture:
    def __init__(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.registry_path = self.root / "skill-registry.yaml"
        self.component_path = self.root / "component-registry.yaml"
        self.registry = {
            "schemaVersion": 2,
            "canonical": True,
            "sources": {
                "local": {
                    "root": ".",
                    "namespace": None,
                    "discover": True,
                    "migrationState": "local-source",
                }
            },
            "authorityEffects": [
                "read",
                "workspace-write",
                "state-append",
                "state-mutate",
                "external-propose",
                "external-execute",
                "destructive",
            ],
            "externalCapabilities": [],
            "skills": [],
        }
        self.components = {
            "schemaVersion": 1,
            "canonical": True,
            "authorityEffects": [
                "read",
                "workspace-write",
                "state-append",
                "state-mutate",
                "external-propose",
                "external-execute",
            ],
            "components": [],
        }

    def close(self) -> None:
        self.temp.cleanup()

    def add_source(self, source: str, root: Path, *, namespace: str | None) -> None:
        self.registry["sources"][source] = {
            "root": str(root),
            "namespace": namespace,
            "discover": True,
            "migrationState": "legacy-packaged",
        }

    def add_skill_files(
        self,
        root: Path,
        directory: str,
        *,
        frontmatter_name: str | None = None,
        implicit_policy: bool | None = None,
    ) -> None:
        skill_dir = root / directory
        skill_dir.mkdir(parents=True)
        name = frontmatter_name or directory
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            f"name: {name}\n"
            f"description: Perform {name} when its governed fixture requests it.\n"
            "---\n\n"
            f"# {name}\n",
            encoding="utf-8",
        )
        if implicit_policy is not None:
            agents = skill_dir / "agents"
            agents.mkdir()
            (agents / "openai.yaml").write_text(
                "interface:\n"
                f'  display_name: "{name}"\n'
                "policy:\n"
                f"  allow_implicit_invocation: {str(implicit_policy).lower()}\n",
                encoding="utf-8",
            )

    def write(self) -> None:
        self.registry_path.write_text(
            yaml.safe_dump(self.registry, sort_keys=False), encoding="utf-8"
        )
        self.component_path.write_text(
            yaml.safe_dump(self.components, sort_keys=False), encoding="utf-8"
        )

    def validate(self):
        self.write()
        return validate(self.registry_path, self.component_path)


class SkillRegistryV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = RegistryFixture()

    def tearDown(self) -> None:
        self.fixture.close()

    def add_local(self, name: str, capability: str, **kwargs) -> dict:
        implicit_policy = kwargs.pop("implicit_policy", None)
        frontmatter_name = kwargs.pop("frontmatter_name", None)
        skill = make_skill(name, capability, **kwargs)
        self.fixture.registry["skills"].append(skill)
        self.fixture.add_skill_files(
            self.fixture.root,
            name,
            frontmatter_name=frontmatter_name,
            implicit_policy=implicit_policy,
        )
        return skill

    def test_canonical_registry_is_complete_and_valid(self) -> None:
        result = validate(DEFAULT_REGISTRY, DEFAULT_COMPONENTS)
        self.assertTrue(result.valid, result.errors)
        self.assertEqual(result.skills, 58)
        self.assertEqual(result.discovered, 58)
        self.assertEqual(result.components, 29)

    def test_unregistered_discovered_skill_is_an_error(self) -> None:
        self.add_local("registered", "fixture.registered")
        self.fixture.add_skill_files(self.fixture.root, "unregistered")
        result = self.fixture.validate()
        self.assertIn(
            "unregistered discovered skill: unregistered",
            result.errors,
        )

    def test_invalid_semver_and_fingerprint_are_errors(self) -> None:
        skill = self.add_local("bad-version", "fixture.bad-version")
        skill["version"] = "latest"
        result = self.fixture.validate()
        self.assertTrue(any("invalid SemVer" in error for error in result.errors))
        self.assertTrue(
            any("contractFingerprint must be" in error for error in result.errors)
        )

    def test_contract_content_change_requires_a_new_digest(self) -> None:
        skill = self.add_local("contract-change", "fixture.contract-change")
        original = skill["contractContentDigest"]
        skill["authority"]["effects"] = ["read", "state-append"]

        result = self.fixture.validate()

        self.assertNotEqual(original, contract_content_digest(skill))
        self.assertTrue(
            any("contractContentDigest must be" in error for error in result.errors)
        )

    def test_validation_can_bind_to_supplied_immutable_snapshots(self) -> None:
        self.add_local("snapshot", "fixture.snapshot")
        self.fixture.write()
        registry_snapshot = self.fixture.registry_path.read_bytes()
        component_snapshot = self.fixture.component_path.read_bytes()
        self.fixture.registry_path.write_text("skills: invalid\n", encoding="utf-8")

        result = validate(
            self.fixture.registry_path,
            self.fixture.component_path,
            registry_snapshot=registry_snapshot,
            component_snapshot=component_snapshot,
        )

        self.assertTrue(result.valid, result.errors)

    def test_internal_skill_cannot_be_implicit(self) -> None:
        self.add_local(
            "internal-worker",
            "fixture.internal",
            surface="internal",
            invocation="implicit",
        )
        result = self.fixture.validate()
        self.assertTrue(
            any(
                "internal Skill may not be implicitly invoked" in error
                for error in result.errors
            )
        )

    def test_duplicate_capability_is_an_error(self) -> None:
        self.add_local("first", "fixture.same")
        self.add_local("second", "fixture.same")
        result = self.fixture.validate()
        self.assertIn("skill-registry: duplicate capability id", result.errors)

    def test_capability_dependency_cycle_is_an_error(self) -> None:
        first = self.add_local("first", "fixture.first")
        second = self.add_local("second", "fixture.second")
        first["dependencies"]["capabilities"] = ["fixture.second"]
        second["dependencies"]["capabilities"] = ["fixture.first"]
        result = self.fixture.validate()
        self.assertTrue(
            any("skill dependency cycle:" in error for error in result.errors)
        )

    def test_duplicate_state_owner_is_an_error(self) -> None:
        first = self.add_local("first", "fixture.first")
        second = self.add_local("second", "fixture.second")
        for skill in (first, second):
            skill["authority"]["effects"] = ["read", "state-mutate"]
            skill["data"] = {
                "reads": ["shared-state"],
                "writes": [{"domain": "shared-state", "mode": "mutate"}],
                "ownerOf": ["shared-state"],
            }
        result = self.fixture.validate()
        self.assertTrue(
            any(
                "state owner conflict for 'shared-state'" in error
                for error in result.errors
            )
        )

    def test_write_mode_requires_matching_authority_effect(self) -> None:
        skill = self.add_local("unsafe-writer", "fixture.unsafe-writer")
        skill["data"]["writes"] = [{"domain": "state", "mode": "append"}]
        skill["contractContentDigest"] = contract_content_digest(skill)

        result = self.fixture.validate()

        self.assertTrue(
            any(
                "requires authority effect 'state-append'" in error
                for error in result.errors
            )
        )

    def test_component_write_mode_requires_matching_authority_effect(self) -> None:
        self.fixture.components["components"].append(
            {
                "id": "unsafe.component",
                "kind": "store",
                "status": "planned",
                "responsibility": "Persist governed fixture state for validation testing.",
                "implementation": None,
                "dependencies": [],
                "authority": {"default": "deny", "effects": ["read"]},
                "data": {
                    "reads": [],
                    "writes": [{"domain": "state", "mode": "mutate"}],
                },
                "version": "1.0.0",
            }
        )

        result = self.fixture.validate()

        self.assertTrue(
            any(
                "requires authority effect 'state-mutate'" in error
                for error in result.errors
            )
        )

    def test_completion_contract_rejects_vacuous_proof_and_unknown_verifier(
        self,
    ) -> None:
        skill = self.add_local("weak-proof", "fixture.weak-proof")
        skill["completion"] = {"proof": [None], "verifier": "humna"}
        skill["contractContentDigest"] = contract_content_digest(skill)

        result = self.fixture.validate()

        self.assertTrue(
            any(
                "proof entries must be non-empty strings" in error
                for error in result.errors
            )
        )
        self.assertTrue(
            any("invalid completion verifier" in error for error in result.errors)
        )

    def test_exact_implicit_trigger_collision_is_advisory(self) -> None:
        first = self.add_local("first-trigger", "fixture.first-trigger")
        second = self.add_local("second-trigger", "fixture.second-trigger")
        second["triggers"]["positive"] = first["triggers"]["positive"].copy()
        second["contractContentDigest"] = contract_content_digest(second)

        result = self.fixture.validate()

        self.assertTrue(result.valid, result.errors)
        self.assertTrue(
            any(
                "implicit trigger collision advisory" in warning
                for warning in result.warnings
            )
        )

    def test_unknown_component_reference_is_an_error(self) -> None:
        skill = self.add_local("component-user", "fixture.component-user")
        skill["dependencies"]["components"] = ["missing.component"]
        result = self.fixture.validate()
        self.assertTrue(
            any("unknown component dependencies" in error for error in result.errors)
        )

    def test_frontmatter_name_must_match_directory_and_key(self) -> None:
        self.add_local(
            "directory-name",
            "fixture.frontmatter",
            frontmatter_name="other-name",
        )
        result = self.fixture.validate()
        self.assertTrue(
            any("frontmatter name 'other-name'" in error for error in result.errors)
        )

    def test_local_explicit_skill_requires_explicit_false_policy(self) -> None:
        self.add_local(
            "explicit-worker",
            "fixture.explicit",
            invocation="explicit-only",
        )
        result = self.fixture.validate()
        self.assertTrue(
            any(
                "conflicts with registry invocation=explicit-only" in error
                for error in result.errors
            )
        )

    def test_legacy_packaged_policy_gap_is_warning(self) -> None:
        plugin_root = self.fixture.root / "plugin-skills"
        plugin_root.mkdir()
        self.fixture.add_source("legacy", plugin_root, namespace="legacy")
        self.fixture.add_skill_files(plugin_root, "worker")
        self.fixture.registry["skills"].append(
            make_skill(
                "worker",
                "fixture.legacy-worker",
                source="legacy",
                surface="internal",
                invocation="orchestrator-only",
                migration_state="legacy-packaged",
            )
        )
        result = self.fixture.validate()
        self.assertTrue(result.valid, result.errors)
        self.assertTrue(
            any(
                "legacy:worker: openai policy" in warning for warning in result.warnings
            )
        )


if __name__ == "__main__":
    unittest.main()
