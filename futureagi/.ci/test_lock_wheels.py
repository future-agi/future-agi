"""Credential-free checks for the once-per-run lock wheel fetch, fail-closed paths
included, and for the workflow wiring that keeps every backend `uv sync` off
the network for those wheels. Synthetic locks and wheels only: no download."""

import hashlib
import importlib.util
import io
import itertools
import pathlib
import re
import sys
import tempfile
import tomllib
import unittest
import urllib.error

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "futureagi/.ci/lock_wheels.py"
SPEC = importlib.util.spec_from_file_location("lock_wheels", SCRIPT)
lock_wheels = importlib.util.module_from_spec(SPEC)
sys.modules["lock_wheels"] = lock_wheels
SPEC.loader.exec_module(lock_wheels)

NAME = "en-core-web-sm"
URL = (
    "https://github.com/explosion/spacy-models/releases/download/"
    "en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"
)
WHEEL = b"synthetic wheel bytes\n"
DIGEST = hashlib.sha256(WHEEL).hexdigest()
REGISTRY = '{ registry = "https://pypi.org/simple" }'


def row(url=URL, digest="sha256:" + DIGEST):
    return f'{{ url = "{url}", hash = "{digest}" }}'


def package(name, version, source, wheels=""):
    lines = ["", "[[package]]", f'name = "{name}"', f'version = "{version}"']
    lines += [f"source = {source}", f"wheels = {wheels}" if wheels else ""]
    return "\n".join(lines) + "\n"


def lock_text(
    *, name=NAME, version="3.8.0", source=None, wheels=None, extra_entry=False
):
    source = source if source is not None else f'{{ url = "{URL}" }}'
    wheels = wheels if wheels is not None else f"[\n    {row()},\n]"
    entry = package(name, version, source, wheels)
    other = package("other", "1.0", REGISTRY)
    return "version = 1\n" + entry + other + (entry if extra_entry else "")


class Opener:
    """Stands in for urllib: replays failures, then serves `body`."""

    def __init__(self, body=WHEEL, failures=()):
        self.body, self.failures, self.calls = body, list(failures), []

    def __call__(self, request, timeout):
        self.calls.append((request.full_url, timeout))
        if self.failures:
            raise self.failures.pop(0)
        return io.BytesIO(self.body)


class LockParsingTest(unittest.TestCase):
    def test_a_well_formed_entry_yields_the_lock_hash_and_wheel_name(self):
        item = lock_wheels.locked(lock_text(), NAME)
        self.assertEqual(item.url, URL)
        self.assertEqual(item.sha256, DIGEST)
        self.assertEqual(item.version, "3.8.0")
        self.assertEqual(item.filename, "en_core_web_sm-3.8.0-py3-none-any.whl")

    def test_anything_but_one_unambiguous_hashed_github_wheel_fails_closed(self):
        other_url = URL.replace("3.8.0-py3", "3.7.1-py3")
        md5 = "md5:" + DIGEST[:32]
        short = "sha256:" + DIGEST[:63]
        for label, text in {
            "absent": lock_text(name="en-core-web-md"),
            "duplicated": lock_text(extra_entry=True),
            "registry source": lock_text(source=REGISTRY),
            "no wheels": lock_text(wheels="[]"),
            "two wheels": lock_text(wheels=f"[{row()}, {row()}]"),
            "wheel is not the source": lock_text(wheels=f"[{row(other_url)}]"),
            "no hash": lock_text(wheels=f'[{{ url = "{URL}" }}]'),
            "not sha256": lock_text(wheels=f"[{row(digest=md5)}]"),
            "short hash": lock_text(wheels=f"[{row(digest=short)}]"),
            "other version": lock_text(version="3.7.1"),
            "not toml": "[[package]\n",
        }.items():
            with self.subTest(label):
                with self.assertRaises(lock_wheels.LockError):
                    lock_wheels.locked(text, NAME)

    def test_a_lock_pointing_at_another_host_or_scheme_is_refused(self):
        for url in (
            URL.replace("https://github.com/", "https://example.com/"),
            URL.replace("https://", "http://"),
            URL + "?download=1",
            URL.replace("en_core_web_sm-3.8.0-py3", "other_model-3.8.0-py3"),
        ):
            with self.subTest(url):
                text = lock_text().replace(URL, url)
                with self.assertRaises(lock_wheels.LockError):
                    lock_wheels.locked(text, NAME)

    def test_the_key_moves_with_the_locked_hash(self):
        first = lock_wheels.key([lock_wheels.locked(lock_text(), NAME)])
        other = lock_text().replace(DIGEST, "0" * 64)
        second = lock_wheels.key([lock_wheels.locked(other, NAME)])
        self.assertRegex(first, r"^backend-lock-wheels-v1-[0-9a-f]{64}$")
        self.assertNotEqual(first, second)


