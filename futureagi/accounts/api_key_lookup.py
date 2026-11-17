"""Find an API key by its public id and verify its secret; backfill stored secrets.

Every path that authenticates an ``X-Api-Key``/``X-Secret-Key`` pair goes through
``find_api_key``: DRF's ``APIKeyAuthentication`` and its Langfuse Basic variant,
Omega's observe-target and the MCP transport. A row still holding a plaintext
secret (one migration 0027 has not sealed yet, or one a pod of the previous
release wrote while a deploy rolls) is accepted once and re-sealed on that use;
``backfill_secret_hashes`` seals the rest. Where a row has a hash, the hash
decides (``stored_plaintext``): a plaintext an old pod wrote beside it is
cleared, never sealed.
"""

from __future__ import annotations

import structlog
from django.db import connections

# The key types the platform presents itself, so their secret stays recoverable
# (a Fernet copy under INTEGRATION_ENCRYPTION_KEY beside the hash): an org's
# system key, and an agent's write-only ``ingest`` key, which every launch of
# that agent's sandbox is handed again (tracer/services/collector_ingest.py).
# User and MCP keys are shown once, when created, and keep only the hash.
from accounts.api_key_constants import LEGACY_ORG_KEY_TYPE, RECOVERABLE_TYPES
from accounts.api_secrets import (
    CIPHERTEXT_CONFIGURED,
    CIPHERTEXT_DERIVED,
    CIPHERTEXT_UNDECRYPTABLE,
    MAX_SECRET_LENGTH,
    SEALED_FIELDS,
    ApiSecretConfigError,
    ApiSecretNotRecoverable,
    ciphertext_key,
    decrypt_secret,
    reencrypt_under_configured_key,
    revocation_payloads,
    seal,
    stored_plaintext,
    verify_secret,
)

logger = structlog.get_logger(__name__)


def find_api_key(queryset, api_key: str, presented_secret: str, *, reseal: bool = True):
    """The key in ``queryset`` whose ``api_key`` is given and whose secret matches.

    ``queryset`` carries the caller's own filters (enabled, deleted, related rows).
    Returns ``None`` for an unknown key, a wrong secret or an over-long input; all
    three cost one indexed lookup, one HMAC and one constant-time compare.
    Read-only invitation authentication disables opportunistic resealing only;
    secret verification and the caller's queryset restrictions are unchanged.
    """
    if not api_key or not presented_secret:
        return None
    if len(api_key) > MAX_SECRET_LENGTH or len(presented_secret) > MAX_SECRET_LENGTH:
        verify_secret("", "", presented_secret[:MAX_SECRET_LENGTH])
        return None
    key = queryset.filter(api_key=api_key).first()
    verdict = verify_secret(
        key.secret_key_hash if key is not None else "",
        key.secret_key if key is not None else "",
        presented_secret,
    )
    if key is None or not verdict.ok:
        return None
    # A plaintext still on the row is either the legacy secret just verified or a
    # stale value beside the hash: sealing the proven secret clears both.
    if reseal and (verdict.needs_rehash or key.secret_key):
        _reseal(key, presented_secret)
    return key


def _reseal(key, presented_secret: str) -> None:
    """Re-store a proven secret: legacy plaintext, stale plaintext or retired pepper.

    Conditional on the row being unchanged, so two pods re-sealing at once write
    once. A failure here never fails the request that proved the secret.
    """
    if not key.secret_key_hash:
        reason = "legacy_plaintext"
    elif key.secret_key:
        reason = "stale_plaintext"
    else:
        reason = "retired_pepper"
    try:
        fields = seal(presented_secret, recoverable=key.type in RECOVERABLE_TYPES)
        written = (
            type(key)
            ._base_manager.filter(
                pk=key.pk,
                secret_key=key.secret_key,
                secret_key_hash=key.secret_key_hash,
            )
            .update(**fields)
        )
    except Exception:
        logger.warning(
            "api_secret_reseal_failed", key_id=str(key.pk), reason=reason, exc_info=True
        )
        return
    for name, value in fields.items():
        setattr(key, name, value)
    logger.info(
        "api_secret_resealed", key_id=str(key.pk), reason=reason, written=written
    )


