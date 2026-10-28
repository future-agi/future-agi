import io
import json
import os
import shlex
import subprocess
import sys
import tarfile
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from minio.error import S3Error
from structlog.testing import capture_logs

from simulate.models import (
    HarnessAgentVersionRuntime,
    HarnessAssembledBundle,
    HostedHarnessAttempt,
    HostedHarnessJob,
    HostedHarnessScenario,
)
from simulate.services import hosted_harness_gateway as gateway_module
from simulate.services.harness_capacity import SandboxCapacity
from simulate.services.harness_versioning import archives, assembly
from simulate.services.harness_versioning.backfill import backfill_environment
from simulate.services.harness_versioning.versions import record_authoring
from simulate.services.hosted_harness import (
    HostedHarnessError,
    create_selected_harness_run,
    register_attempt,
)
from simulate.services.hosted_harness_gateway import (
    HostedHarnessGateway,
    _empty_source_archive,
)
from simulate.tests.test_harness_amend_archive import NAMES, _key
from simulate.tests.test_harness_versioning_backfill import (
    _agent_version,
    _definition,
    _legacy,
)
from simulate.tests.test_harness_versioning_backfill import (
    storage as storage,  # noqa: F401 - fixture
)
from simulate.tests.test_harness_versioning_record import _authoring, _scenario
from simulate.tests.test_harness_versioning_runs import _run
from simulate.tests.test_hosted_harness_gateway import _Daytona

pytestmark = pytest.mark.django_db

SOURCE_DIGEST = "e" * 64
BUNDLE_DIGEST = "sha256:" + "d" * 64
MANIFEST = {
    "files": [{"path": "world.sqlite", "sha256": "a" * 64, "size": 2}],
    "digest": BUNDLE_DIGEST,
    "provenance": {"source_digest": SOURCE_DIGEST},
}


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False


def _certificate(digest=BUNDLE_DIGEST):
    return {"status": "passed", "compiler": {"bundle_digest": digest}}


def _sealed_authoring(*, digest=BUNDLE_DIGEST, certified=None, **extra):
    """An authoring tree whose sealed bundle has ``digest`` and a certificate for
    ``certified`` (the same bundle unless given)."""
    return _authoring(
        **{
            "environment-bundle/manifest.json": json.dumps(
                {**MANIFEST, "digest": digest}
            ),
            "runtime-validation.json": json.dumps(_certificate(certified or digest)),
            "generic-harness/certification.json": json.dumps({"status": "certified"}),
            **extra,
        }
    )


def _graded_authoring(sub_goals):
    """A sealed tree whose scenarios grade these sub-goals; its catalogue is ``refunded``.

    A scenario given ``None`` has no ``sub_goals`` field.
    """
    documents = [
        {**_scenario(name), **({} if goals is None else {"sub_goals": goals})}
        for name, goals in sub_goals.items()
    ]
    files = {"scenarios.json": json.dumps(documents)}
    for document in documents:
        files[f"scenarios/{document['name']}/scenario.json"] = json.dumps(document)
    return _sealed_authoring(**files)


def _environment(user, workspace, key, storage, *, body=None):
    """A backfilled environment whose sealed bundle is cached for A1 × E1 × S1."""
    definition = _definition(user, workspace)
    _agent_version(definition)
    environment = _legacy(
        user,
        workspace,
        key,
        storage,
        body=_sealed_authoring() if body is None else body,
        definition=definition,
    )
    backfill_environment(environment.id)
    environment.refresh_from_db()
    assert environment.assembled_bundle_id is not None
    return environment


def _row(environment):
    return HarnessAssembledBundle.no_workspace_objects.get(
        id=environment.assembled_bundle_id
    )


def _uncache(environment):
    HarnessAssembledBundle._base_manager.filter(job=environment).update(
        state="pending", object_key=None, certificate=None
    )


def _files(body):
    return archives.read_archive(body)


@pytest.fixture
def gateway(monkeypatch, settings):
    settings.ALK_HOSTED_BASE_EGRESS_DOMAINS = ["ingest.example.com"]
    settings.ALK_HOSTED_AUTHORING_MAX_DURATION_SECONDS = 3600
    settings.ALK_HOSTED_SANDBOX_TTL_SECONDS = 7200
    monkeypatch.setattr(
        "simulate.services.hosted_harness_gateway.HostedSourceAcquirer.acquire",
        lambda _self, _job: (_empty_source_archive(), ""),
    )
    monkeypatch.setattr(
        "simulate.services.hosted_harness_gateway.PlatformSecretResolver.resolve",
        lambda _self, _job: {},
    )
    monkeypatch.setattr(
        "simulate.services.hosted_harness_gateway._platform_simulator_material",
        lambda: {"AGENTCC_BASE_URL": "https://gateway.example.test"},
    )
    monkeypatch.setattr(
        "simulate.services.harness_capacity.configured_capacity",
        lambda _payload: SandboxCapacity("selected", 2, 4096, 10, 1),
    )
    instance = object.__new__(HostedHarnessGateway)
    instance.client = _Daytona()
    instance.snapshot = "alk-hosted-v1"
    instance.snapshot_digest = ""
    return instance


def _launch(gateway, run):
    attempt = gateway.launch(run, endpoint_base_url="https://platform.example.com")
    run.refresh_from_db()
    return attempt


def _command(gateway):
    return gateway.client.sandbox.process.session_request.command


def _move_defaults(environment):
    """A new build, so runs made before it are pinned to other than the defaults."""
    record_authoring(
        environment,
        _sealed_authoring(contract={"agent": "other", "tools": []}),
        origin="authoring",
    )
    environment.refresh_from_db()


def _off_defaults(run):
    """As a rerun pinned to versions other than its environment's defaults."""
    payload = dict(run.payload)
    metadata = dict(payload["metadata"])
    metadata["assembly"] = {**metadata["assembly"], "defaults": False}
    payload["metadata"] = metadata
    HostedHarnessJob.no_workspace_objects.filter(id=run.id).update(payload=payload)
    run.refresh_from_db()
    return run


def _as_provider_agent(run):
    payload = dict(run.payload)
    payload["source"] = {"kind": "provider"}
    HostedHarnessJob.no_workspace_objects.filter(id=run.id).update(payload=payload)
    run.refresh_from_db()


def _exit_78(gateway, monkeypatch):
    monkeypatch.setattr(
        gateway,
        "inspect",
        lambda _: {"exit_code": 78, "logs": "rejected", "process_logs": ""},
    )
    monkeypatch.setattr(
        gateway,
        "_delete_and_record",
        lambda attempt, *, retry_pending=False: attempt.job,
    )


