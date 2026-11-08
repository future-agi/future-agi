"""Synthetic context fixtures exercise real scoped services and persistence."""

from uuid import UUID, uuid4

import pytest

from model_hub.models.skills_hub import HubSkill
from model_hub.services.skills_hub.v11.types import SkillPrincipal, SkillScope
from model_hub.services.skills_hub.v12.authoring import (
    acknowledge_context_head,
    apply_dependencies,
    create_context,
    get_compiled_draft,
    preview_dependencies,
    publish_context_draft,
    review_context_head,
)


@pytest.fixture
def principal(organization, workspace, user):
    return SkillPrincipal(
        SkillScope(organization.pk, workspace.pk), user.pk, None, "human"
    )


@pytest.fixture
def context_factory(principal):
    def make(name="support-context", files=None):
        result = create_context(
            principal=principal,
            name=name,
            description="Support handling with a reviewed shared policy.",
            tags=[],
            files=files,
            idempotency_key=uuid4(),
        )
        return HubSkill.no_workspace_objects.get(pk=UUID(result["context_id"]))

    return make


@pytest.fixture
def context(context_factory):
    return context_factory()


@pytest.fixture
def accept_links(principal):
    def accept(context, links, refresh=None):
        draft = context.drafts.get(user_id=principal.user_id)
        preview = preview_dependencies(
            principal=principal,
            context_id=context.pk,
            revision=draft.revision,
            links=links,
            refresh_skill_ids=refresh or [],
        )
        receipt = apply_dependencies(
            principal=principal,
            context_id=context.pk,
            request={
                "revision": draft.revision,
                "links": links,
                "refresh_skill_ids": refresh or [],
                "expected_fingerprint": preview["fingerprint"],
            },
            idempotency_key=uuid4(),
        )
        draft.refresh_from_db()
        assert receipt["revision"] == draft.revision
        return draft

    return accept


@pytest.fixture
def publish_context(principal):
    def publish(context, **overrides):
        draft = context.drafts.get(user_id=principal.user_id)
        compiled = get_compiled_draft(
            principal=principal, context_id=context.pk, revision=draft.revision
        )
        review = review_context_head(
            principal=principal, context_id=context.pk, revision=draft.revision
        )
        draft = acknowledge_context_head(
            principal=principal,
            context_id=context.pk,
            revision=draft.revision,
            expected_digest=compiled.digest,
            expected_head_version=review["expected_head_version"],
            review_fingerprint=review["review_fingerprint"],
        )
        args = {
            "principal": principal,
            "context_id": context.pk,
            "revision": draft.revision,
            "base_version": draft.base_version,
            "expected_digest": compiled.digest,
            "expected_head_version": draft.expected_head_version,
            "review_fingerprint": draft.head_review_fingerprint,
            "acknowledge_warnings": True,
            "note": "",
            "idempotency_key": uuid4(),
        }
        args.update(overrides)
        return publish_context_draft(**args)

    return publish
