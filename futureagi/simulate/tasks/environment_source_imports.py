from __future__ import annotations

import re

from django.db import transaction
from django.db.models import F
from django.utils import timezone

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
        owner_id=match["owner"],
        staged_digest=match["digest"],
    )
    with transaction.atomic():
        rows = list(reviews.select_for_update().only("expires_at"))
        if any(row.expires_at > timezone.now() for row in rows):
            return {"expired": False}
        reviews.filter(
            state__in=[
                HarnessSourceReview.State.READING,
                HarnessSourceReview.State.READY,
                HarnessSourceReview.State.NEEDS_REPAIR,
            ]
        ).update(
            state=HarnessSourceReview.State.EXPIRED,
            revision=F("revision") + 1,
            preview={},
        )
    remove_private(object_key)
    return {"expired": True}
