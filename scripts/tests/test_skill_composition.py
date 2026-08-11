"""Unit tests for check_skill_composition() in scripts/validate_workspace.py.

Run: python -X utf8 -m unittest discover -s scripts/tests -v

Each rule gets a pass case and a fail case. The module keeps findings in module
globals, so every test builds a throwaway skills tree, repoints the module at
it, and resets the finding lists.
"""
from __future__ import annotations

import importlib.util
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parent.parent / "validate_workspace.py"
spec = importlib.util.spec_from_file_location("validate_workspace", SCRIPT)
vw = importlib.util.module_from_spec(spec)
sys.modules["validate_workspace"] = vw
spec.loader.exec_module(vw)

FRONTMATTER = "---\nname: {name}\ndescription: d\n---\n"

REPO = SCRIPT.resolve().parent.parent
_skills_before: set[str] = set()


def setUpModule() -> None:
    # skills/ is the junction source for ~/.codex/skills, ~/.agents/skills and
    # (via symlink) ~/.claude/skills, and .githooks/pre-push runs this suite.
    # A fixture that leaks a directory here would land in the machine's live
    # agent configuration, so the whole tree is snapshotted, not a name prefix.
    _skills_before.update(p.name for p in (REPO / "skills").iterdir())


def tearDownModule() -> None:
    after = {p.name for p in (REPO / "skills").iterdir()}
    if after != _skills_before:
        raise AssertionError(
            f"tests changed the live skills tree: added {sorted(after - _skills_before)}, "
            f"removed {sorted(_skills_before - after)}"
        )


