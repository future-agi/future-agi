"""Docker end-to-end tests of bin/update on Distributed and production installs.

Skipped unless FUTURE_AGI_UPDATE_E2E=1 and Docker with Compose v2 is
available. deploy/tests/update_e2e/harness.py runs the registry, keys and
signed manifests; deploy/tests/update_e2e/distributed.py adds fake images for
the seven Distributed repositories, a locally built fi-collector, compose
files shaped like docker-compose.distributed.yml and
deploy/docker-compose.production.yml, and checkouts with .env or
deploy/.env.production. Each test owns its Compose project (prefix
fagi-upd-e2e-dist-) and drives that checkout's bin/update exactly as an
operator would, then inspects the env files, containers, container env,
volumes and receipts. Production updates in two steps (deploy/README.md ›
Upgrading a production install): `apply --stage`, the release's
initialization job (`compose run --rm`), then `apply`; the fake check-only
bootstrap job fails until that initialization ran on the release's image.
Commands bin/update prints for the operator are run as pasted.

  FUTURE_AGI_UPDATE_E2E=1 python3 -m unittest discover -s deploy/tests \\
      -p 'test_update_e2e_distributed.py' -v

Tests run FUTURE_AGI_UPDATE_E2E_JOBS at a time (default 4; 1 = one by one).
"""

from __future__ import annotations

import functools
import json
import re
import shlex
import stat
import sys
import time
import unittest
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from update_e2e import distributed as dist  # noqa: E402
from update_e2e import harness as e2e  # noqa: E402

V51, V52, V53 = dist.V51, dist.V52, dist.V53
VERSION_KEYS = dist.VERSION_KEYS
ACTIVE_KEYS = tuple(k for k in VERSION_KEYS if k != "SIMULATION_RUNNER_VERSION")
APP_SERVICES = tuple(s for s in dist.SERVICES if s != dist.BOOTSTRAP)
DEFAULT_SERVICES = tuple(s for s in APP_SERVICES if s not in dist.PROFILE_SERVICES)
LOCAL_NOTE = ("fi-collector is built from this checkout today (FI_COLLECTOR_VERSION unset); the update switches "
              "it to the published futureagi/fi-collector image of the release")
# Why a running container of a service outside the active profiles blocks (plan._judge_strays); its
# group is the command that removes it.
STRAY_BLOCKS = (r"service worker-simulation-runner has containers on Future AGI images but is not in the active "
                r"Compose configuration \(a profile that is not enabled now\): worker-simulation-runner \(running, "
                r"{version}\)\. The update to {target} does not replace its containers, so they would keep running as "
                r"they are next to it\. Enable the profile \(COMPOSE_PROFILES in \.env\), or remove it with "
                r"`((?:docker compose|\S*docker-compose) rm -sf worker-simulation-runner)`, then check again")
# Where a production install's initialization jobs are named (plan.jobs_text): `docker compose` (or the
# standalone binary) with exactly this install's env file and compose files, then the rule about shell exports
# (plan.shell_rule).
JOBS = (r"`((?:docker compose|\S*docker-compose) --env-file {env} -f docker-compose\.distributed\.yml "
        r"-f deploy/docker-compose\.production\.yml) run --rm \.\.\.` \(deploy/README\.md › Upgrading a production "
        r"install\. Run it from a shell that does not export any key of {name} "
        r"\(Compose prefers your shell over the env file\)\.\)")

H: Optional[dist.DistributedHarness] = None


def setUpModule() -> None:
    global H
    reason = dist.availability()
    if reason:
        raise unittest.SkipTest(reason)
    harness = dist.DistributedHarness()
    try:
        harness.start(dist_releases=[(V52, "trusted"), (V53, "trusted")])
    except BaseException:
        harness.stop()
        raise
    H = harness


def tearDownModule() -> None:
    if H is not None:
        H.stop()


def load_tests(loader: unittest.TestLoader, tests: unittest.TestSuite, pattern: Optional[str]) -> unittest.TestSuite:
    # FUTURE_AGI_UPDATE_E2E_JOBS tests at a time (each owns its project and ports).
    return e2e.parallel_suite(tests)


def manifest(version: str) -> List[str]:
    assert H is not None
    return ["--manifest", str(H.dist_release(version).manifest)]


def release_options(version: str) -> str:
    """manifest(version) as the apply commands bin/update prints repeat it (shell-quoted)."""

    return " ".join(shlex.quote(word) for word in manifest(version))


def jobs_pattern(env_name: str = dist.PRODUCTION_ENV) -> str:
    """A regex of how bin/update names the initialization jobs of a production install keeping its settings in
    `env_name`; its group is the Compose command."""

    return JOBS.format(env=re.escape(shlex.quote(env_name)), name=re.escape(env_name))


def two_steps(version: str) -> str:
    """A regex of why a plain `--production apply` of `version` is refused (the update has two steps)."""

    options = release_options(version)
    return (re.escape(f"production installs update in two steps: ./bin/update --production apply --stage {options} "
                      "(backs up, then pulls and verifies the release; restarts nothing), run the release's "
                      "initialization jobs with ") + jobs_pattern()
            + re.escape(f", then ./bin/update --production apply {options}. Nothing was changed"))


def bootstrap_line(version: str, *args: str) -> str:
    return " ".join(("bootstrap", version, "backend", *args))


