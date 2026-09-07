#!/bin/sh
set -eu

# Additive, idempotent bootstrap for the OSS unified property catalog.
# This script creates the isolated catalog database, its seven pinned tables,
# a separate derived-source namespace, and dedicated local identities. It never
# mutates source tables or sends these credentials to a discovered source host.

SOURCE_DATABASE=${PROPERTY_CATALOG_SOURCE_DATABASE:-${CH25_DATABASE:-default}}
TARGET_DATABASE=${PROPERTY_CATALOG_TARGET_DATABASE:-property_catalog_dev_oss}
# Installation-owned name, not an operator setting. Keep the suffix aligned
# with the source_capture contract and production grant validation.
CAPTURE_DATABASE=${TARGET_DATABASE}_source_capture
CLICKHOUSE_HOST=${CLICKHOUSE_HOST:-clickhouse}
CLICKHOUSE_PORT=${CLICKHOUSE_PORT:-9000}
SCHEMA_DIRECTORY=${PROPERTY_CATALOG_SCHEMA_DIRECTORY:-/property-catalog-schema}
CONTROL_SCHEMA="$(dirname "$0")/reader_activation_control.sql"

SOURCE_PASSWORD=${PROPERTY_CATALOG_SOURCE_PASSWORD:-oss-catalog-source-local-only}
CONTROL_PASSWORD=${PROPERTY_CATALOG_CONTROL_PASSWORD:-oss-catalog-control-local-only}
CONSUMER_PASSWORD=${PROPERTY_CATALOG_CONSUMER_PASSWORD:-oss-catalog-consumer-local-only}
LEDGER_PASSWORD=${PROPERTY_CATALOG_LEDGER_PASSWORD:-oss-catalog-ledger-local-only}
API_PASSWORD=${PROPERTY_CATALOG_API_PASSWORD:-oss-catalog-api-local-only}

SOURCE_USER=property_catalog_oss_source
CONTROL_USER=property_catalog_oss_control
CONSUMER_USER=property_catalog_oss_consumer
LEDGER_USER=property_catalog_oss_ledger
API_USER=property_catalog_oss_api

case "$SOURCE_DATABASE" in
  ''|*[!A-Za-z0-9_]*)
    echo >&2 "PROPERTY_CATALOG_SOURCE_DATABASE must be one ClickHouse identifier"
    exit 64
    ;;
esac
case "$TARGET_DATABASE" in
  ''|[!a-z]*|*[!a-z0-9_]*)
    echo >&2 "PROPERTY_CATALOG_TARGET_DATABASE must be a lowercase ClickHouse identifier"
    exit 64
    ;;
esac
if [ "${#TARGET_DATABASE}" -gt 128 ]; then
  echo >&2 "PROPERTY_CATALOG_TARGET_DATABASE must contain at most 128 characters"
  exit 64
fi
case "$TARGET_DATABASE" in
  default|futureagi|information_schema|property_catalog|system)
    echo >&2 "PROPERTY_CATALOG_TARGET_DATABASE must be isolated from production and source databases"
    exit 64
    ;;
esac
if [ "$SOURCE_DATABASE" = "$TARGET_DATABASE" ]; then
  echo >&2 "property catalog source and target databases must differ"
  exit 64
fi
if [ "$SOURCE_DATABASE" = "$CAPTURE_DATABASE" ]; then
  echo >&2 "property catalog source and derived capture databases must differ"
  exit 64
fi

validate_password() {
  case "$2" in
    ''|*[!A-Za-z0-9._-]*)
      echo >&2 "$1 must contain only A-Z, a-z, 0-9, dot, underscore, or dash"
      exit 64
      ;;
  esac
  if [ "${#2}" -lt 16 ] || [ "${#2}" -gt 128 ]; then
    echo >&2 "$1 must contain 16 to 128 characters"
    exit 64
  fi
}

validate_password PROPERTY_CATALOG_SOURCE_PASSWORD "$SOURCE_PASSWORD"
validate_password PROPERTY_CATALOG_CONTROL_PASSWORD "$CONTROL_PASSWORD"
validate_password PROPERTY_CATALOG_CONSUMER_PASSWORD "$CONSUMER_PASSWORD"
validate_password PROPERTY_CATALOG_LEDGER_PASSWORD "$LEDGER_PASSWORD"
validate_password PROPERTY_CATALOG_API_PASSWORD "$API_PASSWORD"

for schema in \
  025_property_catalog_data.sql \
  026_property_catalog_state.sql \
  027_property_catalog_delivery.sql
do
  if [ ! -f "$SCHEMA_DIRECTORY/$schema" ]; then
    echo >&2 "missing pinned property catalog schema: $schema"
    exit 66
  fi
