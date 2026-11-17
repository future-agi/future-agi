"""The section 9.1 cells for what only admins may receive, over FA's real authentication (each person with their own
API key) and, where a delivery is sent, the real worker:

- A Member with ``members_can_manage`` may edit an endpoint, but may not move where a full-mode
  (prompt text) or run-targeting endpoint points, nor one with such deliveries still waiting, nor redeliver a body they
  may not read (sections 8.1 and 9.1).
- The delivery detail shows custom header names only to those who see the endpoint's.
- Turning ``full_payload_allowed`` off moves full-mode endpoints to thin, audited.
"""

from __future__ import annotations

import time

import pytest

from accounts.models.audit_log import AuditLog
from tfc.constants.levels import Level
from webhooks.audit import target_uuid
from webhooks.models import Delivery, Endpoint, Payload, Policy
from webhooks.store import OrmStore
from webhooks.tests.api_support import (
    EP,
    Worker,
    client_for,
    make,
    person,
    pub,
    statuses,
    wait_for,
)


def code_of(r):
    return r.json().get("code")


@pytest.fixture
def people(organization, workspace):
    member = person(
        organization, workspace, Level.MEMBER, Level.WORKSPACE_MEMBER, "mem"
    )
    lead = person(organization, workspace, Level.MEMBER, Level.WORKSPACE_ADMIN, "lead")
    return {
        "member": client_for(member, organization, workspace),
        "lead": client_for(lead, organization, workspace),
    }


@pytest.mark.django_db
def test_m3_a_member_cannot_move_where_admin_only_bodies_go(
    env, owner, organization, workspace, people
):
    Policy.objects.create(
        organization=organization, members_can_manage=True, full_payload_allowed=True
    )
    full, _ = make(owner, "https://hooks.acme.ai/full", payload_mode="full")
    targeting, _ = make(owner, "https://hooks.acme.ai/run", accepts_run_targeting=True)
    thin, _ = make(owner, "https://hooks.acme.ai/thin")
    m, lead = people["member"], people["lead"]
    own = "https://collector.member-own.ai/c"
    for ep_id, body, attr in (
        (full, {"url": own}, "url"),
        (full, {"headers": {"X-To": "me"}}, "headers"),
        (targeting, {"url": own}, "url"),
    ):
        r = m.patch(f"{EP}{ep_id}/", body, format="json")
        assert (r.status_code, code_of(r), r.json().get("attr")) == (
            403,
            "WEBHOOK_FORBIDDEN",
            attr,
        ), (ep_id, body)
    assert Endpoint.objects.get(id=full).url_host == "hooks.acme.ai"
    assert Endpoint.objects.get(id=targeting).url_host == "hooks.acme.ai"
    # Other edits stay theirs (members_can_manage), and so does the URL of a thin endpoint.
    assert (
        m.patch(f"{EP}{full}/", {"description": "x"}, format="json").status_code == 200
    )
    assert m.patch(f"{EP}{thin}/", {"url": own}, format="json").status_code == 200
    # A Workspace Admin may move it.
    r = lead.patch(
        f"{EP}{targeting}/", {"url": "https://hooks.acme.ai/run2"}, format="json"
    )
    assert r.status_code == 200, r.content
    # Downgraded to thin by the member, but with a full-mode delivery still waiting: the URL stays put.
    pub(organization, workspace, "m3-1")
    OrmStore().fan_out(int(time.time() * 1000), 10)
    assert Delivery.objects.get(endpoint_id=full).status == "pending"
    assert (
        m.patch(f"{EP}{full}/", {"payload_mode": "thin"}, format="json").status_code
        == 200
    )
    r = m.patch(f"{EP}{full}/", {"url": own}, format="json")
    assert (r.status_code, code_of(r)) == (403, "WEBHOOK_FORBIDDEN")
    Delivery.objects.filter(endpoint_id=full).update(status="succeeded")
    assert m.patch(f"{EP}{full}/", {"url": own}, format="json").status_code == 200


