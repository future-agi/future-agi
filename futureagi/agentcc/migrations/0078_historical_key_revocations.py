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

UPDATE_SAFETY = {
    "schema_version": 1,
    "risk_classes": ["data_migration"],
    "tables": [
        "postgres:agentcc_api_key",
    ],
    "mitigation": (
        "Reads every member of the Redis set agentcc:key:revoked once (SMEMBERS). For "
        "each key id, one UPDATE sets status=revoked and updated_at on the "
        "agentcc_api_key rows with that gateway_key_id that are not already revoked. "
        "When those rows hold one distinct, valid SHA-256 key_hash and the id has the "
        "random-id form, the hash is added to the Redis set "
        "agentcc:key:revoked:sha256:v1 and published on agentcc:key:revoke only if it "
        "was newly added. atomic=False: each UPDATE commits on its own; Redis is "
        "outside the database transaction, and the source set is never cleared. "
        "Replay-safe: rows already revoked are excluded, and adding a hash that is "
        "already present adds and publishes nothing, so a re-run or a resumed run "
        "changes nothing already done. Reverse is a no-op. Row scale: one UPDATE per "
        "member of the revoked-key set; the rows matched by gateway_key_id."
    ),
    "reviewer": "<actual-github-login>",
    "review": "<actual-PR-review-discussion-permalink>",
    "large_volume_rehearsal": True,
}


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