done

if [ ! -f "$CONTROL_SCHEMA" ]; then
  echo >&2 "missing pinned property catalog activation-control schema"
  exit 66
fi

clickhouse() {
  clickhouse-client \
    --host "$CLICKHOUSE_HOST" \
    --port "$CLICKHOUSE_PORT" \
    --user default \
    "$@"
}

clickhouse --query "CREATE DATABASE IF NOT EXISTS \`$TARGET_DATABASE\`"
# The runtime may create/drop only its bounded derived tables, never databases.
# Do not require source spans here: normal application migrations may follow
# this bootstrap. Source member/table identity is mandatory before capture.
clickhouse --query "CREATE DATABASE IF NOT EXISTS \`$CAPTURE_DATABASE\` ENGINE = Atomic"
CAPTURE_ENGINE=$(clickhouse --format TabSeparatedRaw --query "SELECT engine FROM system.databases WHERE name='$CAPTURE_DATABASE'")
if [ "$CAPTURE_ENGINE" != "Atomic" ]; then
  echo >&2 "property catalog source capture namespace must use Atomic"
  exit 65
fi
for schema in \
  025_property_catalog_data.sql \
  026_property_catalog_state.sql \
  027_property_catalog_delivery.sql
do
  clickhouse --database "$TARGET_DATABASE" --multiquery < "$SCHEMA_DIRECTORY/$schema"
done
clickhouse --database "$TARGET_DATABASE" --multiquery < "$CONTROL_SCHEMA"

# Add the managed FOLLOW action without rewriting existing event values or
# digests. Only this exact prior enum is upgradeable; unknown contracts stop.
CONTROL_ACTION_TYPE=$(clickhouse --format TabSeparatedRaw --query "SELECT type FROM system.columns WHERE database='$TARGET_DATABASE' AND table='property_catalog_activation_control_events' AND name='action'")
case "$CONTROL_ACTION_TYPE" in
  "Enum8('activate' = 1, 'disable' = 2, 'rollback' = 3)")
    clickhouse --database "$TARGET_DATABASE" --query "ALTER TABLE property_catalog_activation_control_events MODIFY COLUMN action Enum8('activate' = 1, 'disable' = 2, 'rollback' = 3, 'follow' = 4)"
    ;;
  "Enum8('activate' = 1, 'disable' = 2, 'rollback' = 3, 'follow' = 4)") ;;
  *)
    echo >&2 "unsupported property catalog activation-control action contract"
    exit 65
    ;;
esac

clickhouse --query "CREATE USER IF NOT EXISTS $SOURCE_USER IDENTIFIED WITH sha256_password BY '$SOURCE_PASSWORD' HOST ANY"
clickhouse --query "ALTER USER $SOURCE_USER IDENTIFIED WITH sha256_password BY '$SOURCE_PASSWORD' HOST ANY SETTINGS readonly=1, max_execution_time=30, max_threads=4, max_memory_usage=4294967296, max_bytes_to_read=42949672960, max_result_rows=250000, max_result_bytes=67108864, read_overflow_mode='throw', result_overflow_mode='throw', timeout_overflow_mode='throw'"

clickhouse --query "CREATE USER IF NOT EXISTS $CONTROL_USER IDENTIFIED WITH sha256_password BY '$CONTROL_PASSWORD' HOST ANY"
clickhouse --query "ALTER USER $CONTROL_USER IDENTIFIED WITH sha256_password BY '$CONTROL_PASSWORD' HOST ANY"
clickhouse --query "CREATE USER IF NOT EXISTS $CONSUMER_USER IDENTIFIED WITH sha256_password BY '$CONSUMER_PASSWORD' HOST ANY"
clickhouse --query "ALTER USER $CONSUMER_USER IDENTIFIED WITH sha256_password BY '$CONSUMER_PASSWORD' HOST ANY"

clickhouse --query "CREATE USER IF NOT EXISTS $LEDGER_USER IDENTIFIED WITH sha256_password BY '$LEDGER_PASSWORD' HOST ANY"
clickhouse --query "ALTER USER $LEDGER_USER IDENTIFIED WITH sha256_password BY '$LEDGER_PASSWORD' HOST ANY SETTINGS readonly=2"
clickhouse --query "CREATE USER IF NOT EXISTS $API_USER IDENTIFIED WITH sha256_password BY '$API_PASSWORD' HOST ANY"
clickhouse --query "ALTER USER $API_USER IDENTIFIED WITH sha256_password BY '$API_PASSWORD' HOST ANY SETTINGS readonly=2, max_execution_time=10, max_threads=2, max_memory_usage=536870912, max_bytes_to_read=536870912, max_rows_to_read=5000000, max_result_bytes=8388608, max_result_rows=256, read_overflow_mode='throw', result_overflow_mode='throw', timeout_overflow_mode='throw'"

