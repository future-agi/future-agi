"""Operator grants are narrow, superuser-approved, and atomic with their audit."""

from __future__ import annotations

import json
import re
from io import StringIO
from unittest.mock import patch

import pytest
from django.contrib.auth.models import Group, Permission
from django.core.management import CommandError, call_command

from accounts.models import User
from accounts.models.audit_log import AuditLog, AuditScope
from accounts.models.organization_membership import OrganizationMembership
from omega_rollout import audit
from omega_rollout import products as p
from omega_rollout.authority import (
    RolloutChangeRefused,
    home_organization,
    require_operator,
)
from omega_rollout.models import ProductRollout, RolloutAuditEvent, RolloutEpoch

pytestmark = pytest.mark.django_db


@pytest.fixture
def approver():
    user = User.objects.create_superuser(
        email="approver@example.com", name="Approver"
    )
    assert home_organization(user) is None
    return user


@pytest.fixture
def operate_permission():
    return Permission.objects.get(
        content_type__app_label="omega_rollout", codename="operate_rollout"
    )


def run_command(*args):
    out, err = StringIO(), StringIO()
    call_command("omega_rollout", *args, stdout=out, stderr=err)
    return out.getvalue(), err.getvalue()


def operator_args(action, person, approver, reason="approved for rollout duty"):
    return [
        action,
        "--email", person.email,
        "--by", approver.email,
        "--reason", reason,
    ]


def user_state(user):
    fresh = User.objects.get(pk=user.pk)
    return {
        "fields": User.objects.filter(pk=user.pk).values().get(),
        "permissions": set(fresh.user_permissions.values_list("pk", flat=True)),
        "groups": set(fresh.groups.values_list("pk", flat=True)),
        "memberships": list(
            OrganizationMembership.no_workspace_objects.filter(user=fresh)
            .order_by("pk")
            .values()
        ),
    }


def operator_change_state():
    return {
        "users": [user_state(user) for user in User.objects.order_by("pk")],
        "permissions": list(Permission.objects.order_by("pk").values()),
        "events": list(RolloutAuditEvent.objects.order_by("pk").values()),
        "logs": list(AuditLog.objects.order_by("pk").values()),
    }


def make_operator(person, permission):
    person.is_staff = True
    person.save(update_fields=["is_staff"])
    person.user_permissions.add(permission)


def test_grant_makes_person_an_operator_without_an_approver_organization(
    tenants, approver, operate_permission
):
    person = tenants.a.user
    before = user_state(person)

    out, err = run_command(*operator_args("grant-operator", person, approver))

    fresh = User.objects.get(pk=person.pk)
    assert require_operator(fresh) == fresh
    expected = before
    expected["fields"]["is_staff"] = True
    expected["permissions"].add(operate_permission.pk)
    assert user_state(person) == expected
    assert out == f"grant-operator {person.email}: operator now = yes\n"
    assert err == ""
    assert not ProductRollout.objects.exists()
    assert not RolloutEpoch.objects.exists()


def test_second_grant_is_unchanged_but_is_audited(tenants, approver):
    person = tenants.a.user
    args = operator_args("grant-operator", person, approver)
    run_command(*args)
    before = user_state(person)

    out, err = run_command(*args)

    assert user_state(person) == before
    assert out == f"grant-operator {person.email}: operator now = yes (unchanged)\n"
    assert err == ""
    assert RolloutAuditEvent.objects.count() == 2
    assert AuditLog.objects.filter(action="omega_rollout.operator.granted").count() == 2
    event = RolloutAuditEvent.objects.first()
    assert event.before == event.after


def test_revoke_removes_only_the_grant_and_leaves_staff(
    tenants, approver, operate_permission
):
    person = tenants.a.user
    make_operator(person, operate_permission)
    before = user_state(person)

    out, err = run_command(*operator_args("revoke-operator", person, approver))

    expected = before
    expected["permissions"].remove(operate_permission.pk)
    assert user_state(person) == expected
    fresh = User.objects.get(pk=person.pk)
    with pytest.raises(RolloutChangeRefused, match="Only active Future AGI staff"):
        require_operator(fresh)
    assert fresh.is_staff is True
    assert out == (
        f"revoke-operator {person.email}: operator now = no (staff status kept)\n"
    )
    assert err == ""