class RealLockTest(unittest.TestCase):
    def test_every_prefetched_package_is_pinned_by_the_real_lock(self):
        # Parses futureagi/uv.lock as CI does; the expected hash is never
        # restated here, only its presence and shape.
        items = lock_wheels.locked_all(ROOT / "futureagi/uv.lock")
        self.assertEqual([i.name for i in items], list(lock_wheels.PACKAGES))
        pyproject = (ROOT / "futureagi/pyproject.toml").read_text()
        for item in items:
            self.assertIn(f'"{item.name} @ {item.url}"', pyproject)

    def test_no_other_direct_url_wheel_is_left_on_the_network(self):
        lock = tomllib.loads((ROOT / "futureagi/uv.lock").read_text())
        sources = {p["name"]: p.get("source", {}) for p in lock["package"]}
        urls = sorted(name for name, src in sources.items() if set(src) == {"url"})
        self.assertEqual(urls, sorted(lock_wheels.PACKAGES))


class FetchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        self.cache, self.out = root / "cache", root / "out"
        self.items = [lock_wheels.locked(lock_text(), NAME)]
        self.filename = self.items[0].filename
        self.sleeps = []

    def fetch(self, opener, clock=None):
        lock_wheels.fetch(
            self.items,
            self.cache,
            self.out,
            opener=opener,
            sleep=self.sleeps.append,
            clock=clock or (lambda: 0.0),
        )

    def assertNothingKept(self):
        self.assertEqual(sorted(p.name for p in self.cache.iterdir()), [])
        self.assertFalse((self.out / self.filename).exists())

    def test_cold_fetch_downloads_once_verifies_and_stages(self):
        opener = Opener()
        self.fetch(opener)
        self.assertEqual(opener.calls, [(URL, lock_wheels.READ_TIMEOUT_SECONDS)])
        self.assertEqual((self.cache / self.filename).read_bytes(), WHEEL)
        self.assertEqual((self.out / self.filename).read_bytes(), WHEEL)
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), [self.filename])

    def test_warm_cache_is_verified_and_used_without_network(self):
        self.cache.mkdir()
        (self.cache / self.filename).write_bytes(WHEEL)
        opener = Opener()
        self.fetch(opener)
        self.assertEqual(opener.calls, [])
        self.assertEqual((self.out / self.filename).read_bytes(), WHEEL)

    def test_a_cache_copy_that_differs_from_the_lock_is_never_used(self):
        self.cache.mkdir()
        (self.cache / self.filename).write_bytes(b"tampered")
        opener = Opener()
        self.fetch(opener)
        self.assertEqual(len(opener.calls), 1)
        self.assertEqual((self.out / self.filename).read_bytes(), WHEEL)

    def test_transient_failures_are_retried_with_backoff(self):
        opener = Opener(
            failures=[
                urllib.error.HTTPError(URL, 503, "Service Unavailable", {}, None),
                ConnectionResetError("refused stream"),
                TimeoutError("read timed out"),
            ]
        )
        self.fetch(opener)
        self.assertEqual(len(opener.calls), 4)
        self.assertEqual(self.sleeps, list(lock_wheels.BACKOFF_SECONDS[:3]))
        self.assertEqual((self.out / self.filename).read_bytes(), WHEEL)

    def test_rate_limits_and_server_errors_are_retried(self):
        failures = [
            urllib.error.HTTPError(URL, code, "transient", {}, None)
            for code in (403, 429, 408, 502)
        ]
        opener = Opener(failures=failures)
        self.fetch(opener)
        self.assertEqual(len(opener.calls), 5)
        self.assertEqual((self.out / self.filename).read_bytes(), WHEEL)

    def test_a_permanent_http_answer_fails_at_once_without_retry(self):
        for code in (400, 404, 410):
            with self.subTest(code=code):
                self.sleeps.clear()
                error = urllib.error.HTTPError(URL, code, "permanent", {}, None)
                opener = Opener(failures=[error])
                with self.assertRaises(lock_wheels.WheelError):
                    self.fetch(opener)
                self.assertEqual(len(opener.calls), 1)
                self.assertEqual(self.sleeps, [])
                self.assertNothingKept()

    def test_retries_are_bounded_and_leave_nothing_behind(self):
        failures = [urllib.error.URLError("503")] * lock_wheels.ATTEMPTS
        opener = Opener(failures=failures)
        with self.assertRaises(lock_wheels.WheelError):
            self.fetch(opener)
        self.assertEqual(len(opener.calls), lock_wheels.ATTEMPTS)
        self.assertEqual(len(self.sleeps), lock_wheels.ATTEMPTS - 1)
        self.assertNothingKept()

    def test_the_wall_clock_deadline_stops_retries_early(self):
        late = lock_wheels.DEADLINE_SECONDS - 1.0
        clock = itertools.chain([0.0], itertools.repeat(late))
        opener = Opener(failures=[urllib.error.URLError("503")] * 2)
        with self.assertRaises(lock_wheels.WheelError):
            self.fetch(opener, clock=clock.__next__)
        self.assertEqual(len(opener.calls), 1)
        self.assertEqual(self.sleeps, [])
        self.assertNothingKept()

    def test_a_download_that_differs_from_the_lock_fails_without_retry(self):
        opener = Opener(body=b"some other asset")
        with self.assertRaises(lock_wheels.WheelError):
            self.fetch(opener)
        self.assertEqual(len(opener.calls), 1)
        self.assertNothingKept()

    def test_the_staging_directory_must_start_empty(self):
        self.out.mkdir()
        (self.out / "stray.whl").write_bytes(b"x")
        with self.assertRaises(lock_wheels.WheelError):
            self.fetch(Opener())


class ConsumerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = pathlib.Path(self.tmp.name)
        self.items = [lock_wheels.locked(lock_text(), NAME)]
        self.wheel = self.dir / self.items[0].filename

    def test_a_verified_artifact_becomes_a_hash_pinned_local_requirement(self):
        self.wheel.write_bytes(WHEEL)
        text = lock_wheels.requirements(self.items, self.dir)
        self.assertEqual(
            text, f"{NAME} @ {self.wheel.resolve().as_uri()} --hash=sha256:{DIGEST}\n"
        )
        self.assertNotIn("github.com", text)

    def test_a_missing_or_corrupt_artifact_never_falls_back_to_a_download(self):
        with self.assertRaises(lock_wheels.WheelError):
            lock_wheels.requirements(self.items, self.dir)
        self.wheel.write_bytes(b"corrupt")
        with self.assertRaises(lock_wheels.WheelError):
            lock_wheels.requirements(self.items, self.dir)

    def test_the_installed_version_must_be_the_locked_one(self):
        python = self.dir / "python"
        for printed, ok in (("3.8.0", True), ("3.7.1", False), ("", False)):
            with self.subTest(printed=printed):
                python.write_text(f"#!/bin/sh\necho '{printed}'\n")
                python.chmod(0o755)
                if ok:
                    lock_wheels.check(self.items, str(python))
                else:
                    with self.assertRaises(lock_wheels.WheelError):
                        lock_wheels.check(self.items, str(python))

    def test_the_command_line_reports_errors_as_a_failed_exit(self):
        lock = self.dir / "uv.lock"
        lock.write_text(lock_text(source=REGISTRY))
        self.assertEqual(lock_wheels.main(["key", str(lock)]), 1)


def jobs(workflow):
    body = workflow.split("\njobs:\n", 1)[1]
    names = re.findall(r"^  ([a-z0-9-]+):$", body, re.MULTILINE)
    parts = re.split(r"^  [a-z0-9-]+:$", body, flags=re.MULTILINE)[1:]
    return dict(zip(names, parts, strict=True))


