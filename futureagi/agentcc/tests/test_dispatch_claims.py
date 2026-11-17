"""PostgreSQL transaction contracts; no transport, EE, or replica is needed."""

from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from importlib.util import find_spec
from threading import Barrier
from unittest.mock import patch

import pytest
from django.db import (
    DatabaseError,
    IntegrityError,
    connection,
    connections,
    transaction,
)
from django.test import override_settings
from django.utils import timezone

from tfc.routers import ReadReplicaRouter, force_primary

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def claim_once():
    # Keep collection working on the base: each control then reports the missing
    # public API, rather than hiding the entire runlist behind a collection error.
    assert connection.vendor == "postgresql", "Run this contract on PostgreSQL"
    assert not connection.in_atomic_block
    assert connection.get_autocommit()
    module_name = "agentcc.services.dispatch_claims"
    assert find_spec(module_name) is not None, "Missing atomic dispatch claim primitive"
    return import_module(module_name).claim_dispatch_once


def claim_model():
    return import_module("agentcc.models").AgentccDispatchClaim


def count_claims(key):
    # Assertions also read the primary, never an eventual-consistency replica.
    with force_primary():
        return claim_model().objects.filter(idempotency_key=key).count()


def test_two_connections_race_one_key(claim_once):
    barrier = Barrier(2, timeout=10)

    def contender():
        db = connections["default"]
        try:
            with db.cursor() as cursor:
                cursor.execute("SET statement_timeout = '10s'")
                cursor.execute("SET lock_timeout = '10s'")
                cursor.execute("SELECT pg_backend_pid()")
                backend_pid = cursor.fetchone()[0]
            barrier.wait()
            return backend_pid, claim_once("dispatch-race")
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(contender) for _ in range(2)]
        results = [future.result(timeout=25) for future in futures]

    assert len({pid for pid, _ in results}) == 2
    assert sorted(result for _, result in results) == [False, True]
    assert all(type(result) is bool for _, result in results)
    assert count_claims("dispatch-race") == 1


def test_second_call_after_commit_is_false(claim_once):
    assert claim_once("dispatch-sequential") is True
    assert not connection.in_atomic_block
    assert connection.get_autocommit()
    assert claim_once("dispatch-sequential") is False
    assert count_claims("dispatch-sequential") == 1


def test_fresh_connection_replay_is_false(claim_once):
    assert claim_once("dispatch-restart") is True
    original_connection = connection.connection
    connection.close()
    assert claim_once("dispatch-restart") is False
    assert connection.connection is not original_connection
    assert count_claims("dispatch-restart") == 1


def test_true_is_visible_to_another_connection_before_replay(claim_once):
    assert claim_once("dispatch-visible") is True

    def observer():
        try:
            return count_claims("dispatch-visible"), claim_once("dispatch-visible")
        finally:
            connections["default"].close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        visible_rows, replay = pool.submit(observer).result(timeout=15)
    assert visible_rows == 1
    assert replay is False


def test_later_rollback_cannot_undo_claim(claim_once):
    assert claim_once("dispatch-durable") is True
    with pytest.raises(RuntimeError, match="later operation"):
        with transaction.atomic():
            raise RuntimeError("later operation")
    assert claim_once("dispatch-durable") is False
    assert count_claims("dispatch-durable") == 1


def test_insert_database_error_never_returns_true(claim_once):
    def fail_insert(execute, sql, params, many, context):
        if sql.lstrip().upper().startswith("INSERT"):
            raise DatabaseError("injected insert failure")
        return execute(sql, params, many, context)

    with connection.execute_wrapper(fail_insert):
        with pytest.raises(DatabaseError, match="injected insert failure"):
            claim_once("dispatch-insert-error")
    assert count_claims("dispatch-insert-error") == 0
    assert claim_once("dispatch-insert-error") is True


def test_commit_database_error_never_returns_true(claim_once):
    with patch.object(
        connection, "commit", side_effect=DatabaseError("injected commit failure")
    ):
        with pytest.raises(DatabaseError, match="injected commit failure"):
            claim_once("dispatch-commit-error")
    assert count_claims("dispatch-commit-error") == 0
    assert claim_once("dispatch-commit-error") is True


def test_open_atomic_block_is_refused_without_an_insert(claim_once):
    with transaction.atomic():
        with pytest.raises(RuntimeError, match="autocommit"):
            claim_once("dispatch-nested")
        assert count_claims("dispatch-nested") == 0
    assert count_claims("dispatch-nested") == 0
    assert claim_once("dispatch-nested") is True


