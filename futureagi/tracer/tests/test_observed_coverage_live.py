"""Live-ClickHouse contract for the coverage probes.

``test_observed_coverage.py`` drives ``observed_scope_coverage`` with doubles
that decide "in flight" versus "settled" and "bare" versus "indexable" by
looking for the gates' text in the statement. That pins the shape of the SQL;
it cannot tell whether the predicate ClickHouse actually evaluates does what
the text says. A gate that is present but silently always-false would make
every unindexed project read as covered -- the one direction this module must
never fail in -- and every unit test would stay green. So the verdicts here
come from the real ``observed_scope_coverage`` reading the real ``spans``
table as the schema applier leaves it, with ``created_at`` set explicitly so
arrival and the span's own clock can disagree.

Gated on the same explicitly isolated test ClickHouse as the other live catalog
contracts: unset, it skips; configured, a wrong verdict is a failure.
"""

import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "services/clickhouse/v2/schema"
GRANULE = 8192

KEY_COLUMNS = (
    "organization_id",
    "workspace_id",
    "project_id",
    "source_kind",
    "attribute_key",
    "attribute_type",
    "key_folded",
    "first_seen",
    "last_seen",
)


def _real_spans_ddl(database):
    """The production spans table, as the applier leaves it, in a scratch database.

    002 creates it, 013 turns attributes_extra into a String, 024 adds the
    arrival minmax index. Only the tiered storage policy and its TTLs are
    stripped -- a scratch server has one volume -- so the sorting key, the
    partitioning, the projections and every skip index (including the
    ``mapKeys`` bloom filters that change how the maps are read) are the real
    ones. A pruning claim proved on a simplified key is not a claim about
    production.
    """
    text = (SCHEMA_DIR / "002_spans_v2.sql").read_text()
    start = text.index("CREATE TABLE IF NOT EXISTS spans")
    end = text.index(";", text.index("SETTINGS", start)) + 1
    create = text[start:end]
    create = re.sub(r"\nTTL\s.*?(?=\nSETTINGS)", "", create, flags=re.S)
    create = re.sub(r"\n\s*storage_policy\s*=\s*'tiered',", "", create)
    create = create.replace(
        "CREATE TABLE IF NOT EXISTS spans", f"CREATE TABLE {database}.spans", 1
    )
    return [
        create,
        f"ALTER TABLE {database}.spans MODIFY COLUMN attributes_extra String "
        "DEFAULT '{}' CODEC(ZSTD(3))",
        f"ALTER TABLE {database}.spans ADD INDEX IF NOT EXISTS "
        "auto_minmax_index_created_at created_at TYPE minmax() GRANULARITY 1",
    ]


@pytest.fixture
def live():
    import clickhouse_connect

    host = os.environ.get("OBSERVED_CATALOG_TEST_CH_HOST")
    if not host:
        pytest.skip("requires an explicitly isolated observed catalog test ClickHouse")
    assert host in {"clickhouse", "127.0.0.1", "localhost"}
    client = clickhouse_connect.get_client(
        host=host,
        port=int(os.environ.get("OBSERVED_CATALOG_TEST_CH_PORT", "8123")),
        username=os.environ.get("OBSERVED_CATALOG_TEST_CH_USER", "test"),
        password=os.environ.get("OBSERVED_CATALOG_TEST_CH_PASSWORD", "test"),
    )
    database = "test_observed_coverage_" + uuid4().hex
    client.command(f"CREATE DATABASE {database}")
    try:
        client.command(
            f"""
CREATE TABLE {database}.observed_attribute_keys (
    organization_id String, workspace_id String, project_id String,
    source_kind LowCardinality(String), attribute_key String,
    attribute_type LowCardinality(String), key_folded String,
    first_seen SimpleAggregateFunction(min, DateTime64(6, 'UTC')),
    last_seen  SimpleAggregateFunction(max, DateTime64(6, 'UTC')))
ENGINE = AggregatingMergeTree
ORDER BY (organization_id, workspace_id, project_id, source_kind, attribute_key, attribute_type)
"""
        )
        for statement in _real_spans_ddl(database):
            client.command(statement)
        yield SimpleNamespace(client=client, database=database, reads=[])
    finally:
        client.command(f"DROP DATABASE IF EXISTS {database}")


def _coverage(live, scope):
    from tracer.services.clickhouse.v2.property_catalog.coverage import (
        observed_scope_coverage,
    )
    from tracer.services.clickhouse.v2.property_catalog.reader import ObservedRead

    class Executor:
        def execute(self, sql, params, timeout_ms=None, settings=None):
            result = live.client.query(sql, parameters=params)
            return SimpleNamespace(
                data=[dict(zip(result.column_names, row)) for row in result.result_rows]
            )

    class Client:
        # The probes name the bare ``spans`` table; point them at the fixture,
        # run them under the settings and wall they were handed, and record
        # what the server says it read.
        def execute_read(self, sql, params=None, timeout_ms=None, settings=None):
            result = live.client.query(
                sql.replace("FROM spans", f"FROM {live.database}.spans"),
                parameters=params or {},
                settings={
                    **(settings or {}),
                    "max_execution_time": timeout_ms / 1000.0,
                },
            )
            live.reads.append(int((result.summary or {}).get("read_rows", -1)))
            return (
                list(result.result_rows),
                result.column_names,
                len(result.result_rows),
            )

        def execute(self, *args, **kwargs):
            raise AssertionError("coverage must use execute_read")

    deadline = SimpleNamespace(remaining_ms=lambda floor_ms=1, **_: 5_000)
    return observed_scope_coverage(
        scope=scope,
        deadline=deadline,
        observed=ObservedRead(
            Executor(), catalog_database=live.database, deadline=deadline
        ),
        client=Client(),
    )


