import importlib
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.apps import apps
from django.db import connection
from django.core.management import call_command

from accounts.models import OrgApiKey
from accounts.tests.test_personal_keys import actor, key_for

pytestmark = pytest.mark.django_db
migration = importlib.import_module("accounts.migrations.0031_retire_org_keys")


def test_retirement_preserves_original_state_and_secrets_across_replays(actor):
    rows = [
        key_for(actor, type=kind, enabled=enabled)
        for kind in ("system", "mcp", "ingest", "user")
        for enabled in (True, False)
        if not (kind == "system" and not enabled)
    ]
    rows.append(key_for(actor, type="system", enabled=False, deleted=True))
    original = {}
    for row in rows:
        row.refresh_from_db()
        original[row.pk] = (row.enabled, row.secret_key_hash, row.secret_key_ciphertext)
    editor = SimpleNamespace(connection=connection)
    migration.retire(apps, editor)
    first_times = {}
    for row in rows:
        row.refresh_from_db()
        old = original[row.pk]
        assert (row.secret_key_hash, row.secret_key_ciphertext) == old[1:]
        if row.type == "user":
            assert row.enabled == old[0]
            assert row.retired_at is None and row.prior_enabled is None
        else:
            assert not row.enabled
            assert row.prior_enabled == old[0]
            assert row.retired_reason == "733"
            first_times[row.pk] = row.retired_at
    OrgApiKey._base_manager.filter(pk=rows[0].pk).update(enabled=True)
    migration.retire(apps, editor)
    for row in rows:
        row.refresh_from_db()
        if row.type != "user":
            assert not row.enabled
            assert row.retired_at == first_times[row.pk]
            assert row.prior_enabled == original[row.pk][0]
    migration.restore(apps, editor)
    for row in rows:
        row.refresh_from_db()
        assert row.enabled == original[row.pk][0]
        assert row.retired_at is None and row.retired_reason is None
        assert row.prior_enabled is None
        assert (row.secret_key_hash, row.secret_key_ciphertext) == original[row.pk][1:]


def test_reverse_does_not_restore_a_different_retirement(actor):
    key = key_for(actor, enabled=False, retired_reason="other", prior_enabled=True)
    migration.restore(apps, SimpleNamespace(connection=connection))
    key.refresh_from_db()
    assert not key.enabled
    assert key.retired_reason == "other"


def test_deploy_command_republishes_without_mutating_keys(actor):
    key = key_for(actor, type="system", enabled=False, retired_reason="733")
    personal = key_for(actor)
    with patch(
        "accounts.management.commands.publish_retired_key_revocations.publish_key_revocation"
    ) as publish:
        call_command("publish_retired_key_revocations")
        call_command("publish_retired_key_revocations")
    assert [call.args[0].pk for call in publish.call_args_list] == [key.pk, key.pk]
    personal.refresh_from_db()
    key.refresh_from_db()
    assert personal.enabled and not key.enabled
