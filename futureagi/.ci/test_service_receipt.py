"""The service-container receipt: allowlisted fields, read by image ID, no registry, never fails a shard."""

import importlib.util
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "futureagi/.ci/service_receipt.py"
SPEC = importlib.util.spec_from_file_location("service_receipt", SCRIPT)
receipt_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(receipt_module)

SERVICES = ("test-clickhouse", "test-db", "test-minio", "test-redis")
TAGS = {
    "test-clickhouse": "clickhouse/clickhouse-server:25.3-alpine",
    "test-db": "postgres:16-alpine",
    "test-minio": "ghcr.io/coollabsio/minio:RELEASE.2025-10-15T17-29-55Z@sha256:"
    + "6" * 64,
    "test-redis": "redis:7-alpine",
}
IMAGES = {service: "sha256:" + f"{n}" * 64 for n, service in enumerate(SERVICES, 1)}
REDIS_INFO = (
    "# Server\r\nredis_version:7.4.2\r\nredis_git_sha1:00000000\r\n"
    "redis_build_id:5c6f1e7a2b3d4e5f\r\nredis_mode:standalone\r\nos:Linux 6.8\r\n"
    "executable:/data/redis-server\r\nconfig_file:\r\n"
)

# A stand-in for the docker CLI: answers the reads the script makes, logs every
# argv, and can fail or hang any call whose argv contains a configured word.
FAKE = r"""
import json, os, sys, time
argv = sys.argv[1:]
with open(os.environ["FAKE_DOCKER_LOG"], "a") as log:
    log.write(json.dumps(argv) + "\n")
plan = json.loads(os.environ["FAKE_DOCKER_PLAN"])
for word in plan.get("hang", []):
    if word in argv:
        time.sleep(30)
for word in plan.get("fail", []):
    if word in argv:
        sys.stderr.write("Error response from daemon: boom\n")
        sys.exit(1)
services, tags, images = plan["services"], plan["tags"], plan["images"]
compose = ["compose", "-f", "docker-compose.test.yml", "-p", "futureagi-test"]
if argv[:5] == compose:
    rest = argv[5:]
    if rest == ["config", "--services"]:
        print("\n".join(services))
    elif rest[:2] == ["ps", "-q"]:
        print("" if rest[2] in plan.get("stopped", []) else "cid-" + rest[2])
    elif rest[:2] == ["exec", "-T"]:
        service = rest[2]
        if service == "test-clickhouse":
            print("25.3.6.56")
        elif service == "test-db":
            print("PostgreSQL 16.11 on x86_64-pc-linux-musl, compiled by gcc 14.2.0, 64-bit")
        elif service == "test-redis":
            sys.stdout.write(plan["redis_info"])
    sys.exit(0)
if argv[:3] == ["inspect", "--type", "container"]:
    service = argv[-1][len("cid-"):]
    print(json.dumps({"id": argv[-1], "image": images[service], "config_image": tags[service],
                      "started_at": "2026-11-24T19:40:00.1Z"}))
elif argv[:2] == ["image", "inspect"]:
    print(json.dumps({"repo_digests": ["example@sha256:" + "a" * 64], "os": "linux",
                      "architecture": "amd64", "variant": "", "created": "2026-11-01T00:00:00Z"}))
elif argv[0] == "version":
    print(json.dumps("28.5.1"))
elif argv[0] == "info":
    print(json.dumps({"driver": "overlay2", "driver_status": [["Backing Filesystem", "extfs"]]}))
else:
    sys.exit(3)
"""


