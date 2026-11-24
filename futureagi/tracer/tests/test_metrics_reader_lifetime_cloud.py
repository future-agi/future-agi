"""Standalone cloud-only PG16/CH25.8 lifetime qualification, synthetic scope.

Run outside the app pytest configuration (no app services or production data):
  PYTHONPATH=futureagi pytest -c /dev/null --confcutdir=<this-directory> <this-file>
Requires METRICS_READER_CLOUD=1 on the owned disposable test VM. These tests use
real Django transactions and the same lifecycle advisory key, with a tiny
metrics-owned synthetic pin table. They do NOT claim provider/real-model binding.
"""

import hashlib
import multiprocessing
import os
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import uuid4

import pytest

from tracer.services.clickhouse.application_read_transport import (
    create_metrics_bounded_read_transport,
)
from tracer.services.derived_metrics.reader_lifetime import (
    IncapableReadLane,
    PreparedGuardStore,
    ReadAcquisitionTimeout,
    ReadBudget,
    ReadCancelled,
    ReadCleanupPending,
    ReadDeadlineExceeded,
    ReadReceipt,
    bounded_read,
    prepare_capture,
    recover_read,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("METRICS_READER_CLOUD") != "1", reason="owned cloud VM only"
)

PROJECT = "00000000-0000-0000-0000-000000000151"
LOCK = int.from_bytes(
    hashlib.sha256(f"metric-lifecycle:{PROJECT}".encode()).digest()[:8],
    "big",
    signed=True,
)
PG = {"host": "127.0.0.1", "port": 5432, "user": "postgres", "dbname": "postgres"}
LONG_SQL = "SELECT sleepEachRow(0.1) FROM numbers(30)"


def pg():
    import psycopg

    return psycopg.connect(**PG, autocommit=True)


def ch():
    from clickhouse_driver import Client

    return Client("127.0.0.1")


@dataclass(frozen=True)
class SyntheticCapture:
    receipt_file: str | None = None
    port: int = 5432

    def __call__(self, receipt, budget):
        import django
        from django.conf import settings
        from django.db import connections

        if not settings.configured:
            settings.configure(
                DATABASES={
                    "default": {
                        "ENGINE": "django.db.backends.postgresql",
                        "NAME": "postgres",
                        "USER": "postgres",
                        "HOST": "127.0.0.1",
                        "PORT": self.port,
                        "OPTIONS": {"connect_timeout": 2},
                    }
                },
                INSTALLED_APPS=[],
            )
            django.setup()
        db = connections["default"]

        def capture():
            # Synthetic substitute for capture_pin's model access; the same PG
            # transaction/advisory semantics, never fixture authority for BL01-Q.
            with db.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", [LOCK])
                cursor.execute(
                    "INSERT INTO metrics_reader_test_pins VALUES (%s)",
                    [receipt.query_id],
                )
            return PROJECT

        try:
            result = prepare_capture(db, receipt, budget, capture)
            if self.receipt_file:
                Path(self.receipt_file).write_text(receipt.query_id)
            return result
        finally:
            db.close()


@pytest.fixture(scope="module", autouse=True)
def schema():
    if os.environ.get("METRICS_READER_CLOUD") != "1":
        yield
        return
    with pg() as db:
        assert not db.execute("SELECT gid FROM pg_prepared_xacts").fetchall()
        db.execute("CREATE TABLE metrics_reader_test_pins (id text primary key)")
    client = ch()
    client.execute(
        "CREATE TABLE metrics_reader_local (value UInt64) ENGINE=MergeTree ORDER BY value"
    )
    client.execute("INSERT INTO metrics_reader_local VALUES (1), (2)")
    yield
    with pg() as db:
        assert not db.execute("SELECT gid FROM pg_prepared_xacts").fetchall()
        assert (
            db.execute("SELECT count(*) FROM metrics_reader_test_pins").fetchone()[0]
            == 0
        )
        db.execute("DROP TABLE metrics_reader_test_pins")
    client.execute("DROP TABLE metrics_reader_local")
    client.disconnect()


@pytest.fixture
def budget():
    # Test-only numbers, NOT approved production latency or memory budgets.
    return ReadBudget(3, 5, 12, 3, 2, 128 * 1024**2, 1024**2, 1)