def seal_before_save(key) -> tuple[str, tuple[str, ...]]:
    """Seal ``key``'s secret in place before ``OrgApiKey.save`` writes it.

    Returns the plaintext this save issues or re-seals (the instance keeps it, so
    its caller can show it once) and the columns changed. A key without a hash
    gets one (a fresh secret when it has none); a plaintext that fails the stored
    hash is cleared, never sealed. To give an existing key a new secret, clear
    ``secret_key_hash`` too.
    """
    issued = stored_plaintext(key.secret_key_hash, key.secret_key)
    if not issued and not key.secret_key_hash and key._state.adding:
        issued = key.generate_secret_key()
    if issued:
        for name, value in seal(
            issued, recoverable=key.type in RECOVERABLE_TYPES
        ).items():
            setattr(key, name, value)
        return issued, SEALED_FIELDS
    if key.secret_key:
        key.secret_key = ""
        return "", ("secret_key",)
    return "", ()


def recoverable_secret(key) -> str | None:
    """The plaintext when this process may know it, else ``None`` (never raises)."""
    plaintext = stored_plaintext(key.secret_key_hash, key.secret_key)
    if plaintext:
        return plaintext
    if key.secret_key_ciphertext:
        try:
            return decrypt_secret(key.secret_key_ciphertext)
        except ApiSecretNotRecoverable:
            logger.error("api_secret_not_recoverable", key_id=str(key.pk))
    return None


def key_revocation_payloads(key) -> list[str]:
    return revocation_payloads(key.api_key, recoverable_secret(key))


# The columns a stale plaintext's clearing leaves as they are.
_KEPT_UNLESS_SEALED = tuple(name for name in SEALED_FIELDS if name != "secret_key")


def backfill_secret_hashes(
    model, *, batch_size: int = 500, using: str = "default"
) -> dict:
    """Leave no plaintext secret in any row. Idempotent; never one long lock.

    A row without a hash is sealed. A row whose plaintext sits beside a hash it
    does not match (a pod of the previous release wrote it) has only the
    plaintext cleared: the hash is the key's record.

    Rows go in primary-key batches, each written by one UPDATE (``_seal_batch``),
    so outside a transaction (migration 0027, ``manage.py api_secrets backfill``)
    each batch is one short statement that commits on its own, and nothing is
    held between batches. A configuration error (no pepper, no encryption key)
    stops the run; any other error sealing one row counts in ``failed``, leaves
    that row's plaintext for a later run and lets the run go on. Works on the
    live model and on a migration's historical model (it reads only columns).
    """
    manager = model._base_manager.db_manager(using)
    counts = dict.fromkeys(("sealed", "system", "stale_cleared", "skipped"), 0)
    counts["failed"] = 0
    columns = ("pk", "type", "secret_key", "secret_key_hash")
    last_pk = None
    while True:
        pending = manager.exclude(secret_key="").order_by("pk")
        if last_pk is not None:
            pending = pending.filter(pk__gt=last_pk)
        rows = list(pending.values(*columns)[:batch_size])
        if not rows:
            return counts
        last_pk = rows[-1]["pk"]
        _seal_batch(model, using, rows, counts)


def _seal_batch(model, using: str, rows: list, counts: dict) -> None:
    """Seal one batch with one UPDATE, written only where a row is unchanged.

    Each row is rewritten only if its ``secret_key`` and ``secret_key_hash`` are
    still the ones read, so a re-seal on use meanwhile is never overwritten
    (that row counts as skipped). The statement locks the rows it writes until it
    commits, and the table only in ROW EXCLUSIVE mode: a lookup reads without
    waiting, and a re-seal on use of one of these rows waits for that statement.
    """
    planned = []
    for row in rows:
        columns = _sealed_columns(row)
        if columns is None:
            counts["failed"] += 1
        else:
            planned.append((row, columns))
    written = _write_where_unchanged(model, using, planned) if planned else set()
    for row, columns in planned:
        if row["pk"] not in written:
            counts["skipped"] += 1
        elif columns["secret_key_hash"] is None:
            counts["stale_cleared"] += 1
        else:
            counts["sealed"] += 1
            counts["system"] += int(row["type"] in RECOVERABLE_TYPES)


def _sealed_columns(row: dict) -> dict | None:
    """The sealed columns of one row (``None`` keeps a column as it is): a
    re-seal of its proven plaintext, or a stale plaintext cleared alone.

    ``None`` when sealing it failed; only the error's class is logged, since its
    text could quote the secret.
    """
    try:
        secret = stored_plaintext(row["secret_key_hash"], row["secret_key"])
        if not secret:
            return dict.fromkeys(_KEPT_UNLESS_SEALED) | {"secret_key": ""}
        return seal(secret, recoverable=row["type"] in RECOVERABLE_TYPES)
    except ApiSecretConfigError:
        raise
    except Exception as exc:
        logger.error(
            "api_secret_backfill_row_failed",
            key_id=str(row["pk"]),
            error=type(exc).__name__,
        )
        return None


