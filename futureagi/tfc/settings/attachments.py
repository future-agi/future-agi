"""Private attachment settings, independent of legacy/public media storage.

The feature remains off until its deployment passes qualification. Native S3 is
an implementation candidate, not a support claim. Self-hosted MinIO and GCS stay
unqualified; there is intentionally no ambient/default credential fallback.
"""

import os

FI_ATTACHMENTS_ENABLED = os.getenv("FI_ATTACHMENTS_ENABLED", "false").lower() == "true"
# Exact https origin of the app that proxies /tracer/v1/attachment-media/*.
# Empty keeps the media-session exchange and gateway dark (404).
FI_ATTACHMENTS_MEDIA_ORIGIN = os.getenv("FI_ATTACHMENTS_MEDIA_ORIGIN", "")
FI_ATTACHMENTS_STORAGE = {
    "backend": os.getenv("FI_ATTACHMENTS_STORAGE_BACKEND", "disabled"),
    "region": os.getenv("FI_ATTACHMENTS_STORAGE_REGION", ""),
    "quarantine_bucket": os.getenv("FI_ATTACHMENTS_QUARANTINE_BUCKET", ""),
    "serving_bucket": os.getenv("FI_ATTACHMENTS_SERVING_BUCKET", ""),
    "quarantine_access_key": os.getenv("FI_ATTACHMENTS_QUARANTINE_ACCESS_KEY", ""),
    "quarantine_secret_key": os.getenv("FI_ATTACHMENTS_QUARANTINE_SECRET_KEY", ""),
    "serving_access_key": os.getenv("FI_ATTACHMENTS_SERVING_ACCESS_KEY", ""),
    "serving_secret_key": os.getenv("FI_ATTACHMENTS_SERVING_SECRET_KEY", ""),
}
