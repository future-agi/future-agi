"""RC-05 (waitlist) and RC-10 (audit and authority) through the real APIs, and RC-04's FA leg: a
change applies to the next request, because nothing is cached across requests."""

from __future__ import annotations

import pytest
from django.urls import reverse

from accounts.models.audit_log import AuditLog
from omega_rollout import products as p
from omega_rollout import service
from omega_rollout.models import ProductAccess, RolloutAuditEvent
from omega_rollout.tests.conftest import admit, client_for

pytestmark = pytest.mark.django_db

ADMIN_CHANGES = [
    (
        "patch",
        "omega_rollout:admin-product",
        {"product": p.CONSOLE},
        {"stage": "S1", "reason": "r"},
    ),
    ("post", "omega_rollout:admin-kill", {"product": p.CONSOLE}, {"reason": "r"}),
    ("post", "omega_rollout:admin-restore", {"product": p.CONSOLE}, {"reason": "r"}),
    (
        "put",
        "omega_rollout:admin-access",
        {"product": p.CONSOLE},
        {"organization_id": None, "effect": "allow", "reason": "r"},
    ),
    (
        "post",
        "omega_rollout:admin-access-remove",
        {"product": p.CONSOLE},
        {"organization_id": None, "reason": "r"},
    ),
]


def _body(body, tenants):
    return {
        k: (str(tenants.b.organization.pk) if k == "organization_id" else v)
        for k, v in body.items()
    }


class TestAuthority:
    @pytest.mark.parametrize("method,name,kwargs,body", ADMIN_CHANGES)
    def test_non_staff_and_staff_without_the_operate_permission_cannot_change(
        self, tenants, method, name, kwargs, body
    ):
        url = reverse(name, kwargs=kwargs)
        for person in (tenants.a, tenants.reader):
            response = getattr(client_for(person), method)(
                url, _body(body, tenants), format="json"
            )
            assert response.status_code == 403, (person.email, response.data)
        assert RolloutAuditEvent.objects.count() == 0
        assert not ProductAccess.objects.exists()

    def test_staff_reader_can_read_and_a_member_cannot(self, tenants):
        url = reverse("omega_rollout:admin-products")
        assert client_for(tenants.reader).get(url).status_code == 200
        assert client_for(tenants.a).get(url).status_code == 403

    def test_a_change_without_a_reason_is_refused(self, tenants):
        url = reverse("omega_rollout:admin-product", kwargs={"product": p.CONSOLE})
        response = client_for(tenants.operator).patch(
            url, {"stage": "S1"}, format="json"
        )
        assert response.status_code == 400
        assert RolloutAuditEvent.objects.count() == 0


class TestAudit:
    def test_every_change_writes_actor_before_after_and_reason(self, tenants):
        op = client_for(tenants.operator)
        org_b = str(tenants.b.organization.pk)
        calls = [
            (
                "patch",
                reverse("omega_rollout:admin-product", kwargs={"product": p.HUB}),
                {"stage": "S3", "reason": "go to S3"},
                "stage_changed",
            ),
            (
                "patch",
                reverse("omega_rollout:admin-product", kwargs={"product": p.HUB}),
                {"percentage": 10, "reason": "first step"},
                "percentage_changed",
            ),
            (
                "put",
                reverse("omega_rollout:admin-access", kwargs={"product": p.HUB}),
                {"organization_id": org_b, "effect": "allow", "reason": "partner"},
                "allow_set",
            ),
            (
                "put",
                reverse("omega_rollout:admin-access", kwargs={"product": p.HUB}),
                {"organization_id": org_b, "effect": "deny", "reason": "abuse"},
                "deny_set",
            ),
            (
                "post",
                reverse("omega_rollout:admin-kill", kwargs={"product": p.HUB}),
                {"reason": "P0 drill", "pause_installs": True},
                "killed",
            ),
            (
                "post",
                reverse("omega_rollout:admin-restore", kwargs={"product": p.HUB}),
                {"reason": "fixed"},
                "restored",
            ),
        ]
        for method, url, body, action in calls:
            response = getattr(op, method)(url, body, format="json")
            assert response.status_code == 200, response.data
            event = RolloutAuditEvent.objects.order_by("-created_at").first()
            assert event.action == action and event.actor_id == tenants.operator.user.pk
            assert event.reason == body["reason"]
            assert event.before != event.after
            home = AuditLog.objects.filter(
                organization=tenants.operator.organization,
                action=f"omega_rollout.{action}",
                metadata__rollout_event_id=str(event.pk),
            ).get()
            assert home.actor_id == tenants.operator.user.pk
            assert (
                home.changes["before"] == event.before
                and home.changes["after"] == event.after
            )
            assert home.metadata["reason"] == body["reason"]
        # Org-scoped changes are also in the target org's own audit log.
        assert (
            AuditLog.objects.filter(
                organization=tenants.b.organization,
                action__in=["omega_rollout.allow_set", "omega_rollout.deny_set"],
            ).count()
            == 2
        )
        kill = RolloutAuditEvent.objects.get(action="killed")
        assert {"draining_checks", "held_checks", "cancelled", "refused"} <= set(
            kill.counts
        )

    def test_epoch_grows_with_every_change(self, tenants):
        before = service.claim_set(
            tenants.a.organization.pk, tenants.a.workspace.pk
        ).epoch
        admit(tenants, p.CONSOLE)
        after = service.claim_set(
            tenants.a.organization.pk, tenants.a.workspace.pk
        ).epoch
        assert after == before + 3


