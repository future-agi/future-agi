"""Registering the pinned ready environments: the list, the download, the archive and the
bootstrap command that puts them on every organization's shelf."""

import gzip
import hashlib
import io
import json
import tarfile

import httpx
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from simulate.models import HarnessEnvironmentRelease, HarnessSuiteRelease
from simulate.services import harness_releases as releases
from simulate.services import ready_environments as ready
from simulate.services.ready_environments import ReadyEnvironmentError

URL = "https://github.com/future-agi/agent-learning-kit/releases/download/ready-environments-v1.2.3/"


def _seal(directory, name, document):
    manifest = {
        **document,
        "files": [
            {
                "path": path.relative_to(directory).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size": path.stat().st_size,
            }
            for path in sorted(directory.rglob("*"))
            if path.is_file()
        ],
    }
    manifest["digest"] = releases._release_digest(manifest)
    (directory / name).write_text(json.dumps(manifest))
    return manifest


def _sealed(root, name="retail"):
    """A sealed environment and a suite proved against it, as ALK writes them."""
    environment = root / "environment"
    environment.mkdir(parents=True)
    (environment / "tools.json").write_text(
        json.dumps([{"name": "get_order", "description": "", "parameters": {}}])
    )
    (environment / "policy.json").write_text("{}")
    release = _seal(
        environment,
        "release.json",
        {
            "schema_version": releases.ENVIRONMENT_RELEASE_SCHEMA,
            "name": name,
            "metadata": {"catalogue": {"title": name.title()}},
        },
    )
    suite = root / "suite"
    (suite / "scenarios/refund").mkdir(parents=True)
    (suite / "scenarios/refund/scenario.json").write_text('{"name": "refund"}')
    (suite / "proofs.json").write_text(
        json.dumps(
            {
                "runtime": {
                    "engine": "postgres",
                    "scenarios": {"refund": {"holds": True}},
                }
            }
        )
    )
    (suite / "provision.json").write_text(
        json.dumps({"personas": [{"scenario_key": "refund", "name": "Gareth"}]})
    )
    proved = _seal(
        suite,
        "suite.json",
        {
            "schema_version": releases.SUITE_RELEASE_SCHEMA,
            "name": f"{name}_base",
            "environment": {"digest": release["digest"]},
            "scenarios": [
                {"key": "refund", "path": "scenarios/refund", "origin": "base"}
            ],
        },
    )
    return release["digest"], proved["digest"]