@pytest.fixture(params=["native", "http"])
def lane(request):
    client = ch()
    server = client.execute("SELECT toString(serverUUID())")[0][0]
    client.disconnect()
    return create_metrics_bounded_read_transport(
        host="127.0.0.1",
        port=9000 if request.param == "native" else 8123,
        database="default",
        server_uuid=server,
        protocol=request.param,
        user="default",
        password="",
        local_tables=("metrics_reader_local",),
    )


@pytest.fixture
def guards():
    return PreparedGuardStore(PG)


def invoke(lane, guards, budget, **kwargs):
    return bounded_read(
        kwargs.pop("sql", "SELECT value FROM metrics_reader_local ORDER BY value"),
        kwargs.pop("params", {}),
        capture=kwargs.pop("capture", SyntheticCapture()),
        lane=lane,
        control=kwargs.pop("control", lane),
        guards=guards,
        budget=budget,
        **kwargs,
    )


def progress_available():
    # Publication AND erasure both take this same lifecycle lock. A separate
    # connection is the negative control: early release changes False to True.
    with pg() as db:
        with db.transaction():
            acquired = db.execute(
                "SELECT pg_try_advisory_xact_lock(%s)", [LOCK]
            ).fetchone()[0]
            if acquired:
                # Synthetic publication then erasure under the SAME lifecycle
                # lock. This proves follower progress, not full model binding.
                marker = str(uuid4())
                db.execute("INSERT INTO metrics_reader_test_pins VALUES (%s)", [marker])
                db.execute("DELETE FROM metrics_reader_test_pins WHERE id=%s", [marker])
            return acquired


def wait_running(receipt_file):
    deadline = time.monotonic() + 10
    client = ch()
    try:
        while time.monotonic() < deadline:
            if receipt_file.exists():
                query_id = receipt_file.read_text()
                if client.execute(
                    "SELECT count() FROM system.processes WHERE query_id=%(id)s",
                    {"id": query_id},
                )[0][0]:
                    return ReadReceipt(query_id)
            time.sleep(0.02)
    finally:
        client.disconnect()
    raise AssertionError("real query did not become live")


@pytest.mark.parametrize(
    "field,value",
    [
        ("execute_s", 0),
        ("cancel_s", float("inf")),
        ("acquire_s", float("nan")),
        ("release_s", -1),
        ("transport_s", True),
        ("memory_bytes", 0),
        ("result_bytes", 1.5),
        ("threads", False),
    ],
)
def test_budget_refuses_unbounded_values(budget, field, value):
    with pytest.raises(ValueError):
        replace(budget, **{field: value})


def test_normal_native_and_http_read_release_protection(lane, guards, budget):
    rows = invoke(lane, guards, budget)
    assert [row[0] for row in rows] == [1, 2]
    assert all(type(row[0]) is int for row in rows)
    assert progress_available()


def test_native_and_http_bind_typed_parameters(lane, guards, budget):
    from datetime import UTC, datetime

    rows = invoke(
        lane,
        guards,
        budget,
        sql="SELECT toString(%(at)s), %(text)s",
        params={"at": datetime(2026, 11, 5, tzinfo=UTC), "text": "synthetic ' quote"},
    )
    assert list(rows[0]) == ["2026-11-05 00:00:00", "synthetic ' quote"]
    assert progress_available()


def test_result_types_order_and_limit_match_native(lane, guards, budget):
    rows = invoke(
        lane,
        guards,
        budget,
        sql=(
            "SELECT CAST(number AS UInt64), "
            "CAST(-9223372036854775808 AS Int64), "
            "CAST(18446744073709551615 AS UInt64), "
            "CAST(number = 1 AS Bool), '1', "
            "CAST(NULL AS Nullable(Int64)) "
            "FROM numbers(3) ORDER BY number DESC LIMIT 2"
        ),
    )
    assert [list(row) for row in rows] == [
        [2, -(2**63), 2**64 - 1, False, "1", None],
        [1, -(2**63), 2**64 - 1, True, "1", None],
    ]
    # Equality alone would accept True == 1. Do not coerce numeric strings or
    # booleans and accidentally hide a native/HTTP representation regression.
    assert [[type(value) for value in row] for row in rows] == [
        [int, int, int, bool, str, type(None)],
        [int, int, int, bool, str, type(None)],
    ]
    assert progress_available()


