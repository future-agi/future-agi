"""Fake Docker/OpenSSL setup tests; one real daemon-free Compose dotenv roundtrip."""

import base64
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
URL_SAFE_BASE64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_="
APPLICATION_SECRETS = (
    "SECRET_KEY", "FI_API_SECRET_PEPPER", "AGENTCC_INTERNAL_API_KEY", "AGENTCC_ADMIN_TOKEN",
    "AGENTCC_WEBHOOK_SECRET", "OMEGA_HUB_CHECKER_TOKEN", "PG_PASSWORD",
    "MINIO_ROOT_PASSWORD", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY", "INTEGRATION_ENCRYPTION_KEY", "EE_LICENSE_KEY",
    "RECAPTCHA_SECRET_KEY", "MAILGUN_API_KEY", "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY", "FUTURE_AGI_CLOUD_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
)
# A Fernet key, a webhook secret and an API secret pepper the tests own, as a
# retained file keeps them.
RETAINED_KEY = base64.urlsafe_b64encode(b"k" * 32).decode()
RETAINED_KEY_LINE = f'INTEGRATION_ENCRYPTION_KEY="{RETAINED_KEY}"\n'.encode()
RETAINED_WEBHOOK_LINE = f'AGENTCC_WEBHOOK_SECRET="{"w" * 64}"\n'.encode()
RETAINED_PEPPER_LINE = f'FI_API_SECRET_PEPPER="{"p" * 64}"\n'.encode()
RETAINED_LINES = RETAINED_KEY_LINE + RETAINED_WEBHOOK_LINE + RETAINED_PEPPER_LINE
PUBLISHED_PEPPER = "local-dev-only-api-secret-pepper-replace-me"
PLACEHOLDER_PEPPER = "CHANGEME-api-secret-pepper"
OLDER_PEPPER = "an-older-operator-pepper-0123456789abcdef"
# What the fake `openssl rand -hex 32` prints: the pepper setup generates.
NEW_PEPPER = "a" * 64


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
    assert args[0] == "rand" and args[1] in ("-hex", "-base64"), args
    if args[1] == "-hex":
        print("a" * (2 * int(args[2])))
    else:  # standard base64, with the + and / that setup.sh must translate
        import base64
        print(base64.b64encode((bytes([0xFB, 0xEF, 0xFF]) * 11)[:int(args[2])]).decode())
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
        if os.environ.get("SETUP_TEST_CONFIG_STDERR"):
            sys.stderr.write(os.environ["SETUP_TEST_CONFIG_STDERR"])
            sys.exit(1)
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
            "OMEGA_HUB_CHECKER_VERSION": "test-hub-checker-release",
            "OMEGA_HUB_RUNNER_IMAGE": "registry.example.invalid/omega-hub-runner@sha256:" + "0" * 64,
            "OMEGA_HUB_RUNNER_DOCKER_HOST": "tcp://hub-runner.example.invalid:2376",
            "OMEGA_HUB_RUNNER_TLS_DIR": "/etc/futureagi/hub-runner-client",
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
                    "OMEGA_HUB_CHECKER_VERSION", "OMEGA_HUB_RUNNER_IMAGE", "OMEGA_HUB_RUNNER_DOCKER_HOST",
                    "OMEGA_HUB_RUNNER_TLS_DIR",
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
        try:
            from .compose_test_tools import compose_command
        except ImportError:
            from compose_test_tools import compose_command
        rendered = subprocess.run(
            [*compose_command(), "--env-file", str(self.config), "-f", str(fixture),
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
        original = b"# operator-owned retained credentials\n" + RETAINED_LINES
        self.config.write_bytes(original)
        result = self.run_setup("--skip-up", stdin="yes\n")
        self.assertEqual(result.returncode, 0)
        self.assertTrue(self.config.read_bytes() == original)
        self.assertFalse(any(call[0] == "openssl" for call in self.calls()))
        self.assert_no_lifecycle()

    def test_invalid_retained_config_is_not_silently_repaired(self):
        original = b"# missing required credentials\n" + RETAINED_LINES
        self.config.write_bytes(original)
        result = self.run_setup("--non-interactive", "--skip-up", SETUP_TEST_INVALID_CONFIG="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.config.read_bytes() == original)
        self.assertFalse(any(call[0] == "openssl" for call in self.calls()))
        self.assert_no_lifecycle()

    def key_lines(self):
        return [line for line in self.config.read_text().splitlines()
                if line.startswith("INTEGRATION_ENCRYPTION_KEY=")]

    def test_an_existing_file_without_the_integration_key_gets_one_once(self):
        # Stored credentials are never encrypted under a key made up for one
        # process: setup adds the missing key (and webhook secret), and only that.
        original = (b"# retained configuration from before the keyed-hash release\nSECRET_KEY=\"retained\"\n"
                    + RETAINED_PEPPER_LINE)
        self.config.write_bytes(original)
        result = self.run_setup("--non-interactive", "--skip-up")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(self.key_lines()), 1)
        filled = self.config.read_bytes()
        self.assertTrue(filled.startswith(original), "an existing setting changed")
        key = self.generated_value("INTEGRATION_ENCRYPTION_KEY")
        self.assert_fernet_key(key, "added key")
        self.assertIn(["openssl", "rand", "-base64", "32"], self.calls())
        self.assertIn("Added the missing INTEGRATION_ENCRYPTION_KEY", result.stdout)
        self.assertTrue(key not in result.stdout + result.stderr, "the key was printed")
        self.assert_compose_credentials({"INTEGRATION_ENCRYPTION_KEY": key})
        # The next run finds it and changes nothing.
        before = len(self.calls())
        self.assertEqual(self.run_setup("--non-interactive", "--skip-up").returncode, 0)
        self.assertTrue(self.config.read_bytes() == filled, "the second run changed the file")
        self.assertFalse(any(call[0] == "openssl" for call in self.calls()[before:]))
        self.assert_no_lifecycle()

    def test_an_empty_integration_key_is_filled_and_compose_reads_the_new_one(self):
        original = b'SECRET_KEY="retained"\nINTEGRATION_ENCRYPTION_KEY=""\n'
        self.config.write_bytes(original)
        result = self.run_setup("--non-interactive", "--skip-up")
        self.assertEqual(result.returncode, 0)
        self.assertTrue(self.config.read_bytes().startswith(original), "an existing setting changed")
        lines = self.key_lines()
        self.assertEqual(len(lines), 2)
        key = lines[-1].split("=", 1)[1].strip('"')
        self.assert_fernet_key(key, "added key")
        # Compose reads a key's last line: the added one.
        self.assert_compose_credentials({"INTEGRATION_ENCRYPTION_KEY": key})

    def test_exported_values_fill_a_file_that_lacks_them(self):
        # As on a new file: an explicit value is kept, a missing one generated.
        self.config.write_bytes(b'SECRET_KEY="retained"\n')
        supplied = {
            "INTEGRATION_ENCRYPTION_KEY": base64.urlsafe_b64encode(b"s" * 32).decode(),
            "AGENTCC_WEBHOOK_SECRET": "e" * 64,
            "FI_API_SECRET_PEPPER": "f" * 64,
        }
        result = self.run_setup("--non-interactive", "--skip-up", **supplied)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(self.key_lines()), 1)
        for name, value in supplied.items():
            self.assertTrue(self.generated_value(name) == value, f"not the exported {name}")
        self.assertFalse(any(call[0] == "openssl" for call in self.calls()))

    def test_running_setup_twice_keeps_the_pepper_and_fills_each_key_once(self):
        # The pepper is never rotated; the integration key and the gateway's
        # webhook secret are each filled once, then kept.
        names = ("FI_API_SECRET_PEPPER", "INTEGRATION_ENCRYPTION_KEY", "AGENTCC_WEBHOOK_SECRET")
        retained = b'FI_API_SECRET_PEPPER="' + b"p" * 64 + b'"\nSECRET_KEY="retained"\n'
        for case, original in (("new file", None), ("existing file", retained)):
            with self.subTest(case=case):
                if original is None:
                    self.config.unlink(missing_ok=True)
                else:
                    self.config.write_bytes(original)
                self.assertEqual(self.run_setup("--non-interactive", "--skip-up").returncode, 0)
                first = self.config.read_bytes()
                lines = first.decode().splitlines()
                for name in names:
                    self.assertEqual(sum(line.startswith(name + "=") for line in lines), 1, name)
                if original is not None:
                    self.assertTrue(first.startswith(original), "the pepper or another setting changed")
                webhook = self.generated_value("AGENTCC_WEBHOOK_SECRET")
                self.assertTrue(len(webhook) == 64 and set(webhook) <= set("0123456789abcdef"), "no hex webhook secret")
                self.assert_fernet_key(self.generated_value("INTEGRATION_ENCRYPTION_KEY"), "key")
                self.assert_compose_credentials({name: self.generated_value(name) for name in names})
                before = len(self.calls())
                result = self.run_setup("--non-interactive", "--skip-up")
                self.assertEqual(result.returncode, 0)
                self.assertTrue(self.config.read_bytes() == first, "the second run changed the file")
                self.assertFalse(any(call[0] == "openssl" for call in self.calls()[before:]))
                self.assertNotIn("Added the missing", result.stdout)

    def test_an_existing_integration_key_is_never_changed(self):
        forms = (
            RETAINED_KEY_LINE,
            f"INTEGRATION_ENCRYPTION_KEY={RETAINED_KEY}\n".encode(),
            f"INTEGRATION_ENCRYPTION_KEY='{RETAINED_KEY}'\n".encode(),
            f"export INTEGRATION_ENCRYPTION_KEY={RETAINED_KEY}\n".encode(),
        )
        for index, line in enumerate(forms):
            with self.subTest(form=index):
                original = line + RETAINED_WEBHOOK_LINE + RETAINED_PEPPER_LINE + b'SECRET_KEY="retained"\n'
                self.config.write_bytes(original)
                before = len(self.calls()) if self.record.exists() else 0
                result = self.run_setup("--non-interactive", "--skip-up")
                self.assertEqual(result.returncode, 0)
                self.assertTrue(self.config.read_bytes() == original, "the file changed")
                self.assertFalse(any(call[0] == "openssl" for call in self.calls()[before:]))

    # The API secret pepper follows bin/install's rule (bin/lib/secrets.sh), as
    # bin/update does: a missing, published or CHANGEME- pepper is replaced with
    # the previous value retired; any other value is kept.
    def last_value(self, key):
        """KEY's last line, unquoted: what Compose reads (setup appends lines and edits none)."""
        lines = [entry for entry in self.config.read_text().splitlines() if entry.startswith(key + "=")]
        return lines[-1].split("=", 1)[1].strip('"') if lines else None

    def test_an_existing_file_without_a_pepper_gets_one_once(self):
        original = (b"# retained configuration from before the keyed-hash release\n"
                    + RETAINED_KEY_LINE + RETAINED_WEBHOOK_LINE)
        self.config.write_bytes(original)
        result = self.run_setup("--non-interactive", "--skip-up")
        self.assertEqual(result.returncode, 0)
        filled = self.config.read_bytes()
        self.assertTrue(filled.startswith(original), "an existing setting changed")
        self.assertTrue(self.last_value("FI_API_SECRET_PEPPER") == NEW_PEPPER, "no new pepper")
        self.assertEqual(self.last_value("FI_API_SECRET_PEPPERS_RETIRED"), PUBLISHED_PEPPER)
        self.assertIn(["openssl", "rand", "-hex", "32"], self.calls())
        self.assertIn("Set a new FI_API_SECRET_PEPPER", result.stdout)
        self.assertTrue(NEW_PEPPER not in result.stdout + result.stderr, "the pepper was printed")
        self.assert_compose_credentials({"FI_API_SECRET_PEPPER": NEW_PEPPER,
                                         "FI_API_SECRET_PEPPERS_RETIRED": PUBLISHED_PEPPER})
        # The next run finds the pepper the file now has and changes nothing.
        before = len(self.calls())
        self.assertEqual(self.run_setup("--non-interactive", "--skip-up").returncode, 0)
        self.assertTrue(self.config.read_bytes() == filled, "the second run changed the file")
        self.assertFalse(any(call[0] == "openssl" for call in self.calls()[before:]))

    def test_a_set_pepper_and_its_retired_list_are_kept(self):
        original = (RETAINED_KEY_LINE + RETAINED_WEBHOOK_LINE + RETAINED_PEPPER_LINE
                    + f"FI_API_SECRET_PEPPERS_RETIRED='{OLDER_PEPPER}'\n".encode())
        self.config.write_bytes(original)
        self.assertEqual(self.run_setup("--non-interactive", "--skip-up").returncode, 0)
        self.assertTrue(self.config.read_bytes() == original, "the file changed")
        self.assertFalse(any(call[0] == "openssl" for call in self.calls()))

    def test_a_published_or_placeholder_pepper_is_replaced_with_the_previous_value_retired(self):
        for previous in (PUBLISHED_PEPPER, PLACEHOLDER_PEPPER):
            for form in ("{}", '"{}"', "'{}'"):
                with self.subTest(previous=previous, form=form):
                    original = (RETAINED_KEY_LINE + RETAINED_WEBHOOK_LINE
                                + f"FI_API_SECRET_PEPPER={form.format(previous)}\n".encode()
                                + f"FI_API_SECRET_PEPPERS_RETIRED='{OLDER_PEPPER}'\n".encode())
                    self.config.write_bytes(original)
                    result = self.run_setup("--non-interactive", "--skip-up")
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertTrue(self.config.read_bytes().startswith(original), "an existing line changed")
                    self.assertTrue(self.last_value("FI_API_SECRET_PEPPER") == NEW_PEPPER, "the pepper was kept")
                    self.assertEqual(self.last_value("FI_API_SECRET_PEPPERS_RETIRED"), f"{OLDER_PEPPER},{previous}")
                    self.assertTrue(NEW_PEPPER not in result.stdout + result.stderr, "the pepper was printed")
                    self.assert_compose_credentials({"FI_API_SECRET_PEPPER": NEW_PEPPER,
                                                     "FI_API_SECRET_PEPPERS_RETIRED": f"{OLDER_PEPPER},{previous}"})

    def test_a_missing_pepper_takes_an_exported_one_unless_that_is_a_default_too(self):
        for exported, expected in (("e" * 64, "e" * 64), (PUBLISHED_PEPPER, NEW_PEPPER),
                                   (PLACEHOLDER_PEPPER, NEW_PEPPER), ("", NEW_PEPPER)):
            with self.subTest(index=len(exported)):
                self.config.write_bytes(RETAINED_KEY_LINE + RETAINED_WEBHOOK_LINE)
                result = self.run_setup("--non-interactive", "--skip-up", FI_API_SECRET_PEPPER=exported)
                self.assertEqual(result.returncode, 0)
                self.assertTrue(self.last_value("FI_API_SECRET_PEPPER") == expected, "not the expected pepper")
                self.assertEqual(self.last_value("FI_API_SECRET_PEPPERS_RETIRED"), PUBLISHED_PEPPER)

    def test_a_retired_list_setup_cannot_keep_exactly_is_refused_and_nothing_changes(self):
        for line in ('FI_API_SECRET_PEPPERS_RETIRED="a$b"\n', "FI_API_SECRET_PEPPERS_RETIRED=\"it's\"\n",
                     "FI_API_SECRET_PEPPERS_RETIRED=$OTHER\n"):
            with self.subTest(line=line):
                original = (RETAINED_KEY_LINE + RETAINED_WEBHOOK_LINE
                            + f"FI_API_SECRET_PEPPER={PUBLISHED_PEPPER}\n".encode() + line.encode())
                self.config.write_bytes(original)
                result = self.run_setup("--non-interactive", "--skip-up")
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue(self.config.read_bytes() == original, "the file changed")
                self.assertIn("FI_API_SECRET_PEPPERS_RETIRED", result.stderr)
                self.assert_no_lifecycle()

    def generated_value(self, key):
        line = next(
            entry
            for entry in self.config.read_text().splitlines()
            if entry.startswith(key + "=")
        )
        return line.split("=", 1)[1].strip('"')

    def assert_fernet_key(self, value, name):
        # What cryptography's Fernet accepts: 32 bytes in URL-safe base64.
        self.assertTrue(len(value) == 44 and value.endswith("="), name)
        self.assertTrue(set(value) <= set(URL_SAFE_BASE64), name)
        self.assertTrue(len(base64.urlsafe_b64decode(value)) == 32, name)

    def test_fresh_install_generates_a_fernet_integration_key(self):
        # Every organization's system API key is sealed under it at sign-up: an
        # empty key failed the first sign-up of a production install (L03).
        result = self.run_setup("--non-interactive", "--skip-up")
        self.assertEqual(result.returncode, 0)
        self.assertIn(["openssl", "rand", "-base64", "32"], self.calls())
        self.assert_fernet_key(self.generated_value("INTEGRATION_ENCRYPTION_KEY"), "key")
        self.assertTrue(len(self.generated_value("FI_API_SECRET_PEPPER")) >= 32)

    def test_fresh_install_gives_every_production_process_both_keys(self):
        # Real openssl and the real overlay: what an operator's first install renders.
        docker_only = self.root / "docker-only"
        docker_only.mkdir()
        shutil.copy2(self.root / "fake-bin" / "docker", docker_only / "docker")
        path = os.pathsep.join((str(docker_only), os.environ.get("PATH", os.defpath)))
        self.assertEqual(self.run_setup("--non-interactive", "--skip-up", PATH=path).returncode, 0)
        key = self.generated_value("INTEGRATION_ENCRYPTION_KEY")
        pepper = self.generated_value("FI_API_SECRET_PEPPER")
        self.assert_fernet_key(key, "generated key")
        # The new file enables the Hub (COMPOSE_PROFILES=hub), so the Hub's production file follows.
        rendered = subprocess.run(
            [shutil.which("docker"), "compose", "--env-file", str(self.config),
             "-f", "docker-compose.distributed.yml", "-f", "deploy/docker-compose.production.yml",
             "-f", "deploy/docker-compose.production.hub.yml",
             "config", "--format", "json"],
            cwd=ROOT, env={k: v for k, v in os.environ.items() if k in ("PATH", "HOME")},
            text=True, capture_output=True, timeout=60,
        )
        self.assertTrue(rendered.returncode == 0, "compose config failed")  # never print secrets
        services = json.loads(rendered.stdout)["services"]
        django = [name for name, svc in services.items()
                  if (svc.get("environment") or {}).get("ENV_TYPE") == "production"]
        self.assertIn("backend", django)
        for name in django:
            env = services[name]["environment"]
            self.assertTrue(env.get("INTEGRATION_ENCRYPTION_KEY") == key, name)
            self.assertTrue(env.get("FI_API_SECRET_PEPPER") == pepper, name)
        collector = services["fi-collector"]["environment"]
        self.assertTrue(collector.get("FI_API_SECRET_PEPPER") == pepper, "fi-collector")
        # The Hub checker shares the backend's generated token and dials the runner host only.
        token = self.generated_value("OMEGA_HUB_CHECKER_TOKEN")
        self.assertTrue(len(token) >= 32)
        checker = services["hub-checker"]
        self.assertTrue(checker["environment"].get("OMEGA_HUB_CHECKER_TOKEN") == token, "hub-checker")
        self.assertTrue(services["backend"]["environment"].get("OMEGA_HUB_CHECKER_TOKEN") == token, "backend")
        self.assertEqual(checker["environment"]["DOCKER_HOST"], self.env["OMEGA_HUB_RUNNER_DOCKER_HOST"])
        self.assertEqual(checker["image"], "futureagi/omega-hub-checker:test-hub-checker-release")
        self.assertEqual([(v["source"], v["target"], v["read_only"]) for v in checker["volumes"]],
                         [(self.env["OMEGA_HUB_RUNNER_TLS_DIR"], "/certs/client", True)])
        self.assertNotIn("hub-runner", services)
        self.assertEqual(services["backend"]["environment"]["OMEGA_HUB_APP_URL"],
                         "https://app.example.invalid/dashboard/hub")
        self.assertEqual(services["backend"]["environment"]["OMEGA_HUB_PUBLIC_API_URL"],
                         "https://api.example.invalid/omega-hub/v1")

    def test_hub_runner_must_be_a_pinned_image_on_a_tcp_runner_host(self):
        for key, value, why in (
            ("OMEGA_HUB_RUNNER_IMAGE", "registry.example.invalid/omega-hub-runner:latest", "pinned by digest"),
            ("OMEGA_HUB_RUNNER_DOCKER_HOST", "unix:///var/run/docker.sock", "never this host's Docker socket"),
        ):
            with self.subTest(key=key):
                result = self.run_setup("--non-interactive", "--skip-up", **{key: value})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(why, result.stderr)
                self.assertFalse(self.config.exists())
        self.assert_no_lifecycle()

    def test_a_missing_required_setting_is_named_without_any_value(self):
        # Setup adds only the integration key, the webhook secret and the API
        # secret pepper; any other required setting stays missing and is named.
        original = b"# retained configuration from before the keyed-hash release\n" + RETAINED_LINES
        self.config.write_bytes(original)
        message = (
            "error while interpolating services.backend.environment.PROPERTY_CATALOG_API_PASSWORD: "
            "required variable PROPERTY_CATALOG_API_PASSWORD is missing a value: must be set for production\n"
            "unrelated line quoting a value: private-value-never-echoed\n"
        )
        result = self.run_setup("--non-interactive", "--skip-up", SETUP_TEST_CONFIG_STDERR=message)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Required setting missing from", result.stderr)
        self.assertIn(": PROPERTY_CATALOG_API_PASSWORD (deploy/README.md", result.stderr)
        self.assertNotIn("private-value-never-echoed", result.stdout + result.stderr)
        self.assertTrue(self.config.read_bytes() == original)
        self.assert_no_lifecycle()

    def test_retained_catalog_credentials_cannot_be_overridden_by_ambient_environment(self):
        original = b"# installed catalog credentials must come from this file\n" + RETAINED_LINES
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

    PRODUCTION_FILES = ["docker-compose.distributed.yml", "deploy/docker-compose.production.yml"]
    HUB_FILES = [*PRODUCTION_FILES, "deploy/docker-compose.production.hub.yml"]

    def boot_compose_files(self, file_line, exported):
        """The -f files of every Compose call and printed Compose command of a confirmed boot.

        `file_line`: the env file's COMPOSE_PROFILES line(s), or None for none.
        `exported`: the shell's COMPOSE_PROFILES, or None when it exports none.
        """
        if self.record.exists():
            self.record.unlink()
        self.config.write_text("# supplied retained configuration\n"
                               + ("" if file_line is None else file_line + "\n"))
        changes = {} if exported is None else {"COMPOSE_PROFILES": exported}
        result = self.run_setup("--non-interactive", "--confirm-initialized", **changes)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [call[1:] for call in self.calls()
                 if call[0] == "docker" and call[1:] != ["compose", "version"]]
        printed = [line.split() for line in result.stdout.splitlines() if "--env-file" in line]
        self.assertEqual((len(calls), len(printed)), (4, 2))  # config x2, pull, up; ps, exec
        return [[words[i + 1] for i, word in enumerate(words[:-1]) if word == "-f"]
                for words in calls + printed]

    def test_hub_profile_adds_the_hub_file_to_every_compose_command(self):
        for file_line, exported in (
                ("COMPOSE_PROFILES=hub", None),
                ('COMPOSE_PROFILES="ml,hub"', None),
                ("COMPOSE_PROFILES=hub # the Omega Hub", None),
                ("COMPOSE_PROFILES=ml\nCOMPOSE_PROFILES=sandbox,hub", None),  # the last one counts
                ("COMPOSE_PROFILES=ml, hub", None),  # each entry is stripped of spaces
                ("COMPOSE_PROFILES=*", None),  # every profile, the Hub's too
                ('COMPOSE_PROFILES="ml,*"', None),
                (None, "hub"),
                (None, "*"),
                (None, "ml, hub"),
                ("COMPOSE_PROFILES=ml", "sandbox,hub"),  # Compose prefers the shell's value
        ):
            with self.subTest(file_line=file_line, exported=exported):
                for files in self.boot_compose_files(file_line, exported):
                    self.assertEqual(files, self.HUB_FILES)

    def test_other_profiles_leave_the_hub_file_out(self):
        for file_line, exported in (
                (None, None),
                ("COMPOSE_PROFILES=", None),
                ("COMPOSE_PROFILES=hubx", None),
                ("COMPOSE_PROFILES=ml,sandbox", None),
                ("COMPOSE_PROFILES=hub\nCOMPOSE_PROFILES=ml", None),  # the last one counts
                ("COMPOSE_PROFILES=hub*", None),
                ("COMPOSE_PROFILES=**", None),
                (None, "hub-extra"),
                ("COMPOSE_PROFILES=hub", "ml"),  # Compose prefers the shell's value
        ):
            with self.subTest(file_line=file_line, exported=exported):
                for files in self.boot_compose_files(file_line, exported):
                    self.assertEqual(files, self.PRODUCTION_FILES)

    def profile_lines(self):
        return [line for line in self.config.read_text().splitlines()
                if line.startswith("COMPOSE_PROFILES=")]

    def test_a_new_env_file_turns_the_hub_on(self):
        # A new install supplies every Hub input, so its file enables the Hub and every
        # Compose call uses the Hub's production file.
        self.assertEqual(self.run_setup("--non-interactive", "--skip-up").returncode, 0)
        self.assertEqual(self.profile_lines(), ["COMPOSE_PROFILES=hub"])
        calls = [call[1:] for call in self.calls() if call[0] == "docker" and "config" in call]
        self.assertEqual(len(calls), 2)  # config --quiet, config --images
        for call in calls:
            files = [call[i + 1] for i, word in enumerate(call[:-1]) if word == "-f"]
            self.assertEqual(files, self.HUB_FILES)

    def test_an_existing_env_file_never_gains_compose_profiles(self):
        for original in (b'SECRET_KEY="retained"\n', b"# retained\n" + RETAINED_LINES):
            with self.subTest(original=original):
                self.config.write_bytes(original)
                self.assertEqual(self.run_setup("--non-interactive", "--skip-up").returncode, 0)
                self.assertEqual(self.profile_lines(), [])
                self.assertTrue(self.config.read_bytes().startswith(original))

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
