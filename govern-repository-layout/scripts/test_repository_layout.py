from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

import repository_layout as rl


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


class RepositoryLayoutTests(unittest.TestCase):
    def make_repo(self, root: Path) -> Path:
        repo = root / "repo"
        repo.mkdir()
        git(repo, "init", "-q")
        git(repo, "config", "user.email", "test@example.invalid")
        git(repo, "config", "user.name", "Layout Test")
        (repo / "README.md").write_text("# test\n", encoding="utf-8")
        git(repo, "add", "README.md")
        git(repo, "commit", "-qm", "fixture")
        return repo

    def test_normal_repo_is_detected_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.make_repo(Path(tmp))
            before = hashlib.sha256((repo / "README.md").read_bytes()).hexdigest()
            report = rl.audit(repo)
            after = hashlib.sha256((repo / "README.md").read_bytes()).hexdigest()
            self.assertEqual(report["repo_root"], repo.resolve().as_posix())
            self.assertEqual(report["root_kind"], "git-repository")
            self.assertEqual(report["write_count"], 0)
            self.assertEqual(before, after)

    def test_outer_workspace_resolves_single_nested_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            repo = self.make_repo(workspace)
            report = rl.audit(workspace)
            self.assertEqual(report["repo_root"], repo.resolve().as_posix())
            self.assertEqual(report["root_kind"], "workspace")
            self.assertIn("OUTER_WORKSPACE", {v["code"] for v in report["violations"]})

    def test_deprecated_path_and_fixed_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.make_repo(Path(tmp))
            (repo / "output").mkdir()
            (repo / "outputs").mkdir()
            (repo / "README.md").write_text("See outputs/result.json\n", encoding="utf-8")
            git(repo, "add", "README.md")
            manifest = {
                "deprecated_paths": {
                    "outputs": {"replacement": "output", "policy": "no-new-writes"}
                }
            }
            report = rl.audit(repo, manifest)
            self.assertEqual(len(report["fixed_references"]), 1)
            self.assertIn("DEPRECATED_PATH_PRESENT", {v["code"] for v in report["violations"]})

    def test_protected_external_is_never_migration_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            repo = self.make_repo(workspace)
            source = workspace / "client-source"
            source.mkdir()
            manifest = {
                "protected_external": [{"path": "../client-source", "access": "read-only"}],
                "deprecated_paths": {},
            }
            report = rl.audit(repo, manifest)
            self.assertTrue(report["protected_external"][0]["resolved_outside_repo"])
            plan = rl.migration_plan(report, manifest)
            self.assertIn("never auto-move", plan)

    def test_baseline_suppresses_existing_but_not_new_violation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.make_repo(Path(tmp))
            (repo / "output").mkdir()
            (repo / "outputs").mkdir()
            manifest_path = repo / "policy.yaml"
            manifest = {
                "deprecated_paths": {
                    "outputs": {"replacement": "output", "policy": "no-new-writes"}
                },
                "constraints": {"new_violation_policy": "error"},
            }
            manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
            first = rl.audit(repo, manifest)
            baseline = repo / "baseline.json"
            baseline.write_text(json.dumps({
                "fingerprints": [v["fingerprint"] for v in first["violations"]]
            }), encoding="utf-8")
            result, status = rl.check(repo, manifest_path, baseline)
            self.assertEqual(status, 0)
            self.assertTrue(result["valid"])
            manifest["protected_external"] = [{"path": "../missing", "access": "read-only"}]
            manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
            result, status = rl.check(repo, manifest_path, baseline)
            self.assertEqual(status, 2)
            self.assertFalse(result["valid"])


if __name__ == "__main__":
    unittest.main()