@pytest.mark.parametrize("source", ["none", "group", "superuser"])
def test_revoke_without_a_direct_grant_leaves_all_user_state_unchanged(
    tenants, approver, operate_permission, source
):
    person = tenants.a.user
    if source != "none":
        person.is_staff = True
        person.save(update_fields=["is_staff"])
    if source == "group":
        group = Group.objects.create(name="existing operators without direct grants")
        group.permissions.add(operate_permission)
        person.groups.add(group)
    elif source == "superuser":
        person.is_superuser = True
        person.save(update_fields=["is_superuser"])
    assert not person.user_permissions.filter(pk=operate_permission.pk).exists()
    before = user_state(person)

    out, err = run_command(*operator_args("revoke-operator", person, approver))

    assert user_state(person) == before
    operator_now = "no" if source == "none" else "yes"
    assert out == (
        f"revoke-operator {person.email}: operator now = {operator_now} (nothing to revoke)\n"
    )
    assert err == ""
    event = RolloutAuditEvent.objects.get()
    assert event.action == "operator.revoked"
    assert event.before == event.after
    log = AuditLog.objects.get(action="omega_rollout.operator.revoked")
    assert log.changes == {"product": "", "before": event.before, "after": event.after}


@pytest.mark.parametrize("action", ["grant-operator", "revoke-operator"])
def test_other_fields_permissions_groups_and_memberships_are_preserved(
    tenants, approver, operate_permission, action
):
    person = tenants.a.user
    unrelated = Permission.objects.get(
        content_type__app_label="accounts", codename="view_user"
    )
    group = Group.objects.create(name="unrelated access")
    group.permissions.add(unrelated)
    person.groups.add(group)
    person.user_permissions.add(unrelated)
    person.invited_by = tenants.operator.user
    person.config = {"keep": "unchanged"}
    person.save(update_fields=["invited_by", "config"])
    if action == "revoke-operator":
        make_operator(person, operate_permission)
    before = user_state(person)
    approver_before = user_state(approver)

    run_command(*operator_args(action, person, approver))

    expected = before
    if action == "grant-operator":
        expected["fields"]["is_staff"] = True
        expected["permissions"].add(operate_permission.pk)
    else:
        expected["permissions"].remove(operate_permission.pk)
    assert user_state(person) == expected
    assert user_state(approver) == approver_before


@pytest.mark.parametrize("action", ["grant-operator", "revoke-operator"])
@pytest.mark.parametrize("other_active", [True, False], ids=["active", "inactive"])
def test_case_insensitive_person_collision_is_refused(
    tenants, approver, operate_permission, action, other_active
):
    person, other = tenants.a.user, tenants.b.user
    person.email = "member@example.com"
    person.save(update_fields=["email"])
    other.email = "Member@example.com"
    other.is_active = other_active
    other.save(update_fields=["email", "is_active"])
    assert person.is_active is True
    assert home_organization(person) is not None
    if other_active:
        assert home_organization(other) is not None
    if action == "revoke-operator":
        make_operator(person, operate_permission)
        make_operator(other, operate_permission)
    before = operator_change_state()
    message = (
        "More than one account matches that address when letter case is ignored; "
        "nothing was changed."
    )

    with pytest.raises(CommandError, match=f"^{re.escape(message)}$"):
        run_command(*operator_args(action, person, approver))

    assert operator_change_state() == before


@pytest.mark.parametrize("action", ["grant-operator", "revoke-operator"])
@pytest.mark.parametrize("other_active", [True, False], ids=["active", "inactive"])
def test_case_insensitive_approver_collision_is_refused(
    tenants, approver, operate_permission, action, other_active
):
    person, ordinary = tenants.a.user, tenants.b.user
    ordinary.email = approver.email.upper()
    ordinary.is_active = other_active
    ordinary.save(update_fields=["email", "is_active"])
    assert ordinary.is_superuser is False
    if action == "revoke-operator":
        make_operator(person, operate_permission)
    args = operator_args(action, person, approver)
    args[4] = ordinary.email
    before = operator_change_state()
    message = (
        "More than one account matches that address when letter case is ignored; "
        "nothing was changed."
    )

    with pytest.raises(CommandError, match=f"^{re.escape(message)}$"):
        run_command(*args)

    assert operator_change_state() == before


