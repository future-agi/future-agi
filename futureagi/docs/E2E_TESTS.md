# E2E Test Suite — ClickHouse 25.3 Migration

End-to-end tests that verify the full span lifecycle: OTLP ingestion through fi-collector, ClickHouse 25.3 storage, and Django read-path visibility.

## Quick Start

```bash
# Full suite (starts compose, applies schema, runs Go + Python tests)
./scripts/run-e2e.sh

# Skip infrastructure startup (compose already running)
./scripts/run-e2e.sh --skip-infra

# Go only / Python only
./scripts/run-e2e.sh --go-only
./scripts/run-e2e.sh --py-only
```

## Prerequisites

- Docker with compose v2
- Go 1.24+ (for fi-collector tests)
- Python venv at `.venv/` with all Django deps
- Test compose services: `docker compose -f docker-compose.test.yml -p futureagi-test up -d`

### Test Infrastructure Ports

| Service    | Port  | Purpose                    |
|------------|-------|----------------------------|
| PostgreSQL | 15432 | Django test DB             |
| Redis      | 16379 | Cache/Celery               |
| ClickHouse | 18123 | HTTP (queries + inserts into `test_tfc`) |
| ClickHouse | 19000 | Native TCP                 |
| MinIO      | 19005 | Object storage (GCS stub)  |

## Test Files

### Go (fi-collector)

| File | Description |
|------|-------------|
| `fi-collector/exporter/clickhouse25exporter/e2e_ch_test.go` | Integration tests: converter -> chwriter -> real CH -> query back |

Build tag: `//go:build integration`. Requires `CH_TEST_HOST` env var (set by `run-e2e.sh`).

```bash
CH_TEST_HOST=localhost:18123 CH_TEST_DATABASE=test_tfc go test -tags integration -v ./exporter/clickhouse25exporter/
```

### Python (Django)

| File | Description |
|------|-------------|
| `tracer/tests/test_e2e_collector_to_django.py` | Collector -> CH -> Django API visibility |
| `tracer/tests/test_e2e_eval_lifecycle.py` | Span -> eval task -> eval result in CH |

Markers: `@pytest.mark.integration`, `@pytest.mark.e2e`, `@pytest.mark.slow`.

```bash
# Run only E2E tests
pytest tracer/tests/test_e2e_collector_to_django.py tracer/tests/test_e2e_eval_lifecycle.py -v -m e2e

# Excluded from default pytest run (addopts has -m "not e2e")
```

## Skip Behavior

- **Go tests**: skip if `CH_TEST_HOST` is not set or CH is unreachable
- **Python collector tests**: skip if fi-collector is not running at `localhost:4318`
- **Python eval tests**: fall back to direct CH seeding (via `_ch_seed.py`) when collector is unavailable

## Test Isolation

- Each test uses unique span IDs (UUID-based) to avoid cross-test collisions
- CH `spans` table is truncated between tests via `autouse` fixtures
- Go tests generate deterministic-but-unique IDs from the test name

## Scenarios Covered

1. **Typed-Map split** — `attrs_string`, `attrs_number`, `attrs_bool` populated correctly
2. **JSON columns** — `metadata`, `resource_attrs` parse as JSON; `attributes_extra` is queryable
3. **is_deleted = 0** for fresh spans
4. **LLM span** — hot columns (model, provider, tokens) populated from GenAI semconv
5. **Error span** — status=ERROR, status_message preserved
6. **Tool span** — parent_span_id link, observation_type=TOOL
7. **Voice/conversation span** — fi_native semconv, CONVERSATION type
8. **Multi-span trace** — 3 spans same trace, all queryable
9. **Django visibility** — span visible via `list_spans_observe` endpoint
10. **Dataset creation** — ingested spans can be added to a new dataset
11. **Eval lifecycle** — span -> eval task creation -> eval result visibility