class TestKey:
    def test_runs_and_the_backfill_agree_on_the_key(self, user, workspace, storage):
        environment = _environment(user, workspace, "asm-key", storage)
        row = _row(environment)
        runtime = HarnessAgentVersionRuntime.no_workspace_objects.get(
            agent_version_id=environment.agent_version_id
        )

        run = _run(environment)

        assert row.assembly_key == assembly.bundle_key(
            runtime, environment.environment_version, environment.suite_version
        )
        assert run.assembled_bundle_id == row.id
        assert run.payload["metadata"]["version_pins"]["assembly_key"] == (
            row.assembly_key
        )

    def test_the_backfilled_row_keeps_the_source_it_was_built_from(
        self, user, workspace, storage
    ):
        row = _row(_environment(user, workspace, "asm-key-digest", storage))

        assert row.certificate == {
            **_certificate(),
            "source_digest": SOURCE_DIGEST,
        }
        assert row.bundle_digest == BUNDLE_DIGEST


class TestSeeding:
    def _rebuilt(self, environment, origin="authoring", **sealed):
        body = _sealed_authoring(
            contract={"agent": "support", "tools": ["refund"]}, **sealed
        )
        return record_authoring(environment, body, origin=origin)

    def test_a_build_caches_its_sealed_bundle_for_the_new_pair(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, "asm-seed", storage)

        recorded = self._rebuilt(environment)

        row = HarnessAssembledBundle.no_workspace_objects.get(
            job=environment, environment_version=recorded.environment
        )
        assert (row.state, row.agent_version_id) == (
            "ready",
            environment.agent_version_id,
        )
        assert row.suite_version_id == recorded.suite.id
        assert row.certificate["source_digest"] == SOURCE_DIGEST
        assert row.bundle_digest == BUNDLE_DIGEST
        files = _files(storage.objects[row.object_key])
        assert "environment-bundle/manifest.json" in files
        environment.refresh_from_db()
        assert _run(environment).assembled_bundle_id == row.id

    def test_an_edit_does_not_cache_the_bundle_it_carries(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, "asm-seed-edit", storage)

        recorded = self._rebuilt(environment, origin="manual")

        assert not HarnessAssembledBundle.no_workspace_objects.filter(
            environment_version=recorded.environment
        ).exists()

    def test_a_bundle_its_certificate_is_not_for_is_not_cached(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, "asm-seed-stale", storage)

        with capture_logs() as logs:
            recorded = self._rebuilt(environment, certified="sha256:" + "0" * 64)

        assert not HarnessAssembledBundle.no_workspace_objects.filter(
            environment_version=recorded.environment
        ).exists()
        assert {
            "outcome": "seed_skipped",
            "reason": "certificate_digest_mismatch",
        }.items() <= next(one for one in logs if "outcome" in one).items()

    def test_a_bundle_without_a_digest_is_not_cached(self, user, workspace, storage):
        older = _authoring()
        assert not assembly.certifies_bundle(_files(older))

        environment = _legacy(user, workspace, "asm-seed-older", storage, body=older)
        backfill_environment(environment.id)

        environment.refresh_from_db()
        assert environment.environment_version_id is not None
        assert environment.assembled_bundle_id is None

    def test_a_newer_build_replaces_the_bundle_of_the_same_pair(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, "asm-seed-newer", storage)
        recorded = self._rebuilt(environment)
        before = HarnessAssembledBundle.no_workspace_objects.get(
            job=environment, environment_version=recorded.environment
        )
        newer = "sha256:" + "a" * 64

        again = self._rebuilt(environment, digest=newer)

        after = HarnessAssembledBundle.no_workspace_objects.get(id=before.id)
        assert (again.environment, again.suite) == (
            recorded.environment,
            recorded.suite,
        )
        assert (after.state, after.bundle_digest) == ("ready", newer)
        assert after.object_key != before.object_key

    def test_a_seeding_error_never_fails_the_save(
        self, user, workspace, storage, monkeypatch
    ):
        environment = _environment(user, workspace, "asm-seed-error", storage)

        def broken(*_args, **_kwargs):
            HarnessAssembledBundle._base_manager.filter(job=environment).delete()
            raise RuntimeError("seeding broke")

        monkeypatch.setattr(assembly, "seed_from_build", broken)

        recorded = self._rebuilt(environment)

        environment.refresh_from_db()
        assert environment.environment_version_id == recorded.environment.id
        # The savepoint rolled the half-done seeding back.
        assert HarnessAssembledBundle.no_workspace_objects.filter(
            job=environment
        ).exists()

    def test_nothing_is_cached_without_an_agent_side_row(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, "asm-seed-noside", storage)
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_version_id=environment.agent_version_id
        ).delete()

        recorded = self._rebuilt(environment)

        assert not HarnessAssembledBundle.no_workspace_objects.filter(
            environment_version=recorded.environment
        ).exists()


