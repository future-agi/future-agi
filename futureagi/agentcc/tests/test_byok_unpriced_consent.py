"""BYOK unknown-price consent is role-bound and audited atomically with config."""

from unittest.mock import MagicMock, patch

import pytest
from rest_framework.exceptions import PermissionDenied

from accounts.models.audit_log import AuditLog, AuditScope
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.permissions import GATEWAY_ADMIN_MESSAGE
from agentcc.services.org_config_writer import (
    REPLACE_SECTIONS,
    activate_config_version,
    write_config_version,
    write_config_version_with,
)
from tfc.constants.levels import Level
from tfc.utils import audit

pytestmark = pytest.mark.integration
ACTION = "agentcc.byok_unpriced_under_cap_changed"
FIELD = "unpriced_under_cap"


def routing(value, location="primary", alias=FIELD):
    result = {"agents": {alias: value}} if value is not None else {}
    if location == "rollout":
        return {"rollout": {
            "version": "consent-test", "percent": 50,
            "sticky_by": "request", "routing": result,
        }}
    return result


def key(location):
    return FIELD if location == "primary" else "rollout_unpriced_under_cap"


def stored(org, value, *, version=1, active=True, location="primary"):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=org, version=version, is_active=active,
        routing=routing(value, location),
    )


def snapshot(org):
    return list(AgentccOrgConfig.no_workspace_objects.filter(organization=org)
                .order_by("version").values_list("pk", "version", "is_active", "routing"))


def rows(org):
    return AuditLog.objects.filter(organization=org, action=ACTION)


def write(org, user, value, *, location="primary", alias=FIELD, push=None):
    return write_config_version(
        org, {"routing": routing(value, location, alias)},
        mode=REPLACE_SECTIONS, user=user,
        push=push if push is not None else lambda config: True,
    )[0]


def assert_row(row, user, config, changes):
    assert row.actor_id == user.pk
    assert row.organization_id == config.organization_id
    assert row.created_at is not None
    assert str(row.target_id) == str(config.organization_id)
    assert row.scope == AuditScope.ORGANIZATION
    assert row.changes == changes
    assert row.metadata == {"config_version": config.version}


@pytest.mark.parametrize("level", [Level.OWNER, Level.ADMIN])
@pytest.mark.parametrize("location", ["primary", "rollout"])
@pytest.mark.parametrize("alias", [FIELD, "unpricedUnderCap"])
def test_allow_create_records_actor_time_org_version(user, level, location, alias):
    with patch.object(type(user), "get_membership_level", return_value=level):
        config = write(user.organization, user, "allow", location=location, alias=alias)
    assert_row(rows(user.organization).get(), user, config,
               {key(location): {"old": None, "new": "allow"}})


@pytest.mark.parametrize("location", ["primary", "rollout"])
@pytest.mark.parametrize("old,new", [
    (None, "reject"), (None, "allow"), ("reject", None),
    ("reject", "allow"), ("allow", None), ("allow", "reject"),
])
def test_every_setting_transition_is_audited(user, location, old, new):
    stored(user.organization, old, location=location)
    with patch.object(type(user), "get_membership_level", return_value=Level.OWNER):
        config = write(user.organization, user, new, location=location)
    assert_row(rows(user.organization).get(), user, config,
               {key(location): {"old": old, "new": new}})


@pytest.mark.parametrize("location", ["primary", "rollout"])
@pytest.mark.parametrize("value", [None, "reject", "allow"])
def test_unchanged_value_does_not_add_audit_row(user, location, value):
    stored(user.organization, value, location=location)
    with patch.object(type(user), "get_membership_level", return_value=Level.OWNER):
        config = write(user.organization, user, value, location=location)
        activate_config_version(config, user=user, push=lambda config: True)
    assert not rows(user.organization).exists()


@pytest.mark.parametrize("location", ["primary", "rollout"])
def test_alias_only_change_does_not_add_audit_row(user, location):
    stored(user.organization, "allow", location=location)
    with patch.object(type(user), "get_membership_level", return_value=Level.OWNER):
        write(user.organization, user, "allow", location=location, alias="unpricedUnderCap")
    assert not rows(user.organization).exists()


@pytest.mark.parametrize("location", ["primary", "rollout"])
@pytest.mark.parametrize("operation", ["create", "activate"])
@pytest.mark.parametrize("failure", ["missing", "storage", "muted"])
def test_audit_failure_rolls_back_config_and_never_pushes(user, location, operation, failure):
    org = user.organization
    old = stored(org, "reject", location=location)
    target = stored(org, "allow", version=2, active=False, location=location)
    before = snapshot(org)
    push = MagicMock(return_value=True)
    if failure == "missing":
        failing = patch("tfc.utils.audit.log_audit", return_value=None)
    elif failure == "storage":
        failing = patch.object(AuditLog.objects, "create", side_effect=RuntimeError("storage unavailable"))
    else:
        failing = audit.mute_audit_signals()
    with patch.object(type(user), "get_membership_level", return_value=Level.OWNER), failing:
        with pytest.raises(RuntimeError, match="audit"):
            if operation == "activate":
                activate_config_version(target, user=user, push=push)
            else:
                write(org, user, "allow", location=location, push=push)
    assert snapshot(org) == before
    old.refresh_from_db()
    assert old.is_active
    assert not rows(org).exists()
    push.assert_not_called()


