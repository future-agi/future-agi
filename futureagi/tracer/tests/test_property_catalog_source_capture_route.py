"""Real admission/schema helpers, recording SOURCE drivers; no live operations."""

from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from tracer.services.clickhouse.v2.property_catalog.source_capture import (
    SourceCaptureError,
)
from tracer.services.clickhouse.v2.property_catalog.source_capture_route import (
    resolve_capture_source,
)
from tracer.tests.test_property_catalog_write_admission import Probe, admit

DATABASE = "default"
TABLE = str(UUID(int=1010))


def create_sql(
    engine="ReplicatedReplacingMergeTree", *, table_uuid=TABLE, replica="r1"
):
    args = "_version" if "Replacing" in engine else ""
    if engine.startswith("Replicated"):
        args = f"'/clickhouse/source/spans', '{replica}'" + (
            ", " + args if args else ""
        )
    return (
        f"CREATE TABLE default.spans UUID '{table_uuid}' "
        "(id String, _version UInt64, is_deleted UInt8, start_time DateTime64(6)) "
        f"ENGINE = {engine}({args}) ORDER BY id TTL start_time + INTERVAL 366 DAY "
        "SETTINGS index_granularity = 8192"
    )


class Source:
    def __init__(
        self,
        server,
        *,
        engine="ReplicatedReplacingMergeTree",
        table_uuid=TABLE,
        replica="r1",
    ):
        self.host, self.port = "source-service", 9000
        self.database, self.user, self.password = (
            DATABASE,
            "source",
            "SOURCE-fixture-only",
        )
        self.server_enforced_readonly = True
        self.server = server
        self.tables = [
            (
                table_uuid,
                engine,
                create_sql(engine, table_uuid=table_uuid, replica=replica),
            )
        ]
        self.replicas = (
            [("/clickhouse/source/spans", "default")]
            if engine.startswith("Replicated")
            else []
        )
        self.calls = []
        self.result_override = None

    def execute_read(self, sql, params, **options):
        self.calls.append((sql, params, options))
        assert options == {"timeout_ms": 30_000}  # no readonly=1 setting overrides
        assert params == {"database": DATABASE}
        assert sql.startswith("SELECT toString(serverUUID()) AS capture_server_uuid,")
        assert "FROM system.tables" in sql and "FROM system.replicas" in sql
        assert sql.count("LIMIT 2") == 2
        assert "name='spans'" in sql and "table='spans'" in sql
        assert " JOIN " not in sql
        if self.result_override is not None:
            return self.result_override
        return (
            [(self.server, self.tables, self.replicas)],
            [
                ("capture_server_uuid", "String"),
                ("capture_tables", "Array(Tuple)"),
                ("capture_replicas", "Array(Tuple)"),
            ],
            0,
        )


@pytest.fixture(scope="module")
def admissions(tmp_path_factory):
    result = {}
    for count in (1, 2):
        probe = Probe(count)
        admission = admit(tmp_path_factory.mktemp(f"route-admission-{count}"), probe)
        connections = tuple(
            replace(
                c,
                driver=SimpleNamespace(
                    host=f"mapped-{c.name}",
                    port=19000 + i,
                    database=admission.database,
                    user="writer",
                    password="WRITER-fixture-only",
                    server_enforced_readonly=False,
                    execute_read=lambda *a, **k: pytest.fail(
                        "route queried a writer/proof driver"
                    ),
                ),
            )
            for i, c in enumerate(probe.connections)
        )
        result[count] = admission, connections
    return result


def route_case(
    admissions, *, count=2, original_number=2, engine="ReplicatedReplacingMergeTree"
):
    admission, connections = admissions[count]
    original = Source(
        admission.members[original_number - 1].server_uuid,
        engine=engine,
        replica=f"r{original_number}",
    )
    selected = Source(
        admission.members[0].server_uuid,
        engine=engine,
        table_uuid=TABLE if original_number == 1 else str(UUID(int=2020)),
    )
    factories = []

    def factory(connection):
        assert connection in connections
        host, port = connection.driver.host, connection.driver.port
        factories.append((host, port))
        selected.host, selected.port = host, port
        return selected

    def resolve(**changes):
        return resolve_capture_source(
            original,
            **{
                "source_database": DATABASE,
                "admission": admission,
                "connections": connections,
                "driver_factory": factory,
                **changes,
            },
        )

    return original, selected, factories, resolve