def syncs(text):
    """Every `uv sync` command line, comments excluded."""
    return [
        line.strip()
        for line in text.splitlines()
        if re.search(r"\buv sync\b", line) and not line.strip().startswith("#")
    ]


def selects_lock_wheels(command):
    # en-core-web-sm is in the `pii` extra and the default `dev` group.
    return (
        "--no-dev" not in command
        or "--all-extras" in command
        or re.search(r"--extra[ =]pii\b", command) is not None
    )


class WorkflowWiringTest(unittest.TestCase):
    ACTION = "uses: ./.github/actions/backend-test-packages/lock-wheels\n"

    def setUp(self):
        self.workflow = (ROOT / ".github/workflows/backend-ci.yml").read_text()
        self.jobs = jobs(self.workflow)
        actions = ROOT / ".github/actions/backend-test-packages"
        self.environment = (actions / "environment/action.yml").read_text()
        self.setup = (actions / "environment/setup.sh").read_text()
        self.install = (actions / "lock-wheels/action.yml").read_text()

    def test_every_selecting_sync_skips_the_wheels_and_installs_the_run_copy(self):
        skip = " ".join(f"--no-install-package {name}" for name in lock_wheels.PACKAGES)
        selecting = []
        for name, job in self.jobs.items():
            for command in syncs(job):
                if not selects_lock_wheels(command):
                    continue
                selecting.append(name)
                with self.subTest(job=name, command=command):
                    self.assertIn(skip, command)
                    self.assertIn(self.ACTION, job)
                    self.assertLess(job.index(command), job.index(self.ACTION))
                    self.assertRegex(job, r"\n    needs: \[[^\]]*\bplan\b")
        self.assertCountEqual(selecting, ["saml-multiworker", "agentic-eval"])
        # The base-only jobs do not select the wheel; if one starts to, the
        # loop above makes it adopt the same wiring.
        for name in ("update-safety", "slim-smoke"):
            self.assertEqual(syncs(self.jobs[name]), ["run: uv sync --frozen --no-dev"])
        # The shard environment: sync skips, then the composite installs.
        (command,) = syncs(self.setup)
        self.assertIn(skip, command)
        self.assertLess(
            self.environment.index("setup.sh"), self.environment.index(self.ACTION)
        )

    def test_jobs_using_the_environment_action_get_the_artifact_from_plan(self):
        for name in ("test", "migrated-database"):
            self.assertRegex(self.jobs[name], r"\n    needs: \[[^\]]*\bplan\b")
        plan = self.jobs["plan"]
        upload = plan.index("name: backend-lock-wheels\n")
        # The opt-in cold trial runs the environment action inside plan itself.
        self.assertLess(upload, plan.index("      - id: cold\n"))
        self.assertLess(plan.index("lock_wheels.py fetch"), upload)
        self.assertLess(
            plan.index("lock_wheels.py key"), plan.index("lock_wheels.py fetch")
        )
        self.assertIn("key: ${{ steps.lock-wheels.outputs.key }}", plan)
        self.assertIn("name: backend-lock-wheels\n", self.install)

    def test_the_install_is_offline_hash_checked_and_never_resyncs(self):
        self.assertIn("--offline --no-deps --require-hashes", self.install)
        self.assertIn("lock_wheels.py requirements uv.lock", self.install)
        self.assertIn("lock_wheels.py check uv.lock", self.install)
        self.assertIn('echo "UV_NO_SYNC=true" >> "$GITHUB_ENV"', self.install)
        self.assertNotIn("lock_wheels.py fetch", self.install)
        self.assertNotIn("github.com", self.install.split("runs:", 1)[1])

    def test_artifact_and_cache_actions_are_pinned_by_sha(self):
        for action in ("cache", "upload-artifact", "download-artifact"):
            for ref in re.findall(rf"uses: actions/{action}@(\S+)", self.install):
                self.assertRegex(ref, r"^[0-9a-f]{40}$")
        tail = self.jobs["plan"].split("lock_wheels.py key", 1)[1]
        plan_lock_steps = tail.split("id: probe", 1)[0]
        for ref in re.findall(r"uses: actions/[a-z/-]+@(\S+)", plan_lock_steps):
            self.assertRegex(ref, r"^[0-9a-f]{40}$")


