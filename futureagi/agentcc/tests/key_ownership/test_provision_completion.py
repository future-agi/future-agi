"""Only provisioning may finish its own still-unowned, hashless sync row."""

import hashlib
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.db import transaction

from agentcc.models import AgentccAPIKey
from agentcc.models.api_key_ownership import APIKeyQuerySet
from agentcc.serializers.api_key import AgentccAPIKeySerializer
from agentcc.services import auth_bridge


def _snapshot(key):
    return AgentccAPIKey._base_manager.values().get(pk=key.pk)


def _raced_row(case, gateway, **overrides):
    fields = {
        "user": None,
        "organization": case.user.organization,
        "workspace": None,
        "gateway_key_id": gateway.create_key.return_value["id"],
        "key_hash": "",
        "name": "Unfinished sync row",
    }
    fields.update(overrides)
    return AgentccAPIKey.no_workspace_objects.create(**fields)


def _returned_hash(gateway):
    return hashlib.sha256(gateway.create_key.return_value["key"].encode()).hexdigest()


def test_provision_completes_only_its_fresh_hashless_sync_row(ownership_case, gateway):
    case = ownership_case
    raced = []

    def create_with_racing_sync(**kwargs):
        raced.append(
            _raced_row(
                case,
                gateway,
                organization=case.other["organization"],
                workspace=case.other["workspace"],
            )
        )
        return gateway.create_key.return_value

    gateway.create_key.side_effect = create_with_racing_sync
    key, raw_key = auth_bridge.provision_key(name="Completed", user=case.user)
    key.refresh_from_db()
    assert key.pk == raced[0].pk
    assert key.created_at == raced[0].created_at
    assert key.gateway_key_id == gateway.create_key.return_value["id"]
    assert key.user_id == case.user.pk
    assert key.organization_id == case.user.organization_id
    assert key.workspace_id is None
    assert key.key_hash == _returned_hash(gateway)
    assert raw_key == gateway.create_key.return_value["key"]
    assert key.name == "Completed"
    assert key.status == AgentccAPIKey.ACTIVE
    assert (
        AgentccAPIKey._base_manager.filter(gateway_key_id=key.gateway_key_id).count()
        == 1
    )
    gateway.revoke_key.assert_not_called()
    # Ordinary edits remain supported, without reopening ownership assignment.
    auth_bridge.update_key(key, name="Renamed after completion")
    key.refresh_from_db()
    assert key.name == "Renamed after completion"
    assert key.user_id == case.user.pk
    assert key.organization_id == case.user.organization_id
    assert key.workspace_id is None
    assert key.key_hash == _returned_hash(gateway)
    gateway.update_key.assert_called_once()
    case.unchanged()


def test_provision_cannot_adopt_a_legacy_hashed_ownerless_row(ownership_case, gateway):
    case = ownership_case
    # Matching hashes avoid collision detection masking the ownership refusal.
    key = _raced_row(case, gateway, key_hash=_returned_hash(gateway))
    before = _snapshot(key)
    with pytest.raises(ValidationError, match="immutable"):
        auth_bridge.provision_key(name="Adopt legacy key", user=case.user)
    assert _snapshot(key) == before
    gateway.revoke_key.assert_called_once_with(key.gateway_key_id)
    case.unchanged()


def test_provision_does_not_adopt_a_hashless_row_with_another_gateway_id(
    ownership_case, gateway
):
    case = ownership_case
    unrelated = _raced_row(case, gateway, gateway_key_id="unrelated-sync-row")
    before = _snapshot(unrelated)
    key, _ = auth_bridge.provision_key(name="Fresh personal key", user=case.user)
    assert key.pk != unrelated.pk
    assert key.gateway_key_id == gateway.create_key.return_value["id"]
    assert key.user_id == case.user.pk
    assert key.key_hash == _returned_hash(gateway)
    assert _snapshot(unrelated) == before
    gateway.revoke_key.assert_not_called()
    case.unchanged()


def test_completion_refuses_a_lookup_returning_a_different_gateway_id(
    ownership_case, gateway
):
    case = ownership_case
    unrelated = _raced_row(case, gateway, gateway_key_id="different-sync-row")
    before = _snapshot(unrelated)
    # Fault-inject a misdirected lookup: the write itself must bind the minted ID,
    # not merely trust the row returned by get_or_create (also used by upserts).
    with patch.object(
        APIKeyQuerySet, "get_or_create", return_value=(unrelated, False)
    ) as lookup:
        with pytest.raises(ValidationError, match="immutable"):
            auth_bridge.provision_key(name="Misdirected completion", user=case.user)
    assert (
        lookup.call_args.kwargs["gateway_key_id"]
        == gateway.create_key.return_value["id"]
    )
    assert _snapshot(unrelated) == before
    gateway.revoke_key.assert_called_once_with(gateway.create_key.return_value["id"])
    case.unchanged()