def test_testcase_atomic_exemption_is_also_refused(claim_once):
    with transaction.atomic():
        # Django permits durable blocks below its TestCase wrapper. This public
        # primitive must reject every enclosing block, including that exemption.
        connection.atomic_blocks[-1]._from_testcase = True
        with pytest.raises(RuntimeError, match="autocommit"):
            claim_once("dispatch-testcase-wrapper")
    assert count_claims("dispatch-testcase-wrapper") == 0


def test_manual_transaction_is_refused(claim_once):
    transaction.set_autocommit(False)
    try:
        with pytest.raises(RuntimeError, match="autocommit"):
            claim_once("dispatch-manual-transaction")
    finally:
        transaction.rollback()
        transaction.set_autocommit(True)
    assert count_claims("dispatch-manual-transaction") == 0
    assert claim_once("dispatch-manual-transaction") is True


@pytest.mark.parametrize(
    "key",
    [None, True, 0, b"dispatch", [], {}, "", " \t\n", "x" * 256, "bad\x00key"],
    ids=[
        "none",
        "boolean",
        "number",
        "bytes",
        "list",
        "dict",
        "empty",
        "blank",
        "too-long",
        "nul",
    ],
)
def test_invalid_keys_raise_before_database_access(claim_once, key):
    with patch.object(
        connection, "cursor", side_effect=AssertionError("validation touched database")
    ):
        with pytest.raises(ValueError):
            claim_once(key)


def test_maximum_length_unicode_key_is_supported(claim_once):
    key = "\U0001f30d" * 255
    assert claim_once(key) is True
    assert claim_once(key) is False
    assert count_claims(key) == 1


def test_distinct_keys_are_not_normalized(claim_once):
    for key in ["dispatch-case", "DISPATCH-CASE", " dispatch-case "]:
        assert claim_once(key) is True
        assert claim_once(key) is False


def test_database_unique_constraint_and_minimal_row(claim_once):
    before = timezone.now()
    assert claim_once("dispatch-schema") is True
    model = claim_model()
    assert {field.name for field in model._meta.fields} == {
        "id",
        "idempotency_key",
        "created_at",
    }
    assert model._meta.get_field("idempotency_key").unique
    assert model._meta.get_field("idempotency_key").max_length == 255
    with force_primary():
        row = model.objects.get(idempotency_key="dispatch-schema")
    assert before <= row.created_at <= timezone.now()
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            model.objects.create(idempotency_key="dispatch-schema")
    assert count_claims("dispatch-schema") == 1


def test_old_claim_is_retained_and_still_blocks_replay(claim_once):
    assert claim_once("dispatch-retained") is True
    old = timezone.now() - timezone.timedelta(days=31)
    claim_model().objects.filter(idempotency_key="dispatch-retained").update(
        created_at=old
    )
    assert claim_once("dispatch-retained") is False
    with force_primary():
        assert (
            claim_model().objects.get(idempotency_key="dispatch-retained").created_at
            == old
        )


def test_decision_never_reads_or_opens_a_replica(claim_once):
    observed_sql = []
    aliases = []
    handler_type = type(connections)
    original_getitem = handler_type.__getitem__

    def primary_only(handler, alias):
        aliases.append(alias)
        assert alias == "default", "claim tried to open a non-primary alias"
        return original_getitem(handler, alias)

    def record_sql(execute, sql, params, many, context):
        assert context["connection"].alias == "default"
        if "agentcc_dispatch_claim" in sql.lower():
            observed_sql.append(sql.lstrip().upper())
        return execute(sql, params, many, context)

    with override_settings(READ_REPLICA_OPT_IN=["AgentccDispatchClaim"]):
        with patch("tfc.routers._replica_configured", return_value=True):
            assert ReadReplicaRouter().db_for_read(claim_model()) == "replica"
            with (
                patch.object(handler_type, "__getitem__", primary_only),
                patch(
                    "django.db.router.db_for_read",
                    side_effect=AssertionError("claim consulted the read router"),
                ),
                connection.execute_wrapper(record_sql),
            ):
                assert claim_once("dispatch-primary-only") is True
                assert claim_once("dispatch-primary-only") is False

    assert aliases and set(aliases) == {"default"}
    assert len(observed_sql) == 2
    assert all(sql.startswith("INSERT") for sql in observed_sql)
    assert count_claims("dispatch-primary-only") == 1
