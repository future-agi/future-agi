"""The job documents a reading and a scan hand the sandbox carry every field
agent-learning-kit's job model requires, so the sandbox never refuses them."""

import json
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

from simulate.models import AgentDefinition, AgentScan, HarnessAgentVersionRuntime
from simulate.models.agent_setup import AgentVersionSetup
from simulate.services import agent_reading_sandbox
from simulate.services.agent_reading_sandbox import (
    SECRETS_PATH,
    SIMULATOR_SECRETS_PATH,
    _launch_spec,
    _sandbox,
    _source_document,
    _store_reading,
    read_version,
    reading_job_document,
    scan_job_document,
    scan_source,
)
from simulate.services.agent_setup import store_agent
from simulate.temporal.constants import READING_ACTIVITY_TIMEOUT_SECONDS
from simulate.tests.test_agent_setup_service import source_agent
from simulate.tests.test_code_archives import storage as storage  # noqa: F401
from simulate.tests.test_harness_connected_versions import _connect, _runtime

pytestmark = pytest.mark.django_db

Status = HarnessAgentVersionRuntime.PreflightStatus
EVALS = "simulate.services.harness_evals.briefing_evals"
MATERIAL = {
    "ALK_HARNESS": "vertex-gemini",
    "AGENTCC_API_KEY": "harness-virtual-key",
    "LIVEKIT_URL": "wss://lk.example.test",
    "LIVEKIT_API_KEY": "lk-key",
    "LIVEKIT_API_SECRET": "lk-secret",
}

ALK_REQUIRED_JOB_FIELDS = {"job_id", "run_id", "execution", "source", "agent"}
ALK_ARTIFACT_LEVELS = {
    "metadata-only",
    "traces",
    "traces-and-recordings",
    "full",
    "local-only",
}


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False


@pytest.fixture
def agent(organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="support",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        organization=organization,
        workspace=workspace,
    )


def _assert_alk_accepts(document):
    assert ALK_REQUIRED_JOB_FIELDS <= document.keys()
    assert document["execution"] == "hosted"
    level = (document.get("artifacts") or {}).get("level", "traces")
    assert level in ALK_ARTIFACT_LEVELS


def test_a_reading_job_carries_what_the_sandbox_requires(agent):
    version = _connect(agent)
    runtime = _runtime(version)

    document = reading_job_document(agent, runtime)

    _assert_alk_accepts(document)
    assert document["job_id"] == str(runtime.agent_version_id)
    assert document["metadata"]["agent_name"] == "support"
    assert document["source"]["commit_sha"] == "a" * 40


def test_each_reading_gets_its_own_run(agent):
    version = _connect(agent)
    runtime = _runtime(version)

    first = reading_job_document(agent, runtime)
    second = reading_job_document(agent, runtime)

    assert first["run_id"] != second["run_id"]


def test_a_scan_job_carries_what_the_sandbox_requires(organization, workspace):
    scan = AgentScan.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        source={"kind": "github", "repository": "acme/agent", "commit_sha": "b" * 40},
    )

    document = scan_job_document(scan)

    _assert_alk_accepts(document)
    assert document["job_id"] == str(scan.id)
    assert document["metadata"]["name"] == "acme/agent"
    assert "agent_name" not in document["metadata"]


def test_a_scan_job_carries_the_commit_resolved_for_it(organization, workspace):
    scan = AgentScan.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        source={"kind": "github", "repository": "acme/agent", "ref": "main"},
        status=AgentScan.Status.PENDING,
    )
    sent = {}

    def run_reading(source_archive, job_document, **_):
        sent.update(job_document)
        return None, {"status": "none", "agents": []}, None

    with (
        patch.object(agent_reading_sandbox, "require_harness_authoring"),
        patch(
            "simulate.services.agent_versions.branch_head_of",
            return_value="c" * 40,
        ),
        patch.object(
            agent_reading_sandbox,
            "acquire_source_archive",
            return_value=(b"archive", "c" * 40),
        ),
        patch.object(agent_reading_sandbox, "run_reading", side_effect=run_reading),
    ):
        scan_source(scan.id)

    assert sent["source"]["commit_sha"] == "c" * 40
    scan.refresh_from_db()
    assert scan.status == AgentScan.Status.NONE


