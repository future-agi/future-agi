# fi-collector

A custom Go OTLP receiver and ClickHouse 25.3 writer for Future AGI's v2 spans
schema. It uses OTel `pdata` and gRPC libraries, **not** the OpenTelemetry Collector
service/processor/exporter framework or OCB. The writer uses **HTTP JSONEachRow**,
not the native `clickhouse-go` protocol.

```text
SDK → OTLP/HTTP JSON or protobuf, or OTLP/gRPC
    → authentication + usage check + byte leases
    → converter (typed Maps + JSON overflow + GenAI hot columns)
    → in-memory pending rows (retain accounted work leases)
    → bounded concurrent flushers → ClickHouse HTTP JSONEachRow
    → optional best-effort curated dimensions and configured sidecars
```

Default batch triggers are **5,000 rows or 5 seconds**, not a hard maximum batch
size. Current ingestion uses byte leases from reading through synchronous writer
and sidecar ownership. See the operational limits below before treating an OTLP
acknowledgement as durable storage. A byte budget is not a hard RSS bound.

## Layout and build

- `cmd/fi-collector`: custom entrypoint; YAML plus `FI_*` environment configuration.
- `pkg/server`: HTTP/gRPC receivers, usage checks, byte admission and batching.
- `exporter/clickhouse25exporter/converter.go`: OTLP-to-v2 row conversion.
- `pkg/adapter`, `pkg/chwriter`: attribute splitting and HTTP inserts/retries.
- `pkg/curatedwriter`, `pkg/observedcatalog`, `pkg/traceavailable`: sidecar paths.
- `config/collector.yaml`: current configuration (no local/prod OCB configs).
- `cmd/loadgen`: deterministic seeding tool; direct converter/writer by default,
  optional OTLP/gRPC wire mode. Its manifest is not a loss/throughput benchmark.
- `cmd/otlpbench`, `pkg/ingestbench`, `benchmark/`: bounded OTLP measurement and
  independent persistence verification, reporting and sizing inputs.

From this directory, with the Go toolchain declared in `go.mod`:

```bash
go build -o bin/fi-collector ./cmd/fi-collector
go test ./...
./bin/fi-collector --config config/collector.yaml
```

There is no Makefile or OCB install step. Configure real PostgreSQL auth and the
ClickHouse schema/endpoint before running the production binary. It fails startup
without PostgreSQL auth. Root application Compose deployments provide the broader
stack; the older `docker-compose.standalone.yml` is **not a complete authenticated
standalone deployment** and must not be advertised as a working two-service
production setup. Do not start it on shared host ports as a benchmark shortcut.

### Observed attributes

Observed attributes are disabled by default. Configured direct mode writes the
observed catalog from the collector; Kafka mode uses a durable observation spool
and `fi-property-catalog-consumer`. That spool is for attribute observations after
source inserts, **not a durable queue for incoming canonical span payloads**. See
[OSS/local setup and recovery](PROPERTY_CATALOG_OSS.md).

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
go build -o bin/fi-collector ./cmd/fi-collector
```

**Note:** Token-based cost computation also includes a per-organization fallback (`CustomAIModel`)
for models not in the litellm table. Custom model pricing is stored in the Django Postgres database
(scoped by organization); the collector reads it on-demand and caches for 24 hours. When neither
the litellm table nor custom pricing applies, the span's cost is 0.

## Ingest limits and backpressure

```
SDK ──OTLP/HTTP or gRPC──► fi-collector
                            ├─ auth, quota, live-heap check     (nothing read yet)
                            ├─ wire lease, then read the body   (capped; read deadline)
                            ├─ scan the body, work lease        (limits; price of decode + rows)
                            ├─ decode, route/stamp, reprice     (final authenticated shape)
                            ├─ convert, park the rows           (the rows keep their lease)
                            └─ flush: inserts of a bounded size, ─► ClickHouse 25.3
                               up to 3 at once
