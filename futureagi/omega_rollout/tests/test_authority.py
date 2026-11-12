"""RC-10 authority (READINESS 3.13, OD-23): only a named staff operator changes the rollout, from a
browser session, and every change is audited under the operator's own organization.

Regressions for the A2 verifier's findings:
- A1: an org API key pair is not a person. Any member can read the org's `system` key from
  `GET /accounts/keys/`, and that key signs in as the org's first user. With a staff operator as the
  first user, a plain member could kill a product with the key while the audit named the operator.
- A6: a staff operator with no organization could change the rollout and leave no
  `accounts_audit_log` row, because the audit skipped an actor without a home org. `ops` and the
  CLI refuse such an operator with `operator_without_organization`. Over HTTP the membership gate
  in `accounts.authentication` refuses the request first, with 403 `no_active_organization`,
  before any rollout view runs.
"""

from __future__ import annotations

import uuid

import pytest
from django.contrib.auth.models import Permission
from django.core.management import CommandError, call_command
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.authentication import NO_ACTIVE_ORGANIZATION
from accounts.models import OrgApiKey, User
from accounts.models.audit_log import AuditLog
from accounts.models.organization_membership import OrganizationMembership
from omega_hub.tests.helpers import jwt_for, make_person
from omega_rollout import admin_api, audit, ops, service
from omega_rollout import products as p
from omega_rollout.authority import SESSION_REQUIRED, home_organization
from omega_rollout.models import ProductAccess, ProductRollout, RolloutAuditEvent
from omega_rollout.tests.conftest import client_for, key_client
from tfc.constants.levels import Level

pytestmark = pytest.mark.django_db

ADMIN_ROUTES = [
    ("get", "omega_rollout:admin-products", {}, None),
    ("get", "omega_rollout:admin-audit", {}, None),
    ("get", "omega_rollout:admin-waitlist", {}, None),
    ("patch", "omega_rollout:admin-product", {"product": p.CONSOLE}, {"stage": "S1"}),
    ("post", "omega_rollout:admin-kill", {"product": p.CONSOLE}, {}),
    ("post", "omega_rollout:admin-restore", {"product": p.CONSOLE}, {}),
    ("put", "omega_rollout:admin-access", {"product": p.CONSOLE}, {"effect": "allow"}),
    ("post", "omega_rollout:admin-access-remove", {"product": p.CONSOLE}, {}),
]


def system_key_read_by(person):
    """The org's `system` key exactly as any member reads it from the keys page."""
    response = client_for(person).get("/accounts/keys/")
    assert response.status_code == 200, response.content
    data = response.json()["data"]
    return data["api_key"], data["secret_key"]


def orgless_operator():
    user = User.objects.create_user(
        email=f"orgless-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Orgless Operator",
    )
    user.is_staff = True
    user.save(update_fields=["is_staff"])
    user.user_permissions.add(
        Permission.objects.get(
            content_type__app_label="omega_rollout", codename="operate_rollout"
        )
    )
    return User.objects.get(pk=user.pk)  # a fresh instance: no cached permissions


def nothing_changed():
    assert RolloutAuditEvent.objects.count() == 0
    assert not AuditLog.objects.filter(action__startswith="omega_rollout.").exists()
    assert not ProductRollout.objects.filter(paused=True).exists()


