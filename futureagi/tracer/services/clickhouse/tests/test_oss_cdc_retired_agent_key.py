"""The retired agent column is retained metadata, never a required PG field."""
import copy

import pytest

from tracer.services.clickhouse import oss_cdc_bootstrap as core
from tracer.services.clickhouse.tests.test_oss_cdc_bootstrap import (
    MemoryClient,
    run,
    run_source,
    source_client,
)

TABLE = 'simulate_agent_definition'


def current_source_client():
    client = source_client()
    snapshot = client.inspect_source()
    original = snapshot.tables[TABLE]
    snapshot.tables[TABLE] = core.SourceTable(
        tuple((name, kind) for name, kind in original.columns if name != 'api_key')
        + (('encrypted_api_key', 'Nullable(String)'),),
        original.primary_key,
    )
    client.inspect_source = lambda: snapshot
    client.columns[TABLE].pop('api_key', None)
    client.columns[TABLE]['encrypted_api_key'] = ('Nullable(String)', '', '')
    return client


def test_legacy_declaration_no_longer_requires_the_removed_source_column():
    assert 'api_key' not in core._table(core.LANDING[TABLE])[0]


@pytest.mark.parametrize('source_owned', [False, True])
@pytest.mark.parametrize('retained', [False, True])
def test_retired_agent_key_is_preserved_only_when_already_present(source_owned, retained):
    client = current_source_client() if source_owned else MemoryClient(complete=True, ledger=True)
    client.columns[TABLE].pop('api_key', None)
    if retained:
        client.columns[TABLE]['api_key'] = ('Nullable(String)', '', '')
    before = copy.deepcopy(client.columns[TABLE])
    execute = run_source if source_owned else run
    execute(client)
    assert client.columns[TABLE] == before
    assert not any(event[0] == 'ADD' and event[1] == TABLE for event in client.writes)
    writes = list(client.writes)
    assert execute(client) == core.BootstrapResult((), False)
    assert client.writes == writes
    if not source_owned:
        assert not writes


@pytest.mark.parametrize('source_owned', [False, True])
@pytest.mark.parametrize('shape', [
    ('String', '', ''),
    ('Nullable(UUID)', '', ''),
    ('Nullable(String)', 'DEFAULT', "''"),
])
def test_retained_agent_key_still_requires_its_exact_old_shape(source_owned, shape):
    client = current_source_client() if source_owned else MemoryClient(complete=True, ledger=True)
    client.columns[TABLE]['api_key'] = shape
    with pytest.raises(core.BootstrapError, match=r'simulate_agent_definition\.api_key: incompatible'):
        (run_source if source_owned else run)(client)
    assert client.writes == []


@pytest.mark.parametrize('source_owned', [False, True])
def test_retention_does_not_accept_any_other_unexpected_column(source_owned):
    client = current_source_client() if source_owned else MemoryClient(complete=True, ledger=True)
    client.columns[TABLE]['old_api_key'] = ('Nullable(String)', '', '')
    with pytest.raises(core.BootstrapError, match='missing/unexpected columns'):
        (run_source if source_owned else run)(client)
    assert client.writes == []


def test_current_source_fields_remain_required():
    client = current_source_client()
    snapshot = client.inspect_source()
    original = snapshot.tables[TABLE]
    snapshot.tables[TABLE] = core.SourceTable(
        tuple((name, kind) for name, kind in original.columns if name != 'agent_name'),
        original.primary_key,
    )
    with pytest.raises(core.BootstrapError, match='simulate_agent_definition: missing application source columns'):
        core._source_definitions(snapshot)


def test_source_inventory_remains_authoritative_when_it_still_owns_the_field():
    client = current_source_client()
    snapshot = client.inspect_source()
    original = snapshot.tables[TABLE]
    snapshot.tables[TABLE] = core.SourceTable(
        original.columns + (('api_key', 'String'),), original.primary_key,
    )
    client.columns[TABLE]['api_key'] = ('String', '', '')
    run_source(client)
    assert client.columns[TABLE]['api_key'] == ('String', '', '')


def test_packaged_landing_declaration_stays_one_statement():
    # The packaged CREATE splitter is not comment aware: it splits on a semicolon
    # at a line end. A stray one in a declaration breaks every bootstrap path.
    from tracer.services.clickhouse.v2 import apply_schema

    assert len(apply_schema.split_statements(core.LANDING[TABLE])) == 1
