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

# Completed-recording imports (B07/B08/B10/B11). All inert while disabled.
# "id:base64url(32 bytes)[,id:key...]"; the first key seals. Never SECRET_KEY.
FI_ATTACHMENTS_ENVELOPE_KEYS = os.getenv("FI_ATTACHMENTS_ENVELOPE_KEYS", "")
# Exact hosts (or parent domains) the importer may contact. Empty denies all.
FI_ATTACHMENTS_IMPORT_HOSTS = tuple(
    host.strip().lower()
    for host in os.getenv("FI_ATTACHMENTS_IMPORT_HOSTS", "").split(",")
    if host.strip()
)
FI_ATTACHMENTS_TRANSFER_SECONDS = int(
    os.getenv("FI_ATTACHMENTS_TRANSFER_SECONDS", "120")
)
FI_ATTACHMENTS_IMPORT_BYTES_PER_SECOND = int(
    os.getenv("FI_ATTACHMENTS_IMPORT_BYTES_PER_SECOND", str(8 * 1024 * 1024))
)
# Private worker scratch for one source snapshot plus one output window.
FI_ATTACHMENTS_WORK_DIR = os.getenv("FI_ATTACHMENTS_WORK_DIR", "")
# Caps of the deployment import budget row ``global:imports``, in percent
# (1-100) of the deployment caps (``imports.import_budgets``). Default: half.
FI_ATTACHMENTS_IMPORT_GLOBAL_PERCENT = int(
    os.getenv("FI_ATTACHMENTS_IMPORT_GLOBAL_PERCENT", "50")
)
