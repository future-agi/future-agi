"""Versions of a connected agent: made with no environment, passed into production,
followed or pinned by the environments that test the agent."""

from unittest.mock import patch

import pytest

from simulate.models import (
    AgentDefinition,
    AgentVersion,
    HarnessAgentVersionRuntime,
    HostedHarnessJob,
)
from simulate.services.agent_setup import store_agent
from simulate.services.agent_versions import set_production
from simulate.services.harness_agent_versions import (
    CONNECTED_OWNER,
    POLICY_FOLLOW,
    POLICY_KEY,
    POLICY_PINNED,
    activate_agent_version,
    create_agent_version,
    environment_policy,
)
from simulate.services.hosted_harness import BUILD_PIN_KEY
from simulate.tests.test_agent_overview_api import _environment
from simulate.tests.test_agent_setup_service import source_agent
from simulate.tests.test_harness_agent_versions import _preflight
from simulate.tests.test_harness_versioning_backfill import (
    storage as storage,  # noqa: F401 - fixture
)

pytestmark = pytest.mark.django_db

Trigger = HarnessAgentVersionRuntime.Trigger
Status = HarnessAgentVersionRuntime.PreflightStatus


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


def _source(commit="a"):
    return {
        "kind": "github",
        "repository": "acme/agent",
        "ref": "main",
        "commit_sha": commit * 40,
        "visibility": "public",
    }


def _connect(agent, *, parent=None, commit="a", trigger=Trigger.CONNECT, **extra):
    return create_agent_version(
        agent,
        parent=parent,
        env=None,
        source=_source(commit),
        prompt=extra.pop("prompt", "You help."),
        agent_payload={"connector": "auto", "config": {}},
        trigger=trigger,
        **extra,
    )


def _runtime(version):
    return HarnessAgentVersionRuntime.no_workspace_objects.get(agent_version=version)


def _completed(environment, version=None):
    HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
        state=HostedHarnessJob.State.COMPLETED, agent_version=version
    )
    environment.refresh_from_db()
    return environment


def _with_policy(environment, policy):
    environment.payload = {
        **environment.payload,
        "metadata": {
            **environment.payload.get("metadata", {}),
            POLICY_KEY: policy,
        },
    }
    environment.save(update_fields=["payload", "updated_at"])
    return environment


def _pinned(environment):
    return _with_policy(environment, POLICY_PINNED)


def _following(environment):
    return _with_policy(environment, POLICY_FOLLOW)


def test_a_connected_version_is_the_agents_own_and_a_pass_makes_it_production(agent):
    version = _connect(agent)

    runtime = _runtime(version)
    assert version.status == "draft"
    assert version.version_number == 1
    assert runtime.trigger == Trigger.CONNECT
    assert runtime.source_kind == HarnessAgentVersionRuntime.SourceKind.GITHUB
    assert runtime.source["commit_sha"] == "a" * 40
    assert runtime.agent_payload["definition_owner"] == CONNECTED_OWNER
    assert runtime.agent_payload["requested_by_environments"] == []
    assert runtime.agent_payload["requested_defaults"] == {}
    assert "created_for_environment" not in runtime.agent_payload
    assert runtime.preflight_status == Status.PENDING

    with patch(
        "simulate.services.harness_versioning.nudges.schedule_environment_nudge"
    ):
        result = _preflight(version)

    assert result.status == Status.PASSED
    version.refresh_from_db()
    assert version.status == "active"
    assert agent.active_version == version


def test_the_same_content_returns_the_existing_version(agent):
    first = _connect(agent)

    again = _connect(agent, parent=first, trigger=Trigger.SYNC)

    assert again.id == first.id
    assert AgentVersion.objects.filter(agent_definition=agent).count() == 1


def test_a_parent_without_a_side_row_needs_no_environment(agent):
    legacy = agent.create_version(
        description="Older prompt.", commit_message="v1", status="active"
    )

    version = _connect(agent, parent=legacy, prompt="")

    assert version.id != legacy.id
    assert version.description == "Older prompt."
    assert _runtime(version).parent is None