def _scope(*projects):
    return {
        "organization_id": "org",
        "workspace_id": "ws",
        "project_ids": list(projects),
    }


def _spans(live, rows, *, bare=False, deleted=False):
    """Insert (project, start_time, created_at) rows into the real table.

    Attributed unless ``bare``; a tombstone (``is_deleted = 1``) if ``deleted``.
    Every other column takes the production default.
    """
    attrs = {} if bare else {"k": "v"}
    live.client.insert(
        f"{live.database}.spans",
        [
            [
                project,
                "span",
                start,
                created,
                str(uuid4()),
                str(uuid4()),
                "n",
                attrs,
                1 if deleted else 0,
            ]
            for project, start, created in rows
        ],
        column_names=[
            "project_id",
            "observation_type",
            "start_time",
            "created_at",
            "trace_id",
            "id",
            "name",
            "attrs_string",
            "is_deleted",
        ],
    )


def _index(live, project, seen):
    live.client.insert(
        f"{live.database}.observed_attribute_keys",
        [["org", "ws", project, "custom_attribute", "k", "string", "k", seen, seen]],
        column_names=list(KEY_COLUMNS),
    )


def _covered_big_project(live, now, rows=40_000, days=5):
    """Several days and several granules of history, indexed from the oldest span."""
    project = str(uuid4())
    oldest = (now - timedelta(days=days)).replace(minute=0, second=0, microsecond=0)
    step = timedelta(days=days) / rows
    _spans(live, [[project, oldest + step * n, oldest + step * n] for n in range(rows)])
    live.client.command(f"OPTIMIZE TABLE {live.database}.spans FINAL")
    _index(live, project, oldest)
    return project, rows


# --- arrival ------------------------------------------------------------------


def test_a_project_whose_spans_all_arrived_within_the_margin_is_not_a_gap(live):
    """Spans planted yesterday by their own clock, arrived seconds ago: in flight."""
    now = datetime.now(UTC)
    fresh = str(uuid4())
    _spans(
        live,
        [
            [fresh, now - timedelta(days=1), now - timedelta(seconds=20)],
            [fresh, now - timedelta(days=1, hours=-2), now - timedelta(minutes=30)],
        ],
    )
    result = _coverage(live, _scope(fresh))
    assert (result.complete, result.reason) == (True, "covered")


def test_a_span_that_arrived_before_the_margin_and_is_unindexed_is_a_gap(live):
    """The un-backfilled upgrade: history arrived long ago, index knows nothing.

    This is the case the unit doubles cannot protect: a gate that never matched
    would turn it into ``covered``.
    """
    now = datetime.now(UTC)
    stale = str(uuid4())
    _spans(
        live,
        [
            [stale, now - timedelta(days=1), now - timedelta(hours=3)],
            [stale, now - timedelta(seconds=5), now - timedelta(seconds=5)],
        ],
    )
    result = _coverage(live, _scope(stale))
    assert (result.complete, result.reason) == (False, "project_unindexed")


def test_an_in_flight_project_does_not_mask_a_settled_gap_in_the_same_scope(live):
    now = datetime.now(UTC)
    fresh, stale = str(uuid4()), str(uuid4())
    _spans(
        live,
        [
            [fresh, now - timedelta(days=1), now - timedelta(seconds=20)],
            [stale, now - timedelta(days=1), now - timedelta(hours=3)],
        ],
    )
    result = _coverage(live, _scope(fresh, stale))
    assert (result.complete, result.reason) == (False, "project_unindexed")


def test_a_late_arrival_below_the_floor_is_tolerated_until_it_settles(live):
    """An indexed project receiving yesterday's trace from a client buffer."""
    now = datetime.now(UTC)
    project = str(uuid4())
    _index(live, project, now - timedelta(minutes=10))
    _spans(
        live,
        [
            [project, now - timedelta(minutes=10), now - timedelta(minutes=10)],
            [project, now - timedelta(days=1), now - timedelta(seconds=10)],
        ],
    )
    assert _coverage(live, _scope(project)).complete is True

    # The same span, had it arrived two hours ago and still not been indexed.
    live.client.command(
        f"ALTER TABLE {live.database}.spans UPDATE created_at = now64(6, 'UTC') - INTERVAL 2 HOUR "
        f"WHERE project_id = '{project}' AND start_time < now64(6, 'UTC') - INTERVAL 12 HOUR "
        "SETTINGS mutations_sync = 2"
    )
    settled = _coverage(live, _scope(project))
    assert (settled.complete, settled.reason) == (False, "source_predates_index")


