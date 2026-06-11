"""Comprehensive tests for NodeTemplateViewSet."""

import uuid

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from agent_playground.models.choices import PortMode
from agent_playground.models.node_template import NodeTemplate

AUTH_REQUIRED_STATUS_CODES = (
    status.HTTP_401_UNAUTHORIZED,
    status.HTTP_403_FORBIDDEN,
)


@pytest.fixture
def api_client():
    """Create an API client."""
    return APIClient()


@pytest.fixture
def authenticated_client(api_client, user):
    """Create an authenticated API client."""
    api_client.force_authenticate(user=user)
    return api_client


class TestNodeTemplateList:
    """Tests for GET /agent-playground/node-templates/"""

    def test_list_returns_all_templates(
        self, authenticated_client, node_template, dynamic_node_template
    ):
        """Test that list returns all templates."""
        url = reverse("node-template-list")
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] is True
        assert len(response.data["result"]["node_templates"]) == 2

    def test_list_returns_lightweight_fields(self, authenticated_client, node_template):
        """Test that list returns only lightweight fields."""
        url = reverse("node-template-list")
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        templates = response.data["result"]["node_templates"]
        template_data = templates[0]

        # Should have lightweight fields
        assert "id" in template_data
        assert "name" in template_data
        assert "display_name" in template_data
        assert "description" in template_data
        assert "icon" in template_data
        assert "categories" in template_data

        # Should NOT have detail fields
        assert "input_definition" not in template_data
        assert "output_definition" not in template_data
        assert "config_schema" not in template_data

    def test_list_requires_authentication(self, api_client, node_template):
        """Test that list requires authentication."""
        url = reverse("node-template-list")
        response = api_client.get(url)

        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_list_empty_when_no_templates(self, authenticated_client):
        """Test that list returns empty when no templates exist."""
        url = reverse("node-template-list")
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["result"]["node_templates"] == []

    def test_list_returns_all_three_mode_types(
        self,
        authenticated_client,
        node_template,
        dynamic_node_template,
        extensible_node_template,
    ):
        """Test that list returns templates of all port modes."""
        url = reverse("node-template-list")
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        templates = response.data["result"]["node_templates"]
        assert len(templates) == 3

        names = {t["name"] for t in templates}
        assert "test_template" in names
        assert "dynamic_template" in names
        assert "extensible_template" in names

    def test_list_response_format(self, authenticated_client, node_template):
        """Test that list response has correct format."""
        url = reverse("node-template-list")
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert "status" in response.data
        assert "result" in response.data
        assert "node_templates" in response.data["result"]
        assert response.data["status"] is True

    def test_list_with_multiple_categories(self, authenticated_client, db):
        """Test list with templates having multiple categories."""
        NodeTemplate.no_workspace_objects.create(
            name="multi_cat",
            display_name="Multi Category",
            categories=["AI", "LLM", "Text", "Utility"],
        )
        url = reverse("node-template-list")
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        template = next(
            t
            for t in response.data["result"]["node_templates"]
            if t["name"] == "multi_cat"
        )
        assert len(template["categories"]) == 4

    def test_list_preserves_category_order(self, authenticated_client, db):
        """Test that category order is preserved."""
        NodeTemplate.no_workspace_objects.create(
            name="ordered_cat",
            display_name="Ordered Categories",
            categories=["Zebra", "Alpha", "Beta"],
        )
        url = reverse("node-template-list")
        response = authenticated_client.get(url)

        template = next(
            t
            for t in response.data["result"]["node_templates"]
            if t["name"] == "ordered_cat"
        )
        assert template["categories"] == ["Zebra", "Alpha", "Beta"]

    def test_list_includes_icon_url(self, authenticated_client, db):
        """Test that list includes icon URL when present."""
        NodeTemplate.no_workspace_objects.create(
            name="with_icon",
            display_name="With Icon",
            icon="https://example.com/icon.svg",
            categories=["test"],
        )
        url = reverse("node-template-list")
        response = authenticated_client.get(url)

        template = next(
            t
            for t in response.data["result"]["node_templates"]
            if t["name"] == "with_icon"
        )
        assert template["icon"] == "https://example.com/icon.svg"


