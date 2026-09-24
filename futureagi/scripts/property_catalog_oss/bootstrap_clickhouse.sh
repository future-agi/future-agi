#!/bin/sh
set -eu

# Additive, idempotent bootstrap for the OSS unified property catalog.
# This script may create only the isolated catalog database, its six pinned
# tables, and dedicated local identities. It never mutates source tables.

SOURCE_DATABASE=${PROPERTY_CATALOG_SOURCE_DATABASE:-${CH25_DATABASE:-default}}
TARGET_DATABASE=${PROPERTY_CATALOG_TARGET_DATABASE:-property_catalog_dev_oss}
CLICKHOUSE_HOST=${CLICKHOUSE_HOST:-clickhouse}
CLICKHOUSE_PORT=${CLICKHOUSE_PORT:-9000}
SCHEMA_DIRECTORY=${PROPERTY_CATALOG_SCHEMA_DIRECTORY:-/property-catalog-schema}

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

clickhouse() {
  clickhouse-client \
    --host "$CLICKHOUSE_HOST" \
    --port "$CLICKHOUSE_PORT" \
    --user default \
    "$@"
}

clickhouse --query "CREATE DATABASE IF NOT EXISTS \`$TARGET_DATABASE\`"
for schema in \
  025_property_catalog_data.sql \
  026_property_catalog_state.sql \
  027_property_catalog_delivery.sql
do
  clickhouse --database "$TARGET_DATABASE" --multiquery < "$SCHEMA_DIRECTORY/$schema"
done

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
clickhouse --query "GRANT SELECT ON system.settings TO $SOURCE_USER"
clickhouse --query "GRANT SELECT, INSERT ON \`$TARGET_DATABASE\`.* TO $CONTROL_USER"
for table in databases settings tables
do
  clickhouse --query "GRANT SELECT ON system.$table TO $CONTROL_USER"
done
for table in property_definition_catalog span_attribute_value_catalog property_catalog_deliveries
do
  clickhouse --query "GRANT INSERT ON \`$TARGET_DATABASE\`.$table TO $CONSUMER_USER"
done
for table in property_catalog_activations property_catalog_checkpoints property_catalog_deliveries property_catalog_source_streams
do
  clickhouse --query "GRANT SELECT ON \`$TARGET_DATABASE\`.$table TO $LEDGER_USER"
done
clickhouse --query "GRANT SELECT ON \`$TARGET_DATABASE\`.* TO $API_USER"

TABLE_COUNT=$(clickhouse --format TabSeparatedRaw --query "SELECT count() FROM system.tables WHERE database='$TARGET_DATABASE'")
PINNED_COUNT=$(clickhouse --format TabSeparatedRaw --query "SELECT count() FROM system.tables WHERE database='$TARGET_DATABASE' AND name IN ('property_definition_catalog','span_attribute_value_catalog','property_catalog_checkpoints','property_catalog_activations','property_catalog_deliveries','property_catalog_source_streams')")
if [ "$TABLE_COUNT" != "6" ] || [ "$PINNED_COUNT" != "6" ]; then
  echo >&2 "isolated property catalog database does not contain exactly the six pinned tables"
  exit 65
fi

echo "OSS property catalog ClickHouse bootstrap complete: $TARGET_DATABASE"
