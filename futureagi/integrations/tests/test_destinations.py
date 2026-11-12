"""The one destination registry (webhooks DESIGN.md section 11.1): ``integrations.services.destinations`` and
``GET /integrations/destinations/``, over real rows in Postgres and FA's real API-key authentication."""

from __future__ import annotations

import hashlib
from datetime import timedelta

import pytest
from django.utils import timezone

from integrations.models import (
    ConnectionStatus,
    IntegrationConnection,
    IntegrationPlatform,
)
from integrations.services.credentials import CredentialManager
from integrations.services.destinations import (
    HEALTH_STATES,
    list_destinations,
    webhooks_availability,
)
from tfc.constants.levels import Level
from tracer.models.feed_alert import ErrorFeedAlertRule
from webhooks.models import Attempt, Delivery, EndpointState, Event, Payload
from webhooks.tests.api_support import client_for, person, workspace_in
from webhooks.tests.factories import make_endpoint, use_test_keys

URL = "/integrations/destinations/"
KEYS = {"kind", "id", "name", "health", "used_by", "last_used_at"}

pytestmark = pytest.mark.django_db


@pytest.fixture
def keys(monkeypatch):
    use_test_keys(monkeypatch)
    monkeypatch.delenv("FI_WEBHOOKS_ENABLED", raising=False)
    # These tests run as the release configuration: section 18's gate is passed (``webhooks.release_gate``), so the
    # section shows without the section 17 test plan switch; test_the_release_gate_keeps_the_section_absent proves the
    # gate itself with an open row put back.
    monkeypatch.delenv("FI_WEBHOOKS_TEST_PLAN", raising=False)


def _slack(org, ws, user, name="Alerts Slack", status=ConnectionStatus.ACTIVE):
    return IntegrationConnection.no_workspace_objects.create(
        organization=org,
        workspace=ws,
        created_by=user,
        platform=IntegrationPlatform.SLACK,
        display_name=name,
        host_url="https://slack.com",
        encrypted_credentials=CredentialManager.encrypt({"bot_token": "xoxb-test"}),
        external_project_name="",
        status=status,
    )


def _delivery(ep, status, *, created_ago_h=1.0, is_test=False, attempt_at=None):
    now = timezone.now()
    ev = Event.objects.create(
        organization_id=ep.organization_id,
        workspace_id=ep.workspace_id,
        type="prompt.version.created",
        occurred_at=now,
        producer="test",
        source_key=f"k-{Event.objects.count()}",
        source_hash="0" * 64,
        data={},
        is_test=is_test,
    )
    body = b"{}"
    pl = Payload.objects.create(
        organization_id=ep.organization_id,
        event=ev,
        payload_mode="thin",
        body=body,
        body_sha256=hashlib.sha256(body).hexdigest(),
        body_bytes=len(body),
    )
    d = Delivery.objects.create(
        organization_id=ep.organization_id,
        event=ev,
        endpoint=ep,
        payload=pl,
        status=status,
        is_test=is_test,
    )
    Delivery.objects.filter(id=d.id).update(
        created_at=now - timedelta(hours=created_ago_h)
    )
    if attempt_at is not None:
        Attempt.objects.create(
            organization_id=ep.organization_id,
            delivery=d,
            n=1,
            started_at=attempt_at,
            outcome="success" if status == "succeeded" else "http_error",
        )
    return d


