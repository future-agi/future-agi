"""0031 and 0041 build five indexes each with CREATE INDEX CONCURRENTLY, outside a
transaction. An interrupted build leaves an INVALID index and the migration
unrecorded, so the rerun must complete: an invalid index from the earlier run is
rebuilt, a finished one is kept, and a missing one is built.

Each test runs the migration's own database operations on connections["default"]
from the state before it, with the table's indexes left as an interrupted run
leaves them, and puts every index back valid afterwards.
"""

import pytest
from django.db import connections
from django.db.migrations.loader import MigrationLoader

CASES = {
    "0031": (
        "0031_request_log_v2_indexes",
        "0030_request_log_v2",
        "agentcc_request_log",
        {
            "agentcc_rl_org_run_idx": "(organization_id, agent_run_id)",
            "agentcc_rl_org_agent_idx": "(organization_id, agent_id, started_at DESC)",
            "agentcc_rl_org_billing_idx": (
                "(organization_id, billing_mode, started_at DESC)"
            ),
            "agentcc_rl_org_request_idx": "(organization_id, request_id)",
            "agentcc_rl_org_variant_idx": (
                "(organization_id, policy_variant, started_at DESC)"
            ),
        },
    ),
    "0041": (
        "0041_mcp_indexes",
        "0040_mcp_oauth",
        "agentcc_tool_call",
        {
            "agentcc_tc_org_started_idx": "(organization_id, started_at DESC)",
            "agentcc_tc_org_run_idx": "(organization_id, agent_run_id)",
            "agentcc_tc_org_server_tool_idx": (
                "(organization_id, server_id, tool, started_at DESC)"
            ),
            "agentcc_tc_org_key_idx": "(organization_id, api_key_id, started_at DESC)",
            "agentcc_tc_org_generation_idx": "(organization_id, generation_id)",
        },
    ),
}


def _conn():
    return connections["default"]


def _indexes(table):
    """{name: (valid, definition)} for the table's indexes."""
    with _conn().cursor() as cursor:
        cursor.execute(
            """
            SELECT c.relname, i.indisvalid, pg_get_indexdef(i.indexrelid)
            FROM pg_index i
            JOIN pg_class c ON c.oid = i.indexrelid
            JOIN pg_class t ON t.oid = i.indrelid
            WHERE t.relname = %s
            """,
            [table],
        )
        return {name: (valid, definition) for name, valid, definition in cursor}


def _execute(sql, params=None):
    with _conn().cursor() as cursor:
        cursor.execute(sql, params)


def _invalidate(name):
    # What an interrupted CREATE INDEX CONCURRENTLY leaves: the index, INVALID.
    _execute(
        "UPDATE pg_index SET indisvalid = false WHERE indexrelid = %s::regclass",
        [name],
    )


def _restore(table, wanted):
    """Every wanted index present and valid again, as the migrated schema has it."""
    for name, columns in wanted.items():
        valid = _indexes(table).get(name, (None,))[0]
        if valid is False:
            _execute(f'DROP INDEX CONCURRENTLY IF EXISTS "{name}"')
        _execute(
            f'CREATE INDEX CONCURRENTLY IF NOT EXISTS "{name}" ON {table} {columns}'
        )


def _run_migration(name, previous):
    """The migration's database operations, from the state before it."""
    connection = _conn()
    loader = MigrationLoader(connection)
    migration = loader.get_migration("prism", name)
    state = loader.project_state(("prism", previous), at_end=True)
    with connection.schema_editor(atomic=False) as editor:
        for operation in migration.operations:
            new_state = state.clone()
            operation.state_forwards("prism", new_state)
            operation.database_forwards("prism", editor, state, new_state)
            state = new_state


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("case", sorted(CASES))
def test_rerun_completes_after_a_run_interrupted_mid_build(case):
    name, previous, table, wanted = CASES[case]
    names = list(wanted)
    try:
        # The first index finished, the second was being built, the rest were
        # never started.
        _invalidate(names[1])
        for missing in names[2:]:
            _execute(f'DROP INDEX CONCURRENTLY IF EXISTS "{missing}"')

        _run_migration(name, previous)

        found = _indexes(table)
        for index, columns in wanted.items():
            assert index in found, index
            valid, definition = found[index]
            assert valid is True, index
            assert definition.endswith(columns), (index, definition)
    finally:
        _restore(table, wanted)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("case", sorted(CASES))
def test_rerun_completes_when_every_index_already_exists(case):
    name, previous, table, wanted = CASES[case]
    try:
        _run_migration(name, previous)
        found = _indexes(table)
        assert all(found[index][0] is True for index in wanted)
    finally:
        _restore(table, wanted)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("case", sorted(CASES))
def test_rerun_rebuilds_an_index_left_invalid(case):
    name, previous, table, wanted = CASES[case]
    try:
        for index in wanted:
            _invalidate(index)
        _run_migration(name, previous)
        found = _indexes(table)
        assert all(found[index][0] is True for index in wanted)
    finally:
        _restore(table, wanted)
