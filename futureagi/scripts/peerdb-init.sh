#!/bin/bash
# =============================================================================
# PeerDB CDC Setup — One-command initialization
#
# Sets up PeerDB CDC replication from PostgreSQL to ClickHouse.
# Safe to run multiple times (idempotent).
#
# Usage:
#   ./scripts/peerdb-init.sh
#
# Environment variables (auto-detected from running containers):
#   PG_CONTAINER     — PostgreSQL container name (default: auto-detect)
#   CH_CONTAINER     — ClickHouse container name (default: auto-detect)
#   BACKEND_CONTAINER — Backend container name (default: auto-detect)
#   PG_HOST          — PG hostname inside Docker network (default: container name)
#   PG_PORT          — PG port (default: 5432)
#   PG_USER          — PG user (default: user)
#   PG_PASSWORD      — PG password (default: password)
#   PG_DB            — PG database (default: tfc)
#   CH_HOST          — CH hostname inside Docker network (default: container name)
#   CH_PORT          — CH port (default: 9000)
#   CH_USER          — CH user (default: default)
#   CH_PASSWORD      — CH password (default: empty)
#   CH_DB            — CH database (default: auto-detect from backend env)
# =============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[OK]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
err()  { echo -e "${RED}[ERR]${NC} $1"; }
step() { echo -e "\n${GREEN}==>${NC} $1"; }

# ─── Auto-detect containers ──────────────────────────────────────────────────

find_container() {
    local pattern="$1"
    docker ps --format '{{.Names}}' | grep -i "$pattern" | head -1
}

PG_CONTAINER="${PG_CONTAINER:-$(find_container postgres)}"
CH_CONTAINER="${CH_CONTAINER:-$(find_container clickhouse)}"
BACKEND_CONTAINER="${BACKEND_CONTAINER:-$(find_container backend)}"

if [ -z "$PG_CONTAINER" ] || [ -z "$CH_CONTAINER" ]; then
    err "Cannot find postgres or clickhouse containers. Are they running?"
    exit 1
fi

# Auto-detect hostnames (container names = DNS names in Docker network)
PG_HOST="${PG_HOST:-$PG_CONTAINER}"
PG_PORT="${PG_PORT:-5432}"
PG_USER="${PG_USER:-user}"
PG_PASSWORD="${PG_PASSWORD:-password}"
PG_DB="${PG_DB:-tfc}"

CH_HOST="${CH_HOST:-$CH_CONTAINER}"
CH_PORT="${CH_PORT:-9000}"
CH_USER="${CH_USER:-default}"
CH_PASSWORD="${CH_PASSWORD:-}"

# Auto-detect CH database from backend env
if [ -z "${CH_DB:-}" ] && [ -n "$BACKEND_CONTAINER" ]; then
    CH_DB=$(docker exec "$BACKEND_CONTAINER" env 2>/dev/null | grep "^CH_DATABASE=" | cut -d= -f2 || echo "")
fi
CH_DB="${CH_DB:-default}"

echo "Configuration:"
echo "  PG: ${PG_HOST}:${PG_PORT}/${PG_DB} (container: ${PG_CONTAINER})"
echo "  CH: ${CH_HOST}:${CH_PORT}/${CH_DB} (container: ${CH_CONTAINER})"
echo "  Backend: ${BACKEND_CONTAINER:-none}"

# ─── Helper: run SQL on PeerDB ───────────────────────────────────────────────

PEERDB_CONN="host=peerdb-server port=9900 user=peerdb password=peerdb dbname=peerdb"

peerdb_sql() {
    docker exec "$PG_CONTAINER" psql "$PEERDB_CONN" -c "$1" 2>&1
}

# ─── Step 1: Ensure PeerDB containers are running ────────────────────────────

step "Checking PeerDB containers..."

PEERDB_CONTAINERS="peerdb-catalog peerdb-temporal peerdb-minio peerdb-server peerdb-flow-api peerdb-flow-worker"
MISSING=""
for c in $PEERDB_CONTAINERS; do
    if ! docker ps --format '{{.Names}}' | grep -q "^${c}$"; then
        MISSING="$MISSING $c"
    fi
done

if [ -n "$MISSING" ]; then
    err "Missing PeerDB containers:$MISSING"
    err "Start PeerDB first:"
    err "  docker compose -f docker-compose.yml -f docker-compose.peerdb.yml up -d"
    exit 1
fi
log "All PeerDB containers running"

# ─── Step 2: Wait for PeerDB server ─────────────────────────────────────────

step "Waiting for PeerDB server..."
for i in $(seq 1 30); do
    if docker exec "$PG_CONTAINER" psql "$PEERDB_CONN" -c "SELECT 1" &>/dev/null; then
        log "PeerDB server ready"
        break
    fi
    if [ "$i" -eq 30 ]; then
        err "PeerDB server not ready after 60s"
        exit 1
    fi
    sleep 2
