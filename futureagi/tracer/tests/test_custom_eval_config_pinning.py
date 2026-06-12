"""Regression tests for task eval version pinning (PR #811).

1. Pin set → resolved version is the pinned one, not default.
2. Cross-template pin → rejected by serializer.
3. Pinned version deleted → model property returns None (fallback to default).
"""

import uuid

import pytest
from rest_framework.test import APIClient

from tfc.middleware.workspace_context import set_workspace_context


@pytest.fixture
def organization(db):
    from accounts.models.organization import Organization

    return Organization.objects.create(name="Test Org Pinning")


@pytest.fixture
def user(db, organization):
    from accounts.models import User

    return User.objects.create_user(
        email=f"pin-test-{uuid.uuid4().hex[:6]}@example.com",
        password="testpass123",
        name="Pin Test User",
        organization=organization,
    )


@pytest.fixture
def workspace(db, organization, user):
    from accounts.models.workspace import Workspace

    return Workspace.objects.create(
        name="Default Workspace",
        organization=organization,
        is_default=True,
        created_by=user,
    )


@pytest.fixture
def project(db, organization, workspace):
    from tracer.models.project import Project

    return Project.objects.create(
        name="Test Project",
        organization=organization,
        workspace=workspace,
        model_type="Numeric",
        trace_type="observe",
    )


@pytest.fixture
def auth_client(user, workspace):
    client = APIClient()
    client.force_authenticate(user=user)
    set_workspace_context(workspace=workspace, organization=user.organization)
    return client


@pytest.fixture
def template_a(db, organization, workspace):
    from model_hub.models.evals_metric import EvalTemplate

    return EvalTemplate.objects.create(
        name=f"template-a-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        criteria="Test criteria A",
        config={"rule_prompt": "Prompt A", "eval_type_id": "CustomPromptEvaluator"},
    )


@pytest.fixture
def template_b(db, organization, workspace):
    from model_hub.models.evals_metric import EvalTemplate

    return EvalTemplate.objects.create(
        name=f"template-b-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        criteria="Test criteria B",
        config={"rule_prompt": "Prompt B", "eval_type_id": "CustomPromptEvaluator"},
    )


@pytest.fixture
def version_a(db, template_a, organization, workspace):
    from model_hub.models.evals_metric import EvalTemplateVersion

    v = EvalTemplateVersion.objects.create_version(
        eval_template=template_a,
        config_snapshot={"rule_prompt": "Pinned prompt A"},
        criteria="Pinned criteria A",
        model="gpt-4",
        user=None,
        organization=organization,
        workspace=workspace,
    )
    v.is_default = True
    v.save(update_fields=["is_default"])
    return v


@pytest.fixture
def version_b(db, template_b, organization, workspace):
    from model_hub.models.evals_metric import EvalTemplateVersion

    return EvalTemplateVersion.objects.create_version(
        eval_template=template_b,
        config_snapshot={"rule_prompt": "Version B prompt"},
        criteria="Version B criteria",
        model="gpt-4",
        user=None,
        organization=organization,
        workspace=workspace,
    )


@pytest.mark.django_db
class TestCustomEvalConfigPinning:
    def test_pinned_version_number_returns_correct_value(
        self, project, template_a, version_a
    ):
        """Pin set → pinned_version_number returns the version's number."""
        from tracer.models.custom_eval_config import CustomEvalConfig

        config = CustomEvalConfig.objects.create(
            eval_template=template_a,
            project=project,
            name="test-pinned",
            pinned_version=version_a,
        )
        assert config.pinned_version_number == version_a.version_number

    def test_unpinned_version_number_returns_none(self, project, template_a):
        """No pin → pinned_version_number returns None."""
        from tracer.models.custom_eval_config import CustomEvalConfig

        config = CustomEvalConfig.objects.create(
            eval_template=template_a,
            project=project,
            name="test-unpinned",
            pinned_version=None,
        )
        assert config.pinned_version_number is None

    def test_cross_template_pin_rejected(
        self, project, template_a, version_b
    ):
        """Pinning a version from template B onto a config for template A is rejected."""
        from tracer.serializers.custom_eval_config import CustomEvalConfigSerializer

        data = {
            "eval_template": template_a.id,
            "project": project.id,
            "name": "cross-template-test",
            "pinned_version": version_b.id,
        }
        serializer = CustomEvalConfigSerializer(data=data)
        assert not serializer.is_valid()
        assert "pinned_version" in serializer.errors

    def test_deleted_version_pin_rejected(
        self, project, template_a, version_a
    ):
        """Pinning a soft-deleted version is rejected by the serializer."""
        from model_hub.models.evals_metric import EvalTemplateVersion
        from tracer.models.custom_eval_config import CustomEvalConfig
        from tracer.serializers.custom_eval_config import CustomEvalConfigSerializer

        # Create a config first, then soft-delete the version and try to pin it
        config = CustomEvalConfig.objects.create(
            eval_template=template_a,
            project=project,
            name="deleted-version-test",
        )

        EvalTemplateVersion.all_objects.filter(id=version_a.id).update(deleted=True)
        version_a.refresh_from_db()

        serializer = CustomEvalConfigSerializer(
            instance=config,
            data={"pinned_version": version_a.id},
            partial=True,
        )
        # The FK field will reject the soft-deleted version since
        # BaseModelManager filters deleted=True out of the queryset
        assert not serializer.is_valid()
        assert "pinned_version" in serializer.errors

    def test_deleted_pinned_version_returns_none(
        self, project, template_a, version_a
    ):
        """When the pinned version is deleted, SET_NULL makes it None."""
        from model_hub.models.evals_metric import EvalTemplateVersion
        from tracer.models.custom_eval_config import CustomEvalConfig

        config = CustomEvalConfig.objects.create(
            eval_template=template_a,
            project=project,
            name="test-delete-fallback",
            pinned_version=version_a,
        )
        assert config.pinned_version_number == version_a.version_number

        # Hard-delete the version row → SET_NULL triggers
        EvalTemplateVersion.all_objects.filter(id=version_a.id).delete()
        config.refresh_from_db()
        assert config.pinned_version is None
        assert config.pinned_version_number is None