class FakeDocker:
    def __init__(self, testcase, **plan):
        tmp = tempfile.TemporaryDirectory(prefix="service-receipt-")
        testcase.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name)
        (self.root / "bin").mkdir()
        docker = self.root / "bin/docker"
        docker.write_text(f"#!{sys.executable}\n{FAKE}")
        docker.chmod(0o755)
        self.log = self.root / "argv.jsonl"
        self.summary = self.root / "summary.md"
        self.out = self.root / "shard-receipt/services.json"
        plan = {
            "services": list(SERVICES),
            "tags": TAGS,
            "images": IMAGES,
            "redis_info": REDIS_INFO,
            **plan,
        }
        self.env = {
            **os.environ,
            "PATH": f"{self.root / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}",
            "FAKE_DOCKER_LOG": str(self.log),
            "FAKE_DOCKER_PLAN": json.dumps(plan),
            "GITHUB_STEP_SUMMARY": str(self.summary),
        }

    def run(self):
        proc = subprocess.run(
            [sys.executable, "-I", str(SCRIPT), str(self.out)],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        return proc

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def receipt(self):
        return json.loads(self.out.read_text())


def fields(template):
    return set(re.findall(r"\{\{json \.([A-Za-z.]+)\}\}", template))


class ReceiptTest(unittest.TestCase):
    def test_every_service_is_recorded_by_its_running_image_id(self):
        fake = FakeDocker(self)
        proc = fake.run()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("::warning", proc.stdout)
        receipt = fake.receipt()
        self.assertEqual(set(receipt["services"]), set(SERVICES))
        for service in SERVICES:
            entry = receipt["services"][service]
            self.assertEqual(entry["container"]["image"], IMAGES[service])
            self.assertEqual(entry["container"]["config_image"], TAGS[service])
            self.assertEqual(entry["image"]["architecture"], "amd64")
        inspected = [
            call[-1] for call in fake.calls() if call[:2] == ["image", "inspect"]
        ]
        self.assertEqual(sorted(inspected), sorted(IMAGES.values()))
        servers = {name: entry["server"] for name, entry in receipt["services"].items()}
        self.assertEqual(servers["test-clickhouse"], {"version": "25.3.6.56"})
        self.assertTrue(servers["test-db"]["version"].startswith("PostgreSQL 16.11 "))
        self.assertIsNone(servers["test-minio"])
        self.assertEqual(
            receipt["daemon"],
            {
                "server_version": "28.5.1",
                "driver": "overlay2",
                "driver_status": [["Backing Filesystem", "extfs"]],
            },
        )
        # The same JSON is in the log, and one summary line per service.
        self.assertIn(fake.out.read_text(), proc.stdout)
        lines = fake.summary.read_text().splitlines()
        self.assertEqual(len(lines), 1 + len(SERVICES))
        self.assertIn(
            f"- `test-clickhouse`: {TAGS['test-clickhouse']} ran {IMAGES['test-clickhouse']} "
            "(linux/amd64); server 25.3.6.56",
            lines,
        )

    def test_only_allowlisted_fields_are_read_or_recorded(self):
        self.assertEqual(
            fields(receipt_module.CONTAINER_FORMAT),
            {"Id", "Image", "Config.Image", "State.StartedAt"},
        )
        self.assertEqual(
            fields(receipt_module.IMAGE_FORMAT),
            {"RepoDigests", "Os", "Architecture", "Variant", "Created"},
        )
        self.assertEqual(fields(receipt_module.INFO_FORMAT), {"Driver", "DriverStatus"})
        self.assertEqual(fields(receipt_module.VERSION_FORMAT), {"Server.Version"})
        fake = FakeDocker(self)
        self.assertEqual(fake.run().returncode, 0)
        receipt = fake.receipt()
        self.assertEqual(set(receipt), {"schema", "daemon", "services"})
        self.assertEqual(
            receipt["services"]["test-redis"]["server"],
            {
                "redis_version": "7.4.2",
                "redis_build_id": "5c6f1e7a2b3d4e5f",
                "redis_mode": "standalone",
            },
        )
        for entry in receipt["services"].values():
            self.assertEqual(set(entry), {"container", "image", "server"})

    def test_it_never_pulls_or_asks_a_registry(self):
        fake = FakeDocker(self)
        self.assertEqual(fake.run().returncode, 0)
        compose = ["compose", "-f", "docker-compose.test.yml", "-p", "futureagi-test"]
        formats = {
            receipt_module.CONTAINER_FORMAT,
            receipt_module.IMAGE_FORMAT,
            receipt_module.INFO_FORMAT,
            receipt_module.VERSION_FORMAT,
        }
        calls = fake.calls()
        self.assertTrue(calls)
        for call in calls:
            with self.subTest(call=call):
                if call[:5] == compose:
                    self.assertIn(call[5], {"config", "ps", "exec"})
                    if call[5] == "exec":
                        self.assertEqual(call[6], "-T")
                        self.assertIn(call[7], receipt_module.SERVER_COMMANDS)
                        self.assertEqual(
                            tuple(call[8:]), receipt_module.SERVER_COMMANDS[call[7]]
                        )
                elif call[:2] == ["image", "inspect"]:
                    self.assertEqual(call[2], "--format")
                    self.assertRegex(call[4], r"^sha256:[0-9a-f]{64}$")
                else:
                    self.assertIn(call[0], {"inspect", "version", "info"})
                    self.assertIn(call[call.index("--format") + 1], formats)
                for word in (
                    "pull",
                    "manifest",
                    "imagetools",
                    "buildx",
                    "login",
                    "search",
                ):
                    self.assertNotIn(word, call)

    def test_a_failed_read_is_null_with_a_warning_and_exit_0(self):
        fake = FakeDocker(self, fail=["clickhouse-client"], stopped=["test-db"])
        proc = fake.run()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        receipt = fake.receipt()
        self.assertIsNone(receipt["services"]["test-clickhouse"]["server"])
        self.assertEqual(
            receipt["services"]["test-clickhouse"]["container"]["image"],
            IMAGES["test-clickhouse"],
        )
        self.assertEqual(
            receipt["services"]["test-db"],
            {"container": None, "image": None, "server": None},
        )
        self.assertIn(
            "::warning title=Service receipt::test-clickhouse server version: exit 1 "
            "Error response from daemon: boom\n",
            proc.stdout,
        )
        self.assertIn(
            "::warning title=Service receipt::test-db container: 0 running, expected 1\n",
            proc.stdout,
        )

    def test_an_unreachable_daemon_still_writes_a_receipt(self):
        fake = FakeDocker(self, fail=["--format", "config"])
        proc = fake.run()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            fake.receipt(),
            {
                "schema": 1,
                "daemon": {
                    "server_version": None,
                    "driver": None,
                    "driver_status": None,
                },
                "services": {},
            },
        )
        self.assertEqual(proc.stdout.count("::warning title=Service receipt::"), 3)

    def run_in_process(self, fake, call_seconds, budget_seconds):
        out = io.StringIO()
        with (
            patch.dict(os.environ, fake.env, clear=True),
            patch.object(receipt_module, "CALL_SECONDS", call_seconds),
            patch.object(receipt_module, "BUDGET_SECONDS", budget_seconds),
        ):
            started = time.monotonic()
            status = receipt_module.main([str(fake.out)], out)
            elapsed = time.monotonic() - started
        self.assertEqual(status, 0)
        self.assertTrue(fake.out.exists())
        return out.getvalue(), elapsed

    def test_each_read_has_its_own_timeout(self):
        fake = FakeDocker(self, hang=["exec"])
        out, elapsed = self.run_in_process(fake, 0.5, 30)
        self.assertLess(elapsed, 10)
        self.assertIn(
            "::warning title=Service receipt::test-clickhouse server version: "
            "no answer within 0.5 s\n",
            out,
        )
        services = fake.receipt()["services"]
        self.assertIsNone(services["test-redis"]["server"])
        self.assertEqual(
            services["test-redis"]["container"]["image"], IMAGES["test-redis"]
        )

    def test_all_reads_share_one_budget(self):
        # Two reads that never answer spend the whole budget; nothing else runs.
        fake = FakeDocker(self, hang=["info", "version"])
        out, elapsed = self.run_in_process(fake, 0.5, 1.0)
        self.assertLess(elapsed, 10)
        self.assertIn(
            "::warning title=Service receipt::compose services: not read, "
            "the 1 s budget is spent\n",
            out,
        )
        self.assertEqual(fake.receipt()["services"], {})
        self.assertEqual([call[0] for call in fake.calls()], ["info", "version"])

    def test_the_step_budget_fits_inside_the_step_timeout(self):
        self.assertLessEqual(receipt_module.CALL_SECONDS, 10)
        self.assertLess(receipt_module.BUDGET_SECONDS + 30, 120)


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        workflow = (ROOT / ".github/workflows/backend-ci.yml").read_text()
        self.shard = workflow.split("\n  test:\n", 1)[1].split(
            "\n  saml-multiworker:", 1
        )[0]
        self.step = self.shard.split(
            "      - name: Record the running service containers (observation only)\n",
            1,
        )[1].split("\n      - ", 1)[0]

    def test_it_runs_after_the_health_gate_and_before_any_pytest(self):
        order = [
            "uses: ./.github/actions/backend-test-packages/environment\n",
            "- name: Record the running service containers (observation only)\n",
            "- name: Key of the migrated database for this checkout\n",
            "uv run pytest",
        ]
        positions = [self.shard.index(text) for text in order]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("        timeout-minutes: 2\n", self.step)
        self.assertIn(
            'run: python3 -I .ci/service_receipt.py "$RUNNER_TEMP/shard-receipt/services.json"',
            self.step,
        )

    def test_it_rides_the_existing_artifact_and_leaves_the_identity_alone(self):
        self.assertNotIn("receipt.json", self.step)
        upload = self.shard.split(
            "      - name: Upload the record of what this shard collected\n", 1
        )[1].split("\n      - ", 1)[0]
        self.assertIn("path: ${{ runner.temp }}/shard-receipt\n", upload)
        run_shard = self.shard.split("      - name: Run shard\n", 1)[1]
        self.assertNotIn("services.json", run_shard.split("uv run pytest", 1)[0])


if __name__ == "__main__":
    unittest.main()
