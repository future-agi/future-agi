# Local Omega Node worker

This is the local Error Feed Node service under integration.
It installs one Omega runtime npm tarball into a private local Docker image. There
is no Python wheel, npm publication, Docker push, or Omega source copy into Django.

The daemon records Kafka notifications in Django before acknowledging offsets,
claims due investigations, downloads scoped ClickHouse evidence to temporary files,
and runs an Omega controller with bounded child investigations and a final verifier.
It saves finished reports before publication so upload retries do not rerun inference.
The file-tool adapter has a separate engine version; previous full-prompt benchmark
scores do not establish its accuracy. The cross-service worker test passes with
real Kafka, ClickHouse, Django and Postgres, and a scripted gateway response.

## Build locally

From this worker repository, follow the [root build instructions](../../README.md)
to fetch the private runtime tarball from GitHub Packages. Then:

```sh
node workers/error-feed-node/prepare-image.mjs
docker build -t omega-error-feed:local .artifacts/node-worker
docker run --rm omega-error-feed:local worker/worker.mjs --healthcheck
docker run --rm -i --network none omega-error-feed:local --input-type=module < workers/error-feed-node/kafka-codec-smoke.mjs
```

Tarballs and the build context are generated under ignored `.artifacts/`.
Only that allowlisted context is sent to Docker; never build this worker from
the repository root. The final image contains compiled runtime packages and
worker code, not source checkout, .git, experiment traces or credentials.

The collector emits Snappy-compressed records. The daemon registers the pinned
`kafkajs-snappy` / `snappyjs` decoder; KafkaJS alone cannot consume these records.
The build verifies all three public dependency tarballs and reuses them offline.
The image-level codec check exercises the actual daemon import and both raw and
Xerial-framed Snappy. The current-stack run exposed this missing codec before
any model call; do not interpret a healthy Kafka TCP port as a working consumer.

## Run a local job

Mount a directory containing a job and evidence. For example:

```json
{"id":"job_1","objective":"Inspect the recorded refund amount.","evidence_file":"trace.jsonl"}
```

Configure `AGENTCC_BASE_URL` ending in `/v1`, `OMEGA_MODEL_ID`, and either
`AGENTCC_API_KEY_FILE` (preferred runtime-mounted secret) or `AGENTCC_API_KEY`.
The model must be allowed and priced by the gateway's existing configuration.
Use Docker's runtime `--env-file` or secret mounts, never build arguments.

```sh
docker run --rm --read-only --tmpfs /tmp:rw,noexec,nosuid,size=128m \
  --mount type=volume,src=omega-local-scratch,dst=/var/lib/omega/scratch \
  --env-file /absolute/path/to/local-worker.env \
  --mount type=bind,src=/absolute/path/to/evidence,dst=/evidence,readonly \
  --mount type=bind,src=/absolute/path/to/results,dst=/results \
  omega-error-feed:local worker/worker.mjs --job /evidence/job.json --output /results/result.json
```

The result directory must be writable by container UID 1000. A failed invocation
returns a failed result and exits nonzero. The file-job command is a runtime smoke
test. The image starts the Kafka/coordinator daemon by default.

## Run the daemon

Set these variables at runtime, never in the image:

| Variable | Value |
| --- | --- |
| `OMEGA_DJANGO_URL` | Backend base URL, without `/tracer` |
| `OMEGA_INTERNAL_API_SECRET_FILE` | Mounted Django internal-service secret |
| `OMEGA_KAFKA_BROKERS` | Comma-separated broker addresses |
| `OMEGA_KAFKA_TOPIC` | Default `error-feed.trace-available.v1` |
| `OMEGA_KAFKA_GROUP` | Default `omega-error-feed-v1` |
| `OMEGA_KAFKA_TLS` | `true` for TLS |
| `OMEGA_KAFKA_USERNAME`, `OMEGA_KAFKA_PASSWORD_FILE` | Optional SCRAM-SHA-512 credentials |
| `OMEGA_ENGINE_VERSION` | Must match the enabled project's `scan_version` |
| `OMEGA_CONCURRENCY` | Active investigations per worker; default 4, maximum 50 |
| `OMEGA_CLICKHOUSE_URL` | HTTP endpoint for the v2 spans table |
| `OMEGA_CLICKHOUSE_DATABASE` | Database; default `default` |
| `OMEGA_CLICKHOUSE_USERNAME`, `OMEGA_CLICKHOUSE_PASSWORD_FILE` | Dedicated SELECT-only account |
| `OMEGA_AUDIO_ALLOWED_ORIGINS` | Optional comma-separated exact HTTPS storage origins; enables one question-driven Gemini audio inspection per investigation. The worker reads the URL from a scoped span, checks it with HEAD, and passes the URL through AgentCC without downloading audio. Leave unset to disable. |
| `OMEGA_REPORT_SPOOL` | Persistent mounted directory writable by UID 1000; image default `/var/lib/omega/reports` |
| `OMEGA_SCRATCH_DIR` | Temporary evidence on disk; image default `/var/lib/omega/scratch` |

