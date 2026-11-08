"""Cloud-only service qualification; not GitHub or installed-client evidence."""

import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from django.db import close_old_connections, connection, transaction
from django.utils import timezone

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import WorkspaceMembership
from model_hub.models.skills_hub import HubSkill
from model_hub.models.skills_hub_v12 import (
    HubSkillContextBinding,
    HubSkillContextQuarantine,
)
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.types import Actor
from model_hub.services.skills_hub.v11.types import SkillPrincipal, SkillScope
from model_hub.services.skills_hub.v12 import campaigns as service
from model_hub.services.skills_hub.versions import persist_immutable_version_locked
from model_hub.tests.skills_hub.r3_l12.model_contract import (
    HubSkillContextCampaign,
    HubSkillContextCampaignTarget,
)
from tfc.constants.levels import Level

pytestmark = pytest.mark.django_db(transaction=True)
DIGEST = "sha256:" + "a" * 64


@pytest.fixture(scope="session", autouse=True)
def campaign_tables(django_db_setup, django_db_blocker):
    # Test-only contract tables: existing migrations are not changed or bypassed.
    with django_db_blocker.unblock():
        existing = connection.introspection.table_names()
        created = []
        with connection.schema_editor() as schema:
            for model in (HubSkillContextCampaign, HubSkillContextCampaignTarget):
                if model._meta.db_table not in existing:
                    schema.create_model(model)
                    created.append(model)
    yield
    with django_db_blocker.unblock(), connection.schema_editor() as schema:
        for model in reversed(created):
            schema.delete_model(model)


@pytest.fixture
def actor(user, workspace, organization):
    return SkillPrincipal(
        SkillScope(organization.pk, workspace.pk), user.pk, None, "human"
    )


@pytest.fixture
def release(actor, user):
    skill = HubSkill.all_objects.create(
        **service._scope(actor),
        name="campaign-context",
        kind="agent_context",
        description="Synthetic repository policy",
        created_by=user,
    )
    files = [
        {"path": "AGENTS.md", "content": "Preserve local policy.\n"},
        {
            "path": ".futureagi/context.json",
            "content": '{"schema":1,"kind":"agent_context","name":"campaign-context","description":"Synthetic repository policy","entrypoint":"AGENTS.md","owned_paths":["AGENTS.md"],"links":[]}',
        },
    ]
    with transaction.atomic():
        version, _ = persist_immutable_version_locked(
            skill=skill,
            actor=Actor(user, None),
            files=files,
            note="",
            parent_number=None,
            origin_number=None,
            description=skill.description,
            frontmatter={},
            checks={"items": [], "summary": {"errors": 0, "warnings": 0, "infos": 0}},
            source={},
        )
    return version


@pytest.fixture
def binding(actor, release):
    return HubSkillContextBinding.objects.create(
        **service._scope(actor),
        context=release.skill,
        name="synthetic-repository",
        kind="repository",
        selector={"label": "production"},
        update_mode="repository_lock",
        profile="codex",
        profile_version="1",
        repository={
            "provider": "github",
            "repository_id": "100",
            "default_branch": "main",
            "project_scope": "",
        },
        expected_report_interval_seconds=3600,
    )


@pytest.fixture
def enabled(settings):
    settings.SKILLS_CONTEXT_CAMPAIGNS_ENABLED = True


def create(actor, binding, *, key=None, targets=None, parent=None):
    return service.create_campaign(
        principal=actor,
        request={
            "name": "Synthetic rollout",
            "targets": (
                targets
                if targets is not None
                else [{"binding_id": str(binding.pk), "version": 1}]
            ),
            "parent_campaign_id": parent,
        },
        idempotency_key=key or uuid4(),
    )


