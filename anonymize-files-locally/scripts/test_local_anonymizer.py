#!/usr/bin/env python3
from __future__ import annotations

import csv
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

SCRIPT = Path(__file__).with_name("local_anonymizer.py")


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        size = int(self.headers["Content-Length"])
        request = json.loads(self.rfile.read(size))
        if "Input JSON array follows:\n\n" in request["prompt"]:
            values = json.loads(
                request["prompt"].split("Input JSON array follows:\n\n", 1)[1]
            )
            replacement_name = ("[PERSON_001]" if request.get("model") == "legacy"
                                else "{foo}" if request.get("model") == "malformed"
                                else "{humannameA}")
            replacement_mail = "[EMAIL_001]" if request.get("model") == "legacy" else "{mailaddressA}"
            redacted_values = [
                value.replace("Hanako Example", replacement_name).replace(
                    "hanako@example.test", replacement_mail
                )
                for value in values
            ]
            model_result = {"decisions": [
                {
                    "is_sensitive": redacted != original,
                    "anonymized_value": redacted,
                }
                for original, redacted in zip(values, redacted_values)
            ]}
        else:
            source = request["prompt"].split("Input follows:\n\n", 1)[1]
            replacement_name = ("[PERSON_001]" if request.get("model") == "legacy"
                                else "{foo}" if request.get("model") == "malformed"
                                else "{humannameA}")
            replacement_mail = "[EMAIL_001]" if request.get("model") == "legacy" else "{mailaddressA}"
            redacted = source.replace("Hanako Example", replacement_name).replace(
                "hanako@example.test", replacement_mail)
            model_result = {"anonymized_text": redacted}
        response = json.dumps({
            "response": json.dumps(model_result),
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, *_args) -> None:
        pass


class LocalAnonymizerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def run_cli(self, *arguments: str) -> tuple[subprocess.CompletedProcess, dict]:
        process = subprocess.run(
            [sys.executable, str(SCRIPT), *arguments],
            text=True, encoding="utf-8", capture_output=True, check=False)
        return process, json.loads(process.stdout)

    def test_anonymize_metadata_only_and_stale_detection(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder, "private.txt")
            secret_name = "Hanako Example"
            secret_email = "hanako@example.test"
            source.write_text(f"Contact {secret_name} at {secret_email}.", encoding="utf-8")
            endpoint = f"http://127.0.0.1:{self.server.server_port}/api/generate"
            process, result = self.run_cli(
                "anonymize", "--input", str(source), "--endpoint", endpoint, "--model", "test")
            self.assertEqual(process.returncode, 0)
            self.assertEqual(result["status"], "pending_review")
            self.assertNotIn(secret_name, process.stdout)
            self.assertNotIn(secret_email, process.stdout)
            manifest = Path(result["manifest_path"])
            stored = manifest.read_text(encoding="utf-8")
            self.assertNotIn(secret_name, stored)
            self.assertNotIn(secret_email, stored)
            manifest_data = json.loads(stored)
            evidence = manifest_data["network_evidence"]
            self.assertEqual(evidence["destination"]["host"], "127.0.0.1")
            self.assertEqual(evidence["destination"]["port"], self.server.server_port)
            self.assertFalse(evidence["payload_recorded"])
            draft = Path(result["draft_path"])
            self.assertNotEqual(source, draft)
            self.assertIn("{humannameA}", draft.read_text(encoding="utf-8"))
            source.write_text("changed", encoding="utf-8")
            status_process, status = self.run_cli("status", "--manifest", str(manifest))
            self.assertEqual(status_process.returncode, 0)
            self.assertEqual(status["status"], "stale")

    def test_rejects_non_loopback_endpoint_without_leaking_source(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder, "private.txt")
            secret = "TOP-SECRET-VALUE"
            source.write_text(secret, encoding="utf-8")
            process, result = self.run_cli(
                "anonymize", "--input", str(source),
                "--endpoint", "https://example.com/api/generate")
            self.assertEqual(process.returncode, 2)
            self.assertEqual(result["error_code"], "non_loopback_endpoint")
            self.assertNotIn(secret, process.stdout + process.stderr)

    def test_rejects_legacy_square_bracket_placeholders(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder, "private.txt")
            source.write_text("Hanako Example", encoding="utf-8")
            endpoint = f"http://127.0.0.1:{self.server.server_port}/api/generate"
            process, result = self.run_cli(
                "anonymize", "--input", str(source),
                "--endpoint", endpoint, "--model", "legacy")
            self.assertEqual(process.returncode, 2)
            self.assertEqual(result["error_code"], "legacy_placeholder_format")

    def test_rejects_unknown_curly_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder, "private.txt")
            source.write_text("Hanako Example", encoding="utf-8")
            endpoint = f"http://127.0.0.1:{self.server.server_port}/api/generate"
            process, result = self.run_cli(
                "anonymize", "--input", str(source),
                "--endpoint", endpoint, "--model", "malformed")
            self.assertEqual(process.returncode, 2)
            self.assertEqual(result["error_code"], "invalid_placeholder_format")

    def test_folder_preserves_tree_and_reports_aggregate_status_only(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source_dir = Path(folder, "source")
            nested = source_dir / "nested"
            nested.mkdir(parents=True)
            secrets = ["Hanako Example", "hanako@example.test"]
            (source_dir / "one.txt").write_text(
                f"{secrets[0]} {secrets[1]}", encoding="utf-8")
            (nested / "two.md").write_text(
                f"# {secrets[0]}\n{secrets[1]}", encoding="utf-8")
            (nested / "ignored.bin").write_bytes(b"\x00\x01")
            endpoint = f"http://127.0.0.1:{self.server.server_port}/api/generate"
            process, result = self.run_cli(
                "anonymize-folder", "--input-dir", str(source_dir),
                "--endpoint", endpoint, "--model", "test")
            self.assertEqual(process.returncode, 0)
            self.assertEqual(result["status"], "pending_review")
            self.assertEqual(result["file_count"], 2)
            for secret in secrets:
                self.assertNotIn(secret, process.stdout)
            output_dir = Path(f"{source_dir}.anonymized")
            self.assertTrue((output_dir / "one.txt").is_file())
            self.assertTrue((output_dir / "nested" / "two.md").is_file())
            self.assertFalse((output_dir / "nested" / "ignored.bin").exists())
            batch_path = Path(result["batch_manifest_path"])
            batch = json.loads(batch_path.read_text(encoding="utf-8"))
            for manifest_value in batch["item_manifests"]:
                item_path = Path(manifest_value)
                item = json.loads(item_path.read_text(encoding="utf-8"))
                item["status"] = "approved"
                item["reviewed_at"] = "2026-07-30T00:00:00+00:00"
                item_path.write_text(json.dumps(item), encoding="utf-8")
            status_process, status = self.run_cli(
                "folder-status", "--batch-manifest", str(batch_path))
            self.assertEqual(status_process.returncode, 0)
            self.assertEqual(status["status"], "approved")
            self.assertEqual(status["status_counts"]["approved"], 2)

    def test_csv_is_rebuilt_from_cell_values(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder, "private.csv")
            source.write_text(
                'name,email,note,amount\n"Hanako Example",hanako@example.test,"a,b",1200\n',
                encoding="utf-8",
            )
            endpoint = f"http://127.0.0.1:{self.server.server_port}/api/generate"
            process, result = self.run_cli(
                "anonymize", "--input", str(source),
                "--endpoint", endpoint, "--model", "test")
            self.assertEqual(process.returncode, 0)
            self.assertEqual(result["status"], "pending_review")
            draft = Path(result["draft_path"])
            rows = list(csv.reader(io.StringIO(draft.read_text(encoding="utf-8"))))
            self.assertEqual(len(rows), 2)
            self.assertEqual([len(row) for row in rows], [4, 4])
            self.assertEqual(rows[1][0], "{humannameA}")
            self.assertEqual(rows[1][1], "{mailaddressA}")
            self.assertEqual(rows[1][2], "a,b")
            self.assertEqual(rows[1][3], "1200")


if __name__ == "__main__":
    unittest.main()
