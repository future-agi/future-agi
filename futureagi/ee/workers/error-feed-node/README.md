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

Moved here from the separate `future-agi/omega-error-feed-worker` repo
(TH-8393) — see **EE tree and runtime-source decision** below. Requires Node
22, npm and Docker with BuildKit. From the monorepo root:

```sh
NODE_AUTH_TOKEN="$(gh auth token)" node futureagi/ee/workers/error-feed-node/fetch-runtime.mjs
node futureagi/ee/workers/error-feed-node/prepare-image.mjs
docker build -t omega-error-feed:local .artifacts/node-worker
docker run --rm omega-error-feed:local worker/worker.mjs --healthcheck
docker run --rm -i --network none omega-error-feed:local --input-type=module < futureagi/ee/workers/error-feed-node/kafka-codec-smoke.mjs
```

`fetch-runtime.mjs` needs a GitHub token with `packages:read` on the private
`@future-agi/omega-runtime` package (published from the separate `omega`
repo); it checks the tarball against its reviewed SHA-256 pin. Tarballs and
the build context are generated under ignored `.artifacts/` at the monorepo
root (`resolve(worker, '../../.artifacts/node-worker')`, i.e.
`futureagi/ee/.artifacts/node-worker`). Only that allowlisted context is sent
to Docker; never build this worker from the repository root. The final image
contains compiled runtime packages and worker code, not source checkout,
.git, experiment traces or credentials.

## EE tree and runtime-source decision (TH-8393)

Two choices this move made, written down per the ticket:

1. **Where in the monorepo.** This worker is cloud-only — not something a
   self-hosted customer runs — so it lives under `ee/` (`ee/workers/error-feed-node`),
   the same tree as `ee/usage`, `ee/licensing`, etc. The OSS image still ships
   this directory (nothing here is physically stripped; `ee/` code ships in
   every image and is gated by license checks the same way the existing EE
   apps are — confirmed no file outside `ee/` imports or references this
   worker, so the OSS Python backend boots identically whether or not this
   directory exists).
2. **Fetch the published `@future-agi/omega-runtime` package, not build it
   from the `omega` repo's source.** The package is already a deliberate,
   reviewed artifact — pinned exact version, sha256 and source commit in
   `fetch-runtime.mjs` — and the `omega` repo itself is large and has its own
   independent release cadence. Pulling its full source into this monorepo's
   build graph for one dependency would trade a small, well-defined coupling
   (one pinned npm package) for a much bigger one (a second repo's entire
   history and build surface). Keeping the fetch is the smaller, safer
   coupling.

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
| `OMEGA_CONCURRENCY` | Active investigations per worker; default 4, maximum 512. Claims are fetched in batches of at most 50. |
| `OMEGA_CLICKHOUSE_URL` | HTTP endpoint for the v2 spans table |
| `OMEGA_CLICKHOUSE_DATABASE` | Database; default `default` |
| `OMEGA_CLICKHOUSE_USERNAME`, `OMEGA_CLICKHOUSE_PASSWORD_FILE` | Dedicated SELECT-only account |
| `OMEGA_AUDIO_ALLOWED_ORIGINS` | Optional comma-separated exact HTTPS storage origins; enables one question-driven Gemini audio inspection per investigation. The worker reads the URL from a scoped span and passes it through AgentCC without probing or downloading audio. Leave unset to disable. |
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

Simulation Debug Analysis uses the same daemon and Django control plane but not
Kafka notifications or a per-project trace scanner. For a completed
`TestExecution`, `POST /simulate/test-executions/{id}/debug-analysis/` queues one
execution-scoped job; `GET` returns its current state and findings. Claims carry
`workload_type=simulation_test_execution` and `omega-simulation/v1`. Django pages
only that execution's terminal `CallExecution` rows and transcripts from
PostgreSQL. The worker cites call execution IDs, not span IDs, and publishes
through the existing report endpoint. It has no direct PostgreSQL access.

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

## F6 grouping worker (separate process)

