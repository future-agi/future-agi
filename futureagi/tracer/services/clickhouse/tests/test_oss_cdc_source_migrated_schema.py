"""Migrated-schema CDC source contract, read-only against the pytest database.

The offline suites use synthetic PG metadata, so a Django field on a mirrored
table whose PostgreSQL type is outside the CDC map (for example a
PositiveSmallIntegerField stored as int2) passed unit CI and only failed when
peerdb-init inspected the real schema during the e2e boot. This runs the same
SELECT-only source inspection and landing-profile derivation against the
schema produced by this checkout's migrations.
"""

import pytest
from django.db import connection

from tfc import ee_loader
from tracer.services.clickhouse import oss_cdc_bootstrap as core
from tracer.services.clickhouse import oss_cdc_source
from tracer.services.clickhouse.oss_cdc_inventory import PeerTarget

pytestmark = pytest.mark.django_db


def _query(sql, parameters):
    assert sql.lstrip().startswith("SELECT ")
    with connection.cursor() as cursor:
        cursor.execute(sql, parameters)
        return cursor.fetchall()


def _unsupported_columns(tables):
    """Name each column the source inspection rejects, for a readable failure."""
    rows = _query(
        oss_cdc_source._COLUMNS_SQL,
        {"database": connection.settings_dict["NAME"], "tables": sorted(tables)},
    )
    rejected = []
    for row in rows:
        try:
            oss_cdc_source._mapped_type(row)
        except oss_cdc_source.SourceError:
            rejected.append(f"{row[2]}.{row[3]} ({row[6]}, nullable={row[7]})")
    return rejected


def test_migrated_landing_tables_satisfy_the_cdc_source_contract():
    # Same selection as oss_cdc_install.Config: usage follows the installed app.
    include_usage = ee_loader.has_ee("ee.usage")
    tables = core.landing_tables(include_usage_schema=include_usage)
    source = PeerTarget("pg_source", "postgres", 5432, connection.settings_dict["NAME"])
    try:
        inventory = oss_cdc_source.inspect_source(_query, source=source, tables=tables)
    except oss_cdc_source.SourceError as error:
        pytest.fail(
            f"peerdb-init would reject the migrated schema ({error}); columns "
            f"outside the CDC type map: {_unsupported_columns(tables)}"
        )
    definitions = core._source_definitions(
        inventory, include_usage_schema=include_usage
    )
    assert set(definitions) == set(tables)
    for ddl in definitions.values():
        # setup/install compare PeerDB-created landing tables against these.
        core._table(ddl)