def plan(binding, release, revision=1):
    value = {
        "campaign_revision": revision,
        "binding_revision": binding.revision,
        "repository_id": binding.repository["repository_id"],
        "base_commit": "a" * 40,
        "previous_lock_digest": None,
        "desired_version_id": str(release.pk),
        "materialization_digest": DIGEST,
        "plan_digest": DIGEST,
        "changes": [
            {
                "path": "AGENTS.md",
                "action": "replace",
                "before_sha256": "a" * 64,
                "after_sha256": "b" * 64,
                "ownership": "adopted",
                "diff_preview": "Keep local notes; add reviewed managed instructions.",
            }
        ],
        "local_review_required": True,
        "blockers": [],
    }
    value["plan_digest"] = service.target_plan_digest(value)
    return value


def prepare(actor, binding, release):
    campaign = create(actor, binding)
    value = plan(binding, release)
    saved = service.record_target_plan(
        principal=actor,
        campaign_id=campaign["id"],
        binding_id=binding.pk,
        request=value,
    )
    current = service.get_campaign(principal=actor, campaign_id=campaign["id"])
    service.authorize_campaign(
        principal=actor,
        campaign_id=campaign["id"],
        revision=saved["revision"],
        expected_manifest_digest=current["manifest_digest"],
        decisions=[
            {
                "binding_id": str(binding.pk),
                "plan_digest": value["plan_digest"],
                "decision": "include",
                "reason": "Reviewed preserved local notes.",
            }
        ],
        acknowledge_repository_writes=True,
    )
    return campaign, value


def claim(actor, campaign, binding, value, key=None):
    return service.claim_campaign_target(
        principal=actor,
        campaign_id=campaign["id"],
        binding_id=binding.pk,
        idempotency_key=key or uuid4(),
        plan_digest=value["plan_digest"],
    )


def report(actor, campaign, binding, claimed, state="unknown"):
    return service.record_campaign_attempt(
        principal=actor,
        campaign_id=campaign["id"],
        binding_id=binding.pk,
        request={
            "attempt_id": claimed["attempt_id"],
            "claim_generation": claimed["claim_generation"],
            "state": state,
            "repository_commit": "b" * 40 if state in {"pr_open", "merged"} else None,
            "pull_request_number": 42 if state in {"pr_open", "merged"} else None,
            "reason": "Synthetic transport receipt",
        },
    )


def test_default_switch_blocks_before_persistence(actor, binding):
    with pytest.raises(SkillsHubError, match="not enabled") as error:
        create(actor, binding)
    assert error.value.code == "skills_unavailable"
    assert HubSkillContextCampaign.objects.count() == 0


def test_exact_create_replay_precedes_binding_resolution(enabled, actor, binding):
    key = uuid4()
    first = create(actor, binding, key=key)
    binding.retired_at = timezone.now()
    binding.save(update_fields=["retired_at"])
    assert create(actor, binding, key=key) == first
    with pytest.raises(SkillsHubError) as error:
        create(
            actor,
            binding,
            key=key,
            targets=[{"binding_id": str(binding.pk), "version": 2}],
        )
    assert error.value.code == "idempotency_conflict"


@pytest.mark.parametrize(
    "targets",
    [
        [],
        [{"binding_id": str(uuid4()), "version": True}],
        [{"binding_id": str(uuid4()), "version": 1}] * 101,
    ],
)
def test_invalid_target_inventory(enabled, actor, binding, targets):
    with pytest.raises(SkillsHubError) as error:
        create(actor, binding, targets=targets)
    assert error.value.code == "invalid_campaign"


def test_foreign_scope_and_wrong_kind_do_not_leak(
    enabled, actor, binding, hub_other_workspace
):
    foreign = SkillPrincipal(
        SkillScope(actor.scope.organization_id, hub_other_workspace.pk),
        actor.user_id,
        None,
        "human",
    )
    with pytest.raises(SkillsHubError) as error:
        create(foreign, binding)
    assert error.value.code == "resource_not_found"
    binding.context.kind = "skill"
    binding.context.save(update_fields=["kind"])
    with pytest.raises(SkillsHubError) as error:
        create(actor, binding)
    assert error.value.code == "resource_not_found"
    assert HubSkillContextCampaign.objects.count() == 0