@pytest.fixture
def no_evals():
    with patch(EVALS, return_value=[]) as evals:
        yield evals


@pytest.fixture
def simulator(monkeypatch):
    monkeypatch.setattr(
        agent_reading_sandbox, "_platform_simulator_material", lambda: dict(MATERIAL)
    )
    monkeypatch.setattr(
        agent_reading_sandbox,
        "_authoring_egress_domains",
        lambda *_args, **_kwargs: ["gateway.example.test"],
    )
    monkeypatch.setattr(
        agent_reading_sandbox,
        "_validate_resolved_egress_domains",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(agent_reading_sandbox, "_alk_source_overlay", lambda: None)


class _ReadingSandbox:
    id = "reading-sandbox"

    def __init__(self):
        self.uploads = {}
        self.commands = []
        self.fs = SimpleNamespace(upload_file=self._upload)
        self.process = SimpleNamespace(exec=self._exec)

    def _upload(self, body, path):
        self.uploads[path] = body

    def _exec(self, command, **_kwargs):
        self.commands.append(command)
        return SimpleNamespace(exit_code=0, result="")


class _Provider:
    name = "daytona"
    max_egress_domains = 20
    create_timeout_seconds = 60

    def __init__(self):
        self.sandbox = _ReadingSandbox()
        self.deleted = False

    def create(self, spec, timeout):
        self.spec = spec
        return self.sandbox

    def delete(self, sandbox, **_kwargs):
        self.deleted = True


def _never(*_args, **_kwargs):
    raise AssertionError("the source must not be fetched")


def _reading(monkeypatch, outcome):
    sent = {}
    monkeypatch.setattr(
        agent_reading_sandbox, "require_harness_authoring", lambda _organization: None
    )
    monkeypatch.setattr(
        agent_reading_sandbox,
        "acquire_source_archive",
        lambda *_args, **_kwargs: (b"archive", "a" * 40),
    )

    def run_reading(source_archive, job_document, **_kwargs):
        sent.update(job_document)
        return outcome

    monkeypatch.setattr(agent_reading_sandbox, "run_reading", run_reading)
    return sent


def _billed(monkeypatch):
    billed = []
    monkeypatch.setattr(
        agent_reading_sandbox,
        "record_agent_reading_usage",
        lambda **kwargs: billed.append(kwargs),
    )
    return billed


def _read(document):
    return {"agent": document, "sdk_version": "0.2.3"}


def _in_folder(runtime, subfolder):
    HarnessAgentVersionRuntime.no_workspace_objects.filter(id=runtime.id).update(
        source={**runtime.source, "subfolder": subfolder}
    )
    runtime.refresh_from_db()
    return runtime


class TestReadingJobSource:
    def test_a_reading_of_an_installed_repository_names_its_installation(
        self, agent, no_evals
    ):
        runtime = _runtime(_connect(agent))
        runtime.agent_payload = {
            **runtime.agent_payload,
            "source_access": {
                **(runtime.agent_payload.get("source_access") or {}),
                "visibility": "private",
                "installation_id": 42,
            },
        }
        runtime.save(update_fields=["agent_payload"])

        document = reading_job_document(agent, runtime)

        assert document["source"]["installation_id"] == "42"
        assert document["source"]["visibility"] == "private"

    def test_a_public_repository_names_no_installation(self):
        document = _source_document(
            HarnessAgentVersionRuntime.SourceKind.GITHUB,
            {"repository": "acme/agent"},
            {},
        )

        assert "installation_id" not in document


class TestReadingJobEvals:
    def test_a_reading_is_offered_the_evals_a_build_would_be(self, agent):
        offered = [{"id": "eval-1", "name": "Task completion"}]
        with patch(EVALS, return_value=offered) as evals:
            document = reading_job_document(agent, _runtime(_connect(agent)))

        assert document["metadata"]["available_evals"] == offered
        assert evals.call_args.args == (agent.organization, agent.workspace)

    @pytest.mark.parametrize(
        "catalogue",
        [{"return_value": []}, {"side_effect": RuntimeError("catalogue down")}],
    )
    def test_a_reading_with_no_evals_to_offer_names_none(self, agent, catalogue):
        with patch(EVALS, **catalogue):
            document = reading_job_document(agent, _runtime(_connect(agent)))

        assert "available_evals" not in document["metadata"]


class TestScanJobName:
    def test_a_scan_of_an_upload_names_the_uploaded_folder(
        self, organization, workspace
    ):
        artifact = str(uuid4())
        scan = AgentScan.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            source={"kind": "archive", "archive_artifact_id": artifact},
        )

        with patch.object(
            agent_reading_sandbox, "stored_upload_name", return_value="harbor-agent"
        ) as named:
            document = scan_job_document(scan)

        _assert_alk_accepts(document)
        assert named.call_args.args == (scan.organization_id, artifact)
        assert document["metadata"]["name"] == "harbor-agent"
        assert document["metadata"]["agent_name"] == "harbor-agent"

    def test_a_scan_of_an_older_upload_names_no_agent(self, organization, workspace):
        scan = AgentScan.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            source={"kind": "archive", "archive_artifact_id": str(uuid4())},
        )

        with patch.object(
            agent_reading_sandbox, "stored_upload_name", return_value=None
        ):
            document = scan_job_document(scan)

        assert document["metadata"]["name"] == "upload"
        assert "agent_name" not in document["metadata"]

    def test_a_scan_of_a_repository_never_looks_up_an_upload(
        self, organization, workspace
    ):
        scan = AgentScan.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            source={"kind": "github", "repository": "acme/agent"},
        )

        with patch.object(
            agent_reading_sandbox, "stored_upload_name", side_effect=_never
        ):
            document = scan_job_document(scan)

        assert document["metadata"]["name"] == "acme/agent"


