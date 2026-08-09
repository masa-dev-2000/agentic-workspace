import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("skill_entry_router", ROOT / "scripts" / "skill_entry_router.py")
router = importlib.util.module_from_spec(spec)
assert spec.loader
sys.modules[spec.name] = router
spec.loader.exec_module(router)


class SkillEntryRouterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = router.load_registry()

    def test_registry_routes_are_acyclic(self):
        router.validate_routes(self.registry)

    def test_explicit_skill_wins(self):
        route = router.route_request({"explicit_skill": "gan", "project_id": "p1"}, self.registry)
        self.assertEqual((route.entrypoint, route.skill), ("explicit", "gan"))

    def test_project_routes_to_project_orchestrator(self):
        route = router.route_request({"project_id": "p1", "nontrivial": True}, self.registry)
        self.assertEqual((route.entrypoint, route.skill), ("project", "ai-project-manager:project-orchestrator"))

    def test_human_authority_routes_to_human_queue(self):
        route = router.route_request({"approval_required": True}, self.registry)
        self.assertEqual((route.entrypoint, route.skill), ("human", "ai-project-manager:human-task-requester"))

    def test_plan_and_review_have_dedicated_entries(self):
        self.assertEqual(router.route_request({"plan_only": True}, self.registry).skill, "planning")
        self.assertEqual(router.route_request({"review_only": True}, self.registry).skill, "gan")

    def test_unknown_explicit_skill_fails_closed(self):
        with self.assertRaises(router.EntryRoutingError):
            router.route_request({"explicit_skill": "missing"}, self.registry)

    def test_nontrivial_defaults_to_adaptive(self):
        route = router.route_request({"nontrivial": True}, self.registry)
        self.assertEqual((route.entrypoint, route.skill), ("adaptive", "adaptive-orchestrator"))


if __name__ == "__main__":
    unittest.main()