def _pack(root):
    """The archive ALK's `fi.alk.environments.ready` writes: sorted files, no times."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        for part in ("environment", "suite"):
            for path in sorted((root / part).rglob("*")):
                if path.is_file():
                    body = path.read_bytes()
                    info = tarfile.TarInfo(path.relative_to(root).as_posix())
                    info.size = len(body)
                    tar.addfile(info, io.BytesIO(body))
    return gzip.compress(raw.getvalue(), mtime=0)


def _tar(members):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        for info, body in members:
            tar.addfile(info, io.BytesIO(body) if body is not None else None)
    return gzip.compress(raw.getvalue(), mtime=0)


def _file(name, body=b"x"):
    info = tarfile.TarInfo(name)
    info.size = len(body)
    return info, body


@pytest.fixture
def stored(monkeypatch):
    """The object store registration uploads archives to."""
    objects = {}
    monkeypatch.setattr(releases, "_upload", objects.__setitem__)
    return objects


@pytest.fixture
def published(tmp_path):
    """Two ready environments as a release publishes them: archives at URL, and the list."""
    archives = {}
    entries = []
    for name in ("banking", "retail"):
        environment, suite = _sealed(tmp_path / "sealed" / name, name)
        body = _pack(tmp_path / "sealed" / name)
        archive = f"ready-environment-{name}.tar.gz"
        archives[URL + archive] = body
        entries.append(
            {
                "name": name,
                "archive": archive,
                "sha256": hashlib.sha256(body).hexdigest(),
                "size": len(body),
                "environment": environment,
                "suite": suite,
            }
        )
    pinned = tmp_path / "ready_environments.json"
    pinned.write_text(
        json.dumps(
            {
                "schema_version": ready.SCHEMA,
                "alk_version": "1.2.3",
                "alk_source_revision": "abc123",
                "download_url": URL,
                "environments": entries,
            }
        )
    )
    return pinned, archives, entries


@pytest.fixture
def fetched(published):
    """Every download, served from the published archives and checked as `download` checks."""
    _, archives, _ = published
    calls = []

    def fetch(url, *, sha256, size):
        calls.append(url)
        body = archives[url]
        if len(body) != size or hashlib.sha256(body).hexdigest() != sha256:
            raise ReadyEnvironmentError(f"{url} is not the archive the list names")
        return body

    fetch.calls = calls
    return fetch


@pytest.mark.django_db
class TestSeed:
    def test_registers_each_pinned_environment_as_a_system_release(
        self, published, fetched, stored
    ):
        pinned, _, entries = published

        seeded = ready.seed(pinned, fetch=fetched)

        assert seeded.registered == ["banking", "retail"]
        assert seeded.failed == {}
        for entry in entries:
            environment = HarnessEnvironmentRelease.no_workspace_objects.get(
                digest=entry["environment"]
            )
            assert environment.organization is None
            assert environment.name == entry["name"]
            suite = HarnessSuiteRelease.no_workspace_objects.get(digest=entry["suite"])
            assert suite.organization is None and suite.environment == environment
        assert len(stored) == 4
        assert [one["name"] for one in releases.list_releases(None)] == [
            "banking",
            "retail",
        ]

    def test_a_second_run_downloads_nothing(self, published, fetched, stored):
        pinned, _, _ = published
        ready.seed(pinned, fetch=fetched)
        fetched.calls.clear()

        seeded = ready.seed(pinned, fetch=fetched)

        assert seeded.present == ["banking", "retail"]
        assert seeded.registered == [] and fetched.calls == []
        assert HarnessEnvironmentRelease.no_workspace_objects.count() == 2

    def test_a_removed_environment_stays_off_the_shelf(
        self, published, fetched, stored
    ):
        pinned, _, entries = published
        ready.seed(pinned, fetch=fetched)
        HarnessEnvironmentRelease.no_workspace_objects.filter(
            digest=entries[0]["environment"]
        ).update(deleted=True)
        fetched.calls.clear()

        seeded = ready.seed(pinned, fetch=fetched)

        assert "banking" in seeded.present and fetched.calls == []

    def test_one_that_fails_leaves_the_others_registered(
        self, published, fetched, stored
    ):
        pinned, archives, _ = published
        archives[URL + "ready-environment-banking.tar.gz"] = b"tampered"

        seeded = ready.seed(pinned, fetch=fetched)

        assert seeded.registered == ["retail"]
        assert list(seeded.failed) == ["banking"]
        assert "not the archive the list names" in seeded.failed["banking"]
        assert HarnessEnvironmentRelease.no_workspace_objects.count() == 1

    def test_an_archive_of_other_releases_is_refused(
        self, published, fetched, stored, tmp_path
    ):
        pinned, archives, entries = published
        # A correctly sealed archive, but of another world than the list names.
        _sealed(tmp_path / "other", "hr")
        body = _pack(tmp_path / "other")
        archives[URL + entries[0]["archive"]] = body
        entries[0].update(sha256=hashlib.sha256(body).hexdigest(), size=len(body))
        document = json.loads(pinned.read_text())
        document["environments"] = entries
        pinned.write_text(json.dumps(document))

        seeded = ready.seed(pinned, fetch=fetched)

        assert "other releases than the list names" in seeded.failed["banking"]

    def test_a_tampered_release_inside_the_archive_is_refused(
        self, published, fetched, stored, tmp_path
    ):
        pinned, archives, entries = published
        root = tmp_path / "sealed" / "banking"
        (root / "environment/tools.json").write_text("[]")
        body = _pack(root)
        archives[URL + entries[0]["archive"]] = body
        entries[0].update(sha256=hashlib.sha256(body).hexdigest(), size=len(body))
        document = json.loads(pinned.read_text())
        document["environments"] = entries
        pinned.write_text(json.dumps(document))

        seeded = ready.seed(pinned, fetch=fetched)

        assert seeded.failed["banking"].startswith("release_invalid:")
        assert not HarnessEnvironmentRelease.no_workspace_objects.filter(
            name="banking"
        ).exists()

    def test_an_empty_list_registers_nothing(self, tmp_path, fetched):
        pinned = tmp_path / "ready_environments.json"
        pinned.write_text(
            json.dumps(
                {"schema_version": ready.SCHEMA, "download_url": "", "environments": []}
            )
        )

        seeded = ready.seed(pinned, fetch=fetched)

        assert (seeded.registered, seeded.present, seeded.failed) == ([], [], {})
        assert fetched.calls == []


class TestPinned:
    def test_the_committed_list_is_valid(self):
        ready.pinned()

    @pytest.mark.parametrize(
        "change",
        [
            {"schema_version": "another"},
            {"download_url": "http://example.com/"},
            {"download_url": "https://example.com/no-slash"},
            {"environments": None},
        ],
        ids=["schema", "not-https", "not-a-folder", "no-list"],
    )
    def test_a_malformed_list_is_refused(self, published, change):
        pinned, _, _ = published
        pinned.write_text(json.dumps({**json.loads(pinned.read_text()), **change}))

        with pytest.raises(ReadyEnvironmentError):
            ready.pinned(pinned)

    @pytest.mark.parametrize(
        "change",
        [
            {"archive": "../ready-environment-banking.tar.gz"},
            {"archive": "banking.zip"},
            {"name": "Banking!"},
            {"sha256": "nope"},
            {"size": 0},
            {"size": ready.MAX_ARCHIVE_BYTES + 1},
            {"environment": "sha256:short"},
            {"suite": None},
        ],
        ids=[
            "archive-path",
            "archive-name",
            "name",
            "sha256",
            "empty",
            "too-large",
            "environment",
            "suite",
        ],
    )
    def test_an_invalid_entry_is_refused(self, published, change):
        pinned, _, entries = published
        document = json.loads(pinned.read_text())
        document["environments"] = [{**entries[0], **change}]
        pinned.write_text(json.dumps(document))

        with pytest.raises(ReadyEnvironmentError):
            ready.pinned(pinned)

    def test_a_name_listed_twice_is_refused(self, published):
        pinned, _, entries = published
        document = json.loads(pinned.read_text())
        document["environments"] = [entries[0], entries[0]]
        pinned.write_text(json.dumps(document))

        with pytest.raises(ReadyEnvironmentError, match="twice"):
            ready.pinned(pinned)


class TestUnpack:
    def test_writes_the_environment_and_the_suite(self, tmp_path):
        ready.unpack(
            _tar([_file("environment/tools.json", b"[]"), _file("suite/suite.json")]),
            tmp_path,
        )

        assert (tmp_path / "environment/tools.json").read_bytes() == b"[]"
        assert (tmp_path / "suite/suite.json").is_file()

    @pytest.mark.parametrize(
        "name",
        ["../escape", "/etc/passwd", "environment/../../escape", "other/file", "loose"],
    )
    def test_a_path_outside_the_release_is_refused(self, tmp_path, name):
        with pytest.raises(ReadyEnvironmentError, match="outside the release"):
            ready.unpack(_tar([_file(name)]), tmp_path / "out")

        assert not (tmp_path / "escape").exists()

    @pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE])
    def test_a_link_is_refused(self, tmp_path, kind):
        link = tarfile.TarInfo("environment/tools.json")
        link.type, link.linkname = kind, "/etc/passwd"

        with pytest.raises(ReadyEnvironmentError, match="not a regular file"):
            ready.unpack(_tar([(link, None)]), tmp_path)

    def test_more_than_a_release_holds_is_refused(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ready, "MAX_FILES", 1)

        with pytest.raises(ReadyEnvironmentError, match="larger than a release"):
            ready.unpack(
                _tar([_file("environment/a"), _file("environment/b")]), tmp_path
            )


class TestDownload:
    def _serve(self, monkeypatch, handler):
        transport = httpx.MockTransport(handler)
        real = httpx.Client

        monkeypatch.setattr(
            ready.httpx,
            "Client",
            lambda **kwargs: real(transport=transport, **kwargs),
        )

    def test_follows_the_release_redirect_and_checks_the_bytes(self, monkeypatch):
        body = b"archive"

        def handler(request):
            if request.url.host == "github.com":
                return httpx.Response(
                    302, headers={"location": "https://objects.example/a"}
                )
            return httpx.Response(200, content=body)

        self._serve(monkeypatch, handler)

        assert (
            ready.download(
                URL + "a.tar.gz",
                sha256=hashlib.sha256(body).hexdigest(),
                size=len(body),
            )
            == body
        )

    @pytest.mark.parametrize(
        ("served", "match"),
        [(b"other", "not the archive"), (b"x" * 100, "larger than")],
        ids=["changed", "too-large"],
    )
    def test_other_bytes_are_refused(self, monkeypatch, served, match):
        self._serve(monkeypatch, lambda request: httpx.Response(200, content=served))

        with pytest.raises(ReadyEnvironmentError, match=match):
            ready.download(
                URL + "a.tar.gz", sha256=hashlib.sha256(b"archive").hexdigest(), size=7
            )

    def test_a_missing_archive_is_an_http_error(self, monkeypatch):
        self._serve(monkeypatch, lambda request: httpx.Response(404))

        with pytest.raises(httpx.HTTPStatusError):
            ready.download(URL + "a.tar.gz", sha256="0" * 64, size=1)


@pytest.mark.django_db
class TestCommand:
    def test_reports_what_it_registered(self, published, fetched, stored, monkeypatch):
        pinned, _, _ = published
        monkeypatch.setattr(ready, "download", fetched)
        out = io.StringIO()

        call_command("seed_ready_environments", "--list", str(pinned), stdout=out)

        assert "2 registered, 0 already present, 0 failed" in out.getvalue()

    def test_fails_when_one_was_not_registered(
        self, published, fetched, stored, monkeypatch
    ):
        pinned, archives, _ = published
        archives[URL + "ready-environment-banking.tar.gz"] = b"tampered"
        monkeypatch.setattr(ready, "download", fetched)
        out, err = io.StringIO(), io.StringIO()

        with pytest.raises(CommandError, match="1 ready environment"):
            call_command(
                "seed_ready_environments",
                "--list",
                str(pinned),
                stdout=out,
                stderr=err,
            )

        assert "1 registered, 0 already present, 1 failed" in out.getvalue()
        assert "banking:" in err.getvalue()

    def test_a_malformed_list_fails(self, tmp_path):
        broken = tmp_path / "ready_environments.json"
        broken.write_text("{")

        with pytest.raises(CommandError, match="not readable JSON"):
            call_command("seed_ready_environments", "--list", str(broken))
