import copy
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from simulate.models import HarnessAgentVersionRuntime, HostedHarnessJob
from simulate.services.agent_reading_sandbox import (
    PROFILE_ARGS,
    PROFILE_PATH,
    READ_ARGS,
    read_version_contract,
    reading_job_document,
)
from simulate.services.agent_version_steps import _same_code
from simulate.services.harness_design_review import (
    AWAITING_APPROVAL,
    AWAITING_STAGE,
    READING,
    READING_FAILED,
    READING_STAGE,
    begin_design_review,
    close_open_review,
    design_review_pending,
    open_review_after_reading,
    review_applies,
    review_wanted,
    reviewable,
    stored_review,
)
from simulate.services.hosted_harness import create_hosted_job
from simulate.services.hosted_harness_gateway import _seed_profile_for
from simulate.tests.test_harness_agent_creation import (  # noqa: F401
    _body,
    _job,
    _livekit,
    _phone,
    _post,
    _vapi,
    gates,
)
from simulate.tests.test_harness_design_review import CONTRACT
from simulate.tests.test_harness_provider import _v1_payload

SourceKind = HarnessAgentVersionRuntime.SourceKind
SCHEDULE = "simulate.tasks.harness_agent_versions.schedule_design_reading"
STORED = "simulate.services.agent_contracts.stored_version_contract"
SIDE_ROW = "simulate.services.harness_agent_versions._side_row"
COMMIT = "a" * 40


@pytest.fixture
def review_on(settings):
    settings.HARNESS_DESIGN_REVIEW_ENABLED = True
    settings.HARNESS_SEED_CONTRACT_ENABLED = True
    settings.ALK_HOSTED_BUILDER_SESSION = True
    settings.HARNESS_HOSTED_DESIGN_REVIEW_ENABLED = False
    return settings


@pytest.fixture
def hosted_on(review_on):
    review_on.HARNESS_HOSTED_DESIGN_REVIEW_ENABLED = True
    return review_on


@pytest.fixture
def client(user):
    api = APIClient()
    api.force_authenticate(user=user)
    return api


def _runtime(
    kind=SourceKind.GITHUB, connector="auto", mode="", version_id=None, description=""
):
    return SimpleNamespace(
        agent_version_id=version_id or uuid.uuid4(),
        agent_version=SimpleNamespace(description=description),
        source_kind=kind,
        source={},
        agent_payload={"connector": connector, **({"mode": mode} if mode else {})},
    )


def _received(user, workspace, **overrides):
    job, _ = create_hosted_job(
        user.organization,
        _v1_payload(scenario_count=4, **overrides),
        idempotency_key=uuid.uuid4().hex,
        workspace=workspace,
        state=HostedHarnessJob.State.RECEIVED,
    )
    return job


def _reading(user, workspace, runtime, capture):
    job = _received(user, workspace)
    with (
        patch(STORED, return_value=None),
        patch(SCHEDULE) as schedule,
        capture(execute=True),
    ):
        begin_design_review(job, runtime, None)
    schedule.assert_called_once_with(str(job.id))
    return job