@pytest.mark.django_db
def test_m3_a_member_cannot_redeliver_a_body_they_may_not_read(
    env, owner, organization, workspace, people
):
    Policy.objects.create(
        organization=organization, members_can_manage=True, full_payload_allowed=True
    )
    full, _ = make(owner, "https://hooks.acme.ai/full", payload_mode="full")
    thin, _ = make(owner, "https://hooks.acme.ai/thin")
    pub(organization, workspace, "m3-2")
    OrmStore().fan_out(int(time.time() * 1000), 10)
    d_full = Delivery.objects.get(endpoint_id=full)
    d_thin = Delivery.objects.get(endpoint_id=thin)
    Delivery.objects.filter(id__in=[d_full.id, d_thin.id]).update(status="failed")
    m, lead = people["member"], people["lead"]
    r = m.post(f"/webhooks/deliveries/{d_full.id}/redeliver/")
    assert (r.status_code, code_of(r)) == (403, "WEBHOOK_FORBIDDEN")
    assert Delivery.objects.get(id=d_full.id).status == "failed"
    assert m.post(f"/webhooks/deliveries/{d_thin.id}/redeliver/").status_code == 202
    assert lead.post(f"/webhooks/deliveries/{d_full.id}/redeliver/").status_code == 202


@pytest.mark.django_db(transaction=True)
def test_s4_custom_header_names_in_the_log_are_for_admins(
    env, owner, organization, workspace, receiver, people
):
    ep_id, _ = make(owner, receiver.url, headers={"X-Team-Tag": "v" * 20})
    pub(organization, workspace, "s4-1")
    with Worker():
        assert wait_for(lambda: statuses(ep_id) == ["succeeded"], 20)
    d_id = Delivery.objects.get(endpoint_id=ep_id).id
    seen = {}
    for who in ("member", "lead"):
        r = people[who].get(f"/webhooks/deliveries/{d_id}/")
        assert r.status_code == 200, r.content
        seen[who] = {
            k.lower(): v for k, v in r.json()["result"]["request_headers"].items()
        }
    assert "x-team-tag" not in seen["member"]
    assert seen["lead"]["x-team-tag"] == "••••vvvv"
    # Future AGI's own headers stay visible to both.
    for who in seen:
        assert {"webhook-id", "webhook-signature", "x-futureagi-delivery"} <= set(
            seen[who]
        )
    assert receiver.seen[0]["headers"]["x-team-tag"] == "v" * 20


@pytest.mark.django_db(transaction=True)
def test_s5_turning_full_payloads_off_moves_full_endpoints_to_thin(
    env, owner, organization, workspace, receiver
):
    Policy.objects.create(organization=organization, full_payload_allowed=True)
    ep_id, _ = make(owner, receiver.url, payload_mode="full")
    r = owner.put("/webhooks/policy/", {"full_payload_allowed": False}, format="json")
    assert r.status_code == 200, r.content
    assert r.json()["result"]["downgraded_endpoint_ids"] == [ep_id]
    assert Endpoint.objects.get(id=ep_id).payload_mode == "thin"
    entry = AuditLog.objects.get(
        scope="webhooks",
        action="webhook.endpoint.updated",
        target_id=target_uuid(ep_id),
        metadata__reason="full_payload_allowed_off",
    )
    assert entry.changes == {"payload_mode": {"before": "full", "after": "thin"}}
    assert entry.actor_id is not None
    pub(organization, workspace, "s5-1")
    with Worker():
        assert wait_for(lambda: statuses(ep_id) == ["succeeded"], 20)
    d = Delivery.objects.get(endpoint_id=ep_id)
    assert Payload.objects.get(id=d.payload_id).payload_mode == "thin"
    assert len(receiver.seen) == 1
    # It cannot be turned back to full while the policy is off.
    r = owner.patch(f"{EP}{ep_id}/", {"payload_mode": "full"}, format="json")
    assert (r.status_code, code_of(r)) == (400, "WEBHOOK_PAYLOAD_FULL_NOT_ALLOWED")