class TestNodeTemplateRetrieve:
    """Tests for GET /agent-playground/node-templates/{id}/"""

    def test_retrieve_returns_full_details(self, authenticated_client, node_template):
        """Test that retrieve returns full template details."""
        url = reverse("node-template-detail", kwargs={"pk": str(node_template.id)})
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["status"] is True

        template_data = response.data["result"]
        assert template_data["id"] == str(node_template.id)
        assert template_data["name"] == node_template.name
        assert template_data["display_name"] == node_template.display_name
        assert template_data["input_definition"] == node_template.input_definition
        assert template_data["output_definition"] == node_template.output_definition
        assert template_data["input_mode"] == node_template.input_mode
        assert template_data["output_mode"] == node_template.output_mode
        assert template_data["config_schema"] == node_template.config_schema

    def test_retrieve_requires_authentication(self, api_client, node_template):
        """Test that retrieve requires authentication."""
        url = reverse("node-template-detail", kwargs={"pk": str(node_template.id)})
        response = api_client.get(url)

        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_retrieve_nonexistent_returns_404(self, authenticated_client):
        """Test that retrieve returns 404 for nonexistent template."""
        url = reverse("node-template-detail", kwargs={"pk": str(uuid.uuid4())})
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_retrieve_dynamic_template(
        self, authenticated_client, dynamic_node_template
    ):
        """Test retrieve of dynamic port mode template."""
        url = reverse(
            "node-template-detail", kwargs={"pk": str(dynamic_node_template.id)}
        )
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        template_data = response.data["result"]
        assert template_data["input_mode"] == PortMode.DYNAMIC
        assert template_data["output_mode"] == PortMode.DYNAMIC
        assert template_data["input_definition"] == []
        assert template_data["output_definition"] == []

    def test_retrieve_extensible_template(
        self, authenticated_client, extensible_node_template
    ):
        """Test retrieve of extensible port mode template."""
        url = reverse(
            "node-template-detail", kwargs={"pk": str(extensible_node_template.id)}
        )
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        template_data = response.data["result"]
        assert template_data["input_mode"] == PortMode.EXTENSIBLE
        assert template_data["output_mode"] == PortMode.EXTENSIBLE
        assert len(template_data["input_definition"]) == 1
        assert len(template_data["output_definition"]) == 1

    def test_retrieve_with_complex_config_schema(self, authenticated_client, db):
        """Test retrieve with complex config schema."""
        config_schema = {
            "type": "object",
            "properties": {
                "model": {"type": "string", "enum": ["gpt-4", "gpt-3.5-turbo"]},
                "temperature": {"type": "number", "minimum": 0, "maximum": 2},
                "max_tokens": {"type": "integer", "maximum": 4096},
            },
            "required": ["model"],
        }
        template = NodeTemplate.no_workspace_objects.create(
            name="complex_config",
            display_name="Complex Config",
            categories=["LLM"],
            config_schema=config_schema,
        )
        url = reverse("node-template-detail", kwargs={"pk": str(template.id)})
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["result"]["config_schema"] == config_schema

    def test_retrieve_with_complex_port_definitions(self, authenticated_client, db):
        """Test retrieve with complex port definitions."""
        input_def = [
            {
                "key": "messages",
                "data_schema": {
                    "type": "array",
                    "items": {"type": "object"},
                },
                "required": True,
            },
        ]
        output_def = [
            {
                "key": "response",
                "data_schema": {"type": "object"},
            },
        ]
        template = NodeTemplate.no_workspace_objects.create(
            name="complex_ports",
            display_name="Complex Ports",
            categories=["LLM"],
            input_definition=input_def,
            output_definition=output_def,
        )
        url = reverse("node-template-detail", kwargs={"pk": str(template.id)})
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["result"]["input_definition"] == input_def
        assert response.data["result"]["output_definition"] == output_def

    def test_retrieve_response_format(self, authenticated_client, node_template):
        """Test that retrieve response has correct format."""
        url = reverse("node-template-detail", kwargs={"pk": str(node_template.id)})
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert "status" in response.data
        assert "result" in response.data
        assert response.data["status"] is True
        # Result should be a single object, not wrapped in a list
        assert isinstance(response.data["result"], dict)

    def test_retrieve_invalid_uuid_format(self, authenticated_client):
        """Test retrieve with invalid UUID format returns 404."""
        url = reverse("node-template-detail", kwargs={"pk": "not-a-valid-uuid"})
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestNodeTemplateReadOnly:
    """Tests to ensure NodeTemplateViewSet is read-only."""

    def test_post_not_allowed(self, authenticated_client):
        """Test that POST is not allowed."""
        url = reverse("node-template-list")
        response = authenticated_client.post(url, data={"name": "test"})

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_put_not_allowed(self, authenticated_client, node_template):
        """Test that PUT is not allowed."""
        url = reverse("node-template-detail", kwargs={"pk": str(node_template.id)})
        response = authenticated_client.put(url, data={"name": "updated"})

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_patch_not_allowed(self, authenticated_client, node_template):
        """Test that PATCH is not allowed."""
        url = reverse("node-template-detail", kwargs={"pk": str(node_template.id)})
        response = authenticated_client.patch(url, data={"name": "updated"})

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_delete_not_allowed(self, authenticated_client, node_template):
        """Test that DELETE is not allowed."""
        url = reverse("node-template-detail", kwargs={"pk": str(node_template.id)})
        response = authenticated_client.delete(url)

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    def test_post_with_valid_data_not_allowed(self, authenticated_client):
        """Test that POST is not allowed even with valid data."""
        url = reverse("node-template-list")
        data = {
            "name": "new_template",
            "display_name": "New Template",
            "categories": ["test"],
            "input_definition": [],
            "output_definition": [],
            "input_mode": PortMode.DYNAMIC,
            "output_mode": PortMode.DYNAMIC,
            "config_schema": {},
        }
        response = authenticated_client.post(url, data=data, format="json")

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


