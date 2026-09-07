"""Exact activation-query routing through real native agreement, fake I/O only."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tracer.services.clickhouse.v2.property_catalog import state_store as state
from tracer.services.clickhouse.v2.property_catalog.dev_runtime import (
    NativeCatalogClient,
    PropertyCatalogDevRuntimeError,
)
from tracer.services.clickhouse.v2.property_catalog.durable_native_writer import (
    DurableNativeCatalogWriter,
)
from tracer.services.clickhouse.v2.property_catalog.mutation_lock import (
    InProcessCatalogMutationSerializer,
)
from tracer.services.clickhouse.v2.property_catalog.native_write_proof import (
    _MAX_AGREEMENT_BYTES,
    _MAX_ROWS,
    NativeReadAgreement,
    NativeWriteProofError,
)
from tracer.tests.test_property_catalog_activation_latest_status import SCOPE, _record
from tracer.tests.test_property_catalog_native_read_agreement import observed_proof
from tracer.tests.test_property_catalog_native_write_proof import DATABASE

VARIANTS = (False, True)


def query_case(through_revision, database=DATABASE):
    params = dict(SCOPE)
    if through_revision:
        params["catalog_revision"] = 2
    return state.activation_latest_rows_sql(
        database, through_revision=through_revision
    ), params


def driver_for(database=DATABASE):
    return SimpleNamespace(
        database=database,
        user="catalog_writer",
        server_enforced_readonly=False,
        host="member-1.internal",
        port=9000,
        execute_read=Mock(side_effect=AssertionError("no local fallback")),
    )


def delegation_client(database=DATABASE):
    driver = driver_for(database)
    writer = object.__new__(DurableNativeCatalogWriter)
    writer.driver, writer.database = driver, database
    writer.query = Mock(return_value=({"native": "unchanged"},))
    return (
        NativeCatalogClient(driver, database=database, durable_writer=writer),
        driver,
        writer,
    )


def sample_rows():
    return [
        state._activation_row(_record(revision=revision, sequence=revision))
        for revision in (1, 2)
    ]


def real_client(tmp_path, left, right):
    # Keep complete columns even for empty observations. Only transport and
    # attestation are fake; the adapter, durable writer and agreement are real.
    names = state._ACTIVATION_COLUMNS
    proof, calls = observed_proof(
        tmp_path,
        [tuple(row[name] for name in names) for row in left],
        right
        if isinstance(right, Exception)
        else [tuple(row[name] for name in names) for row in right],
        left_columns=names,
        right_columns=names,
    )
    for index, connection in enumerate(proof.connections, start=1):
        connection.driver.host = f"member-{index}.internal"
        connection.driver.port = 9000
    proof.attest = Mock()
    driver = driver_for()
    writer = DurableNativeCatalogWriter(
        driver, directory=tmp_path, proof=proof, member_name=proof.connections[0].name
    )
    return (
        NativeCatalogClient(driver, database=DATABASE, durable_writer=writer),
        proof,
        calls,
    )


def caller_read(client, through_revision):
    if not through_revision:
        return state.ClickHouseCatalogStateStore(
            client,
            database=DATABASE,
            serializer=InProcessCatalogMutationSerializer(),
            timeout_ms=1000,
        ).list_activations(**SCOPE)
    return state.ClickHouseCurrentBindingReader(
        client, database=DATABASE, timeout_ms=1000
    ).read_current(
        context=SimpleNamespace(**SCOPE, catalog_revision=3, projection_version=1),
        source_adapter="span_attributes",
        at_revision=2,
        build_token=_record(revision=3).build_token,
    )


@pytest.mark.parametrize("through_revision", VARIANTS)
@pytest.mark.parametrize("database", [DATABASE, "property_catalog"])
def test_only_two_exact_queries_forward_complete_contract(through_revision, database):
    client, driver, writer = delegation_client(database)
    sql, params = query_case(through_revision, database)
    original_params = dict(params)
    assert client.query(sql, params, timeout_ms=1000) is writer.query.return_value
    writer.query.assert_called_once_with(
        sql, params, timeout_ms=1000, agreement=NativeReadAgreement.complete_result()
    )
    assert writer.query.call_args.args[1] is params
    assert params == original_params
    assert client._read_agreements == {
        query_case(variant, database)[0]: NativeReadAgreement.complete_result()
        for variant in VARIANTS
    }
    driver.execute_read.assert_not_called()


def altered_sql(sql, mutation):
    return {
        "whitespace": sql + " ",
        "comment": sql + " -- reviewed-looking",
        "inner_limit": sql.replace("LIMIT 4096", "LIMIT 4095"),
        "outer_limit": sql + " LIMIT 4096",
        "status_filter": sql.replace(
            "AND activation._version=recent.latest_version",
            "AND activation.status='active' AND activation._version=recent.latest_version",
        ),
        "projection": sql.replace("activation.status, ", ""),
        "order": sql + " DESC",
        "other_read": f"SELECT * FROM `{DATABASE}`.`property_catalog_checkpoints`",
    }[mutation]


@pytest.mark.parametrize("through_revision", VARIANTS)
@pytest.mark.parametrize(
    "mutation",
    [
        "whitespace",
        "comment",
        "inner_limit",
        "outer_limit",
        "status_filter",
        "projection",
        "order",
        "other_read",
    ],
)
def test_nearby_sql_keeps_strict_default(tmp_path, through_revision, mutation):
    sql, params = query_case(through_revision)
    changed = altered_sql(sql, mutation)
    assert changed != sql
    client, driver, writer = delegation_client()
    assert client.query(changed, params, timeout_ms=1000) is writer.query.return_value
    writer.query.assert_called_once_with(changed, params, timeout_ms=1000)
    driver.execute_read.assert_not_called()

    a, b = sample_rows()
    client, proof, calls = real_client(tmp_path, [a, b], [b, a, a])
    with pytest.raises(NativeWriteProofError, match="disagrees"):
        client.query(changed, params, timeout_ms=1000)
    assert len(calls) == 2 and proof.attest.call_count == 1
    client._driver.execute_read.assert_not_called()


@pytest.mark.parametrize("through_revision", VARIANTS)
@pytest.mark.parametrize("reverse_members", [False, True])
def test_real_agreement_preserves_first_members_physical_typed_rows(
    tmp_path, through_revision, reverse_members
):
    a, b = sample_rows()
    left, right = ([b, a, a], [a, b])
    if reverse_members:
        left, right = right, left
    client, proof, calls = real_client(tmp_path, left, right)
    sql, params = query_case(through_revision)
    result = client.query(sql, params, timeout_ms=1000)
    assert result == tuple(left)
    assert result[0]["updated_at"] is left[0]["updated_at"]
    assert [call[0] for call in calls] == ["replica1", "replica2"]
    assert all(call[1] == sql and call[2] == params for call in calls)
    assert proof.attest.call_count == 2


@pytest.mark.parametrize("through_revision", VARIANTS)
def test_nested_logical_window_is_not_a_physical_sentinel(tmp_path, through_revision):
    row = sample_rows()[0]
    left = [row] * 4097
    client, proof, calls = real_client(tmp_path, left, [row])
    sql, params = query_case(through_revision)
    assert client.query(sql, params, timeout_ms=5000) == tuple(left)
    assert len(calls) == proof.attest.call_count == 2


def test_agreed_duplicate_activations_still_parse_and_select(tmp_path):
    a, b = sample_rows()
    client, proof, calls = real_client(tmp_path, [b, a, a], [a, b])
    assert caller_read(client, False) == (
        _record(revision=1, sequence=1),
        _record(revision=2, sequence=2),
    )
    assert len(calls) == proof.attest.call_count == 2


@pytest.mark.parametrize("through_revision", VARIANTS)
@pytest.mark.parametrize(
    "change", [{"status": "disabled"}, {"status": "building"}, {"value_rows": 100}]
)
def test_agreed_same_version_variants_reach_existing_caller_conflict_checks(
    tmp_path, through_revision, change
):
    row = sample_rows()[0]
    changed = {**row, **change}
    left, right = [row, changed, row], [changed, row]
    client, proof, calls = real_client(tmp_path, left, right)
    sql, params = query_case(through_revision)
    assert client.query(sql, params, timeout_ms=1000) == tuple(left)
    with pytest.raises(state.PropertyCatalogStateConflict, match="different rows"):
        caller_read(client, through_revision)
    assert len(calls) == proof.attest.call_count == 4
    assert all(call[1] == sql and call[2] == params for call in calls)


@pytest.mark.parametrize("through_revision", VARIANTS)
@pytest.mark.parametrize("status", ["disabled", "building", "unexpected"])
def test_agreed_nonactive_status_survives_for_caller_validation(
    tmp_path, through_revision, status
):
    row = {**sample_rows()[0], "status": status}
    client, _, calls = real_client(tmp_path, [row, row], [row])
    sql, params = query_case(through_revision)
    assert client.query(sql, params, timeout_ms=1000) == (row, row)
    if status == "unexpected":
        with pytest.raises(
            state.PropertyCatalogStateConflict, match="unsupported latest status"
        ):
            caller_read(client, through_revision)
    else:
        assert caller_read(client, through_revision) == ()
    assert all(call[1] == sql for call in calls)


@pytest.mark.parametrize("through_revision", VARIANTS)
@pytest.mark.parametrize(
    "change",
    [{"status": "disabled"}, {"value_rows": 100}, {"_version": 2}, {"_version": "1"}],
)
@pytest.mark.parametrize("retain_original", [False, True])
def test_mismatched_members_reject_full_value_or_variant_differences(
    tmp_path, through_revision, change, retain_original
):
    row = sample_rows()[0]
    changed = {**row, **change}
    right = [row, changed] if retain_original else [changed]
    client, proof, calls = real_client(tmp_path, [row], right)
    with pytest.raises(NativeWriteProofError, match="disagrees"):
        caller_read(client, through_revision)
    assert len(calls) == 2 and proof.attest.call_count == 1


@pytest.mark.parametrize("through_revision", VARIANTS)
@pytest.mark.parametrize("bound", ["rows", "bytes"])
@pytest.mark.parametrize("overflow_first", [False, True])
def test_every_members_physical_bounds_precede_deduplication_and_caller_parsing(
    tmp_path, monkeypatch, through_revision, bound, overflow_first
):
    row = sample_rows()[0]
    if bound == "rows":
        overflow, reason = [row] * (_MAX_ROWS + 1), "result is incomplete"
    else:
        row = {**row, "source_manifest_json": "x" * (_MAX_AGREEMENT_BYTES // 2 + 1)}
        overflow, reason = [row, row], "exceeds byte bound"
    left, right = (overflow, [row]) if overflow_first else ([row], overflow)
    client, proof, calls = real_client(tmp_path, left, right)
    parser = Mock(side_effect=AssertionError("must prove all members before parsing"))
    monkeypatch.setattr(
        state, "_active_lineage" if through_revision else "_latest_row", parser
    )
    with pytest.raises(NativeWriteProofError, match=reason):
        caller_read(client, through_revision)
    parser.assert_not_called()
    assert len(calls) == (1 if overflow_first else 2)
    assert proof.attest.call_count == 1
    client._driver.execute_read.assert_not_called()


@pytest.mark.parametrize("through_revision", VARIANTS)
@pytest.mark.parametrize("right", [[], TimeoutError("second member lost")])
def test_missing_or_unavailable_member_never_returns_local_success(
    tmp_path, through_revision, right
):
    client, proof, calls = real_client(tmp_path, sample_rows(), right)
    expected = TimeoutError if isinstance(right, Exception) else NativeWriteProofError
    with pytest.raises(expected):
        caller_read(client, through_revision)
    assert len(calls) == 2 and proof.attest.call_count == 1
    client._driver.execute_read.assert_not_called()


@pytest.mark.parametrize("through_revision", VARIANTS)
def test_empty_state_still_requires_both_members(tmp_path, through_revision):
    client, proof, calls = real_client(tmp_path, [], [])
    assert caller_read(client, through_revision) == ()
    assert len(calls) == proof.attest.call_count == 2


@pytest.mark.parametrize("through_revision", VARIANTS)
@pytest.mark.parametrize("params", [None, [], [("catalog_epoch", 1)]])
def test_complete_contract_does_not_coerce_invalid_parameters(
    tmp_path, through_revision, params
):
    client, proof, calls = real_client(tmp_path, [], [])
    sql, _ = query_case(through_revision)
    with pytest.raises(NativeWriteProofError, match="typed contract and parameters"):
        client.query(sql, params, timeout_ms=1000)
    assert calls == [] and proof.attest.call_count == 1


@pytest.mark.parametrize(
    "field,value", [("database", "foreign"), ("server_enforced_readonly", True)]
)
def test_identity_guard_precedes_contract_delegation(field, value):
    client, driver, writer = delegation_client()
    setattr(driver, field, value)
    sql, params = query_case(False)
    with pytest.raises(PropertyCatalogDevRuntimeError):
        client.query(sql, params, timeout_ms=1000)
    writer.query.assert_not_called()
    driver.execute_read.assert_not_called()


@pytest.mark.parametrize("through_revision", VARIANTS)
def test_foreign_database_query_is_still_rejected(through_revision):
    client, driver, writer = delegation_client()
    sql, params = query_case(through_revision, "property_catalog_dev_foreign")
    with pytest.raises(PropertyCatalogDevRuntimeError, match="exact six qualified"):
        client.query(sql, params, timeout_ms=1000)
    writer.query.assert_not_called()
    driver.execute_read.assert_not_called()


@pytest.mark.parametrize("through_revision", VARIANTS)
def test_legacy_nondurable_path_is_unchanged(through_revision):
    driver = driver_for()
    driver.execute_read.return_value = ([], [("native_column", "String")], {})
    driver.execute_read.side_effect = None
    client = NativeCatalogClient(driver, database=DATABASE)
    sql, params = query_case(through_revision)
    assert client.query(sql, params, timeout_ms=1000) == ()
    driver.execute_read.assert_called_once_with(
        sql, params, timeout_ms=1000, settings={"readonly": 2}
    )
