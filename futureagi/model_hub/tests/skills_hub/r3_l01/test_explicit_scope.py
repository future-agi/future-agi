"""Explicit token and recovery scope must not inherit the caller's ambient workspace."""

import pytest

from model_hub.models.skills_hub import HubSkill
from model_hub.selectors.skills_hub_v12 import resources_for_scope
from model_hub.services.skills_hub.v11.types import SkillScope
from tfc.middleware.workspace_context import workspace_context

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("archived", [False, True])
def test_context_selector_uses_explicit_scope_even_with_another_ambient_workspace(
    organization, workspace, hub_other_workspace, user, archived
):
    context = HubSkill.no_workspace_objects.create(
        organization=organization,
        workspace=hub_other_workspace,
        kind="agent_context",
        name="explicit-context-scope",
        description="Synthetic scope boundary",
        deleted=archived,
    )
    with workspace_context(workspace=workspace, organization=organization, user=user):
        rows = resources_for_scope(
            scope=SkillScope(organization.pk, hub_other_workspace.pk),
            kind="agent_context",
            archived=archived,
        )
        assert list(rows.values_list("id", flat=True)) == [context.pk]
        assert not resources_for_scope(
            scope=SkillScope(organization.pk, workspace.pk),
            kind="agent_context",
            archived=archived,
        ).exists()
