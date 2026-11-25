# ruff: noqa: F811 -- shared real PG/CH fixtures
"""Real source -> publication/pin/reader component interactions, CLOUD ONLY.

No FixtureAuthority, synthetic pin table, producer enrollment or endpoint claim.
"""

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from django.db import connection

from tracer.models.derived_metric import MetricGenerationPin, MetricReadManifest
from tracer.services.derived_metrics import base_list_current_source as source
from tracer.services.derived_metrics.base_list_current_reader import PinnedStoreCount
from tracer.services.derived_metrics.base_list_current_source_publication import (
    capture_source_publication_cut,
    source_cut_ceilings,
    verify_source_manifest,
)
from tracer.services.derived_metrics.reader_lifetime import (
    IncapableReadLane,
    PinCapture,
    PreparedGuardStore,
    ReadBudget,
    ReadCancelled,
    ReadDeadlineExceeded,
    bounded_read,
)
from tracer.services.derived_metrics.reader_transport import MetricsReadTransport
from tracer.services.derived_metrics.storage import FIELD_VALUES
from tracer.tests._assembly_reader_queries import SlowPinnedQuery
from tracer.tests.test_derived_metric_publication_clickhouse import (  # noqa: F401 -- shared pytest fixtures
    field_query,
    live_writer,
    metric_ch,
    prepare_field,
    publication_store,
)

pytestmark = pytest.mark.django_db(transaction=True)

# ReadReceipt.gid in reader_lifetime.py.
READER_GID_PREFIX = "metrics-reader:"


@pytest.fixture(autouse=True)
def prepared_transaction_capable_pg(transactional_db):
    """Fail, never skip, on a PG that cannot PREPARE; fail on leaked reader GIDs.

    The pinned reader lane PREPAREs its guard transaction, so these tests need
    max_prepared_transactions > 0 (docker-compose.test.yml sets it for test-db).
    A leaked metrics-reader GID keeps holding the advisory lock and blocks
    dropping or reusing the test database, so teardown rolls it back and fails.
    """
    import psycopg
    from psycopg import sql

    with connection.cursor() as cursor:
        cursor.execute("SHOW max_prepared_transactions")
        capacity = int(cursor.fetchone()[0])
    if capacity <= 0:
        pytest.fail(
            "test_session_assembly_runtime needs a Postgres with "
            "max_prepared_transactions > 0 (found 0). Start the test database "
            "from futureagi/docker-compose.test.yml, which runs test-db with "
            "-c max_prepared_transactions=16."
        )
    yield
    with psycopg.connect(**connection_options(), autocommit=True) as pg:
        leaked = [
            gid
            for (gid,) in pg.execute(
                "SELECT gid FROM pg_prepared_xacts "
                "WHERE database = current_database() AND starts_with(gid, %s)",
                [READER_GID_PREFIX],
            ).fetchall()
        ]
        for gid in leaked:
            pg.execute(sql.SQL("ROLLBACK PREPARED {}").format(sql.Literal(gid)))
    assert not leaked, f"metrics reader leaked prepared transactions: {leaked}"


@pytest.fixture
def trace_source(live_writer, settings, monkeypatch):
    import clickhouse_connect

    from tracer.services.clickhouse.v2 import trace_writer

    writer = live_writer
    database = writer.store.identity()[1]
    schema = Path(trace_writer.__file__).parent / "schema/015_traces_and_trace_dict.sql"
    create = schema.read_text().split("CREATE TABLE IF NOT EXISTS traces", 1)[1]
    create = create.split("SETTINGS index_granularity = 8192;", 1)[0]
    writer.store.execute(
        "CREATE TABLE IF NOT EXISTS traces"
        + create
        + "SETTINGS index_granularity = 8192"
    )
    client = clickhouse_connect.get_client(
        host="127.0.0.1", port=18123, database=database
    )
    monkeypatch.setattr(trace_writer, "_get_client", lambda: client)
    settings.CLICKHOUSE_V2 = {
        **settings.CLICKHOUSE_V2,
        "CH25_DATABASE": database,
        "CH25_HOST": "127.0.0.1",
        "CH25_HTTP_PORT": 18123,
    }
    settings.FI_SESSIONS_SOURCE_INTENTS_ENABLED = True
    settings.FI_SESSIONS_SOURCE_INTENT_LANE = "direct-local-mergetree-v1"
    from tracer.tests.source_intent_cloud import fixture

    scope = source.SourceScope(writer.org_id, writer.lease.project_id)
    try:
        yield writer, fixture(scope), trace_writer
    finally:
        client.close()


