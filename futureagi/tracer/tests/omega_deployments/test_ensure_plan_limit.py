"""The plan's monitor limit applies to the two default alerts as it does in Observe -> Alerts.

Decision (fix round 1 of DS-FA-OBSERVE-LINK): at the limit, ensure still answers 200 with the
project and link, creates no monitor, and says why in ``alertsDeferred``; a later ensure creates
the pair once the plan has room. The defaults are optional (SPEC 2.7: they can be switched off),
and the collector makes ``deploy/<name>`` on the first trace whatever the plan says, so a 402
would only hide the way into Observe.

Defer is the default, provided the user sees why and how to
free a monitor, and the deferral is logged and counted. The message names the numbers and how
many monitors to delete (muted ones count); a plan without monitors says upgrade; a plan that
could not be read says the next ensure retries. Each deferral bumps the link's
``alerts_deferred_count`` and logs ``omega_deployment_observe_alerts_deferred``.

Each test runs as Cloud with a stood-in plan number (``conftest.cloud_plan``); the entitlement
rule itself (``Entitlements.can_create``) and ``check_ee_can_create`` run unchanged.
"""

from unittest.mock import patch

import pytest
from structlog.testing import capture_logs

from tfc.utils.error_codes import get_error_message
from tracer.models.monitor import UserAlertMonitor
from tracer.models.omega_deployment_link import OmegaDeploymentObserveLink
from tracer.models.project import Project
from tracer.tests.omega_deployments.conftest import (
    CloudPlan,
    cloud_plan,
    ensure_url,
    new_deployment_id,
)
from tracer.utils.otel import get_or_create_project

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

ALERTS_URL = "/tracer/user-alerts/"


def _ensure(client, deployment_id, name):
    return client.post(ensure_url(deployment_id), {"name": name}, format="json")


def _org_monitors(organization):
    return UserAlertMonitor.no_workspace_objects.filter(organization=organization)


def _manual_project(user, workspace):
    return get_or_create_project(
        project_name="hand-made-alerts",
        organization_id=str(workspace.organization_id),
        project_type="observe",
        user_id=str(user.id),
        workspace_id=str(workspace.id),
    )


def _alerts_api_create(client, project, name):
    """A monitor made by hand in Observe -> Alerts, through its own API."""
    return client.post(
        ALERTS_URL,
        {
            "name": name,
            "metric_type": "count_of_errors",
            "project": str(project.id),
            "threshold_operator": "greater_than",
            "threshold_type": "static",
            "critical_threshold_value": 1,
            "alert_frequency": 15,
        },
        format="json",
    )


def _hand_made(client, project, count):
    for index in range(count):
        response = _alerts_api_create(client, project, f"hand-made-{index}")
        assert response.status_code == 200, response.data


DEFERRED_EVENT = "omega_deployment_observe_alerts_deferred"


def _link(deployment_id):
    return OmegaDeploymentObserveLink.no_workspace_objects.get(
        ee_deployment_id=deployment_id
    )


def _deferral_logs(logs):
    return [entry for entry in logs if entry["event"] == DEFERRED_EVENT]


def _at_limit(needed, limit, current):
    return {
        "code": "ENTITLEMENT_LIMIT",
        "message": get_error_message("OMEGA_DEPLOYMENT_OBSERVE_ALERTS_AT_LIMIT").format(
            needed=needed,
            limit=limit,
            current=current,
            to_free=current + needed - limit,
        ),
        "needed": needed,
        "limit": limit,
        "currentUsage": current,
        "upgradeCta": CloudPlan.UPGRADE_CTA,
    }


def test_at_the_plan_limit_ensure_links_the_project_and_creates_no_monitor(
    auth_client, user, workspace, organization
):
    project = _manual_project(user, workspace)
    with cloud_plan(3):
        _hand_made(auth_client, project, 3)
        control = _alerts_api_create(auth_client, project, "one-too-many")
        assert control.status_code == 402, control.data

        responses = [
            _ensure(auth_client, new_deployment_id(), f"cap-probe-{index}")
            for index in range(3)
        ]

    assert [response.status_code for response in responses] == [200, 200, 200]
    for index, response in enumerate(responses):
        result = response.data["result"]
        assert result["projectName"] == f"deploy/cap-probe-{index}"
        assert result["alerts"] == []
        assert result["alertsDeferred"] == _at_limit(needed=2, limit=3, current=3)
        assert Project.no_workspace_objects.filter(
            id=result["projectId"], workspace=workspace, trace_type="observe"
        ).exists()
    assert _org_monitors(organization).count() == 3
    links = OmegaDeploymentObserveLink.no_workspace_objects.filter(workspace=workspace)
    assert links.count() == 3
    assert all(link.alerts_created_at is None for link in links)
    assert all(link.errors_monitor_id is None for link in links)
    assert all(link.alerts_deferred_count == 1 for link in links)
    assert all(link.alerts_last_deferred_at is not None for link in links)