def test_one_uniform_list_over_connections_and_endpoints(
    keys, organization, workspace, user
):
    slack = _slack(organization, workspace, user)
    ErrorFeedAlertRule.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        name="New issues to #oncall",
        trigger_type="new_issue",
        slack_connection=slack,
        slack_channel_id="C1",
        slack_channel_name="oncall",
    )
    mine = make_endpoint(
        organization, workspace, name="ci-hook", event_types=("prompt.label.changed",)
    )
    org_wide = make_endpoint(organization, None, name="org-hook")
    other_ws = workspace_in(organization, "Other", user)
    make_endpoint(organization, other_ws, name="elsewhere")
    gone = make_endpoint(organization, workspace, name="deleted-hook")
    gone.deleted = True
    gone.save(update_fields=["deleted"])
    from accounts.models.organization import Organization

    org_b = Organization.objects.create(name="Org B")
    make_endpoint(org_b, None, name="org-b-hook")

    rows = list_destinations(
        organization.id, str(workspace.id), can_view_endpoint=lambda _ws: True
    )

    assert [(r["kind"], r["name"]) for r in rows] == [
        ("slack", "Alerts Slack"),
        ("webhook", "ci-hook"),
        ("webhook", "org-hook"),
    ]
    for r in rows:
        assert KEYS <= set(r)
        assert r["health"]["state"] in HEALTH_STATES
    assert rows[0]["used_by"] == [
        {
            "kind": "error_feed_alert",
            "id": str(ErrorFeedAlertRule.no_workspace_objects.get().id),
            "name": "New issues to #oncall",
        }
    ]
    assert rows[1]["id"] == mine.id and rows[1]["org_wide"] is False
    assert rows[1]["used_by"] == [
        {
            "kind": "event_type",
            "id": "prompt.label.changed",
            "name": "prompt.label.changed",
        }
    ]
    assert rows[2]["id"] == org_wide.id and rows[2]["org_wide"] is True
    # The caller's own rule decides which endpoints appear; the default shows none.
    only_ws = list_destinations(
        organization.id, str(workspace.id), can_view_endpoint=lambda ws: ws is not None
    )
    assert [r["name"] for r in only_ws] == ["Alerts Slack", "ci-hook"]
    assert [
        r["kind"] for r in list_destinations(organization.id, str(workspace.id))
    ] == ["slack"]
    assert [
        r["name"]
        for r in list_destinations(
            organization.id,
            str(workspace.id),
            can_view_endpoint=lambda _w: True,
            kinds=("webhook",),
        )
    ] == ["ci-hook", "org-hook"]


@pytest.mark.parametrize(
    ("status", "reason", "state_fields", "expected"),
    [
        ("enabled", "", {}, ("healthy", None)),
        (
            "enabled",
            "",
            {"failing_since": "now", "last_outcome": "http_error"},
            ("failing", "http_error"),
        ),
        ("enabled", "", {"lane": "slow"}, ("slow", "slow_lane")),
        ("disabled_auto", "gone_410", {}, ("off", "gone_410")),
        ("disabled_auto", "unsafe_address", {}, ("off", "unsafe_address")),
        ("disabled_auto", "failing", {}, ("off", "failing")),
        ("disabled_manual", "creator_removed", {}, ("off", "creator_removed")),
        ("disabled_manual", "manual", {}, ("off", "manual")),
        ("blocked_by_policy", "policy", {}, ("blocked", "policy")),
    ],
)
def test_endpoint_health(
    keys, organization, workspace, user, status, reason, state_fields, expected
):
    ep = make_endpoint(organization, workspace, status=status, disabled_reason=reason)
    if state_fields:
        fields = {
            k: (timezone.now() if v == "now" else v) for k, v in state_fields.items()
        }
        EndpointState.objects.filter(endpoint_id=ep.id).update(**fields)
    (row,) = list_destinations(
        organization.id, str(workspace.id), can_view_endpoint=lambda _w: True
    )
    assert (row["health"]["state"], row["health"]["reason"]) == expected


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (ConnectionStatus.ACTIVE, "healthy"),
        (ConnectionStatus.SYNCING, "healthy"),
        (ConnectionStatus.PAUSED, "off"),
        (ConnectionStatus.ERROR, "failing"),
    ],
)
def test_connection_health(keys, organization, workspace, user, status, expected):
    _slack(organization, workspace, user, status=status)
    (row,) = list_destinations(organization.id, str(workspace.id))
    assert row["health"] == {"state": expected, "reason": status, "since": None}


