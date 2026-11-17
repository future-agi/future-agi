from __future__ import annotations

import re

from tfc.settings.settings import UPLOAD_BUCKET_NAME
from tfc.temporal.drop_in import temporal_activity
from tfc.utils.storage_client import get_storage_client

_STAGING_KEY = re.compile(
    r"environment-source-imports/[^/]+/[^/]+/[0-9a-f]{64}\.json\Z"
)


@temporal_activity(time_limit=60, max_retries=20, retry_delay=60, queue="default")
def expire_environment_source_import(object_key: str) -> dict[str, str]:
    if not _STAGING_KEY.fullmatch(object_key):
        raise ValueError("invalid environment source import key")
    get_storage_client().remove_object(UPLOAD_BUCKET_NAME, object_key)
    return {"object_key": object_key}