@pytest.mark.parametrize("has_hash", [False, True])
@pytest.mark.parametrize("other_person", [False, True])
def test_provision_refuses_completion_of_an_already_owned_row(
    ownership_case, gateway, has_hash, other_person
):
    case = ownership_case
    key = _raced_row(
        case,
        gateway,
        user=case.user,
        key_hash=_returned_hash(gateway) if has_hash else "",
    )
    before = _snapshot(key)
    # A repeated completion is forbidden even when it names the SAME person.
    user = case.other["user"] if other_person else case.user
    with pytest.raises(ValidationError, match="immutable"):
        auth_bridge.provision_key(
            name="Second completion", user=user, organization=case.user.organization
        )
    assert _snapshot(key) == before
    gateway.revoke_key.assert_called_once_with(key.gateway_key_id)
    case.unchanged()


def test_a_completed_racing_row_cannot_be_completed_twice(ownership_case, gateway):
    case = ownership_case
    row = _raced_row(case, gateway)
    key, _ = auth_bridge.provision_key(name="First completion", user=case.user)
    assert key.pk == row.pk
    assert key.user_id == case.user.pk
    assert key.key_hash == _returned_hash(gateway)
    before = _snapshot(key)
    with pytest.raises(ValidationError, match="immutable"):
        auth_bridge.provision_key(name="Second completion", user=case.user)
    assert _snapshot(key) == before
    gateway.revoke_key.assert_called_once_with(key.gateway_key_id)
    case.unchanged()


@pytest.mark.parametrize("missing", ["person", "raw_key"])
def test_completion_requires_a_person_and_the_new_key_hash(
    ownership_case, gateway, missing
):
    case = ownership_case
    row = _raced_row(case, gateway)
    before = _snapshot(row)
    user = None if missing == "person" else case.user
    if missing == "raw_key":
        gateway.create_key.return_value["key"] = ""
    with pytest.raises(ValidationError, match="immutable"):
        auth_bridge.provision_key(
            name="Incomplete completion", user=user, organization=case.user.organization
        )
    assert _snapshot(row) == before
    gateway.revoke_key.assert_called_once_with(row.gateway_key_id)
    case.unchanged()


@pytest.mark.parametrize("has_hash", [False, True])
@pytest.mark.parametrize(
    "path",
    [
        "save",
        "save_base",
        "update",
        "bulk_update",
        "update_or_create",
        "bulk_create",
        "reverse_add",
        "reverse_add_save",
        "bridge",
        "serializer",
    ],
)
def test_ownerless_rows_cannot_be_adopted_outside_provisioning(
    ownership_case, gateway, has_hash, path
):
    case = ownership_case
    key = _raced_row(
        case, gateway, key_hash=_returned_hash(gateway) if has_hash else ""
    )
    before = _snapshot(key)
    manager = AgentccAPIKey.no_workspace_objects
    if path == "serializer":
        serializer = AgentccAPIKeySerializer(
            instance=key, data={"user": str(case.user.pk)}, partial=True
        )
        assert not serializer.is_valid()
        assert "user" in serializer.errors
    else:
        with pytest.raises(ValidationError, match="immutable"):
            with transaction.atomic():
                if path in {"save", "save_base", "bulk_update", "bulk_create"}:
                    key.user = case.user
                    if path == "bulk_update":
                        manager.bulk_update([key], ["user"])
                    elif path == "bulk_create":
                        manager.bulk_create(
                            [key],
                            update_conflicts=True,
                            update_fields=["user"],
                            unique_fields=["id"],
                        )
                    else:
                        getattr(key, path)()
                elif path == "update":
                    manager.filter(pk=key.pk).update(user=case.user)
                elif path == "update_or_create":
                    manager.update_or_create(pk=key.pk, defaults={"user": case.user})
                elif path in {"reverse_add", "reverse_add_save"}:
                    case.user.agentcc_api_keys.add(key, bulk=path == "reverse_add")
                elif path == "bridge":
                    auth_bridge.update_key(key, user=case.user)
    assert _snapshot(key) == before
    gateway.update_key.assert_not_called()
    gateway.create_key.assert_not_called()
    case.unchanged()
