"""Person/tenant binding must survive every supported key write surface."""

import socket
from unittest.mock import patch

import pytest
from django.contrib.admin.sites import AdminSite
from django.core.exceptions import ValidationError
from django.test import RequestFactory
from rest_framework.test import APIRequestFactory

from agentcc.admin import AgentccAPIKeyAdmin
from agentcc.models import AgentccAPIKey
from agentcc.serializers.api_key import (
    AgentccAPIKeySerializer,
    AgentccAPIKeyUpdateSerializer,
)
from agentcc.services import auth_bridge
from agentcc.views.api_key_bulk import APIKeyBulkView

FIELDS = (
    "user",
    "user_id",
    "organization",
    "organization_id",
    "workspace",
    "workspace_id",
)


def test_guard_refuses_nonlocal_dns_and_connect():
    with pytest.raises(AssertionError, match="non-loopback"):
        socket.getaddrinfo("ownership.invalid", 443)
    with socket.socket() as sock:
        with pytest.raises(AssertionError, match="non-loopback"):
            sock.connect(("192.0.2.1", 443))
        with pytest.raises(AssertionError, match="non-loopback"):
            sock.connect_ex(("192.0.2.1", 443))


def test_admin_change_excludes_authority_and_ignores_forged_post(ownership_case):
    case = ownership_case
    request = RequestFactory().get("/admin/prism/agentccapikey/")
    request.user = case.user
    request.user.is_superuser = True
    model_admin = AgentccAPIKeyAdmin(AgentccAPIKey, AdminSite())
    for field in ("user", "organization", "workspace"):
        assert field in model_admin.get_readonly_fields(request, case.key)
    form_class = model_admin.get_form(request, case.key, change=True)
    for field in ("user", "organization", "workspace"):
        assert field not in form_class.base_fields
    # Generate an otherwise valid admin POST, retaining all editable fields.
    initial = form_class(instance=case.key)
    data = {name: initial[name].value() for name in initial.fields}
    data.update({field: str(value.pk) for field, value in case.other.items()})
    data["name"] = "Admin renamed"
    form = form_class(data=data, instance=case.key)
    assert form.is_valid(), form.errors
    model_admin.save_model(request, form.save(commit=False), form, change=True)
    case.key.refresh_from_db()
    assert case.key.name == "Admin renamed"
    assert case.key.user_id == case.user.pk
    assert case.key.organization_id == case.user.organization_id
    assert case.key.workspace_id != case.other["workspace"].pk
    assert set(model_admin.get_actions(request)) <= {"delete_selected"}


def test_admin_cannot_add_a_key_for_an_arbitrary_person(ownership_case):
    request = RequestFactory().get("/admin/prism/agentccapikey/add/")
    request.user = ownership_case.user
    request.user.is_superuser = True
    model_admin = AgentccAPIKeyAdmin(AgentccAPIKey, AdminSite())
    assert model_admin.has_add_permission(request) is False
    ownership_case.unchanged()


@pytest.mark.parametrize(
    "serializer_class", [AgentccAPIKeySerializer, AgentccAPIKeyUpdateSerializer]
)
@pytest.mark.parametrize("field", FIELDS)
def test_every_update_serializer_explicitly_refuses_authority(
    ownership_case, serializer_class, field
):
    case = ownership_case
    value = str(case.other[field.removesuffix("_id")].pk)
    serializer = serializer_class(
        instance=case.key, data={field: value, "name": "Changed"}, partial=True
    )
    assert not serializer.is_valid(), "ownership fields must not be silently accepted"
    assert field in serializer.errors
    case.unchanged()


@pytest.mark.parametrize("method", ["patch", "put"])
@pytest.mark.parametrize("field", FIELDS)
def test_public_updates_refuse_authority(
    ownership_case, auth_client, gateway, method, field
):
    case = ownership_case
    response = getattr(auth_client, method)(
        f"/agentcc/api-keys/{case.key.pk}/",
        {field: str(case.other[field.removesuffix("_id")].pk), "name": "Changed"},
        format="json",
    )
    assert response.status_code == 400
    gateway.update_key.assert_not_called()
    case.unchanged()


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_bulk_sync_is_read_only_even_with_valid_admin_auth(ownership_case, method):
    case = ownership_case
    request = getattr(APIRequestFactory(), method)(
        "/agentcc/api-keys/bulk/",
        {
            "id": str(case.key.pk),
            "user": str(case.other["user"].pk),
            "organization": str(case.other["organization"].pk),
        },
        format="json",
    )
    with patch("agentcc.permissions.IsAdminToken.has_permission", return_value=True):
        response = APIKeyBulkView.as_view()(request)
    assert response.status_code == 405
    case.unchanged()


