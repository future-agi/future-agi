OTLP ingestion benchmark
========================

Scope and acceptance (TH-8687)
-----------------------------

This is reproducible measurement infrastructure, not a published capacity claim.
The acceptance target is **15,000 spans/second for 24 hours** with no missing
attempted span IDs, followed by a separately measured **60,000 spans/second burst
for 60 seconds**. Those are 1,296,000,000 and 3,600,000 spans respectively, NOT
traces. A 24-hour rate extrapolation from a short run is not a soak test.

Shipped:

* A real OTLP/HTTP JSON sender -> collector receiver -> production converter ->
  batch writer -> independently queried ClickHouse path. No direct-insert shortcut.
* Synthetic GenAI payload sizes, deterministic trace/span IDs, root/children,
  session grouping, token metadata, cohorts and one percent synthetic errors.
* Offline-by-default planning, local smoke and explicit full-scale target profiles.
* Generated, attempted, HTTP-200, accepted, acknowledged, partial rejection,
  unacknowledged, persisted distinct, missing and duplicate accounting.
* Bounded exact-ID verification, sampled visibility lag, authenticated application
  read probes, optional per-node ClickHouse and explicit-container resource samples.
* Versioned JSON results, a plain-text report builder and an input-driven sizing
  worksheet. Tests cover dropped/duplicate rows, OTLP partial success, malformed
  responses, redirected requests, budget exhaustion and run-ID reuse.
* Collector pending+in-flight row backpressure and accurate unresolved-project
  partial-success counts, with deterministic reproducing tests.

Deliberately pending, not completed by merging the harness:

* Provision and approve dedicated infrastructure, credentials, hardware/network
  inventory, application fixtures and monitoring for the full-scale profiles.
* Run the entire 24-hour soak and burst, confirm achieved rate and independent
  zero-loss verification, inspect replica consistency and publish reviewed results.
* Measure real list/filter/session/dashboard requests against that data volume.
  A missing read configuration is reported as ``not-configured``, not zero latency.
* Calibrate synthetic size weights/entropy from approved aggregate trace-size
  measurements; no real customer prompts or confidential trace data are included.
* Measure storage/index/rollup amplification and per-shard sustainable capacity,
  fill the sizing worksheet and publish hardware-specific sizing numbers.
* Durable pre-ack span queuing and byte-level/process memory admission control are
  not implemented. A row cap does not establish either guarantee.

Build and deterministic tests
-----------------------------

From ``fi-collector``::

    go build -o /path/to/new/output/otlpbench ./cmd/otlpbench
    go test ./pkg/ingestbench ./cmd/otlpbench ./pkg/server ./cmd/fi-collector
    go test -race ./pkg/ingestbench ./cmd/otlpbench ./pkg/server ./cmd/fi-collector

The mock integration test runs the real receiver/converter/writer against a tiny
in-memory **MOCK ClickHouse HTTP fixture**. It verifies protocol and accounting,
not ClickHouse SQL, storage, throughput or production behavior. To save its
explicitly labeled artifact to a NEW path::

    OTLPBENCH_MOCK_RESULT=/path/to/new/mock-result.json \
      go test ./pkg/ingestbench -run TestMockCollectorPipelineAndIndependentLossDetection -count=1

An opt-in real ClickHouse test sends just 20 spans through the in-process real
collector (PG authentication bypassed ONLY in this test fixture). It requires an
ALREADY provisioned isolated v2 schema, never starts/stops containers and never
creates or deletes tables. Use a dedicated database; it leaves its run-tagged rows
for inspection. The normal test suite explicitly skips this test when unconfigured::

    OTLPBENCH_LIVE_CH_URL=http://127.0.0.1:18123 \
    OTLPBENCH_LIVE_CH_DATABASE=isolated_benchmark \
    OTLPBENCH_LIVE_CONFIRM=isolated-test-stack \
    OTLPBENCH_LIVE_RESULT=/path/to/new/live-result.json \
      go test ./pkg/ingestbench -run '^TestLiveClickHouseCollectorPipeline$' -count=1 -v

Optional credentials: ``OTLPBENCH_LIVE_CH_USER`` and
``OTLPBENCH_LIVE_CH_PASSWORD``. Remote URLs fail closed in this fixture. A passing
20-span test is functional integration evidence, NOT a capacity benchmark.

