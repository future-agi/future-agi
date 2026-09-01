#!/bin/sh
set -eu

# Create only the read-only relational source identity used by the OSS catalog
# supervisor. No application table or row is created, altered, or deleted.

CATALOG_READER_PASSWORD=${PROPERTY_CATALOG_PG_READER_PASSWORD:-oss-catalog-postgres-reader-local-only}

case "$CATALOG_READER_PASSWORD" in
  ''|*[!A-Za-z0-9._-]*)
    echo >&2 "PROPERTY_CATALOG_PG_READER_PASSWORD contains unsupported characters"
    exit 64
    ;;
esac
if [ "${#CATALOG_READER_PASSWORD}" -lt 16 ] || [ "${#CATALOG_READER_PASSWORD}" -gt 128 ]; then
  echo >&2 "PROPERTY_CATALOG_PG_READER_PASSWORD must contain 16 to 128 characters"
  exit 64
fi

psql --set=ON_ERROR_STOP=1 --set=catalog_reader_password="$CATALOG_READER_PASSWORD" <<'SQL'
DO $catalog_role$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles
        WHERE rolname = 'property_catalog_oss_reader'
    ) THEN
        CREATE ROLE property_catalog_oss_reader
            LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
            NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 2;
    END IF;
END
$catalog_role$;

ALTER ROLE property_catalog_oss_reader
    LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 2
    PASSWORD :'catalog_reader_password';
GRANT pg_read_all_data TO property_catalog_oss_reader;
ALTER ROLE property_catalog_oss_reader SET default_transaction_read_only = 'on';
ALTER ROLE property_catalog_oss_reader SET statement_timeout = '100000ms';
ALTER ROLE property_catalog_oss_reader SET lock_timeout = '1000ms';
ALTER ROLE property_catalog_oss_reader
    SET idle_in_transaction_session_timeout = '10000ms';
SQL

echo "OSS property catalog PostgreSQL read identity is ready"