done

# ─── Step 3: Register Temporal search attributes ─────────────────────────────

step "Registering Temporal search attributes..."
docker exec peerdb-temporal temporal operator search-attribute create \
    --namespace default --name MirrorName --type Keyword \
    --address peerdb-temporal:7233 2>/dev/null || true
log "Search attributes registered"

# ─── Step 4: Ensure CH database exists ───────────────────────────────────────

step "Ensuring ClickHouse database '${CH_DB}' exists..."
docker exec "$CH_CONTAINER" clickhouse-client --query "CREATE DATABASE IF NOT EXISTS ${CH_DB}" 2>&1
log "Database ready"

# ─── Step 5: Initialize CH schema via backend ────────────────────────────────

if [ -n "$BACKEND_CONTAINER" ]; then
    step "Initializing ClickHouse schema..."
    docker exec "$BACKEND_CONTAINER" python manage.py shell -c "
from tracer.services.clickhouse.schema import get_all_schema_ddl
from tracer.services.clickhouse.client import get_clickhouse_client
from tracer.services.clickhouse.v2.apply_schema_rewriter import with_dictionary_credentials
ch = get_clickhouse_client()
ok, err = 0, 0
for name, ddl in get_all_schema_ddl():
    try:
        # Dictionaries read their source with the credentials this client uses.
        ch.execute(with_dictionary_credentials(ddl, ch.user, ch.password))
        ok += 1
    except Exception as e:
        if 'already exists' not in str(e).lower():
            err += 1
print(f'{ok} created, {err} errors')
" 2>&1 | grep -E "created|error" | tail -1
    log "Schema initialized"

    # Ensure usage_apicalllog has the correct schema with materialized columns.
    # If the table exists but is missing eval_score (old schema), drop and recreate.
    step "Checking usage_apicalllog schema..."
    EVAL_TABLE_STATUS=$(docker exec "$BACKEND_CONTAINER" python manage.py shell -c "
from tracer.services.clickhouse.client import get_clickhouse_client
from tracer.services.clickhouse.schema import CDC_USAGE_APICALLLOG
c = get_clickhouse_client()
try:
    c.execute_read('SELECT eval_score FROM usage_apicalllog LIMIT 0')
    print('OK')
except Exception as e:
    if 'usage_apicalllog' in str(e) and 'UNKNOWN_TABLE' in str(e).upper():
        # Table doesn't exist — create it fresh
        c.execute(CDC_USAGE_APICALLLOG)
        print('CREATED')
    else:
        # Table exists but missing materialized columns — drop and recreate
        c.execute('DROP TABLE IF EXISTS usage_apicalllog')
        c.execute(CDC_USAGE_APICALLLOG)
        print('RECREATED')
" 2>&1 | grep -E '^(OK|CREATED|RECREATED)$' | tail -1)

    case "$EVAL_TABLE_STATUS" in
        OK)        log "usage_apicalllog schema OK (materialized columns present)" ;;
        CREATED)   log "usage_apicalllog created fresh with materialized columns" ;;
        RECREATED)
            log "usage_apicalllog recreated with new schema"
            warn "Dropping old mirror for fresh sync..."
            peerdb_sql "DROP MIRROR IF EXISTS mirror_usage_apicalllog" 2>/dev/null || true
            sleep 5
            ;;
        *)         warn "usage_apicalllog status unclear: $EVAL_TABLE_STATUS" ;;
    esac
fi

# ─── Step 6: Create peers ───────────────────────────────────────────────────

step "Creating PeerDB peers..."

# PostgreSQL source
RESULT=$(peerdb_sql "CREATE PEER IF NOT EXISTS pg_source FROM POSTGRES WITH (
    host = '${PG_HOST}',
    port = '${PG_PORT}',
    user = '${PG_USER}',
    password = '${PG_PASSWORD}',
    database = '${PG_DB}'
)")
if echo "$RESULT" | grep -q "OK\|already exists"; then
    log "pg_source peer ready"
else
    warn "pg_source: $RESULT"
fi

# ClickHouse destination
RESULT=$(peerdb_sql "CREATE PEER IF NOT EXISTS ch_dest FROM CLICKHOUSE WITH (
    host = '${CH_HOST}',
    port = '${CH_PORT}',
    user = '${CH_USER}',
    password = '${CH_PASSWORD}',
    database = '${CH_DB}',
    disable_tls = 'true'
)")
if echo "$RESULT" | grep -q "OK\|already exists"; then
    log "ch_dest peer ready"
else
    warn "ch_dest: $RESULT"
fi