def complete(trace_source):
    writer, (scope, rows, pages, target), transport = trace_source
    token = source.begin_source_intent(scope, uuid4(), pages, expected_stores=(target,))
    transport.insert_trace_rows(rows, source_intent=token, selected_store=target)
    cut = capture_source_publication_cut(
        scope, server_id=target.server_id, database=target.database
    )
    return token, cut


def prepared(writer, cut):
    attempt = writer.begin(
        ceilings=source_cut_ceilings(cut),
        ledger={"dirty_after": [{"unit_id": "unit-a"}]},
    )
    sql, params = field_query()
    writer.freeze(attempt, FIELD_VALUES, sql, params)
    writer.seal(attempt)
    return attempt


def test_actual_source_receipts_bind_existing_manifest_and_hash(trace_source):
    writer, (_, _, _, target), _ = trace_source
    token, cut = complete(trace_source)
    attempt = prepared(writer, cut)
    manifest = writer.stage_and_publish(attempt, source_cut=cut)
    assert manifest.visibility_receipts["source_cut"]["producer_complete"] is False
    verify_source_manifest(
        manifest, cut, server_id=target.server_id, database=target.database
    )
    assert writer.stage_and_publish(attempt, source_cut=cut).pk == manifest.pk
    with pytest.raises(source.SourceUnavailable):
        verify_source_manifest(
            manifest, cut, server_id=uuid4(), database=target.database
        )
    with pytest.raises(source.SourceUnavailable):
        verify_source_manifest(
            manifest,
            replace(cut, revision=cut.revision + 1),
            server_id=target.server_id,
            database=target.database,
        )
    manifest.generation += 1
    with pytest.raises(source.SourceUnavailable):
        verify_source_manifest(
            manifest, cut, server_id=target.server_id, database=target.database
        )


@pytest.mark.parametrize("uncertain", [False, True])
def test_pending_send_prevents_publication_without_losing_original_head(
    trace_source, uncertain
):
    writer, (scope, rows, pages, target), _ = trace_source
    _, cut = complete(trace_source)
    attempt = prepared(writer, cut)
    pending = source.begin_source_intent(
        scope, uuid4(), pages, expected_stores=(target,)
    )
    if uncertain:
        source.claim_dispatch(pending, target)
    with pytest.raises(source.SourceUnavailable):
        writer.stage_and_publish(attempt, source_cut=cut)
    assert not MetricReadManifest.objects.filter(published_by=attempt).exists()
    with pytest.raises(source.SourceUnavailable):
        capture_source_publication_cut(
            scope, server_id=target.server_id, database=target.database
        )


def test_scalar_publication_does_not_carry_source_cut_forward(trace_source):
    writer = trace_source[0]
    _, cut = complete(trace_source)
    first = writer.stage_and_publish(prepared(writer, cut), source_cut=cut)
    attempt, _ = prepare_field(writer, unit_id="other")
    second = writer.stage_and_publish(attempt)
    assert "source_cut" in first.visibility_receipts
    assert "source_cut" not in second.visibility_receipts


def connection_options():
    db = connection.settings_dict
    return {
        "host": db["HOST"],
        "port": int(db["PORT"]),
        "user": db["USER"],
        "password": db["PASSWORD"],
        "dbname": db["NAME"],
    }


def reader_parts(writer, protocol, compiler):
    shard = writer.store.identity()
    lane = MetricsReadTransport(
        host="127.0.0.1",
        port=19000 if protocol == "native" else 18123,
        database=shard[1],
        server_uuid=shard[0],
        protocol=protocol,
        user="default",
        password="",
        local_tables=(FIELD_VALUES.name,),
    )
    capture = PinCapture(
        dict(connection.settings_dict),
        str(writer.lease.project_id),
        query_compiler=compiler,
    )
    # Test-only capacity/budgets, not production qualification or p95 targets.
    return (
        capture,
        lane,
        PreparedGuardStore(connection_options()),
        ReadBudget(20, 8, 12, 4, 2, 128 * 1024**2, 1024**2, 1),
    )


