"""Synthetic retained releases using the foundation persistence and real principals."""

import json
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import transaction
from django.utils import timezone

from model_hub.models.skills_hub import HubSkill
from model_hub.models.skills_hub_v12 import HubSkillContextDependency
from model_hub.services.skills_hub.v11.types import SkillPrincipal, SkillScope
from model_hub.services.skills_hub.v12.consumers import create_binding
from model_hub.services.skills_hub.v12.credentials import (
    authenticate_credential,
    issue_credential,
)
from model_hub.services.skills_hub.versions import persist_immutable_version_locked


@pytest.fixture(autouse=True)
def consumption_enabled(settings):
    settings.SKILLS_CONTEXT_CONSUMPTION_ENABLED = True


@pytest.fixture
def principal(user, organization, workspace):
    return SkillPrincipal(
        SkillScope(organization.pk, workspace.pk), user.pk, None, "human"
    )


@pytest.fixture
def context(organization, workspace, user):
    return HubSkill._base_manager.create(
        organization=organization,
        workspace=workspace,
        name="synthetic-context",
        kind="agent_context",
        description="Synthetic policy",
        tags=[],
        created_by=user,
        protected_label_names=[],
    )


@pytest.fixture
def context_version(hub_actor):
    def publish(context, *, body="Follow the reviewed synthetic policy.", child=None):
        links = []
        files = [{"path": "AGENTS.md", "content": body}]
        if child:
            mount = f"skills/{child.skill.name}"
            links = [
                {
                    "mount": mount,
                    "skill_id": str(child.skill_id),
                    "requested": {"version": child.number},
                    "version": child.number,
                    "version_id": str(child.pk),
                    "digest": child.digest,
                }
            ]
            files += [
                {"path": f"{mount}/{file.path}", "content": file.blob.content}
                for file in child.files.select_related("blob")
            ]
        descriptor = {
            "schema": 1,
            "kind": "agent_context",
            "name": context.name,
            "description": context.description,
            "entrypoint": "AGENTS.md",
            "owned_paths": ["AGENTS.md"],
            "links": links,
        }
        files.append(
            {
                "path": ".futureagi/context.json",
                "content": json.dumps(
                    descriptor, sort_keys=True, separators=(",", ":")
                ),
            }
        )
        with transaction.atomic():
            locked = HubSkill._base_manager.select_for_update().get(pk=context.pk)
            version, _ = persist_immutable_version_locked(
                skill=locked,
                actor=hub_actor,
                files=files,
                note="",
                parent_number=locked.latest_version_number or None,
                origin_number=None,
                description=context.description,
                frontmatter={},
                checks={
                    "items": [],
                    "summary": {"errors": 0, "warnings": 0, "infos": 0},
                },
                source={},
            )
            if child:
                HubSkillContextDependency.objects.create(
                    organization_id=context.organization_id,
                    workspace_id=context.workspace_id,
                    parent_version=version,
                    child_version=child,
                    child_skill_id=child.skill_id,
                    mount=links[0]["mount"],
                    requested_selector={"version": child.number},
                )
        return version

    return publish


@pytest.fixture
def binding(principal, context):
    return create_binding(
        principal=principal,
        context_id=context.pk,
        idempotency_key=uuid4(),
        request={
            "name": "Synthetic runtime",
            "kind": "runtime",
            "selector": {"label": "production"},
            "update_mode": "new_thread",
            "profile": "deepagents",
            "profile_version": "test-1",
            "repository": None,
            "expected_report_interval_seconds": 60,
        },
    )


@pytest.fixture
def credential(principal):
    def issue(version, *, purpose="consume", binding_id=None):
        result = issue_credential(
            principal=principal,
            idempotency_key=uuid4(),
            request={
                "purpose": purpose,
                "grants": (
                    [
                        {
                            "kind": version.skill.kind,
                            "resource_id": str(version.skill_id),
                            "labels": ["production"],
                            "version_ids": [],
                        }
                    ]
                    if purpose == "consume"
                    else []
                ),
                "binding_ids": [str(binding_id)] if binding_id else [],
                "expires_at": timezone.now() + timedelta(days=1),
            },
        )
        return authenticate_credential(token=result["secret"])

    return issue