def test_delivered_24h_and_last_used(keys, organization, workspace, user):
    ep = make_endpoint(organization, workspace, name="stats")
    t1 = timezone.now() - timedelta(minutes=30)
    t2 = timezone.now() - timedelta(minutes=5)
    _delivery(ep, "succeeded", attempt_at=t1)
    _delivery(ep, "succeeded", attempt_at=t2)
    _delivery(ep, "failed", attempt_at=t1)
    _delivery(ep, "retrying")
    _delivery(ep, "succeeded", created_ago_h=30)  # outside the 24 h window
    # Tests are not deliveries of events; an attempt row alone moves nothing.
    _delivery(ep, "succeeded", is_test=True, attempt_at=timezone.now())
    (row,) = list_destinations(
        organization.id, str(workspace.id), can_view_endpoint=lambda _w: True
    )
    assert row["stats_24h"] == {"total": 4, "succeeded": 2, "failed": 1}
    # Last use comes from the dispatcher's state row (the later of the last success and the last
    # failure, tests never counted), not from a scan of every attempt.
    assert row["last_used_at"] is None
    EndpointState.objects.filter(endpoint_id=ep.id).update(
        last_success_at=t1, last_failure_at=t2
    )
    (row,) = list_destinations(
        organization.id, str(workspace.id), can_view_endpoint=lambda _w: True
    )
    assert row["last_used_at"] == t2
    EndpointState.objects.filter(endpoint_id=ep.id).update(
        last_success_at=t2, last_failure_at=None
    )
    (row,) = list_destinations(
        organization.id, str(workspace.id), can_view_endpoint=lambda _w: True
    )
    assert row["last_used_at"] == t2


def test_connections_are_scoped_like_the_connections_api(
    keys, organization, workspace, user
):
    """The registry lists exactly the connections GET /integrations/connections/ lists for the same
    workspace, by the same rule (``_connection_scope``), in the org's default workspace and in another one."""
    assert workspace.is_default
    other = workspace_in(organization, "Other", user)
    _slack(organization, workspace, user, name="Default ws")
    _slack(organization, other, user, name="Other ws")

    for ws, expected in ((workspace, ["Default ws"]), (other, ["Other ws"])):
        assert [
            r["name"] for r in list_destinations(organization.id, str(ws.id))
        ] == expected
        c = client_for(user, organization, ws)
        listed = c.get("/integrations/connections/").json()["result"]["connections"]
        assert [x["display_name"] for x in listed] == expected
        registry = c.get(URL).json()["result"]["destinations"]
        assert [d["name"] for d in registry if d["kind"] == "slack"] == expected


def test_the_route_follows_the_workspace_the_request_names(
    keys, organization, workspace, user
):
    """A workspace's settings page names its workspace in X-Workspace-Id, whatever the session's current
    one is. The registry then lists that workspace's endpoints and says what the caller may do there; a workspace
    the caller cannot open is refused by FA's authentication."""
    other = workspace_in(organization, "Other", user)
    make_endpoint(organization, workspace, name="a-hook")
    make_endpoint(organization, other, name="b-hook")
    make_endpoint(organization, None, name="org-hook")
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.workspace import WorkspaceMembership

    both = person(organization, workspace, Level.MEMBER, Level.WORKSPACE_MEMBER, "both")
    WorkspaceMembership.no_workspace_objects.create(
        user=both,
        workspace=other,
        role=Level.LEVEL_TO_WS_ROLE[Level.WORKSPACE_ADMIN],
        level=Level.WORKSPACE_ADMIN,
        is_active=True,
        organization_membership=OrganizationMembership.no_workspace_objects.get(
            user=both, organization=organization
        ),
    )

    in_a = client_for(both, organization, workspace).get(URL).json()["result"]
    assert in_a["webhooks"]["workspace_id"] == str(workspace.id)
    assert [d["name"] for d in in_a["destinations"]] == ["a-hook", "org-hook"]
    assert (in_a["webhooks"]["can_manage"], in_a["webhooks"]["can_admin"]) == (
        False,
        False,
    )

    in_b = client_for(both, organization, other).get(URL).json()["result"]
    assert in_b["webhooks"]["workspace_id"] == str(other.id)
    assert [d["name"] for d in in_b["destinations"]] == ["b-hook", "org-hook"]
    assert (in_b["webhooks"]["can_manage"], in_b["webhooks"]["can_admin"]) == (
        True,
        True,
    )

    only_a = person(
        organization, workspace, Level.MEMBER, Level.WORKSPACE_MEMBER, "onlya"
    )
    refused = client_for(only_a, organization, other).get(URL)
    assert refused.status_code == 403
    assert "b-hook" not in refused.content.decode()