def test_compiler_boolean_predicates_preserve_admission(lane, guards, budget):
    rows = invoke(
        lane,
        guards,
        budget,
        sql=(
            "SELECT value FROM metrics_reader_local "
            "WHERE value >= %(lower)s AND (value = 1 OR value = 2) ORDER BY value"
        ),
        params={"lower": 2},
    )
    assert [int(row[0]) for row in rows] == [2]
    assert progress_available()
    # Structural connectors must not mask an otherwise forbidden descendant.
    with pytest.raises(IncapableReadLane):
        lane._check_local_sql(
            "SELECT 1 WHERE 1 = 1 AND (1 = 0 OR dictGet('remote', 'value', 1) = 1)",
            budget,
        )
    with pytest.raises(IncapableReadLane):
        lane._check_local_sql(
            "SELECT 1 WHERE 1 = 1 AND 1 IN (SELECT number FROM system.numbers)",
            budget,
        )


def test_blocked_pg_acquisition_is_bounded_and_never_submits(lane, guards, budget):
    with pg() as blocker:
        with blocker.transaction():
            blocker.execute("SELECT pg_advisory_xact_lock(%s)", [LOCK])
            start = time.monotonic()
            with pytest.raises(ReadAcquisitionTimeout):
                invoke(lane, guards, replace(budget, acquire_s=0.5))
            assert time.monotonic() - start < 5
    assert progress_available()
    with pg() as db:
        assert db.execute("SELECT count(*) FROM pg_prepared_xacts").fetchone()[0] == 0


def test_real_query_cancel_ack_precedes_release(lane, guards, budget, tmp_path):
    receipt_file = tmp_path / "cancel-receipt"
    cancel = threading.Event()
    observations = []

    def interrupt():
        wait_running(receipt_file)
        observations.append(progress_available())
        cancel.set()

    thread = threading.Thread(target=interrupt)
    thread.start()
    with pytest.raises(ReadCancelled):
        invoke(
            lane,
            guards,
            budget,
            sql=LONG_SQL,
            cancel=cancel,
            capture=SyntheticCapture(str(receipt_file)),
        )
    thread.join(10)
    assert not thread.is_alive()
    assert observations == [False]
    assert progress_available()
    client = ch()
    assert (
        client.execute(
            "SELECT count() FROM system.processes WHERE query_id=%(id)s",
            {"id": receipt_file.read_text()},
        )[0][0]
        == 0
    )
    client.disconnect()


def test_transport_timeout_is_not_server_cancellation(lane, guards, budget, tmp_path):
    receipt_file = tmp_path / "interrupted-receipt"
    # A very short receive timeout drops a real native/HTTP connection during a
    # long server query. Break the independent control lane too: protect, don't
    # mistake either network failure for terminal acknowledgement.
    with pytest.raises(ReadCleanupPending) as error:
        invoke(
            lane,
            guards,
            replace(budget, transport_s=0.2),
            sql=LONG_SQL,
            capture=SyntheticCapture(str(receipt_file)),
            control=replace(lane, port=19001),
        )
    receipt = error.value.receipt
    assert receipt.query_id == receipt_file.read_text()
    client = ch()
    client.execute("SYSTEM FLUSH LOGS")  # owned synthetic test server only
    assert (
        client.execute(
            "SELECT count() FROM system.query_log WHERE query_id=%(id)s",
            {"id": receipt.query_id},
        )[0][0]
        > 0
    )
    client.disconnect()
    assert not progress_available()
    # This would FAIL if cleanup simply exited the pin context on timeout.
    with pg() as db:
        assert db.execute(
            "SELECT 1 FROM pg_prepared_xacts WHERE gid=%s", [receipt.gid]
        ).fetchone()
    recover_read(receipt, control=lane, guards=guards, budget=budget)
    assert progress_available()


def test_wall_deadline_kills_client_but_retains_guard_until_ack(lane, guards, budget):
    start = time.monotonic()
    with pytest.raises(ReadDeadlineExceeded):
        invoke(lane, guards, replace(budget, execute_s=0.8), sql=LONG_SQL)
    assert time.monotonic() - start < budget.total_s
    assert progress_available()


def test_absent_query_is_not_terminal_evidence(lane, guards, budget):
    receipt = ReadReceipt(str(uuid4()))
    SyntheticCapture()(receipt, budget)
    with pytest.raises(ReadCleanupPending):
        recover_read(
            receipt, control=lane, guards=guards, budget=replace(budget, cancel_s=0.8)
        )
    assert not progress_available()
    # Fixture alone knows this ID was NEVER submitted. Production recovery may
    # not make this inference from an empty process/query-log lookup.
    guards.release(receipt, budget)
    assert progress_available()


