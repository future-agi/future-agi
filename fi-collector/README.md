# fi-collector

A custom Go OTLP receiver and ClickHouse 25.3 writer for Future AGI's v2 spans
schema. It uses OTel `pdata` and gRPC libraries, **not** the OpenTelemetry Collector
service/processor/exporter framework or OCB. The writer uses **HTTP JSONEachRow**,
not the native `clickhouse-go` protocol.

```text
SDK → OTLP/HTTP JSON or protobuf, or OTLP/gRPC
    → authentication + usage check
    → converter (typed Maps + JSON overflow + GenAI hot columns)
    → in-memory pending rows (whole-request admission budget)
    → single batch flusher → ClickHouse HTTP JSONEachRow
    → optional best-effort curated dimensions and configured sidecars
```

Default batch triggers are **5,000 rows or 5 seconds**, not a hard maximum batch
size. The pending + in-flight row budget is **disabled by default** (0); a
positive `max_pending_rows` explicitly enables admission. See the operational
limits below before treating an OTLP acknowledgement as durable storage.

## Layout and build

- `cmd/fi-collector`: custom entrypoint; YAML plus `FI_*` environment configuration.
- `pkg/server`: HTTP/gRPC receivers, usage checks, row admission and batching.
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
If the file at this path can't be read or parsed, the collector logs a warning and falls back to
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

## Operational backpressure and durability

- HTTP request bodies are capped at 16 MiB; the configurable gRPC receive limit
  defaults to 16 MiB. These are per-request limits, not a total memory limiter.
- `server.max_pending_rows` / `FI_MAX_PENDING_ROWS` opts into a row admission
  budget for both the pending buffer and the batch currently being flushed.
  Default: **0 (disabled)**, preserving the previous unbounded buffer. An explicit
  positive value requires deployment sizing; without one, retained rows and crash
  loss have no configured bound. With admission enabled, a full budget
  rejects the **whole request before enqueue**: HTTP 503 with `Retry-After`, or
  gRPC `RESOURCE_EXHAUSTED` with `RetryInfo`; both are retryable. A request with
  more spans than the entire budget is admitted whenever the buffer is below the
  budget, so the buffer can exceed the budget by at most that one request (itself
  bounded by the 16 MiB size caps); while it is over, every request gets the same
  retryable refusal. No request is refused as non-retryable because of the budget.
  Keep exporter batches at or below the budget anyway: under sustained overload a
  larger one waits like every other request. Do not hot-loop retries.
- `batch_max_rows` above a positive `max_pending_rows` could never trigger a flush, so it is
  clamped to `max_pending_rows` with a startup warning.
- On graceful shutdown, admission closes before the final drain. An HTTP handler
  still running after the 5 s shutdown timeout is refused (HTTP 503 with
  `Retry-After`, gRPC `UNAVAILABLE`) instead of acknowledging rows after the last
  drain.
- Every OTLP/HTTP 4xx/5xx response body is a `google.rpc.Status` message, as the
  OTLP/HTTP specification requires: protobuf for `application/x-protobuf` requests
  and for failures before the media type is known, JSON for `application/json`
  requests. The HTTP status line is authoritative for retryability (429, 502, 503
  and 504 are retryable; every other failure is not).
- The cap bounds retained row count, **not bytes**: large payloads, concurrent
  request decoding, converter allocations and sidecars still use memory. There
  is no Collector-framework `memory_limiter` or advertised 1,000-request queue.
- A successful OTLP response acknowledges in-memory enqueue, **before** the
  ClickHouse insert. Unresolved project spans are reported through OTLP partial
  success, counted as spans rather than ResourceSpans containers. Backpressure
  rejections do not enqueue data or emit accepted-ingestion usage.
- The single flusher uses the writer's configured retry/backoff/timeout policy.
  Failed canonical inserts are appended to a local dead-letter file when that
  disk write succeeds. This is **not** a pre-ack persistent queue and has no
  automatic replay. A full/unwritable disk can also make dead-letter writes fail.
- A hard process/container crash can lose acknowledged in-memory spans. Graceful
  shutdown attempts to drain; it is not crash durability or an exactly-once claim.
- An ambiguous failed insert followed by retry can create duplicate rows. Review
  database state before manually replaying dead-letter data.
- `async_insert` defaults **off**. Enabling it changes acknowledgement/durability
  semantics and is disallowed with the observed catalog path in current startup
  validation. Record the actual settings in any benchmark.
- The admin listener (`:9464`) exposes `/healthz` with writer statistics, a
  `build` object (Go version, VCS revision when the binary was built from a git
  checkout, and `FI_BUILD_REVISION` when set), a `runtime` object (logical CPUs,
  `GOMAXPROCS`, OS/arch and any Go memory limit) and the admission `limits` in force
  (`max_pending_rows`, `batch_max_rows`, `batch_max_age_ms`, gRPC and HTTP size
  caps) so a benchmark can record the revision and budget it measured instead of
  assuming them. Its `admission` object reports the current backlog and, since
  start, the requests and rows refused because the budget was full, the
  larger-than-budget requests admitted, and the requests refused during shutdown.
  The stock binary does not expose the `/metrics` endpoint claimed
  by older docs. `/healthz` is not a loss detector or a proof that every
  acknowledged span landed.
- OTLP/HTTP accepts `Content-Encoding: gzip` (and identity). The 16 MiB cap applies
  to the compressed bytes read and to the decoded bytes; any other coding gets 415.
  gRPC accepts gzip-compressed messages, bounded by the gRPC receive limit.

### Client-visible changes from the previous collector (release note)

The previous collector had no row admission limit. That default is preserved;
no deployment acquires a new row limit just by upgrading. Exporters see these
differences:

1. Only after an operator explicitly enables a positive row budget, a full buffer
   returns HTTP 503 with `Retry-After` or gRPC `RESOURCE_EXHAUSTED` with `RetryInfo`
   (retryable) instead of buffering without limit. Size the budget for the load
   and exporter batches before enabling it (see the benchmark's collector settings).
2. Spans whose project cannot be resolved are reported as OTLP partial success
   (`rejected_spans`, counted as spans) instead of a full success that silently
   dropped them. This corrects reporting, not routing: the same unresolved spans
   are dropped as before, valid spans are still admitted, and OTLP partial success
   must not be retried. Existing routing partial-success responses are preserved.
3. With admission enabled, a request larger than the whole budget is admitted only while the buffer is
   below the budget (see above); it is never refused as non-retryable.
4. Requests that arrive after the final shutdown drain are refused as retryable.
5. OTLP/HTTP error bodies are `google.rpc.Status` messages; gzip request bodies
   are accepted on both transports.

## End-to-end benchmark and current evidence

See [benchmark design and runbook](benchmark/README.rst) for safe defaults, the
OTLP/HTTP protobuf, JSON and gRPC (optionally gzip) wire paths, per-request
outcome accounting (acknowledged, partial, whole-request rejected, ambiguous),
exact-ID ClickHouse verification that separates acknowledged-but-missing spans from
rejected ones and can resume from a checkpoint, the collector settings the 15k
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