class TestReadingSandbox:
    @pytest.mark.parametrize(
        "configured, timeout",
        [
            (10_000, READING_ACTIVITY_TIMEOUT_SECONDS - 600),
            (1200, 1200),
        ],
    )
    def test_a_reading_ends_inside_its_activity(
        self, settings, simulator, configured, timeout
    ):
        settings.ALK_HOSTED_READING_TIMEOUT = configured

        spec, env, _material, got = _launch_spec(_Provider(), {})

        assert got == timeout
        assert env["ALK_READING_MAX_DURATION_SECONDS"] == str(timeout - 420)
        assert spec.ttl_seconds == timeout + 300

    def test_a_short_reading_still_gets_a_minute(self, settings, simulator):
        settings.ALK_HOSTED_READING_TIMEOUT = 300

        _spec, env, _material, _timeout = _launch_spec(_Provider(), {})

        assert env["ALK_READING_MAX_DURATION_SECONDS"] == "60"

    def test_the_simulator_values_reach_the_sandbox_in_a_private_file(
        self, simulator, monkeypatch
    ):
        provider = _Provider()
        monkeypatch.setattr(agent_reading_sandbox, "_provider", lambda: provider)

        with _sandbox(b"source", {"job_id": "job-1"}, {}):
            pass

        uploaded = json.loads(provider.sandbox.uploads[SIMULATOR_SECRETS_PATH])
        assert uploaded == {
            "ALK_HARNESS": "vertex-gemini",
            "AGENTCC_API_KEY": "harness-virtual-key",
            "HARNESS_OBSERVABILITY": "off",
        }
        [prepare] = provider.sandbox.commands
        assert prepare.endswith(
            f"chmod 0600 /work/job.json {SECRETS_PATH} {SIMULATOR_SECRETS_PATH}"
        )
        assert provider.deleted is True

    def test_only_the_model_gateway_values_reach_a_reading(
        self, simulator, monkeypatch
    ):
        gateway = {
            "ALK_HARNESS": "claude",
            "ALK_HARNESS_MODEL": "vertex_ai/gemini-3.7-flash",
            "CLAUDE_CODE_USE_VERTEX": "0",
            "ALK_CLAUDE_GATEWAY_URL": "https://gateway.example.test",
            "ALK_CLAUDE_GATEWAY_API_KEY": "harness-virtual-key",
            "AGENTCC_BASE_URL": "https://gateway.example.test",
            "AGENTCC_API_KEY": "harness-virtual-key",
        }
        withheld = {
            "FI_API_KEY": "fi-key",
            "FI_SECRET_KEY": "fi-secret",
            "FI_BASE_URL": "https://collector.example.test",
            "SIP_OUTBOUND_TRUNK_ID": "trunk-1",
            "SIP_OUTBOUND_FROM_NUMBER": "+15550100",
            "SIMULATOR_STT_PROVIDER": "deepgram",
            "LIVEKIT_API_SECRET": "lk-secret",
            "HARNESS_OBSERVABILITY": "on",
        }
        monkeypatch.setattr(
            agent_reading_sandbox,
            "_platform_simulator_material",
            lambda: {**gateway, **withheld},
        )
        provider = _Provider()
        monkeypatch.setattr(agent_reading_sandbox, "_provider", lambda: provider)

        with _sandbox(b"source", {"job_id": "job-1"}, {}) as (_box, env, _timeout):
            pass

        uploaded = json.loads(provider.sandbox.uploads[SIMULATOR_SECRETS_PATH])
        secret_names = set(withheld) - {"HARNESS_OBSERVABILITY"}
        assert secret_names.isdisjoint(env)
        assert secret_names.isdisjoint(uploaded)
        assert uploaded == {**gateway, "HARNESS_OBSERVABILITY": "off"}
        assert env["HARNESS_OBSERVABILITY"] == "off"
        assert {name: env[name] for name in gateway} == gateway