class ImageWiringTest(unittest.TestCase):
    """The backend image installs its extras against uv.lock's hashes, and the
    E2E builds hand it the run's verified wheel instead of downloading it."""

    def setUp(self):
        self.dockerfile = (ROOT / "futureagi/Dockerfile.oss").read_text()
        workflows = ROOT / ".github/workflows"
        self.e2e = jobs((workflows / "e2e-ci.yml").read_text())
        self.cache = (workflows / "e2e-cache.yml").read_text()

    def test_the_extras_install_requires_every_lock_hash(self):
        self.assertNotIn("--no-hashes", self.dockerfile)
        install = self.dockerfile.split("-o /tmp/extras-requirements.txt", 1)[1]
        self.assertIn(
            "uv pip install --system --require-hashes -r /tmp/extras-requirements.txt",
            install,
        )
        # A placed wheel replaces only its lock URL; the hash line stays.
        self.assertIn('@ file://$wheel #" /tmp/extras-requirements.txt', install)
        copy = self.dockerfile.index("COPY docker/lock-wheels/ /tmp/lock-wheels/\n")
        self.assertLess(copy, self.dockerfile.index("uv export --frozen --no-dev"))
        ignore = ROOT / "futureagi/docker/lock-wheels/.gitignore"
        self.assertIn("*.whl", ignore.read_text().splitlines())

    def test_e2e_fetches_once_and_every_shard_builds_with_that_copy(self):
        fetch = self.e2e["lock-wheels"]
        self.assertLess(
            fetch.index("lock_wheels.py key"), fetch.index("lock_wheels.py fetch")
        )
        self.assertLess(
            fetch.index("lock_wheels.py fetch"), fetch.index("name: e2e-lock-wheels\n")
        )
        self.assertIn("key: ${{ steps.lock-wheels.outputs.key }}", fetch)
        self.assertNotIn("\n    needs:", fetch)  # beside changes, not after it
        shard = self.e2e["e2e"]
        self.assertIn("\n    needs: [changes, lock-wheels]\n", shard)
        # Required only when the plan has a Distributed stack.
        self.assertIn("needs.lock-wheels.result == 'success'", shard)
        self.assertIn(
            "!contains(fromJSON(needs.changes.outputs.stacks), 'distributed')", shard
        )
        self.assertIn(
            "if: env.BUILD_BACKEND == 'true' && matrix.stack == 'distributed'", shard
        )
        self.assertNotIn("\n    permissions:", fetch)  # the workflow's read only
        place = shard.index("name: e2e-lock-wheels\n")
        self.assertIn("path: futureagi/docker/lock-wheels\n", shard[place:])
        self.assertLess(place, shard.index("name: Build backend image from PR code"))

    def test_the_cache_writer_places_the_same_wheel_before_building(self):
        steps = self.cache.split("\n    steps:\n", 1)[1]
        self.assertLess(
            steps.index("lock_wheels.py key"), steps.index("lock_wheels.py fetch")
        )
        self.assertIn("futureagi/docker/lock-wheels/", steps)
        self.assertLess(
            steps.index("lock_wheels.py fetch"),
            steps.index("uses: docker/build-push-action"),
        )
        # The standard build only: slim installs no extras.
        self.assertEqual(steps.count("if: matrix.tag == 'backend'"), 3)
        self.assertNotIn("matrix.context == 'futureagi'", steps)

    def test_e2e_lock_wheel_actions_are_pinned_by_sha(self):
        refs = re.findall(
            r"uses: actions/(?:cache|upload-artifact)@(\S+)", self.e2e["lock-wheels"]
        )
        refs += re.findall(r"uses: actions/cache@(\S+)", self.cache)
        # The shard's download step is the one that names this artifact.
        place = self.e2e["e2e"].split("name: e2e-lock-wheels\n", 1)[0]
        refs.append(re.findall(r"uses: actions/download-artifact@(\S+)", place)[-1])
        self.assertEqual(len(refs), 4)
        for ref in refs:
            self.assertRegex(ref, r"^[0-9a-f]{40}$")


if __name__ == "__main__":
    unittest.main()