@pytest.mark.parametrize("field", FIELDS)
def test_bridge_refuses_authority_before_any_gateway_effect(
    ownership_case, gateway, field
):
    case = ownership_case
    value = case.other[field.removesuffix("_id")]
    if field.endswith("_id"):
        value = value.pk
    with pytest.raises(ValidationError, match="immutable"):
        auth_bridge.update_key(case.key, **{field: value, "name": "Changed"})
    gateway.update_key.assert_not_called()
    case.unchanged()


def test_bridge_rejects_an_already_mutated_instance_before_gateway(
    ownership_case, gateway
):
    case = ownership_case
    case.key.user = case.other["user"]
    with pytest.raises(ValidationError, match="immutable"):
        auth_bridge.update_key(case.key, name="Changed")
    gateway.update_key.assert_not_called()
    case.unchanged()


def test_personal_session_creation_binds_the_creating_person(
    ownership_case, session_auth_client, gateway
):
    case = ownership_case
    with patch("agentcc.views.api_key.ensure_first_org_config"):
        response = session_auth_client.post(
            "/agentcc/api-keys/", {"name": "My personal key"}, format="json"
        )
    assert response.status_code == 201, response.data
    key = AgentccAPIKey.no_workspace_objects.get(gateway_key_id="ownership-created")
    assert key.user_id == case.user.pk
    assert key.organization_id == case.user.organization_id
    assert key.workspace_id is None
    assert key.key_hash
    gateway.create_key.assert_called_once()
    case.unchanged()


def test_non_owner_settings_still_update(ownership_case, auth_client, gateway):
    case = ownership_case
    response = auth_client.patch(
        f"/agentcc/api-keys/{case.key.pk}/",
        {
            "name": "Renamed",
            "owner": "Display label",
            "allowed_models": ["synthetic-model"],
        },
        format="json",
    )
    assert response.status_code == 200, response.data
    case.key.refresh_from_db()
    assert case.key.name == "Renamed"
    assert case.key.owner == "Display label"  # A label, not the user FK.
    assert case.key.allowed_models == ["synthetic-model"]
    assert case.key.user_id == case.user.pk
    gateway.update_key.assert_called_once()


@pytest.mark.parametrize("field", ["user", "organization", "workspace"])
def test_provision_cannot_rebind_an_existing_issued_key(ownership_case, gateway, field):
    case = ownership_case
    # Same key hash as the gateway response, so collision-by-hash is not the refusal.
    import hashlib

    key = AgentccAPIKey.no_workspace_objects.create(
        user=case.user,
        organization=case.user.organization,
        workspace=case.key.workspace if field == "workspace" else None,
        gateway_key_id="ownership-provision-rebind",
        name="Original",
        key_hash=hashlib.sha256(
            gateway.create_key.return_value["key"].encode()
        ).hexdigest(),
    )
    original = (key.user_id, key.organization_id, key.workspace_id)
    gateway.create_key.return_value["id"] = key.gateway_key_id
    args = {
        "name": "Changed",
        "user": case.user,
        "organization": case.user.organization,
    }
    if field != "workspace":
        args[field] = case.other[field]
    # provision_key sets workspace=None; that too must not widen an existing key.
    with pytest.raises(ValidationError, match="immutable"):
        auth_bridge.provision_key(**args)
    key.refresh_from_db()
    assert (key.user_id, key.organization_id, key.workspace_id) == original
    assert key.name == "Original"
    case.unchanged()


def test_sync_preserves_stored_authority(ownership_case, gateway):
    case = ownership_case
    gateway.list_keys.return_value = {
        "data": [
            {
                "id": case.key.gateway_key_id,
                "status": "revoked",
                "user_id": str(case.other["user"].pk),
                "metadata": {"org_id": str(case.other["organization"].pk)},
            }
        ]
    }
    auth_bridge.sync_keys(org=case.other["organization"])
    case.unchanged()
    assert case.key.status == AgentccAPIKey.REVOKED


def test_sync_adoption_race_cannot_rebind_an_existing_row(ownership_case, gateway):
    case = ownership_case
    gateway.list_keys.return_value = {
        "data": [
            {
                "id": case.key.gateway_key_id,
                "name": "Changed",
                "metadata": {"org_id": str(case.other["organization"].pk)},
            }
        ]
    }
    manager = AgentccAPIKey.no_workspace_objects
    real_filter = manager.filter

    def stale_census(*args, **kwargs):
        if kwargs == {"gateway_key_id__in": [case.key.gateway_key_id]}:
            return []  # Row committed after the sync's initial census.
        return real_filter(*args, **kwargs)

    with patch.object(manager, "filter", side_effect=stale_census):
        with pytest.raises(ValidationError, match="immutable"):
            auth_bridge.sync_keys(org=case.other["organization"])
    case.unchanged()