@pytest.mark.parametrize("location", ["primary", "rollout"])
@pytest.mark.parametrize("operation", ["merge", "replace", "updater", "activate"])
@pytest.mark.parametrize("level", [Level.MEMBER, Level.VIEWER, None])
def test_non_admin_cannot_set_allow_through_any_writer(user, location, operation, level):
    org = user.organization
    stored(org, "reject", location=location)
    target = stored(org, "allow", version=2, active=False, location=location)
    before = snapshot(org)
    push = MagicMock(return_value=True)
    actor = user if level is not None else None
    with patch.object(type(user), "get_membership_level", return_value=level):
        with pytest.raises(PermissionDenied, match=GATEWAY_ADMIN_MESSAGE):
            if operation == "activate":
                activate_config_version(target, user=actor, push=push)
            elif operation == "updater":
                write_config_version_with(org, lambda config: setattr(
                    config, "routing", routing("allow", location)), user=actor, push=push)
            elif operation == "replace":
                write(org, actor, "allow", location=location, push=push)
            else:
                write_config_version(org, {"routing": routing("allow", location)}, user=actor, push=push)
    assert snapshot(org) == before
    assert not rows(org).exists()
    push.assert_not_called()


@pytest.mark.parametrize("location", ["primary", "rollout"])
def test_rollback_audits_current_value_to_historical_value(user, location):
    org = user.organization
    with patch.object(type(user), "get_membership_level", return_value=Level.ADMIN):
        older = write(org, user, "allow", location=location)
        write(org, user, "reject", location=location)
        rows(org).delete()
        restored, _ = activate_config_version(older, user=user, push=lambda config: True)
    assert restored.version == older.version
    assert_row(rows(org).get(), user, restored,
               {key(location): {"old": "reject", "new": "allow"}})


@pytest.mark.parametrize("location", ["primary", "rollout"])
def test_audit_is_visible_inside_transaction_before_gateway_push(user, location):
    from django.db import connection

    def push(config):
        assert connection.in_atomic_block
        assert_row(rows(user.organization).get(), user, config,
                   {key(location): {"old": None, "new": "allow"}})
        return True

    with patch.object(type(user), "get_membership_level", return_value=Level.OWNER):
        write(user.organization, user, "allow", location=location, push=push)


def test_two_changed_routes_make_one_value_free_row(user):
    candidate = routing("allow")
    candidate.update(routing("reject", "rollout"))
    candidate["agents"]["price_ceiling"] = {"request_usd": 0.5}
    with patch.object(type(user), "get_membership_level", return_value=Level.OWNER):
        config, _ = write_config_version(user.organization, {"routing": candidate},
                                         user=user, push=lambda config: True)
    assert_row(rows(user.organization).get(), user, config, {
        FIELD: {"old": None, "new": "allow"},
        "rollout_unpriced_under_cap": {"old": None, "new": "reject"},
    })


@pytest.mark.parametrize("location", ["primary", "rollout"])
def test_alias_precedence_audits_the_value_the_gateway_receives(user, location):
    candidate = routing("reject", location)
    target = candidate if location == "primary" else candidate["rollout"]["routing"]
    target["agents"]["unpricedUnderCap"] = "allow"
    with patch.object(type(user), "get_membership_level", return_value=Level.OWNER):
        config, _ = write_config_version(user.organization, {"routing": candidate},
                                         user=user, push=lambda config: True)
    # The generated gateway contract makes canonical spelling authoritative.
    assert_row(rows(user.organization).get(), user, config,
               {key(location): {"old": None, "new": "reject"}})


@pytest.mark.parametrize("level", [Level.MEMBER, Level.VIEWER])
@pytest.mark.parametrize("operation", ["create", "put", "patch", "activate", "update-config"])
def test_api_non_admin_cannot_set_allow(auth_client, user, level, operation):
    org = user.organization
    stored(org, "reject")
    target = stored(org, "allow", version=2, active=False)
    before = snapshot(org)
    payload = {"routing": routing("allow")}
    with patch.object(type(user), "get_membership_level", return_value=level):
        if operation == "update-config":
            response = auth_client.post("/agentcc/gateways/default/update-config/", payload, format="json")
        elif operation == "create":
            response = auth_client.post("/agentcc/org-configs/", payload, format="json")
        elif operation == "activate":
            response = auth_client.post(f"/agentcc/org-configs/{target.pk}/activate/", {}, format="json")
        else:
            response = getattr(auth_client, operation)(f"/agentcc/org-configs/{target.pk}/", payload, format="json")
    assert response.status_code == 403
    assert GATEWAY_ADMIN_MESSAGE in str(response.data)
    assert snapshot(org) == before
    assert not rows(org).exists()


def test_api_owner_create_and_rollback_are_audited(auth_client, user):
    with (
        patch.object(type(user), "get_membership_level", return_value=Level.OWNER),
        patch("agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway", return_value=True),
    ):
        for value in ("allow", "reject"):
            response = auth_client.post("/agentcc/org-configs/", {"routing": routing(value)}, format="json")
            assert response.status_code == 200
        older = AgentccOrgConfig.no_workspace_objects.get(organization=user.organization, version=1)
        response = auth_client.post(f"/agentcc/org-configs/{older.pk}/activate/", {}, format="json")
        assert response.status_code == 200
    records = list(rows(user.organization).order_by("created_at"))
    assert len(records) == 3
    for row, old, new, version in zip(records, [None, "allow", "reject"],
                                      ["allow", "reject", "allow"], [1, 2, 1], strict=True):
        assert row.actor_id == user.pk and row.created_at is not None
        assert row.changes == {FIELD: {"old": old, "new": new}}
        assert row.metadata == {"config_version": version}
