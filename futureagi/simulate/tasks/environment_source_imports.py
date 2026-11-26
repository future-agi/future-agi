from __future__ import annotations

import re
from uuid import UUID

from django.db import transaction
from django.utils import timezone
from temporalio import activity

from simulate.models import HarnessSourceReview
from simulate.services.harness_storage import remove_private
from tfc.temporal.drop_in import temporal_activity

_STAGING_KEY = re.compile(
    r"environment-source-imports/(?P<org>[^/]+)/(?P<owner>[^/]+)/(?P<digest>[0-9a-f]{64})\.json\Z"
)


@temporal_activity(time_limit=60, max_retries=20, retry_delay=60, queue="default")
def expire_environment_source_import(object_key: str) -> dict[str, bool]:
    match = _STAGING_KEY.fullmatch(object_key)
    if not match:
        raise ValueError("invalid environment source import key")
    reviews = HarnessSourceReview.objects.filter(
        organization_id=match["org"],
        owner_id=UUID(match["owner"]),
        staged_digest=match["digest"],
    )
    with transaction.atomic():
        rows = list(reviews.select_for_update())
        if any(row.expires_at > timezone.now() for row in rows):
            return {"expired": False}
        from simulate.services.harness_source_reviews import _expire

        for row in rows:
            _expire(row)
    remove_private(object_key)
    return {"expired": True}


_GENERATION_RETRIES = 3


@temporal_activity(
    time_limit=14400,
    max_retries=_GENERATION_RETRIES,
    retry_delay=60,
    queue="simulation_runner",
)
def generate_environment_source_scenarios(generation_id: str) -> dict:
    from simulate.services.harness_source_generation import process

    if not activity.in_activity():
        return process(generation_id)
    info = activity.info()
    attempts = (
        info.retry_policy.maximum_attempts
        if info.retry_policy is not None
        else _GENERATION_RETRIES + 1
    )
    return process(
        generation_id, retry_available=attempts == 0 or info.attempt < attempts
    )