Also set the AgentCC variables described above. Supply secrets through mounted files;
the corresponding variable without `_FILE` is supported for local fixtures.

Use a dedicated account with SELECT only on the intended spans table and
`readonly=2`, so bounded per-query settings remain configurable. The worker does
not change the account's read-only mode. Verify the account's identity, grants,
and a bounded empty-scope read before starting it; do not use a writable service
account. The current-stack account was verified with no INSERT/ALTER/DROP/CREATE
privileges before removing the incompatible query-level read-only override.

Enable `ERROR_FEED_OMEGA_ENABLED` in Django and set the project's scanner
configuration to `engine=omega`, `enabled=true`, and the matching `scan_version`.
Legacy ingestion and sweep paths skip Omega projects. Enable collector notifications
with `FI_ERROR_FEED_ENABLED=true` and `FI_ERROR_FEED_KAFKA_BROKERS`; asynchronous
ClickHouse insert acknowledgement is rejected for this mode.

The collector announces an ended root after its batch write. It does not certify
that all children have arrived. Django applies the readiness delay. The worker's
server-issued read cutoff excludes later rows but cannot recover historical versions
removed by ClickHouse merges. Oversized or malformed evidence fails the attempt;
it is never silently truncated into a successful scan. Spans with non-empty
`input_gcs_url` or `output_gcs_url` are marked as unresolved in the model's
inventory and make `coverage.read_complete=false`. They block a success conclusion
but do not discard failures supported by inline span evidence. The worker never
fetches these URLs; external payload resolution and customer application readback
still require authorized evidence adapters and an approved bucket/tenant mapping.
No external URL resolver ships with this worker.

Mount a persistent spool separately from temporary evidence. Temporary files are
removed after each attempt; saved reports remain until Django acknowledges them.
Do not use tmpfs for the report spool in a real deployment.
Mount separate disk-backed volumes at both image defaults when using a read-only
root filesystem. New Docker volumes inherit UID-1000 directory ownership from the
image; pre-existing volumes or bind directories must already have suitable access.
Per-trace byte limits do not replace host disk monitoring and retention for crash
residue. A full disk prevents report persistence; do not acknowledge publication
or delete pending reports to hide that failure.

## Verification boundary

Focused Node tests cover exact evidence bytes, scope rejection, overflow, host-owned
citations, controller/child/return/verifier execution, cost preservation, lease
cancellation, bounded concurrency and publication recovery. The model responses in
those tests are scripted. Collector tests cover write-before-notify, failed writes,
batch limits and scope filtering. Django tests cover the private API and persistence.

The cross-service smoke also loses the first publication acknowledgement after
Django commits. The worker republishes the saved report; Django returns the same
report ID, and model calls remain at four. The persisted synthetic result contains
one pending occurrence and USD 0.0004 of fixture-reported cost. Actual inference
cost is zero. This test does not include OTLP collector ingestion or a real provider.

The backend also has bounded, cursor-based notification reconciliation and reviewed
feedback, memory candidates, evaluation receipts, promotion and rollback. Its 21
focused PostgreSQL/contract tests pass. Candidate generation and replay execution
are not yet wired; accepting an evaluation receipt is not proof that replay ran.

Still required before release: OTLP-to-report test, live-model regression of the
file-tool adapter, scheduled runners, learning/replay execution, clustering consumer
integration and per-tenant gateway credential/accounting validation. The current
integration work reuses existing scanner clustering behind a replaceable adapter;
Atharva's new clustering is not required to exercise the existing UI path. Do not
claim the whole product is shipping from these tests.

Kafka offset handling follows the [KafkaJS consumer contract](https://kafka.js.org/docs/consuming).

## Pricing

AgentCC's `x-agentcc-cost` header is the reported USD charge. We retain each
call's request/response IDs, requested/routed model, cache status, raw token usage
including cached/reasoning details, and cost. Integer micro-USD accumulation
avoids summing per-call rounded floating-point rates. No static model pricing is
maintained here. Missing or malformed costs make the total unknown while
preserving the known subtotal. An explicit reported zero remains zero. Cache
hits with no cost header remain unknown; zero-charge cache policy belongs to
the gateway. This does not independently certify gateway billing accuracy.

Source boundary inspected:
`future-agi/agentcc-gateway/internal/server/handlers.go:setAgentccHeaders`
and `internal/plugins/cost/cost.go`. Non-streaming responses are used so
pricing is read after gateway completion.

Local HTTP mock tests exercise protocol and accounting; they are not live-model
quality evaluations. Gateway error bodies are excluded from worker error logs.
No model calls happen during package building or image healthcheck.