class TestApiKeysAreNotPeople:
    def test_a1_a_member_with_the_system_key_cannot_kill_as_the_staff_operator(
        self, tenants
    ):
        # Org S: its first user is the staff operator; M is a plain member who joined later.
        member = make_person(
            "member-s",
            org_level=Level.MEMBER,
            organization=tenants.operator.organization,
        )
        kill = reverse("omega_rollout:admin-kill", kwargs={"product": p.CONSOLE})
        assert (
            client_for(member).post(kill, {"reason": "x"}, format="json").status_code
            == 403
        )
        api_key, secret_key = system_key_read_by(member)
        response = key_client(api_key, secret_key).post(
            kill, {"reason": "x"}, format="json"
        )
        assert response.status_code == 403, response.content
        assert response.json()["code"] == SESSION_REQUIRED
        nothing_changed()

    @pytest.mark.parametrize("method,name,kwargs,body", ADMIN_ROUTES)
    def test_every_admin_route_refuses_a_key_even_the_operators_own(
        self, tenants, method, name, kwargs, body
    ):
        url = reverse(name, kwargs=kwargs)
        own_key = OrgApiKey.objects.create(
            organization=tenants.operator.organization,
            type="user",
            user=tenants.operator.user,
            workspace=tenants.operator.workspace,
        )
        payload = {
            **(body or {}),
            "reason": "r",
            "organization_id": str(tenants.b.organization.pk),
        }
        for client in (
            key_client(*system_key_read_by(tenants.operator)),
            key_client(own_key.api_key, own_key.secret_key),
        ):
            response = getattr(client, method)(
                url, payload if body is not None else None, format="json"
            )
            assert response.status_code == 403, (name, response.content)
            assert response.json()["code"] == SESSION_REQUIRED
        nothing_changed()
        # The same operator's browser session still reads.
        assert (
            client_for(tenants.operator)
            .get(reverse("omega_rollout:admin-products"))
            .status_code
            == 200
        )

    def test_early_access_refuses_a_key_so_a_member_cannot_ask_as_the_owner(
        self, tenants
    ):
        ops.set_stage(p.HUB, p.S2, actor=tenants.operator.user, reason="waitlist")
        early = reverse("omega_rollout:early-access")
        assert (
            client_for(tenants.member_b)
            .post(early, {"product": p.HUB}, format="json")
            .status_code
            == 403
        )
        keys = key_client(
            *system_key_read_by(tenants.member_b)
        )  # signs in as B's owner
        for response in (
            keys.post(early, {"product": p.HUB}, format="json"),
            keys.get(early),
        ):
            assert response.status_code == 403, response.content
            assert response.json()["code"] == SESSION_REQUIRED
        assert not service.admission(
            p.HUB, tenants.b.organization.pk, tenants.b.workspace.pk
        ).admitted
        from omega_rollout.models import WaitlistRequest

        assert not WaitlistRequest.objects.exists()
        # The owner's own session still asks.
        assert (
            client_for(tenants.b)
            .post(early, {"product": p.HUB}, format="json")
            .status_code
            == 200
        )


