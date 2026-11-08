"""Bounded exact review reads and observations that do not stale content review."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone

from model_hub.models.skills_hub_v12 import (
    HubSkillContextQuarantine,
    HubSkillContextReceipt,
)
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.v12.authoring import (
    save_context_draft,
    start_context_draft,
)
from model_hub.services.skills_hub.v12.releases import (
    compare_context,
    context_dependencies,
    release_impact,
    review_context_release,
)
from model_hub.tests.skills_hub.r3_l03.test_releases import binding

pytestmark = pytest.mark.django_db


def test_compare_pages_cover_the_complete_tree_with_revision_bound_text_links(
    principal, context, publish_context
):
    first = publish_context(context).version
    draft = start_context_draft(
        principal=principal,
        context_id=context.pk,
        revision=0,
        from_version=first.number,
    )
    draft = save_context_draft(
        principal=principal,
        context_id=context.pk,
        revision=draft.revision,
        base_version=1,
        files=[{"path": "AGENTS.md", "content": "Repair"}]
        + [{"path": f"reference/{index}.md", "content": "Text"} for index in range(30)],
        description="Repair",
        note="",
    )
    pages = [
        compare_context(
            principal=principal,
            context_id=context.pk,
            from_ref=1,
            to_ref="draft",
            revision=draft.revision,
            page=page,
        )
        for page in [1, 2]
    ]
    rows = [row for page in pages for row in page["files"]]
    assert len(rows) == 32 and len({row["path"] for row in rows}) == 32
    assert rows[0]["path"] == "AGENTS.md"
    assert all(f"revision={draft.revision}" in row["to_text_url"] for row in rows)
    assert all("to_text" not in row for row in rows)
    review = review_context_release(
        principal=principal,
        context_id=context.pk,
        operation="publish",
        revision=draft.revision,
    )
    continuation = review_context_release(
        principal=principal,
        context_id=context.pk,
        operation="publish",
        revision=draft.revision,
        page=2,
    )
    assert review["review_fingerprint"] == continuation["review_fingerprint"]
    assert len(continuation["changes"]["files"]) == 7
    with pytest.raises(SkillsHubError) as error:
        compare_context(
            principal=principal,
            context_id=context.pk,
            from_ref=1,
            to_ref="draft",
            revision=draft.revision - 1,
        )
    assert error.value.code == "draft_conflict"


def test_quarantined_published_context_cannot_be_compared_or_started_normally(
    principal, context, publish_context
):
    first = publish_context(context).version
    HubSkillContextQuarantine.objects.create(
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        version=first,
        revision=1,
        active=True,
        reason="Fixture",
        actor_id=principal.user_id,
    )
    for call in [
        lambda: compare_context(
            principal=principal, context_id=context.pk, from_ref=1, to_ref=1
        ),
        lambda: start_context_draft(
            principal=principal, context_id=context.pk, revision=0, from_version=1
        ),
    ]:
        with pytest.raises(SkillsHubError) as error:
            call()
        assert error.value.code == "context_quarantined"
    assert not context.drafts.exists()


def test_dependency_page_uses_accepted_identity_and_authorized_upstream(
    principal,
    context,
    hub_skill,
    publish_skill,
    make_skill_files,
    accept_links,
    publish_context,
):
    leaf = publish_skill(hub_skill, labels=["production"]).version
    accept_links(
        context, [{"skill_id": str(hub_skill.pk), "selector": {"label": "production"}}]
    )
    parent = publish_context(context).version
    publish_skill(
        hub_skill, labels=["production"], files=make_skill_files(body="Updated leaf")
    )
    result = context_dependencies(
        principal=principal, context_id=context.pk, number=parent.number
    )
    assert result["digest"] == parent.digest
    assert result["results"][0]["version_id"] == str(leaf.pk)
    assert result["results"][0]["upstream_version"] == 2
    assert result["results"][0]["update_state"] == "update_available"
    hub_skill.deleted = True
    hub_skill.save(update_fields=["deleted"])
    retired = context_dependencies(
        principal=principal, context_id=context.pk, number=parent.number
    )
    assert retired["results"][0]["update_state"] == "unavailable"
    assert retired["results"][0]["version_id"] == str(leaf.pk)


def test_receipts_change_observed_count_not_impact_fingerprint(
    principal, context, publish_context
):
    version = publish_context(context).version
    target = binding(context, principal, "consumer", {"label": "production"})
    before = release_impact(
        context=context, label="production", target_version=1, operation="move"
    )
    HubSkillContextReceipt.objects.create(
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        binding=target,
        reporter_key="fixture",
        event_id=uuid4(),
        instance_id=uuid4(),
        sequence=1,
        stage="resolved",
        version=version,
        digest=version.digest,
        request_digest="sha256:" + "e" * 64,
        observed_at=timezone.now(),
        origin="client_reported",
        expires_at=timezone.now() + timedelta(days=1),
    )
    after = release_impact(
        context=context, label="production", target_version=1, operation="move"
    )
    assert before["unobserved"] == 1 and after["unobserved"] == 0
    assert before["fingerprint"] == after["fingerprint"]