The image also contains `worker/grouping-daemon.mjs`. Run it as a separate
deployment by overriding the image command; the existing investigation command
and model-serving API are unchanged. This code is not enabled by building it.

The grouping process uses the existing `OMEGA_DJANGO_URL`,
`OMEGA_INTERNAL_API_SECRET[_FILE]`, `AGENTCC_BASE_URL`, and
`AGENTCC_API_KEY[_FILE]` settings. It additionally requires:

- `GROUPING_MODEL_ID=google/gemini-3.8-flash`.
- `GROUPING_CALL_RESERVATION_USD`: explicit worst-case per-call reservation.
  Django independently enforces cumulative work, project and tenant caps;
  unknown charges retain their reservations. A reservation is not a provider
  price guarantee.
- `GROUPING_EMBEDDING_URL`: the existing serving `/model/v1/embed` endpoint.
- `GROUPING_EMBEDDING_SERVING_RELEASE`: an operator-managed cache namespace.
  This is **not** a verified model-weights revision. The serving API does not
  supply a weights digest or token-coverage proof.

Optional concurrency settings are `GROUPING_FEATURE_CONCURRENCY` (default 2)
and `GROUPING_CONCURRENCY` (default 1). Feature work and LLM work use separate
pools. The worker has no PostgreSQL or ClickHouse credentials; Django owns
feature persistence, scoped snapshots and atomic Feed publication.

With `OMEGA_KAFKA_BROKERS` configured, the process reuses existing Kafka
TLS/SASL settings to publish durable outbox notifications and consume hints.
`GROUPING_KAFKA_TOPIC` defaults to `error-feed.grouping-ready.v1`; provision
the topic explicitly and grant producer/consumer permissions. Auto-topic
creation is disabled. `GROUPING_KAFKA_GROUP` defaults to `omega-grouping-v1`.
Broker acknowledgement precedes outbox acknowledgement. Lost acknowledgements
may duplicate hints, not memberships. Polling continues if Kafka is unavailable;
restart the process after a terminal Kafka connection failure to restore hints.

Before enabling Django grouping, provision its versioned feature/bucket tables
through the operator migration path and set all three explicit budget caps.
Do not enable it merely because mocked tests pass. This production adaptation
uses MiniLM and bounded, lossless-input chunk pooling rather than the historical
Gemini embeddings, so historical benchmark scores are not production scores.

Review limits remain explicit: existing issues above 16 members, or beyond the
64-member candidate-window budget, are omitted rather than partially validated.
These are review-window limits, not permission to delete existing memberships.
Deferred findings are retained, but automatic reconsideration when a later
report arrives is not yet wired. An oversized checkpoint fails closed without
Feed publication. Paid-call receipts survive a checkpoint failure; exact received
results are reused on retry instead of automatically spending again. These
limits need capacity testing before broad rollout.

## FutureAGI observability

Both daemons support traceAI instrumentation with content capture off by default. At runtime set
`OMEGA_OBSERVABILITY_ENABLED=true` and mount `FI_API_KEY_FILE` / `FI_SECRET_KEY_FILE`. Direct `FI_API_KEY` /
`FI_SECRET_KEY` values work for local development. Set `FI_BASE_URL` for a
custom collector; otherwise the SDK uses FutureAGI cloud. Credentials must
belong to the internal telemetry workspace. Keep Error Feed scanning disabled
on both projects to prevent recursive investigation of worker traces.

The investigation daemon defaults to the internal Observe project
`error-feed-investigation`. The grouping daemon defaults to `error-feed-grouping`,
including feature preparation and severity. Override `FI_PROJECT_NAME` separately
on each deployment. Use the same name for a shared internal project, or different names for separate projects.

The source claim's `organization_id` is exported as `user.id` on every root
and descendant span. This identifies the customer organization using Error Feed.
Updated Django claims also supply `organization_name` and `project_name`, exported
as `user.name`, `error_feed.organization_name`, and `error_feed.project_name`.
The stable user ID does not change when the organization is renamed.
The existing `error_feed.organization_id` and `error_feed.project_id` retain
source scope; they are distinct from the internal Observe project. Concurrent
organizations remain isolated, and an absent organization does not inherit a
user from another root trace.

