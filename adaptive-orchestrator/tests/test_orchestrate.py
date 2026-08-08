import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("orchestrate", ROOT / "scripts" / "orchestrate.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class OrchestratorTests(unittest.TestCase):
    def test_safe_action_is_allowed(self):
        result = MODULE.policy({"actions": [{"action_id": "write-local", "side_effect_classes": ["local-reversible"]}]})
        self.assertEqual(result["decision"], "allow")

    def test_fatal_action_requires_approval(self):
        result = MODULE.policy({"actions": [{"action_id": "delete-prod", "side_effect_classes": ["destructive-delete"]}]})
        self.assertEqual(result["decision"], "require_approval")
        self.assertEqual(result["fatal_actions"][0]["action_id"], "delete-prod")

    def test_unknown_action_requires_approval(self):
        result = MODULE.policy({"actions": [{"action_id": "unclear", "side_effect_classes": []}]})
        self.assertEqual(result["decision"], "require_approval")
        self.assertEqual(result["unknown_actions"], ["unclear"])

    def test_plan_adds_execution_contract(self):
        result = MODULE.plan({"goal": "build", "tasks": [{"title": "implement"}]})
        task = result["tasks"][0]
        self.assertEqual(result["schema"], "orchestration_plan_v1")
        self.assertTrue(result["plan_digest"])
        self.assertTrue(task["task_id"])
        self.assertIn("write_scope", task)
        self.assertIn("acceptance_criteria", task)
        self.assertIn("side_effect_classes", task)
        self.assertIn("objective", task)
        self.assertIn("role", task)
        self.assertIn("model_provider", task)
        self.assertIn("verification", task)
        self.assertIn("fallback", task)

    def test_event_is_body_free(self):
        result = MODULE.event({
            "job_id": "job-1",
            "prompt": "must not persist",
            "response": "must not persist",
            "tool_output": "must not persist",
            "total_tokens": 12,
        })
        self.assertTrue(result["body_free"])
        self.assertEqual(result["total_tokens"], 12)
        self.assertNotIn("prompt", result)
        self.assertNotIn("response", result)
        self.assertNotIn("tool_output", result)

    def test_entry_shadow_routes_nontrivial_work_to_adaptive(self):
        result = MODULE.entry({"request": {"nontrivial": True}})
        self.assertEqual(result["schema"], "skill_entry_rollout_v1")
        self.assertEqual(result["entrypoint"], "adaptive")
        self.assertEqual(result["skill"], "adaptive-orchestrator")
        self.assertTrue(result["shadow"])
        self.assertTrue(result["legacy_execution_unchanged"])

    def test_entry_explicit_request_wins_over_project(self):
        result = MODULE.entry({"request": {"explicit_skill": "gan", "project_id": "p1"}})
        self.assertEqual((result["entrypoint"], result["skill"]), ("explicit", "gan"))


if __name__ == "__main__":
    unittest.main()
