import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import runtime_cutover_e2e as E


class RuntimeCutoverE2ETests(unittest.TestCase):
    def test_isolated_hook_runner_shadow_canary_cutover(self):
        result = E.run_e2e()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual({case["classification"] for case in result["cases"]}, {"PASS"})
        self.assertEqual(result["external_runtime"], "NOT_OBSERVABLE")


if __name__ == "__main__":
    unittest.main()