Each investigation, grouping, feature preparation and severity attempt gets a
separate trace. Investigation includes nested Omega agent stages and evidence
tools; model spans include usage, gateway request ID and reported cost. Filter
by `error_feed.attempt_id` (or `error_feed.feature_attempt_id`) and source
`error_feed.project_id`. Investigation also records the source trace and job
IDs. Customer trace IDs are attributes, not parent trace IDs. Investigation
retries share a session based on the job ID.

Set `OMEGA_OBSERVABILITY_CAPTURE_CONTENT=true` to also export actual work inputs,
agent responses, model requests/responses and tool arguments/results as
`input.value` / `output.value` JSON. It is off by default and only operates when
observability itself is enabled. Content may include customer evidence and
recording URLs. Each field is bounded to 32 KiB with a visible truncation marker.
Control claims are allowlisted to omit lease tokens; known credential object keys
are redacted. This is not a general PII/secret scrubber for free-form content.
Exception messages and stacks are never exported.

Model spans use the routed model as `llm.model_name`, preserving the requested
alias in `gen_ai.request.model`. `gen_ai.cost.total` receives the exact six-decimal
USD charge from AgentCC's response header (including explicit zero), so Observe
does not reprice a known charge. Missing receipts remain unknown in
`error_feed.gateway.cost_status`; Observe may still estimate cost from tokens.
This reflects AgentCC accounting, not a reconciliation with a cloud invoice.
Usage belongs only to model spans: do not copy it to parents and double-count
span/session totals. The current Observe trace list displays root-span usage;
it needs child aggregation to display these trace totals. Session IDs propagate
to every child, allowing whole-session detail aggregation.
Tracing is disabled by default, fails open on configuration/export errors,
and shuts down after active work with a five-second drain limit. Billing and
publication continue to use durable Django receipts.

`telemetry/package-lock.json` pins the traceAI dependency tree. Image preparation
runs `npm ci` and bundles it into a checksum-verified tarball, which is reused
for offline builds while its source digest matches. The final Docker install
remains network-free. The pinned fi-core 1.0.0 uses its working simple exporter;
its batch option does not attach the batch processor correctly with OTel 2.x.

