"""Frozen migration algorithm for the accepted v1 codec (not an app accessor).

Requires a deployment fence covering ALL readers/writers/workers and a qualified
pre-upgrade backup. Every original survives until M3's atomic schema removal.
Needs-reentry lives in the authenticated per-field envelope, never a mutable flag.
"""

import json
import math
from collections import Counter
from uuid import UUID

from django.db import transaction

from agentcc.services import webhook_credential_storage as codec

BATCH_SIZE = 250
STORAGE_FIELDS = ("encrypted_headers", "encrypted_secret", "credential_storage_version")
REFUSAL = "Webhook credential migration refused"
ROLLBACK = "Cannot reverse webhook credential migration; use reviewed pre-upgrade backup recovery"


def closed_call(function, *args):
    """Do not retain raw driver/codec exceptions as __context__ or __cause__."""
    try:
        return function(*args)
    except Exception:
        pass
    raise ValueError(REFUSAL) from None


def _json_original(value, parents=None):
    """Reject non-JSON adapters before falsy normalization or duplicate collapse."""
    kind = type(value)
    if value is None or kind is str or kind is bool or kind is int:
        return
    if kind is float and math.isfinite(value):
        return
    if kind is not list and kind is not dict:
        raise ValueError(REFUSAL)
    parents = set() if parents is None else parents
    if id(value) in parents:
        raise ValueError(REFUSAL)
    parents.add(id(value))
    try:
        if kind is dict:
            for key, item in value.items():
                if type(key) is not str:
                    raise ValueError(REFUSAL)
                _json_original(item, parents)
        else:
            for item in value:
                _json_original(item, parents)
    finally:
        parents.remove(id(value))


def expected(field, original):
    """Frozen header_map semantics on JSON builtins; no secret coercion."""
    _json_original(original)
    if field == "secret":
        return (
            original,
            type(original) is not str,
            "secret_string" if type(original) is str else "secret_unsupported",
        )
    source = "headers_" + ("null" if original is None else type(original).__name__)
    if not original:
        return {}, False, source + "_empty"
    if type(original) is dict:
        pairs = original.items()
    elif type(original) is list and all(
        type(pair) is list and len(pair) == 2 for pair in original
    ):
        pairs = original
    else:
        return original, True, source + "_preserved"
    result = {}
    for name, value in pairs:
        if type(name) is not str or not name:
            return original, True, source + "_preserved"
        result[name] = value
    return result, False, source + "_active"


def context(row, field):
    if type(row.pk) is not UUID or type(row.organization_id) is not UUID:
        raise ValueError(REFUSAL)
    if type(row.url) is not str or not row.url:
        raise ValueError(REFUSAL)
    return codec.StorageContext(str(row.organization_id), str(row.pk), field, row.url)


def blob(value):
    if type(value) is memoryview:
        value = value.tobytes()
    if type(value) is not bytes or not value:
        raise ValueError(REFUSAL)
    return value


def _version(row):
    if (
        type(row.credential_storage_version) is not int
        or row.credential_storage_version != codec.STORAGE_VERSION
    ):
        raise ValueError(REFUSAL)


def verify_field(row, field):
    original = getattr(row, field)
    material, malformed, category = expected(field, original)
    trusted = context(row, field)
    ciphertext = blob(getattr(row, "encrypted_" + field))
    preserved = False
    try:
        recovered = codec.decrypt_for_delivery(
            trusted, storage_version=codec.STORAGE_VERSION, ciphertext=ciphertext
        )
    except codec.WebhookCredentialStorageError as failure:
        if failure.code != codec.ErrorCode.NEEDS_REENTRY:
            raise
        preserved = True
    if preserved:
        codec.verify_preserved_for_migration(
            trusted,
            original,
            storage_version=codec.STORAGE_VERSION,
            ciphertext=ciphertext,
        )
    elif malformed or json.dumps(recovered, allow_nan=False) != json.dumps(
        material, allow_nan=False
    ):
        raise ValueError(REFUSAL)
    return category, preserved


def verify_row(row, counts):
    _version(row)
    needs_reentry = False
    for field in ("headers", "secret"):
        category, preserved = verify_field(row, field)
        counts[category] += 1
        needs_reentry |= preserved
    counts["needs_reentry_rows"] += int(needs_reentry)


