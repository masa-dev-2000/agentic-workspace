from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import configure_hooks


class ConfigureHooksBootstrapTests(unittest.TestCase):
    def test_install_bootstraps_state_before_enabling_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            hooks_path = Path(temporary) / "hooks.json"

            result = configure_hooks.install(hooks_path)

            state = hooks_path.parent / "skill-telemetry"
            self.assertTrue(result["initialized"])
            self.assertTrue((state / "secret.key").is_file())
            self.assertTrue((state / "telemetry.sqlite3").is_file())
            self.assertTrue(all(result["installed"].values()))


if __name__ == "__main__":
    unittest.main()