The ``otlp-benchmark-smoke`` job in ``.github/workflows/fi-collector-ci.yml``
provisions one disposable, pinned ClickHouse 25.3.14.14 container on the hosted CI
runner (2 CPUs/2 GiB). It applies the actual canonical spans SQL and the existing
curated-table DDL, runs the 20-span real collector-to-ClickHouse test, requires an
explicit test PASS rather than a skip, and uploads its JSON/log artifacts. The
job deletes only its uniquely named CI Compose project. It never runs the
sustained/burst profiles or touches a shared developer stack. Adding this job is
not evidence that it has passed; inspect the PR's actual run before claiming that.


Authenticated external-collector smoke
-------------------------------------

Use a provisioned test project, the real collector's API-key authentication, a
separate ClickHouse read credential and the same schema the deployment uses.
Verification requests enforce ``readonly=2``, a 30s server query limit and a
1 GiB query memory limit in addition to the client timeout. The read account must
permit these bounded per-query settings; a stricter profile rejecting them fails
the preflight before load (there is no silent unsafe fallback).
The production collector binary requires PostgreSQL auth; merely running the old
standalone Compose example does not supply a valid auth deployment.

Set variables in your shell or secret manager (do not commit values)::

    export BENCH_PROJECT_ID=<dedicated-test-project-uuid>
    export BENCH_PROJECT_NAME=<dedicated-test-project-name>
    export BENCH_RUN_ID=<new-16-lowercase-hex-value>
    export BENCH_OTLP_URL=http://127.0.0.1:4318/v1/traces
    export BENCH_CH_URL=http://127.0.0.1:8123
    export BENCH_DATABASE=<dedicated-schema>
    export BENCH_OTLP_HEADERS='{"X-API-Key":"<test-key>","X-Secret-Key":"<test-secret>"}'
    export BENCH_CH_HEADERS='{"X-ClickHouse-User":"<reader>","X-ClickHouse-Key":"<password>"}'

Plan first; this performs NO network calls and needs no secret values::

    bash benchmark/run-profile.sh smoke

Only after checking the printed endpoint, project, byte budget and duration::

    bash benchmark/run-profile.sh smoke --execute --out /path/to/new/smoke.json
    go run ./cmd/otlpbench report /path/to/new/smoke.json

The smoke target is 100 spans/s for 5s, <=16 MiB request bodies total. All normal
invocations are dry runs unless ``--execute`` is present. Existing artifacts are
never overwritten. There are no automatic retries or cleanup/deletion commands.

Non-loopback hosts require BOTH exact ``--allow-hosts host-a,host-b`` and
``--confirm-remote "$BENCH_RUN_ID"``. No wildcard, redirects, environment proxies,
URL credentials, credential-like URL parameters or implicit production defaults.
All OTLP, ClickHouse, node-metrics and application-probe URLs are checked. HTTP is
only appropriate inside an isolated trusted test network; use verified HTTPS for
remote credential-bearing requests. This is an operator safety gate, not a claim
that an allowlisted hostname cannot be production: the operator must verify it.

Above 1,000 spans/s, 1 minute or 64 MiB also requires
``--confirm-load "$BENCH_RUN_ID"``. Duration must be <= an explicit
``--max-duration`` (absolute maximum 24h). Every export is <=16 MiB; reduce batch
size for larger payloads. ``--max-bytes`` caps cumulative OTLP request BODY bytes,
not TLS/network overhead, verification responses, disk usage or resource artifacts.
In-flight exports have a timeout; the CLI also bounds settle/verification work.
Ctrl-C stops scheduling and writes an incomplete result when cancellation can be
handled; SIGKILL cannot guarantee a final artifact.

Sustained and burst profiles
---------------------------

Run on dedicated load-generator hosts and provisioned isolated storage, not a
shared developer VM. Scripts do not provision, resize or restart anything::

    bash benchmark/run-profile.sh sustained   # offline plan, 15k/s x 24h
    bash benchmark/run-profile.sh burst       # offline plan, 60k/s x 60s

Executing either requires ``--execute --confirm-load "$BENCH_RUN_ID" --out ...``
and the remote-host approvals where applicable. The sustained script caps body
bytes at 7 TB, and the burst at 24 GB, in decimal bytes. These are safety ceilings,
NOT predictions of compressed storage or measured throughput. Use a fresh run ID
for each run. The profile label is descriptive; it never silently changes flags.