def test_after_an_upgrade_the_next_ensure_creates_the_deferred_pair_once(
    auth_client, user, workspace, organization
):
    deployment_id = new_deployment_id()
    project = _manual_project(user, workspace)
    with cloud_plan(3) as plan:
        _hand_made(auth_client, project, 3)
        deferred = _ensure(auth_client, deployment_id, "upgrade-probe")
        assert deferred.data["result"]["alertsDeferred"]["limit"] == 3

        plan.limit = 15
        created = _ensure(auth_client, deployment_id, "upgrade-probe")

        plan.limit = 3
        reads_before = plan.limit_reads
        later = _ensure(auth_client, deployment_id, "upgrade-probe")

    result = created.data["result"]
    assert created.status_code == 200, created.data
    assert result["projectId"] == deferred.data["result"]["projectId"]
    assert [alert["kind"] for alert in result["alerts"]] == [
        "errors_spike",
        "daily_tokens",
    ]
    assert result["alertsDeferred"] is None
    assert _org_monitors(organization).count() == 5
    link = _link(deployment_id)
    assert link.alerts_created_at is not None
    # The deferral stays on record after the pair is created.
    assert link.alerts_deferred_count == 1
    # The pair exists, so a downgrade later neither asks the plan nor hides it.
    assert later.data["result"] == result
    assert plan.limit_reads == reads_before


@pytest.mark.parametrize(
    ("existing", "created"),
    [(0, True), (1, True), (2, False)],
    ids=["room-for-three", "room-for-two", "room-for-one"],
)
def test_room_for_one_monitor_is_not_room_for_the_pair(
    auth_client, user, workspace, organization, existing, created
):
    project = _manual_project(user, workspace)
    with cloud_plan(3):
        _hand_made(auth_client, project, existing)
        response = _ensure(auth_client, new_deployment_id(), "boundary")

    result = response.data["result"]
    assert response.status_code == 200, response.data
    if created:
        assert len(result["alerts"]) == 2
        assert result["alertsDeferred"] is None
        assert _org_monitors(organization).count() == existing + 2
    else:
        assert result["alerts"] == []
        assert result["alertsDeferred"] == _at_limit(
            needed=2, limit=3, current=existing
        )
        assert _org_monitors(organization).count() == existing


def test_monitors_in_another_workspace_of_the_org_count_against_the_plan(
    auth_client, second_workspace_client, user, second_workspace, organization
):
    """The plan's limit is per organization: the second workspace's monitors use it up."""
    project = _manual_project(user, second_workspace)
    with cloud_plan(3):
        _hand_made(second_workspace_client, project, 3)
        response = _ensure(auth_client, new_deployment_id(), "org-wide")

    assert response.status_code == 200, response.data
    deferred = response.data["result"]["alertsDeferred"]
    assert deferred == _at_limit(needed=2, limit=3, current=3)
    # Observe -> Alerts lists one workspace, and none of these three are in the caller's,
    # so the message must send the user to every workspace of the organization.
    assert "in any of your organization's workspaces" in deferred["message"]
    assert _org_monitors(organization).count() == 3


def _unreachable(org_id, resource, current_count):
    raise RuntimeError("entitlement store down")


@pytest.mark.parametrize(
    ("cause", "message_code"),
    [
        ("plan_without_monitors", "OMEGA_DEPLOYMENT_OBSERVE_ALERTS_NOT_ON_PLAN"),
        ("entitlements_down", "OMEGA_DEPLOYMENT_OBSERVE_ALERTS_PLAN_UNAVAILABLE"),
    ],
)
def test_a_plan_without_monitors_says_upgrade_and_an_unreadable_plan_says_it_retries(
    auth_client, organization, cause, message_code
):
    from ee.usage.services.entitlements import Entitlements

    deployment_id = new_deployment_id()
    with cloud_plan(0):
        if cause == "entitlements_down":
            with patch.object(Entitlements, "can_create", staticmethod(_unreachable)):
                response = _ensure(auth_client, deployment_id, "neutral")
        else:
            response = _ensure(auth_client, deployment_id, "neutral")

    assert response.status_code == 200, response.data
    result = response.data["result"]
    assert result["alerts"] == []
    assert result["alertsDeferred"] == {
        "code": "ENTITLEMENT_DENIED",
        "message": get_error_message(message_code),
        "needed": 2,
        "limit": None,
        "currentUsage": 0,
        "upgradeCta": (
            CloudPlan.UPGRADE_CTA if cause == "plan_without_monitors" else None
        ),
    }
    assert not _org_monitors(organization).exists()
    assert _link(deployment_id).alerts_deferred_count == 1


