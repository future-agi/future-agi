"""Exact batched checkpoint SQL, active validation, and native proof brackets."""

import json
import re
import sqlite3
from datetime import datetime
from unittest.mock import Mock

import pytest

from tracer.services.clickhouse.v2.property_catalog import durable_lifecycle as dl
from tracer.services.clickhouse.v2.property_catalog import state_store as state
from tracer.services.clickhouse.v2.property_catalog.dev_runtime import (
    NativeCatalogClient,
)
from tracer.services.clickhouse.v2.property_catalog.durable_native_writer import (
    DurableNativeCatalogWriter,
)
from tracer.services.clickhouse.v2.property_catalog.mutation_lock import (
    InProcessCatalogMutationSerializer,
)
from tracer.services.clickhouse.v2.property_catalog.native_write_proof import (
    NativeWriteProofError,
)
from tracer.tests.test_property_catalog_native_read_agreement import observed_proof
from tracer.tests.test_property_catalog_native_write_proof import DATABASE
from tracer.tests.test_property_catalog_physical_snapshot_lifecycle import (
    INITIAL_UNTIL,
    _PhysicalSnapshot,
)
from tracer.tests.test_property_catalog_state_read_agreement import driver_for


class CheckpointSQL:
    """Execute the actual window/predicate/order/limit SQL with bound parameters.

    SQLite substitutes only database qualification and driver parameter syntax;
    it does not implement a separate checkpoint selection algorithm.
    """

    catalog_database = DATABASE

    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def query(self, sql, params, *, timeout_ms):
        self.calls.append((sql, params, timeout_ms))
        assert 0 < timeout_ms <= 1000
        sql = sql.replace(f"`{DATABASE}`.", "")
        params = dict(params)
        if "stream_pairs" in params:
            pairs = params.pop("stream_pairs")
            placeholders = []
            for index, (adapter, stream) in enumerate(pairs):
                params[f"adapter{index}"] = adapter
                params[f"stream{index}"] = stream
                placeholders.append(f"(:adapter{index}, :stream{index})")
            sql = sql.replace("%(stream_pairs)s", f"({','.join(placeholders)})")
        sql = re.sub(r"%\((\w+)\)s", r":\1", sql)
        with sqlite3.connect(":memory:") as db:
            names = state._CHECKPOINT_COLUMNS
            db.execute(f"CREATE TABLE property_catalog_checkpoints ({','.join(names)})")
            db.executemany(
                f"INSERT INTO property_catalog_checkpoints VALUES ({','.join('?' for _ in names)})",
                [
                    tuple(
                        json.dumps(row[name])
                        if isinstance(row[name], list)
                        else row[name].isoformat()
                        if isinstance(row[name], datetime)
                        else row[name]
                        for name in names
                    )
                    for row in self.rows
                ],
            )
            result = [
                dict(zip(names, row, strict=True)) for row in db.execute(sql, params)
            ]
        for row in result:
            row["gap_reasons"] = json.loads(row["gap_reasons"])
        return result


@pytest.fixture
def case():
    original = _PhysicalSnapshot()
    original.catalog_database = DATABASE
    rows = [
        state._checkpoint_row(value, now=INITIAL_UNTIL, version=index + 1)
        for index, value in enumerate(original.checkpoints.values())
    ]
    client = CheckpointSQL(rows)
    store = make_store(client)
    params = {
        "organization_id": original.scope.organization_id,
        "workspace_id": original.scope.workspace_id,
        "catalog_epoch": original.scope.catalog_epoch,
        "catalog_revision": original.lease.catalog_revision,
        "build_token": original.lease.build_token,
        "stream_pairs": tuple(original.checkpoints),
    }
    reader = dl.ClickHouseLifecycleStateReader(
        original, database=DATABASE, checkpoint_store=store, timeout_ms=1000
    )
    return original, client, store, params, reader


def make_store(client):
    return state.ClickHouseCatalogStateStore(
        client,
        database=DATABASE,
        serializer=InProcessCatalogMutationSerializer(),
        timeout_ms=1000,
    )


def single_reads(store, params):
    scope = {key: value for key, value in params.items() if key != "stream_pairs"}
    return {
        pair: store.load_checkpoint_write(
            **scope, source_adapter=pair[0], producer_stream_id=pair[1]
        )
        for pair in params["stream_pairs"]
    }