class CompositionCase(unittest.TestCase):
    def run_check(self, body: str, *, name: str = "sample", acks: dict | None = None):
        """Write one skill with `body`, run the check, return (errors, warnings)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill = root / "skills" / name
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                FRONTMATTER.format(name=name) + body, encoding="utf-8"
            )
            (root / "config").mkdir()
            if acks is not None:
                (root / "config" / "skill-composition-acknowledged.json").write_text(
                    json.dumps({"acknowledged": acks}), encoding="utf-8"
                )
            old_root, old_ack = vw.ROOT, vw.COMPOSITION_ACK_PATH
            vw.ROOT = root
            vw.COMPOSITION_ACK_PATH = root / "config" / "skill-composition-acknowledged.json"
            vw.errors.clear()
            vw.warnings.clear()
            try:
                vw.check_skill_composition()
                return list(vw.errors), list(vw.warnings)
            finally:
                vw.ROOT, vw.COMPOSITION_ACK_PATH = old_root, old_ack
                vw.errors.clear()
                vw.warnings.clear()

    def assertClean(self, result):
        errors, warns = result
        self.assertEqual(([], []), (errors, warns))


class TestBodyLength(CompositionCase):
    def test_under_limit_passes(self):
        self.assertClean(self.run_check("x\n" * (vw.SKILL_BODY_MAX_LINES - 10)))

    def test_over_limit_fails(self):
        errors, warns = self.run_check("x\n" * (vw.SKILL_BODY_MAX_LINES + 10))
        self.assertEqual(1, len(errors))
        self.assertIn("not under the 500-line limit", errors[0])
        self.assertEqual([], warns)

    def test_frontmatter_is_not_counted_and_500_is_not_under_500(self):
        # Exactly 500 body lines is not "under 500", and the 4 frontmatter
        # lines must not be what pushes a 497-line body over.
        self.assertClean(self.run_check("x\n" * (vw.SKILL_BODY_MAX_LINES - 1)))
        errors, _ = self.run_check("x\n" * vw.SKILL_BODY_MAX_LINES)
        self.assertEqual(1, len(errors))
        self.assertIn("body is 500 lines", errors[0])


class TestAbsolutePath(CompositionCase):
    def test_relative_path_passes(self):
        self.assertClean(self.run_check("See `skills/foo/scripts/run.py` and ~/.claude.\n"))

    def test_absolute_user_path_fails(self):
        errors, _ = self.run_check("Run `C:\\Users\\masa\\dev\\thing.py`.\n")
        self.assertEqual(1, len(errors))
        self.assertIn("machine-specific user path at line(s) 5", errors[0])

    def test_forward_slash_variant_also_fails(self):
        errors, _ = self.run_check("Path: D:/Users/someone/x\n")
        self.assertEqual(1, len(errors))


class TestInlineSchema(CompositionCase):
    def _yaml_block(self, n: int) -> str:
        return "```yaml\n" + "k: v\n" * n + "```\n"

    def test_short_schema_snippet_passes(self):
        self.assertClean(self.run_check(self._yaml_block(vw.SKILL_INLINE_SCHEMA_MAX_LINES)))

    def test_long_schema_block_fails(self):
        errors, _ = self.run_check(self._yaml_block(vw.SKILL_INLINE_SCHEMA_MAX_LINES + 1))
        self.assertEqual(1, len(errors))
        self.assertIn("block(s) of 21 lines", errors[0])

    def test_crlf_file_is_still_parsed(self):
        body = "```yaml\n" + "k: v\n" * (vw.SKILL_INLINE_SCHEMA_MAX_LINES + 1) + "```\n"
        errors, _ = self.run_check(body.replace("\n", "\r\n"))
        self.assertEqual(1, len(errors))

    def test_tilde_fence_is_not_an_escape_hatch(self):
        errors, _ = self.run_check(
            "~~~yaml\n" + "k: v\n" * (vw.SKILL_INLINE_SCHEMA_MAX_LINES + 1) + "~~~\n"
        )
        self.assertEqual(1, len(errors))

    def test_language_tag_with_attributes_still_counts(self):
        errors, _ = self.run_check(
            '```json title="contract"\n'
            + "{}\n" * (vw.SKILL_INLINE_SCHEMA_MAX_LINES + 1)
            + "```\n"
        )
        self.assertEqual(1, len(errors))

    def test_long_command_block_is_not_a_schema(self):
        self.assertClean(self.run_check("```bash\n" + "echo hi\n" * 40 + "```\n"))


class TestCommandBlocks(CompositionCase):
    def _blocks(self, n: int) -> str:
        return "```bash\necho hi\n```\n" * n

    def test_at_limit_passes(self):
        self.assertClean(self.run_check(self._blocks(vw.SKILL_COMMAND_BLOCK_MAX)))

    def test_over_limit_is_a_warning_not_an_error(self):
        errors, warns = self.run_check(self._blocks(vw.SKILL_COMMAND_BLOCK_MAX + 1))
        self.assertEqual([], errors)
        self.assertEqual(1, len(warns))
        self.assertIn("holds 11 command blocks", warns[0])


class TestFenceParsing(CompositionCase):
    def test_indented_fence_is_still_a_fence(self):
        errors, _ = self.run_check(
            "- step:\n"
            + "   ```yaml\n"
            + "   k: v\n" * (vw.SKILL_INLINE_SCHEMA_MAX_LINES + 1)
            + "   ```\n"
        )
        self.assertEqual(1, len(errors), errors)
        self.assertIn("inlines", errors[0])

    def test_four_backtick_wrapper_does_not_desync_fence_pairing(self):
        inner = "```yaml\n" + "k: v\n" * (vw.SKILL_INLINE_SCHEMA_MAX_LINES + 1) + "```\n"
        blocks = vw.fenced_blocks("````markdown\n" + inner + "````\n")
        # One block, tagged markdown, with the nested fence intact in its body —
        # not two mis-paired blocks with the yaml tag lost.
        self.assertEqual([("markdown", inner)], blocks)


class TestAcknowledgements(CompositionCase):
    ACK = {"sample": {"body-length": {"issue": "#17", "reason": "tracked"}}}

    def test_malformed_ack_entry_reports_error_not_traceback(self):
        errors, _ = self.run_check(
            "x\n" * (vw.SKILL_BODY_MAX_LINES + 10),
            acks={"sample": {"body-length": "#17"}},  # shorthand typo
        )
        self.assertIn("entry must be an object", " ".join(errors))
        self.assertIn("not under the 500-line limit", " ".join(errors))

    def test_acknowledged_violation_is_silent(self):
        self.assertClean(
            self.run_check("x\n" * (vw.SKILL_BODY_MAX_LINES + 10), acks=self.ACK)
        )

    def test_acknowledgement_does_not_cover_other_rules(self):
        errors, _ = self.run_check(
            "x\n" * (vw.SKILL_BODY_MAX_LINES + 10) + "C:\\Users\\masa\\x\n", acks=self.ACK
        )
        self.assertEqual(1, len(errors))
        self.assertIn("machine-specific user path", errors[0])

    def test_stale_acknowledgement_fails(self):
        errors, _ = self.run_check("short body\n", acks=self.ACK)
        self.assertEqual(1, len(errors))
        self.assertIn("no longer violates the rule", errors[0])

    def test_acknowledgement_without_issue_fails(self):
        errors, _ = self.run_check(
            "x\n" * (vw.SKILL_BODY_MAX_LINES + 10),
            acks={"sample": {"body-length": {"reason": "because"}}},
        )
        self.assertIn("must name the tracking issue", " ".join(errors))

    def test_non_object_toplevel_reports_error_not_traceback(self):
        for raw in ("[]", "null", '"hi"'):
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "config").mkdir()
                (root / "config" / "skill-composition-acknowledged.json").write_text(
                    raw, encoding="utf-8"
                )
                old_root, old_ack = vw.ROOT, vw.COMPOSITION_ACK_PATH
                vw.ROOT = root
                vw.COMPOSITION_ACK_PATH = (
                    root / "config" / "skill-composition-acknowledged.json"
                )
                vw.errors.clear()
                try:
                    self.assertEqual({}, vw.load_composition_acks())
                    self.assertIn("top-level value must be an object",
                                  " ".join(vw.errors))
                finally:
                    vw.ROOT, vw.COMPOSITION_ACK_PATH = old_root, old_ack
                    vw.errors.clear()

    def test_unknown_rule_name_fails(self):
        errors, _ = self.run_check(
            "short\n", acks={"sample": {"be-nice": {"issue": "#1", "reason": "r"}}}
        )
        self.assertIn("unknown rule 'be-nice'", " ".join(errors))


class TestExitCode(unittest.TestCase):
    """End-to-end: the workspace as committed must be green, and an
    unacknowledged warning must not be able to hide behind exit 0 — every
    consumer of this script (pre-push hook, CI, health_check.py) reads only the
    exit code."""

    def validate(self):
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", str(SCRIPT), "--no-live"],
            cwd=REPO, capture_output=True, text=True, encoding="utf-8",
        )
        return proc.returncode, proc.stdout

    def test_committed_workspace_is_green(self):
        rc, out = self.validate()
        self.assertEqual(0, rc, out)
        self.assertNotIn("WARN:", out)

    def test_main_runs_the_composition_check(self):
        """Guards the wiring, not the rule: with every other check stubbed and
        ROOT pointed at a tree holding one over-limit skill, main() must still
        fail. Without this, deleting the call in main() keeps the suite green."""
        stubs = {name: (lambda *a, **k: None) for name in dir(vw)
                 if name.startswith("check_") and name != "check_skill_composition"}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill = root / "skills" / "sample"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                FRONTMATTER.format(name="sample") + "x\n" * (vw.SKILL_BODY_MAX_LINES + 5),
                encoding="utf-8",
            )
            (root / "config").mkdir()
            old_root, old_ack = vw.ROOT, vw.COMPOSITION_ACK_PATH
            vw.ROOT = root
            vw.COMPOSITION_ACK_PATH = root / "config" / "skill-composition-acknowledged.json"
            out = io.StringIO()
            vw.errors.clear()
            vw.warnings.clear()
            try:
                with mock.patch.object(sys, "argv", ["validate_workspace.py", "--no-live"]), \
                        mock.patch.multiple(vw, **stubs), \
                        contextlib.redirect_stdout(out):
                    rc = vw.main()
                self.assertEqual(1, rc)
                self.assertIn("not under the 500-line limit", out.getvalue())
            finally:
                vw.ROOT, vw.COMPOSITION_ACK_PATH = old_root, old_ack
                vw.errors.clear()
                vw.warnings.clear()

    def test_unacknowledged_warning_makes_exit_nonzero(self):
        """The check functions are stubbed out and one warning is injected, so
        this exercises main()'s exit decision without writing anything into the
        repo — skills/ here is the junction source for the live agent skill
        directories, and a test that gates `git push` must not be able to leave
        a stray skill in the machine's agent configuration."""
        stubs = {name: (lambda *a, **k: None)
                 for name in dir(vw) if name.startswith("check_")}
        with mock.patch.object(sys, "argv", ["validate_workspace.py", "--no-live"]), \
                mock.patch.multiple(vw, **stubs), \
                contextlib.redirect_stdout(io.StringIO()):
            vw.errors.clear()
            vw.warnings.clear()
            vw.warn("skills/probe: SKILL.md holds 11 command blocks (over 10)")
            try:
                self.assertEqual(1, vw.main())
            finally:
                vw.errors.clear()
                vw.warnings.clear()


if __name__ == "__main__":
    unittest.main()