class TestGate:
    def test_code_agents_are_reviewed_and_hosted_ones_wait_for_their_switch(
        self, review_on
    ):
        assert reviewable(SourceKind.GITHUB, "auto")
        assert reviewable(SourceKind.ARCHIVE, "auto")
        assert not reviewable(SourceKind.PHONE, "phone")
        assert not reviewable(SourceKind.PROVIDER, "vapi")

    def test_every_hosted_agent_is_reviewed_once_switched_on(self, hosted_on):
        assert reviewable(SourceKind.PHONE, "phone")
        assert reviewable(SourceKind.LIVEKIT, "livekit")
        for connector in ("vapi", "retell", "retell_chat"):
            assert reviewable(SourceKind.PROVIDER, connector)

    def test_a_remote_endpoint_or_an_import_is_never_reviewed(self, hosted_on):
        assert not reviewable(SourceKind.PROVIDER, "remote")
        assert not reviewable(SourceKind.PROVIDER, "vapi", "provider_import")
        assert not reviewable(SourceKind.GITHUB, "vapi", "provider_import")

    def test_a_runtime_is_judged_by_its_connector(self, hosted_on):
        assert review_applies(_runtime(SourceKind.PROVIDER, "retell"))
        assert not review_applies(_runtime(SourceKind.PROVIDER, "remote"))

    def test_nothing_is_reviewed_without_the_review_switches(self, settings):
        settings.HARNESS_DESIGN_REVIEW_ENABLED = False
        settings.HARNESS_HOSTED_DESIGN_REVIEW_ENABLED = True

        assert not review_applies(_runtime())
        assert not review_wanted(_phone(), None)

    def test_a_new_agent_is_judged_by_its_payload(self, hosted_on):
        assert review_wanted(_body(), None)
        assert review_wanted(_phone(), None)
        assert review_wanted(_livekit(), None)
        assert review_wanted(_vapi(), None)
        hosted_on.HARNESS_HOSTED_DESIGN_REVIEW_ENABLED = False
        assert review_wanted(_body(), None)
        assert not review_wanted(_vapi(), None)


@pytest.mark.django_db
class TestBeginReview:
    def test_a_version_with_a_design_waits_for_approval(
        self, user, workspace, review_on
    ):
        job = _received(user, workspace)

        with (
            patch(
                STORED, return_value={**copy.deepcopy(CONTRACT), "reading_inputs": {}}
            ),
            patch(SCHEDULE) as schedule,
        ):
            begin_design_review(job, _runtime(), None)

        job.refresh_from_db()
        assert (job.state, job.current_stage) == (
            HostedHarnessJob.State.RECEIVED,
            AWAITING_STAGE,
        )
        assert stored_review(job)["status"] == AWAITING_APPROVAL
        schedule.assert_not_called()

    def test_a_version_without_a_design_is_read_first(
        self, user, workspace, hosted_on, django_capture_on_commit_callbacks
    ):
        runtime = _runtime(SourceKind.PHONE, "phone", "connect_only")

        job = _reading(user, workspace, runtime, django_capture_on_commit_callbacks)

        job.refresh_from_db()
        assert (job.state, job.current_stage) == (
            HostedHarnessJob.State.RECEIVED,
            READING_STAGE,
        )
        review = stored_review(job)
        assert review["status"] == READING
        assert review["agent_version_id"] == str(runtime.agent_version_id)
        assert design_review_pending(job)

    def test_a_build_sends_the_agent_its_reading_read(
        self, user, workspace, hosted_on, django_capture_on_commit_callbacks
    ):
        runtime = _runtime(
            SourceKind.PHONE, "phone", "connect_only", description="You are the clinic."
        )

        job = _reading(user, workspace, runtime, django_capture_on_commit_callbacks)

        job.refresh_from_db()
        agent = job.payload["agent"]
        assert agent["config"]["target_system_prompt"] == "You are the clinic."
        assert agent["mode"] == "connect_only"

    def test_an_agent_that_is_not_reviewed_is_queued(self, user, workspace, review_on):
        job = _received(user, workspace)

        with patch(STORED) as stored, patch(SCHEDULE) as schedule:
            begin_design_review(job, _runtime(SourceKind.PHONE, "phone"), None)

        job.refresh_from_db()
        assert (job.state, job.current_stage) == (
            HostedHarnessJob.State.QUEUED,
            "queued",
        )
        assert stored_review(job) is None
        stored.assert_not_called()
        schedule.assert_not_called()

    def test_a_reading_that_cannot_start_fails_the_build(
        self, user, workspace, hosted_on, django_capture_on_commit_callbacks
    ):
        job = _received(user, workspace)

        with (
            patch(STORED, return_value=None),
            patch(SCHEDULE, side_effect=RuntimeError("temporal down")),
            django_capture_on_commit_callbacks(execute=True),
        ):
            begin_design_review(job, _runtime(SourceKind.LIVEKIT, "livekit"), None)

        job.refresh_from_db()
        assert job.state == HostedHarnessJob.State.FAILED
        assert job.failure["code"] == "scheduler_unavailable"
        assert stored_review(job)["status"] == READING_FAILED

    def test_the_design_api_has_nothing_to_show_while_the_agent_is_read(
        self, client, user, workspace, hosted_on, django_capture_on_commit_callbacks
    ):
        job = _reading(
            user,
            workspace,
            _runtime(SourceKind.PHONE, "phone"),
            django_capture_on_commit_callbacks,
        )

        response = client.get(
            f"/simulate/api/harness-environments/{job.id}/design-review/",
            HTTP_X_WORKSPACE_ID=str(workspace.id),
        )

        assert response.status_code == 404
        assert response.json()["error"] == "design_review_not_found"