def test_system_principal_never_owns_campaign(enabled, actor, binding):
    system = SkillPrincipal(actor.scope, None, uuid4(), "system_key")
    with pytest.raises(SkillsHubError) as error:
        create(system, binding)
    assert error.value.code == "principal_not_supported"


@pytest.mark.parametrize(
    "path",
    [
        "../AGENTS.md",
        "/AGENTS.md",
        ".github/workflows/test.yml",
        ".claude/settings.json",
        ".mcp.json",
        "package.json",
        ".futureagi/context/a/.git/config",
    ],
)
def test_host_control_and_path_escape_refused(binding, release, path):
    value = plan(binding, release)
    value["changes"][0]["path"] = path
    value["plan_digest"] = service.target_plan_digest(value)
    with pytest.raises(SkillsHubError) as error:
        service.validate_target_plan(value)
    assert error.value.code == "destination_forbidden"


def test_plan_digest_covers_base_preimages_and_review(binding, release):
    value = plan(binding, release)
    for mutate in (
        lambda row: row.update(base_commit="b" * 40),
        lambda row: row["changes"][0].update(before_sha256="c" * 64),
        lambda row: row["changes"][0].update(diff_preview="Different review"),
    ):
        changed = copy.deepcopy(value)
        mutate(changed)
        with pytest.raises(SkillsHubError) as error:
            service.validate_target_plan(changed)
        assert error.value.code == "target_plan_changed"
    assert service.validate_target_plan(value) == value


def test_adoption_and_delete_are_not_silent(binding, release):
    value = plan(binding, release)
    value["local_review_required"] = False
    value["plan_digest"] = service.target_plan_digest(value)
    with pytest.raises(SkillsHubError):
        service.validate_target_plan(value)
    value["local_review_required"] = True
    value["changes"][0].update(action="remove", after_sha256=None)
    value["plan_digest"] = service.target_plan_digest(value)
    with pytest.raises(SkillsHubError):
        service.validate_target_plan(value)


def test_fixed_40_target_fixture_and_independent_stages(
    enabled, actor, binding, release
):
    bindings = [binding]
    for index in range(1, 40):
        bindings.append(
            HubSkillContextBinding.objects.create(
                **service._scope(actor),
                context=release.skill,
                name=f"repository-{index:02}",
                kind="repository",
                selector=(
                    {"version": 99} if 32 <= index < 36 else {"label": "production"}
                ),
                update_mode="repository_lock",
                profile="codex",
                profile_version="1",
                repository={
                    "provider": "github",
                    "repository_id": str(100 + index),
                    "default_branch": "main",
                    "project_scope": "",
                },
                expected_report_interval_seconds=3600,
            )
        )
    campaign = create(
        actor,
        binding,
        targets=[{"binding_id": str(row.pk), "version": 1} for row in bindings],
    )
    revision, decisions, plans = 1, [], {}
    for index, row in enumerate(bindings):
        value = plan(row, release, revision)
        if index in {36, 37, 38}:
            value["blockers"] = [
                "local_modifications" if index != 38 else "repository_unavailable"
            ]
        value["plan_digest"] = service.target_plan_digest(value)
        revision = service.record_target_plan(
            principal=actor,
            campaign_id=campaign["id"],
            binding_id=row.pk,
            request=value,
        )["revision"]
        plans[row.pk] = value
        decisions.append(
            {
                "binding_id": str(row.pk),
                "plan_digest": value["plan_digest"],
                "decision": "include" if index < 32 or index == 39 else "exclude",
                "reason": (
                    "Reviewed local segment"
                    if index < 32 or index == 39
                    else "Retain pin or local exception"
                ),
            }
        )
    current = service.get_campaign(principal=actor, campaign_id=campaign["id"])
    with pytest.raises(SkillsHubError):
        service.authorize_campaign(
            principal=actor,
            campaign_id=campaign["id"],
            revision=revision,
            expected_manifest_digest=current["manifest_digest"],
            decisions=decisions[:-1],
            acknowledge_repository_writes=True,
        )
    authorized = service.authorize_campaign(
        principal=actor,
        campaign_id=campaign["id"],
        revision=revision,
        expected_manifest_digest=current["manifest_digest"],
        decisions=decisions,
        acknowledge_repository_writes=True,
    )
    assert authorized["target_count"] == 40
    pages = [
        service.list_campaign_targets(
            principal=actor, campaign_id=campaign["id"], page=page
        )
        for page in (1, 2)
    ]
    assert {row["binding_id"] for page in pages for row in page["results"]} == {
        str(row.pk) for row in bindings
    }
    assert (
        sum(row["decision"] == "exclude" for page in pages for row in page["results"])
        == 7
    )
    first = claim(actor, campaign, binding, plans[binding.pk])
    report(actor, campaign, binding, first, "merged")
    observed = service.campaign_target_observations(
        principal=actor, campaign_id=campaign["id"], binding_id=binding.pk
    )
    assert observed["installed"] is None and observed["instructions_delivered"] is None
    last = bindings[-1]
    sent = claim(actor, campaign, last, plans[last.pk])
    report(actor, campaign, last, sent)
    reconcile = claim(actor, campaign, last, plans[last.pk])
    assert reconcile["mode"] == "reconcile_only"
    assert (
        reconcile["branch"] == sent["branch"] and reconcile["marker"] == sent["marker"]
    )
    report(actor, campaign, last, reconcile, "pr_open")
    with pytest.raises(SkillsHubError):
        claim(actor, campaign, last, plans[last.pk])


