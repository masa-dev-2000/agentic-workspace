import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from agent_router import RoutingError, route_task, route_tasks
from model_catalog import ModelResolutionError
from runtime_dispatcher import DispatchEnvelope, Dispatcher, MultiAgentBackendAdapter


class Backend:
    def __init__(self):
        self.requests = []

    def start(self, request):
        self.requests.append(request)
        status = "failed" if len(self.requests) == 1 else "succeeded"
        return DispatchEnvelope(f"d{len(self.requests)}", request["run_id"], request["node_id"], request["attempt"], status, status == "failed", False, "ok" if status == "succeeded" else None, "transient" if status == "failed" else None, "evidence://opaque/routing")

    def wait(self, dispatch_id):
        return DispatchEnvelope(dispatch_id, "r", "t", 1, "succeeded", False, False, "ok", None, "evidence://opaque/routing")


class GlobalRoutingTests(unittest.TestCase):
    def test_route_resolves_logical_model_to_runtime_model(self):
        result = route_task({"task_id": "t", "task_type": "implementation", "required_capabilities": ["implement"], "required_authority": ["read"]})
        self.assertEqual(result["model_key"], "coding-default")
        self.assertEqual(result["runtime_model_id"], "gpt-5.4")
        self.assertIn("fallback_routes", route_tasks([{**result, "task_id": "t"}])["routes"][0])

    def test_unknown_model_fails_before_backend(self):
        registry = {"schema": "agent_registry_v1", "revision": "r", "agents": [{"id": "x", "role": "x", "model": "missing", "capabilities": ["read"], "authority": ["read"], "risk_level": "low", "preferred_task_types": ["research"], "fallback_agents": [], "verification_required": True}]}
        policy = {"schema": "agent_policy_v1", "revision": "p", "agents": {"x": {"allowed_operations": ["read"], "approval_required": False, "max_risk": "low"}}}
        with self.assertRaises(ModelResolutionError):
            route_task({"task_id": "t", "task_type": "research", "required_capabilities": ["read"]}, registry, policy)

    def test_fallback_re_resolves_model(self):
        route = {"task_id": "t", "agent_id": "a", "model": "logical-a", "runtime_model_id": "gpt-a", "provider": "p", "authority": ["read"], "fallback_agents": ["b"], "fallback_routes": [{"task_id": "t", "agent_id": "b", "model": "logical-b", "runtime_model_id": "gpt-b", "provider": "p", "authority": ["read"], "fallback_agents": [], "verification_required": True}], "approval_required": False}
        backend = Backend()
        result = Dispatcher(backend, max_fallbacks=1).run(run_id="r", tasks=[{"task_id": "t", "write_scope": []}], routes=[route])
        self.assertEqual(result.state, "COMPLETED")
        self.assertEqual(backend.requests[1]["model"], "gpt-b")
        self.assertEqual(backend.requests[1]["model_key"], "logical-b")

    def test_parent_agent_id_is_accepted_as_dispatch_id(self):
        backend = MultiAgentBackendAdapter(lambda request: {"agent_id": "agent-1"}, lambda dispatch_id: {"status": "completed", "result": "ok", "run_id": "r", "node_id": "t", "attempt": 1})
        result = Dispatcher(backend).run(run_id="r", tasks=[{"task_id": "t", "write_scope": []}], routes=[{"task_id": "t", "agent_id": "a", "model": "logical", "runtime_model_id": "gpt", "authority": ["read"], "fallback_agents": [], "approval_required": False}])
        self.assertEqual(result.state, "COMPLETED")


if __name__ == "__main__":
    unittest.main()
