"""Bruno examples use environment references for keys and are covered by gitleaks."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = {
    "futureagi/api_docs/add columns/preview.bru": ("api_key", "pinecone_api_key"),
    "futureagi/api_docs/add columns/vector db.bru": ("api_key", "pinecone_api_key"),
    "futureagi/api_docs/create custom model.bru": ("key", "friendli_api_key"),
    "futureagi/api_docs/accounts/Users/get logged in user.bru": ("access_token_id", "access_token_id"),
    "futureagi/api_docs/api-keys/Disable user secret key.bru": ("key_id", "user_secret_key_id"),
    "futureagi/api_docs/api-keys/Delete user secret key.bru": ("key_id", "user_secret_key_id"),
    "futureagi/api_docs/api-keys/Enable user secret key.bru": ("key_id", "user_secret_key_id"),
}


class BrunoCredentialsTests(unittest.TestCase):
    def test_provider_credentials_are_environment_references(self):
        for relative, (field, variable) in FIXTURES.items():
            with self.subTest(path=relative):
                values = re.findall(
                    rf'"{field}"\s*:\s*"([^"]*)"',
                    (ROOT / relative).read_text(),
                )
                # Never print a discovered credential in test failure output.
                self.assertTrue(
                    values == ["{{" + variable + "}}"],
                    "provider credential must be a Bruno environment reference",
                )

    def test_no_literal_bearer_token_in_changed_fixtures(self):
        for relative in FIXTURES:
            with self.subTest(path=relative):
                values = re.findall(
                    r"(?im)^\s*Authorization:\s*Bearer\s+(.*)$",
                    (ROOT / relative).read_text(),
                )
                self.assertTrue(
                    all(value.strip() == "{{access_token}}" for value in values),
                    "bearer credential must be a Bruno environment reference",
                )

    def scan(self, content):
        # Input and scanner output are separate fresh directories. Only synthetic
        # input is scanned here, never a real account key or network endpoint.
        # The input is committed to a throwaway git repository and scanned in git
        # mode, as pre-commit and CI do: gitleaks 8.27+ reads .bru files as brotli
        # archives in --no-git mode and skips them, which would hide findings.
        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as output:
            collection = Path(source) / "collection"
            collection.mkdir()
            (collection / "provider.bru").write_text(content)
            git = ["git", "-C", source, "-c", "user.name=fixture",
                   "-c", "user.email=fixture@example.com", "-c", "commit.gpgsign=false"]
            subprocess.run(git + ["init", "-q"], check=True)
            subprocess.run(git + ["add", "-A"], check=True)
            subprocess.run(git + ["commit", "-q", "-m", "fixture"], check=True)
            report = Path(output) / "report.json"
            result = subprocess.run(
                ["gitleaks", "detect", "--source", source,
                 "--config", str(ROOT / ".gitleaks.toml"), "--redact",
                 "--exit-code", "23", "--report-format", "json",
                 "--report-path", str(report)],
                capture_output=True, text=True, check=False,
            )
            findings = json.loads(report.read_text()) if report.exists() else []
            return result.returncode, findings

    def test_scanner_flags_provider_keys_in_bruno_files(self):
        for prefix in ("pcsk_", "flp_"):
            with self.subTest(provider_prefix=prefix):
                synthetic = prefix + hashlib.sha256(b"noncredential scanner regression fixture").hexdigest()
                code, findings = self.scan('body:json {\n{"api_key": "' + synthetic + '"}\n}\n')
                self.assertEqual(code, 23, "gitleaks should report provider keys found in .bru files")
                self.assertTrue(any(item["File"].endswith("provider.bru") for item in findings))

    def test_scanner_accepts_bruno_environment_references(self):
        code, findings = self.scan('body:json {\n{"api_key": "{{pinecone_api_key}}", "key": "{{friendli_api_key}}"}\n}\n')
        self.assertEqual(code, 0)
        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
