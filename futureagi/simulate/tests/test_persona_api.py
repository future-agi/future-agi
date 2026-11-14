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


@pytest.fixture
def other_workspace_persona(db, organization, user):
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
        description="Persona owned by a different workspace",
    )
    return other_workspace, other_persona


@pytest.fixture
def system_persona(db):
    from tfc.middleware.workspace_context import (
        clear_workspace_context,
        get_current_organization,
        get_current_workspace,
        set_workspace_context,
    )

    saved_workspace = get_current_workspace()
    saved_organization = get_current_organization()
    clear_workspace_context()
    try:
        persona = Persona.no_workspace_objects.create(
            persona_type=Persona.PersonaType.SYSTEM,
            organization=None,
            workspace=None,
            name="Global System Persona",
            description="System-level persona visible everywhere",
        )
    finally:
        set_workspace_context(
            workspace=saved_workspace, organization=saved_organization
        )
    return persona


def _extract_list_items(payload):
    result = payload["result"]
    if isinstance(result, dict) and "results" in result:
        return result["results"]
    return result


@pytest.mark.integration
@pytest.mark.api
class TestPersonaListView:
    def test_list_returns_workspace_personas(self, auth_client, source_persona):
        response = auth_client.get("/simulate/api/personas/")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] is True
        listed_ids = {row["id"] for row in _extract_list_items(data)}
        assert str(source_persona.id) in listed_ids

    def test_list_excludes_other_workspace_personas(
        self, auth_client, source_persona, other_workspace_persona
    ):
        _, hidden_persona = other_workspace_persona

        response = auth_client.get("/simulate/api/personas/")

        assert response.status_code == status.HTTP_200_OK
        listed_ids = {row["id"] for row in _extract_list_items(response.json())}
        assert str(source_persona.id) in listed_ids
        assert str(hidden_persona.id) not in listed_ids