def _write_where_unchanged(model, using: str, planned: list) -> set:
    """``UPDATE ... FROM (VALUES ...)``: one statement for the batch.

    Returns the primary keys it wrote. Table and column names come from the
    model, values are parameters.
    """
    connection = connections[using]
    quote = connection.ops.quote_name
    meta = model._meta
    pk = quote(meta.pk.column)
    column = {name: quote(meta.get_field(name).column) for name in SEALED_FIELDS}
    kept = _KEPT_UNLESS_SEALED  # NULL in the VALUES keeps the stored value
    assignments = [f"{column['secret_key']} = ''"] + [
        f"{column[name]} = COALESCE(v.{name}, t.{column[name]})" for name in kept
    ]
    text_values = ", ".join(["CAST(%s AS text)"] * (2 + len(kept)))
    row_sql = f"(CAST(%s AS {meta.pk.db_type(connection)}), {text_values})"
    sql = (
        f"UPDATE {quote(meta.db_table)} AS t SET {', '.join(assignments)}"
        f" FROM (VALUES {', '.join([row_sql] * len(planned))})"
        f" AS v(pk, read_secret_key, read_secret_key_hash, {', '.join(kept)})"
        f" WHERE t.{pk} = v.pk AND t.{column['secret_key']} = v.read_secret_key"
        f" AND t.{column['secret_key_hash']} = v.read_secret_key_hash"
        f" RETURNING t.{pk}"
    )
    params = []
    for row, columns in planned:
        params += [str(row["pk"]), row["secret_key"], row["secret_key_hash"]]
        params += [columns[name] for name in kept]
    from django.db import transaction

    from accounts.services.hard_erase_sql import sql_authority

    with transaction.atomic(using=using), sql_authority(using, "seal"):
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            return {written_pk for (written_pk,) in cursor.fetchall()}


def plaintext_secret_count(model, using: str = "default") -> int:
    return model._base_manager.db_manager(using).exclude(secret_key="").count()


def _system_ciphertexts(
    model, using: str, batch_size: int = 500, types=RECOVERABLE_TYPES
):
    """(pk, ciphertext, deleted) of every key of ``types`` with a copy, in pk batches."""
    rows = (
        model._base_manager.db_manager(using)
        .filter(type__in=types)
        .exclude(secret_key_ciphertext="")
        .order_by("pk")
    )
    last_pk = None
    while True:
        batch = rows if last_pk is None else rows.filter(pk__gt=last_pk)
        page = list(
            batch.values_list("pk", "secret_key_ciphertext", "deleted")[:batch_size]
        )
        if not page:
            return
        yield from page
        last_pk = page[-1][0]


def reencrypt_system_secrets(model, *, using: str = "default") -> dict:
    """Move system- and ingest-key copies sealed under the derived key to INTEGRATION_ENCRYPTION_KEY.

    A local install that ran migration 0027 before the variable was set sealed
    them under the key derived from SECRET_KEY; they keep decrypting
    (``decrypt_secret``), but only while SECRET_KEY stays the same. Each row is
    updated only if its ciphertext is still the one read. Idempotent.
    """
    manager = model._base_manager.db_manager(using)
    counts = {"reencrypted": 0, "skipped": 0}
    for pk, token, _deleted in _system_ciphertexts(model, using):
        moved = reencrypt_under_configured_key(token)
        if moved is None:
            continue
        written = manager.filter(pk=pk, secret_key_ciphertext=token).update(
            secret_key_ciphertext=moved
        )
        counts["reencrypted" if written else "skipped"] += 1
    return counts


def system_ciphertext_counts(
    model, using: str = "default", types=(LEGACY_ORG_KEY_TYPE,)
) -> dict:
    """Live keys of ``types`` by the key their copy opens under (``api_secrets status``)."""
    names = (CIPHERTEXT_CONFIGURED, CIPHERTEXT_DERIVED, CIPHERTEXT_UNDECRYPTABLE)
    counts = dict.fromkeys(names, 0)
    for _pk, token, deleted in _system_ciphertexts(model, using, types=types):
        if not deleted:
            counts[ciphertext_key(token)] += 1
    return counts
