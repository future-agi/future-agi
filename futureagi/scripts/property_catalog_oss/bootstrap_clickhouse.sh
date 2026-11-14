#!/bin/sh
set -eu

# No arguments provisions local OSS; --check only validates a provisioned index.
# Reject every other invocation before any client call.
CHECK_ONLY=false
if [ "$#" -ne 0 ]; then
  if [ "$#" -eq 1 ] && [ "$1" = "--check" ]; then
    CHECK_ONLY=true
  else
    echo >&2 "usage: bootstrap_clickhouse.sh [--check]"; exit 64
  fi
fi

# This schema is separate from source migrations.
SOURCE_DATABASE=${PROPERTY_CATALOG_SOURCE_DATABASE:-default}
TARGET_DATABASE=${PROPERTY_CATALOG_DATABASE:-property_catalog}
SCHEMA_FILE=${PROPERTY_CATALOG_SCHEMA_FILE:-/observed-catalog/schema.sql}
VALIDATION_FILE=$(dirname "$0")/validate_clickhouse.sql
WRITER_PASSWORD=${PROPERTY_CATALOG_CONSUMER_PASSWORD:-oss-observed-writer-local-only}
READER_PASSWORD=${PROPERTY_CATALOG_API_PASSWORD:-oss-observed-reader-local-only}

validate_identifier() {
  case "$2" in
    ''|[0-9]*|*[!A-Za-z0-9_]*)
      echo >&2 "$1 must be a ClickHouse identifier"; exit 64 ;;
  esac
  [ "${#2}" -le 128 ] || { echo >&2 "$1 exceeds 128 characters"; exit 64; }
}
validate_identifier PROPERTY_CATALOG_SOURCE_DATABASE "$SOURCE_DATABASE"
validate_identifier PROPERTY_CATALOG_DATABASE "$TARGET_DATABASE"
case "$TARGET_DATABASE" in
  system|information_schema|INFORMATION_SCHEMA)
    echo >&2 "catalog database must be an isolated application database"; exit 64 ;;
esac
if [ "$SOURCE_DATABASE" = "$TARGET_DATABASE" ]; then
  echo >&2 "catalog database must differ from the source database"
  exit 64
fi
[ -f "$SCHEMA_FILE" ] || { echo >&2 "missing observed catalog schema"; exit 66; }
[ -f "$VALIDATION_FILE" ] || { echo >&2 "missing observed catalog validation"; exit 66; }

clickhouse() {
  clickhouse-client \
    --host "${CLICKHOUSE_HOST:-clickhouse}" \
    --port "${CLICKHOUSE_PORT:-9000}" \
    --user "${CLICKHOUSE_USER:-default}" \
    --password "${CLICKHOUSE_PASSWORD:-}" "$@"
}

# --param_* uses ClickHouse's Escaped text format, not raw bytes. Hex escapes
# preserve quotes, backslashes, Unicode and whitespace without SQL interpolation.
password_parameter() {
  printf '%s' "$1" | od -An -v -tx1 | tr -d ' \n' | sed 's/../\\x&/g'
}

if [ "$CHECK_ONLY" = false ]; then
  WRITER_PARAMETER=$(password_parameter "$WRITER_PASSWORD")
  READER_PARAMETER=$(password_parameter "$READER_PASSWORD")
  clickhouse --query "CREATE DATABASE IF NOT EXISTS \`$TARGET_DATABASE\`"
  clickhouse --database "$TARGET_DATABASE" --multiquery < "$SCHEMA_FILE"
fi

# Check only our new tables: historical tables and their rows remain untouched.
SCHEMA_MATCHES=$(clickhouse --param_database "$TARGET_DATABASE" --format TabSeparatedRaw --queries-file "$VALIDATION_FILE")
[ "$SCHEMA_MATCHES" = "1" ] || {
  echo >&2 "observed catalog schema is incompatible (columns, identity, engine or constraints)"; exit 65;
}

if [ "$CHECK_ONLY" = true ]; then
  # Metadata compatibility alone does not prove this reader can query both indexes.
  # LIMIT 0 checks every column's SELECT permission without reading stored rows.
  for table in observed_attribute_keys observed_attribute_values; do
    clickhouse --query "SELECT * FROM \`$TARGET_DATABASE\`.$table LIMIT 0"
  done
  echo "Observed attribute index validated: $TARGET_DATABASE"
  exit 0
fi

clickhouse --param_password "$WRITER_PARAMETER" --query "CREATE USER IF NOT EXISTS observed_catalog_writer IDENTIFIED WITH sha256_password BY {password:String} HOST ANY"
clickhouse --param_password "$WRITER_PARAMETER" --query "ALTER USER observed_catalog_writer IDENTIFIED WITH sha256_password BY {password:String} HOST ANY"
clickhouse --param_password "$READER_PARAMETER" --query "CREATE USER IF NOT EXISTS observed_catalog_reader IDENTIFIED WITH sha256_password BY {password:String} HOST ANY"
clickhouse --param_password "$READER_PARAMETER" --query "ALTER USER observed_catalog_reader IDENTIFIED WITH sha256_password BY {password:String} HOST ANY SETTINGS readonly=2"
for table in observed_attribute_keys observed_attribute_values; do
  clickhouse --query "GRANT SELECT, INSERT ON \`$TARGET_DATABASE\`.$table TO observed_catalog_writer"
  clickhouse --query "GRANT SELECT ON \`$TARGET_DATABASE\`.$table TO observed_catalog_reader"
done

echo "Observed attribute index ready: $TARGET_DATABASE"