def test_real_store_active_uses_one_batch_instead_of_ten_single_queries(case):
    original, client, store, params, reader = case
    assert single_reads(store, params) == original.checkpoints
    assert len(client.calls) == 10
    client.calls.clear()
    store.load_checkpoint_write = Mock(side_effect=AssertionError("single loader used"))
    active = reader.load_latest_active(original.scope)
    assert len(active.streams) == 10
    assert active.build_plan == original.plan
    assert len(client.calls) == 1
    sql, observed, timeout = client.calls[0]
    assert "PARTITION BY source_adapter, producer_stream_id" in sql
    assert "WHERE _version=latest_version" in sql
    assert "AND (source_adapter, producer_stream_id) IN %(stream_pairs)s" in sql
    assert "projection_version=" not in sql
    assert "DISTINCT" not in sql and "FINAL" not in sql and "LIMIT BY" not in sql
    assert observed == {**params, "row_limit": 321}
    assert timeout == 1000
    store.load_checkpoint_write.assert_not_called()


def test_latest_per_exact_pair_excludes_other_builds_but_keeps_metadata_variants(case):
    original, client, store, params, _ = case
    row = client.rows[0]
    client.rows.extend(
        [
            dict(row, _version=0, projection_version=1),
            dict(row, worker_id="another-worker"),
            dict(row, catalog_revision=99, _version=999),
            dict(
                row, workspace_id="99999999-9999-4999-8999-999999999999", _version=999
            ),
        ]
    )
    assert store.load_checkpoint_writes(**params) == original.checkpoints


@pytest.mark.parametrize("present", [0, 3, 10])
def test_resume_batch_preserves_missing_checkpoint_and_plan_order(case, present):
    original, client, store, _, reader = case
    client.rows = client.rows[:present]
    store.load_checkpoint_write = Mock(side_effect=AssertionError("single loader used"))
    expected = tuple(original.checkpoints.values())[:present]
    assert reader.load_resumes(original.lease) == expected
    assert len(client.calls) == 1
    store.load_checkpoint_write.assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [("projection_version", 2), ("source_version_fence", 2), ("terminal", 0)],
)
def test_resume_batch_does_not_accept_invalid_present_checkpoint(case, field, value):
    original, client, store, _, reader = case
    client.rows.append(dict(client.rows[0], _version=100, **{field: value}))
    store.load_checkpoint_write = Mock(side_effect=AssertionError("single loader used"))
    with pytest.raises(dl.DurableLifecycleError):
        reader.load_resumes(original.lease)
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    "field,value", [("projection_version", 2), ("source_cursor", "changed")]
)
def test_same_version_logical_variant_is_not_hidden(case, field, value):
    _, client, store, params, _ = case
    client.rows.append(dict(client.rows[0], **{field: value}))
    with pytest.raises(state.PropertyCatalogStateConflict, match="different rows"):
        store.load_checkpoint_writes(**params)


@pytest.mark.parametrize("count,allowed", [(32, True), (33, False)])
def test_per_stream_physical_cap_even_below_global_sentinel(case, count, allowed):
    original, client, store, params, _ = case
    client.rows = [client.rows[0]] * count + client.rows[1:]
    assert len(client.rows) < 321
    if allowed:
        assert store.load_checkpoint_writes(**params) == original.checkpoints
    else:
        with pytest.raises(
            state.PropertyCatalogStateConflict, match="checkpoint stream.*row cap"
        ):
            store.load_checkpoint_writes(**params)


def test_global_sentinel_is_rejected_before_truncated_result_is_trusted(case):
    original, client, store, params, _ = case
    client.rows = [row for row in client.rows for _ in range(32)]
    assert store.load_checkpoint_writes(**params) == original.checkpoints
    client.rows.extend([client.rows[0]] * 100)
    with pytest.raises(
        state.PropertyCatalogStateConflict, match="checkpoint batch.*row cap"
    ):
        store.load_checkpoint_writes(**params)