class TestStageChangesApplyOnTheNextRequest:
    def test_allow_then_remove_admits_then_refuses_immediately(self, tenants):
        op = client_for(tenants.operator)
        org_a = str(tenants.a.organization.pk)
        ws = tenants.a.workspace.pk
        op.patch(
            reverse("omega_rollout:admin-product", kwargs={"product": p.SANDBOXES}),
            {"stage": "S1", "reason": "partners"},
            format="json",
        )
        assert not service.admission(p.SANDBOXES, org_a, ws).admitted
        op.put(
            reverse("omega_rollout:admin-access", kwargs={"product": p.SANDBOXES}),
            {"organization_id": org_a, "effect": "allow", "reason": "partner"},
            format="json",
        )
        assert service.admission(p.SANDBOXES, org_a, ws).admitted
        op.post(
            reverse(
                "omega_rollout:admin-access-remove", kwargs={"product": p.SANDBOXES}
            ),
            {"organization_id": org_a, "reason": "ended"},
            format="json",
        )
        assert not service.admission(p.SANDBOXES, org_a, ws).admitted


class TestWaitlist:
    def test_request_staff_see_invite_admit_and_audit(self, tenants):
        op = client_for(tenants.operator)
        org_b = tenants.b.organization
        early = reverse("omega_rollout:early-access")
        # S0 is internal: not listed and not requestable.
        assert all(
            row["product"] != p.HUB
            for row in client_for(tenants.b).get(early).data["products"]
        )
        assert (
            client_for(tenants.b)
            .post(early, {"product": p.HUB}, format="json")
            .status_code
            == 404
        )
        op.patch(
            reverse("omega_rollout:admin-product", kwargs={"product": p.HUB}),
            {"stage": "S1", "reason": "partners"},
            format="json",
        )
        # A member cannot ask for the org; the owner can, once.
        assert (
            client_for(tenants.member_b)
            .post(early, {"product": p.HUB}, format="json")
            .status_code
            == 403
        )
        asked = client_for(tenants.b).post(
            early, {"product": p.HUB, "note": "we want it"}, format="json"
        )
        assert asked.status_code == 200, asked.data
        row = next(r for r in asked.data["products"] if r["product"] == p.HUB)
        assert row["request_status"] == "requested" and row["admitted"] is False
        listed = op.get(
            reverse("omega_rollout:admin-waitlist"), {"product": p.HUB}
        ).json()["requests"]
        assert [r["organization_id"] for r in listed] == [str(org_b.pk)]
        decision = reverse(
            "omega_rollout:admin-waitlist-decision",
            kwargs={"request_id": listed[0]["id"]},
        )
        # Invitations admit from S2: at S1 the invitation is refused and nothing changes.
        refused = op.post(
            decision, {"invite": True, "reason": "batch 1"}, format="json"
        )
        assert (
            refused.status_code == 409
            and refused.data["code"] == "stage_below_waitlist"
        )
        op.patch(
            reverse("omega_rollout:admin-product", kwargs={"product": p.HUB}),
            {"stage": "S2", "reason": "waitlist"},
            format="json",
        )
        assert (
            op.post(
                decision, {"invite": True, "reason": "batch 1"}, format="json"
            ).status_code
            == 200
        )
        assert service.admission(p.HUB, org_b.pk, tenants.b.workspace.pk).admitted
        row = next(
            r
            for r in client_for(tenants.b).get(early).data["products"]
            if r["product"] == p.HUB
        )
        assert row["admitted"] is True and row["request_status"] == "invited"
        actions = list(
            AuditLog.objects.filter(organization=org_b)
            .order_by("created_at")
            .values_list("action", flat=True)
        )
        assert actions == ["omega_rollout.access_requested", "omega_rollout.invited"]