# --- eligibility --------------------------------------------------------------


def test_a_project_of_bare_spans_is_not_a_gap_but_one_attributed_span_makes_it_one(
    live,
):
    """Nothing the catalog would index means nothing is missing, at any age."""
    now = datetime.now(UTC)
    project = str(uuid4())
    _spans(
        live, [[project, now - timedelta(days=2), now - timedelta(hours=3)]], bare=True
    )
    assert _coverage(live, _scope(project)).reason == "covered"

    _spans(live, [[project, now - timedelta(days=2), now - timedelta(hours=3)]])
    assert _coverage(live, _scope(project)).reason == "project_unindexed"


def test_a_span_carrying_only_extra_attributes_still_counts(live):
    """attributes_extra feeds the catalog too; an old unindexed one is a gap."""
    project = str(uuid4())
    live.client.command(
        f"INSERT INTO {live.database}.spans "
        "(project_id, observation_type, start_time, created_at, trace_id, id, name, attributes_extra) "
        f"VALUES ('{project}', 'span', now64(6, 'UTC') - INTERVAL 2 DAY, "
        f"now64(6, 'UTC') - INTERVAL 3 HOUR, '{uuid4()}', '{uuid4()}', 'n', "
        '\'{"nested": {"a": 1}}\')'
    )
    assert _coverage(live, _scope(project)).reason == "project_unindexed"


def test_an_old_bare_span_below_the_floor_is_not_a_gap_but_an_attributed_one_is(live):
    """The eligibility arm on the FLOOR probe, judged by the real statement."""
    now = datetime.now(UTC)
    project = str(uuid4())
    _index(live, project, now - timedelta(minutes=10))
    _spans(live, [[project, now - timedelta(minutes=10), now - timedelta(minutes=10)]])
    _spans(
        live, [[project, now - timedelta(days=1), now - timedelta(hours=3)]], bare=True
    )
    assert _coverage(live, _scope(project)).reason == "covered"

    _spans(live, [[project, now - timedelta(days=1), now - timedelta(hours=3)]])
    assert _coverage(live, _scope(project)).reason == "source_predates_index"


def test_a_tombstoned_span_the_backfill_would_skip_is_not_a_gap(live):
    """Deleted before the upgrade: never replayed, so never missing."""
    now = datetime.now(UTC)
    project = str(uuid4())
    _spans(
        live,
        [[project, now - timedelta(days=2), now - timedelta(hours=3)]],
        deleted=True,
    )
    assert _coverage(live, _scope(project)).reason == "covered"

    _spans(live, [[project, now - timedelta(days=2), now - timedelta(hours=3)]])
    assert _coverage(live, _scope(project)).reason == "project_unindexed"


# --- cost and per-project bounds ----------------------------------------------


def test_a_covered_project_costs_granules_not_its_history(live):
    """The steady state of a healthy install must not scan history.

    On the production key (project_id, observation_type, service_name, hour,
    ...) the hour range cannot be decided for a granule that straddles a type
    or service boundary, so the honest bound is a few granules, not zero --
    and it must hold whatever OTHER floors sit in the scope. An earlier
    revision bounded the whole scope by its largest floor, which read the big
    tenant end to end whenever any newer project shared the scope.
    """
    now = datetime.now(UTC)
    big, rows = _covered_big_project(live, now)
    young = str(uuid4())
    _spans(live, [[young, now - timedelta(minutes=5), now - timedelta(minutes=5)]])
    _index(live, young, now - timedelta(minutes=5))

    alone = _coverage(live, _scope(big))
    assert (alone.complete, alone.reason) == (True, "covered")
    assert live.reads[-1] <= 2 * GRANULE, live.reads

    mixed = _coverage(live, _scope(big, young))
    assert (mixed.complete, mixed.reason) == (True, "covered")
    assert live.reads[-1] <= 2 * GRANULE, live.reads
    assert live.reads[-1] < rows // 4


def test_each_project_is_judged_against_its_own_floor(live):
    """A shared bound taken from the oldest floor would hide a younger project's gap.

    Two projects: the first indexed from long ago, the second only from ten
    minutes ago but holding an attributed span that arrived hours ago and
    sits between the two floors. Bound to its own floor that span is found;
    bound to the older project's floor it is excluded before it is ever
    compared, and the scope reads covered -- open, the one direction this
    module must never fail in.
    """
    now = datetime.now(UTC)
    old, young = str(uuid4()), str(uuid4())
    _index(live, old, now - timedelta(days=30))
    _spans(live, [[old, now - timedelta(days=30), now - timedelta(days=30)]])
    _index(live, young, now - timedelta(minutes=10))
    _spans(
        live,
        [
            [young, now - timedelta(minutes=10), now - timedelta(minutes=10)],
            [young, now - timedelta(days=2), now - timedelta(hours=3)],
        ],
    )
    result = _coverage(live, _scope(old, young))
    assert (result.complete, result.reason) == (False, "source_predates_index")