def test_production_moves_following_environments_and_offers_pinned_ones(agent):
    first = _connect(agent)
    with patch(
        "simulate.services.harness_versioning.nudges.schedule_environment_nudge"
    ):
        _preflight(first)
    following = _following(
        _built_from(_completed(_environment(agent, 1), first), first, source_agent())
    )
    pinned = _pinned(_completed(_environment(agent, 2), first))
    assert environment_policy(following) == "follow"
    assert environment_policy(pinned) == POLICY_PINNED

    second = _connect(agent, parent=first, commit="b", trigger=Trigger.SYNC)
    assert store_agent(
        agent_version_id=second.id, agent=source_agent(), sdk_version="0.2.3"
    )
    with patch(
        "simulate.services.harness_versioning.nudges.schedule_environment_nudge"
    ) as nudge:
        result = _preflight(second)
        set_production(second)

    assert result.status == Status.PASSED
    second.refresh_from_db()
    first.refresh_from_db()
    assert second.status == "active"
    assert first.status == "archived"
    following.refresh_from_db()
    pinned.refresh_from_db()
    assert following.agent_version_id == second.id
    assert pinned.agent_version_id == first.id
    offered = {
        (str(call.args[0]), str(call.args[1]), call.kwargs.get("default_moved"))
        for call in nudge.call_args_list
    }
    assert (str(pinned.id), str(second.id), False) in offered
    assert (str(following.id), str(second.id), True) in offered
    report = _runtime(second).preflight_report["activation"]
    assert {entry["environment_id"]: entry["outcome"] for entry in report} == {
        str(following.id): "activated",
        str(pinned.id): "pinned",
    }


def test_production_offers_an_environment_that_never_chose_to_follow(agent):
    first = _connect(agent)
    with patch(
        "simulate.services.harness_versioning.nudges.schedule_environment_nudge"
    ):
        _preflight(first)
    unchosen = _built_from(
        _completed(_environment(agent, 1), first), first, source_agent()
    )
    assert POLICY_KEY not in unchosen.payload.get("metadata", {})
    assert environment_policy(unchosen) == POLICY_PINNED

    second = _connect(agent, parent=first, commit="b", trigger=Trigger.SYNC)
    assert store_agent(
        agent_version_id=second.id, agent=source_agent(), sdk_version="0.2.3"
    )
    with patch(
        "simulate.services.harness_versioning.nudges.schedule_environment_nudge"
    ):
        _preflight(second)
    with patch(
        "simulate.services.harness_versioning.nudges.schedule_environment_nudge"
    ) as nudge:
        set_production(second)

    unchosen.refresh_from_db()
    assert unchosen.agent_version_id == first.id
    assert [
        (str(call.args[0]), call.kwargs.get("default_moved"))
        for call in nudge.call_args_list
    ] == [(str(unchosen.id), False)]
    report = _runtime(second).preflight_report["activation"]
    assert {entry["environment_id"]: entry["outcome"] for entry in report} == {
        str(unchosen.id): "pinned",
    }


def test_a_later_version_that_passes_is_offered_and_waits_for_production(agent):
    first = _connect(agent)
    with patch(
        "simulate.services.harness_versioning.nudges.schedule_environment_nudge"
    ):
        _preflight(first)
    following = _following(
        _built_from(_completed(_environment(agent, 1), first), first, source_agent())
    )
    second = _connect(agent, parent=first, commit="b", trigger=Trigger.SYNC)
    assert store_agent(
        agent_version_id=second.id, agent=source_agent(), sdk_version="0.2.3"
    )

    with patch(
        "simulate.services.harness_versioning.nudges.schedule_environment_nudge"
    ) as nudge:
        result = _preflight(second)

    assert result.status == Status.PASSED
    first.refresh_from_db()
    second.refresh_from_db()
    following.refresh_from_db()
    assert first.status == "active"
    assert second.status != "active"
    assert following.agent_version_id == first.id
    assert [
        (str(call.args[0]), str(call.args[1]), call.kwargs.get("default_moved"))
        for call in nudge.call_args_list
    ] == [(str(following.id), str(second.id), False)]
    report = _runtime(second).preflight_report["activation"]
    assert {entry["environment_id"]: entry["outcome"] for entry in report} == {
        str(following.id): "not_requested",
    }


