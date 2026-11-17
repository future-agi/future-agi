"""M2: resumable all-row encryption, then complete verification; NO clearing.

Fence/drain all old readers and writers before M1 and keep the fence through M3.
Rollback is the reviewed pre-upgrade backup, never plaintext reconstruction.

Every stored value is bound to its webhook's address, and an empty address
cannot be bound. So before the first batch, a webhook with no address that is
not converted yet gets the reserved address ``MISSING_ADDRESS`` (``.invalid``
never resolves, so nothing is ever delivered to it) and is turned off; its
values are then converted like any other row. When its owner sets the real
address, the webhook settings ask for every non-empty header and secret value
again, as for any change of address. A count line says how many there were.
"""

from django.db import migrations, transaction
from django.db.migrations.exceptions import IrreversibleError

from agentcc.migrations import _integration_key_guard as key_guard
from agentcc.migrations import _webhook_credentials_v1 as storage
from tfc.migration_database import executor_alias_scope

# RFC 2606 reserves .invalid: this address never resolves.
MISSING_ADDRESS = "https://webhook-address-missing.invalid/"

UPDATE_SAFETY = {
    "schema_version": 1,
    "risk_classes": ["data_migration"],
    "tables": [
        "postgres:accounts_organization",
        "postgres:agentcc_webhook",
    ],
    "mitigation": (
        "Encrypts the headers and secret of every agentcc_webhook row into "
        "encrypted_headers and encrypted_secret with credential_storage_version 1, "
        "leaving the original columns in place. First, a row with an empty url and "
        "no storage columns set gets url https://webhook-address-missing.invalid/ "
        "(a reserved name that never resolves) and is_active false, in primary-key "
        "batches of 250 locked with SELECT ... FOR UPDATE, each committed on its own; "
        "a count of those rows is printed, without ids or values. Reads rows in "
        "primary-key order, 250 "
        "per batch, each batch locked with SELECT ... FOR UPDATE and committed on its "
        "own (atomic=False), and checks that each batch's organization ids exist in "
        "accounts_organization. Each new value is verified against the original before "
        "the row's UPDATE; a second full pass then verifies every row. Refuses if the "
        "row count changes during the run. Replay-safe: a row whose storage columns "
        "are already set is verified and not rewritten, so a re-run or a resumed run "
        "converts only the remaining rows, and no row is left with an empty url to "
        "change again; a failed batch rolls back alone. Reverse "
        "refuses (IrreversibleError). Row scale: one row per webhook endpoint, all "
        "organizations. Needs a stable INTEGRATION_ENCRYPTION_KEY; refuses to start "
        "without one when there is anything to encrypt."
    ),
    "reviewer": "<actual-github-login>",
    "review": "<actual-PR-review-discussion-permalink>",
    "large_volume_rehearsal": True,
}


def _not_converted(row):
    return all(getattr(row, name) is None for name in storage.STORAGE_FIELDS)


def give_missing_addresses(apps, connection):
    """The reserved address and is_active false for each row with an empty
    url and no storage columns, in locked primary-key batches. Returns how
    many rows changed."""
    using = connection.alias
    rows = apps.get_model("prism", "AgentccWebhook")._base_manager.using(using)
    candidates = rows.filter(
        url="",
        encrypted_headers__isnull=True,
        encrypted_secret__isnull=True,
        credential_storage_version__isnull=True,
    )
    last_pk = None
    given = 0
    while True:
        with transaction.atomic(using=using):
            batch = candidates.order_by("pk")
            if last_pk is not None:
                batch = batch.filter(pk__gt=last_pk)
            batch = list(batch.select_for_update()[: storage.BATCH_SIZE])
            if not batch:
                break
            for row in batch:
                if type(row.url) is not str or row.url or not _not_converted(row):
                    continue
                changed = rows.filter(pk=row.pk).update(
                    url=MISSING_ADDRESS, is_active=False
                )
                if changed != 1:
                    raise ValueError(storage.REFUSAL)
                given += 1
            last_pk = batch[-1].pk
    return given


def forwards(apps, schema_editor):
    with executor_alias_scope(schema_editor):
        # Before the first batch: a row not converted yet would be encrypted.
        webhooks = apps.get_model("prism", "AgentccWebhook")._base_manager.using(
            schema_editor.connection.alias
        )
        key_guard.require_stable_key(
            lambda: webhooks.filter(
                encrypted_headers__isnull=True,
                encrypted_secret__isnull=True,
                credential_storage_version__isnull=True,
            ).exists()
        )
        given = storage.closed_call(
            give_missing_addresses, apps, schema_editor.connection
        )
        if given:
            # A count only: no ids, addresses or values.
            webhooks_had = f"{given} webhook{'' if given == 1 else 's'} had"
            print(
                f"Webhook credential migration: {webhooks_had} no address: each "
                f"now has the address {MISSING_ADDRESS} and is turned off; set its "
                "address, and enter its secret and header values again, in the "
                "webhook settings"
            )
        storage.closed_call(storage.convert, apps, schema_editor.connection)


def backwards(apps, schema_editor):
    with executor_alias_scope(schema_editor):
        raise IrreversibleError(storage.ROLLBACK)


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("prism", "0075_webhook_credential_columns")]
    operations = [migrations.RunPython(forwards, backwards, atomic=False)]
