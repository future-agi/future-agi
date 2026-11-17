"""Inspect or finish the sealing of API secrets (READINESS L03).

    manage.py api_secrets status            counts per storage form; exit 1 if any
                                            row still holds a plaintext secret
    manage.py api_secrets backfill          seal every plaintext row and move system
                                            keys' copies under the configured
                                            INTEGRATION_ENCRYPTION_KEY (idempotent)

``status`` writes nothing, so the mutation-free startup guard (model_hub/apps.py)
admits it in any process. ``backfill`` runs only as an operator job
(SERVICE_TYPE=bootstrap with STARTUP_DB_MUTATION_MODE=operator) or, on a local
install, with NO_STARTUP_DB_MUTATIONS=false.

Migration 0027 runs the backfill once. Run ``status`` after a deploy. While the
deploy rolls, a pod of the previous release can still write a plaintext: a key it
creates (sealed on its first use, or by ``backfill``), or, when it saves a sealed
key, a random value beside the hash (``plaintext_beside_hash``; the hash stays the
key's record, and the value is cleared on the key's next use or by ``backfill``).
A row the backfill failed to seal keeps its plaintext too (``failed``; logged as
``api_secret_backfill_row_failed`` with the key's id).

``hashed_under_retired_pepper`` counts the keys a request can still re-hash after
a pepper rotation: enabled and not deleted. The rotation is done when it is 0.
Disabled keys still under a retired pepper are counted apart
(``hashed_under_retired_pepper_disabled``): nothing re-hashes them, so one
re-enabled after the old pepper is dropped fails and must be reissued. Deleted
keys are not counted: they never authenticate again.

``system_copies`` counts live system keys by the key their encrypted copy opens
under. ``derived`` means a local install sealed them before
INTEGRATION_ENCRYPTION_KEY was set (they depend on SECRET_KEY until ``backfill``
moves them); ``undecryptable`` means INTEGRATION_ENCRYPTION_KEY changed and the
platform cannot present those keys. Both must be 0 once the variable is set.
"""

import json

from django.core.management.base import BaseCommand, CommandError

from accounts.api_key_lookup import (
    backfill_secret_hashes,
    plaintext_secret_count,
    reencrypt_system_secrets,
    system_ciphertext_counts,
)
from accounts.api_secrets import peppers


class Command(BaseCommand):
    help = "Report or seal API secrets stored at rest (keyed hash, no plaintext)."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["status", "backfill"])
        parser.add_argument("--batch-size", type=int, default=500)

    def handle(self, *args, action, batch_size, **options):
        from accounts.models import OrgApiKey

        if batch_size < 1:
            raise CommandError("--batch-size must be at least 1.")
        failed = 0
        if action == "backfill":
            counts = backfill_secret_hashes(OrgApiKey, batch_size=batch_size)
            failed = counts["failed"]
            moved = reencrypt_system_secrets(OrgApiKey)
            self.stdout.write(
                json.dumps(
                    {"action": "backfill", **counts, "system_copies_moved": moved}
                )
            )
        status = self._status(OrgApiKey)
        self.stdout.write(json.dumps({"action": "status", **status}))
        if status["plaintext"]:
            next_step = (
                f"{failed} failed to seal in this run: see "
                "api_secret_backfill_row_failed in the log."
                if failed
                else "run `manage.py api_secrets backfill`."
            )
            raise CommandError(
                f"{status['plaintext']} API keys still hold a plaintext secret; "
                + next_step
            )

    @staticmethod
    def _status(model) -> dict:
        from accounts.api_key_constants import LEGACY_ORG_KEY_TYPE

        current, _known = peppers()
        rows = model._base_manager.all()
        hashed = rows.exclude(secret_key_hash="")
        retired = hashed.filter(deleted=False).exclude(
            secret_key_hash__startswith=f"hmac-sha256${current.id}$"
        )
        return {
            "total": rows.count(),
            "plaintext": plaintext_secret_count(model),
            "plaintext_beside_hash": hashed.exclude(secret_key="").count(),
            "hashed": hashed.count(),
            "hashed_under_retired_pepper": retired.filter(enabled=True).count(),
            "hashed_under_retired_pepper_disabled": retired.filter(
                enabled=False
            ).count(),
            "system_without_encrypted_copy": rows.filter(
                type=LEGACY_ORG_KEY_TYPE, secret_key="", secret_key_ciphertext=""
            ).count(),
            "system_copies": system_ciphertext_counts(model),
            # Agents' sandbox keys are recoverable too (round 7: the platform
            # hands them to every launch, tracer/services/collector_ingest.py).
            "ingest_without_encrypted_copy": rows.filter(
                type="ingest", secret_key="", secret_key_ciphertext=""
            ).count(),
            "ingest_copies": system_ciphertext_counts(model, types=("ingest",)),
            "current_pepper_id": current.id,
        }
