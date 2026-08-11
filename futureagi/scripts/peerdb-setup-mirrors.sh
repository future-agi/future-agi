#!/bin/bash
# Setup PeerDB mirrors for PG -> CH replication.
#
# This script runs INSIDE a Docker container (peerdb-init) and connects
# to the PeerDB server via the Docker network. It creates:
#   1. A PostgreSQL peer (source)
#   2. A ClickHouse peer (destination)
#   3. CDC mirrors for each replicated table
#
# It can also be run locally if you have psql installed:
#   PEERDB_HOST=localhost PEERDB_PORT=9900 ./scripts/peerdb-setup-mirrors.sh

set -euo pipefail

# PeerDB connection (inside Docker: peerdb-server:9900, locally: localhost:9900)
PEERDB_HOST="${PEERDB_HOST:-peerdb-server}"
PEERDB_PORT="${PEERDB_PORT:-9900}"

# Source PG config (Docker service names)
SRC_PG_HOST="${SRC_PG_HOST:-db}"
SRC_PG_PORT="${SRC_PG_PORT:-5432}"
SRC_PG_USER="${SRC_PG_USER:-user}"
SRC_PG_PASSWORD="${SRC_PG_PASSWORD:-password}"
SRC_PG_DB="${SRC_PG_DB:-tfc}"

# Destination CH config (Docker service names)
DST_CH_HOST="${DST_CH_HOST:-clickhouse}"
DST_CH_PORT="${DST_CH_PORT:-9000}"
DST_CH_USER="${DST_CH_USER:-default}"
DST_CH_PASSWORD="${DST_CH_PASSWORD:-}"
DST_CH_DB="${DST_CH_DB:-futureagi}"

echo "==> Waiting for PeerDB server at ${PEERDB_HOST}:${PEERDB_PORT}..."
for i in $(seq 1 60); do
    if psql "host=${PEERDB_HOST} port=${PEERDB_PORT} user=peerdb password=peerdb dbname=peerdb" -c "SELECT 1" &>/dev/null; then
        echo "==> PeerDB is ready"
        break
    fi
    if [ "$i" -eq 60 ]; then
        echo "==> ERROR: PeerDB did not become ready in 120s"
        exit 1
    fi
    echo "    Waiting... ($i/60)"
    sleep 2
done

run_sql() {
    psql "host=${PEERDB_HOST} port=${PEERDB_PORT} user=peerdb password=peerdb dbname=peerdb" -c "$1"
}

echo ""
echo "==> Creating PostgreSQL source peer..."
run_sql "
CREATE PEER IF NOT EXISTS pg_source FROM POSTGRES WITH (
    host = '${SRC_PG_HOST}',
    port = '${SRC_PG_PORT}',
    user = '${SRC_PG_USER}',
    password = '${SRC_PG_PASSWORD}',
    database = '${SRC_PG_DB}'
);
" || echo "    (peer may already exist)"

echo ""
echo "==> Creating ClickHouse destination peer..."
run_sql "
CREATE PEER IF NOT EXISTS ch_dest FROM CLICKHOUSE WITH (
    host = '${DST_CH_HOST}',
    port = '${DST_CH_PORT}',
    user = '${DST_CH_USER}',
    password = '${DST_CH_PASSWORD}',
    database = '${DST_CH_DB}',
    disable_tls = true
);
" || echo "    (peer may already exist)"

echo ""
echo "==> Creating CDC mirrors..."

# CH25 cutover: skip the tracer_observation_span connector when the
# legacy CDC chain is retired (default for dev/local docker compose,
# opt-in for prod via env var). fi-collector is the canonical writer
# for `spans` post-cutover. See docs/CH25_MIGRATION.md.
DROP_LEGACY_CDC_CHAIN="${CH25_DROP_LEGACY_CDC_CHAIN:-false}"
case "${DROP_LEGACY_CDC_CHAIN,,}" in
    1|true|yes|on) SKIP_OBS_SPAN_MIRROR=1 ;;
    *)             SKIP_OBS_SPAN_MIRROR=0 ;;
esac

# Fact tables: high-frequency writes, need initial snapshot, 10s sync
FACT_TABLES=()
if [[ "${SKIP_OBS_SPAN_MIRROR}" -eq 0 ]]; then
    FACT_TABLES+=("public.tracer_observation_span:tracer_observation_span")
else
    echo "==> CH25_DROP_LEGACY_CDC_CHAIN set — skipping tracer_observation_span mirror"
fi
FACT_TABLES+=(
    # Trace analytics (kept — actively used by `trace_dict`, eval reads, sessions)
    "public.tracer_trace:tracer_trace"
    "public.tracer_eval_logger:tracer_eval_logger"
    "public.trace_annotation:trace_annotation"
    "public.model_hub_score:model_hub_score"
    "public.trace_session:trace_session"
    # Dataset analytics
    "public.model_hub_dataset:model_hub_dataset"
    "public.model_hub_column:model_hub_column"
    "public.model_hub_row:model_hub_row"
    "public.model_hub_cell:model_hub_cell"
    # Simulation analytics (fact/event tables)
    "public.simulate_test_execution:simulate_test_execution"
    "public.simulate_call_execution:simulate_call_execution"
    # Usage / Eval analytics
    "public.usage_apicalllog:usage_apicalllog"
)

# Dimension tables: infrequently updated lookup tables, 30s sync
DIMENSION_TABLES=(
    "public.simulate_scenarios:simulate_scenarios"
    "public.simulate_agent_definition:simulate_agent_definition"
    "public.simulate_agent_version:simulate_agent_version"
    "public.simulate_run_test:simulate_run_test"
    # Prompt tables
    "public.model_hub_promptversion:model_hub_promptversion"
    "public.model_hub_prompttemplate:model_hub_prompttemplate"
    "public.model_hub_promptlabel:model_hub_promptlabel"
    # User profiles
    "public.tracer_enduser:tracer_enduser"
)

# CDC-only mirrors — no initial snapshot. Use peerdb-bulk-copy.sh for initial data copy.
create_mirror() {
    local src_table="$1"
    local dst_table="$2"
    local sync_interval="$3"
    local mirror_name="mirror_${dst_table}"

    echo "    Creating mirror: ${src_table} -> ${dst_table} (sync=${sync_interval}s, CDC-only)"
    if ! run_sql "
    CREATE MIRROR IF NOT EXISTS ${mirror_name}
    FROM pg_source TO ch_dest
    WITH TABLE MAPPING (
        ${src_table}:${dst_table}
    )
    WITH (
        do_initial_copy = false,
        soft_delete = true,
        soft_delete_col_name = '_peerdb_is_deleted',
        synced_at_col_name = '_peerdb_synced_at',
        sync_interval = ${sync_interval}
    );
    " 2>&1; then
        echo "    WARNING: Failed to create mirror ${mirror_name} (may already exist)"
    fi
}

# Create mirrors for fact tables (10s sync interval)
for mapping in "${FACT_TABLES[@]}"; do
    src_table="${mapping%%:*}"
    dst_table="${mapping##*:}"
    create_mirror "${src_table}" "${dst_table}" 10
done

# Create mirrors for dimension tables (30s sync interval)
for mapping in "${DIMENSION_TABLES[@]}"; do
    src_table="${mapping%%:*}"
    dst_table="${mapping##*:}"
    create_mirror "${src_table}" "${dst_table}" 30
done

echo ""
echo "==> Done! All CDC mirrors configured (CDC-only, no initial snapshot)."
echo "    Run peerdb-bulk-copy.sh to copy existing data, then CDC handles changes."
