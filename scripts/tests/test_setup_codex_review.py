from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "skills"
    / ".system"
    / "setup-codex-review"
    / "scripts"
    / "setup_codex_review.py"
)
SPEC = importlib.util.spec_from_file_location("setup_codex_review", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class SetupCodexReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp_dir.name) / "repo"
        self.repo.mkdir()
        subprocess.run(
            ["git", "init", "-b", "main", str(self.repo)],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "-C", str(self.repo), "config", "user.email", "test@example.com"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.repo), "config", "user.name", "Test"],
            check=True,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def commit(self) -> None:
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(self.repo), "commit", "-m", "fixture"],
            check=True,
            capture_output=True,
            text=True,
        )

    def config(self) -> Path:
        path = Path(self.temp_dir.name) / "config.json"
        path.write_text(
            json.dumps(
                {
                    "validation_commands": ["pnpm lint", "pnpm test"],
                    "review_rules": [
                        {
                            "title": "API compatibility",
                            "text": "Flag changes that break public behavior without a migration path.",
                        },
                        {
                            "title": "Data safety",
                            "text": "Flag destructive writes that lack bounded rollback evidence.",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_scan_infers_package_commands(self) -> None:
        (self.repo / "package.json").write_text(
            json.dumps(
                {
                    "scripts": {
                        "lint": "eslint .",
                        "test": "vitest run",
                        "build": "vite build",
                    }
                }
            ),
            encoding="utf-8",
        )
        (self.repo / "pnpm-lock.yaml").write_text("", encoding="utf-8")
        self.commit()

        result = MODULE.scan(self.repo)

        self.assertEqual(
            result["candidate_validation_commands"],
            ["pnpm lint", "pnpm test", "pnpm build"],
        )
        self.assertTrue(result["clean_worktree"])
        self.assertFalse(result["template_conflict"])

    def test_apply_is_idempotent(self) -> None:
        (self.repo / "README.md").write_text("# Demo\n", encoding="utf-8")
        self.commit()
        config = self.config()

        first = MODULE.apply(self.repo, config, write=True)
        second = MODULE.check(self.repo, config)

        self.assertEqual(
            first["changed"], ["AGENTS.md", ".github/pull_request_template.md"]
        )
        self.assertTrue(second["valid"])
        self.assertIn("## Code Review Rules", (self.repo / "AGENTS.md").read_text())
        self.assertIn(
            "@codex review",
            (self.repo / ".github/pull_request_template.md").read_text(),
        )

    def test_existing_content_is_preserved_byte_for_byte(self) -> None:
        existing_agents = "# Existing rules\n\nKeep this sentence.  \n\n\n"
        existing_template = "# Existing template\n\n<!-- keep -->  \n"
        (self.repo / "AGENTS.md").write_text(existing_agents, encoding="utf-8")
        template = self.repo / ".github" / "pull_request_template.md"
        template.parent.mkdir(parents=True)
        template.write_text(existing_template, encoding="utf-8")
        self.commit()

        MODULE.apply(self.repo, self.config(), write=True)

        self.assertTrue(
            (self.repo / "AGENTS.md").read_text(encoding="utf-8").startswith(
                existing_agents
            )
        )
        self.assertTrue(
            template.read_text(encoding="utf-8").startswith(existing_template)
        )

    def test_unmanaged_review_rules_fail_closed(self) -> None:
        (self.repo / "AGENTS.md").write_text(
            "# Instructions\n\n## Code Review Rules\n\n- Existing rule.\n",
            encoding="utf-8",
        )
        self.commit()

        with self.assertRaises(MODULE.SetupError):
            MODULE.apply(self.repo, self.config(), write=True)

    def test_multiple_template_files_are_reported_and_fail_closed(self) -> None:
        first = self.repo / ".github" / "PULL_REQUEST_TEMPLATE" / "feature.md"
        second = self.repo / ".github" / "PULL_REQUEST_TEMPLATE" / "bug.md"
        first.parent.mkdir(parents=True)
        first.write_text("# Feature\n", encoding="utf-8")
        second.write_text("# Bug\n", encoding="utf-8")
        self.commit()

        scan = MODULE.scan(self.repo)

        self.assertTrue(scan["template_conflict"])
        self.assertEqual(
            scan["pull_request_templates"],
            [
                ".github/PULL_REQUEST_TEMPLATE/bug.md",
                ".github/PULL_REQUEST_TEMPLATE/feature.md",
            ],
        )
        with self.assertRaises(MODULE.SetupError):
            MODULE.apply(self.repo, self.config(), write=True)

    def test_duplicate_managed_blocks_fail_closed(self) -> None:
        block = (
            f"{MODULE.AGENTS_START}\nmanaged\n{MODULE.AGENTS_END}\n"
        )
        (self.repo / "AGENTS.md").write_text(block + block, encoding="utf-8")
        self.commit()

        with self.assertRaises(MODULE.SetupError):
            MODULE.apply(self.repo, self.config(), write=True)


if __name__ == "__main__":
    unittest.main()