The scheduler paces batches with bounded concurrent requests, not an unbounded
work queue. A slow generator or collector causes missed scheduled work, observable
as ``attempted_spans < planned_spans``. CPU-starved generators must not be used to
claim a storage throughput ceiling. Each run owns one deterministic namespace;
multiple generators need distinct run IDs and their combined rates must be summed
explicitly rather than assuming a single run met a cluster target.

Single-node and 3-shard x 2-replica integration
---------------------------------------------

The benchmark accepts any provisioned topology through explicit endpoints. For a
3x2 test, point OTLP at the cluster-configured collector, ``--ch-url`` at one
coordinator and ``--table`` at the logical Distributed spans table. Do not sum two
replicas of each shard as though they held distinct data. Set
``BENCH_TOPOLOGY=3-shards-2-replicas``. Record the cluster/schema commit alongside
the collector commit or image digest with ``BENCH_REVISION``.

Provide ``--nodes /path/to/nodes.json`` with each physical node exactly once::

    [
      {"name":"shard1-replica1","url":"http://127.0.0.1:18123"},
      {"name":"shard1-replica2","url":"http://127.0.0.1:18124"},
      {"name":"shard2-replica1","url":"http://127.0.0.1:18125"},
      {"name":"shard2-replica2","url":"http://127.0.0.1:18126"},
      {"name":"shard3-replica1","url":"http://127.0.0.1:18127"},
      {"name":"shard3-replica2","url":"http://127.0.0.1:18128"}
    ]

Those ports are illustrative, NOT assumed deployment ports. Configure the actual
published ports. Authentication is the ClickHouse header environment supplied to
the run. Alternatively collect container stats using ``--containers`` with an
explicit list of test containers. Neither path modifies infrastructure.

Verification requires a quiescent run and a coordinator/read configuration whose
replica selection and consistency are understood. The default 15s settle (2m for
large profiles) is NOT a replication guarantee. The tool does not run ``OPTIMIZE``,
``SYSTEM SYNC REPLICA`` or queue-flush commands on your cluster. Configure and
verify consistency externally. A timeout, missing rows or changing totals fails
the run rather than being called zero loss. Cluster replication/failover under
load remains a separate required test; a single-node pass is not a 3x2 result.

Payload model and identity
--------------------------

* ``small``: 512 bytes input+output text per span.
* ``medium``: 4,096 bytes; ``large``: 32,768 bytes.
* ``mixed``: repeating 70% small / 25% medium / 5% large sequence.
* Input/output split: 75/25; deterministic synthetic pseudo-random ASCII text.
  Repeated payload templates make compression more favorable than fully unique
  prompts. Record that limitation and calibrate before sizing real deployments.
* Default: 8 spans per trace (one agent root + 7 LLM children), 4 traces per
  session; configurable independently. Batch boundaries need not align to traces.
* Trace ID = 64-bit run namespace + 64-bit trace ordinal; span ID = 64-bit span
  ordinal. Both are nonzero. The converter stores dashed UUID trace IDs and hex
  span IDs. Exact IDs, payloads and grouping are deterministic given config,
  sequence and generation timestamp. Timestamps are current in a live run.
* ``benchmark.run_id`` and string ``benchmark.seq`` are stamped on every span.
  A nonempty preflight run namespace is refused. Concurrent reuse of a namespace
  is not coordinated; operators must ensure exclusive run-ID ownership.

Accounting and interpretation
-----------------------------

``generated_spans`` counts fabricated spans, including the final batch rejected by
local byte/duration limits. ``attempted_spans`` counts spans in initiated HTTP
exports; timeout before any bytes leave is still an attempted export, not a
confirmed send. ``http_200_spans`` does NOT imply a valid OTLP response.
``accepted_spans`` and ``acknowledged_spans`` intentionally have the same value:
HTTP 200 + valid OTLP response, minus ``partialSuccess.rejectedSpans``. They reflect
what the collector claims to have accepted, not a durable commit.

``unacknowledged_spans`` = attempted - acknowledged - explicit partial rejections.
It includes transport failures, malformed responses and HTTP failures. Such spans
might still exist in storage. Rejected identities cannot be inferred from an OTLP
partial-success count; do not pretend the last N spans were rejected. No exporter
retries means duplicated rows detected here were not created by retrying this CLI.
The collector writer itself can retry ambiguous failures, so duplicates are still
possible and must be measured.

