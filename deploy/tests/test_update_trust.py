"""bin/update trust: fetch the release manifest once, verify that private copy with cosign.

No network and no Docker: urlopen, cosign/docker lookup (which) and the
subprocess runner are replaced by small fakes that record what they were asked.
"""

import errno
import hashlib
import http.client
import importlib.util
import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import tracemalloc
import unittest
from email.message import Message
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deploy.update import trust  # noqa: E402
from deploy.update.manifest import (  # noqa: E402
    BUNDLE_ASSET, COMPONENT_REPOSITORIES, COMPONENT_TAG_SUFFIX, MANIFEST_ASSET, ManifestError,
)
from deploy.update.settings import DEFAULTS, Settings, SettingsError, load_settings  # noqa: E402
from deploy.update.trust import (  # noqa: E402
    MAX_DOWNLOAD_BYTES, FetchedManifest, Verification, VerificationError, Verifier, fetch_manifest,
    remove_workdir,
)

# The trust anchor is spelled out literally: a test derived from the settings
# module would follow a change to the anchor instead of catching it.
PINNED_IDENTITY_V123 = (
    "https://github.com/future-agi/future-agi/.github/workflows/release-update-manifest.yml@refs/tags/v1.2.3"
)
PINNED_ISSUER = "https://token.actions.githubusercontent.com"
PINNED_IMAGE = (
    "ghcr.io/sigstore/cosign/cosign:v3.1.3"
    "@sha256:9e5c2f2edc34351160407ca3416c61855bdf9403c3c5936e0f0be7fc261611b8"
)
DIGEST = "sha256:" + "0" * 64
COSIGN = "/opt/sigstore/bin/cosign"
BUNDLE_BYTES = b'{"mediaType": "application/vnd.dev.sigstore.bundle.v0.3+json", "test": "bundle"}'
LATEST_URL = "https://github.com/future-agi/future-agi/releases/latest/download/future-agi-update-manifest.json"


def raw_settings(**values):
    """Settings made without load_settings, so none of its cross-checks apply (as a caller could make them)."""

    return Settings(dict(DEFAULTS, **values), {})


def make_manifest(version="v1.2.3", *, channel=None, bundle=BUNDLE_ASSET, **signature):
    if channel is None:
        channel = "beta" if "-" in version else "stable"
    return {
        "schema_version": 1,
        "product": "future-agi",
        "channel": channel,
        "version": version,
        "source_sha": "a" * 40,
        "published_at": "2026-10-19T00:00:00Z",
        "minimum_version": "v0.1.0",
        "signature": {"bundle": bundle, **signature},
        "components": {
            name: {"image": f"docker.io/{repo}:{version}{COMPONENT_TAG_SUFFIX.get(name, '')}", "digest": DIGEST}
            for name, repo in COMPONENT_REPOSITORIES.items()
        },
        "helm": {"chart": "oci://ghcr.io/future-agi/charts/futureagi", "version": version[1:], "digest": DIGEST},
        "deployment": {"files": {"docker-compose.yml": "b" * 64}},
        "migration": {"requires_bootstrap": False, "rollback": "manual"},
    }


def encode(manifest):
    return json.dumps(manifest, indent=2).encode("utf-8")


def padded(manifest, size):
    """A valid manifest followed by JSON whitespace up to exactly `size` bytes."""

    raw = json.dumps(manifest).encode("utf-8")
    return raw + b" " * (size - len(raw))


def finishes(test, call, seconds=5):
    """Run `call` in a thread and return what it raised; fail if it is still running after `seconds`."""

    outcome = {}

    def run():
        try:
            call()
        except BaseException as exc:  # noqa: BLE001 - handed back to the test
            outcome["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        test.fail(f"still running after {seconds} seconds: it blocked")
    return outcome.get("error")


def release_fifo_writer(fifo, writer, seconds=30):
    """Drain `fifo` until the `writer` thread ends.

    A reader that refuses a FIFO without opening it leaves a writer waiting in
    open() for good; opening the read end lets it write and finish.
    """

    drain = os.open(str(fifo), os.O_RDONLY | os.O_NONBLOCK)
    try:
        deadline = time.monotonic() + seconds
        while writer.is_alive() and time.monotonic() < deadline:
            try:
                if os.read(drain, 1 << 16):
                    continue
            except BlockingIOError:
                pass
            time.sleep(0.01)
    finally:
        os.close(drain)


def release(tag, *assets, draft=False, prerelease=False, repo="future-agi/future-agi"):
    """One entry of GitHub's GET /repos/{owner}/{repo}/releases answer."""

    return {
        "tag_name": tag, "name": tag, "draft": draft, "prerelease": prerelease,
        "assets": [{"name": name, "browser_download_url": f"https://github.com/{repo}/releases/download/{tag}/{name}"}
                   for name in assets],
    }


class FakeResponse:
    def __init__(self, body, final_url, read_error=None):
        self.body = body
        self.final_url = final_url
        self.read_error = read_error
        self.reads = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True
        return False

    def geturl(self):
        return self.final_url

    def read(self, amount=None):
        self.reads.append(amount)
        if self.read_error is not None:
            raise self.read_error
        if amount is None or amount < 0:
            return self.body
        return self.body[:amount]


class FakeWeb:
    """Stands in for urllib's urlopen: serves registered URLs, 404 otherwise."""

    def __init__(self):
        self.routes = {}
        self.requests = []
        self.timeouts = []
        self.responses = []
        self.errors = []

    def http_error(self, url, code, message="Not Found"):
        error = HTTPError(url, code, message, Message(), None)
        self.errors.append(error)
        return error

    def close(self):
        for error in self.errors:
            try:
                error.close()  # Python 3.12+ gives every HTTPError a body file
            except Exception:  # noqa: BLE001 - Python 3.9 has none to close
                pass

    def serve(self, url, body=b"", *, final_url=None, error=None, read_error=None):
        self.routes[url] = (body, final_url or url, error, read_error)

    def __call__(self, request, timeout=None):
        url = request.full_url
        self.requests.append(request)
        self.timeouts.append(timeout)
        if url not in self.routes:
            raise self.http_error(url, 404)
        body, final_url, error, read_error = self.routes[url]
        if error is not None:
            raise error
        response = FakeResponse(body, final_url, read_error)
        self.responses.append(response)
        return response

    @property
    def urls(self):
        return [request.full_url for request in self.requests]


class FakeRunner:
    def __init__(self, returncode=0, stdout="", stderr="", error=None, on_call=None):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.error = error
        self.on_call = on_call
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), dict(kwargs)))
        if self.on_call is not None:
            self.on_call(list(command))
        if self.error is not None:
            raise self.error
        return subprocess.CompletedProcess(list(command), self.returncode, self.stdout, self.stderr)

    @property
    def argv(self):
        if len(self.calls) != 1:
            raise AssertionError(f"expected exactly one cosign run, got {len(self.calls)}")
        return self.calls[0][0]


class FakeWhich:
    def __init__(self, found=None):
        self.found = dict(found or {})
        self.calls = []

    def __call__(self, name):
        self.calls.append(name)
        return self.found.get(name)


def cosign_version_json(version):
    """What `cosign version --json` prints (captured from the pinned v3.1.3 image)."""

    return json.dumps({"gitVersion": version, "gitCommit": "11926fa5bbbbde47e88fc006b625a17769b743b2",
                       "gitTreeState": "clean", "buildDate": "2026-08-05T23:43:27Z", "goVersion": "go1.26.4",
                       "compiler": "gc", "platform": "linux/arm64"}, indent=2) + "\n"


def cosign_version_text(version):
    """What plain `cosign version` prints."""

    return (" cosign: A tool for Container Signing, Verification and Storage in an OCI registry\n\n"
            f"GitVersion:    {version}\nGitCommit:     11926fa5bbbbde47e88fc006b625a17769b743b2\n"
            "GitTreeState:  clean\nBuildDate:     2026-08-05T23:43:27Z\n")


class FakeProbe:
    """Answers `cosign version` like a cosign of `version`: --json, or only the text form when json=False."""

    def __init__(self, version="v3.1.3", *, json_form=True, returncode=0, error=None, stdout=None):
        self.version = version
        self.json_form = json_form
        self.returncode = returncode
        self.error = error
        self.stdout = stdout
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), dict(kwargs)))
        if self.error is not None:
            raise self.error
        if self.stdout is not None:
            return subprocess.CompletedProcess(list(command), self.returncode, self.stdout, "")
        if command[1:] == ["version", "--json"]:
            if not self.json_form:
                return subprocess.CompletedProcess(list(command), 1, "", "Error: unknown flag: --json\n")
            return subprocess.CompletedProcess(list(command), self.returncode, cosign_version_json(self.version), "")
        return subprocess.CompletedProcess(list(command), self.returncode, cosign_version_text(self.version), "")


class TrustTestCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name).resolve()
        self.checkout = self.tmp / "checkout"
        self.checkout.mkdir()
        self.caller = self.tmp / "caller"
        self.caller.mkdir()
        self.web = FakeWeb()
        self.addCleanup(self.web.close)
        patcher = mock.patch.object(trust, "urlopen", self.web)
        patcher.start()
        self.addCleanup(patcher.stop)
        self._workdirs = 0

    # ------------------------------------------------------------ helpers --
    def workdir(self):
        self._workdirs += 1
        return self.tmp / "state" / f"verify-{self._workdirs}"

    def write_release(self, directory, manifest=None, bundle=BUNDLE_BYTES, name="future-agi-update-manifest.json"):
        directory.mkdir(parents=True, exist_ok=True)
        manifest = make_manifest() if manifest is None else manifest
        path = directory / name
        path.write_bytes(manifest if isinstance(manifest, bytes) else encode(manifest))
        if bundle is not None:
            bundle_name = BUNDLE_ASSET
            if not isinstance(manifest, bytes):
                bundle_name = manifest["signature"]["bundle"]
            (directory / bundle_name).write_bytes(bundle)
        return path

    def local_fetch(self, manifest=None, bundle=BUNDLE_BYTES):
        path = self.write_release(self.tmp / "release", manifest, bundle)
        return fetch_manifest(str(path), self.workdir(), cwd=self.caller)

    def settings(self, **values):
        return load_settings(self.checkout, overrides=values, environ={})

    def verifier(self, *, which=None, runner=None, docker=None, probe=None, environ=None, settings=None, **values):
        self.runner = runner or FakeRunner()
        self.which = which or FakeWhich({"cosign": COSIGN, "docker": "/usr/bin/docker"})
        self.probe = probe or FakeProbe()  # a cosign on PATH is v3.1.3 unless a test says otherwise
        environ = {} if environ is None else dict(environ)
        # Docker's config.json is looked up under HOME: never the real one.
        environ.setdefault("HOME", str(self.tmp / "home"))
        return Verifier(settings or self.settings(**values), docker=docker, runner=self.runner, which=self.which,
                        probe=self.probe, environ=environ)

    def docker_only(self):
        return FakeWhich({"docker": "/usr/bin/docker"})

    def umask(self, value):
        previous = os.umask(value)
        self.addCleanup(os.umask, previous)

    def mode(self, path):
        return stat.S_IMODE(path.stat().st_mode)


# ================================================================ fetching ==

class FetchLocalManifestTests(TrustTestCase):
    def test_max_download_bytes_is_four_mebibytes(self):
        self.assertEqual(MAX_DOWNLOAD_BYTES, 4 * 1024 * 1024)

    def test_relative_source_is_resolved_against_the_callers_cwd(self):
        manifest = make_manifest()
        self.write_release(self.caller / "rel", manifest)
        workdir = self.workdir()
        self.assertNotEqual(Path(os.getcwd()).resolve(), self.caller)

        fetched = fetch_manifest("rel/future-agi-update-manifest.json", workdir, cwd=self.caller)

        self.assertEqual(fetched.source, "rel/future-agi-update-manifest.json")
        self.assertEqual(fetched.directory, workdir)
        self.assertEqual(fetched.raw, encode(manifest))
        self.assertEqual(fetched.data, manifest)
        self.assertEqual(fetched.manifest_path, workdir / "manifest.json")
        self.assertEqual(fetched.manifest_path.read_bytes(), encode(manifest))
        self.assertEqual(fetched.bundle_source, str(self.caller / "rel" / BUNDLE_ASSET))
        self.assertEqual(fetched.bundle_path, workdir / "bundle.json")
        self.assertEqual(fetched.bundle_path.read_bytes(), BUNDLE_BYTES)
        self.assertIsNone(fetched.bundle_error)
        self.assertEqual(self.web.requests, [])

    def test_relative_source_with_parent_directory_components(self):
        self.write_release(self.tmp / "elsewhere", make_manifest())
        fetched = fetch_manifest("../elsewhere/future-agi-update-manifest.json", self.workdir(), cwd=self.caller)
        self.assertEqual(fetched.data["version"], "v1.2.3")
        self.assertEqual(fetched.bundle_path.read_bytes(), BUNDLE_BYTES)

    def test_absolute_source_ignores_cwd(self):
        path = self.write_release(self.tmp / "release", make_manifest("v2.0.0"))
        fetched = fetch_manifest(str(path), self.workdir(), cwd=self.tmp / "does-not-exist")
        self.assertEqual(fetched.data["version"], "v2.0.0")
        self.assertEqual(fetched.bundle_source, str(self.tmp / "release" / BUNDLE_ASSET))
        self.assertEqual(fetched.bundle_path.read_bytes(), BUNDLE_BYTES)

    def test_relative_source_is_not_resolved_against_the_process_cwd(self):
        with self.assertRaises(ManifestError) as caught:
            fetch_manifest("future-agi-update-manifest.json", self.workdir(), cwd=self.caller)
        self.assertIn(f"cannot read manifest {self.caller / 'future-agi-update-manifest.json'}", str(caught.exception))

    def test_missing_manifest(self):
        missing = self.tmp / "nope.json"
        with self.assertRaisesRegex(ManifestError, "^cannot read manifest " + str(missing) + ": "):
            fetch_manifest(str(missing), self.workdir(), cwd=self.caller)

    def test_directory_is_not_a_manifest(self):
        with self.assertRaisesRegex(ManifestError, "^cannot read manifest "):
            fetch_manifest(str(self.tmp), self.workdir(), cwd=self.caller)

    def test_manifest_one_byte_over_the_cap_is_refused_before_anything_is_written(self):
        path = self.tmp / "big.json"
        path.write_bytes(padded(make_manifest(), MAX_DOWNLOAD_BYTES + 1))
        workdir = self.workdir()
        with self.assertRaises(ManifestError) as caught:
            fetch_manifest(str(path), workdir, cwd=self.caller)
        self.assertEqual(str(caught.exception), f"manifest {path} is larger than {MAX_DOWNLOAD_BYTES} bytes")
        self.assertFalse((workdir / "manifest.json").exists())

    def test_manifest_exactly_at_the_cap_is_accepted(self):
        path = self.tmp / "release" / "future-agi-update-manifest.json"
        self.write_release(self.tmp / "release", make_manifest())
        path.write_bytes(padded(make_manifest(), MAX_DOWNLOAD_BYTES))
        fetched = fetch_manifest(str(path), self.workdir(), cwd=self.caller)
        self.assertEqual(len(fetched.raw), MAX_DOWNLOAD_BYTES)
        self.assertEqual(fetched.data, make_manifest())

    @unittest.skipUnless(hasattr(os, "mkfifo"), "needs a FIFO")
    def test_local_manifest_read_stops_at_the_size_cap(self):
        # A FIFO (or /dev/zero, or a mistyped multi-GB path) must not be read
        # to the end before the cap applies: memory use has to stay bounded.
        # A FIFO is refused outright as not a regular file (a huge regular
        # file is covered by test_huge_local_manifest_is_never_read_whole).
        fifo = self.tmp / "endless.json"
        os.mkfifo(str(fifo))
        hold = threading.Event()
        outcome = {}

        def writer():
            try:
                with open(str(fifo), "wb") as pipe:
                    pipe.write(b" " * (MAX_DOWNLOAD_BYTES + 2))
                    pipe.flush()
                    hold.wait(30)
            except OSError:  # a bounded reader closed its end first
                pass

        def reader():
            try:
                fetch_manifest(str(fifo), self.workdir(), cwd=self.caller)
            except BaseException as exc:  # noqa: BLE001 - reported below
                outcome["error"] = exc
            else:
                outcome["error"] = None

        threads = [threading.Thread(target=writer, daemon=True), threading.Thread(target=reader, daemon=True)]
        for thread in threads:
            thread.start()
        threads[1].join(3)
        kept_reading = threads[1].is_alive()
        hold.set()
        release_fifo_writer(fifo, threads[0])
        for thread in threads:
            thread.join(30)
        self.assertFalse(kept_reading, "fetch_manifest kept reading a local manifest past "
                                       f"{MAX_DOWNLOAD_BYTES} bytes, waiting for end of file")
        self.assertIsInstance(outcome.get("error"), ManifestError)
        self.assertEqual(str(outcome["error"]), f"cannot read manifest {fifo}: it is a named pipe, not a regular file")

    def test_huge_local_manifest_is_never_read_whole(self):
        # A sparse 64 MiB file costs no disk; reading it whole would allocate
        # 64 MiB before the size check, a capped read about 4 MiB.
        path = self.tmp / "huge.json"
        with open(str(path), "wb") as handle:
            handle.truncate(16 * MAX_DOWNLOAD_BYTES)
        tracemalloc.start()
        try:
            with self.assertRaises(ManifestError) as caught:
                fetch_manifest(str(path), self.workdir(), cwd=self.caller)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertEqual(str(caught.exception), f"manifest {path} is larger than {MAX_DOWNLOAD_BYTES} bytes")
        self.assertLess(peak, 3 * MAX_DOWNLOAD_BYTES)

    def test_directory_is_refused_as_not_a_regular_file(self):
        with self.assertRaises(ManifestError) as caught:
            fetch_manifest(str(self.tmp), self.workdir(), cwd=self.caller)
        self.assertEqual(str(caught.exception), f"cannot read manifest {self.tmp}: it is a directory, not a regular file")

    @unittest.skipUnless(os.path.exists("/dev/zero"), "needs /dev/zero")
    def test_device_is_refused_without_reading_it(self):
        workdir = self.workdir()
        error = finishes(self, lambda: fetch_manifest("/dev/zero", workdir, cwd=self.caller))
        self.assertIsInstance(error, ManifestError)
        self.assertEqual(str(error), "cannot read manifest /dev/zero: it is a device, not a regular file")
        self.assertFalse((workdir / "manifest.json").exists())

    @unittest.skipUnless(hasattr(os, "mkfifo"), "needs a FIFO")
    def test_fifo_without_a_writer_is_refused_without_blocking(self):
        fifo = self.tmp / "pipe.json"
        os.mkfifo(str(fifo))
        error = finishes(self, lambda: fetch_manifest(str(fifo), self.workdir(), cwd=self.caller))
        self.assertIsInstance(error, ManifestError)
        self.assertEqual(str(error), f"cannot read manifest {fifo}: it is a named pipe, not a regular file")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "needs a FIFO")
    def test_fifo_swapped_in_after_the_check_is_refused_without_blocking(self):
        # The path passes the first check as a regular file, then turns out to
        # be a FIFO when opened: the opened descriptor is checked too, and the
        # open itself must not wait for a writer.
        fifo = self.tmp / "pipe.json"
        os.mkfifo(str(fifo))
        regular = self.write_release(self.tmp / "release", make_manifest())
        real_stat = os.stat

        def stat_before_the_swap(target, *args, **kwargs):
            if Path(target) == fifo:
                return real_stat(str(regular))
            return real_stat(target, *args, **kwargs)

        with mock.patch.object(os, "stat", stat_before_the_swap):
            error = finishes(self, lambda: fetch_manifest(str(fifo), self.workdir(), cwd=self.caller))
        self.assertIsInstance(error, ManifestError)
        self.assertEqual(str(error), f"cannot read manifest {fifo}: it is a named pipe, not a regular file")

    def test_symlink_to_a_regular_manifest_is_followed(self):
        path = self.write_release(self.tmp / "release", make_manifest())
        link = self.caller / "current.json"
        link.symlink_to(path)
        fetched = fetch_manifest("current.json", self.workdir(), cwd=self.caller)
        self.assertEqual(fetched.data, make_manifest())

    def test_missing_manifest_message_names_the_reason_once(self):
        missing = self.tmp / "nope.json"
        with self.assertRaises(ManifestError) as caught:
            fetch_manifest(str(missing), self.workdir(), cwd=self.caller)
        self.assertEqual(str(caught.exception), f"cannot read manifest {missing}: No such file or directory")

    def test_invalid_json(self):
        for name, body in (("garbage", b"this is not json"), ("empty", b""), ("truncated", encode(make_manifest())[:-5]),
                           ("not utf-8", b"\x80\x81\x82"), ("nan", b"NaN-ish {")):
            with self.subTest(name):
                path = self.tmp / f"{name.replace(' ', '-')}.json"
                path.write_bytes(body)
                with self.assertRaisesRegex(ManifestError, "^manifest is not valid JSON: "):
                    fetch_manifest(str(path), self.workdir(), cwd=self.caller)

    def test_json_that_is_not_a_manifest(self):
        wrong_product = make_manifest()
        wrong_product["product"] = "something-else"
        extra = make_manifest()
        extra["components_override"] = {}
        no_signature = make_manifest()
        del no_signature["signature"]
        cases = (
            ("array", b"[]", "manifest must be an object"),
            ("string", b'"v1.2.3"', "manifest must be an object"),
            ("wrong product", encode(wrong_product), "manifest is for 'something-else'"),
            ("unknown field", encode(extra), "unknown field"),
            ("no signature", encode(no_signature), "manifest is missing signature"),
        )
        for name, body, message in cases:
            with self.subTest(name):
                path = self.tmp / "invalid.json"
                path.write_bytes(body)
                with self.assertRaisesRegex(ManifestError, message):
                    fetch_manifest(str(path), self.workdir(), cwd=self.caller)

    def test_bundle_name_must_be_a_file_name_not_a_path(self):
        outside = self.tmp / "outside.json"
        outside.write_bytes(b"attacker-chosen")
        for bundle in ("../outside.json", "../../etc/passwd", "sub/bundle.json", str(outside), ".hidden",
                       "bundle json", ""):
            with self.subTest(bundle=bundle):
                path = self.tmp / "release" / "m.json"
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(encode(make_manifest(bundle=bundle)))
                with self.assertRaises(ManifestError):
                    fetch_manifest(str(path), self.workdir(), cwd=self.caller)


class FetchLocalBundleTests(TrustTestCase):
    def test_sibling_bundle_uses_the_name_the_manifest_gives(self):
        release = self.tmp / "release"
        self.write_release(release, make_manifest(bundle="custom.sigstore.json"), bundle=b"custom")
        (release / BUNDLE_ASSET).write_bytes(b"default-name")
        fetched = fetch_manifest(str(release / "future-agi-update-manifest.json"), self.workdir(), cwd=self.caller)
        self.assertEqual(fetched.bundle_source, str(release / "custom.sigstore.json"))
        self.assertEqual(fetched.bundle_path.read_bytes(), b"custom")

    def test_sibling_bundle_next_to_a_manifest_with_another_file_name(self):
        release = self.tmp / "release"
        self.write_release(release, make_manifest(), name="v1.2.3.json")
        fetched = fetch_manifest(str(release / "v1.2.3.json"), self.workdir(), cwd=self.caller)
        self.assertEqual(fetched.bundle_source, str(release / BUNDLE_ASSET))
        self.assertEqual(fetched.bundle_path.read_bytes(), BUNDLE_BYTES)

    def test_explicit_absolute_bundle_wins_over_the_sibling(self):
        path = self.write_release(self.tmp / "release", make_manifest())
        explicit = self.tmp / "elsewhere" / "my.bundle"
        explicit.parent.mkdir()
        explicit.write_bytes(b"explicit")
        fetched = fetch_manifest(str(path), self.workdir(), cwd=self.caller, bundle=str(explicit))
        self.assertEqual(fetched.bundle_source, str(explicit))
        self.assertEqual(fetched.bundle_path.read_bytes(), b"explicit")
        self.assertIsNone(fetched.bundle_error)

    def test_explicit_relative_bundle_is_resolved_against_cwd(self):
        path = self.write_release(self.tmp / "release", make_manifest())
        (self.caller / "b.json").write_bytes(b"relative")
        fetched = fetch_manifest(str(path), self.workdir(), cwd=self.caller, bundle="b.json")
        self.assertEqual(fetched.bundle_source, "b.json")
        self.assertEqual(fetched.bundle_path.read_bytes(), b"relative")

    def test_empty_bundle_argument_falls_back_to_the_sibling(self):
        path = self.write_release(self.tmp / "release", make_manifest())
        fetched = fetch_manifest(str(path), self.workdir(), cwd=self.caller, bundle="")
        self.assertEqual(fetched.bundle_source, str(self.tmp / "release" / BUNDLE_ASSET))
        self.assertEqual(fetched.bundle_path.read_bytes(), BUNDLE_BYTES)

    def test_explicit_https_bundle_for_a_local_manifest(self):
        path = self.write_release(self.tmp / "release", make_manifest())
        url = "https://downloads.example.com/v1.2.3/bundle.json"
        self.web.serve(url, b"remote-bundle")
        fetched = fetch_manifest(str(path), self.workdir(), cwd=self.caller, bundle=url)
        self.assertEqual(fetched.bundle_source, url)
        self.assertEqual(fetched.bundle_path.read_bytes(), b"remote-bundle")
        self.assertEqual(self.web.urls, [url])

    def test_explicit_http_bundle_is_refused_without_a_request_but_fetch_succeeds(self):
        path = self.write_release(self.tmp / "release", make_manifest())
        url = "http://downloads.example.com/bundle.json"
        fetched = fetch_manifest(str(path), self.workdir(), cwd=self.caller, bundle=url)
        self.assertIsNone(fetched.bundle_path)
        self.assertEqual(fetched.data, make_manifest())
        self.assertEqual(
            fetched.bundle_error,
            f"signature bundle not available ({url}): refusing to download over a non-https URL: {url}")
        self.assertEqual(self.web.requests, [])

    def test_missing_sibling_bundle_is_reported_and_fetch_still_succeeds(self):
        path = self.write_release(self.tmp / "release", make_manifest(), bundle=None)
        workdir = self.workdir()
        fetched = fetch_manifest(str(path), workdir, cwd=self.caller)
        sibling = self.tmp / "release" / BUNDLE_ASSET
        self.assertEqual(fetched.data, make_manifest())
        self.assertEqual(fetched.manifest_path.read_bytes(), encode(make_manifest()))
        self.assertIsNone(fetched.bundle_path)
        self.assertEqual(fetched.bundle_source, str(sibling))
        self.assertTrue(fetched.bundle_error.startswith(f"signature bundle not available ({sibling}): "),
                        fetched.bundle_error)
        self.assertFalse((workdir / "bundle.json").exists())

    def test_missing_explicit_bundle_is_reported(self):
        path = self.write_release(self.tmp / "release", make_manifest())
        fetched = fetch_manifest(str(path), self.workdir(), cwd=self.caller, bundle=str(self.tmp / "gone.json"))
        self.assertIsNone(fetched.bundle_path)
        self.assertIn(f"signature bundle not available ({self.tmp / 'gone.json'})", fetched.bundle_error)

    def test_bundle_that_is_a_directory_is_reported(self):
        path = self.write_release(self.tmp / "release", make_manifest(), bundle=None)
        (self.tmp / "release" / BUNDLE_ASSET).mkdir()
        fetched = fetch_manifest(str(path), self.workdir(), cwd=self.caller)
        self.assertIsNone(fetched.bundle_path)
        self.assertIn("signature bundle not available", fetched.bundle_error)

    def test_local_bundle_over_the_size_cap_is_not_accepted(self):
        # The https bundle download is capped at MAX_DOWNLOAD_BYTES; a local
        # bundle must not be read without limit either.
        path = self.write_release(self.tmp / "release", make_manifest(), bundle=b" " * (MAX_DOWNLOAD_BYTES + 1))
        fetched = fetch_manifest(str(path), self.workdir(), cwd=self.caller)
        self.assertIsNone(fetched.bundle_path)
        self.assertIsNotNone(fetched.bundle_error)
        self.assertIn(f"larger than {MAX_DOWNLOAD_BYTES} bytes", fetched.bundle_error)

    def test_local_bundle_over_the_size_cap_message(self):
        path = self.write_release(self.tmp / "release", make_manifest(), bundle=b" " * (MAX_DOWNLOAD_BYTES + 1))
        workdir = self.workdir()
        fetched = fetch_manifest(str(path), workdir, cwd=self.caller)
        sibling = self.tmp / "release" / BUNDLE_ASSET
        self.assertEqual(fetched.bundle_error, f"signature bundle not available ({sibling}): "
                                               f"{sibling} is larger than {MAX_DOWNLOAD_BYTES} bytes")
        self.assertFalse((workdir / "bundle.json").exists())

    def test_local_bundle_exactly_at_the_size_cap_is_accepted(self):
        bundle = b" " * MAX_DOWNLOAD_BYTES
        path = self.write_release(self.tmp / "release", make_manifest(), bundle=bundle)
        fetched = fetch_manifest(str(path), self.workdir(), cwd=self.caller)
        self.assertIsNone(fetched.bundle_error)
        self.assertEqual(fetched.bundle_path.read_bytes(), bundle)

    def test_bundle_that_is_a_directory_message(self):
        path = self.write_release(self.tmp / "release", make_manifest(), bundle=None)
        sibling = self.tmp / "release" / BUNDLE_ASSET
        sibling.mkdir()
        fetched = fetch_manifest(str(path), self.workdir(), cwd=self.caller)
        self.assertEqual(fetched.bundle_error, f"signature bundle not available ({sibling}): "
                                               f"cannot read {sibling}: it is a directory, not a regular file")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "needs a FIFO")
    def test_bundle_that_is_a_fifo_is_refused_without_blocking(self):
        path = self.write_release(self.tmp / "release", make_manifest(), bundle=None)
        sibling = self.tmp / "release" / BUNDLE_ASSET
        os.mkfifo(str(sibling))
        workdir = self.workdir()
        result = {}
        error = finishes(self, lambda: result.setdefault("fetched", fetch_manifest(str(path), workdir, cwd=self.caller)))
        self.assertIsNone(error)
        fetched = result["fetched"]
        self.assertIsNone(fetched.bundle_path)
        self.assertEqual(fetched.bundle_error, f"signature bundle not available ({sibling}): "
                                               f"cannot read {sibling}: it is a named pipe, not a regular file")
        self.assertFalse((workdir / "bundle.json").exists())

    @unittest.skipUnless(os.path.exists("/dev/zero"), "needs /dev/zero")
    def test_bundle_that_is_a_device_is_refused_without_reading_it(self):
        path = self.write_release(self.tmp / "release", make_manifest())
        result = {}
        error = finishes(self, lambda: result.setdefault(
            "fetched", fetch_manifest(str(path), self.workdir(), cwd=self.caller, bundle="/dev/zero")))
        self.assertIsNone(error)
        self.assertIsNone(result["fetched"].bundle_path)
        self.assertEqual(result["fetched"].bundle_error, "signature bundle not available (/dev/zero): "
                                                         "cannot read /dev/zero: it is a device, not a regular file")

    def test_missing_local_bundle_message(self):
        path = self.write_release(self.tmp / "release", make_manifest(), bundle=None)
        sibling = self.tmp / "release" / BUNDLE_ASSET
        fetched = fetch_manifest(str(path), self.workdir(), cwd=self.caller)
        self.assertEqual(fetched.bundle_error, f"signature bundle not available ({sibling}): "
                                               f"cannot read {sibling}: No such file or directory")

    def test_failed_bundle_copy_leaves_no_bundle_path(self):
        # Disk full while copying the bundle: the error is recorded, and the
        # half-written copy must not be offered to cosign as the bundle.
        path = self.write_release(self.tmp / "release", make_manifest())
        original = trust._write_private

        def disk_full(target, data):
            if target.name != "bundle.json":
                return original(target, data)
            fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.write(fd, data[:3])
            os.close(fd)
            raise OSError(errno.ENOSPC, "No space left on device")

        with mock.patch.object(trust, "_write_private", disk_full):
            fetched = fetch_manifest(str(path), self.workdir(), cwd=self.caller)
        self.assertIn("No space left on device", fetched.bundle_error or "")
        self.assertIsNone(fetched.bundle_path)

        verification = self.verifier().verify(fetched)
        self.assertFalse(verification.verified)
        self.assertEqual(self.runner.calls, [])
        self.assertIn("No space left on device", verification.error)


