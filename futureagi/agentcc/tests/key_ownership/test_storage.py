"""Ordinary ORM helpers cannot bypass API-key ownership immutability."""

from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F

from accounts.models import User
from agentcc.models import AgentccAPIKey
from tfc.middleware.workspace_context import set_workspace_context

FIELDS = ("user", "organization", "workspace")
MANAGERS = ("objects", "all_objects", "no_workspace_objects", "_base_manager")


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("bulk", [True, False])
def test_reverse_relation_add_refuses_rebinding(ownership_case, field, bulk):
    case = ownership_case
    with pytest.raises(ValidationError, match="immutable"):
        case.other[field].agentcc_api_keys.add(case.key, bulk=bulk)
    case.unchanged()


@pytest.mark.parametrize("field", ["user", "workspace"])
@pytest.mark.parametrize("method", ["remove", "clear", "set"])
def test_reverse_relation_cannot_clear_ownership(ownership_case, field, method):
    case = ownership_case
    manager = getattr(case.key, field).agentcc_api_keys
    with pytest.raises(ValidationError, match="immutable"):
        if method == "remove":
            manager.remove(case.key)
        elif method == "set":
            manager.set([])
        else:
            manager.clear()
    case.unchanged()


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize(
    "mode",
    ["save", "save_fields", "save_base", "new_instance", "deferred", "soft_deleted"],
)
def test_model_save_cannot_change_ownership(ownership_case, field, mode):
    case = ownership_case
    key = case.key
    if mode == "new_instance":
        # _state.adding is not proof of INSERT: a supplied existing PK can UPDATE.
        key = AgentccAPIKey(
            id=key.pk,
            user_id=key.user_id,
            organization_id=key.organization_id,
            workspace_id=key.workspace_id,
            gateway_key_id=key.gateway_key_id,
        )
    elif mode == "deferred":
        key = AgentccAPIKey.no_workspace_objects.only("id", "name").get(pk=key.pk)
    elif mode == "soft_deleted":
        key.delete()
    setattr(key, field, case.other[field])
    with pytest.raises(ValidationError, match="immutable"):
        with transaction.atomic():
            if mode == "save_base":
                key.save_base()
            elif mode == "save_fields":
                key.save(update_fields=[field, "name"])
            elif mode == "new_instance":
                key.save(force_update=True)
            else:
                key.save()
    case.unchanged()


@pytest.mark.parametrize("field", ["user", "workspace"])
def test_owner_cannot_be_cleared_by_save(ownership_case, field):
    case = ownership_case
    setattr(case.key, field, None)
    with pytest.raises(ValidationError, match="immutable"):
        case.key.save()
    case.unchanged()


@pytest.mark.parametrize("manager_name", MANAGERS)
@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("alias", [False, True])
def test_queryset_update_refuses_authority(ownership_case, manager_name, field, alias):
    case = ownership_case
    value = case.other[field]
    if alias:
        field, value = field + "_id", value.pk
    with pytest.raises(ValidationError, match="immutable"):
        getattr(AgentccAPIKey, manager_name).filter(pk=case.key.pk).update(
            **{field: value, "name": "Changed"}
        )
    case.unchanged()


@pytest.mark.parametrize("manager_name", MANAGERS)
@pytest.mark.parametrize("field", FIELDS)
def test_bulk_update_refuses_authority(ownership_case, manager_name, field):
    case = ownership_case
    setattr(case.key, field, case.other[field])
    with pytest.raises(ValidationError, match="immutable"):
        getattr(AgentccAPIKey, manager_name).bulk_update([case.key], [field])
    case.unchanged()


@pytest.mark.parametrize("field", FIELDS)
def test_upsert_refuses_owner_change(ownership_case, field):
    case = ownership_case
    with pytest.raises(ValidationError, match="immutable"):
        AgentccAPIKey.no_workspace_objects.update_or_create(
            pk=case.key.pk, defaults={field: case.other[field], "name": "Changed"}
        )
    case.unchanged()


@pytest.mark.parametrize("field", FIELDS)
def test_bulk_conflict_update_refuses_authority(ownership_case, field):
    case = ownership_case
    setattr(case.key, field, case.other[field])
    with pytest.raises(ValidationError, match="immutable"):
        AgentccAPIKey.no_workspace_objects.bulk_create(
            [case.key],
            update_conflicts=True,
            update_fields=[field],
            unique_fields=["id"],
        )
    case.unchanged()