def convert_row(row, rows, counts):
    values = [getattr(row, name) for name in STORAGE_FIELDS]
    if all(value is None for value in values):
        encrypted = {}
        for field in ("headers", "secret"):
            material, malformed, _ = expected(field, getattr(row, field))
            trusted = context(row, field)
            encrypted["encrypted_" + field] = (
                codec.preserve_for_migration(trusted, getattr(row, field))
                if malformed
                else codec.encrypt_for_storage(trusted, material)
            )
        encrypted["credential_storage_version"] = codec.STORAGE_VERSION
        # Authenticate BOTH fields against independent originals BEFORE writing.
        for name, value in encrypted.items():
            setattr(row, name, value)
        verify_row(row, counts)
        if rows.filter(pk=row.pk).update(**encrypted) != 1:
            raise ValueError(REFUSAL)
        counts["converted_rows"] += 1
    else:
        verify_row(row, counts)
        counts["already_encrypted_rows"] += 1


def census(apps, connection):
    model = apps.get_model("prism", "AgentccWebhook")
    rows = model._base_manager.using(connection.alias)
    count = rows.count()
    table = connection.ops.quote_name(model._meta.db_table)
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        if cursor.fetchone()[0] != count:
            raise ValueError(REFUSAL)
    return rows, count


def scan(apps, connection, mode, total):
    """All queries use the executor, bounded PK-ordered locked atomic batches."""
    using = connection.alias
    rows, initial = census(apps, connection)
    if initial != total:
        raise ValueError(REFUSAL)
    organization = apps.get_model("accounts", "Organization")
    last_pk = None
    verified = 0
    counts = Counter()
    while True:
        with transaction.atomic(using=using):
            batch = rows.order_by("pk")
            if last_pk is not None:
                batch = batch.filter(pk__gt=last_pk)
            batch = list(batch.select_for_update()[:BATCH_SIZE])
            if not batch:
                break
            for row in batch:
                context(row, "headers")
            org_ids = {row.organization_id for row in batch}
            found = set(
                organization._base_manager.using(using)
                .filter(pk__in=org_ids)
                .values_list("pk", flat=True)
            )
            if org_ids != found:
                raise ValueError(REFUSAL)
            for row in batch:
                if mode == "convert":
                    convert_row(row, rows, counts)
                elif mode == "verify":
                    verify_row(row, counts)
                elif mode == "final":
                    verify_final_row(row)
                else:
                    raise ValueError(REFUSAL)
                verified += 1
            last_pk = batch[-1].pk
    if verified != total or census(apps, connection)[1] != total:
        raise ValueError(REFUSAL)
    return counts


def convert(apps, connection):
    _, total = census(apps, connection)
    converted = scan(apps, connection, "convert", total)
    verified = scan(apps, connection, "verify", total)
    # Closed classes and aggregate counts only: no IDs, values or fingerprints.
    print(
        f"Webhook credential migration: verified {total} rows; converted {converted['converted_rows']} rows; needs_reentry {verified['needs_reentry_rows']} rows"
    )
    print(
        "Webhook credential migration classes: "
        + ", ".join(f"{name}={count}" for name, count in sorted(verified.items()))
    )


def verify_all(apps, connection):
    _, total = census(apps, connection)
    scan(apps, connection, "verify", total)


def verify_final_row(row):
    _version(row)
    for field in ("headers", "secret"):
        trusted = context(row, field)
        ciphertext = blob(getattr(row, "encrypted_" + field))
        preserved = False
        try:
            codec.decrypt_for_delivery(
                trusted, storage_version=codec.STORAGE_VERSION, ciphertext=ciphertext
            )
        except codec.WebhookCredentialStorageError as failure:
            if failure.code != codec.ErrorCode.NEEDS_REENTRY:
                raise
            preserved = True
        if preserved:
            codec.recover_for_migration(
                trusted, storage_version=codec.STORAGE_VERSION, ciphertext=ciphertext
            )


def expansion_check(apps, connection):
    rows, _ = census(apps, connection)
    # Resolves the new columns, without reading/printing credential material.
    rows.filter(encrypted_headers__isnull=False).count()
    rows.filter(encrypted_secret__isnull=False).count()
    rows.filter(credential_storage_version__isnull=False).count()


def final_check(apps, connection):
    model = apps.get_model("prism", "AgentccWebhook")
    with connection.cursor() as cursor:
        columns = connection.introspection.get_table_description(
            cursor, model._meta.db_table
        )
    by_name = {column.name: column for column in columns}
    if {"headers", "secret"} & by_name.keys():
        raise ValueError(REFUSAL)
    if any(name not in by_name or by_name[name].null_ok for name in STORAGE_FIELDS):
        raise ValueError(REFUSAL)
    _, total = census(apps, connection)
    scan(apps, connection, "final", total)