def test_activating_a_connected_version_again_moves_nothing_twice(agent):
    first = _connect(agent)
    with patch(
        "simulate.services.harness_versioning.nudges.schedule_environment_nudge"
    ):
        _preflight(first)
    environment = _completed(_environment(agent, 1), first)

    with patch(
        "simulate.services.harness_versioning.nudges.schedule_environment_nudge"
    ) as nudge:
        moved = activate_agent_version(first)

    assert moved == []
    environment.refresh_from_db()
    assert environment.agent_version_id == first.id
    assert nudge.call_count == 0


def _built_from(environment, version, document):
    assert store_agent(agent_version_id=version.id, agent=document, sdk_version="0.2.3")
    HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
        payload={
            **environment.payload,
            "metadata": {
                **environment.payload.get("metadata", {}),
                BUILD_PIN_KEY: str(version.id),
            },
        }
    )
    environment.refresh_from_db()
    return environment


def test_a_major_change_is_offered_to_a_following_environment_not_applied(agent):
    first = _connect(agent)
    with patch(
        "simulate.services.harness_versioning.nudges.schedule_environment_nudge"
    ):
        _preflight(first)
    following = _following(
        _built_from(_completed(_environment(agent, 1), first), first, source_agent())
    )
    second = _connect(agent, parent=first, commit="b", trigger=Trigger.SYNC)
    document = source_agent()
    document["tools"].append(
        {
            "name": "issue_refund",
            "description": "Refund an order.",
            "kind": "builtin",
            "parameters": {"type": "object", "properties": {}, "required": []},
            "endpoint": None,
        }
    )
    assert store_agent(agent_version_id=second.id, agent=document, sdk_version="0.2.3")

    with patch(
        "simulate.services.harness_versioning.nudges.schedule_environment_nudge"
    ) as nudge:
        _preflight(second)
        set_production(second)

    second.refresh_from_db()
    following.refresh_from_db()
    assert second.status == "active"
    assert following.agent_version_id == first.id
    offered = {
        (str(call.args[0]), call.kwargs.get("default_moved"))
        for call in nudge.call_args_list
    }
    assert (str(following.id), False) in offered
    report = _runtime(second).preflight_report["activation"]
    assert {entry["environment_id"]: entry["outcome"] for entry in report} == {
        str(following.id): "major_change",
    }


def test_a_version_that_was_never_read_is_offered_not_applied(agent):
    first = _connect(agent)
    with patch(
        "simulate.services.harness_versioning.nudges.schedule_environment_nudge"
    ):
        _preflight(first)
    following = _following(
        _built_from(_completed(_environment(agent, 1), first), first, source_agent())
    )
    second = _connect(agent, parent=first, commit="b", trigger=Trigger.SYNC)

    with patch(
        "simulate.services.harness_versioning.nudges.schedule_environment_nudge"
    ) as nudge:
        _preflight(second)
        set_production(second)

    following.refresh_from_db()
    assert following.agent_version_id == first.id
    assert (str(following.id), False) in {
        (str(call.args[0]), call.kwargs.get("default_moved"))
        for call in nudge.call_args_list
    }
    report = _runtime(second).preflight_report["activation"]
    assert {entry["environment_id"]: entry["outcome"] for entry in report} == {
        str(following.id): "unmeasured",
    }


