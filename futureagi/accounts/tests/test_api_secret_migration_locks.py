"""The locks of migrations 0026 and 0027: no lookup waits long on either (READINESS L03).

Fix round 3. Migration 0026 used to add the columns and seal every row in one
transaction, so the ACCESS EXCLUSIVE lock its ALTERs take was held until the last
row was sealed and every API-key lookup waited for it (the verifier measured
38.9 s at 20,000 keys). 0026 is now the schema alone, one ALTER TABLE as its last
statement, and its wait for that lock is bounded by a 5 s lock_timeout. 0027 is
non-atomic and writes each batch of 500 keys with one UPDATE statement
(``api_key_lookup._seal_batch``), each its own short transaction.

On real Postgres, the real migrations run through ``MigrationExecutor`` while a
second connection does what a concurrent request does. 0027 is paused between
two batches and between a batch's read and its write; there the probe's lookups
must answer within a 200 ms ``lock_timeout`` and the migration must hold nothing.
A request re-seals a key in the middle of a batch, and the batch must not
overwrite it. A sampler records the table locks the migration holds while its
UPDATEs run. A positive control shows the probe fails against a table held the
way the old 0026 held it. 0026 is run against an open transaction that has read
the table: it gives up after its lock_timeout, changes nothing, releases the
lookups queued behind it, and succeeds when run again. What 0027 does with a row
it cannot seal, and without a pepper, is test_api_secret_seal_failures.py.
"""

import importlib
import threading
import time

import pytest
from django.db import OperationalError, connection
from django.db.migrations.executor import MigrationExecutor

from accounts import api_key_lookup
from accounts.models import Organization, OrgApiKey
from accounts.tests.api_secret_migration_probes import (
    BEFORE,
    LIVE_SIBLINGS,
    SCHEMA,
    SEAL,
    applied,
    lookup,
    migrate_to,
    migrate_to_latest,
    observe,
    plaintext_ids,
    probe_connection,
    reseal_from_outside,
    seed_plaintext_keys,
    table_locks,
    two_tenants,
    write_receipt,
)

# backfill_secret_hashes' batch size, the one the migration runs with.
BATCH = 500
# Table locks that leave lookups (AccessShareLock) and updates of other rows
# (RowExclusiveLock) free to go on; ACCESS EXCLUSIVE is what the old 0026 held.
ROW_LEVEL_ONLY = {"AccessShareLock", "RowShareLock", "RowExclusiveLock"}


def _sample_locks_until(stop, pid, seen):
    """Every 5 ms until ``stop``: the table locks the migration holds."""
    sampler = probe_connection()
    try:
        while not stop.is_set():
            seen.append(table_locks(sampler, pid))
            time.sleep(0.005)
    finally:
        sampler.close()