clickhouse --query "GRANT SELECT ON \`$SOURCE_DATABASE\`.spans TO $SOURCE_USER"
clickhouse --query "GRANT SELECT ON \`$CAPTURE_DATABASE\`.* TO $SOURCE_USER"
clickhouse --query "GRANT SELECT ON system.settings TO $SOURCE_USER"
clickhouse --query "GRANT SELECT(database, table, active, name, hash_of_all_files, rows, bytes_on_disk, disk_name) ON system.parts TO $SOURCE_USER"
# Metadata stays on the read-only source identity. These exact fields identify
# both table incarnations and prove required columns exist in captured parts.
clickhouse --query "GRANT SELECT(name, uuid, engine) ON system.databases TO $SOURCE_USER"
clickhouse --query "GRANT SELECT(database, name, uuid, engine, create_table_query, storage_policy) ON system.tables TO $SOURCE_USER"
clickhouse --query "GRANT SELECT(policy_name, disks) ON system.storage_policies TO $SOURCE_USER"
clickhouse --query "GRANT SELECT(database, table, name, type, default_kind, default_expression) ON system.columns TO $SOURCE_USER"
clickhouse --query "GRANT SELECT(database, table, active, name, rows, bytes_on_disk, column, type) ON system.parts_columns TO $SOURCE_USER"
# Source-local headroom observation only; this does not reserve disk bytes.
clickhouse --query "GRANT SELECT(name, path, total_space, unreserved_space, keep_free_space, type, is_read_only) ON system.disks TO $SOURCE_USER"
# Exact replicated-source routing identity; no Keeper enumeration or writes.
clickhouse --query "GRANT SELECT(database, table, zookeeper_path, zookeeper_name) ON system.replicas TO $SOURCE_USER"
clickhouse --query "GRANT SELECT, INSERT ON \`$TARGET_DATABASE\`.* TO $CONTROL_USER"
# Reuse the existing lifecycle CONTROL identity, not source/ledger/consumer/API.
# No source INSERT, ALTER, DROP, CREATE DATABASE, or grant option is added.
clickhouse --query "GRANT SELECT ON \`$SOURCE_DATABASE\`.spans TO $CONTROL_USER"
clickhouse --query "GRANT SELECT, INSERT, CREATE TABLE, ALTER DELETE, ALTER TTL, DROP TABLE ON \`$CAPTURE_DATABASE\`.* TO $CONTROL_USER"
for table in databases settings tables
do
  clickhouse --query "GRANT SELECT ON system.$table TO $CONTROL_USER"
done
for table in property_definition_catalog span_attribute_value_catalog property_catalog_deliveries
do
  clickhouse --query "GRANT INSERT ON \`$TARGET_DATABASE\`.$table TO $CONSUMER_USER"
done
for table in property_catalog_activations property_catalog_checkpoints property_catalog_deliveries property_catalog_source_streams property_definition_catalog span_attribute_value_catalog property_catalog_activation_control_events
do
  clickhouse --query "GRANT SELECT ON \`$TARGET_DATABASE\`.$table TO $LEDGER_USER"
done
# Direct write proof reads exact catalog/node metadata. Keeper remains SELECT-only;
# query_log supplies optional positive lost-ACK evidence, never permission to replay.
# Do not replace these exact grants with a database wildcard or writer privileges.
for table in databases tables replicas clusters parts zookeeper zookeeper_connection query_log
do
  clickhouse --query "GRANT SELECT ON system.$table TO $LEDGER_USER"
done
clickhouse --query "GRANT SELECT ON \`$TARGET_DATABASE\`.* TO $API_USER"

TABLE_COUNT=$(clickhouse --format TabSeparatedRaw --query "SELECT count() FROM system.tables WHERE database='$TARGET_DATABASE'")
PINNED_COUNT=$(clickhouse --format TabSeparatedRaw --query "SELECT count() FROM system.tables WHERE database='$TARGET_DATABASE' AND name IN ('property_definition_catalog','span_attribute_value_catalog','property_catalog_checkpoints','property_catalog_activations','property_catalog_deliveries','property_catalog_source_streams','property_catalog_activation_control_events')")
if [ "$TABLE_COUNT" != "7" ] || [ "$PINNED_COUNT" != "7" ]; then
  echo >&2 "isolated property catalog database does not contain exactly the seven pinned tables"
  exit 65
fi

echo "OSS property catalog ClickHouse bootstrap complete: $TARGET_DATABASE"
