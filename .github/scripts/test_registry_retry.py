"""Exercise registry-retry.sh against a fake pull and a fake sleep, offline."""

import json
import re
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
SCRIPT = SCRIPTS / "registry-retry.sh"
SIGNATURES = SCRIPTS.parent / "ci-infra-signatures.json"
WORKFLOWS = SCRIPTS.parent / "workflows"

RATE_LIMIT = "toomanyrequests: You have reached your pull rate limit."
HTTP_429 = "Error response from daemon: unexpected status code 429 Too Many Requests"
TIMEOUT = "Get https://registry-1.docker.io/v2/: dial tcp: i/o timeout"
GATEWAY = "received unexpected HTTP status: 502 Bad Gateway"
MISSING = "manifest unknown: manifest unknown"
UNKNOWN = "something nobody has seen before"


class RegistryRetry(unittest.TestCase):
    def run_pull(self, outputs, attempts=None):
        """Run the script on a pull that prints outputs[i] and fails, then succeeds."""
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            (temp / "outputs.json").write_text(json.dumps(outputs))
            pull = temp / "pull"
            pull.write_text(
                textwrap.dedent(
                    f"""\
                    #!/usr/bin/env python3
                    import json, pathlib, sys
                    count = pathlib.Path({str(temp / "count")!r})
                    n = int(count.read_text()) if count.exists() else 0
                    count.write_text(str(n + 1))
                    outputs = json.loads(pathlib.Path({str(temp / "outputs.json")!r}).read_text())
                    if n < len(outputs):
                        print(outputs[n])
                        sys.exit(18)
                    print("pulled")
                    """
                )
            )
            pull.chmod(0o755)
            sleep = temp / "sleep"
            sleep.write_text(f'#!/bin/sh\necho "$1" >> {temp / "slept"}\n')
            sleep.chmod(0o755)
            env = {"PATH": f"{temp}:/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin"}
            if attempts is not None:
                env["REGISTRY_RETRY_ATTEMPTS"] = attempts
            run = subprocess.run(
                ["bash", str(SCRIPT), "Test pull", str(pull)],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            slept = (
                (temp / "slept").read_text().split()
                if (temp / "slept").exists()
                else []
            )
            count = temp / "count"
            calls = int(count.read_text()) if count.exists() else 0
            return run, [int(s) for s in slept], calls

    def test_a_rate_limit_backs_off_and_then_succeeds(self):
        for message in (RATE_LIMIT, HTTP_429):
            with self.subTest(message=message):
                run, slept, calls = self.run_pull([message, message, message])
                self.assertEqual(run.returncode, 0, run.stdout)
                self.assertEqual(slept, [30, 60, 120])
                self.assertEqual(calls, 4)
                self.assertIn("registry rate limit on attempt 1/4", run.stdout)

    def test_a_rate_limit_that_outlasts_the_attempts_fails_with_its_status(self):
        run, slept, calls = self.run_pull([RATE_LIMIT] * 4)
        self.assertEqual(run.returncode, 18)
        self.assertEqual((slept, calls), ([30, 60, 120], 4))
        self.assertIn(
            "failed (attempt 4/4, exit 18) after a registry rate limit", run.stdout
        )

    def test_a_transient_error_retries_with_a_short_backoff(self):
        run, slept, calls = self.run_pull([TIMEOUT, GATEWAY])
        self.assertEqual(run.returncode, 0, run.stdout)
        self.assertEqual((slept, calls), ([10, 20], 3))

    def test_permanent_and_unknown_errors_fail_at_once(self):
        # An apt signature is the batch train's, never a pull retry.
        apt = "Get:1 http://archive.ubuntu.com/ubuntu noble InRelease exit code 124"
        for message in (
            MISSING,
            "unauthorized: authentication required",
            UNKNOWN,
            "",
            apt,
        ):
            with self.subTest(message=message):
                run, slept, calls = self.run_pull([message])
                self.assertEqual(run.returncode, 18)
                self.assertEqual((slept, calls), ([], 1))

    def test_a_success_runs_once_and_keeps_the_output(self):
        run, slept, calls = self.run_pull([])
        self.assertEqual((run.returncode, slept, calls), (0, [], 1))
        self.assertIn("pulled", run.stdout)

    def test_bad_arguments_and_attempts_fail_closed(self):
        run = subprocess.run(
            ["bash", str(SCRIPT), "only-a-label"], capture_output=True, check=False
        )
        self.assertEqual(run.returncode, 2)
        for attempts in ("0", "x", "-1"):
            with self.subTest(attempts=attempts):
                run, _, calls = self.run_pull([TIMEOUT], attempts=attempts)
                self.assertEqual(run.returncode, 2)
                self.assertEqual(calls, 0)

    def test_signatures_are_a_list_of_named_classified_regexes(self):
        signatures = json.loads(SIGNATURES.read_text())
        self.assertIsInstance(signatures, list)
        # The batch train reads this file too, with Python's re. It translates
        # only these POSIX classes, and matches the apt class across lines.
        posix = {"[:space:]": r"\s", "[:digit:]": r"\d"}
        for entry in signatures:
            self.assertEqual(set(entry), {"name", "class", "regex"}, entry)
            self.assertIn(
                entry["class"], {"rate_limit", "transient", "permanent", "apt"}
            )
            regex = entry["regex"]
            for posix_class, python_class in posix.items():
                regex = regex.replace(posix_class, python_class)
            self.assertNotRegex(regex, r"\[:[a-z]+:\]", entry["name"])
            re.compile(regex)
        self.assertEqual(len({entry["name"] for entry in signatures}), len(signatures))

    def test_every_registry_pull_in_the_suites_goes_through_the_script(self):
        backend = (WORKFLOWS / "backend-ci.yml").read_text()
        e2e = (WORKFLOWS / "e2e-ci.yml").read_text()
        profile = (WORKFLOWS / "e2e-stack-profile.yml").read_text()
        # No bare pull, and no `up` without the retried pull before it.
        for text in (backend, e2e, profile):
            self.assertNotIn("toomanyrequests", text)
            for line in text.splitlines():
                if re.search(r"compose\b.*\bpull\b", line):
                    self.assertIn("registry-retry.sh", line, line)
        self.assertEqual(
            backend.count('bash "$GITHUB_WORKSPACE/.github/scripts/registry-retry.sh"'),
            4,
        )
        for up in ("Start test services", "Start test dependencies"):
            step = backend.split(f"      - name: {up}\n", 1)[1].split("\n      - ", 1)[
                0
            ]
            self.assertLess(
                step.index("registry-retry.sh"), step.index(" up -d --wait")
            )
        for text in (e2e, profile):
            prefetch = text.split("      - name: Prefetch vendor images\n", 1)[1]
            self.assertIn("registry-retry.sh", prefetch.split("\n      - ", 1)[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