@pytest.mark.django_db
class TestAfterReading:
    def test_the_read_design_opens_for_review_on_the_read_commit(
        self, user, workspace, review_on, django_capture_on_commit_callbacks
    ):
        runtime = _runtime()
        job = _reading(user, workspace, runtime, django_capture_on_commit_callbacks)

        with (
            patch(SIDE_ROW, return_value=runtime),
            patch(STORED, return_value=copy.deepcopy(CONTRACT)),
        ):
            opened = open_review_after_reading(str(job.id), commit_sha=COMMIT)

        assert opened == "opened"
        job.refresh_from_db()
        assert (job.state, job.current_stage) == (
            HostedHarnessJob.State.RECEIVED,
            AWAITING_STAGE,
        )
        review = stored_review(job)
        assert review["status"] == AWAITING_APPROVAL
        assert len(review["proposal"]["tools"]) == 4
        assert job.payload["source"]["commit_sha"] == COMMIT

    def test_a_reading_that_left_no_design_fails_the_build(
        self, user, workspace, hosted_on, django_capture_on_commit_callbacks
    ):
        runtime = _runtime(SourceKind.PROVIDER, "vapi", "connect_only")
        job = _reading(user, workspace, runtime, django_capture_on_commit_callbacks)

        with patch(SIDE_ROW, return_value=runtime), patch(STORED, return_value=None):
            opened = open_review_after_reading(
                str(job.id),
                code="provider_profile_unavailable",
                message="The provider's definition of this agent couldn't be read.",
                retryable=True,
            )

        assert opened == "failed"
        job.refresh_from_db()
        assert job.state == HostedHarnessJob.State.FAILED
        assert job.failure["code"] == "provider_profile_unavailable"
        assert job.failure["stage"] == READING_STAGE
        assert not design_review_pending(job)

    def test_a_build_closed_while_its_agent_was_read_is_left_alone(
        self, user, workspace, review_on, django_capture_on_commit_callbacks
    ):
        runtime = _runtime()
        job = _reading(user, workspace, runtime, django_capture_on_commit_callbacks)
        assert close_open_review(job)
        job.save(update_fields=["payload", "updated_at"])

        with (
            patch(SIDE_ROW, return_value=runtime),
            patch(STORED, return_value=copy.deepcopy(CONTRACT)),
        ):
            opened = open_review_after_reading(str(job.id))

        assert opened == "closed"
        job.refresh_from_db()
        assert stored_review(job)["status"] == "discarded"