@pytest.mark.parametrize(
    "engine",
    [
        "MergeTree",
        "ReplacingMergeTree",
        "ReplicatedMergeTree",
        "ReplicatedReplacingMergeTree",
    ],
)
def test_same_member_preserves_exact_table_and_source_credentials(admissions, engine):
    original, selected, factories, resolve = route_case(
        admissions, count=1, original_number=1, engine=engine
    )
    member, driver, metadata = resolve()
    assert member == admissions[1][0].members[0]
    assert driver is selected and driver is not original
    assert metadata == {
        "server_uuid": selected.server,
        "table_uuid": TABLE,
        "create_table_query": selected.tables[0][2],
    }
    assert factories == [("mapped-replica1", 19000)]
    assert len(original.calls) == len(selected.calls) == 1


@pytest.mark.parametrize(
    "engine", ["ReplicatedMergeTree", "ReplicatedReplacingMergeTree"]
)
def test_different_replica_uuid_and_replica_literal_are_allowed(admissions, engine):
    original, selected, _, resolve = route_case(admissions, engine=engine)
    member, driver, metadata = resolve(connections=tuple(reversed(admissions[2][1])))
    assert member.name == "replica1" and driver is selected
    assert metadata["table_uuid"] != original.tables[0][0]
    assert metadata["server_uuid"] == member.server_uuid


def test_load_balanced_original_never_changes_stable_selected_member(admissions):
    for number in (2, 1, 2):
        _, _, factories, resolve = route_case(admissions, original_number=number)
        assert resolve()[0].name == "replica1"
        assert factories == [("mapped-replica1", 19000)]


@pytest.mark.parametrize("side", ["original", "selected"])
@pytest.mark.parametrize("empty", [False, True])
def test_same_query_server_identity_is_checked_even_with_empty_tables(
    admissions, side, empty
):
    original, selected, factories, resolve = route_case(admissions)
    driver = original if side == "original" else selected
    driver.server = str(uuid4())
    if empty:
        driver.tables = []
    with pytest.raises(SourceCaptureError, match="outside write admission"):
        resolve()
    if side == "original":
        assert factories == [] and selected.calls == []


def test_selected_endpoint_reaching_another_admitted_member_is_not_accepted(admissions):
    original, selected, _, resolve = route_case(admissions)
    selected.server = original.server
    with pytest.raises(SourceCaptureError, match="outside write admission"):
        resolve()


@pytest.mark.parametrize("side", ["original", "selected"])
@pytest.mark.parametrize("mode", ["absent", "duplicate", "malformed"])
def test_table_inventory_must_be_exactly_one_complete_result(admissions, side, mode):
    original, selected, factories, resolve = route_case(admissions)
    driver = original if side == "original" else selected
    driver.tables = {
        "absent": [],
        "duplicate": driver.tables * 2,
        "malformed": [[TABLE]],
    }[mode]
    with pytest.raises(SourceCaptureError):
        resolve()
    if side == "original":
        assert not factories


@pytest.mark.parametrize(
    "replicas",
    [
        [],
        [("/p", "default"), ("/p", "default")],
        [("", "default")],
        [("/p", "")],
        [(None, "default")],
        [("relative", "default")],
        [("/p", "default", "extra")],
    ],
)
def test_replica_identity_cannot_be_empty_partial_or_ambiguous(admissions, replicas):
    original, _, factories, resolve = route_case(admissions)
    original.replicas = replicas
    with pytest.raises(SourceCaptureError):
        resolve()
    assert not factories


@pytest.mark.parametrize("change", ["path", "name", "auxiliary"])
def test_cross_replica_requires_same_path_and_admitted_keeper_ensemble(
    admissions, change
):
    original, selected, _, resolve = route_case(admissions)
    if change == "path":
        selected.replicas = [("/different/source", "default")]
    else:
        selected.replicas = [("/clickhouse/source/spans", "auxiliary")]
        if change == "auxiliary":
            original.replicas = list(selected.replicas)
    with pytest.raises(SourceCaptureError, match="common admitted source Keeper"):
        resolve()


@pytest.mark.parametrize("engine", ["MergeTree", "ReplacingMergeTree"])
def test_nonreplicated_source_cannot_move_nodes_even_with_same_table_uuid(
    admissions, engine
):
    _, selected, _, resolve = route_case(admissions, engine=engine)
    selected.tables = [(TABLE, engine, create_sql(engine))]
    with pytest.raises(SourceCaptureError, match="common admitted source Keeper"):
        resolve()


def test_same_member_requires_exact_table_uuid(admissions):
    _, selected, _, resolve = route_case(admissions, original_number=1)
    changed = str(uuid4())
    selected.tables = [(changed, selected.tables[0][1], create_sql(table_uuid=changed))]
    with pytest.raises(SourceCaptureError, match="same source member changed"):
        resolve()