class FetchPrivateCopyTests(TrustTestCase):
    def test_copies_are_0644_inside_a_0755_directory_even_with_umask_0(self):
        self.umask(0)
        path = self.write_release(self.tmp / "release", make_manifest())
        workdir = self.tmp / "state" / "nested" / "verify-1"
        fetched = fetch_manifest(str(path), workdir, cwd=self.caller)
        self.assertTrue(workdir.is_dir())
        self.assertEqual(self.mode(workdir), 0o755)
        self.assertEqual(self.mode(fetched.manifest_path), 0o644)
        self.assertEqual(self.mode(fetched.bundle_path), 0o644)
        self.assertEqual(sorted(p.name for p in workdir.iterdir()), ["bundle.json", "manifest.json"])

    def test_https_copies_are_private_too(self):
        self.umask(0)
        source = "https://downloads.example.com/v1/future-agi-update-manifest.json"
        self.web.serve(source, encode(make_manifest()))
        self.web.serve(f"https://downloads.example.com/v1/{BUNDLE_ASSET}", BUNDLE_BYTES)
        workdir = self.workdir()
        fetched = fetch_manifest(source, workdir, cwd=self.caller)
        self.assertEqual(self.mode(workdir), 0o755)
        self.assertEqual(self.mode(fetched.manifest_path), 0o644)
        self.assertEqual(self.mode(fetched.bundle_path), 0o644)

    def test_planted_manifest_symlink_in_the_workdir_is_not_followed(self):
        path = self.write_release(self.tmp / "release", make_manifest())
        workdir = self.workdir()
        workdir.mkdir(parents=True)
        victim = self.tmp / "victim.txt"
        victim.write_bytes(b"do not touch")
        (workdir / "manifest.json").symlink_to(victim)
        with self.assertRaises((OSError, ManifestError)):
            fetch_manifest(str(path), workdir, cwd=self.caller)
        self.assertEqual(victim.read_bytes(), b"do not touch")

    def test_planted_bundle_symlink_in_the_workdir_is_not_followed(self):
        path = self.write_release(self.tmp / "release", make_manifest())
        workdir = self.workdir()
        workdir.mkdir(parents=True)
        victim = self.tmp / "victim.txt"
        victim.write_bytes(b"do not touch")
        (workdir / "bundle.json").symlink_to(victim)
        fetched = fetch_manifest(str(path), workdir, cwd=self.caller)
        self.assertEqual(victim.read_bytes(), b"do not touch")
        self.assertIn("signature bundle not available", fetched.bundle_error or "")