@pytest.mark.django_db(transaction=True)
def test_no_lookup_waits_on_the_seal_inside_a_batch_or_between_batches(
    monkeypatch, capsys
):
    tenants = two_tenants()
    old_apps = migrate_to(BEFORE)
    probe = probe_connection()
    stop, sampled = threading.Event(), []
    try:
        # 1,100 keys: batches of 500, 500 and 100.
        keys = seed_plaintext_keys(old_apps, tenants, per_tenant=550)
        migrate_to(SCHEMA)
        # 0026 is the schema alone: every row still holds its plaintext.
        assert len(plaintext_ids()) == len(keys) == 1100
        raced = keys[BATCH + 250]
        watched = (
            ("sealed", keys[0]),
            ("in_batch", raced),
            ("not_reached", keys[2 * BATCH + 50]),
        )
        observed, batches = {}, []
        real_seal_batch = api_key_lookup._seal_batch
        real_sealed_columns = api_key_lookup._sealed_columns

        def seal_batch(model, using, rows, counts):
            batches.append(len(rows))
            if len(batches) == 2:  # batch 1 written, batch 2 not read yet
                observed["between"] = observe(probe, watched)
            real_seal_batch(model, using, rows, counts)

        def sealed_columns(row):
            if row["pk"] == keys[BATCH].id and "inside" not in observed:
                # Batch 2 is read and not written yet.
                observed["inside"] = observe(probe, watched)
                # A request re-seals one of its keys now, from its own connection.
                reseal_from_outside(probe, raced, hint="raced")
            return real_sealed_columns(row)

        monkeypatch.setattr(api_key_lookup, "_seal_batch", seal_batch)
        monkeypatch.setattr(api_key_lookup, "_sealed_columns", sealed_columns)
        connection.ensure_connection()
        sampler = threading.Thread(
            target=_sample_locks_until,
            args=(stop, connection.connection.info.backend_pid, sampled),
        )
        sampler.start()
        sealed_apps = migrate_to(SEAL)
        stop.set()
        sampler.join(30)
        monkeypatch.undo()
        printed = capsys.readouterr().out

        modes = sorted(set().union(*sampled))
        write_receipt(
            "0027_seal",
            {
                "batches": batches,
                **observed,
                "lock_samples": len(sampled),
                "lock_modes_sampled": modes,
            },
        )
        assert batches == [BATCH, BATCH, 100]
        between, inside = observed["between"], observed["inside"]
        assert (
            between["lookups"]
            == inside["lookups"]
            == {
                "sealed": ("answered", True),
                "in_batch": ("answered", False),
                "not_reached": ("answered", False),
            }
        )
        # Between batches, and between a batch's read and its write, the
        # migration holds nothing and has no open transaction.
        for moment in (between, inside):
            assert moment["migration_table_locks"] == set(), moment
            assert moment["migration_state"] == "idle", moment
        # While its UPDATEs ran: nothing a lookup or another row's update waits on.
        assert sampled and set(modes) <= ROW_LEVEL_ONLY, modes
        # The re-seal in the middle of the batch was not overwritten.
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT secret_key, secret_key_hint FROM accounts_orgapikey"
                " WHERE id = %s",
                [raced.id],
            )
            assert cursor.fetchone() == ("", "raced")
        assert "1099 sealed (2 system keys)" in printed, printed
        assert "1 already sealed by a request" in printed, printed

        assert applied(SEAL) and plaintext_ids() == set()
        # The database is deliberately still at 0027. Query its historical
        # model, not today's model (which also selects retirement columns).
        SealedKey = sealed_apps.get_model("accounts", "OrgApiKey")
        live = SealedKey._base_manager.filter(enabled=True, deleted=False)
        for key in keys:
            assert api_key_lookup.find_api_key(live, key.api_key, key.secret)
    finally:
        stop.set()
        monkeypatch.undo()
        probe.close()
        migrate_to_latest()


@pytest.mark.django_db(transaction=True)
def test_the_probe_fails_against_a_table_held_like_the_old_0026():
    """Positive control: the probe above detects the lock the old 0026 held."""
    org = Organization.objects.create(name="Seal control")
    key = OrgApiKey.no_workspace_objects.create(organization=org, type="system")
    holder, probe = probe_connection(), probe_connection()
    try:
        with holder.transaction():
            holder.execute("LOCK TABLE accounts_orgapikey IN ACCESS EXCLUSIVE MODE")
            locks = table_locks(probe, holder.info.backend_pid)
            assert lookup(probe, key.api_key) == ("waited", None)
        assert "AccessExclusiveLock" in locks and not locks <= ROW_LEVEL_ONLY
        assert lookup(probe, key.api_key) == ("answered", True)
    finally:
        holder.close()
        probe.close()


def _unapply_schema_only():
    """Unapply 0026_orgapikey_secret_hash alone, keeping dev's other children of
    0025 (LIVE_SIBLINGS, round 7) applied: migrating to BEFORE would drop their
    columns of the same table as well."""
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate([BEFORE], plan=[(executor.loader.graph.nodes[SCHEMA], True)])


def _statements_of(target):
    """Every statement migrating to ``target`` sends, in order, with each COMMIT.

    ``target`` is a node, or a function that migrates (``_unapply_schema_only``)."""
    sent = []
    real_commit = connection.commit

    def commit():
        sent.append("COMMIT")
        return real_commit()

    def record(execute, sql, params, many, context):
        sent.append(" ".join(str(sql).split()))
        return execute(sql, params, many, context)

    connection.commit = commit
    try:
        with connection.execute_wrapper(record):
            target() if callable(target) else migrate_to(target)
    finally:
        del connection.commit
    return sent


