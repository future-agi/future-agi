"""Docker fault-injection tests of bin/update: every call it makes to Docker, failed or killed.

deploy/tests/update_e2e/faults.py puts a docker (and docker-compose) wrapper
first on the PATH of one bin/update process. It records every call and, at a
chosen call, fails it with the error Docker gives (registry unreachable, no
space left on device, daemon not running, image not found), or SIGKILLs or
SIGTERMs bin/update's whole process group, or pauses it. Each matrix test
first records the full call sequence of one command on a fresh install (a
Standalone apply, a Standalone rollback, a Distributed apply, a production
`apply --stage` and the `apply` that finishes it), then runs that command
again once per (call, fault) and checks, after every run:

* the exit code is the one deploy/update/README.md gives for where it
  stopped (2 or 3 before anything changed, 4 before the restart, 5 or 130
  from the restart on; a killed run is killed), and no traceback is printed;
* before the durable rollout boundary, the env file is byte-identical to
  what it was (a killed preparation is put back by an ownership-validated
  recovery); before an actual `up`, container IDs and fixture data stay intact;
* from the rollout boundary (including ownership reads before `up`), the
  next `apply` of the same release or `rollback
  --yes` (alternately) converges to a healthy stack whose running images are
  the ones the env file selects, by their registry digests;
* `bin/update status` tells the truth: what runs, what the env file selects,
  an unfinished run (and its stage) exactly when a journal is left, the last
  receipt; no run holds the lock, and no journal is left after a recovery;
* the run made the same calls as the recording up to the fault (the
  sequence is deterministic up to the restart).

The rest covers what the other suites do not: Docker unavailable, an env
file or checkout that cannot be written, an unusable state directory, a
checkout path with spaces and non-ASCII characters, a symlinked checkout run
from elsewhere, two applies at once, COMPOSE_PROJECT_NAME / COMPOSE_FILE in
the shell versus .env, backup hooks that hang or fail, a Distributed release
whose image differs from the signed one for one service, and `status` at
every single call of an apply.

The wrapper and the matrix logic have unit tests here that need no Docker.
The Docker tests are skipped unless FUTURE_AGI_UPDATE_E2E=1:

  FUTURE_AGI_UPDATE_E2E=1 python3 -m unittest discover -s deploy/tests \\
      -p 'test_update_e2e_faults.py' -v

FUTURE_AGI_UPDATE_E2E_FAULTS_SCOPE=state runs only the state-changing calls
and their neighbours of the Standalone sequences too (CI); the default,
full, runs every call there. Tests run FUTURE_AGI_UPDATE_E2E_JOBS at a time.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from update_e2e import distributed as dist  # noqa: E402
from update_e2e import faults  # noqa: E402
from update_e2e import harness as e2e  # noqa: E402

V51, V52 = "v1.51.0", "v1.52.0"
# A Distributed release whose serving image is re-pushed after it was signed (only test_23 uses its tags).
MISMATCH = "v1.52.1"
# Each production matrix owns different tags. Stage prepare() removes its
# targets; sharing them with the concurrent finish matrix corrupts both traces.
PROD_STAGE, PROD_FINISH = "v1.52.3", "v1.52.4"
VERSION_KEY = "FUTURE_AGI_VERSION"
JOURNAL_NOTE = "an interrupted bin/update run will be recovered by the next apply or rollback"
DAEMON_HINT = "Docker is not running or not reachable (check DOCKER_HOST and docker context ls)"
ACTIVE_KEYS = tuple(k for k in dist.VERSION_KEYS if k != "SIMULATION_RUNNER_VERSION")
DEFAULT_SERVICES = tuple(s for s in dist.SERVICES if s not in dist.PROFILE_SERVICES and s != dist.BOOTSTRAP)
WORKTREE = Path(__file__).resolve().parents[2]

H: Optional[dist.DistributedHarness] = None
SKIP_REASON = ""
# What the matrix saw that it does not fail on, said again at the end of the run (tearDownModule).
NOTES: List[str] = []


def setUpModule() -> None:
    """The run's harness, when the Docker tests can run. The unit tests below run either way."""

    global H, SKIP_REASON
    reason = dist.availability()
    if reason is None:
        try:
            faults.faults_prefix()
            faults.scope()
        except ValueError as exc:
            reason = str(exc)
    if reason:
        SKIP_REASON = reason
        return
    harness = dist.DistributedHarness(prefix=faults.faults_prefix())
    try:
        harness.start(releases=[(V52, "default", "trusted")], extra_images={MISMATCH: "ok", PROD_STAGE: "ok", PROD_FINISH: "ok"},
                      dist_releases=[(V52, "trusted")])
        for version in (MISMATCH, PROD_STAGE, PROD_FINISH):
            for component in dist.COMPONENTS:
                harness.push_component(component, version, "ok")
        for version in (MISMATCH, PROD_STAGE, PROD_FINISH):
            harness.dist_release(version)
    except BaseException:
        harness.stop()
        raise
    H = harness


def tearDownModule() -> None:
    for note in NOTES:
        e2e.log(f"note: {note}")
    if H is not None:
        H.stop()


def load_tests(loader: unittest.TestLoader, tests: unittest.TestSuite, pattern: Optional[str]) -> unittest.TestSuite:
    return e2e.parallel_suite(tests)


def with_version(text: bytes, value: str, key: str = VERSION_KEY) -> bytes:
    replaced, count = re.subn(rf"(?m)^{key}=.*$".encode(), f"{key}={value}".encode(), text)
    if count != 1:
        raise AssertionError(f"expected one {key}= line, found {count}")
    return replaced


def env_values(text: bytes, keys: Sequence[str] = dist.VERSION_KEYS) -> Dict[str, str]:
    """The (last) value of each of `keys` in an env file's text; absent keys are left out."""

    values: Dict[str, str] = {}
    for line in text.decode("utf-8", "replace").splitlines():
        match = re.match(r"^([A-Z][A-Z0-9_]*)=(\S*)", line)
        if match and match.group(1) in keys:
            values[match.group(1)] = match.group(2)
    return values


def lock_held(state_dir: Path) -> bool:
    """Whether a bin/update holds the checkout's update lock (a shared probe that never waits)."""

    try:
        fd = os.open(str(state_dir / "update.lock"), os.O_RDONLY)
    except OSError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def article(kind: str) -> str:
    return f"an {kind}" if kind[:1] in "aeiou" else f"a {kind}"


def leftovers(co: e2e.Checkout) -> List[str]:
    """Temporary files a run may leave: env-file and journal temp files (verify-* dirs are reported apart)."""

    found = []
    for directory in (co.root, co.root / "deploy", co.state_dir):
        if directory.is_dir():
            found += [str(p.relative_to(co.root)) for p in directory.iterdir()
                      if p.name.startswith((".env.futureagi-update-", ".journal.json."))]
    return sorted(found)


def verify_dirs(co: e2e.Checkout) -> List[str]:
    return sorted(p.name for p in co.state_dir.iterdir() if p.name.startswith("verify-")) \
        if co.state_dir.is_dir() else []


# ================================================================ unit tests --

FAKE_DOCKER = """#!/bin/sh
# A stand-in for docker in the fault injector's unit tests: says what it was given.
echo "REAL $*"
[ -n "${FAKE_TOUCH:-}" ] && : > "$FAKE_TOUCH"
exit "${FAKE_EXIT:-0}"
"""

# A parent that runs one command (the wrapper) the way bin/update runs docker, and says how it ended.
PARENT = ("import subprocess, sys\n"
          "result = subprocess.run(sys.argv[1:])\n"
          "print('parent saw', result.returncode, flush=True)\n"
          "sys.exit(7)\n")