def _crash_owner(lane, guards, budget, receipt_file):
    invoke(lane, guards, budget, sql=LONG_SQL, capture=SyntheticCapture(receipt_file))


def test_supervisor_crash_keeps_pg_protection(lane, guards, budget, tmp_path):
    receipt_file = tmp_path / "crash-receipt"
    owner = multiprocessing.get_context("spawn").Process(
        target=_crash_owner, args=(lane, guards, budget, str(receipt_file))
    )
    owner.start()
    receipt = wait_running(receipt_file)
    owner.kill()
    owner.join(5)
    assert not owner.is_alive()
    assert not progress_available()
    recover_read(receipt, control=lane, guards=guards, budget=budget)
    assert progress_available()


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM remote('127.0.0.1', system.one)",
        "SELECT * FROM system.numbers",
        "SELECT dictGet('remote_dictionary', 'value', 1)",
        "SELECT 1; SELECT 2",
        "DROP TABLE metrics_reader_local",
        "SELECT 1 SETTINGS max_execution_time=0",
    ],
)
def test_incapable_sources_refused_before_data_submission(lane, budget, sql):
    with pytest.raises(IncapableReadLane):
        lane._check_local_sql(sql, budget)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1 FORMAT JSON SETTINGS max_execution_time=0",
        "SELECT 1 UNION ALL SELECT sleepEachRow(0.1) FROM numbers(30) SETTINGS max_execution_time=0",
        "SELECT 1 UNION ALL SELECT sleepEachRow(0.1) FROM numbers(1) SETTINGS log_queries=0",
        "SELECT 1 FORMAT JSON",
        "SELECT 1 UNION ALL SELECT 2 FORMAT JSONCompact",
        "SELECT * FROM (SELECT 1 SETTINGS log_queries=0)",
    ],
)
def test_reader_owned_clauses_rejected_at_every_depth(lane, budget, sql):
    # Parse only: never submit a query with a disabled server cap.
    with pytest.raises(IncapableReadLane):
        lane._check_local_sql(sql, budget)


@pytest.mark.parametrize(
    "profile",
    [
        "log_queries_min_query_duration_ms=10000",
        "log_queries_min_type='EXCEPTION_WHILE_PROCESSING'",
        "log_queries=0, log_queries_probability=0, "
        "log_queries_min_query_duration_ms=10000, "
        "log_queries_min_type='EXCEPTION_WHILE_PROCESSING'",
    ],
)
def test_inherited_log_filters_cannot_strand_successful_read(
    lane, guards, budget, tmp_path, profile
):
    client = ch()
    user = "metrics_reader_log_profile"
    receipt_file = tmp_path / "log-profile"
    try:
        client.execute(f"CREATE USER {user} SETTINGS {profile}")
        client.execute(f"GRANT SELECT, KILL QUERY ON *.* TO {user}")
        rows = invoke(
            replace(lane, user=user),
            guards,
            budget,
            capture=SyntheticCapture(str(receipt_file)),
        )
        assert [row[0] for row in rows] == [1, 2]
        assert all(type(row[0]) is int for row in rows)
        assert progress_available()
        # Do not flush logs: ordinary bounded cleanup must find the receipt.
        assert (
            client.execute(
                "SELECT count() FROM system.query_log WHERE query_id=%(id)s "
                "AND type='QueryFinish'",
                {"id": receipt_file.read_text()},
            )[0][0]
            == 1
        )
        with pg() as db:
            assert not db.execute("SELECT gid FROM pg_prepared_xacts").fetchall()
    finally:
        client.execute(f"DROP USER IF EXISTS {user}")
        client.disconnect()


@pytest.mark.parametrize(
    "profile",
    [
        "log_queries_min_query_duration_ms=10000 CONST",
        "log_queries_min_type='EXCEPTION_WHILE_PROCESSING' CONST",
    ],
)
def test_locked_log_filter_refuses_before_data_and_releases_guard(
    lane, guards, budget, tmp_path, profile
):
    client = ch()
    user = "metrics_reader_locked_profile"
    receipt_file = tmp_path / "locked-profile"
    try:
        client.execute(f"CREATE USER {user} SETTINGS {profile}")
        client.execute(f"GRANT SELECT, KILL QUERY ON *.* TO {user}")
        with pytest.raises(IncapableReadLane):
            invoke(
                replace(lane, user=user),
                guards,
                budget,
                capture=SyntheticCapture(str(receipt_file)),
            )
        assert progress_available()
        query_id = receipt_file.read_text()
        client.execute("SYSTEM FLUSH LOGS")  # owned synthetic server only
        assert (
            client.execute(
                "SELECT count() FROM system.query_log WHERE query_id=%(id)s",
                {"id": query_id},
            )[0][0]
            == 0
        )
        with pg() as db:
            assert not db.execute("SELECT gid FROM pg_prepared_xacts").fetchall()
    finally:
        client.execute(f"DROP USER IF EXISTS {user}")
        client.disconnect()