def test_pause_stops_new_work_but_accepts_inflight_receipt(
    enabled, actor, binding, release, settings
):
    campaign, value = prepare(actor, binding, release)
    sent = claim(actor, campaign, binding, value)
    current = service.get_campaign(principal=actor, campaign_id=campaign["id"])
    paused = service.change_campaign_status(
        principal=actor,
        campaign_id=campaign["id"],
        revision=current["revision"],
        action="pause",
        reason="Inspect Git outcome",
    )
    settings.SKILLS_CONTEXT_CAMPAIGNS_ENABLED = False
    receipt = report(actor, campaign, binding, sent)
    assert receipt["state"] == "unknown"
    with pytest.raises(SkillsHubError):
        service.change_campaign_status(
            principal=actor,
            campaign_id=campaign["id"],
            revision=paused["revision"],
            action="resume",
            reason="Resume",
        )
    assert (
        service.get_campaign(principal=actor, campaign_id=campaign["id"])["status"]
        == "paused"
    )


def test_lost_claim_never_issues_another_write_and_fences_stale_sender(
    enabled, actor, binding, release
):
    campaign, value = prepare(actor, binding, release)
    first = claim(actor, campaign, binding, value)
    target = HubSkillContextCampaignTarget.objects.get(campaign_id=campaign["id"])
    target.attempts[-1]["expires_at"] = (
        timezone.now() - timedelta(seconds=1)
    ).isoformat()
    target.save(update_fields=["attempts"])
    second = claim(actor, campaign, binding, value)
    assert second["mode"] == "reconcile_only" and second["claim_generation"] == 2
    with pytest.raises(SkillsHubError) as error:
        report(actor, campaign, binding, first, "pr_open")
    assert error.value.code == "receipt_conflict"
    receipt = report(actor, campaign, binding, second, "pr_open")
    assert report(actor, campaign, binding, second, "pr_open") == receipt
    with pytest.raises(SkillsHubError):
        report(actor, campaign, binding, second, "failed")


def test_claim_replay_recovers_original_without_renewed_write_authority(
    enabled, actor, binding, release
):
    campaign, value = prepare(actor, binding, release)
    key = uuid4()
    first = claim(actor, campaign, binding, value, key)
    binding.revision += 1
    binding.save(update_fields=["revision"])
    replay = claim(actor, campaign, binding, value, key)
    assert replay["attempt_id"] == first["attempt_id"]
    assert replay["expires_at"] == first["expires_at"]
    assert replay["mode"] == "reconcile_only" and replay["replay"] is True
    assert (
        len(
            HubSkillContextCampaignTarget.objects.get(
                campaign_id=campaign["id"]
            ).attempts
        )
        == 1
    )


