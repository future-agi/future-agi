"""A connected agent's version lifecycle: rollbacks past a move by hand, production
moves fenced to followers of the version still production, a check that never
started, a lost cancel, and the version the change and production answers carry."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework import status
from structlog.testing import capture_logs
from temporalio.service import RPCError, RPCStatusCode

from simulate.models import HarnessAgentVersionRuntime, HostedHarnessJob
from simulate.services.agent_setup import store_agent
from simulate.services.agent_version_steps import check_failure
from simulate.services.agent_versions import set_production
from simulate.services.harness_agent_changes import HeadResult
from simulate.services.harness_agent_versions import (
    HAND_MOVE_KEY,
    POLICY_KEY,
    POLICY_PINNED,
    _schedule_preflight,
    _turned_down,
    retry_preflight,
)
from simulate.tests.test_agent_overview_api import _environment
from simulate.tests.test_agent_setup_service import source_agent
from simulate.tests.test_harness_agent_versions import _preflight
from simulate.tests.test_harness_connected_versions import (
    _built_from,
    _completed,
    _connect,
    _following,
    _passed,
    _runtime,
)
from simulate.tests.test_harness_connected_versions import (
    agent as agent,  # noqa: F401 - fixture
)
from simulate.tests.test_harness_connected_versions import (
    versioning_on as versioning_on,  # noqa: F401 - fixture
)

pytestmark = pytest.mark.django_db

AGENTS = "/simulate/agent-definitions/"
NUDGE = "simulate.services.harness_versioning.nudges.schedule_environment_nudge"
Trigger = HarnessAgentVersionRuntime.Trigger
Status = HarnessAgentVersionRuntime.PreflightStatus


def _checked(agent, parent, commit, document=None):
    version = _connect(agent, parent=parent, commit=commit, trigger=Trigger.SYNC)
    assert store_agent(
        agent_version_id=version.id,
        agent=document or source_agent(),
        sdk_version="0.2.3",
    )
    with patch(NUDGE):
        _preflight(version)
    version.refresh_from_db()
    return version


def _major_change():
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
    return document


def _outcomes(version):
    return {
        entry["environment_id"]: entry["outcome"]
        for entry in _runtime(version).preflight_report.get("activation") or []
    }


def _set_metadata(environment, **metadata):
    environment.refresh_from_db()
    HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
        payload={
            **environment.payload,
            "metadata": {**environment.payload.get("metadata", {}), **metadata},
        }
    )
    environment.refresh_from_db()
    return environment


def test_a_rollback_after_a_move_by_hand_carries_the_follower_back(agent, user):
    first = _passed(agent)
    following = _following(
        _built_from(_completed(_environment(agent, 1), first), first, source_agent())
    )
    second = _checked(agent, first, "b")
    third = _checked(agent, second, "c")
    HostedHarnessJob.no_workspace_objects.filter(id=following.id).update(
        agent_version=third
    )
    moved_at = (timezone.now() - timedelta(minutes=1)).isoformat()
    _set_metadata(
        following,
        **{HAND_MOVE_KEY: {"at": moved_at, "by": str(user.id), "to": str(third.id)}},
    )
    assert str(following.id) not in (
        _runtime(third).agent_payload.get("requested_at") or {}
    )

    with patch(NUDGE):
        moved = set_production(second)

    following.refresh_from_db()
    assert following.agent_version_id == second.id
    assert [str(one) for one in moved] == [str(following.id)]
    assert _outcomes(second)[str(following.id)] == "activated"


def test_a_request_with_no_time_keeps_the_higher_numbered_default(agent):
    first = _passed(agent)
    second = _checked(agent, first, "b")
    third = _checked(agent, second, "c")
    environment = _completed(_environment(agent, 1), third)
    runtime = _runtime(second)
    assert str(environment.id) not in (runtime.agent_payload.get("requested_at") or {})
    assert str(environment.id) not in (
        _runtime(third).agent_payload.get("requested_at") or {}
    )

    assert _turned_down(environment, second, runtime) == "superseded"


def test_a_follower_pinned_while_production_moves_stays_where_it_is(agent):
    from simulate.services import agent_compatibility

    first = _passed(agent)
    following = _following(
        _built_from(_completed(_environment(agent, 1), first), first, source_agent())
    )
    second = _checked(agent, first, "b")
    real = agent_compatibility.environment_compatibility

    def pinned_meanwhile(environment, version, **kwargs):
        fit = real(environment, version, **kwargs)
        _set_metadata(following, **{POLICY_KEY: POLICY_PINNED})
        return fit

    with (
        patch.object(
            agent_compatibility,
            "environment_compatibility",
            side_effect=pinned_meanwhile,
        ),
        patch(NUDGE) as nudge,
    ):
        moved = set_production(second)

    following.refresh_from_db()
    assert moved == []
    assert following.agent_version_id == first.id
    assert _outcomes(second) == {str(following.id): "pinned"}
    assert (str(following.id), False) in {
        (str(call.args[0]), call.kwargs.get("default_moved"))
        for call in nudge.call_args_list
    }


def test_a_deferred_move_to_a_version_no_longer_production_never_lands(agent):
    from simulate.services.harness_versioning.rebuild import (
        retry_deferred_activations,
    )

    first = _passed(agent)
    following = _following(
        _built_from(_completed(_environment(agent, 1), first), first, source_agent())
    )
    second = _checked(agent, first, "b")
    HostedHarnessJob.no_workspace_objects.filter(id=following.id).update(
        state=HostedHarnessJob.State.RUNNING
    )
    with patch(NUDGE):
        set_production(second)
    assert _outcomes(second) == {str(following.id): "environment_busy"}

    third = _checked(agent, second, "c", document=_major_change())
    with patch(NUDGE):
        set_production(third)
    assert _outcomes(third)[str(following.id)] in {"major_change", "unmeasured"}
    HostedHarnessJob.no_workspace_objects.filter(id=following.id).update(
        state=HostedHarnessJob.State.COMPLETED
    )

    with patch(NUDGE):
        outcomes = retry_deferred_activations(second.id)

    following.refresh_from_db()
    assert outcomes == {str(following.id): "not_moved"}
    assert following.agent_version_id == first.id
    assert _outcomes(second)[str(following.id)] == "superseded"


def test_a_check_that_never_started_fails_retryably_and_retry_starts_it(
    agent, django_capture_on_commit_callbacks
):
    version = _connect(agent)
    assert _runtime(version).preflight_status == Status.PENDING

    with (
        patch(
            "simulate.services.agent_version_steps.workflow_applies_to",
            return_value=True,
        ),
        patch(
            "simulate.temporal.client.start_agent_version_workflow",
            side_effect=RuntimeError("temporal down"),
        ),
    ):
        _schedule_preflight(version.id)

    runtime = _runtime(version)
    assert runtime.preflight_status == Status.FAILED
    failure = check_failure(runtime)
    assert failure["code"] == "check_not_started"
    assert failure["retryable"] is True

    with (
        patch(
            "simulate.services.agent_version_steps.workflow_applies_to",
            return_value=True,
        ),
        patch("simulate.temporal.client.start_agent_version_workflow") as started,
        django_capture_on_commit_callbacks(execute=True),
    ):
        retry_preflight(version)

    started.assert_called_once_with(str(version.id))
    assert _runtime(version).preflight_status == Status.PENDING


def _cancel_raising(error):
    from simulate.temporal import client

    def raising(_version_id):
        raise error

    with (
        patch.object(client, "async_to_sync", return_value=raising),
        capture_logs() as logs,
    ):
        client.cancel_agent_version_workflow("v1")
    return logs


def test_cancelling_a_workflow_that_is_over_is_quiet():
    logs = _cancel_raising(RPCError("not found", RPCStatusCode.NOT_FOUND, b""))

    assert [one["log_level"] for one in logs] == ["info"]
    assert logs[0]["event"] == "agent_version_workflow_not_running"


@pytest.mark.parametrize(
    "error",
    [
        RPCError("unavailable", RPCStatusCode.UNAVAILABLE, b""),
        RuntimeError("no client"),
    ],
)
def test_a_cancel_that_fails_is_warned_with_its_trace_and_not_raised(error):
    logs = _cancel_raising(error)

    assert [one["log_level"] for one in logs] == ["warning"]
    assert logs[0]["event"] == "agent_version_workflow_cancel_failed"
    assert logs[0]["version_id"] == "v1"
    assert logs[0]["exc_info"] is True


def test_the_production_answer_carries_the_versions_check_and_environments(
    auth_client, agent
):
    production = _passed(agent)
    _completed(_environment(agent, 1), production)

    with patch(NUDGE):
        response = auth_client.post(
            f"{AGENTS}{agent.id}/versions/{production.id}/production/"
        )

    assert response.status_code == status.HTTP_200_OK, response.content
    version = response.json()["version"]
    assert version["preflight_status"] == "passed"
    assert version["origin"] == "connect"
    assert version["environments_count"] == 1


def test_the_change_answer_carries_the_new_versions_check(auth_client, agent):
    _passed(agent)

    with patch(
        "simulate.services.harness_agent_changes.github_head",
        return_value=HeadResult("ok", "b" * 40),
    ):
        response = auth_client.post(
            f"{AGENTS}{agent.id}/versions/change/", {"kind": "sync"}, format="json"
        )

    assert response.status_code == status.HTTP_202_ACCEPTED, response.content
    version = response.json()["version"]
    assert version["preflight_status"] == "pending"
    assert version["origin"] == "sync"
    assert version["environments_count"] == 0
