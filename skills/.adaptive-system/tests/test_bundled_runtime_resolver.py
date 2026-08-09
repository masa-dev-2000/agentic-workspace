from __future__ import annotations

import ast
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


SYSTEM_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = SYSTEM_ROOT / "runtime"
sys.path.insert(0, str(RUNTIME_ROOT))

from bundled_runtime_resolver import (  # noqa: E402
    BundledRuntimeResolutionError,
    main,
    resolve_bundled_runtime,
)


def create_bundle(root: Path) -> dict[str, Path]:
    python_path = root / "python" / "python.exe"
    poppler = root / "native" / "poppler" / "Library" / "bin"
    pdfinfo = poppler / "pdfinfo.exe"
    pdftoppm = poppler / "pdftoppm.exe"
    for path in (python_path, pdfinfo, pdftoppm):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
    return {
        "root": root.resolve(),
        "python": python_path.resolve(),
        "poppler": poppler.resolve(),
        "pdfinfo": pdfinfo.resolve(),
        "pdftoppm": pdftoppm.resolve(),
    }


class BundledRuntimeResolverTests(unittest.TestCase):
    def test_resolves_direct_dependency_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            expected = create_bundle(Path(temp) / "dependencies")

            result = resolve_bundled_runtime([expected["root"]])

            self.assertEqual(expected["root"], result.dependency_root)
            self.assertEqual(expected["python"], result.python_path)
            self.assertEqual(expected["poppler"], result.poppler_bin_dir)
            self.assertEqual(expected["pdfinfo"], result.pdfinfo_path)
            self.assertEqual(expected["pdftoppm"], result.pdftoppm_path)

    def test_discovers_version_neutral_runtime_below_fixture_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = Path(temp)
            expected = create_bundle(
                fixture
                / "codex-primary-runtime"
                / "release-name-not-assumed"
                / "dependencies"
            )

            result = resolve_bundled_runtime([fixture])

            self.assertEqual(expected["root"], result.dependency_root)
            source = (RUNTIME_ROOT / "bundled_runtime_resolver.py").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("26.727.11326", source)

    def test_fixture_root_does_not_fall_through_to_host_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(BundledRuntimeResolutionError) as raised:
                resolve_bundled_runtime(
                    [Path(temp)],
                    environ={
                        "CODEX_RUNTIME_DEPENDENCIES": "C:/untrusted/host/runtime"
                    },
                )

            self.assertEqual("bundled-python-not-found", raised.exception.reason)
            self.assertTrue(
                all(
                    attempt.source == "explicit"
                    for attempt in raised.exception.attempts
                )
            )

    def test_cli_outputs_paths_as_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            expected = create_bundle(Path(temp) / "dependencies")
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = main(["--fixture-root", str(expected["root"])])

            payload = json.loads(output.getvalue())
            self.assertEqual(0, exit_code)
            self.assertEqual("resolved", payload["status"])
            self.assertEqual(str(expected["python"]), payload["pythonPath"])
            self.assertEqual(str(expected["poppler"]), payload["popplerBinDir"])

    def test_missing_python_is_nonzero_with_clear_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = main(["--root", temp])

            payload = json.loads(output.getvalue())
            self.assertEqual(2, exit_code)
            self.assertEqual("unresolved", payload["status"])
            self.assertEqual("bundled-python-not-found", payload["reason"])
            self.assertIn("python executable", payload["message"])

    def test_requires_both_native_poppler_executables(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "dependencies"
            python_path = root / "python" / "python.exe"
            pdfinfo = (
                root
                / "native"
                / "poppler"
                / "Library"
                / "bin"
                / "pdfinfo.exe"
            )
            python_path.parent.mkdir(parents=True)
            pdfinfo.parent.mkdir(parents=True)
            python_path.write_bytes(b"fixture")
            pdfinfo.write_bytes(b"fixture")

            with self.assertRaises(BundledRuntimeResolutionError) as raised:
                resolve_bundled_runtime([root])

            self.assertEqual(
                "native-poppler-tools-not-found",
                raised.exception.reason,
            )
            best = next(
                attempt
                for attempt in raised.exception.attempts
                if attempt.reason == "native-poppler-tools-not-found"
            )
            self.assertEqual(("pdftoppm.exe",), best.missing)

    def test_resolution_reads_metadata_but_never_file_contents(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            expected = create_bundle(Path(temp) / "dependencies")

            with (
                patch.object(
                    Path,
                    "read_text",
                    side_effect=AssertionError("must not read executable content"),
                ),
                patch.object(
                    Path,
                    "read_bytes",
                    side_effect=AssertionError("must not read executable content"),
                ),
            ):
                result = resolve_bundled_runtime([expected["root"]])

            self.assertEqual(expected["python"], result.python_path)

    def test_resolver_source_has_no_command_execution_api(self) -> None:
        source_path = RUNTIME_ROOT / "bundled_runtime_resolver.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        forbidden_imports = {"subprocess", "shlex"}
        forbidden_calls = {
            "system",
            "popen",
            "run",
            "call",
            "check_call",
            "check_output",
            "execv",
            "execve",
            "spawnl",
            "spawnv",
        }

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.assertTrue(
                    forbidden_imports.isdisjoint(alias.name for alias in node.names)
                )
            if isinstance(node, ast.ImportFrom):
                self.assertNotIn(node.module, forbidden_imports)
            if isinstance(node, ast.Call):
                called = node.func
                if isinstance(called, ast.Attribute):
                    self.assertNotIn(called.attr.lower(), forbidden_calls)
                elif isinstance(called, ast.Name):
                    self.assertNotIn(called.id.lower(), forbidden_calls)


if __name__ == "__main__":
    unittest.main()
