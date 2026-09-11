"""Tests for get_project_eval_configs — PG-native eval-config discovery."""
import pytest

from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.project import Project
from tracer.utils.helper import get_project_eval_configs


@pytest.mark.django_db
def test_returns_non_deleted_configs_for_project(project, eval_template):
    keep = CustomEvalConfig.objects.create(
        project=project, eval_template=eval_template, name="keep"
    )
    CustomEvalConfig.objects.create(
        project=project, eval_template=eval_template, name="gone", deleted=True
    )

    configs, ids = get_project_eval_configs(project.id)

    assert [c.id for c in configs] == [keep.id]
    assert ids == [str(keep.id)]


@pytest.mark.django_db
def test_excludes_other_projects_and_empty_case(project, eval_template):
    other_project = Project.objects.create(
        name="Other Project",
        organization=project.organization,
        workspace=project.workspace,
        model_type=project.model_type,
        trace_type="experiment",
    )
    CustomEvalConfig.objects.create(
        project=other_project, eval_template=eval_template, name="foreign"
    )

    configs, ids = get_project_eval_configs(project.id)

    assert configs == []
    assert ids == []