@pytest.mark.parametrize(
    "before,after",
    [
        ("INTERVAL 366 DAY", "INTERVAL 365 DAY"),
        ("index_granularity = 8192", "index_granularity = 4096"),
        ("_version)", "is_deleted)"),
        ("id String", "id UInt64"),
        ("ORDER BY id", "ORDER BY (id, _version)"),
    ],
)
def test_replica_schema_comparison_keeps_semantic_differences(
    admissions, before, after
):
    _, selected, _, resolve = route_case(admissions)
    uuid, engine, sql = selected.tables[0]
    assert before in sql
    selected.tables = [(uuid, engine, sql.replace(before, after))]
    with pytest.raises(SourceCaptureError, match="normalized schema differs"):
        resolve()


def test_schema_normalization_allows_formatting_and_absent_header_uuid(admissions):
    _, selected, _, resolve = route_case(admissions)
    uuid, engine, sql = selected.tables[0]
    selected.tables = [
        (
            uuid,
            engine,
            sql.replace(f" UUID '{uuid}'", "")
            .replace("default.spans", "`default`.`spans`")
            .replace("ORDER BY", "/* formatting */ ORDER   BY"),
        )
    ]
    assert resolve()[2]["table_uuid"] == uuid


@pytest.mark.parametrize(
    "mutation",
    ["other_table", "other_database", "header_uuid", "engine", "malformed_sql"],
)
def test_create_header_engine_and_parser_remain_exact(admissions, mutation):
    original, _, factories, resolve = route_case(admissions)
    uuid, engine, sql = original.tables[0]
    sql = {
        "other_table": sql.replace("default.spans", "default.other"),
        "other_database": sql.replace("default.spans", "other.spans"),
        "header_uuid": sql.replace(uuid, str(uuid4())),
        "engine": sql.replace(
            "ENGINE = ReplicatedReplacingMergeTree", "ENGINE = MergeTree"
        ),
        "malformed_sql": sql + "; SELECT 1",
    }[mutation]
    original.tables = [(uuid, engine, sql)]
    with pytest.raises(SourceCaptureError):
        resolve()
    assert not factories


@pytest.mark.parametrize(
    "engine", ["Distributed", "View", "Log", "AggregatingMergeTree"]
)
def test_unsupported_source_engines_block_before_factory(admissions, engine):
    original, _, factories, resolve = route_case(admissions)
    original.tables = [(TABLE, engine, "invalid")]
    with pytest.raises(SourceCaptureError, match="supported MergeTree"):
        resolve()
    assert not factories


@pytest.mark.parametrize(
    "attribute,value",
    [
        ("database", "catalog"),
        ("user", "writer"),
        ("password", "WRITER-fixture-only"),
        ("server_enforced_readonly", False),
    ],
)
def test_factory_must_preserve_source_database_credentials_and_readonly(
    admissions, attribute, value
):
    _, selected, _, resolve = route_case(admissions)
    setattr(selected, attribute, value)
    with pytest.raises(SourceCaptureError):
        resolve()
    assert selected.calls == []


def test_factory_cannot_return_catalog_connection_driver(admissions):
    _, _, _, resolve = route_case(admissions)
    with pytest.raises(SourceCaptureError, match="SOURCE identity"):
        resolve(driver_factory=lambda _: admissions[2][1][0].driver)


def test_factory_cannot_route_to_other_endpoint_or_fallback_after_failure(admissions):
    _, selected, factories, resolve = route_case(admissions)
    with pytest.raises(SourceCaptureError, match="SOURCE identity"):
        resolve(driver_factory=lambda _: selected)
    assert not selected.calls
    selected.tables = []
    with pytest.raises(SourceCaptureError):
        resolve()
    assert factories == [("mapped-replica1", 19000)]


@pytest.mark.parametrize(
    "change", ["missing", "duplicate", "hostname", "database", "port", "host"]
)
def test_connection_set_must_match_existing_admission_before_source_probe(
    admissions, change
):
    original, _, factories, resolve = route_case(admissions)
    connections = list(admissions[2][1])
    if change == "missing":
        connections.pop()
    elif change == "duplicate":
        connections[1] = connections[0]
    elif change == "hostname":
        connections[0] = replace(connections[0], expected_hostname="wrong")
    else:
        driver = SimpleNamespace(**vars(connections[0].driver))
        setattr(driver, change, {"database": "wrong", "port": True, "host": ""}[change])
        connections[0] = replace(connections[0], driver=driver)
    with pytest.raises(SourceCaptureError):
        resolve(connections=connections)
    assert not original.calls and not factories


def test_corrupt_admission_is_not_accepted_as_boolean_authority(admissions):
    original, _, factories, resolve = route_case(admissions)
    with pytest.raises(SourceCaptureError):
        resolve(admission=True)
    assert not original.calls and not factories
