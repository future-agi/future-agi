"""Fake Docker/OpenSSL setup tests; one real daemon-free Compose dotenv roundtrip."""

import json
import os
import select
import shutil
import subprocess
import sys
import tempfile
import termios
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APPLICATION_SECRETS = (
    "SECRET_KEY", "AGENTCC_INTERNAL_API_KEY", "AGENTCC_ADMIN_TOKEN", "PG_PASSWORD",
    "MINIO_ROOT_PASSWORD", "RABBITMQ_PASSWORD", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY", "INTEGRATION_ENCRYPTION_KEY", "EE_LICENSE_KEY",
    "RECAPTCHA_SECRET_KEY", "MAILGUN_API_KEY", "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY", "FUTURE_AGI_CLOUD_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
)


class ProductionSetupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        deploy = self.root / "deploy"
        deploy.mkdir()
        for name in ("setup.sh", ".env.production.example"):
            shutil.copyfile(ROOT / "deploy" / name, deploy / name)
        self.config = deploy / ".env.production"
        self.record = self.root / "calls.jsonl"
        executables = self.root / "fake-bin"
        executables.mkdir()
        fake = f"#!{sys.executable}\n" + '''import json, os, sys
from pathlib import Path
tool = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["SETUP_TEST_RECORD"], "a") as log:
    log.write(json.dumps([tool, *args]) + "\\n")
if tool == "openssl":
    assert args[:2] == ["rand", "-hex"]
    print("a" * (2 * int(args[2])))
elif tool == "docker":
    assert args[0] == "compose"
    if args[1:] == ["version"]:
        sys.exit(0)
    assert "--env-file" in args
    if os.environ.get("SETUP_TEST_FILE_CREDENTIALS_ONLY") and any(
        name in os.environ for name in ("PROPERTY_CATALOG_API_PASSWORD", "PROPERTY_CATALOG_CONSUMER_PASSWORD")
    ):
        sys.exit(93)
    if any(name in os.environ for name in json.loads(os.environ.get("SETUP_TEST_ABSENT_ENV", "[]"))):
        sys.exit(94)
    if "config" in args:
        if os.environ.get("SETUP_TEST_INVALID_CONFIG"):
            sys.exit(17)
        if "--images" in args:
            print("futureagi/fi-collector:" + os.environ.get("FI_COLLECTOR_VERSION", "test-release"))
    elif "pull" in args:
        pass
    elif "up" in args:
        sys.exit(int(os.environ.get("SETUP_TEST_UP_EXIT", "0")))
    else:
        sys.exit(91)
else:
    sys.exit(92)
'''
        for name in ("docker", "openssl"):
            path = executables / name
            path.write_text(fake)
            path.chmod(0o700)
        self.env = {
            "PATH": str(executables) + os.pathsep + os.defpath,
            "SETUP_TEST_RECORD": str(self.record),
            "FRONTEND_URL": "https://app.example.invalid",
            "VITE_HOST_API": "https://api.example.invalid",
            "FI_COLLECTOR_VERSION": "test-release",
            "FUTURE_AGI_VERSION": "test-backend-release",
            "FRONTEND_VERSION": "test-frontend-release",
            "AGENTCC_GATEWAY_VERSION": "test-gateway-release",
            "SERVING_VERSION": "test-serving-release",
            "CODE_EXECUTOR_VERSION": "test-executor-release",
            "SIMULATION_RUNNER_VERSION": "test-simulation-release",
            "PROPERTY_CATALOG_API_PASSWORD": "private-reader-$literal # ' \\ ü",
            "PROPERTY_CATALOG_CONSUMER_PASSWORD": "private-writer-$literal # ' \\ ø",
        }

    def run_setup(self, *args, stdin="", **changes):
        env = {**self.env, **changes}
        result = subprocess.run(
            ["/bin/bash", str(self.root / "deploy/setup.sh"), *args],
            cwd=self.root, env=env, input=stdin, text=True,
            capture_output=True, timeout=5,
        )
        for key in (*APPLICATION_SECRETS, "PROPERTY_CATALOG_API_PASSWORD", "PROPERTY_CATALOG_CONSUMER_PASSWORD"):
            if env.get(key):
                self.assertTrue(env[key] not in result.stdout + result.stderr)
        return result

    def calls(self):
        return [json.loads(line) for line in self.record.read_text().splitlines()]

    def assert_no_lifecycle(self):
        self.assertFalse(any("pull" in call or "up" in call for call in self.calls()))

    def test_fresh_configuration_requires_and_stores_catalog_credentials_and_image(self):
        result = self.run_setup("--non-interactive", "--skip-up")
        self.assertEqual(result.returncode, 0)
        content = self.config.read_text()
        for key in ("PROPERTY_CATALOG_API_PASSWORD", "PROPERTY_CATALOG_CONSUMER_PASSWORD"):
            self.assertTrue(any(line.startswith(key + "=") for line in content.splitlines()))
        self.assert_compose_credentials({key: self.env[key] for key in (
            "PROPERTY_CATALOG_API_PASSWORD", "PROPERTY_CATALOG_CONSUMER_PASSWORD")})
        self.assertIn("FI_COLLECTOR_VERSION=test-release\n", content)
        self.assertIn("FUTURE_AGI_VERSION=test-backend-release\n", content)
        self.assertIn("FRONTEND_VERSION=test-frontend-release\n", content)
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)
        self.assert_no_lifecycle()

    def test_noninteractive_missing_required_inputs_fail_without_hanging(self):
        for key in ("FRONTEND_URL", "VITE_HOST_API", "FI_COLLECTOR_VERSION",
                    "FUTURE_AGI_VERSION", "FRONTEND_VERSION", "AGENTCC_GATEWAY_VERSION",
                    "SERVING_VERSION", "CODE_EXECUTOR_VERSION", "SIMULATION_RUNNER_VERSION",
                    "PROPERTY_CATALOG_API_PASSWORD", "PROPERTY_CATALOG_CONSUMER_PASSWORD"):
            with self.subTest(key=key):
                result = self.run_setup("--non-interactive", "--skip-up", **{key: ""})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(key, result.stderr)
                self.assertFalse(self.config.exists())
        self.assert_no_lifecycle()

    def test_generated_credentials_roundtrip_through_compose_without_expansion(self):
        result = self.run_setup("--non-interactive", "--skip-up")
        self.assertEqual(result.returncode, 0)
        self.assert_compose_credentials({key: self.env[key] for key in (
            "PROPERTY_CATALOG_API_PASSWORD", "PROPERTY_CATALOG_CONSUMER_PASSWORD")})
        self.assert_no_lifecycle()

    def assert_compose_credentials(self, expected):
        fixture = self.root / "literal.yml"
        fixture.write_text(
            "services:\n  probe:\n    image: unused:never-started\n    environment:\n"
            + "".join(f"      {key}: ${{{key}:?missing}}\n" for key in expected)
        )
        # Resolve outside the fake PATH. Only parse config: never a daemon call.
        command = shutil.which("docker")
        self.assertIsNotNone(command)
        rendered = subprocess.run(
            [command, "compose", "--env-file", str(self.config), "-f", str(fixture),
             "config", "--format", "json"],
            env={k: v for k, v in os.environ.items() if k in ("PATH", "HOME")},
            text=True, capture_output=True, timeout=20,
        )
        self.assertEqual(rendered.returncode, 0)  # Never print the rendered secrets.
        values = json.loads(rendered.stdout)["services"]["probe"]["environment"]
        for key, value in expected.items():
            # Compose escapes $ for reusable config output, not for the process env.
            self.assertTrue(values[key] == value.replace("$", "$$"), key)

    def test_dotenv_backslash_boundaries_roundtrip(self):
        for index, value in enumerate(("trailing\\", "before\\'quote", "double\\\\", "double\\\\'quote")):
            with self.subTest(case=index):
                expected = {key: value for key in (
                    "PROPERTY_CATALOG_API_PASSWORD", "PROPERTY_CATALOG_CONSUMER_PASSWORD")}
                result = self.run_setup("--non-interactive", "--skip-up", **expected)
                self.assertEqual(result.returncode, 0)
                try:
                    self.assert_compose_credentials(expected)
                finally:
                    self.config.unlink()  # Own temporary fixture, not installed configuration.

    def test_all_retained_secrets_are_file_authoritative(self):
        expected = {key: "retained-" + key for key in APPLICATION_SECRETS}
        original = "".join(f"{key}={value}\n" for key, value in expected.items()).encode()
        self.config.write_bytes(original)
        conflicts = {key: f"private-conflicting-{key}" for key in APPLICATION_SECRETS}
        result = self.run_setup("--non-interactive", "--skip-up",
                                SETUP_TEST_ABSENT_ENV=json.dumps(list(APPLICATION_SECRETS)), **conflicts)
        self.assertEqual(result.returncode, 0)
        self.assertTrue(self.config.read_bytes() == original)
        self.assert_compose_credentials(expected)
        self.assertFalse(any(call[0] == "openssl" for call in self.calls()))
        self.assert_no_lifecycle()

    def test_explicit_new_application_and_provider_secrets_are_preserved(self):
        expected = {key: "supplied-" + key + "-$ # \\\"'\\" for key in APPLICATION_SECRETS}
        result = self.run_setup("--non-interactive", "--skip-up", **expected)
        self.assertEqual(result.returncode, 0)
        self.assert_compose_credentials(expected)
        self.assertFalse(any(call[0] == "openssl" for call in self.calls()))

    def test_provider_key_prompts_do_not_echo_in_a_terminal(self):
        self.assert_provider_terminal()

    def test_provider_prompt_restores_terminal_on_eof(self):
        self.assert_provider_terminal(eof=True)

    def assert_provider_terminal(self, *, eof=False):
        master, slave = os.openpty()
        original_terminal = termios.tcgetattr(slave)
        process = subprocess.Popen(
            ["/bin/bash", str(self.root / "deploy/setup.sh"), "--skip-up"],
            cwd=self.root, env=self.env, stdin=slave, stdout=slave, stderr=slave,
            start_new_session=True,
        )
        os.close(slave)
        secrets = {key: "private-tty-" + key for key in (
            "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY")}
        replies = {label.encode(): b"" for label in (
            "Backend tag", "Frontend tag", "Gateway tag", "Serving tag", "Code-executor tag")}
        replies.update({key.encode(): value.encode() for key, value in secrets.items()})
        transcript = b""
        pending = b""
        answered = set()
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if select.select([master], [], [], 0.1)[0]:
                    try:
                        chunk = os.read(master, 65536)
                    except OSError:
                        break
                    if not chunk:
                        break
                    transcript += chunk
                    pending += chunk
                    if pending.endswith(b": "):
                        for label, reply in replies.items():
                            if label not in answered and label in pending:
                                if label.decode() in secrets:
                                    self.assertFalse(termios.tcgetattr(master)[3] & termios.ECHO,
                                                     "secret prompt published before echo was disabled")
                                os.write(master, b"\x04" if eof and label.decode() in secrets else reply + b"\n")
                                answered.add(label)
                                pending = b""
                                break
                elif process.poll() is not None:
                    break
            status = process.wait(timeout=1)
            self.assertTrue(termios.tcgetattr(master) == original_terminal,
                            "terminal settings were not restored")
            if eof:
                self.assertNotEqual(status, 0)
                self.assertFalse(self.config.exists())
                self.assert_no_lifecycle()
                return
            self.assertEqual(status, 0)
            for key, value in secrets.items():
                self.assertIn(key.encode(), answered)
                self.assertTrue(value.encode() not in transcript, key)
            self.assert_compose_credentials(secrets)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            os.close(master)

    def test_interactive_eof_fails_instead_of_reprompting_forever(self):
        result = self.run_setup("--skip-up", FRONTEND_URL="")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.config.exists())
        self.assert_no_lifecycle()

    def test_existing_configuration_is_never_overwritten_or_secrets_regenerated(self):
        original = b"# operator-owned retained credentials\n"
        self.config.write_bytes(original)
        result = self.run_setup("--skip-up", stdin="yes\n")
        self.assertEqual(result.returncode, 0)
        self.assertTrue(self.config.read_bytes() == original)
        self.assertFalse(any(call[0] == "openssl" for call in self.calls()))
        self.assert_no_lifecycle()

    def test_invalid_retained_config_is_not_silently_repaired(self):
        original = b"# missing required credentials\n"
        self.config.write_bytes(original)
        result = self.run_setup("--non-interactive", "--skip-up", SETUP_TEST_INVALID_CONFIG="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.config.read_bytes() == original)
        self.assertFalse(any(call[0] == "openssl" for call in self.calls()))
        self.assert_no_lifecycle()

    def test_retained_catalog_credentials_cannot_be_overridden_by_ambient_environment(self):
        original = b"# installed catalog credentials must come from this file\n"
        self.config.write_bytes(original)
        result = self.run_setup("--non-interactive", "--skip-up", SETUP_TEST_FILE_CREDENTIALS_ONLY="1")
        self.assertEqual(result.returncode, 0)
        self.assertTrue(self.config.read_bytes() == original)
        self.assert_no_lifecycle()

    def test_boot_requires_explicit_initialization_confirmation(self):
        self.config.write_text("# supplied retained configuration\n")
        result = self.run_setup("--non-interactive")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--confirm-initialized", result.stderr)
        self.assert_no_lifecycle()

    def test_confirmed_boot_validates_then_pulls_and_waits_without_building(self):
        self.config.write_text("# supplied retained configuration\n")
        result = self.run_setup("--non-interactive", "--confirm-initialized")
        self.assertEqual(result.returncode, 0)
        calls = self.calls()
        quiet = next(i for i, c in enumerate(calls) if "--quiet" in c)
        images = next(i for i, c in enumerate(calls) if "--images" in c)
        pull = next(i for i, c in enumerate(calls) if "pull" in c)
        self.assertLess(quiet, pull)
        self.assertLess(images, pull)
        up = [c for c in calls if "up" in c]
        self.assertEqual(len(up), 1)
        self.assertEqual(up[0][up[0].index("up"):],
                         ["up", "-d", "--no-build", "--wait", "--wait-timeout", "1200"])
        self.assertFalse(any("--apply" in c or "--remove-orphans" in c for c in calls))

    def test_local_or_latest_collector_image_is_rejected_before_pull(self):
        self.config.write_text("# supplied retained configuration\n")
        for tag in ("local", "latest"):
            with self.subTest(tag=tag):
                result = self.run_setup("--non-interactive", "--confirm-initialized", FI_COLLECTOR_VERSION=tag)
                self.assertNotEqual(result.returncode, 0)
                self.assert_no_lifecycle()

    def test_failed_boot_is_not_retried_or_reported_successful(self):
        self.config.write_text("# supplied retained configuration\n")
        result = self.run_setup("--non-interactive", "--confirm-initialized", SETUP_TEST_UP_EXIT="19")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len([c for c in self.calls() if "up" in c]), 1)
        self.assertNotIn("Stack is up", result.stdout)


if __name__ == "__main__":
    unittest.main()