class FaultInjectorTests(unittest.TestCase):
    """The docker wrapper itself, against a fake docker: no Docker needed."""

    SHELL = "/bin/sh"

    def setUp(self) -> None:
        if not os.access(self.SHELL, os.X_OK):
            self.skipTest(f"no {self.SHELL} on this host")
        temporary = tempfile.TemporaryDirectory(prefix="fagi-faults-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.fake = self.base / "fake-docker"
        self.fake.write_text(FAKE_DOCKER, encoding="utf-8")
        self.fake.chmod(0o755)
        self.fd = faults.FaultDocker(self.base / "wrapper", docker=str(self.fake), compose=str(self.fake),
                                     shell=self.SHELL)
        self.assertEqual((self.fd.bin / "docker").read_text(encoding="utf-8").splitlines()[0], f"#!{self.SHELL}")

    def call(self, env: Dict[str, str], *args: str, program: str = "docker",
             extra: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess:
        environ = dict(os.environ, **env, **(extra or {}))
        return subprocess.run([str(self.fd.bin / program), *args], env=environ, capture_output=True, text=True,
                              timeout=60, check=False)

    def test_without_its_directory_it_is_transparent(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("FAGI_FAULT_")}
        result = subprocess.run([str(self.fd.bin / "docker"), "ps", "-a"], env=dict(env, FAKE_EXIT="3"),
                                capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (3, "REAL ps -a\n", ""))
        self.assertEqual(self.fd.calls(), [])

    def test_it_records_every_call_with_its_words(self):
        env = self.fd.env()
        self.assertEqual(self.call(env, "compose", "version", "--short").stdout, "REAL compose version --short\n")
        self.assertEqual(self.call(env, "inspect", "a b", "c").returncode, 0)
        self.assertEqual(self.call(env, "-f", "/x y/z.yml", "config", "--quiet", program="docker-compose").stdout,
                         "REAL -f /x y/z.yml config --quiet\n")
        calls = self.fd.calls()
        self.assertEqual([(c.index, c.program, c.action) for c in calls],
                         [(1, "docker", "run"), (2, "docker", "run"), (3, "docker-compose", "run")])
        self.assertEqual(calls[1].args, ("inspect", "a b", "c"))
        # docker-compose is recorded as `compose ...`, so one glob fits either Compose.
        self.assertEqual(calls[2].words, ["compose", "-f", "/x y/z.yml", "config", "--quiet"])
        self.assertEqual([c.kind for c in calls], ["compose-version", "inspect", "config-check"])

    def test_it_fails_only_the_chosen_occurrence_of_a_glob(self):
        env = self.fd.env(at=2, match="ps *", action="fail", stderr=faults.DAEMON_DOWN)
        results = [self.call(env, *args) for args in (["ps", "-a"], ["images"], ["ps", "-q"], ["ps", "-a"])]
        self.assertEqual([r.returncode for r in results], [0, 0, 1, 0])
        self.assertEqual((results[2].stdout, results[2].stderr), ("", faults.DAEMON_DOWN + "\n"))
        self.assertEqual([c.action for c in self.fd.calls()], ["run", "run", "fail", "run"])

    def test_it_can_choose_an_absolute_call_index_instead_of_a_pattern_occurrence(self):
        env = self.fd.env(index=3, match="not-this-command", action="fail", stderr=faults.DAEMON_DOWN)
        results = [self.call(env, *args) for args in (["ps"], ["images"], ["inspect", "container"], ["ps"])]
        self.assertEqual([result.returncode for result in results], [0, 0, 1, 0])
        self.assertEqual([call.action for call in self.fd.calls()], ["run", "run", "fail", "run"])
        with self.assertRaisesRegex(ValueError, "choose at or index"):
            self.fd.env(at=1, index=1)

    def test_a_failure_prints_the_error_on_both_streams_and_exits_with_its_code(self):
        _, stdout, stderr, code = faults.error_for("image-inspect", ["image", "inspect", "repo:v1"])
        env = self.fd.env(at=1, action="fail", stdout=stdout, stderr=stderr, exit_code=code)
        result = self.call(env, "image", "inspect", "repo:v1")
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (1, "", "Error response from daemon: No such image: repo:v1\n"))
        _, _, stderr, code = faults.error_for("run", ["run", "--rm", "ghcr.io/sigstore/cosign/cosign:v3"])
        result = self.call(self.fd.env(at=1, action="fail", stderr=stderr, exit_code=code), "run", "x")
        self.assertEqual(result.returncode, 125)
        self.assertEqual(result.stderr.splitlines()[0], "Unable to find image 'ghcr.io/sigstore/cosign/cosign:v3' locally")

    def test_fail_after_runs_docker_first(self):
        touched = self.base / "ran"
        env = self.fd.env(at=1, action="fail-after", stderr=faults.NO_SPACE)
        result = self.call(env, "compose", "up", "-d", extra={"FAKE_TOUCH": str(touched)})
        self.assertEqual((result.returncode, result.stdout), (1, "REAL compose up -d\n"))
        self.assertIn("no space left on device", result.stderr)
        self.assertTrue(touched.exists())
        self.assertEqual(self.fd.calls()[0].action, "fail-after")
        self.assertTrue(self.fd.calls()[0].ran)

    def test_times_all_fails_every_match_from_the_chosen_one_and_skip_globs_pass(self):
        env = self.fd.env(at=2, action="fail", times="all", stderr=faults.DAEMON_DOWN,
                          skip=faults.skip_without_daemon())
        args = (["ps"], ["compose", "config", "--format", "json"], ["inspect", "x"],
                ["compose", "--env-file", "e", "-f", "a", "config", "--services"], ["compose", "up", "--help"],
                ["image", "inspect", "y"])
        codes = [self.call(env, *a).returncode for a in args]
        # Matches counted: ps (1, runs), inspect (2, fails), image inspect (3, fails); Compose's own work passes.
        self.assertEqual(codes, [0, 0, 1, 0, 0, 1])

    def test_a_skip_glob_is_never_expanded_against_files(self):
        (self.base / "compose config here").write_text("")
        env = self.fd.env(at=1, action="fail", skip=["compose config*"])
        env["PWD"] = str(self.base)
        result = subprocess.run([str(self.fd.bin / "docker"), "compose", "config"], env=dict(os.environ, **env),
                                cwd=str(self.base), capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_sigkill_and_sigterm_take_down_the_parent_that_leads_its_group(self):
        for action, code in (("sigkill", -signal.SIGKILL), ("sigterm", -signal.SIGTERM),
                             ("sigkill-after", -signal.SIGKILL), ("sigterm-after", -signal.SIGTERM)):
            with self.subTest(action=action):
                env = self.fd.env(at=1, action=action)
                touched = self.base / f"ran-{action}"
                parent = subprocess.run([sys.executable, "-c", PARENT, str(self.fd.bin / "docker"), "ps"],
                                        env=dict(os.environ, **env, FAKE_TOUCH=str(touched)), capture_output=True,
                                        text=True, timeout=60, start_new_session=True, check=False)
                self.assertEqual(parent.returncode, code, parent.stderr)
                self.assertNotIn("parent saw", parent.stdout)
                self.assertEqual(touched.exists(), action.endswith("-after"))
                self.assertEqual([line.split()[1] for line in self.fd.signalled()],
                                 ["KILL" if "kill" in action else "TERM"])

    def test_it_never_signals_a_group_bin_update_does_not_lead(self):
        # Not in a session of its own (as run() would leave it): the test runner's group would go down too.
        env = self.fd.env(at=1, action="sigkill")
        parent = subprocess.run([sys.executable, "-c", PARENT, str(self.fd.bin / "docker"), "ps"],
                                env=dict(os.environ, **env), capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(parent.returncode, 7)
        self.assertIn("parent saw 98", parent.stdout)
        self.assertIn("is not led by bin/update", parent.stderr)
        self.assertEqual(self.fd.signalled(), [])

    def test_it_never_signals_the_protected_group(self):
        # A parent leading its own group, named as protected.
        script = ("import os, subprocess, sys\n"
                  "os.environ['FAGI_FAULT_PROTECT'] = str(os.getpgrp())\n"
                  "result = subprocess.run(sys.argv[1:])\n"
                  "print('parent saw', result.returncode)\n")
        env = self.fd.env(at=1, action="sigterm")
        parent = subprocess.run([sys.executable, "-c", script, str(self.fd.bin / "docker"), "ps"],
                                env=dict(os.environ, **env), capture_output=True, text=True, timeout=60,
                                start_new_session=True, check=False)
        self.assertEqual(parent.returncode, 0, parent.stderr)
        self.assertIn("parent saw 98", parent.stdout)
        self.assertIn("protected process group", parent.stderr)

    def test_pause_holds_the_call_until_it_is_released(self):
        env = self.fd.env(at=1, action="pause")
        process = subprocess.Popen([str(self.fd.bin / "docker"), "ps"], env=dict(os.environ, **env),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(process.kill)
        e2e.wait_for(lambda: self.fd.paused() == [1], timeout=30, what="the call to pause")
        self.assertIsNone(process.poll())
        self.assertFalse(self.fd.resumed(1))
        self.fd.release(1)
        stdout, _ = process.communicate(timeout=30)
        self.assertEqual((process.returncode, stdout), (0, "REAL ps\n"))
        self.assertTrue(self.fd.resumed(1))

    def test_observer_records_header_before_failure_and_never_secret_values(self):
        journal = self.base / "journal.json"
        extra = {"FAGI_FAULT_JOURNAL": str(journal)}
        self.call(self.fd.env(), "ps", extra=extra)
        self.assertEqual(self.fd.calls()[0].transaction, ("absent",) * 3)
        secret = "never-record-this-env-value"
        for stage in ("env_written", "pulled", "rollout", secret):
            journal.write_text(json.dumps({"stage": stage, "kind": "apply", "status": "running", "env": secret}))
            original = journal.read_bytes()
            result = self.call(self.fd.env(at=1, action="fail"), "compose", "config", extra=extra)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(self.fd.calls()[0].transaction,
                             ("invalid" if stage == secret else stage, "apply", "running"))
            self.assertEqual(journal.read_bytes(), original)
            self.assertNotIn(secret, (self.fd.state / "calls").read_text())
        journal.write_text("not JSON " + secret)
        self.call(self.fd.env(), "ps", extra=extra)
        self.assertEqual(self.fd.calls()[0].transaction, ("invalid",) * 3)
        self.assertNotIn(secret, (self.fd.state / "calls").read_text())

    def test_profile_result_evidence_is_observed_not_inferred_from_invocations(self):
        self.fake.write_text('#!/bin/sh\nprintf "%s" "$FAKE_OUTPUT"\nexit "${FAKE_EXIT:-0}"\n')
        for output, code, expected in (("", 0, "empty"), ("optional\nextra\n", 0, "nonempty"),
                                       ("not a profile SECRET\n", 0, "invalid"), ("optional\n", 1, "failed")):
            with self.subTest(expected=expected):
                result = self.call(self.fd.env(), "compose", "config", "--profiles",
                                   extra={"FAKE_OUTPUT": output, "FAKE_EXIT": str(code)})
                self.assertEqual((result.returncode, result.stdout), (code, output))
                self.assertEqual(self.fd.calls()[0].profiles, expected)
                sidecar = (self.fd.state / "1.evidence.json").read_text()
                self.assertNotIn("SECRET", sidecar)
                self.assertNotIn("optional", sidecar)
        self.call(self.fd.env(at=1, action="fail"), "compose", "config", "--profiles")
        self.assertEqual(self.fd.calls()[0].profiles, "unobserved")
        self.assertFalse((self.fd.state / "1.evidence.json").exists())

    def test_model_evidence_preserves_service_order_without_logging_config_or_env(self):
        self.fake.write_text('#!/bin/sh\nprintf "%s" "$FAKE_OUTPUT"\nprintf "%s" "$FAKE_ERROR" >&2\nexit "${FAKE_EXIT:-0}"\n')
        secret = "never-record-model-env-secret"
        model = json.dumps({"services": {"redis": {"image": "busybox:1.37", "environment": {"KEY": secret}},
                                         "app": {"image": "repo:v1"}, "builder": {"build": ".", "image": "repo:build"}}})
        def digest(text):
            return hashlib.sha256(text.encode()).hexdigest()
        expected = ((digest("app"), digest("repo:v1")), (digest("redis"), digest("busybox:1.37")))
        for program, words in (("docker", ("compose", "config", "--format", "json")),
                               ("docker-compose", ("-f", "path with spaces.yml", "config", "--format", "json"))):
            result = self.call(self.fd.env(), *words, program=program,
                               extra={"FAKE_OUTPUT": model, "FAKE_ERROR": secret})
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, model, secret))
            self.assertEqual(self.fd.calls()[0].service_images, expected)
            sidecar = (self.fd.state / "1.evidence.json").read_text()
            for forbidden in (secret, "busybox", "redis", "builder", "environment"):
                self.assertNotIn(forbidden, sidecar)
        for output, code in ((model, 7), ("invalid JSON " + secret, 0)):
            result = self.call(self.fd.env(), "compose", "config", "--format", "json",
                               extra={"FAKE_OUTPUT": output, "FAKE_EXIT": str(code)})
            self.assertEqual((result.returncode, result.stdout), (code, output))
            self.assertEqual(self.fd.calls()[0].service_images, ())
        self.call(self.fd.env(), "ps")
        self.assertFalse((self.fd.state / "1.evidence.json").exists())

    def test_each_env_starts_a_new_record(self):
        self.call(self.fd.env(), "ps")
        self.call(self.fd.env(), "inspect", "x")
        self.assertEqual([c.text for c in self.fd.calls()], ["inspect x"])


class DashFaultInjectorTests(FaultInjectorTests):
    """The same, run by dash: Ubuntu's /bin/sh, which CI's wrappers run under."""

    SHELL = "/bin/dash"


class FaultLogicTests(unittest.TestCase):
    """How recorded calls are classified, selected and judged: no Docker needed."""

    PROJECT = "label=com.docker.compose.project=fagi-upd-e2e-flt-x"
    ID = "a" * 64
    SEQUENCE = [
        ("docker", ["compose", "version", "--short"]),
        ("docker-compose", ["version", "--short"]),
        ("docker-compose", ["config", "--format", "json"]),
        ("docker-compose", ["config", "--services"]),
        ("docker", ["run", "--rm", "--read-only", "-v", "/x/.futureagi-updates/verify-1:/work:ro,z", "cosign"]),
        ("docker", ["ps", "-a", "-q", "--no-trunc", "--filter", PROJECT]),
        ("docker", ["inspect", ID]),
        ("docker", ["image", "inspect", "sha256:" + "b" * 64]),
        ("docker", ["image", "inspect", "localhost:5000/futureagi/standalone:v1.52.0"]),
        ("docker-compose", ["config", "--quiet"]),
        ("docker-compose", ["config", "--format", "json"]),
        ("docker", ["image", "inspect", "localhost:5000/futureagi/standalone:v1.52.0"]),
        ("docker-compose", ["pull", "app"]),
        ("docker", ["image", "inspect", "busybox:1.37"]),
        ("docker", ["image", "inspect", "localhost:5000/futureagi/standalone:v1.52.0"]),
        ("docker-compose", ["up", "--help"]),
        ("docker-compose", ["up", "-d", "--no-build", "--pull", "never"]),
        ("docker-compose", ["logs", "--no-color", "--tail", "2000", "app"]),
        ("docker", ["ps", "-a", "-q", "--no-trunc", "--filter", PROJECT]),
        ("docker", ["inspect", ID]),
    ]

    def calls(self, actions: Optional[Dict[int, str]] = None) -> List[faults.Call]:
        return [faults.Call(i + 1, program, (actions or {}).get(i + 1, "run"), "1", tuple(words),
                            ("absent", "absent", "absent") if i < 9 else
                            ("env_written" if i < 14 else "pulled" if i < 16 else "rollout", "apply", "running"))
                for i, (program, words) in enumerate(self.SEQUENCE)]

    def test_classify(self):
        self.assertEqual([c.kind for c in self.calls()], [
            "compose-version", "compose-version", "config", "config-services", "run", "ps", "inspect",
            "image-inspect", "image-inspect", "config-check", "config", "image-inspect", "pull", "image-inspect",
            "image-inspect", "up-help", "up", "logs", "ps", "inspect"])
        production = ["compose", "--env-file", "/r/deploy/.env.production", "-f", "/r/a.yml", "-f", "/r/b.yml", "up",
                      "-d", "--no-build", "--wait", "--wait-timeout", "300"]
        self.assertEqual(faults.classify(production), "up")
        self.assertEqual(faults.classify(["compose", "up", "--help"]), "up-help")
        self.assertEqual(faults.classify(["tag", "sha256:x", "repo:v1"]), "tag")
        self.assertEqual(faults.classify([]), "other")

    def test_signature_keeps_what_tells_calls_apart_and_drops_what_changes_per_run(self):
        calls = self.calls()
        self.assertEqual(calls[5].glob, "ps -a -q --no-trunc --filter label=com.docker.compose.project=*")
        self.assertEqual(calls[6].glob, "inspect *")
        self.assertEqual(calls[4].glob, "run *")
        self.assertEqual(calls[7].glob, "image inspect sha256:*")
        self.assertEqual(calls[8].glob, "image inspect localhost:5000/futureagi/standalone:v1.52.0")
        self.assertEqual(faults.signature(["compose", "--env-file", "/r/e", "-f", "/r/a", "config", "--quiet"]),
                         "compose * config --quiet")
        self.assertEqual(faults.signature(["compose", "config", "--quiet"]), "compose config --quiet")
        # Glob characters in a word match only themselves.
        self.assertEqual(faults.signature(["image", "inspect", "repo:v[1]*"]), "image inspect repo:v[[]1][*]")
        import fnmatch
        self.assertTrue(fnmatch.fnmatchcase("image inspect repo:v[1]*", "image inspect repo:v[[]1][*]"))
        self.assertFalse(fnmatch.fnmatchcase("image inspect repo:v1x", "image inspect repo:v[[]1][*]"))

    def test_occurrence_counts_earlier_calls_of_the_same_glob(self):
        calls = self.calls()
        self.assertEqual([faults.occurrence(calls, i) for i in (5, 18)], [1, 2])
        self.assertEqual([faults.occurrence(calls, i) for i in (8, 11, 14)], [1, 2, 3])
        self.assertEqual(faults.occurrence(calls, 2), 1)
        self.assertEqual(faults.occurrence(calls, 10), 2)

    def test_full_matrix_splits_at_the_restart(self):
        calls = self.calls()
        before = faults.full_matrix(calls, ["fail"], before_restart=True)
        after = faults.full_matrix(calls, ["fail", "sigkill"], before_restart=False)
        self.assertEqual([p.index for p in before], list(range(1, 17)))
        self.assertEqual([(p.index, p.action) for p in after],
                         [(17, "fail"), (17, "sigkill"), (18, "fail"), (18, "sigkill"), (19, "fail"),
                          (19, "sigkill"), (20, "fail"), (20, "sigkill")])
        self.assertEqual(before[12].error, "registry-unreachable")
        self.assertEqual(after[0].error, "no-space")
        self.assertEqual(after[1].error, "")
        # Without a restart (a stage), there is nothing after it.
        stage = calls[:15]
        self.assertEqual(faults.full_matrix(stage, ["fail"], before_restart=False), [])
        self.assertEqual(len(faults.full_matrix(stage, ["fail"], before_restart=True)), 15)

    def test_state_matrix_is_the_state_changes_and_their_neighbours(self):
        calls = self.calls()
        self.assertEqual(faults.state_positions(calls), [8, 9, 10, 11, 12, 13, 15, 16, 17, 18, 19])
        points = faults.state_matrix(calls)
        self.assertEqual(len(points), 22)
        self.assertEqual({p.kind for p in points if p.index in (13, 17)}, {"pull", "up"})

    def test_extended_actions_only_at_state_changes(self):
        calls = self.calls()
        self.assertEqual(faults.actions_for(calls[12], extended=True),
                         ["fail", "sigkill", "fail-after", "sigkill-after", "sigterm", "sigterm-after"])
        self.assertEqual(faults.actions_for(calls[5], extended=True), ["fail", "sigkill"])

    def test_phase_of_a_run(self):
        self.assertEqual(faults.phase_of(self.calls())[0], "none")
        for index, phase in ((1, "plan"), (9, "plan"), (10, "prepare"), (13, "prepare"), (16, "prepare"),
                             (17, "rollout"), (20, "rollout")):
            with self.subTest(index=index):
                run = self.calls({index: "fail"})[:index]
                found, faulted = faults.phase_of(run)
                self.assertEqual((found, faulted.index), (phase, index))
        run = self.calls({17: "sigkill"})[:17]
        self.assertFalse(faults.restarted(run))
        run = self.calls({17: "sigkill-after"})[:17]
        self.assertTrue(faults.restarted(run))

    def test_prefix_matches_compares_up_to_the_fault(self):
        recorded = self.calls()
        run = self.calls({12: "fail"})[:12]
        self.assertIsNone(faults.prefix_matches(recorded, run))
        changed = list(run)
        changed[3] = faults.Call(4, "docker-compose", "run", "1", ("config", "--profiles"))
        self.assertIn("call 4 is `compose config --profiles`", faults.prefix_matches(recorded, changed))

    def test_error_for_each_kind(self):
        self.assertEqual(faults.error_for("ps")[2], faults.DAEMON_DOWN)
        self.assertIn("no space left on device", faults.error_for("up")[2])
        self.assertEqual([faults.error_for("pull", name=faults.error_name("pull", v))[0] for v in range(4)],
                         ["registry-unreachable", "no-space", "image-not-found", "registry-unreachable"])
        self.assertEqual(faults.error_for("compose-version")[2], faults.NO_COMPOSE)
        self.assertEqual(faults.error_for("config-check")[2], faults.INVALID_COMPOSE_CONFIG)
        self.assertEqual(faults.error_for("tag", ["tag", "sha256:abc", "r:v1"])[2],
                         "Error response from daemon: No such image: sha256:abc")
        with self.assertRaises(ValueError):
            faults.error_for("ps", name="nonsense")

    def test_parse_calls_rejects_what_the_wrapper_never_writes(self):
        with self.assertRaises(ValueError):
            faults.parse_calls("not a call\n")
        self.assertEqual(faults.parse_calls("1\tdocker\trun\t9\tabsent\tabsent\tabsent\t\n")[0].args, ())

    def test_ownership_reads_after_up_help_are_rollout_before_any_container_mutation(self):
        for kind in ("apply", "rollback"):
            for action in ("fail", "sigkill", "sigterm"):
                with self.subTest(kind=kind, action=action):
                    calls = [replace(c, transaction=(c.transaction[0], kind, "running"))
                             if c.transaction[0] != "absent" else c for c in self.calls()[:16]]
                    call = faults.Call(17, "docker-compose", action, "1", ("config", "--format", "json"),
                                       ("rollout", kind, "running"))
                    calls.append(call)
                    faults.validate_transactions(calls, kind)
                    self.assertEqual(faults.phase_of(calls), ("rollout", call))
                    self.assertFalse(faults.restarted(calls))

    def test_transaction_negative_controls_missing_wrong_late_and_regressing_markers(self):
        good = self.calls()
        faults.validate_transactions(good, "apply")
        for index, header in ((9, ("absent",) * 3), (12, ("rollout", "apply", "running")),
                              (15, ("env_written", "apply", "running")),
                              (16, ("absent",) * 3), (16, ("pulled", "apply", "running")),
                              (16, ("rollout", "rollback", "running")),
                              (16, ("rollout", "apply", "succeeded")),
                              (18, ("pulled", "apply", "running"))):
            with self.subTest(index=index + 1, header=header):
                bad = list(good)
                bad[index] = replace(bad[index], transaction=header)
                with self.assertRaises(AssertionError):
                    faults.validate_transactions(bad, "apply")
        # No up-help / marker, yet a container mutation: cannot be classified as preparation.
        bad = good[:15] + [replace(good[16], transaction=("pulled", "apply", "running"))]
        with self.assertRaisesRegex(AssertionError, "container mutation without rollout"):
            faults.validate_transactions(bad, "apply")
        for words in (("compose", "restart", "app"), ("compose", "run", "app"),
                      ("stop", "container"), ("run", "--rm", "unrelated-image")):
            with self.subTest(unexpected_mutation=words):
                bad = good[:15] + [faults.Call(16, "docker", "run", "1", words,
                                               ("pulled", "apply", "running"))]
                with self.assertRaisesRegex(AssertionError, "container mutation without rollout"):
                    faults.validate_transactions(bad, "apply")
        for stage in ("absent", "pulled"):
            # The late-marker negative control is a READ, not only an `up`.
            bad = good[:16] + [faults.Call(17, "docker-compose", "fail", "1", ("config", "--format", "json"),
                                           (stage, "apply", "running"))]
            with self.subTest(ownership_read_stage=stage), self.assertRaisesRegex(AssertionError, "expected stage"):
                faults.validate_transactions(bad, "apply")

    def test_classifier_refuses_missing_or_malformed_observations(self):
        for header in (("unobserved",) * 3, ("invalid",) * 3, ("prepare", "apply", "running")):
            with self.subTest(header=header), self.assertRaises(AssertionError):
                faults.phase_of([replace(self.calls()[0], action="fail", transaction=header)])

    def test_parse_status(self):
        stdout = ("Installation  Standalone (Compose project p, .env)\n"
                  "  app                          localhost:1/futureagi/standalone:v1.52.0             running v1.51.0  "
                  "(FUTURE_AGI_VERSION=v1.52.0)\n"
                  "  postgres-schema-bootstrap    r/futureagi/future-agi:v1                            running -  "
                  "(FUTURE_AGI_VERSION=unset)\n"
                  "Interrupted   an apply to v1.52.0 stopped at stage env_written; the next apply or rollback "
                  "recovers it\n"
                  "Last run      none\n")
        status = faults.parse_status(stdout)
        self.assertEqual(status.installation, "Standalone (Compose project p, .env)")
        self.assertEqual(status.services["app"], ("localhost:1/futureagi/standalone:v1.52.0", "v1.51.0",
                                                  "FUTURE_AGI_VERSION", "v1.52.0"))
        self.assertEqual(status.services["postgres-schema-bootstrap"][1:], ("-", "FUTURE_AGI_VERSION", "unset"))
        self.assertTrue(status.interrupted.startswith("an apply to v1.52.0 stopped at stage env_written"))
        self.assertEqual(status.last_run, "none")
        self.assertIsNone(status.in_progress)
        down = faults.parse_status("Installation  Standalone (Compose project p, .env)\n"
                                   "  app    r/futureagi/standalone:v1.51.0    (FUTURE_AGI_VERSION=v1.51.0)\n"
                                   "  running: unknown (`docker ps` failed)\nLast run      none\n")
        self.assertEqual(down.services["app"][1:], (None, "FUTURE_AGI_VERSION", "v1.51.0"))
        self.assertEqual(down.unknown, "running: unknown (`docker ps` failed)")


class LoaderTests(unittest.TestCase):
    """bin/update as operators reach it: no Docker needed (it fails before any Docker call)."""

    def test_bin_update_reached_through_a_symlink_to_the_script_finds_its_checkout(self):
        # Linked into a PATH directory (/usr/local/bin/futureagi-update), absolutely, relatively or through a
        # second link, bin/update still runs its own checkout. This checkout has no .env, so status stops there
        # (exit 2), naming the checkout: proof that it looked in the right place.
        with tempfile.TemporaryDirectory(prefix="fagi-update-link-") as tmp:
            links = Path(tmp).resolve() / "bin"  # resolved: a relative link counts from the real directory
            links.mkdir()
            absolute = links / "futureagi-update"
            absolute.symlink_to(WORKTREE / "bin" / "update")
            relative = links / "relative-update"
            relative.symlink_to(os.path.relpath(WORKTREE / "bin" / "update", links))
            second = links / "second-hop"
            second.symlink_to(absolute)
            for link in (absolute, relative, second):
                with self.subTest(link=link.name):
                    result = subprocess.run(
                        [str(link), "status"], capture_output=True, text=True, timeout=120, check=False, cwd=tmp,
                        env={k: v for k, v in os.environ.items() if not k.startswith(("FUTURE_AGI_", "COMPOSE_"))})
                    self.assertNotIn("Traceback", result.stderr)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertEqual(result.stderr, f"bin/update: no .env in {WORKTREE.resolve()}: run bin/update "
                                                    "from the checkout ./bin/install set up\n")


# ================================================================ Docker tests --

@dataclass
class Spec:
    """One command a matrix faults, on one install, and how to judge and undo each run of it."""

    name: str
    co: e2e.Checkout
    flags: List[str]
    command: List[str]
    kind: str  # the receipt's and journal's kind: apply or rollback
    target: str  # the release the command moves to
    stage_only: bool = False
    # The finishing apply of a staged update, or a rollback: its plan-phase message names the pending update.
    env_before: bytes = b""  # the env file before each run (E0)
    env_after: bytes = b""  # the env file once the command succeeded (E1, from the recording)
    recorded: List[faults.Call] = field(default_factory=list)
    prepare: Callable[[], None] = lambda: None
    # Asserts the stack runs `version` healthy, by the env file's selection and the expected digests.
    converged: Callable[[str], None] = lambda version: None
    # From any converged state back to the state each run starts from; asserts it.
    reset: Callable[[], None] = lambda: None
    # The commands that converge after a stop from the restart on: (args, version it converges to, what it says).
    forward: Tuple[List[str], str, str] = ([], "", "")
    back: Optional[Tuple[List[str], str, str]] = None
    source: str = ""  # the release that runs before each run


class FaultE2EBase(unittest.TestCase):
    """What the Docker tests share."""

    maxDiff = None

    def setUp(self) -> None:
        if H is None:
            self.skipTest(SKIP_REASON or "the Docker end-to-end harness did not start")
        started = time.monotonic()
        self.addCleanup(lambda: e2e.log(f"{self._testMethodName} took {time.monotonic() - started:.0f}s"))

    # ----------------------------------------------------------- fixtures --
    def checkout(self, name: str, **kwargs: Any) -> e2e.Checkout:
        assert H is not None
        checkout = H.new_checkout(name, **kwargs)
        self.addCleanup(checkout.down)
        return checkout

    def dist_checkout(self, name: str, **kwargs: Any) -> dist.DistributedCheckout:
        assert H is not None
        checkout = H.new_distributed_checkout(name, **kwargs)
        self.addCleanup(checkout.down)
        return checkout

    def own_registry(self, name: str, *versions: str) -> e2e.PrivateRegistry:
        assert H is not None
        registry = H.private_registry(name, versions)
        self.addCleanup(registry.remove)
        return registry

    def faults(self, name: str, *, stable_environment: bool = False) -> faults.FaultDocker:
        assert H is not None
        directory = H.hook_dir(f"faults-{name}")
        if directory.exists():
            shutil.rmtree(directory)
        injector = faults.FaultDocker(directory, stable_environment=stable_environment)
        self.addCleanup(injector.release_all)
        return injector

    @staticmethod
    def flags(co: e2e.Checkout) -> List[str]:
        if isinstance(co, dist.DistributedCheckout) and co.production:
            return ["--production"] + (["--env-file", co.env_name] if co.env_name != dist.PRODUCTION_ENV else [])
        return []

    # ------------------------------------------------------------ running --
    def spawn(self, co: e2e.Checkout, args: Sequence[str], env: Dict[str, str]) -> e2e.Spawned:
        """bin/update in a session of its own (a kill takes its process group, never the test runner's).

        The cleanup kills its group only while its leader was not reaped yet:
        a group id of a process long gone may name another process by then.
        """

        run = co.spawn(*args, env=dict(env, FAGI_FAULT_JOURNAL=str(co.state_dir / "journal.json")))
        self.addCleanup(lambda: run.alive() and run.kill_group())
        return run

    def spawn_run(self, co: e2e.Checkout, args: Sequence[str], env: Dict[str, str], timeout: float = 1200) -> e2e.Result:
        result = self.spawn(co, args, env).wait(timeout=timeout)
        self.assertNotIn("[harness] killed after", result.stderr, f"bin/update timed out:\n{result.describe()}")
        return result

    def assertExit(self, result: e2e.Result, code: int, *needles: str, stream: str = "stderr") -> None:
        message = f"\n{result.describe()}"
        self.assertEqual(result.code, code, message)
        text = result.output if stream == "both" else getattr(result, stream)
        for needle in needles:
            self.assertIn(needle, text, message)
        self.assertNotIn("Traceback (most recent call last)", result.output, message)

    @staticmethod
    def started(co: e2e.Checkout) -> List[tuple]:
        """Each service container (one-offs left out) with what a restart changes."""

        return sorted((c.service, c.id, c.image_id, c.started_at, c.restart_count) for c in co.containers()
                      if not c.oneoff)

    def snapshot(self, co: e2e.Checkout) -> Dict[str, Any]:
        return {"env": co.env_bytes(), "containers": self.started(co), "receipts": [p.name for p in co.receipt_paths()],
                "journal": co.journal()}

    def persistent_data(self, co: e2e.Checkout) -> str:
        """Read fixture data strictly: a failed probe is not an empty-data success."""

        if isinstance(co, dist.DistributedCheckout):
            volume, files = "bootstrap-data", ["bootstrap-history"]
        else:
            volume, files = "app-data", ["history", "schema"]
        result = e2e.run(["docker", "run", "--rm", "--name", f"{co.project}-data-probe",
                          "-v", f"{co.project}_{volume}:/volume:ro", e2e.BASE_IMAGE,
                          "cat", *[f"/volume/{name}" for name in files]])
        self.assertTrue(result.stdout, "fixture data probe returned no data")
        return result.stdout

    @staticmethod
    def private_state(co: e2e.Checkout) -> Dict[str, tuple]:
        """Fingerprint env/journal/receipts/retention without exposing their contents.

        Ignore the advisory lock and transient signature verification workspace;
        neither is transaction/retention state. Include inodes, modes and mtimes
        to catch a rewrite of identical bytes as well as changed bytes.
        """

        paths = [co.env_path]
        if co.state_dir.is_dir():
            paths += [p for p in co.state_dir.rglob("*") if p.is_file()
                      and p.name != "update.lock"
                      and not any(part.startswith("verify-") for part in p.relative_to(co.state_dir).parts)]
        result = {str(p.relative_to(co.root)): (p.stat().st_ino, p.stat().st_mode, p.stat().st_mtime_ns,
                                               hashlib.sha256(p.read_bytes()).hexdigest()) for p in paths}
        # Empty operation directories are evidence too; no new retention entry
        # or metadata rewrite is allowed while ownership cannot be established.
        retention = co.state_dir / "env-commits"
        if retention.is_dir():
            for directory in [retention, *[p for p in retention.rglob("*") if p.is_dir()]]:
                info = directory.stat()
                result[str(directory.relative_to(co.root))] = (info.st_ino, info.st_mode, info.st_mtime_ns, "directory")
        return result

    # ------------------------------------------------------------- status --
    def assertStatusTells(self, co: e2e.Checkout, *, held: bool = False, holder: Optional[int] = None) -> faults.Status:
        """`bin/update status` says what is true: what runs and what the env file selects (per service), the
        run in progress (held) or the run left unfinished (a journal), and the last receipt."""

        result = co.bin_update("status", *self.flags(co))
        self.assertExit(result, 0, stream="stdout")
        status = faults.parse_status(result.stdout)
        message = result.describe()
        containers = [c for c in co.containers() if not c.oneoff]
        ids = sorted({c.image_id for c in containers if c.running})
        labels: Dict[str, str] = {}
        if ids:
            records = json.loads(e2e.run(["docker", "image", "inspect", *ids]).stdout)
            labels = {r["Id"]: ((r.get("Config") or {}).get("Labels") or {}).get("org.opencontainers.image.version", "")
                      or "?" for r in records}
        running: Dict[str, List[str]] = {}
        for container in containers:
            if container.running:
                running.setdefault(container.service, []).append(labels.get(container.image_id, "?"))
        values = env_values(co.env_bytes())
        self.assertTrue(status.services, message)
        for service, (_, versions, variable, value) in status.services.items():
            with self.subTest(status_of=service):
                self.assertEqual(sorted((versions or "-").split(",")), sorted(running.get(service) or ["-"]), message)
                self.assertEqual(value, values.get(variable) or "unset", message)
        journal = co.journal()
        if held:
            self.assertIsNotNone(status.in_progress, message)
            if holder is not None:
                self.assertIn(f"(pid {holder})", status.in_progress, message)
            if journal:
                self.assertIn(f"(now: {journal['kind']} to {journal['target_version']}, stage {journal['stage']})",
                              status.in_progress, message)
            self.assertIsNone(status.interrupted, message)
        else:
            self.assertIsNone(status.in_progress, message)
            if journal:
                self.assertEqual(status.interrupted,
                                 f"{article(journal['kind'])} to {journal['target_version']} stopped at stage "
                                 f"{journal['stage']}; the next apply or rollback recovers it", message)
            else:
                self.assertIsNone(status.interrupted, message)
        receipts = co.receipts()
        if receipts:
            last = receipts[-1]
            self.assertTrue((status.last_run or "").startswith(
                f"{last['kind']} to {last['target_version']}: {last['status']} ("), message)
            self.assertTrue((status.last_run or "").endswith(last["_path"]), message)
        else:
            self.assertEqual(status.last_run, "none", message)
        return status

    def assertSettled(self, co: e2e.Checkout) -> None:
        """No run holds the lock, no journal and no temporary file is left."""

        self.assertFalse(lock_held(co.state_dir), "a bin/update still holds the lock")
        self.assertIsNone(co.journal())
        self.assertEqual(leftovers(co), [])

    # --------------------------------------------------------- the matrix --
    def record(self, spec: Spec, fd: faults.FaultDocker) -> List[faults.Call]:
        """Run the command once, unfaulted, and keep its calls and the env file it leaves (E1); then reset."""

        spec.prepare()
        result = self.spawn_run(spec.co, [*spec.flags, *spec.command], fd.env())
        self.assertExit(result, 0)
        calls = fd.calls()
        (spec.co.harness.logs_dir / (spec.name + '-recorded.json')).write_text(
            json.dumps([call.__dict__ for call in calls], indent=2) + '\n', encoding='utf-8')
        faults.validate_transactions(calls, spec.kind)
        e2e.log(f"{spec.name}: {len(calls)} Docker calls:\n" + "\n".join(f"    {c}" for c in calls))
        self.assertTrue(calls and all(c.action == "run" for c in calls))
        kinds = [c.kind for c in calls]
        self.assertIn("config-check", kinds)
        self.assertEqual("up" in kinds, not spec.stage_only, kinds)
        spec.recorded = calls
        spec.env_after = spec.co.env_bytes()
        spec.converged(spec.target)
        self.assertSettled(spec.co)
        spec.reset()
        return calls

    def run_matrix(self, spec: Spec, fd: faults.FaultDocker, points: Sequence[faults.FaultPoint]) -> List[str]:
        """Every point, one after the other, from the state spec.reset() leaves; returns a line per point."""

        self.assertTrue(points, f"{spec.name}: no fault points")
        e2e.log(f"{spec.name}: {len(points)} fault points")
        report: List[str] = []
        counter = {"converge": 0}
        for number, point in enumerate(points, 1):
            with self.subTest(point=point.label):
                started = time.monotonic()
                outcome = self.run_point(spec, fd, point, counter)
                line = f"{spec.name} {number}/{len(points)} {point.label}: {outcome} ({time.monotonic() - started:.0f}s)"
                e2e.log(line)
                report.append(line)
        return report

    def run_point(self, spec: Spec, fd: faults.FaultDocker, point: faults.FaultPoint, counter: Dict[str, int]) -> str:
        co = spec.co
        before = self.snapshot(co)
        data_before = self.persistent_data(co)
        self.assertEqual(before["env"], spec.env_before, f"{spec.name}: the point did not start from the reset state")
        spec.prepare()
        words = spec.recorded[point.index - 1].words
        result = self.spawn_run(co, [*spec.flags, *spec.command], fd.point_env(point, words))
        calls = fd.calls()
        # Keep the selected event and exact prefix even when its assertion fails;
        # the next point resets the injector, not this diagnostic evidence.
        (co.harness.logs_dir / f'{spec.name}-{point.index}-{point.action}.json').write_text(
            json.dumps({'point': point.__dict__, 'exit': result.code,
                        'calls': [call.__dict__ for call in calls],
                        'env_before_sha256': hashlib.sha256(before['env']).hexdigest(),
                        'env_after_sha256': hashlib.sha256(co.env_bytes()).hexdigest(),
                        'containers_before': before['containers'], 'containers_after': self.started(co),
                        'receipts_before': before['receipts'],
                        'receipts_after': [p.name for p in co.receipt_paths()]}, indent=2) + '\n', encoding='utf-8')
        faults.validate_transactions(calls, spec.kind)
        phase, faulted = faults.phase_of(calls)
        restart = faults.restart_position(spec.recorded)
        message = (f"\n{point.label} -> phase {phase}\n{result.describe()}\ncalls:\n"
                   + "\n".join(f"    {c}" for c in calls))
        self.assertNotIn("Traceback (most recent call last)", result.output, message)
        if faulted is None:
            # Only a readiness poll after the restart may not come again (the stack was ready sooner).
            self.assertTrue(restart is not None and point.index - 1 > restart, f"fault not reached{message}")
        if faulted is not None and (restart is None or point.index - 1 <= restart):
            # Determinism extends through the post-marker ownership reads, not
            # just the preparation phase. Readiness polling alone may vary.
            self.assertIsNone(faults.prefix_matches(spec.recorded, calls), message)
            self.assertEqual(faulted.index, point.index, message)
        if faulted is not None:
            self.assertEqual(faulted.action, point.action, message)
        outcome = self.judge(spec, point, result, calls, phase, faulted, before, message)
        if not faults.restarted(calls):
            self.assertEqual(self.started(co), before["containers"], message)
            self.assertEqual(self.persistent_data(co), data_before, message)
        self.assertEqual(leftovers(co), [], message)
        left = verify_dirs(co)
        if left:
            # A run killed while it verified the signature leaves its verify-<pid> directory: the next
            # verifying run removes it (a check here, which changes nothing else).
            NOTES.append(f"{spec.name} {point.label}: {', '.join(left)} left in {co.state_dir}, removed by check")
            co.bin_update(*spec.flags, "check", *self.manifest_of(spec), timeout=600)
            self.assertEqual(verify_dirs(co), [], f"{', '.join(left)} outlived the next check{message}")
        state = outcome.split(":")[0]
        if state in ("unchanged", "recovered"):
            self.assertStatusTells(co)
            self.assertSettled(co)
            return outcome
        if state == "done":
            spec.converged(spec.target)
            self.assertStatusTells(co)
            self.assertSettled(co)
            spec.reset()
            return outcome
        # Stopped (or killed) from the restart on: the next run converges, alternately forward and back.
        self.assertStatusTells(co)
        counter["converge"] += 1
        way = spec.forward if (spec.back is None or counter["converge"] % 2) else spec.back
        args, version, says = way
        converge = co.bin_update(*spec.flags, *args, timeout=1200)
        needles = [says]
        if state == "killed":
            needles.append(f"Recovered an interrupted {spec.kind} to {spec.target}: it stopped while containers were "
                           "being replaced (receipt ")
        self.assertExit(converge, 0, *needles)
        spec.converged(version)
        self.assertStatusTells(co)
        self.assertSettled(co)
        spec.reset()
        return f"{outcome}; converged with `{' '.join(args[:2])}` to {version}"

    def judge(self, spec: Spec, point: faults.FaultPoint, result: e2e.Result, calls: List[faults.Call], phase: str,
              faulted: Optional[faults.Call], before: Dict[str, Any], message: str) -> str:
        """Assert what one faulted run left; returns what follows: unchanged, recovered, done, stopped or killed."""

        co = spec.co
        receipts = co.receipts()
        new = [r for r in receipts if Path(r["_path"]).name not in before["receipts"]]
        action = faulted.action if faulted is not None else "none"
        what = f"{spec.kind} to {spec.target}"
        if result.code == 0:
            # A success receipt/env is not proof that a mandatory check ran.
            # Require an explicitly optional call site or its observed fallback,
            # checking ALL faults (an optional first one cannot hide a later one).
            faults.validate_survived_failures(calls, spec.recorded)
            self.assertTrue(action == "none" or action.startswith("fail"), message)
            self.assertEqual(co.env_bytes(), spec.env_after, message)
            self.assertEqual([r["status"] for r in new], ["staged" if spec.stage_only else
                                                          "rolled_back" if spec.kind == "rollback" else "succeeded"],
                             message)
            return f"done: exit 0 ({'not reached' if action == 'none' else 'the failure was survived'})"
        if action.startswith("sigkill"):
            self.assertEqual(result.code, faults.KILLED, message)
            journal = co.journal()
            if phase == "plan":
                # Killed before the env file was touched: nothing to recover.
                self.assertIsNone(journal, message)
                self.assertEqual(self.snapshot(co), before, message)
                return "unchanged: killed before anything changed"
            self.assertIsNotNone(journal, message)
            assert journal is not None
            self.assertEqual((journal["kind"], journal["target_version"], journal["status"]),
                             (spec.kind, spec.target, "running"), message)
            if phase == "prepare":
                self.assertIn(journal["stage"], ("env_written", "pulled"), message)
                # The old release keeps running; the killed run's env file is put back by the next run.
                self.assertEqual(self.started(co), before["containers"], message)
                # Until it is recovered, the env file is what the killed run made of it, and status says so.
                self.assertEqual(co.env_bytes(), spec.env_after, message)
                self.assertStatusTells(co)
                if spec.kind == "apply":
                    # check plans as the recovery will leave things, and changes nothing.
                    check = co.bin_update(*spec.flags, "check", *self.manifest_of(spec))
                    self.assertIn(JOURNAL_NOTE, check.stdout, check.describe())
                    self.assertNotIn("Traceback", check.output)
                    self.assertEqual(co.env_bytes(), spec.env_after, check.describe())
                    self.assertIsNotNone(co.journal())
                recovery = co.bin_update(*spec.flags, "rollback")
                self.assertExit(recovery, 0, f"Recovered an interrupted {what}: it stopped before restarting "
                                             "anything; its version settings were put back exactly (receipt ")
                self.assertEqual(self.snapshot(co)["env"], spec.env_before, message)
                self.assertEqual(self.started(co), before["containers"], message)
                self.assertEqual(co.receipts()[-1]["status"], "interrupted_before_rollout")
                return f"recovered: killed at stage {journal['stage']}, recovered by `rollback`"
            self.assertEqual(journal["stage"], "rollout", message)
            self.assertEqual(co.env_bytes(), spec.env_after, message)
            self.assertEqual(new, [], message)
            if not faults.restarted(calls):
                self.assertEqual(self.started(co), before["containers"], f"killed before `up` ran{message}")
            return "killed: at stage rollout"
        if action.startswith("sigterm"):
            self.assertEqual(result.code, 130, message)
            self.assertIsNone(co.journal(), message)
            if phase == "plan":
                self.assertIn("bin/update: interrupted before anything was changed", result.stderr, message)
                self.assertEqual(self.snapshot(co), before, message)
                return "unchanged: interrupted before anything changed"
            if phase == "prepare":
                self.assertIn(f"{spec.kind} stopped before restarting anything: SIGTERM", result.stderr, message)
                self.assertEqual(co.env_bytes(), spec.env_before, message)
                self.assertEqual(self.started(co), before["containers"], message)
                self.assertEqual([r["status"] for r in new], ["interrupted_before_rollout"], message)
                return "unchanged: exit 130 before the restart"
            self.assertIn(f"{what} stopped while containers were being replaced: SIGTERM", result.stderr, message)
            self.assertEqual(co.env_bytes(), spec.env_after, message)
            self.assertEqual([r["status"] for r in new], ["interrupted_during_rollout"], message)
            if not faults.restarted(calls):
                self.assertEqual(self.started(co), before["containers"], f"interrupted before `up` ran{message}")
            return "stopped: exit 130 from the restart on"
        self.assertTrue(action.startswith("fail"), message)
        if phase == "plan":
            self.assertIn(result.code, (2, 3), message)
            self.assertTrue(result.stderr.rstrip().splitlines()[-1].startswith("bin/update: "), message)
            self.assertEqual(self.snapshot(co), before, message)
            if point.error == "daemon-down":
                self.assertIn(DAEMON_HINT, result.stderr, message)
            return f"unchanged: exit {result.code} before anything changed"
        if phase == "prepare":
            self.assertEqual(result.code, 4, message)
            self.assertIn(f"{spec.kind} stopped before restarting anything: ", result.stderr, message)
            self.assertEqual(co.env_bytes(), spec.env_before, message)
            self.assertEqual(self.started(co), before["containers"], message)
            self.assertEqual([r["status"] for r in new], ["failed_before_rollout"], message)
            self.assertIn(f"Receipt: {new[0]['_path']}", result.stderr, message)
            return "unchanged: exit 4 before the restart"
        self.assertEqual(result.code, 5, message)
        self.assertIn(f"{what} stopped while containers were being replaced: ", result.stderr, message)
        self.assertIn(f"The env file selects {spec.target}.", result.stderr, message)
        self.assertEqual(co.env_bytes(), spec.env_after, message)
        self.assertEqual([r["status"] for r in new], ["failed_during_rollout"], message)
        if not faults.restarted(calls):
            self.assertEqual(self.started(co), before["containers"], f"failed before `up` ran{message}")
        return "stopped: exit 5 from the restart on"

    @staticmethod
    def manifest_of(spec: Spec) -> List[str]:
        words = spec.command
        return words[words.index("--manifest"):words.index("--manifest") + 2] if "--manifest" in words else []




class StandaloneMatrixE2E(FaultE2EBase):
    """The Standalone apply and rollback sequences, every call failed and killed (FAULTS_SCOPE=full)."""

    # ------------------------------------------------------------- specs --
    def standalone_spec(self, name: str, *, rollback: bool = False) -> Spec:
        """A v1.51.0 install with a registry of its own (each run really pulls: prepare() removes the tag the
        command pulls), and the command: `apply` of v1.52.0, or (rollback) `rollback --yes` of that apply."""

        assert H is not None
        registry = self.own_registry(name, V51, V52)
        release = H.release(V52, registry=registry.host)
        images = {version: H.image_ref(version, registry=registry.host) for version in (V51, V52)}
        co = self.checkout(name, registry=registry.host)
        co.install()
        apply = ["apply", "--manifest", str(release.manifest), "--allow-no-backup"]
        rollback_yes = ["rollback", "--yes"]

        def converged(version: str) -> None:
            app = co.app()
            self.assertTrue(app.running, f"app is {app.status}")
            self.assertEqual(app.image, images[version])
            info = co.image_info(app.image_id)
            self.assertEqual(info["version"], version)
            self.assertIn(f"{registry.host}/{e2e.REPOSITORY}@{registry.digests[version]}", info["repo_digests"])
            self.assertEqual(env_values(co.env_bytes()).get(VERSION_KEY), version)
            co.wait_healthy(timeout=180)

        def to(version: str) -> None:
            if env_values(co.env_bytes()).get(VERSION_KEY) == version and co.running_version() == version:
                return
            if version == V51:
                self.assertExit(co.bin_update(*rollback_yes, timeout=1200), 0, f"Rolled back to {V51}")
            else:
                self.assertExit(co.bin_update(*apply, timeout=1200), 0, f"Updated to {V52}")

        if rollback:
            to(V52)
            spec = Spec(name=name, co=co, flags=[], command=rollback_yes, kind="rollback", target=V51, source=V52,
                        prepare=lambda: e2e.run(["docker", "rmi", images[V51]], check=False),
                        forward=(rollback_yes, V51, f"Rolled back to {V51}. Receipt: "))
        else:
            spec = Spec(name=name, co=co, flags=[], command=apply, kind="apply", target=V52, source=V51,
                        prepare=lambda: e2e.run(["docker", "rmi", images[V52]], check=False),
                        forward=(apply, V52, f"Updated to {V52}. Receipt: "),
                        back=(rollback_yes, V51, f"Rolled back to {V51}. Receipt: "))
        spec.env_before = co.env_bytes()

        def reset() -> None:
            to(spec.source)
            self.assertEqual(co.env_bytes(), spec.env_before)
            converged(spec.source)
            self.assertSettled(co)

        spec.converged = converged
        spec.reset = reset
        return spec

    def matrix(self, name: str, *, rollback: bool = False, actions: Sequence[str],
               before_restart: Optional[bool] = None, extended: bool = False) -> None:
        spec = self.standalone_spec(name, rollback=rollback)
        fd = self.faults(name)
        calls = self.record(spec, fd)
        if extended:
            positions = [i for i, call in enumerate(calls) if call.kind in faults.STATE_CHANGING]
            points = faults.points_at(calls, positions, actions)
        elif faults.scope() == "full":
            points = faults.full_matrix(calls, actions, before_restart=before_restart)
        else:
            restart = faults.restart_position(calls)
            positions = [i for i in faults.state_positions(calls)
                         if before_restart is None or restart is None or (i < restart) == before_restart]
            points = faults.points_at(calls, positions, actions)
        self.run_matrix(spec, fd, points)

    # ------------------------------------------------------------- tests --
    @e2e.slow
    def test_11_apply_failures_before_the_restart_change_nothing(self):
        self.matrix("sa-apply-fail-pre", actions=["fail"], before_restart=True)

    @e2e.slow
    def test_12_apply_killed_before_the_restart_is_put_back_by_the_next_run(self):
        self.matrix("sa-apply-kill-pre", actions=["sigkill"], before_restart=True)

    @e2e.slow
    def test_13_apply_failures_from_the_restart_on_converge(self):
        self.matrix("sa-apply-fail-post", actions=["fail"], before_restart=False)

    @e2e.slow
    def test_14_apply_killed_from_the_restart_on_converges(self):
        self.matrix("sa-apply-kill-post", actions=["sigkill"], before_restart=False)

    @e2e.slow
    def test_15_apply_state_changes_failed_or_killed_after_they_ran_and_sigterm(self):
        self.matrix("sa-apply-after", actions=["fail-after", "sigkill-after", "sigterm", "sigterm-after"],
                    extended=True)

    @e2e.slow
    def test_16_rollback_failures_and_kills_before_the_restart(self):
        self.matrix("sa-rollback-pre", rollback=True, actions=["fail", "sigkill"], before_restart=True)

    @e2e.slow
    def test_17_rollback_failures_and_kills_from_the_restart_on(self):
        self.matrix("sa-rollback-post", rollback=True, actions=["fail", "sigkill"], before_restart=False)

    @e2e.slow
    def test_18_the_pull_reports_registry_full_disk_and_missing_image_failures(self):
        spec = self.standalone_spec("sa-pull-errors")
        fd = self.faults("sa-pull-errors")
        calls = self.record(spec, fd)
        position = next(i for i, call in enumerate(calls) if call.kind == "pull")
        call = calls[position]
        index = faults.occurrence(calls, position)
        points = [faults.FaultPoint(call.index, call.kind, call.glob, index, "fail", name)
                  for name in faults.PULL_ERRORS]
        self.run_matrix(spec, fd, points)


class DistributedMatrixE2E(FaultE2EBase):
    """Distributed and production sequences: the state-changing calls and their neighbours, failed and killed."""

    def dist_converged(self, co: dist.DistributedCheckout, version: str) -> None:
        assert H is not None
        stack = co.stack()
        infos = co.images_info(stack[s].image_id for s in DEFAULT_SERVICES)
        for service in DEFAULT_SERVICES:
            component = dist.SERVICES[service]
            container = stack[service]
            with self.subTest(converged=service, version=version):
                self.assertTrue(container.running, f"{service} is {container.status}")
                self.assertEqual(container.image, H.dist_image(component, version))
                self.assertEqual(infos[container.image_id]["version"], version)
                self.assertIn(f"{H.registry}/{dist.COMPONENTS[component][0]}@{H.dist_digests[(component, version)]}",
                              infos[container.image_id]["repo_digests"])
        bootstrap = co.inspect(stack[dist.BOOTSTRAP].id)
        self.assertEqual((bootstrap["State"]["Status"], bootstrap["State"]["ExitCode"]), ("exited", 0))
        self.assertEqual(bootstrap["Config"]["Image"], H.dist_image("backend", version))
        values = env_values(co.env_bytes())
        self.assertEqual({key: values.get(key) for key in ACTIVE_KEYS}, {key: version for key in ACTIVE_KEYS})
        co.wait_healthy(timeout=180)

    def staged(self, co: dist.DistributedCheckout, version: str, running: str) -> None:
        """The env file selects `version`, its images are on the host by their signed digests, `running` runs."""

        assert H is not None
        values = env_values(co.env_bytes())
        self.assertEqual({key: values.get(key) for key in dist.VERSION_KEYS}, {key: version for key in dist.VERSION_KEYS})
        refs = {s: H.dist_image(dist.SERVICES[s], version) for s in DEFAULT_SERVICES}
        infos = co.images_info(refs.values())
        for service, ref in refs.items():
            component = dist.SERVICES[service]
            self.assertIn(f"{H.registry}/{dist.COMPONENTS[component][0]}@{H.dist_digests[(component, version)]}",
                          infos[ref]["repo_digests"])
        stack = co.stack()
        infos = co.images_info(stack[s].image_id for s in DEFAULT_SERVICES)
        for service in DEFAULT_SERVICES:
            self.assertTrue(stack[service].running, service)
            self.assertEqual(infos[stack[service].image_id]["version"], running, service)
        co.wait_healthy(timeout=180)

    def remove_images(self, version: str) -> None:
        assert H is not None
        refs = sorted({H.dist_image(dist.SERVICES[s], version) for s in DEFAULT_SERVICES})
        e2e.run(["docker", "rmi", *refs], check=False)

    @e2e.slow
    def test_20_distributed_apply_faults_at_the_state_changes_and_their_neighbours(self):
        assert H is not None
        co = self.dist_checkout("dist-matrix", versions=dist.pinned(V51, runner=None))
        co.install()
        manifest = ["--manifest", str(H.dist_release(V52).manifest)]
        apply = ["apply", *manifest, "--allow-no-backup"]
        rollback_yes = ["rollback", "--yes"]

        def reset() -> None:
            if env_values(co.env_bytes()).get(VERSION_KEY) != V51 or co.service("backend").image != H.dist_image(
                    "backend", V51):
                self.assertExit(co.bin_update(*rollback_yes, timeout=1200), 0, f"Rolled back to {V51}")
            self.assertEqual(co.env_bytes(), spec.env_before)
            self.dist_converged(co, V51)
            self.assertSettled(co)

        spec = Spec(name="dist-matrix", co=co, flags=[], command=apply, kind="apply", target=V52, source=V51,
                    env_before=co.env_bytes(), prepare=lambda: self.remove_images(V52),
                    converged=lambda version: self.dist_converged(co, version), reset=reset,
                    forward=(apply, V52, f"Updated to {V52}. Receipt: "),
                    back=(rollback_yes, V51, f"Rolled back to {V51}. Receipt: "))
        with self.subTest("COMPOSE_FILE exported in the shell as .env records it is not refused"):
            result = co.bin_update("check", *manifest, env={"COMPOSE_FILE": dist.DISTRIBUTED_FILE})
            self.assertExit(result, 0, f"  Installation  distributed (Compose project {co.project}, .env)",
                            "  Decision      update available", stream="stdout")
        fd = self.faults("dist-matrix")
        calls = self.record(spec, fd)
        self.run_matrix(spec, fd, faults.state_matrix(calls))

    def production_spec(self, name: str, version: str, *, finish_recorder=None) -> Tuple[Spec, Spec, Callable[[], None]]:
        """One test's release: stage, separately approved initialization, then finish.

        A recorded finish must use the SAME shell environment as its stage;
        fault selection is out-of-band, not a changed installation fingerprint.
        """

        assert H is not None
        co = self.dist_checkout(name, production=True)
        co.install()
        flags = ["--production"]
        manifest = ["--manifest", str(H.dist_release(version).manifest)]
        stage = ["apply", "--stage", *manifest, "--allow-no-backup"]
        finish = ["apply", *manifest, "--allow-no-backup"]
        approved_finish = [*finish, "--acknowledge-stale-stage"]
        rollback_yes = ["rollback", "--yes"]
        original = co.env_bytes()

        def running() -> str:
            return co.images_info([co.service("backend").image_id])[co.service("backend").image_id]["version"]

        def back_to_v51() -> None:
            if running() != V51:
                self.assertExit(co.bin_update(*flags, *rollback_yes, timeout=1200), 0, f"Rolled back to {V51}")
            elif co.env_bytes() != original:
                self.assertExit(co.bin_update(*flags, *rollback_yes, timeout=1200), 0,
                                f"Rolled back to {V51} without restarting anything")
            self.assertEqual(co.env_bytes(), original)
            self.dist_converged(co, V51)
            self.assertSettled(co)

        def staged_and_initialized() -> None:
            if finish_recorder is None:
                result = co.bin_update(*flags, *stage, timeout=1200)
                environment = None
            else:
                result = self.spawn_run(co, [*flags, *stage], finish_recorder.env())
                environment = dict(finish_recorder.env(), FAGI_FAULT_JOURNAL=str(co.state_dir / "journal.json"))
            self.assertExit(result, 0, f"Staged {version}")
            co.initialize()
            self.assertEqual(co.bootstrap_history()[-1:], [f"bootstrap {version} backend migrate --noinput"])
            e2e.assert_stage_acknowledgment_required(self, co, *flags, *finish, env=environment)

        def stage_converged(target: str) -> None:
            if target == version:
                self.staged(co, version, V51)
            else:
                self.dist_converged(co, V51)

        stage_spec = Spec(name=f"{name}-stage", co=co, flags=flags, command=stage, kind="apply", target=version,
                          source=V51, stage_only=True, env_before=original,
                          prepare=lambda: self.remove_images(version), converged=stage_converged, reset=back_to_v51)

        def finish_reset() -> None:
            if running() == version or co.env_bytes() == original:
                back_to_v51()
                staged_and_initialized()
            self.assertEqual(co.env_bytes(), finish_spec.env_before)
            self.staged(co, version, V51)
            self.assertSettled(co)

        def finish_converged(target: str) -> None:
            self.dist_converged(co, target)
            if target == version:
                bootstrap = co.inspect(co.service(dist.BOOTSTRAP).id)
                self.assertIn("--check", [*(bootstrap["Config"].get("Entrypoint") or []),
                                          *(bootstrap["Config"].get("Cmd") or [])])

        finish_spec = Spec(name=f"{name}-finish", co=co, flags=flags, command=approved_finish, kind="apply", target=version,
                           source=V51, converged=finish_converged, reset=finish_reset,
                           # Recovery after rollout resumes an interrupted apply,
                           # NOT a staged one; acknowledgment must not be added.
                           forward=(finish, version, f"Updated to {version}. Receipt: "),
                           back=(rollback_yes, V51, f"Rolled back to {V51}. Receipt: "))
        return stage_spec, finish_spec, staged_and_initialized

    @e2e.slow
    def test_21_production_stage_faults_and_a_failing_backup_hook(self):
        assert H is not None
        stage_spec, _, _ = self.production_spec("prod-stage", PROD_STAGE)
        co = stage_spec.co
        before = self.snapshot(co)

        with self.subTest("a backup hook that exits non-zero stops the stage before anything changes"):
            directory = H.hook_dir("faults-prod-hook")
            log = directory / "ran"
            hook = H.write_script(directory / "backup", f'echo "$FUTURE_AGI_UPDATE_ENV_FILE" >> {shlex.quote(str(log))}\n'
                                                       'echo "e2e: the backup target is full" >&2\nexit 3\n')
            command = [arg for arg in stage_spec.command if arg != "--allow-no-backup"]
            result = co.bin_update(*stage_spec.flags, *command, "--backup-command", str(hook), timeout=1200)
            self.assertExit(result, 4, "the backup hook exited with code 3 (output above); nothing was changed",
                            "e2e: the backup target is full")
            self.assertNotIn("Pulling", result.stderr)
            self.assertEqual(log.read_text(encoding="utf-8").split("\n")[:-1], [str(co.env_path)])
            self.assertEqual(self.snapshot(co), before)
            self.assertSettled(co)

        with self.subTest("the env file's project and compose files win over the shell's"):
            assert H is not None
            shell = {"COMPOSE_PROJECT_NAME": f"{H.prefix}not-this-install", "COMPOSE_FILE": "docker-compose.yml"}
            manifest = stage_spec.command[stage_spec.command.index("--manifest"):][:2]
            result = co.bin_update("--production", "check", "--format", "json", *manifest, env=shell)
            self.assertExit(result, 0, stream="stdout")
            plan = json.loads(result.stdout)
            self.assertEqual((plan["decision"], plan["installation"]["project"], plan["installation"]["mode"]),
                             ("update_available", co.project, "production"))
            self.assertEqual(self.snapshot(co), before)

        fd = self.faults("prod-stage")
        calls = self.record(stage_spec, fd)
        self.run_matrix(stage_spec, fd, faults.state_matrix(calls))

    @e2e.slow
    def test_22_production_finish_faults_from_the_staged_update(self):
        fd = self.faults("prod-finish", stable_environment=True)
        _, finish_spec, staged_and_initialized = self.production_spec("prod-finish", PROD_FINISH, finish_recorder=fd)
        staged_and_initialized()
        finish_spec.env_before = finish_spec.co.env_bytes()
        calls = self.record(finish_spec, fd)
        self.run_matrix(finish_spec, fd, faults.state_matrix(calls))

    @e2e.slow
    def test_23_a_release_whose_image_differs_from_the_signed_one_for_one_service_is_refused(self):
        """MISMATCH's serving image is pushed again after its manifest was signed, so only that service's tag
        resolves to another digest. The apply pulls every image, refuses before restarting anything (exit 4),
        and puts the env file back byte for byte; once the registry serves the signed image again, the next
        apply pulls only serving and finishes."""

        assert H is not None
        co = self.dist_checkout("one-digest", versions=dist.pinned(V51, runner=None))
        co.install()
        release = H.dist_release(MISMATCH)
        serving_signed = H.dist_digests[("serving", MISMATCH)]
        self.assertIn(serving_signed, json.dumps(release.data()))  # the digest the manifest signs
        before = self.snapshot(co)
        hook_log = H.hook_dir("faults-one-digest") / "ran"
        hook = H.write_script(H.hook_dir("faults-one-digest") / "backup", f"echo ran >> {shlex.quote(str(hook_log))}\n")
        apply = ["apply", "--manifest", str(release.manifest), "--backup-command", str(hook)]
        result = co.bin_update("check", "--manifest", str(release.manifest))
        self.assertExit(result, 0, "  Decision      update available", "  Signature     verified", stream="stdout")

        # The registry's serving tag moves to another image after signing (MODE differs, so its digest does).
        tampered = H.push_component("serving", MISMATCH, "tampered")
        self.addCleanup(H.push_component, "serving", MISMATCH, "ok")
        self.assertNotEqual(tampered, serving_signed)
        serving = H.dist_image("serving", MISMATCH)
        result = co.bin_update(*apply, timeout=1200)
        self.assertExit(result, 4, "apply stopped before restarting anything: ",
                        f"{serving} resolves to {tampered} on this host, not the signed {serving_signed}. Refusing to "
                        "start it", "Nothing was changed; the previous versions are configured and running")
        self.assertEqual(hook_log.read_text(encoding="utf-8").split(), ["ran"])  # the backup ran first
        after = self.snapshot(co)
        self.assertEqual((after["env"], after["containers"], after["journal"]),
                         (before["env"], before["containers"], None))
        receipt = co.receipts()[-1]
        self.assertEqual((receipt["status"], receipt["stage"]), ("failed_before_rollout", "pulled"))
        self.assertSettled(co)
        self.assertStatusTells(co)
        # Every other image was pulled and carries its signed digest; serving holds the tampered one.
        others = sorted({H.dist_image(dist.SERVICES[s], MISMATCH) for s in DEFAULT_SERVICES} - {serving})
        infos = co.images_info([*others, serving])
        for ref in others:
            component = next(c for c in dist.COMPONENTS if H.dist_image(c, MISMATCH) == ref)
            self.assertIn(f"{H.registry}/{dist.COMPONENTS[component][0]}@{H.dist_digests[(component, MISMATCH)]}",
                          infos[ref]["repo_digests"])
        self.assertIn(f"{H.registry}/futureagi/serving@{tampered}", infos[serving]["repo_digests"])

        # The registry serves the signed image again: only serving is pulled, the rest is verified on the host.
        self.assertEqual(H.push_component("serving", MISMATCH, "ok"), serving_signed)
        result = co.bin_update(*apply, timeout=1200)
        self.assertExit(result, 0, f"Pulling {serving}\n", f"Updated to {MISMATCH}. Receipt: ")
        self.assertRegex(result.stderr, "Already on this host with the expected digest, not pulled: [^\n]*"
                         + re.escape(H.dist_image("backend", MISMATCH)))
        self.dist_converged(co, MISMATCH)
        self.assertStatusTells(co)
        self.assertSettled(co)


class ScenariosE2E(FaultE2EBase):
    """Corners of a real host that the other suites do not reach."""

    def standalone(self, name: str, **kwargs: Any) -> Tuple[e2e.Checkout, e2e.PrivateRegistry, List[str]]:
        """A v1.51.0 install on a registry of its own, and the arguments of its apply to v1.52.0."""

        assert H is not None
        registry = self.own_registry(name, V51, V52)
        release = H.release(V52, registry=registry.host)
        co = self.checkout(name, registry=registry.host, **kwargs)
        co.install()
        return co, registry, ["apply", "--manifest", str(release.manifest), "--allow-no-backup"]

    def assertRuns(self, co: e2e.Checkout, registry: e2e.PrivateRegistry, version: str) -> None:
        assert H is not None
        app = co.app()
        self.assertTrue(app.running, app.status)
        self.assertEqual(app.image, H.image_ref(version, registry=registry.host))
        info = co.image_info(app.image_id)
        self.assertEqual(info["version"], version)
        self.assertIn(f"{registry.host}/{e2e.REPOSITORY}@{registry.digests[version]}", info["repo_digests"])
        self.assertEqual(env_values(co.env_bytes()).get(VERSION_KEY), version)
        co.wait_healthy(timeout=180)

    @e2e.slow
    def test_30_status_tells_the_truth_at_every_docker_call_of_an_apply(self):
        """Every Docker call of an apply is held in turn; at each, status says what runs, what the env file
        selects, whether a run holds the lock (and its stage), and never calls a live run interrupted. At the
        restart, apply and rollback are refused (exit 2) and check is blocked (exit 3), changing nothing."""

        co, registry, apply = self.standalone("sweep")
        original = co.env_bytes()
        fd = self.faults("sweep")
        run = self.spawn(co, apply, fd.env(at=1, action="pause", times="all"))
        checked, stages, refused = 0, [], False
        while True:
            e2e.wait_for(lambda: len(fd.paused()) > checked or not run.alive(), timeout=900, interval=0.2,
                         what="bin/update's next Docker call")
            paused = fd.paused()
            if len(paused) == checked:
                break
            index = paused[checked]
            checked += 1
            call = fd.calls()[index - 1]
            journal = co.journal()
            held = lock_held(co.state_dir)
            stages.append((call.kind, held, (journal or {}).get("stage")))
            with self.subTest(call=str(call)):
                if journal is None:
                    self.assertEqual(co.env_bytes(), original)
                else:
                    self.assertTrue(held, "a journal without the lock while the run is alive")
                    self.assertEqual(co.env_bytes(), with_version(original, V52))
                self.assertStatusTells(co, held=held, holder=run.pid if held else None)
                if call.kind == "up" and not refused:
                    refused = True
                    busy = f"another bin/update is running in this checkout (pid {run.pid})"
                    self.assertExit(co.bin_update(*apply), 2, busy)
                    self.assertExit(co.bin_update("rollback", "--yes"), 2, busy)
                    self.assertExit(co.bin_update("check", *apply[1:3]), 3,
                                    f"a bin/update run is in progress (pid {run.pid})", stream="stdout")
                    self.assertEqual((co.journal(), co.env_bytes()), (journal, with_version(original, V52)))
            fd.release(index)
        result = run.wait(timeout=900)
        self.assertExit(result, 0, f"Updated to {V52}. Receipt: ")
        e2e.log("sweep: " + ", ".join(f"{kind}{'*' if held else ''}:{stage or '-'}" for kind, held, stage in stages))
        self.assertTrue(refused, f"no `up` call was held: {stages}")
        self.assertEqual(checked, len(fd.calls()))
        # The lock is taken after the release was verified, before planning: held from then to the end.
        held_from = next(i for i, (_, held, _) in enumerate(stages) if held)
        self.assertTrue(all(held for _, held, _ in stages[held_from:]), stages)
        self.assertEqual([stage for _, _, stage in stages if stage][:1], ["env_written"])
        self.assertIn("rollout", [stage for _, _, stage in stages])
        self.assertRuns(co, registry, V52)
        self.assertStatusTells(co)
        self.assertSettled(co)

    @e2e.slow
    def test_31_docker_daemon_unavailable(self):
        """With the daemon down (Compose still reads its files), status still reads the env file, receipts
        and an interrupted run's journal (exit 2); check, apply and rollback stop with exit 2 and the hint,
        changing nothing. Recovery must first prove ownership: it is refused
        without Docker, then restores the exact env once ownership is available."""

        assert H is not None
        co, registry, apply = self.standalone("daemon")
        image52 = H.image_ref(V52, registry=registry.host)
        original = co.env_bytes()
        fd = self.faults("daemon")
        hook_log = H.hook_dir("daemon-backup") / "ran"
        hook = H.write_script(hook_log.parent / "backup", f'echo backup >> {shlex.quote(str(hook_log))}\n')
        apply = [arg for arg in apply if arg != "--allow-no-backup"] + ["--backup-command", str(hook)]

        def down() -> Dict[str, str]:
            return fd.env(at=1, action="fail", times="all", stderr=faults.DAEMON_DOWN,
                          skip=faults.skip_without_daemon())

        before = self.snapshot(co)
        result = co.bin_update("status", env=down())
        self.assertExit(result, 2, DAEMON_HINT)
        status = faults.parse_status(result.stdout)
        self.assertTrue((status.unknown or "").startswith("running: unknown (`docker ps` failed"), result.describe())
        self.assertEqual(status.services["app"][1:], (None, VERSION_KEY, V51))
        self.assertEqual(status.last_run, "none")
        for args in (["check", *apply[1:3]], apply):
            with self.subTest(command=args[0]):
                self.assertExit(co.bin_update(*args, env=down()), 2, DAEMON_HINT)
                self.assertEqual(self.snapshot(co), before)
        self.assertTrue(any(c.action == "fail" for c in fd.calls()))

        with self.subTest("a run killed at its pull, then the daemon goes away"):
            e2e.run(["docker", "rmi", image52], check=False)
            killed = self.spawn_run(co, apply, fd.env(at=1, match="compose*pull *", action="sigkill"))
            self.assertEqual(killed.code, faults.KILLED, killed.describe())
            self.assertEqual((co.journal() or {}).get("stage"), "env_written")
            pending = self.snapshot(co)
            retained = self.private_state(co)
            data = self.persistent_data(co)
            self.assertEqual(hook_log.read_text(), "backup\n")
            result = co.bin_update("status", env=down())
            self.assertExit(result, 2, DAEMON_HINT)
            status = faults.parse_status(result.stdout)
            self.assertEqual(status.interrupted, f"an apply to {V52} stopped at stage env_written; the next apply or "
                                                 "rollback recovers it")
            self.assertEqual(status.services["app"][3], V52)  # what the env file selects now
            self.assertEqual(self.snapshot(co), pending)
            self.assertEqual(self.private_state(co), retained)
            # Both mutating entry points must prove ownership before recovery,
            # even rollback without --yes. A refused probe cannot run a backup.
            for args in (apply, ["rollback"], ["rollback", "--yes"]):
                with self.subTest(refused_recovery=args[0], yes="--yes" in args):
                    result = co.bin_update(*args, env=down())
                    self.assertExit(result, 2, DAEMON_HINT)
                    self.assertNotIn("Recovered an interrupted", result.output)
                    self.assertEqual(self.snapshot(co), pending)
                    self.assertEqual(self.private_state(co), retained)
                    self.assertEqual(self.persistent_data(co), data)
                    self.assertEqual(hook_log.read_text(), "backup\n")
                    self.assertFalse(lock_held(co.state_dir))

        with self.subTest("Docker is back: ownership permits exact recovery, without a restart or backup"):
            recovery = co.bin_update("rollback")
            self.assertExit(recovery, 0, f"Recovered an interrupted apply to {V52}: it stopped before restarting "
                                        "anything; its version settings were put back exactly (receipt ")
            self.assertEqual(co.env_bytes(), original)
            self.assertEqual(self.started(co), before["containers"])
            self.assertEqual(self.persistent_data(co), data)
            self.assertEqual(hook_log.read_text(), "backup\n")
            self.assertEqual(co.receipts()[-1]["status"], "interrupted_before_rollout")
            self.assertSettled(co)
            self.assertStatusTells(co)

        with self.subTest("Docker is back: the next apply finishes the update"):
            self.assertExit(co.bin_update(*apply, timeout=1200), 0, f"Updated to {V52}. Receipt: ")
            self.assertEqual(hook_log.read_text(), "backup\nbackup\n")
            self.assertRuns(co, registry, V52)
        with self.subTest("rollback with the daemon down changes nothing"):
            before = self.snapshot(co)
            self.assertExit(co.bin_update("rollback", "--yes", env=down()), 2, DAEMON_HINT)
            self.assertEqual(self.snapshot(co), before)
            self.assertStatusTells(co)

    @e2e.slow
    def test_32_an_env_file_or_checkout_that_cannot_be_written_changes_nothing(self):
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("root writes read-only directories")
        assert H is not None
        co, registry, apply = self.standalone("readonly")
        original = co.env_bytes()
        data = self.persistent_data(co)
        fd = self.faults("readonly")

        def refused_commit(before: Dict[str, Any]) -> None:
            result = self.spawn_run(co, apply, fd.env())
            # An EnvCommitError is deliberately fail-closed, even when this
            # fixture proves the exchange did not happen. No implicit inverse
            # or success/failure receipt may erase the retained evidence.
            self.assertExit(result, 2, "cannot write ", "env commit conflict or indeterminate result",
                            "no automatic restoration or cleanup was attempted", f"Kept {co.state_dir / 'journal.json'}")
            self.assertEqual(co.env_bytes(), original)
            self.assertEqual(self.started(co), before["containers"])
            self.assertEqual(self.persistent_data(co), data)
            self.assertEqual([p.name for p in co.receipt_paths()], before["receipts"])
            journal = co.journal()
            self.assertIsNotNone(journal)
            self.assertEqual((journal["kind"], journal["stage"], journal["status"]), ("apply", "prepare", "running"))
            self.assertFalse(lock_held(co.state_dir))
            self.assertFalse(any(c.kind in faults.STATE_CHANGING for c in fd.calls()))

        def recover_unchanged(before: Dict[str, Any]) -> None:
            # Restore directory access before recovery, without retargeting the
            # env alias. The real recovery must prove the unexchanged operation.
            result = co.bin_update("rollback")
            self.assertExit(result, 0, f"Recovered an interrupted apply to {V52}: it stopped before restarting "
                                      "anything; its version settings were put back exactly (receipt ")
            self.assertEqual(co.env_bytes(), original)
            self.assertEqual(self.started(co), before["containers"])
            self.assertEqual(self.persistent_data(co), data)
            self.assertEqual(co.receipts()[-1]["status"], "interrupted_before_rollout")
            self.assertSettled(co)
            self.assertStatusTells(co)

        with self.subTest(".env is a symlink into a directory bin/update cannot write"):
            store = H.run_dir / "env-store" / co.project
            store.mkdir(parents=True)
            shutil.move(str(co.env_path), str(store / ".env"))
            co.env_path.symlink_to(store / ".env")
            store.chmod(0o555)
            self.addCleanup(store.chmod, 0o755)
            before = self.snapshot(co)
            refused_commit(before)
            self.assertTrue(co.env_path.is_symlink())
            self.assertEqual(sorted(p.name for p in store.iterdir()), [".env"])
            self.assertStatusTells(co)
            store.chmod(0o755)
            recover_unchanged(before)

        with self.subTest("the checkout directory is read-only (the state directory is not)"):
            # Keep the prior symlink target stable. Moving it back would change
            # the receipt's env-file identity; these are separate installations,
            # not permission to make status adopt the earlier fixture's receipt.
            co, registry, apply = self.standalone("readonly-root")
            original = co.env_bytes()
            data = self.persistent_data(co)
            fd = self.faults("readonly-root")
            self.assertExit(co.bin_update("check", *apply[1:3]), 0, stream="stdout")
            co.root.chmod(0o555)
            self.addCleanup(co.root.chmod, 0o755)
            before = self.snapshot(co)
            refused_commit(before)
            entries = {str(p): (p.stat().st_ino, hashlib.sha256(p.read_bytes()).hexdigest())
                       for name in ("candidate", "slot") for p in co.state_dir.glob(f"env-commits/*/*/{name}")}
            self.assertTrue(entries, "the prepared native exchange must retain its candidate and slot")
            self.assertStatusTells(co)
            co.root.chmod(0o755)
            recover_unchanged(before)
            self.assertEqual({path: (Path(path).stat().st_ino, hashlib.sha256(Path(path).read_bytes()).hexdigest())
                              for path in entries}, entries, "recovery must not remove retained inodes")

        with self.subTest("the checkout directory is read-only and holds no state directory yet"):
            shutil.rmtree(co.state_dir)
            co.root.chmod(0o555)
            before = self.snapshot(co)
            for args in (["check", *apply[1:3]], apply):
                with self.subTest(command=args[0]):
                    self.assertExit(co.bin_update(*args), 2, f"cannot create {co.state_dir}")
            result = co.bin_update("status")
            self.assertExit(result, 0, "Last run      none", stream="stdout")
            self.assertFalse(co.state_dir.exists())
            self.assertEqual(self.snapshot(co), before)
            co.root.chmod(0o755)

        # A read-only .env (0400) in a writable directory: replaced atomically, and it stays 0400.
        co.env_path.chmod(0o400)
        self.addCleanup(lambda: co.env_path.exists() and co.env_path.chmod(0o600))
        self.assertExit(co.bin_update(*apply, timeout=1200), 0, f"Updated to {V52}. Receipt: ")
        self.assertEqual(co.env_bytes(), with_version(original, V52))
        self.assertEqual(stat.S_IMODE(co.env_path.stat().st_mode), 0o400)
        self.assertRuns(co, registry, V52)
        self.assertSettled(co)

    @e2e.slow
    def test_33_an_unusable_state_directory_stops_every_command_that_writes_it(self):
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("root reads and writes whatever the modes say")
        co, _, apply = self.standalone("statedir")
        check = ["check", *apply[1:3]]

        with self.subTest("mode 0500: bin/update gives its own directory back its 0700"):
            self.assertExit(co.bin_update(*check), 0, stream="stdout")
            co.state_dir.chmod(0o500)
            self.addCleanup(lambda: co.state_dir.is_dir() and co.state_dir.chmod(0o700))
            self.assertExit(co.bin_update(*check), 0, "  Decision      update available", stream="stdout")
            self.assertEqual(stat.S_IMODE(co.state_dir.stat().st_mode), 0o700)

        with self.subTest("a journal bin/update cannot read stops apply, check and status (exit 2)"):
            journal = co.state_dir / "journal.json"
            journal.write_text('{"kind": "apply"}\n', encoding="utf-8")
            journal.chmod(0)
            before = {"env": co.env_bytes(), "containers": self.started(co)}
            for args, stream in ((apply, "stderr"), (check, "stderr"), (["status"], "stderr")):
                with self.subTest(command=args[0]):
                    self.assertExit(co.bin_update(*args), 2, f"cannot read {journal}", stream=stream)
            self.assertEqual({"env": co.env_bytes(), "containers": self.started(co)}, before)
            journal.chmod(0o600)
            journal.unlink()

        with self.subTest(".futureagi-updates is a file"):
            shutil.rmtree(co.state_dir)
            co.state_dir.write_text("not a directory\n", encoding="utf-8")
            self.addCleanup(lambda: co.state_dir.is_file() and co.state_dir.unlink())
            before = {"env": co.env_bytes(), "containers": self.started(co)}
            for args in (check, apply, ["rollback", "--yes"]):
                with self.subTest(command=args[0]):
                    self.assertExit(co.bin_update(*args), 2, str(co.state_dir))
            self.assertExit(co.bin_update("status"), 0, "Last run      none", stream="stdout")
            self.assertEqual(co.state_dir.read_text(encoding="utf-8"), "not a directory\n")
            self.assertEqual({"env": co.env_bytes(), "containers": self.started(co)}, before)
            co.state_dir.unlink()

        if sys.platform == "darwin" and shutil.which("chflags"):
            with self.subTest("an immutable state directory (chflags uchg)"):
                co.state_dir.mkdir(mode=0o700)
                e2e.run(["chflags", "uchg", str(co.state_dir)])
                self.addCleanup(e2e.run, ["chflags", "nouchg", str(co.state_dir)], check=False)
                before = {"env": co.env_bytes(), "containers": self.started(co)}
                for args in (check, apply):
                    with self.subTest(command=args[0]):
                        self.assertExit(co.bin_update(*args), 2, str(co.state_dir))
                self.assertEqual({"env": co.env_bytes(), "containers": self.started(co)}, before)
                e2e.run(["chflags", "nouchg", str(co.state_dir)])

    @e2e.slow
    def test_34_a_checkout_path_with_spaces_and_non_ascii_characters(self):
        assert H is not None
        root = H.run_dir / "Mein Büro (prod)" / "fagi checkout ñ 日本"
        co, registry, apply = self.standalone("unicode-path", root=root)
        self.assertEqual(co.root, root)
        # Docker sees the bind-mounted files under that path.
        self.assertIn("mode=small-host", co.compose("exec", "-T", "postgres", "cat", "/etc/datastore/datastore.conf").stdout)
        elsewhere = H.run_dir / "unicode-elsewhere"
        elsewhere.mkdir()
        cd = f"cd {shlex.quote(str(root))} && "

        result = co.bin_update("check", "--format", "json", *apply[1:3], cwd=elsewhere)
        self.assertExit(result, 0, stream="stdout")
        plan = json.loads(result.stdout)
        self.assertEqual((plan["decision"], plan["signature"]["verified"], plan["installation"]["root"]),
                         ("update_available", True, str(root)))
        self.assertTrue(plan["next_command"].startswith(f"{cd}./bin/update apply --manifest "), plan["next_command"])
        pasted = e2e.run(["sh", "-c", plan["next_command"] + " --allow-no-backup"], cwd=elsewhere, env=co.update_env(),
                         check=False, timeout=1200)
        self.assertExit(pasted, 0, f"Updated to {V52}. Receipt: {root / e2e.STATE_DIR}")
        self.assertRuns(co, registry, V52)
        self.assertStatusTells(co)
        result = co.bin_update("status", cwd=elsewhere)
        self.assertExit(result, 0, f"Last run      apply to {V52}: succeeded", stream="stdout")
        result = co.bin_update("rollback", "--yes", cwd=elsewhere, timeout=1200)
        self.assertExit(result, 0, f"Rolled back to {V51}. Receipt: {root / e2e.STATE_DIR}")
        self.assertRuns(co, registry, V51)
        self.assertEqual(sorted(p.name for p in elsewhere.iterdir()), [])
        self.assertSettled(co)

    @e2e.slow
    def test_35_a_symlinked_checkout_run_from_another_directory_is_one_install_with_one_lock(self):
        assert H is not None
        co, registry, apply = self.standalone("symlinked")
        link = H.run_dir / "links" / "current"
        link.parent.mkdir(parents=True)
        link.symlink_to(co.root, target_is_directory=True)
        elsewhere = H.run_dir / "symlinked-elsewhere"
        elsewhere.mkdir()

        def via_link(*args: str, timeout: float = 1200) -> e2e.Result:
            result = e2e.run([str(link / "bin" / "update"), *args], cwd=elsewhere, env=co.update_env(), check=False,
                             timeout=timeout)
            co._log(f"[via {link}, cwd {elsewhere}]\n{result.describe()}")
            return result

        result = via_link("check", "--format", "json", *apply[1:3])
        self.assertExit(result, 0, stream="stdout")
        plan = json.loads(result.stdout)
        self.assertEqual((plan["installation"]["root"], plan["installation"]["project"]), (str(co.root), co.project))
        self.assertTrue(plan["next_command"].startswith(f"cd {shlex.quote(str(co.root))} && ./bin/update apply "),
                        plan["next_command"])

        # One lock, whichever path reaches the checkout: an apply held at its restart blocks the link's.
        fd = self.faults("symlinked")
        first = self.spawn(co, apply, fd.env(at=1, match="compose*up -d*", action="pause"))
        e2e.wait_for(lambda: fd.paused() or not first.alive(), timeout=900, what="the apply to reach its restart")
        self.assertTrue(first.alive(), first.stderr_so_far())
        self.assertExit(via_link(*apply), 2, f"another bin/update is running in this checkout (pid {first.pid})")
        self.assertExit(via_link("status"), 0, f"a bin/update run is in progress (pid {first.pid})", stream="stdout")
        fd.release_all()
        self.assertExit(first.wait(timeout=900), 0, f"Updated to {V52}")
        self.assertRuns(co, registry, V52)

        result = via_link("rollback", "--yes")
        self.assertExit(result, 0, f"Rolled back to {V51}. Receipt: {co.state_dir}")
        self.assertRuns(co, registry, V51)
        self.assertEqual([r["status"] for r in co.receipts()], ["succeeded", "rolled_back"])
        self.assertFalse((elsewhere / e2e.STATE_DIR).exists())
        self.assertEqual(sorted(p.name for p in link.parent.iterdir()), ["current"])
        self.assertSettled(co)

    @e2e.slow
    def test_36_two_applies_released_at_once_exactly_one_wins_the_lock(self):
        """Both applies are held at their signature check (before the lock) and let go together: one takes
        the lock and updates, the other is refused (exit 2) and changes nothing."""

        co, registry, apply = self.standalone("race")
        injectors = [self.faults("race-1"), self.faults("race-2")]
        runs = [self.spawn(co, apply, fd.env(at=1, match="run *", action="pause")) for fd in injectors]
        e2e.wait_for(lambda: all(fd.paused() for fd in injectors) or not all(r.alive() for r in runs), timeout=900,
                     what="both applies to reach their signature check")
        self.assertTrue(all(r.alive() for r in runs), [r.stderr_so_far() for r in runs])
        for fd in injectors:
            fd.release_all()
        results = [run.wait(timeout=900) for run in runs]
        codes = sorted(r.code for r in results)
        self.assertEqual(codes, [0, 2], "\n".join(r.describe() for r in results))
        winner = runs[[r.code for r in results].index(0)]
        loser = results[[r.code for r in results].index(2)]
        self.assertExit(loser, 2, f"another bin/update is running in this checkout (pid {winner.pid})")
        self.assertNotIn("Restarting", loser.stderr)
        self.assertEqual([r["status"] for r in co.receipts()], ["succeeded"])
        self.assertRuns(co, registry, V52)
        self.assertSettled(co)

    @e2e.slow
    def test_37_compose_settings_in_the_shell_versus_the_env_file(self):
        """A shell COMPOSE_FILE that differs from .env's is refused; the same COMPOSE_PROJECT_NAME as .env's
        changes nothing; a production install takes both from its env file whatever the shell exports."""

        assert H is not None
        co, _, apply = self.standalone("shell-compose")
        check = ["check", *apply[1:3]]
        before = self.snapshot(co)
        self.assertExit(co.bin_update(*check, env={"COMPOSE_FILE": "docker-compose.yml"}), 2,
                        "COMPOSE_FILE=docker-compose.yml is set in your shell, and Docker Compose uses it instead of "
                        ".env (no COMPOSE_FILE)")
        result = co.bin_update(*check, env={"COMPOSE_PROJECT_NAME": co.project})
        self.assertExit(result, 0, f"  Installation  standalone (Compose project {co.project}, .env)",
                        "  Decision      update available", stream="stdout")
        self.assertEqual(self.snapshot(co), before)

    @e2e.slow
    def test_38_a_shell_project_name_naming_another_install_never_touches_it(self):
        """Two Standalone installs on one host. The shell exports COMPOSE_PROJECT_NAME naming install B (left
        exported from working on B) while install A's bin/update runs. Compose would follow the export, so A's
        apply would plan against B's containers, write A's .env and recreate B from A's files. bin/update
        refuses it before any Docker call, as it refuses a shell COMPOSE_FILE, and neither install changes.
        """

        assert H is not None
        a, _, apply = self.standalone("shell-a")
        b, _, _ = self.standalone("shell-b")
        before_a, before_b = self.snapshot(a), self.snapshot(b)
        for command in (apply, ["rollback", "--yes"], ["status"]):
            with self.subTest(command=command[0]):
                result = a.bin_update(*command, env={"COMPOSE_PROJECT_NAME": b.project}, timeout=1200)
                self.assertExit(result, 2, f"COMPOSE_PROJECT_NAME={b.project} is set in your shell, and Docker "
                                           "Compose uses it instead of .env",
                                f"bin/update would restart project {b.project} with this checkout's files")
        self.assertEqual(self.snapshot(a), before_a)
        self.assertEqual(self.snapshot(b), before_b)
        b.wait_healthy(timeout=60)

    @e2e.slow
    def test_39_a_backup_hook_that_ignores_sigterm_is_killed_with_its_children_after_the_timeout(self):
        assert H is not None
        co, _, apply = self.standalone("hook-timeout")
        directory = H.hook_dir("faults-hook-timeout")
        paths = {name: directory / name for name in ("pid", "child", "started")}
        # The hook and the child it starts both ignore SIGTERM: only the SIGKILL after the grace period stops them.
        child = H.write_script(directory / "child", f"""
trap '' TERM
echo $$ > {shlex.quote(str(paths['child']))}
while :; do sleep 1; done
""")
        hook = H.write_script(directory / "backup", f"""
echo $$ > {shlex.quote(str(paths['pid']))}
trap '' TERM
{shlex.quote(str(child))} &
: > {shlex.quote(str(paths['started']))}
while :; do sleep 1; done
""")
        before = self.snapshot(co)
        command = [arg for arg in apply if arg != "--allow-no-backup"]
        result = co.bin_update(*command, "--backup-command", str(hook),
                               env={"FUTURE_AGI_UPDATE_BACKUP_TIMEOUT_SECONDS": "2"})
        self.assertExit(result, 4, "the backup hook did not finish within 2s; nothing was changed")
        pids = [int(paths[name].read_text().strip()) for name in ("pid", "child")]

        def gone() -> bool:
            for pid in pids:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    continue
                return False
            return True

        e2e.wait_for(gone, timeout=30, what=f"the hook and its child ({pids}) to be gone")
        self.assertEqual(self.snapshot(co), before)
        self.assertSettled(co)


class FaultOracleNegativeTests(unittest.TestCase):
    """Exercise the real matrix judge without Docker; never replace its assertions."""

    def test_rollout_failure_rejects_wrong_exit_receipt_and_automatic_env_rollback(self):
        from unittest.mock import Mock

        judge = FaultE2EBase()
        co = Mock()
        spec = Spec("negative", co, [], [], "apply", V52, env_before=b"old", env_after=b"new")
        call = faults.Call(17, "docker-compose", "fail", "1", ("config", "--format", "json"),
                           ("rollout", "apply", "running"))
        point = faults.FaultPoint(17, "config", call.glob, 1, "fail")
        before = {"receipts": [], "containers": []}
        text = (f"apply to {V52} stopped while containers were being replaced: fault. "
                f"The env file selects {V52}.")
        for code, env, receipt in ((5, b"new", "failed_during_rollout"),
                                   (4, b"new", "failed_during_rollout"),
                                   (5, b"old", "failed_during_rollout"),
                                   (5, b"new", "failed_before_rollout")):
            with self.subTest(code=code, env=env, receipt=receipt):
                co.env_bytes.return_value = env
                co.receipts.return_value = [{"_path": "/receipt.json", "status": receipt}]
                co.containers.return_value = []
                result = Mock(code=code, stderr=text)
                args = (spec, point, result, [call], "rollout", call, before, "negative control")
                if (code, env, receipt) == (5, b"new", "failed_during_rollout"):
                    self.assertTrue(judge.judge(*args).startswith("stopped: exit 5"))
                else:
                    with self.assertRaises(AssertionError):
                        judge.judge(*args)

    def test_killed_rollout_rejects_missing_marker_and_automatic_env_rollback(self):
        from unittest.mock import Mock

        judge = FaultE2EBase()
        co = Mock()
        spec = Spec("negative", co, [], [], "rollback", V51, env_before=b"old", env_after=b"new")
        call = faults.Call(17, "docker-compose", "sigkill", "1", ("config", "--format", "json"),
                           ("rollout", "rollback", "running"))
        point = faults.FaultPoint(17, "config", call.glob, 1, "sigkill")
        for stage, env, code in (("rollout", b"new", -9), ("pulled", b"new", -9),
                                 ("rollout", b"old", -9), ("rollout", b"new", 4)):
            with self.subTest(stage=stage, env=env, code=code):
                co.env_bytes.return_value = env
                co.receipts.return_value = []
                co.containers.return_value = []
                co.journal.return_value = {"kind": "rollback", "target_version": V51,
                                           "stage": stage, "status": "running"}
                args = (spec, point, Mock(code=code), [call], "rollout", call,
                        {"receipts": [], "containers": []}, "negative control")
                if (stage, env, code) == ("rollout", b"new", -9):
                    self.assertEqual(judge.judge(*args), "killed: at stage rollout")
                else:
                    with self.assertRaises(AssertionError):
                        judge.judge(*args)


class FaultOracleSuccessTests(unittest.TestCase):
    """Adversarial outcomes, not updater mocks: feed traces to the real matrix judge.

    The four rollout reads below reproduce the independent review's eight
    false-success controls (each read, for apply and rollback). Positive
    controls keep optional probes distinct from mandatory ownership evidence.
    """

    IMAGE = "localhost:5000/futureagi/standalone:v1.52.0"
    SOURCE_ID = "sha256:" + "b" * 64
    OWNERSHIP = (
        ("compose", "config", "--format", "json"),
        ("compose", "config", "--services"),
        ("ps", "-a", "-q", "--no-trunc", "--filter", FaultLogicTests.PROJECT),
        ("inspect", FaultLogicTests.ID),
    )

    @staticmethod
    def renumber(calls):
        return [replace(c, index=i + 1) for i, c in enumerate(calls)]

    def reference(self, operation="apply"):
        # Same independent command landmarks as the captured native-host run;
        # no result/receipt is used to decide which phase a call belongs to.
        rows = [("absent", ("compose", "version", "--short"))]
        rows += [("absent", words) for words in self.OWNERSHIP]
        rows += [("absent", ("image", "inspect", self.SOURCE_ID)),
                 ("absent", ("compose", "config", "--profiles")),
                 ("absent", ("compose", "config", "--format", "json")),
                 ("absent", ("image", "inspect", self.IMAGE)),
                 ("env_written", ("compose", "config", "--quiet")),
                 ("env_written", ("image", "inspect", self.IMAGE))]
        rows += [("env_written", words) for words in self.OWNERSHIP]
        rows += [("env_written", ("compose", "pull", "app")),
                 ("env_written", ("image", "inspect", "busybox:1.37")),
                 ("pulled", ("image", "inspect", self.IMAGE)),
                 ("pulled", ("compose", "up", "--help"))]
        rows += [("rollout", words) for words in self.OWNERSHIP]
        rows += [("rollout", ("compose", "up", "-d", "--no-build", "--pull", "never")),
                 ("rollout", ("compose", "logs", "--no-color", "--tail", "2000", "app")),
                 ("rollout", self.OWNERSHIP[2]), ("rollout", self.OWNERSHIP[3])]
        images = tuple((hashlib.sha256(name.encode()).hexdigest(), hashlib.sha256(image.encode()).hexdigest())
                       for name, image in (("app", self.IMAGE), ("datastore", "busybox:1.37")))
        return [faults.Call(i + 1, "docker", "run", "1", words,
                            ("absent",) * 3 if stage == "absent" else (stage, operation, "running"),
                            service_images=images if words == self.OWNERSHIP[0] else ())
                for i, (stage, words) in enumerate(rows)]

    def inject(self, calls, kind, stage, action="fail"):
        calls = list(calls)
        index = next(i for i, c in enumerate(calls) if c.kind == kind and c.transaction[0] == stage)
        calls[index] = replace(calls[index], action=action)
        return calls

    def judge_trace(self, operation, calls, *, code=0, env=b"new", status=None, journal=None, recorded=None):
        from unittest.mock import Mock

        faults.validate_transactions(calls, operation)
        phase, faulted = faults.phase_of(calls)
        target = V52 if operation == "apply" else V51
        co = Mock()
        co.env_bytes.return_value = env
        co.receipts.return_value = [] if code == -9 else [{
            "_path": "/receipt.json", "status": status or ("succeeded" if operation == "apply" else "rolled_back")}]
        co.journal.return_value = journal
        co.containers.return_value = []
        spec = Spec("oracle-control", co, [], [], operation, target, env_before=b"old", env_after=b"new",
                    recorded=self.reference(operation) if recorded is None else recorded)
        point = faults.FaultPoint(faulted.index, faulted.kind, faulted.glob, 1, faulted.action) if faulted else \
            faults.FaultPoint(len(calls) + 1, "inspect", "inspect *", 1, "fail")
        text = (f"{operation} to {target} stopped while containers were being replaced: "
                f"{'SIGTERM' if code == 130 else 'fault'}. The env file selects {target}.")
        return FaultE2EBase().judge(spec, point, Mock(code=code, stderr=text), calls, phase, faulted,
                                   {"receipts": [], "containers": []}, "oracle outcome control")

    def reject_ownership_success(self, operation, kind):
        calls = self.inject(self.reference(operation), kind, "rollout")
        fault = next(c for c in calls if c.action == "fail")
        for trace in (calls, calls[:fault.index]):
            with self.subTest(continued_after_failure=len(trace) > fault.index):
                self.assertEqual(faults.phase_of(trace)[0], "rollout")
                with self.assertRaisesRegex(AssertionError, "mandatory failure"):
                    self.judge_trace(operation, trace)

    # Keep all eight as individually discoverable regressions, not one loop
    # whose first failure prevents the other operation/call sites from running.
    def test_apply_config_failure_is_not_success(self):
        self.reject_ownership_success("apply", "config")

    def test_apply_config_services_failure_is_not_success(self):
        self.reject_ownership_success("apply", "config-services")

    def test_apply_ps_failure_is_not_success(self):
        self.reject_ownership_success("apply", "ps")

    def test_apply_inspect_failure_is_not_success(self):
        self.reject_ownership_success("apply", "inspect")

    def test_rollback_config_failure_is_not_success(self):
        self.reject_ownership_success("rollback", "config")

    def test_rollback_config_services_failure_is_not_success(self):
        self.reject_ownership_success("rollback", "config-services")

    def test_rollback_ps_failure_is_not_success(self):
        self.reject_ownership_success("rollback", "ps")

    def test_rollback_inspect_failure_is_not_success(self):
        self.reject_ownership_success("rollback", "inspect")

    def test_optional_help_logs_and_image_probes_can_succeed(self):
        for operation in ("apply", "rollback"):
            for kind, stage in (("up-help", "pulled"), ("logs", "rollout"),
                                ("image-inspect", "absent"), ("image-inspect", "env_written"),
                                ("config-profiles", "absent")):
                for action in ("fail", "fail-after"):
                    with self.subTest(operation=operation, kind=kind, stage=stage, action=action):
                        calls = self.inject(self.reference(operation), kind, stage, action)
                        self.assertEqual(self.judge_trace(operation, calls), "done: exit 0 (the failure was survived)")
            calls = self.reference(operation)
            position = next(i for i, c in enumerate(calls) if c.words == ["image", "inspect", self.IMAGE])
            calls[position] = replace(calls[position], action="fail")  # optional plan cache probe
            self.assertTrue(self.judge_trace(operation, calls).startswith("done:"))

    def test_plugin_failure_needs_the_actual_standalone_fallback(self):
        for operation in ("apply", "rollback"):
            calls = self.reference(operation)
            calls = [replace(c, program="docker-compose", args=c.args[1:]) if c.words[0] == "compose" else c
                     for c in calls]
            calls.insert(0, replace(self.reference(operation)[0], action="fail"))
            calls = self.renumber(calls)
            self.assertTrue(self.judge_trace(operation, calls).startswith("done:"))
            for variant in (calls[:1] + calls[2:],
                            [calls[0], replace(calls[1], action="fail"), *calls[2:]],
                            [calls[0], replace(calls[1], program="docker", args=("compose", "version", "--short")),
                             *calls[2:]]):
                with self.subTest(operation=operation), self.assertRaises(AssertionError):
                    self.judge_trace(operation, self.renumber(variant))

    def test_all_profiles_model_failure_needs_a_strict_replacement_model(self):
        for operation in ("apply", "rollback"):
            calls = self.reference(operation)
            position = next(i for i, c in enumerate(calls) if c.kind == "config-profiles") + 1
            calls[position - 1] = replace(calls[position - 1], profiles="nonempty")
            calls.insert(position, replace(calls[position], action="fail"))
            calls = self.renumber(calls)
            self.assertTrue(self.judge_trace(operation, calls).startswith("done:"))
            for variant in (calls[:position + 1] + calls[position + 2:],
                            calls[:position + 1] + [replace(calls[position + 1], action="fail")] + calls[position + 2:]):
                with self.subTest(operation=operation), self.assertRaises(AssertionError):
                    self.judge_trace(operation, self.renumber(variant))

    def test_third_party_cache_miss_needs_its_immediate_owned_pull(self):
        for operation in ("apply", "rollback"):
            calls = self.reference(operation)
            position = next(i for i, c in enumerate(calls) if c.words == ["image", "inspect", "busybox:1.37"])
            calls[position] = replace(calls[position], action="fail")
            images = next(c.service_images for c in calls if c.kind == "config")
            repair = [replace(calls[position], args=words, action="run",
                              service_images=images if words == self.OWNERSHIP[0] else ()) for words in self.OWNERSHIP]
            repair.append(replace(calls[position], args=("compose", "pull", "datastore"), action="run"))
            repaired = self.renumber(calls[:position + 1] + repair + calls[position + 1:])
            self.assertTrue(self.judge_trace(operation, repaired).startswith("done:"))
            for variant in (calls, calls[:position + 1] + repair[-1:] + calls[position + 1:]):
                with self.subTest(operation=operation), self.assertRaises(AssertionError):
                    self.judge_trace(operation, self.renumber(variant))
            with self.assertRaises(AssertionError):  # no independent reference for the third-party exemption
                self.judge_trace(operation, repaired, recorded=[])

    def test_target_cache_miss_needs_pull_or_matching_tag_and_digest_verification(self):
        for operation in ("apply", "rollback"):
            calls = self.inject(self.reference(operation), "image-inspect", "env_written")
            pull = next(i for i, c in enumerate(calls) if c.kind == "pull")
            # The rollback adoption path tags the exact missing target, then verifies it.
            tagged = list(calls)
            tagged[pull] = replace(tagged[pull], args=("tag", self.SOURCE_ID, self.IMAGE))
            self.assertTrue(self.judge_trace(operation, tagged).startswith("done:"))
            variants = [calls[:pull] + calls[pull + 1:],
                        [c for c in calls if not (c.kind == "image-inspect" and c.transaction[0] == "pulled")],
                        tagged[:pull] + [replace(tagged[pull], args=("tag", self.SOURCE_ID, "wrong-image"))]
                        + tagged[pull + 1:]]
            for variant in variants:
                with self.subTest(operation=operation), self.assertRaises(AssertionError):
                    self.judge_trace(operation, self.renumber(variant))

    def test_optional_failure_cannot_hide_a_second_mandatory_failure(self):
        for operation in ("apply", "rollback"):
            calls = self.inject(self.reference(operation), "up-help", "pulled")
            for kind in ("config", "config-services", "ps", "inspect", "up"):
                with self.subTest(operation=operation, kind=kind), self.assertRaises(AssertionError):
                    self.judge_trace(operation, self.inject(calls, kind, "rollout"))

    def test_no_invented_ownership_retry_or_later_readiness_poll_can_authorize_success(self):
        for operation in ("apply", "rollback"):
            reference = self.reference(operation)
            ownership = [c for c in reference if c.transaction[0] == "rollout"][:4]
            up = next(i for i, c in enumerate(reference) if c.kind == "up")
            for kind in ("config", "config-services", "ps", "inspect"):
                calls = self.inject(reference, kind, "rollout")
                for replacement in (ownership, ownership[1:], ownership[-1:]):
                    with self.subTest(operation=operation, kind=kind, replacement=len(replacement)):
                        with self.assertRaises(AssertionError):
                            self.judge_trace(operation, self.renumber(calls[:up] + replacement + calls[up:]))

    def test_mandatory_planning_preparation_verification_and_mutation_failures_are_not_success(self):
        for operation in ("apply", "rollback"):
            for kind, stage in (("config", "absent"), ("config-services", "absent"), ("ps", "absent"),
                                ("inspect", "absent"), ("config-check", "env_written"), ("config", "env_written"),
                                ("config-services", "env_written"), ("ps", "env_written"), ("inspect", "env_written"),
                                ("pull", "env_written"), ("image-inspect", "pulled"), ("up", "rollout")):
                for action in ("fail", "fail-after", "sigkill", "sigterm"):
                    with self.subTest(operation=operation, kind=kind, stage=stage, action=action):
                        with self.assertRaises(AssertionError):
                            self.judge_trace(operation, self.inject(self.reference(operation), kind, stage, action))
            # Readiness's later ps/inspect calls are mandatory too, not retries.
            for position in (-2, -1):
                calls = self.reference(operation)
                calls[position] = replace(calls[position], action="fail")
                with self.assertRaises(AssertionError):
                    self.judge_trace(operation, calls)

    def test_rollout_exit_receipt_and_unsafe_inverse_guards_for_each_ownership_read(self):
        for operation in ("apply", "rollback"):
            for kind in ("config", "config-services", "ps", "inspect"):
                calls = self.inject(self.reference(operation), kind, "rollout")
                calls = calls[:next(c.index for c in calls if c.action == "fail")]
                good = {"code": 5, "status": "failed_during_rollout"}
                self.assertTrue(self.judge_trace(operation, calls, **good).startswith("stopped: exit 5"))
                for changed in ({"code": 4}, {"code": 2}, {"status": "failed_before_rollout"}, {"env": b"old"}):
                    with self.subTest(operation=operation, kind=kind, changed=changed), self.assertRaises(AssertionError):
                        self.judge_trace(operation, calls, **dict(good, **changed))
                for header in (("absent",) * 3, ("pulled", operation, "running"), ("invalid",) * 3,
                               ("rollout", "rollback" if operation == "apply" else "apply", "running"),
                               ("rollout", operation, "succeeded")):
                    with self.subTest(operation=operation, kind=kind, header=header), self.assertRaises(AssertionError):
                        self.judge_trace(operation, calls[:-1] + [replace(calls[-1], transaction=header)], **good)

    def test_signal_outcomes_never_restore_the_env_after_the_rollout_marker(self):
        for operation in ("apply", "rollback"):
            for kind in ("config", "config-services", "ps", "inspect"):
                for action, code in (("sigkill", -9), ("sigterm", 130)):
                    calls = self.inject(self.reference(operation), kind, "rollout", action)
                    calls = calls[:next(c.index for c in calls if c.action == action)]
                    journal = {"kind": operation, "target_version": V52 if operation == "apply" else V51,
                               "status": "running", "stage": "rollout"} if code == -9 else None
                    good = {"code": code, "status": "interrupted_during_rollout", "journal": journal}
                    self.assertTrue(self.judge_trace(operation, calls, **good).startswith(
                        "killed:" if code == -9 else "stopped: exit 130"))
                    for changed in ({"code": 4}, {"env": b"old"},
                                    {"journal": dict(journal, stage="pulled")} if journal else
                                    {"status": "interrupted_before_rollout"}):
                        with self.subTest(operation=operation, kind=kind, action=action, changed=changed):
                            with self.assertRaises(AssertionError):
                                self.judge_trace(operation, calls, **dict(good, **changed))

    def test_optional_and_not_reached_success_still_require_expected_receipt_and_env(self):
        for operation in ("apply", "rollback"):
            reference = self.reference(operation)
            for calls in (reference, self.inject(reference, "up-help", "pulled")):
                self.assertTrue(self.judge_trace(operation, calls).startswith("done:"))
                for changed in ({"env": b"old"}, {"status": "failed_during_rollout"}, {"status": "staged"},
                                {"status": "rolled_back" if operation == "apply" else "succeeded"}):
                    with self.subTest(operation=operation, changed=changed), self.assertRaises(AssertionError):
                        self.judge_trace(operation, calls, **changed)

    def test_every_matrix_point_and_the_independent_command_landmarks_are_retained(self):
        for operation in ("apply", "rollback"):
            calls = self.reference(operation)
            faults.validate_transactions(calls, operation)
            full = faults.full_matrix(calls)
            self.assertEqual([(p.index, p.action) for p in full],
                             [(i, action) for i in range(1, len(calls) + 1) for action in ("fail", "sigkill")])
            self.assertEqual(full, faults.full_matrix(calls, before_restart=True)
                             + faults.full_matrix(calls, before_restart=False))
            for kind in ("config", "config-services", "ps", "inspect"):
                faulted = self.inject(calls, kind, "rollout")
                phase, call = faults.phase_of(faulted)
                self.assertEqual(phase, "rollout")
                self.assertIsNone(faults.prefix_matches(calls, faulted))
                self.assertFalse(faults.restarted(faulted[:call.index]))


class FaultOracleEdgeTests(unittest.TestCase):
    """Production-ordered controls for strict models and batched cache recovery.

    Only command execution is recorded here; no filesystem qualification or
    production outcome is mocked into a pass. Successful final outcomes are
    deliberately synthetic input to the oracle, as in the independent review.
    """

    def setUp(self):
        self.oracle = FaultOracleSuccessTests()

    def batch_trace(self, operation, misses, names=("postgres", "redis")):
        from types import SimpleNamespace
        from deploy.update.apply import Updater
        from deploy.update.install import Installation

        ref = self.oracle.reference(operation)
        at = next(i for i, c in enumerate(ref) if c.words == ["image", "inspect", "busybox:1.37"])
        template = ref[at]
        config = {"services": {"app": {"image": self.oracle.IMAGE},
                                **{name: {"image": "busybox:1.37"} for name in names}}}
        images = tuple((hashlib.sha256(name.encode()).hexdigest(), hashlib.sha256(service["image"].encode()).hexdigest())
                       for name, service in sorted(config["services"].items()))
        ref = [replace(c, service_images=images) if c.kind == "config" else c for c in ref]
        pull = max(i for i, c in enumerate(ref[:at]) if c.kind == "pull")
        ownership = ref[pull - 4:pull]

        class Recorder:
            compose_run = Installation.compose_run

            def __init__(self):
                self.calls = []
                self.probes = 0

            def image(self, name):
                failed = self.probes in misses
                self.probes += 1
                self.calls.append(replace(template, args=("image", "inspect", name), action="fail" if failed else "run"))
                return None if failed else {"id": "sha256:" + "c" * 64}

            def check_ownership(self):
                self.calls.extend(ownership)

            def compose_args(self, *args):
                return ["docker", "compose", *args]

            def run(self, argv, **kwargs):
                self.calls.append(replace(template, args=tuple(argv[1:]), action="run"))
                return SimpleNamespace(returncode=0, stdout="", stderr="")

        recorder = Recorder()
        updater = object.__new__(Updater)
        updater.installation = recorder
        updater.say = lambda message: None
        updater._pull_missing(config, [{"service": "app"}])
        self.assertEqual([c.kind for c in recorder.calls], ["image-inspect"] * len(names)
                         + ["config", "config-services", "ps", "inspect", "pull"])
        self.assertEqual(recorder.calls[-1].words, ["compose", "pull", *[n for i, n in enumerate(sorted(names)) if i in misses]])
        recorded = self.oracle.renumber(ref[:at] + [replace(template, action="run") for _ in names] + ref[at + 1:])
        calls = self.oracle.renumber(ref[:at] + recorder.calls + ref[at + 1:])
        return calls, recorded

    def assert_batch(self, operation, misses, names=("postgres", "redis")):
        calls, recorded = self.batch_trace(operation, misses, names)
        self.assertIsNone(faults.prefix_matches(recorded, calls))
        self.assertEqual(self.oracle.judge_trace(operation, calls, recorded=recorded),
                         "done: exit 0 (the failure was survived)")

    # The eight independent batch controls remain separately discoverable.
    def test_apply_first_of_two_misses(self):
        self.assert_batch("apply", {0})

    def test_apply_both_of_two_miss(self):
        self.assert_batch("apply", {0, 1})

    def test_apply_last_of_two_misses(self):
        self.assert_batch("apply", {1})

    def test_apply_only_service_misses(self):
        self.assert_batch("apply", {0}, ("postgres",))

    def test_rollback_first_of_two_misses(self):
        self.assert_batch("rollback", {0})

    def test_rollback_both_of_two_miss(self):
        self.assert_batch("rollback", {0, 1})

    def test_rollback_last_of_two_misses(self):
        self.assert_batch("rollback", {1})

    def test_rollback_only_service_misses(self):
        self.assert_batch("rollback", {0}, ("postgres",))

    def strict_model(self, operation):
        from types import SimpleNamespace
        from deploy.update.install import Installation, InstallError

        ref = self.oracle.reference(operation)
        at = next(i for i, c in enumerate(ref) if c.kind == "config-profiles")

        class Recorder:
            _probe_services = Installation._probe_services
            _profiles = Installation._profiles
            _probe = Installation._probe

            def __init__(self):
                self.calls = []

            def compose_args(self, *args):
                return ["docker", "compose", *args]

            def run(self, argv, **kwargs):
                profiles = argv[-1] == "--profiles"
                self.calls.append(replace(ref[at], args=tuple(argv[1:]), action="run" if profiles else "fail",
                                          profiles="empty" if profiles else "unobserved"))
                return SimpleNamespace(args=argv, returncode=0 if profiles else 1, stdout="", stderr="fault")

            def explain_failure(self, result):
                return "strict model failure"

            describe_failure = explain_failure

        recorder = Recorder()
        with self.assertRaises(InstallError):
            recorder._probe_services()
        self.assertEqual(len(recorder.calls), 2)
        self.assertEqual(recorder.calls[-1].words, ["compose", "config", "--format", "json"])
        calls = self.oracle.renumber(ref[:at] + recorder.calls + [replace(recorder.calls[-1], action="run")] + ref[at + 2:])
        self.assertIsNone(faults.prefix_matches(ref, calls))
        with self.assertRaisesRegex(AssertionError, "mandatory failure"):
            self.oracle.judge_trace(operation, calls, recorded=ref)

    def test_apply_mandatory_strict_model_cannot_gain_a_fabricated_retry(self):
        self.strict_model("apply")

    def test_rollback_mandatory_strict_model_cannot_gain_a_fabricated_retry(self):
        self.strict_model("rollback")

    def test_profile_model_requires_observed_valid_nonempty_discovery_in_same_context(self):
        for operation in ("apply", "rollback"):
            ref = self.oracle.reference(operation)
            at = next(i for i, c in enumerate(ref) if c.kind == "config-profiles")
            for evidence in ("empty", "invalid", "failed", "unobserved", "nonempty"):
                for action in ("fail", "fail-after"):
                    calls = self.oracle.renumber(ref[:at] + [replace(ref[at], profiles=evidence),
                        replace(ref[at + 1], action=action)] + ref[at + 1:])
                    with self.subTest(operation=operation, evidence=evidence, action=action):
                        if evidence == "nonempty":
                            self.assertTrue(self.oracle.judge_trace(operation, calls).startswith("done:"))
                        else:
                            with self.assertRaisesRegex(AssertionError, "mandatory failure"):
                                self.oracle.judge_trace(operation, calls)
            calls = self.oracle.renumber(ref[:at] + [replace(ref[at], profiles="nonempty"),
                replace(ref[at + 1], action="fail")] + ref[at + 1:])
            for change in ({"pgid": "other"}, {"args": ("compose", "-p", "other", "config", "--profiles")},
                           {"program": "docker-compose", "args": ("config", "--profiles")}):
                with self.subTest(change=change), self.assertRaises(AssertionError):
                    self.oracle.judge_trace(operation, calls[:at] + [replace(calls[at], **change)] + calls[at + 1:])

    def test_batch_pull_cannot_cross_transaction_order_target_or_verification(self):
        for operation in ("apply", "rollback"):
            for misses in ({0}, {0, 1}, {1}):
                calls, recorded = self.batch_trace(operation, misses)
                at = next(i for i, c in enumerate(calls) if c.action == "fail")
                pull = next(i for i in range(at + 1, len(calls)) if calls[i].kind == "pull")
                start = pull - 4
                variants = {
                    "wrong-target": calls[:pull] + [replace(calls[pull], args=("compose", "pull", "app"))] + calls[pull + 1:],
                    "unknown-target": calls[:pull] + [replace(calls[pull], args=("compose", "pull", "unrelated"))] + calls[pull + 1:],
                    "wrong-same-image-service": calls[:pull] + [replace(calls[pull], args=("compose", "pull", "redis" if 0 in misses else "postgres"))] + calls[pull + 1:],
                    "bare-pull": calls[:start] + calls[pull:],
                    "pull-before-probes": calls[:at] + calls[start:pull + 1] + calls[at:start] + calls[pull + 1:],
                    "unrelated-intervening-call": calls[:start] + [replace(calls[start], args=("info",))] + calls[start:],
                    "missing-release-verification": [c for c in calls if not (c.kind == "image-inspect" and c.transaction[0] == "pulled")],
                    "no-model-evidence": [replace(c, service_images=()) for c in calls],
                    "changed-model": calls[:start] + [replace(calls[start], service_images=())] + calls[start + 1:],
                    "foreign-process": calls[:start] + [replace(c, pgid="other") for c in calls[start:pull + 1]] + calls[pull + 1:],
                    "foreign-project": calls[:pull] + [replace(calls[pull], args=("compose", "-p", "other", "pull", "postgres", "redis"))] + calls[pull + 1:],
                }
                for header in (("env_written", "other", "running"), ("pulled", operation, "running"),
                               ("rollout", operation, "running")):
                    variants[str(header)] = calls[:start] + [replace(c, transaction=header) for c in calls[start:pull + 1]] + calls[pull + 1:]
                for kind in ("config", "config-services", "ps", "inspect", "pull"):
                    variants["failed-" + kind] = [replace(c, action="fail") if start <= i <= pull and c.kind == kind else c
                                                for i, c in enumerate(calls)]
                for label, variant in variants.items():
                    with self.subTest(operation=operation, misses=misses, variant=label):
                        with self.assertRaises(AssertionError):
                            self.oracle.judge_trace(operation, self.oracle.renumber(variant), recorded=recorded)
                # Dropping a release target's verification from the faulted
                # trace cannot turn that target into a third-party exemption.
                target = self.oracle.inject(recorded, "image-inspect", "env_written")
                with self.assertRaises(AssertionError):
                    self.oracle.judge_trace(operation, [c for c in target if not (
                        c.kind == "image-inspect" and c.transaction[0] == "pulled")], recorded=recorded)


if __name__ == "__main__":
    unittest.main()
