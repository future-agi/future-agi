"""Fresh migrated PostgreSQL must satisfy every selected CDC landing contract.

Run with --migrations --create-db on a PostgreSQL test database. Synthetic source
inventories cannot detect fields removed by application migrations.
"""
import json

import pytest
from django.apps import apps
from django.db import connection

from tracer.services.clickhouse import oss_cdc_bootstrap as core
from tracer.services.clickhouse import oss_cdc_source
from tracer.services.clickhouse.oss_cdc_inventory import PeerTarget


@pytest.mark.django_db
@pytest.mark.parametrize('include_usage_schema', [True, False])
def test_fresh_migrated_source_definitions(include_usage_schema):
    assert connection.vendor == 'postgresql'
    if include_usage_schema:
        assert apps.is_installed('ee.usage'), 'usage-enabled case requires installed usage app'
    tables = core.landing_tables(include_usage_schema=include_usage_schema)

    def query(sql, parameters):
        assert sql.lstrip().startswith('SELECT ')
        with connection.cursor() as cursor:
            cursor.execute(sql, parameters)
            return cursor.fetchall()

    source = PeerTarget('pg_source', 'postgres', 5432, connection.settings_dict['NAME'])
    inventory = oss_cdc_source.inspect_source(query, source=source, tables=tables)
    derived = core.upgrade.MIGRATIONS['cdc_002_usage_eval_fields.sql']
    missing = {}
    for table in tables:
        required = {
            column for column in core._table(core.LANDING[table])[0]
            if not column.startswith('_peerdb_') and (table, column) not in derived
        }
        absent = sorted(required - dict(inventory.tables[table].columns).keys())
        if absent:
            missing[table] = absent
    # Names only: no row contents, credentials or connection parameters.
    print('CDC_FRESH_BOOT_DIAG=' + json.dumps({
        'include_usage_schema': include_usage_schema,
        'tables': len(tables),
        'missingColumns': missing,
    }), flush=True)
    definitions = core._source_definitions(inventory, include_usage_schema=include_usage_schema)
    assert set(definitions) == set(tables)
    for table, ddl in definitions.items():
        columns = core._table(ddl)[0]
        assert set(dict(inventory.tables[table].columns)) <= set(columns)
    assert not missing
