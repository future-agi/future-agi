"""Private attachment settings using existing provider/configuration delivery.

Private buckets and purpose credentials remain explicit. Provider configuration
is shared, not public-media clients/URLs/policies. Resolution is lazy while the
feature is disabled; enabling resolves configuration, not native qualification.
"""

import os

from django.utils.functional import SimpleLazyObject

from tfc.utils.storage_config import (
    attachment_binding_access_settings,
    attachment_storage_settings,
)

FI_ATTACHMENTS_ENABLED = os.getenv("FI_ATTACHMENTS_ENABLED", "false").lower() == "true"
# Exact https origin of the app that proxies /tracer/v1/attachment-media/*.
# Empty keeps the media-session exchange and gateway dark (404).
FI_ATTACHMENTS_MEDIA_ORIGIN = os.getenv("FI_ATTACHMENTS_MEDIA_ORIGIN", "")
# A bad current default must not prevent historical A from starting/cleaning up.
# New-admission callers still activate the existing strict default resolver.
FI_ATTACHMENTS_STORAGE = SimpleLazyObject(
    lambda: attachment_storage_settings(enabled=FI_ATTACHMENTS_ENABLED)
)
# Explicit JSON reference delivery, keyed by immutable binding fingerprint.
# Neither default endpoints nor historical mappings are parsed at import time.
FI_ATTACHMENTS_BINDING_ACCESS = SimpleLazyObject(attachment_binding_access_settings)
# The standard storage bridge carries FI_ATTACHMENTS_POSTURE_CREDENTIAL_REF as
# a validated symbolic name. Deployment Python settings may supply already-
# constructed typed objects here; never parse tokens/paths or invoke ADC.
FI_ATTACHMENTS_POSTURE_CREDENTIALS = {}

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