# ─── Step 7: Create CDC mirrors FIRST (captures WAL during bulk copy) ────────
#
# IMPORTANT: Mirrors must be created BEFORE the bulk copy so PeerDB's
# replication slot captures all WAL changes that happen during the copy.
# Without this, rows written during the copy window would be lost.
#
# Sequence:
#   1. Create mirrors (do_initial_copy=false) → creates PG replication slot
#   2. Bulk copy PG → CH via peerdb-bulk-copy.sh
#   3. CDC catches up with changes made during the copy

SKIP_BULK_COPY="${SKIP_BULK_COPY:-false}"

step "Creating CDC mirrors (do_initial_copy=false)..."

# Fact tables (10s sync)
FACT_TABLES=(
    "tracer_trace"
    "tracer_observation_span"
    "tracer_eval_logger"
    "trace_annotation"
    "trace_session"
    "model_hub_score"
    "simulate_test_execution"
    "simulate_call_execution"
    "usage_apicalllog"
)

# Dimension tables (30s sync)
DIM_TABLES=(
    "simulate_scenarios"
    "simulate_agent_definition"
    "simulate_agent_version"
    "simulate_run_test"
    "tracer_enduser"
    "model_hub_dataset"
    "model_hub_column"
    "model_hub_row"
    "model_hub_cell"
    "model_hub_promptversion"
    "model_hub_prompttemplate"
    "model_hub_promptlabel"
)

create_mirror() {
    local table="$1"
    local sync_interval="$2"
    local mirror_name="mirror_${table}"

    RESULT=$(peerdb_sql "CREATE MIRROR IF NOT EXISTS ${mirror_name}
        FROM pg_source TO ch_dest
        WITH TABLE MAPPING (public.${table}:${table})
        WITH (
            do_initial_copy = false,
            soft_delete = true,
            soft_delete_col_name = '_peerdb_is_deleted',
            synced_at_col_name = '_peerdb_synced_at',
            sync_interval = ${sync_interval}
        )")

    if echo "$RESULT" | grep -qiE "OK|CREATE|MIRROR"; then
        log "  ${mirror_name} (sync=${sync_interval}s)"
    elif echo "$RESULT" | grep -qi "already exists"; then
        log "  ${mirror_name} (exists)"
    else
        warn "  ${mirror_name}: $(echo "$RESULT" | grep -i error | head -1 | cut -c1-120)"
    fi
}

echo "Fact tables (10s sync):"
for table in "${FACT_TABLES[@]}"; do
    create_mirror "$table" 10
done

echo "Dimension tables (30s sync):"
for table in "${DIM_TABLES[@]}"; do
    create_mirror "$table" 30
done

log "Mirrors created — replication slots now capturing WAL changes"

# ─── Step 8: Hybrid bulk copy ────────────────────────────────────────────────
# Runs AFTER mirrors so the replication slot captures any writes during copy.

if [ "$SKIP_BULK_COPY" != "true" ]; then
    step "Running hybrid bulk copy (PG → CH via postgresql())..."
    step "Replication slot is active — no data will be lost during copy"
    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
    if [ -f "${SCRIPT_DIR}/peerdb-bulk-copy.sh" ]; then
        CH_MODE=docker \
        CH_CONTAINER="$CH_CONTAINER" \
        PG_CONTAINER="$PG_CONTAINER" \
        PG_HOST="$PG_HOST" \
        PG_PORT="$PG_PORT" \
        PG_USER="$PG_USER" \
        PG_PASSWORD="$PG_PASSWORD" \
        PG_DB="$PG_DB" \
        CH_USER="$CH_USER" \
        CH_PASSWORD="$CH_PASSWORD" \
        "${SCRIPT_DIR}/peerdb-bulk-copy.sh"
        log "Bulk copy complete"
    else
        warn "peerdb-bulk-copy.sh not found — run it manually, CDC will catch up"
    fi
fi

# ─── Step 9: Verify sync ────────────────────────────────────────────────────

step "Waiting for CDC to catch up (30s)..."
sleep 30

step "Sync status:"
for table in tracer_trace tracer_observation_span tracer_eval_logger model_hub_score trace_annotation usage_apicalllog; do
    PG_COUNT=$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -t -A -c "SELECT count(*) FROM ${table}" 2>/dev/null || echo "?")
    CH_COUNT=$(docker exec "$CH_CONTAINER" clickhouse-client --query "SELECT uniqExact(id) FROM ${table}" 2>/dev/null || echo "?")
    if [ "$PG_COUNT" = "$CH_COUNT" ] && [ "$PG_COUNT" != "?" ]; then
        log "  ${table}: PG=${PG_COUNT} CH=${CH_COUNT} (synced)"
    else
        warn "  ${table}: PG=${PG_COUNT} CH=${CH_COUNT} (syncing...)"
    fi
done

echo ""
log "PeerDB CDC setup complete!"
echo "  PeerDB UI: http://localhost:3001"
echo "  Mirrors will continue syncing CDC changes in the background."