def test_the_route_for_each_role(keys, organization, workspace, user):
    make_endpoint(organization, workspace, name="ws-hook")
    make_endpoint(organization, None, name="org-hook")
    owner = client_for(user, organization, workspace)
    body = owner.get(URL).json()["result"]
    assert body["webhooks"] == {
        "available": True,
        "reasons": [],
        "can_view": True,
        "can_manage": True,
        "can_admin": True,
        "org_admin": True,
        "workspace_id": str(workspace.id),
    }
    assert [d["name"] for d in body["destinations"]] == ["org-hook", "ws-hook"]

    viewer = person(
        organization, workspace, Level.VIEWER, Level.WORKSPACE_VIEWER, "viewer"
    )
    v = client_for(viewer, organization, workspace).get(URL).json()["result"]
    assert v["destinations"] == []
    assert v["webhooks"]["can_view"] is False and v["webhooks"]["can_manage"] is False

    member = person(
        organization, workspace, Level.MEMBER, Level.WORKSPACE_MEMBER, "member"
    )
    m = client_for(member, organization, workspace).get(URL).json()["result"]
    assert [d["name"] for d in m["destinations"]] == ["org-hook", "ws-hook"]
    assert (
        m["webhooks"]["can_view"],
        m["webhooks"]["can_manage"],
        m["webhooks"]["can_admin"],
        m["webhooks"]["org_admin"],
    ) == (True, False, False, False)

    ws_admin = person(
        organization, workspace, Level.MEMBER, Level.WORKSPACE_ADMIN, "wsadmin"
    )
    a = (
        client_for(ws_admin, organization, workspace)
        .get(URL)
        .json()["result"]["webhooks"]
    )
    assert (a["can_manage"], a["can_admin"], a["org_admin"]) == (True, True, False)

    assert (
        owner.get(URL, {"kind": "webhook"}).json()["result"]["destinations"][0]["kind"]
        == "webhook"
    )
    bad = owner.get(URL, {"nope": "1"})
    assert bad.status_code == 400


def test_the_section_is_absent_where_webhooks_cannot_work(
    monkeypatch, organization, workspace, user
):
    make_endpoint_keys = use_test_keys
    make_endpoint_keys(monkeypatch)
    monkeypatch.delenv("FI_WEBHOOKS_TEST_PLAN", raising=False)
    make_endpoint(organization, workspace, name="ws-hook")
    owner = client_for(user, organization, workspace)

    monkeypatch.setenv("FI_WEBHOOKS_ENABLED", "false")
    r = owner.get(URL).json()["result"]
    assert r["webhooks"]["available"] is False
    assert r["webhooks"]["reasons"] == ["sending_off"]
    assert (
        r["destinations"] == []
    )  # endpoints are not listed while the section is absent
    assert webhooks_availability() == {"available": False, "reasons": ["sending_off"]}

    monkeypatch.delenv("FI_WEBHOOKS_ENABLED")
    monkeypatch.delenv("WEBHOOKS_ENCRYPTION_KEYS")
    assert webhooks_availability() == {"available": False, "reasons": ["keys_missing"]}

    use_test_keys(monkeypatch)
    from webhooks import config

    monkeypatch.setattr(config, "is_hosted", lambda: True)
    monkeypatch.delenv("WEBHOOKS_RESOLVERS", raising=False)
    # Section 18's release gate is passed, so hosted settings are held only by their own gates (sections 6.2 and 6.4).
    assert webhooks_availability() == {
        "available": False,
        "reasons": ["hosted_gates"],
    }


