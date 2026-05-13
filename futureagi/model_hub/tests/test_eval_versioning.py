"""
Tests for Phase 5: Eval Template Versioning.
"""

import pytest

from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion


@pytest.fixture
def user_template(organization, workspace, user):
    return EvalTemplate.no_workspace_objects.create(
        name="versioned-eval",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "Pass/Fail"},
        eval_tags=["llm"],
        criteria="Check {{response}}",
        model="turing_large",
        visible_ui=True,
    )


# =============================================================================
# Unit: EvalTemplateVersionManager
# =============================================================================


@pytest.mark.unit
@pytest.mark.django_db
class TestVersionManager:
    def test_create_first_version(self, user_template, user, organization, workspace):
        v = EvalTemplateVersion.objects.create_version(
            eval_template=user_template,
            criteria="Check {{response}}",
            model="turing_large",
            user=user,
            organization=organization,
            workspace=workspace,
        )
        assert v.version_number == 1
        assert v.is_default is True
        assert v.criteria == "Check {{response}}"

    def test_create_second_version_increments(
        self, user_template, user, organization, workspace
    ):
        EvalTemplateVersion.objects.create_version(
            eval_template=user_template,
            criteria="V1",
            user=user,
            organization=organization,
        )
        v2 = EvalTemplateVersion.objects.create_version(
            eval_template=user_template,
            criteria="V2",
            user=user,
            organization=organization,
        )
        assert v2.version_number == 2

    def test_get_default(self, user_template, user, organization):
        v1 = EvalTemplateVersion.objects.create_version(
            eval_template=user_template,
            criteria="V1",
            user=user,
            organization=organization,
        )
        default = EvalTemplateVersion.objects.get_default(user_template)
        assert default.id == v1.id


# =============================================================================
# E2E: Version List API
# =============================================================================


@pytest.mark.e2e
@pytest.mark.django_db
class TestVersionListAPI:
    def _url(self, template_id):
        return f"/model-hub/eval-templates/{template_id}/versions/"

    def test_list_empty(self, auth_client, user_template):
        response = auth_client.get(self._url(user_template.id))
        assert response.status_code == 200
        result = response.data["result"]
        assert result["total"] == 0
        assert result["versions"] == []

    def test_list_with_versions(self, auth_client, user_template, user, organization):
        EvalTemplateVersion.objects.create_version(
            eval_template=user_template,
            criteria="V1",
            user=user,
            organization=organization,
        )
        EvalTemplateVersion.objects.create_version(
            eval_template=user_template,
            criteria="V2",
            user=user,
            organization=organization,
        )
        response = auth_client.get(self._url(user_template.id))
        assert response.status_code == 200
        result = response.data["result"]
        assert result["total"] == 2
        # Ordered by version_number desc
        assert result["versions"][0]["version_number"] == 2
        assert result["versions"][1]["version_number"] == 1

    def test_list_nonexistent_template(self, auth_client):
        response = auth_client.get(
            "/model-hub/eval-templates/00000000-0000-0000-0000-000000000000/versions/"
        )
        assert response.status_code == 404


# =============================================================================
# E2E: Version Create API
# =============================================================================


@pytest.mark.e2e
@pytest.mark.django_db
class TestVersionCreateAPI:
    def _url(self, template_id):
        return f"/model-hub/eval-templates/{template_id}/versions/create/"

    def test_create_version(self, auth_client, user_template):
        response = auth_client.post(self._url(user_template.id), {}, format="json")
        assert response.status_code == 200
        result = response.data["result"]
        assert result["version_number"] == 1
        assert result["is_default"] is True

    def test_create_multiple_versions(self, auth_client, user_template):
        r1 = auth_client.post(self._url(user_template.id), {}, format="json")
        r2 = auth_client.post(self._url(user_template.id), {}, format="json")
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.data["result"]["version_number"] == 1
        assert r2.data["result"]["version_number"] == 2
        # Latest should be default
        assert r2.data["result"]["is_default"] is True

    def test_create_version_with_overrides(self, auth_client, user_template):
        response = auth_client.post(
            self._url(user_template.id),
            {"criteria": "New instructions {{var}}", "model": "turing_flash"},
            format="json",
        )
        assert response.status_code == 200
        v = EvalTemplateVersion.objects.get(id=response.data["result"]["id"])
        assert v.criteria == "New instructions {{var}}"
        assert v.model == "turing_flash"

    def test_create_version_sets_new_default(self, auth_client, user_template):
        r1 = auth_client.post(self._url(user_template.id), {}, format="json")
        r2 = auth_client.post(self._url(user_template.id), {}, format="json")

        v1 = EvalTemplateVersion.objects.get(id=r1.data["result"]["id"])
        v2 = EvalTemplateVersion.objects.get(id=r2.data["result"]["id"])

        v1.refresh_from_db()
        assert v1.is_default is False
        assert v2.is_default is True

    def test_create_version_nonexistent_template(self, auth_client):
        response = auth_client.post(
            "/model-hub/eval-templates/00000000-0000-0000-0000-000000000000/versions/create/",
            {},
            format="json",
        )
        assert response.status_code == 404


