"""Real PostgreSQL concurrent callers and current-principal isolation."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from django.db import close_old_connections, connection
from django.test.utils import CaptureQueriesContext

from accounts.models.organization_membership import OrganizationMembership
from model_hub.services.skills_hub.errors import SkillsHubError
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
from model_hub.services.skills_hub.v12.releases import (
    context_dependents,
    context_release_state,
)

pytestmark = pytest.mark.django_db(transaction=True)


def race(call):
    barrier = Barrier(2)

    def worker():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return call()
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker) for _ in range(2)]
        return [future.result(timeout=30) for future in futures]


def test_same_dependency_operation_concurrently_mutates_once(
    principal, context, hub_skill, publish_skill
):
    leaf = publish_skill(hub_skill).version
    links = [{"skill_id": str(hub_skill.pk), "selector": {"version": leaf.number}}]
    preview = preview_dependencies(
        principal=principal,
        context_id=context.pk,
        revision=1,
        links=links,
        refresh_skill_ids=[],
    )
    key = uuid4()
    request = {
        "revision": 1,
        "links": links,
        "refresh_skill_ids": [],
        "expected_fingerprint": preview["fingerprint"],
    }
    results = race(
        lambda: apply_dependencies(
            principal=principal,
            context_id=context.pk,
            request=request,
            idempotency_key=key,
        )
    )
    assert results[0] == results[1]
    assert context.drafts.get().revision == 2
    assert context.drafts.get().resolved_lock["links"][0]["version_id"] == str(leaf.pk)


def test_same_publication_concurrently_allocates_once(principal, context):
    review = review_context_head(principal=principal, context_id=context.pk, revision=1)
    draft = acknowledge_context_head(
        principal=principal,
        context_id=context.pk,
        revision=1,
        expected_digest=review["candidate_digest"],
        expected_head_version=None,
        review_fingerprint=review["review_fingerprint"],
    )
    args = {
        "principal": principal,
        "context_id": context.pk,
        "revision": draft.revision,
        "base_version": None,
        "expected_digest": review["candidate_digest"],
        "expected_head_version": None,
        "review_fingerprint": draft.head_review_fingerprint,
        "acknowledge_warnings": True,
        "note": "",
        "idempotency_key": uuid4(),
    }
    results = race(lambda: str(publish_context_draft(**args).version.pk))
    assert results[0] == results[1]
    assert context.versions.count() == 1 and not context.drafts.exists()


def test_graph_locks_all_parent_child_rows_in_one_uuid_order(
    principal, context, hub_skill, publish_skill
):
    leaf = publish_skill(hub_skill).version
    with CaptureQueriesContext(connection) as captured:
        preview_dependencies(
            principal=principal,
            context_id=context.pk,
            revision=1,
            links=[
                {"skill_id": str(hub_skill.pk), "selector": {"version": leaf.number}}
            ],
            refresh_skill_ids=[],
        )
    locks = [
        query["sql"]
        for query in captured
        if "FOR UPDATE" in query["sql"] and 'FROM "model_hub_hubskill"' in query["sql"]
    ]
    assert len(locks) == 1
    assert 'ORDER BY "model_hub_hubskill"."id" ASC' in locks[0]
    assert str(context.pk).replace("-", "") in locks[0].replace("-", "")
    assert str(hub_skill.pk).replace("-", "") in locks[0].replace("-", "")


def test_system_keys_cannot_borrow_a_private_context_owner(principal, context):
    system = SkillPrincipal(principal.scope, None, uuid4(), "system_key")
    with pytest.raises(SkillsHubError) as error:
        get_compiled_draft(principal=system, context_id=context.pk, revision=1)
    assert error.value.code == "principal_not_supported"
    with pytest.raises(SkillsHubError) as error:
        create_context(
            principal=system,
            name="blocked",
            description="Blocked",
            tags=[],
            files=None,
            idempotency_key=uuid4(),
        )
    assert error.value.code == "principal_not_supported"


def test_scope_checks_precede_counts_and_foreign_dependencies(
    principal, context, hub_skill, publish_skill, hub_other_workspace
):
    publish_skill(hub_skill)
    foreign = SkillPrincipal(
        SkillScope(principal.scope.organization_id, hub_other_workspace.pk),
        principal.user_id,
        None,
        "human",
    )
    for call in [
        lambda: context_release_state(principal=foreign, context_id=context.pk),
        lambda: context_dependents(principal=foreign, skill_id=hub_skill.pk),
    ]:
        with pytest.raises(SkillsHubError) as error:
            call()
        assert error.value.code == "resource_not_found"
        assert context.name not in str(error.value.detail)


def test_membership_revocation_blocks_even_original_operation_replay(principal):
    args = {
        "principal": principal,
        "name": "revocation",
        "description": "Purpose",
        "tags": [],
        "files": None,
        "idempotency_key": uuid4(),
    }
    create_context(**args)
    OrganizationMembership.no_workspace_objects.filter(
        user_id=principal.user_id, organization_id=principal.scope.organization_id
    ).update(is_active=False)
    with pytest.raises(SkillsHubError) as error:
        create_context(**args)
    assert error.value.code == "access_revoked"
