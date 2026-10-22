"""Encrypt the alert channel URLs that carry a credential in org-config rows.

A Slack incoming-webhook URL is the credential for its channel, and so is a
webhook URL with a token in its path or query. The write path stores such a
URL encrypted under an ``enc::`` prefix (the same Fernet blob as 0047's and
0050's secrets); this migration encrypts each one a row holds that is not
stored as ciphertext, in every version of every org (active, inactive and
soft-deleted).

Which URLs: the rule of ``agentcc.services.org_config_secrets
.alert_url_carries_credential``, inlined here (as 0047 and 0050 inline their
leaves) so this migration keeps treating the same URLs the same way even if
the live rule changes later: a ``slack`` channel's URL; any URL that does not
parse or has a user or password, a query or a fragment; and any URL with a
path segment that is not a plain word (lowercase letters joined by ``-``,
``_`` or ``.``, at most 24 characters).

Idempotent, reversible and batched exactly as 0047: a URL already stored as
readable ciphertext, or as a well-formed token this deployment cannot
decrypt, is left alone; ``backwards`` decrypts the URL leaves only (0047's and
0050's own ``backwards`` handle the rest); ``BATCH_SIZE`` rows per
transaction, ``atomic = False``. Within one org a given URL is encrypted once
and the blob reused across that org's versions, so a diff between versions
that held the same URL does not show it as changed. Headers on a channel are
0047's leaves and are not touched here.
"""

import base64
import re
from urllib.parse import urlsplit

from django.db import DEFAULT_DB_ALIAS, migrations, transaction

ENCRYPTED_PREFIX = "enc::"

# Rows read, rewritten and committed per transaction (as 0047).
BATCH_SIZE = 500

CLEAR, CIPHERTEXT, UNREADABLE = "clear", "ciphertext", "unreadable"
_FERNET_VERSION = b"\x80"
_FERNET_OVERHEAD = 1 + 8 + 16 + 32  # version, timestamp, IV, HMAC
_AES_BLOCK = 16

_PLAIN_PATH_SEGMENT = re.compile(r"[a-z]+(?:[-_.][a-z]+)*")
_PLAIN_PATH_SEGMENT_MAX = 24


def _carries_credential(channel_type, url):
    """The rule, as of this migration (see the module docstring)."""
    if not isinstance(url, str) or not url:
        return False
    if isinstance(channel_type, str) and channel_type.strip().lower() == "slack":
        return True
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 (raises ValueError for a bad port)
    except ValueError:
        return True
    if parts.username is not None or parts.password is not None:
        return True
    if parts.query or parts.fragment:
        return True
    return any(
        segment
        and (
            len(segment) > _PLAIN_PATH_SEGMENT_MAX
            or not _PLAIN_PATH_SEGMENT.fullmatch(segment)
        )
        for segment in parts.path.split("/")
    )


def _fernet_shaped(text):
    try:
        raw = base64.b64decode(text.encode("ascii"), altchars=b"-_", validate=True)
    except (UnicodeEncodeError, ValueError):  # binascii.Error is a ValueError
        return False
    body = len(raw) - _FERNET_OVERHEAD
    return raw[:1] == _FERNET_VERSION and body >= _AES_BLOCK and body % _AES_BLOCK == 0


def _reader(CredentialManager):
    """``read(value) -> (kind, clear)``, as 0047's."""

    def _read(value):
        if not value.startswith(ENCRYPTED_PREFIX):
            return CLEAR, value
        raw = value[len(ENCRYPTED_PREFIX) :]
        try:
            data = CredentialManager.decrypt(raw.encode("utf-8"))
        except Exception:
            return (UNREADABLE, None) if _fernet_shaped(raw) else (CLEAR, value)
        if isinstance(data, dict) and "v" in data:
            return CIPHERTEXT, data["v"]
        return UNREADABLE, None

    return _read


def _encrypter(CredentialManager):
    def _encrypt(cache, org_id, clear):
        key = (org_id, clear)
        if key not in cache:
            blob = CredentialManager.encrypt({"v": clear})
            text = blob.decode("utf-8") if isinstance(blob, bytes) else blob
            cache[key] = ENCRYPTED_PREFIX + text
        return cache[key]

    return _encrypt


def _entries(value):
    if isinstance(value, list):
        return list(enumerate(value))
    if isinstance(value, dict):
        return list(value.items())
    return []


def _walk_url_leaves(config):
    """Yield ``(channel, "url")`` for every alert channel whose URL carries a
    credential, or is already stored ciphertext (so ``backwards`` finds it)."""
    alerting = config.alerting if isinstance(config.alerting, dict) else {}
    for _key, channel in _entries(alerting.get("channels")):
        if not isinstance(channel, dict):
            continue
        url = channel.get("url")
        if not isinstance(url, str) or not url:
            continue
        if url.startswith(ENCRYPTED_PREFIX) or _carries_credential(
            channel.get("type"), url
        ):
            yield channel, "url"


_FIELDS = ["alerting", "updated_at"]


def _apply(apps, schema_editor, transform):
    """Run ``transform(config, value)`` on every credential URL leaf of every
    row, ``BATCH_SIZE`` rows per transaction (keyset by primary key);
    ``transform`` returns the new value, or ``None`` to leave the leaf."""
    AgentccOrgConfig = apps.get_model("prism", "AgentccOrgConfig")
    using = schema_editor.connection.alias if schema_editor else DEFAULT_DB_ALIAS
    # _base_manager has no soft-delete filter, so a deleted version (which
    # still holds the URL) is encrypted too.
    manager = AgentccOrgConfig._base_manager.db_manager(using)
    after = None
    while True:
        size = BATCH_SIZE
        with transaction.atomic(using=using):
            batch = manager.select_for_update().order_by("pk")
            if after is not None:
                batch = batch.filter(pk__gt=after)
            rows = list(batch[:size])
            for config in rows:
                if _transform_row(config, transform):
                    config.save(using=using, update_fields=_FIELDS)
        if len(rows) < size:
            return
        after = rows[-1].pk


def _transform_row(config, transform):
    changed = False
    for container, key in _walk_url_leaves(config):
        new = transform(config, container[key])
        if new is None:
            continue
        container[key] = new
        changed = True
    return changed


def forwards(apps, schema_editor):
    from integrations.services.credentials import CredentialManager

    encrypt = _encrypter(CredentialManager)
    read = _reader(CredentialManager)
    cache = {}

    def _forward(config, value):
        if read(value)[0] != CLEAR:
            return None  # idempotent: stored ciphertext, readable or not
        return encrypt(cache, str(config.organization_id), value)

    _apply(apps, schema_editor, _forward)


def backwards(apps, schema_editor):
    from integrations.services.credentials import CredentialManager

    read = _reader(CredentialManager)

    def _backward(_config, value):
        kind, clear = read(value)
        return clear if kind == CIPHERTEXT else None  # reverse: decrypt only

    _apply(apps, schema_editor, _backward)


class Migration(migrations.Migration):
    # Each batch commits on its own (see the module docstring).
    atomic = False

    dependencies = [
        ("prism", "0050_encrypt_org_config_secret_aliases"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
