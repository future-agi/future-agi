"""Encrypt the infrastructure secrets stored in org-config rows.

The write path stores the cache backends' Redis password, S3 secret access
key, Azure connection string and SAS token, the semantic cache's
vector-store API keys, the A2A agents' tokens, the audit sinks' and alert
channels' webhook headers, and the MCP servers' auth encrypted under an
``enc::`` prefix; this migration encrypts each such value a row holds that
is not stored as ciphertext, in every version of every org (active,
inactive and soft-deleted).

Idempotent: a leaf already encrypted is left alone, so a re-run is a no-op.
"Already encrypted" means ``enc::`` plus a
blob that decrypts to a stored secret, not the prefix alone: a clear secret
is any string, so one that merely begins with ``enc::``
(``enc::hunter2``) is a clear value and is encrypted like any other. A
well-formed Fernet token this deployment cannot decrypt is left as it is,
never wrapped a second time. Reversible: ``backwards`` decrypts the stored
ciphertext again and leaves the rest. The secret locations and the Fernet
check are inlined here (not imported from the service) so this migration keeps
treating the same leaves the same way even if the live code changes later.
Within one org a given clear value is encrypted once and the blob reused
across that org's versions (and across batches), so a diff between two
versions that held the same secret does not show it as changed afterwards.

Batched, not one transaction (``atomic = False``): the table is read in
primary-key order, ``BATCH_SIZE`` rows at a time, each batch read with
``SELECT ... FOR UPDATE``, rewritten and committed in a transaction of its own.
A lock is held only on one batch's rows and only while that batch runs, so
config writes elsewhere in the table are not blocked for the whole backfill,
and a failure rolls back that batch alone: the batches before it stay done and
a re-run (idempotent) finishes the rest. Volume: one row per saved org-config
version, for every org and every version, soft-deleted ones included; the
batch size, not the table size, bounds each transaction's locks and work.
Every read path masks secret leaves.
"""

import base64

from django.db import DEFAULT_DB_ALIAS, migrations, transaction

ENCRYPTED_PREFIX = "enc::"

# Rows read, rewritten and committed per transaction.
BATCH_SIZE = 500

CLEAR, CIPHERTEXT, UNREADABLE = "clear", "ciphertext", "unreadable"
_FERNET_VERSION = b"\x80"
_FERNET_OVERHEAD = 1 + 8 + 16 + 32  # version, timestamp, IV, HMAC
_AES_BLOCK = 16


def _fernet_shaped(text):
    try:
        raw = base64.b64decode(text.encode("ascii"), altchars=b"-_", validate=True)
    except (UnicodeEncodeError, ValueError):  # binascii.Error is a ValueError
        return False
    body = len(raw) - _FERNET_OVERHEAD
    return raw[:1] == _FERNET_VERSION and body >= _AES_BLOCK and body % _AES_BLOCK == 0


def _reader(CredentialManager):
    """``read(value) -> (kind, clear)``: ``CIPHERTEXT`` with the clear value
    when ``value`` is ``enc::`` plus a blob that decrypts to ``{"v": ...}``,
    ``UNREADABLE`` for a well-formed token that does not, else ``CLEAR``."""

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


def _headers(container):
    node = container.get("headers") if isinstance(container, dict) else None
    return node if isinstance(node, dict) else None


def _entries(value):
    if isinstance(value, list):
        return list(enumerate(value))
    if isinstance(value, dict):
        return list(value.items())
    return []


def _walk_leaves(config):
    """Yield ``(container, key)`` for every secret leaf in a config instance's
    ``cache``, ``a2a``, ``audit``, ``alerting`` and ``mcp`` sections."""
    cache = config.cache if isinstance(config.cache, dict) else {}
    redis = cache.get("redis")
    if isinstance(redis, dict) and "password" in redis:
        yield redis, "password"
    s3 = cache.get("s3")
    if isinstance(s3, dict) and "secret_access_key" in s3:
        yield s3, "secret_access_key"
    azure = cache.get("azure_blob") or cache.get("azureBlob")
    if isinstance(azure, dict):
        for key in ("connection_string", "sas_token"):
            if key in azure:
                yield azure, key
    semantic = cache.get("semantic")
    if isinstance(semantic, dict):
        for backend in ("qdrant", "weaviate", "pinecone"):
            node = semantic.get(backend)
            if isinstance(node, dict) and "api_key" in node:
                yield node, "api_key"

    a2a = config.a2a if isinstance(config.a2a, dict) else {}
    for _name, agent in _entries(a2a.get("agents")):
        auth = agent.get("auth") if isinstance(agent, dict) else None
        if isinstance(auth, dict):
            for key in ("token", "key"):
                if key in auth:
                    yield auth, key

    audit = config.audit if isinstance(config.audit, dict) else {}
    for _idx, sink in _entries(audit.get("sinks")):
        headers = _headers(sink)
        if headers:
            for name in list(headers):
                yield headers, name

    alerting = config.alerting if isinstance(config.alerting, dict) else {}
    for _idx, channel in _entries(alerting.get("channels")):
        headers = _headers(channel)
        if headers:
            for name in list(headers):
                yield headers, name

    mcp = config.mcp if isinstance(config.mcp, dict) else {}
    for _id, server in _entries(mcp.get("servers")):
        if not isinstance(server, dict):
            continue
        auth = server.get("auth")
        if isinstance(auth, dict):
            for key in ("token", "key"):
                if key in auth:
                    yield auth, key
        credential = server.get("credential")
        secret = credential.get("secret") if isinstance(credential, dict) else None
        if isinstance(secret, dict) and "value" in secret:
            yield secret, "value"
    managed = mcp.get("managed_tools") or mcp.get("managedTools")
    credentials = managed.get("credentials") if isinstance(managed, dict) else None
    for _cid, entry in _entries(credentials):
        secret = entry.get("secret") if isinstance(entry, dict) else None
        if isinstance(secret, dict) and "value" in secret:
            yield secret, "value"


_SECRET_FIELDS = ["cache", "a2a", "audit", "alerting", "mcp", "updated_at"]


def _apply(apps, schema_editor, transform):
    """Run ``transform(config, value)`` on every non-empty string secret leaf
    of every row, ``BATCH_SIZE`` rows per transaction (keyset by primary key);
    ``transform`` returns the new value, or ``None`` to leave the leaf."""
    AgentccOrgConfig = apps.get_model("prism", "AgentccOrgConfig")
    using = schema_editor.connection.alias if schema_editor else DEFAULT_DB_ALIAS
    # _base_manager has no soft-delete filter, so a deleted version (which
    # still holds the secret) is encrypted too.
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
                    config.save(using=using, update_fields=_SECRET_FIELDS)
        if len(rows) < size:
            return
        after = rows[-1].pk


def _transform_row(config, transform):
    changed = False
    for container, key in _walk_leaves(config):
        value = container[key]
        if not isinstance(value, str) or not value:
            continue
        new = transform(config, value)
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
    # Each batch commits on its own (see the module docstring), never the
    # whole backfill in one transaction.
    atomic = False

    dependencies = [
        ("prism", "0046_org_config_db_defaults"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
