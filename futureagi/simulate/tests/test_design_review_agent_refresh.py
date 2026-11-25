import copy
from unittest.mock import patch

import pytest

from simulate.models import AgentDefinition, AgentVersion, HarnessAgentVersionRuntime
from simulate.services import agent_versions
from simulate.services.harness_agent_versions import create_agent_version
from simulate.services.harness_design_review import (
    AWAITING_APPROVAL,
    READING,
    READING_STAGE,
    begin_design_review,
    stored_review,
)
from simulate.services.hosted_harness import BUILD_PIN_KEY, HostedHarnessError
from simulate.services.provider_profile import _safe, profile_digest
from simulate.tests.test_harness_design_review import CONTRACT
from simulate.tests.test_harness_design_review_reading import _received

pytestmark = pytest.mark.django_db

Trigger = HarnessAgentVersionRuntime.Trigger
READ = "simulate.services.provider_profile.read_profile"
STORED = "simulate.services.agent_contracts.stored_version_contract"
SCHEDULE = "simulate.tasks.harness_agent_versions.schedule_design_reading"
PREFLIGHT = "simulate.services.harness_agent_versions._schedule_preflight"
LOGGER = "simulate.services.harness_design_review.logger"

PINNED = {
    "name": "Parcelpoint",
    "model": {
        "provider": "openai",
        "model": "gpt-4o",
        "messages": [{"role": "system", "content": "Track parcels."}],
        "tools": [{"type": "function", "function": {"name": "track_parcel"}}],
    },
}


def _live():
    live = copy.deepcopy(PINNED)
    live["model"]["messages"][0]["content"] = "Track parcels and open claims."
    live["model"]["tools"].append(
        {"type": "function", "function": {"name": "open_claim"}}
    )
    return live


@pytest.fixture(autouse=True)
def hosted_review(settings):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False
    settings.HARNESS_DESIGN_REVIEW_ENABLED = True
    settings.HARNESS_SEED_CONTRACT_ENABLED = True
    settings.ALK_HOSTED_BUILDER_SESSION = True
    settings.HARNESS_HOSTED_DESIGN_REVIEW_ENABLED = True
    return settings


@pytest.fixture
def agent(organization, workspace):
    definition = AgentDefinition.objects.create(
        agent_name="parcelpoint",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        provider="vapi",
        assistant_id="asst-1",
        inbound=True,
        organization=organization,
        workspace=workspace,
    )
    with patch(PREFLIGHT):
        create_agent_version(
            definition,
            parent=None,
            env=None,
            source={"kind": "provider"},
            agent_payload={"connector": "vapi", "config": {"assistant_id": "asst-1"}},
            trigger=Trigger.CONNECT,
        )
    return definition


def _first(definition):
    return AgentVersion.no_workspace_objects.get(
        agent_definition=definition, version_number=1
    )


def _runtime_of(version):
    return HarnessAgentVersionRuntime.no_workspace_objects.get(agent_version=version)


def _versions(definition):
    return list(
        AgentVersion.no_workspace_objects.filter(agent_definition=definition)
        .order_by("version_number")
        .values_list("version_number", flat=True)
    )


def _pin(definition):
    with patch(READ, return_value=_safe(copy.deepcopy(PINNED))):
        agent_versions.sync_version(definition)
    return _first(definition)


def _build_on(user, workspace, version):
    job = _received(user, workspace)
    job.agent_version = version
    job.save(update_fields=["agent_version", "updated_at"])
    return job


def _begin(job, *, live=None, failure=None, runtime=None, contract=None):
    with (
        patch(
            READ,
            return_value=_safe(copy.deepcopy(live)) if live is not None else None,
            side_effect=failure,
        ) as read,
        patch(STORED, return_value=None),
        patch(SCHEDULE),
        patch(PREFLIGHT),
        patch(LOGGER) as logger,
    ):
        begin_design_review(job, runtime, contract)
    job.refresh_from_db()
    return read, logger


class TestRefreshBeforeReview:
    def test_a_changed_agent_is_synced_and_read_on_its_new_version(
        self, agent, user, workspace
    ):
        pinned = _pin(agent)
        job = _build_on(user, workspace, pinned)

        _begin(job, live=_live())

        assert _versions(agent) == [1, 2]
        current = AgentVersion.no_workspace_objects.get(
            agent_definition=agent, version_number=2
        )
        assert job.agent_version_id == current.id
        assert job.payload["metadata"][BUILD_PIN_KEY] == str(current.id)
        assert _runtime_of(current).source["profile_digest"] == profile_digest(
            _safe(_live())
        )
        review = stored_review(job)
        assert review["status"] == READING
        assert review["agent_version_id"] == str(current.id)
        assert job.current_stage == READING_STAGE

    def test_a_design_read_from_the_pinned_agent_is_not_reviewed(
        self, agent, user, workspace
    ):
        pinned = _pin(agent)
        job = _build_on(user, workspace, pinned)

        _begin(
            job,
            live=_live(),
            runtime=_runtime_of(pinned),
            contract={**copy.deepcopy(CONTRACT), "reading_inputs": {}},
        )

        review = stored_review(job)
        assert review["status"] == READING
        assert review["agent_version_id"] == str(job.agent_version_id)
        assert job.agent_version_id != pinned.id

    def test_an_unchanged_agent_keeps_its_version(self, agent, user, workspace):
        pinned = _pin(agent)
        job = _build_on(user, workspace, pinned)

        read, _logger = _begin(job, live=PINNED)

        read.assert_called_once()
        assert _versions(agent) == [1]
        assert job.agent_version_id == pinned.id
        assert stored_review(job)["agent_version_id"] == str(pinned.id)

    def test_an_unchanged_agent_with_a_design_opens_it_at_once(
        self, agent, user, workspace
    ):
        pinned = _pin(agent)
        job = _build_on(user, workspace, pinned)

        _begin(
            job,
            live=PINNED,
            runtime=_runtime_of(pinned),
            contract={**copy.deepcopy(CONTRACT), "reading_inputs": {}},
        )

        assert stored_review(job)["status"] == AWAITING_APPROVAL
        assert job.agent_version_id == pinned.id

    def test_an_unreachable_provider_keeps_the_pinned_profile(
        self, agent, user, workspace
    ):
        pinned = _pin(agent)
        job = _build_on(user, workspace, pinned)
        unreachable = HostedHarnessError(
            "provider_unreachable",
            "The provider could not be reached; try again",
            status_code=503,
            retryable=True,
        )

        read, logger = _begin(job, failure=unreachable)

        read.assert_called_once()
        assert _versions(agent) == [1]
        assert job.agent_version_id == pinned.id
        assert stored_review(job)["status"] == READING
        warning = logger.warning.call_args
        assert warning.args == ("design_review_agent_not_refreshed",)
        assert warning.kwargs["code"] == "provider_unreachable"
        assert warning.kwargs["exc_info"] is True

    def test_a_version_with_nothing_pinned_is_not_read_again(
        self, agent, user, workspace
    ):
        job = _build_on(user, workspace, _first(agent))

        read, _logger = _begin(job, live=_live())

        read.assert_not_called()
        assert _versions(agent) == [1]
        assert stored_review(job)["agent_version_id"] == str(_first(agent).id)