class TestReadingSpend:
    @pytest.mark.parametrize(
        ("code", "warned"),
        [("reading_file_missing", False), ("reading_file_invalid", True)],
    )
    def test_an_unreadable_cost_file_is_logged_but_a_missing_one_is_not(
        self, monkeypatch, code, warned
    ):
        from simulate.services import agent_reading
        from simulate.services.agent_reading import ReadingResultInvalid

        def unreadable(_sandbox, path, _max_bytes):
            raise ReadingResultInvalid(code, path)

        monkeypatch.setattr(agent_reading, "_json_object", unreadable)
        warnings = []
        monkeypatch.setattr(
            agent_reading_sandbox.logger,
            "warning",
            lambda event, **fields: warnings.append((event, fields)),
        )

        spend = agent_reading_sandbox._spend(SimpleNamespace(id="sb-1"))

        assert spend is None
        assert [event for event, _ in warnings] == (
            ["agent_reading_spend_unreadable"] if warned else []
        )
        if warned:
            assert warnings[0][1]["code"] == code


class TestReadingBill:
    def test_a_reading_is_billed_for_its_own_run(self, agent, monkeypatch, no_evals):
        version = _connect(agent)
        spend = {"stages": [{"stage": "understanding-agent"}]}
        failure = {
            "code": "agent_unreadable",
            "message": "no agent",
            "retryable": False,
        }
        sent = _reading(monkeypatch, (failure, None, None, spend))
        billed = _billed(monkeypatch)

        outcome = read_version(version.id)

        assert outcome.code == "agent_unreadable"
        assert billed == [
            {
                "organization_id": agent.organization_id,
                "workspace_id": agent.workspace_id,
                "subject_id": version.id,
                "run_id": sent["run_id"],
                "spend": spend,
            }
        ]

    def test_a_scan_is_billed_for_its_own_run(
        self, organization, workspace, monkeypatch
    ):
        scan = AgentScan.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            source={
                "kind": "github",
                "repository": "acme/agent",
                "commit_sha": "c" * 40,
            },
            status=AgentScan.Status.PENDING,
        )
        spend = {"stages": [{"stage": "finding-agents"}]}
        sent = _reading(monkeypatch, (None, {"status": "none", "agents": []}, spend))
        billed = _billed(monkeypatch)

        scan_source(scan.id)

        assert [(one["subject_id"], one["run_id"]) for one in billed] == [
            (scan.id, sent["run_id"])
        ]