class TestRunCreate:
    def test_a_ready_row_is_a_hit(self, user, workspace, storage):
        environment = _environment(user, workspace, "asm-hit", storage)
        row = _row(environment)

        run = _run(environment)

        assert run.payload["metadata"]["assembly"] == {
            "key": row.assembly_key,
            "bundle_id": str(row.id),
            "cached": True,
            "defaults": True,
        }

    def test_a_new_combination_is_a_pending_miss(self, user, workspace, storage):
        environment = _environment(user, workspace, "asm-miss", storage)
        HarnessAssembledBundle._base_manager.filter(job=environment).delete()

        run = _run(environment)

        row = HarnessAssembledBundle.no_workspace_objects.get(job=environment)
        assert row.state == "pending"
        assert run.assembled_bundle_id == row.id
        assert run.payload["metadata"]["assembly"]["cached"] is False

    def test_a_failed_row_is_built_again(self, user, workspace, storage):
        environment = _environment(user, workspace, "asm-failed", storage)
        HarnessAssembledBundle._base_manager.filter(job=environment).update(
            state="failed", error={"code": "x"}
        )

        run = _run(environment)

        row = _row(environment)
        assert (row.state, row.error, row.object_key) == ("pending", None, None)
        assert run.payload["metadata"]["assembly"]["cached"] is False

    def test_without_a_side_row_the_run_is_not_assembled(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, "asm-noside", storage)
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_version_id=environment.agent_version_id
        ).delete()

        run = _run(environment)

        assert run.assembled_bundle_id is None
        assert "assembly" not in run.payload["metadata"]
        assert "assembly_key" not in run.payload["metadata"]["version_pins"]

    def test_with_the_flag_off_the_run_is_not_assembled(
        self, user, workspace, storage, settings
    ):
        environment = _environment(user, workspace, "asm-flag-off", storage)
        settings.HARNESS_VERSIONING_ENABLED = False

        run = _run(environment)

        assert run.assembled_bundle_id is None
        assert "assembly" not in run.payload["metadata"]

    def test_a_scenario_that_grades_no_sub_goal_is_refused(
        self, user, workspace, storage
    ):
        body = _graded_authoring({NAMES[0]: ["unknown"], NAMES[1]: ["refunded"]})
        environment = _environment(
            user, workspace, "asm-ungradable", storage, body=body
        )

        with pytest.raises(HostedHarnessError) as error:
            create_selected_harness_run(
                environment,
                scenario_keys=[_key(NAMES[0]), _key(NAMES[1])],
                trials=1,
                idempotency_key="r",
            )

        assert (error.value.code, error.value.status_code) == (
            "scenario_ungradable",
            422,
        )
        assert error.value.message.startswith(f"{_key(NAMES[0])} grades no sub-goal")
        assert _run(environment, [_key(NAMES[1])], key="r2").assembled_bundle_id

    def test_a_scenario_without_sub_goals_is_not_refused(
        self, user, workspace, storage
    ):
        body = _graded_authoring({NAMES[0]: None, NAMES[1]: ["refunded"]})
        environment = _environment(
            user, workspace, "asm-no-sub-goals", storage, body=body
        )

        run = _run(environment, [_key(NAMES[0])])

        assert run.payload["metadata"]["assembly"]["cached"] is True

    @pytest.mark.parametrize("certify", [False, True])
    def test_a_miss_that_runs_the_default_pair_is_not_refused(
        self, certify, user, workspace, storage, settings
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = certify
        body = _graded_authoring({NAMES[0]: ["unknown"], NAMES[1]: ["refunded"]})
        environment = _environment(
            user, workspace, f"asm-ungradable-miss-{certify}", storage, body=body
        )
        _uncache(environment)

        run = _run(environment, [_key(NAMES[0])])

        assert run.payload["metadata"]["assembly"]["cached"] is False

    def test_todays_path_never_refuses_an_ungradable_scenario(
        self, user, workspace, storage, settings
    ):
        body = _graded_authoring({NAMES[0]: [], NAMES[1]: ["refunded"]})
        environment = _environment(
            user, workspace, "asm-ungradable-off", storage, body=body
        )
        settings.HARNESS_VERSIONING_ENABLED = False

        run = _run(environment, [_key(NAMES[0])])

        assert run.assembled_bundle_id is None


class TestLaunchHit:
    def test_the_cached_bundle_is_replayed_after_a_source_check(
        self, user, workspace, storage, gateway
    ):
        environment = _environment(user, workspace, "asm-launch-hit", storage)
        run = _run(environment)

        attempt = _launch(gateway, run)

        assert attempt.state == HostedHarnessAttempt.State.RUNNING
        command = _command(gateway)
        check = "from fi.alk.harness.provision import source_fingerprint"
        copy = "cp -a /work/authoring/environment-bundle /work/bundle"
        assert check in command and SOURCE_DIGEST in command
        assert command.index(check) < command.index(copy)
        assert "sys.exit(78)" in command
        assert "--certify-only" not in command
        assert "/work/certify-job.json" not in gateway.client.sandbox.fs.uploads
        metadata = run.payload["metadata"]
        assert metadata["authoring_object_key"].startswith("harness-assembled/")
        assert metadata["assembly"]["outcome"] == "hit"
        files = _files(gateway.client.sandbox.fs.uploads["/work/authoring.tar.gz"])
        assert json.loads(files["environment-bundle/manifest.json"]) == MANIFEST
        assert "runtime-validation.json" in files
        # Every sealed file is replayed, so the run reads its certification as today.
        assert "generic-harness/certification.json" in files
        assert f"scenarios/{NAMES[0]}/scenario.json" in files
        assert run.bundle_digest == BUNDLE_DIGEST

    def test_a_second_run_reuses_the_assembled_archive(
        self, user, workspace, storage, monkeypatch
    ):
        environment = _environment(user, workspace, "asm-reuse", storage)
        first, second = _run(environment, key="r1"), _run(environment, key="r2")
        composed = []
        real_compose = archives.compose
        monkeypatch.setattr(
            archives,
            "compose",
            lambda *parts: (composed.append(1), real_compose(*parts))[1],
        )

        assembly.prepare_launch(first)
        with patch.object(archives, "get_archive") as download:
            assert assembly.prepare_launch(second).hit

        first.refresh_from_db()
        second.refresh_from_db()
        assert len(composed) == 1
        download.assert_not_called()
        assert second.payload["metadata"]["authoring_object_key"] == (
            first.payload["metadata"]["authoring_object_key"]
        )

    def test_a_row_without_a_source_digest_checks_the_certificates(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, "asm-hit-old-row", storage)
        first, second = _run(environment, key="r1"), _run(environment, key="r2")
        assert assembly.prepare_launch(first).hit
        HarnessAssembledBundle._base_manager.filter(job=environment).update(
            certificate={**_certificate(), "source": {"digest": SOURCE_DIGEST}}
        )

        # The archive is built already, so only the row's certificate is read.
        with patch.object(archives, "get_archive") as download:
            plan = assembly.prepare_launch(second)

        download.assert_not_called()
        assert plan.source_digest == SOURCE_DIGEST
        assert _row(environment).certificate["source_digest"] == SOURCE_DIGEST

    def test_a_hit_keeps_the_sealed_bundles_contract(self, user, workspace, storage):
        environment = _environment(user, workspace, "asm-hit-prompt", storage)
        run = _run(environment)
        _as_provider_agent(run)

        assert assembly.prepare_launch(run).hit

        run.refresh_from_db()
        files = _files(storage.objects[run.payload["metadata"]["authoring_object_key"]])
        assert "system_prompt_excerpt" not in json.loads(files["contract.json"])

    def test_a_storage_outage_leaves_the_row_ready(
        self, user, workspace, storage, gateway, monkeypatch
    ):
        environment = _environment(user, workspace, "asm-outage", storage)
        run = _run(environment)

        def unavailable(_key):
            raise S3Error(None, "SlowDown", "busy", _key, "request", "host")

        monkeypatch.setattr(archives, "get_archive", unavailable)

        with pytest.raises(S3Error):
            gateway.launch(run, endpoint_base_url="https://platform.example.com")

        assert _row(environment).state == "ready"
        assert not run.attempts.exists()

    def test_an_unreadable_cached_object_is_built_again(self, user, workspace, storage):
        environment = _environment(user, workspace, "asm-unreadable", storage)
        run = _run(environment)
        storage.objects[_row(environment).object_key] = b"not an archive"

        with capture_logs() as logs:
            assert assembly.prepare_launch(run) is None

        assert _row(environment).state == "pending"
        assert "unreadable" in [one.get("reason") for one in logs]

    def test_a_failed_source_check_puts_the_row_back_to_pending(
        self, user, workspace, storage, gateway, monkeypatch
    ):
        environment = _environment(user, workspace, "asm-hit-78", storage)
        run = _run(environment)
        attempt = _launch(gateway, run)
        _exit_78(gateway, monkeypatch)

        gateway.reconcile_completed(attempt)

        row = _row(environment)
        assert (row.state, row.object_key, row.certificate) == ("pending", None, None)

    def test_the_source_check_is_runnable_shell(self, tmp_path):
        package = tmp_path / "fi" / "alk" / "harness"
        package.mkdir(parents=True)
        for folder in (tmp_path / "fi", tmp_path / "fi" / "alk", package):
            (folder / "__init__.py").write_text("")
        (package / "provision.py").write_text(
            "import os\n\n\ndef source_fingerprint(path):\n"
            "    assert path == '/work/source'\n"
            "    return os.environ['FOUND']\n"
        )
        command = assembly.source_check_command(SOURCE_DIGEST)
        assert command.startswith("python -c ") and command.endswith(" && ")
        runnable = shlex.quote(sys.executable) + command.removeprefix("python") + "true"

        def run(found):
            return subprocess.run(
                runnable,
                shell=True,
                cwd=tmp_path,
                env={**os.environ, "PYTHONPATH": str(tmp_path), "FOUND": found},
                capture_output=True,
                text=True,
                check=False,
            )

        matched, mismatched = run(SOURCE_DIGEST), run("f" * 64)

        assert (matched.returncode, matched.stderr) == (0, "")
        assert mismatched.returncode == 78
        assert "bundle_source_mismatch" in mismatched.stderr
        assert f"expected {SOURCE_DIGEST}, found {'f' * 64}" in mismatched.stderr

    def test_the_assembled_archive_is_built_once(
        self, user, workspace, storage, monkeypatch
    ):
        environment = _environment(user, workspace, "asm-memo", storage)
        run = _run(environment)
        puts = []
        real_put = archives.put_archive
        monkeypatch.setattr(
            archives,
            "put_archive",
            lambda key, body: (puts.append(key), real_put(key, body)),
        )

        first = assembly.prepare_launch(run)
        run.refresh_from_db()
        second = assembly.prepare_launch(run)

        assert first == second
        assert first.hit
        assert len(puts) == 1

    def test_a_missing_cached_object_is_a_miss(self, user, workspace, storage, gateway):
        environment = _environment(user, workspace, "asm-gone", storage)
        run = _run(environment)
        del storage.objects[_row(environment).object_key]

        with capture_logs() as logs:
            _launch(gateway, run)

        row = _row(environment)
        assert (row.state, row.object_key) == ("pending", None)
        assert "miss_fallback" in [one.get("outcome") for one in logs]
        assert "assembly" in run.payload["metadata"]
        assert "outcome" not in run.payload["metadata"]["assembly"]


class TestTodaysPath:
    def _unassembled(self, setup, user, workspace, storage, settings):
        key = f"asm-today-{setup}"
        if setup == "unversioned":
            return _legacy(user, workspace, key, storage, body=_sealed_authoring())
        environment = _environment(user, workspace, key, storage)
        if setup == "flag_off":
            settings.HARNESS_VERSIONING_ENABLED = False
        else:
            HarnessAgentVersionRuntime.no_workspace_objects.filter(
                agent_version_id=environment.agent_version_id
            ).delete()
        return environment

    @pytest.mark.parametrize("setup", ["flag_off", "unversioned", "no_side_row"])
    def test_an_unassembled_run_launches_exactly_as_today(
        self, setup, user, workspace, storage, gateway, settings
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment = self._unassembled(setup, user, workspace, storage, settings)
        run = _run(environment)
        default_pair = run.payload["metadata"]["authoring_object_key"]

        attempt = _launch(gateway, run)

        assert attempt.state == HostedHarnessAttempt.State.RUNNING
        assert _command(gateway) == (
            "if [ ! -f /work/authoring/contract.json ]; then "
            "python -m "
            "fi.alk.harness.hosted_authoring_entrypoint /work/job.json "
            "--source /work/source --output /work/authoring "
            f"--adjustments {gateway_module._ADJUSTMENTS_PATH} ; fi && "
            "rm -rf /work/bundle && "
            "cp -a /work/authoring/environment-bundle /work/bundle && "
            "if [ -f /work/authoring/runtime-validation.json ]; then "
            "cp /work/authoring/runtime-validation.json /work/runtime-validation.json; "
            "fi && "
            "python -m fi.alk.harness.hosted_entrypoint /work/job.json "
            "--source /work/source --output /work/artifacts"
        )
        uploads = gateway.client.sandbox.fs.uploads
        assert set(uploads) == {
            gateway_module._CHAT_POLICY_PATH,
            "/work/source.tar.gz",
            "/work/job.json",
            "/run/futureagi/secrets.json",
            gateway_module._SIMULATOR_SECRETS_PATH,
            "/run/futureagi/capabilities.json",
            "/work/authoring.tar.gz",
            gateway_module._ENTRYPOINT_COMMAND_ID_FILE,
        }
        assert uploads["/work/authoring.tar.gz"] == storage.objects[default_pair]
        assert run.payload["metadata"]["authoring_object_key"] == default_pair
        assert set(gateway.client.params.allowed_domains) == set(
            gateway_module._resolved_egress_domains(
                run.payload,
                {},
                {"AGENTCC_BASE_URL": "https://gateway.example.test"},
                "platform.example.com",
            )
        )


class TestLaunchFailure:
    def _broken(self, monkeypatch):
        def broken(_job):
            raise HostedHarnessError("archive_invalid", "unreadable", status_code=422)

        monkeypatch.setattr(assembly, "prepare_launch", broken)

    def test_a_run_of_the_default_pair_falls_back_to_today(
        self, user, workspace, storage, gateway, monkeypatch
    ):
        environment = _environment(user, workspace, "asm-broken-default", storage)
        run = _run(environment)
        default_pair = run.payload["metadata"]["authoring_object_key"]
        self._broken(monkeypatch)

        attempt = _launch(gateway, run)

        assert attempt.state == HostedHarnessAttempt.State.RUNNING
        assert "source_fingerprint" not in _command(gateway)
        assert gateway.client.sandbox.fs.uploads["/work/authoring.tar.gz"] == (
            storage.objects[default_pair]
        )

    def test_a_run_of_other_versions_fails_without_a_retry(
        self, user, workspace, storage, gateway, monkeypatch
    ):
        environment = _environment(user, workspace, "asm-broken-pinned", storage)
        run = _off_defaults(_run(environment))
        self._broken(monkeypatch)

        attempt = _launch(gateway, run)

        assert gateway.client.params is None
        assert attempt.terminal_failure["code"] == "assembly_unavailable"
        assert attempt.terminal_failure["stage"] == "validating_environment"
        assert run.state == HostedHarnessJob.State.FAILED
        assert run.failure["code"] == "assembly_unavailable"


class TestLaunchMiss:
    @pytest.mark.parametrize("certify", [False, True])
    def test_a_miss_of_the_default_pair_takes_todays_path(
        self, certify, user, workspace, storage, gateway, settings
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = certify
        environment = _environment(user, workspace, f"asm-fallback-{certify}", storage)
        _uncache(environment)
        run = _run(environment)
        default_pair = run.payload["metadata"]["authoring_object_key"]

        with capture_logs() as logs:
            _launch(gateway, run)

        assert [one["outcome"] for one in logs if "outcome" in one] == ["miss_fallback"]
        assert run.payload["metadata"]["authoring_object_key"] == default_pair
        command = _command(gateway)
        assert "--certify-only" not in command
        assert "source_fingerprint" not in command
        assert "cp -a /work/authoring/environment-bundle /work/bundle" in command
        assert "/work/certify-job.json" not in gateway.client.sandbox.fs.uploads
        assert gateway.client.sandbox.fs.uploads["/work/authoring.tar.gz"] == (
            storage.objects[default_pair]
        )

    def test_certifying_is_off_so_other_pins_cannot_run(self, user, workspace, storage):
        environment = _environment(user, workspace, "asm-fallback-pinned", storage)
        _uncache(environment)

        with pytest.raises(HostedHarnessError) as raised:
            assembly.prepare_launch(_off_defaults(_run(environment)))

        assert raised.value.code == "assembly_unavailable"
        assert raised.value.status_code == 422
        assert not raised.value.retryable

    def test_a_run_made_on_the_defaults_keeps_todays_path_after_they_move(
        self, user, workspace, storage, settings
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment = _environment(user, workspace, "asm-defaults-moved", storage)
        _uncache(environment)
        run = _run(environment)
        _move_defaults(environment)

        assert assembly.pins_are_defaults(run)
        assert assembly.prepare_launch(run) is None

    def test_a_run_made_before_defaults_were_recorded_compares_the_current_pair(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, "asm-defaults-legacy", storage)
        run = _run(environment)
        payload = dict(run.payload)
        payload["metadata"]["assembly"].pop("defaults")
        HostedHarnessJob.no_workspace_objects.filter(id=run.id).update(payload=payload)
        run.refresh_from_db()
        assert assembly.pins_are_defaults(run)

        _move_defaults(environment)

        assert not assembly.pins_are_defaults(run)

    def test_a_miss_certifies_the_pinned_pair(
        self, user, workspace, storage, gateway, settings
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment = _environment(user, workspace, "asm-certify", storage)
        _uncache(environment)
        run = _off_defaults(_run(environment))

        _launch(gateway, run)

        uploads = gateway.client.sandbox.fs.uploads
        certify = json.loads(uploads["/work/certify-job.json"])
        job = json.loads(uploads["/work/job.json"])
        assert certify["scenario_count"] == 2
        assert job["scenario_count"] == 1
        for name in (
            "execution_manifest",
            "selected_scenario_keys",
            "trials",
            "simulation_only",
        ):
            assert name not in certify["metadata"]
            assert name in job["metadata"]
        assert certify["metadata"]["generic_harness_v1"] is True
        command = _command(gateway)
        chain = (
            "if [ -f /work/authoring/scenarios.json ]; then "
            "cp /work/authoring/scenarios.json /work/suite-scenarios.json; fi && "
            "python -m "
            "fi.alk.harness.hosted_authoring_entrypoint /work/certify-job.json "
            "--source /work/source --output /work/authoring --certify-only "
            "--bundle-output /work/bundle "
            "&& rm -rf /work/authoring/environment-bundle "
            "&& cp -a /work/bundle /work/authoring/environment-bundle && "
            "python -m fi.alk.harness.hosted_entrypoint /work/job.json"
        )
        assert chain in command
        # Certifying writes the run's certificate itself, after the bundle is in place.
        assert "/work/runtime-validation.json" not in command
        assert "cp -a /work/authoring/environment-bundle /work/bundle" not in command
        assert "/work/certify-job.json" in gateway.client.sandbox.process.exec_calls[0]
        assert {"pypi.org", "github.com"} <= set(gateway.client.params.allowed_domains)
        files = _files(uploads["/work/authoring.tar.gz"])
        assert not any(path.startswith("environment-bundle/") for path in files)
        assert "runtime-validation.json" not in files
        assert "contract.json" in files
        assert run.payload["metadata"]["assembly"]["outcome"] == "miss"

    def test_a_miss_overlays_the_pinned_prompt(
        self, user, workspace, storage, settings
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment = _environment(user, workspace, "asm-miss-prompt", storage)
        _uncache(environment)
        run = _off_defaults(_run(environment))
        _as_provider_agent(run)

        assert not assembly.prepare_launch(run).hit

        run.refresh_from_db()
        files = _files(storage.objects[run.payload["metadata"]["authoring_object_key"]])
        contract = json.loads(files["contract.json"])
        assert contract["system_prompt_excerpt"] == run.agent_version.description

    def test_a_provider_agent_certifies_without_provider_secrets(
        self, user, workspace, storage, gateway, settings, monkeypatch
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True

        def provider_material(*_args):
            raise AssertionError("certifying fetched provider secrets")

        monkeypatch.setattr(
            gateway_module, "_provider_import_authoring_material", provider_material
        )
        environment = _environment(user, workspace, "asm-certify-vapi", storage)
        _uncache(environment)
        run = _off_defaults(_run(environment))
        payload = dict(run.payload)
        payload["agent"] = {
            **payload["agent"],
            "mode": "provider_import",
            "connector": "vapi",
        }
        HostedHarnessJob.no_workspace_objects.filter(id=run.id).update(payload=payload)
        run.refresh_from_db()

        attempt = _launch(gateway, run)

        assert attempt.state == HostedHarnessAttempt.State.RUNNING
        command = _command(gateway)
        assert "--certify-only --bundle-output /work/bundle " in command
        assert "--target-secrets" not in command
        assert "--provider-profile-cache" not in command
        uploads = gateway.client.sandbox.fs.uploads
        assert "/work/certify-job.json" in uploads
        assert "/run/futureagi/authoring-target-secrets.json" not in uploads

    def test_too_many_egress_domains_fail_the_run(
        self, user, workspace, storage, gateway, settings
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        settings.ALK_HOSTED_AUTHORING_EGRESS_DOMAINS = [
            f"pkg{index}.example.com" for index in range(25)
        ]
        environment = _environment(user, workspace, "asm-egress", storage)
        _uncache(environment)
        run = _off_defaults(_run(environment))

        attempt = _launch(gateway, run)

        assert attempt.terminal_failure["code"] == "bundle_cache_miss_egress"
        assert attempt.terminal_failure["stage"] == "validating_environment"
        assert gateway.client.params is None
        assert run.state == HostedHarnessJob.State.FAILED
        assert run.failure["code"] == "bundle_cache_miss_egress"


class TestOverlay:
    def _tree(self):
        return {
            "contract.json": json.dumps(
                {"agent": "support", "system_prompt_excerpt": "old"}
            ).encode()
        }

    def test_a_provider_agent_gets_the_pinned_prompt(self):
        tree = self._tree()

        changed = assembly.overlay_agent_prompt(
            tree,
            {"source": {"kind": "provider"}},
            SimpleNamespace(description="be kind"),
        )

        assert changed
        assert json.loads(tree["contract.json"])["system_prompt_excerpt"] == "be kind"

    def test_a_code_agent_keeps_its_prompt(self):
        tree = self._tree()

        changed = assembly.overlay_agent_prompt(
            tree,
            {"source": {"kind": "github"}},
            SimpleNamespace(description="be kind"),
        )

        assert not changed
        assert tree == self._tree()


class _FillFilesystem:
    def __init__(self, files):
        self.files = files
        self.uploads = {}
        self.downloads = []

    def upload_file(self, content, path):
        self.uploads[path] = content

    def download_file(self, path, timeout=None):
        self.downloads.append(path)
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]


class _FillProcess:
    def __init__(self, exit_code=0):
        self.exit_code = exit_code
        self.exec_calls = []

    def exec(self, command, **kwargs):
        self.exec_calls.append(command)
        return SimpleNamespace(exit_code=self.exit_code, result="")


def _tar(files):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as archive:
        for path, data in files.items():
            member = tarfile.TarInfo(path)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return out.getvalue()


def _fill_sandbox(
    *,
    exit_code=0,
    digest=BUNDLE_DIGEST,
    certified=None,
    complete=True,
    report=None,
    extra=None,
):
    manifest = json.dumps({**MANIFEST, "digest": digest}).encode()
    certificate = json.dumps(
        {
            "status": "certified",
            "api_key": "x",
            "compiler": {"bundle_digest": certified or digest},
        }
    ).encode()
    files = {
        "/work/bundle/manifest.json": manifest,
        "/work/runtime-validation.json": certificate,
        "/tmp/harness-bundle-fill.tar.gz": _tar(
            {
                "bundle/manifest.json": manifest,
                "bundle/world.sqlite": b"db",
                # Packed while the certificate is still being written.
                "runtime-validation.json": certificate if complete else certificate[:9],
                "authoring/generic-harness/certification.json": b'{"ok": true}',
                **(extra or {}),
            }
        ),
    }
    if report is not None:
        files[assembly.CERTIFY_REPORT_PATH] = json.dumps(report).encode()
    return SimpleNamespace(fs=_FillFilesystem(files), process=_FillProcess(exit_code))


def _outcomes(logs):
    return [(one["outcome"], one.get("reason")) for one in logs if "outcome" in one]


class TestFill:
    def _missed(self, user, workspace, storage, key, settings):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment = _environment(user, workspace, key, storage)
        _uncache(environment)
        run = _off_defaults(_run(environment))
        assert not assembly.prepare_launch(run).hit
        attempt = register_attempt(
            run.id, endpoint_base_url="https://platform.example.com"
        ).attempt
        run.refresh_from_db()
        return environment, run, attempt

    def test_a_certified_miss_fills_the_cache(self, user, workspace, storage, settings):
        environment, run, attempt = self._missed(
            user, workspace, storage, "asm-fill", settings
        )
        sandbox = _fill_sandbox()

        with capture_logs() as logs:
            HostedHarnessGateway._sync_authoring_progress(attempt, sandbox)

        row = _row(environment)
        assert row.state == "ready"
        assert row.bundle_digest == BUNDLE_DIGEST
        assert row.built_by_attempt_id == attempt.id
        assert row.certificate == {
            "status": "certified",
            "api_key": "[redacted]",
            "compiler": {"bundle_digest": BUNDLE_DIGEST},
            "source_digest": SOURCE_DIGEST,
        }
        assert row.warnings == []
        assert row.object_key.startswith(f"harness-bundles/{row.organization_id}/")
        files = _files(storage.objects[row.object_key])
        assert set(files) == {
            "environment-bundle/manifest.json",
            "environment-bundle/world.sqlite",
            "runtime-validation.json",
            "generic-harness/certification.json",
        }
        command = sandbox.process.exec_calls[0]
        assert "authoring/generic-harness/certification.json" in command
        assert "bundle/world.sqlite" not in command
        assert f"--null -T {assembly._FILL_LIST} runtime-validation.json " in command
        assert sandbox.fs.uploads[assembly._FILL_LIST] == (
            b"bundle/manifest.json\0bundle/world.sqlite\0"
        )
        assert _outcomes(logs) == [("filled", None)]
        run.refresh_from_db()
        assert run.bundle_digest == BUNDLE_DIGEST

    def test_only_the_files_the_manifest_lists_are_cached(
        self, user, workspace, storage, settings
    ):
        environment, _run_job, attempt = self._missed(
            user, workspace, storage, "asm-fill-stray", settings
        )
        sandbox = _fill_sandbox(
            extra={
                "bundle/stray.log": b"written by the run",
                "bundle/__pycache__/world.cpython-312.pyc": b"cache",
            }
        )

        HostedHarnessGateway._sync_authoring_progress(attempt, sandbox)

        assert b"stray" not in sandbox.fs.uploads[assembly._FILL_LIST]
        assert b"__pycache__" not in sandbox.fs.uploads[assembly._FILL_LIST]
        files = _files(storage.objects[_row(environment).object_key])
        assert {path for path in files if path.startswith("environment-bundle/")} == {
            "environment-bundle/manifest.json",
            "environment-bundle/world.sqlite",
        }

    def test_the_manifest_names_the_files_to_pack(self):
        listed = ["world.sqlite", "runtime/__pycache__/app.pyc", "world.sqlite"]

        assert assembly.bundle_paths(
            {"files": [{"path": path} for path in listed]}
        ) == ["manifest.json", "world.sqlite"]
        for unsafe in ("../secrets.json", "/etc/passwd", "", "a\0b"):
            assert assembly.bundle_paths({"files": [{"path": unsafe}]}) is None

    def test_the_certify_report_is_kept_as_warnings(
        self, user, workspace, storage, settings
    ):
        environment, _run_job, attempt = self._missed(
            user, workspace, storage, "asm-fill-report", settings
        )
        report = {"dropped": ["refund-late"], "narrowed": {"cancel": ["refunded"]}}

        HostedHarnessGateway._sync_authoring_progress(
            attempt, _fill_sandbox(report=report)
        )

        assert _row(environment).warnings == [
            {"code": "scenario_dropped", "keys": ["refund-late"]},
            {"code": "sub_goals_narrowed", "scenarios": {"cancel": ["refunded"]}},
        ]

    def test_a_filled_attempt_stops_reading_the_certificate(
        self, user, workspace, storage, settings
    ):
        _environment_job, _run_job, attempt = self._missed(
            user, workspace, storage, "asm-fill-once", settings
        )
        sandbox = _fill_sandbox()
        HostedHarnessGateway._sync_authoring_progress(attempt, sandbox)
        sandbox.fs.downloads.clear()

        HostedHarnessGateway._sync_authoring_progress(attempt, sandbox)

        assert "/work/runtime-validation.json" not in sandbox.fs.downloads
        assert len(sandbox.process.exec_calls) == 1

    def test_an_incomplete_bundle_is_filled_on_a_later_poll(
        self, user, workspace, storage, settings
    ):
        environment, _run_job, attempt = self._missed(
            user, workspace, storage, "asm-fill-partial", settings
        )

        with capture_logs() as logs:
            HostedHarnessGateway._sync_authoring_progress(
                attempt, _fill_sandbox(complete=False)
            )
            HostedHarnessGateway._sync_authoring_progress(attempt, _fill_sandbox())

        assert _outcomes(logs) == [("fill_skipped", "incomplete"), ("filled", None)]
        assert _row(environment).state == "ready"

    def test_a_bundle_its_certificate_is_not_for_is_not_cached(
        self, user, workspace, storage, settings
    ):
        environment, _run_job, attempt = self._missed(
            user, workspace, storage, "asm-fill-stale", settings
        )

        with capture_logs() as logs:
            HostedHarnessGateway._sync_authoring_progress(
                attempt, _fill_sandbox(certified="sha256:" + "0" * 64)
            )

        assert _outcomes(logs) == [("fill_skipped", "certificate_digest_mismatch")]
        assert _row(environment).state == "pending"

    def test_a_second_fill_changes_nothing(
        self, user, workspace, storage, settings, monkeypatch
    ):
        environment, _run_job, attempt = self._missed(
            user, workspace, storage, "asm-fill-twice", settings
        )
        first = {"object_key": "harness-bundles/first.tar.gz", "digest": BUNDLE_DIGEST}
        real_put = archives.put_archive

        def another_run_fills_first(key, body):
            # Another miss of the same combination finishes while this one packs.
            HarnessAssembledBundle._base_manager.filter(job=environment).update(
                state="ready",
                object_key=first["object_key"],
                bundle_digest=first["digest"],
            )
            real_put(key, body)

        monkeypatch.setattr(archives, "put_archive", another_run_fills_first)

        with capture_logs() as logs:
            HostedHarnessGateway._sync_authoring_progress(
                attempt, _fill_sandbox(digest="sha256:" + "f" * 64)
            )

        after = _row(environment)
        assert (after.object_key, after.bundle_digest, after.built_by_attempt_id) == (
            first["object_key"],
            first["digest"],
            None,
        )
        assert _outcomes(logs) == [("fill_skipped", "different_digest")]

    def test_a_failed_pack_never_fails_the_run_or_packs_again(
        self, user, workspace, storage, settings
    ):
        environment, run, attempt = self._missed(
            user, workspace, storage, "asm-fill-fail", settings
        )

        with capture_logs() as logs:
            HostedHarnessGateway._sync_authoring_progress(
                attempt, _fill_sandbox(exit_code=2)
            )

        assert _row(environment).state == "pending"
        assert _outcomes(logs) == [("fill_skipped", "pack_failed")]
        # The claim is kept: packing the same files would fail again.
        run.refresh_from_db()
        assert run.payload["metadata"]["assembly"]["fill_attempt"] == (
            attempt.attempt_number
        )
        sandbox = _fill_sandbox()
        HostedHarnessGateway._sync_authoring_progress(attempt, sandbox)
        assert sandbox.process.exec_calls == []
        assert _row(environment).state == "pending"

    def test_a_transport_error_is_filled_on_a_later_poll(
        self, user, workspace, storage, settings
    ):
        environment, run, attempt = self._missed(
            user, workspace, storage, "asm-fill-transport", settings
        )
        broken = _fill_sandbox()
        del broken.fs.files["/tmp/harness-bundle-fill.tar.gz"]

        with capture_logs() as logs:
            HostedHarnessGateway._sync_authoring_progress(attempt, broken)

        assert _outcomes(logs) == [("fill_skipped", "error")]
        run.refresh_from_db()
        assert "fill_attempt" not in run.payload["metadata"]["assembly"]
        HostedHarnessGateway._sync_authoring_progress(attempt, _fill_sandbox())
        assert _row(environment).state == "ready"

    def test_a_row_already_ready_still_records_the_runs_bundle(
        self, user, workspace, storage, settings
    ):
        environment, run, attempt = self._missed(
            user, workspace, storage, "asm-fill-ready", settings
        )
        HarnessAssembledBundle._base_manager.filter(job=environment).update(
            state="ready", object_key="harness-bundles/other.tar.gz"
        )
        built = "sha256:" + "f" * 64
        sandbox = _fill_sandbox(digest=built)

        HostedHarnessGateway._sync_authoring_progress(attempt, sandbox)

        assert sandbox.process.exec_calls == []
        run.refresh_from_db()
        assert run.bundle_digest == built

    def test_a_miss_indexes_the_suite_it_was_assembled_with(
        self, user, workspace, storage, settings
    ):
        _environment_job, run, attempt = self._missed(
            user, workspace, storage, "asm-fill-index", settings
        )
        suite = [_scenario(name) for name in NAMES[:2]]
        sandbox = SimpleNamespace(
            fs=_FillFilesystem(
                {
                    # Certifying pruned the authoring index to one scenario.
                    "/work/authoring/scenarios.json": json.dumps(suite[:1]).encode(),
                    assembly.SUITE_INDEX_PATH: json.dumps(suite).encode(),
                }
            ),
            process=_FillProcess(),
        )

        HostedHarnessGateway._sync_authoring_progress(attempt, sandbox)

        assert set(
            HostedHarnessScenario.no_workspace_objects.filter(job=run).values_list(
                "scenario_key", flat=True
            )
        ) == {_key(name) for name in NAMES[:2]}

    def test_a_rejected_certify_marks_the_row_failed(
        self, user, workspace, storage, gateway, settings, monkeypatch
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment = _environment(user, workspace, "asm-78", storage)
        _uncache(environment)
        run = _off_defaults(_run(environment))
        attempt = _launch(gateway, run)
        _exit_78(gateway, monkeypatch)

        gateway.reconcile_completed(attempt)

        attempt.refresh_from_db()
        row = _row(environment)
        assert attempt.terminal_failure["code"] == "authoring_runtime_validation_failed"
        assert row.state == "failed"
        assert row.error["code"] == "authoring_runtime_validation_failed"


class TestRerun:
    def _source(self, user, workspace, storage, key):
        """A completed run of the environment's defaults, with the source it ran."""
        environment = _environment(user, workspace, key, storage)
        source = _run(environment, [_key(NAMES[1])])
        payload = dict(source.payload)
        payload["source"] = {**payload["source"], "commit_sha": "c" * 40}
        HostedHarnessJob.no_workspace_objects.filter(id=source.id).update(
            payload=payload, state=HostedHarnessJob.State.COMPLETED
        )
        source.refresh_from_db()
        return environment, source

    def _rerun(self, user, workspace, source, settings):
        from simulate.services.harness_provider import HostedHarnessProvider

        settings.HARNESS_PUBLIC_BASE_URL = "https://harness.example.test"
        with patch("simulate.temporal.client.start_hosted_harness_gateway_workflow"):
            result = HostedHarnessProvider().rerun_saved(
                str(source.id),
                organization=user.organization,
                workspace=workspace,
                environment_values={},
            )
        return HostedHarnessJob.no_workspace_objects.get(id=result["job"]["job_id"])

    def _uncache_source(self, source):
        HarnessAssembledBundle._base_manager.filter(
            id=source.assembled_bundle_id
        ).update(state="pending", object_key=None, certificate=None)

    @staticmethod
    def _change_agent(environment):
        payload = dict(environment.payload)
        payload["agent"] = {**payload["agent"], "config": {"model": "changed"}}
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            payload=payload
        )
        environment.refresh_from_db()

    @staticmethod
    def _pins(job):
        return (job.agent_version_id, job.environment_version_id, job.suite_version_id)

    def test_a_rerun_of_the_defaults_keeps_them(
        self, user, workspace, storage, settings
    ):
        environment, source = self._source(user, workspace, storage, "asm-rerun-def")
        _uncache(environment)

        run = self._rerun(user, workspace, source, settings)

        assert self._pins(run) == self._pins(source) == self._pins(environment)
        assert run.payload["metadata"]["rerun_of"] == str(source.id)
        assert run.payload["metadata"]["assembly"]["defaults"] is True
        assert run.payload["source"] == source.payload["source"]
        assert run.payload["metadata"]["authoring_object_key"] == (
            environment.payload["metadata"]["authoring_object_key"]
        )
        assert assembly.prepare_launch(run) is None

    def test_a_rerun_replays_its_sources_cached_bundle(
        self, user, workspace, storage, settings
    ):
        environment, source = self._source(user, workspace, storage, "asm-rerun-hit")
        _move_defaults(environment)

        run = self._rerun(user, workspace, source, settings)

        assert self._pins(run) == self._pins(source) != self._pins(environment)
        assert run.assembled_bundle_id == source.assembled_bundle_id
        metadata = run.payload["metadata"]
        assert metadata["version_pins"] == source.payload["metadata"]["version_pins"]
        assert metadata["assembly"] == {
            **source.payload["metadata"]["assembly"],
            "defaults": False,
        }
        assert metadata["rerun_of"] == str(source.id)
        assert run.payload["source"] == source.payload["source"]
        assert assembly.prepare_launch(run).hit

    def test_a_rerun_keeps_the_agent_its_sources_pins_ran(
        self, user, workspace, storage, settings
    ):
        environment, source = self._source(user, workspace, storage, "asm-rerun-agent")
        _move_defaults(environment)
        self._change_agent(environment)

        run = self._rerun(user, workspace, source, settings)

        assert self._pins(run) == self._pins(source)
        assert run.payload["agent"] == source.payload["agent"]
        assert run.payload["agent"] != environment.payload["agent"]

    def test_a_rerun_of_the_defaults_runs_the_environments_agent(
        self, user, workspace, storage, settings
    ):
        environment, source = self._source(user, workspace, storage, "asm-rerun-agent2")
        _move_defaults(environment)
        self._uncache_source(source)
        self._change_agent(environment)

        run = self._rerun(user, workspace, source, settings)

        assert self._pins(run) == self._pins(environment)
        assert run.payload["agent"] == environment.payload["agent"]

    def test_a_kept_pin_whose_bundle_went_before_launch_fails_typed(
        self, user, workspace, storage, gateway, settings
    ):
        environment, source = self._source(user, workspace, storage, "asm-rerun-gone")
        _move_defaults(environment)
        run = self._rerun(user, workspace, source, settings)
        assert self._pins(run) == self._pins(source)
        self._uncache_source(source)

        attempt = _launch(gateway, run)

        assert gateway.client.params is None
        assert attempt.terminal_failure["code"] == "assembly_unavailable"
        assert attempt.terminal_failure["stage"] == "validating_environment"
        assert run.state == HostedHarnessJob.State.FAILED
        assert run.failure["code"] == "assembly_unavailable"

    def test_a_rerun_that_cannot_run_its_sources_pins_runs_the_defaults(
        self, user, workspace, storage, settings
    ):
        environment, source = self._source(user, workspace, storage, "asm-rerun-old")
        _move_defaults(environment)
        self._uncache_source(source)

        run = self._rerun(user, workspace, source, settings)

        assert self._pins(run) == self._pins(environment) != self._pins(source)
        metadata = run.payload["metadata"]
        assert "rerun_of" not in metadata
        assert metadata["assembly"]["defaults"] is True
        assert metadata["version_pins"]["environment_version_id"] == str(
            environment.environment_version_id
        )
        assert run.payload["source"] == environment.payload["source"]
        assert metadata["authoring_object_key"] == (
            environment.payload["metadata"]["authoring_object_key"]
        )

    def test_a_rerun_certifies_its_sources_pins_when_they_are_not_cached(
        self, user, workspace, storage, settings
    ):
        settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True
        environment, source = self._source(user, workspace, storage, "asm-rerun-cert")
        _move_defaults(environment)
        self._uncache_source(source)

        run = self._rerun(user, workspace, source, settings)

        assert self._pins(run) == self._pins(source) != self._pins(environment)
        assert run.payload["metadata"]["assembly"]["defaults"] is False
        assert run.payload["source"] == source.payload["source"]
        assert assembly.prepare_launch(run).outcome == "miss"

    def test_a_rerun_of_an_environment_uses_its_defaults(
        self, user, workspace, storage, settings
    ):
        from simulate.services.harness_provider import HostedHarnessProvider

        settings.HARNESS_PUBLIC_BASE_URL = "https://harness.example.test"
        environment = _environment(user, workspace, "asm-rerun-env", storage)

        with patch("simulate.temporal.client.start_hosted_harness_gateway_workflow"):
            result = HostedHarnessProvider().rerun_saved(
                str(environment.id),
                organization=user.organization,
                workspace=workspace,
                environment_values={},
            )

        run = HostedHarnessJob.no_workspace_objects.get(id=result["job"]["job_id"])
        assert run.environment_version_id == environment.environment_version_id
        assert "rerun_of" not in run.payload["metadata"]