def test_refused_query_clauses_release_without_data_submission(lane, guards, budget):
    with pytest.raises(IncapableReadLane):
        invoke(
            lane,
            guards,
            budget,
            sql="SELECT 1 UNION ALL SELECT 2 SETTINGS log_queries=0",
        )
    assert progress_available()


def test_disabled_prepared_transactions_refuse_lane(lane, guards, budget):
    # Second owned PG16 instance uses the server default (max_prepared=0).
    with pytest.raises(IncapableReadLane):
        invoke(lane, guards, budget, capture=SyntheticCapture(port=5433))
    assert progress_available()


def test_refused_compiler_subset_does_not_leak_guard(lane, guards, budget):
    with pytest.raises(IncapableReadLane):
        invoke(lane, guards, budget, sql="SELECT * FROM system.numbers")
    assert progress_available()


def test_wrong_physical_shard_refuses_before_data(lane, guards, budget):
    with pytest.raises(IncapableReadLane):
        invoke(replace(lane, server_uuid=str(uuid4())), guards, budget)
    assert progress_available()


def test_cancel_before_submit_releases_only_own_capture(lane, guards, budget):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ReadCancelled):
        invoke(lane, guards, budget, cancel=cancel)
    assert progress_available()


def test_capture_backend_must_exit_before_guard_release(guards, budget):
    import psycopg

    receipt = ReadReceipt(str(uuid4()))
    SyntheticCapture()(receipt, budget)
    # Model a late PREPARE acknowledgement: the capture backend is still present.
    # An empty client process list must not let a PG submission land after cleanup.
    with psycopg.connect(**PG, application_name=receipt.gid, autocommit=True):
        with pytest.raises(ReadCleanupPending):
            guards.release(receipt, replace(budget, release_s=0.2))
        assert not progress_available()
    guards.release(receipt, budget)  # fixture knows no CH query was submitted
    assert progress_available()


def test_existing_caller_transaction_is_not_discarded(guards, budget):
    from django.db import connections, transaction

    initial = ReadReceipt(str(uuid4()))
    SyntheticCapture()(initial, budget)
    guards.release(initial, budget)
    db = connections["default"]
    marker = str(uuid4())
    with transaction.atomic():
        with db.cursor() as cursor:
            cursor.execute("INSERT INTO metrics_reader_test_pins VALUES (%s)", [marker])
        with pytest.raises(IncapableReadLane):
            prepare_capture(db, ReadReceipt(str(uuid4())), budget, lambda: None)
        with db.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM metrics_reader_test_pins WHERE id=%s", [marker]
            )
            assert cursor.fetchone()[0] == marker
    with pg() as connection:
        assert (
            connection.execute(
                "DELETE FROM metrics_reader_test_pins WHERE id=%s RETURNING id",
                [marker],
            ).fetchone()[0]
            == marker
        )
    db.close()


def _large_ipc_result():
    return b"synthetic" * 4096


def test_ipc_result_is_byte_bounded_and_worker_reaped():
    from tracer.services.derived_metrics.reader_lifetime import _run_stage

    start = time.monotonic()
    state, value = _run_stage(
        _large_ipc_result,
        (),
        deadline=start + 2,
        reap_deadline=start + 3,
        payload_limit=128,
    )
    assert (state, value) == ("failed", None)
    assert time.monotonic() - start < 3


def test_native_default_transport_is_unchanged():
    from tracer.services.clickhouse.application_read_transport import (
        application_native_read_transport,
    )

    class Socket:
        def __init__(self):
            self.timeout = 7

        def gettimeout(self):
            return self.timeout

        def settimeout(self, value):
            self.timeout = value

    class Connection:
        connected = True
        send_receive_timeout = 7
        socket = Socket()

    class Client:
        connection = Connection()

    client = Client()
    with application_native_read_transport(client):
        assert client.connection.send_receive_timeout is None
        assert client.connection.socket.gettimeout() is None
    assert client.connection.send_receive_timeout == 7
    assert client.connection.socket.gettimeout() == 7
