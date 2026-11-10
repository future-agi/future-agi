"""Real PostgreSQL publication/campaign lock ordering regression."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from django.db import close_old_connections, connection

from model_hub.models.skills_hub_v12 import HubSkillContextBinding
from model_hub.services.skills_hub.v12.authoring import (
    acknowledge_context_head,
    publish_context_draft,
    review_context_head,
)
from model_hub.services.skills_hub.v12.campaigns import create_campaign


@pytest.mark.django_db(transaction=True)
def test_campaign_workspace_lock_allows_publication_fk_checks(
    principal, context, settings
):
    settings.SKILLS_CONTEXT_CAMPAIGNS_ENABLED = True
    binding = HubSkillContextBinding.objects.create(
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        context=context,
        name="publication-race",
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
    review = review_context_head(principal=principal, context_id=context.pk, revision=1)
    draft = acknowledge_context_head(
        principal=principal,
        context_id=context.pk,
        revision=1,
        expected_digest=review["candidate_digest"],
        expected_head_version=None,
        review_fingerprint=review["review_fingerprint"],
    )
    context_locked, workspace_locked = Event(), Event()

    def pause_publication(execute, sql, params, many, query_context):
        result = execute(sql, params, many, query_context)
        if (
            not context_locked.is_set()
            and 'FROM "model_hub_hubskill"' in sql
            and "FOR UPDATE" in sql
        ):
            context_locked.set()
            assert workspace_locked.wait(10), "Campaign did not acquire workspace lock"
        return result

    def signal_workspace(execute, sql, params, many, query_context):
        result = execute(sql, params, many, query_context)
        if 'FROM "accounts_workspace"' in sql and (
            "FOR UPDATE" in sql or "FOR NO KEY UPDATE" in sql
        ):
            workspace_locked.set()
        return result

    def worker(call, wrapper):
        close_old_connections()
        try:
            # Bound failures even if an unrelated query regresses into a wait.
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '8s'")
            with connection.execute_wrapper(wrapper):
                return call()
        finally:
            connection.close()

    def publish():
        return publish_context_draft(
            principal=principal,
            context_id=context.pk,
            revision=draft.revision,
            base_version=None,
            expected_digest=review["candidate_digest"],
            expected_head_version=None,
            review_fingerprint=draft.head_review_fingerprint,
            acknowledge_warnings=True,
            note="",
            idempotency_key=uuid4(),
        ).version.pk

    def campaign():
        assert context_locked.wait(10), "Publication did not acquire context lock"
        return create_campaign(
            principal=principal,
            request={
                "name": "Concurrent publication",
                "targets": [{"binding_id": str(binding.pk), "version": 1}],
                "parent_campaign_id": None,
            },
            idempotency_key=uuid4(),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        published = pool.submit(worker, publish, pause_publication)
        created = pool.submit(worker, campaign, signal_workspace)
        version_id = published.result(timeout=25)
        result = created.result(timeout=25)
    assert context.versions.get(pk=version_id).number == 1
    assert result["id"]
    assert context_locked.is_set() and workspace_locked.is_set()