@pytest.mark.integration
@pytest.mark.api
class TestPersonaCRUD:
    def _create_payload(self, **overrides):
        payload = {
            "name": "Created Persona",
            "description": "A persona created via the API",
        }
        payload.update(overrides)
        return payload

    def test_create_persists_workspace_persona(
        self, auth_client, organization, workspace
    ):
        response = auth_client.post(
            "/simulate/api/personas/",
            self._create_payload(name="Fresh Persona"),
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        body = response.json()
        assert body["status"] is True
        result = body["result"]
        assert result["name"] == "Fresh Persona"
        assert result["description"] == "A persona created via the API"
        persona = Persona.no_workspace_objects.get(id=result["id"])
        assert persona.workspace_id == workspace.id
        assert persona.organization_id == organization.id
        assert persona.persona_type == Persona.PersonaType.WORKSPACE

    def test_create_ignores_unknown_body_field(self, auth_client):
        response = auth_client.post(
            "/simulate/api/personas/",
            self._create_payload(
                name="Persona With Extras", legacy_extra="ignore me"
            ),
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        result = response.json()["result"]
        assert result["name"] == "Persona With Extras"
        assert "legacy_extra" not in result

    def test_create_scopes_new_persona_to_active_workspace(
        self, auth_client, workspace, other_workspace_persona
    ):
        other_workspace, _ = other_workspace_persona

        response = auth_client.post(
            "/simulate/api/personas/",
            self._create_payload(name="Scoped Persona"),
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        persona = Persona.no_workspace_objects.get(
            id=response.json()["result"]["id"]
        )
        assert persona.workspace_id == workspace.id
        assert persona.workspace_id != other_workspace.id

    def test_retrieve_returns_persona_detail(self, auth_client, source_persona):
        response = auth_client.get(f"/simulate/api/personas/{source_persona.id}/")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] is True
        assert data["result"]["id"] == str(source_persona.id)
        assert data["result"]["name"] == source_persona.name

    def test_retrieve_persona_not_found_returns_404(self, auth_client):
        response = auth_client.get(
            f"/simulate/api/personas/{ANONYMOUS_PERSONA_ID}/"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_retrieve_other_workspace_returns_404(
        self, auth_client, other_workspace_persona
    ):
        _, hidden_persona = other_workspace_persona

        response = auth_client.get(
            f"/simulate/api/personas/{hidden_persona.id}/"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_partial_update_updates_fields(self, auth_client, source_persona):
        response = auth_client.patch(
            f"/simulate/api/personas/{source_persona.id}/",
            {"description": "Patched description"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["result"]["description"] == "Patched description"
        source_persona.refresh_from_db()
        assert source_persona.description == "Patched description"

    def test_partial_update_other_workspace_returns_404(
        self, auth_client, other_workspace_persona
    ):
        _, hidden_persona = other_workspace_persona

        response = auth_client.patch(
            f"/simulate/api/personas/{hidden_persona.id}/",
            {"description": "Should not apply"},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        hidden_persona.refresh_from_db()
        assert hidden_persona.description == "Persona owned by a different workspace"

    def test_update_updates_fields(self, auth_client, source_persona):
        response = auth_client.put(
            f"/simulate/api/personas/{source_persona.id}/",
            {
                "name": "Updated Persona",
                "description": "Replaced description",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["result"]["name"] == "Updated Persona"
        source_persona.refresh_from_db()
        assert source_persona.name == "Updated Persona"
        assert source_persona.description == "Replaced description"

    def test_update_other_workspace_returns_404(
        self, auth_client, other_workspace_persona
    ):
        _, hidden_persona = other_workspace_persona

        response = auth_client.put(
            f"/simulate/api/personas/{hidden_persona.id}/",
            {
                "name": "Leaked Persona",
                "description": "Should not apply",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        hidden_persona.refresh_from_db()
        assert hidden_persona.name == "Other Workspace Persona"
        assert hidden_persona.description == "Persona owned by a different workspace"

    def test_destroy_soft_deletes_persona(self, auth_client, source_persona):
        response = auth_client.delete(
            f"/simulate/api/personas/{source_persona.id}/"
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT
        source_persona.refresh_from_db()
        assert source_persona.deleted is True
        assert source_persona.deleted_at is not None

    def test_destroy_other_workspace_returns_404(
        self, auth_client, other_workspace_persona
    ):
        _, hidden_persona = other_workspace_persona

        response = auth_client.delete(
            f"/simulate/api/personas/{hidden_persona.id}/"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        hidden_persona.refresh_from_db()
        assert hidden_persona.deleted is False
        assert hidden_persona.deleted_at is None


@pytest.mark.integration
@pytest.mark.api
class TestPersonaActionRoutes:
    def test_system_personas_returns_system_list(
        self, auth_client, system_persona, source_persona
    ):
        response = auth_client.get("/simulate/api/personas/system/")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] is True
        listed_ids = {row["id"] for row in data["result"]}
        assert str(system_persona.id) in listed_ids
        assert str(source_persona.id) not in listed_ids

    def test_system_personas_scope_is_org_neutral(
        self, auth_client, system_persona, other_workspace_persona
    ):
        _, hidden_persona = other_workspace_persona

        response = auth_client.get("/simulate/api/personas/system/")

        assert response.status_code == status.HTTP_200_OK
        listed = {row["id"] for row in response.json()["result"]}
        assert str(system_persona.id) in listed
        assert str(hidden_persona.id) not in listed

    def test_workspace_personas_returns_workspace_list(
        self, auth_client, source_persona, system_persona
    ):
        response = auth_client.get("/simulate/api/personas/workspace/")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        listed_ids = {row["id"] for row in data["result"]}
        assert str(source_persona.id) in listed_ids
        assert str(system_persona.id) not in listed_ids

    def test_workspace_personas_excludes_other_workspace(
        self, auth_client, source_persona, other_workspace_persona
    ):
        _, hidden_persona = other_workspace_persona

        response = auth_client.get("/simulate/api/personas/workspace/")

        assert response.status_code == status.HTTP_200_OK
        listed_ids = {row["id"] for row in response.json()["result"]}
        assert str(source_persona.id) in listed_ids
        assert str(hidden_persona.id) not in listed_ids

    def test_field_options_returns_choice_shape(self, auth_client):
        response = auth_client.get("/simulate/api/personas/field-options/")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] is True
        result = data["result"]
        expected_keys = {
            "gender_choices",
            "age_group_choices",
            "location_choices",
            "profession_choices",
            "personality_choices",
            "communication_style_choices",
            "accent_choices",
            "language_choices",
            "conversation_speed_choices",
            "tone_choices",
            "verbosity_choices",
            "punctuation_choices",
            "emoji_usage_choices",
            "slang_usage_choices",
            "typos_frequency_choices",
            "regional_mix_choices",
        }
        assert expected_keys.issubset(result.keys())
        assert isinstance(result["gender_choices"], list)
        assert result["gender_choices"]
        first_choice = result["gender_choices"][0]
        assert "value" in first_choice
        assert "label" in first_choice

    def test_duplicate_viewset_action_success(
        self, auth_client, source_persona
    ):
        response = auth_client.post(
            f"/simulate/api/personas/{source_persona.id}/duplicate/",
            {"name": "Duplicated Persona"},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        body = response.json()
        assert body["status"] is True
        assert body["result"]["name"] == "Duplicated Persona"
        assert Persona.no_workspace_objects.filter(
            name="Duplicated Persona",
            persona_type=Persona.PersonaType.WORKSPACE,
        ).exists()

    def test_duplicate_viewset_action_other_workspace_returns_404(
        self, auth_client, other_workspace_persona
    ):
        _, hidden_persona = other_workspace_persona

        response = auth_client.post(
            f"/simulate/api/personas/{hidden_persona.id}/duplicate/",
            {"name": "Cross Workspace Duplicate"},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert not Persona.no_workspace_objects.filter(
            name="Cross Workspace Duplicate"
        ).exists()