class TestEveryChangeHasAnAuditableOperator:
    def test_a6_an_operator_with_no_organization_changes_nothing(self, tenants):
        orgless = orgless_operator()
        assert home_organization(orgless) is None
        for change in (
            lambda: ops.kill(p.SANDBOXES, actor=orgless, reason="x"),
            lambda: ops.set_stage(p.SANDBOXES, p.S1, actor=orgless, reason="x"),
            lambda: ops.set_access(
                p.SANDBOXES,
                tenants.a.organization,
                effect=p.EFFECT_ALLOW,
                actor=orgless,
                reason="x",
            ),
        ):
            with pytest.raises(ops.RolloutChangeRefused) as refused:
                change()
            assert refused.value.code == "operator_without_organization"
        nothing_changed()
        assert not ProductAccess.objects.exists()

    def test_a6_through_the_api_and_the_cli(self, tenants):
        orgless = orgless_operator()
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {jwt_for(orgless)}")
        response = client.post(
            reverse("omega_rollout:admin-kill", kwargs={"product": p.SANDBOXES}),
            {"reason": "x"},
            format="json",
        )
        # The membership gate refuses an account with no active organization
        # before the view (and so before `require_operator`) runs.
        assert response.status_code == 403, response.content
        assert response.json()["code"] == NO_ACTIVE_ORGANIZATION
        # The CLI does not authenticate a request, so `require_operator` refuses.
        with pytest.raises(CommandError, match="operator_without_organization"):
            call_command(
                "omega_rollout",
                "kill",
                p.SANDBOXES,
                "--actor",
                orgless.email,
                "--reason",
                "x",
            )
        nothing_changed()

    @pytest.mark.parametrize("method,name,kwargs,body", ADMIN_ROUTES)
    def test_a6_every_admin_route_refuses_an_orgless_operator_at_the_gate(
        self, tenants, method, name, kwargs, body
    ):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {jwt_for(orgless_operator())}")
        payload = {
            **(body or {}),
            "reason": "r",
            "organization_id": str(tenants.a.organization.pk),
        }
        response = getattr(client, method)(
            reverse(name, kwargs=kwargs),
            payload if body is not None else None,
            format="json",
        )
        assert response.status_code == 403, (name, response.content)
        assert response.json()["code"] == NO_ACTIVE_ORGANIZATION
        nothing_changed()
        assert not ProductAccess.objects.exists()

    def test_no_admin_view_serves_an_account_without_an_organization(self):
        """The gate applies only while no rollout view opts out of it."""
        views = [
            value
            for value in vars(admin_api).values()
            if isinstance(value, type) and issubclass(value, admin_api.StaffView)
        ]
        assert admin_api.KillView in views
        opted_out = [
            view.__name__
            for view in views
            if getattr(view, "allows_no_organization", False)
        ]
        assert opted_out == []

    def test_a_removed_operator_is_not_an_operator_of_their_old_org(self, tenants):
        """Only ACTIVE memberships count: a deactivated one (what removing a member does) leaves the
        operator with no org to audit under, whatever the legacy `user.organization` key says."""
        OrganizationMembership.no_workspace_objects.filter(
            user=tenants.operator.user
        ).update(is_active=False)
        operator = User.objects.get(pk=tenants.operator.user.pk)
        assert operator.organization_id == tenants.operator.organization.pk
        with pytest.raises(ops.RolloutChangeRefused) as refused:
            ops.kill(p.CONSOLE, actor=operator, reason="x")
        assert refused.value.code == "operator_without_organization"
        nothing_changed()

    def test_no_actor_is_refused_and_only_a_seed_writes_without_one(self, tenants):
        with pytest.raises(ops.RolloutChangeRefused) as refused:
            ops.kill(p.CONSOLE, actor=None, reason="x")
        assert refused.value.code == "operator_required"
        with pytest.raises(ops.RolloutChangeRefused) as refused:
            ops.kill(p.CONSOLE, actor=tenants.a.user, reason="x")  # not staff
        assert refused.value.code == "not_an_operator"
        nothing_changed()
        ops.set_access(
            p.CONSOLE,
            tenants.a.organization,
            effect=p.EFFECT_ALLOW,
            cohort=p.COHORT_STAFF,
            actor=ops.SEED,
            reason="local stack seed",
        )
        event = RolloutAuditEvent.objects.get()
        assert event.actor_id is None and event.action == "allow_set"
        # The seed's row is in the target org's audit log, with no actor.
        row = AuditLog.objects.get(
            organization=tenants.a.organization, action="omega_rollout.allow_set"
        )
        assert row.actor_id is None

    def test_a_seed_never_decides_a_waitlist_request(self, tenants):
        ops.set_stage(p.HUB, p.S2, actor=tenants.operator.user, reason="waitlist")
        request = ops.request_access(
            p.HUB, tenants.b.organization, user=tenants.b.user, note=""
        )
        for actor in (ops.SEED, None):
            with pytest.raises(ops.RolloutChangeRefused) as refused:
                ops.decide_request(request, invite=True, actor=actor, reason="x")
            assert refused.value.code == "operator_required"
        assert not service.admission(
            p.HUB, tenants.b.organization.pk, tenants.b.workspace.pk
        ).admitted

    def test_the_audit_refuses_a_person_with_no_organization(self, tenants):
        with pytest.raises(ValueError, match="organization"):
            audit.record(
                action="killed",
                product=p.CONSOLE,
                actor=orgless_operator(),
                before={"paused": False},
                after={"paused": True},
                reason="x",
                epoch=1,
            )
        assert RolloutAuditEvent.objects.count() == 0
