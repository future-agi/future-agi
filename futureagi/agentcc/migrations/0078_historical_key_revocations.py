"""Preserve historical revocations before serving credential-hash readers.

Reverse is deliberately a noop: rolling back code must never reissue a key.
Redis is external to the SQL transaction. Both admission paths MUST dual-read
legacy IDs and credential hashes before this operation starts; every ID marker
is retained on success, on an interrupted run, and on an executor retry.
"""

import json
import re

from django.db import migrations
from django.utils import timezone

from agentcc.services.key_revocation_authority import (
    RevocationAuthorityUnavailable,
    failure_reason,
    revocation_authority,
)

# Freeze the historical wire format and resolver policy in the migration.
LEGACY = "agentcc:key:revoked"
HASHES = "agentcc:key:revoked:sha256:v1"
CHANNEL = "agentcc:key:revoke"


def historical_hash(key_id, stored_hashes):
    # Old sequential/config IDs were reusable. A CURRENT slot occupant is not
    # evidence of which historical credential was revoked. Only random IDs
    # with one unambiguous persisted identity (including deleted rows) qualify.
    if not re.fullmatch(r"key_(?:[0-9a-f]{24}|[0-9a-f]{32})", key_id):
        return None
    if not stored_hashes or any(
        not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", value)
        for value in stored_hashes
    ):
        return None
    candidates = {value.lower() for value in stored_hashes}
    return next(iter(candidates)) if len(candidates) == 1 else None


def forwards(apps, schema_editor):
    report = {"resolved": 0, "unresolved": 0}
    try:
        keys = apps.get_model("prism", "AgentccAPIKey")
        alias = schema_editor.connection.alias
        with revocation_authority() as client:
            # SMEMBERS is one atomic inventory snapshot. Never SCAN without
            # deduplication, never clear the source, never expire a tombstone.
            for key_id in sorted(client.smembers(LEGACY)):
                rows = keys._base_manager.using(alias).filter(gateway_key_id=key_id)
                identity = historical_hash(
                    key_id, list(rows.values_list("key_hash", flat=True))
                )
                # Retire hashless, ambiguous, deleted and all-workspace rows too.
                # No timestamp churn on replay; one SQL statement is atomic.
                rows.exclude(status="revoked").update(
                    status="revoked", updated_at=timezone.now()
                )
                if identity is None:
                    report["unresolved"] += 1
                    continue
                # Add before notifying; missed publication cannot reopen a key.
                # A replay of an already installed hash makes no durable change.
                if client.sadd(HASHES, identity):
                    client.publish(CHANNEL, "sha256:" + identity)
                report["resolved"] += 1
    except RevocationAuthorityUnavailable:
        # It already names the failed check, and carries no value.
        raise
    except Exception as exc:
        # No member, hash, SQL error, Redis endpoint or raw credential in output.
        raise RevocationAuthorityUnavailable(
            failure_reason("migration setup failed", exc)
        ) from None
    print(json.dumps(report, sort_keys=True))
    return report


class Migration(migrations.Migration):
    # The exact base has two prism leaves. Merge them rather than adding a third.
    dependencies = [
        ("prism", "0075_w11_approval_lifecycle"),
        ("prism", "0077_remove_webhook_plaintext"),
    ]
    atomic = False
    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop, atomic=False),
    ]
