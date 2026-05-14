"""
Tests for Phase 1: Eval List & Navigation API.

Covers:
- Unit tests for derive_eval_type, derive_output_type, get_created_by_name
- E2E API tests for EvalTemplateListView and EvalTemplateBulkDeleteView
"""

import pytest
from django.utils import timezone

from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate
from model_hub.utils.eval_list import (
    derive_eval_type,
    derive_output_type,
    get_created_by_name,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def system_eval_template(organization, workspace):
    """System-owned eval template (LLM type, Pass/Fail output)."""
    return EvalTemplate.no_workspace_objects.create(
        name="hallucination_check",
        organization=None,
        workspace=None,
        owner=OwnerChoices.SYSTEM.value,
        eval_type="llm",
        config={"output": "Pass/Fail", "eval_type_id": "CustomPromptEvaluator"},
        eval_tags=["llm", "safety"],
        visible_ui=True,
    )


@pytest.fixture
def user_eval_template(organization, workspace, user):
    """User-owned eval template (code type, score output)."""
    return EvalTemplate.no_workspace_objects.create(
        name="user_custom_eval",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        eval_type="code",
        config={"output": "score", "eval_type_id": "Regex"},
        eval_tags=["code", "function"],
        visible_ui=True,
    )


@pytest.fixture
def agent_eval_template(organization, workspace):
    """User-owned agent eval template."""
    return EvalTemplate.no_workspace_objects.create(
        name="agent_quality_eval",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        eval_type="agent",
        config={"output": "choices", "eval_type_id": "AgentEval"},
        eval_tags=["agent", "agentic"],
        visible_ui=True,
    )


@pytest.fixture
def multiple_eval_templates(organization, workspace):
    """Create multiple eval templates for pagination/filter tests."""
    templates = []
    for i in range(5):
        templates.append(
            EvalTemplate.no_workspace_objects.create(
                name=f"eval_template_{i}",
                organization=organization,
                workspace=workspace,
                owner=OwnerChoices.USER.value,
                config={"output": "score"},
                eval_tags=["llm"],
                visible_ui=True,
            )
        )
    return templates


# =============================================================================
# Unit Tests: derive_eval_type
# =============================================================================


@pytest.mark.unit
class TestDeriveEvalType:
    def test_llm_from_eval_type_id(self, system_eval_template):
        """Template with LLM eval_type_id should return 'llm'."""
        assert derive_eval_type(system_eval_template) == "llm"

    def test_code_from_eval_type_id(self, user_eval_template):
        """Template with function eval_type_id should return 'code'."""
        assert derive_eval_type(user_eval_template) == "code"

    def test_agent_from_tags(self, agent_eval_template):
        """Template with agent tags should return 'agent'."""
        assert derive_eval_type(agent_eval_template) == "agent"

    def test_default_llm(self, db, organization):
        """Template with no tags or eval_type_id should default to 'llm'."""
        template = EvalTemplate.no_workspace_objects.create(
            name="bare_template",
            organization=organization,
            owner=OwnerChoices.USER.value,
            config={},
            eval_tags=[],
            visible_ui=True,
        )
        assert derive_eval_type(template) == "llm"

    def test_code_from_tags_only(self, db, organization):
        """Template with code tags but no eval_type_id should return 'code'."""
        template = EvalTemplate.no_workspace_objects.create(
            name="code_tagged",
            organization=organization,
            owner=OwnerChoices.USER.value,
            config={},
            eval_tags=["function"],
            visible_ui=True,
        )
        assert derive_eval_type(template) == "code"

    def test_deterministic_evaluator_is_llm(self, db, organization):
        """DeterministicEvaluator is LLM-based (uses LLM with structured output), not code."""
        template = EvalTemplate.no_workspace_objects.create(
            name="deterministic_test",
            organization=organization,
            owner=OwnerChoices.USER.value,
            config={"eval_type_id": "DeterministicEvaluator"},
            eval_tags=["FUTURE_EVALS", "LLMS"],
            visible_ui=True,
        )
        assert derive_eval_type(template) == "llm"

    def test_precision_at_k_is_code(self, db, organization):
        """PrecisionAtK is a function/code eval (deterministic scoring)."""
        template = EvalTemplate.no_workspace_objects.create(
            name="precision_test",
            organization=organization,
            owner=OwnerChoices.USER.value,
            config={"eval_type_id": "PrecisionAtK"},
            eval_tags=["FUNCTION", "RAG"],
            visible_ui=True,
        )
        assert derive_eval_type(template) == "code"


# =============================================================================
# Unit Tests: derive_output_type
# =============================================================================


@pytest.mark.unit
class TestDeriveOutputType:
    def test_pass_fail(self, system_eval_template):
        """Config output 'Pass/Fail' -> 'pass_fail'."""
        assert derive_output_type(system_eval_template) == "pass_fail"

    def test_score(self, user_eval_template):
        """Config output 'score' -> 'percentage'."""
        assert derive_output_type(user_eval_template) == "percentage"

    def test_choices(self, agent_eval_template):
        """Config output 'choices' -> 'deterministic'."""
        assert derive_output_type(agent_eval_template) == "deterministic"

    def test_empty_config(self, db, organization):
        """Empty config should default to 'percentage'."""
        template = EvalTemplate.no_workspace_objects.create(
            name="empty_config",
            organization=organization,
            owner=OwnerChoices.USER.value,
            config={},
            eval_tags=[],
            visible_ui=True,
        )
        assert derive_output_type(template) == "percentage"


# =============================================================================
# Unit Tests: get_created_by_name
# =============================================================================


@pytest.mark.unit
class TestGetCreatedByName:
    def test_system_owner(self, system_eval_template):
        """System-owned template should return 'System'."""
        assert get_created_by_name(system_eval_template) == "System"

    def test_user_owner_without_evaluator(self, user_eval_template, organization):
        """User-owned template without creator metadata falls back to org name."""
        assert get_created_by_name(user_eval_template) == organization.display_name


# =============================================================================
# E2E API Tests: EvalTemplateListView
# =============================================================================


@pytest.mark.e2e
@pytest.mark.django_db
class TestEvalTemplateListAPI:
    url = "/model-hub/eval-templates/list/"

    def test_list_default(self, auth_client, system_eval_template, user_eval_template):
        """Default list returns both system and user templates."""
        response = auth_client.post(self.url, {}, format="json")
        assert response.status_code == 200
        data = response.data
        assert data["status"] is True
        result = data["result"]
        assert "items" in result
        assert "total" in result
        assert "page" in result
        assert "page_size" in result
        # Should find at least the user template (system templates may not have org)
        assert result["total"] >= 1

    def test_list_response_shape(self, auth_client, user_eval_template):
        """Verify each item has all required fields.

        Note: response.data returns snake_case keys (pre-rendering).
        The camelCase middleware converts at HTTP level, not in test client.
        """
        response = auth_client.post(self.url, {}, format="json")
        assert response.status_code == 200
        items = response.data["result"]["items"]
        assert len(items) >= 1

        item = items[0]
        required_fields = [
            "id",
            "name",
            "template_type",
            "eval_type",
            "output_type",
            "owner",
            "created_by_name",
            "version_count",
            "current_version",
            "last_updated",
            "thirty_day_chart",
            "thirty_day_error_rate",
            "thirty_day_run_count",
            "tags",
        ]
        for field in required_fields:
            assert field in item, f"Missing field: {field}"

    def test_list_search(self, auth_client, user_eval_template, agent_eval_template):
        """Search filter returns matching templates."""
        response = auth_client.post(self.url, {"search": "user_custom"}, format="json")
        assert response.status_code == 200
        items = response.data["result"]["items"]
        for item in items:
            assert "user_custom" in item["name"].lower()

    def test_list_owner_filter_user(
        self, auth_client, system_eval_template, user_eval_template
    ):
        """owner_filter='user' excludes system templates."""
        response = auth_client.post(self.url, {"owner_filter": "user"}, format="json")
        assert response.status_code == 200
        items = response.data["result"]["items"]
        for item in items:
            assert item["owner"] == "user"

    def test_list_owner_filter_system(
        self, auth_client, system_eval_template, user_eval_template
    ):
        """owner_filter='system' excludes user templates."""
        response = auth_client.post(self.url, {"owner_filter": "system"}, format="json")
        assert response.status_code == 200
        items = response.data["result"]["items"]
        for item in items:
            assert item["owner"] == "system"

    def test_list_pagination(self, auth_client, multiple_eval_templates):
        """Pagination returns correct page size and total."""
        response = auth_client.post(
            self.url, {"page": 0, "page_size": 2}, format="json"
        )
        assert response.status_code == 200
        result = response.data["result"]
        assert len(result["items"]) <= 2
        assert result["total"] >= 5

    def test_list_pagination_page_2(self, auth_client, multiple_eval_templates):
        """Second page returns different items."""
        r1 = auth_client.post(self.url, {"page": 0, "page_size": 2}, format="json")
        r2 = auth_client.post(self.url, {"page": 1, "page_size": 2}, format="json")
        assert r1.status_code == 200
        assert r2.status_code == 200
        ids1 = {item["id"] for item in r1.data["result"]["items"]}
        ids2 = {item["id"] for item in r2.data["result"]["items"]}
        # Pages should not overlap
        assert ids1.isdisjoint(ids2)

    def test_list_sort_by_name_asc(self, auth_client, multiple_eval_templates):
        """sort_by='name', sort_order='asc' returns alphabetical order."""
        response = auth_client.post(
            self.url,
            {"sort_by": "name", "sort_order": "asc", "page_size": 100},
            format="json",
        )
        assert response.status_code == 200
        items = response.data["result"]["items"]
        names = [item["name"] for item in items]
        assert names == sorted(names)

    def test_list_invalid_page_size(self, auth_client):
        """Invalid page_size returns 400."""
        response = auth_client.post(self.url, {"page_size": -1}, format="json")
        assert response.status_code == 400

    def test_list_invalid_page_size_too_large(self, auth_client):
        """page_size > 100 returns 400."""
        response = auth_client.post(self.url, {"page_size": 200}, format="json")
        assert response.status_code == 400

    def test_list_eval_type_fields(
        self, auth_client, user_eval_template, agent_eval_template
    ):
        """Verify eval_type is correctly derived for different templates."""
        response = auth_client.post(self.url, {"page_size": 100}, format="json")
        assert response.status_code == 200
        items = response.data["result"]["items"]
        items_by_name = {item["name"]: item for item in items}

        if "user_custom_eval" in items_by_name:
            assert items_by_name["user_custom_eval"]["eval_type"] == "code"
        if "agent_quality_eval" in items_by_name:
            assert items_by_name["agent_quality_eval"]["eval_type"] == "agent"

    def test_list_creator_fallback_uses_org_name(
        self, auth_client, user_eval_template, organization
    ):
        response = auth_client.post(
            self.url, {"search": user_eval_template.name}, format="json"
        )
        assert response.status_code == 200
        items = response.data["result"]["items"]
        assert len(items) == 1
        assert items[0]["created_by_name"] == organization.display_name

    def test_list_created_by_filter_matches_org_name(
        self, auth_client, user_eval_template, organization
    ):
        response = auth_client.post(
            self.url,
            {
                "owner_filter": "user",
                "filters": {"created_by": [organization.display_name]},
                "page_size": 100,
            },
            format="json",
        )
        assert response.status_code == 200
        item_names = {item["name"] for item in response.data["result"]["items"]}
        assert user_eval_template.name in item_names

    def test_list_version_defaults_no_versions(self, auth_client, user_eval_template):
        """Templates without any version rows fall back to V1 / count=1."""
        response = auth_client.post(self.url, {}, format="json")
        assert response.status_code == 200
        for item in response.data["result"]["items"]:
            assert item["current_version"] == "V1"
            assert item["version_count"] == 1

    def test_list_reflects_default_version(
        self, auth_client, organization, workspace, user
    ):
        """List should reflect the version flagged is_default, not always V1."""
        from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion

        template = EvalTemplate.no_workspace_objects.create(
            name="multi_version_eval",
            organization=organization,
            workspace=workspace,
            owner=OwnerChoices.USER.value,
            config={"output": "score"},
            eval_tags=["llm"],
            visible_ui=True,
        )
        EvalTemplateVersion.objects.create(
            eval_template=template,
            version_number=1,
            is_default=False,
            organization=organization,
            workspace=workspace,
        )
        EvalTemplateVersion.objects.create(
            eval_template=template,
            version_number=2,
            is_default=True,
            organization=organization,
            workspace=workspace,
        )
        EvalTemplateVersion.objects.create(
            eval_template=template,
            version_number=3,
            is_default=False,
            organization=organization,
            workspace=workspace,
        )

        response = auth_client.post(
            self.url, {"search": "multi_version_eval"}, format="json"
        )
        assert response.status_code == 200
        items = response.data["result"]["items"]
        assert len(items) == 1
        assert items[0]["current_version"] == "V2"
        assert items[0]["version_count"] == 3

    def test_list_template_type_single(self, auth_client, user_eval_template):
        """All templates should show 'single' until Phase 7."""
        response = auth_client.post(self.url, {}, format="json")
        assert response.status_code == 200
        for item in response.data["result"]["items"]:
            assert item["template_type"] == "single"


# =============================================================================
# E2E API Tests: Negation Filters (TH-4359)
# =============================================================================


@pytest.mark.e2e
@pytest.mark.django_db
class TestEvalListNegationFilters:
    """Tests for ``_not`` filter variants added in TH-4359."""

    url = "/model-hub/eval-templates/list/"

    # --- eval_type_not ---

    def test_eval_type_not_excludes_matching(
        self, auth_client, system_eval_template, user_eval_template, agent_eval_template
    ):
        """eval_type_not=['code'] should exclude code evals, keep llm and agent."""
        response = auth_client.post(
            self.url,
            {"filters": {"eval_type_not": ["code"]}, "page_size": 100},
            format="json",
        )
        assert response.status_code == 200
        items = response.data["result"]["items"]
        eval_types = {item["eval_type"] for item in items}
        assert "code" not in eval_types

    def test_eval_type_not_keeps_non_matching(
        self, auth_client, system_eval_template, user_eval_template, agent_eval_template
    ):
        """eval_type_not=['llm'] should still return code and agent evals."""
        response = auth_client.post(
            self.url,
            {"filters": {"eval_type_not": ["llm"]}, "page_size": 100},
            format="json",
        )
        assert response.status_code == 200
        items = response.data["result"]["items"]
        item_names = {item["name"] for item in items}
        # user_eval_template is code, agent_eval_template is agent — both should survive
        assert user_eval_template.name in item_names
        assert agent_eval_template.name in item_names

    def test_eval_type_not_multiple(
        self, auth_client, system_eval_template, user_eval_template, agent_eval_template
    ):
        """Excluding multiple types leaves only the remaining type."""
        response = auth_client.post(
            self.url,
            {"filters": {"eval_type_not": ["llm", "agent"]}, "page_size": 100},
            format="json",
        )
        assert response.status_code == 200
        items = response.data["result"]["items"]
        for item in items:
            assert item["eval_type"] not in ("llm", "agent")

    # --- created_by_not ---

    def test_created_by_not_excludes_user(
        self, auth_client, organization, workspace, user
    ):
        """created_by_not should exclude evals created by the named user."""
        from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion

        keep_tmpl = EvalTemplate.no_workspace_objects.create(
            name="keep_this_eval",
            organization=organization,
            workspace=workspace,
            owner=OwnerChoices.USER.value,
            config={"output": "score"},
            visible_ui=True,
        )
        exclude_tmpl = EvalTemplate.no_workspace_objects.create(
            name="exclude_this_eval",
            organization=organization,
            workspace=workspace,
            owner=OwnerChoices.USER.value,
            config={"output": "score"},
            visible_ui=True,
        )
        # Create versions with different creators
        EvalTemplateVersion.objects.create(
            eval_template=keep_tmpl,
            version_number=1,
            is_default=True,
            organization=organization,
            workspace=workspace,
            created_by=user,
        )
        from accounts.models import User

        other_user = User.objects.create_user(
            email="other@example.com",
            password="test",
            name="OtherUser",
        )
        EvalTemplateVersion.objects.create(
            eval_template=exclude_tmpl,
            version_number=1,
            is_default=True,
            organization=organization,
            workspace=workspace,
            created_by=other_user,
        )

        response = auth_client.post(
            self.url,
            {
                "owner_filter": "user",
                "filters": {"created_by_not": ["OtherUser"]},
                "page_size": 100,
            },
            format="json",
        )
        assert response.status_code == 200
        item_names = {item["name"] for item in response.data["result"]["items"]}
        assert "exclude_this_eval" not in item_names
        assert "keep_this_eval" in item_names

    # --- Serializer accepts _not fields ---

    def test_serializer_accepts_eval_type_not(self, auth_client):
        """eval_type_not should be accepted without 400."""
        response = auth_client.post(
            self.url,
            {"filters": {"eval_type_not": ["llm"]}},
            format="json",
        )
        assert response.status_code == 200

    def test_serializer_accepts_output_type_not(self, auth_client):
        """output_type_not should be accepted without 400."""
        response = auth_client.post(
            self.url,
            {"filters": {"output_type_not": ["pass_fail"]}},
            format="json",
        )
        assert response.status_code == 200

    def test_serializer_accepts_created_by_not(self, auth_client):
        """created_by_not should be accepted without 400."""
        response = auth_client.post(
            self.url,
            {"filters": {"created_by_not": ["SomeUser"]}},
            format="json",
        )
        assert response.status_code == 200

    def test_serializer_rejects_invalid_eval_type_not(self, auth_client):
        """Invalid choice in eval_type_not should return 400."""
        response = auth_client.post(
            self.url,
            {"filters": {"eval_type_not": ["invalid_type"]}},
            format="json",
        )
        assert response.status_code == 400


# =============================================================================
# E2E API Tests: EvalTemplateBulkDeleteView
# =============================================================================


@pytest.mark.e2e
@pytest.mark.django_db
class TestEvalTemplateBulkDeleteAPI:
    url = "/model-hub/eval-templates/bulk-delete/"

    def test_delete_user_templates(self, auth_client, user_eval_template):
        """Deleting user-owned templates should succeed."""
        response = auth_client.post(
            self.url,
            {"template_ids": [str(user_eval_template.id)]},
            format="json",
        )
        assert response.status_code == 200
        assert response.data["result"]["deleted_count"] == 1

        # Verify template is soft-deleted
        user_eval_template.refresh_from_db()
        assert user_eval_template.deleted is True

    def test_delete_system_templates_rejected(self, auth_client, system_eval_template):
        """System templates should not be deleted."""
        response = auth_client.post(
            self.url,
            {"template_ids": [str(system_eval_template.id)]},
            format="json",
        )
        assert response.status_code == 200
        # deleted_count should be 0 since system templates are filtered out
        assert response.data["result"]["deleted_count"] == 0

        # Verify template is still alive
        system_eval_template.refresh_from_db()
        assert system_eval_template.deleted is False

    def test_delete_empty_list(self, auth_client):
        """Empty template_ids list should return validation error."""
        response = auth_client.post(
            self.url,
            {"template_ids": []},
            format="json",
        )
        assert response.status_code == 400

    def test_delete_mixed_templates(
        self, auth_client, system_eval_template, user_eval_template
    ):
        """Bulk delete with mixed system/user templates only deletes user ones."""
        response = auth_client.post(
            self.url,
            {
                "template_ids": [
                    str(system_eval_template.id),
                    str(user_eval_template.id),
                ]
            },
            format="json",
        )
        assert response.status_code == 200
        assert response.data["result"]["deleted_count"] == 1

        system_eval_template.refresh_from_db()
        assert system_eval_template.deleted is False

        user_eval_template.refresh_from_db()
        assert user_eval_template.deleted is True