Verification separately counts every run-tagged row in the test project. For each
bounded sequence/trace-ID chunk it checks BOTH deterministic IDs and computes
``uniqExactIf`` over expected span ordinals. The distinct count is exact for the
requested IDs; counting rows alone would let one duplicate hide one missing span.
It reports missing expected spans, excess visible duplicate rows and malformed or
out-of-range rows separately. Unexpected rows prevent a clean result. Chunk state
is bounded (default 65,536, full-scale script 1,000,000), unlike a billion-ID client
set or one billion-row distinct hash table. Verification has a 10-minute budget
and per-query CPU/memory/response limits; incomplete scans are not certified.
Trace-range pruning performance and the verification budget must be validated on
the provisioned schema; reducing verifier cost is not grounds to use approximate
counts as evidence of zero loss.

These reads are not a transactional cross-shard snapshot. The tool checks summed
chunk totals against total rows and fails if they differ, but this is not a
replication correctness proof. MergeTree merges may already have removed duplicate
physical rows; the reported duplicates are only those visible to this scan. No
``FINAL`` hides them deliberately.

Lag is sampled from deterministic batches, at most 4,096 samples, from send time
to the first successful query observing that ID. Reported p50/p95/p99 include
poll/query delay and are **upper bounds**, not insertion commit timestamps.
Unseen samples and query failures remain visible. Histograms have bounded memory:
1ms upper-edge buckets to 10s, 1s above. Failed application requests have separate
latencies; they are not silently discarded to improve p99. A request interrupted
by normal load-window completion is explicitly counted in
``window_censored_requests`` (``measured-with-censoring``), not as a server error
or a successful latency sample. An HTTP failure or a client timeout while the
load window remains open is still an error.

Application read workload
-------------------------

The four categories are ``trace-list``, ``attribute-filter``, ``session-view`` and
``dashboard``. Supply a JSON array with actual current read-only API requests,
usually captured from a test account in the application. Use the generated project,
session IDs and time window; ensure queries return relevant seeded data, not an
empty project or a static health response. A probe is HTTP-level API latency, not
browser rendering/end-user latency. It does not call ClickHouse in place of the app.

Each record requires ``name``, ``url``, ``method`` (GET/POST), ``read_only: true``,
``headers_env``, ``expect_status: 200``, ``expect_json_key`` (top-level response key),
and ``interval_ms >= 1000``. POST bodies are explicit JSON in ``body``. The
read-only assertion is the operator's responsibility; the tool is not a generic
mutation stress tester. Auth comes from the named environment variable (header
map); it is not written into results. Missing auth variables fail before load.
The response must be HTTP 200 with parseable JSON and the expected key, so a login
page or HTTP-200 error without that key is not counted as a successful read.

Example STRUCTURE, deliberately not an invented working route::

    [
      {
        "name":"trace-list",
        "url":"http://127.0.0.1:8000/<actual-read-route-and-query>",
        "method":"GET",
        "read_only":true,
        "headers_env":"BENCH_APP_HEADERS",
        "expect_status":200,
        "expect_json_key":"<actual-response-key>",
        "interval_ms":1000
      }
    ]

Provide all four real request definitions with ``--read-probes`` for a full run.
The result includes successes/errors/status codes, successful and failed latency
histograms, response bytes and explicit missing categories. The harness does not
automatically seed dashboards or authenticate a browser. Those are dependencies,
not implemented features or fabricated measurements.

Artifacts and resource measurements
----------------------------------

``--out run.json`` creates a new mode-0600 JSON result with schema
``fi.otlpbench.v1`` and a separate ``run.json.telemetry.jsonl`` stream. First/last
resource snapshots are in the main result; periodic resources (10s cadence, serial
bounded queries) and independent 1s cumulative-counter samples go to the stream to avoid a day-long in-memory sample list.
Every stream line has a schema version, run ID and kind; progress counter deltas
divided by actual elapsed-time deltas expose achieved interval rates and stalls.
Sampling timestamps, not assumed exact timer cadence, are authoritative. Resource
permissions/errors are recorded, not replaced with zero. Do not commit
unreviewed artifacts: URLs, queries, project names and machine/container labels can
identify a private deployment even though credential headers are excluded.