def test_returning_production_to_an_earlier_version_carries_followers_back(agent):
    first = _connect(agent)
    with patch(
        "simulate.services.harness_versioning.nudges.schedule_environment_nudge"
    ):
        _preflight(first)
    following = _following(
        _built_from(_completed(_environment(agent, 1), first), first, source_agent())
    )
    second = _connect(agent, parent=first, commit="b", trigger=Trigger.SYNC)
    assert store_agent(
        agent_version_id=second.id, agent=source_agent(), sdk_version="0.2.3"
    )
    with patch(
        "simulate.services.harness_versioning.nudges.schedule_environment_nudge"
    ):
        _preflight(second)
        set_production(second)
    following.refresh_from_db()
    assert following.agent_version_id == second.id

    with patch(
        "simulate.services.harness_versioning.nudges.schedule_environment_nudge"
    ):
        moved = set_production(first)

    first.refresh_from_db()
    second.refresh_from_db()
    following.refresh_from_db()
    assert first.status == "active"
    assert second.status == "archived"
    assert following.agent_version_id == first.id
    assert [str(one) for one in moved] == [str(following.id)]


def _passed(agent):
    first = _connect(agent)
    with patch(
        "simulate.services.harness_versioning.nudges.schedule_environment_nudge"
    ):
        _preflight(first)
    first.refresh_from_db()
    return first


def _compatible_draft(agent, parent):
    second = _connect(agent, parent=parent, commit="b", trigger=Trigger.SYNC)
    assert store_agent(
        agent_version_id=second.id, agent=source_agent(), sdk_version="0.2.3"
    )
    with patch(
        "simulate.services.harness_versioning.nudges.schedule_environment_nudge"
    ):
        _preflight(second)
    second.refresh_from_db()
    return second


def test_a_pin_made_while_a_move_waits_keeps_the_environment_where_it_is(agent, user):
    from simulate.services.agent_versions import set_environment_policy
    from simulate.services.harness_versioning.rebuild import (
        retry_deferred_activations,
    )

    first = _passed(agent)
    following = _following(
        _built_from(_completed(_environment(agent, 1), first), first, source_agent())
    )
    second = _compatible_draft(agent, first)
    HostedHarnessJob.no_workspace_objects.filter(id=following.id).update(
        state=HostedHarnessJob.State.RUNNING
    )
    with patch(
        "simulate.services.harness_versioning.nudges.schedule_environment_nudge"
    ):
        set_production(second)
    report = _runtime(second).preflight_report["activation"]
    assert {entry["environment_id"]: entry["outcome"] for entry in report} == {
        str(following.id): "environment_busy",
    }

    following.refresh_from_db()
    set_environment_policy(following, policy=POLICY_PINNED, user=user)
    HostedHarnessJob.no_workspace_objects.filter(id=following.id).update(
        state=HostedHarnessJob.State.COMPLETED
    )
    with patch(
        "simulate.services.harness_versioning.nudges.schedule_environment_nudge"
    ):
        outcomes = retry_deferred_activations(second.id)

    following.refresh_from_db()
    assert outcomes == {str(following.id): "not_moved"}
    assert following.agent_version_id == first.id
    assert environment_policy(following) == POLICY_PINNED


def test_compatibility_starts_from_the_agent_the_world_was_rebuilt_for(agent):
    from simulate.services.agent_compatibility import built_from
    from simulate.tests.test_agent_overview_api import _environment_version

    first = _passed(agent)
    second = _compatible_draft(agent, first)
    environment = _built_from(
        _completed(_environment(agent, 1), second), first, source_agent()
    )
    rebuilt = _environment_version(environment, 2)
    rebuilt.agent_version = second
    rebuilt.save(update_fields=["agent_version"])
    HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
        environment_version=rebuilt
    )
    environment.refresh_from_db()

    assert built_from(environment) == second


def test_the_first_build_pin_is_the_base_when_no_world_records_its_agent(agent):
    from simulate.services.agent_compatibility import built_from

    first = _passed(agent)
    second = _compatible_draft(agent, first)
    environment = _built_from(
        _completed(_environment(agent, 1), second), first, source_agent()
    )

    assert built_from(environment) == first