class TestReadingFolder:
    @pytest.mark.parametrize(
        "asked, echoed",
        [
            ("agents/support", None),
            ("agents/support", "agents/other"),
            ("", "agents/support"),
        ],
    )
    def test_a_reading_of_another_folder_is_refused(self, agent, asked, echoed):
        runtime = _in_folder(_runtime(_connect(agent)), asked)
        extension = {"source": {"subfolder": echoed}} if echoed else {}

        outcome = _store_reading(
            runtime,
            agent,
            _read(source_agent(extension=extension)),
            {"agent": {"name": "support"}},
            None,
        )

        assert (outcome.status, outcome.code) == ("failed", "subfolder_ignored")
        assert not AgentVersionSetup.no_workspace_objects.filter(
            agent_version_id=runtime.agent_version_id
        ).exists()

    @pytest.mark.usefixtures("storage")
    def test_a_reading_of_the_folder_asked_for_is_stored(self, agent):
        runtime = _in_folder(_runtime(_connect(agent)), "agents/support")
        document = source_agent(extension={"source": {"subfolder": "agents/support"}})

        outcome = _store_reading(
            runtime, agent, _read(document), {"agent": {"name": "support"}}, None
        )

        assert outcome.status == "stored"


class TestDocumentOnlyReading:
    def test_a_version_with_a_document_is_not_read_again(self, agent, monkeypatch):
        version = _connect(agent)
        store_agent(
            agent_version_id=version.id, agent=source_agent(), sdk_version="0.2.3"
        )
        monkeypatch.setattr(agent_reading_sandbox, "acquire_source_archive", _never)

        outcome = read_version(version.id, document_only=True)

        assert (outcome.status, outcome.code) == ("skipped", "already_stored")

    @pytest.mark.usefixtures("storage", "no_evals")
    def test_the_document_is_stored_and_the_version_and_agent_left_alone(
        self, agent, monkeypatch
    ):
        version = _connect(agent)
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_version=version
        ).update(preflight_status=Status.PASSED)
        _reading(
            monkeypatch,
            (
                None,
                _read(source_agent(type="voice")),
                {"agent": {"name": "support"}},
                None,
            ),
        )
        assert read_version(version.id).code == "not_pending"

        outcome = read_version(version.id, document_only=True)

        assert outcome.status == "stored"
        setup = AgentVersionSetup.no_workspace_objects.get(
            agent_version_id=version.id, status=AgentVersionSetup.Status.STORED
        )
        assert setup.agent_type == "voice"
        agent.refresh_from_db()
        assert agent.agent_type == AgentDefinition.AgentTypeChoices.TEXT
        assert _runtime(version).preflight_status == Status.PASSED

    def test_a_credentials_change_on_the_same_code_takes_its_parents_document(
        self, agent, monkeypatch
    ):
        from simulate.tasks.harness_agent_versions import (
            read_harness_agent_version_document,
        )

        parent = _connect(agent)
        store_agent(
            agent_version_id=parent.id, agent=source_agent(), sdk_version="0.2.3"
        )
        child = _connect(
            agent,
            parent=parent,
            trigger=HarnessAgentVersionRuntime.Trigger.CREDENTIALS,
            secret_values={"API_KEY": "fresh"},
        )
        assert child.id != parent.id
        monkeypatch.setattr(agent_reading_sandbox, "run_reading", _never)
        monkeypatch.setattr(
            "simulate.tasks.harness_agent_versions.close_old_connections", lambda: None
        )
        billed = _billed(monkeypatch)

        outcome = read_harness_agent_version_document._original_func(str(child.id))

        assert outcome == "reused"
        assert billed == []
        assert AgentVersionSetup.no_workspace_objects.filter(
            agent_version_id=child.id, status=AgentVersionSetup.Status.STORED
        ).exists()
