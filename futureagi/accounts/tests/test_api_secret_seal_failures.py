"""What migration 0027 does with a row it cannot seal, and without a pepper (L03).

Fix round 3: the seal is non-atomic, so one bad row must not abort the run (the
repo's migration standard). A row that fails for any reason but configuration
is counted, logged by id and error class (never the error's text, which could
quote the secret) and left with its plaintext; the migration is applied and the
deploy goes on, ``api_secrets status`` exits 1 until ``backfill`` seals it. A
missing pepper is configuration: the run stops at the first row, having written
nothing, and the migration stays unapplied. Real Postgres, two tenants.
"""

import pytest
import structlog
from django.test import override_settings

from accounts import api_key_lookup
from accounts.api_secrets import ApiSecretConfigError
from accounts.models import OrgApiKey
from accounts.tests.api_secret_migration_probes import (
    BEFORE,
    SCHEMA,
    SEAL,
    applied,
    migrate_to,
    migrate_to_latest,
    plaintext_ids,
    seed_plaintext_keys,
    two_tenants,
)


@pytest.mark.django_db(transaction=True)
def test_a_row_that_fails_to_seal_is_counted_and_the_migration_goes_on(
    monkeypatch, capsys
):
    tenants = two_tenants()
    old_apps = migrate_to(BEFORE)
    try:
        keys = seed_plaintext_keys(old_apps, tenants, per_tenant=3)
        migrate_to(SCHEMA)
        bad = next(key for key in keys if key.kind == "user")
        real_seal = api_key_lookup.seal

        def seal(secret, *, recoverable):
            if secret == bad.secret:
                # An error whose text quotes the secret, as a real one might.
                raise ValueError(f"cannot seal {secret}")
            return real_seal(secret, recoverable=recoverable)

        monkeypatch.setattr(api_key_lookup, "seal", seal)
        with structlog.testing.capture_logs() as logs:
            migrate_to(SEAL)
        printed = capsys.readouterr().out
        monkeypatch.undo()

        # The deploy goes on: 0027 is applied and only that row keeps a plaintext.
        assert applied(SEAL) and plaintext_ids() == {bad.id}
        assert "5 sealed (2 system keys)" in printed and "1 failed" in printed
        failures = [e for e in logs if e["event"] == "api_secret_backfill_row_failed"]
        assert [(e["key_id"], e["error"]) for e in failures] == [
            (str(bad.id), "ValueError")
        ]
        assert bad.secret not in printed and bad.secret not in repr(logs)

        # Once the cause is gone, the operator backfill seals it.
        counts = api_key_lookup.backfill_secret_hashes(OrgApiKey)
        assert (counts["sealed"], counts["failed"]) == (1, 0)
        assert plaintext_ids() == set()
    finally:
        monkeypatch.undo()
        migrate_to_latest()


@pytest.mark.django_db(transaction=True)
def test_a_missing_pepper_stops_the_seal_before_it_writes():
    tenants = two_tenants()
    old_apps = migrate_to(BEFORE)
    try:
        keys = seed_plaintext_keys(old_apps, tenants, per_tenant=2)
        migrate_to(SCHEMA)
        with override_settings(ENV_TYPE="production", API_SECRET_PEPPER=""):
            with pytest.raises(ApiSecretConfigError, match="FI_API_SECRET_PEPPER"):
                migrate_to(SEAL)
        assert not applied(SEAL)
        assert plaintext_ids() == {key.id for key in keys}
    finally:
        migrate_to_latest()
