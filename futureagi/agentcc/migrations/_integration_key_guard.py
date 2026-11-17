"""Frozen refusal for the migrations that encrypt or drop stored secrets.

Django never loads this module as a migration (its name starts with "_"). The
key check is a copy of model_hub 0125's key_problem, frozen here so a later
change to settings handling cannot change what these migrations do: the key is
unset, made up for this process (INTEGRATION_ENCRYPTION_KEY_EPHEMERAL), or not
a key Fernet accepts. Each migration calls require_stable_key before its first
write, and refuses only when it has something to encrypt or drop.
"""

import base64

from cryptography.fernet import Fernet
from django.conf import settings

REFUSAL = (
    "INTEGRATION_ENCRYPTION_KEY must be set to a stable Fernet key before this "
    "update: this step encrypts stored secrets in place, and a key made up for "
    "one process cannot read them back. Set it, keep it with your database "
    "backups, and run the update again."
)

KEY_UNSET = "is not set"
KEY_EPHEMERAL = "is made up for this process"
KEY_MALFORMED = "is not a valid Fernet key (32 url-safe base64-encoded bytes)"

ENCRYPTED_PREFIX = "enc::"
BATCH_SIZE = 500

_FERNET_VERSION = b"\x80"
_FERNET_OVERHEAD = 1 + 8 + 16 + 32  # version, timestamp, IV, HMAC
_AES_BLOCK = 16


class IntegrationKeyRequired(RuntimeError):
    """There is something to encrypt or drop, and no stable key."""


def key_problem():
    """Why this deployment cannot encrypt (KEY_UNSET, KEY_EPHEMERAL,
    KEY_MALFORMED), or None when it can. Never returns or names the key."""
    key = getattr(settings, "INTEGRATION_ENCRYPTION_KEY", "")
    if not key:
        return KEY_UNSET
    if getattr(settings, "INTEGRATION_ENCRYPTION_KEY_EPHEMERAL", False):
        return KEY_EPHEMERAL
    try:
        Fernet(key.encode() if isinstance(key, str) else key)
    except (TypeError, ValueError):  # binascii.Error is a ValueError
        return KEY_MALFORMED
    return None


def require_stable_key(has_work):
    """Refuse with REFUSAL when the key has a problem and has_work() reports
    something to encrypt or drop. has_work runs only when the key has a
    problem, so a deployment with a stable key reads nothing more."""
    if key_problem() is None:
        return
    if has_work():
        raise IntegrationKeyRequired(REFUSAL)


def _fernet_shaped(text):
    try:
        raw = base64.b64decode(text.encode("ascii"), altchars=b"-_", validate=True)
    except (UnicodeEncodeError, ValueError):  # binascii.Error is a ValueError
        return False
    body = len(raw) - _FERNET_OVERHEAD
    return raw[:1] == _FERNET_VERSION and body >= _AES_BLOCK and body % _AES_BLOCK == 0


def stored_in_clear(value):
    """Whether an org-config secret leaf would be encrypted when no key can
    read it: a non-empty string that is not enc:: plus a well-formed Fernet
    token (a token the key cannot read is left as it is)."""
    if not isinstance(value, str) or not value:
        return False
    if not value.startswith(ENCRYPTED_PREFIX):
        return True
    return not _fernet_shaped(value[len(ENCRYPTED_PREFIX) :])


def any_org_config(apps, using, matches):
    """Whether any agentcc_org_config row (every version, soft-deleted ones
    too) matches, read in primary-key order without locks."""
    model = apps.get_model("prism", "AgentccOrgConfig")
    rows = model._base_manager.db_manager(using).order_by("pk")
    return any(matches(row) for row in rows.iterator(chunk_size=BATCH_SIZE))
