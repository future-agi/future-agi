"""Local context preflight uses real scope, catalog and immutable historical data."""

import json
from uuid import uuid4

import pytest
from django.db import transaction

from model_hub.models import HubSkill, HubSkillContextQuarantine, HubSkillJob
from model_hub.models.api_key import ApiKey
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.types import Actor
from model_hub.services.skills_hub.v11.types import SkillPrincipal, SkillScope
from model_hub.services.skills_hub.v12.testing import (
    prepare_context_test_plan,
    preview_context_test,
)
from model_hub.services.skills_hub.versions import persist_immutable_version_locked

pytestmark = pytest.mark.django_db


@pytest.fixture
def principal(user, organization, workspace):
    return SkillPrincipal(
        SkillScope(organization.id, workspace.id), user.id, None, "human"
    )


@pytest.fixture
def context(user, organization, workspace, settings):
    settings.SKILLS_VERIFIED_MODELS = ["gpt-4o-mini"]
    ApiKey.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        provider="openai",
        key="synthetic-test-value",
    )
    skill = HubSkill.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        name="synthetic-context",
        kind="agent_context",
    )
    descriptor = {
        "schema": 1,
        "kind": "agent_context",
        "name": skill.name,
        "description": "Synthetic standing policy",
        "entrypoint": "AGENTS.md",
        "owned_paths": ["AGENTS.md"],
        "links": [],
    }
    for number in range(1, 7):
        with transaction.atomic():
            locked = HubSkill.no_workspace_objects.select_for_update().get(pk=skill.pk)
            version, _ = persist_immutable_version_locked(
                skill=locked,
                actor=Actor(user, None),
                files=[
                    {
                        "path": "AGENTS.md",
                        "content": f"Synthetic policy version {number}.",
                    },
                    {
                        "path": ".futureagi/context.json",
                        "content": json.dumps(
                            descriptor, sort_keys=True, separators=(",", ":")
                        ),
                    },
                ],
                note="Synthetic fixture",
                parent_number=number - 1 or None,
                origin_number=number - 1 or None,
                description=descriptor["description"],
                frontmatter={},
                checks={
                    "summary": {"errors": 0, "warnings": 0, "infos": 0},
                    "items": [],
                },
                source={"type": "test"},
            )
    skill.refresh_from_db()
    return skill, version


def configuration(version):
    return {
        "harness": "contexts-text-v1.2",
        "candidate": {
            "kind": "version",
            "version": version.number,
            "digest": version.digest,
        },
        "cases": {
            "kind": "ad_hoc",
            "cases": [
                {
                    "id": str(uuid4()),
                    "title": "Synthetic case",
                    "source_group_id": "synthetic",
                    "prompt": "Explain the synthetic request.",
                    "fixtures": [],
                    "expected_trigger": None,
                    "checks": [
                        {
                            "id": str(uuid4()),
                            "kind": "contains",
                            "critical": True,
                            "config": {"text": "safe", "case_sensitive": False},
                        }
                    ],
                    "reviewed": True,
                }
            ],
        },
        "baselines": [{"kind": "version", "version": 4}],
        "competitors": [],
        "model": {
            "model_id": "gpt-4o-mini",
            "temperature": 0,
            "max_output_tokens": 1024,
        },
        "judge": None,
        "profile": "quick",
        "repetitions": 1,
        "limits": {"max_total_tokens": 10000, "max_estimated_cost_usd": "1.00"},
        "purpose": "regression",
        "parent_run_id": None,
    }


def test_historical_preview_pins_v4_not_latest_v6_and_creates_no_job(
    context, principal, monkeypatch
):
    import litellm

    async def no_provider(*args, **kwargs):
        raise AssertionError("Local preflight must not contact a provider")

    monkeypatch.setattr(litellm, "acompletion", no_provider)
    skill, version = context
    config = configuration(version)
    preview, snapshot = prepare_context_test_plan(
        principal=principal, context_id=skill.id, configuration=config
    )
    assert preview["candidate"]["version"] == 6
    assert preview["reference"]["version"] == 4
    assert preview["production"] is None
    assert snapshot["reference"]["files"][1]["content"] == "Synthetic policy version 4."
    assert preview["attempt_count"] == 2
    assert preview["max_judge_calls"] == 0
    assert not HubSkillJob.objects.exists()
    assert (
        preview_context_test(
            principal=principal, context_id=skill.id, configuration=config
        )["fingerprint"]
        == preview["fingerprint"]
    )


def test_context_preflight_rechecks_historical_quarantine(context, principal, user):
    skill, version = context
    historical = skill.versions.get(number=4)
    HubSkillContextQuarantine.objects.create(
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        version=historical,
        revision=1,
        active=True,
        reason="Synthetic quarantine",
        actor=user,
    )
    with pytest.raises(SkillsHubError) as error:
        preview_context_test(
            principal=principal,
            context_id=skill.id,
            configuration=configuration(version),
        )
    assert error.value.code == "context_quarantined"
    assert not HubSkillJob.objects.exists()


def test_worker_hook_rechecks_frozen_reference_after_admission(
    context, principal, user
):
    from model_hub.services.skills_hub.v12.testing import (
        recheck_context_snapshot_access,
    )

    skill, version = context
    _, snapshot = prepare_context_test_plan(
        principal=principal, context_id=skill.id, configuration=configuration(version)
    )
    recheck_context_snapshot_access(scope=principal.scope, snapshot=snapshot)
    HubSkillContextQuarantine.objects.create(
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        version=skill.versions.get(number=4),
        revision=1,
        active=True,
        reason="Synthetic late quarantine",
        actor=user,
    )
    with pytest.raises(SkillsHubError) as error:
        recheck_context_snapshot_access(scope=principal.scope, snapshot=snapshot)
    assert error.value.code == "context_quarantined"


def test_worker_hook_checks_draft_accepted_children(
    principal, hub_skill, publish_skill, user
):
    from model_hub.services.skills_hub.v12.testing import (
        recheck_context_snapshot_access,
    )

    child = publish_skill(hub_skill).version
    snapshot = {
        "candidate": {
            "version_id": None,
            "descriptor": {
                "links": [
                    {
                        "skill_id": str(hub_skill.id),
                        "version_id": str(child.id),
                        "version": child.number,
                        "digest": child.digest,
                    }
                ]
            },
        }
    }
    recheck_context_snapshot_access(scope=principal.scope, snapshot=snapshot)
    HubSkillContextQuarantine.objects.create(
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        version=child,
        revision=1,
        active=True,
        reason="Synthetic child quarantine",
        actor=user,
    )
    with pytest.raises(SkillsHubError) as error:
        recheck_context_snapshot_access(scope=principal.scope, snapshot=snapshot)
    assert error.value.code == "context_quarantined"


def test_missing_production_has_no_latest_fallback(context, principal):
    skill, version = context
    config = configuration(version)
    config["baselines"] = [{"kind": "production"}]
    with pytest.raises(SkillsHubError) as error:
        preview_context_test(
            principal=principal, context_id=skill.id, configuration=config
        )
    assert error.value.code == "label_not_found"


def test_context_preflight_changes_when_the_environment_changes(context, principal):
    skill, version = context
    config = configuration(version)
    original = preview_context_test(
        principal=principal, context_id=skill.id, configuration=config
    )
    config["model"]["temperature"] = 0.5
    changed = preview_context_test(
        principal=principal, context_id=skill.id, configuration=config
    )
    assert original["fingerprint"] != changed["fingerprint"]
    assert original["candidate"] == changed["candidate"]