class TestNodeTemplateOrganizationIndependence:
    """Test that NodeTemplates are organization-independent."""

    def test_templates_visible_to_all_authenticated_users(
        self, api_client, node_template, db
    ):
        """Test that templates are visible regardless of organization."""
        from accounts.models.organization import Organization
        from accounts.models.user import User

        # Create a different org and user
        other_org = Organization.objects.create(name="Other Organization")
        other_user = User.objects.create_user(
            email="other@example.com",
            password="testpassword",
            name="Other User",
            organization=other_org,
        )

        api_client.force_authenticate(user=other_user)
        url = reverse("node-template-list")
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        templates = response.data["result"]["node_templates"]
        template_ids = [t["id"] for t in templates]
        assert str(node_template.id) in template_ids

    def test_template_detail_visible_to_different_org(
        self, api_client, node_template, db
    ):
        """Test that template detail is visible to users from different org."""
        from accounts.models.organization import Organization
        from accounts.models.user import User

        other_org = Organization.objects.create(name="Another Organization")
        other_user = User.objects.create_user(
            email="another@example.com",
            password="testpassword",
            name="Another User",
            organization=other_org,
        )

        api_client.force_authenticate(user=other_user)
        url = reverse("node-template-detail", kwargs={"pk": str(node_template.id)})
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["result"]["id"] == str(node_template.id)


class TestNodeTemplateEdgeCases:
    """Test edge cases for NodeTemplateViewSet."""

    def test_retrieve_with_empty_description(self, authenticated_client, db):
        """Test retrieve when template has empty description."""
        template = NodeTemplate.no_workspace_objects.create(
            name="empty_desc",
            display_name="Empty Description",
            description="",
            categories=["test"],
        )
        url = reverse("node-template-detail", kwargs={"pk": str(template.id)})
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["result"]["description"] == ""

    def test_retrieve_with_null_icon(self, authenticated_client, db):
        """Test retrieve when template has null icon."""
        template = NodeTemplate.no_workspace_objects.create(
            name="null_icon",
            display_name="Null Icon",
            icon=None,
            categories=["test"],
        )
        url = reverse("node-template-detail", kwargs={"pk": str(template.id)})
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["result"]["icon"] is None

    def test_list_with_empty_categories(self, authenticated_client, db):
        """Test list with template having empty categories."""
        NodeTemplate.no_workspace_objects.create(
            name="empty_cat",
            display_name="Empty Categories",
            categories=[],
        )
        url = reverse("node-template-list")
        response = authenticated_client.get(url)

        template = next(
            t
            for t in response.data["result"]["node_templates"]
            if t["name"] == "empty_cat"
        )
        assert template["categories"] == []

    def test_list_large_number_of_templates(self, authenticated_client, db):
        """Test list with a large number of templates."""
        for i in range(50):
            NodeTemplate.no_workspace_objects.create(
                name=f"template_{i}",
                display_name=f"Template {i}",
                categories=["test"],
            )

        url = reverse("node-template-list")
        response = authenticated_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["result"]["node_templates"]) == 50
