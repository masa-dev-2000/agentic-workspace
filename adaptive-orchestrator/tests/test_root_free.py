import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import agent_router as R
import orchestrate as O
import root_free as F


class RootFreeTests(unittest.TestCase):
    def test_default_is_backward_compatible(self):
        result = O.plan({"goal": "x", "tasks": [{"title": "read"}]})
        self.assertFalse(result["root_free_mode"])
        self.assertEqual(result["execution_mode"], "direct")

    def test_root_free_routes_and_orders_dag(self):
        result = O.plan({"goal": "x", "root_free_mode": True, "tasks": [
            {"task_id": "research", "objective": "research", "task_type": "research", "required_capabilities": ["research"], "required_authority": ["read"]},
            {"task_id": "doc", "objective": "document", "task_type": "documentation", "required_capabilities": ["document"], "required_authority": ["read"], "dependencies": ["research"]},
        ]})
        self.assertEqual(result["execution_mode"], "root_free_mode")
        self.assertEqual(result["routing"]["execution_order"], ["research", "doc"])
        self.assertEqual(result["routing"]["routes"][0]["agent_id"], "researcher")
        self.assertEqual(result["routing"]["routes"][1]["agent_id"], "documenter")

    def test_independent_tasks_are_parallelizable(self):
        result = R.route_tasks([
            {"task_id": "a", "task_type": "research", "required_capabilities": ["research"]},
            {"task_id": "b", "task_type": "test", "required_capabilities": ["test"]},
        ])
        self.assertEqual(result["parallel_groups"], [["a", "b"]])

    def test_unknown_registry_and_cycle_fail_closed(self):
        with self.assertRaises(R.RoutingError):
            O.plan({"goal": "x", "root_free_mode": True, "tasks": [{"task_id": "a", "objective": "research", "task_type": "research", "preferred_agent": "missing", "required_capabilities": ["research"]}]})
        with self.assertRaises(R.RoutingError):
            R.route_tasks([{"task_id": "a", "task_type": "research", "required_capabilities": ["research"], "dependencies": ["b"]}, {"task_id": "b", "task_type": "research", "required_capabilities": ["research"], "dependencies": ["a"]}])

    def test_root_return_conditions_and_approval_separation(self):
        self.assertEqual(F.evaluate({"status": "running"})["root_return"], False)
        approval = F.evaluate({"status": "running", "side_effect_class": "external-send"})
        self.assertEqual(approval["state"], "BLOCKED_APPROVAL")
        self.assertEqual(approval["approval_status"], "approval_requested")
        self.assertEqual(approval["next_action"], "return_root")
        self.assertTrue(approval["human_approval"])
        self.assertEqual(F.evaluate({"status": "completed", "verification_match": True})["reason"], "completed_for_integration")
        self.assertEqual(F.evaluate({"status": "failed", "failure_class": "transient", "attempt": 2, "max_attempts": 2})["state"], "FAILED_RETRY_EXHAUSTED")

    def test_event_is_body_free_with_routing_metadata(self):
        result = O.event({"job_id": "j", "agent_id": "researcher", "authority": ["read"], "route_reason": "capability_authority_risk_match", "root_return_state": "WAITING_CHILDREN", "prompt": "secret"})
        self.assertNotIn("prompt", result)
        self.assertEqual(result["agent_id"], "researcher")
        self.assertTrue(result["body_free"])


if __name__ == "__main__":
    unittest.main()