REFUSALS = [
    ("missing-person", "--email must name an existing active person."),
    ("inactive-person", "--email must name an existing active person."),
    (
        "no-active-organization",
        "The person has no active organization, so the change could not be audited.",
    ),
    ("missing-by", "--by must name an existing active superuser."),
    ("inactive-by", "--by must name an existing active superuser."),
    ("non-superuser-by", "--by must name an existing active superuser."),
    ("same-person", "--by and --email must name different people."),
    ("missing-reason", "Every rollout change needs a reason."),
    ("blank-reason", "Every rollout change needs a reason."),
    ("missing-email-option", "--email must name an existing active person."),
    ("missing-by-option", "--by must name an existing active superuser."),
]


@pytest.mark.parametrize("action", ["grant-operator", "revoke-operator"])
@pytest.mark.parametrize("case,message", REFUSALS, ids=[case for case, _ in REFUSALS])
def test_refusal_makes_no_change(
    tenants, approver, operate_permission, action, case, message
):
    person = tenants.a.user
    if action == "revoke-operator":
        make_operator(person, operate_permission)
    args = operator_args(action, person, approver)
    if case == "missing-person":
        args[2] = "missing@example.com"
    elif case == "inactive-person":
        person.is_active = False
        person.save(update_fields=["is_active"])
    elif case == "no-active-organization":
        OrganizationMembership.no_workspace_objects.filter(user=person).update(
            is_active=False
        )
        assert person.organization_id is not None
        assert home_organization(person) is None
    elif case == "missing-by":
        args[4] = "missing@example.com"
    elif case in ("inactive-by", "non-superuser-by"):
        field = "is_active" if case == "inactive-by" else "is_superuser"
        setattr(approver, field, False)
        approver.save(update_fields=[field])
    elif case == "same-person":
        person.is_superuser = True
        person.save(update_fields=["is_superuser"])
        args[4] = person.email.upper()
    elif case == "missing-reason":
        args = args[:-2]
    elif case == "blank-reason":
        args[-1] = " \t\n "
    elif case == "missing-email-option":
        del args[1:3]
    elif case == "missing-by-option":
        del args[3:5]
    before = user_state(person)
    approver_before = user_state(approver)
    event_count = RolloutAuditEvent.objects.count()
    log_count = AuditLog.objects.count()

    with pytest.raises(CommandError, match=re.escape(message)):
        run_command(*args)

    assert user_state(person) == before
    assert user_state(approver) == approver_before
    assert RolloutAuditEvent.objects.count() == event_count
    assert AuditLog.objects.count() == log_count


@pytest.mark.parametrize("action", ["grant-operator", "revoke-operator"])
def test_audit_names_person_approver_reason_and_person_organization(
    tenants, approver, operate_permission, action
):
    person = tenants.a.user
    granting = action == "grant-operator"
    if not granting:
        make_operator(person, operate_permission)
    reason = "approved for rollout duty"

    run_command(*operator_args(action, person, approver, f"  {reason}  "))

    event = RolloutAuditEvent.objects.get()
    expected_action = "operator.granted" if granting else "operator.revoked"
    assert event.action == expected_action
    assert event.actor_id == approver.pk
    assert event.organization_id == tenants.a.organization.pk
    assert event.workspace_id is None
    assert event.product == ""
    assert event.epoch == 0
    assert event.counts == {}
    assert event.reason == reason
    identity = {"user_id": str(person.pk), "email": person.email}
    assert event.before == {
        **identity,
        "is_staff": not granting,
        "operate_rollout": not granting,
        "operator": not granting,
    }
    assert event.after == {
        **identity,
        "is_staff": True,
        "operate_rollout": granting,
        "operator": granting,
    }
    log = AuditLog.objects.get(action=f"omega_rollout.{expected_action}")
    assert log.actor_id == approver.pk
    assert log.organization_id == tenants.a.organization.pk
    assert log.scope == AuditScope.ORGANIZATION
    assert log.target_id == tenants.a.organization.pk
    assert log.changes == {
        "product": "", "before": event.before, "after": event.after
    }
    assert log.metadata == {
        "reason": reason, "rollout_event_id": str(event.pk), "epoch": 0
    }


def test_audit_uses_the_person_organization_not_the_approver_home(tenants):
    approver = tenants.b.user
    approver.is_superuser = True
    approver.save(update_fields=["is_superuser"])
    assert home_organization(approver) == tenants.b.organization

    run_command(*operator_args("grant-operator", tenants.a.user, approver))

    logs = AuditLog.objects.filter(action="omega_rollout.operator.granted")
    assert list(logs.values_list("organization_id", flat=True)) == [
        tenants.a.organization.pk
    ]
    assert RolloutAuditEvent.objects.get().actor_id == approver.pk