class FetchUrlTests(TrustTestCase):
    SOURCE = "https://downloads.example.com/releases/v1.2.3/future-agi-update-manifest.json"
    SIBLING = f"https://downloads.example.com/releases/v1.2.3/{BUNDLE_ASSET}"

    def test_https_manifest_and_sibling_bundle(self):
        self.web.serve(self.SOURCE, encode(make_manifest()))
        self.web.serve(self.SIBLING, BUNDLE_BYTES)
        workdir = self.workdir()
        fetched = fetch_manifest(self.SOURCE, workdir, cwd=self.caller)
        self.assertEqual(fetched.source, self.SOURCE)
        self.assertEqual(fetched.raw, encode(make_manifest()))
        self.assertEqual(fetched.data, make_manifest())
        self.assertEqual(fetched.manifest_path.read_bytes(), fetched.raw)
        self.assertEqual(fetched.bundle_source, self.SIBLING)
        self.assertEqual(fetched.bundle_path.read_bytes(), BUNDLE_BYTES)
        self.assertIsNone(fetched.bundle_error)
        self.assertEqual(self.web.urls, [self.SOURCE, self.SIBLING])

    def test_request_headers_timeout_and_bounded_read(self):
        self.web.serve(self.SOURCE, encode(make_manifest()))
        self.web.serve(self.SIBLING, BUNDLE_BYTES)
        fetch_manifest(self.SOURCE, self.workdir(), cwd=self.caller)
        for request in self.web.requests:
            self.assertEqual(request.get_header("Accept"), "application/json")
            self.assertEqual(request.get_header("User-agent"), "future-agi-bin-update")
            self.assertIsNone(request.data)
        self.assertEqual(self.web.timeouts, [30.0, 30.0])
        for response in self.web.responses:
            self.assertEqual(response.reads, [MAX_DOWNLOAD_BYTES + 1])
            self.assertTrue(response.closed)

    def test_manifest_is_requested_exactly_once(self):
        self.web.serve(self.SOURCE, encode(make_manifest()))
        self.web.serve(self.SIBLING, BUNDLE_BYTES)
        fetch_manifest(self.SOURCE, self.workdir(), cwd=self.caller)
        self.assertEqual(self.web.urls.count(self.SOURCE), 1)

    def test_plain_http_manifest_url_is_refused_without_a_request(self):
        with self.assertRaises(ManifestError) as caught:
            fetch_manifest("http://downloads.example.com/m.json", self.workdir(), cwd=self.caller)
        self.assertEqual(str(caught.exception), "manifest URL must use https, not http://")
        self.assertEqual(self.web.requests, [])

    def test_other_schemes_are_refused_without_a_request(self):
        cases = {
            "ftp://downloads.example.com/m.json": "manifest URL must use https, not ftp://",
            "file:///etc/passwd": "manifest URL must use https, not file://",
            "ws://downloads.example.com/m.json": "manifest URL must use https, not ws://",
            "data://text/plain,{}": "manifest URL must use https, not data://",
            "s3://bucket/m.json": "manifest URL must use https, not s3://",
        }
        for url, message in cases.items():
            with self.subTest(url=url):
                with self.assertRaises(ManifestError) as caught:
                    fetch_manifest(url, self.workdir(), cwd=self.caller)
                self.assertEqual(str(caught.exception), message)
        self.assertEqual(self.web.requests, [])

    def test_uppercase_scheme_is_refused_without_a_request(self):
        with self.assertRaisesRegex(ManifestError, "non-https"):
            fetch_manifest("HTTPS://downloads.example.com/m.json", self.workdir(), cwd=self.caller)
        self.assertEqual(self.web.requests, [])

    def test_redirect_to_plain_http_is_refused_before_reading(self):
        self.web.serve(self.SOURCE, encode(make_manifest()), final_url="http://mirror.example.com/m.json")
        with self.assertRaises(ManifestError) as caught:
            fetch_manifest(self.SOURCE, self.workdir(), cwd=self.caller)
        self.assertEqual(str(caught.exception), f"{self.SOURCE} redirected to a non-https URL")
        self.assertEqual(self.web.responses[0].reads, [])

    def test_redirect_to_another_https_host_is_followed(self):
        # GitHub serves release assets from a redirect to its asset CDN.
        self.web.serve(self.SOURCE, encode(make_manifest()),
                       final_url="https://release-assets.githubusercontent.com/asset?sig=x")
        self.web.serve(self.SIBLING, BUNDLE_BYTES)
        fetched = fetch_manifest(self.SOURCE, self.workdir(), cwd=self.caller)
        self.assertEqual(fetched.data, make_manifest())
        self.assertEqual(fetched.bundle_source, self.SIBLING)

    def test_download_one_byte_over_the_cap_is_refused(self):
        self.web.serve(self.SOURCE, padded(make_manifest(), MAX_DOWNLOAD_BYTES + 1))
        workdir = self.workdir()
        with self.assertRaises(ManifestError) as caught:
            fetch_manifest(self.SOURCE, workdir, cwd=self.caller)
        self.assertEqual(str(caught.exception), f"{self.SOURCE} is larger than {MAX_DOWNLOAD_BYTES} bytes")
        self.assertFalse((workdir / "manifest.json").exists())

    def test_download_exactly_at_the_cap_is_accepted(self):
        self.web.serve(self.SOURCE, padded(make_manifest(), MAX_DOWNLOAD_BYTES))
        self.web.serve(self.SIBLING, BUNDLE_BYTES)
        fetched = fetch_manifest(self.SOURCE, self.workdir(), cwd=self.caller)
        self.assertEqual(len(fetched.raw), MAX_DOWNLOAD_BYTES)

    def test_http_error(self):
        with self.assertRaises(ManifestError) as caught:
            fetch_manifest(self.SOURCE, self.workdir(), cwd=self.caller)
        self.assertEqual(str(caught.exception), f"could not download {self.SOURCE}: HTTP 404")

    def test_http_error_codes_are_reported(self):
        for code in (401, 403, 500, 503):
            with self.subTest(code=code):
                self.web.serve(self.SOURCE, error=self.web.http_error(self.SOURCE, code, "nope"))
                with self.assertRaises(ManifestError) as caught:
                    fetch_manifest(self.SOURCE, self.workdir(), cwd=self.caller)
                self.assertEqual(str(caught.exception), f"could not download {self.SOURCE}: HTTP {code}")

    def test_network_errors_are_manifest_errors(self):
        cases = (
            ("dns", URLError("nodename nor servname provided"), "nodename nor servname provided"),
            ("tls", URLError(OSError("certificate verify failed")), "certificate verify failed"),
            ("timeout", TimeoutError("timed out"), "timed out"),
            ("socket timeout", socket.timeout("read timed out"), "read timed out"),
            ("reset", ConnectionResetError(errno.ECONNRESET, "Connection reset by peer"), "Connection reset by peer"),
            ("remote closed", http.client.RemoteDisconnected("Remote end closed connection"), "Remote end closed"),
        )
        for name, error, message in cases:
            with self.subTest(name):
                self.web.serve(self.SOURCE, error=error)
                with self.assertRaises(ManifestError) as caught:
                    fetch_manifest(self.SOURCE, self.workdir(), cwd=self.caller)
                self.assertTrue(str(caught.exception).startswith(f"could not download {self.SOURCE}: "))
                self.assertIn(message, str(caught.exception))

    def test_error_while_reading_the_body_is_a_manifest_error(self):
        self.web.serve(self.SOURCE, read_error=socket.timeout("read timed out"))
        with self.assertRaisesRegex(ManifestError, "^could not download .*read timed out"):
            fetch_manifest(self.SOURCE, self.workdir(), cwd=self.caller)

    def test_truncated_download_is_a_manifest_error(self):
        # http.client raises IncompleteRead (an HTTPException, not an OSError)
        # when a chunked body ends early. bin/update documents exit 2 with a
        # message for this, not a Python traceback.
        self.web.serve(self.SOURCE, read_error=http.client.IncompleteRead(b'{"schema', 4096))
        with self.assertRaisesRegex(ManifestError, f"could not download {self.SOURCE}"):
            fetch_manifest(self.SOURCE, self.workdir(), cwd=self.caller)

    def test_malformed_https_url_is_a_manifest_error(self):
        # Real urllib, no network: http.client rejects these URLs with
        # InvalidURL before it opens a socket.
        connections = []

        def no_network(*args, **kwargs):
            connections.append(args)
            raise AssertionError("tests must not open network connections")

        from urllib.request import urlopen as real_urlopen
        with mock.patch.object(trust, "urlopen", real_urlopen), \
                mock.patch.object(socket, "create_connection", no_network), \
                mock.patch.object(socket, "getaddrinfo", no_network):
            for url in ("https://example.invalid:abc/future-agi-update-manifest.json",
                        "https://example.invalid/future agi/manifest.json"):
                with self.subTest(url=url):
                    with self.assertRaisesRegex(ManifestError, "could not download"):
                        fetch_manifest(url, self.workdir(), cwd=self.caller)
        self.assertEqual(connections, [])

    def test_invalid_json_download(self):
        self.web.serve(self.SOURCE, b"<html>rate limited</html>")
        with self.assertRaisesRegex(ManifestError, "^manifest is not valid JSON"):
            fetch_manifest(self.SOURCE, self.workdir(), cwd=self.caller)

    def test_invalid_manifest_download(self):
        self.web.serve(self.SOURCE, b'{"schema_version": 1}')
        with self.assertRaisesRegex(ManifestError, "manifest is missing"):
            fetch_manifest(self.SOURCE, self.workdir(), cwd=self.caller)

    def test_generic_https_sibling_is_resolved_with_urljoin(self):
        cases = (
            ("https://mirror.example.com/a/b/manifest.json", f"https://mirror.example.com/a/b/{BUNDLE_ASSET}"),
            ("https://mirror.example.com/manifest.json", f"https://mirror.example.com/{BUNDLE_ASSET}"),
            ("https://mirror.example.com/a/manifest.json#frag", f"https://mirror.example.com/a/{BUNDLE_ASSET}"),
            ("https://mirror.example.com:8443/x/m.json", f"https://mirror.example.com:8443/x/{BUNDLE_ASSET}"),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.web.serve(source, encode(make_manifest()))
                self.web.serve(expected, BUNDLE_BYTES)
                fetched = fetch_manifest(source, self.workdir(), cwd=self.caller)
                self.assertEqual(fetched.bundle_source, expected)
                self.assertEqual(fetched.bundle_path.read_bytes(), BUNDLE_BYTES)

    def test_https_sibling_uses_the_manifest_bundle_name(self):
        self.web.serve(self.SOURCE, encode(make_manifest(bundle="other.sigstore.json")))
        expected = "https://downloads.example.com/releases/v1.2.3/other.sigstore.json"
        self.web.serve(expected, b"other")
        fetched = fetch_manifest(self.SOURCE, self.workdir(), cwd=self.caller)
        self.assertEqual(fetched.bundle_source, expected)
        self.assertEqual(fetched.bundle_path.read_bytes(), b"other")

    def test_url_manifest_with_explicit_local_bundle(self):
        self.web.serve(self.SOURCE, encode(make_manifest()))
        (self.caller / "local.bundle").write_bytes(b"local")
        fetched = fetch_manifest(self.SOURCE, self.workdir(), cwd=self.caller, bundle="local.bundle")
        self.assertEqual(fetched.bundle_path.read_bytes(), b"local")
        self.assertEqual(self.web.urls, [self.SOURCE])

    def test_url_manifest_with_explicit_https_bundle(self):
        self.web.serve(self.SOURCE, encode(make_manifest()))
        explicit = "https://other.example.com/sig.json"
        self.web.serve(explicit, b"explicit")
        self.web.serve(self.SIBLING, b"sibling")
        fetched = fetch_manifest(self.SOURCE, self.workdir(), cwd=self.caller, bundle=explicit)
        self.assertEqual(fetched.bundle_source, explicit)
        self.assertEqual(fetched.bundle_path.read_bytes(), b"explicit")
        self.assertEqual(self.web.urls, [self.SOURCE, explicit])

    def test_missing_https_bundle_is_reported_and_fetch_still_succeeds(self):
        self.web.serve(self.SOURCE, encode(make_manifest()))
        fetched = fetch_manifest(self.SOURCE, self.workdir(), cwd=self.caller)
        self.assertEqual(fetched.data, make_manifest())
        self.assertIsNone(fetched.bundle_path)
        self.assertEqual(fetched.bundle_error,
                         f"signature bundle not available ({self.SIBLING}): could not download {self.SIBLING}: HTTP 404")

    def test_bundle_redirect_to_http_is_reported(self):
        self.web.serve(self.SOURCE, encode(make_manifest()))
        self.web.serve(self.SIBLING, BUNDLE_BYTES, final_url="http://cdn.example.com/b.json")
        fetched = fetch_manifest(self.SOURCE, self.workdir(), cwd=self.caller)
        self.assertIsNone(fetched.bundle_path)
        self.assertIn("redirected to a non-https URL", fetched.bundle_error)

    def test_bundle_over_the_cap_is_reported(self):
        self.web.serve(self.SOURCE, encode(make_manifest()))
        self.web.serve(self.SIBLING, b" " * (MAX_DOWNLOAD_BYTES + 1))
        fetched = fetch_manifest(self.SOURCE, self.workdir(), cwd=self.caller)
        self.assertIsNone(fetched.bundle_path)
        self.assertIn(f"is larger than {MAX_DOWNLOAD_BYTES} bytes", fetched.bundle_error)

    def test_truncated_bundle_download_is_reported_not_raised(self):
        self.web.serve(self.SOURCE, encode(make_manifest()))
        self.web.serve(self.SIBLING, read_error=http.client.IncompleteRead(b"{", 512))
        fetched = fetch_manifest(self.SOURCE, self.workdir(), cwd=self.caller)
        self.assertIsNone(fetched.bundle_path)
        self.assertIn("signature bundle not available", fetched.bundle_error or "")

    def test_http_exceptions_and_value_errors_from_urlopen_are_manifest_errors(self):
        cases = (
            ("invalid url", http.client.InvalidURL("nonnumeric port: 'abc'"), "nonnumeric port: 'abc'"),
            ("bad status line", http.client.BadStatusLine("HTTP/9 ???"), "HTTP/9 ???"),
            ("header line too long", http.client.LineTooLong("header line"),
             "got more than 65536 bytes when reading header line"),
            ("value error", ValueError("Invalid IPv6 URL"), "Invalid IPv6 URL"),
        )
        for name, error, message in cases:
            with self.subTest(name):
                self.web.serve(self.SOURCE, error=error)
                with self.assertRaises(ManifestError) as caught:
                    fetch_manifest(self.SOURCE, self.workdir(), cwd=self.caller)
                self.assertEqual(str(caught.exception), f"could not download {self.SOURCE}: {message}")
                self.assertNotIsInstance(caught.exception, trust.NotFound)

    def test_bundle_http_exceptions_and_value_errors_are_reported_not_raised(self):
        for name, error in (("invalid url", http.client.InvalidURL("nonnumeric port: 'x'")),
                            ("value error", ValueError("Invalid IPv6 URL")),
                            ("remote closed", http.client.RemoteDisconnected("Remote end closed connection"))):
            with self.subTest(name):
                self.web.serve(self.SOURCE, encode(make_manifest()))
                self.web.serve(self.SIBLING, error=error)
                fetched = fetch_manifest(self.SOURCE, self.workdir(), cwd=self.caller)
                self.assertEqual(fetched.data, make_manifest())
                self.assertIsNone(fetched.bundle_path)
                self.assertEqual(fetched.bundle_error,
                                 f"signature bundle not available ({self.SIBLING}): could not download {self.SIBLING}: {error}")

    def test_unparsable_manifest_url_is_a_manifest_error_without_a_request(self):
        with self.assertRaises(ManifestError) as caught:
            fetch_manifest("https://[::1/future-agi-update-manifest.json", self.workdir(), cwd=self.caller)
        self.assertTrue(str(caught.exception).startswith(
            "invalid manifest URL https://[::1/future-agi-update-manifest.json: "), str(caught.exception))
        self.assertEqual(self.web.requests, [])

    def test_only_http_404_is_not_found(self):
        with self.assertRaises(trust.NotFound) as caught:
            trust._download(self.SOURCE)
        self.assertIsInstance(caught.exception, ManifestError)
        self.assertEqual(str(caught.exception), f"could not download {self.SOURCE}: HTTP 404")
        for code in (403, 410, 500):
            with self.subTest(code=code):
                self.web.serve(self.SOURCE, error=self.web.http_error(self.SOURCE, code, "nope"))
                with self.assertRaises(ManifestError) as other:
                    trust._download(self.SOURCE)
                self.assertNotIsInstance(other.exception, trust.NotFound)


class GitHubLatestRewriteTests(TrustTestCase):
    def serve_latest(self, source, manifest, bundle_url, bundle=BUNDLE_BYTES):
        self.web.serve(source, encode(manifest))
        self.web.serve(bundle_url, bundle)

    def test_latest_download_bundle_comes_from_the_manifests_own_release(self):
        pinned = f"https://github.com/future-agi/future-agi/releases/download/v1.4.0/{BUNDLE_ASSET}"
        self.serve_latest(LATEST_URL, make_manifest("v1.4.0"), pinned)
        # A newer release published between the two downloads: its bundle must not be used.
        self.web.serve(f"https://github.com/future-agi/future-agi/releases/latest/download/{BUNDLE_ASSET}", b"newer")
        fetched = fetch_manifest(LATEST_URL, self.workdir(), cwd=self.caller)
        self.assertEqual(fetched.bundle_source, pinned)
        self.assertEqual(fetched.bundle_path.read_bytes(), BUNDLE_BYTES)
        self.assertEqual(self.web.urls, [LATEST_URL, pinned])

    def test_rewrite_for_a_fork_with_a_custom_bundle_name(self):
        source = "https://github.com/acme/agi-fork/releases/latest/download/manifest.json"
        pinned = "https://github.com/acme/agi-fork/releases/download/v3.0.1/acme.sigstore.json"
        self.serve_latest(source, make_manifest("v3.0.1", bundle="acme.sigstore.json"), pinned)
        fetched = fetch_manifest(source, self.workdir(), cwd=self.caller)
        self.assertEqual(fetched.bundle_source, pinned)

    def test_rewrite_uses_a_prerelease_version(self):
        pinned = f"https://github.com/future-agi/future-agi/releases/download/v2.0.0-rc.1/{BUNDLE_ASSET}"
        self.serve_latest(LATEST_URL, make_manifest("v2.0.0-rc.1"), pinned)
        fetched = fetch_manifest(LATEST_URL, self.workdir(), cwd=self.caller)
        self.assertEqual(fetched.bundle_source, pinned)

    def test_specific_release_url_uses_its_sibling(self):
        source = "https://github.com/future-agi/future-agi/releases/download/v1.2.3/future-agi-update-manifest.json"
        sibling = f"https://github.com/future-agi/future-agi/releases/download/v1.2.3/{BUNDLE_ASSET}"
        self.serve_latest(source, make_manifest(), sibling)
        fetched = fetch_manifest(source, self.workdir(), cwd=self.caller)
        self.assertEqual(fetched.bundle_source, sibling)

    def test_latest_path_on_another_host_is_not_rewritten(self):
        for host in ("mirror.example.com", "github.com.evil.example", "www.github.com"):
            with self.subTest(host=host):
                source = f"https://{host}/future-agi/future-agi/releases/latest/download/m.json"
                sibling = f"https://{host}/future-agi/future-agi/releases/latest/download/{BUNDLE_ASSET}"
                self.serve_latest(source, make_manifest(), sibling)
                fetched = fetch_manifest(source, self.workdir(), cwd=self.caller)
                self.assertEqual(fetched.bundle_source, sibling)

    def test_explicit_bundle_wins_over_the_rewrite(self):
        explicit = "https://example.com/explicit.json"
        self.serve_latest(LATEST_URL, make_manifest(), explicit, bundle=b"explicit")
        fetched = fetch_manifest(LATEST_URL, self.workdir(), cwd=self.caller, bundle=explicit)
        self.assertEqual(fetched.bundle_source, explicit)
        self.assertEqual(fetched.bundle_path.read_bytes(), b"explicit")

    def test_missing_release_bundle_is_reported(self):
        self.web.serve(LATEST_URL, encode(make_manifest("v1.4.0")))
        fetched = fetch_manifest(LATEST_URL, self.workdir(), cwd=self.caller)
        pinned = f"https://github.com/future-agi/future-agi/releases/download/v1.4.0/{BUNDLE_ASSET}"
        self.assertIsNone(fetched.bundle_path)
        self.assertEqual(fetched.bundle_source, pinned)
        self.assertIn(f"signature bundle not available ({pinned})", fetched.bundle_error)

    def test_latest_download_found_has_no_note(self):
        pinned = f"https://github.com/future-agi/future-agi/releases/download/v1.4.0/{BUNDLE_ASSET}"
        self.serve_latest(LATEST_URL, make_manifest("v1.4.0"), pinned)
        fetched = fetch_manifest(LATEST_URL, self.workdir(), cwd=self.caller)
        self.assertEqual(fetched.source, LATEST_URL)
        self.assertIsNone(fetched.note)


class GitHubLatestFallbackTests(TrustTestCase):
    """releases/latest/download answers 404 while the newest release has no manifest yet.

    release-please creates the release first; release-update-manifest.yml
    attaches the manifest only once the images are published (30-90 minutes
    later, or never if it failed), and an ad-hoc 'demo' release can be the
    latest release too.
    """

    API_URL = "https://api.github.com/repos/future-agi/future-agi/releases?per_page=30"
    MISSING = (f"{LATEST_URL} was not found: the newest release of future-agi/future-agi has no update manifest "
               "yet (it is attached once the release's images are published). Retry later, or pass --manifest "
               "with the manifest URL of a specific release")

    def download(self, tag, asset, repo="future-agi/future-agi"):
        return f"https://github.com/{repo}/releases/download/{tag}/{asset}"

    def serve_api(self, releases, url=API_URL):
        self.web.serve(url, json.dumps(releases).encode("utf-8"))

    def serve_release(self, tag, bundle_bytes=BUNDLE_BYTES, repo="future-agi/future-agi", asset=MANIFEST_ASSET,
                      **manifest):
        self.web.serve(self.download(tag, asset, repo), encode(make_manifest(tag, **manifest)))
        if bundle_bytes is not None:
            bundle_name = manifest.get("bundle", BUNDLE_ASSET)
            self.web.serve(self.download(tag, bundle_name, repo), bundle_bytes)

    def test_newest_release_without_a_manifest_falls_back_to_the_newest_that_has_one(self):
        self.serve_api([
            release("v1.5.0", "source.tar.gz"),
            release("v1.4.0", MANIFEST_ASSET, BUNDLE_ASSET),
            release("v1.3.0", MANIFEST_ASSET, BUNDLE_ASSET),
        ])
        self.serve_release("v1.4.0")
        self.serve_release("v1.3.0", bundle_bytes=b"older")
        fetched = fetch_manifest(LATEST_URL, self.workdir(), cwd=self.caller)

        chosen = self.download("v1.4.0", MANIFEST_ASSET)
        self.assertEqual(fetched.source, chosen)
        self.assertEqual(fetched.data, make_manifest("v1.4.0"))
        self.assertEqual(fetched.raw, encode(make_manifest("v1.4.0")))
        self.assertEqual(fetched.manifest_path.read_bytes(), fetched.raw)
        self.assertEqual(fetched.note, "the newest release v1.5.0 has no update manifest yet; "
                                       "using v1.4.0, the newest release that has one")
        # The bundle comes from the release the manifest came from.
        self.assertEqual(fetched.bundle_source, self.download("v1.4.0", BUNDLE_ASSET))
        self.assertEqual(fetched.bundle_path.read_bytes(), BUNDLE_BYTES)
        self.assertIsNone(fetched.bundle_error)
        self.assertEqual(self.web.urls, [LATEST_URL, self.API_URL, chosen, self.download("v1.4.0", BUNDLE_ASSET)])

    def test_release_list_request(self):
        self.serve_api([release("v1.4.0", MANIFEST_ASSET, BUNDLE_ASSET)])
        self.serve_release("v1.4.0")
        fetch_manifest(LATEST_URL, self.workdir(), cwd=self.caller)
        api = self.web.requests[1]
        self.assertEqual(api.full_url, self.API_URL)
        self.assertEqual(api.get_header("Accept"), "application/vnd.github+json")
        self.assertEqual(api.get_header("User-agent"), "future-agi-bin-update")
        self.assertIsNone(api.data)
        self.assertEqual(self.web.timeouts[1], 30.0)
        self.assertEqual(self.web.responses[0].reads, [MAX_DOWNLOAD_BYTES + 1])
        self.assertTrue(self.web.responses[0].closed)
        # The manifest itself is still asked for as JSON.
        self.assertEqual(self.web.requests[2].get_header("Accept"), "application/json")

    def test_highest_version_wins_not_the_first_listed(self):
        self.serve_api([
            release("v1.11.0", BUNDLE_ASSET),
            release("v1.3.0", MANIFEST_ASSET, BUNDLE_ASSET),
            release("v1.10.0", MANIFEST_ASSET, BUNDLE_ASSET),
            release("v1.9.0", MANIFEST_ASSET, BUNDLE_ASSET),
        ])
        for tag in ("v1.3.0", "v1.10.0", "v1.9.0"):
            self.serve_release(tag)
        fetched = fetch_manifest(LATEST_URL, self.workdir(), cwd=self.caller)
        self.assertEqual(fetched.source, self.download("v1.10.0", MANIFEST_ASSET))
        self.assertEqual(fetched.data["version"], "v1.10.0")
        self.assertEqual(fetched.note, "the newest release v1.11.0 has no update manifest yet; "
                                       "using v1.10.0, the newest release that has one")

    def test_drafts_prereleases_demo_tags_and_releases_without_the_manifest_are_skipped(self):
        self.serve_api([
            release("v2.0.0", MANIFEST_ASSET, BUNDLE_ASSET, draft=True),
            release("v1.9.0", MANIFEST_ASSET, BUNDLE_ASSET, prerelease=True),
            release("v1.8.0-rc.1", MANIFEST_ASSET, BUNDLE_ASSET),
            release("v1.8.0-beta.2", MANIFEST_ASSET, BUNDLE_ASSET),
            release("demo-2026-10-19", MANIFEST_ASSET, BUNDLE_ASSET),
            release("demo", MANIFEST_ASSET),
            release("v1.7", MANIFEST_ASSET),
            release("1.7.0", MANIFEST_ASSET),
            release("v1.6.0", BUNDLE_ASSET, "future-agi-update-manifest.json.bak"),
            release("v1.5.0", MANIFEST_ASSET, BUNDLE_ASSET),
            release("v1.4.0", MANIFEST_ASSET, BUNDLE_ASSET),
            "not a release",
            {"tag_name": "v9.0.0", "draft": False, "prerelease": False, "assets": "broken"},
            {"tag_name": "v9.1.0", "draft": False, "prerelease": False},
            {"tag_name": None, "assets": [{"name": MANIFEST_ASSET}]},
            {"tag_name": "v9.2.0", "assets": ["future-agi-update-manifest.json"]},
        ])
        self.serve_release("v1.5.0")
        fetched = fetch_manifest(LATEST_URL, self.workdir(), cwd=self.caller)
        self.assertEqual(fetched.source, self.download("v1.5.0", MANIFEST_ASSET))
        self.assertEqual(fetched.note, "the newest release v9.2.0 has no update manifest yet; "
                                       "using v1.5.0, the newest release that has one")

    def test_latest_release_that_is_not_a_platform_release(self):
        # A 'demo' release is GitHub's latest; the newest platform release has
        # its manifest.
        self.serve_api([release("demo-2026-10-19", "demo.zip"), release("v1.4.0", MANIFEST_ASSET, BUNDLE_ASSET)])
        self.serve_release("v1.4.0")
        fetched = fetch_manifest(LATEST_URL, self.workdir(), cwd=self.caller)
        self.assertEqual(fetched.source, self.download("v1.4.0", MANIFEST_ASSET))
        self.assertEqual(fetched.note, "the latest GitHub release has no update manifest; "
                                       "using v1.4.0, the newest release that has one")

    def test_fork_uses_its_own_repository_and_asset_name(self):
        source = "https://github.com/acme/agi-fork/releases/latest/download/acme-manifest.json"
        api = "https://api.github.com/repos/acme/agi-fork/releases?per_page=30"
        self.serve_api([release("v3.1.0", "other.json", repo="acme/agi-fork"),
                        release("v3.0.1", "acme-manifest.json", "acme.sigstore.json", repo="acme/agi-fork"),
                        release("v3.0.0", MANIFEST_ASSET, BUNDLE_ASSET, repo="acme/agi-fork")], url=api)
        self.serve_release("v3.0.1", repo="acme/agi-fork", asset="acme-manifest.json", bundle="acme.sigstore.json")
        self.web.serve(self.download("v3.0.1", "acme.sigstore.json", "acme/agi-fork"), b"acme-bundle")
        fetched = fetch_manifest(source, self.workdir(), cwd=self.caller)
        self.assertEqual(fetched.source, self.download("v3.0.1", "acme-manifest.json", "acme/agi-fork"))
        self.assertEqual(fetched.bundle_source, self.download("v3.0.1", "acme.sigstore.json", "acme/agi-fork"))
        self.assertEqual(fetched.bundle_path.read_bytes(), b"acme-bundle")
        self.assertEqual(self.web.urls[:2], [source, api])

    def test_bundle_missing_from_the_chosen_release_is_reported(self):
        self.serve_api([release("v1.5.0"), release("v1.4.0", MANIFEST_ASSET)])
        self.serve_release("v1.4.0", bundle_bytes=None)
        fetched = fetch_manifest(LATEST_URL, self.workdir(), cwd=self.caller)
        bundle = self.download("v1.4.0", BUNDLE_ASSET)
        self.assertIsNone(fetched.bundle_path)
        # Round 5 (T26/T27): the fallback then looks for a complete pair, and says that none exists.
        self.assertEqual(fetched.bundle_error,
                         f"signature bundle not available ({bundle}): could not download {bundle}: HTTP 404. "
                         f"No stable release of future-agi/future-agi has both {MANIFEST_ASSET} and its signature "
                         f"bundle {BUNDLE_ASSET}. Retry later, or pass --manifest and --bundle for a specific release")

    def test_explicit_bundle_still_wins(self):
        explicit = "https://example.com/explicit.json"
        self.serve_api([release("v1.5.0"), release("v1.4.0", MANIFEST_ASSET, BUNDLE_ASSET)])
        self.serve_release("v1.4.0")
        self.web.serve(explicit, b"explicit")
        fetched = fetch_manifest(LATEST_URL, self.workdir(), cwd=self.caller, bundle=explicit)
        self.assertEqual(fetched.bundle_source, explicit)
        self.assertEqual(fetched.bundle_path.read_bytes(), b"explicit")

    def test_no_release_with_the_manifest_is_a_clear_error(self):
        for name, releases in (
            ("no releases", []),
            ("none has it", [release("v1.5.0", BUNDLE_ASSET), release("v1.4.0", "notes.md")]),
            ("only drafts and prereleases have it", [release("v1.5.0", MANIFEST_ASSET, draft=True),
                                                     release("v1.5.0-rc.1", MANIFEST_ASSET)]),
            ("not a list", {"message": "Moved Permanently"}),
        ):
            with self.subTest(name):
                self.serve_api(releases)
                with self.assertRaises(ManifestError) as caught:
                    fetch_manifest(LATEST_URL, self.workdir(), cwd=self.caller)
                self.assertEqual(str(caught.exception), self.MISSING)
                self.assertEqual(self.web.urls[-2:], [LATEST_URL, self.API_URL])

    def test_release_list_failure_is_a_clear_error(self):
        cases = (
            ("rate limited", dict(error=self.web.http_error(self.API_URL, 403, "rate limit exceeded")),
             f"could not download {self.API_URL}: HTTP 403"),
            ("not found", {"error": self.web.http_error(self.API_URL, 404)}, f"could not download {self.API_URL}: HTTP 404"),
            ("offline", {"error": URLError("nodename nor servname provided")},
             f"could not download {self.API_URL}: nodename nor servname provided"),
            ("redirected to http", {"body": b"[]", "final_url": "http://api.example.com/releases"},
             f"{self.API_URL} redirected to a non-https URL"),
            ("too large", {"body": b" " * (MAX_DOWNLOAD_BYTES + 1)},
             f"{self.API_URL} is larger than {MAX_DOWNLOAD_BYTES} bytes"),
            ("not json", {"body": b"<html>unicorn</html>"}, "Expecting value"),
            ("not utf-8", {"body": b"\xff\xfe\xfa"}, ""),
        )
        for name, route, reason in cases:
            with self.subTest(name):
                self.web.serve(self.API_URL, **route)
                with self.assertRaises(ManifestError) as caught:
                    fetch_manifest(LATEST_URL, self.workdir(), cwd=self.caller)
                message = str(caught.exception)
                self.assertTrue(message.startswith(self.MISSING + ". Looking for an older release that has one failed: "),
                                message)
                self.assertIn(reason, message)
                self.assertIn("--manifest", message)

    def test_manifest_of_the_chosen_release_failing_to_download_is_an_error(self):
        self.serve_api([release("v1.5.0"), release("v1.4.0", MANIFEST_ASSET, BUNDLE_ASSET)])
        chosen = self.download("v1.4.0", MANIFEST_ASSET)
        self.web.serve(chosen, error=self.web.http_error(chosen, 502, "Bad Gateway"))
        with self.assertRaises(ManifestError) as caught:
            fetch_manifest(LATEST_URL, self.workdir(), cwd=self.caller)
        self.assertEqual(str(caught.exception), f"could not download {chosen}: HTTP 502")

    def test_404_from_any_other_url_does_not_fall_back(self):
        for source in (
            "https://downloads.example.com/releases/latest/download/future-agi-update-manifest.json",
            "https://mirror.example.com/future-agi/future-agi/releases/latest/download/future-agi-update-manifest.json",
            "https://github.com/future-agi/future-agi/releases/download/v1.2.3/future-agi-update-manifest.json",
            "https://github.com/future-agi/future-agi/releases/latest/download/future-agi-update-manifest.json?x=1",
            "https://www.github.com/future-agi/future-agi/releases/latest/download/future-agi-update-manifest.json",
        ):
            with self.subTest(source=source):
                self.web.requests.clear()
                self.serve_api([release("v1.4.0", MANIFEST_ASSET, BUNDLE_ASSET)])
                with self.assertRaises(ManifestError) as caught:
                    fetch_manifest(source, self.workdir(), cwd=self.caller)
                self.assertEqual(str(caught.exception), f"could not download {source}: HTTP 404")
                self.assertEqual(self.web.urls, [source])

    def test_other_errors_from_the_latest_url_do_not_fall_back(self):
        for code in (401, 403, 500, 503):
            with self.subTest(code=code):
                self.web.requests.clear()
                self.web.serve(LATEST_URL, error=self.web.http_error(LATEST_URL, code, "nope"))
                self.serve_api([release("v1.4.0", MANIFEST_ASSET, BUNDLE_ASSET)])
                with self.assertRaises(ManifestError) as caught:
                    fetch_manifest(LATEST_URL, self.workdir(), cwd=self.caller)
                self.assertEqual(str(caught.exception), f"could not download {LATEST_URL}: HTTP {code}")
                self.assertEqual(self.web.urls, [LATEST_URL])

    def test_fallback_manifest_is_verified_against_its_own_version(self):
        self.serve_api([release("v1.5.0"), release("v1.2.3", MANIFEST_ASSET, BUNDLE_ASSET)])
        self.serve_release("v1.2.3")
        fetched = fetch_manifest(LATEST_URL, self.workdir(), cwd=self.caller)
        verification = self.verifier().verify(fetched)
        self.assertTrue(verification.verified)
        argv = self.runner.argv
        self.assertEqual(argv[argv.index("--certificate-identity") + 1], PINNED_IDENTITY_V123)
        self.assertEqual(argv[argv.index("--bundle") + 1], str(fetched.bundle_path))


class TimeOfCheckTests(TrustTestCase):
    def test_overwriting_the_source_after_fetch_changes_nothing(self):
        release = self.tmp / "release"
        path = self.write_release(release, make_manifest())
        fetched = fetch_manifest(str(path), self.workdir(), cwd=self.caller)
        original = fetched.raw

        path.write_bytes(encode(make_manifest("v9.9.9")))
        (release / BUNDLE_ASSET).write_bytes(b"forged-bundle")

        self.assertEqual(fetched.raw, original)
        self.assertEqual(fetched.data["version"], "v1.2.3")
        self.assertEqual(fetched.manifest_path.read_bytes(), original)
        self.assertEqual(fetched.bundle_path.read_bytes(), BUNDLE_BYTES)

    def test_local_source_is_read_exactly_once(self):
        # Counts both ways a local file can be read here: Path.read_bytes and a
        # bounded read through os.open (Path.read_bytes does not go through the
        # patched os.open, so nothing is counted twice).
        path = self.write_release(self.tmp / "release", make_manifest())
        opened = []
        real_open, real_read_bytes = os.open, Path.read_bytes

        def os_open(target, *args, **kwargs):
            opened.append(Path(target))
            return real_open(target, *args, **kwargs)

        def read_bytes(self_path):
            opened.append(Path(self_path))
            return real_read_bytes(self_path)

        with mock.patch.object(os, "open", os_open), mock.patch.object(Path, "read_bytes", read_bytes):
            fetch_manifest(str(path), self.workdir(), cwd=self.caller)
        self.assertEqual([p for p in opened if p == path], [path])

    def test_parsed_data_is_the_parse_of_the_verified_copy(self):
        fetched = self.local_fetch()
        self.assertEqual(json.loads(fetched.manifest_path.read_bytes()), fetched.data)
        self.assertEqual(fetched.manifest_path.read_bytes(), fetched.raw)

    def test_data_is_detached_from_the_raw_bytes(self):
        fetched = self.local_fetch()
        fetched.data["version"] = "v9.9.9"
        self.assertEqual(json.loads(fetched.raw)["version"], "v1.2.3")

    def test_cosign_verifies_the_private_copies_not_the_sources(self):
        release = self.tmp / "release"
        path = self.write_release(release, make_manifest())
        fetched = fetch_manifest(str(path), self.workdir(), cwd=self.caller)
        path.write_bytes(encode(make_manifest("v9.9.9")))
        (release / BUNDLE_ASSET).write_bytes(b"forged-bundle")
        seen = {}

        def look(command):
            seen["manifest"] = Path(command[-1]).read_bytes()
            seen["bundle"] = Path(command[command.index("--bundle") + 1]).read_bytes()

        verifier = self.verifier(runner=FakeRunner(on_call=look))
        verification = verifier.verify(fetched)
        self.assertTrue(verification.verified)
        argv = self.runner.argv
        self.assertEqual(argv[-1], str(fetched.manifest_path))
        self.assertEqual(argv[argv.index("--bundle") + 1], str(fetched.bundle_path))
        self.assertNotIn(str(path), argv)
        self.assertNotIn(str(release / BUNDLE_ASSET), argv)
        self.assertEqual(seen, {"manifest": fetched.raw, "bundle": BUNDLE_BYTES})

    def test_https_manifest_overwritten_upstream_after_fetch_changes_nothing(self):
        source = "https://downloads.example.com/m.json"
        self.web.serve(source, encode(make_manifest()))
        self.web.serve(f"https://downloads.example.com/{BUNDLE_ASSET}", BUNDLE_BYTES)
        fetched = fetch_manifest(source, self.workdir(), cwd=self.caller)
        self.web.serve(source, encode(make_manifest("v9.9.9")))
        verifier = self.verifier()
        verifier.verify(fetched)
        self.assertEqual(Path(self.runner.argv[-1]).read_bytes(), encode(make_manifest()))
        self.assertEqual(self.web.urls.count(source), 1)


# ============================================================== verifying ==

class KeylessVerificationTests(TrustTestCase):
    def test_default_trust_anchor(self):
        verifier = self.verifier()
        self.assertEqual(verifier.expected_signer("v1.2.3"), PINNED_IDENTITY_V123)
        self.assertEqual(verifier.settings.get("FUTURE_AGI_UPDATE_TRUSTED_ISSUER"), PINNED_ISSUER)

    def test_keyless_argv_uses_the_pinned_identity_and_issuer(self):
        fetched = self.local_fetch()
        verification = self.verifier().verify(fetched)
        self.assertEqual(self.runner.argv, [
            COSIGN, "verify-blob", "--bundle", str(fetched.bundle_path),
            "--certificate-identity", PINNED_IDENTITY_V123,
            "--certificate-oidc-issuer", PINNED_ISSUER,
            "--certificate-github-workflow-repository", "future-agi/future-agi",
            "--certificate-github-workflow-ref", "refs/tags/v1.2.3",
            str(fetched.manifest_path),
        ])
        self.assertEqual(self.runner.calls[0][1],
                         {"capture_output": True, "text": True, "check": False, "timeout": 300})
        self.assertEqual(verification, Verification(True, method="keyless", signer=PINNED_IDENTITY_V123,
                                                    issuer=PINNED_ISSUER, tool=COSIGN))
        self.assertEqual(verification.error, "")

    def test_version_comes_from_the_manifest(self):
        for version in ("v0.9.0", "v2.0.0", "v2.0.0-beta.1", "v10.20.30-rc.4"):
            with self.subTest(version=version):
                fetched = self.local_fetch(make_manifest(version))
                verification = self.verifier().verify(fetched)
                expected = ("https://github.com/future-agi/future-agi/.github/workflows/"
                            f"release-update-manifest.yml@refs/tags/{version}")
                argv = self.runner.argv
                self.assertEqual(argv[argv.index("--certificate-identity") + 1], expected)
                self.assertEqual(verification.signer, expected)

    def test_manifest_declaring_the_pinned_identity_and_issuer_is_verified(self):
        fetched = self.local_fetch(make_manifest(identity=PINNED_IDENTITY_V123, issuer=PINNED_ISSUER))
        verification = self.verifier().verify(fetched)
        self.assertTrue(verification.verified)
        argv = self.runner.argv
        self.assertEqual(argv[argv.index("--certificate-identity") + 1], PINNED_IDENTITY_V123)
        self.assertEqual(argv[argv.index("--certificate-oidc-issuer") + 1], PINNED_ISSUER)

    def test_manifest_declaring_another_identity_fails_without_running_cosign(self):
        others = (
            "https://github.com/future-agi/future-agi/.github/workflows/release-update-manifest.yml@refs/tags/v1.2.2",
            "https://github.com/attacker/future-agi/.github/workflows/release-update-manifest.yml@refs/tags/v1.2.3",
            "https://github.com/future-agi/future-agi/.github/workflows/other.yml@refs/tags/v1.2.3",
            "https://github.com/future-agi/future-agi/.github/workflows/release-update-manifest.yml@refs/heads/main",
            PINNED_IDENTITY_V123 + "/",
            PINNED_IDENTITY_V123.upper(),
            "someone@example.com",
        )
        for identity in others:
            with self.subTest(identity=identity):
                fetched = self.local_fetch(make_manifest(identity=identity))
                verifier = self.verifier()
                verification = verifier.verify(fetched)
                self.assertEqual(self.runner.calls, [])
                self.assertEqual(self.which.calls, [])
                self.assertFalse(verification.verified)
                self.assertEqual(verification.method, "keyless")
                self.assertEqual(verification.signer, PINNED_IDENTITY_V123)
                self.assertEqual(verification.issuer, PINNED_ISSUER)
                self.assertEqual(verification.tool, "")
                self.assertEqual(verification.error, (
                    f"manifest says it was signed by identity {identity!r}, "
                    f"but bin/update only trusts {PINNED_IDENTITY_V123!r}"))

    def test_manifest_declaring_another_issuer_fails_without_running_cosign(self):
        for issuer in ("https://accounts.google.com", PINNED_ISSUER + "/", "http://token.actions.githubusercontent.com",
                       "https://token.actions.githubusercontent.com.evil.example"):
            with self.subTest(issuer=issuer):
                fetched = self.local_fetch(make_manifest(identity=PINNED_IDENTITY_V123, issuer=issuer))
                verification = self.verifier().verify(fetched)
                self.assertEqual(self.runner.calls, [])
                self.assertFalse(verification.verified)
                self.assertEqual(verification.error, (
                    f"manifest says it was signed by issuer {issuer!r}, "
                    f"but bin/update only trusts {PINNED_ISSUER!r}"))

    def test_manifest_without_declared_signer_uses_the_pinned_one(self):
        fetched = self.local_fetch(make_manifest())
        self.assertNotIn("identity", fetched.data["signature"])
        self.assertTrue(self.verifier().verify(fetched).verified)
        self.assertIn(PINNED_IDENTITY_V123, self.runner.argv)

    def test_operator_identity_with_version_placeholder(self):
        template = "https://github.com/acme/agi-fork/.github/workflows/publish.yml@refs/tags/{version}"
        fetched = self.local_fetch(make_manifest("v4.5.6"))
        verification = self.verifier(FUTURE_AGI_UPDATE_TRUSTED_IDENTITY=template).verify(fetched)
        expected = "https://github.com/acme/agi-fork/.github/workflows/publish.yml@refs/tags/v4.5.6"
        argv = self.runner.argv
        self.assertEqual(argv[argv.index("--certificate-identity") + 1], expected)
        self.assertEqual(argv[argv.index("--certificate-oidc-issuer") + 1], PINNED_ISSUER)
        self.assertEqual(verification.signer, expected)

    def test_operator_identity_without_version_placeholder(self):
        identity = "https://github.com/acme/agi-fork/.github/workflows/publish.yml@refs/heads/main"
        fetched = self.local_fetch(make_manifest("v4.5.6"))
        verification = self.verifier(FUTURE_AGI_UPDATE_TRUSTED_IDENTITY=identity).verify(fetched)
        argv = self.runner.argv
        self.assertEqual(argv[argv.index("--certificate-identity") + 1], identity)
        self.assertEqual(verification.signer, identity)

    def test_every_version_placeholder_is_replaced(self):
        verifier = self.verifier(FUTURE_AGI_UPDATE_TRUSTED_IDENTITY="https://x.example/{version}/y@refs/tags/{version}")
        self.assertEqual(verifier.expected_signer("v1.0.0"), "https://x.example/v1.0.0/y@refs/tags/v1.0.0")

    def test_operator_issuer_override(self):
        fetched = self.local_fetch()
        verification = self.verifier(FUTURE_AGI_UPDATE_TRUSTED_ISSUER="https://issuer.acme.example").verify(fetched)
        argv = self.runner.argv
        self.assertEqual(argv[argv.index("--certificate-oidc-issuer") + 1], "https://issuer.acme.example")
        self.assertEqual(verification.issuer, "https://issuer.acme.example")

    def test_manifest_cannot_bring_back_the_default_signer_when_the_operator_pinned_another(self):
        template = "https://github.com/acme/agi-fork/.github/workflows/publish.yml@refs/tags/{version}"
        fetched = self.local_fetch(make_manifest(identity=PINNED_IDENTITY_V123))
        verification = self.verifier(FUTURE_AGI_UPDATE_TRUSTED_IDENTITY=template).verify(fetched)
        self.assertFalse(verification.verified)
        self.assertEqual(self.runner.calls, [])
        self.assertIn("only trusts 'https://github.com/acme/agi-fork/", verification.error)

    def test_identity_pin_from_the_environment_applies_too(self):
        identity = "https://github.com/acme/agi-fork/.github/workflows/publish.yml@refs/tags/{version}"
        settings = load_settings(self.checkout, environ={"FUTURE_AGI_UPDATE_TRUSTED_IDENTITY": identity})
        runner = FakeRunner()
        verifier = Verifier(settings, runner=runner, which=FakeWhich({"cosign": COSIGN}))
        fetched = self.local_fetch()
        verifier.verify(fetched)
        self.assertIn("https://github.com/acme/agi-fork/.github/workflows/publish.yml@refs/tags/v1.2.3",
                      runner.argv)

    def test_offline_never_skips_the_transparency_log_for_keyless(self):
        fetched = self.local_fetch()
        # Round 4 (S20): load_settings refuses OFFLINE without a public key;
        # Settings made any other way still never skip the log for keyless.
        with self.assertRaises(SettingsError):
            self.settings(FUTURE_AGI_UPDATE_COSIGN_OFFLINE="true")
        self.verifier(settings=raw_settings(FUTURE_AGI_UPDATE_COSIGN_OFFLINE="true")).verify(fetched)
        self.assertFalse(any(arg.startswith("--insecure-ignore-tlog") for arg in self.runner.argv))
        self.assertNotIn("--key", self.runner.argv)


class KeyVerificationTests(TrustTestCase):
    def write_key(self, content=b"-----BEGIN PUBLIC KEY-----\nMFkw...\n-----END PUBLIC KEY-----\n"):
        key = self.tmp / "keys" / "cosign.pub"
        key.parent.mkdir(exist_ok=True)
        key.write_bytes(content)
        return key

    def test_key_mode_argv_uses_a_private_copy_of_the_key(self):
        self.umask(0)
        key = self.write_key()
        fetched = self.local_fetch()
        verification = self.verifier(FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY=str(key)).verify(fetched)
        copy = fetched.directory / "trusted-key.pub"
        self.assertEqual(self.runner.argv, [
            COSIGN, "verify-blob", "--key", str(copy), "--bundle", str(fetched.bundle_path),
            str(fetched.manifest_path),
        ])
        self.assertEqual(copy.read_bytes(), key.read_bytes())
        self.assertEqual(self.mode(copy), 0o644)
        import hashlib
        self.assertEqual(verification, Verification(True, method="public key", signer=str(key), issuer="",
                                                    tool=COSIGN, public_key_sha256=hashlib.sha256(key.read_bytes()).hexdigest()))
        self.assertEqual(self.verifier(FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY=str(key)).public_key_sha256(),
                         verification.public_key_sha256)
        self.assertNotIn("public_key_sha256", verification.as_dict())

    def test_key_mode_online_keeps_the_transparency_log(self):
        key = self.write_key()
        fetched = self.local_fetch()
        self.verifier(FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY=str(key), FUTURE_AGI_UPDATE_COSIGN_OFFLINE="false").verify(fetched)
        self.assertFalse(any("insecure" in arg for arg in self.runner.argv))
        self.assertNotIn("--certificate-identity", self.runner.argv)

    def test_key_mode_offline_skips_the_transparency_log(self):
        key = self.write_key()
        fetched = self.local_fetch()
        for offline in ("true", "1", "yes", "on", "TRUE"):
            with self.subTest(offline=offline):
                self.verifier(FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY=str(key),
                              FUTURE_AGI_UPDATE_COSIGN_OFFLINE=offline).verify(fetched)
                self.assertEqual(self.runner.argv, [
                    COSIGN, "verify-blob", "--key", str(fetched.directory / "trusted-key.pub"),
                    "--bundle", str(fetched.bundle_path), "--insecure-ignore-tlog=true", str(fetched.manifest_path),
                ])

    def test_key_mode_does_not_use_the_keyless_identity(self):
        key = self.write_key()
        fetched = self.local_fetch(make_manifest(identity="https://github.com/someone/else/x.yml@refs/tags/v1.2.3"))
        verification = self.verifier(FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY=str(key)).verify(fetched)
        self.assertTrue(verification.verified)
        self.assertNotIn("--certificate-identity", self.runner.argv)
        self.assertNotIn("--certificate-oidc-issuer", self.runner.argv)

    def test_key_changed_after_the_copy_does_not_reach_cosign(self):
        key = self.write_key(b"trusted")
        fetched = self.local_fetch()
        seen = []

        def look(command):
            seen.append(Path(command[command.index("--key") + 1]).read_bytes())
            key.write_bytes(b"swapped")

        verifier = self.verifier(FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY=str(key), runner=FakeRunner(on_call=look))
        verifier.verify(fetched)
        self.assertEqual(seen, [b"trusted"])

    def test_verifying_twice_with_a_key_works(self):
        key = self.write_key()
        fetched = self.local_fetch()
        verifier = self.verifier(FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY=str(key))
        self.assertTrue(verifier.verify(fetched).verified)
        self.assertTrue(verifier.verify(fetched).verified)
        self.assertEqual(len(self.runner.calls), 2)

    def test_missing_key(self):
        missing = self.tmp / "keys" / "absent.pub"
        fetched = self.local_fetch()
        verification = self.verifier(FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY=str(missing)).verify(fetched)
        self.assertFalse(verification.verified)
        self.assertTrue(verification.error.startswith(f"cannot read FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY {missing}: "),
                        verification.error)
        self.assertEqual(self.runner.calls, [])
        self.assertFalse((fetched.directory / "trusted-key.pub").exists())

    def test_key_that_is_a_directory(self):
        directory = self.tmp / "keys"
        directory.mkdir()
        fetched = self.local_fetch()
        verification = self.verifier(FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY=str(directory)).verify(fetched)
        self.assertFalse(verification.verified)
        self.assertIn(f"cannot read FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY {directory}", verification.error)
        self.assertEqual(self.runner.calls, [])

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root reads any file")
    def test_unreadable_key(self):
        key = self.write_key()
        key.chmod(0)
        self.addCleanup(key.chmod, 0o600)
        fetched = self.local_fetch()
        verification = self.verifier(FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY=str(key)).verify(fetched)
        self.assertFalse(verification.verified)
        self.assertIn(f"cannot read FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY {key}", verification.error)
        self.assertIn("ermission denied", verification.error)
        self.assertEqual(self.runner.calls, [])


class CosignCommandTests(TrustTestCase):
    def test_cosign_binary_is_preferred_over_docker(self):
        fetched = self.local_fetch()
        verification = self.verifier().verify(fetched)
        self.assertEqual(self.runner.argv[0], COSIGN)
        self.assertEqual(verification.tool, COSIGN)
        self.assertEqual(self.which.calls, ["cosign"])

    def test_docker_fallback_keyless_argv(self):
        fetched = self.local_fetch()
        verification = self.verifier(which=self.docker_only()).verify(fetched)
        self.assertEqual(self.runner.argv, [
            "docker", "run", "--rm", "--read-only",
            "--user", f"{os.getuid()}:{os.getgid()}",
            "--tmpfs", "/tmp:rw,size=64m", "-e", "HOME=/tmp",
            "-v", f"{fetched.directory}:/work:ro,z",
            PINNED_IMAGE,
            "verify-blob", "--bundle", "/work/bundle.json",
            "--certificate-identity", PINNED_IDENTITY_V123,
            "--certificate-oidc-issuer", PINNED_ISSUER,
            "--certificate-github-workflow-repository", "future-agi/future-agi",
            "--certificate-github-workflow-ref", "refs/tags/v1.2.3",
            "/work/manifest.json",
        ])
        self.assertEqual(self.which.calls, ["cosign", "docker"])
        self.assertTrue(verification.verified)
        self.assertEqual(verification.tool, PINNED_IMAGE)

    def test_docker_fallback_online_keeps_the_network(self):
        fetched = self.local_fetch()
        self.verifier(which=self.docker_only()).verify(fetched)
        self.assertNotIn("--network", self.runner.argv)

    def test_docker_fallback_offline_disables_the_network(self):
        for key_mode in (False, True):
            with self.subTest(key_mode=key_mode):
                values = {"FUTURE_AGI_UPDATE_COSIGN_OFFLINE": "true"}
                if key_mode:
                    key = self.tmp / "cosign.pub"
                    key.write_bytes(b"key")
                    values["FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY"] = str(key)
                fetched = self.local_fetch()
                # Round 4 (S20): OFFLINE without a key is refused by
                # load_settings, and keyless keeps the network it needs.
                self.verifier(which=self.docker_only(), settings=raw_settings(**values)).verify(fetched)
                argv = self.runner.argv
                if not key_mode:
                    self.assertNotIn("--network", argv)
                    continue
                image = argv.index(PINNED_IMAGE)
                self.assertEqual(argv[argv.index("--network"):argv.index("--network") + 2], ["--network", "none"])
                self.assertLess(argv.index("--network"), image)

    def test_docker_fallback_key_mode_offline_argv(self):
        key = self.tmp / "cosign.pub"
        key.write_bytes(b"key")
        fetched = self.local_fetch()
        verification = self.verifier(which=self.docker_only(), FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY=str(key),
                                     FUTURE_AGI_UPDATE_COSIGN_OFFLINE="true").verify(fetched)
        self.assertEqual(self.runner.argv, [
            "docker", "run", "--rm", "--read-only",
            "--user", f"{os.getuid()}:{os.getgid()}",
            "--tmpfs", "/tmp:rw,size=64m", "-e", "HOME=/tmp",
            "-v", f"{fetched.directory}:/work:ro,z",
            "--network", "none",
            PINNED_IMAGE,
            "verify-blob", "--key", "/work/trusted-key.pub", "--bundle", "/work/bundle.json",
            "--insecure-ignore-tlog=true", "/work/manifest.json",
        ])
        self.assertEqual(verification.method, "public key")
        self.assertEqual(verification.signer, str(key))

    def test_docker_fallback_rewrites_every_workdir_path(self):
        key = self.tmp / "cosign.pub"
        key.write_bytes(b"key")
        fetched = self.local_fetch()
        self.verifier(which=self.docker_only(), FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY=str(key)).verify(fetched)
        argv = self.runner.argv
        after_image = argv[argv.index(PINNED_IMAGE) + 1:]
        self.assertFalse([arg for arg in after_image if str(fetched.directory) in arg], after_image)
        self.assertFalse([arg for arg in after_image if str(self.tmp) in arg], after_image)
        mounts = [argv[i + 1] for i, arg in enumerate(argv) if arg == "-v"]
        self.assertEqual(mounts, [f"{fetched.directory}:/work:ro,z"])

    def test_docker_fallback_uses_the_configured_image(self):
        image = "registry.internal.example/sigstore/cosign:v3.1.3@sha256:" + "1" * 64
        fetched = self.local_fetch()
        verification = self.verifier(which=self.docker_only(), FUTURE_AGI_UPDATE_COSIGN_IMAGE=image).verify(fetched)
        self.assertIn(image, self.runner.argv)
        self.assertNotIn(PINNED_IMAGE, self.runner.argv)
        self.assertEqual(verification.tool, image)

    def test_docker_fallback_uses_the_installations_docker_command(self):
        which = FakeWhich({"/usr/local/bin/docker": "/usr/local/bin/docker"})
        fetched = self.local_fetch()
        self.verifier(which=which, docker=["/usr/local/bin/docker", "--context", "colima"]).verify(fetched)
        self.assertEqual(self.runner.argv[:4], ["/usr/local/bin/docker", "--context", "colima", "run"])
        self.assertEqual(which.calls, ["cosign", "/usr/local/bin/docker"])

    def test_default_docker_command(self):
        which = FakeWhich({"docker": "/usr/bin/docker"})
        runner = FakeRunner()
        verifier = Verifier(self.settings(), runner=runner, which=which)
        self.assertEqual(verifier.docker, ["docker"])
        verifier.verify(self.local_fetch())
        self.assertEqual(runner.argv[:2], ["docker", "run"])

    def test_neither_cosign_nor_docker(self):
        fetched = self.local_fetch()
        verification = self.verifier(which=FakeWhich()).verify(fetched)
        self.assertEqual(self.runner.calls, [])
        self.assertFalse(verification.verified)
        self.assertEqual(verification.method, "keyless")
        self.assertEqual(verification.signer, PINNED_IDENTITY_V123)
        self.assertEqual(verification.issuer, PINNED_ISSUER)
        self.assertEqual(verification.tool, "")
        self.assertEqual(verification.error, (
            "cosign is needed to verify the release signature: install cosign v3 or later "
            "(https://docs.sigstore.dev/cosign/system_config/installation/) or make Docker available"))

    def test_neither_cosign_nor_docker_in_key_mode(self):
        key = self.tmp / "cosign.pub"
        key.write_bytes(b"key")
        fetched = self.local_fetch()
        verification = self.verifier(which=FakeWhich(), FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY=str(key)).verify(fetched)
        self.assertFalse(verification.verified)
        self.assertEqual(verification.method, "public key")
        self.assertIn("cosign is needed to verify the release signature", verification.error)
        self.assertEqual(self.runner.calls, [])

    def test_verification_error_is_a_runtime_error(self):
        self.assertTrue(issubclass(VerificationError, RuntimeError))


class CosignResultTests(TrustTestCase):
    def verify_with(self, runner, *, docker=False, **values):
        fetched = self.local_fetch()
        which = self.docker_only() if docker else None
        return fetched, self.verifier(runner=runner, which=which, **values).verify(fetched)

    def test_failure_reports_the_last_stderr_line(self):
        runner = FakeRunner(returncode=1, stdout="ignored stdout", stderr=(
            "Verifying blob...\nError: none of the expected identities matched what was in the certificate\n\n  \n"))
        _, verification = self.verify_with(runner)
        self.assertFalse(verification.verified)
        self.assertEqual(verification.error, "signature verification failed: "
                                             "Error: none of the expected identities matched what was in the certificate")
        self.assertEqual(verification.tool, COSIGN)
        self.assertEqual(verification.method, "keyless")
        self.assertEqual(verification.signer, PINNED_IDENTITY_V123)
        self.assertEqual(verification.issuer, PINNED_ISSUER)

    def test_failure_falls_back_to_stdout(self):
        runner = FakeRunner(returncode=2, stdout="first\nError: invalid signature when validating ASN.1 encoded signature\n")
        _, verification = self.verify_with(runner)
        self.assertEqual(verification.error,
                         "signature verification failed: Error: invalid signature when validating ASN.1 encoded signature")

    def test_failure_without_output(self):
        for stdout, stderr in (("", ""), (None, None), ("  \n\n", "\n  \n")):
            with self.subTest(stdout=stdout, stderr=stderr):
                _, verification = self.verify_with(FakeRunner(returncode=1, stdout=stdout, stderr=stderr))
                self.assertFalse(verification.verified)
                self.assertEqual(verification.error, "signature verification failed")

    def test_any_nonzero_exit_fails(self):
        for code in (1, 2, 125, 126, 127, 255, -9):
            with self.subTest(code=code):
                _, verification = self.verify_with(FakeRunner(returncode=code, stderr="boom"))
                self.assertFalse(verification.verified)
                self.assertEqual(verification.error, "signature verification failed: boom")

    def test_docker_cannot_see_the_workdir(self):
        for stderr in ("Error: open /work/bundle.json: no such file or directory\nmain.go:74: error during command execution",
                       "Error: open /work/manifest.json: No Such File or directory"):
            with self.subTest(stderr=stderr):
                fetched, verification = self.verify_with(FakeRunner(returncode=1, stderr=stderr), docker=True)
                self.assertFalse(verification.verified)
                self.assertEqual(verification.tool, PINNED_IMAGE)
                self.assertEqual(verification.error, (
                    f"Docker cannot see {fetched.directory}, so cosign (run from {PINNED_IMAGE}) cannot read the "
                    "manifest. Share the checkout with the Docker VM (./bin/install checks the same thing) "
                    "or install cosign v3 or later"))

    def test_no_such_file_from_a_cosign_binary_is_a_plain_failure(self):
        stderr = "Error: open /work/bundle.json: no such file or directory"
        _, verification = self.verify_with(FakeRunner(returncode=1, stderr=stderr))
        self.assertEqual(verification.error, f"signature verification failed: {stderr}")

    def test_other_docker_failures_are_plain_failures(self):
        for stderr in ("Error: invalid signature", "open /etc/ssl/cert.pem: no such file or directory",
                       "Error: /work/bundle.json: permission denied"):
            with self.subTest(stderr=stderr):
                _, verification = self.verify_with(FakeRunner(returncode=1, stderr=stderr), docker=True)
                self.assertEqual(verification.error, f"signature verification failed: {stderr}")

    def test_runner_os_error(self):
        error = PermissionError(errno.EACCES, "Permission denied", COSIGN)
        _, verification = self.verify_with(FakeRunner(error=error))
        self.assertFalse(verification.verified)
        self.assertEqual(verification.error, f"could not run cosign: {error}")
        self.assertEqual(verification.tool, COSIGN)
        self.assertEqual(verification.method, "keyless")

    def test_runner_missing_executable(self):
        error = FileNotFoundError(errno.ENOENT, "No such file or directory", "docker")
        _, verification = self.verify_with(FakeRunner(error=error), docker=True)
        self.assertFalse(verification.verified)
        self.assertEqual(verification.error, f"could not run cosign: {error}")
        self.assertEqual(verification.tool, PINNED_IMAGE)

    def test_runner_timeout(self):
        error = subprocess.TimeoutExpired([COSIGN, "verify-blob"], 300)
        _, verification = self.verify_with(FakeRunner(error=error))
        self.assertFalse(verification.verified)
        self.assertEqual(verification.error, f"could not run cosign: {error}")
        self.assertIn("timed out after 300 seconds", verification.error)

    def test_success_in_key_mode(self):
        key = self.tmp / "cosign.pub"
        key.write_bytes(b"key")
        _, verification = self.verify_with(FakeRunner(stdout="Verified OK"), FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY=str(key))
        self.assertTrue(verification.verified)
        self.assertEqual(verification.error, "")
        self.assertEqual(verification.method, "public key")


class MissingBundleTests(TrustTestCase):
    def test_missing_bundle_is_not_verified_and_cosign_never_runs(self):
        fetched = self.local_fetch(bundle=None)
        self.assertIsNotNone(fetched.bundle_error)
        verification = self.verifier().verify(fetched)
        self.assertFalse(verification.verified)
        self.assertEqual(verification.error, fetched.bundle_error)
        self.assertEqual(verification.method, "")
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(self.which.calls, [])

    def test_missing_bundle_without_a_recorded_error(self):
        directory = self.tmp / "work"
        directory.mkdir()
        fetched = FetchedManifest("m.json", directory, directory / "manifest.json", b"{}", make_manifest())
        verification = self.verifier().verify(fetched)
        self.assertEqual(verification, Verification(False, error="no signature bundle"))
        self.assertEqual(self.runner.calls, [])

    def test_missing_bundle_in_key_mode_does_not_copy_the_key(self):
        key = self.tmp / "cosign.pub"
        key.write_bytes(b"key")
        fetched = self.local_fetch(bundle=None)
        verification = self.verifier(FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY=str(key)).verify(fetched)
        self.assertFalse(verification.verified)
        self.assertEqual(self.runner.calls, [])
        self.assertFalse((fetched.directory / "trusted-key.pub").exists())

    def test_missing_bundle_with_a_mismatched_identity_still_reports_the_bundle(self):
        fetched = self.local_fetch(make_manifest(identity="someone@example.com"), bundle=None)
        verification = self.verifier().verify(fetched)
        self.assertEqual(verification.error, fetched.bundle_error)


class SmallPiecesTests(TrustTestCase):
    def test_verification_as_dict(self):
        verification = Verification(True, method="keyless", signer="s", issuer="i", tool="t", error="",
                                    details={"secret": "x"})
        self.assertEqual(verification.as_dict(), {"verified": True, "method": "keyless", "signer": "s",
                                                  "issuer": "i", "tool": "t", "error": ""})

    def test_verification_defaults(self):
        verification = Verification(False)
        self.assertEqual((verification.method, verification.signer, verification.issuer, verification.tool,
                          verification.error, verification.details), ("", "", "", "", "", {}))
        self.assertIsNot(Verification(False).details, verification.details)

    def test_remove_workdir(self):
        fetched = self.local_fetch()
        remove_workdir(fetched.directory)
        self.assertFalse(fetched.directory.exists())
        remove_workdir(fetched.directory)  # already gone: no error

    def test_public_names(self):
        self.assertEqual(set(trust.__all__), {"BUNDLE_ASSET", "FetchedManifest", "Verification", "VerificationError",
                                              "Verifier", "fetch_manifest", "remove_workdir"})
        self.assertEqual(trust.BUNDLE_ASSET, "future-agi-update-manifest.sigstore.json")


# ======================================================= review round 2 ==

class FakeCosignCertificate:
    """A runner that answers verify-blob the way cosign does for one Fulcio certificate.

    cosign checks every --certificate-* flag it is given against the
    certificate and nothing it is not given: a flag left out is never checked.
    """

    FLAGS = {
        "--certificate-identity": "identity",
        "--certificate-oidc-issuer": "issuer",
        "--certificate-github-workflow-repository": "repository",
        "--certificate-github-workflow-ref": "ref",
    }

    def __init__(self, **claims):
        self.claims = claims
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append(list(command))
        for flag, claim in self.FLAGS.items():
            if flag in command:
                expected = command[command.index(flag) + 1]
                if self.claims.get(claim) != expected:
                    return subprocess.CompletedProcess(list(command), 1, "", (
                        f"Error: failed to verify certificate identity: expected {claim} to be {expected!r}, "
                        f"got {self.claims.get(claim)!r}\n"))
        return subprocess.CompletedProcess(list(command), 0, "", "Verified OK\n")


class WorkflowClaimsPinnedTests(TrustTestCase):
    """Finding 2: the SAN names the called workflow file for every caller; the repository and ref are pinned too."""

    REPOSITORY = "--certificate-github-workflow-repository"
    REF = "--certificate-github-workflow-ref"

    def flag(self, argv, name):
        return argv[argv.index(name) + 1] if name in argv else None

    def test_default_identity_pins_this_repository_and_the_manifests_tag(self):
        for version in ("v1.2.3", "v4.5.6", "v2.0.0-beta.1"):
            with self.subTest(version=version):
                fetched = self.local_fetch(make_manifest(version))
                self.verifier().verify(fetched)
                argv = self.runner.argv
                self.assertEqual(self.flag(argv, self.REPOSITORY), "future-agi/future-agi")
                self.assertEqual(self.flag(argv, self.REF), f"refs/tags/{version}")
                self.assertLess(argv.index(self.REF), argv.index(str(fetched.manifest_path)))

    def test_the_docker_fallback_pins_them_too(self):
        fetched = self.local_fetch()
        self.verifier(which=self.docker_only()).verify(fetched)
        argv = self.runner.argv
        after_image = argv[argv.index(PINNED_IMAGE) + 1:]
        self.assertEqual(self.flag(after_image, self.REPOSITORY), "future-agi/future-agi")
        self.assertEqual(self.flag(after_image, self.REF), "refs/tags/v1.2.3")

    def test_a_signature_from_a_run_in_another_repository_is_refused(self):
        # What Fulcio issues when attacker/agi calls this repository's
        # release-update-manifest.yml@v1.2.3 from its own tag v1.2.3: the SAN is
        # the pinned identity, only the repository claim tells them apart.
        fetched = self.local_fetch()
        forged = FakeCosignCertificate(identity=PINNED_IDENTITY_V123, issuer=PINNED_ISSUER,
                                       repository="attacker/agi", ref="refs/tags/v1.2.3")
        verification = self.verifier(runner=forged).verify(fetched)
        self.assertFalse(verification.verified)
        self.assertIn("expected repository to be 'future-agi/future-agi'", verification.error)
        # The release workflow's own run still verifies.
        genuine = FakeCosignCertificate(identity=PINNED_IDENTITY_V123, issuer=PINNED_ISSUER,
                                        repository="future-agi/future-agi", ref="refs/tags/v1.2.3")
        verification = self.verifier(runner=genuine).verify(fetched)
        self.assertTrue(verification.verified, verification.error)

    def test_a_signature_from_another_ref_of_this_repository_is_refused(self):
        fetched = self.local_fetch()
        branch = FakeCosignCertificate(identity=PINNED_IDENTITY_V123, issuer=PINNED_ISSUER,
                                       repository="future-agi/future-agi", ref="refs/heads/main")
        self.assertFalse(self.verifier(runner=branch).verify(fetched).verified)

    def test_a_fork_identity_of_the_same_form_pins_the_forks_repository(self):
        template = "https://github.com/acme/agi-fork.v2/.github/workflows/publish.yaml@refs/tags/{version}"
        fetched = self.local_fetch(make_manifest("v4.5.6"))
        self.verifier(FUTURE_AGI_UPDATE_TRUSTED_IDENTITY=template).verify(fetched)
        self.assertEqual(self.flag(self.runner.argv, self.REPOSITORY), "acme/agi-fork.v2")
        self.assertEqual(self.flag(self.runner.argv, self.REF), "refs/tags/v4.5.6")

    def test_only_a_github_workflow_at_the_version_tag_adds_claims(self):
        fetched = self.local_fetch()
        self.verifier().verify(fetched)
        self.assertIn(self.REPOSITORY, self.runner.argv)
        key = self.tmp / "cosign.pub"
        key.write_bytes(b"key")
        self.verifier(FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY=str(key)).verify(fetched)
        self.assertNotIn(self.REPOSITORY, self.runner.argv)
        self.assertNotIn(self.REF, self.runner.argv)
        for identity in (
            "https://github.com/acme/agi/.github/workflows/publish.yml@refs/heads/main",
            "https://github.com/acme/agi/.github/workflows/publish.yml@refs/tags/v1.2.3",
            "https://github.com/acme/agi/.github/workflows/publish.yml@refs/tags/{version}-signed",
            "https://github.com/acme/agi/.github/workflows/nested/publish.yml@refs/tags/{version}",
            "https://github.com/acme/agi/actions/publish.yml@refs/tags/{version}",
            "https://gitlab.com/acme/agi//.gitlab-ci.yml@refs/tags/{version}",
            "https://github.example.com/acme/agi/.github/workflows/publish.yml@refs/tags/{version}",
            "https://github.com/-acme/agi/.github/workflows/publish.yml@refs/tags/{version}",
            "release@acme.example",
        ):
            with self.subTest(identity=identity):
                fetched = self.local_fetch()
                self.verifier(FUTURE_AGI_UPDATE_TRUSTED_IDENTITY=identity).verify(fetched)
                self.assertNotIn(self.REPOSITORY, self.runner.argv)
                self.assertNotIn(self.REF, self.runner.argv)
                self.assertIn(identity.replace("{version}", "v1.2.3"), self.runner.argv)

    def test_workflow_claims(self):
        default = load_settings(self.checkout, environ={}).get("FUTURE_AGI_UPDATE_TRUSTED_IDENTITY")
        self.assertEqual(trust.workflow_claims(default, "v1.53.0"), ("future-agi/future-agi", "refs/tags/v1.53.0"))
        self.assertIsNone(trust.workflow_claims(PINNED_IDENTITY_V123, "v1.2.3"))  # no {version} placeholder
        self.assertIsNone(trust.workflow_claims(default + " ", "v1.53.0"))


class CosignVersionGateTests(TrustTestCase):
    """Findings 28 and 36: a cosign on PATH older than v3 cannot read the v3 bundle the release is signed with."""

    def assert_docker(self, verification):
        self.assertEqual(self.runner.argv[:2], ["docker", "run"])
        self.assertIn(PINNED_IMAGE, self.runner.argv)
        self.assertEqual(verification.tool, PINNED_IMAGE)

    def test_cosign_v3_on_path_is_used_and_asked_once(self):
        probe = FakeProbe("v3.1.3")
        verifier = self.verifier(probe=probe)
        fetched = self.local_fetch()
        self.assertTrue(verifier.verify(fetched).verified)
        self.assertTrue(verifier.verify(fetched).verified)
        self.assertEqual([call[0][0] for call in self.runner.calls], [COSIGN, COSIGN])
        self.assertEqual(probe.calls, [([COSIGN, "version", "--json"],
                                        {"capture_output": True, "text": True, "check": False, "timeout": 60})])
        self.assertEqual(verifier.cosign_version(COSIGN), ((3, 1, 3), "v3.1.3"))
        self.assertEqual(len(probe.calls), 1)
        self.assertEqual(self.which.calls, ["cosign", "cosign"])
        for version in ("v3.0.0", "3.0.2", "v3.2.0-rc.1", "v4.0.0", "v12.1.0"):
            with self.subTest(version=version):
                probe = FakeProbe(version)
                verification = self.verifier(probe=probe).verify(self.local_fetch())
                self.assertEqual(self.runner.argv[0], COSIGN)
                self.assertEqual(verification.tool, COSIGN)
                self.assertEqual(len(probe.calls), 1)

    def test_cosign_2_on_path_falls_back_to_the_pinned_image(self):
        for version in ("v2.6.1", "v2.4.1", "v2.0.0", "v1.13.6", "2.2.4"):
            with self.subTest(version=version):
                fetched = self.local_fetch()
                verification = self.verifier(probe=FakeProbe(version)).verify(fetched)
                self.assert_docker(verification)
                self.assertTrue(verification.verified)
                self.assertEqual(self.which.calls, ["cosign", "docker"])
                self.assertEqual(verification.details, {"note": (
                    f"bin/update used {PINNED_IMAGE} because the cosign on PATH, {COSIGN}, is {version}, older than v3")})

    def test_cosign_2_without_docker_is_a_clear_error_naming_the_version(self):
        fetched = self.local_fetch()
        verification = self.verifier(probe=FakeProbe("v2.4.1"), which=FakeWhich({"cosign": COSIGN})).verify(fetched)
        self.assertFalse(verification.verified)
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(verification.method, "keyless")
        self.assertEqual(verification.error, (
            f"cosign {COSIGN} is v2.4.1, which cannot verify the release signature: bin/update needs cosign v3 or "
            "later. Upgrade cosign (https://docs.sigstore.dev/cosign/system_config/installation/), or make Docker "
            f"available so bin/update runs cosign from {PINNED_IMAGE}"))

    def test_the_text_form_is_read_when_cosign_has_no_json_output(self):
        probe = FakeProbe("v1.6.0", json_form=False)
        verification = self.verifier(probe=probe).verify(self.local_fetch())
        self.assert_docker(verification)
        self.assertEqual([call[0] for call in probe.calls], [[COSIGN, "version", "--json"], [COSIGN, "version"]])
        probe = FakeProbe("v3.0.1", json_form=False)
        verification = self.verifier(probe=probe).verify(self.local_fetch())
        self.assertEqual(verification.tool, COSIGN)

    def test_a_version_that_is_not_a_release_number_prefers_the_pinned_image(self):
        for stdout in ('{"gitVersion": "(devel)"}', '{"gitVersion": ""}', "", "garbage", "[" * 5000, "[]"):
            with self.subTest(stdout=stdout[:20]):
                verification = self.verifier(probe=FakeProbe(stdout=stdout)).verify(self.local_fetch())
                self.assert_docker(verification)
                self.assertTrue(verification.verified)

    def test_a_cosign_that_does_not_run_prefers_the_pinned_image(self):
        for error in (FileNotFoundError(errno.ENOENT, "No such file or directory", COSIGN),
                      PermissionError(errno.EACCES, "Permission denied", COSIGN),
                      OSError(errno.ENOEXEC, "Exec format error", COSIGN),
                      subprocess.TimeoutExpired([COSIGN, "version", "--json"], 60)):
            with self.subTest(error=type(error).__name__):
                probe = FakeProbe(error=error)
                verification = self.verifier(probe=probe).verify(self.local_fetch())
                self.assert_docker(verification)
                self.assertEqual(len(probe.calls), 1)
                self.assertIn(f"because the cosign on PATH, {COSIGN}, did not report its version",
                              verification.details["note"])

    def test_an_unreadable_version_without_docker_still_tries_the_only_cosign_and_says_why_on_failure(self):
        which = FakeWhich({"cosign": COSIGN})
        fetched = self.local_fetch()
        verifier = self.verifier(probe=FakeProbe(stdout='{"gitVersion": "(devel)"}'), which=which,
                                 runner=FakeRunner(returncode=1, stderr="Error: bundle does not contain cert"))
        verification = verifier.verify(fetched)
        self.assertEqual(self.runner.argv[0], COSIGN)
        self.assertEqual(verification.error, (
            "signature verification failed: Error: bundle does not contain cert "
            f"(cosign {COSIGN} reports version '(devel)', not a release number; "
            "bin/update needs cosign v3 or later)"))

    def test_why_the_image_ran_is_part_of_a_failure(self):
        fetched = self.local_fetch()
        verifier = self.verifier(probe=FakeProbe("v2.4.1"), runner=FakeRunner(returncode=1, stderr="Error: no match"))
        verification = verifier.verify(fetched)
        self.assertEqual(verification.error, (
            "signature verification failed: Error: no match "
            f"(bin/update used {PINNED_IMAGE} because the cosign on PATH, {COSIGN}, is v2.4.1, older than v3)"))

    def test_the_default_probe_runs_a_real_process_even_with_a_fake_runner(self):
        self.assertIs(Verifier(self.settings()).probe, subprocess.run)
        self.assertIs(Verifier(self.settings(), runner=FakeRunner()).probe, subprocess.run)


class CosignContainerAccessTests(TrustTestCase):
    """Findings 9 and 18: rootless Docker, userns-remap and SELinux must be able to read the verification copy."""

    def test_the_copies_are_world_readable_and_owner_writable_whatever_the_umask(self):
        for umask in (0o077, 0o027, 0o022, 0o000):
            with self.subTest(umask=oct(umask)):
                self.umask(umask)
                key = self.tmp / f"key-{umask}.pub"
                key.write_bytes(b"key")
                fetched = self.local_fetch()
                self.verifier(FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY=str(key)).verify(fetched)
                self.assertEqual(self.mode(fetched.directory), 0o755)
                for path in (fetched.manifest_path, fetched.bundle_path, fetched.directory / "trusted-key.pub"):
                    self.assertEqual(self.mode(path), 0o644, path.name)

    def test_an_existing_workdir_is_opened_up_too(self):
        workdir = self.workdir()
        workdir.mkdir(parents=True, mode=0o700)
        os.chmod(str(workdir), 0o700)
        path = self.write_release(self.tmp / "release", make_manifest())
        fetch_manifest(str(path), workdir, cwd=self.caller)
        self.assertEqual(self.mode(workdir), 0o755)

    def test_the_mount_is_read_only_and_relabelled_and_the_user_is_kept(self):
        fetched = self.local_fetch()
        self.verifier(which=self.docker_only()).verify(fetched)
        argv = self.runner.argv
        self.assertEqual([argv[i + 1] for i, arg in enumerate(argv) if arg == "-v"], [f"{fetched.directory}:/work:ro,z"])
        self.assertEqual(argv[argv.index("--user") + 1], f"{os.getuid()}:{os.getgid()}")
        self.assertIn("--read-only", argv)


class CosignContainerProxyTests(TrustTestCase):
    """Finding 29: keyless verification in the container fetches Sigstore's trusted root through the host's proxy."""

    PROXIES = {"HTTPS_PROXY": "http://user:secret@proxy.internal:3128", "HTTP_PROXY": "http://proxy.internal:3128",
               "NO_PROXY": "localhost,.internal", "https_proxy": "http://proxy.internal:3128",
               "http_proxy": "http://proxy.internal:3128", "no_proxy": "localhost"}

    def forwarded(self, argv):
        before_image = argv[:argv.index(PINNED_IMAGE)]
        return [before_image[i + 1] for i, arg in enumerate(before_image) if arg == "-e"]

    def test_every_proxy_variable_set_is_forwarded_by_name(self):
        fetched = self.local_fetch()
        self.verifier(which=self.docker_only(), environ=dict(self.PROXIES, PATH="/usr/bin")).verify(fetched)
        argv = self.runner.argv
        self.assertEqual(self.forwarded(argv), ["HOME=/tmp", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy",
                                                "http_proxy", "no_proxy"])
        self.assertFalse([arg for arg in argv if "secret" in arg or "proxy.internal" in arg], argv)
        self.assertNotIn("--network", argv)
        # Offline verification has no network, so nothing to proxy. Round 4
        # (S20): offline needs a public key.
        key = self.tmp / "cosign.pub"
        key.write_bytes(b"key")
        self.verifier(which=self.docker_only(), environ=self.PROXIES, FUTURE_AGI_UPDATE_COSIGN_OFFLINE="true",
                      FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY=str(key)).verify(fetched)
        self.assertEqual(self.forwarded(self.runner.argv), ["HOME=/tmp"])
        self.assertIn("--network", self.runner.argv)
        # A cosign binary inherits the environment: no extra arguments.
        self.verifier(environ=self.PROXIES).verify(fetched)
        self.assertEqual(self.runner.argv[0], COSIGN)
        self.assertNotIn("-e", self.runner.argv)

    def test_only_the_variables_that_are_set_are_forwarded(self):
        fetched = self.local_fetch()
        self.verifier(which=self.docker_only(), environ={"https_proxy": "http://p:1", "NO_PROXY": ""}).verify(fetched)
        self.assertEqual(self.forwarded(self.runner.argv), ["HOME=/tmp", "NO_PROXY", "https_proxy"])
        self.verifier(which=self.docker_only(), environ={"PATH": "/usr/bin", "ALL_PROXY": "socks5://x"}).verify(fetched)
        self.assertEqual(self.forwarded(self.runner.argv), ["HOME=/tmp"])

    def test_the_process_environment_is_the_default(self):
        with mock.patch.dict(os.environ, {"HTTPS_PROXY": "http://proxy.internal:3128",
                                          "DOCKER_CONFIG": str(self.tmp / "docker-config")}):
            runner = FakeRunner()
            Verifier(self.settings(), runner=runner, which=self.docker_only()).verify(self.local_fetch())
        self.assertIn("HTTPS_PROXY", self.forwarded(runner.argv))


class DockerRunFailureTests(TrustTestCase):
    """Finding 30: `docker run` failing before cosign ran is not a failed signature."""

    ADVICE = ("Make sure Docker can pull and run that image, or install cosign v3 or later "
              "(https://docs.sigstore.dev/cosign/system_config/installation/)")

    def run_docker(self, returncode, stderr, stdout=""):
        fetched = self.local_fetch()
        runner = FakeRunner(returncode=returncode, stderr=stderr, stdout=stdout)
        return self.verifier(which=self.docker_only(), runner=runner).verify(fetched)

    def test_docker_cli_and_daemon_failures(self):
        cases = (
            (125, "docker: Error response from daemon: No such image: ghcr.io/sigstore/cosign/cosign:v3.1.3\n\n"
                  "Run 'docker run --help' for more information\n",
             "Error response from daemon: No such image: ghcr.io/sigstore/cosign/cosign:v3.1.3"),
            (125, "Unable to find image 'ghcr.io/sigstore/cosign/cosign:v3.1.3' locally\n"
                  "docker: Error response from daemon: failed to resolve reference: dial tcp: no such host\n\n"
                  "See 'docker run --help'.\n",
             "Error response from daemon: failed to resolve reference: dial tcp: no such host"),
            (1, "permission denied while trying to connect to the Docker daemon socket at unix:///var/run/docker.sock: "
                "Head \"http://%2Fvar%2Frun%2Fdocker.sock/_ping\": dial unix /var/run/docker.sock: connect: "
                "permission denied\n",
             "permission denied while trying to connect to the Docker daemon socket at unix:///var/run/docker.sock: "
             "Head \"http://%2Fvar%2Frun%2Fdocker.sock/_ping\": dial unix /var/run/docker.sock: connect: "
             "permission denied"),
            (1, "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?\n",
             "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?"),
            (1, "failed to connect to the docker API at unix:///run/user/1000/docker.sock; check if the path is "
                "correct and if the daemon is running: dial unix /run/user/1000/docker.sock: connect: no such file "
                "or directory\n",
             "failed to connect to the docker API at unix:///run/user/1000/docker.sock; check if the path is "
             "correct and if the daemon is running: dial unix /run/user/1000/docker.sock: connect: no such file "
             "or directory"),
            (127, "docker: Error response from daemon: failed to create task for container: exec: \"cosign\": "
                  "executable file not found in $PATH\n\nRun 'docker run --help' for more information\n",
             "Error response from daemon: failed to create task for container: exec: \"cosign\": "
             "executable file not found in $PATH"),
            (125, "Trying to pull ghcr.io/sigstore/cosign/cosign:v3.1.3...\nError: initializing source "
                  "docker://ghcr.io/sigstore/cosign/cosign:v3.1.3: pinging container registry ghcr.io: timeout\n",
             "Error: initializing source docker://ghcr.io/sigstore/cosign/cosign:v3.1.3: pinging container "
             "registry ghcr.io: timeout"),
            (126, "", "docker run exited with status 126"),
            (125, "\n\nRun 'docker run --help' for more information\n", "docker run exited with status 125"),
        )
        for returncode, stderr, detail in cases:
            with self.subTest(returncode=returncode, stderr=stderr[:40]):
                verification = self.run_docker(returncode, stderr)
                self.assertFalse(verification.verified)
                self.assertEqual(verification.tool, PINNED_IMAGE)
                self.assertEqual(verification.error, f"could not run cosign from {PINNED_IMAGE}: {detail}. {self.ADVICE}")
                self.assertNotIn("signature verification failed", verification.error)
        # A pull that worked does not hide cosign's own failure after it.
        stderr = ("Unable to find image 'ghcr.io/sigstore/cosign/cosign:v3.1.3' locally\n"
                  "v3.1.3: Pulling from sigstore/cosign/cosign\n0123abcd: Pull complete\n"
                  "Digest: sha256:" + "9" * 64 + "\nStatus: Downloaded newer image for ghcr.io/sigstore/cosign/cosign\n"
                  "Error: none of the expected identities matched what was in the certificate\n"
                  "error during command execution: none of the expected identities matched\n")
        verification = self.run_docker(1, stderr)
        self.assertEqual(verification.error,
                         "signature verification failed: error during command execution: none of the expected "
                         "identities matched")
        # A cosign binary is not docker: whatever it exits with is its own answer.
        fetched = self.local_fetch()
        verification = self.verifier(runner=FakeRunner(returncode=125, stderr="docker: something")).verify(fetched)
        self.assertEqual(verification.error, "signature verification failed: docker: something")


class UntrustedJsonTests(TrustTestCase):
    """Finding 44: no traceback from a manifest or release list json cannot parse."""

    API_URL = GitHubLatestFallbackTests.API_URL
    MISSING = GitHubLatestFallbackTests.MISSING
    DEEP = b"[" * 200000
    LONG_INT = b'{"schema_version": ' + b"9" * 5000 + b"}"

    def setUp(self):
        super().setUp()
        self.release_dir = self.tmp / "hostile"
        self.release_dir.mkdir()

    def fetch_local(self, body):
        path = self.release_dir / "m.json"
        path.write_bytes(body)
        return fetch_manifest(str(path), self.workdir(), cwd=self.caller)

    def test_a_deeply_nested_manifest_is_a_manifest_error(self):
        for body in (self.DEEP, b'{"a":' * 200000 + b"1" + b"}" * 200000):
            with self.subTest(size=len(body)):
                with self.assertRaises(ManifestError) as caught:
                    self.fetch_local(body)
                self.assertTrue(str(caught.exception).startswith("manifest is not valid JSON: "), caught.exception)

    def test_the_nesting_message(self):
        with self.assertRaises(ManifestError) as caught:
            self.fetch_local(self.DEEP)
        self.assertEqual(str(caught.exception), "manifest is not valid JSON: it is nested too deeply")
        self.assertIsInstance(caught.exception.__cause__, RecursionError)

    @unittest.skipUnless(hasattr(sys, "get_int_max_str_digits"), "this Python has no integer string limit")
    def test_an_integer_too_long_to_convert_is_a_manifest_error(self):
        with self.assertRaises(ManifestError) as caught:
            self.fetch_local(self.LONG_INT)
        message = str(caught.exception)
        self.assertTrue(message.startswith("manifest is not valid JSON: Exceeds the limit"), message)
        self.assertNotIn("set_int_max_str_digits", message)

    def test_a_hostile_https_manifest_is_a_manifest_error(self):
        source = "https://mirror.example.com/future-agi-update-manifest.json"
        self.web.serve(source, self.DEEP)
        with self.assertRaisesRegex(ManifestError, "^manifest is not valid JSON: it is nested too deeply$"):
            fetch_manifest(source, self.workdir(), cwd=self.caller)

    def test_a_hostile_release_list_is_a_manifest_error(self):
        bodies = [("nested", self.DEEP, "it is nested too deeply")]
        if hasattr(sys, "get_int_max_str_digits"):
            bodies.append(("long integer", b"[" + b"7" * 5000 + b"]", "Exceeds the limit"))
        for name, body, reason in bodies:
            with self.subTest(name):
                self.web.serve(self.API_URL, body)
                with self.assertRaises(ManifestError) as caught:
                    fetch_manifest(LATEST_URL, self.workdir(), cwd=self.caller)
                message = str(caught.exception)
                self.assertTrue(message.startswith(self.MISSING + ". Looking for an older release that has one "
                                                   f"failed: the release list {self.API_URL} is not valid JSON: "),
                                message)
                self.assertIn(reason, message)


class TrustSpecialFileIntegrationTests(unittest.TestCase):
    """_read_local names every kind of file that is not a regular one."""

    def test_every_kind_has_a_name(self):
        for mode, kind in ((stat.S_IFREG | 0o644, None), (stat.S_IFDIR | 0o755, "a directory"),
                           (stat.S_IFIFO | 0o600, "a named pipe"), (stat.S_IFCHR | 0o600, "a device"),
                           (stat.S_IFBLK | 0o600, "a device"), (stat.S_IFSOCK | 0o600, "a socket"),
                           (0, "a special file")):
            with self.subTest(kind=kind):
                self.assertEqual(trust._special_kind(mode), kind)

    @unittest.skipUnless(hasattr(socket, "AF_UNIX"), "needs Unix sockets")
    def test_a_socket_is_refused_before_it_is_opened(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            path = Path(tmp) / "m.sock"
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.addCleanup(server.close)
            server.bind(str(path))
            with self.assertRaisesRegex(ManifestError, "it is a socket, not a regular file$"):
                trust._read_local(path, "manifest")



# ------------------------------------------------------------ integration --

class WritePrivateWithoutFchmodTests(unittest.TestCase):
    """Where os.fchmod is missing, the copy cosign reads is still readable by any container uid."""

    def test_the_copy_is_0644_under_a_umask_that_allows_it(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "manifest.json"
        fchmod = os.fchmod
        del os.fchmod
        umask = os.umask(0o022)
        try:
            trust._write_private(path, b"{}")
        finally:
            os.fchmod = fchmod
            os.umask(umask)
        self.assertEqual(path.read_bytes(), b"{}")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)



# ================================================================ round 3 ==

class NewerReleaseTests(TrustTestCase):
    """R6: FetchedManifest.newer_release is set only when a newer platform release is still waiting for its manifest.

    The plan tells the operator to wait for that manifest only then; otherwise
    (an ad-hoc 'demo' latest, an older backport latest) the release whose
    manifest was used is the newest one, and no further manifest is coming.
    """

    API_URL = GitHubLatestFallbackTests.API_URL
    download = GitHubLatestFallbackTests.download
    serve_api = GitHubLatestFallbackTests.serve_api
    serve_release = GitHubLatestFallbackTests.serve_release

    def test_the_newest_release_without_a_manifest_is_the_newer_release(self):
        self.serve_api([release("v1.53.0", "source.tar.gz"), release("v1.52.0", MANIFEST_ASSET, BUNDLE_ASSET)])
        self.serve_release("v1.52.0")
        fetched = fetch_manifest(LATEST_URL, self.workdir(), cwd=self.caller)
        self.assertEqual(fetched.data["version"], "v1.52.0")
        self.assertEqual(fetched.newer_release, "v1.53.0")
        self.assertEqual(fetched.note, "the newest release v1.53.0 has no update manifest yet; "
                                       "using v1.52.0, the newest release that has one")

    def test_a_demo_latest_release_is_not_a_newer_release(self):
        self.serve_api([release("demo-2026-10-19", "demo.zip"), release("demo"),
                        release("v1.52.0", MANIFEST_ASSET, BUNDLE_ASSET), release("v1.51.0", MANIFEST_ASSET)])
        self.serve_release("v1.52.0")
        fetched = fetch_manifest(LATEST_URL, self.workdir(), cwd=self.caller)
        self.assertEqual(fetched.data["version"], "v1.52.0")
        self.assertIsNone(fetched.newer_release)
        self.assertEqual(fetched.note, "the latest GitHub release has no update manifest; "
                                       "using v1.52.0, the newest release that has one")

    def test_a_backport_latest_older_than_the_chosen_release_is_not_a_newer_release(self):
        # v1.51.3 was published last (GitHub's latest) and waits for its manifest.
        self.serve_api([release("v1.51.3", "source.tar.gz"), release("v1.52.0", MANIFEST_ASSET, BUNDLE_ASSET),
                        release("v1.51.2", MANIFEST_ASSET, BUNDLE_ASSET)])
        self.serve_release("v1.52.0")
        fetched = fetch_manifest(LATEST_URL, self.workdir(), cwd=self.caller)
        self.assertEqual(fetched.source, self.download("v1.52.0", MANIFEST_ASSET))
        self.assertIsNone(fetched.newer_release)
        self.assertEqual(fetched.note, "the latest GitHub release has no update manifest; "
                                       "using v1.52.0, the newest release that has one")

    def test_drafts_and_prereleases_are_never_a_newer_release(self):
        self.serve_api([release("v2.0.0", draft=True), release("v1.60.0", prerelease=True), release("v1.53.0-rc.1"),
                        release("v1.52.0", MANIFEST_ASSET, BUNDLE_ASSET)])
        self.serve_release("v1.52.0")
        self.assertIsNone(fetch_manifest(LATEST_URL, self.workdir(), cwd=self.caller).newer_release)

    def test_a_manifest_at_least_as_new_as_the_newest_tag_has_no_newer_release(self):
        # The chosen release's file names another version: nothing newer than it is waiting.
        for inside in ("v1.53.0", "v1.54.0"):
            with self.subTest(inside=inside):
                self.serve_api([release("v1.53.0"), release("v1.52.0", MANIFEST_ASSET, BUNDLE_ASSET)])
                self.web.serve(self.download("v1.52.0", MANIFEST_ASSET), encode(make_manifest(inside)))
                self.web.serve(self.download("v1.52.0", BUNDLE_ASSET), BUNDLE_BYTES)
                fetched = fetch_manifest(LATEST_URL, self.workdir(), cwd=self.caller)
                self.assertEqual(fetched.data["version"], inside)
                self.assertIsNone(fetched.newer_release)

    def test_without_the_fallback_there_is_no_newer_release(self):
        self.web.serve(LATEST_URL, encode(make_manifest("v1.52.0")))
        self.web.serve(self.download("v1.52.0", BUNDLE_ASSET), BUNDLE_BYTES)
        fetched = fetch_manifest(LATEST_URL, self.workdir(), cwd=self.caller)
        self.assertIsNone(fetched.note)
        self.assertIsNone(fetched.newer_release)
        self.assertNotIn(self.API_URL, self.web.urls)
        explicit = self.download("v1.51.0", MANIFEST_ASSET)
        self.web.serve(explicit, encode(make_manifest("v1.51.0")))
        self.assertIsNone(fetch_manifest(explicit, self.workdir(), cwd=self.caller).newer_release)
        self.assertIsNone(self.local_fetch().newer_release)

    def test_the_field_defaults_to_none(self):
        directory = self.tmp / "made"
        fetched = FetchedManifest("m.json", directory, directory / "manifest.json", b"{}", make_manifest())
        self.assertIsNone(fetched.newer_release)


class CosignImageNoteTests(TrustTestCase):
    """R7: why bin/update chose the cosign image is said as a choice, and only once cosign ran in it."""

    INSTALL = "https://docs.sigstore.dev/cosign/system_config/installation/"
    PULL_DENIED = ("docker: Error response from daemon: pull access denied for ghcr.io/sigstore/cosign/cosign, "
                   "repository does not exist or may require 'docker login': denied: requested access to the "
                   "resource is denied\n\nRun 'docker run --help' for more information\n")
    DENIED = ("Error response from daemon: pull access denied for ghcr.io/sigstore/cosign/cosign, repository does "
              "not exist or may require 'docker login': denied: requested access to the resource is denied")
    # (probe, why the image was chosen, what the cosign on PATH is)
    HOST_COSIGNS = (
        (lambda: FakeProbe("v2.4.1"), "is v2.4.1, older than v3", "is v2.4.1"),
        (lambda: FakeProbe(stdout='{"gitVersion": "(devel)"}'), "reports version '(devel)', not a release number",
         "reports version '(devel)'"),
        (lambda: FakeProbe(error=FileNotFoundError(errno.ENOENT, "No such file or directory", COSIGN)),
         "did not report its version", "did not report its version"),
    )

    def test_the_note_is_the_reason_the_image_was_chosen(self):
        for probe, reason, _ in self.HOST_COSIGNS:
            with self.subTest(reason=reason):
                verification = self.verifier(probe=probe()).verify(self.local_fetch())
                self.assertTrue(verification.verified)
                self.assertEqual(verification.details, {
                    "note": f"bin/update used {PINNED_IMAGE} because the cosign on PATH, {COSIGN}, {reason}"})
                self.assertNotIn("cosign ran", verification.details["note"])

    def test_a_docker_run_that_failed_does_not_say_cosign_ran(self):
        for probe, reason, state in self.HOST_COSIGNS:
            with self.subTest(reason=reason):
                runner = FakeRunner(returncode=125, stderr=self.PULL_DENIED)
                verification = self.verifier(probe=probe(), runner=runner).verify(self.local_fetch())
                self.assertFalse(verification.verified)
                self.assertEqual(verification.tool, PINNED_IMAGE)
                self.assertEqual(verification.error, (
                    f"could not run cosign from {PINNED_IMAGE}: {self.DENIED}. Make sure Docker can pull and run that "
                    f"image, or replace the cosign on PATH, {COSIGN}, which {state}, with cosign v3 or later "
                    f"({self.INSTALL})"))
                self.assertNotIn("ran from", verification.error)
                self.assertNotIn("bin/update used", verification.error)
                self.assertEqual(verification.details, {})

    def test_without_a_cosign_on_path_the_docker_failure_says_install(self):
        runner = FakeRunner(returncode=125, stderr=self.PULL_DENIED)
        verification = self.verifier(which=self.docker_only(), runner=runner).verify(self.local_fetch())
        self.assertEqual(verification.error, (
            f"could not run cosign from {PINNED_IMAGE}: {self.DENIED}. Make sure Docker can pull and run that image, "
            f"or install cosign v3 or later ({self.INSTALL})"))

    def test_docker_that_cannot_be_started_has_no_note(self):
        for error in (FileNotFoundError(errno.ENOENT, "No such file or directory", "docker"),
                      PermissionError(errno.EACCES, "Permission denied", "docker"),
                      subprocess.TimeoutExpired(["docker", "run"], 300)):
            with self.subTest(error=type(error).__name__):
                verification = self.verifier(probe=FakeProbe("v2.4.1"), runner=FakeRunner(error=error)).verify(
                    self.local_fetch())
                self.assertEqual(verification.error, f"could not run cosign: {error}")

    def test_the_only_cosign_still_says_why_it_may_fail_when_it_cannot_run(self):
        error = OSError(errno.ENOEXEC, "Exec format error", COSIGN)
        verifier = self.verifier(probe=FakeProbe(stdout='{"gitVersion": "(devel)"}'), which=FakeWhich({"cosign": COSIGN}),
                                 runner=FakeRunner(error=error))
        verification = verifier.verify(self.local_fetch())
        self.assertEqual(verification.error, (
            f"could not run cosign: {error} (cosign {COSIGN} reports version '(devel)', not a release number; "
            "bin/update needs cosign v3 or later)"))

    def test_failures_after_cosign_ran_in_the_image_keep_the_reason(self):
        note = f"(bin/update used {PINNED_IMAGE} because the cosign on PATH, {COSIGN}, is v2.4.1, older than v3)"
        fetched = self.local_fetch()
        runner = FakeRunner(returncode=1, stderr="Error: open /work/bundle.json: no such file or directory")
        verification = self.verifier(probe=FakeProbe("v2.4.1"), runner=runner).verify(fetched)
        self.assertEqual(verification.error, (
            f"Docker cannot see {fetched.directory}, so cosign (run from {PINNED_IMAGE}) cannot read the manifest. "
            "Share the checkout with the Docker VM (./bin/install checks the same thing) or install cosign v3 or "
            f"later {note}"))


class CosignContainerProxyReachabilityTests(TrustTestCase):
    """R8: the cosign container gets only a proxy it can reach, and a proxy failure says what to do."""

    INSTALL = "https://docs.sigstore.dev/cosign/system_config/installation/"
    LOOPBACK = ("http://127.0.0.1:3128", "127.0.0.1:3128", "http://localhost:3128", "HTTP://LOCALHOST:3128",
                "localhost:3128", "http://user:secret@127.0.0.1:3128", "http://127.1.2.3:8080", "http://[::1]:3128",
                "[::1]:3128", "socks5://127.0.0.1:1080", "http://0.0.0.0:3128", "0.0.0.0:3128", "http://[::]:3128",
                "http://localhost.:3128", "http://proxy.localhost:3128", "http://[::ffff:127.0.0.1]:3128",
                " http://127.0.0.1:3128 ")
    REACHABLE = ("http://proxy.internal:3128", "http://10.0.0.5:3128", "http://172.17.0.1:3128",
                 "http://host.docker.internal:3128", "http://[fd00::1]:3128", "http://128.0.0.1:3128",
                 "http://localhost-proxy:3128", "http://mylocalhost:3128", "http://user:secret@proxy.corp:8080", "",
                 "http://[::1", "::")
    TUF_PROXY = ("Error: getting trusted root: updating local metadata and targets: error updating to TUF remote "
                 "mirror: tuf: failed to download 12.root.json: Get \"https://tuf-repo-cdn.sigstore.dev/12.root.json\": "
                 "proxyconnect tcp: dial tcp 127.0.0.1:3128: connect: connection refused")
    TUF_DIRECT = ("Error: getting trusted root: updating local metadata and targets: error updating to TUF remote "
                  "mirror: tuf: failed to download 12.root.json: Get \"https://tuf-repo-cdn.sigstore.dev/12.root.json\": "
                  "dial tcp: lookup tuf-repo-cdn.sigstore.dev on 192.168.65.7:53: no such host")

    def forwarded(self, argv):
        return CosignContainerProxyTests.forwarded(self, argv)

    def home(self):
        return self.tmp / "home"

    def docker_config(self, config, directory=None):
        directory = directory or self.home() / ".docker"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "config.json").write_text(config if isinstance(config, str) else json.dumps(config))
        return directory / "config.json"

    def run_docker(self, environ, **kwargs):
        verifier = self.verifier(which=kwargs.pop("which", self.docker_only()), environ=environ, **kwargs)
        return verifier.verify(self.local_fetch())

    def advice(self, config, cosign="install cosign v3 or later"):
        return (f"Give containers a proxy they can reach in the \"proxies\" section of {config} "
                f"(https://docs.docker.com/engine/cli/proxy/), or {cosign} ({self.INSTALL}): bin/update runs a "
                "cosign v3 on PATH directly, with this host's proxy settings")

    def test_a_loopback_proxy_is_not_forwarded(self):
        for value in self.LOOPBACK:
            with self.subTest(value=value):
                self.run_docker({"HTTPS_PROXY": value, "NO_PROXY": "localhost,127.0.0.1"})
                self.assertEqual(self.forwarded(self.runner.argv), ["HOME=/tmp", "NO_PROXY"])

    def test_a_proxy_the_container_can_reach_is_still_forwarded(self):
        for value in self.REACHABLE:
            with self.subTest(value=value):
                self.run_docker({"HTTPS_PROXY": value})
                self.assertEqual(self.forwarded(self.runner.argv), ["HOME=/tmp", "HTTPS_PROXY"])

    def test_each_variable_is_decided_on_its_own(self):
        self.run_docker({"HTTPS_PROXY": "http://127.0.0.1:3128", "https_proxy": "http://proxy.corp:3128",
                         "HTTP_PROXY": "localhost:3128", "http_proxy": "http://proxy.corp:3128",
                         "NO_PROXY": "127.0.0.1", "no_proxy": "localhost"})
        self.assertEqual(self.forwarded(self.runner.argv),
                         ["HOME=/tmp", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy"])
        self.assertFalse([arg for arg in self.runner.argv if "proxy.corp" in arg or "3128" in arg])

    def test_proxies_in_docker_config_are_left_to_docker(self):
        environ = {"HTTPS_PROXY": "http://proxy.corp:3128", "http_proxy": "http://proxy.corp:3128", "NO_PROXY": "x"}
        # Round 4 (S33-S35): only the kinds of proxy the entry for this daemon
        # sets are left to Docker; the host's other variables still go.
        for name, config, expected in (
            ("default https", {"proxies": {"default": {"httpsProxy": "http://host.docker.internal:3128"}}},
             ["HOME=/tmp", "NO_PROXY", "http_proxy"]),
            ("default http", {"auths": {"ghcr.io": {"auth": "c2VjcmV0"}},
                              "proxies": {"default": {"httpProxy": "http://172.17.0.1:3128", "noProxy": "*.corp"}}},
             ["HOME=/tmp", "HTTPS_PROXY"]),
            ("one daemon", {"proxies": {"tcp://docker.corp:2376": {"httpsProxy": "http://172.17.0.1:3128"}}},
             ["HOME=/tmp", "HTTPS_PROXY", "NO_PROXY", "http_proxy"]),
        ):
            with self.subTest(name):
                self.docker_config(config)
                self.run_docker(environ)
                self.assertEqual(self.forwarded(self.runner.argv), expected)

    def test_docker_config_is_honoured(self):
        environ = {"HTTPS_PROXY": "http://proxy.corp:3128"}
        elsewhere = self.tmp / "docker-config"
        self.docker_config({"proxies": {"default": {"httpsProxy": "http://172.17.0.1:3128"}}}, elsewhere)
        self.run_docker(dict(environ, DOCKER_CONFIG=str(elsewhere)))
        self.assertEqual(self.forwarded(self.runner.argv), ["HOME=/tmp"])
        # DOCKER_CONFIG wins over ~/.docker, as for the docker CLI.
        self.docker_config({"proxies": {"default": {"httpsProxy": "http://172.17.0.1:3128"}}})
        self.docker_config({}, self.tmp / "empty-config")
        self.run_docker(dict(environ, DOCKER_CONFIG=str(self.tmp / "empty-config")))
        self.assertEqual(self.forwarded(self.runner.argv), ["HOME=/tmp", "HTTPS_PROXY"])
        # An empty DOCKER_CONFIG is unset.
        self.run_docker(dict(environ, DOCKER_CONFIG=""))
        self.assertEqual(self.forwarded(self.runner.argv), ["HOME=/tmp"])

    def test_docker_config_without_container_proxies_keeps_forwarding(self):
        environ = {"HTTPS_PROXY": "http://proxy.corp:3128"}
        self.run_docker(environ)  # no config.json at all
        self.assertEqual(self.forwarded(self.runner.argv), ["HOME=/tmp", "HTTPS_PROXY"])
        for config in ({}, [], {"auths": {}}, {"proxies": {}}, {"proxies": "nope"}, {"proxies": {"default": "x"}},
                       {"proxies": {"default": {"noProxy": "*.corp", "allProxy": "socks5://172.17.0.1:1080"}}},
                       {"proxies": {"default": {"httpsProxy": "", "httpProxy": "  "}}},
                       {"proxies": {"default": {"httpsProxy": 3128}}}, "{not json", "[" * 100000):
            with self.subTest(config=str(config)[:40]):
                self.docker_config(config)
                self.run_docker(environ)
                self.assertEqual(self.forwarded(self.runner.argv), ["HOME=/tmp", "HTTPS_PROXY"])
        (self.home() / ".docker" / "config.json").unlink()
        (self.home() / ".docker" / "config.json").mkdir()
        self.run_docker(environ)
        self.assertEqual(self.forwarded(self.runner.argv), ["HOME=/tmp", "HTTPS_PROXY"])

    @unittest.skipUnless(hasattr(os, "mkfifo"), "needs named pipes")
    def test_a_docker_config_that_is_a_pipe_does_not_hang(self):
        (self.home() / ".docker").mkdir(parents=True)
        os.mkfifo(str(self.home() / ".docker" / "config.json"))
        error = finishes(self, lambda: self.run_docker({"HTTPS_PROXY": "http://proxy.corp:3128"}))
        self.assertIsNone(error)
        self.assertEqual(self.forwarded(self.runner.argv), ["HOME=/tmp", "HTTPS_PROXY"])

    def test_home_is_found_the_way_the_docker_cli_finds_it(self):
        # No HOME: the CLI asks the user database, and so does bin/update.
        self.docker_config({"proxies": {"default": {"httpsProxy": "http://172.17.0.1:3128"}}})
        environ = {"HTTPS_PROXY": "http://proxy.corp:3128"}
        account = mock.Mock(pw_dir=str(self.home()))
        for name, users, expected in (
            ("found", mock.Mock(getpwuid=mock.Mock(return_value=account)), ["HOME=/tmp"]),
            ("unknown uid", mock.Mock(getpwuid=mock.Mock(side_effect=KeyError(4242))), ["HOME=/tmp", "HTTPS_PROXY"]),
            ("no user database", None, ["HOME=/tmp", "HTTPS_PROXY"]),
        ):
            with self.subTest(name), mock.patch.object(trust, "pwd", users):
                runner = FakeRunner()
                Verifier(self.settings(), runner=runner, which=self.docker_only(), environ=environ).verify(
                    self.local_fetch())
                self.assertEqual(self.forwarded(runner.argv), expected)
                if users is not None:
                    users.getpwuid.assert_called_with(os.getuid())

    def test_offline_verification_never_reads_docker_config(self):
        key = self.tmp / "cosign.pub"
        key.write_bytes(b"key")
        with mock.patch.object(trust, "_docker_config_proxies") as proxies:
            self.run_docker({"HTTPS_PROXY": "http://127.0.0.1:3128"}, FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY=str(key),
                            FUTURE_AGI_UPDATE_COSIGN_OFFLINE="true")
        proxies.assert_not_called()
        self.assertEqual(self.forwarded(self.runner.argv), ["HOME=/tmp"])

    def test_a_proxy_the_container_cannot_use_is_named_as_the_cause(self):
        config = self.home() / ".docker" / "config.json"
        cases = (
            (self.TUF_PROXY + "\nmain.go:74: error during command execution: " + self.TUF_PROXY.split(": ", 1)[1],
             "main.go:74: error during command execution: " + self.TUF_PROXY.split(": ", 1)[1]),
            ("Error: Get \"https://tuf-repo-cdn.sigstore.dev/timestamp.json\": socks connect tcp 127.0.0.1:1080->"
             "tuf-repo-cdn.sigstore.dev:443: dial tcp 127.0.0.1:1080: connect: connection refused",
             "Error: Get \"https://tuf-repo-cdn.sigstore.dev/timestamp.json\": socks connect tcp 127.0.0.1:1080->"
             "tuf-repo-cdn.sigstore.dev:443: dial tcp 127.0.0.1:1080: connect: connection refused"),
        )
        for stderr, line in cases:
            with self.subTest(line=line[-40:]):
                verification = self.run_docker({"HTTPS_PROXY": "http://10.9.9.9:3128"},
                                               runner=FakeRunner(returncode=1, stderr=stderr))
                self.assertFalse(verification.verified)
                self.assertEqual(verification.error, (
                    f"cosign, run from {PINNED_IMAGE}, could not connect through its proxy: {line}. The proxy must be "
                    "reachable from inside a container, where localhost and 127.0.0.1 are the container itself. "
                    + self.advice(config)))
        # Round 4 (S36): a 407 came from a proxy cosign reached; it wants
        # credentials, and is never reported as unreachable.
        line = "Error: Get \"https://tuf-repo-cdn.sigstore.dev/timestamp.json\": Proxy Authentication Required"
        verification = self.run_docker({"HTTPS_PROXY": "http://10.9.9.9:3128"},
                                       runner=FakeRunner(returncode=1, stderr=line + "\n"))
        self.assertFalse(verification.verified)
        self.assertIn(f"reached its proxy, but the proxy refused it: {line}.", verification.error)
        self.assertNotIn("must be reachable", verification.error)

    def test_the_proxy_advice_names_docker_config_and_an_old_cosign(self):
        elsewhere = self.tmp / "docker-config"
        self.docker_config({"proxies": {"default": {"httpsProxy": "http://127.0.0.1:3128"}}}, elsewhere)
        verification = self.verifier(probe=FakeProbe("v2.4.1"), runner=FakeRunner(returncode=1, stderr=self.TUF_PROXY),
                                     environ={"DOCKER_CONFIG": str(elsewhere)}).verify(self.local_fetch())
        self.assertEqual(verification.error, (
            f"cosign, run from {PINNED_IMAGE}, could not connect through its proxy: {self.TUF_PROXY}. The proxy must "
            "be reachable from inside a container, where localhost and 127.0.0.1 are the container itself. "
            + self.advice(elsewhere / "config.json", f"replace the cosign on PATH, {COSIGN}, which is v2.4.1, with "
                                                     "cosign v3 or later")
            + f" (bin/update used {PINNED_IMAGE} because the cosign on PATH, {COSIGN}, is v2.4.1, older than v3)"))
        # Without a home to find it in, the usual place is named.
        with mock.patch.object(trust, "pwd", None):
            runner = FakeRunner(returncode=1, stderr=self.TUF_PROXY)
            verification = Verifier(self.settings(), runner=runner, which=self.docker_only(), environ={}).verify(
                self.local_fetch())
        self.assertTrue(verification.error.endswith(self.advice("~/.docker/config.json")), verification.error)

    def test_a_skipped_loopback_proxy_is_named_when_the_container_cannot_reach_sigstore(self):
        config = self.home() / ".docker" / "config.json"
        for environ, names in (
            ({"HTTPS_PROXY": "http://user:secret@127.0.0.1:3128"}, "HTTPS_PROXY on this host points at a loopback "
             "address, which inside a container is the container itself, so bin/update did not pass it"),
            ({"HTTPS_PROXY": "http://127.0.0.1:3128", "https_proxy": "localhost:3128", "NO_PROXY": "x"},
             "HTTPS_PROXY, https_proxy on this host point at a loopback address, which inside a container is the "
             "container itself, so bin/update did not pass them"),
        ):
            with self.subTest(names=names[:30]):
                verification = self.run_docker(environ, runner=FakeRunner(returncode=1, stderr=self.TUF_DIRECT))
                self.assertEqual(verification.error, (
                    f"cosign, run from {PINNED_IMAGE}, could not reach Sigstore: {self.TUF_DIRECT}. {names} to the "
                    f"cosign container. {self.advice(config)}"))
                self.assertNotIn("secret", verification.error)

    def test_other_failures_stay_plain(self):
        cases = (
            # A loopback proxy was skipped, but the signature itself is wrong.
            ({"HTTPS_PROXY": "http://127.0.0.1:3128"}, "Error: none of the expected identities matched"),
            # The network failed, but nothing was skipped.
            ({"HTTPS_PROXY": "http://10.9.9.9:3128"}, self.TUF_DIRECT),
            ({}, self.TUF_DIRECT),
        )
        for environ, stderr in cases:
            with self.subTest(environ=environ, stderr=stderr[-30:]):
                verification = self.run_docker(environ, runner=FakeRunner(returncode=1, stderr=stderr))
                self.assertEqual(verification.error, f"signature verification failed: {stderr}")

    def test_a_cosign_binary_has_the_hosts_proxy_and_its_failure_stays_plain(self):
        verification = self.verifier(environ={"HTTPS_PROXY": "http://127.0.0.1:3128"},
                                     runner=FakeRunner(returncode=1, stderr=self.TUF_PROXY)).verify(self.local_fetch())
        self.assertEqual(self.runner.argv[0], COSIGN)
        self.assertEqual(verification.error, f"signature verification failed: {self.TUF_PROXY}")

    def test_a_daemon_that_cannot_pull_through_its_proxy_is_a_docker_failure(self):
        detail = ("Error response from daemon: Get \"https://ghcr.io/v2/\": proxyconnect tcp: dial tcp "
                  "127.0.0.1:3128: connect: connection refused")
        verification = self.run_docker({"HTTPS_PROXY": "http://10.9.9.9:3128"}, runner=FakeRunner(
            returncode=125, stderr=f"Unable to find image '{PINNED_IMAGE}' locally\ndocker: {detail}\n\n"
                                   "Run 'docker run --help' for more information\n"))
        self.assertEqual(verification.error, (
            f"could not run cosign from {PINNED_IMAGE}: {detail}. Make sure Docker can pull and run that image, or "
            f"install cosign v3 or later ({self.INSTALL})"))



# ======================================================== round 4 (trust) ==

class _TrustRound4ProxyCase(TrustTestCase):
    """The helpers of CosignContainerProxyReachabilityTests, without running its tests again."""

    TUF_DIRECT = CosignContainerProxyReachabilityTests.TUF_DIRECT
    forwarded = CosignContainerProxyReachabilityTests.forwarded
    home = CosignContainerProxyReachabilityTests.home
    docker_config = CosignContainerProxyReachabilityTests.docker_config
    run_docker = CosignContainerProxyReachabilityTests.run_docker


class TrustRound4DockerProxyEntryTests(_TrustRound4ProxyCase):
    """S33-S35: only the config.json entry the docker CLI uses for this daemon, and only the kinds it sets."""

    ALL = {"HTTPS_PROXY": "http://proxy.corp:3128", "https_proxy": "http://proxy.corp:3128",
           "HTTP_PROXY": "http://proxy.corp:3128", "http_proxy": "http://proxy.corp:3128",
           "NO_PROXY": ".corp", "no_proxy": ".corp"}

    def context(self, name, host, directory=None):
        """A context the way `docker context create NAME --docker host=HOST` stores it."""

        directory = directory or self.home() / ".docker"
        meta = directory / "contexts" / "meta" / hashlib.sha256(name.encode("utf-8")).hexdigest()
        meta.mkdir(parents=True, exist_ok=True)
        (meta / "meta.json").write_text(json.dumps(
            {"Name": name, "Metadata": {}, "Endpoints": {"docker": {"Host": host, "SkipTLSVerify": False}}}))

    def held(self, environ):
        self.run_docker(environ)
        forwarded = self.forwarded(self.runner.argv)
        return [name for name in trust.PROXY_VARIABLES if name in environ and name not in forwarded]

    def test_an_entry_for_another_daemon_holds_nothing_back(self):
        # The local socket is in use: tcp://remote.corp:2376's entry is not.
        self.docker_config({"proxies": {"tcp://remote.corp:2376": {"httpsProxy": "http://172.17.0.1:3128",
                                                                   "httpProxy": "http://172.17.0.1:3128",
                                                                   "noProxy": "*.corp"}}})
        self.assertEqual(self.held(self.ALL), [])
        self.assertEqual(self.held(dict(self.ALL, DOCKER_HOST="unix:///var/run/docker.sock")), [])

    def test_the_entry_of_the_daemon_in_docker_host_is_the_one_used(self):
        self.docker_config({"proxies": {"default": {"httpProxy": "http://172.17.0.1:3128"},
                                        "tcp://remote.corp:2376": {"httpsProxy": "http://172.17.0.1:3128"}}})
        environ = dict(self.ALL, DOCKER_HOST="tcp://remote.corp:2376")
        # That entry replaces "default" entirely, as for the docker CLI.
        self.assertEqual(self.held(environ), ["HTTPS_PROXY", "https_proxy"])
        self.assertEqual(self.held(self.ALL), ["HTTP_PROXY", "http_proxy"])

    def test_docker_host_is_compared_the_way_the_cli_normalizes_it(self):
        self.docker_config({"proxies": {"tcp://remote.corp:2375": {"httpsProxy": "http://172.17.0.1:3128"},
                                        "tcp://[fd00::5]:2375": {"httpsProxy": "http://172.17.0.1:3128"},
                                        "unix:///var/run/docker.sock": {"noProxy": "*.corp"},
                                        "tcp://localhost:2376": {"httpProxy": "http://172.17.0.1:3128"}}})
        for host, held in (
            ("tcp://remote.corp", ["HTTPS_PROXY", "https_proxy"]),  # the default port
            ("remote.corp:2375", ["HTTPS_PROXY", "https_proxy"]),  # host:port is tcp
            (" tcp://remote.corp:2375 ", ["HTTPS_PROXY", "https_proxy"]),
            ("[fd00::5]:2375", ["HTTPS_PROXY", "https_proxy"]),
            ("tcp://admin@Remote.corp", []),  # the user name goes, the case stays
            ("tcp://admin@remote.corp", ["HTTPS_PROXY", "https_proxy"]),
            ("tcp://[fd00::5]", []),  # refused by the CLI: no entry applies
            ("tcp://remote.corp/", []),  # the path stays: tcp://remote.corp:2375/
            ("tcp://remote.corp:2377", []),
            ("unix://", ["NO_PROXY", "no_proxy"]),  # the default socket
            ("ssh://user@remote.corp", []),
        ):
            with self.subTest(host=host):
                self.assertEqual(self.held(dict(self.ALL, DOCKER_HOST=host)), held)
        # DOCKER_TLS_VERIFY without DOCKER_HOST: the CLI talks to tcp://localhost:2376.
        self.assertEqual(self.held(dict(self.ALL, DOCKER_TLS_VERIFY="1")), ["HTTP_PROXY", "http_proxy"])

    def test_an_ssh_daemon_is_known_to_the_cli_by_its_helper_address(self):
        self.docker_config({"proxies": {"http://docker.example.com": {"httpsProxy": "http://172.17.0.1:3128"}}})
        self.assertEqual(self.held(dict(self.ALL, DOCKER_HOST="ssh://user@remote.corp")),
                         ["HTTPS_PROXY", "https_proxy"])

    def test_the_current_context_names_the_daemon(self):
        self.context("remote", "tcp://remote.corp:2376")
        self.context("other", "unix:///run/user/1000/docker.sock")
        self.docker_config({"currentContext": "remote",
                            "proxies": {"default": {"noProxy": "*.corp"},
                                        "tcp://remote.corp:2376": {"httpsProxy": "http://172.17.0.1:3128"},
                                        "unix:///run/user/1000/docker.sock": {"httpProxy": "http://10.0.0.1:3128"}}})
        self.assertEqual(self.held(self.ALL), ["HTTPS_PROXY", "https_proxy"])
        # DOCKER_CONTEXT wins over currentContext, and DOCKER_HOST over both.
        self.assertEqual(self.held(dict(self.ALL, DOCKER_CONTEXT="other")), ["HTTP_PROXY", "http_proxy"])
        self.assertEqual(self.held(dict(self.ALL, DOCKER_CONTEXT="default")), ["NO_PROXY", "no_proxy"])
        self.assertEqual(self.held(dict(self.ALL, DOCKER_CONTEXT="other", DOCKER_HOST="tcp://remote.corp:2376")),
                         ["HTTPS_PROXY", "https_proxy"])
        # An empty DOCKER_HOST or DOCKER_CONTEXT is unset.
        self.assertEqual(self.held(dict(self.ALL, DOCKER_HOST="", DOCKER_CONTEXT="")), ["HTTPS_PROXY", "https_proxy"])
        # A context that does not exist: the default entry (docker run fails on its own).
        self.assertEqual(self.held(dict(self.ALL, DOCKER_CONTEXT="gone")), ["NO_PROXY", "no_proxy"])

    def test_a_context_endpoint_is_compared_as_it_was_written(self):
        self.context("remote", "tcp://remote.corp")
        self.docker_config({"currentContext": "remote",
                            "proxies": {"tcp://remote.corp": {"httpsProxy": "http://172.17.0.1:3128"},
                                        "tcp://remote.corp:2375": {"noProxy": "*.corp"}}})
        self.assertEqual(self.held(self.ALL), ["HTTPS_PROXY", "https_proxy"])

    def test_contexts_live_next_to_the_config_file_docker_config_names(self):
        elsewhere = self.tmp / "docker-config"
        self.context("remote", "tcp://remote.corp:2376", elsewhere)
        self.docker_config({"currentContext": "remote",
                            "proxies": {"tcp://remote.corp:2376": {"httpsProxy": "http://172.17.0.1:3128"}}}, elsewhere)
        self.assertEqual(self.held(dict(self.ALL, DOCKER_CONFIG=str(elsewhere))), ["HTTPS_PROXY", "https_proxy"])

    def test_each_kind_of_proxy_is_held_back_on_its_own(self):
        for fields, held in (
            ({"httpProxy": "http://172.17.0.1:3128"}, ["HTTP_PROXY", "http_proxy"]),
            ({"httpsProxy": "http://172.17.0.1:3128"}, ["HTTPS_PROXY", "https_proxy"]),
            ({"noProxy": "*.corp"}, ["NO_PROXY", "no_proxy"]),
            ({"httpProxy": "http://172.17.0.1:3128", "noProxy": "*.corp"},
             ["HTTP_PROXY", "NO_PROXY", "http_proxy", "no_proxy"]),
            ({"allProxy": "socks5://172.17.0.1:1080", "ftpProxy": "http://172.17.0.1:21"}, []),
            ({"httpsProxy": "", "httpProxy": "   "}, []),
        ):
            with self.subTest(fields=fields):
                self.docker_config({"proxies": {"default": fields}})
                self.assertEqual(self.held(self.ALL), held)

    def test_field_names_are_read_in_any_letter_case_like_go(self):
        self.docker_config({"Proxies": {"default": {"HTTPSPROXY": "http://172.17.0.1:3128",
                                                    "NoProxy": "*.corp"}}})
        self.assertEqual(self.held(self.ALL), ["HTTPS_PROXY", "NO_PROXY", "https_proxy", "no_proxy"])
        # The last key that matches a field wins, as in Go.
        self.docker_config('{"proxies": {"default": {"httpsProxy": "http://172.17.0.1:3128", "HTTPSPROXY": ""}}}')
        self.assertEqual(self.held(self.ALL), [])

    def test_a_daemon_entry_that_is_not_an_object_still_replaces_default(self):
        self.docker_config({"proxies": {"default": {"httpsProxy": "http://172.17.0.1:3128"},
                                        "tcp://remote.corp:2376": "nope"}})
        self.assertEqual(self.held(dict(self.ALL, DOCKER_HOST="tcp://remote.corp:2376")), [])
        self.assertEqual(self.held(self.ALL), ["HTTPS_PROXY", "https_proxy"])

    def test_a_held_back_variable_is_not_reported_as_a_loopback_proxy(self):
        self.docker_config({"proxies": {"default": {"httpsProxy": "http://172.17.0.1:3128"}}})
        verification = self.run_docker({"HTTPS_PROXY": "http://127.0.0.1:3128", "HTTP_PROXY": "http://127.0.0.1:3128"},
                                       runner=FakeRunner(returncode=1, stderr=self.TUF_DIRECT))
        self.assertEqual(self.forwarded(self.runner.argv), ["HOME=/tmp"])
        self.assertIn("HTTP_PROXY on this host points at a loopback address", verification.error)
        self.assertNotIn("HTTPS_PROXY", verification.error)

    def test_the_context_store_is_read_only_when_an_entry_names_a_daemon(self):
        self.docker_config({"currentContext": "remote", "proxies": {"default": {"httpsProxy": "http://x:1"}}})
        with mock.patch.object(trust, "_docker_daemon_host") as daemon:
            self.assertEqual(self.held(self.ALL), ["HTTPS_PROXY", "https_proxy"])
        daemon.assert_not_called()


class TrustRound4ProxyAuthenticationTests(_TrustRound4ProxyCase):
    """S36: a 407 means the proxy was reached and wants credentials; it is never reported as unreachable."""

    LINE = "Error: Get \"https://tuf-repo-cdn.sigstore.dev/timestamp.json\": Proxy Authentication Required"
    DOCS = "https://docs.docker.com/engine/cli/proxy/"
    CREDENTIALS = "the proxy URL with its credentials (http://user:password@proxy:3128)"
    DIRECT = ("install cosign v3 or later (https://docs.sigstore.dev/cosign/system_config/installation/): bin/update "
              "runs a cosign v3 on PATH directly, with this host's proxy settings")

    def refused(self, environ, stderr=None):
        return self.run_docker(environ, runner=FakeRunner(returncode=1, stderr=stderr or self.LINE + "\n")).error

    def opening(self, line=None):
        return (f"cosign, run from {PINNED_IMAGE}, reached its proxy, but the proxy refused it: {line or self.LINE}. "
                "The proxy needs credentials that cosign did not send, or rejected the ones it sent. ")

    def test_the_hosts_proxy_that_was_passed_is_the_one_to_fix(self):
        config = self.home() / ".docker" / "config.json"
        self.assertEqual(self.refused({"HTTPS_PROXY": "http://proxy.corp:3128"}), (
            self.opening() + f"bin/update passed this host's HTTPS_PROXY to the container. Put {self.CREDENTIALS} in "
            f"HTTPS_PROXY, or give containers that proxy URL in the \"proxies\" section of {config} ({self.DOCS}), "
            f"or {self.DIRECT}"))
        self.assertIn("this host's HTTPS_PROXY and https_proxy to the container",
                      self.refused({"HTTPS_PROXY": "http://proxy.corp:3128", "https_proxy": "http://proxy.corp:3128"}))
        # A cosign on PATH has this host's proxy itself: its failure stays plain.
        verification = self.verifier(environ={"HTTPS_PROXY": "http://proxy.corp:3128"},
                                     runner=FakeRunner(returncode=1, stderr=self.LINE)).verify(self.local_fetch())
        self.assertEqual(verification.error, f"signature verification failed: {self.LINE}")

    def test_the_proxy_docker_gives_containers_is_the_one_to_fix(self):
        elsewhere = self.tmp / "docker-config"
        self.docker_config({"proxies": {"default": {"httpsProxy": "http://172.17.0.1:3128"}}}, elsewhere)
        error = self.refused({"HTTPS_PROXY": "http://user:secret@proxy.corp:3128", "DOCKER_CONFIG": str(elsewhere)})
        self.assertEqual(error, self.opening() + (
            f"The container's HTTPS proxy is the httpsProxy in the \"proxies\" section of {elsewhere / 'config.json'}, "
            f"which Docker sets in every container. Put {self.CREDENTIALS} there ({self.DOCS}), or {self.DIRECT}"))
        self.assertNotIn("secret", error)

    def test_without_a_known_https_proxy_both_places_are_named(self):
        config = self.home() / ".docker" / "config.json"
        self.assertEqual(self.refused({}), self.opening() + (
            f"Put {self.CREDENTIALS} in the \"proxies\" section of {config} ({self.DOCS}) or in this host's "
            f"HTTPS_PROXY, or {self.DIRECT}"))

    def test_any_letter_case_and_the_last_such_line_are_used(self):
        stderr = ("Error: first: proxy authentication required\n"
                  "main.go:74: error during command execution: Get \"https://x\": PROXY AUTHENTICATION REQUIRED\n")
        error = self.refused({"HTTPS_PROXY": "http://proxy.corp:3128"}, stderr)
        self.assertTrue(error.startswith(self.opening(
            "main.go:74: error during command execution: Get \"https://x\": PROXY AUTHENTICATION REQUIRED")), error)

    def test_a_proxy_that_answered_is_never_called_unreachable(self):
        stderr = ("Error: Get \"https://tuf-repo-cdn.sigstore.dev/a\": proxyconnect tcp: dial tcp 10.9.9.9:3128: "
                  "connect: connection refused\n" + self.LINE + "\n")
        error = self.refused({"HTTPS_PROXY": "http://10.9.9.9:3128"}, stderr)
        self.assertIn("reached its proxy, but the proxy refused it", error)
        self.assertNotIn("could not connect through its proxy", error)
        self.assertNotIn("must be reachable", error)


class TrustRound4OfflineNeedsAKeyTests(TrustTestCase):
    """S20: offline verification is a public-key verification; keyless always keeps the network."""

    def test_load_settings_refuses_offline_without_a_key(self):
        with self.assertRaises(SettingsError) as caught:
            self.settings(FUTURE_AGI_UPDATE_COSIGN_OFFLINE="true")
        self.assertIn("FUTURE_AGI_UPDATE_COSIGN_OFFLINE", str(caught.exception))
        self.assertIn("FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY", str(caught.exception))

    def test_keyless_settings_made_another_way_keep_the_network_and_the_proxy(self):
        self.verifier(which=self.docker_only(), environ={"HTTPS_PROXY": "http://proxy.corp:3128"},
                      settings=raw_settings(FUTURE_AGI_UPDATE_COSIGN_OFFLINE="true")).verify(self.local_fetch())
        argv = self.runner.argv
        self.assertNotIn("--network", argv)
        self.assertIn("HTTPS_PROXY", argv[:argv.index(PINNED_IMAGE)])
        self.assertFalse(any(arg.startswith("--insecure-ignore-tlog") for arg in argv))
        # With a key, offline still means no network and no proxy.
        key = self.tmp / "cosign.pub"
        key.write_bytes(b"key")
        self.verifier(which=self.docker_only(), environ={"HTTPS_PROXY": "http://proxy.corp:3128"},
                      FUTURE_AGI_UPDATE_COSIGN_OFFLINE="true",
                      FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY=str(key)).verify(self.local_fetch())
        argv = self.runner.argv
        self.assertEqual(argv[argv.index("--network") + 1], "none")
        self.assertNotIn("HTTPS_PROXY", argv)
        self.assertIn("--insecure-ignore-tlog=true", argv)



class IntegratorRound4DockerHostTests(_TrustRound4ProxyCase):
    """S33 edges: a daemon address the docker CLI refuses, or a context it cannot read, picks no daemon entry."""

    ALL = TrustRound4DockerProxyEntryTests.ALL
    context = TrustRound4DockerProxyEntryTests.context
    held = TrustRound4DockerProxyEntryTests.held

    def test_addresses_the_cli_refuses_have_no_entry(self):
        for value in ("unix://unix:///var/run/docker.sock", "npipe://x://y", "http://remote.corp:2375",
                      "tcp://[fd00::5]"):
            with self.subTest(value=value):
                self.assertIsNone(trust._docker_host_url(value, False))
        self.assertEqual(trust._docker_host_url("fd://", False), "fd://")
        self.assertEqual(trust._docker_host_url("npipe://", False), "npipe:////./pipe/docker_engine")

    def test_a_context_whose_endpoint_has_no_host_string_holds_nothing_back(self):
        self.context("odd", 5)
        self.docker_config({"currentContext": "odd",
                            "proxies": {"default": {"httpsProxy": "http://172.17.0.1:3128"},
                                        "unix:///var/run/docker.sock": {"httpProxy": "http://172.17.0.1:3128"}}})
        # No daemon address to match: the "default" entry applies.
        self.assertEqual(self.held(self.ALL), ["HTTPS_PROXY", "https_proxy"])
        # An empty Host is the default socket, as the CLI reads it.
        self.context("empty", "")
        self.assertEqual(self.held(dict(self.ALL, DOCKER_CONTEXT="empty")), ["HTTP_PROXY", "http_proxy"])



class IntegratorRound4BundleFallbackTests(TrustTestCase):
    """S12 on the host: the latest release's manifest without its bundle falls back to a complete pair.

    The release workflow uploads the bundle first and the manifest last, so
    only a bundle deleted by hand (or a release published before that order)
    leaves a manifest alone. A host then uses the newest release that has
    both, with a note, rather than refusing an unverifiable signature.
    """

    API_URL = GitHubLatestFallbackTests.API_URL
    MISSING = GitHubLatestFallbackTests.MISSING
    download = GitHubLatestFallbackTests.download
    serve_api = GitHubLatestFallbackTests.serve_api
    serve_release = GitHubLatestFallbackTests.serve_release

    def latest_without_its_bundle(self, version="v1.5.0"):
        self.web.serve(LATEST_URL, encode(make_manifest(version)))
        return self.download(version, BUNDLE_ASSET)

    def test_the_newest_complete_pair_is_used_with_a_note(self):
        missing = self.latest_without_its_bundle()
        self.serve_api([release("v1.5.0", MANIFEST_ASSET), release("v1.2.3", MANIFEST_ASSET, BUNDLE_ASSET),
                        release("v1.2.0", MANIFEST_ASSET, BUNDLE_ASSET)])
        self.serve_release("v1.2.3")
        fetched = fetch_manifest(LATEST_URL, self.workdir(), cwd=self.caller)
        chosen = self.download("v1.2.3", MANIFEST_ASSET)
        self.assertEqual(fetched.source, chosen)
        self.assertEqual(fetched.data, make_manifest("v1.2.3"))
        self.assertEqual(fetched.manifest_path.read_bytes(), encode(make_manifest("v1.2.3")))
        self.assertEqual(fetched.raw, encode(make_manifest("v1.2.3")))
        self.assertEqual(fetched.bundle_source, self.download("v1.2.3", BUNDLE_ASSET))
        self.assertEqual(fetched.bundle_path.read_bytes(), BUNDLE_BYTES)
        self.assertIsNone(fetched.bundle_error)
        self.assertEqual(fetched.newer_release, "v1.5.0")
        self.assertEqual(fetched.note, "the newest release v1.5.0 has no complete update manifest and signature "
                                       "bundle yet; using v1.2.3, the newest release that has one")
        self.assertEqual(self.web.urls, [LATEST_URL, missing, self.API_URL, chosen,
                                         self.download("v1.2.3", BUNDLE_ASSET)])
        # The fallback is verified against its own release's signing identity.
        self.assertTrue(self.verifier().verify(fetched).verified)
        argv = self.runner.argv
        self.assertEqual(argv[argv.index("--certificate-identity") + 1], PINNED_IDENTITY_V123)

    def test_a_latest_release_that_is_not_a_platform_release(self):
        self.latest_without_its_bundle()
        self.serve_api([release("demo-2026-10-19", MANIFEST_ASSET), release("v1.4.0", MANIFEST_ASSET, BUNDLE_ASSET)])
        self.serve_release("v1.4.0")
        fetched = fetch_manifest(LATEST_URL, self.workdir(), cwd=self.caller)
        self.assertEqual(fetched.data["version"], "v1.4.0")
        self.assertIsNone(fetched.newer_release)
        self.assertEqual(fetched.note, "the latest GitHub release has no complete update manifest and signature "
                                       "bundle; using v1.4.0, the newest release that has one")

    def test_no_complete_pair_reports_the_missing_bundle_and_why_nothing_older_was_used(self):
        missing = self.latest_without_its_bundle()
        self.serve_api([release("v1.5.0", MANIFEST_ASSET), release("v1.4.0", MANIFEST_ASSET)])
        fetched = fetch_manifest(LATEST_URL, self.workdir(), cwd=self.caller)
        self.assertEqual((fetched.source, fetched.data["version"], fetched.bundle_path), (LATEST_URL, "v1.5.0", None))
        # Round 5 (T27): what is missing, not the manifest-404 text (the manifest was found).
        self.assertEqual(fetched.bundle_error,
                         f"signature bundle not available ({missing}): could not download {missing}: HTTP 404. "
                         f"No stable release of future-agi/future-agi has both {MANIFEST_ASSET} and its signature "
                         f"bundle {BUNDLE_ASSET}. Retry later, or pass --manifest and --bundle for a specific release")
        self.assertFalse(self.verifier().verify(fetched).verified)

    def test_a_fallback_manifest_that_cannot_be_downloaded_is_reported(self):
        missing = self.latest_without_its_bundle()
        self.serve_api([release("v1.5.0", MANIFEST_ASSET), release("v1.4.0", MANIFEST_ASSET, BUNDLE_ASSET)])
        chosen = self.download("v1.4.0", MANIFEST_ASSET)
        self.web.serve(chosen, error=self.web.http_error(chosen, 502, "Bad Gateway"))
        fetched = fetch_manifest(LATEST_URL, self.workdir(), cwd=self.caller)
        self.assertEqual((fetched.source, fetched.data["version"]), (LATEST_URL, "v1.5.0"))
        self.assertEqual(fetched.bundle_error,
                         f"signature bundle not available ({missing}): could not download {missing}: HTTP 404. "
                         "Looking for an older release that has a complete manifest and signature bundle failed: "
                         f"could not download {chosen}: HTTP 502")

    def test_a_release_list_that_says_this_release_is_complete_keeps_the_missing_bundle(self):
        # The bundle was uploaded between the two requests: the same release is not fetched again.
        missing = self.latest_without_its_bundle()
        self.serve_api([release("v1.5.0", MANIFEST_ASSET, BUNDLE_ASSET)])
        fetched = fetch_manifest(LATEST_URL, self.workdir(), cwd=self.caller)
        self.assertEqual((fetched.source, fetched.note), (LATEST_URL, None))
        self.assertEqual(fetched.bundle_error, f"signature bundle not available ({missing}): could not download "
                                               f"{missing}: HTTP 404")
        self.assertEqual(self.web.urls, [LATEST_URL, missing, self.API_URL])

    def test_an_explicit_bundle_or_another_source_never_falls_back(self):
        explicit = "https://example.com/explicit.sigstore.json"
        self.latest_without_its_bundle()
        fetched = fetch_manifest(LATEST_URL, self.workdir(), cwd=self.caller, bundle=explicit)
        self.assertEqual(fetched.bundle_error, f"signature bundle not available ({explicit}): could not download "
                                               f"{explicit}: HTTP 404")
        source = self.download("v1.5.0", MANIFEST_ASSET)
        self.web.serve(source, encode(make_manifest("v1.5.0")))
        self.web.requests.clear()
        fetched = fetch_manifest(source, self.workdir(), cwd=self.caller)
        bundle = self.download("v1.5.0", BUNDLE_ASSET)
        self.assertEqual(fetched.bundle_error, f"signature bundle not available ({bundle}): could not download "
                                               f"{bundle}: HTTP 404")
        self.assertNotIn(self.API_URL, self.web.urls)



class TrustRound5ManifestFallbackBundleTests(TrustTestCase):
    """T26: the manifest-404 fallback ends on the newest release that has both the manifest and its bundle.

    The release list names a release with the manifest but not its bundle
    (deleted by hand): the fallback no longer stops there with an unverifiable
    manifest, it goes on to the newest complete pair.
    """

    API_URL = GitHubLatestFallbackTests.API_URL
    download = GitHubLatestFallbackTests.download
    serve_api = GitHubLatestFallbackTests.serve_api
    serve_release = GitHubLatestFallbackTests.serve_release

    def test_a_chosen_release_without_its_bundle_falls_back_to_the_newest_complete_pair(self):
        self.serve_api([release("v1.5.0", "source.tar.gz"), release("v1.4.0", MANIFEST_ASSET),
                        release("v1.2.3", MANIFEST_ASSET, BUNDLE_ASSET),
                        release("v1.2.0", MANIFEST_ASSET, BUNDLE_ASSET)])
        self.serve_release("v1.4.0", bundle_bytes=None)
        self.serve_release("v1.2.3")
        self.serve_release("v1.2.0", bundle_bytes=b"older")
        fetched = fetch_manifest(LATEST_URL, self.workdir(), cwd=self.caller)
        chosen = self.download("v1.2.3", MANIFEST_ASSET)
        self.assertEqual(fetched.source, chosen)
        self.assertEqual(fetched.data, make_manifest("v1.2.3"))
        self.assertEqual(fetched.raw, encode(make_manifest("v1.2.3")))
        self.assertEqual(fetched.manifest_path.read_bytes(), encode(make_manifest("v1.2.3")))
        self.assertEqual(fetched.bundle_source, self.download("v1.2.3", BUNDLE_ASSET))
        self.assertEqual(fetched.bundle_path.read_bytes(), BUNDLE_BYTES)
        self.assertIsNone(fetched.bundle_error)
        self.assertEqual(fetched.newer_release, "v1.5.0")
        self.assertEqual(fetched.note, "the newest release v1.5.0 has no complete update manifest and signature "
                                       "bundle yet; using v1.2.3, the newest release that has one")
        self.assertEqual(self.web.urls, [LATEST_URL, self.API_URL, self.download("v1.4.0", MANIFEST_ASSET),
                                         self.download("v1.4.0", BUNDLE_ASSET), self.API_URL, chosen,
                                         self.download("v1.2.3", BUNDLE_ASSET)])
        # Verified against the identity of the release it came from.
        self.assertTrue(self.verifier().verify(fetched).verified)
        argv = self.runner.argv
        self.assertEqual(argv[argv.index("--certificate-identity") + 1], PINNED_IDENTITY_V123)

    def test_a_fork_is_held_to_the_bundle_its_own_manifest_names(self):
        source = "https://github.com/acme/agi-fork/releases/latest/download/acme-manifest.json"
        api = "https://api.github.com/repos/acme/agi-fork/releases?per_page=30"
        self.serve_api([release("v3.1.0", "acme-manifest.json", repo="acme/agi-fork"),
                        release("v3.0.1", "acme-manifest.json", "acme.sigstore.json", repo="acme/agi-fork")], url=api)
        self.serve_release("v3.1.0", bundle_bytes=None, repo="acme/agi-fork", asset="acme-manifest.json",
                           bundle="acme.sigstore.json")
        self.serve_release("v3.0.1", bundle_bytes=b"acme-bundle", repo="acme/agi-fork", asset="acme-manifest.json",
                           bundle="acme.sigstore.json")
        # GitHub's latest release is not the fork's newest platform release, so the latest URL answers 404.
        fetched = fetch_manifest(source, self.workdir(), cwd=self.caller)
        self.assertEqual(fetched.data["version"], "v3.0.1")
        self.assertEqual(fetched.bundle_source, self.download("v3.0.1", "acme.sigstore.json", "acme/agi-fork"))
        self.assertEqual(fetched.bundle_path.read_bytes(), b"acme-bundle")
        self.assertIsNone(fetched.bundle_error)

    def test_a_release_list_that_says_the_chosen_release_is_complete_keeps_its_missing_bundle(self):
        # The list names v1.4.0's bundle, but it answers 404: v1.4.0 is not fetched again, the error stays.
        self.serve_api([release("v1.5.0"), release("v1.4.0", MANIFEST_ASSET, BUNDLE_ASSET)])
        self.serve_release("v1.4.0", bundle_bytes=None)
        fetched = fetch_manifest(LATEST_URL, self.workdir(), cwd=self.caller)
        bundle = self.download("v1.4.0", BUNDLE_ASSET)
        self.assertEqual(fetched.data["version"], "v1.4.0")
        self.assertEqual(fetched.note, "the newest release v1.5.0 has no update manifest yet; "
                                       "using v1.4.0, the newest release that has one")
        self.assertEqual(fetched.bundle_error,
                         f"signature bundle not available ({bundle}): could not download {bundle}: HTTP 404")
        self.assertEqual(self.web.urls, [LATEST_URL, self.API_URL, self.download("v1.4.0", MANIFEST_ASSET), bundle,
                                         self.API_URL])

    def test_an_explicit_bundle_is_still_never_replaced(self):
        # A guard: the second lookup belongs only to the bundle bin/update chose itself.
        explicit = "https://example.com/explicit.sigstore.json"
        self.serve_api([release("v1.5.0"), release("v1.4.0", MANIFEST_ASSET)])
        self.serve_release("v1.4.0", bundle_bytes=None)
        fetched = fetch_manifest(LATEST_URL, self.workdir(), cwd=self.caller, bundle=explicit)
        self.assertEqual(fetched.bundle_error, f"signature bundle not available ({explicit}): could not download "
                                               f"{explicit}: HTTP 404")
        self.assertEqual(self.web.urls.count(self.API_URL), 1)


class TrustRound5MissingBundleMessageTests(TrustTestCase):
    """T27: a missing bundle is reported as what is missing, never as a manifest that was not found."""

    API_URL = GitHubLatestFallbackTests.API_URL
    MISSING = GitHubLatestFallbackTests.MISSING
    download = GitHubLatestFallbackTests.download
    serve_api = GitHubLatestFallbackTests.serve_api

    def latest_without_its_bundle(self, version="v1.5.0"):
        self.web.serve(LATEST_URL, encode(make_manifest(version)))
        return self.download(version, BUNDLE_ASSET)

    def test_no_release_with_both_says_exactly_that(self):
        missing = self.latest_without_its_bundle()
        for name, releases in (
            ("only manifests", [release("v1.5.0", MANIFEST_ASSET), release("v1.4.0", MANIFEST_ASSET)]),
            ("only bundles", [release("v1.4.0", BUNDLE_ASSET)]),
            ("complete pairs only in drafts and prereleases", [
                release("v1.6.0", MANIFEST_ASSET, BUNDLE_ASSET, draft=True),
                release("v1.6.0-rc.1", MANIFEST_ASSET, BUNDLE_ASSET)]),
            ("no releases", []),
            ("not a list", {"message": "Moved Permanently"}),
        ):
            with self.subTest(name):
                self.serve_api(releases)
                fetched = fetch_manifest(LATEST_URL, self.workdir(), cwd=self.caller)
                self.assertEqual(fetched.bundle_error,
                                 f"signature bundle not available ({missing}): could not download {missing}: HTTP 404. "
                                 f"No stable release of future-agi/future-agi has both {MANIFEST_ASSET} and its "
                                 f"signature bundle {BUNDLE_ASSET}. Retry later, or pass --manifest and --bundle for a "
                                 "specific release")
                self.assertNotIn("was not found", fetched.bundle_error)
                self.assertNotIn("has no update manifest", fetched.bundle_error)

    def test_a_forks_names_are_the_ones_reported(self):
        source = "https://github.com/acme/agi-fork/releases/latest/download/acme-manifest.json"
        api = "https://api.github.com/repos/acme/agi-fork/releases?per_page=30"
        self.web.serve(source, encode(make_manifest("v3.1.0", bundle="acme.sigstore.json")))
        self.serve_api([release("v3.1.0", "acme-manifest.json", repo="acme/agi-fork")], url=api)
        fetched = fetch_manifest(source, self.workdir(), cwd=self.caller)
        self.assertTrue(fetched.bundle_error.endswith(
            ". No stable release of acme/agi-fork has both acme-manifest.json and its signature bundle "
            "acme.sigstore.json. Retry later, or pass --manifest and --bundle for a specific release"),
            fetched.bundle_error)

    def test_a_release_list_that_cannot_be_read_is_reported_once_without_the_manifest_404_text(self):
        missing = self.latest_without_its_bundle()
        for name, route, reason in (
            ("rate limited", {"error": self.web.http_error(self.API_URL, 403, "rate limit exceeded")},
             f"could not download {self.API_URL}: HTTP 403"),
            ("offline", {"error": URLError("nodename nor servname provided")},
             f"could not download {self.API_URL}: nodename nor servname provided"),
            ("not json", {"body": b"<html>unicorn</html>"},
             f"the release list {self.API_URL} is not valid JSON: Expecting value: line 1 column 1 (char 0)"),
            ("nested too deeply", {"body": b"[" * 200000},
             f"the release list {self.API_URL} is not valid JSON: it is nested too deeply"),
        ):
            with self.subTest(name):
                self.web.serve(self.API_URL, **route)
                fetched = fetch_manifest(LATEST_URL, self.workdir(), cwd=self.caller)
                self.assertEqual(fetched.bundle_error,
                                 f"signature bundle not available ({missing}): could not download {missing}: HTTP 404. "
                                 "Looking for an older release that has a complete manifest and signature bundle "
                                 f"failed: {reason}")
                self.assertEqual(fetched.bundle_error.count("Looking for"), 1)
                self.assertNotIn(self.MISSING, fetched.bundle_error)

    def test_the_manifest_404_messages_are_unchanged(self):
        # A guard: when the manifest itself is missing, its text still says so.
        self.serve_api([release("v1.5.0", BUNDLE_ASSET)])
        with self.assertRaises(ManifestError) as caught:
            fetch_manifest(LATEST_URL, self.workdir(), cwd=self.caller)
        self.assertEqual(str(caught.exception), self.MISSING)
        self.assertNotIsInstance(caught.exception, trust.NoCompleteRelease)


class TrustRound5DockerContextHostTests(_TrustRound4ProxyCase):
    """T30: a context endpoint without a Host is the default socket, as the docker CLI connects to it."""

    ALL = TrustRound4DockerProxyEntryTests.ALL
    held = TrustRound4DockerProxyEntryTests.held

    PROXIES = {"default": {"httpsProxy": "http://172.17.0.1:3128"},
               "unix:///var/run/docker.sock": {"httpProxy": "http://172.17.0.1:3128"},
               "tcp://localhost:2376": {"noProxy": "*.corp"}}

    def endpoint(self, name, docker_endpoint):
        """A context whose meta.json holds `docker_endpoint` as its docker endpoint (None: no docker endpoint)."""

        meta = self.home() / ".docker" / "contexts" / "meta" / hashlib.sha256(name.encode("utf-8")).hexdigest()
        meta.mkdir(parents=True, exist_ok=True)
        endpoints = {} if docker_endpoint is None else {"docker": docker_endpoint}
        (meta / "meta.json").write_text(json.dumps({"Name": name, "Metadata": {}, "Endpoints": endpoints}))

    def test_an_endpoint_without_a_host_key_is_the_default_socket(self):
        # What `docker context create emptyhost --docker host=` writes (docker 29.5.2).
        self.endpoint("emptyhost", {"SkipTLSVerify": False})
        self.docker_config({"currentContext": "emptyhost", "proxies": self.PROXIES})
        self.assertEqual(self.held(self.ALL), ["HTTP_PROXY", "http_proxy"])
        self.assertEqual(trust._docker_daemon_host({"DOCKER_CONTEXT": "emptyhost"}, {},
                                                   self.home() / ".docker"), "unix:///var/run/docker.sock")

    def test_a_null_host_is_the_default_socket_too(self):
        self.endpoint("nullhost", {"Host": None, "SkipTLSVerify": False})
        self.docker_config({"currentContext": "nullhost", "proxies": self.PROXIES})
        self.assertEqual(self.held(self.ALL), ["HTTP_PROXY", "http_proxy"])

    def test_tls_on_the_endpoint_does_not_move_the_default_socket(self):
        # TLS settings on a context name no address: the CLI's client keeps its default host.
        self.endpoint("tlsonly", {"SkipTLSVerify": True})
        self.docker_config({"currentContext": "tlsonly", "proxies": self.PROXIES})
        self.assertEqual(self.held(dict(self.ALL, DOCKER_TLS_VERIFY="1", DOCKER_TLS="1")), ["HTTP_PROXY", "http_proxy"])

    def test_docker_tls_moves_the_default_contexts_address_like_docker_tls_verify(self):
        self.docker_config({"proxies": self.PROXIES})
        for variable in ("DOCKER_TLS", "DOCKER_TLS_VERIFY"):
            with self.subTest(variable=variable):
                self.assertEqual(self.held(dict(self.ALL, **{variable: "1"})), ["NO_PROXY", "no_proxy"])
        # Empty is unset, as for the CLI; DOCKER_HOST still names the address itself.
        self.assertEqual(self.held(dict(self.ALL, DOCKER_TLS="")), ["HTTP_PROXY", "http_proxy"])
        self.assertEqual(self.held(dict(self.ALL, DOCKER_TLS="1", DOCKER_HOST="unix:///var/run/docker.sock")),
                         ["HTTP_PROXY", "http_proxy"])

    def test_what_still_names_no_daemon(self):
        # A guard: no docker endpoint at all, or a Host the CLI cannot read, picks the "default" entry.
        for name, endpoint in (("noendpoint", None), ("numberhost", {"Host": 5}), ("listhost", {"Host": ["x"]})):
            with self.subTest(context=name):
                self.endpoint(name, endpoint)
                self.docker_config({"currentContext": name, "proxies": self.PROXIES})
                self.assertEqual(self.held(self.ALL), ["HTTPS_PROXY", "https_proxy"])


class TrustRound5VerifierWithoutVerifySettingsTests(TrustTestCase):
    """G3: settings loaded for a command that does not verify keep OFFLINE without a key harmless."""

    def test_offline_without_a_key_from_load_settings_keeps_keyless_verification_online(self):
        settings = load_settings(self.checkout, overrides={"FUTURE_AGI_UPDATE_COSIGN_OFFLINE": "true"}, environ={},
                                 verify=False)
        self.assertIs(settings.boolean("FUTURE_AGI_UPDATE_COSIGN_OFFLINE"), True)
        verification = self.verifier(which=self.docker_only(), environ={"HTTPS_PROXY": "http://proxy.corp:3128"},
                                     settings=settings).verify(self.local_fetch())
        self.assertTrue(verification.verified, verification.error)
        self.assertEqual(verification.method, "keyless")
        argv = self.runner.argv
        self.assertNotIn("--network", argv)
        self.assertIn("HTTPS_PROXY", argv[:argv.index(PINNED_IMAGE)])
        self.assertFalse(any(arg.startswith("--insecure-ignore-tlog") for arg in argv))
        with self.assertRaises(SettingsError):
            load_settings(self.checkout, overrides={"FUTURE_AGI_UPDATE_COSIGN_OFFLINE": "true"}, environ={})



class TrustRound6VersionNumberLengthTests(TrustTestCase):
    """U38: a version number of absurdly many digits, from a manifest, a release list or cosign, is no traceback.

    int() refuses a string of more than 4300 digits on Python 3.11+ with a
    plain ValueError, which bin/update's CLI does not catch.
    """

    API_URL = GitHubLatestFallbackTests.API_URL
    TOO_LONG = ("1" * 19, "7" * 5000)

    def test_a_manifest_whose_version_has_a_number_too_long_is_a_manifest_error(self):
        for digits in self.TOO_LONG:
            for field in ("version", "minimum_version"):
                with self.subTest(field=field, length=len(digits)):
                    if field == "version":
                        manifest = make_manifest(f"v{digits}.1.0")
                    else:
                        manifest = make_manifest()
                        manifest["minimum_version"] = f"v1.{digits}.0"
                    with self.assertRaises(ManifestError) as caught:
                        self.local_fetch(manifest)
                    message = str(caught.exception)
                    self.assertIn("each number at most 18 digits long", message)
                    self.assertLess(len(message), 200)

    def test_a_release_list_tag_with_a_number_too_long_is_not_a_release(self):
        for digits in self.TOO_LONG:
            with self.subTest(length=len(digits)):
                tag = f"v{digits}.0.0"
                self.web.serve(self.API_URL, json.dumps([
                    release(tag, MANIFEST_ASSET, BUNDLE_ASSET),
                    release("v1.4.0", MANIFEST_ASSET, BUNDLE_ASSET),
                    release(f"v1.5.0-rc.{digits}", MANIFEST_ASSET, BUNDLE_ASSET),
                ]).encode("utf-8"))
                chosen = f"https://github.com/future-agi/future-agi/releases/download/v1.4.0/{MANIFEST_ASSET}"
                self.web.serve(chosen, encode(make_manifest("v1.4.0")))
                self.web.serve(chosen.replace(MANIFEST_ASSET, BUNDLE_ASSET), BUNDLE_BYTES)
                fetched = fetch_manifest(LATEST_URL, self.workdir(), cwd=self.caller)
                self.assertEqual(fetched.source, chosen)
                self.assertEqual(fetched.data["version"], "v1.4.0")
                self.assertIsNone(fetched.newer_release)
                self.assertFalse(any(tag in url for url in self.web.urls))

    def test_a_cosign_reporting_a_number_too_long_is_not_taken_for_v3(self):
        for reported in ("v3." + "1" * 19 + ".0", "v" + "3" * 5000 + ".0.0", "3.0." + "9" * 19):
            for json_form in (True, False):
                with self.subTest(reported=reported[:24], json_form=json_form):
                    verifier = self.verifier(probe=FakeProbe(reported, json_form=json_form))
                    self.assertEqual(verifier.cosign_version(COSIGN), (None, reported))
                    verification = verifier.verify(self.local_fetch())
                    self.assertTrue(verification.verified, verification.error)
                    self.assertEqual(self.runner.argv[:2], ["docker", "run"])
                    self.assertEqual(verification.tool, PINNED_IMAGE)
                    self.assertIn("not a release number", verification.details["note"])
        # 18 digits are still a release number.
        verifier = self.verifier(probe=FakeProbe("v3." + "1" * 18 + ".0"))
        self.assertEqual(verifier.cosign_version(COSIGN)[0], (3, int("1" * 18), 0))
        self.assertEqual(verifier.verify(self.local_fetch()).tool, COSIGN)


# ================================================================ round 7 ==

class TrustRound7WithoutUserDatabaseTests(TrustTestCase):
    """trust.py imports where the pwd module does not exist (it guards the import for platforms without one)."""

    def load_without_pwd(self):
        """A fresh copy of trust.py, imported while `import pwd` fails."""

        with mock.patch.dict(sys.modules, {"pwd": None}):
            spec = importlib.util.spec_from_file_location("deploy.update._trust_without_pwd", trust.__file__)
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
        return module

    def test_without_pwd_the_docker_config_is_found_only_through_home(self):
        module = self.load_without_pwd()
        self.assertIsNone(module.pwd)
        self.assertIsNone(module._docker_config_file({}))
        self.assertEqual(module._docker_config_file({"HOME": "/home/op"}), Path("/home/op/.docker/config.json"))
        # Without HOME and a user database, no config.json gives containers a proxy: the host's is forwarded.
        runner = FakeRunner()
        verification = module.Verifier(self.settings(), runner=runner, which=self.docker_only(),
                                       environ={"HTTPS_PROXY": "http://proxy.corp:3128"}).verify(self.local_fetch())
        self.assertTrue(verification.verified, verification.error)
        self.assertEqual(CosignContainerProxyTests.forwarded(self, runner.argv), ["HOME=/tmp", "HTTPS_PROXY"])


class TrustRound7DockerFailureLineTests(TrustTestCase):
    """Which line of a failed `docker run` is named: never a warning printed before the error."""

    run_docker = DockerRunFailureTests.run_docker
    ADVICE = DockerRunFailureTests.ADVICE
    PODMAN_NOT_FOUND = ("Error: crun: executable file `cosign` not found in $PATH: No such file or directory: OCI "
                        "runtime attempted to invoke a command that was not found")
    PODMAN_WARNING = 'WARN[0000] "/" is not a shared mount, this could cause issues or missing mounts with rootless containers'

    def assertDockerFailure(self, verification, detail):
        self.assertFalse(verification.verified)
        self.assertEqual(verification.error, f"could not run cosign from {PINNED_IMAGE}: {detail}. {self.ADVICE}")

    def test_exit_127_without_a_docker_prefix_is_dockers_own_failure(self):
        # Podman (as docker) exits 127 when the image has no cosign to run; no line starts with "docker: ".
        self.assertDockerFailure(self.run_docker(127, self.PODMAN_NOT_FOUND + "\n"), self.PODMAN_NOT_FOUND)
        self.assertDockerFailure(self.run_docker(127, ""), "docker run exited with status 127")
        # Every other status is cosign's answer.
        for returncode in (1, 124, 128):
            with self.subTest(returncode=returncode):
                verification = self.run_docker(returncode, self.PODMAN_NOT_FOUND + "\n")
                self.assertEqual(verification.error, f"signature verification failed: {self.PODMAN_NOT_FOUND}")

    def test_a_warning_before_the_error_is_not_named_as_the_cause(self):
        warnings = (self.PODMAN_WARNING,
                    "WARNING: The requested image's platform (linux/amd64) does not match the detected host platform "
                    "(linux/arm64/v8) and no specific platform was requested",
                    'time="2026-10-22T10:00:00Z" level=warning msg="cgroupv2 manager is set to systemd"')
        for warning in warnings:
            with self.subTest(warning=warning[:30]):
                verification = self.run_docker(127, f"{warning}\n{self.PODMAN_NOT_FOUND}\n")
                self.assertDockerFailure(verification, self.PODMAN_NOT_FOUND)
        # A warning that is all there is still says something.
        self.assertDockerFailure(self.run_docker(126, self.PODMAN_WARNING + "\n"), self.PODMAN_WARNING)

    def test_the_first_of_several_errors_is_named(self):
        first = "Error: initializing source docker://ghcr.io/sigstore/cosign/cosign:v3.1.3: pinging container registry"
        stderr = f"Trying to pull ghcr.io/sigstore/cosign/cosign:v3.1.3...\n{first}\nError: exit status 125\n"
        self.assertDockerFailure(self.run_docker(125, stderr), first)


class TrustRound7ReadLocalWithoutNonblockTests(TrustTestCase):
    """Where os.O_NONBLOCK is missing, a manifest and its bundle are still read (and only read)."""

    def test_a_local_release_is_read(self):
        nonblock = os.O_NONBLOCK
        del os.O_NONBLOCK
        try:
            fetched = self.local_fetch()
        finally:
            os.O_NONBLOCK = nonblock
        self.assertEqual(json.loads(fetched.manifest_path.read_bytes()), make_manifest())
        self.assertEqual(fetched.bundle_path.read_bytes(), BUNDLE_BYTES)
        self.assertEqual((self.tmp / "release" / BUNDLE_ASSET).read_bytes(), BUNDLE_BYTES)


class TrustRound7CosignVersionOnStderrTests(TrustTestCase):
    """A cosign that prints its version text on stderr (and has no --json) is still recognised."""

    def test_the_git_version_line_on_stderr_is_read(self):
        def probe(command, **kwargs):
            probe.calls.append(list(command))
            if command[1:] == ["version", "--json"]:
                return subprocess.CompletedProcess(list(command), 1, "", "Error: unknown flag: --json\n")
            return subprocess.CompletedProcess(list(command), 0, "", cosign_version_text("v3.0.2"))
        probe.calls = []

        verifier = self.verifier(probe=probe)
        self.assertEqual(verifier.cosign_version(COSIGN), ((3, 0, 2), "v3.0.2"))
        verification = verifier.verify(self.local_fetch())
        self.assertTrue(verification.verified, verification.error)
        self.assertEqual(verification.tool, COSIGN)
        self.assertEqual(self.runner.argv[0], COSIGN)
        self.assertEqual(probe.calls, [[COSIGN, "version", "--json"], [COSIGN, "version"]])


class TrustRound7OldCosignWithDockerOptionsTests(TrustTestCase):
    """An old cosign on PATH falls back to the image through the installation's docker words, options and all."""

    def test_the_docker_program_is_looked_up_not_its_last_option(self):
        which = FakeWhich({"cosign": COSIGN, "/usr/local/bin/docker": "/usr/local/bin/docker"})
        verification = self.verifier(which=which, probe=FakeProbe("v2.4.1"),
                                     docker=["/usr/local/bin/docker", "--context", "colima"]).verify(self.local_fetch())
        self.assertTrue(verification.verified, verification.error)
        self.assertEqual(self.runner.argv[:4], ["/usr/local/bin/docker", "--context", "colima", "run"])
        self.assertEqual(verification.tool, PINNED_IMAGE)
        self.assertEqual(which.calls, ["cosign", "/usr/local/bin/docker"])


class TrustRound7LoopbackProxyLastErrorTests(TrustTestCase):
    """With a loopback proxy left out, the last network error cosign printed is the one named (as for the others)."""

    def test_the_last_network_error_is_named(self):
        first = ("Error: getting trusted root: Get \"https://tuf-repo-cdn.sigstore.dev/timestamp.json\": dial tcp: "
                 "lookup tuf-repo-cdn.sigstore.dev: i/o timeout")
        last = ("main.go:74: error during command execution: Get \"https://tuf-repo-cdn.sigstore.dev/timestamp.json\": "
                "dial tcp: lookup tuf-repo-cdn.sigstore.dev: no such host")
        runner = FakeRunner(returncode=1, stderr=f"{first}\n{last}\n")
        verification = self.verifier(which=self.docker_only(), runner=runner,
                                     environ={"HTTPS_PROXY": "http://127.0.0.1:3128"}).verify(self.local_fetch())
        self.assertFalse(verification.verified)
        self.assertTrue(verification.error.startswith(
            f"cosign, run from {PINNED_IMAGE}, could not reach Sigstore: {last}. HTTPS_PROXY on this host points at a "
            "loopback address"), verification.error)
        self.assertNotIn(first, verification.error)


class TrustRound7UnencodableContextTests(_TrustRound4ProxyCase):
    """A context name no context can have (invalid bytes in DOCKER_CONTEXT, a lone surrogate in config.json) is no
    context: the "default" proxies entry applies, and verification goes on instead of crashing."""

    ALL = TrustRound4DockerProxyEntryTests.ALL
    held = TrustRound4DockerProxyEntryTests.held
    PROXIES = TrustRound5DockerContextHostTests.PROXIES

    def test_an_unencodable_context_name_picks_the_default_entry(self):
        self.docker_config({"proxies": self.PROXIES})
        # What os.environ holds for DOCKER_CONTEXT=$'fagi\xff' under a UTF-8 locale.
        self.assertEqual(self.held(dict(self.ALL, DOCKER_CONTEXT="fagi\udcff")), ["HTTPS_PROXY", "https_proxy"])
        self.docker_config({"currentContext": "fagi\ud800", "proxies": self.PROXIES})
        self.assertEqual(self.held(self.ALL), ["HTTPS_PROXY", "https_proxy"])
        self.assertTrue(self.run_docker(self.ALL).verified)


class TrustRound7PublicKeyFileTests(TrustTestCase):
    """FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY is read like the manifest: a pipe or device never hangs bin/update."""

    write_key = KeyVerificationTests.write_key

    @unittest.skipUnless(hasattr(os, "mkfifo"), "needs named pipes")
    def test_a_key_that_is_a_named_pipe_is_refused_without_hanging(self):
        fifo = self.tmp / "keys" / "cosign.pub"
        fifo.parent.mkdir()
        os.mkfifo(str(fifo))
        fetched = self.local_fetch()
        outcome = {}
        error = finishes(self, lambda: outcome.update(
            verification=self.verifier(FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY=str(fifo)).verify(fetched)))
        self.assertIsNone(error)
        verification = outcome["verification"]
        self.assertFalse(verification.verified)
        self.assertEqual(verification.error, f"cannot read FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY {fifo}: it is a named "
                                             "pipe, not a regular file")
        self.assertEqual(self.runner.calls, [])
        self.assertFalse((fetched.directory / "trusted-key.pub").exists())

    def test_a_key_larger_than_the_cap_is_refused(self):
        key = self.write_key(b"k" * (MAX_DOWNLOAD_BYTES + 1))
        fetched = self.local_fetch()
        verification = self.verifier(FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY=str(key)).verify(fetched)
        self.assertFalse(verification.verified)
        self.assertEqual(verification.error,
                         f"FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY {key} is larger than {MAX_DOWNLOAD_BYTES} bytes")
        self.assertEqual(self.runner.calls, [])
        self.assertFalse((fetched.directory / "trusted-key.pub").exists())

    def test_a_link_planted_where_the_key_copy_goes_is_never_written_through(self):
        key = self.write_key()
        fetched = self.local_fetch()
        target = self.tmp / "elsewhere" / "planted.pub"
        (fetched.directory / "trusted-key.pub").symlink_to(target)
        verification = self.verifier(FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY=str(key)).verify(fetched)
        self.assertFalse(verification.verified)
        # The key itself was read: what failed is bin/update's copy of it.
        self.assertEqual(verification.error, f"cannot copy FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY {key} to "
                                             f"{fetched.directory / 'trusted-key.pub'}: File exists")
        self.assertFalse(target.exists())
        self.assertFalse(target.parent.exists())
        self.assertEqual(self.runner.calls, [])

    def test_a_copy_that_cannot_be_written_is_not_blamed_on_the_key(self):
        # A full disk under the state directory: the operator must not go looking at a key file that is fine.
        key = self.write_key(b"the key")
        fetched = self.local_fetch()
        full = OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        for name, error in (("a full disk", full), ("an error without a reason", OSError("short write"))):
            with self.subTest(name):
                with mock.patch.object(trust, "_write_private", side_effect=error) as write:
                    verification = self.verifier(FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY=str(key)).verify(fetched)
                self.assertFalse(verification.verified)
                self.assertEqual(verification.error, f"cannot copy FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY {key} to "
                                                     f"{fetched.directory / 'trusted-key.pub'}: "
                                                     f"{error.strerror or error}")
                write.assert_called_once_with(fetched.directory / "trusted-key.pub", b"the key")
                self.assertEqual(self.runner.calls, [])

    def test_errors_reading_the_key_name_the_key_and_why(self):
        directory = self.tmp / "keys"
        directory.mkdir()
        missing = directory / "absent.pub"
        fetched = self.local_fetch()
        for key, why in ((missing, "No such file or directory"), (directory, "it is a directory, not a regular file")):
            with self.subTest(why=why):
                verification = self.verifier(FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY=str(key)).verify(fetched)
                self.assertFalse(verification.verified)
                self.assertEqual(verification.error, f"cannot read FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY {key}: {why}")
        self.assertEqual(self.runner.calls, [])
        self.assertFalse((fetched.directory / "trusted-key.pub").exists())

    def test_the_copy_holds_the_keys_bytes(self):
        key = self.write_key(b"k" * MAX_DOWNLOAD_BYTES)
        fetched = self.local_fetch()
        self.assertTrue(self.verifier(FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY=str(key)).verify(fetched).verified)
        self.assertEqual((fetched.directory / "trusted-key.pub").read_bytes(), b"k" * MAX_DOWNLOAD_BYTES)


class ConditionalTransportTests(TrustTestCase):
    def test_https_endpoint_normalization_and_rejections(self):
        self.assertEqual(trust.https_url("https://EXAMPLE.test:443"), "https://example.test/")
        self.assertEqual(trust.https_url("https://[::1]:8443/x"), "https://[::1]:8443/x")
        self.assertEqual(trust.https_url("https://host/x?a=1"), "https://host/x?a=1")
        for value in (None, "https://", "https://u:p@host/x", "http://host/x", "https://host:0/x",
                      "https://host:wrong/x", "https://host/#fragment", "https://host/\r\nSECRET", "https://host/ x",
                      "https://host/" + "x" * 8192, "https://host\\\\evil/x", "https://[broken/x", "https://host/é"):
            with self.subTest(value=str(value)[:80]):
                with self.assertRaisesRegex(ManifestError, "^unsafe HTTPS endpoint$"):
                    trust.https_url(value)

    def test_conditional_transport_reads_bounded_body_and_metadata(self):
        url = "https://example.test/manifest.json"
        response = FakeResponse(b"payload", url)
        response.status = 200
        response.headers = {"ETag": '"etag"', "Last-Modified": "yesterday", "Content-Length": "7", "Link": "link"}
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch.object(trust, "build_opener", return_value=opener):
            result = trust.http_get(url, {"If-None-Match": '"old"'}, timeout=90)
        self.assertEqual(result, trust.HttpResponse(200, b"payload", '"etag"', "yesterday", url, link="link"))
        request = opener.open.call_args[0][0]
        self.assertEqual(request.get_header("If-none-match"), '"old"')
        self.assertEqual(opener.open.call_args[1]["timeout"], 30)
        self.assertEqual(response.reads, [MAX_DOWNLOAD_BYTES + 1])
        self.assertTrue(response.closed)

    def test_truncated_and_oversized_transport_body_are_rejected(self):
        url = "https://example.test/manifest.json"
        for length in ("8", "-1", "not-an-integer"):
            response = FakeResponse(b"payload", url)
            response.headers = {"Content-Length": length}
            with mock.patch.object(trust, "build_opener") as builder:
                builder.return_value.open.return_value = response
                with self.assertRaisesRegex(ManifestError, "incomplete HTTP body"):
                    trust.http_get(url, {})
            self.assertTrue(response.closed)
        for body, error in ((b"x" * (MAX_DOWNLOAD_BYTES + 1), None),
                            (b"", http.client.IncompleteRead(b"partial", 123))):
            with mock.patch.object(trust, "build_opener") as builder:
                builder.return_value.open.return_value = FakeResponse(body, url, error)
                with self.assertRaises(ManifestError):
                    trust.http_get(url, {})

    def test_http_error_metadata_is_bounded_and_body_closed_not_read(self):
        url = "https://example.test/manifest.json"
        for status in (304, 403, 404, 410, 429, 500):
            headers = Message()
            headers["ETag"] = '"a"'
            headers["Retry-After"] = "120"
            body = mock.Mock()
            error = HTTPError(url, status, "UNTRUSTED", headers, body)
            with mock.patch.object(trust, "build_opener") as builder:
                builder.return_value.open.side_effect = error
                result = trust.http_get(url, {})
            self.assertEqual(result.status, status)
            self.assertEqual(result.etag, '"a"')
            self.assertEqual(result.retry_after, "120")
            self.assertEqual(result.body, b"")
            body.read.assert_not_called()
            body.close.assert_called()

    def test_redirects_validate_before_network_and_never_drain_body(self):
        from urllib.request import Request
        handler = trust._HTTPSRedirect(time.monotonic() + 30)
        handler.parent = mock.Mock()
        request = Request("https://example.test/manifest", headers={"Authorization": "secret", "Cookie": "secret",
                          "If-None-Match": '"a"', "If-Modified-Since": "yesterday"})
        for target in ("http://evil.test/m", "https://u:secret@evil.test/m", "file:///secret"):
            fp = mock.Mock()
            with self.assertRaises(ManifestError):
                handler.http_error_302(request, fp, 302, "redirect", {"location": target})
            fp.close.assert_called_once()
            fp.read.assert_not_called()
        handler.parent.open.assert_not_called()
        fp = mock.Mock()
        handler.http_error_302(request, fp, 302, "redirect", {"location": "https://cdn.test/asset?sig=x"})
        redirected = handler.parent.open.call_args[0][0]
        self.assertEqual(redirected.full_url, "https://cdn.test/asset?sig=x")
        for name in ("Authorization", "Cookie", "If-none-match", "If-modified-since"):
            self.assertIsNone(redirected.get_header(name))
        fp.read.assert_not_called()
        self.assertGreater(handler.parent.open.call_args[1]["timeout"], 0)
        handler.http_error_307(request, mock.Mock(), 307, "redirect", {"location": "/same-host"})
        redirected = handler.parent.open.call_args[0][0]
        self.assertEqual(redirected.get_header("Authorization"), "secret")
        self.assertEqual(redirected.get_header("If-none-match"), '"a"')

    def test_redirect_loops_and_deadline_are_bounded(self):
        from urllib.request import Request
        request = Request("https://example.test/m")
        handler = trust._HTTPSRedirect(time.monotonic() + 30)
        handler.parent = mock.Mock()
        for _ in range(5):
            handler.http_error_302(request, mock.Mock(), 302, "redirect", {"location": "/other"})
        with self.assertRaises(ManifestError):
            handler.http_error_302(request, mock.Mock(), 302, "redirect", {"location": "/other"})
        self.assertEqual(handler.parent.open.call_count, 5)
        handler = trust._HTTPSRedirect(time.monotonic() - 1)
        with self.assertRaises(ManifestError):
            handler.http_error_302(request, mock.Mock(), 302, "redirect", {"location": "/other"})
        handler = trust._HTTPSRedirect(time.monotonic() + 30)
        with self.assertRaises(ManifestError):
            handler.http_error_302(request, mock.Mock(), 302, "redirect", {})

    def test_injected_download_preserves_legacy_pair_fallback(self):
        source = "https://example.test/m.json"
        calls = []

        def download(url, **kwargs):
            calls.append(url)
            return encode(make_manifest()) if url == source else BUNDLE_BYTES

        fetched = fetch_manifest(source, self.workdir(), cwd=self.caller, download=download)
        self.assertEqual(fetched.data, make_manifest())
        self.assertEqual(fetched.bundle_path.read_bytes(), BUNDLE_BYTES)
        self.assertEqual(calls, [source, "https://example.test/" + BUNDLE_ASSET])
        self.assertEqual(self.web.calls if hasattr(self.web, "calls") else self.web.requests, [])

    def test_host_only_and_budget_do_not_change_manual_defaults(self):
        verifier = self.verifier(which=self.docker_only())
        verifier.host_only = True
        verification = verifier.verify(self.local_fetch())
        self.assertFalse(verification.verified)
        self.assertEqual(verification.error, "host_verifier_required")
        self.assertEqual(self.runner.calls, [])
        verifier.deadline = time.monotonic() - 1
        with self.assertRaisesRegex(VerificationError, "discovery_incomplete"):
            verifier.timeout(30)

    def test_anchor_fingerprint_covers_content_mode_and_claims(self):
        verifier = self.verifier()
        baseline = verifier.trust_fingerprint()
        for name, value in (("FUTURE_AGI_UPDATE_TRUSTED_IDENTITY", "https://other.test/{version}"),
                            ("FUTURE_AGI_UPDATE_TRUSTED_ISSUER", "https://other.test"),
                            ("FUTURE_AGI_UPDATE_COSIGN_OFFLINE", "true")):
            verifier.settings = raw_settings(**{name: value})
            self.assertNotEqual(baseline, verifier.trust_fingerprint())
        key = self.tmp / "key.pub"
        key.write_bytes(b"key-one")
        verifier.settings = raw_settings(FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY=str(key))
        old = verifier.trust_fingerprint()
        key.write_bytes(b"key-two")
        self.assertNotEqual(old, verifier.trust_fingerprint())
        same = self.tmp / "same.pub"
        same.write_bytes(b"key-two")
        current = verifier.trust_fingerprint()
        verifier.settings = raw_settings(FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY=str(same))
        self.assertEqual(current, verifier.trust_fingerprint())


if __name__ == "__main__":
    unittest.main()