def test_claim_race_only_one_execute_admission(enabled, actor, binding, release):
    campaign, value = prepare(actor, binding, release)
    barrier = Barrier(2)

    def worker():
        close_old_connections()
        barrier.wait()
        try:
            return claim(actor, campaign, binding, value)["mode"]
        except SkillsHubError as error:
            return error.code
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: worker(), range(2)))
    assert sorted(outcomes) == ["campaign_conflict", "execute"]


def test_binding_change_and_quarantine_rechecked_on_claim(
    enabled, actor, binding, release
):
    campaign, value = prepare(actor, binding, release)
    binding.revision += 1
    binding.save(update_fields=["revision"])
    with pytest.raises(SkillsHubError) as error:
        claim(actor, campaign, binding, value)
    assert error.value.code == "target_plan_changed"
    binding.revision -= 1
    binding.save(update_fields=["revision"])
    HubSkillContextQuarantine.objects.create(
        **service._scope(actor),
        version=release,
        revision=1,
        active=True,
        reason="Synthetic quarantine",
    )
    with pytest.raises(SkillsHubError) as error:
        claim(actor, campaign, binding, value)
    assert error.value.code == "context_quarantined"


def test_current_role_and_membership_rechecked(enabled, actor, binding, release):
    campaign, value = prepare(actor, binding, release)
    OrganizationMembership.no_workspace_objects.filter(
        user_id=actor.user_id, organization_id=actor.scope.organization_id
    ).update(is_active=False)
    with pytest.raises(SkillsHubError) as error:
        claim(actor, campaign, binding, value)
    assert error.value.code == "access_revoked"


def test_authorizer_role_loss_blocks_execution(enabled, actor, binding, release):
    campaign, value = prepare(actor, binding, release)
    OrganizationMembership.no_workspace_objects.filter(
        user_id=actor.user_id, organization_id=actor.scope.organization_id
    ).update(level=Level.WORKSPACE_MEMBER)
    WorkspaceMembership.no_workspace_objects.filter(
        user_id=actor.user_id, workspace_id=actor.scope.workspace_id
    ).update(level=Level.WORKSPACE_MEMBER)
    with pytest.raises(SkillsHubError) as error:
        claim(actor, campaign, binding, value)
    assert error.value.code == "permission_denied"


def test_unrelated_binding_does_not_invalidate_review(enabled, actor, binding, release):
    campaign, value = prepare(actor, binding, release)
    HubSkillContextBinding.objects.create(
        **service._scope(actor),
        context=release.skill,
        name="unrelated",
        kind="repository",
        selector={"label": "staging"},
        update_mode="repository_lock",
        profile="codex",
        profile_version="1",
        repository={
            "provider": "github",
            "repository_id": "200",
            "default_branch": "main",
            "project_scope": "",
        },
        expected_report_interval_seconds=3600,
    )
    assert claim(actor, campaign, binding, value)["mode"] == "execute"
    assert (
        service.get_campaign(principal=actor, campaign_id=campaign["id"])[
            "target_count"
        ]
        == 1
    )


def test_reverse_preview_is_not_an_automatic_reset(enabled, actor, binding, release):
    campaign, value = prepare(actor, binding, release)
    current = service.get_campaign(principal=actor, campaign_id=campaign["id"])
    reverse = service.preview_campaign_rollback(
        principal=actor, campaign_id=campaign["id"], revision=current["revision"]
    )
    assert reverse["executable"] is False
    change = reverse["targets"][0]["reverse_changes"][0]
    assert change["before_sha256"] == value["changes"][0]["after_sha256"]
    assert change["after_sha256"] == value["changes"][0]["before_sha256"]
    assert "current_repository_preimages_required" in reverse["targets"][0]["blockers"]
    child = create(actor, binding, parent=campaign["id"])
    assert child["parent_campaign_id"] == campaign["id"] and child["status"] == "draft"
    assert (
        HubSkillContextCampaignTarget.objects.get(campaign_id=child["id"]).plan is None
    )
