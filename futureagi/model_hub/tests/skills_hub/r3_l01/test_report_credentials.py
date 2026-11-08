"""Report authority is confined to explicitly named live bindings, never content."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone

from model_hub.models.skills_hub import HubSkill
from model_hub.models.skills_hub_v12 import HubSkillContextBinding
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.v11.types import SkillPrincipal, SkillScope
from model_hub.services.skills_hub.v12.consumption import resolve_consumption
from model_hub.services.skills_hub.v12.credentials import (
    authenticate_credential,
    authorize_report_binding,
    issue_credential,
)

pytestmark = pytest.mark.django_db


def test_report_grant_does_not_read_content_or_other_bindings(
    organization, workspace, user
):
    context = HubSkill.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        kind="agent_context",
        name="report-only-context",
        description="Synthetic report scope",
    )
    binding = HubSkillContextBinding.objects.create(
        organization=organization,
        workspace=workspace,
        context=context,
        name="Synthetic runtime",
        kind="runtime",
        selector={"label": "production"},
        update_mode="new_thread",
        profile="synthetic",
        profile_version="1",
        expected_report_interval_seconds=60,
    )
    human = SkillPrincipal(
        SkillScope(organization.pk, workspace.pk), user.pk, None, "human"
    )
    issued = issue_credential(
        principal=human,
        request={
            "purpose": "report",
            "grants": [],
            "binding_ids": [str(binding.pk)],
            "expires_at": timezone.now() + timedelta(days=1),
        },
        idempotency_key=uuid4(),
    )
    principal = authenticate_credential(token=issued["secret"])
    assert (
        authorize_report_binding(principal=principal, binding_id=binding.pk).pk
        == binding.pk
    )
    with pytest.raises(SkillsHubError) as error:
        resolve_consumption(
            principal=principal, kind="agent_context", name=context.name
        )
    assert error.value.code == "credential_operation_forbidden"
    with pytest.raises(SkillsHubError) as error:
        authorize_report_binding(principal=principal, binding_id=uuid4())
    assert error.value.code == "resource_not_found"
    binding.retired_at = timezone.now()
    binding.save(update_fields=["retired_at"])
    with pytest.raises(SkillsHubError) as error:
        authorize_report_binding(principal=principal, binding_id=binding.pk)
    assert error.value.code == "resource_not_found"
