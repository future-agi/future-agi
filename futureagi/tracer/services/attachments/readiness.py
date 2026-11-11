"""Fail-closed readiness for attachment media workers (DPL02).

Run at worker start (and by the dispatcher before each tick) when attachments
are enabled. Any missing capability raises a content-free AttachmentError, so
a misconfigured worker never processes media.
"""

from django.conf import settings

from tracer.services.attachments import envelopes, sandbox
from tracer.services.attachments.contract import AttachmentError
from tracer.services.attachments.storage import from_settings


def check(*, storage=None):
    """Return the verified storage. Raises when any requirement is missing."""
    if not getattr(settings, "FI_ATTACHMENTS_ENABLED", False):
        raise AttachmentError("disabled", 503)
    # 1. The decoder boundary is actually active in THIS runtime.
    sandbox.self_test()
    # 2. A dedicated envelope key exists (never SECRET_KEY, never plaintext).
    envelopes._keys()
    # 3. Private storage passes its policy/ACL/CORS/versioning checks.
    storage = storage or from_settings(settings)
    storage.verify_readiness()
    # 4. Bounded scratch space for one source snapshot plus one window.
    if not getattr(settings, "FI_ATTACHMENTS_WORK_DIR", ""):
        raise AttachmentError("storage_unverified", 503)
    return storage