Generator metrics identify themselves as GENERATOR-only: OS/architecture, CPU
count, Go/runtime heap, process user/system CPU seconds, peak RSS bytes and working
filesystem capacity. They are not the collector's CPU or disk. Per-node optional
queries collect ClickHouse ``system.asynchronous_metrics``, ``system.metrics``,
``system.disks`` and active ``system.parts`` by table. Docker stats records named
container CPU%, memory, network and cumulative block I/O with Docker's displayed
units; block I/O is not free disk. Neither node sampling path promises OS metrics
unavailable in that deployment. Record external infrastructure telemetry when
query permissions or container visibility are absent.

Review measured target/achieved duration and counts, partial responses, query
errors, lag samples, app coverage, resource gaps and hardware inventory before
publishing. ``measured-profile-complete`` means the requested span count was
acknowledged and independently verified with no visible duplicates/unexpected IDs;
it does NOT mean full-scale targets were tested or all four app probes configured.
A dry run, mock fixture or small real smoke must stay labeled as such.

Sizing worksheet: inputs, not invented nodes
-------------------------------------------

Copy ``sizing-input.template.json`` outside the repository and replace every null
with a measurement. The builder rejects missing, nonpositive, infinite or
inconsistent inputs. A pre-filled node-count recommendation is intentionally
absent until a capacity test supplies it::

    go run ./cmd/otlpbench size /path/to/measured-sizing-inputs.json

The output is labeled ``extrapolation_not_measured_capacity``. Units/formulas:

* Retained physical bytes = spans/day x retention days x measured physical
  bytes/(logical span x replica) x replicas. Include indexes, rollups and actual
  observed compression. Measure before/after active part bytes on an otherwise
  quiet dedicated workload. Do not substitute uncompressed OTLP body bytes.
* Storage nodes = ceil(retained bytes / (disk bytes/node x usable fraction)).
* Throughput shards = ceil(peak spans/s / measured sustainable spans/(s x shard)).
* Minimum nodes = max(storage nodes, throughput shards x replicas), rounded up
  to a full replica group. This is only a planning lower bound, not an SLA.

Record CPUs, RAM, disk medium/capacity/IOPS, network bandwidth/RTT, cloud/instance
class, collector replicas/resources, CH version/settings, shard/replica count,
replica write/read consistency, compression, TTLs, rollups, batching, writer retry
policy, probe rate, producer utilization and noisy-neighbor isolation for every
published row. Test failover/headroom separately; extrapolation assumes per-shard
linearity and does not establish it. Do not extrapolate trace rates to span rates
without a measured spans/trace distribution.

Public comparison context (checked 2026-10-21)
---------------------------------------------

These are vendor-published claims, not measurements from this harness and not an
apples-to-apples ranking. Dates, topology, span sizes and loss/lag methodology vary.
No claim such as "second fastest" follows from these sources.

* Langfuse enterprise page: >80M TRACES/day and >5B tracing EVENTS/month across a
  multi-tenant cloud deployment. Neither unit can be substituted for spans/s.
  https://langfuse.com/enterprise/enterprise
* Opik scaling guide: production workload of 4-6M TRACES/day; 40M stored traces,
  250M stored spans, published backend/infrastructure inputs. Those stored totals
  are not a measured spans/day ingestion rate.
  https://www.comet.com/docs/opik/self-host/scaling.mdx
* Opik engineering article (2025-01-29): roughly 3,000 EVENTS/s on one local
  machine, including workload sizes and traces with three spans. Events are not
  silently relabeled as spans.
  https://www.comet.com/site/blog/llm-observability-architecture-engineering/
* Arize adb: terabytes daily, billions of spans and traces monthly, petabyte-scale
  managed architecture. Those statements are not a same-hardware zero-loss 24h
  benchmark against this collector.
  https://arize.com/docs/ax/concepts/adb/overview
  https://arize.com/blog/adb-database-realtime-ingestion/
* OTLP response and retry semantics:
  https://opentelemetry.io/docs/specs/otlp/

The competitive opportunity is reproducible, transparent evidence with hardware,
units, failure counts and workload definitions. Publishing an unrun target or
calling an acknowledgement durable storage would not provide that evidence.