def test_a_plan_smaller_than_the_pair_says_upgrade_never_delete(
    auth_client, organization
):
    """With room for one monitor in all, no deletion fits two: the message says upgrade only."""
    deployment_id = new_deployment_id()
    with cloud_plan(1):
        response = _ensure(auth_client, deployment_id, "too-small")

    assert response.status_code == 200, response.data
    deferred = response.data["result"]["alertsDeferred"]
    expected = get_error_message("OMEGA_DEPLOYMENT_OBSERVE_ALERTS_PLAN_TOO_SMALL")
    assert deferred["message"] == expected.format(needed=2, limit=1)
    assert "delete" not in deferred["message"]
    assert (deferred["code"], deferred["limit"], deferred["currentUsage"]) == (
        "ENTITLEMENT_LIMIT",
        1,
        0,
    )
    assert deferred["upgradeCta"] is not None
    assert not _org_monitors(organization).exists()
    assert _link(deployment_id).alerts_deferred_count == 1


def test_a_muted_monitor_still_counts_and_deleting_one_frees_room_for_the_pair(
    auth_client, user, workspace, organization
):
    """What the at-limit message tells the user to do is what frees room."""
    deployment_id = new_deployment_id()
    project = _manual_project(user, workspace)
    with cloud_plan(4):
        _hand_made(auth_client, project, 3)
        first = _ensure(auth_client, deployment_id, "make-room")
        hand_made = list(_org_monitors(organization).values_list("id", flat=True))

        muted = auth_client.post(
            f"{ALERTS_URL}bulk-mute/",
            {"ids": [str(hand_made[0])], "is_mute": True},
            format="json",
        )
        assert muted.status_code == 200, muted.data
        after_mute = _ensure(auth_client, deployment_id, "make-room")

        deleted = auth_client.delete(
            ALERTS_URL, {"ids": [str(hand_made[0])]}, format="json"
        )
        assert deleted.status_code == 200, deleted.data
        after_delete = _ensure(auth_client, deployment_id, "make-room")

    expected = _at_limit(needed=2, limit=4, current=3)
    assert "delete 1 of your monitors" in expected["message"]
    assert "a muted monitor still counts" in expected["message"]
    assert first.data["result"]["alertsDeferred"] == expected
    assert after_mute.data["result"]["alertsDeferred"] == expected
    created = after_delete.data["result"]
    assert created["alertsDeferred"] is None
    assert [alert["kind"] for alert in created["alerts"]] == [
        "errors_spike",
        "daily_tokens",
    ]
    assert _org_monitors(organization).count() == 4
    link = _link(deployment_id)
    assert link.alerts_created_at is not None
    assert link.alerts_deferred_count == 2


def test_each_deferral_is_counted_on_the_link_and_logged_with_its_numbers(
    auth_client, user, workspace, organization
):
    deployment_id = new_deployment_id()
    project = _manual_project(user, workspace)
    with cloud_plan(2) as plan:
        _hand_made(auth_client, project, 2)
        with capture_logs() as logs:
            responses = [
                _ensure(auth_client, deployment_id, "counted") for _ in range(3)
            ]
        link = _link(deployment_id)
        assert [response.status_code for response in responses] == [200] * 3
        assert link.alerts_deferred_count == 3
        assert link.alerts_last_deferred_at is not None
        last_deferred_at = link.alerts_last_deferred_at

        plan.limit = 15
        with capture_logs() as later_logs:
            created = _ensure(auth_client, deployment_id, "counted")

    events = _deferral_logs(logs)
    assert [event["deferred_count"] for event in events] == [1, 2, 3]
    for event in events:
        assert event["log_level"] == "info"
        assert {
            key: event[key]
            for key in event
            if key not in ("event", "log_level", "deferred_count")
        } == {
            "organization_id": str(organization.id),
            "workspace_id": str(workspace.id),
            "ee_deployment_id": deployment_id,
            "project_id": responses[0].data["result"]["projectId"],
            "code": "ENTITLEMENT_LIMIT",
            "limit": 2,
            "current_usage": 2,
            "needed": 2,
        }
    assert len(created.data["result"]["alerts"]) == 2
    assert _deferral_logs(later_logs) == []
    link.refresh_from_db()
    assert link.alerts_deferred_count == 3
    assert link.alerts_last_deferred_at == last_deferred_at


def test_a_reused_name_adopts_the_pair_at_the_limit_because_it_creates_nothing(
    auth_client, organization
):
    with cloud_plan(15) as plan:
        first = _ensure(auth_client, new_deployment_id(), "reused").data["result"]
        plan.limit = 2
        second = _ensure(auth_client, new_deployment_id(), "reused")

    assert second.status_code == 200, second.data
    assert second.data["result"]["alerts"] == first["alerts"]
    assert second.data["result"]["alertsDeferred"] is None
    assert _org_monitors(organization).count() == 2


def test_self_hosted_never_asks_the_plan(auth_client, organization):
    from ee.usage.services.entitlements import Entitlements

    with patch.object(
        Entitlements,
        "can_create",
        staticmethod(lambda *args: pytest.fail("self-hosted asked the plan")),
    ):
        response = _ensure(auth_client, new_deployment_id(), "self-hosted")

    assert response.status_code == 200, response.data
    assert len(response.data["result"]["alerts"]) == 2
    assert response.data["result"]["alertsDeferred"] is None
    assert _org_monitors(organization).count() == 2