@pytest.mark.django_db(transaction=True)
def test_0026_holds_the_table_lock_for_its_alter_alone_either_way():
    """0026's ALTER TABLE is its transaction's only statement besides the lock
    bound, and that transaction commits as it returns, before Django records the
    migration: the table lock lasts the statement.

    Measured beside this (the lane's component/migration-0026-shapes.json): three
    AddField operations in an atomic migration held it 0.29-1.51 s (Django
    re-renders the state between them); one ALTER TABLE in an atomic migration
    still held it up to 0.95 s, while Django recorded the migration; this shape
    1.0-1.6 ms (component/migration-scale.json).
    """
    migrate_to(BEFORE)
    try:
        # dev's other children of 0025 alter the same table; they are applied
        # first, outside the measurement, so 0026's statements are measured alone.
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(list(LIVE_SIBLINGS))
        for target, verb in (
            (SCHEMA, "ADD COLUMN IF NOT EXISTS"),
            (_unapply_schema_only, "DROP"),
        ):
            sent = _statements_of(target)
            table = [i for i, sql in enumerate(sent) if '"accounts_orgapikey"' in sql]
            assert len(table) == 1, sent
            alter = sent[table[0]]
            assert alter.startswith('ALTER TABLE "accounts_orgapikey"'), alter
            assert alter.count(verb) == 3, alter
            assert sent[table[0] - 1] == "SET LOCAL lock_timeout = '5s'", sent
            assert sent[table[0] + 1] == "COMMIT", sent
            # Then only the record of the migration, in its own transactions.
            after = sent[table[0] + 2 :]
            assert after and all(
                sql == "COMMIT" or "django_migrations" in sql for sql in after
            ), after
    finally:
        migrate_to_latest()


@pytest.mark.django_db(transaction=True)
def test_0026_runs_again_after_stopping_between_its_alter_and_its_record():
    """Its ALTER commits before the record, so a process can stop in between."""
    migration = importlib.import_module(
        "accounts.migrations.0026_orgapikey_secret_hash"
    )
    migrate_to(BEFORE)
    try:
        with connection.schema_editor(atomic=False) as editor:
            migration.add_columns(None, editor)  # columns added, 0026 unrecorded
        assert not applied(SCHEMA)
        migrate_to(SCHEMA)
        assert applied(SCHEMA)
        with connection.schema_editor(atomic=False) as editor:
            migration.drop_columns(None, editor)  # dropped, 0026 still recorded
        migrate_to(BEFORE)
        assert not applied(SCHEMA)
    finally:
        migrate_to_latest()


def _wait_for_a_queued_alter(probe):
    """Until 0026's ALTER is queued for the table's ACCESS EXCLUSIVE lock."""
    deadline = time.monotonic() + 30
    query = (
        "SELECT count(*) FROM pg_locks WHERE NOT granted"
        " AND mode = 'AccessExclusiveLock'"
        " AND relation = 'accounts_orgapikey'::regclass"
    )
    while probe.execute(query).fetchone()[0] == 0:
        assert time.monotonic() < deadline, "0026 never queued for the lock"
        time.sleep(0.02)


@pytest.mark.django_db(transaction=True)
def test_0026_waits_at_most_its_lock_timeout_and_changes_nothing_when_it_gives_up():
    migrate_to(BEFORE)
    holder = probe_connection()
    # No lock_timeout: this lookup queues behind 0026 for as long as 0026 waits.
    queued_lookup = probe_connection(lock_timeout="0")
    queued = {}

    def lookup_while_0026_waits():
        _wait_for_a_queued_alter(queued_lookup)
        start = time.monotonic()
        queued_lookup.execute("SELECT count(*) FROM accounts_orgapikey").fetchone()
        queued["s"] = time.monotonic() - start

    try:
        # An open transaction that has read the table, as a slow request's would.
        holder.execute("BEGIN")
        holder.execute("SELECT count(*) FROM accounts_orgapikey").fetchone()
        thread = threading.Thread(target=lookup_while_0026_waits)
        thread.start()
        start = time.monotonic()
        with pytest.raises(OperationalError, match="lock timeout"):
            migrate_to(SCHEMA)
        gave_up_after = time.monotonic() - start
        thread.join(30)
        write_receipt(
            "0026_schema",
            {"migrate_gave_up_after_s": gave_up_after, "lookup_queued_s": queued},
        )
        assert 4.5 <= gave_up_after < 20, gave_up_after
        # The lookup queued behind the waiting ALTER and ran once it gave up.
        assert 1.0 <= queued["s"] < gave_up_after, (queued, gave_up_after)
        assert not applied(SCHEMA)
        holder.execute("ROLLBACK")
        migrate_to(SEAL)  # run again: both apply
        assert applied(SCHEMA) and applied(SEAL)
    finally:
        holder.close()
        queued_lookup.close()
        migrate_to_latest()