@pytest.mark.parametrize("protocol", ["native", "http"])
def test_actual_pinned_manifest_rows_read_and_release(live_writer, protocol):
    writer = live_writer
    attempt, _ = prepare_field(writer)
    manifest = writer.stage_and_publish(attempt)
    compiler = PinnedStoreCount(
        str(writer.org_id),
        str(writer.lease.project_id),
        FIELD_VALUES.name,
        tuple(writer.store.identity()),
    )
    capture, lane, guards, budget = reader_parts(writer, protocol, compiler)
    rows = bounded_read(
        "SELECT 999",
        {},
        capture=capture,
        lane=lane,
        control=lane,
        guards=guards,
        budget=budget,
    )
    assert [list(row) for row in rows] == [[1]]
    assert type(rows[0][0]) is int
    assert not MetricGenerationPin.objects.filter(
        project_id=writer.lease.project_id
    ).exists()
    assert MetricReadManifest.objects.get(pk=manifest.pk).revoked_at is None
    # Exact selected-server mismatch is refused before a data query.
    wrong = replace(
        capture,
        query_compiler=replace(compiler, shard=(str(uuid4()), compiler.shard[1])),
    )
    with pytest.raises(IncapableReadLane):
        bounded_read(
            "SELECT 1",
            {},
            capture=wrong,
            lane=lane,
            control=lane,
            guards=guards,
            budget=budget,
        )


@pytest.mark.parametrize("protocol", ["native", "http"])
def test_real_live_query_retains_pin_lock_then_publication_and_erasure_progress(
    live_writer, protocol
):
    import psycopg
    from clickhouse_driver import Client

    writer = live_writer
    attempt, _ = prepare_field(writer)
    first = writer.stage_and_publish(attempt)
    capture, lane, guards, budget = reader_parts(
        writer, protocol, SlowPinnedQuery(str(writer.lease.project_id), str(first.pk))
    )
    cancelled = Event()
    lock = int.from_bytes(
        hashlib.sha256(f"metric-lifecycle:{writer.lease.project_id}".encode()).digest()[
            :8
        ],
        "big",
        signed=True,
    )
    client = Client("127.0.0.1", port=19000)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            bounded_read,
            "SELECT 999",
            {},
            capture=capture,
            lane=lane,
            control=lane,
            guards=guards,
            budget=budget,
            cancel=cancelled,
        )
        try:
            end = time.monotonic() + 30
            query_id = None
            while time.monotonic() < end and not future.done():
                with psycopg.connect(**connection_options(), autocommit=True) as pg:
                    gids = pg.execute(
                        "SELECT gid FROM pg_prepared_xacts WHERE database=current_database()"
                    ).fetchall()
                    for (gid,) in gids:
                        candidate = gid.removeprefix("metrics-reader:")
                        if client.execute(
                            "SELECT count() FROM system.processes WHERE query_id=%(q)s",
                            {"q": candidate},
                        )[0][0]:
                            query_id = candidate
                            with pg.transaction():
                                assert (
                                    pg.execute(
                                        "SELECT pg_try_advisory_xact_lock(%s)", [lock]
                                    ).fetchone()[0]
                                    is False
                                )
                            break
                if query_id:
                    break
                time.sleep(0.05)
            assert query_id, future.result() if future.done() else "query did not start"
            cancelled.set()
            with pytest.raises((ReadCancelled, ReadDeadlineExceeded)):
                future.result(40)
            assert (
                client.execute(
                    "SELECT count() FROM system.processes WHERE query_id=%(q)s",
                    {"q": query_id},
                )[0][0]
                == 0
            )
            client.execute("SYSTEM FLUSH LOGS")
            assert (
                client.execute(
                    "SELECT count() FROM system.query_log WHERE query_id=%(q)s AND type IN ('QueryFinish','ExceptionWhileProcessing')",
                    {"q": query_id},
                )[0][0]
                > 0
            )
            with psycopg.connect(**connection_options(), autocommit=True) as pg:
                assert not pg.execute(
                    "SELECT 1 FROM pg_prepared_xacts WHERE gid=%s",
                    [f"metrics-reader:{query_id}"],
                ).fetchone()
            assert not MetricGenerationPin.objects.filter(
                project_id=writer.lease.project_id
            ).exists()
            # Actual existing writer and actual revocation, not synthetic marker rows.
            next_attempt, _ = prepare_field(writer, unit_id="after-drain")
            next_manifest = writer.stage_and_publish(next_attempt)
            assert next_manifest.generation > first.generation
            from tracer.services.derived_metrics.deletion_protocol import _intent
            from tracer.services.derived_metrics.retention import purge_by_scope

            intent = _intent(
                organization_id=writer.org_id,
                project_id=writer.lease.project_id,
                scope="project",
            )
            purge_by_scope(writer.store, intent)
            next_manifest.refresh_from_db()
            assert next_manifest.revoked_at is not None
            assert (
                writer.store.execute(f"SELECT count() FROM {FIELD_VALUES.name}")[0][0]
                == 0
            )
        finally:
            cancelled.set()
            client.disconnect()
