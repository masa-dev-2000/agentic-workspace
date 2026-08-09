import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from runtime_dispatcher import DispatchEnvelope, Dispatcher, MultiAgentBackendAdapter


class FakeBackend:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def start(self, request):
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        return DispatchEnvelope(f"d-{len(self.requests)}", request["run_id"], request["node_id"], request["attempt"], outcome["status"], outcome.get("retryable", False), outcome.get("approval_required", False), outcome.get("result"), outcome.get("error_class"), "evidence")

    def wait(self, dispatch_id):
        outcome = self.outcomes.pop(0)
        request = self.requests[-1]
        return DispatchEnvelope(dispatch_id, request["run_id"], request["node_id"], request["attempt"], outcome["status"], outcome.get("retryable", False), outcome.get("approval_required", False), outcome.get("result"), outcome.get("error_class"), "evidence")


def task(task_id, dependencies=None):
    return {"task_id": task_id, "dependencies": dependencies or [], "write_scope": []}


def route(task_id, fallback_agents=None, approval_required=False):
    return {"task_id": task_id, "agent_id": "worker", "model": "test", "authority": ["read"], "fallback_agents": fallback_agents or [], "approval_required": approval_required}


class RuntimeDispatcherTests(unittest.TestCase):
    def test_starts_waits_and_integrates_results(self):
        backend = FakeBackend([{"status": "running"}, {"status": "succeeded", "result": {"artifact": "a"}}])
        result = Dispatcher(backend).run(run_id="r", tasks=[task("a")], routes=[route("a")])
        self.assertEqual(result.state, "COMPLETED")
        self.assertEqual(result.results["a"], {"artifact": "a"})
        self.assertEqual(result.next_action, "integrate_and_report")

    def test_dependency_order_is_enforced(self):
        backend = FakeBackend([{"status": "succeeded", "result": "a"}, {"status": "succeeded", "result": "b"}])
        result = Dispatcher(backend).run(run_id="r", tasks=[task("a"), task("b", ["a"])], routes=[route("a"), route("b")])
        self.assertEqual(result.state, "COMPLETED")
        self.assertEqual([request["node_id"] for request in backend.requests], ["a", "b"])

    def test_retry_uses_fallback_once_and_stops_when_exhausted(self):
        backend = FakeBackend([{"status": "failed", "retryable": True, "error_class": "transient"}, {"status": "succeeded", "result": "ok"}])
        result = Dispatcher(backend, max_fallbacks=1).run(run_id="r", tasks=[task("a")], routes=[route("a", ["fallback"])])
        self.assertEqual(result.state, "COMPLETED")
        self.assertEqual(backend.requests[1]["agent_id"], "fallback")

    def test_approval_never_self_grants(self):
        backend = FakeBackend([])
        result = Dispatcher(backend).run(run_id="r", tasks=[task("a")], routes=[route("a", approval_required=True)])
        self.assertEqual(result.state, "BLOCKED_APPROVAL")
        self.assertEqual(result.approval_status, "approval_requested")
        self.assertEqual(backend.requests, [])

    def test_unknown_execution_state_returns_root(self):
        backend = FakeBackend([{"status": "unknown"}])
        result = Dispatcher(backend).run(run_id="r", tasks=[task("a")], routes=[route("a")])
        self.assertEqual(result.state, "RETURN_ROOT")
        self.assertTrue(all("result" not in item for item in result.evidence))

    def test_multi_agent_adapter_normalizes_completed_result(self):
        backend = MultiAgentBackendAdapter(lambda request: {"dispatch_id": "d", "status": "accepted"}, lambda dispatch_id: {"status": "completed", "result": "ok", "run_id": "r", "node_id": "a", "attempt": 1})
        result = Dispatcher(backend).run(run_id="r", tasks=[task("a")], routes=[route("a")])
        self.assertEqual(result.state, "COMPLETED")
        self.assertEqual(result.results["a"], "ok")

    def test_backend_exception_returns_root(self):
        backend = MultiAgentBackendAdapter(lambda request: (_ for _ in ()).throw(RuntimeError("unavailable")), lambda dispatch_id: {})
        result = Dispatcher(backend).run(run_id="r", tasks=[task("a")], routes=[route("a")])
        self.assertEqual(result.reason, "backend_unavailable")


if __name__ == "__main__":
    unittest.main()