def test_a_first_production_only_lands_when_the_agent_has_none(agent):
    from simulate.services.harness_agent_versions import make_production

    first = _passed(agent)
    second = _connect(agent, parent=first, commit="b", trigger=Trigger.SYNC)

    assert make_production(second, only_if_none=True) == "has_production"
    second.refresh_from_db()
    first.refresh_from_db()
    assert second.status != "active"
    assert first.status == "active"

    AgentVersion._base_manager.filter(id=first.id).update(status="archived")
    assert make_production(second, only_if_none=True) == "promoted"
    second.refresh_from_db()
    assert second.status == "active"


def test_a_discarded_version_never_becomes_production(agent):
    from simulate.services.harness_agent_versions import (
        make_production,
        promote_to_production,
    )
    from simulate.services.hosted_harness import HostedHarnessError

    first = _passed(agent)
    second = _compatible_draft(agent, first)
    HarnessAgentVersionRuntime.no_workspace_objects.filter(agent_version=second).update(
        preflight_status=Status.DISCARDED
    )

    assert make_production(second) == "discarded"
    with pytest.raises(HostedHarnessError) as refused:
        promote_to_production(second)

    assert refused.value.code == "version_discarded"
    assert refused.value.status_code == 409
    second.refresh_from_db()
    first.refresh_from_db()
    assert second.status != "active"
    assert first.status == "active"


def test_discard_reads_production_now_not_from_a_stale_copy(agent):
    from simulate.services.agent_versions import discard_version
    from simulate.services.hosted_harness import HostedHarnessError

    first = _passed(agent)
    stale = AgentVersion.objects.get(id=first.id)
    stale.status = "draft"

    with pytest.raises(HostedHarnessError) as refused:
        discard_version(stale)

    assert refused.value.code == "version_in_production"
    assert _runtime(first).preflight_status == Status.PASSED


def test_production_adds_followers_to_the_environments_already_waiting(agent):
    first = _passed(agent)
    following = _following(
        _built_from(_completed(_environment(agent, 1), first), first, source_agent())
    )
    second = _compatible_draft(agent, first)
    waiting = "00000000-0000-0000-0000-000000000042"
    runtime = _runtime(second)
    runtime.agent_payload = {
        **runtime.agent_payload,
        "requested_by_environments": [waiting],
        "requested_at": {waiting: "2026-11-02T00:00:00+00:00"},
    }
    runtime.save(update_fields=["agent_payload"])

    with patch(
        "simulate.services.harness_versioning.nudges.schedule_environment_nudge"
    ):
        set_production(second)

    payload = _runtime(second).agent_payload
    assert payload["requested_by_environments"] == [waiting, str(following.id)]
    assert set(payload["requested_at"]) == {waiting, str(following.id)}


def test_a_follow_set_by_hand_can_return_to_an_earlier_production(agent, user):
    from simulate.services.agent_versions import set_environment_policy

    nudge = "simulate.services.harness_versioning.nudges.schedule_environment_nudge"
    first = _passed(agent)
    pinned = _pinned(
        _built_from(_completed(_environment(agent, 1), first), first, source_agent())
    )
    second = _compatible_draft(agent, first)
    with patch(nudge):
        set_production(second)
    pinned.refresh_from_db()
    assert pinned.agent_version_id == first.id

    with patch(nudge):
        following = set_environment_policy(pinned, policy=POLICY_FOLLOW, user=user)
    assert following.agent_version_id == second.id

    with patch(nudge):
        moved = set_production(first)

    following.refresh_from_db()
    assert following.agent_version_id == first.id
    assert [str(one) for one in moved] == [str(following.id)]
    report = _runtime(first).preflight_report["activation"]
    assert {entry["environment_id"]: entry["outcome"] for entry in report} == {
        str(following.id): "activated"
    }


def test_switching_to_follow_while_on_production_records_the_request(agent, user):
    from simulate.services.agent_versions import set_environment_policy

    first = _passed(agent)
    pinned = _pinned(
        _built_from(_completed(_environment(agent, 1), first), first, source_agent())
    )

    following = set_environment_policy(pinned, policy=POLICY_FOLLOW, user=user)

    assert following.agent_version_id == first.id
    assert str(following.id) in _runtime(first).agent_payload["requested_at"]
