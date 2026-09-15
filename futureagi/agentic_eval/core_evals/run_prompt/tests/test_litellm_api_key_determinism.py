"""Behavioral regression test for #319 / PR #351.

Exercises the REAL LiteLLMModelManager.get_api_key call path: when duplicate ApiKey
rows exist for the same (organization, provider), selection must be DETERMINISTIC by
id. Pre-fix the fallback was ``ApiKey.objects.filter(**query).first()`` -- and because
ApiKey.Meta.ordering is ("-created_at",), a bare .first() returns the NEWEST key, not a
stable one. The fix forces ``.order_by("id").first()``.

This test would have caught the bug: it makes id-order and created_at-order DIVERGE --
the LOW-id row is the OLDEST, the HIGH-id row is the NEWEST. So:
  * fixed  (.order_by("id")) -> LOW-id row
  * buggy  (bare .first(), honoring -created_at) -> HIGH-id row (newest)
The assertion pins the deterministic LOW-id winner, so the buggy version fails.
"""
import datetime
import uuid

import pytest
from django.utils import timezone

from accounts.models import Organization
from agentic_eval.core_evals.run_prompt.litellm_models import LiteLLMModelManager
from model_hub.models.api_key import ApiKey

LOW_ID = uuid.UUID("00000000-0000-4000-8000-000000000000")
HIGH_ID = uuid.UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")


@pytest.fixture
def organization(db):
    return Organization.objects.create(name="APIKey Determinism Org")


@pytest.mark.unit
@pytest.mark.django_db
def test_get_api_key_deterministic_on_duplicate_keys(organization):
    provider = "openai"

    ApiKey.objects.create(id=LOW_ID, organization=organization, provider=provider, key="KEY_LOW")
    ApiKey.objects.create(id=HIGH_ID, organization=organization, provider=provider, key="KEY_HIGH")
    # Force id-order != created_at-order: LOW-id is OLDEST, HIGH-id is NEWEST.
    # .update() bypasses auto_now_add so the timestamps are controlled and distinct.
    ApiKey.objects.filter(id=LOW_ID).update(
        created_at=timezone.make_aware(datetime.datetime(2020, 1, 1))
    )
    ApiKey.objects.filter(id=HIGH_ID).update(
        created_at=timezone.make_aware(datetime.datetime(2026, 1, 1))
    )

    manager = LiteLLMModelManager(model_name="gpt-4o")
    result = manager.get_api_key(organization_id=organization.id, provider=provider)

    expected = ApiKey.objects.get(id=LOW_ID).actual_key
    # fixed -> LOW (min id). buggy bare .first() -> HIGH (newest via -created_at). They diverge.
    assert result == expected