@pytest.mark.unit
@pytest.mark.django_db
class TestVersionSnapshotColumns:
    def test_auto_capture_from_template(
        self, user_template, user, organization, workspace
    ):
        user_template.output_type_normalized = "pass_fail"
        user_template.pass_threshold = 0.7
        user_template.choice_scores = {"Yes": 1.0, "No": 0.0}
        user_template.error_localizer_enabled = True
        user_template.eval_tags = ["safety", "quality"]
        user_template.save()

        v = EvalTemplateVersion.objects.create_version(
            eval_template=user_template,
            criteria="X",
            model="turing_large",
            user=user,
            organization=organization,
            workspace=workspace,
        )
        assert v.output_type_normalized == "pass_fail"
        assert v.pass_threshold == 0.7
        assert v.choice_scores == {"Yes": 1.0, "No": 0.0}
        assert v.error_localizer_enabled is True
        assert v.eval_tags == ["safety", "quality"]

    def test_explicit_override_wins(
        self, user_template, user, organization, workspace
    ):
        user_template.pass_threshold = 0.9
        user_template.save()

        v = EvalTemplateVersion.objects.create_version(
            eval_template=user_template,
            criteria="X",
            model="turing_large",
            user=user,
            organization=organization,
            workspace=workspace,
            pass_threshold=0.3,
        )
        assert v.pass_threshold == 0.3

    def test_explicit_none_is_honored(
        self, user_template, user, organization, workspace
    ):
        user_template.choice_scores = {"Yes": 1.0}
        user_template.save()

        v = EvalTemplateVersion.objects.create_version(
            eval_template=user_template,
            criteria="X",
            model="turing_large",
            user=user,
            organization=organization,
            workspace=workspace,
            choice_scores=None,
        )
        assert v.choice_scores is None

    def test_eval_tags_is_list_copied(
        self, user_template, user, organization, workspace
    ):
        user_template.eval_tags = ["a", "b"]
        user_template.save()

        v = EvalTemplateVersion.objects.create_version(
            eval_template=user_template,
            criteria="X",
            model="turing_large",
            user=user,
            organization=organization,
            workspace=workspace,
        )
        user_template.eval_tags.append("c")
        user_template.save()
        v.refresh_from_db()
        assert v.eval_tags == ["a", "b"]


@pytest.mark.unit
@pytest.mark.django_db
class TestApplyVersionSnapshotToTemplate:
    def test_applies_non_null_fields(
        self, user_template, user, organization, workspace
    ):
        from model_hub.views.separate_evals import (
            _apply_version_snapshot_to_template,
        )

        v = EvalTemplateVersion.objects.create_version(
            eval_template=user_template,
            criteria="updated",
            model="turing_large",
            user=user,
            organization=organization,
            workspace=workspace,
            output_type_normalized="percentage",
            pass_threshold=0.6,
            eval_tags=["restored"],
        )

        user_template.output_type_normalized = "pass_fail"
        user_template.pass_threshold = 0.1
        user_template.eval_tags = ["drifted"]
        user_template.save()

        fields = _apply_version_snapshot_to_template(user_template, v)
        user_template.save(update_fields=fields)
        user_template.refresh_from_db()

        assert user_template.output_type_normalized == "percentage"
        assert user_template.pass_threshold == 0.6
        assert user_template.eval_tags == ["restored"]
        assert user_template.criteria == "updated"

    def test_skips_null_snapshot_fields(
        self, user_template, user, organization, workspace
    ):
        """NULL snapshot fields simulate a pre-migration-0091 version row;
        restore must preserve the template's current values."""
        from model_hub.views.separate_evals import (
            _apply_version_snapshot_to_template,
        )

        user_template.pass_threshold = 0.42
        user_template.eval_tags = ["keep-me"]
        user_template.save()

        v = EvalTemplateVersion.objects.create_version(
            eval_template=user_template,
            criteria="X",
            model="turing_large",
            user=user,
            organization=organization,
            workspace=workspace,
            output_type_normalized=None,
            pass_threshold=None,
            choice_scores=None,
            error_localizer_enabled=None,
            eval_tags=None,
        )

        fields = _apply_version_snapshot_to_template(user_template, v)
        user_template.save(update_fields=fields)
        user_template.refresh_from_db()

        assert user_template.pass_threshold == 0.42
        assert user_template.eval_tags == ["keep-me"]
        assert "pass_threshold" not in fields
        assert "eval_tags" not in fields

    def test_eval_tags_mutation_isolation_on_restore(
        self, user_template, user, organization, workspace
    ):
        from model_hub.views.separate_evals import (
            _apply_version_snapshot_to_template,
        )

        v = EvalTemplateVersion.objects.create_version(
            eval_template=user_template,
            criteria="X",
            model="turing_large",
            user=user,
            organization=organization,
            workspace=workspace,
            eval_tags=["a", "b"],
        )
        fields = _apply_version_snapshot_to_template(user_template, v)
        user_template.save(update_fields=fields)

        user_template.eval_tags.append("c")
        user_template.save()
        v.refresh_from_db()
        assert v.eval_tags == ["a", "b"]


@pytest.mark.unit
@pytest.mark.django_db
class TestOutputTypeNormalizedChoices:
    def test_accepts_valid_values(self, organization, workspace):
        from django.core.exceptions import ValidationError

        for value in ("pass_fail", "percentage", "deterministic"):
            t = EvalTemplate.no_workspace_objects.create(
                name=f"t-{value}",
                organization=organization,
                workspace=workspace,
                owner=OwnerChoices.USER.value,
                config={},
                eval_tags=[],
                criteria="X",
                model="turing_large",
                output_type_normalized=value,
            )
            try:
                t.full_clean()
            except ValidationError:
                raise AssertionError(
                    f"'{value}' should be a valid OutputTypeNormalized choice"
                )

    def test_rejects_invalid_value(self, organization, workspace):
        from django.core.exceptions import ValidationError

        with pytest.raises(ValidationError) as exc_info:
            EvalTemplate.no_workspace_objects.create(
                name="t-invalid",
                organization=organization,
                workspace=workspace,
                owner=OwnerChoices.USER.value,
                config={},
                eval_tags=[],
                criteria="X",
                model="turing_large",
                output_type_normalized="not_a_real_choice",
            )
        assert "output_type_normalized" in str(exc_info.value)
