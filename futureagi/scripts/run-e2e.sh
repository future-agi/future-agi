#!/usr/bin/env bash
# =============================================================================
# run-e2e.sh — Start test infrastructure, apply schema, run E2E tests.
#
# Usage:
#   ./scripts/run-e2e.sh              # full suite (Go + Python)
#   ./scripts/run-e2e.sh --go-only    # Go integration tests only
#   ./scripts/run-e2e.sh --py-only    # Python E2E tests only
#   ./scripts/run-e2e.sh --skip-infra # assume compose already up
#
# Prerequisites:
#   - Docker (with compose v2)
#   - Go 1.24+
#   - Python venv at .venv/ with Django deps installed
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FUTUREAGI_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
FI_COLLECTOR_DIR="$(cd "$FUTUREAGI_DIR/../fi-collector" && pwd 2>/dev/null || echo "")"

# Parse flags
RUN_GO=true
RUN_PY=true
SKIP_INFRA=false

for arg in "$@"; do
    case "$arg" in
        --go-only)   RUN_PY=false ;;
        --py-only)   RUN_GO=false ;;
        --skip-infra) SKIP_INFRA=true ;;
        --help|-h)
            head -15 "$0" | tail -10
            exit 0
            ;;
    esac
done

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[e2e]${NC} $*"; }
warn()  { echo -e "${YELLOW}[e2e]${NC} $*"; }
error() { echo -e "${RED}[e2e]${NC} $*"; }

# Exit code tracker
EXIT_CODE=0

# --------------------------------------------------------------------------
# 1. Start test infrastructure
# --------------------------------------------------------------------------

if [ "$SKIP_INFRA" = false ]; then
    info "Starting test compose services..."
    cd "$FUTUREAGI_DIR"
    docker compose -f docker-compose.test.yml -p futureagi-test up -d

    info "Waiting for ClickHouse health..."
    RETRIES=0
    MAX_RETRIES=30
    until docker compose -f docker-compose.test.yml -p futureagi-test \
          exec -T test-clickhouse wget --quiet --tries=1 --spider http://localhost:8123/ping 2>/dev/null; do
        RETRIES=$((RETRIES + 1))
        if [ "$RETRIES" -ge "$MAX_RETRIES" ]; then
            error "ClickHouse did not become healthy after ${MAX_RETRIES} attempts"
            exit 1
        fi
        sleep 1
    done
    info "ClickHouse is healthy."

    info "Waiting for PostgreSQL health..."
    RETRIES=0
    until docker compose -f docker-compose.test.yml -p futureagi-test \
          exec -T test-db pg_isready -U test_user -d test_tfc 2>/dev/null; do
        RETRIES=$((RETRIES + 1))
        if [ "$RETRIES" -ge "$MAX_RETRIES" ]; then
            error "PostgreSQL did not become healthy after ${MAX_RETRIES} attempts"
            exit 1
        fi
        sleep 1
    done
    info "PostgreSQL is healthy."
fi

# --------------------------------------------------------------------------
# 2. Apply schema via Django migrate
# --------------------------------------------------------------------------

info "Applying Django migrations (includes CH schema)..."
cd "$FUTUREAGI_DIR"
if [ -f ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
elif command -v python3 &>/dev/null; then
    PYTHON="python3"
else
    PYTHON="python"
fi

export DJANGO_SETTINGS_MODULE="tfc.settings.test"
$PYTHON manage.py migrate --run-syncdb --no-input 2>&1 | tail -5
info "Migrations applied."

# --------------------------------------------------------------------------
# 3. Go integration tests
# --------------------------------------------------------------------------

if [ "$RUN_GO" = true ]; then
    if [ -z "$FI_COLLECTOR_DIR" ] || [ ! -d "$FI_COLLECTOR_DIR" ]; then
        warn "fi-collector directory not found — skipping Go tests"
    else
        info "Running Go integration tests..."
        cd "$FI_COLLECTOR_DIR"

        export CH_TEST_HOST="localhost:18123"
        export CH_TEST_DATABASE="test_tfc"

        if go test -tags integration -v -count=1 -timeout 120s \
            ./exporter/clickhouse25exporter/ 2>&1; then
            info "Go integration tests PASSED"
        else
            error "Go integration tests FAILED"
            EXIT_CODE=1
        fi
    fi
fi

# --------------------------------------------------------------------------
# 4. Python E2E tests
# --------------------------------------------------------------------------

if [ "$RUN_PY" = true ]; then
    info "Running Python E2E tests..."
    cd "$FUTUREAGI_DIR"

    if $PYTHON -m pytest \
        tracer/tests/test_e2e_collector_to_django.py \
        tracer/tests/test_e2e_eval_lifecycle.py \
        -v -m "e2e" \
        --tb=short \
        -x \
        2>&1; then
        info "Python E2E tests PASSED"
    else
        error "Python E2E tests FAILED"
        EXIT_CODE=1
    fi
fi

# --------------------------------------------------------------------------
# 5. Summary
# --------------------------------------------------------------------------

echo ""
if [ "$EXIT_CODE" -eq 0 ]; then
    info "All E2E tests passed."
else
    error "Some E2E tests failed — see output above."
fi

exit $EXIT_CODE