class DistributedUpdateE2E(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        started = time.monotonic()
        self.addCleanup(lambda: e2e.log(f"{self._testMethodName} took {time.monotonic() - started:.0f}s"))

    # ----------------------------------------------------------- helpers --
    def checkout(self, name: str, **kwargs: Any) -> dist.DistributedCheckout:
        assert H is not None
        checkout = H.new_distributed_checkout(name, **kwargs)
        self.addCleanup(checkout.down)
        return checkout

    def backup_hook(self, name: str) -> Tuple[Path, Callable[[], List[str]]]:
        """A backup hook (for --backup-command) that records each run; returns
        it and a reader of its runs (the release each one backed up for)."""

        assert H is not None
        directory = H.hook_dir(f"dist-{name}")
        runs = directory / "runs"
        if runs.exists():
            runs.unlink()
        hook = H.write_script(directory / "backup",
                              f'echo "$FUTURE_AGI_UPDATE_TO_VERSION" >> {shlex.quote(str(runs))}\n')
        return hook, lambda: runs.read_text(encoding="utf-8").split() if runs.exists() else []

    def assertStaged(self, checkout: dist.DistributedCheckout, version: str, services: Any) -> None:
        """`version`'s images of `services` are on the host, by their signed registry digests."""

        assert H is not None
        refs = {service: H.dist_image(dist.SERVICES[service], version) for service in services}
        infos = checkout.images_info(refs.values())
        for service, ref in refs.items():
            component = dist.SERVICES[service]
            with self.subTest(staged=service):
                self.assertEqual(infos[ref]["version"], version)
                self.assertIn(f"{H.registry}/{dist.COMPONENTS[component][0]}@{H.dist_digests[(component, version)]}",
                              infos[ref]["repo_digests"])

    def assertExit(self, result: e2e.Result, code: int, *needles: str, stream: str = "stderr") -> None:
        message = f"\n{result.describe()}"
        self.assertEqual(result.code, code, message)
        text = result.output if stream == "both" else getattr(result, stream)
        for needle in needles:
            self.assertIn(needle, text, message)

    def assertUnchanged(self, checkout: dist.DistributedCheckout, before: Dict[str, Any]) -> None:
        self.assertEqual(checkout.snapshot(), before)
        self.assertEqual(checkout.temp_env_files(), [])

    def assertOnlyVersionKeysChanged(self, original: bytes, now: bytes, expected: Mapping[str, List[str]]) -> None:
        """Every line but the version keys is byte-identical and in order; each
        version key has exactly the lines in `expected` ([] = absent)."""

        rest_before, _ = dist.split_env(original.decode("utf-8"))
        rest_now, values = dist.split_env(now.decode("utf-8"))
        self.assertEqual(rest_now, rest_before, "a line other than a version key changed")
        for key, want in expected.items():
            self.assertEqual(values.get(key, []), want, f"{key} in\n{now.decode('utf-8')}")

    def assertComponents(self, checkout: dist.DistributedCheckout, version: str, services: Any,
                         stack: Optional[Dict[str, e2e.Container]] = None) -> Dict[str, e2e.Container]:
        """Each of `services` runs the published `version` image of its
        component: by tag, OCI label and signed registry digest."""

        assert H is not None
        stack = stack if stack is not None else checkout.stack()
        missing = [s for s in services if s not in stack]
        self.assertEqual(missing, [], f"no container for {missing} in {sorted(stack)}")
        infos = checkout.images_info(stack[s].image_id for s in services)
        for service in services:
            component = dist.SERVICES[service]
            container = stack[service]
            with self.subTest(service=service, version=version):
                self.assertTrue(container.running, f"{service} is {container.status}")
                self.assertEqual(container.image, H.dist_image(component, version))
                info = infos[container.image_id]
                self.assertEqual(info["version"], version)
                digest = H.dist_digests[(component, version)]
                self.assertIn(f"{H.registry}/{dist.COMPONENTS[component][0]}@{digest}", info["repo_digests"])
        return stack

    def assertBootstrap(self, checkout: dist.DistributedCheckout, version: str, *, check_only: bool,
                        stack: Optional[Dict[str, e2e.Container]] = None) -> List[str]:
        """The one-shot schema job ran `version` to completion (exit 0),
        check-only or not; returns its run history (one line per run)."""

        assert H is not None
        stack = stack if stack is not None else checkout.stack()
        self.assertIn(dist.BOOTSTRAP, stack)
        record = checkout.inspect(stack[dist.BOOTSTRAP].id)
        state = record["State"]
        self.assertEqual((state["Status"], state["ExitCode"]), ("exited", 0), json.dumps(state))
        self.assertEqual(record["Config"]["Image"], H.dist_image("backend", version))
        self.assertEqual(record["Config"]["Labels"].get("org.opencontainers.image.version"), version)
        command = [*(record["Config"].get("Entrypoint") or []), *(record["Config"].get("Cmd") or [])]
        self.assertEqual("--check" in command, check_only, command)
        history = checkout.bootstrap_history()
        args = ("migrate", "--check", "--noinput") if check_only else ("migrate", "--noinput")
        self.assertEqual(history[-1:], [bootstrap_line(version, *args)], history)
        return history

    def assertServes(self, checkout: dist.DistributedCheckout, version: str) -> None:
        checkout.wait_healthy(timeout=90)
        self.assertEqual((e2e.http_text(checkout.url("/version.txt")) or "").strip(), f"backend {version} ok")
        self.assertIn(f"frontend {version}", e2e.http_text(checkout.url("/", ui=True)) or "")

    def assertNoSecrets(self, checkout: dist.DistributedCheckout, *values: str) -> None:
        for path in checkout.receipt_paths():
            text = path.read_text(encoding="utf-8")
            for value in values:
                if value:
                    self.assertNotIn(value, text, f"{path} leaks a secret")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, path)
        self.assertEqual(stat.S_IMODE(checkout.state_dir.stat().st_mode), 0o700)

    # ------------------------------------------------------------- tests --
    def test_00_the_fixture_compose_files_resolve_only_when_interpolated_on_older_compose(self):
        """The fixture carries what Compose before v2.40.3 cannot read
        without interpolating: binds whose host path is a variable with a
        default (./${AGENTCC_CONFIG_PATH:-...} on the gateway,
        ${GOOGLE_APPLICATION_CREDENTIALS:-/dev/null} on the profile's runner),
        and the production overlay's required ${VAR:?} pins. Interpolated,
        with every profile on (as bin/update asks Compose), both stacks
        resolve; `config --no-interpolate` fails on such a Compose. So a
        bin/update that needed --no-interpolate would fail this suite's other
        tests on the Compose this run uses. With the profile off, `config
        --services` leaves its runner out on every Compose, while Compose
        v2.24.7's `config --format json` still lists it: on that release the
        other tests show bin/update keeps such a service inactive (not active,
        not an orphan) and still moves its pin."""

        assert H is not None
        found = re.match(r"v?(\d+)\.(\d+)\.(\d+)", H.compose_version)
        self.assertIsNotNone(found, H.compose_description)
        assert found is not None
        version = tuple(int(part) for part in found.groups())
        e2e.log(f"test_00 runs {H.compose_description}")

        def compose_config(co: dist.DistributedCheckout, *args: str, **env: str) -> e2e.Result:
            assert H is not None
            return e2e.run([*H.compose, *co.compose_prefix(), "config", *args], cwd=co.root,
                           env=e2e.clean_environ(env), check=False, timeout=120)

        for production in (False, True):
            co = self.checkout("fixture-prod" if production else "fixture", production=production)
            config = functools.partial(compose_config, co)
            with self.subTest("every profile on, interpolated: both binds resolve", production=production):
                result = config("--format", "json", COMPOSE_PROFILES="workers")
                self.assertEqual(result.code, 0, result.describe())
                services = json.loads(result.stdout)["services"]
                binds = {(name, volume.get("target")): volume.get("source") for name in ("agentcc-gateway",
                                                                                       "worker-simulation-runner")
                         for volume in services[name].get("volumes") or [] if volume.get("type") == "bind"}
                self.assertEqual(binds.get(("agentcc-gateway", "/app/config.yaml")),
                                 str(co.root / "agentcc-gateway/config.example.yaml"), binds)
                self.assertEqual(binds.get(("worker-simulation-runner", "/app/backend/Vertex_AI_Creds.json")),
                                 "/dev/null", binds)
            with self.subTest("profile off: `config --services` leaves its runner out", production=production):
                result = config("--services")
                self.assertEqual(result.code, 0, result.describe())
                self.assertEqual(sorted(result.stdout.split()),
                                 sorted([*DEFAULT_SERVICES, dist.BOOTSTRAP, *dist.DATASTORES]), result.describe())
                result = config("--format", "json")
                self.assertEqual(result.code, 0, result.describe())
                listed = "worker-simulation-runner" in json.loads(result.stdout)["services"]
                e2e.log(f"profile off, `config --format json` ({'production' if production else 'distributed'}) "
                        f"{'lists' if listed else 'leaves out'} worker-simulation-runner")
                if version == (2, 24, 7):
                    # The one release from v2.24.0 to v5.6.0 known to do this: a run on it must exercise it.
                    self.assertTrue(listed, "Compose 2.24.7's `config --format json` lists disabled profiles' services")
            if production:
                with self.subTest("the overlay's image pins are required"):
                    result = config("--format", "json", FUTURE_AGI_VERSION="")
                    self.assertNotEqual(result.code, 0, result.describe())
                    self.assertIn("must be set for production", result.stderr, result.describe())
            with self.subTest("uninterpolated", production=production, compose=H.compose_version):
                result = config("--no-interpolate", "--format", "json")
                if version >= (2, 40, 3):
                    self.assertEqual(result.code, 0, result.describe())
                else:
                    self.assertNotEqual(result.code, 0, result.describe())
                    e2e.log(f"`config --no-interpolate` ({'production' if production else 'distributed'}) fails "
                            f"as expected: {result.stderr.strip().splitlines()[-1:]}")

    @e2e.slow
    def test_01_update_moves_every_component_and_rollback_restores_the_local_collector(self):
        """A Distributed install at v1.51.0 with the collector built locally and
        the workers profile on: check notes the local collector, apply moves
        every component to v1.52.0, rollback puts the previous keys back."""

        assert H is not None
        co = self.checkout("main", versions=dist.pinned(V51, collector=None, runner=None), profiles="workers")
        co.install()
        original = co.env_bytes()

        stack = co.stack()
        with self.subTest("the install runs the locally built collector and the profile's runner"):
            collector = stack["fi-collector"]
            self.assertEqual((collector.image, collector.image_id), (H.collector_local_ref, H.collector_local_id))
            runner = stack["worker-simulation-runner"]
            self.assertEqual(runner.image, H.dist_image("simulation_runner", "latest"))
            self.assertEqual(co.images_info([runner.image_id])[runner.image_id]["version"], V51)
            self.assertEqual(self.assertBootstrap(co, V51, check_only=False, stack=stack),
                             [bootstrap_line(V51, "migrate", "--noinput")])
        datastores = {name: (stack[name].id, stack[name].started_at) for name in dist.DATASTORES}
        before = co.snapshot()

        # ------------------------------------------------------- check --
        result = co.bin_update("check", *manifest(V52))
        self.assertExit(result, 0, stream="stdout")
        for line in (
            f"  Installation  distributed (Compose project {co.project}, .env)",
            f"  Installed     {V51}",
            f"  Signature     verified (public key: {H.trusted_pub})",
            f"  Decision      update available: run ./bin/update apply {release_options(V52)}",
            f"  · {LOCAL_NOTE}",
        ):
            self.assertIn(line + "\n", result.stdout, result.describe())
        self.assertRegex(result.stdout, rf"\n    fi_collector\s+\?\s+->\s+{re.escape(V52)}\s")
        self.assertRegex(result.stdout, rf"\n    simulation_runner\s+{re.escape(V51)}\s+->\s+{re.escape(V52)}\s")
        self.assertNotIn("✗", result.stdout)

        result = co.bin_update("check", "--format", "json", *manifest(V52))
        self.assertExit(result, 0, stream="stdout")
        plan = json.loads(result.stdout)
        self.assertEqual(plan["decision"], "update_available")
        self.assertEqual(plan["installation"], {"mode": "distributed", "project": co.project, "env_file": ".env",
                                                "root": str(co.root)})
        self.assertEqual(plan["current_versions"], [V51])
        self.assertEqual(plan["env_changes"], {key: V52 for key in VERSION_KEYS})
        self.assertEqual((plan["reasons"], plan["deployment_drift"], plan["missing_env_keys"]), ([], [], []))
        self.assertIn(LOCAL_NOTE, plan["notes"])
        components = {item["component"]: item for item in plan["components"]}
        self.assertEqual(sorted(components), sorted(dist.COMPONENTS))
        for name, item in components.items():
            self.assertEqual((item["action"], item["target_tag"], item["target_digest"]),
                             ("update", V52, H.dist_digests[(name, V52)]), name)
        self.assertEqual(components["backend"]["services"], ["backend", dist.BOOTSTRAP, "worker"])
        self.assertEqual((components["fi_collector"]["current_version"], components["fi_collector"]["current_source"],
                          components["fi_collector"]["current_tags"]), (None, "built from this checkout", ["local"]))
        self.assertEqual((components["simulation_runner"]["current_version"],
                          components["simulation_runner"]["current_source"],
                          components["simulation_runner"]["services"]),
                         (V51, "running image (follows :latest)", ["worker-simulation-runner"]))
        self.assertEqual(components["frontend"]["current_source"], "FRONTEND_VERSION in .env")
        self.assertUnchanged(co, before)

        # ------------------------------------------------------- apply --
        result = co.bin_update("apply", *manifest(V52), "--allow-no-backup", timeout=1200)
        self.assertExit(result, 0, f"Updated to {V52}. Receipt:")

        with self.subTest("all seven version keys are written; nothing else in .env changes"):
            self.assertOnlyVersionKeysChanged(original, co.env_bytes(), {key: [V52] for key in VERSION_KEYS})
            self.assertEqual(stat.S_IMODE(co.env_path.stat().st_mode), 0o600)
            self.assertEqual(co.temp_env_files(), [])
        stack = co.stack()
        with self.subTest("every component, the profile's runner too, runs the signed v1.52.0 image"):
            self.assertComponents(co, V52, APP_SERVICES, stack)
        with self.subTest("fi-collector now runs the published image, not the local build"):
            self.assertNotEqual(stack["fi-collector"].image_id, H.collector_local_id)
            self.assertEqual(stack["fi-collector"].image, H.dist_image("fi_collector", V52))
        with self.subTest("the bootstrap job re-ran on v1.52.0 and exited 0"):
            history = self.assertBootstrap(co, V52, check_only=False, stack=stack)
            self.assertEqual(history, [bootstrap_line(V51, "migrate", "--noinput"),
                                       bootstrap_line(V52, "migrate", "--noinput")])
        with self.subTest("/health/ answers from v1.52.0"):
            self.assertServes(co, V52)
        with self.subTest("datastores are not restarted"):
            self.assertEqual({n: (stack[n].id, stack[n].started_at) for n in dist.DATASTORES}, datastores)
        with self.subTest("receipt"):
            self.assertIsNone(co.journal())
            receipt = co.receipts()[-1]
            self.assertEqual((receipt["kind"], receipt["status"], receipt["mode"], receipt["production"],
                              receipt["env_file"], receipt["project"], receipt["target_version"]),
                             ("apply", "succeeded", "distributed", False, ".env", co.project, V52))
            self.assertEqual(receipt["env_changes"], {key: V52 for key in VERSION_KEYS})
            expected_previous: Dict[str, Optional[str]] = {key: V51 for key in VERSION_KEYS}
            expected_previous.update(FI_COLLECTOR_VERSION=None, SIMULATION_RUNNER_VERSION=None)
            self.assertEqual(receipt["previous_values"], expected_previous)
            previous_collector = receipt["previous_images"]["fi-collector"]
            self.assertEqual((previous_collector["image"], previous_collector["image_id"],
                              previous_collector["repo_digests"], previous_collector["version"]),
                             (H.collector_local_ref, H.collector_local_id, H.collector_local_repo_digests, ""))
            self.assertNoSecrets(co, co.secret, co.pg_password)

        result = co.bin_update("check", *manifest(V52))
        self.assertExit(result, 0, "  Decision      up to date, nothing to do\n", stream="stdout")
        self.assertRegex(result.stdout, rf"\n    fi_collector\s+{re.escape(V52)}\s+keep\s+{re.escape(V52)}\s")

        # ---------------------------------------------------- rollback --
        with self.subTest("rollback --yes restores the previous keys and the local collector"):
            result = co.bin_update("rollback", "--yes", timeout=1200)
            self.assertExit(result, 0, "Rolled back to")
            rest_before, _ = dist.split_env(original.decode("utf-8"))
            rest_now, values = dist.split_env(co.env_text())
            self.assertEqual(rest_now, rest_before)
            for key in ACTIVE_KEYS:
                if key != "FI_COLLECTOR_VERSION":
                    self.assertEqual(values.get(key), [V51], key)
            # FI_COLLECTOR_VERSION is unset again (or local): the collector is the local build.
            self.assertIn(values.get("FI_COLLECTOR_VERSION", []), ([], [""], ["local"]))
            # The runner followed :latest; rollback pins it to the version that ran.
            self.assertEqual(values.get("SIMULATION_RUNNER_VERSION"), [V51])
            stack = co.stack()
            collector = stack["fi-collector"]
            self.assertTrue(collector.running)
            self.assertEqual((collector.image, collector.image_id), (H.collector_local_ref, H.collector_local_id))
            self.assertComponents(co, V51, [s for s in APP_SERVICES if s != "fi-collector"], stack)
            self.assertBootstrap(co, V51, check_only=False, stack=stack)
            self.assertServes(co, V51)
            receipt = co.receipts()[-1]
            self.assertEqual((receipt["kind"], receipt["status"]), ("rollback", "rolled_back"))

    @e2e.slow
    def test_02_cross_mode_refusals_then_an_unhealthy_component_fails_during_rollout(self):
        """Every way of pointing bin/update at the wrong stack is refused and
        changes nothing; then a release whose agentcc-gateway never turns
        healthy fails during the rollout (exit 5) and rollback recovers."""

        assert H is not None
        co = self.checkout("bad", versions=dist.pinned(V51, runner=None))
        co.install()
        original = co.env_bytes()
        before = co.snapshot()

        with self.subTest("--env-file without --production"):
            result = co.bin_update("check", "--env-file", ".env.production", *manifest(V52))
            self.assertExit(result, 2, "--env-file is only for production installs (--production)")
        with self.subTest("--production without deploy/.env.production"):
            result = co.bin_update("--production", "check", *manifest(V52))
            self.assertExit(result, 2, "deploy/.env.production does not exist. Production installs are "
                                       "configured by deploy/setup.sh")
        with self.subTest("--production on an install started without the overlay"):
            production = co.production_env_path
            production.write_text(dist.production_env(
                co.project, versions=dist.pinned(V51), backend_port=co.backend_port, frontend_port=co.frontend_port,
                secret=co.secret, pg_password="cross mode pg", admin_token="cross-mode-admin-token"), encoding="utf-8")
            production.chmod(0o600)
            try:
                staged = co.snapshot()
                # The ownership gate precedes planning: selecting an overlay
                # absent from the containers' provenance cannot authorize apply.
                result = co.bin_update("apply", "--production", *manifest(V52), "--allow-no-backup")
                self.assertExit(result, 2, f"cannot establish Compose project {co.project!r} ownership",
                                "records different config_files", str(co.root / dist.DISTRIBUTED_FILE),
                                "Refusing to use this project's containers", "original Compose files and env file")
                self.assertUnchanged(co, staged)
            finally:
                production.unlink()
        with self.subTest("COMPOSE_FILE exported in the shell"):
            result = co.bin_update("check", *manifest(V52), env={"COMPOSE_FILE": "docker-compose.yml"})
            self.assertExit(result, 2, "COMPOSE_FILE=docker-compose.yml is set in your shell")
        with self.subTest("a version key exported in the shell"):
            result = co.bin_update("check", *manifest(V52), env={"FRONTEND_VERSION": V52})
            self.assertExit(result, 2, "FRONTEND_VERSION is exported in your shell and would override .env")
        with self.subTest(".env selects Standalone while the project runs the Distributed stack"):
            standalone = re.sub(r"(?m)^COMPOSE_FILE=.*\n", "", original.decode("utf-8"))
            self.assertNotIn("COMPOSE_FILE=", standalone)
            co.env_path.write_text(standalone, encoding="utf-8")
            try:
                staged = co.snapshot()
                result = co.bin_update("apply", *manifest(V52), "--allow-no-backup")
                self.assertExit(result, 2, f"cannot establish Compose project {co.project!r} ownership",
                                "records different config_files", str(co.root / dist.DISTRIBUTED_FILE),
                                "Refusing to use this project's containers", "original Compose files and env file")
                self.assertUnchanged(co, staged)
            finally:
                co.env_path.write_bytes(original)
        self.assertUnchanged(co, before)

        # ------------------------------------------ an unhealthy release --
        result = co.bin_update("apply", *manifest(V53), "--allow-no-backup", timeout=1200)
        self.assertExit(result, 5, f"apply to {V53} stopped while containers were being replaced",
                        f"The env file selects {V53}", "./bin/update rollback --receipt")
        failed = co.receipts()[-1]
        e2e.log(f"exit-5 receipt error: {failed.get('error')}")
        with self.subTest("the env file keeps the new keys (they match what runs)"):
            self.assertOnlyVersionKeysChanged(original, co.env_bytes(), {key: [V53] for key in ACTIVE_KEYS})
        with self.subTest("the unhealthy component is the v1.53.0 gateway"):
            gateway = co.service("agentcc-gateway")
            self.assertEqual(gateway.image, H.dist_image("agentcc_gateway", V53))
            self.assertEqual(gateway.health, "unhealthy")
            self.assertIn("agentcc-gateway", result.stderr)
            self.assertIn("unhealthy", result.stderr)
        self.assertEqual((failed["kind"], failed["status"], failed["stage"], failed["target_version"]),
                         ("apply", "failed_during_rollout", "rollout", V53))
        self.assertIsNone(co.journal())
        self.assertIn(failed["_path"], result.stderr)

        result = co.bin_update("rollback", "--yes", timeout=1200)
        self.assertExit(result, 0, f"Rolled back to {V51}")
        with self.subTest("rollback restores .env byte for byte"):
            self.assertEqual(co.env_bytes(), original)
        with self.subTest("every component runs v1.51.0 again, the gateway healthy"):
            e2e.wait_for(lambda: co.service("agentcc-gateway").health == "healthy", timeout=120,
                         what="the v1.51.0 gateway to turn healthy")
            stack = self.assertComponents(co, V51, DEFAULT_SERVICES)
            self.assertBootstrap(co, V51, check_only=False, stack=stack)
            self.assertServes(co, V51)
        receipt = co.receipts()[-1]
        self.assertEqual((receipt["kind"], receipt["status"], receipt["rolls_back"]),
                         ("rollback", "rolled_back", failed["_path"]))
        self.assertNoSecrets(co, co.secret, co.pg_password)

    @e2e.slow
    def test_03_production_update_and_rollback_use_the_production_files(self):
        """A deploy/setup.sh install: bin/update --production updates in two
        steps around the release's initialization. A plain apply is refused
        before the backup; apply --stage backs up, rewrites only the pins in
        deploy/.env.production (the inactive profile's too), pulls and
        verifies, and restarts nothing; the initialization job then runs on
        the staged image; apply finishes without a second backup, keeps the
        bootstrap check-only and never lets the shell override the file;
        rollback restores every pin. Without --production it is refused."""

        assert H is not None
        co = self.checkout("prod", production=True)
        co.install()
        original = co.env_bytes()
        self.assertFalse(co.dotenv_path.exists())
        with self.subTest("deploy/setup.sh initialized v1.51.0; the overlay's bootstrap job only checks"):
            self.assertEqual(self.assertBootstrap(co, V51, check_only=True),
                             [bootstrap_line(V51, "migrate", "--noinput"),
                              bootstrap_line(V51, "migrate", "--check", "--noinput")])
        before = co.snapshot()
        hook, backups = self.backup_hook("prod")
        # Exported in the operator's shell; deploy/setup.sh ignores them, so must bin/update.
        shell = {"PG_PASSWORD": "shell-pg-password-must-not-win", "SECRET_KEY": "shell-secret-key-must-not-win",
                 "FUTURE_AGI_VERSION": V51, "FRONTEND_VERSION": V51}

        with self.subTest("without --production (no .env): refused"):
            result = co.bin_update("check", *manifest(V52))
            self.assertExit(result, 2, "no .env in")
            self.assertUnchanged(co, before)
        with self.subTest("without --production (no .env): the refusal points at --production"):
            self.assertIn("--production", result.stderr, result.describe())
        with self.subTest("without --production (.env present): production overlay detected"):
            co.dotenv_path.write_text(f"COMPOSE_PROJECT_NAME={co.project}\nCOMPOSE_FILE={dist.DISTRIBUTED_FILE}\n",
                                      encoding="utf-8")
            try:
                staged = co.snapshot()
                result = co.bin_update("apply", *manifest(V52), "--allow-no-backup")
                self.assertExit(result, 2, f"cannot establish Compose project {co.project!r} ownership",
                                "records different config_files", str(co.root / dist.PRODUCTION_OVERLAY),
                                "Refusing to use this project's containers", "with its original env file")
                # No planning against a different file set; retain the runnable
                # production guidance and the exact release-option assertions.
                self.assertNotIn(".env lacks settings", result.stderr, result.describe())
                self.assertEqual(result.stderr.count("cannot establish Compose project"), 1, result.describe())
                printed = re.search(r"use `([^`]+)` with its original env file", result.stderr)
                self.assertIsNotNone(printed, result.describe())
                self.assertEqual(printed.group(1), f"./bin/update --production check {release_options(V52)}")
                self.assertUnchanged(co, staged)
            finally:
                co.dotenv_path.unlink()
        with self.subTest("the printed production check, pasted, checks the release this run was given"):
            result = co.paste(printed.group(1))
            self.assertExit(result, 0, "  Decision      update available: run ./bin/update --production apply --stage "
                                       f"{release_options(V52)} (the first of two steps, see below)\n", stream="stdout")
        self.assertUnchanged(co, before)

        # ------------------------------------------------------- check --
        result = co.bin_update("--production", "check", *manifest(V52), env=shell)
        self.assertExit(result, 0, "  Decision      update available: run ./bin/update --production apply --stage "
                                   f"{release_options(V52)} (the first of two steps, see below)\n", stream="stdout")
        result = co.bin_update("--production", "check", "--format", "json", *manifest(V52), env=shell)
        self.assertExit(result, 0, stream="stdout")
        plan = json.loads(result.stdout)
        self.assertEqual((plan["decision"], plan["reasons"], plan["resume"]), ("update_available", [], False))
        self.assertEqual(plan["next_command"], f"./bin/update --production apply --stage {release_options(V52)}")
        self.assertEqual(plan["installation"], {"mode": "production", "project": co.project,
                                                "env_file": dist.PRODUCTION_ENV, "root": str(co.root)})
        self.assertEqual(plan["current_versions"], [V51])
        self.assertEqual({k: v for k, v in plan["env_changes"].items() if k in ACTIVE_KEYS},
                         {key: V52 for key in ACTIVE_KEYS})
        with self.subTest("check plans every pin of deploy/.env.production, the inactive-profile runner's too"):
            self.assertEqual(plan["env_changes"], {key: V52 for key in VERSION_KEYS})
            runner = {item["component"]: item for item in plan["components"]}["simulation_runner"]
            self.assertEqual((runner["inactive"], runner["services"], runner["action"]), (True, [], "update"))
        self.assertUnchanged(co, before)

        # ----------------------- a plain apply is refused before the backup --
        result = co.bin_update("--production", "apply", *manifest(V52), "--backup-command", str(hook), env=shell)
        self.assertExit(result, 3)
        self.assertRegex(result.stderr, f"bin/update: {two_steps(V52)}\n")
        self.assertEqual(backups(), [])
        self.assertUnchanged(co, before)
        with self.subTest("apply --stage --dry-run changes nothing"):
            result = co.bin_update("--production", "apply", "--stage", "--dry-run", *manifest(V52),
                                   "--backup-command", str(hook), env=shell)
            self.assertExit(result, 0, "Dry run: nothing was changed.", "stop before restarting anything (staged)")
            self.assertEqual(backups(), [])
            self.assertUnchanged(co, before)

        # ----------------------------------------------- step 1: stage --
        result = co.bin_update("--production", "apply", "--stage", *manifest(V52), "--backup-command", str(hook),
                               env=shell, timeout=1200)
        self.assertExit(result, 0, f"Staged {V52}: {dist.PRODUCTION_ENV} now selects it (",
                        "Nothing was restarted: the stack still runs the previous versions.",
                        "Next, run the release's initialization jobs",
                        f"Then run `./bin/update --production apply {release_options(V52)}` to restart the stack on "
                        f"{V52}.")
        self.assertRegex(result.stderr, "Next, run the release's initialization jobs, which now use these images, "
                                        f"with {jobs_pattern()}\\.")
        staged = co.receipts()[-1]
        self.assertIn(f"run `./bin/update --production rollback --receipt {staged['_path']} --yes`", result.stderr)
        with self.subTest("the backup ran once, before anything changed"):
            self.assertEqual(backups(), [V52])
        with self.subTest("every pin is written, the inactive profile's too; nothing else in the file changes"):
            self.assertOnlyVersionKeysChanged(original, co.env_bytes(), {key: [V52] for key in VERSION_KEYS})
            self.assertEqual(stat.S_IMODE(co.env_path.stat().st_mode), 0o600)
            self.assertEqual(co.temp_env_files(), [])
            self.assertFalse(co.dotenv_path.exists())
        with self.subTest("nothing was restarted"):
            self.assertEqual(co.snapshot()["containers"], before["containers"])
            self.assertComponents(co, V51, DEFAULT_SERVICES)
        with self.subTest("the release's images are on the host, by their signed digests"):
            self.assertStaged(co, V52, DEFAULT_SERVICES)
        self.assertEqual((staged["kind"], staged["status"], staged["mode"], staged["production"], staged["env_file"],
                          staged["target_version"]), ("apply", "staged", "production", True, dist.PRODUCTION_ENV, V52))
        self.assertTrue(staged["backup"]["ran"], staged["backup"])
        self.assertEqual(staged["previous_values"], {key: V51 for key in VERSION_KEYS})
        self.assertIsNone(co.journal())

        result = co.bin_update("--production", "check", "--format", "json", *manifest(V52), env=shell)
        self.assertExit(result, 0, stream="stdout")
        plan = json.loads(result.stdout)
        self.assertEqual((plan["decision"], plan["reasons"], plan["resume"], plan["next_command"]),
                         ("update_available", [], True, f"./bin/update --production apply {release_options(V52)}"))

        # ------------- the release's initialization, on the staged image --
        stack = co.snapshot()["containers"]
        co.initialize()
        with self.subTest("the initialization job ran v1.52.0 (the staged pin) and started nothing else"):
            self.assertEqual(co.bootstrap_history()[-1:], [bootstrap_line(V52, "migrate", "--noinput")])
            self.assertEqual(co.snapshot()["containers"], stack)

        # ---------------------------------------------- step 2: finish --
        e2e.assert_stage_acknowledgment_required(self, co, "--production", "apply", *manifest(V52),
                                               "--backup-command", str(hook), env=shell)
        self.assertEqual(backups(), [V52])
        result = co.bin_update("--production", "apply", *manifest(V52), "--backup-command", str(hook),
                               "--acknowledge-stale-stage", env=shell, timeout=1200)
        self.assertExit(result, 0, f"Skipping the backup: it ran when the update to {V52} was staged "
                                   f"({staged['_path']}).", f"Updated to {V52}. Receipt:")
        with self.subTest("no second backup"):
            self.assertEqual(backups(), [V52])
        with self.subTest("the pins stay as staged, every other byte identical"):
            self.assertOnlyVersionKeysChanged(original, co.env_bytes(), {key: [V52] for key in VERSION_KEYS})
            self.assertEqual(stat.S_IMODE(co.env_path.stat().st_mode), 0o600)
            self.assertEqual(co.temp_env_files(), [])
            self.assertFalse(co.dotenv_path.exists())
        stack = co.stack()
        with self.subTest("deploy/.env.production wins over the shell exports"):
            for service in ("backend", "worker"):
                env = stack[service].env
                self.assertEqual((env.get("PG_PASSWORD"), env.get("SECRET_KEY"), env.get("FUTURE_AGI_VERSION"),
                                  env.get("ENV_TYPE")), (co.pg_password, co.secret, V52, "production"), service)
            self.assertEqual(stack["postgres"].env.get("POSTGRES_PASSWORD"), co.pg_password)
            self.assertEqual(stack["agentcc-gateway"].env.get("AGENTCC_ADMIN_TOKEN"), co.admin_token)
        with self.subTest("every component runs the signed v1.52.0 image"):
            self.assertComponents(co, V52, DEFAULT_SERVICES, stack)
        with self.subTest("the bootstrap job stays check-only, and passes on the initialized schema"):
            history = self.assertBootstrap(co, V52, check_only=True, stack=stack)
            self.assertEqual(history, [bootstrap_line(V51, "migrate", "--noinput"),
                                       bootstrap_line(V51, "migrate", "--check", "--noinput"),
                                       bootstrap_line(V52, "migrate", "--noinput"),
                                       bootstrap_line(V52, "migrate", "--check", "--noinput")])
        with self.subTest("/health/ answers from v1.52.0"):
            self.assertServes(co, V52)
        receipt = co.receipts()[-1]
        self.assertEqual((receipt["kind"], receipt["status"], receipt["mode"], receipt["production"],
                          receipt["env_file"], receipt["project"]),
                         ("apply", "succeeded", "production", True, dist.PRODUCTION_ENV, co.project))
        self.assertEqual((receipt["backup"], receipt["resumes"]), ({"ran": False, "staged": staged["_path"]},
                                                                   staged["_path"]))
        self.assertEqual(receipt["previous_values"], {key: V51 for key in VERSION_KEYS})
        self.assertIsNone(co.journal())
        self.assertNoSecrets(co, co.secret, co.pg_password, co.admin_token)

        result = co.bin_update("--production", "check", *manifest(V52), env=shell)
        self.assertExit(result, 0, "  Decision      up to date, nothing to do\n", stream="stdout")

        # ---------------------------------------------------- rollback --
        result = co.bin_update("rollback", "--production", "--yes", env=shell, timeout=1200)
        self.assertExit(result, 0, f"Rolled back to {V51}", "(Production (Distributed), project")
        with self.subTest("deploy/.env.production is restored byte for byte"):
            self.assertEqual(co.env_bytes(), original)
            self.assertFalse(co.dotenv_path.exists())
        rollback = co.receipts()[-1]
        with self.subTest("SIMULATION_RUNNER_VERSION (inactive profile) is put back too"):
            self.assertEqual(dist.split_env(co.env_text())[1].get("SIMULATION_RUNNER_VERSION"), [V51])
            self.assertEqual(rollback["env_changes"], {key: V51 for key in VERSION_KEYS})
        stack = co.stack()
        with self.subTest("every component runs v1.51.0 again"):
            self.assertComponents(co, V51, DEFAULT_SERVICES, stack)
        with self.subTest("the bootstrap job is still check-only"):
            history = self.assertBootstrap(co, V51, check_only=True, stack=stack)
            self.assertEqual([line for line in history if "--check" not in line],
                             [bootstrap_line(V51, "migrate", "--noinput"), bootstrap_line(V52, "migrate", "--noinput")])
        with self.subTest("the file still wins over the shell"):
            env = stack["backend"].env
            self.assertEqual((env.get("PG_PASSWORD"), env.get("SECRET_KEY"), env.get("FUTURE_AGI_VERSION")),
                             (co.pg_password, co.secret, V51))
        self.assertServes(co, V51)
        self.assertEqual((rollback["kind"], rollback["status"], rollback["production"], rollback["env_file"],
                          rollback["rolls_back"]),
                         ("rollback", "rolled_back", True, dist.PRODUCTION_ENV, receipt["_path"]))
        self.assertNoSecrets(co, co.secret, co.pg_password, co.admin_token)

    @e2e.slow
    def test_04_a_running_container_of_a_service_outside_the_active_profiles_blocks_check(self):
        """The workers profile was on when the stack started and is off now
        (COMPOSE_PROFILES taken out of .env): Compose leaves its runner
        running on the old image, and `up` would never replace it. check and
        apply block before the backup and name both ways out; each one works
        as printed."""

        assert H is not None
        co = self.checkout("stray", versions=dist.pinned(V51), profiles="workers")
        co.install()
        self.assertTrue(co.stack()["worker-simulation-runner"].running)
        with_profile = co.env_bytes()
        without_profile = re.sub(rb"(?m)^COMPOSE_PROFILES=.*\n", b"", with_profile)
        self.assertNotEqual(without_profile, with_profile)
        co.env_path.write_bytes(without_profile)
        before = co.snapshot()
        hook, backups = self.backup_hook("stray")

        result = co.bin_update("check", *manifest(V52))
        self.assertExit(result, 3, "  Decision      blocked (see below)\n", stream="stdout")
        found = re.search(r"\n  ✗ " + STRAY_BLOCKS.format(version=re.escape(V51), target=re.escape(V52)) + r"\n",
                          result.stdout)
        self.assertIsNotNone(found, result.describe())
        assert found is not None
        remedy = found.group(1)
        self.assertUnchanged(co, before)

        result = co.bin_update("apply", *manifest(V52), "--backup-command", str(hook))
        self.assertExit(result, 3, "the update is blocked: ")
        self.assertRegex(result.stderr, STRAY_BLOCKS.format(version=re.escape(V51), target=re.escape(V52)))
        self.assertEqual(backups(), [])
        self.assertUnchanged(co, before)

        with self.subTest("enabling the profile again unblocks it"):
            co.env_path.write_bytes(with_profile)
            try:
                result = co.bin_update("check", "--format", "json", *manifest(V52))
                self.assertExit(result, 0, stream="stdout")
                plan = json.loads(result.stdout)
                self.assertEqual((plan["decision"], plan["reasons"]), ("update_available", []))
            finally:
                co.env_path.write_bytes(without_profile)
        with self.subTest("so does removing the container with the printed command"):
            result = co.paste(remedy, timeout=300)
            self.assertEqual(result.code, 0, result.describe())
            self.assertNotIn("worker-simulation-runner", co.stack())
            result = co.bin_update("check", "--format", "json", *manifest(V52))
            self.assertExit(result, 0, stream="stdout")
            plan = json.loads(result.stdout)
            self.assertEqual((plan["decision"], plan["reasons"]), ("update_available", []))
            runner = {item["component"]: item for item in plan["components"]}["simulation_runner"]
            self.assertEqual((runner["inactive"], runner["services"], runner["target_tag"]), (True, [], V52))
        self.assertEqual(backups(), [])

    @e2e.slow
    def test_05_a_compose_override_that_hardcodes_an_image_tag_blocks_before_the_backup(self):
        """docker-compose.override.yml (in COMPOSE_FILE) names the images of
        serving and of worker with the very tags .env selects today, so the
        resolved images look right, but writing SERVING_VERSION and
        FUTURE_AGI_VERSION would not move them (worker's siblings backend and
        the bootstrap job do follow FUTURE_AGI_VERSION). Only resolving the
        files with each version key at a placeholder shows that those two
        follow no key. check blocks with both, and apply blocks before its
        backup; once the override follows the keys, as the reasons say, check
        and apply agree and the update goes through."""

        assert H is not None
        co = self.checkout("override", versions=dist.pinned(V51, runner=None))
        override = co.root / "docker-compose.override.yml"
        fixed = {"serving": H.dist_image("serving", V51), "worker": H.dist_image("backend", V51)}
        override.write_text("# A GPU host's override (bin/update e2e fixture): these images are set here.\n"
                            f"services:\n  serving:\n    image: {fixed['serving']}\n"
                            f"  worker:\n    image: {fixed['worker']}\n", encoding="utf-8")
        listed = f"COMPOSE_FILE={dist.DISTRIBUTED_FILE}:docker-compose.override.yml\n"
        text = co.env_text().replace(f"COMPOSE_FILE={dist.DISTRIBUTED_FILE}\n", listed)
        self.assertIn(listed, text)
        co.env_path.write_text(text, encoding="utf-8")
        co.install()
        stack = co.stack()
        self.assertEqual({name: stack[name].image for name in fixed}, fixed)
        # What `config` resolves today is exactly what the keys would select: nothing but the keys can tell.
        self.assertEqual(stack["backend"].image, fixed["worker"])
        before = co.snapshot()
        hook, backups = self.backup_hook("override")
        reasons = [
            f"service {name}'s image is set directly in a compose file ({fixed[name]}), not through {variable}: "
            f"bin/update can only change {variable} in .env. Make {name} use it (image: {H.registry}/{repository}:"
            f"${{{variable}}}) or remove that image setting (a docker-compose.override.yml, say), then check again"
            for name, repository, variable in (("serving", "futureagi/serving", "SERVING_VERSION"),
                                               ("worker", "futureagi/future-agi", "FUTURE_AGI_VERSION"))]

        result = co.bin_update("check", *manifest(V52))
        self.assertExit(result, 3, "  Decision      blocked (see below)\n", *(f"  ✗ {reason}\n" for reason in reasons),
                        stream="stdout")
        result = co.bin_update("check", "--format", "json", *manifest(V52))
        self.assertExit(result, 3, stream="stdout")
        plan = json.loads(result.stdout)
        self.assertEqual((plan["decision"], sorted(plan["reasons"])), ("blocked", sorted(reasons)))
        self.assertUnchanged(co, before)
        result = co.bin_update("apply", *manifest(V52), "--backup-command", str(hook))
        self.assertExit(result, 3, "the update is blocked: ", *reasons)
        self.assertEqual(backups(), [])
        self.assertUnchanged(co, before)

        # The fix the reasons name: the override's images follow SERVING_VERSION and FUTURE_AGI_VERSION.
        override.write_text(f"services:\n  serving:\n    image: {H.registry}/futureagi/serving:${{SERVING_VERSION}}\n"
                            f"  worker:\n    image: {H.registry}/futureagi/future-agi:${{FUTURE_AGI_VERSION}}\n",
                            encoding="utf-8")
        result = co.bin_update("check", "--format", "json", *manifest(V52))
        self.assertExit(result, 0, stream="stdout")
        plan = json.loads(result.stdout)
        self.assertEqual((plan["decision"], plan["reasons"]), ("update_available", []))
        result = co.bin_update("apply", *manifest(V52), "--backup-command", str(hook), timeout=1200)
        self.assertExit(result, 0, f"Updated to {V52}. Receipt:")
        self.assertEqual(backups(), [V52])
        self.assertComponents(co, V52, DEFAULT_SERVICES)
        self.assertServes(co, V52)

    @e2e.slow
    def test_06_a_staged_update_blocks_a_newer_release_and_rolls_back_without_a_restart(self):
        """While v1.52.0 is staged on a production install, a newer release is
        blocked with both ways out named; the rollback printed there, pasted,
        puts the old pins back without replacing a single container."""

        assert H is not None
        co = self.checkout("staged", production=True)
        co.install()
        original = co.env_bytes()
        before = co.snapshot()
        result = co.bin_update("--production", "apply", "--stage", *manifest(V52), "--allow-no-backup", timeout=1200)
        self.assertExit(result, 0, f"Staged {V52}: {dist.PRODUCTION_ENV} now selects it (")
        staged = co.receipts()[-1]
        self.assertEqual((staged["kind"], staged["status"], staged["backup"]), ("apply", "staged", {"ran": False}))
        self.assertEqual(co.snapshot()["containers"], before["containers"])
        self.assertStaged(co, V52, DEFAULT_SERVICES)
        pending = co.snapshot()

        result = co.bin_update("--production", "check", *manifest(V53))
        self.assertExit(result, 3, stream="stdout")
        # Finishing it takes v1.52.0's deployment files back (git checkout), its initialization jobs, then its apply.
        self.assertRegex(result.stdout, re.escape(
            f"  ✗ the update to {V52} is staged (its images are pulled and verified, {dist.PRODUCTION_ENV} selects "
            f"them, and nothing was restarted). Finish it first (check out {V52} with `git checkout {V52}`, or at "
            "least its deployment files (your env file stays as it is), run its initialization jobs with ")
            + jobs_pattern() + re.escape(" if they have not run yet, then ./bin/update --production apply --manifest "))
        finish = re.search(r"then (\./bin/update --production apply --manifest \S+)\) or undo it", result.stdout)
        undo = re.search(r" or undo it \((\./bin/update --production rollback --receipt \S+ --yes)\), then check "
                         r"again\n", result.stdout)
        self.assertIsNotNone(finish, result.describe())
        self.assertIsNotNone(undo, result.describe())
        assert finish is not None and undo is not None
        self.assertEqual(shlex.split(finish.group(1))[-1], str(H.dist_release(V52).manifest))
        self.assertEqual(shlex.split(undo.group(1))[4], staged["_path"])
        self.assertUnchanged(co, pending)
        result = co.bin_update("--production", "apply", "--stage", *manifest(V53), "--allow-no-backup")
        self.assertExit(result, 3, "the update is blocked: ", f"the update to {V52} is staged")
        self.assertUnchanged(co, pending)

        # The way back, pasted as printed: the old pins return, and no container is replaced.
        result = co.paste(undo.group(1))
        self.assertEqual(result.code, 0, result.describe())
        self.assertIn(f"Rolled back to {V51}", result.stderr)
        self.assertEqual(co.env_bytes(), original)
        self.assertEqual(co.temp_env_files(), [])
        unchanged = lambda containers: sorted((c[0], c[1], c[2]) for c in containers)  # noqa: E731
        self.assertEqual(unchanged(co.snapshot()["containers"]), unchanged(before["containers"]))
        running = [c for c in before["containers"] if c[0] != dist.BOOTSTRAP]
        self.assertTrue(set(running) <= set(co.snapshot()["containers"]), "a running container was restarted")
        self.assertComponents(co, V51, DEFAULT_SERVICES)
        rollback = co.receipts()[-1]
        self.assertEqual((rollback["kind"], rollback["status"], rollback["rolls_back"]),
                         ("rollback", "rolled_back", staged["_path"]))
        self.assertIsNone(co.journal())

        result = co.bin_update("--production", "check", "--format", "json", *manifest(V52))
        self.assertExit(result, 0, stream="stdout")
        plan = json.loads(result.stdout)
        self.assertEqual((plan["decision"], plan["resume"], plan["next_command"]),
                         ("update_available", False, f"./bin/update --production apply --stage {release_options(V52)}"))

    @e2e.slow
    def test_07_an_orphan_of_a_retired_service_is_noted_and_left_running(self):
        """The stack was started from an older release's compose file, which
        still defined a service on the backend image; this release's file no
        longer does, so `up` left that container running (an orphan). check
        names it in a note and does not block; apply and rollback finish
        without touching it; the removal the note prints works as pasted."""

        assert H is not None
        co = self.checkout("orphan", versions=dist.pinned(V51, runner=None))
        up = co.install_older_release()
        with self.subTest("Compose left the retired service's container running"):
            self.assertIn("orphan", up.output.lower(), up.describe())
            orphan = co.stack()[dist.RETIRED_SERVICE]
            self.assertTrue(orphan.running, orphan.status)
            self.assertEqual(orphan.image, H.dist_image("backend", V51))
            config = json.loads(co.compose("config", "--format", "json").stdout)
            self.assertNotIn(dist.RETIRED_SERVICE, config["services"])
        original = co.env_bytes()
        before = co.snapshot()
        # The orphan as check names it: its service, state and version, and the command that removes it.
        note = (f"service {dist.RETIRED_SERVICE} (running, {V51}) is not defined by this release's compose files any "
                "more; bin/update leaves its containers as they are. Once its retirement is approved, remove them "
                f"with `docker rm -f {orphan.id[:12]}`")

        result = co.bin_update("check", *manifest(V52))
        self.assertExit(result, 0, f"  Decision      update available: run ./bin/update apply {release_options(V52)}\n",
                        f"  · {note}\n", stream="stdout")
        self.assertNotIn("✗", result.stdout)
        result = co.bin_update("check", "--format", "json", *manifest(V52))
        self.assertExit(result, 0, stream="stdout")
        plan = json.loads(result.stdout)
        self.assertEqual((plan["decision"], plan["reasons"], plan["next_command"]),
                         ("update_available", [], f"./bin/update apply {release_options(V52)}"))
        self.assertIn(note, plan["notes"])
        self.assertUnchanged(co, before)

        result = co.bin_update("apply", *manifest(V52), "--allow-no-backup", timeout=1200)
        self.assertExit(result, 0, f"Updated to {V52}. Receipt:")
        stack = co.stack()
        with self.subTest("every component runs v1.52.0"):
            self.assertComponents(co, V52, DEFAULT_SERVICES, stack)
            self.assertServes(co, V52)
        with self.subTest("the orphan is the same container, still running v1.51.0"):
            self.assertEqual((stack[dist.RETIRED_SERVICE].id, stack[dist.RETIRED_SERVICE].started_at,
                              stack[dist.RETIRED_SERVICE].status), (orphan.id, orphan.started_at, "running"))
            self.assertEqual(stack[dist.RETIRED_SERVICE].image, H.dist_image("backend", V51))
        self.assertOnlyVersionKeysChanged(original, co.env_bytes(), {key: [V52] for key in ACTIVE_KEYS})
        self.assertEqual((co.receipts()[-1]["kind"], co.receipts()[-1]["status"]), ("apply", "succeeded"))

        result = co.bin_update("check", *manifest(V52))
        self.assertExit(result, 0, "  Decision      up to date, nothing to do\n", f"  · {note}\n", stream="stdout")
        self.assertNotIn("✗", result.stdout)

        result = co.bin_update("rollback", "--yes", timeout=1200)
        self.assertExit(result, 0, f"Rolled back to {V51}")
        self.assertEqual(co.env_bytes(), original)
        stack = self.assertComponents(co, V51, DEFAULT_SERVICES)
        self.assertEqual((stack[dist.RETIRED_SERVICE].id, stack[dist.RETIRED_SERVICE].started_at),
                         (orphan.id, orphan.started_at))

        # Its retirement approved: the removal the note printed, pasted.
        removal = re.search(r"remove them with `([^`\n]+)`", note)
        assert removal is not None
        result = co.paste(removal.group(1), timeout=300)
        self.assertEqual(result.code, 0, result.describe())
        self.assertNotIn(dist.RETIRED_SERVICE, co.stack())
        result = co.bin_update("check", "--format", "json", *manifest(V52))
        self.assertExit(result, 0, stream="stdout")
        plan = json.loads(result.stdout)
        self.assertEqual((plan["decision"], plan["reasons"]), ("update_available", []))
        self.assertEqual([n for n in plan["notes"] if dist.RETIRED_SERVICE in n], [])

    @e2e.slow
    def test_08_a_stopped_container_outside_the_active_profiles_blocks_nothing(self):
        """The workers profile was turned off (COMPOSE_PROFILES taken out of
        .env) and its runner is left over. While it runs v1.51.0, check is
        blocked; once it is stopped, check only notes it, apply goes through
        without touching it, the up-to-date check that follows is not blocked
        either, and the removal the note prints works as pasted."""

        assert H is not None
        co = self.checkout("idle", versions=dist.pinned(V51), profiles="workers")
        co.install()
        runner = co.stack()["worker-simulation-runner"]
        self.assertTrue(runner.running, runner.status)
        with_profile = co.env_bytes()
        co.env_path.write_bytes(re.sub(rb"(?m)^COMPOSE_PROFILES=.*\n", b"", with_profile))
        original = co.env_bytes()
        self.assertNotEqual(original, with_profile)

        with self.subTest("running on the old release, it blocks the update"):
            result = co.bin_update("check", "--format", "json", *manifest(V52))
            self.assertExit(result, 3, stream="stdout")
            plan = json.loads(result.stdout)
            self.assertEqual(plan["decision"], "blocked")
            self.assertEqual(len(plan["reasons"]), 1, plan["reasons"])
            self.assertRegex(plan["reasons"][0], "^" + STRAY_BLOCKS.format(version=re.escape(V51),
                                                                           target=re.escape(V52)) + "$")

        e2e.run(["docker", "stop", "-t", "5", runner.id], timeout=120)
        runner = co.stack()["worker-simulation-runner"]
        self.assertEqual(runner.status, "exited")
        before = co.snapshot()
        hint = r"(?:docker compose|\S*docker-compose)"
        note = re.compile(
            r"bin/update leaves these containers of services outside the active Compose profiles as they are: "
            rf"worker-simulation-runner \(stopped, {re.escape(V51)}\)\. Enabling their profile \(COMPOSE_PROFILES in "
            r"\.env\) lets Compose recreate them on the version \.env selects; to remove them, run "
            rf"`({hint} rm -sf worker-simulation-runner)`$")

        def noted(notes: List[str]) -> str:
            found = [m.group(1) for m in map(note.match, notes) if m]
            self.assertEqual(len(found), 1, notes)
            return found[0]

        result = co.bin_update("check", *manifest(V52))
        self.assertExit(result, 0, f"  Decision      update available: run ./bin/update apply {release_options(V52)}\n",
                        stream="stdout")
        self.assertNotIn("✗", result.stdout)
        result = co.bin_update("check", "--format", "json", *manifest(V52))
        self.assertExit(result, 0, stream="stdout")
        plan = json.loads(result.stdout)
        self.assertEqual((plan["decision"], plan["reasons"]), ("update_available", []))
        remedy = noted(plan["notes"])
        runner_plan = {item["component"]: item for item in plan["components"]}["simulation_runner"]
        self.assertEqual((runner_plan["inactive"], runner_plan["services"], runner_plan["target_tag"]),
                         (True, [], V52))
        self.assertUnchanged(co, before)

        result = co.bin_update("apply", *manifest(V52), "--allow-no-backup", timeout=1200)
        self.assertExit(result, 0, f"Updated to {V52}. Receipt:")
        stack = co.stack()
        with self.subTest("every active component runs v1.52.0"):
            self.assertComponents(co, V52, DEFAULT_SERVICES, stack)
            self.assertServes(co, V52)
        with self.subTest("the stopped runner is the same container, still stopped on v1.51.0"):
            left = stack["worker-simulation-runner"]
            self.assertEqual((left.id, left.image_id, left.status, left.image),
                             (runner.id, runner.image_id, "exited", runner.image))
        with self.subTest("every version key moves, the inactive profile's pin too"):
            self.assertOnlyVersionKeysChanged(original, co.env_bytes(), {key: [V52] for key in VERSION_KEYS})
        self.assertEqual((co.receipts()[-1]["kind"], co.receipts()[-1]["status"]), ("apply", "succeeded"))

        # Up to date now; the stopped runner on v1.51.0 is still there and still only a note.
        result = co.bin_update("check", *manifest(V52))
        self.assertExit(result, 0, "  Decision      up to date, nothing to do\n", stream="stdout")
        self.assertNotIn("✗", result.stdout)
        result = co.bin_update("check", "--format", "json", *manifest(V52))
        self.assertExit(result, 0, stream="stdout")
        plan = json.loads(result.stdout)
        self.assertEqual((plan["decision"], plan["reasons"], plan["env_changes"]), ("up_to_date", [], {}))
        self.assertEqual(noted(plan["notes"]), remedy)

        result = co.paste(remedy, timeout=300)
        self.assertEqual(result.code, 0, result.describe())
        self.assertNotIn("worker-simulation-runner", co.stack())
        result = co.bin_update("check", "--format", "json", *manifest(V52))
        self.assertExit(result, 0, stream="stdout")
        plan = json.loads(result.stdout)
        self.assertEqual((plan["decision"], plan["reasons"]), ("up_to_date", []))
        self.assertEqual([n for n in plan["notes"] if "worker-simulation-runner" in n], [])

    @e2e.slow
    def test_09_a_production_install_with_its_own_env_file_is_told_to_initialize_with_that_file(self):
        """A production install whose settings live in another env file
        (--env-file, with a space in its name) and no deploy/.env.production.
        Every command bin/update prints carries --env-file, and the
        initialization jobs it names run with that env file and both compose
        files; pasted as printed (with the job's arguments), they run the
        staged release, and the printed apply and rollback finish the job."""

        assert H is not None
        env_name = "deploy/prod eu.env"
        co = self.checkout("prodenv", production=True, env_file=env_name,
                           settings={"FUTURE_AGI_UPDATE_MANIFEST_URL": str(H.dist_release(V52).manifest)})
        co.install()
        self.assertFalse(co.production_env_path.exists())
        self.assertFalse(co.dotenv_path.exists())
        self.assertEqual(self.assertBootstrap(co, V51, check_only=True),
                         [bootstrap_line(V51, "migrate", "--noinput"),
                          bootstrap_line(V51, "migrate", "--check", "--noinput")])
        original = co.env_bytes()
        before = co.snapshot()
        prefix = f"./bin/update --production --env-file {shlex.quote(env_name)}"
        flags = ("--production", "--env-file", env_name)
        # docker compose (or the standalone binary), then exactly this install's env file and compose files.
        hint = re.compile(jobs_pattern(env_name))

        def jobs_prefix(text: str) -> str:
            found = sorted(set(hint.findall(text)))
            self.assertEqual(len(found), 1, text)
            return found[0]

        with self.subTest("--production alone reads deploy/.env.production, which this install does not have"):
            result = co.bin_update("--production", "check")
            self.assertExit(result, 2, "deploy/.env.production does not exist")

        result = co.bin_update(*flags, "check")
        self.assertExit(result, 0, f"  Installation  production (Compose project {co.project}, {env_name})\n",
                        f"  Decision      update available: run {prefix} apply --stage (the first of two steps, "
                        "see below)\n", stream="stdout")
        compose = jobs_prefix(result.stdout)
        result = co.bin_update(*flags, "check", "--format", "json")
        self.assertExit(result, 0, stream="stdout")
        plan = json.loads(result.stdout)
        self.assertEqual((plan["decision"], plan["reasons"], plan["next_command"]),
                         ("update_available", [], f"{prefix} apply --stage"))
        self.assertEqual(plan["installation"]["env_file"], env_name)
        self.assertIn(f"production installs update in two steps: {prefix} apply --stage (backs up, then pulls and "
                      f"verifies the release; restarts nothing), run the release's initialization jobs with "
                      f"`{compose} run --rm ...`", "\n".join(plan["notes"]))
        self.assertUnchanged(co, before)

        # ------------------------------------- step 1: the printed stage --
        result = co.paste(f"{plan['next_command']} --allow-no-backup")
        self.assertExit(result, 0, f"Staged {V52}: {env_name} now selects it (",
                        "Next, run the release's initialization jobs, which now use these images, with ",
                        f"Then run `{prefix} apply` to restart the stack on {V52}.")
        staged = co.receipts()[-1]
        self.assertEqual((staged["status"], staged["env_file"]), ("staged", env_name))
        self.assertEqual(jobs_prefix(result.stderr), compose)
        self.assertIn(f"run `{prefix} rollback --receipt {shlex.quote(staged['_path'])} --yes`", result.stderr)
        self.assertOnlyVersionKeysChanged(original, co.env_bytes(), {key: [V52] for key in VERSION_KEYS})
        self.assertEqual(co.snapshot()["containers"], before["containers"])
        self.assertFalse(co.production_env_path.exists())
        finish = re.search(r"Then run `([^`\n]+)` to restart the stack", result.stderr)
        assert finish is not None

        result = co.bin_update(*flags, "check")
        self.assertExit(result, 0, f"  Installed     {V51} running, {V52} staged\n", stream="stdout")
        self.assertEqual(jobs_prefix(result.stdout), compose)
        self.assertIn(f"; then run {prefix} apply (finishes the staged update)\n", result.stdout)
        result = co.bin_update(*flags, "check", "--format", "json")
        self.assertExit(result, 0, stream="stdout")
        plan = json.loads(result.stdout)
        self.assertEqual((plan["decision"], plan["resume"], plan["next_command"]),
                         ("update_available", True, f"{prefix} apply"))
        self.assertEqual(jobs_prefix(plan["before_next_command"]), compose)

        # ----------- the initialization, with the printed compose command --
        containers = co.snapshot()["containers"]
        job = f"{compose} {' '.join(shlex.quote(word) for word in dist.INIT_JOB)}"
        result = co.paste(job, timeout=600)
        self.assertEqual(result.code, 0, result.describe())
        with self.subTest("the job read the custom env file: it ran the staged v1.52.0 in this project"):
            self.assertEqual(co.bootstrap_history()[-1:], [bootstrap_line(V52, "migrate", "--noinput")])
            self.assertEqual(co.snapshot()["containers"], containers)

        # The printed guidance is unchanged; missing automatic backup proof
        # requires a separate explicit host acknowledgment, not stage creation.
        e2e.assert_stage_acknowledgment_required(self, co, *flags, "apply",
                                               invoke=lambda: co.paste(finish.group(1)))
        # ------------------------------------ step 2: explicitly approved apply --
        result = co.paste(finish.group(1) + " --acknowledge-stale-stage")
        self.assertExit(result, 0, f"Updated to {V52}. Receipt:")
        stack = co.stack()
        self.assertComponents(co, V52, DEFAULT_SERVICES, stack)
        self.assertBootstrap(co, V52, check_only=True, stack=stack)
        self.assertServes(co, V52)
        for service in ("backend", "worker"):
            self.assertEqual((stack[service].env.get("PG_PASSWORD"), stack[service].env.get("SECRET_KEY")),
                             (co.pg_password, co.secret), service)
        receipt = co.receipts()[-1]
        self.assertEqual((receipt["status"], receipt["env_file"], receipt["resumes"]),
                         ("succeeded", env_name, staged["_path"]))
        self.assertOnlyVersionKeysChanged(original, co.env_bytes(), {key: [V52] for key in VERSION_KEYS})
        self.assertFalse(co.production_env_path.exists())
        self.assertEqual(co.temp_env_files(), [])
        self.assertNoSecrets(co, co.secret, co.pg_password, co.admin_token)

        result = co.paste(f"{prefix} rollback --yes")
        self.assertExit(result, 0, f"Rolled back to {V51}")
        self.assertEqual(co.env_bytes(), original)
        self.assertComponents(co, V51, DEFAULT_SERVICES)
        self.assertFalse(co.production_env_path.exists())

    @e2e.slow
    def test_10_a_disabled_profile_container_blocks_while_an_orphan_beside_it_is_only_noted(self):
        """The stack was started from an older release's compose file with
        the workers profile on; then this release's file came back and the
        profile was turned off. Two containers now run outside the active
        configuration: the runner, whose service a profile still defines, and
        the retired service's, which nothing defines. The runner blocks the
        update (it would stay on v1.51.0 next to v1.52.0); the orphan is only
        noted. Compose v2.24.x resolves only the enabled profiles' services
        unless every profile is turned on, so bin/update tells the two apart
        there only if it asks for every profile. With the profile on again the
        runner is updated with the rest, and the orphan is left running."""

        assert H is not None
        co = self.checkout("mixed", versions=dist.pinned(V51), profiles="workers")
        co.install_older_release()
        stack = co.stack()
        orphan, runner = stack[dist.RETIRED_SERVICE], stack["worker-simulation-runner"]
        self.assertEqual((orphan.status, runner.status), ("running", "running"))
        with_profile = co.env_bytes()
        without_profile = re.sub(rb"(?m)^COMPOSE_PROFILES=.*\n", b"", with_profile)
        self.assertNotEqual(without_profile, with_profile)
        co.env_path.write_bytes(without_profile)
        before = co.snapshot()
        hook, backups = self.backup_hook("mixed")
        blocks = STRAY_BLOCKS.format(version=re.escape(V51), target=re.escape(V52))
        note = (f"service {dist.RETIRED_SERVICE} (running, {V51}) is not defined by this release's compose files any "
                "more; bin/update leaves its containers as they are. Once its retirement is approved, remove them "
                f"with `docker rm -f {orphan.id[:12]}`")

        result = co.bin_update("check", "--format", "json", *manifest(V52))
        self.assertExit(result, 3, stream="stdout")
        plan = json.loads(result.stdout)
        with self.subTest("the runner is a disabled profile's container, and it blocks"):
            self.assertEqual(plan["decision"], "blocked")
            self.assertEqual(len(plan["reasons"]), 1, plan["reasons"])
            self.assertRegex(plan["reasons"][0], "^" + blocks + "$")
            self.assertEqual([n for n in plan["notes"] if "worker-simulation-runner" in n and "not defined" in n], [])
            component = {item["component"]: item for item in plan["components"]}["simulation_runner"]
            self.assertEqual((component["inactive"], component["services"], component["target_tag"]), (True, [], V52))
        with self.subTest("the retired service's container is an orphan: a note, not a reason"):
            self.assertIn(note, plan["notes"])
            self.assertEqual([r for r in plan["reasons"] if dist.RETIRED_SERVICE in r], [])
        result = co.bin_update("check", *manifest(V52))
        self.assertExit(result, 3, "  Decision      blocked (see below)\n", f"  · {note}\n", stream="stdout")
        self.assertRegex(result.stdout, r"\n  ✗ " + blocks + r"\n")
        result = co.bin_update("apply", *manifest(V52), "--backup-command", str(hook))
        self.assertExit(result, 3, "the update is blocked: ")
        self.assertRegex(result.stderr, blocks)
        self.assertEqual(backups(), [])
        self.assertUnchanged(co, before)

        # The profile on again: the runner is active and moves with the rest; the orphan is still only noted.
        co.env_path.write_bytes(with_profile)
        result = co.bin_update("check", "--format", "json", *manifest(V52))
        self.assertExit(result, 0, stream="stdout")
        plan = json.loads(result.stdout)
        self.assertEqual((plan["decision"], plan["reasons"]), ("update_available", []))
        self.assertIn(note, plan["notes"])
        component = {item["component"]: item for item in plan["components"]}["simulation_runner"]
        self.assertEqual((bool(component.get("inactive")), component["services"]),
                         (False, ["worker-simulation-runner"]))
        result = co.bin_update("apply", *manifest(V52), "--backup-command", str(hook), timeout=1200)
        self.assertExit(result, 0, f"Updated to {V52}. Receipt:")
        self.assertEqual(backups(), [V52])
        stack = self.assertComponents(co, V52, APP_SERVICES)
        with self.subTest("the orphan is the same container, still running v1.51.0"):
            left = stack[dist.RETIRED_SERVICE]
            self.assertEqual((left.id, left.started_at, left.status, left.image),
                             (orphan.id, orphan.started_at, "running", H.dist_image("backend", V51)))
        self.assertOnlyVersionKeysChanged(with_profile, co.env_bytes(), {key: [V52] for key in VERSION_KEYS})
        self.assertEqual((co.receipts()[-1]["kind"], co.receipts()[-1]["status"]), ("apply", "succeeded"))


if __name__ == "__main__":
    unittest.main()
