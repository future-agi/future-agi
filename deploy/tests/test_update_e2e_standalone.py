"""Docker end-to-end tests of bin/update on Standalone installs.

Skipped unless FUTURE_AGI_UPDATE_E2E=1 and Docker with Compose v2 is
available; deploy/tests/update_e2e/harness.py describes the setup (one local
registry, fake images, real signed manifests, fake checkouts). Each test owns
its Compose project and fake checkout and drives that checkout's bin/update
exactly as an operator would, then inspects .env, containers, the data
volume, receipts and the journal.

  FUTURE_AGI_UPDATE_E2E=1 python3 -m unittest discover -s deploy/tests \\
      -p 'test_update_e2e_standalone.py' -v

Tests run FUTURE_AGI_UPDATE_E2E_JOBS at a time (default 4; 1 = one by one).
FUTURE_AGI_UPDATE_E2E_COMPOSE=<path to a docker-compose binary> runs the whole
suite on that Compose version (say v2.24.0, the documented minimum); the
harness logs which version a run used, and test_00 checks bin/update gets it.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import signal
import shutil
import subprocess
import stat
import sys
import threading
import time
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from update_e2e import harness as e2e  # noqa: E402

V51, V52, V53, V54, V55 = "v1.51.0", "v1.52.0", "v1.53.0", "v1.54.0", "v1.55.0"
# Only the tag-moved-after-signing test uses this release: its tag is
# repointed at another image after the manifest was signed.
MOVED = "v1.52.1"
VERSION_KEY = "FUTURE_AGI_VERSION"
INTERRUPTED_NOTE = "an interrupted bin/update run will be recovered by the next apply or rollback"

RELEASES = [
    (V52, "default", "trusted"),
    (V53, "default", "trusted"),
    (V54, "default", "trusted"),
    (V55, "default", "trusted"),
    (MOVED, "default", "trusted"),
    (V52, "default", "attacker"),
    (V52, "newkey", "trusted"),
    (V52, "nopin", "trusted"),
]

H: Optional[e2e.Harness] = None


def setUpModule() -> None:
    global H
    reason = e2e.availability()
    if reason:
        raise unittest.SkipTest(reason)
    harness = e2e.Harness()
    try:
        harness.start(releases=RELEASES, extra_images={MOVED: "ok"})
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


def release(version: str, variant: str = "default", signer: str = "trusted",
            registry: Optional[str] = None) -> e2e.Release:
    assert H is not None
    return H.release(version, variant=variant, signer=signer, registry=registry)


def with_version(text: str, value: str, key: str = VERSION_KEY) -> str:
    """`text` with exactly one `key=` line set to `value`."""

    replaced, count = re.subn(rf"(?m)^{key}=.*$", f"{key}={value}", text)
    if count != 1:
        raise AssertionError(f"expected one {key}= line, found {count}")
    return replaced


def parse_hook_log(path: Path) -> List[Dict[str, str]]:
    runs: List[Dict[str, str]] = []
    if not path.exists():
        return runs
    for line in path.read_text(encoding="utf-8").splitlines():
        if line == "--- run":
            runs.append({})
        elif runs and "=" in line:
            key, _, value = line.partition("=")
            runs[-1][key] = value
    return runs


class StandaloneE2EHelpers:
    """What the test classes below share. A mixin, so it holds no tests of its own."""

    maxDiff = None

    def setUp(self) -> None:
        started = time.monotonic()
        # Registered first, so it runs last: the time includes the project's removal.
        self.addCleanup(lambda: e2e.log(f"{self._testMethodName} took {time.monotonic() - started:.0f}s"))

    # ----------------------------------------------------------- helpers --
    def checkout(self, name: str, **kwargs: Any) -> e2e.Checkout:
        assert H is not None
        checkout = H.new_checkout(name, **kwargs)
        self.addCleanup(checkout.down)
        return checkout

    def own_registry(self, name: str, *versions: str) -> e2e.PrivateRegistry:
        """A registry only this test pulls from, holding `versions` (call it before checkout()).

        bin/update pulls only an image the host lacks, and every test shares
        the host: whether the run's registry's v1.52.0 still needs a pull
        depends on which test ran first. Images named after this registry are
        pulled the first time for certain, a test may remove them, and the
        registry can be stopped (PrivateRegistry).
        """

        assert H is not None
        registry = H.private_registry(name, versions)
        self.addCleanup(registry.remove)
        return registry

    def assertExit(self, result: e2e.Result, code: int, *needles: str, stream: str = "stderr") -> None:
        message = f"\n{result.describe()}"
        self.assertEqual(result.code, code, message)
        text = result.output if stream == "both" else getattr(result, stream)
        for needle in needles:
            self.assertIn(needle, text, message)

    def assertUnchanged(self, checkout: e2e.Checkout, before: Dict[str, Any]) -> None:
        self.assertEqual(checkout.snapshot(), before)
        self.assertEqual(checkout.temp_env_files(), [])

    def assertRuns(self, checkout: e2e.Checkout, version: str, *, image: Optional[str] = None) -> e2e.Container:
        """The app container runs `version` (label), healthy and answering."""

        app = checkout.app()
        self.assertTrue(app.running, f"app container is {app.status}")
        self.assertEqual(checkout.image_info(app.image_id)["version"], version)
        if image is not None:
            self.assertEqual(app.image, image)
        checkout.wait_healthy(timeout=60)
        return app

    def assertPrivate(self, path: Path, mode: int) -> None:
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode, path)

    def recording_hook(self, name: str, *, exit_code: int = 0, message: str = "",
                       wait: bool = False) -> Dict[str, Path]:
        """A backup hook that records its context (and optionally fails, or
        waits until released). Returns its paths."""

        assert H is not None
        directory = H.hook_dir(name)
        paths = {"hook": directory / "backup.sh", "log": directory / "hook.log",
                 "started": directory / "started", "release": directory / "release", "pid": directory / "pid"}
        body = f"""
