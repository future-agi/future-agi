import pytest
from accounts.models import Workspace
from rest_framework import status

from simulate.models import Persona


@pytest.fixture
def source_persona(db, organization, workspace):
    return Persona.no_workspace_objects.create(
        persona_type=Persona.PersonaType.WORKSPACE,
        organization=organization,
        workspace=workspace,
        name="Source Persona",
        description="Reusable source persona",
    )


ANONYMOUS_PERSONA_ID = "00000000-0000-4000-8000-000000001022"


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/simulate/api/personas/", None),
        ("post", "/simulate/api/personas/", {}),
        ("post", f"/simulate/api/personas/duplicate/{ANONYMOUS_PERSONA_ID}/", {}),
        ("get", "/simulate/api/personas/field-options/", None),
        ("get", "/simulate/api/personas/system/", None),
        ("get", "/simulate/api/personas/workspace/", None),
        ("get", f"/simulate/api/personas/{ANONYMOUS_PERSONA_ID}/", None),
        ("put", f"/simulate/api/personas/{ANONYMOUS_PERSONA_ID}/", {}),
        ("patch", f"/simulate/api/personas/{ANONYMOUS_PERSONA_ID}/", {}),
        ("delete", f"/simulate/api/personas/{ANONYMOUS_PERSONA_ID}/", None),
        (
            "post",
            f"/simulate/api/personas/{ANONYMOUS_PERSONA_ID}/duplicate/",
            {},
        ),
    ],
)
def test_persona_routes_reject_anonymous_before_work(
    api_client, method, path, body
):
    request = getattr(api_client, method)

    if body is None:
        response = request(path)
    else:
        response = request(path, body, format="json")

    assert response.status_code in {
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    }


@pytest.mark.integration
@pytest.mark.api
class TestPersonaDuplicateView:
    def test_list_rejects_invalid_simulation_type(self, auth_client):
        response = auth_client.get(
            "/simulate/api/personas/", {"simulation_type": "fax"}
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        assert data["status"] is False
        assert "simulation_type" in data["details"]

    def test_create_rejects_null_name(self, auth_client):
        response = auth_client.post(
            "/simulate/api/personas/",
            {
                "name": None,
                "description": "Invalid persona",
                "gender": ["male"],
                "language": ["English"],
                "simulation_type": "voice",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        assert data["status"] is False
        assert "name" in data["details"]

    def test_duplicate_custom_route_success(self, auth_client, source_persona):
        response = auth_client.post(
            f"/simulate/api/personas/duplicate/{source_persona.id}/",
            {"name": "Workspace Copy"},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["status"] is True
        assert data["result"]["name"] == "Workspace Copy"

    def test_duplicate_custom_route_rejects_unknown_body_field(
        self, auth_client, source_persona
    ):
        response = auth_client.post(
            f"/simulate/api/personas/duplicate/{source_persona.id}/",
            {"name": "Workspace Copy", "legacy_extra": "ignore me"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        assert data["status"] is False
        assert data["details"]["legacy_extra"] == ["Unknown field."]

    def test_duplicate_viewset_route_success(self, auth_client, source_persona):
        response = auth_client.post(
            f"/simulate/api/personas/{source_persona.id}/duplicate/",
            {"name": "Workspace Copy From ViewSet"},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["status"] is True
        assert data["result"]["name"] == "Workspace Copy From ViewSet"

    def test_duplicate_viewset_route_rejects_unknown_body_field(
        self, auth_client, source_persona
    ):
        response = auth_client.post(
            f"/simulate/api/personas/{source_persona.id}/duplicate/",
            {"name": "Workspace Copy From ViewSet", "legacy_extra": "ignore me"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        assert data["status"] is False
        assert data["details"]["legacy_extra"] == ["Unknown field."]

    def test_duplicate_custom_route_rejects_other_workspace_source(
        self, auth_client, organization, user
    ):
        other_workspace = Workspace.no_workspace_objects.create(
            name="Other Workspace",
            organization=organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )
        other_persona = Persona.no_workspace_objects.create(
            persona_type=Persona.PersonaType.WORKSPACE,
            organization=organization,
            workspace=other_workspace,
            name="Other Workspace Persona",
            description="Should not be duplicated from active workspace",
        )

        response = auth_client.post(
            f"/simulate/api/personas/duplicate/{other_persona.id}/",
            {"name": "Cross Workspace Copy"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        assert data["status"] is False
        assert "not found" in data["result"].lower()
        assert not Persona.no_workspace_objects.filter(
            name="Cross Workspace Copy"
        ).exists()

    def test_delete_stamps_deleted_at(self, auth_client, source_persona):
        response = auth_client.delete(f"/simulate/api/personas/{source_persona.id}/")

        assert response.status_code == status.HTTP_204_NO_CONTENT
        source_persona.refresh_from_db()
        assert source_persona.deleted is True
        assert source_persona.deleted_at is not None

    def test_update_rejects_duplicate_workspace_name(
        self, auth_client, source_persona, organization, workspace
    ):
        existing = Persona.no_workspace_objects.create(
            persona_type=Persona.PersonaType.WORKSPACE,
            organization=organization,
            workspace=workspace,
            name="Existing Persona",
            description="Existing active persona",
        )

        response = auth_client.patch(
            f"/simulate/api/personas/{source_persona.id}/",
            {"name": existing.name},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        source_persona.refresh_from_db()
        assert source_persona.name == "Source Persona"
