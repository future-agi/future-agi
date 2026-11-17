"""G045-01: custom-property writes require an Owner/Admin of the request org.

Use real memberships and valid write bodies through the registered API. Forced
login only bypasses authentication; the view's permission checks still run.
Member/Viewer fixtures have workspace write access so it cannot stand in for
organization-admin authorization. No gateway or provider call is needed.
"""

import pytest

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import WorkspaceMembership
from agentcc.models.custom_property import AgentccCustomPropertySchema
from agentcc.tests.r7_console_a_support import (
    LEVEL_ROLES,
    ClientFactory,
    owner_of_new_org,
    user_with_level,
)
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

URL = "/agentcc/custom-properties/"
WRITE_METHODS = ("post", "put", "patch", "delete")
NON_ADMIN_LEVELS = (Level.MEMBER, Level.VIEWER)
ADMIN_LEVELS = (Level.OWNER, Level.ADMIN)

pytestmark = [pytest.mark.django_db, pytest.mark.integration, pytest.mark.api]


@pytest.fixture
def clients():
    factory = ClientFactory()
    yield factory
    factory.close()


@pytest.fixture
def schema(organization, workspace):
    return AgentccCustomPropertySchema.no_workspace_objects.create(
        organization=organization,
        name="customer_tier",
        property_type="string",
        description="original",
        required=True,
    )


def _snapshot():
    """Include soft-deleted rows and timestamps so a refusal must change nothing."""
    return list(AgentccCustomPropertySchema.all_objects.order_by("id").values())


def _write(client, method, schema):
    if method == "post":
        return client.post(
            URL,
            {"name": "new_property", "property_type": "string"},
            format="json",
        )
    detail = f"{URL}{schema.id}/"
    if method == "delete":
        return client.delete(detail)
    data = {"description": "updated"}
    if method == "put":
        data.update(name=schema.name, property_type="string", required=True)
    return getattr(client, method)(detail, data, format="json")


def _assert_write_succeeded(response, method, schema):
    assert response.status_code == 200, response.content
    result = response.json()["result"]
    if method == "post":
        created = AgentccCustomPropertySchema.no_workspace_objects.get(id=result["id"])
        assert created.organization_id == schema.organization_id
        assert created.name == result["name"] == "new_property"
        assert created.property_type == result["property_type"] == "string"
    else:
        stored = AgentccCustomPropertySchema.all_objects.get(id=schema.id)
        if method == "delete":
            assert result == {"deleted": True}
            assert stored.deleted is True
            assert stored.deleted_at is not None
        else:
            assert result["id"] == str(schema.id)
            assert stored.description == result["description"] == "updated"
            assert stored.deleted is False


@pytest.mark.parametrize("level", NON_ADMIN_LEVELS, ids=("member", "viewer"))
@pytest.mark.parametrize("method", WRITE_METHODS)
def test_non_admin_cannot_write(
    organization, workspace, clients, schema, level, method
):
    user, _ = user_with_level(organization, level, workspace)
    before = _snapshot()

    response = _write(clients(user, workspace), method, schema)

    assert response.status_code == 403, response.content
    assert _snapshot() == before


@pytest.mark.parametrize("level", ADMIN_LEVELS, ids=("owner", "admin"))
@pytest.mark.parametrize("method", WRITE_METHODS)
def test_owner_and_admin_can_write(
    organization, workspace, clients, schema, level, method
):
    user, _ = user_with_level(organization, level, workspace)

    response = _write(clients(user, workspace), method, schema)

    _assert_write_succeeded(response, method, schema)


@pytest.mark.parametrize(
    "level",
    (*NON_ADMIN_LEVELS, *ADMIN_LEVELS),
    ids=("member", "viewer", "owner", "admin"),
)
@pytest.mark.parametrize("action", ("list", "retrieve", "validate"))
def test_all_roles_keep_read_and_validate_access(
    organization, workspace, clients, schema, level, action
):
    user, _ = user_with_level(organization, level, workspace)
    client = clients(user, workspace)
    before = _snapshot()

    if action == "validate":
        response = client.post(f"{URL}validate/", {"properties": {}}, format="json")
    else:
        response = client.get(URL if action == "list" else f"{URL}{schema.id}/")

    assert response.status_code == 200, response.content
    result = response.json()["result"]
    if action == "list":
        assert [row["id"] for row in result] == [str(schema.id)]
    elif action == "retrieve":
        assert result["id"] == str(schema.id)
        assert result["name"] == schema.name
    else:
        assert result == {
            "valid": False,
            "errors": [f"Missing required property: {schema.name}"],
        }
    assert _snapshot() == before


@pytest.mark.parametrize(
    "home_level,active_level,allowed",
    (
        (Level.OWNER, Level.MEMBER, False),
        (Level.ADMIN, Level.VIEWER, False),
        (Level.MEMBER, Level.OWNER, True),
        (Level.VIEWER, Level.ADMIN, True),
    ),
    ids=("owner-to-member", "admin-to-viewer", "member-to-owner", "viewer-to-admin"),
)
@pytest.mark.parametrize("method", WRITE_METHODS)
def test_write_uses_request_organization_role(
    organization, workspace, clients, home_level, active_level, allowed, method
):
    user, _ = user_with_level(organization, home_level, workspace)
    active_org, _, active_workspace = owner_of_new_org("Custom property active org")
    membership = OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=active_org,
        role=LEVEL_ROLES[active_level],
        level=active_level,
        is_active=True,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=user,
        workspace=active_workspace,
        role=OrganizationRoles.WORKSPACE_MEMBER,
        level=Level.WORKSPACE_MEMBER,
        organization_membership=membership,
        is_active=True,
    )
    active_schema = AgentccCustomPropertySchema.no_workspace_objects.create(
        organization=active_org, name="active_org_property", property_type="string"
    )
    before = _snapshot()

    response = _write(clients(user, active_workspace), method, active_schema)

    if allowed:
        _assert_write_succeeded(response, method, active_schema)
    else:
        assert response.status_code == 403, response.content
        assert _snapshot() == before