@pytest.mark.django_db
class TestCreate:
    def test_a_build_from_the_drawer_reads_its_agent_then_waits(
        self,
        client,
        workspace,
        gates,  # noqa: F811
        review_on,
        django_capture_on_commit_callbacks,
    ):
        review_on.HARNESS_VERSIONING_ENABLED = True
        review_on.HARNESS_PROVIDER = "hosted"
        with (
            patch(STORED, return_value=None),
            patch(SCHEDULE) as schedule,
            django_capture_on_commit_callbacks(execute=True),
        ):
            job = _job(_post(client, workspace, _body()))

        assert (job.state, job.current_stage) == (
            HostedHarnessJob.State.RECEIVED,
            READING_STAGE,
        )
        assert stored_review(job)["agent_version_id"] == str(job.agent_version_id)
        schedule.assert_called_once_with(str(job.id))
        gates.start.assert_not_called()

    @pytest.mark.parametrize("body", [_phone, _livekit, _vapi])
    def test_a_hosted_build_reads_its_agent_once_switched_on(
        self,
        client,
        workspace,
        gates,  # noqa: F811
        hosted_on,
        django_capture_on_commit_callbacks,
        body,
    ):
        hosted_on.HARNESS_VERSIONING_ENABLED = True
        hosted_on.HARNESS_PROVIDER = "hosted"
        with (
            patch(STORED, return_value=None),
            patch(SCHEDULE) as schedule,
            django_capture_on_commit_callbacks(execute=True),
        ):
            job = _job(_post(client, workspace, body()))

        assert stored_review(job)["status"] == READING
        schedule.assert_called_once_with(str(job.id))
        gates.start.assert_not_called()

    def test_a_hosted_build_starts_at_once_while_its_review_is_off(
        self,
        client,
        workspace,
        gates,  # noqa: F811
        review_on,
    ):
        review_on.HARNESS_VERSIONING_ENABLED = True
        review_on.HARNESS_PROVIDER = "hosted"
        with patch(SCHEDULE) as schedule:
            job = _job(_post(client, workspace, _phone()))

        assert job.state == HostedHarnessJob.State.QUEUED
        assert stored_review(job) is None
        schedule.assert_not_called()
        gates.start.assert_called_once()


@pytest.mark.django_db
class TestHostedReading:
    @pytest.fixture
    def phone_runtime(self, client, workspace, gates, settings):  # noqa: F811
        settings.HARNESS_VERSIONING_ENABLED = True
        settings.HARNESS_PROVIDER = "hosted"
        job = _job(_post(client, workspace, _phone(prompt="You are the clinic.")))
        return HarnessAgentVersionRuntime.no_workspace_objects.get(
            agent_version_id=job.agent_version_id
        )

    @pytest.fixture
    def vapi_runtime(self, client, workspace, gates, settings):  # noqa: F811
        settings.HARNESS_VERSIONING_ENABLED = True
        settings.HARNESS_PROVIDER = "hosted"
        job = _job(_post(client, workspace, _vapi()))
        return HarnessAgentVersionRuntime.no_workspace_objects.get(
            agent_version_id=job.agent_version_id
        )

    def test_a_phone_reading_is_a_connect_only_job_with_no_repository(
        self, phone_runtime
    ):
        with patch("simulate.services.harness_evals.briefing_evals", return_value=[]):
            document = reading_job_document(
                phone_runtime.agent_definition, phone_runtime
            )

        assert document["source"] == {"kind": "provider", "visibility": "public"}
        agent = document["agent"]
        assert (agent["connector"], agent["mode"]) == ("phone", "connect_only")
        assert agent["config"]["target_system_prompt"] == "You are the clinic."
        assert agent["config"]["phone_number"] == "+14155550100"

    def test_a_provider_reading_reads_the_definition_its_version_holds(
        self, vapi_runtime
    ):
        reading = (None, {}, copy.deepcopy(CONTRACT), None)
        with (
            patch("simulate.services.agent_reading_sandbox.require_harness_authoring"),
            patch(
                "simulate.services.agent_reading_sandbox._pinned_profile",
                return_value=b'{"provider": "vapi"}\n',
            ),
            patch(
                "simulate.services.agent_reading_sandbox.run_reading",
                return_value=reading,
            ) as run,
            patch(
                "simulate.services.agent_reading_sandbox.store_version_contract",
                return_value=True,
            ) as store,
            patch("simulate.services.harness_evals.briefing_evals", return_value=[]),
        ):
            outcome = read_version_contract(vapi_runtime.agent_version_id)

        assert outcome.status == "stored"
        call = run.call_args
        assert call.kwargs["args"] == f"{READ_ARGS} {PROFILE_ARGS}"
        assert call.kwargs["files"] == {PROFILE_PATH: b'{"provider": "vapi"}\n'}
        job_document = call.args[1]
        assert job_document["source"]["kind"] == "provider"
        assert job_document["agent"]["config"]["assistant_id"] == "asst-1"
        assert store.call_args.kwargs["contract"] == CONTRACT

    def test_a_provider_whose_definition_cannot_be_read_is_not_read(self, vapi_runtime):
        with (
            patch("simulate.services.agent_reading_sandbox.require_harness_authoring"),
            patch(
                "simulate.services.agent_reading_sandbox._pinned_profile",
                return_value=None,
            ),
            patch("simulate.services.agent_reading_sandbox.run_reading") as run,
        ):
            outcome = read_version_contract(vapi_runtime.agent_version_id)

        assert (outcome.status, outcome.code, outcome.retryable) == (
            "failed",
            "provider_profile_unavailable",
            True,
        )
        run.assert_not_called()

    def test_a_version_with_a_design_is_not_read_again(self, phone_runtime):
        with (
            patch(
                "simulate.services.agent_contracts.version_contract_key",
                return_value="agent-contracts/stored.json",
            ),
            patch(
                "simulate.services.agent_contracts.read_contract",
                return_value={**CONTRACT, "reading_inputs": {}},
            ),
            patch("simulate.services.agent_reading_sandbox.run_reading") as run,
        ):
            outcome = read_version_contract(phone_runtime.agent_version_id)

        assert (outcome.status, outcome.code) == ("skipped", "already_stored")
        run.assert_not_called()

    def test_a_design_stored_without_its_record_is_read_again_and_replaced(
        self, vapi_runtime
    ):
        reading = (None, {}, copy.deepcopy(CONTRACT), None)
        with (
            patch(
                "simulate.services.agent_contracts.version_contract_key",
                return_value="agent-contracts/stored.json",
            ),
            patch(
                "simulate.services.agent_contracts.read_contract",
                return_value=copy.deepcopy(CONTRACT),
            ),
            patch("simulate.services.agent_reading_sandbox.require_harness_authoring"),
            patch(
                "simulate.services.agent_reading_sandbox._pinned_profile",
                return_value=b'{"provider": "vapi"}\n',
            ),
            patch(
                "simulate.services.agent_reading_sandbox.run_reading",
                return_value=reading,
            ) as run,
            patch(
                "simulate.services.agent_reading_sandbox.store_version_contract",
                return_value=True,
            ) as store,
            patch("simulate.services.harness_evals.briefing_evals", return_value=[]),
        ):
            outcome = read_version_contract(vapi_runtime.agent_version_id)

        assert outcome.status == "stored"
        run.assert_called_once()
        assert store.call_args.kwargs["replace"] is True


