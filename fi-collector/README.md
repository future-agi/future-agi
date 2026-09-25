# fi-collector

The FutureAGI OpenTelemetry Collector — production write path for the new
ClickHouse 25.3 spans store. Replaces the entire
`SDK → Django OTLP → Redis → Celery → PG → PeerDB → CH 24.10 → spans_mv` chain
mandated to die by `PLAN_V2_NO_CDC.md`.

```
Customer SDK (OTLP / HTTP / gRPC)
    → fi-collector  (this binary)
       • OTLP receiver
       • memory_limiter (backpressure)
       • batch processor (10K spans / 5s)
       • clickhouse25 exporter — splits OTel attrs into typed Maps + typed JSON,
                                 writes via clickhouse-go native protocol
    → ClickHouse 25.3 spans table
```

No PG span-write path. No Redis buffer. No CDC. No `spans_mv`. The typed-Map split that used
to run inside CH (and caused the OOMs) now runs in this Go binary at ingest
time, with bounded per-batch memory.

## Two run modes

### Observed attributes

The collector durably enqueues attribute observations after source inserts and
publishes them to one Kafka topic. `fi-property-catalog-consumer` writes two
additive indexes; native metadata readers remain in place. See
[OSS/local setup and recovery](PROPERTY_CATALOG_OSS.md) for Compose, credentials,
storage preservation and bounded historical backfill.

### 1. Bundled with the FutureAGI backend (single docker compose up)

The main `docker-compose.yml` at `future-agi/` adds `fi-collector` as a
service alongside Django, Postgres, ClickHouse, etc. One `docker compose up`
brings everything live; SDKs point at the collector instead of Django's
`/v1/traces` endpoint.

### 2. Standalone (just collector + ClickHouse)

```bash
cd fi-collector/
docker compose -f docker-compose.standalone.yml up
# SDKs → http://localhost:4317 (gRPC) or http://localhost:4318 (HTTP)
# ClickHouse 25.3 at localhost:18123 (HTTP) / 19000 (native)
```

Use this for testing the collector in isolation, or for deploying it as a
sidecar in a non-FutureAGI environment.

## Why a custom OTel exporter

Off-the-shelf options considered:

- **opentelemetry-collector-contrib's `clickhouseexporter`** — assumes its own
  hardcoded schema (otel_traces / otel_logs / otel_metrics). Doesn't know
  about typed Map columns, materialized hot LLM keys, the v2 schema's
  PROJECTION shapes, or the `attributes_extra` typed JSON overflow tier.
  Would require us to rebuild every dashboard query against its schema.

- **Direct CH writer in Django** — keeps Python in the hot path, which is the
  entire reason for moving off PG-as-write-target. Doesn't scale to 1B/day.

- **Custom Go OTel exporter (this)** — uses the official OTel Collector
  framework (receivers / processors / exporters / queue / retry / batching
  all come for free); custom exporter component does ONE thing: take OTLP
  span pdata and write a row matching `tracer/services/clickhouse/v2/schema/`
  via the official `clickhouse-go/v2` native driver. Same code path SigNoz,
  ClickStack, and Uptrace all use.

## Layout

```
fi-collector/
├── cmd/fi-collector/main.go               — standalone collector entrypoint
├── pkg/
│   ├── adapter/                           — typed-Map split logic
│   │   ├── adapter.go                     — port of pg_to_ch_adapter.py:split_attributes
│   │   └── adapter_test.go                — table-driven tests pinning every branch
│   └── chwriter/                          — CH 25.3 writer
│       ├── writer.go                      — clickhouse-go/v2 batched bulk insert
│       └── writer_test.go
├── exporter/clickhouse25exporter/         — OTLP span-to-row conversion
│   ├── converter.go
│   ├── converter_test.go
│   └── e2e_ch_test.go
├── config/
│   └── collector.yaml                     — writer, server, auth, and catalog settings
├── Dockerfile                             — Go build + distroless runtime
├── docker-compose.standalone.yml          — collector + CH only, for isolated testing
├── go.mod / go.sum
└── README.md                              — this file
```

## Build / run

Run these commands from `fi-collector/` with Go 1.24.3 or newer (see
`go.mod`). The collector builds directly from the checked-in command; no
OpenTelemetry Collector builder (OCB) or Makefile is required.

```bash
go build -o bin/fi-collector ./cmd/fi-collector
go test ./pkg/adapter ./pkg/chwriter
./bin/fi-collector --help
```

To run against an existing local Future AGI stack, use the checked-in
`config/collector.yaml`. Set `FI_CH_URL` to the ClickHouse **HTTP** endpoint
(not its native-protocol port) and `FI_PG_WRITE` to the platform's Postgres
connection string. Postgres is required to resolve API keys and project IDs.
The config's `clickhouse` and `db` hostnames are container-network names;
replace them with host-accessible endpoints when running the binary on your host.

```bash
# Set FI_CH_URL and FI_PG_WRITE for your running stack before this command.
FI_DEAD_LETTER_FILE=./dead_letter.jsonl \
  ./bin/fi-collector --config config/collector.yaml
```

Also set `FI_AUTH_REDIS_ADDR` to enable quota enforcement, usage metering,
and cache invalidation. See `config/collector.yaml` for the other settings.

## PostgreSQL authentication configuration

The collector still needs PostgreSQL to resolve API keys and project IDs. For
each write/read endpoint, configuration precedence is:

1. Explicit `FI_PG_WRITE` / `FI_PG_READ` connection string, preserved verbatim
   (including its TLS options).
2. Separate `FI_PG_{WRITE,READ}_{HOST,PORT,DATABASE,USER}` fields. All four are
   required together; `PASSWORD` is optional. Use a DNS hostname or an
   **unbracketed** IPv4/IPv6 address, with the numeric port supplied separately.
3. YAML `auth.pg_write` / `auth.pg_read`.

Any nonempty separate field activates validation, even when an explicit
connection string is also set. Partial fields, invalid hosts or invalid ports
fail startup with an error naming the setting, without logging its value;
they never silently select a fallback. If no read endpoint is configured, auth
reuses the write endpoint. Separate passwords are URL-encoded by Go, not by a
deployment template; an empty/unset `PASSWORD` leaves the driver's normal
`PGPASSWORD` / password-file or passwordless-authentication behavior available.

The separate-field form does **not** force `sslmode=disable`. Like the Python
backend's libpq connection, pgx honors `PGSSLMODE`, `PGSSLROOTCERT`, `PGSSLCERT`,
and `PGSSLKEY`. Explicit connection-string options take precedence over these
environment variables. Invalid TLS settings fail connection initialization;
they do not trigger a fallback to another configured endpoint.

Production/remote PostgreSQL **must explicitly configure `verify-full`**:
set `PGSSLMODE=verify-full` and provide the appropriate CA with `PGSSLROOTCERT`
(plus `PGSSLCERT`/`PGSSLKEY` if client certificates are required), or supply the
equivalent TLS options in each explicit connection string. Audit both write and
read strings: their options override the shared environment.
`verify-full` verifies the server identity and has no plaintext fallback.
`require` requires encryption but is not equivalent to hostname verification.
For an intentionally non-TLS local database, set `PGSSLMODE=disable` explicitly.
With no SSL mode configured, pgx retains libpq's `prefer` default for OSS/local
compatibility: it tries TLS but **can fall back to plaintext**. This default is
deliberately compatible with Python/libpq and existing non-TLS local PostgreSQL,
not a secure default for remote connections. The collector does not infer
deployment type from hostnames: production TLS is mandatory deployment
configuration, not automatic runtime enforcement. The root Compose file forwards
these four TLS variables; mount certificate files read-only at their configured
container paths using a deployment override. Other deployments must explicitly
pass the variables and mounts to the collector process/container.

## Pricing Configuration

### FI_PRICING_JSON

Optional path to a litellm `model_prices_and_context_window.json` file. When set, the
collector uses the file at this path to price token-based cost; when empty or unset, the
collector falls back to an embedded snapshot of the litellm pricing table (current at build time).
If the file at this path can't be read or parsed, the collector logs an error and falls back to
the embedded snapshot rather than disabling token-based pricing.

Use this to refresh pricing without rebuilding the collector:

```bash
# Mount a newer pricing file (e.g., from a ConfigMap or shared volume)
FI_PRICING_JSON=/etc/fi-collector/model_prices.json ./bin/fi-collector --config config/collector.yaml
```

Refresh the embedded snapshot by re-vendoring `fi-collector/pkg/pricing/model_prices.json` at build
time (this is a compile-time `//go:embed`):

```bash
# Inside the repo
curl -sSL https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json \
  -o fi-collector/pkg/pricing/model_prices.json
cd fi-collector
go build -o bin/fi-collector ./cmd/fi-collector
```

**Note:** Token-based cost computation also includes a per-organization fallback (`CustomAIModel`)
for models not in the litellm table. Custom model pricing is stored in the Django Postgres database
(scoped by organization); the collector reads it on-demand and caches for 24 hours. When neither
the litellm table nor custom pricing applies, the span's cost is 0.

## Operational backpressure model

```
SDK   ──HTTP/gRPC──►   fi-collector
                        ├─ OTLP receiver       (default queue: 1K requests)
                        ├─ memory_limiter      (hard ceiling — drops if exceeded)
                        ├─ batch processor     (10K spans / 5s, whichever first)
                        ├─ retry-on-failure    (exponential, max 5 min)
                        └─ persistent queue    (disk-backed, survives restart)
                                ▼
                        ClickHouse 25.3 (async_insert=1 server-side batching)
```

If ClickHouse is briefly unavailable, the persistent queue absorbs the
backlog. If memory crosses the limit, the receiver returns 429 and SDKs
back off — no silent drops, no OOM crashes. Same pattern SigNoz uses.

## Migration relationship

- This component is the **steady-state writer**. After it's deployed, the
  old `bulk_create_observation_span_task` (Celery), `PayloadStorage`
  (Redis buffer), the PG `tracer_observation_span` table, all PeerDB infra,
  the CH 24.10 `spans_mv`, and the entire `docker-compose.peerdb.yml`
  CAN be deleted (per `PLAN_V2_NO_CDC.md` §4–5).
- The **historical backfill** (one-shot, Python, lives in `planning/.../migration/scripts/`)
  populates the new CH cluster with everything PG was holding at cutover
  time. After this collector is live, new spans flow direct, and the
  historical-data tooling is archived.

## Status

This is the scaffolding + adapter + writer + exporter component, with
tests. Production readiness needs:

- Real load test (10K+ spans/sec sustained against a real CH cluster)
- Integration with the main `docker-compose.yml`
- Monitoring/metrics exporter for ops visibility