def test_expression_updates_cannot_bypass_authority_guard(ownership_case):
    case = ownership_case
    with pytest.raises(ValidationError, match="immutable"):
        AgentccAPIKey.no_workspace_objects.filter(pk=case.key.pk).update(
            user_id=F("user_id")
        )
    case.unchanged()


def test_context_helper_cannot_assign_a_workspace_to_existing_global_key(
    ownership_case,
):
    case = ownership_case
    key = AgentccAPIKey.no_workspace_objects.create(
        user=case.user,
        organization=case.user.organization,
        workspace=None,
        gateway_key_id="global-existing",
        name="Global",
    )
    set_workspace_context(
        workspace=case.other["workspace"], organization=case.other["organization"]
    )
    with pytest.raises(ValidationError, match="immutable"):
        key.ensure_workspace_and_organization()
    key.refresh_from_db()
    assert key.workspace_id is None
    assert key.user_id == case.user.pk
    assert key.organization_id == case.user.organization_id


@pytest.mark.parametrize("manager_name", MANAGERS)
def test_non_owner_queryset_and_bulk_updates_still_work(ownership_case, manager_name):
    case = ownership_case
    manager = getattr(AgentccAPIKey, manager_name)
    assert manager.filter(pk=case.key.pk).update(name="Updated") == 1
    case.key.refresh_from_db()
    assert case.key.name == "Updated"
    case.key.name = "Bulk updated"
    assert manager.bulk_update([case.key], ["name"]) == 1
    case.key.refresh_from_db()
    assert case.key.name == "Bulk updated"
    assert case.key.user_id == case.user.pk
    assert case.key.organization_id == case.user.organization_id


def test_upsert_with_identical_ownership_can_update_non_owner_fields(ownership_case):
    case = ownership_case
    key, created = AgentccAPIKey.no_workspace_objects.update_or_create(
        pk=case.key.pk,
        defaults={
            "user": case.user,
            "organization": case.user.organization,
            "name": "Updated",
        },
    )
    assert not created
    assert key.name == "Updated"
    key.refresh_from_db()
    assert key.name == "Updated"
    assert key.user_id == case.user.pk


def test_creation_and_context_creation_still_bind_the_person(ownership_case):
    case = ownership_case
    for key in (
        AgentccAPIKey.create_with_context(
            user=case.user, gateway_key_id="context-create", name="Context"
        ),
        AgentccAPIKey.get_or_create_with_context(
            user=case.user,
            gateway_key_id="context-get-create",
            defaults={"name": "Context"},
        )[0],
    ):
        key.refresh_from_db()
        assert key.user_id == case.user.pk
        assert key.organization_id == case.user.organization_id


def test_deleting_a_person_still_revokes_then_unlinks_without_reassignment(
    ownership_case, gateway
):
    case = ownership_case
    # A workspace created by the departing user is itself cascade-deleted.
    # Use an org-wide key so this exercises SET_NULL on a surviving key row.
    case.key = AgentccAPIKey.no_workspace_objects.create(
        user=case.user,
        organization=case.user.organization,
        workspace=None,
        gateway_key_id="ownership-deleted-person",
        name="Survives person deletion",
    )
    stale = AgentccAPIKey.no_workspace_objects.get(pk=case.key.pk)
    # Real Django Collector path, not an application reassignment to None.
    with patch("accounts.api_key_revocation.publish_key_revocation"):
        User.objects.filter(pk=case.user.pk).delete()
    case.key.refresh_from_db()
    assert case.key.user_id is None
    assert case.key.status == AgentccAPIKey.REVOKED
    # The on-commit revocation callback holds a pre-deletion instance. A
    # status-only save must still work, without writing its stale owner FK.
    from agentcc.services import auth_bridge

    _, failed = auth_bridge.revoke_key(stale)
    assert failed is False
    case.key.refresh_from_db()
    assert case.key.user_id is None
    assert case.key.status == AgentccAPIKey.REVOKED
    case.key.user = case.other["user"]
    with pytest.raises(ValidationError, match="immutable"):
        case.key.save()
    case.key.refresh_from_db()
    assert case.key.user_id is None