Validation: from `ee/workers/error-feed-node`, `node --test` (recursively
discovers every `*.test.mjs` under this directory, grouping and f6 included).
The export test sends real SDK spans to a local HTTP receiver using fixture
credentials. It does not send customer data or verify delivery to a live account.
See the [FutureAGI quickstart](https://docs.futureagi.com/docs/observe/quickstart)
and [collector endpoint reference](https://docs.futureagi.com/docs/observe/reference/export-formats).

Completed operations explicitly export OTel `OK`. Thrown errors and returned
failed investigations export `ERROR`; a detected customer failure still counts
as a completed investigation. Historical trace statuses are unchanged.

Feature preparation uses the trace name `error_feed.prepare_findings_for_grouping`: it prepares embeddings and lookup features from investigation findings before grouping compares them.


## Sampled grouping review (TH-8368)

The `f6-minilm-sampled/v2` algorithm is explicitly enabled per project in the
backend. The worker also accepts the existing `f6-minilm/v1` policy. Deploy the
compatible backend and dual-policy worker before activating a project; this
change does not automatically activate projects, requeue failed work, or run
reconciliation. Historical decisions and feature recipes remain unchanged.

Large issues carry a full membership count, revision and digest, plus up to
8 evidence examples. Backend selection retains existing prototypes, a recent
example and lexically diverse examples. It reads finding text for selection;
it does not send every call to the model or fetch every call recording.

Merge reviews use at most 8 examples per source (16 total). Whole examples can
be removed to meet the existing 400 KB evidence budget, retaining at least a
representative and a contrasting example per source when available. An
oversized minimum sample is held without inference. The model must cover every
shown example; uncertainty leaves issues separate. There is no automatic
second review or exhaustive verification pass in this version. Sampling can
miss an incompatible unseen call, so a positive sample is not certification
of every member.

A sampled merge requires a durable `merge-review:` receipt. The backend limits
these requests to 10 per durable work item, across retries, and applies
`ERROR_FEED_GROUPING_MERGE_BUDGET_USD` (default `1`). This additional ceiling
counts maximum reservations conservatively, including interrupted/unknown
usage, even when general budget enforcement is disabled. Existing project,
work and tenant budgets remain additional constraints. Budget denial produces
a recorded hold and permits unrelated grouping results to publish. Failed
model calls stay in the spending ledger but are excluded from publication
evidence. An unresolved prior merge reservation holds without a paid resend.
Under the sampled policy, a general project/work/tenant spending refusal is
also an explicit budget response, distinct from a lease or registry conflict.
The pipeline preserves already admitted commands and marks unfinished findings
`waiting_budget` with `budget_exhausted:<limit>`. Affected work remains
`waiting_budget` and its report remains pending. Backend and worker diagnostics
record the refusal; worker diagnostics include the processing phase. Ordinary
polling does not retry budget-blocked work. The backend's preview-first
`requeue_budget_grouping` command checks current budget, source identity and
registry revision before explicit requeue. It preserves receipts, attempt
counters and cohort spending history, and a fresh claim selects only unassigned
findings without changing full source snapshots. The merge prompt states
the existing headline limit of 12 words / 120 characters.

Unchanged candidates use lightweight membership metadata and sampled finding
rows. Publication locks complete finding rows only for sources of merges,
splits and removals, plus the bounded evidence and pending rows. Candidate
preparation checks available sample capacity before scanning another issue.
Complete membership IDs and sampling statements still require reads; this
change bounds model hydration and finding locks, not all metadata reads.

The backend verifies the complete source membership under locks, including
hard constraints on unsampled findings, then atomically moves all members and
retires the sources. Full membership is not constrained by a model sample cap.
Under the sampled policy, protected issues can receive evidence-grounded new
occurrences while retaining their reviewed title, mechanism and saved diagnosis.
Acknowledged status is retained. Attaching to a resolved issue reopens the same
issue as `for_review`, keeps it protected, and records the transition and its
triggering occurrences in the durable publication decision. Attachment and
reopening commit atomically; repeated publication cannot duplicate the transition.
Protected issues remain excluded from automatic merge/split/removal/refresh;
manual merging is deferred. Partial samples cannot authorize split/removal.
Default-policy worker attachment selection retains its original behavior; the
backend's existing default-policy protected-attachment restriction remains.

A worker checkpoint's topology status is `proposed`, not a database commit.
The coordinator freezes the exact publication payload, including receipt
ordering, before sending it. Ambiguous transport/server errors receive one
identical retry; validation conflicts do not. Reclaimed attempts reuse that
payload without model work. Success requires the backend's `completed`
acknowledgement and registry revision. Backend membership postconditions are
checked before committing the decision.

Review entry points:

- Worker: `grouping/policy.mjs`, `grouping/f6/pipeline.mjs`,
  `grouping/f6/registry.mjs`, `grouping/coordinator.mjs`, `grouping/gateway.mjs`.
- Backend: `tracer/services/grouping/sampling.py`, `context.py`, `publish.py`,
  `accounting.py`, `budget_recovery.py`, and the `enable_sampled_grouping` /
  `requeue_budget_grouping` management commands.
- New backend coverage: `tracer/tests/test_grouping_sampled_merges.py`.
- Worker coverage: `grouping/engine.test.mjs`, `coordinator.test.mjs`,
  `gateway.test.mjs`, `f6/pipeline-budget.test.mjs`; the cross-service fixture
  follows the explicit committed acknowledgement contract.

The activation command previews by default. `--apply` requires the reviewed
registry revision and refuses active attempts or outstanding reservations.
It fences old checkpoints without rewriting them. Previously failed work must be
reviewed and requeued separately after grouping publication is validated.