```

One budget bounds the memory that all OTLP requests (plain, gzip and gRPC)
hold at once, from reading a body until ClickHouse has stored its rows. A
request leases its body before reading it and the price of decoding and
storing it (measured from the body before it is decoded) before decoding it.
When the budget is full, requests wait their turn while it frees up (up to
2 s in all per request, for both leases together, `FI_INGEST_MAX_WAIT`), then
get **503 + Retry-After: 1** (HTTP) or **UNAVAILABLE** (gRPC), which OTLP
exporters retry. The wait is short on purpose: the OpenTelemetry SDKs give an
export 10 s in all, retries included, so a longer wait used up their budget
before they could retry, and they dropped the batch. At 2 s the Python SDK's
exporters get about three attempts per export. A request that can never succeed (over a cap, too large for the whole
work pool, malformed) gets **413/400** or **INVALID_ARGUMENT**, which exporters
do not retry. gRPC calls are authenticated before their message is read.
HTTP's new 30 s body-read deadline starts after the initial wire lease;
gRPC's starts before the length prefix and includes the wire wait. Client
export deadlines may expire earlier (the Collector otlp exporter commonly
uses 5 s per attempt); for large batches that fit, consider 15 s or smaller
batches. Lease ordering prevents cross-pool waits, not same-pool growth:
gzip and chunked plain bodies can wait while holding part of the wire pool
until the no-progress timeout refuses them. Header/auth/routing resource additions
are reserved before conversion by checking the final decoded shape without
serializing another body. This may grow the existing work reservation under the
same wait budget; an occupied pool is retryable, but a final shape larger than
the whole pool or stored-size limit is permanently refused before enqueue or
usage emission. Promoted string columns, their JSON escaping, and zero-parent
curated roots are included. More accurate accounting is not a promise that a
previously accepted batch still fits, nor acceptance of burst-delivery loss.

Quota refusals are HTTP 429 without Retry-After and gRPC RESOURCE_EXHAUSTED
without RetryInfo. The Go SDK and Collector otlp exporter do not retry that
gRPC status; some other SDKs do. Temporary project resolution failures are
503 + Retry-After: 1 / UNAVAILABLE and are not negatively cached. This includes
an authentication-cache entry expired, evicted or replaced before or during
project lookup/create, even a same-workspace replacement. The old lookup cannot merge
into the replacement; the failed export enqueues no spans and emits no usage.
Retry authenticates again; a revoked key remains rejected. Project creation
may already have succeeded, so retry resolves the persisted project.
Deterministic value/check failures may drop resources; their rejected span
count is reported in OTLP partial success.

Resource `project_name` values are kept verbatim, including significant spaces,
commas, and format/variation characters in visible names such as `deploy ✈️`
and `team 👩‍💻`. This applies both to existing and newly created projects.
Ingest still rejects invalid UTF-8, names over 255 Unicode characters, Unicode
Cc controls, and entirely invisible/blank names. These are collector safeguards:
the app serializers accept some invisible/control names, trim edge whitespace,
and are not changed here. The stricter header-only rules (including rejection
of format/variation characters and line separators) do not apply to resource
names. Use resource attributes for names that cannot be sent in a header.

Storage-mode ingestion usage (`observe_add.amount`) uses the canonical OTLP
protobuf size of accepted traces **after** project-header filling, org/project
stamping and permanent resource drops. HTTP protobuf, HTTP JSON, either with
gzip, and gRPC use the same calculation. It includes accepted resource/scope
metadata and stamped attributes; it is decoded semantic bytes, not wire bytes,
JSON text length, or compressed/stored ClickHouse table bytes. This preserves
gRPC's existing byte basis and intentionally replaces HTTP's decompressed-body
length, including on full success for protobuf/JSON/plain/gzip. It is not raw
JSON or compressed-body length: gzip already used the decompressed body before
this change. JSON formatting no longer changes usage, gzip ratio still does not,
and rejected resources never contribute. Event-mode usage still
counts accepted distinct traces plus accepted spans. Fully dropped or empty
batches emit no ingestion usage. Usage follows enqueue, not confirmation of a
durable database insert; existing retry/dedup semantics are unchanged.

Standalone uses GOMEMLIMIT=512MiB: a 216 MiB ingest budget and a 144 MiB
work pool, with three ClickHouse inserts at once. 8,192 spans with 50 short string attributes each can exceed it;
start Collector pipelines at send_batch_size/send_batch_max_size 4096 and
reduce further when needed. GOMEMLIMIT is soft, not a hard RSS bound. The
512MiB setting was measured on the collector alone; whole-container load at
minimum Standalone resources has not been measured, so leave headroom.

| Setting | Default | What |
|---|---|---|
| `FI_INGEST_BUDGET_MIB` (`server.ingest_budget_mib`) | about half of the Go memory limit, less a fixed allowance | the budget. Without GOMEMLIMIT the collector sets the Go memory limit to 90% of its container's memory limit; with neither, the budget is 256 MiB. |
| `FI_OTLP_MAX_BODY_MIB` (`server.otlp_max_body_mib`) | 16 | largest OTLP/HTTP body, as sent and after gzip decompression; lowered to fit a sixth of the budget |
| `FI_GRPC_MAX_RECV_MIB` (`server.grpc_max_recv_mib`) | the body cap | largest OTLP/gRPC message |
| `FI_OTLP_MAX_CONNECTIONS` (`server.otlp_max_connections`) | 0 (no cap) | open connections per listener |
| `FI_INGEST_MAX_WAIT` (`server.ingest_max_wait`) | 2s | longest a request waits, both leases together, before 503 + Retry-After: 1 / UNAVAILABLE. A Go duration (`1500ms`, `3s`); keep it well under your exporters' export timeout |
| `FI_INSERT_CONCURRENCY` (`server.insert_concurrency`) | 3 from a 512 MiB Go memory limit up (and with none), 2 from 384 MiB, else 1 | ClickHouse inserts of pending rows that run at once. Each takes two insert buffers out of the budget's fixed allowance |

Per request, a body may also hold at most 65,536 spans, 10,000 ResourceSpans,
1,048,576 attributes (array entries included), 64 KiB of resource attributes
per ResourceSpans, attribute values nested at most 64 levels, and rows of at
most 8 times the body cap once encoded for ClickHouse. The resolved limits
are logged at startup ("ingest limits"), and `/healthz` reports the budget's
use and the requests refused by transport, reason and code under `ingest`.

If ClickHouse is slow or down, the pending rows keep their share of the
budget until their inserts finish (or are dead-lettered), so new requests
wait and then get 503 / UNAVAILABLE instead of growing memory.

Pending rows are stored by up to `FI_INSERT_CONCURRENCY` inserts at once:
each wake-up of the flusher (row count, age, or a request waiting for the
budget) swaps the pending batch out to one free inserter, and a large
batch's inserts (each of about the insert size) also go out at once, within
the same cap. A batch's leases
are released when its insert has ended and every batch swapped out before it
has too, so a lease is never released while ClickHouse may still be reading
its rows. Shutdown waits for the inserts under way, then stores what is
left. Span rows carry their own `_version`, so the order in which concurrent
inserts land does not matter; the curated `end_users`/`trace_sessions` rows
are version-stamped when their batch is swapped out, in swap order. At one
insert at a time (R1 on #3598), Standalone stored about 500-800
attribute-heavy spans a second.

A chunk failure does not suppress stored-root notifications or catalog handoff
for confirmed successful sibling chunks. These sidecars follow exact input-row
positions, not span IDs (IDs may repeat), and unconfirmed/ambiguous or merely
async-accepted chunks are excluded. Retry and dead-letter policy are unchanged;
there is no extra canonical insert or ingestion usage event for a sidecar.
All row leases remain held through the synchronous catalog and best-effort
curated writes. The HTTP writer also waits for its transport to close every
request body, including redirect/replay bodies, before releasing encoding
ownership; response EOF alone is not that lifetime boundary.

Refusals from the ingest pipeline over OTLP/HTTP (400, 413, 429, 503) carry
a `google.rpc.Status` body in the request's media type (protobuf or JSON),
with the same message as the gRPC status, so exporters can log why; 415 and
405, and the auth middleware's refusals, stay text/plain.

## Measurement and durability boundaries

Successful OTLP responses acknowledge in-memory enqueue before ClickHouse insertion.
The byte budget bounds accounted ingestion ownership, not hard RSS, crash loss or
exactly-once storage. Graceful shutdown closes enqueue before the final drain.
The admin `/healthz` retains `ingest` and adds build/runtime/writer/limits and
observational pending-plus-in-flight row counts (`admission`). These counts include
synchronous sidecars; some counted rows may already be query-visible.
`limits.admission_model` is `byte-leases-v1`; `max_pending_rows: 0` is a legacy
receipt field, not an unbounded-memory configuration. `FI_MAX_PENDING_ROWS` is
obsolete and rejected at startup. Use the byte-budget settings above. gRPC gzip
remains disabled because its decompression cannot be covered by the wire leases;
HTTP gzip is supported and budgeted.

## End-to-end benchmark and current evidence

See [benchmark design and runbook](benchmark/README.rst) for safe defaults, the
OTLP/HTTP protobuf/JSON (optionally gzip) and uncompressed gRPC wire paths, per-request
outcome accounting (acknowledged, partial, whole-request rejected, ambiguous),
exact-ID ClickHouse verification that separates acknowledged-but-missing spans from
rejected ones and can resume from a checkpoint, the measured collector settings for the 15k
spans/s x 24h and 60k spans/s burst target profiles need (neither profile has been
run), direct ClickHouse and authenticated application read probes, provenance
recording, measurement limitations and an explicitly extrapolated sizing
worksheet.

The CLI is offline by default and requires `--execute` to send anything. Remote
hosts need an exact allowlist and run-ID confirmation; larger load also needs a
separate confirmation. It never starts/stops containers or inserts directly into
ClickHouse. Unit/mock tests and a small real smoke do **not** establish capacity.
The full-scale soak, burst, application read latency at that volume and measured
hardware-specific sizing remain pending until run on provisioned infrastructure.

Deploying this collector alone is not authorization to delete old ingestion or
migration services. Cutover, reconciliation, rollback and historical backfill
must be verified for each deployment separately.
