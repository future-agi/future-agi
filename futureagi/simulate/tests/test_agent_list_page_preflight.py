"""Raw framework-owned limits must be refused before required-agent lookup."""

from unittest.mock import patch
from uuid import uuid4

import pytest

from simulate.models import AgentDefinition, AgentVersion
from tfc.utils.pagination import MAX_PAGE_SIZE

URL = "/simulate/agent-definitions/"
pytestmark = pytest.mark.django_db


def test_agent_list_oversize_precedes_required_agent_lookup(auth_client):
    # These are the action's first queryset entry points. Refuse even query
    # construction so no action lookup/count/materialization can precede the cap.
    with (
        patch.object(
            AgentVersion.objects,
            "filter",
            side_effect=AssertionError("version query before page-size refusal"),
        ) as versions,
        patch.object(
            AgentDefinition.objects,
            "filter",
            side_effect=AssertionError("agent query before page-size refusal"),
        ) as agents,
    ):
        response = auth_client.get(
            URL,
            {"limit": MAX_PAGE_SIZE + 1, "agent_definition_id": str(uuid4())},
        )
    assert response.status_code == 400, response.data
    assert response.data["type"] == "validation_error"
    assert response.data["code"] == "page_size_too_large"
    versions.assert_not_called()
    agents.assert_not_called()


def test_agent_list_exact_cap_keeps_required_agent_first(
    auth_client, organization, workspace
):
    required = AgentDefinition.no_workspace_objects.create(
        agent_name="Required agent",
        agent_type="text",
        inbound=False,
        description="Page-size boundary control",
        organization=organization,
        workspace=workspace,
    )
    other = AgentDefinition.no_workspace_objects.create(
        agent_name="Other agent",
        agent_type="text",
        inbound=False,
        description="Page-size boundary control",
        organization=organization,
        workspace=workspace,
    )
    response = auth_client.get(
        URL, {"limit": MAX_PAGE_SIZE, "agent_definition_id": str(required.id)}
    )
    assert response.status_code == 200, response.data
    assert [str(row["id"]) for row in response.data["results"]] == [
        str(required.id),
        str(other.id),
    ]
    assert response.data["count"] == 2
    assert response.data["current_page"] == 1
    assert response.data["next"] is None
