"""A native job's Skills scope is only ever its workspace scope."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.r3 import native_runs


def test_job_scope_carries_its_workspace():
    job = SimpleNamespace(organization_id=uuid4(), workspace_id=uuid4())
    scope = native_runs._scope(job)
    assert (scope.organization_id, scope.workspace_id) == (
        job.organization_id,
        job.workspace_id,
    )


def test_job_without_a_workspace_is_refused_not_scoped_to_none():
    job = SimpleNamespace(organization_id=uuid4(), workspace_id=None)
    with pytest.raises(SkillsHubError) as refused:
        native_runs._scope(job)
    assert (refused.value.code, refused.value.status) == ("resource_not_found", 404)