def test_the_release_gate_is_passed_every_gate_row_has_its_receipt(
    keys, monkeypatch, organization, workspace, user
):
    """DESIGN.md section 18 with WT-09's amended text: every gate row of section 17.2, WT-09b included, has passed,
    so ``OPEN_ROWS`` is empty and the release configuration (sending on, the webhooks keys set, NO
    test plan switch) shows the section: the registry says available and lists the endpoint."""
    from webhooks import config, release_gate

    assert release_gate.OPEN_ROWS == ()
    assert {"WT-09", "WT-09b"} <= set(release_gate.SECTION_ROWS)
    assert len(set(release_gate.SECTION_ROWS)) == len(release_gate.SECTION_ROWS)
    make_endpoint(organization, workspace, name="ws-hook")
    owner = client_for(user, organization, workspace)
    monkeypatch.setattr(config, "is_hosted", lambda: False)
    monkeypatch.delenv("FI_WEBHOOKS_TEST_PLAN", raising=False)
    assert release_gate.section_held() == ()
    assert webhooks_availability() == {"available": True, "reasons": []}
    r = owner.get(URL).json()["result"]
    assert (r["webhooks"]["available"], r["webhooks"]["reasons"]) == (True, [])
    assert [d["name"] for d in r["destinations"] if d["kind"] == "webhook"] == [
        "ws-hook"
    ]
    # The test plan switch changes nothing once the gate is passed.
    for value in ("true", "false", ""):
        monkeypatch.setenv("FI_WEBHOOKS_TEST_PLAN", value)
        assert webhooks_availability() == {"available": True, "reasons": []}, value


def test_the_release_gate_keeps_the_section_absent(
    keys, monkeypatch, organization, workspace, user
):
    """DESIGN.md section 18: while a gate row of section 17 has no live receipt, the section is absent
    on every install, a self-hosted one with sending on and WEBHOOKS_ENCRYPTION_KEYS set included: the registry says
    ``release_gate`` and lists no endpoint. Only the section 17 test plan (FI_WEBHOOKS_TEST_PLAN) shows it, and never
    on hosted settings. With no open row the gate is passed. Every gate row has its receipt now, so an open row is put
    back here to keep the gate's own code proven."""
    from webhooks import config, release_gate

    monkeypatch.setattr(release_gate, "OPEN_ROWS", ("WT-09b",))
    assert set(release_gate.OPEN_ROWS) <= set(release_gate.SECTION_ROWS)
    make_endpoint(organization, workspace, name="ws-hook")
    owner = client_for(user, organization, workspace)
    monkeypatch.setattr(config, "is_hosted", lambda: False)

    assert webhooks_availability() == {"available": False, "reasons": ["release_gate"]}
    r = owner.get(URL).json()["result"]
    assert (r["webhooks"]["available"], r["webhooks"]["reasons"]) == (
        False,
        ["release_gate"],
    )
    assert [d for d in r["destinations"] if d["kind"] == "webhook"] == []
    for off in ("false", "0", ""):
        monkeypatch.setenv("FI_WEBHOOKS_TEST_PLAN", off)
        assert webhooks_availability()["reasons"] == ["release_gate"], off

    monkeypatch.setenv("FI_WEBHOOKS_TEST_PLAN", "true")
    assert webhooks_availability() == {"available": True, "reasons": []}
    r = owner.get(URL).json()["result"]
    assert [d["name"] for d in r["destinations"] if d["kind"] == "webhook"] == [
        "ws-hook"
    ]

    # Hosted settings ignore the test plan switch: the gate holds there (with the hosted gates).
    monkeypatch.setattr(config, "is_hosted", lambda: True)
    assert "release_gate" in webhooks_availability()["reasons"]
    assert release_gate.section_held(hosted=True) == ("WT-09b",)
    assert release_gate.section_held(hosted=False) == ()

    # With every row receipted the gate is passed, test plan or not.
    monkeypatch.setattr(config, "is_hosted", lambda: False)
    monkeypatch.delenv("FI_WEBHOOKS_TEST_PLAN")
    monkeypatch.setattr(release_gate, "OPEN_ROWS", ())
    assert webhooks_availability() == {"available": True, "reasons": []}


def test_another_tenant_sees_nothing(keys, organization, workspace, user):
    make_endpoint(organization, workspace, name="a-hook")
    _slack(organization, workspace, user)
    from accounts.models.organization import Organization

    org_b = Organization.objects.create(name="Org B")
    b_owner = person(org_b, None, Level.OWNER, None, "bowner")
    from accounts.models.workspace import Workspace

    ws_b = Workspace.no_workspace_objects.create(
        name="B",
        organization=org_b,
        is_default=True,
        is_active=True,
        created_by=b_owner,
    )
    r = client_for(b_owner, org_b, ws_b).get(URL).json()["result"]
    assert r["destinations"] == []