@pytest.mark.parametrize("action", ["grant-operator", "revoke-operator"])
def test_audit_failure_rolls_back_the_entire_change(
    tenants, approver, operate_permission, action
):
    person = tenants.a.user
    if action == "revoke-operator":
        make_operator(person, operate_permission)
    before = user_state(person)
    event_count = RolloutAuditEvent.objects.count()
    log_count = AuditLog.objects.count()
    record = audit.record

    def fail_after_record(**kwargs):
        record(**kwargs)
        raise RuntimeError("audit unavailable")

    with patch.object(audit, "record", side_effect=fail_after_record) as patched:
        with pytest.raises(RuntimeError, match="audit unavailable"):
            run_command(*operator_args(action, person, approver))
        patched.assert_called_once()

    assert user_state(person) == before
    assert RolloutAuditEvent.objects.count() == event_count
    assert AuditLog.objects.count() == log_count


def test_grant_uses_case_insensitive_emails_and_existing_reason_limit(
    tenants, approver
):
    args = operator_args("grant-operator", tenants.a.user, approver, "  " + "r" * 2100)
    args[2] = args[2].upper()
    args[4] = args[4].upper()

    out, err = run_command(*args)

    assert out == f"grant-operator {tenants.a.email}: operator now = yes\n"
    assert err == ""
    assert RolloutAuditEvent.objects.get().reason == "r" * 2000


@pytest.mark.parametrize("source", ["group", "superuser"])
def test_revoke_reports_remaining_effective_authority_without_removing_it(
    tenants, approver, operate_permission, source
):
    person = tenants.a.user
    make_operator(person, operate_permission)
    if source == "group":
        group = Group.objects.create(name="existing operators")
        group.permissions.add(operate_permission)
        person.groups.add(group)
    else:
        person.is_superuser = True
        person.save(update_fields=["is_superuser"])
    before = user_state(person)

    out, err = run_command(*operator_args("revoke-operator", person, approver))

    before["permissions"].remove(operate_permission.pk)
    assert user_state(person) == before
    fresh = User.objects.get(pk=person.pk)
    assert require_operator(fresh) == fresh
    assert out == (
        f"revoke-operator {person.email}: operator now = yes "
        "(direct grant removed; still an operator through a group or superuser status; "
        "staff status kept)\n"
    )
    assert err == ""
    event = RolloutAuditEvent.objects.get()
    assert event.after["operate_rollout"] is False
    assert event.after["operator"] is True


@pytest.mark.parametrize("action", ["grant-operator", "revoke-operator"])
def test_operator_actions_refuse_product_arguments(tenants, approver, action):
    person = tenants.a.user
    before = user_state(person)
    args = operator_args(action, person, approver)
    args.insert(1, p.CONSOLE)

    with pytest.raises(
        CommandError, match="No product or value is accepted for operator changes"
    ):
        run_command(*args)

    assert user_state(person) == before
    assert not RolloutAuditEvent.objects.exists()


def test_existing_status_and_stage_argument_handling_is_unchanged(tenants):
    out, err = run_command("status")
    initial = json.loads(out)
    assert err == ""
    assert set(initial["products"]) == set(p.HOSTED_PRODUCTS)
    assert not RolloutAuditEvent.objects.exists()

    with pytest.raises(CommandError, match="product must be one of"):
        run_command("stage", "--actor", tenants.operator.email, "--reason", "test")
    with pytest.raises(CommandError, match="--actor"):
        run_command("stage", p.CONSOLE, "S1", "--reason", "test")

    out, err = run_command(
        "stage", p.CONSOLE, "S1", "--actor", tenants.operator.email,
        "--reason", "existing rollout action",
    )
    assert json.loads(out)["products"][p.CONSOLE]["stage"] == p.S1
    assert err == ""
    event = RolloutAuditEvent.objects.get()
    assert event.action == "stage_changed"
    assert event.actor_id == tenants.operator.user.pk


def test_unknown_audit_actions_are_still_refused(tenants):
    with pytest.raises(ValueError, match="undeclared rollout audit action"):
        audit.record(
            action="operator.unknown", product="", actor=tenants.operator.user,
            before={}, after={}, reason="test", epoch=0,
        )
    assert not RolloutAuditEvent.objects.exists()