echo $$ > '{paths['pid']}'
{{
  echo "--- run"
  echo "CWD=$(pwd -P)"
  echo "ROOT=$FUTURE_AGI_UPDATE_ROOT"
  echo "MODE=$FUTURE_AGI_UPDATE_MODE"
  echo "PROJECT=$FUTURE_AGI_UPDATE_PROJECT"
  echo "ENV_FILE=$FUTURE_AGI_UPDATE_ENV_FILE"
  echo "FROM=$FUTURE_AGI_UPDATE_FROM_VERSION"
  echo "TO=$FUTURE_AGI_UPDATE_TO_VERSION"
  echo "SECRET_KEY=${{SECRET_KEY-<unset>}}"
  echo "VERSION_IN_ENVIRONMENT=${{FUTURE_AGI_VERSION-<unset>}}"
  echo "ENV_LINE=$(grep '^FUTURE_AGI_VERSION=' "$FUTURE_AGI_UPDATE_ENV_FILE")"
}} >> '{paths['log']}'
: > '{paths['started']}'
"""
        if wait:
            body += f"while [ ! -e '{paths['release']}' ]; do sleep 0.2; done\n"
        if message:
            body += f"echo '{message}'\n"
        body += f"exit {exit_code}\n"
        H.write_script(paths["hook"], body)
        return paths

    def manifest_args(self, rel: e2e.Release) -> List[str]:
        return ["--manifest", str(rel.manifest)]

    def containers_as_started(self, checkout: e2e.Checkout) -> List[tuple]:
        """Each service container (one-offs left out) with what a restart changes."""

        return sorted((c.service, c.id, c.image_id, c.started_at, c.restart_count)
                      for c in checkout.containers() if not c.oneoff)


class StandaloneUpdateE2E(StandaloneE2EHelpers, unittest.TestCase):
    # ------------------------------------------------------------- tests --
    def test_01_check_reports_a_verified_update_and_changes_nothing(self):
        r52 = release(V52)
        co = self.checkout("check", settings={"FUTURE_AGI_UPDATE_MANIFEST_URL": str(r52.manifest)})
        co.install()
        before = co.snapshot()

        result = co.bin_update("check")
        self.assertExit(result, 0, stream="stdout")
        for line in (
            "Future AGI update check",
            f"  Installation  standalone (Compose project {co.project}, .env)",
            f"  Installed     {V51}",
            f"  Release       {V52} (stable, published {e2e.PUBLISHED_AT})",
            f"  Signature     verified (public key: {H.trusted_pub})",
            "  Decision      update available: run ./bin/update apply",
            "  Components",
        ):
            self.assertIn(line + "\n", result.stdout, result.describe())
        self.assertRegex(result.stdout, rf"\n    standalone\s+{V51}\s+->\s+{V52}\s+{re.escape(r52.digest[:19])}…\n")
        self.assertNotIn("✗", result.stdout)

        result = co.bin_update("check", "--format", "json")
        self.assertExit(result, 0, stream="stdout")
        plan = json.loads(result.stdout)
        self.assertEqual(plan["decision"], "update_available")
        self.assertFalse(plan["resume"])
        self.assertEqual(plan["target_version"], V52)
        self.assertEqual(plan["current_versions"], [V51])
        self.assertEqual(plan["channel"], "stable")
        self.assertEqual(plan["policy"], "minor")
        self.assertEqual(plan["minimum_version"], V51)
        self.assertEqual(plan["manifest_source"], str(r52.manifest))
        self.assertEqual(plan["signature"], {"verified": True, "method": "public key", "signer": str(H.trusted_pub),
                                             "issuer": "", "tool": plan["signature"]["tool"], "error": ""})
        self.assertEqual(plan["installation"], {"mode": "standalone", "project": co.project, "env_file": ".env",
                                                "root": str(co.root)})
        self.assertEqual(plan["env_changes"], {VERSION_KEY: V52})
        self.assertEqual((plan["reasons"], plan["deployment_drift"], plan["missing_env_keys"]), ([], [], []))
        self.assertEqual(len(plan["components"]), 1, plan["components"])
        component = plan["components"][0]
        self.assertEqual({k: component[k] for k in ("component", "variable", "services", "configured",
                                                    "current_version", "current_source", "current_tags",
                                                    "target_tag", "target_image", "target_digest", "action")}, {
            "component": "standalone", "variable": VERSION_KEY, "services": ["app"], "configured": V51,
            "current_version": V51, "current_source": f"{VERSION_KEY} in .env", "current_tags": [V51],
            "target_tag": V52, "target_image": H.image_ref(V52), "target_digest": r52.digest, "action": "update"})

        # check leaves nothing behind but the (empty, private) state directory.
        self.assertTrue(co.state_dir.is_dir())
        self.assertEqual(sorted(p.name for p in co.state_dir.iterdir()), [])
        self.assertPrivate(co.state_dir, 0o700)

        # A dry run needs no backup and changes nothing either.
        result = co.bin_update("apply", "--dry-run")
        self.assertExit(result, 0, "Dry run: nothing was changed.", f"{VERSION_KEY}={V52}",
                        "Decision      update available")
        self.assertUnchanged(co, before)
        self.assertEqual(sorted(p.name for p in co.state_dir.iterdir()), ["update.lock"])
        self.assertEqual(co.history(), [f"start {V51} ok release"])

    @e2e.slow
    def test_02_apply_runs_the_backup_hook_and_changes_only_the_version_key(self):
        # Its own registry: this run pulls v1.52.0 for certain (bin/update pulls only what the host lacks).
        registry = self.own_registry("apply", V51, V52)
        r52 = release(V52, registry=registry.host)
        hook = self.recording_hook("apply-hook")
        co = self.checkout("apply", settings={"FUTURE_AGI_UPDATE_BACKUP_COMMAND": str(hook["hook"])},
                           registry=registry.host)
        co.install()
        co.exec_app("sh", "-c", "echo precious > /data/precious")
        original = co.env_text()
        old_app, old_db = co.app(), co.service("postgres")

        result = co.bin_update("apply", *self.manifest_args(r52))
        self.assertExit(result, 0, f"Running the backup hook: {hook['hook']}",
                        f"Pulling {H.image_ref(V52, registry=registry.host)}",
                        "Restarting the stack on the new images", f"Updated to {V52}. Receipt: ")

        # The hook ran once, from the checkout, before .env changed, with the
        # documented context and none of .env's values.
        runs = parse_hook_log(hook["log"])
        self.assertEqual(len(runs), 1, runs)
        self.assertEqual(runs[0], {
            "CWD": str(co.root), "ROOT": str(co.root), "MODE": "standalone", "PROJECT": co.project,
            "ENV_FILE": str(co.root / ".env"), "FROM": V51, "TO": V52, "SECRET_KEY": "<unset>",
            "VERSION_IN_ENVIRONMENT": "<unset>", "ENV_LINE": f"{VERSION_KEY}={V51}"})

        # .env: byte for byte the same apart from the version, same mode, no temp files.
        self.assertEqual(co.env_text(), with_version(original, V52))
        self.assertPrivate(co.env_path, 0o600)
        self.assertEqual(co.temp_env_files(), [])

        # Only the app was replaced, by the signed image; the data volume kept its data.
        app = self.assertRuns(co, V52, image=H.image_ref(V52, registry=registry.host))
        self.assertNotEqual(app.id, old_app.id)
        self.assertIn(f"{registry.host}/{e2e.REPOSITORY}@{r52.digest}", co.image_info(app.image_id)["repo_digests"])
        db = co.service("postgres")
        self.assertEqual((db.id, db.started_at), (old_db.id, old_db.started_at))
        self.assertEqual(co.history(), [f"start {V51} ok release", f"start {V52} ok release"])
        self.assertEqual(co.volume_text("precious"), "precious\n")

        # One private receipt, secrets scrubbed, no journal left.
        receipts = co.receipts()
        self.assertEqual(len(receipts), 1)
        receipt = receipts[0]
        self.assertIn(f"Receipt: {receipt['_path']}", result.stderr)
        self.assertEqual({k: receipt[k] for k in ("kind", "status", "mode", "production", "env_file", "project",
                                                  "target_version", "from_versions", "env_changes",
                                                  "previous_values", "schema_version")}, {
            "kind": "apply", "status": "succeeded", "mode": "standalone", "production": False, "env_file": ".env",
            "project": co.project, "target_version": V52, "from_versions": [V51], "env_changes": {VERSION_KEY: V52},
            "previous_values": {VERSION_KEY: V51}, "schema_version": 2})
        self.assertEqual(receipt["stage"], "rollout")
        self.assertTrue(receipt["signature"]["verified"])
        self.assertEqual(receipt["manifest_source"], str(r52.manifest))
        self.assertEqual(receipt["backup"]["ran"], True)
        self.assertEqual(receipt["backup"]["command"], str(hook["hook"]))
        self.assertEqual(receipt["previous_images"]["app"]["version"], V51)
        self.assertEqual(receipt["previous_images"]["app"]["image_id"], old_app.image_id)
        self.assertEqual(receipt["target_image_ids"], {"app": app.image_id})
        self.assertEqual(receipt["components"][0]["target_digest"], r52.digest)
        self.assertNotIn(co.secret, Path(receipt["_path"]).read_text())
        self.assertPrivate(Path(receipt["_path"]), 0o600)
        self.assertPrivate(co.state_dir, 0o700)
        self.assertIsNone(co.journal())

        result = co.bin_update("status")
        self.assertExit(result, 0, f"running {V52}", f"({VERSION_KEY}={V52})",
                        f"Last run      apply to {V52}: succeeded", stream="stdout")
        self.assertIn(f"Installation  Standalone (Compose project {co.project}, .env)", result.stdout)
        self.assertNotIn("Interrupted", result.stdout)

    def test_03_apply_again_is_up_to_date_and_restarts_nothing(self):
        r52 = release(V52)
        co = self.checkout("again")
        co.install()

        result = co.bin_update("apply", *self.manifest_args(r52), "--allow-no-backup")
        self.assertExit(result, 0, "Skipping the backup: --allow-no-backup was given.", f"Updated to {V52}")
        self.assertEqual(co.receipts()[0]["backup"], {"ran": False})
        app = self.assertRuns(co, V52)
        env_after, history = co.env_bytes(), co.history()

        # Up to date: no backup needed, no restart, no receipt, nothing written.
        result = co.bin_update("apply", *self.manifest_args(r52))
        self.assertExit(result, 0, "Decision      up to date, nothing to do",
                        "Nothing to do: this install already runs the release.")
        self.assertNotIn("Restarting", result.stderr)
        again = co.app()
        self.assertEqual((again.id, again.started_at, again.restart_count), (app.id, app.started_at, app.restart_count))
        self.assertEqual(co.env_bytes(), env_after)
        self.assertEqual(len(co.receipts()), 1)
        self.assertEqual(co.history(), history)
        self.assertIsNone(co.journal())

    @e2e.slow
    def test_04_unhealthy_release_fails_during_rollout_and_rolls_back(self):
        registry = self.own_registry("unhealthy", V52, V53)
        image52 = H.image_ref(V52, registry=registry.host)
        r53 = release(V53, registry=registry.host)
        co = self.checkout("unhealthy", version=V52, registry=registry.host)
        co.install()
        original = co.env_text()

        result = co.bin_update("apply", *self.manifest_args(r53), "--allow-no-backup")
        self.assertExit(result, 5, f"apply to {V53} stopped while containers were being replaced",
                        "the stack did not become ready in time",
                        f"the API (/health/ on port {co.backend_port})",
                        f"The env file selects {V53}", "./bin/update rollback --receipt ")
        self.assertEqual(co.env_text(), with_version(original, V53))
        failed = co.receipts()[-1]
        self.assertEqual((failed["kind"], failed["status"], failed["stage"]), ("apply", "failed_during_rollout", "rollout"))
        self.assertIn("did not become ready", failed["error"])
        self.assertIn(f"--receipt {failed['_path']} --yes", result.stderr)
        self.assertIsNone(co.journal())
        app = co.app()
        self.assertEqual(co.image_info(app.image_id)["version"], V53)
        self.assertFalse(e2e.http_ok(co.url("/health/")))

        # Without --yes rollback explains itself and changes nothing.
        unhealthy_app = app
        result = co.bin_update("rollback")
        self.assertExit(result, 3, f"Rolling back {V53} to {VERSION_KEY}={V52} (Standalone, project {co.project}).",
                        "Database migrations the update applied are not reverted",
                        "rollback not started: rerun with --yes to confirm")
        self.assertEqual(co.env_text(), with_version(original, V53))
        self.assertEqual(co.app().id, unhealthy_app.id)

        # The previous image is gone from the host (pruned, say) and the registry is unreachable: the rollback's
        # pull fails before anything restarts. It puts .env back as it found it and says the stopped update is
        # still unfinished, not that nothing was changed.
        e2e.run(["docker", "rmi", image52])
        failing = H.failing_pull("unhealthy-rollback")
        result = co.bin_update("rollback", "--yes", env=failing.env())
        finish = f"./bin/update apply --manifest {shlex.quote(str(r53.manifest))}"
        self.assertExit(result, 4, "rollback stopped before restarting anything: ", "pull app` failed with exit code 1",
                        f"The update to {V53} that stopped while containers were being replaced is still unfinished: "
                        f".env selects it ({VERSION_KEY}={V53}), and some containers may still run {V52}. To finish "
                        f"it, run `{finish}`; to go back, run "
                        f"`./bin/update rollback --receipt {failed['_path']} --yes`.")
        self.assertNotIn("Nothing was changed", result.stderr)
        self.assertEqual(failing.pulls(), 1)
        self.assertEqual(co.env_text(), with_version(original, V53))
        self.assertEqual(co.app().id, unhealthy_app.id)
        stopped = co.receipts()[-1]
        self.assertEqual((stopped["kind"], stopped["status"], stopped["rolls_back"]),
                         ("rollback", "failed_before_rollout", failed["_path"]))
        self.assertIn(f"Receipt: {stopped['_path']}", result.stderr)

        result = co.bin_update("rollback", "--yes")
        self.assertExit(result, 0, f"Pulling {image52}", f"Rolled back to {V52}. Receipt: ")
        self.assertEqual(co.env_text(), original)
        back = self.assertRuns(co, V52, image=image52)
        self.assertEqual(co.history(), [f"start {V52} ok release", f"start {V53} unhealthy release",
                                        f"start {V52} ok release"])
        rolled = co.receipts()[-1]
        self.assertEqual({k: rolled[k] for k in ("kind", "status", "rolls_back", "from_version", "target_version",
                                                 "env_changes", "previous_values")}, {
            "kind": "rollback", "status": "rolled_back", "rolls_back": failed["_path"], "from_version": V53,
            "target_version": V52, "env_changes": {VERSION_KEY: V52}, "previous_values": {VERSION_KEY: V53}})

        # A second rollback is refused and touches nothing.
        result = co.bin_update("rollback", "--yes")
        self.assertExit(result, 3, f"that update was already rolled back ({rolled['_path']})")
        self.assertEqual(co.env_text(), original)
        self.assertEqual(co.app().id, back.id)

        result = co.bin_update("status")
        self.assertExit(result, 0, f"Last run      rollback to {V52}: rolled_back", f"running {V52}", stream="stdout")

    @e2e.slow
    def test_05_crash_looping_release_is_detected_and_rolled_back(self):
        r54 = release(V54)
        co = self.checkout("crash")
        co.install()
        original = co.env_text()

        result = co.bin_update("apply", *self.manifest_args(r54), "--allow-no-backup")
        self.assertExit(result, 5, f"apply to {V54} stopped while containers were being replaced",
                        "the app container keeps restarting", f"The env file selects {V54}")
        self.assertEqual(co.env_text(), with_version(original, V54))
        failed = co.receipts()[-1]
        self.assertEqual(failed["status"], "failed_during_rollout")
        self.assertIn("the app container keeps restarting", failed["error"])
        # A loop, not one failed start: Docker restarted it at least once. Readiness may stop at the
        # second restart while Docker still waits out its back-off (RestartCount 2, two starts so far),
        # so a third start is not guaranteed yet. Read the history once: the container keeps crashing.
        history = co.history()
        crashes = [line for line in history if line.startswith(f"start {V54} crash")]
        self.assertGreaterEqual(len(crashes), 2, history)

        result = co.bin_update("rollback", "--yes")
        self.assertExit(result, 0, f"Rolled back to {V51}.")
        self.assertEqual(co.env_text(), original)
        self.assertRuns(co, V51, image=H.image_ref(V51))
        self.assertEqual(co.history()[-1], f"start {V51} ok release")

    def test_06_failing_or_hanging_backup_hook_changes_nothing(self):
        r52 = release(V52)
        failing = self.recording_hook("failing-hook", exit_code=7, message="e2e backup failed: disk full")
        hanging = self.recording_hook("hanging-hook", wait=True)
        self.addCleanup(hanging["release"].touch)
        co = self.checkout("backup-fails", settings={"FUTURE_AGI_UPDATE_BACKUP_COMMAND": str(failing["hook"])})
        co.install()
        before = co.snapshot()

        result = co.bin_update("apply", *self.manifest_args(r52))
        self.assertExit(result, 4, "the backup hook exited with code 7 (output above); nothing was changed")
        self.assertIn("e2e backup failed: disk full", result.output)
        self.assertEqual(len(parse_hook_log(failing["log"])), 1)
        self.assertUnchanged(co, before)

        # --backup-command beats update.conf; the timeout comes from the environment.
        result = co.bin_update("apply", *self.manifest_args(r52), "--backup-command", str(hanging["hook"]),
                               env={"FUTURE_AGI_UPDATE_BACKUP_TIMEOUT_SECONDS": "2"})
        self.assertExit(result, 4, "the backup hook did not finish within 2s; nothing was changed")
        self.assertEqual(len(parse_hook_log(failing["log"])), 1)
        self.assertEqual(len(parse_hook_log(hanging["log"])), 1)
        self.assertUnchanged(co, before)

        missing = H.hooks_dir / "no-such-hook.sh"
        result = co.bin_update("apply", *self.manifest_args(r52), "--backup-command", str(missing))
        self.assertExit(result, 4, "the backup hook could not run", "nothing was changed")
        self.assertUnchanged(co, before)
        self.assertEqual(co.receipts(), [])
        self.assertIsNone(co.journal())

    def test_07_apply_without_a_backup_is_blocked(self):
        co = self.checkout("no-backup")
        co.install()
        before = co.snapshot()
        result = co.bin_update("apply", *self.manifest_args(release(V52)))
        self.assertExit(result, 3, "no backup is configured. Set FUTURE_AGI_UPDATE_BACKUP_COMMAND in update.conf",
                        "--backup-command", "--allow-no-backup")
        self.assertUnchanged(co, before)
        self.assertEqual(co.receipts(), [])

    def test_08_manifest_signed_by_another_key_is_refused(self):
        forged = release(V52, signer="attacker")
        hook = self.recording_hook("attacker-hook")
        co = self.checkout("attacker", settings={"FUTURE_AGI_UPDATE_BACKUP_COMMAND": str(hook["hook"])})
        co.install()
        before = co.snapshot()

        result = co.bin_update("check", *self.manifest_args(forged))
        self.assertExit(result, 3, "  Signature     NOT verified: signature verification failed",
                        "  Decision      blocked (see below)", "✗ the release signature is not verified",
                        stream="stdout")
        result = co.bin_update("apply", *self.manifest_args(forged))
        self.assertExit(result, 3, "bin/update: the update is blocked: the release signature is not verified")
        self.assertEqual(parse_hook_log(hook["log"]), [])
        self.assertUnchanged(co, before)

    def test_09_tampered_manifest_or_bundle_is_refused(self):
        r52, r53 = release(V52), release(V53)
        co = self.checkout("tampered")
        co.install()
        before = co.snapshot()
        work = H.hook_dir("tampered")

        # A bundle whose signature was altered.
        bad_signature = r52.copy_to(work / "bad-signature")
        bundle = json.loads(bad_signature.bundle.read_text())
        signature = bundle["messageSignature"]["signature"]
        bundle["messageSignature"]["signature"] = ("A" if signature[0] != "A" else "B") + signature[1:]
        bad_signature.bundle.write_text(json.dumps(bundle))
        # A manifest changed after it was signed (still a valid manifest).
        edited = r52.copy_to(work / "edited")
        data = edited.data()
        data["published_at"] = "2026-10-20T00:00:00Z"
        edited.manifest.write_text(json.dumps(data, indent=2))
        # No bundle at all.
        unsigned = r52.copy_to(work / "unsigned", bundle=False)

        for name, args, needle in (
            ("altered signature", self.manifest_args(bad_signature), "NOT verified: signature verification failed"),
            ("edited manifest", self.manifest_args(edited), "NOT verified: signature verification failed"),
            ("another release's bundle", [*self.manifest_args(r52), "--bundle", str(r53.bundle)],
             "NOT verified: signature verification failed"),
            ("no bundle", self.manifest_args(unsigned), "NOT verified: signature bundle not available"),
        ):
            with self.subTest(name):
                result = co.bin_update("check", *args)
                self.assertExit(result, 3, needle, "  Decision      blocked (see below)", stream="stdout")

        result = co.bin_update("apply", *self.manifest_args(bad_signature), "--allow-no-backup")
        self.assertExit(result, 3, "the update is blocked: the release signature is not verified")
        self.assertUnchanged(co, before)

    def test_10_release_tag_moved_after_signing_is_refused_before_restart(self):
        signed = release(MOVED)
        moved_to = H.push_image(MOVED, "ok", flavor="tampered")
        self.assertNotEqual(moved_to, signed.digest)
        hook = self.recording_hook("moved-hook")
        co = self.checkout("moved-tag", settings={"FUTURE_AGI_UPDATE_BACKUP_COMMAND": str(hook["hook"])})
        co.install()
        original, old_app = co.env_text(), co.app()

        result = co.bin_update("apply", *self.manifest_args(signed))
        self.assertExit(result, 4, "apply stopped before restarting anything",
                        f"{H.image_ref(MOVED)} resolves to {moved_to} on this host, not the signed {signed.digest}. "
                        "Refusing to start it",
                        "Nothing was changed; the previous versions are configured and running")
        self.assertEqual(len(parse_hook_log(hook["log"])), 1)  # the backup ran first
        self.assertEqual(co.env_text(), original)
        app = self.assertRuns(co, V51, image=H.image_ref(V51))
        self.assertEqual((app.id, app.started_at), (old_app.id, old_app.started_at))
        self.assertEqual(co.history(), [f"start {V51} ok release"])
        receipt = co.receipts()[-1]
        self.assertEqual((receipt["status"], receipt["stage"]), ("failed_before_rollout", "pulled"))
        self.assertIsNone(co.journal())
        # The moved tag really was pulled: the host now holds the other image under that tag.
        self.assertEqual(co.image_info(H.image_ref(MOVED))["flavor"], "tampered")

    def test_11_deployment_drift_requires_checking_out_the_release(self):
        hook = self.recording_hook("drift-hook")
        co = self.checkout("drift", settings={"FUTURE_AGI_UPDATE_BACKUP_COMMAND": str(hook["hook"])})
        co.install()
        with (co.root / "docker-compose.yml").open("a", encoding="utf-8") as handle:
            handle.write("# a local edit made after the release\n")
        before = co.snapshot()

        result = co.bin_update("check", *self.manifest_args(release(V52)))
        # The fixture checkout is no git work tree (a tarball install): no git command is printed, as none would work.
        self.assertExit(result, 3, f"✗ this checkout's deployment files differ from {V52}'s (docker-compose.yml)",
                        f"git cannot be used in this checkout ({co.root}) to put it back: replace it with release "
                        f"{V52}'s version (your env file stays as it is), then check again", stream="stdout")
        self.assertNotIn("`git ", result.stdout)
        self.assertNotIn(".env lacks", result.stdout)
        result = co.bin_update("apply", *self.manifest_args(release(V52)))
        self.assertExit(result, 3, "the update is blocked: this checkout's deployment files differ")
        self.assertEqual(parse_hook_log(hook["log"]), [])
        self.assertUnchanged(co, before)

    def test_12_missing_env_setting_requires_bin_install(self):
        rel = release(V52, variant="newkey")
        co = self.checkout("new-setting", variant="newkey")
        co.install()
        before = co.snapshot()

        result = co.bin_update("check", *self.manifest_args(rel))
        self.assertExit(result, 3, "✗ .env lacks settings this release's .env.example adds (NEW_RELEASE_SETTING). "
                                   "Run `./bin/install --no-up -y` to add them", stream="stdout")
        plan_text = result.stdout
        self.assertNotIn("deployment files differ", plan_text)
        result = co.bin_update("apply", *self.manifest_args(rel), "--allow-no-backup")
        self.assertExit(result, 3, "the update is blocked: .env lacks settings")
        self.assertUnchanged(co, before)

        # What `./bin/install --no-up -y` does: add the key, keep everything else.
        with co.env_path.open("a", encoding="utf-8") as handle:
            handle.write("NEW_RELEASE_SETTING=enabled\n")
        result = co.bin_update("check", *self.manifest_args(rel))
        self.assertExit(result, 0, "  Decision      update available", stream="stdout")

    def test_13_latest_install_with_empty_version_key_is_pinned_on_apply(self):
        r52 = release(V52)
        co = self.checkout("latest-empty", version="")
        co.install()
        self.assertEqual(co.app().image, H.image_ref("latest"))
        original = co.env_text()
        self.assertIn(f"\n{VERSION_KEY}=\n", original)

        result = co.bin_update("check", "--format", "json", *self.manifest_args(r52))
        self.assertExit(result, 0, stream="stdout")
        plan = json.loads(result.stdout)
        self.assertEqual(plan["decision"], "update_available")
        self.assertEqual(plan["current_versions"], [V51])
        component = plan["components"][0]
        self.assertEqual((component["current_version"], component["current_source"], component["current_tags"],
                          component["configured"], component["action"]),
                         (V51, "running image (follows :latest)", ["latest"], "", "update"))
        self.assertEqual(plan["env_changes"], {VERSION_KEY: V52})

        result = co.bin_update("apply", *self.manifest_args(r52), "--allow-no-backup")
        self.assertExit(result, 0, f"Updated to {V52}", f"  Installed     {V51}")
        self.assertEqual(co.env_text(), with_version(original, V52))
        self.assertRuns(co, V52, image=H.image_ref(V52))
        receipt = co.receipts()[-1]
        self.assertEqual(receipt["previous_values"], {VERSION_KEY: ""})
        self.assertEqual(receipt["previous_images"]["app"]["image"], H.image_ref("latest"))
        self.assertEqual(receipt["previous_images"]["app"]["version"], V51)

    @e2e.slow
    def test_14_latest_install_without_version_key_pins_apply_and_rollback(self):
        rel = release(V52, variant="nopin")
        co = self.checkout("latest-unset", version=None, variant="nopin")
        co.install()
        original = co.env_text()
        self.assertNotIn(VERSION_KEY, original)
        self.assertEqual(co.app().image, H.image_ref("latest"))

        result = co.bin_update("check", *self.manifest_args(rel))
        self.assertExit(result, 0, f"  Installed     {V51}", "  Decision      update available",
                        "  Signature     verified", stream="stdout")
        self.assertRegex(result.stdout, rf"\n    standalone\s+{V51}\s+->\s+{V52}\s")

        result = co.bin_update("apply", *self.manifest_args(rel), "--allow-no-backup")
        self.assertExit(result, 0, f"Updated to {V52}")
        self.assertEqual(co.env_text(), original + f"{VERSION_KEY}={V52}\n")
        self.assertRuns(co, V52, image=H.image_ref(V52))
        self.assertEqual(co.receipts()[-1]["previous_values"], {VERSION_KEY: None})

        # Rollback pins the version that ran, not :latest (which may have moved since).
        result = co.bin_update("rollback", "--yes")
        self.assertExit(result, 0, f"Rolling back {V52} to {VERSION_KEY}={V51}", f"Rolled back to {V51}.")
        self.assertEqual(co.env_text(), original + f"{VERSION_KEY}={V51}\n")
        self.assertRuns(co, V51, image=H.image_ref(V51))
        self.assertEqual(co.receipts()[-1]["env_changes"], {VERSION_KEY: V51})

    def test_15_compose_override_still_applies_after_the_update(self):
        co = self.checkout("override")
        extra_port = e2e.free_port(H.ports)
        (co.root / "override-data").mkdir()
        (co.root / "override-data" / "note.txt").write_text("from the override\n")
        (co.root / "docker-compose.override.yml").write_text(
            "# an operator's local override (not a deployment file)\n"
            "services:\n"
            "  app:\n"
            "    environment:\n"
            "      FAGI_E2E_OVERRIDE: kept-after-update\n"
            "    ports:\n"
            f'      - "{extra_port}:8000"\n'
            "    volumes:\n"
            "      - ./override-data:/override:ro\n")
        co.install()

        def assert_override(app: e2e.Container) -> None:
            self.assertEqual(app.env.get("FAGI_E2E_OVERRIDE"), "kept-after-update")
            self.assertIn(str(extra_port), app.host_ports("8000/tcp"))
            self.assertIn("/override", [m.get("Destination") for m in app.mounts])
            e2e.wait_for(lambda: e2e.http_ok(co.url("/health/", port=extra_port)), timeout=60,
                         what=f"the override's extra port {extra_port}")
            self.assertEqual(co.exec_app("cat", "/override/note.txt").stdout, "from the override\n")

        old = co.app()
        assert_override(old)
        result = co.bin_update("apply", *self.manifest_args(release(V52)), "--allow-no-backup")
        self.assertExit(result, 0, f"Updated to {V52}")
        app = self.assertRuns(co, V52)
        self.assertNotEqual(app.id, old.id)
        assert_override(app)

    def test_16_sigterm_while_pulling_restores_the_previous_version(self):
        registry = self.own_registry("sigterm-pull", V51, V52)  # its pull of v1.52.0 is certain
        co = self.checkout("sigterm-pull", registry=registry.host)
        co.install()
        original, old_app = co.env_text(), co.app()
        slow = H.slow_pull("sigterm-pull")
        run = co.spawn("apply", *self.manifest_args(release(V52, registry=registry.host)), "--allow-no-backup",
                       env=slow.env())
        self.addCleanup(slow.release)
        self.addCleanup(run.kill_group)

        e2e.wait_for(lambda: slow.started.exists() or not run.alive(), timeout=300, what="bin/update to pull")
        self.assertTrue(run.alive(), run.stderr_so_far())
        self.assertEqual((co.journal() or {}).get("stage"), "env_written")
        self.assertEqual(co.env_value(VERSION_KEY), V52)  # .env really was changed before the pull
        run.send(signal.SIGTERM)
        result = run.wait(timeout=300)

        self.assertExit(result, 130, "apply stopped before restarting anything: SIGTERM",
                        "Nothing was changed; the previous versions are configured and running")
        self.assertEqual(co.env_text(), original)
        self.assertEqual(co.temp_env_files(), [])
        app = self.assertRuns(co, V51)
        self.assertEqual((app.id, app.started_at), (old_app.id, old_app.started_at))
        receipt = co.receipts()[-1]
        self.assertEqual((receipt["status"], receipt["stage"], receipt["error"]),
                         ("interrupted_before_rollout", "env_written", "SIGTERM"))
        self.assertIsNone(co.journal())

        result = co.bin_update("status")
        self.assertExit(result, 0, f"Last run      apply to {V52}: interrupted_before_rollout", stream="stdout")
        self.assertNotIn("Interrupted   ", result.stdout)

    @e2e.slow
    def test_17_sigterm_during_rollout_keeps_the_new_version_and_apply_resumes(self):
        r55 = release(V55)
        co = self.checkout("sigterm-rollout")
        co.install()
        original = co.env_text()
        run = co.spawn("apply", *self.manifest_args(r55), "--allow-no-backup")
        self.addCleanup(run.kill_group)

        e2e.wait_for(lambda: (co.journal() or {}).get("stage") == "rollout" or not run.alive(), timeout=600,
                     what="bin/update to start the rollout")

        def slow_app_running() -> bool:
            if not run.alive():
                return True
            apps = [c for c in co.containers() if c.service == "app" and c.running]
            return any(co.image_info(c.image_id)["version"] == V55 for c in apps)

        e2e.wait_for(slow_app_running, timeout=300, interval=0.5, what=f"the {V55} container to start")
        self.assertTrue(run.alive(), f"bin/update finished before it could be interrupted:\n{run.stderr_so_far()}")
        run.send(signal.SIGTERM)
        result = run.wait(timeout=300)
        self.assertExit(result, 130, f"apply to {V55} stopped while containers were being replaced: SIGTERM",
                        f"The env file selects {V55}",
                        f"To finish, run `./bin/update apply --manifest {shlex.quote(str(r55.manifest))}` again for "
                        f"{V55}")
        self.assertEqual(co.env_text(), with_version(original, V55))
        interrupted = co.receipts()[-1]
        self.assertEqual((interrupted["status"], interrupted["stage"]), ("interrupted_during_rollout", "rollout"))
        self.assertIsNone(co.journal())

        result = co.bin_update("check", *self.manifest_args(r55))
        self.assertExit(result, 0, "  Decision      update available: run ./bin/update apply --manifest "
                                   f"{shlex.quote(str(r55.manifest))} (finishes the stopped update)",
                        f"the previous update to {V55} stopped while containers were starting "
                        "(interrupted_during_rollout); apply finishes it", stream="stdout")

        result = co.bin_update("apply", *self.manifest_args(r55), "--allow-no-backup")
        self.assertExit(result, 0, f"Updated to {V55}")
        self.assertEqual(co.env_text(), with_version(original, V55))
        self.assertRuns(co, V55, image=H.image_ref(V55))
        finished = co.receipts()[-1]
        self.assertEqual(finished["status"], "succeeded")
        self.assertEqual(finished["resumes"], interrupted["_path"])
        self.assertEqual(finished["env_changes"], {})
        # What ran before the stopped update stays the rollback target.
        self.assertEqual(finished["previous_values"], {VERSION_KEY: V51})
        self.assertEqual(finished["previous_images"]["app"]["version"], V51)

        # Rollback of the resumed apply goes back to what ran before the stopped update.
        result = co.bin_update("rollback", "--yes")
        self.assertExit(result, 0, f"Rolling back {V55} to {VERSION_KEY}={V51}", f"Rolled back to {V51}. Receipt: ")
        self.assertEqual(co.env_text(), original)
        self.assertRuns(co, V51, image=H.image_ref(V51))
        rolled = co.receipts()[-1]
        self.assertEqual({k: rolled[k] for k in ("kind", "status", "rolls_back", "from_version", "target_version",
                                                 "env_changes", "previous_values")}, {
            "kind": "rollback", "status": "rolled_back", "rolls_back": finished["_path"], "from_version": V55,
            "target_version": V51, "env_changes": {VERSION_KEY: V51}, "previous_values": {VERSION_KEY: V55}})
        before = co.snapshot()
        result = co.bin_update("rollback", "--receipt", interrupted["_path"], "--yes")
        self.assertExit(result, 3, f"that update was already rolled back ({rolled['_path']})")
        self.assertUnchanged(co, before)

    @e2e.slow
    def test_18_sigkill_leaves_a_journal_status_reports_it_and_apply_recovers(self):
        registry = self.own_registry("sigkill", V51, V52)  # its pull of v1.52.0 is certain
        r52 = release(V52, registry=registry.host)
        co = self.checkout("sigkill", registry=registry.host)
        co.install()
        original, old_app = co.env_text(), co.app()
        slow = H.slow_pull("sigkill")
        run = co.spawn("apply", *self.manifest_args(r52), "--allow-no-backup", env=slow.env())
        self.addCleanup(slow.release)
        self.addCleanup(run.kill_group)

        e2e.wait_for(lambda: slow.started.exists() or not run.alive(), timeout=300, what="bin/update to pull")
        self.assertTrue(run.alive(), run.stderr_so_far())
        run.kill_group(signal.SIGKILL)
        self.assertEqual(run.wait(timeout=60).code, -signal.SIGKILL)

        journal = co.journal()
        self.assertIsNotNone(journal)
        self.assertEqual({k: journal[k] for k in ("kind", "stage", "status", "target_version", "env_changes",
                                                  "found_values", "previous_values")}, {
            "kind": "apply", "stage": "env_written", "status": "running", "target_version": V52,
            "env_changes": {VERSION_KEY: V52}, "found_values": {VERSION_KEY: V51},
            "previous_values": {VERSION_KEY: V51}})
        self.assertPrivate(co.state_dir / "journal.json", 0o600)
        self.assertEqual(co.env_text(), with_version(original, V52))  # the killed run had changed .env
        self.assertEqual(co.app().id, old_app.id)

        result = co.bin_update("status")
        self.assertExit(result, 0, f"Interrupted   an apply to {V52} stopped at stage env_written; the next apply or "
                                   "rollback recovers it", stream="stdout")
        self.assertIsNotNone(co.journal())  # status only reports

        with self.subTest("check describes what runs, not the killed run's half-written .env"):
            result = co.bin_update("check", *self.manifest_args(r52))
            self.assertIn(INTERRUPTED_NOTE, result.stdout, result.describe())
            self.assertIn(f"  Installed     {V51}", result.stdout, result.describe())
            self.assertNotIn("up to date", result.stdout, result.describe())
        self.assertEqual(co.env_text(), with_version(original, V52))  # check changes nothing either

        result = co.bin_update("apply", *self.manifest_args(r52), "--allow-no-backup")
        self.assertEqual(co.env_value(VERSION_KEY), V52, result.describe())
        self.assertExit(result, 0, f"Recovered an interrupted apply to {V52}: it stopped before restarting anything; "
                                   "its version settings were put back exactly (receipt ", f"Updated to {V52}")
        self.assertIn(f"  Installed     {V51}\n", result.stderr, result.describe())
        self.assertEqual(co.env_text(), with_version(original, V52))
        self.assertRuns(co, V52)
        receipts = co.receipts()
        self.assertEqual([(r["status"], r["target_version"]) for r in receipts],
                         [("interrupted_before_rollout", V52), ("succeeded", V52)])
        self.assertEqual(receipts[0]["error"], "the bin/update process ended before finishing")
        self.assertEqual(receipts[1]["previous_values"], {VERSION_KEY: V51})
        self.assertIsNone(co.journal())

    @e2e.slow
    def test_19_concurrent_runs_are_refused_while_the_lock_is_held(self):
        r52 = release(V52)
        hook = self.recording_hook("lock-hook", wait=True)
        self.addCleanup(hook["release"].touch)
        co = self.checkout("lock")
        co.install()
        first = co.spawn("apply", *self.manifest_args(r52), "--backup-command", str(hook["hook"]))
        self.addCleanup(first.kill_group)
        e2e.wait_for(lambda: hook["started"].exists() or not first.alive(), timeout=300,
                     what="the first apply to reach its backup hook")
        self.assertTrue(first.alive(), first.stderr_so_far())

        holder = f"another bin/update is running in this checkout (pid {first.pid})"
        self.assertExit(co.bin_update("apply", *self.manifest_args(r52), "--allow-no-backup"), 2, holder)
        self.assertExit(co.bin_update("rollback", "--yes"), 2, holder)
        # Read-only commands report the live lock holder rather than planning a
        # state that may change before the first apply finishes.
        self.assertExit(co.bin_update("check", *self.manifest_args(r52)), 3,
                        "  Decision      blocked (see below)", "a bin/update run is in progress",
                        stream="stdout")
        self.assertExit(co.bin_update("status"), 0, "In progress   a bin/update run is in progress",
                        "Last run      none", stream="stdout")

        hook["release"].touch()
        result = first.wait(timeout=600)
        self.assertExit(result, 0, f"Updated to {V52}")
        self.assertEqual(len(co.receipts()), 1)
        self.assertRuns(co, V52)

    @e2e.slow
    def test_20_runs_from_another_directory_with_relative_paths(self):
        r52 = release(V52)
        co = self.checkout("elsewhere")
        r52.copy_to(co.root / "release-cache" / V52)
        co.write_update_conf({"FUTURE_AGI_UPDATE_MANIFEST_URL": f"release-cache/{V52}/{e2e.MANIFEST_NAME}"})
        co.install()
        elsewhere = H.run_dir / "elsewhere" / "deeper"
        elsewhere.mkdir(parents=True)
        manifest = os.path.relpath(r52.manifest, elsewhere)
        bundle = os.path.relpath(r52.bundle, elsewhere)
        self.assertTrue(manifest.startswith(".."), manifest)

        result = co.bin_update("check", "--format", "json", "--manifest", manifest, cwd=elsewhere)
        self.assertExit(result, 0, stream="stdout")
        plan = json.loads(result.stdout)
        self.assertEqual((plan["decision"], plan["signature"]["verified"]), ("update_available", True))
        self.assertEqual(plan["manifest_source"], str(elsewhere / manifest))

        # FUTURE_AGI_UPDATE_MANIFEST_URL relative to the checkout, not to the caller.
        result = co.bin_update("check", "--format", "json", cwd=elsewhere)
        self.assertExit(result, 0, stream="stdout")
        plan = json.loads(result.stdout)
        self.assertEqual(plan["manifest_source"], str(co.root / "release-cache" / V52 / e2e.MANIFEST_NAME))
        self.assertTrue(plan["signature"]["verified"])

        result = co.bin_update("apply", "--manifest", manifest, "--bundle", bundle, "--allow-no-backup", cwd=elsewhere)
        self.assertExit(result, 0, f"Updated to {V52}")
        self.assertEqual(co.receipts()[-1]["manifest_source"], str(elsewhere / manifest))
        self.assertRuns(co, V52)
        self.assertEqual(sorted(p.name for p in elsewhere.iterdir()), [])

        result = co.bin_update("status", cwd=elsewhere)
        self.assertExit(result, 0, f"running {V52}", f"Last run      apply to {V52}: succeeded", stream="stdout")

    def test_21_sigterm_during_the_backup_hook_exits_130_and_changes_nothing(self):
        hook = self.recording_hook("sigterm-hook", wait=True)
        self.addCleanup(hook["release"].touch)
        co = self.checkout("sigterm-backup")
        co.install()
        before = co.snapshot()
        run = co.spawn("apply", *self.manifest_args(release(V52)), "--backup-command", str(hook["hook"]))
        self.addCleanup(run.kill_group)
        e2e.wait_for(lambda: hook["started"].exists() or not run.alive(), timeout=300,
                     what="apply to reach its backup hook")
        self.assertTrue(run.alive(), run.stderr_so_far())
        run.send(signal.SIGTERM)
        result = run.wait(timeout=120)
        pid = int(hook["pid"].read_text().strip())

        def hook_gone() -> bool:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
            return False

        with self.subTest("SIGTERM is handled like Ctrl-C: exit 130 with a message"):
            self.assertExit(result, 130, "bin/update: ", "interrupted")
        with self.subTest("the backup hook does not outlive bin/update"):
            deadline = time.monotonic() + 10
            while not hook_gone() and time.monotonic() < deadline:
                time.sleep(0.2)
            self.assertTrue(hook_gone(), f"backup hook pid {pid} still runs after bin/update exited "
                                         f"(exit {result.code})")
        hook["release"].touch()
        after = co.snapshot()
        self.assertEqual((after["env"], after["containers"], after["journal"]),
                         (before["env"], before["containers"], before["journal"]))

    @e2e.slow
    def test_22_rollback_after_a_resumed_apply_returns_to_what_ran_before_the_original_update(self):
        # A run killed during the rollout; the next apply recovers its journal and
        # finishes it in the same run; rollback then goes back to the version
        # from before the original update, not to the one the resume found.
        r55 = release(V55)
        co = self.checkout("resume-rollback")
        co.install()
        original, old_app = co.env_text(), co.app()
        run = co.spawn("apply", *self.manifest_args(r55), "--allow-no-backup")
        self.addCleanup(run.kill_group)

        e2e.wait_for(lambda: (co.journal() or {}).get("stage") == "rollout" or not run.alive(), timeout=600,
                     what="bin/update to start the rollout")

        def slow_app_running() -> bool:
            if not run.alive():
                return True
            apps = [c for c in co.containers() if c.service == "app" and c.running and c.id != old_app.id]
            return any(co.image_info(c.image_id)["version"] == V55 for c in apps)

        e2e.wait_for(slow_app_running, timeout=300, interval=0.5, what=f"the {V55} container to start")
        self.assertTrue(run.alive(), f"bin/update finished before it could be killed:\n{run.stderr_so_far()}")
        run.kill_group(signal.SIGKILL)
        self.assertEqual(run.wait(timeout=60).code, -signal.SIGKILL)
        journal = co.journal()
        self.assertEqual({k: (journal or {}).get(k) for k in ("stage", "status", "env_changes", "found_values",
                                                              "previous_values")}, {
            "stage": "rollout", "status": "running", "env_changes": {VERSION_KEY: V55},
            "found_values": {VERSION_KEY: V51}, "previous_values": {VERSION_KEY: V51}})
        self.assertEqual(co.env_text(), with_version(original, V55))  # the new version stays: it is what runs

        result = co.bin_update("check", *self.manifest_args(r55))
        self.assertExit(result, 0, INTERRUPTED_NOTE,
                        "  Decision      update available: run ./bin/update apply --manifest "
                        f"{shlex.quote(str(r55.manifest))} (finishes the stopped update)",
                        stream="stdout")
        self.assertEqual(co.env_text(), with_version(original, V55))

        result = co.bin_update("apply", *self.manifest_args(r55), "--allow-no-backup")
        self.assertExit(result, 0, f"Recovered an interrupted apply to {V55}: it stopped while containers were being "
                                   "replaced (receipt ", f"Updated to {V55}")
        self.assertEqual(co.env_text(), with_version(original, V55))
        self.assertRuns(co, V55, image=H.image_ref(V55))
        stopped, finished = co.receipts()
        self.assertEqual((stopped["status"], stopped["error"], stopped["previous_values"]),
                         ("interrupted_during_rollout", "the bin/update process ended before finishing",
                          {VERSION_KEY: V51}))
        self.assertEqual({k: finished[k] for k in ("status", "resumes", "env_changes", "found_values",
                                                   "previous_values")}, {
            "status": "succeeded", "resumes": stopped["_path"], "env_changes": {}, "found_values": {},
            "previous_values": {VERSION_KEY: V51}})
        self.assertEqual(finished["previous_images"]["app"]["version"], V51)
        self.assertEqual(finished["components"][0]["current_tags"], [V51])
        # The resume restarted nothing: the container the killed run started finished its migrations.
        self.assertEqual(co.history(), [f"start {V51} ok release", f"start {V55} slow release"])

        # The killed run's receipt names the same update: rollback points at the run that finished it.
        before = co.snapshot()
        result = co.bin_update("rollback", "--receipt", stopped["_path"], "--yes")
        self.assertExit(result, 3, f"that update was finished by a later run ({finished['_path']}); to undo it, run "
                                   f"`./bin/update rollback --receipt {finished['_path']} --yes`")
        self.assertUnchanged(co, before)

        result = co.bin_update("rollback", "--yes")
        self.assertExit(result, 0, f"Rolling back {V55} to {VERSION_KEY}={V51}", f"Rolled back to {V51}. Receipt: ")
        self.assertEqual(co.env_text(), original)
        back = self.assertRuns(co, V51, image=H.image_ref(V51))
        self.assertEqual(co.history()[-1], f"start {V51} ok release")
        rolled = co.receipts()[-1]
        self.assertEqual({k: rolled[k] for k in ("kind", "status", "rolls_back", "from_version", "target_version",
                                                 "env_changes", "previous_values")}, {
            "kind": "rollback", "status": "rolled_back", "rolls_back": finished["_path"], "from_version": V55,
            "target_version": V51, "env_changes": {VERSION_KEY: V51}, "previous_values": {VERSION_KEY: V55}})

        # Neither the run that finished the update nor the killed run it resumed can be rolled back again.
        before = co.snapshot()
        for args in ((), ("--receipt", stopped["_path"])):
            with self.subTest(args=args):
                result = co.bin_update("rollback", *args, "--yes")
                self.assertExit(result, 3, f"that update was already rolled back ({rolled['_path']})")
        self.assertUnchanged(co, before)
        self.assertEqual(co.app().id, back.id)

    @e2e.slow
    def test_23_two_updates_roll_back_one_after_the_other(self):
        r52, r55 = release(V52), release(V55)
        co = self.checkout("two-steps")
        co.install()
        original = co.env_text()
        self.assertExit(co.bin_update("apply", *self.manifest_args(r52), "--allow-no-backup"), 0, f"Updated to {V52}")
        first = co.receipts()[-1]
        self.assertExit(co.bin_update("apply", *self.manifest_args(r55), "--allow-no-backup"), 0, f"Updated to {V55}")
        second = co.receipts()[-1]
        self.assertEqual(second["previous_values"], {VERSION_KEY: V52})

        # The older update cannot be rolled back while the newer one stands.
        before = co.snapshot()
        result = co.bin_update("rollback", "--receipt", first["_path"], "--yes")
        self.assertExit(result, 3, f"a newer update ({second['_path']}) ran after that one; roll that back first")
        self.assertUnchanged(co, before)

        result = co.bin_update("rollback", "--yes")
        self.assertExit(result, 0, f"Rolling back {V55} to {VERSION_KEY}={V52}", f"Rolled back to {V52}.")
        self.assertEqual(co.env_text(), with_version(original, V52))
        self.assertRuns(co, V52, image=H.image_ref(V52))
        undone_second = co.receipts()[-1]
        self.assertEqual(undone_second["rolls_back"], second["_path"])

        # Once it was rolled back, the older one can be: back to what ran before both.
        result = co.bin_update("rollback", "--receipt", first["_path"], "--yes")
        self.assertExit(result, 0, f"Rolling back {V52} to {VERSION_KEY}={V51}", f"Rolled back to {V51}.")
        self.assertEqual(co.env_text(), original)
        back = self.assertRuns(co, V51, image=H.image_ref(V51))
        undone_first = co.receipts()[-1]
        self.assertEqual({k: undone_first[k] for k in ("rolls_back", "from_version", "target_version", "env_changes",
                                                       "previous_values")}, {
            "rolls_back": first["_path"], "from_version": V52, "target_version": V51,
            "env_changes": {VERSION_KEY: V51}, "previous_values": {VERSION_KEY: V52}})
        self.assertEqual(co.history(), [f"start {V51} ok release", f"start {V52} ok release",
                                        f"start {V55} slow release", f"start {V52} ok release",
                                        f"start {V51} ok release"])

        # Both are undone now: rollback refuses either and changes nothing.
        before = co.snapshot()
        for receipt, rolled in ((None, undone_second), (first, undone_first)):
            with self.subTest(receipt=receipt and receipt["_path"]):
                args = ("--receipt", receipt["_path"]) if receipt else ()
                result = co.bin_update("rollback", *args, "--yes")
                self.assertExit(result, 3, f"that update was already rolled back ({rolled['_path']})")
        self.assertUnchanged(co, before)
        self.assertEqual(co.app().id, back.id)

    def test_24_cleanup_leaves_another_suites_resources_alone(self):
        # The Standalone and Distributed suites share the Docker host and may run at once.
        dist = e2e.suite_prefixes()[1]
        self.assertTrue(H.owns(f"{H.prefix}app"))
        self.assertFalse(H.owns(f"{dist}app"))
        # Even a Standalone prefix the Distributed one extends does not claim its resources.
        broad = e2e.Harness(base=H.run_dir / "broad", prefix="fagi-upd-e2e-")
        self.assertTrue(broad.owns("fagi-upd-e2e-app"))
        self.assertFalse(broad.owns(f"{dist}app"))

        # A cleanup really removes its own volumes and keeps the other suite's.
        tag = os.urandom(4).hex()
        probe = e2e.Harness(base=H.run_dir / f"probe-{tag}", prefix=f"{H.prefix}probe{tag}-")
        probe.foreign_prefixes = (f"{probe.prefix}dist-",)
        mine, theirs = f"{probe.prefix}mine", f"{probe.prefix}dist-theirs"
        for volume in (mine, theirs):
            e2e.run(["docker", "volume", "create", volume])
            self.addCleanup(e2e.run, ["docker", "volume", "rm", "-f", volume], check=False)
        probe.cleanup_leftovers()
        left = set(e2e.run(["docker", "volume", "ls", "-q", "--filter", f"name={probe.prefix}"]).stdout.split())
        self.assertNotIn(mine, left)
        self.assertIn(theirs, left)

    def test_24b_cleanup_keeps_images_of_a_reused_registry_port_and_of_other_prefixes(self):
        # Image names carry no prefix (localhost:<port>/futureagi/...). An earlier run's state file names its
        # registry port; once that port is free, another suite's registry may get it. Its images are not ours.
        tag = os.urandom(4).hex()
        probe = e2e.Harness(base=H.run_dir / f"probe-{tag}", prefix=f"{H.prefix}probe{tag}-")
        probe.foreign_prefixes = (f"{probe.prefix}dist-",)
        port = e2e.free_port(H.ports)
        registry = f"{probe.prefix}dist-registry"
        unlabelled = f"localhost:{port}/futureagi/standalone:probe-{tag}"
        labelled = f"localhost:{port}/futureagi/standalone:probe-labelled-{tag}"
        e2e.run(["docker", "pull", "-q", H.image_ref(V51)])
        self.assertIn(H.image_ref(V51), H._labelled_images(H.prefix), "the fake images carry their prefix")
        e2e.run(["docker", "tag", e2e.BASE_IMAGE, unlabelled])
        e2e.run(["docker", "tag", H.image_ref(V51), labelled])  # labelled with H's prefix: another run's to the probe
        for image in (unlabelled, labelled):
            self.addCleanup(e2e.run, ["docker", "rmi", image], check=False)
        self.addCleanup(e2e.run, ["docker", "rm", "-f", registry], check=False)
        e2e.run(["docker", "run", "-d", "--name", registry, "-p", f"{port}:5000", e2e.BASE_IMAGE, "sleep", "600"])

        def leftover_state() -> None:
            probe.base.mkdir(parents=True, exist_ok=True)
            probe.state_file.write_text(json.dumps({"registry_port": port, "local_tags": []}), encoding="utf-8")

        def present() -> set:
            return set(e2e.run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"]).stdout.split())

        leftover_state()
        self.assertIn(port, probe.foreign_host_ports() or set())
        probe.cleanup_leftovers()
        self.assertTrue({unlabelled, labelled} <= present(), "another suite's registry publishes that port now")
        self.assertEqual(e2e.run(["docker", "inspect", registry], check=False).code, 0)  # not its container

        e2e.run(["docker", "rm", "-f", registry])
        leftover_state()
        probe.cleanup_leftovers()
        images = present()
        self.assertNotIn(unlabelled, images)  # the earlier run's image from its own (now free) port
        self.assertIn(labelled, images)  # labelled with another prefix: never this run's
        self.assertIn(e2e.BASE_IMAGE, images)  # removing a tag never removes the image it names


class Hangups:
    """Sends `sig` to a spawned run's whole process group every `interval` seconds, the way a
    closing terminal hangs up a background job (bin/update, its backup hook, docker compose),
    and records the journal stage each signal arrived in."""

    def __init__(self, run: e2e.Spawned, checkout: e2e.Checkout, sig: int = signal.SIGHUP,
                 interval: float = 0.2) -> None:
        self.run, self.checkout, self.sig, self.interval = run, checkout, sig, interval
        self.stages: List[str] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="update-e2e-hangups", daemon=True)

    def _loop(self) -> None:
        while not self._stop.is_set() and self.run.alive():
            self.run.kill_group(self.sig)
            self.stages.append((self.checkout.journal() or {}).get("stage") or "-")
            self._stop.wait(self.interval)

    def start(self) -> "Hangups":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=10)


class StandaloneRoundTwoE2E(StandaloneE2EHelpers, unittest.TestCase):
    """Staged updates and live-host edge cases that only Docker can show."""

    @e2e.slow
    def test_25_stage_restarts_nothing_and_apply_finishes_it_without_a_second_backup(self):
        registry = self.own_registry("stage-finish", V51, V52)  # the stage's pull of v1.52.0 is certain
        image52 = H.image_ref(V52, registry=registry.host)
        r52 = release(V52, registry=registry.host)
        finish = f"./bin/update apply --manifest {shlex.quote(str(r52.manifest))}"
        hook = self.recording_hook("stage-hook")
        co = self.checkout("stage-finish", settings={"FUTURE_AGI_UPDATE_BACKUP_COMMAND": str(hook["hook"])},
                           registry=registry.host)
        co.install()
        original = co.env_text()
        started = self.containers_as_started(co)
        old_app, old_db = co.app(), co.service("postgres")

        # A dry run of the stage changes nothing and runs no backup.
        before = co.snapshot()
        result = co.bin_update("apply", "--stage", "--dry-run", *self.manifest_args(r52))
        self.assertExit(result, 0, "Dry run: nothing was changed.", "stop before restarting anything (staged)")
        self.assertUnchanged(co, before)
        self.assertEqual(parse_hook_log(hook["log"]), [])

        result = co.bin_update("apply", "--stage", *self.manifest_args(r52))
        self.assertExit(result, 0, f"Running the backup hook: {hook['hook']}", f"Pulling {image52}",
                        f"Staged {V52}: .env now selects it ({VERSION_KEY}={V52}) and its images are pulled and "
                        "verified. Nothing was restarted: the stack still runs the previous versions.",
                        f"Then run `{finish}` to restart the stack on {V52}.")
        self.assertNotIn("Restarting the stack", result.stderr)
        # Nothing restarted: the very same containers, started at the same moment, never restarted.
        self.assertEqual(self.containers_as_started(co), started)
        self.assertEqual(co.history(), [f"start {V51} ok release"])
        e2e.wait_for(lambda: e2e.http_ok(co.url("/health/")), timeout=30, what="the old app to keep serving")
        # The env file selects the release, byte for byte otherwise; the signed image is on the host.
        self.assertEqual(co.env_text(), with_version(original, V52))
        self.assertPrivate(co.env_path, 0o600)
        self.assertEqual(co.temp_env_files(), [])
        pulled = co.image_info(image52)
        self.assertIn(f"{registry.host}/{e2e.REPOSITORY}@{r52.digest}", pulled["repo_digests"])
        # The backup ran once, before the env file changed.
        runs = parse_hook_log(hook["log"])
        self.assertEqual([(r["FROM"], r["TO"], r["ENV_LINE"]) for r in runs], [(V51, V52, f"{VERSION_KEY}={V51}")])
        staged = co.receipts()[-1]
        self.assertIn(f"Receipt: {staged['_path']}", result.stderr)
        self.assertIn(f"`./bin/update rollback --receipt {staged['_path']} --yes`", result.stderr)
        self.assertEqual({k: staged[k] for k in ("kind", "status", "stage", "target_version", "env_changes",
                                                 "previous_values", "target_image_ids")}, {
            "kind": "apply", "status": "staged", "stage": "pulled", "target_version": V52,
            "env_changes": {VERSION_KEY: V52}, "previous_values": {VERSION_KEY: V51},
            "target_image_ids": {"app": pulled["id"]}})
        self.assertEqual((staged["backup"]["ran"], staged["backup"]["command"]), (True, str(hook["hook"])))
        self.assertEqual(staged["previous_images"]["app"]["image_id"], old_app.image_id)
        self.assertIsNone(co.journal())

        # status and check name the staged update; neither changes anything.
        before = co.snapshot()
        result = co.bin_update("status")
        self.assertExit(result, 0, f"running {V51}  ({VERSION_KEY}={V52})", f"Last run      apply to {V52}: staged",
                        stream="stdout")
        result = co.bin_update("check", *self.manifest_args(r52))
        self.assertExit(result, 0, f"  Decision      update available: run {finish} (finishes the staged update)\n",
                        f"the update to {V52} is staged (its images are pulled and verified, nothing was restarted); "
                        f"{finish} finishes it", stream="stdout")
        plan = json.loads(co.bin_update("check", "--format", "json", *self.manifest_args(r52)).stdout)
        self.assertEqual((plan["decision"], plan["resume"], plan["next_command"]),
                         ("update_available", True, finish))
        self.assertUnchanged(co, before)

        # An ordinary backup hook has no automatic proof: explicit host approval
        # is required, and refusing it must not run a backup or rewrite the stage.
        e2e.assert_stage_acknowledgment_required(self, co, "apply", *self.manifest_args(r52),
                                               backup_paths=[hook["log"]])
        # Approval cannot excuse changed operator config or env bytes. Restore
        # each deliberate edit exactly before testing the valid finish.
        for path, extra in ((co.root / "update.conf", b"\n# changed operator configuration\n"),
                            (co.env_path, b"\nE2E_OPERATOR_EDIT=must-not-be-overwritten\n")):
            with self.subTest(changed=path.name):
                saved = path.read_bytes()
                try:
                    path.write_bytes(saved + extra)
                    refused_before = co.snapshot()
                    result = co.bin_update("apply", *self.manifest_args(r52), "--acknowledge-stale-stage")
                    self.assertExit(result, 3, "bin/update: immutable_release_changed")
                    self.assertUnchanged(co, refused_before)
                    self.assertEqual(path.read_bytes(), saved + extra)
                    self.assertEqual(len(parse_hook_log(hook["log"])), 1)
                finally:
                    path.write_bytes(saved)
        # The intentionally approved finish: no second backup or env change.
        result = co.bin_update("apply", *self.manifest_args(r52), "--acknowledge-stale-stage")
        self.assertExit(result, 0, f"Skipping the backup: it ran when the update to {V52} was staged "
                                   f"({staged['_path']}).", "Restarting the stack on the new images",
                        f"Updated to {V52}. Receipt: ")
        self.assertNotIn("Running the backup hook", result.stderr)
        self.assertEqual(len(parse_hook_log(hook["log"])), 1)
        self.assertEqual(co.env_text(), with_version(original, V52))
        app = self.assertRuns(co, V52, image=image52)
        self.assertNotEqual(app.id, old_app.id)
        db = co.service("postgres")
        self.assertEqual((db.id, db.started_at), (old_db.id, old_db.started_at))
        self.assertEqual(co.history(), [f"start {V51} ok release", f"start {V52} ok release"])
        finished = co.receipts()[-1]
        self.assertEqual({k: finished[k] for k in ("status", "resumes", "backup", "env_changes", "previous_values",
                                                   "target_image_ids")}, {
            "status": "succeeded", "resumes": staged["_path"], "backup": {"ran": False, "staged": staged["_path"]},
            "env_changes": {}, "previous_values": {VERSION_KEY: V51}, "target_image_ids": {"app": app.image_id}})
        self.assertEqual(finished["previous_images"]["app"]["image_id"], old_app.image_id)
        self.assertIsNone(co.journal())

        # Rolling the finished update back returns to what ran before the stage.
        result = co.bin_update("rollback", "--yes")
        self.assertExit(result, 0, f"Rolling back {V52} to {VERSION_KEY}={V51}", f"Rolled back to {V51}. Receipt: ")
        self.assertEqual(co.env_text(), original)
        self.assertRuns(co, V51, image=H.image_ref(V51, registry=registry.host))
        self.assertEqual(co.receipts()[-1]["rolls_back"], finished["_path"])

    @e2e.slow
    def test_26_a_staged_update_blocks_a_newer_release_and_rolls_back_without_a_restart(self):
        r52, r55 = release(V52), release(V55)
        co = self.checkout("stage-rollback")
        co.install()
        original = co.env_bytes()
        started = self.containers_as_started(co)

        result = co.bin_update("apply", "--stage", *self.manifest_args(r52), "--allow-no-backup")
        self.assertExit(result, 0, "Skipping the backup: --allow-no-backup was given.", f"Staged {V52}")
        staged = co.receipts()[-1]
        self.assertEqual((staged["status"], staged["backup"]), ("staged", {"ran": False}))
        self.assertEqual(self.containers_as_started(co), started)

        # A newer release cannot go over the staged one: blocked, naming the staged version and both ways out.
        before = co.snapshot()
        result = co.bin_update("check", *self.manifest_args(r55))
        self.assertExit(result, 3, "  Decision      blocked (see below)",
                        f"✗ the update to {V52} is staged (its images are pulled and verified, .env selects them, and "
                        f"nothing was restarted). Finish it first (check out {V52} with `git checkout {V52}`, or at "
                        f"least its deployment files (your env file stays as it is), then ./bin/update apply --manifest "
                        f"{r52.manifest}) or undo it (./bin/update rollback --receipt {staged['_path']} --yes), then "
                        f"check again",
                        stream="stdout")
        result = co.bin_update("apply", *self.manifest_args(r55), "--allow-no-backup")
        self.assertExit(result, 3, f"the update is blocked: the update to {V52} is staged")
        self.assertUnchanged(co, before)

        result = co.bin_update("rollback")
        self.assertExit(result, 3, f"Rolling back {V52} to {VERSION_KEY}={V51} (Standalone, project {co.project}).",
                        "rollback not started: rerun with --yes to confirm")
        self.assertUnchanged(co, before)

        result = co.bin_update("rollback", "--yes")
        self.assertExit(result, 0, f"Rolled back to {V51} without restarting anything: the update to {V52} was staged")
        # The old pins are back byte for byte and the containers never changed.
        self.assertEqual(co.env_bytes(), original)
        self.assertEqual(self.containers_as_started(co), started)
        self.assertRuns(co, V51, image=H.image_ref(V51))
        self.assertEqual(co.history(), [f"start {V51} ok release"])
        rolled = co.receipts()[-1]
        self.assertEqual({k: rolled[k] for k in ("kind", "status", "rolls_back", "from_version", "target_version",
                                                 "env_changes", "previous_values")}, {
            "kind": "rollback", "status": "rolled_back", "rolls_back": staged["_path"], "from_version": V52,
            "target_version": V51, "env_changes": {VERSION_KEY: V51}, "previous_values": {VERSION_KEY: V52}})
        self.assertIsNone(co.journal())

        # Undone once: refused again; and the newer release is no longer blocked.
        before = co.snapshot()
        result = co.bin_update("rollback", "--receipt", staged["_path"], "--yes")
        self.assertExit(result, 3, f"that update was already rolled back ({rolled['_path']})")
        result = co.bin_update("check", *self.manifest_args(r55))
        self.assertExit(result, 0, "  Decision      update available: run ./bin/update apply", f"  Installed     {V51}",
                        stream="stdout")
        self.assertUnchanged(co, before)

    @e2e.slow
    def test_27_a_oneoff_compose_run_container_does_not_fail_the_rollout(self):
        r52 = release(V52)
        co = self.checkout("oneoff")
        co.install()
        old_app = co.app()
        # What `docker compose run -d app ...` leaves: a container of service app that `up` never replaces.
        result = co.compose("run", "-d", "--no-deps", "app")
        self.assertEqual(result.code, 0, result.describe())
        e2e.wait_for(lambda: any(c.oneoff and c.running for c in co.containers()), timeout=60,
                     what="the one-off app container to run")
        oneoff = next(c for c in co.containers() if c.oneoff)
        self.assertEqual((oneoff.service, co.image_info(oneoff.image_id)["version"]), ("app", V51))

        result = co.bin_update("check", *self.manifest_args(r52))
        self.assertExit(result, 0, f"  Installed     {V51}", "  Decision      update available", stream="stdout")
        result = co.bin_update("apply", *self.manifest_args(r52), "--allow-no-backup")
        self.assertExit(result, 0, f"Updated to {V52}")
        self.assertNotIn("still run the previous images", result.output)
        app = self.assertRuns(co, V52, image=H.image_ref(V52))
        self.assertNotEqual(app.id, old_app.id)
        self.assertEqual(co.receipts()[-1]["target_image_ids"], {"app": app.image_id})

        # The one-off is left alone, still on the old image, and does not count as part of the stack.
        still = next(c for c in co.containers() if c.id == oneoff.id)
        self.assertEqual((still.running, still.image_id, still.started_at),
                         (True, oneoff.image_id, oneoff.started_at))
        result = co.bin_update("status")
        self.assertExit(result, 0, stream="stdout")
        self.assertRegex(result.stdout, rf"\n  app\s+\S+\s+running {re.escape(V52)}  \({VERSION_KEY}={re.escape(V52)}\)\n")
        result = co.bin_update("check", *self.manifest_args(r52))
        self.assertExit(result, 0, f"  Installed     {V52}", "  Decision      up to date", stream="stdout")

        # Rollback does not trip over it either.
        result = co.bin_update("rollback", "--yes")
        self.assertExit(result, 0, f"Rolled back to {V51}.")
        self.assertRuns(co, V51, image=H.image_ref(V51))
        self.assertTrue(any(c.id == oneoff.id and c.running for c in co.containers()))

    @e2e.slow
    def test_28_nohup_apply_survives_hangups_of_its_whole_process_group(self):
        r52 = release(V52)
        hook = self.recording_hook("nohup-hook", wait=True)
        self.addCleanup(hook["release"].touch)
        co = self.checkout("nohup")
        co.install()
        original = co.env_text()

        with self.subTest("without nohup a hangup interrupts apply like Ctrl-C and changes nothing"):
            run = co.spawn("apply", *self.manifest_args(r52), "--backup-command", str(hook["hook"]))
            self.addCleanup(run.kill_group)
            e2e.wait_for(lambda: hook["started"].exists() or not run.alive(), timeout=300,
                         what="apply to reach its backup hook")
            self.assertTrue(run.alive(), run.stderr_so_far())
            run.send(signal.SIGHUP)
            self.assertExit(run.wait(timeout=120), 130, "bin/update: ", "interrupted")
            self.assertEqual(co.env_text(), original)
            self.assertEqual(co.receipts(), [])

        hook["started"].unlink()
        old_app = co.app()
        run = co.spawn("apply", *self.manifest_args(r52), "--backup-command", str(hook["hook"]), nohup=True)
        self.addCleanup(run.kill_group)
        e2e.wait_for(lambda: hook["started"].exists() or not run.alive(), timeout=300,
                     what="nohup apply to reach its backup hook")
        self.assertTrue(run.alive(), run.stderr_so_far())
        # The terminal goes away: SIGHUP for the whole job (bin/update, the hook, and later
        # docker compose pull/up and the readiness wait), again and again until it ends.
        hangups = Hangups(run, co).start()
        self.addCleanup(hangups.stop)
        e2e.wait_for(lambda: len(hangups.stages) >= 10 or not run.alive(), timeout=60, what="ten hangups")
        self.assertTrue(run.alive(), f"a hangup stopped the nohup'd apply:\n{run.stderr_so_far()}")
        hook["release"].touch()
        result = run.wait(timeout=600)
        hangups.stop()
        self.assertExit(result, 0, f"Updated to {V52}. Receipt: ")
        self.assertNotIn("interrupted", result.output)
        self.assertIn("rollout", hangups.stages, f"no hangup arrived during the restart: {hangups.stages}")
        app = self.assertRuns(co, V52, image=H.image_ref(V52))
        self.assertNotEqual(app.id, old_app.id)
        self.assertEqual(co.receipts()[-1]["status"], "succeeded")
        self.assertEqual(len(parse_hook_log(hook["log"])), 2)
        self.assertIsNone(co.journal())

    def test_29_status_and_check_report_a_run_in_progress_and_leave_its_journal_alone(self):
        registry = self.own_registry("status-lock", V51, V52)  # its pull of v1.52.0 is certain
        r52 = release(V52, registry=registry.host)
        co = self.checkout("status-lock", registry=registry.host)
        co.install()
        slow = H.slow_pull("status-lock")
        first = co.spawn("apply", *self.manifest_args(r52), "--allow-no-backup", env=slow.env())
        self.addCleanup(slow.release)
        self.addCleanup(first.kill_group)
        e2e.wait_for(lambda: slow.started.exists() or not first.alive(), timeout=300,
                     what="the first apply to pull while it holds the update lock")
        self.assertTrue(first.alive(), first.stderr_so_far())
        # The live run already wrote its journal and changed .env: a reader must not take that for a killed run.
        journal = co.journal()
        self.assertEqual((journal or {}).get("stage"), "env_written")
        self.assertEqual(co.env_value(VERSION_KEY), V52)
        env = co.env_bytes()
        holder = f"a bin/update run is in progress (pid {first.pid})"

        result = co.bin_update("status")
        self.assertExit(result, 0, f"In progress   {holder}; wait for it to finish (now: apply to {V52}, stage "
                                   "env_written)", "Last run      none", stream="stdout")
        self.assertNotIn("Interrupted", result.stdout)

        result = co.bin_update("check", *self.manifest_args(r52))
        self.assertExit(result, 3, "  Decision      blocked (see below)",
                        f"✗ {holder}; wait for it to finish, then run "
                        f"`./bin/update check --manifest {shlex.quote(str(r52.manifest))}` again", stream="stdout")
        self.assertNotIn(INTERRUPTED_NOTE, result.stdout)
        result = co.bin_update("check", "--format", "json", *self.manifest_args(r52))
        self.assertEqual(result.code, 3, result.describe())
        plan = json.loads(result.stdout)
        self.assertEqual((plan["decision"], plan["next_command"]), ("blocked", None))
        self.assertTrue(any(reason.startswith(holder) for reason in plan["reasons"]), plan["reasons"])

        # Commands that would change something are refused outright.
        busy = f"another bin/update is running in this checkout (pid {first.pid})"
        self.assertExit(co.bin_update("apply", "--dry-run", *self.manifest_args(r52)), 2, busy)
        self.assertExit(co.bin_update("rollback", "--yes"), 2, busy)
        self.assertEqual((co.journal(), co.env_bytes()), (journal, env))
        self.assertTrue(first.alive(), first.stderr_so_far())

        slow.release()
        self.assertExit(first.wait(timeout=600), 0, f"Updated to {V52}")
        result = co.bin_update("status")
        self.assertExit(result, 0, f"Last run      apply to {V52}: succeeded", stream="stdout")
        self.assertNotIn("In progress", result.stdout)
        self.assertExit(co.bin_update("check", *self.manifest_args(r52)), 0, "  Decision      up to date",
                        stream="stdout")


class StandaloneRoundThreeE2E(StandaloneE2EHelpers, unittest.TestCase):
    """What a staged update looks like to check, and to a rollback that stops before restarting anything."""

    @e2e.slow
    def test_30_check_after_a_stage_reports_what_runs_and_what_is_staged(self):
        r52 = release(V52)
        finish = f"./bin/update apply --manifest {shlex.quote(str(r52.manifest))}"
        co = self.checkout("stage-check")
        co.install()
        original = co.env_text()
        self.assertExit(co.bin_update("apply", "--stage", *self.manifest_args(r52), "--allow-no-backup"), 0,
                        f"Staged {V52}: .env now selects it")
        staged = co.receipts()[-1]
        self.assertEqual(staged["status"], "staged")
        self.assertEqual(co.env_value(VERSION_KEY), V52)
        self.assertEqual(co.running_version(), V51)
        before = co.snapshot()

        # The stack still runs v1.51.0 although .env selects v1.52.0: check names both, never "Installed v1.52.0"
        # with "keep", which would read as if the update were live.
        result = co.bin_update("check", *self.manifest_args(r52))
        self.assertExit(result, 0, stream="stdout")
        for line in (f"  Installed     {V51} running, {V52} staged",
                     f"  Decision      update available: run {finish} (finishes the staged update)"):
            self.assertIn(line + "\n", result.stdout, result.describe())
        self.assertRegex(result.stdout, rf"\n    standalone\s+{V51}\s+->\s+{V52}\s+{re.escape(r52.digest[:19])}…\n")
        self.assertNotRegex(result.stdout, r"\n    standalone\s+\S+\s+keep\s")
        self.assertNotIn("stopped update", result.stdout)

        result = co.bin_update("check", "--format", "json", *self.manifest_args(r52))
        self.assertExit(result, 0, stream="stdout")
        plan = json.loads(result.stdout)
        self.assertEqual({k: plan[k] for k in ("decision", "resume", "next_command", "current_versions",
                                               "staged_version", "target_version", "env_changes", "reasons")}, {
            "decision": "update_available", "resume": True, "next_command": finish,
            "current_versions": [V51], "staged_version": V52, "target_version": V52, "env_changes": {},
            "reasons": []})
        component = plan["components"][0]
        self.assertEqual({k: component[k] for k in ("configured", "current_version", "current_source", "current_tags",
                                                    "target_tag", "action")}, {
            "configured": V52, "current_version": V51,
            "current_source": f"running image ({VERSION_KEY} in .env selects the staged {V52})",
            "current_tags": [V52], "target_tag": V52, "action": "update"})
        result = co.bin_update("status")
        self.assertExit(result, 0, f"running {V51}  ({VERSION_KEY}={V52})", f"Last run      apply to {V52}: staged",
                        stream="stdout")
        self.assertUnchanged(co, before)

        # The run that finishes it shows the same plan and records what ran before it, not the staged tag.
        e2e.assert_stage_acknowledgment_required(self, co, "apply", *self.manifest_args(r52), "--allow-no-backup")
        result = co.bin_update("apply", *self.manifest_args(r52), "--allow-no-backup", "--acknowledge-stale-stage")
        self.assertExit(result, 0, f"  Installed     {V51} running, {V52} staged\n", f"Updated to {V52}. Receipt: ")
        self.assertRuns(co, V52, image=H.image_ref(V52))
        self.assertEqual(co.env_text(), with_version(original, V52))
        finished = co.receipts()[-1]
        self.assertEqual({k: finished[k] for k in ("status", "resumes", "from_versions", "env_changes",
                                                   "previous_values")}, {
            "status": "succeeded", "resumes": staged["_path"], "from_versions": [V51], "env_changes": {},
            "previous_values": {VERSION_KEY: V51}})

        # Finished: the release alone is installed, and nothing is staged any more.
        result = co.bin_update("check", *self.manifest_args(r52))
        self.assertExit(result, 0, f"  Installed     {V52}\n", "  Decision      up to date, nothing to do",
                        stream="stdout")
        self.assertNotIn("staged", result.stdout)
        plan = json.loads(co.bin_update("check", "--format", "json", *self.manifest_args(r52)).stdout)
        self.assertEqual((plan["current_versions"], plan["staged_version"], plan["resume"]), ([V52], None, False))

    @e2e.slow
    def test_31_a_staged_update_stays_pending_when_its_rollback_cannot_pull_or_is_interrupted(self):
        registry = self.own_registry("stage-rollback-pull", V51, V52)
        image51 = H.image_ref(V51, registry=registry.host)
        r52 = release(V52, registry=registry.host)
        finish = f"./bin/update apply --manifest {shlex.quote(str(r52.manifest))}"
        co = self.checkout("stage-rollback-pull", registry=registry.host)
        co.install()
        original = co.env_text()
        started = self.containers_as_started(co)
        self.assertExit(co.bin_update("apply", "--stage", *self.manifest_args(r52), "--allow-no-backup"), 0,
                        f"Staged {V52}")
        staged = co.receipts()[-1]
        self.assertEqual(staged["status"], "staged")
        staged_env = co.env_bytes()
        retry = f"./bin/update rollback --receipt {staged['_path']} --yes"
        pending = (f"The update to {V52} staged earlier is still pending: .env selects it ({VERSION_KEY}={V52}), and "
                   f"the stack still runs {V51}. To finish it, run `{finish}`; to drop it, run `{retry}`.")

        def assert_still_staged(result: e2e.Result, status: str, stage: str) -> None:
            # Not "Nothing was changed; the previous versions are configured and running": .env selects v1.52.0.
            self.assertNotIn("Nothing was changed", result.stderr)
            self.assertNotIn("previous versions are configured", result.stderr)
            stopped = co.receipts()[-1]
            self.assertIn(f"Receipt: {stopped['_path']}", result.stderr)
            self.assertEqual({k: stopped[k] for k in ("kind", "status", "stage", "rolls_back", "from_version",
                                                      "target_version", "found_values", "env_changes")}, {
                "kind": "rollback", "status": status, "stage": stage, "rolls_back": staged["_path"],
                "from_version": V52, "target_version": V51, "found_values": {VERSION_KEY: V52},
                "env_changes": {VERSION_KEY: V51}})
            # .env selects the staged release again, byte for byte, and no container was touched.
            self.assertEqual(co.env_bytes(), staged_env)
            self.assertPrivate(co.env_path, 0o600)
            self.assertEqual(co.temp_env_files(), [])
            self.assertEqual(self.containers_as_started(co), started)
            self.assertIsNone(co.journal())
            # check agrees: the update is still staged, and apply finishes it.
            check = co.bin_update("check", *self.manifest_args(r52))
            self.assertExit(check, 0, f"  Installed     {V51} running, {V52} staged\n",
                            f"  Decision      update available: run {finish} (finishes the staged update)",
                            stream="stdout")

        # The rollback needs no pull while the image that ran before is on the host (see test_33). Here its tag is
        # gone (removed by hand; the container keeps running), so the rollback must pull it.
        e2e.run(["docker", "rmi", "-f", image51])
        with self.subTest("the registry cannot be reached: the rollback's pull fails"):
            failing = H.failing_pull("stage-rollback-pull")
            result = co.bin_update("rollback", "--yes", env=failing.env())
            self.assertExit(result, 4, f"Rolling back {V52} to {VERSION_KEY}={V51}",
                            "rollback stopped before restarting anything: ", "pull app` failed with exit code 1",
                            pending, failing.message)
            self.assertEqual(failing.pulls(), 1)
            assert_still_staged(result, "failed_before_rollout", "env_written")

        with self.subTest("the operator presses Ctrl-C (SIGTERM) while the rollback pulls"):
            slow = H.slow_pull("stage-rollback-sigterm")
            run = co.spawn("rollback", "--yes", env=slow.env())
            self.addCleanup(slow.release)
            self.addCleanup(run.kill_group)
            e2e.wait_for(lambda: slow.started.exists() or not run.alive(), timeout=300, what="the rollback to pull")
            self.assertTrue(run.alive(), run.stderr_so_far())
            self.assertEqual(co.env_value(VERSION_KEY), V51)  # the rollback wrote the old pin before pulling
            run.send(signal.SIGTERM)
            result = run.wait(timeout=300)
            self.assertExit(result, 130, "rollback stopped before restarting anything: SIGTERM. ", pending)
            assert_still_staged(result, "interrupted_before_rollout", "env_written")

        # The retry the messages print drops the staged update, and still restarts nothing.
        result = co.bin_update("rollback", "--receipt", staged["_path"], "--yes")
        self.assertExit(result, 0, f"Rolling back {V52} to {VERSION_KEY}={V51}", f"Pulling {image51}",
                        f"Rolled back to {V51} without restarting anything: the update to {V52} was staged")
        self.assertEqual(co.env_text(), original)
        self.assertEqual(self.containers_as_started(co), started)
        self.assertRuns(co, V51, image=image51)
        self.assertEqual(co.history(), [f"start {V51} ok release"])
        rolled = co.receipts()[-1]
        self.assertEqual((rolled["kind"], rolled["status"], rolled["rolls_back"]),
                         ("rollback", "rolled_back", staged["_path"]))
        self.assertEqual([r["status"] for r in co.receipts()],
                         ["staged", "failed_before_rollout", "interrupted_before_rollout", "rolled_back"])
        result = co.bin_update("check", *self.manifest_args(r52))
        self.assertExit(result, 0, f"  Installed     {V51}\n",
                        f"  Decision      update available: run {finish}\n", stream="stdout")
        self.assertNotIn("staged", result.stdout)

    def test_32_rollback_of_a_restaged_update_names_the_later_stage_not_a_finished_one(self):
        r52 = release(V52)
        co = self.checkout("restage")
        co.install()
        original = co.env_bytes()
        started = self.containers_as_started(co)
        hook = self.recording_hook("restage-backup")
        self.assertExit(co.bin_update("apply", "--stage", *self.manifest_args(r52),
                                     "--backup-command", str(hook["hook"])), 0, f"Staged {V52}")
        first = co.receipts()[-1]
        private = co.state_dir / ("stage-" + first["run_id"] + ".json")
        binding = json.loads(private.read_text())
        self.assertEqual(binding["backup_proof_state"], "legacy_absent")
        self.assertNotIn("backup_completed_at", binding)

        def witnesses():
            paths = [Path(first["_path"]), private, hook["log"], hook["pid"], hook["started"]]
            return {str(p): (p.read_bytes(), p.stat().st_ino, p.stat().st_mode,
                             p.stat().st_mtime_ns, p.stat().st_ctime_ns) for p in paths}

        original_witnesses = witnesses()
        # Reverify/cache the same release without authorizing rollout. The first
        # stage's backup, creation time and absent proof still cover this chain.
        result = co.bin_update("apply", "--stage", *self.manifest_args(r52), "--backup-command", str(hook["hook"]))
        self.assertExit(result, 0, f"Skipping the backup: it ran when the update to {V52} was staged ({first['_path']}).",
                        f"Staged {V52}")
        second = co.receipts()[-1]
        self.assertEqual({k: second[k] for k in ("status", "resumes", "env_changes", "previous_values")}, {
            "status": "staged", "resumes": first["_path"], "env_changes": {}, "previous_values": {VERSION_KEY: V51}})
        self.assertEqual(second["backup"], {"ran": False, "staged": first["_path"]})
        self.assertEqual(json.loads((co.state_dir / ("stage-" + second["run_id"] + ".json")).read_text()), binding)
        self.assertEqual(witnesses(), original_witnesses)
        self.assertEqual(hook["log"].read_text().count("--- run"), 1)
        self.assertEqual(self.containers_as_started(co), started)
        e2e.assert_stage_acknowledgment_required(self, co, "apply", *self.manifest_args(r52),
                                               backup_paths=(hook["log"], hook["pid"], hook["started"]))
        self.assertEqual(witnesses(), original_witnesses)
        self.assertEqual(self.containers_as_started(co), started)

        # The first stage's receipt points at the second, which nothing finished: it was only staged again.
        before = co.snapshot()
        result = co.bin_update("rollback", "--receipt", first["_path"], "--yes")
        self.assertExit(result, 3, f"that update was staged again by a later run ({second['_path']}); to undo it, "
                                   f"run `./bin/update rollback --receipt {second['_path']} --yes`")
        self.assertNotIn("finished by a later run", result.stderr)
        self.assertUnchanged(co, before)

        result = co.bin_update("rollback", "--receipt", second["_path"], "--yes")
        self.assertExit(result, 0, f"Rolling back {V52} to {VERSION_KEY}={V51}",
                        f"Rolled back to {V51} without restarting anything: the update to {V52} was staged")
        self.assertEqual(co.env_bytes(), original)
        self.assertEqual(self.containers_as_started(co), started)
        rolled = co.receipts()[-1]
        self.assertEqual((rolled["status"], rolled["rolls_back"]), ("rolled_back", second["_path"]))
        before = co.snapshot()
        for receipt in (first, second):
            with self.subTest(receipt=receipt["_path"]):
                result = co.bin_update("rollback", "--receipt", receipt["_path"], "--yes")
                self.assertExit(result, 3, f"that update was already rolled back ({rolled['_path']})")
        self.assertUnchanged(co, before)


# How bin/update picks its Compose (deploy/update/install.py resolve_compose_command), run with the checkout's
# own deploy/update loaded the way bin/update loads it: prints the command, then its `version --short`.
RESOLVE_COMPOSE = """
import importlib.util, os, subprocess, sys
package = os.path.join(sys.argv[1], "deploy", "update")
spec = importlib.util.spec_from_file_location(
    "futureagi_update", os.path.join(package, "__init__.py"), submodule_search_locations=[package])
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
from futureagi_update.install import resolve_compose_command
command = resolve_compose_command()
print(" ".join(command))
print(subprocess.run([*command, "version", "--short"], capture_output=True, text=True, check=True).stdout.strip())
"""


class StandaloneRoundFourE2E(StandaloneE2EHelpers, unittest.TestCase):
    """The Compose version under test, a registry outage, and commands pasted in another directory."""

    def test_00_bin_update_runs_the_compose_version_under_test(self):
        assert H is not None
        co = self.checkout("compose-version")
        env = co.update_env()
        result = e2e.run([H.python or "python3", "-I", "-c", RESOLVE_COMPOSE, str(co.root)], cwd=co.root, env=env,
                         check=False, timeout=120)
        self.assertEqual(result.code, 0, result.describe())
        command, version = result.stdout.strip().splitlines()
        e2e.log(f"bin/update runs `{command}`: Docker Compose {version} ({H.compose_description})")
        self.assertEqual(version, H.compose_version)
        if e2e.compose_switch() is not None:
            # Whichever way bin/update probes, it gets the binary under test.
            self.assertEqual(command, "docker compose")
            for probe in (["docker", "compose", "version", "--short"], ["docker-compose", "version", "--short"]):
                with self.subTest(probe=" ".join(probe)):
                    answer = subprocess.run(probe, env=env, capture_output=True, text=True, check=False)
                    self.assertEqual((answer.returncode, answer.stdout.strip()), (0, H.compose_version))

        # The fixture's app mounts ./${FAGI_E2E_LIMITS_PATH:-...} as the real file mounts the agentcc config.
        # Compose before v2.40.3 rejects that in `config --no-interpolate`, so bin/update must plan without it.
        raw = co.compose("config", "--no-interpolate", "--format", "json", check=False)
        e2e.log(f"`config --no-interpolate` on Docker Compose {H.compose_version}: exit {raw.code} "
                f"{(raw.stderr.strip().splitlines() or [''])[-1]}")
        if tuple(int(part) for part in re.findall(r"\d+", H.compose_version)[:3]) < (2, 40, 3):
            self.assertIn("too many colons", raw.stderr, raw.describe())
        co.install()
        result = co.bin_update("check", *self.manifest_args(release(V52)))
        self.assertExit(result, 0, f"  Release       {V52}", "  Signature     verified",
                        "  Decision      update available", stream="stdout")

    def test_01_compose_v225_refuses_every_update_command_with_g1(self):
        assert H is not None
        if H.compose_version != "2.25.0":
            self.skipTest("focused G1 refusal test; run with FUTURE_AGI_UPDATE_E2E_COMPOSE=v2.25.0")
        co = self.checkout("compose-v225-refusal")
        before = co.snapshot()
        g1 = ("Docker Compose v2.25.0 cannot print its configuration as JSON, which bin/update needs "
              "(fixed in v2.26.0). Upgrade Docker Compose and run bin/update again")
        for args in (("status",), ("check",), ("apply", "--dry-run"), ("rollback", "--yes")):
            with self.subTest(command=" ".join(args)):
                result = co.bin_update(*args)
                self.assertExit(result, 2, f"bin/update: {g1}")
                self.assertEqual(result.stderr.splitlines()[0], f"bin/update: {g1}", result.describe())
                self.assertUnchanged(co, before)

    @e2e.slow
    def test_33_a_staged_update_finishes_and_rolls_back_while_the_registry_is_down(self):
        # The registry is stopped for real (a private one only this test pulls from). The images the stage pulled
        # and verified, and the ones that ran before, are on the host: no command needs a pull, and every image is
        # still checked against its digest before anything starts.
        assert H is not None
        registry = self.own_registry("outage", V51, V52)
        r52 = release(V52, registry=registry.host)
        image51, image52 = H.image_ref(V51, registry=registry.host), H.image_ref(V52, registry=registry.host)
        co = self.checkout("outage", registry=registry.host)
        co.install()
        original = co.env_text()
        started = self.containers_as_started(co)
        old_app = co.app()
        self.assertEqual(old_app.image, image51)
        not_pulled = "Already on this host with the expected digest, not pulled: "

        result = co.bin_update("apply", "--stage", *self.manifest_args(r52), "--allow-no-backup")
        self.assertExit(result, 0, f"Pulling {image52}", f"Staged {V52}")
        self.assertIn(f"{registry.host}/{e2e.REPOSITORY}@{r52.digest}", co.image_info(image52)["repo_digests"])

        registry.stop()
        outage = e2e.run(["docker", "pull", image52], check=False, timeout=120)
        self.assertNotEqual(outage.code, 0, f"the stopped registry still serves {image52}:\n{outage.describe()}")

        with self.subTest("the staged update is dropped, then staged again: nothing is pulled"):
            result = co.bin_update("rollback", "--yes")
            self.assertExit(result, 0, f"{not_pulled}{image51}",
                            f"Rolled back to {V51} without restarting anything: the update to {V52} was staged")
            self.assertNotIn("Pulling", result.stderr)
            self.assertEqual(co.env_text(), original)
            self.assertEqual(self.containers_as_started(co), started)
            result = co.bin_update("apply", "--stage", *self.manifest_args(r52), "--allow-no-backup")
            self.assertExit(result, 0, f"{not_pulled}{image52}", f"Staged {V52}")
            self.assertNotIn("Pulling", result.stderr)
            self.assertEqual(self.containers_as_started(co), started)
        staged = co.receipts()[-1]
        self.assertEqual((staged.get("status"), staged.get("target_image_ids")),
                         ("staged", {"app": co.image_info(image52)["id"]}))

        e2e.assert_stage_acknowledgment_required(self, co, "apply", *self.manifest_args(r52), "--allow-no-backup")
        with self.subTest("still verified: a local image without the signed digest is pulled, which fails"):
            # Keep the verified image under a second tag, and point the release's tag at another image.
            keep = H.image_ref(f"keep-{os.urandom(4).hex()}", registry=registry.host)
            H.track_tag(keep)
            e2e.run(["docker", "tag", image52, keep])
            self.addCleanup(e2e.run, ["docker", "rmi", keep], check=False)
            e2e.run(["docker", "tag", e2e.BASE_IMAGE, image52])
            try:
                result = co.bin_update("apply", *self.manifest_args(r52), "--allow-no-backup",
                                       "--acknowledge-stale-stage")
            finally:
                e2e.run(["docker", "tag", keep, image52])
            self.assertExit(result, 4, "apply stopped before restarting anything: ", "pull app` failed",
                            f"The update to {V52} staged earlier is still pending: .env selects it")
            self.assertNotIn(not_pulled, result.stderr)
            self.assertEqual(co.env_value(VERSION_KEY), V52)
            self.assertEqual(self.containers_as_started(co), started)
            self.assertEqual(co.receipts()[-1]["status"], "failed_before_rollout")
        self.assertIn(f"{registry.host}/{e2e.REPOSITORY}@{r52.digest}", co.image_info(image52)["repo_digests"])

        e2e.assert_stage_acknowledgment_required(self, co, "apply", *self.manifest_args(r52), "--allow-no-backup")
        result = co.bin_update("apply", *self.manifest_args(r52), "--allow-no-backup", "--acknowledge-stale-stage")
        self.assertExit(result, 0, f"{not_pulled}{image52}", "Restarting the stack on the new images",
                        f"Updated to {V52}. Receipt: ")
        self.assertNotIn("Pulling", result.stderr)
        app = self.assertRuns(co, V52, image=image52)
        self.assertIn(f"{registry.host}/{e2e.REPOSITORY}@{r52.digest}", co.image_info(app.image_id)["repo_digests"])
        finished = co.receipts()[-1]
        self.assertEqual({k: finished[k] for k in ("status", "resumes", "target_image_ids")},
                         {"status": "succeeded", "resumes": staged["_path"], "target_image_ids": {"app": app.image_id}})

        # Rolling the finished update back restarts on the image that ran before, still without the registry.
        result = co.bin_update("rollback", "--yes")
        self.assertExit(result, 0, f"{not_pulled}{image51}", "Restarting the stack on the new images",
                        f"Rolled back to {V51}. Receipt: ")
        self.assertNotIn("Pulling", result.stderr)
        self.assertEqual(co.env_text(), original)
        back = self.assertRuns(co, V51, image=image51)
        self.assertEqual(back.image_id, old_app.image_id)
        self.assertEqual(co.receipts()[-1]["status"], "rolled_back")
        self.assertEqual(co.history(), [f"start {V51} ok release", f"start {V52} ok release", f"start {V51} ok release"])
        self.assertFalse(e2e.http_ok(f"http://{registry.host}/v2/"), "the registry came back during the test")

    @e2e.slow
    def test_34_printed_commands_work_pasted_in_another_directory(self):
        # bin/update run from another directory prints commands that start with `cd <checkout> && `: pasted in
        # that directory (which holds a Compose project of its own), each one acts on the checkout.
        assert H is not None
        r52, r53 = release(V52), release(V53)
        hook = self.recording_hook("pasted-hook")
        co = self.checkout("pasted", settings={"FUTURE_AGI_UPDATE_BACKUP_COMMAND": str(hook["hook"])})
        co.install()
        elsewhere = H.run_dir / "pasted-elsewhere" / "deeper"
        elsewhere.mkdir(parents=True)
        decoy = f"{H.prefix}pasted-decoy"
        (elsewhere / "docker-compose.yml").write_text(
            f"name: {decoy}\nservices:\n  decoy:\n    image: {e2e.BASE_IMAGE}\n", encoding="utf-8")
        cd = f"cd {shlex.quote(str(co.root))} && "

        def paste(command: str) -> e2e.Result:
            result = e2e.run(["sh", "-c", command], cwd=elsewhere, env=co.update_env(), check=False, timeout=900)
            co._log(f"[pasted in {elsewhere}]\n{result.describe()}")
            return result

        manifest52 = os.path.relpath(r52.manifest, elsewhere)
        result = co.bin_update("check", "--manifest", manifest52, cwd=elsewhere)
        self.assertExit(result, 0, stream="stdout")
        decision = re.search(r"\n  Decision      update available: run (.+)\n", result.stdout)
        self.assertIsNotNone(decision, result.describe())
        plan = json.loads(co.bin_update("check", "--format", "json", "--manifest", manifest52, cwd=elsewhere).stdout)
        apply52 = plan["next_command"]
        self.assertEqual(decision.group(1), apply52)
        self.assertTrue(apply52.startswith(f"{cd}./bin/update apply --manifest "), apply52)

        result = paste(apply52)
        self.assertExit(result, 0, f"Running the backup hook: {hook['hook']}", f"Updated to {V52}. Receipt: ")
        self.assertEqual([(r["FROM"], r["TO"]) for r in parse_hook_log(hook["log"])], [(V51, V52)])
        self.assertTrue(os.path.samefile(co.receipts()[-1]["manifest_source"], r52.manifest))
        self.assertRuns(co, V52, image=H.image_ref(V52))

        # A release that never becomes ready: the run stops while containers are replaced and prints how to
        # inspect the stack, finish the update, or go back.
        result = co.bin_update("apply", "--manifest", os.path.relpath(r53.manifest, elsewhere), "--allow-no-backup",
                               cwd=elsewhere)
        self.assertExit(result, 5, f"apply to {V53} stopped while containers were being replaced")
        failed = co.receipts()[-1]
        inspect = re.search(r"Inspect `(.+?) ps -a` and `(.+?) logs`", result.stderr)
        finish = re.search(r"To finish, run `(.+?)` again", result.stderr)
        back = re.search(r"To go back, run `(.+?)`", result.stderr)
        self.assertTrue(inspect and finish and back, result.describe())
        self.assertEqual(inspect.group(1), inspect.group(2))
        self.assertTrue(inspect.group(1).startswith(cd), inspect.group(1))
        self.assertTrue(finish.group(1).startswith(f"{cd}./bin/update apply --manifest "), finish.group(1))
        self.assertEqual(back.group(1), f"{cd}./bin/update rollback --receipt {shlex.quote(failed['_path'])} --yes")

        listing = paste(f"{inspect.group(1)} ps -a")
        self.assertEqual(listing.code, 0, listing.describe())
        self.assertIn(co.app().name, listing.stdout, listing.describe())
        self.assertNotIn(decoy, listing.output)
        logs = paste(f"{inspect.group(1)} logs --no-color app")
        self.assertEqual(logs.code, 0, logs.describe())
        self.assertIn(f"{V53}", logs.output, logs.describe())

        result = paste(back.group(1))
        self.assertExit(result, 0, f"Rolling back {V53} to {VERSION_KEY}={V52}", f"Rolled back to {V52}. Receipt: ")
        self.assertRuns(co, V52, image=H.image_ref(V52))
        self.assertEqual(co.receipts()[-1]["rolls_back"], failed["_path"])
        self.assertEqual(sorted(p.name for p in elsewhere.iterdir()), ["docker-compose.yml"])
        # Nothing ever ran the decoy project.
        decoys = e2e.run(["docker", "ps", "-a", "-q", "--filter", f"label=com.docker.compose.project={decoy}"])
        self.assertEqual(decoys.stdout.split(), [])


class StandaloneRoundFiveE2E(StandaloneE2EHelpers, unittest.TestCase):
    """Git-tagged deployment drift during rollback."""

    @e2e.slow
    def test_35_git_tagged_deployment_drift_blocks_rollback_until_pasted_restore(self):
        # v1.51.0's compose file bind-mounts a file (LEGACY) that v1.52.0 drops: its compose file no longer mounts
        # it, git removes it, and its deployment-files.txt (the checkout's after the update) never listed it. The
        # rollback must still find it in v1.51.0's compose file, name it, and restore it with the pasted command.
        assert H is not None
        rel = release(V52, variant="compose-change")
        co = self.checkout("git-rollback-drift", version=V51)
        legacy, mounted = "deploy/standalone/legacy-app.conf", "/etc/futureagi/legacy-app.conf"
        legacy_text = f"legacy setting only {V51}'s compose file mounts\n"
        limits = "      - ./${FAGI_E2E_LIMITS_PATH:-deploy/standalone/datastore/limits.conf}:/etc/futureagi/e2e-limits.conf:ro\n"
        compose51 = co.root / "docker-compose.yml"
        text51 = compose51.read_text(encoding="utf-8")
        self.assertEqual(text51.count(limits), 1)
        compose51.write_text(text51.replace(limits, f"{limits}      - ./{legacy}:{mounted}:ro\n"), encoding="utf-8")
        (co.root / legacy).write_text(legacy_text, encoding="utf-8")
        co.install()
        self.assertEqual(co.exec_app("cat", mounted).stdout, legacy_text)
        old_app, old_db = co.app(), co.service("postgres")
        original_env = co.env_bytes()

        def git(*args: str) -> e2e.Result:
            return e2e.run(["git", *args], cwd=co.root, check=False)

        def paste(command: str) -> None:
            # Exactly as printed, through a shell, in the directory bin/update ran in.
            pasted = e2e.run(["sh", "-c", command], cwd=co.root, env=co.update_env(), check=False)
            co._log(f"[pasted]\n{pasted.describe()}")
            self.assertEqual(pasted.code, 0, pasted.describe())

        e2e.run(["git", "init", "-q"], cwd=co.root)
        e2e.run(["git", "config", "user.name", "Update E2E"], cwd=co.root)
        e2e.run(["git", "config", "user.email", "update-e2e@example.invalid"], cwd=co.root)
        e2e.run(["git", "add", "--", *e2e.DEPLOYMENT_FILES, "deploy/update/deployment-files.txt", legacy], cwd=co.root)
        e2e.run(["git", "commit", "-q", "-m", f"fixture {V51}"], cwd=co.root)
        e2e.run(["git", "tag", V51], cwd=co.root)

        target = H.template("compose-change") / "docker-compose.yml"
        shutil.copyfile(target, co.root / "docker-compose.yml")
        e2e.run(["git", "rm", "-q", "--", legacy], cwd=co.root)
        e2e.run(["git", "add", "--", "docker-compose.yml"], cwd=co.root)
        e2e.run(["git", "commit", "-q", "-m", f"fixture {V52}"], cwd=co.root)
        e2e.run(["git", "tag", V52], cwd=co.root)
        self.assertNotEqual((co.root / "docker-compose.yml").read_bytes(),
                            (H.template("default") / "docker-compose.yml").read_bytes())
        self.assertFalse(os.path.lexists(co.root / legacy))
        self.assertNotIn(legacy, (co.root / "deploy/update/deployment-files.txt").read_text(encoding="utf-8"))
        self.assertNotIn(legacy, (co.root / "docker-compose.yml").read_text(encoding="utf-8"))
        self.assertIn(f"./{legacy}:{mounted}:ro", git("show", f"{V51}:docker-compose.yml").stdout)

        result = co.bin_update("apply", *self.manifest_args(rel), "--allow-no-backup")
        self.assertExit(result, 0, f"Updated to {V52}. Receipt: ")
        self.assertRuns(co, V52)
        self.assertNotIn(mounted, [mount.get("Destination") for mount in co.app().mounts])
        before = co.snapshot()

        result = co.bin_update("rollback", "--yes")
        restore_old = f"git checkout {V51} -- docker-compose.yml {legacy}"
        rollback = "./bin/update rollback --yes"
        restore_new = f"`git checkout {V52} -- docker-compose.yml` and `git rm -r -f -q --ignore-unmatch -- {legacy}`"
        self.assertExit(result, 3,
                        f"The rollback starts {V51}'s images, which need {V51}'s deployment files; these differ in "
                        f"this checkout: docker-compose.yml, {legacy} (missing).",
                        f"Restore {V51}'s versions with `{restore_old}` (bin/update and your env file stay as they "
                        f"are), then run `{rollback}` again. Before updating again, run {restore_new}.")
        self.assertUnchanged(co, before)
        self.assertFalse(os.path.lexists(co.root / legacy), "the refused rollback created the bind-mount source")
        printed_restore = re.search(rf"Restore {re.escape(V51)}'s versions with `([^`]+)`", result.stderr)
        printed_back = re.search(r"Before updating again, run (`[^`]+`(?: and `[^`]+`)*)\.", result.stderr)
        self.assertTrue(printed_restore and printed_back, result.describe())

        # Paste the exact command from the refusal: it restores v1.51.0's compose file and the file it mounts.
        paste(printed_restore.group(1))
        self.assertEqual(git("diff", "--quiet", V51, "--", "docker-compose.yml", legacy).code, 0)
        self.assertEqual((co.root / legacy).read_text(encoding="utf-8"), legacy_text)
        result = co.bin_update("rollback", "--yes")
        self.assertExit(result, 0, f"Rolled back to {V51}. Receipt: ")
        self.assertEqual(co.env_bytes(), original_env)
        self.assertNotEqual(co.app().id, old_app.id)
        self.assertRuns(co, V51)
        self.assertEqual(co.service("postgres").id, old_db.id)
        self.assertEqual(co.receipts()[-1]["status"], "rolled_back")
        # The old image runs with the restored file mounted (not a directory Docker made in its place).
        self.assertIn(mounted, [mount.get("Destination") for mount in co.app().mounts if mount.get("Type") == "bind"])
        self.assertEqual(co.exec_app("cat", mounted).stdout, legacy_text)
        self.assertTrue((co.root / legacy).is_file())

        # The refusal also printed the exact commands that bring v1.52.0's files back before updating again.
        for command in re.findall(r"`([^`]+)`", printed_back.group(1)):
            paste(command)
        self.assertEqual(git("diff", "--quiet", V52, "--", "docker-compose.yml").code, 0)
        self.assertFalse(os.path.lexists(co.root / legacy))
        self.assertEqual(git("status", "--porcelain", "--", "docker-compose.yml", legacy).stdout, "")
        result = co.bin_update("apply", *self.manifest_args(rel), "--allow-no-backup")
        self.assertExit(result, 0, f"Updated to {V52}. Receipt: ")
        self.assertRuns(co, V52)
        self.assertEqual(co.receipts()[-1]["status"], "succeeded")

class StandaloneRecoveryOwnershipE2E(StandaloneE2EHelpers, unittest.TestCase):
    """Real Docker provenance and a genuinely SIGKILLed transaction; no application mocks."""

    def test_sigkill_restores_exact_duplicate_lexical_bytes(self):
        from update_e2e.faults import FaultDocker

        assert H is not None
        registry = self.own_registry('exact-recovery-registry', V51, V52)
        co = self.checkout('exact-recovery', version=V51, registry=registry.host)
        raw = co.env_bytes().replace(
            f'{VERSION_KEY}={V51}'.encode(),
            (f'export {VERSION_KEY}: "v0.9.0" # first\n'
             f'MIDDLE=${{{VERSION_KEY}}}\n{VERSION_KEY}=\'{V51}\' # last').encode())
        raw = b'\xef\xbb\xbf' + raw.rstrip(b'\n').replace(b'\n', b'\r\n')
        co.env_path.write_bytes(raw)
        co.env_path.chmod(0o640)
        co.install()
        co.exec_app('sh', '-c', 'printf exact-sentinel > /data/operator-sentinel')
        before = self.containers_as_started(co)
        config = json.loads(co.compose('config', '--format', 'json').stdout)
        fd = FaultDocker(H.hook_dir('exact-kill') / 'docker')
        proc = co.spawn('apply', *self.manifest_args(release(V52, registry=registry.host)),
                        '--allow-no-backup', env=fd.env(at=1, match='*pull*', action='sigkill'))
        self.addCleanup(proc.kill_group)
        result = proc.wait(timeout=180)
        self.assertEqual(result.code, -signal.SIGKILL, result.describe())
        self.assertTrue(fd.signalled(), 'the wrapper must prove this was SIGKILL, not a timeout')
        self.assertNotIn('[harness] killed after', result.stderr)
        self.assertEqual(co.journal()['stage'], 'env_written')
        self.assertIn('env_recovery', co.journal())
        self.assertNotEqual(co.env_bytes(), raw)
        self.assertEqual(self.containers_as_started(co), before)
        recovered = co.bin_update('rollback')  # without --yes still performs recovery
        self.assertExit(recovered, 0, 'Recovered an interrupted apply', stream='both')
        self.assertEqual(co.env_bytes(), raw)
        self.assertEqual(stat.S_IMODE(co.env_path.stat().st_mode), 0o640)
        self.assertIsNone(co.journal())
        self.assertEqual(self.containers_as_started(co), before)
        self.assertEqual(json.loads(co.compose('config', '--format', 'json').stdout), config)
        self.assertEqual(co.exec_app('cat', '/data/operator-sentinel').stdout, 'exact-sentinel')
        self.assertNotIn('env_recovery', co.receipts()[-1])
        self.assertNotIn(co.secret, ''.join(p.read_text() for p in co.receipt_paths()))

    def test_sigkill_after_forward_and_inverse_exchange_reconciles_without_toggle(self):
        assert H is not None
        registry = self.own_registry('native-exchange-kills', V51, V52)
        co = self.checkout('native-exchange-kills', version=V51, registry=registry.host)
        co.install()
        original = co.env_bytes()
        before = self.containers_as_started(co)
        source = co.root / 'deploy/update/envcommit.py'
        marker = co.root / '.test-exchange-kill-count'
        source.write_text(source.read_text() + '\n' + '\n'.join((
            'import signal',
            '_kill_exchange = exchange',
            'def exchange(a, b, c, d):',
            '    result = _kill_exchange(a, b, c, d)',
            '    if b == "slot":',
            f'        marker = Path({str(marker)!r})',
            '        count = int(marker.read_text()) if marker.exists() else 0',
            '        if count < 2:',
            '            marker.write_text(str(count + 1))',
            '            os.kill(os.getpid(), signal.SIGKILL)',
            '    return result',
            '')))
        commands = [('apply', *self.manifest_args(release(V52, registry=registry.host)), '--allow-no-backup'),
                    ('rollback',)]
        for number, command in enumerate(commands, 1):
            proc = co.spawn(*command)
            self.addCleanup(proc.kill_group)
            result = proc.wait(timeout=180)
            self.assertEqual(result.code, -signal.SIGKILL, result.describe())
            self.assertEqual(marker.read_text(), str(number))
            self.assertTrue(co.journal())
            self.assertFalse(co.receipts())
            self.assertEqual(self.containers_as_started(co), before)
        self.assertEqual(co.env_bytes(), original)
        result = co.bin_update('rollback')
        self.assertExit(result, 0, 'Recovered an interrupted apply', stream='both')
        self.assertEqual(co.env_bytes(), original)
        self.assertIsNone(co.journal())
        self.assertEqual(marker.read_text(), '2')
        self.assertEqual(len(list(co.env_path.parent.glob('.futureagi-updates/env-commits/*/*/slot'))), 2)
        self.assertEqual(self.containers_as_started(co), before)

    def boundary_conflicts(self, recovering):
        from update_e2e.faults import FaultDocker

        assert H is not None
        registry = self.own_registry('commit-boundary-' + str(recovering).lower(), V51, V52)
        for rename in (False, True):
            with self.subTest(recovering=recovering, rename=rename):
                co = self.checkout('commit-' + str(recovering).lower() + '-' + str(rename).lower(),
                                   version=V51, registry=registry.host)
                co.install()
                before_stack = self.containers_as_started(co)
                original = co.env_bytes()
                args = ('apply', *self.manifest_args(release(V52, registry=registry.host)), '--allow-no-backup')
                if recovering:
                    fd = FaultDocker(H.hook_dir('commit-kill-' + str(rename)) / 'docker')
                    proc = co.spawn(*args, env=fd.env(at=1, match='*pull*', action='sigkill'))
                    self.addCleanup(proc.kill_group)
                    killed = proc.wait(timeout=180)
                    self.assertEqual(killed.code, -signal.SIGKILL, killed.describe())
                    self.assertTrue(fd.signalled())
                before = co.env_bytes()
                operator = before + b'\nODD_NAME=boundary-only-secret-96a8\n'
                # Fault code is appended ONLY to this disposable checkout, never
                # enabled by a production environment variable or CLI switch.
                source = co.root / 'deploy/update/envcommit.py'
                source.write_text(source.read_text() + '\n' + '\n'.join((
                    '_boundary_original = exchange',
                    'def exchange(a, b, c, d):',
                    '    if b == "slot":',
                    f'        target = Path({str(co.env_path)!r})',
                    f'        data = {operator!r}',
                    '        staged = target.with_name("boundary-editor")',
                    '        staged.write_bytes(data)' if rename else '        target.write_bytes(data)',
                    '        os.replace(staged, target)' if rename else '        pass',
                    '    return _boundary_original(a, b, c, d)',
                    '')))
                result = co.bin_update('rollback') if recovering else co.bin_update(*args)
                self.assertExit(result, 2, 'env commit conflict', stream='both')
                self.assertEqual(self.containers_as_started(co), before_stack)
                self.assertTrue(co.journal())
                self.assertFalse(co.receipts())
                direction = 'restore' if recovering else 'forward'
                op_id = co.journal()['env_recovery']['commit'][direction]
                ops = list(co.env_path.parent.glob('.futureagi-updates/env-commits/*/' + op_id))
                self.assertEqual(len(ops), 1)
                self.assertEqual((ops[0] / 'slot').read_bytes(), operator)
                self.assertEqual((ops[0] / 'candidate').read_bytes(), co.env_bytes())
                if recovering:
                    self.assertEqual(co.env_bytes(), original)
                stable = co.env_bytes()
                for _ in range(2):
                    retried = co.bin_update('rollback')
                    self.assertExit(retried, 2, 'conflict', stream='both')
                    self.assertEqual(co.env_bytes(), stable)
                    self.assertTrue(co.journal())
                    self.assertFalse(co.receipts())
                status = co.bin_update('status')
                self.assertExit(status, 2, 'conflict', stream='both')
                self.assertNotIn('boundary-only-secret-96a8', result.output + retried.output + status.output)
                self.assertEqual(self.containers_as_started(co), before_stack)

    def test_final_forward_boundary_preserves_in_place_and_rename_edits(self):
        self.boundary_conflicts(False)

    def test_final_recovery_boundary_preserves_in_place_and_rename_edits(self):
        self.boundary_conflicts(True)

    def test_two_checkouts_unset_export_cannot_adopt_the_default_owner(self):
        assert H is not None
        a = self.checkout('standalone', version=V51)  # exactly the Compose file's fallback name
        b = self.checkout('export-owner-b', version=V51)
        a.install()
        original_b = b.env_bytes()
        b.env_path.write_bytes(re.sub(br'(?m)^COMPOSE_PROJECT_NAME=.*\n', b'', original_b))
        # Always restore B's own selection before its cleanup; never use B's fallback to down A.
        self.addCleanup(b.env_path.write_bytes, original_b)
        exported = e2e.clean_environ({'COMPOSE_PROJECT_NAME': b.project})
        e2e.run([*H.compose, 'up', '-d', '--wait', '--wait-timeout', '120'], cwd=b.root, env=exported, timeout=180)
        b.wait_healthy()
        self.assertEqual(json.loads(b.compose('config', '--format', 'json').stdout)['name'], a.project)
        for co in (a, b):
            e2e.run(['docker', 'exec', co.app().id, 'sh', '-c',
                     'printf ' + co.project + ' > /data/operator-sentinel'])
        hook = self.recording_hook('ownership-must-not-backup')
        b.write_update_conf({'FUTURE_AGI_UPDATE_BACKUP_COMMAND': str(hook['hook'])})
        b.state_dir.mkdir(mode=0o700, exist_ok=True)
        journal = b.state_dir / 'journal.json'
        journal.write_text(json.dumps({'kind': 'apply', 'stage': 'env_written', 'mode': 'standalone',
                                      'production': False, 'env_file': '.env', 'project': a.project,
                                      'target_version': V52, 'found_values': {VERSION_KEY: V51}}))
        before = (a.snapshot(), b.snapshot(), journal.read_bytes())
        for command in (('check', *self.manifest_args(release(V52))),
                        ('apply', *self.manifest_args(release(V52))), ('rollback',), ('rollback', '--yes')):
            with self.subTest(command=command):
                result = b.bin_update(*command)
                self.assertEqual(result.code, 2, result.describe())
                self.assertIn('ownership', result.output)
                self.assertEqual((a.snapshot(), b.snapshot(), journal.read_bytes()), before)
                self.assertFalse(hook['log'].exists())
                for co in (a, b):
                    self.assertEqual(e2e.run(['docker', 'exec', co.app().id, 'cat',
                                             '/data/operator-sentinel']).stdout, co.project)
        # Positive control through the very same updater and Docker daemon.
        result = a.bin_update('apply', *self.manifest_args(release(V52)), '--allow-no-backup')
        self.assertExit(result, 0, f'Updated to {V52}', stream='both')
        self.assertRuns(a, V52)
        self.assertEqual(b.snapshot(), before[1])
        self.assertEqual(e2e.run(['docker', 'exec', a.app().id, 'cat', '/data/operator-sentinel']).stdout, a.project)


    def test_real_compose_explicit_custom_env_file_provenance(self):
        from deploy.update.install import Installation, InstallError

        assert H is not None
        co = self.checkout('custom-env-provenance', version=V51)
        # Only provenance is under test: use the small real-stack fixture as a production-shaped pair.
        base = co.root / 'docker-compose.distributed.yml'
        overlay = co.root / 'deploy/docker-compose.production.yml'
        base.write_bytes((co.root / 'docker-compose.yml').read_bytes())
        overlay.write_text('services: {}\n')
        custom = co.root / 'deploy/prod custom.env'
        custom.write_bytes(co.env_bytes())
        args = ['--env-file', str(custom.relative_to(co.root)), '-f', str(base), '-f', str(overlay)]
        e2e.run([*H.compose, *args, 'up', '-d', '--wait', '--wait-timeout', '120'],
                cwd=co.root, env=e2e.clean_environ(), timeout=180)
        records = json.loads(e2e.run(['docker', 'inspect', *[c.id for c in co.containers()]]).stdout)
        labels = [r['Config']['Labels'] for r in records]
        field = 'com.docker.compose.project.environment_file'
        e2e.log('explicit env provenance on Compose ' + H.compose_version + ': ' +
                json.dumps([label.get(field) for label in labels]))
        inst = Installation(co.root, production=True, env_file=custom, environ=e2e.clean_environ(), compose=H.compose)
        if all(field in label for label in labels):
            self.assertEqual({(co.root / label[field]).resolve() for label in labels}, {custom.resolve()})
            inst.check_ownership()
            other = co.root / 'deploy/other.env'
            other.write_bytes(custom.read_bytes())
            wrong = Installation(co.root, production=True, env_file=other, environ=e2e.clean_environ(), compose=H.compose)
            with self.assertRaisesRegex(InstallError, 'environment_file'):
                wrong.check_ownership()
        else:
            # Older Compose without this provenance cannot prove which production env file owns the stack.
            with self.assertRaisesRegex(InstallError, 'environment_file'):
                inst.check_ownership()
        self.assertEqual(co.env_bytes(), custom.read_bytes())


class StandaloneBuildContextE2E(StandaloneE2EHelpers, unittest.TestCase):
    def test_retained_env_secrets_never_enter_root_or_backend_image_layers(self):
        from update_e2e.build_contexts import assert_private_contexts_excluded
        assert H is not None
        assert_private_contexts_excluded(self, e2e.update_source(), H.base / (H.prefix + 'context-probes'))


if __name__ == "__main__":
    unittest.main()
