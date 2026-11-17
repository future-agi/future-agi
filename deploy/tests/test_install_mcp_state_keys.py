"""Synthetic library-entrypoint cases; both bash and pwsh are required, never skip-as-pass."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from deploy.tests.test_install_secrets import SHELL, POWERSHELL

ROOT = Path(__file__).resolve().parents[2]
KEY = "AGENTCC_MCP_STATE_KEYS"
CURRENT = "synthetic-current-" + "a" * 32
PREVIOUS = "synthetic-previous-" + "b" * 32
HEX64 = re.compile(r"[0-9a-f]{64}")
SEED = "".join(f"{name}={CURRENT}\n" for name in (
    "SECRET_KEY", "PG_PASSWORD", "MINIO_ROOT_PASSWORD", "AGENTCC_INTERNAL_API_KEY",
    "CH_PASSWORD", "FI_API_SECRET_PEPPER", "INTEGRATION_ENCRYPTION_KEY",
    "AGENTCC_WEBHOOK_SECRET", "AGENTCC_ADMIN_TOKEN", "OMEGA_HUB_CHECKER_TOKEN",
    "REDIS_PASSWORD", "PEERDB_UI_PASSWORD", "PEERDB_UI_NEXTAUTH_SECRET"))


class LibraryCases:
    executable = "bash"

    def invoke(self, text, *, exported=None, bad_generator=False, fresh=True, distributed=False):
        self.assertTrue(shutil.which(self.executable) is not None,
                        "required library runtime is unavailable (not a passing skip)")
        environ = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "TMPDIR", "LANG")}
        environ.update(FI_ROOT=str(ROOT), FI_FRESH="1" if fresh else "0")
        if exported is not None:
            environ[KEY] = exported
        if self.executable == "bash":
            script = SHELL.replace("@PROJECT_LINE@", "project_name=synthetic-mcp")
            script = script.replace('write_secrets "$FI_FRESH" 0',
                                    'write_secrets "$FI_FRESH" ' + str(int(distributed)))
            if bad_generator:
                script = script.replace("project_name=synthetic-mcp",
                                        "gen_secret() { printf 'synthetic-invalid'; }\nproject_name=synthetic-mcp")
            argv = ["bash", "-c", script]
        else:
            script = POWERSHELL.replace("@PROJECT_LINE@", "$projectName = 'synthetic-mcp'")
            script = script.replace("-Distributed $false", "-Distributed $" + str(distributed).lower())
            if bad_generator:
                script = script.replace("$projectName = 'synthetic-mcp'",
                    "function New-HexSecret { param([int]$Bytes) 'synthetic-invalid' }\n$projectName = 'synthetic-mcp'")
            argv = ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, ".env")
            path.write_bytes(text.encode("utf-8"))
            result = subprocess.run(argv, cwd=directory, env=environ, capture_output=True, timeout=60)
            after = path.read_bytes().decode("utf-8")
        return result, after

    def refusal(self, extra, **kwargs):
        before = SEED.replace(f"SECRET_KEY={CURRENT}", "SECRET_KEY=CHANGEME-synthetic") + extra
        result, after = self.invoke(before, **kwargs)
        self.assertTrue(result.returncode != 0, "unsafe input was accepted")
        self.assertTrue(after == before, "refusal mutated credentials")
        output = (result.stdout + result.stderr).decode("utf-8", "replace")
        self.assertIn(KEY, output)
        self.assertTrue(CURRENT not in output and PREVIOUS not in output, "a value reached output")

    def test_absent_and_empty_fill_once(self):
        for extra in ("", f"{KEY}=\n", f"{KEY}=''\n"):
            for fresh, distributed in ((True, False), (False, True)):
                with self.subTest(present=bool(extra), fresh=fresh, distributed=distributed):
                    result, after = self.invoke(SEED + extra, fresh=fresh, distributed=distributed)
                    self.assertTrue(result.returncode == 0, "library refused a missing key")
                    rows = [line.split("=", 1)[1] for line in after.splitlines() if line.startswith(KEY + "=")]
                    self.assertTrue(len(rows) == 1 and HEX64.fullmatch(rows[0]) is not None,
                                    "missing generated 64-hex key")
                    result, again = self.invoke(after, fresh=fresh, distributed=distributed)
                    self.assertTrue(result.returncode == 0 and again == after, "second install rotated or rewrote")

    def test_configured_lists_are_byte_identical(self):
        for value in (CURRENT, f"  {CURRENT} , , {PREVIOUS}, ", "CHANGEME-" + "x" * 32):
            before = SEED + f"{KEY}='{value}' # keep lexical bytes\n"
            result, after = self.invoke(before)
            self.assertTrue(result.returncode == 0 and after == before, "operator list was changed")

    def test_case_distinct_assignment_is_preserved_when_filling(self):
        lower = f"{KEY.lower()}='{PREVIOUS}' # separate variable\n"
        for canonical in ("", f"{KEY}=\n"):
            before = SEED + lower + canonical
            result, after = self.invoke(before)
            self.assertTrue(result.returncode == 0, "library refused a missing canonical key")
            self.assertTrue(lower in after, "case-distinct assignment was overwritten")
            rows = [line.split("=", 1)[1] for line in after.splitlines() if line.startswith(KEY + "=")]
            self.assertTrue(len(rows) == 1 and HEX64.fullmatch(rows[0]) is not None,
                            "canonical key is missing or duplicated")
            result, again = self.invoke(after)
            self.assertTrue(result.returncode == 0 and again == after, "second install changed case-distinct keys")

    def test_utf8_byte_boundary(self):
        before = SEED + f"{KEY}='{'é' * 16}'\n"
        result, after = self.invoke(before)
        self.assertTrue(result.returncode == 0 and after == before, "32 UTF-8 bytes were not retained")
        self.refusal(f"{KEY}='{'é' * 15}'\n")

    def test_unicode_trim_boundary(self):
        self.refusal(f"{KEY}=' {'é' * 15}　'\n")
        before = SEED + f"{KEY}=' {'é' * 16}　'\n"
        result, after = self.invoke(before)
        self.assertTrue(result.returncode == 0 and after == before, "Unicode whitespace changed")

    def test_short_entry_refuses_before_other_credentials(self):
        self.refusal(f"{KEY}='{CURRENT},short'\n")

    def test_nonempty_without_keys_refuses(self):
        for value in ("   ", ", ,", " ,　"):
            self.refusal(f"{KEY}='{value}'\n")

    def test_unsafe_dotenv_refuses(self):
        for raw in ('"${SYNTHETIC_STATE}"', '"' + CURRENT + '\\n"', "'unclosed"):
            self.refusal(f"{KEY}={raw}\n")

    def test_ambiguous_definitions_refuse(self):
        for extra in (f"{KEY}={CURRENT}\n{KEY}=\n", f"export {KEY}={CURRENT}\n",
                      f" {KEY}={CURRENT}\n", KEY + "\n"):
            self.refusal(extra)

    def test_exported_empty_refuses(self):
        self.refusal("", exported="")

    def test_exported_configured_refuses(self):
        self.refusal(f"{KEY}={CURRENT}\n", exported=CURRENT)

    def test_generator_bad_output_refuses_before_other_credentials(self):
        self.refusal("", bad_generator=True)

    def test_valid_list_never_calls_generator(self):
        before = SEED + f"{KEY}='{CURRENT},{PREVIOUS}'\n"
        result, after = self.invoke(before, bad_generator=True)
        self.assertTrue(result.returncode == 0 and after == before, "configured list called generator")


class ShellMcpStateKeysTests(LibraryCases, unittest.TestCase):
    executable = "bash"


class PowerShellMcpStateKeysTests(LibraryCases, unittest.TestCase):
    executable = "pwsh"


if __name__ == "__main__":
    unittest.main()