class TestSeededProviderBuild:
    def test_a_seeded_provider_build_reads_the_profile_its_version_holds(self):
        version = SimpleNamespace(id=uuid.uuid4())
        job = SimpleNamespace(
            payload={"source": {"kind": "provider"}, "metadata": {}},
            agent_version=version,
        )

        with patch(
            "simulate.services.hosted_harness_gateway._provider_profile_bytes",
            return_value=b"{}\n",
        ) as profile:
            assert _seed_profile_for(job) == b"{}\n"

        profile.assert_called_once_with(version)

    def test_a_code_build_hands_no_profile(self):
        job = SimpleNamespace(
            payload={"source": {"kind": "github"}, "metadata": {}},
            agent_version=SimpleNamespace(id=uuid.uuid4()),
        )

        assert _seed_profile_for(job) is None


class TestSyncedVersion:
    @pytest.mark.parametrize(
        "kind", [SourceKind.PROVIDER, SourceKind.PHONE, SourceKind.LIVEKIT]
    )
    def test_a_hosted_version_never_takes_its_parents_design(self, kind):
        facts = {"commit_sha": COMMIT, "profile_digest": "sha256:same"}
        runtime = SimpleNamespace(source_kind=kind, source=dict(facts))
        parent = SimpleNamespace(source_kind=kind, source=dict(facts))

        assert not _same_code(runtime, parent)

    def test_a_code_version_on_its_parents_commit_still_takes_its_reading(self):
        runtime = SimpleNamespace(
            source_kind=SourceKind.GITHUB, source={"commit_sha": COMMIT}
        )
        parent = SimpleNamespace(
            source_kind=SourceKind.GITHUB, source={"commit_sha": COMMIT}
        )

        assert _same_code(runtime, parent)