@pytest.mark.parametrize("change", ["missing", "projection", "fence"])
def test_real_active_validation_keeps_missing_projection_and_fence_rejections(
    case, change
):
    original, client, store, _, reader = case
    store.load_checkpoint_write = Mock(side_effect=AssertionError("single loader used"))
    if change == "missing":
        client.rows.pop()
    else:
        field = (
            "projection_version" if change == "projection" else "source_version_fence"
        )
        # Preserve the older valid row to catch accidentally filtering the new
        # invalid projection before selecting the physical latest version.
        client.rows.append(dict(client.rows[0], _version=100, **{field: 2}))
    with pytest.raises(dl.DurableLifecycleError):
        reader.load_latest_active(original.scope)
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("organization_id", "99999999-9999-4999-8999-999999999999"),
        ("workspace_id", "99999999-9999-4999-8999-999999999999"),
        ("catalog_epoch", 2),
        ("catalog_revision", 4),
        ("build_token", "99999999-9999-4999-8999-999999999999"),
        ("source_adapter", "unexpected"),
        ("producer_stream_id", "99999999-9999-4999-8999-999999999999"),
    ],
)
def test_untrusted_transport_cannot_return_wrong_scope_or_stream(case, field, value):
    _, client, store, params, _ = case
    client.query = Mock(return_value=[dict(client.rows[0], **{field: value})])
    with pytest.raises(state.PropertyCatalogStateConflict, match="scope|unrequested"):
        store.load_checkpoint_writes(**params)


def test_explicit_pair_selection_missing_result_and_input_bound(case):
    original, client, store, params, _ = case
    pair = params["stream_pairs"][0]
    assert store.load_checkpoint_writes(**{**params, "stream_pairs": (pair,)}) == {
        pair: original.checkpoints[pair]
    }
    client.rows.clear()
    assert store.load_checkpoint_writes(**params) == {}
    for pairs in ((), (pair, pair), params["stream_pairs"] + (pair,)):
        with pytest.raises(ValueError):
            store.load_checkpoint_writes(**{**params, "stream_pairs": pairs})
    assert len(client.calls) == 2
    assert len(original.plan.streams) == state._MAX_CHECKPOINT_STREAMS == 10


def test_native_strict_batch_has_two_fresh_attests_not_twenty_and_no_cache(
    case, tmp_path
):
    original, client, _, params, _ = case
    proof, _ = observed_proof(
        tmp_path,
        [],
        [],
        left_columns=state._CHECKPOINT_COLUMNS,
        right_columns=state._CHECKPOINT_COLUMNS,
    )
    proof.attest = Mock()
    for index, connection in enumerate(proof.connections, start=1):
        connection.driver.host = f"member-{index}.internal"
        connection.driver.port = 9000

        def execute_read(sql, params, *, timeout_ms, settings):
            assert settings["readonly"] == 2 and settings["use_query_cache"] == 0
            assert settings["result_overflow_mode"] == "throw"
            rows = client.query(sql, params, timeout_ms=timeout_ms)
            return (
                [
                    tuple(row[name] for name in state._CHECKPOINT_COLUMNS)
                    for row in rows
                ],
                state._CHECKPOINT_COLUMNS,
                {},
            )

        connection.driver.execute_read = execute_read
    driver = driver_for()
    writer = DurableNativeCatalogWriter(
        driver, directory=tmp_path, proof=proof, member_name=proof.connections[0].name
    )
    store = make_store(
        NativeCatalogClient(driver, database=DATABASE, durable_writer=writer)
    )
    assert single_reads(store, params) == original.checkpoints
    assert proof.attest.call_count == 20 and len(client.calls) == 20
    proof.attest.reset_mock()
    client.calls.clear()
    assert store.load_checkpoint_writes(**params) == original.checkpoints
    assert proof.attest.call_count == 2 and len(client.calls) == 2
    assert store.load_checkpoint_writes(**params) == original.checkpoints
    assert proof.attest.call_count == 4 and len(client.calls) == 4
    # Strict checkpoint agreement does not inherit the activation exemption.
    first_execute = proof.connections[1].driver.execute_read

    def disagree(*args, **kwargs):
        rows, columns, stats = first_execute(*args, **kwargs)
        return rows + rows[:1], columns, stats

    proof.connections[1].driver.execute_read = disagree
    with pytest.raises(NativeWriteProofError, match="disagrees"):
        store.load_checkpoint_writes(**params)
    driver.execute_read.assert_not_called()
