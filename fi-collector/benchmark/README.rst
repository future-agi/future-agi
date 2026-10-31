OTLP ingestion benchmark
========================

Scope and acceptance (TH-8687)
------------------------------

This is reproducible measurement infrastructure, not a published capacity claim.
The acceptance target is **15,000 spans/second for 24 hours** with no missing
acknowledged span IDs, followed by a separately measured **60,000 spans/second
burst for 60 seconds**. Those are 1,296,000,000 and 3,600,000 spans respectively,
NOT traces. A 24-hour rate extrapolation from a short run is not a soak test.
**Neither profile has been run.** Everything below describes what the harness
does and what its tests prove, not a measured capacity.

Shipped:

* A real OTLP sender -> collector receiver -> production converter -> batch
  writer -> independently queried ClickHouse path, over OTLP/HTTP protobuf
  (default), OTLP/HTTP JSON (both optionally gzip) or uncompressed OTLP/gRPC. No
  direct-insert shortcut.
* A seeded synthetic GenAI workload: distinct texts with lognormal sizes, bounded
  trace trees (agent/chain/LLM/tool/retriever spans), sessions, cohorts, token
  counts and a configurable error rate; realised distributions are reported.
* Per-request outcome accounting with a checked identity: every attempted span is
  acknowledged, partially rejected, rejected with its whole request (retryable or
  not, with the server's retry hint) or ambiguous.
* Exact-ID verification of every attempted span, decomposed by what the collector
  said: acknowledged-but-missing (data loss) versus rejected-and-missing (expected)
  versus ambiguous, plus rejected-but-persisted (a contract violation). Chunked,
  bounded per query, time-pruned, rechecked and resumable from a checkpoint.
* Direct ClickHouse read probes (trace by ID, session by ID, attribute filter)
  during load and after settle, separate from authenticated application API probes.
* Provenance: redacted command line, generator build and host hardware,
  ClickHouse version/table keys/clusters and server memory/CPU limits, the
  collector's ``/healthz`` build, runtime CPUs and admission limits, and the
  revision of the system under test.
* Observed byte-budget provenance and pressure samples. A row-count heuristic
  is retained only for historical collectors; it cannot assess current memory
  sufficiency, exporter retry success or capacity.
* Offline-by-default planning, local smoke and explicit full-scale profiles;
  versioned JSON results; plain-text reports; an input-driven sizing worksheet.
* Five separately counted stages (offered, scheduled, attempted, accepted, durable)
  plus read-visible sentinels, each as a total and a 1 s telemetry series.
* Read-visible lag from the SCHEDULED send on up to 40,960 sentinel spans of the
  load, bracketed by the polls around each one; the server-side insert lag
  (``created_at``) of every stored span, corrected by a measured clock offset;
  request latency from the scheduled send next to the actual send.
* The collector backlog over time (``/healthz`` buffered rows and writer counters)
  with its slope, drain, restarts and insert stalls, separate from throughput.
* Recovery from faults injected by a test harness or declared by an operator:
  acknowledged loss attributed per fault, duplicates, backlog drain and recovery
  time; missing spans listed as request ranges by outcome.
* ``otlpbench publish``: a Markdown and JSON publication with 24 disclosure fields,
  a "Not measured" section and a headline throughput withheld unless the run earns
  it. What every number means and which comparisons it does not support is in
  `METHODOLOGY.rst <METHODOLOGY.rst>`_.

Deliberately pending, not completed by merging the harness:

* Provision and approve dedicated infrastructure, credentials, hardware/network
  inventory, application fixtures and monitoring for the full-scale profiles.
* Run the 24-hour soak and the burst, confirm achieved rate and independent
  zero-loss verification, inspect replica consistency and publish reviewed results.
  The verification cost at 1.3 billion spans (chunk query time, total budget) is
  an unmeasured allowance until then.
* Measure real list/filter/session/dashboard requests against that data volume.
  A missing read configuration is reported as ``not-configured``, not zero latency.
* Calibrate the synthetic size/structure model from approved aggregate trace
  measurements; no real customer prompts or confidential trace data are included.
* Measure storage/index/rollup amplification and per-shard sustainable capacity,
  fill the sizing worksheet and publish hardware-specific sizing numbers.
* Durable pre-ack span queuing and byte-level/process memory admission control are
  not implemented in the collector. A row cap does not establish either guarantee.

Build and deterministic tests
-----------------------------

From ``fi-collector``::

    go build -o /path/to/new/output/otlpbench ./cmd/otlpbench
    go test ./pkg/ingestbench ./cmd/otlpbench ./pkg/server ./cmd/fi-collector
    go test -race ./pkg/ingestbench ./cmd/otlpbench ./pkg/server ./cmd/fi-collector
    go test -run '^$' -bench BenchmarkBuildRequest ./pkg/ingestbench   # generator cost

The mock integration tests run the real receiver/converter/writer against a small
in-memory **MOCK ClickHouse HTTP fixture** that emulates only the verification and
probe queries. They verify protocol and accounting over every wire path, the
backpressure and larger-than-budget cases, HTTP 429 with ``Retry-After`` from a throttling
proxy in front of the collector, the per-request decomposition of missing and
refused-but-stored spans, resumable verification and the rejection of invalid or
changing responses, exactly K missing and J duplicate spans reported when a proxy
drops K stored rows and stores J twice, the read-visible sentinel bracket and the backlog summary,
the falsifiability of the insert lag (a forged ``created_at``, a skewed server
clock, inserts before the scheduled send, a failing lag query), a 1.4 s collector
stall at 100 one-span requests/s timed from the actual and the scheduled send, a collector
restart and a held ClickHouse in the middle of a run (``recovery_test.go``), and
the publication rules (``publish_test.go``); not ClickHouse SQL, storage,
throughput or production behaviour. To save the explicitly labelled artifact to a NEW path::

    OTLPBENCH_MOCK_RESULT=/path/to/new/mock-result.json \
      go test ./pkg/ingestbench -run TestMockCollectorPipelineAndIndependentLossDetection -count=1

Eight opt-in real ClickHouse tests run the in-process real collector (PostgreSQL
authentication bypassed ONLY in this fixture) against an ALREADY provisioned
isolated v2 schema. They never start/stop containers or create/delete tables, and
leave their run-tagged rows for inspection. The normal suite skips them::

    OTLPBENCH_LIVE_CH_URL=http://127.0.0.1:18123 \
    OTLPBENCH_LIVE_CH_DATABASE=isolated_benchmark \
    OTLPBENCH_LIVE_CONFIRM=isolated-test-stack \
    OTLPBENCH_LIVE_RESULT=/path/to/new/live-result.json \
    OTLPBENCH_LIVE_BACKPRESSURE_RESULT=/path/to/new/live-backpressure.json \
    OTLPBENCH_LIVE_RECOVERY_RESULT=/path/to/new/live-recovery.json \
    OTLPBENCH_LIVE_TAMPER_RESULT=/path/to/new/live-tamper.json \
      go test ./pkg/ingestbench -count=1 -v \
        -run '^TestLiveClickHouse'


* ``TestLiveClickHouseCollectorPipeline``: 20 spans, multi-chunk verification,
  read-visible sentinels, the insert lag of all 20 stored spans, the clock probe,
  the settings, DDL-hash and storage provenance, and direct ClickHouse reads that
  must find the acknowledged data.
* ``TestLiveClickHouseBackpressureAccounting``: a real collector with a small
  byte budget and delayed inserts, over HTTP protobuf, HTTP JSON+gzip and
  uncompressed gRPC. HTTP busy responses are 503 with Retry-After. gRPC
  UNAVAILABLE remains **ambiguous** in the receipt because that status can also
  originate from a transport failure; it is never proof that nothing was stored.
  Separate requests exceeding the entire work pool must be permanently refused
  (HTTP 413 / gRPC INVALID_ARGUMENT), with no overshoot. Independent exact-ID
  verification distinguishes acknowledged, refused and ambiguous outcomes.
* ``TestLiveClickHouseVerifierCountsDroppedAndDuplicatedSpans``: a proxy in front
  of ClickHouse drops 3 rows of acknowledged spans and stores 3 spans twice (the
  copy differs only in ``service_name``, an ORDER BY column, so no merge can
  collapse it). The exact-ID SQL must report exactly 3 missing (acknowledged but
  missing: data loss) and 3 duplicate rows despite an unchanged total row count,
  and must not certify the run.
* ``TestLiveClickHouseLatencyBucketsMatchGo``: the durable lag's server-side SQL
  bucketing must put every probe value (bucket edges, powers of two, the clamp)
  in the same bucket as the Go histogram.
* ``TestLiveClickHouseRecovery``: 160 spans per case. The real collector is
  restarted gracefully in the middle of the load; separately, every writer request
  to ClickHouse is held for 1.5 s against a small byte budget. Every acknowledged span
  must be found, the backlog must drain and each fault must recover within the
  run (``measured-recovery-complete``).

Optional credentials: ``OTLPBENCH_LIVE_CH_USER`` and ``OTLPBENCH_LIVE_CH_PASSWORD``.
On a shared, overloaded developer ClickHouse set ``OTLPBENCH_LIVE_SLOW_HOST=1``:
it only lengthens writer, settle and query timeouts (and verifies the backpressure
runs in one chunk); every assertion is unchanged. Remote URLs fail closed. A
passing live test is functional integration evidence, NOT a capacity benchmark.

The ``otlp-benchmark-smoke`` job in ``.github/workflows/fi-collector-ci.yml``
provisions one disposable, pinned ClickHouse 25.3.14.14 container on the hosted CI
runner (2 CPUs/2 GiB), applies the actual canonical spans SQL and the existing
curated-table DDL, runs the configured live tests under ``-race``, requires an explicit
PASS of each (including the three backpressure and two recovery sub-tests) rather
than a skip, publishes the smoke result (its headline must be withheld) and
uploads the JSON results and the publication. It deletes only its uniquely named CI Compose project and
never runs the sustained/burst profiles. Adding this job is not evidence that it
has passed; inspect the PR's actual run before claiming that.

Authenticated external-collector smoke
--------------------------------------

Use a provisioned test project, the real collector's API-key authentication, a
separate ClickHouse read credential and the same schema the deployment uses.
Verification and probe requests enforce ``readonly=2`` plus per-query
``max_execution_time`` and ``max_memory_usage`` limits in addition to a client
deadline. The read account must permit these bounded settings; a stricter profile
rejecting them fails the preflight before load (there is no silent unsafe
fallback). The production collector binary requires PostgreSQL auth; merely
running the old standalone Compose example does not supply a valid auth deployment.

Set variables in your shell or secret manager (do not commit values)::

    export BENCH_PROJECT_ID=<dedicated-test-project-uuid>
    export BENCH_PROJECT_NAME=<dedicated-test-project-name>
    export BENCH_RUN_ID=<new-16-lowercase-hex-value>
    export BENCH_OTLP_URL=http://127.0.0.1:4318/v1/traces
    export BENCH_COLLECTOR_HEALTH_URL=http://127.0.0.1:9464/healthz
    export BENCH_CH_URL=http://127.0.0.1:8123
    export BENCH_DATABASE=<dedicated-schema>
    export BENCH_OTLP_HEADERS='{"X-API-Key":"<test-key>","X-Secret-Key":"<test-secret>"}'
    export BENCH_CH_HEADERS='{"X-ClickHouse-User":"<reader>","X-ClickHouse-Key":"<password>"}'
    # Optional: BENCH_ENCODING=protobuf|json|grpc, BENCH_GZIP=1, BENCH_SEED,
    # BENCH_SUT_REVISION, BENCH_VERIFY_CHECKPOINT. Current gRPC has no gzip.

For ``BENCH_ENCODING=grpc`` set ``BENCH_OTLP_URL=http://host:4317`` (plaintext; use
``https://`` for TLS). Plan first; without ``BENCH_COLLECTOR_HEALTH_URL`` this
performs NO network calls and needs no secret values (with it, the only request is
a read-only GET of ``/healthz``)::

    bash benchmark/run-profile.sh smoke

Only after checking the printed endpoint, project, byte budget, collector settings
and duration::

    bash benchmark/run-profile.sh smoke --execute --out /path/to/new/smoke.json
    go run ./cmd/otlpbench report /path/to/new/smoke.json

The smoke target is 100 spans/s for 5s, <=16 MiB uncompressed request bodies in
total. All normal invocations are dry runs unless ``--execute`` is present.
Existing artifacts are never overwritten. There are no automatic retries of
exports and no cleanup/deletion commands.

Non-loopback hosts require BOTH exact ``--allow-hosts host-a,host-b`` and
``--confirm-remote "$BENCH_RUN_ID"``. No wildcard, redirects, environment proxies
(HTTP or gRPC), URL credentials, credential-like URL parameters or implicit
production defaults. All OTLP, ``/healthz``, ClickHouse, node-metrics and
application-probe URLs are checked. HTTP is only appropriate inside an isolated
trusted test network; use verified HTTPS for remote credential-bearing requests.
This is an operator safety gate, not a claim that an allowlisted hostname cannot be
production: the operator must verify it.

Above 1,000 spans/s, 1 minute or 64 MiB also requires
``--confirm-load "$BENCH_RUN_ID"``. Duration must be <= an explicit
``--max-duration`` (absolute maximum 24h). Every export is <=16 MiB uncompressed;
reduce batch size for larger payloads. ``--max-bytes`` caps cumulative
UNCOMPRESSED request body bytes, not TLS/network overhead, verification responses,
disk usage or resource artifacts. In-flight exports have a timeout; the CLI also
bounds settle, probes and verification. Ctrl-C stops scheduling and writes an
incomplete result when cancellation can be handled; SIGKILL cannot guarantee a
final artifact.

An executed run refuses to start unless it knows the revision of the system under
test: from the collector's ``/healthz`` build (``FI_BUILD_REVISION`` or a
VCS-stamped binary) or ``--revision`` / ``BENCH_SUT_REVISION``. This checkout's own
revision is recorded separately as the generator revision and is never used as the
system-under-test revision.

Collector settings each profile needs
-------------------------------------

Current collectors admit OTLP using byte leases for the wire body, decoding,
converted rows and synchronous sidecars. Rows keep their leases through writer
ownership, including out-of-order concurrent drains. ``FI_INGEST_BUDGET_MIB``,
``FI_INGEST_MAX_WAIT``, ``FI_OTLP_MAX_BODY_MIB``, ``FI_GRPC_MAX_RECV_MIB`` and
``FI_INSERT_CONCURRENCY`` are documented in the collector README and environment
registry. ``FI_MAX_PENDING_ROWS`` is obsolete and rejected at startup; do not
restore it to run a historical profile. A request too large for the whole work
pool is permanently refused; a temporarily occupied pool returns HTTP 503 with
Retry-After / gRPC UNAVAILABLE. Exporter retries have their own deadlines and
must be qualified separately. A byte budget is not a hard RSS or durability bound.

Observe the real settings before planning::

    go run ./cmd/otlpbench collector-budget --rate 15000 --batch-size 500 --concurrency 16 \
      --collector-health-url http://127.0.0.1:9464/healthz

For ``limits.admission_model=byte-leases-v1`` the receipt records actual byte
pools, receive/body caps, wait time and insert concurrency. Its budget assessment
is ``not-assessed``: the tool cannot derive enough memory from spans/s, batch size
and row counts. Capture ``ingest`` pressure/refusal samples, representative
payloads, process RSS and actual exporter retry outcomes before sizing an isolated
capacity run. No automatic byte recommendation or capacity pass is fabricated.

Without observed health, the old row heuristic (trigger rows + two seconds of
arrivals + in-flight batches) is explicitly historical and unobserved. A legacy
zero row-cap field does not imply unbounded memory on a newer collector. Legacy
receipts and declared row settings can still be read; they are not current
collector configuration. Plans remain offline unless explicitly executed.

Sustained and burst profiles
----------------------------

Run on dedicated load-generator hosts and provisioned isolated storage, not a
shared developer VM. Scripts do not provision, resize or restart anything::

    bash benchmark/run-profile.sh sustained   # offline plan, 15k/s x 24h
    bash benchmark/run-profile.sh burst       # offline plan, 60k/s x 60s
    BENCH_DECLARED_FAULTS='clickhouse-stop@30m+60m' \
      bash benchmark/run-profile.sh recovery  # offline plan, 15k/s x 3h with a declared fault

The ``recovery`` profile is for an operator-run disruption of an ISOLATED stack:
the script never stops anything; the operator performs each declared disruption
at its offset and the result analyses it like an injected one (``METHODOLOGY.rst``,
"Recovery").

Executing either requires ``--execute --confirm-load "$BENCH_RUN_ID" --out ...``,
the collector settings above, ``BENCH_VERIFY_CHECKPOINT`` (strongly recommended)
and the remote-host approvals where applicable. The sustained script caps
uncompressed body bytes at 7 TB and the burst at 24 GB, in decimal bytes; these are
safety ceilings, NOT predictions of compressed storage or measured throughput. Use
a fresh run ID for each run. The profile label is descriptive; it never silently
changes flags.

The scheduler paces batches with bounded concurrent requests, not an unbounded
work queue. Requests are built (span planning, text assembly, serialisation,
compression) on GOMAXPROCS worker goroutines, in order, at most one second ahead
of their scheduled dispatch. This holds for every wire path: a gRPC message is
encoded (and gzip-compressed) by the build workers like an HTTP body, and the
gRPC exporter sends those bytes as they are, so no transport's timed send or
concurrency slot includes generator serialisation or compression. The result reports the build latency and how far
behind schedule each dispatch was (``workload.dispatch_delay_behind_schedule``); a
slow generator or collector shows as missed scheduled work (``attempted_spans <
planned_spans``) and a growing dispatch delay. CPU-starved generators must not be
used to claim a storage throughput ceiling: measure the generator host with
``BenchmarkBuildRequest`` first. Default-level gzip is far more expensive than
serialisation: per 500-span mixed request, between 19x and 68x in repeated
runs of ``BenchmarkBuildRequest`` on one heavily loaded 10-core development
machine (load average 35-70), where one build worker reached about 500-3,000
spans/s with gzip (JSON or protobuf) against 31,000-100,000 spans/s for protobuf
alone. Those numbers come from a contended shared host and are not a generator
capacity figure; gzip profiles need proportionally more generator cores or several
generator processes.
Each run owns one deterministic namespace; multiple generators need distinct run
IDs and their combined rates must be summed explicitly rather than assuming a
single run met a cluster target.

Single-node and 3-shard x 2-replica integration
-----------------------------------------------

The benchmark accepts any provisioned topology through explicit endpoints. For a
3x2 test, point OTLP at the cluster-configured collector, ``--ch-url`` at one
coordinator and ``--table`` at the logical Distributed spans table. Do not sum two
replicas of each shard as though they held distinct data. Set
``BENCH_TOPOLOGY=3-shards-2-replicas``; the result also records the
``system.clusters`` layout and table engine/keys the coordinator reports. Record
the cluster/schema commit alongside the collector revision.

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
verify consistency externally. A timeout, missing rows or changing counts fails
the verification rather than being called zero loss. Cluster replication/failover
under load remains a separate required test; a single-node pass is not a 3x2 result.

Wire path
---------

``--encoding protobuf`` (default) sends OTLP/HTTP ``application/x-protobuf``,
``json`` sends OTLP/HTTP ``application/json`` and ``grpc`` calls the OTLP/gRPC
``TraceService/Export``. ``--gzip`` adds ``Content-Encoding: gzip`` (HTTP) or the
gRPC gzip compressor for alternate targets. The current fi-collector deliberately
refuses gRPC gzip as UNIMPLEMENTED before reading the message: decompression
cannot participate in its wire lease. HTTP gzip remains budgeted and supported.
The benchmark never registers a process-global gRPC compressor. The
result records the encoding and gzip choice, and both the wire bytes (compressed;
for gRPC from the transport's own message statistics) and the uncompressed bytes.
The gRPC client uses no environment proxy and no service-config retries.

Workload model and identity
---------------------------

The workload is a seeded model, not a measured customer distribution. Every draw
comes from an integer generator seeded by ``(--seed, sequence number)``; no
floating point is used for any draw, so the same seed, sequence and timestamp give
the same bytes on every platform, and a span's content never depends on batch
boundaries.

* Text: each span's ``input.value`` and ``output.value`` are assembled from a seeded
  pool of ``--text-pool-blocks`` distinct word-like blocks (default 1,024 blocks,
  64-1,024 bytes each, skewed word frequencies), so texts differ per span but share
  vocabulary and repeated passages like template-built prompts. Real prompts may
  compress differently; calibrate before sizing.
* Sizes: ``--payload`` picks the class median of input+output bytes: ``small``
  512, ``medium`` 4,096, ``large`` 32,768; ``mixed`` draws small/medium/large with
  probability 70/25/5 per span. Input is 3/4 and output 1/4 of the median, each drawn
  from a lognormal distribution (sigma 0.6, 64 equal-probability quantiles, at most
  4.265x the median).
* Structure: ``--spans-per-trace`` spans (default 8) in a k-ary tree with
  ``--fan-out`` children per span (default 4), capped at ``--tree-depth`` (default
  4; deeper spans attach to the deepest allowed level). The root is an AGENT span,
  inner spans CHAIN, leaves LLM/TOOL/RETRIEVER (60/25/15). ``--traces-per-session``
  consecutive traces (default 4) share a ``session.id`` and user.
* LLM spans carry a model (three synthetic models, 50/35/15) and token counts of
  text bytes / ``--bytes-per-token`` (default 4). Span durations are lognormal per
  kind, at most 30s.
* ``--error-rate`` (default 0.01) of spans get ERROR status; ``--cohort-variant-fraction``
  (default 0.5) of sessions are in the ``variant`` cohort. Changing the error rate
  changes nothing else about a span.
* The result's ``workload`` reports the REALISED distributions of the attempted
  spans: counts by kind, payload class and depth, error and cohort fractions,
  input/output bytes, tokens, durations and request body sizes (log-linear
  histograms whose percentiles are bucket upper bounds, at most 6.25% high).
* Trace ID = 64-bit run namespace + 64-bit trace ordinal; span ID = 64-bit span
  ordinal. Both are nonzero. The converter stores dashed UUID trace IDs and hex
  span IDs. ``benchmark.run_id`` and string ``benchmark.seq`` are stamped on every
  span. Concurrent reuse of a namespace is not coordinated; operators must ensure
  exclusive run-ID ownership.

Accounting and interpretation
-----------------------------

Every attempted request ends in exactly one outcome class, decided from the
response alone (no retries):

* **acknowledged**: HTTP 200 / gRPC OK with a valid OTLP response in the request's
  media type and no rejected spans (a partial-success warning message alone is
  still acknowledged and counted in ``otlp_partial_responses``).
* **partial rejection**: a valid success response with ``rejected_spans`` > 0.
  The rejected identities are not reported by OTLP.
* **whole-request rejected, retryable**: HTTP 429/503, gRPC ``RESOURCE_EXHAUSTED``
  with ``RetryInfo``. ``request_outcomes.whole_request_rejected_with_retry_hint``
  counts those carrying ``Retry-After`` / ``RetryInfo``; ``retry_hints`` summarises
  the requested delays.
* **whole-request rejected, non-retryable**: every other HTTP 4xx (400, 401, 413,
  415, ...), gRPC ``INVALID_ARGUMENT``, ``UNAUTHENTICATED``, ``PERMISSION_DENIED``,
  ``RESOURCE_EXHAUSTED`` without ``RetryInfo`` and similar.
* **ambiguous**: transport errors and timeouts, HTTP 500/502/504 and other 5xx,
  redirects (never followed), other 2xx, malformed or wrongly typed success bodies,
  impossible ``rejected_spans`` values, and gRPC codes a client can also produce
  locally (``UNAVAILABLE``, ``DEADLINE_EXCEEDED``, ``INTERNAL``, ...). Such spans
  may or may not be stored.

Span counters satisfy, and every run checks (``accounting_error`` otherwise)::

    attempted = acknowledged + otlp_partial_rejected + whole_request_rejected + ambiguous
    whole_request_rejected = ..._retryable + ..._non_retryable
    unacknowledged = whole_request_rejected + ambiguous
    requests = sum(request_outcomes)

``generated_spans`` counts spans built by the pipeline, including the final batch
refused by local byte/duration limits but not a build cut short by the end of the
load window. ``success_status_spans`` (HTTP 200 /
gRPC OK) does NOT imply a valid OTLP response. ``accepted_spans`` and
``acknowledged_spans`` intentionally have the same value: what the collector claims
to have accepted, not a durable commit. ``response_statuses`` counts requests by
``http:<code>``, ``grpc:<Code>`` or ``transport-error``. Read-visible sentinels
are kept only for fully acknowledged requests. The collector writer can retry an ambiguous insert
on its own, so duplicates are possible even without exporter retries and must be
measured.

Verification
------------

After the settle window the tool reads ClickHouse independently (read-only, its
own client) and checks EVERY attempted span by BOTH deterministic IDs:

* **Chunks tile the run's trace-ID namespace.** Each chunk is a whole number of
  traces (``--verify-chunk`` spans rounded down to a multiple of spans-per-trace,
  at most 65,536 requests), and carries a ``start_time`` window derived from a
  per-second record of which sequence numbers were generated when (plus 30s
  maximum span duration and 1 minute of slack). The window lets ClickHouse prune by
  the ``toStartOfHour(start_time)`` primary key instead of scanning the whole
  project for every chunk; the trace-ID range alone cannot prune because trace_id
  is not in the primary index.
* **Exact distinct spans per request.** One grouped query per chunk returns, per
  export request, ``uniqExact`` of valid sequence numbers and the row count; rows
  whose IDs do not match their sequence (or lie beyond the attempted range) are
  counted as ``unexpected_rows``. Excess rows are ``duplicate_expected_rows_visible``.
* **Run rows outside the namespace.** One query per UTC clock hour the run
  touches (so each reads a single ``toStartOfHour`` key bucket) counts
  run-tagged rows whose trace ID is outside this run's namespace (included in
  ``unexpected_rows`` and reported as ``run_rows_outside_trace_namespace``). Rows
  tagged with the run but timestamped outside the run window are not scanned.
* **Recheck.** The last chunk and up to 15 chunks that looked incomplete are
  scanned again and must return identical counts; late inserts, merges or replica
  lag fail the verification instead of certifying it.
* **Decomposition by acknowledgement.** The per-request outcome ledger (one byte
  per request) turns the per-request counts into:

  - ``acknowledged_but_missing_spans``: data loss. For partial-success requests
    the missing spans beyond the rejected count are included (and shown separately
    as a lower bound, since OTLP does not say which spans were rejected).
  - ``whole_request_rejected_and_missing_spans`` and
    ``partial_rejected_and_missing_spans``: expected.
  - ``ambiguous_and_missing_spans``: unknown outcome.
  - ``whole_request_rejected_but_persisted_spans`` and
    ``partial_requests_persisted_beyond_acknowledged_spans``: the collector stored
    something it refused, a rejection-contract violation that fails the run.

  ``data_loss_detected`` and ``rejection_contract_violation`` are set only by a
  finished, rechecked verification. An unfinished one reports
  ``scanned_attempted_spans`` and ``unscanned_attempted_spans`` (counted as missing)
  and decomposes the scanned part only; it concludes nothing.
* **Budgets.** ``--verify-timeout`` bounds the exact-ID verification (default: 10
  minutes plus one second per 50,000 attempted spans, at most 48h; the sustained
  script uses 12h). The durable lag's extra query per chunk has its own
  ``--durable-lag-timeout`` (default: the size of the exact-ID budget; the
  sustained script sets 12h) and never spends the exact-ID budget: the time its
  queries take extends the exact-ID deadline, and when its own budget runs out the
  durable lag stops with status ``errors`` while the exact-ID verification
  carries on. ``durable_lag_timeout_seconds`` and ``durable_lag_elapsed_seconds``
  record both;
  ``--verify-query-timeout`` (default: the larger of 30s and one second per 20,000
  chunk spans, at most 30 minutes; sent to ClickHouse as ``max_execution_time`` and
  enforced by a client deadline 10s later), ``--verify-max-memory`` (default 1 GiB
  per query) and ``--verify-query-retries`` (default 2, exponential backoff).
* **Resume.** ``--verify-checkpoint new-path`` saves the plan, the outcome ledger and
  the progress (atomic rename, mode 0600, no credentials) at most every 10 seconds
  and at least twenty times the last save's own duration apart, so a large ledger
  cannot dominate the budget; failures and completion always save, so only a
  killed process loses the progress since the last save. If the budget runs out or
  a query keeps failing, continue in a new process::

      go run ./cmd/otlpbench verify --checkpoint /path/to/checkpoint.json \
        --out /path/to/new/verification.json --ch-headers-env BENCH_CH_HEADERS \
        [--verify-timeout 6h] [--verify-query-timeout 10m]

  The resumed answer equals an uninterrupted one (tested); a completed checkpoint
  answers without querying again.
* **Reuse preflight.** Before load, the tool searches the entire project/run tag,
  without restricting trace IDs or timestamps, and refuses reuse if any row is
  found. It stops at the first match: ``preexisting_run_rows`` is a lower bound
  (0 or 1), not a total count. Proving absence can scan the whole project and is
  not claimed cheap at scale. The query uses the control-query timeout (at least
  30s, raised by ``--verify-query-timeout``); timeout, resource-limit or invalid
  responses refuse load, with no sampled fallback. This is not an atomic
  reservation: use a fresh run ID, quiesce earlier writers and choose a consistent
  read target; concurrent reuse and old inserts not yet visible are not excluded.

``measured-profile-complete`` means the requested span count was acknowledged and
independently verified with no missing, duplicate or unexpected rows. It does NOT
mean full-scale targets were tested or all four app probes configured. A dry run,
mock fixture or small real smoke must stay labelled as such.

Read-visible lag uses sentinels: the first span of every k-th fully acknowledged
request (at most ``--max-sentinels``, default 40,960). Polling ticks at
``--poll-interval`` (default 100 ms) with a ``start_time`` window; at most 4,096
pending IDs per tick rotate fairly, so lost older IDs cannot starve later ones.
A full pass over 40,960 pending IDs takes ten ticks plus query time, not 100 ms.
Each lag is
bracketed between the start of the last poll that missed the sentinel and the end
of the first that saw it, timed from the scheduled send; the upper bound includes
poll/query delay and neither is a commit timestamp. The insert lag
(``verification.durable_lag``) is ClickHouse's ``created_at`` minus the scheduled
send for every stored span, a lower bound on commit latency; ``--durable-lag=false``
skips its one query per chunk. Unseen sentinels and query failures remain
visible. Latency histograms have bounded memory (about 31 KiB each): log-linear
over whole microseconds, exact below 0.256 ms and then 128 sub-buckets per power
of two, so every bucket from 0.256 ms to 38 h is at most 0.79% wide relative to
its lower edge (``latency_histogram`` in the result). A measured or upper-bound
quantile is its bucket's upper edge capped at the exact maximum (never below the
true value); a lower-bound series (the read-visible lower bound, the insert lag,
censored probe elapsed times) reads lower edges (never above the true value).
Exact definitions: ``METHODOLOGY.rst``.

Direct ClickHouse reads
-----------------------

Separately from application API probes, the tool times three fixed read-only
queries against the logical spans table, shaped like the product's reads and
recorded verbatim in the result (``direct_clickhouse_reads``):

* ``trace-by-id``: all spans of one trace of an acknowledged request, reading
  their ``input``/``output`` payload columns as a trace view does.
* ``session-by-id``: all spans of that request's session (``trace_session_id``
  computed exactly as the converter does), also reading the payloads.
* ``attribute-filter``: ERROR spans of the variant cohort over the last 15 minutes
  of the run.

They run every ``--direct-probe-interval`` during load (trace and session probes
pick the newest retained request acknowledged at least ``--direct-probe-min-age`` ago) and
``--direct-probe-after-settle`` rounds after the settle window (requests spread
across the run), each with ``--direct-probe-timeout``, ``readonly=2`` and the
query cache explicitly off. During-load and after-settle phases are reported
separately with success/error counts, latency percentiles, rows returned, how
many results were non-empty (``skipped-no-candidate`` when no acknowledged request
was old enough in any round; a query cut off by the end of its phase is counted in
``window_censored_queries``, never as a success or an error, and the probe is
``measured-with-censoring``, or ``window-censored`` when no query completed; how
long each cut-off query had already run is kept in
``censored_query_elapsed_lower_bound``, a right-censored lower bound on its
latency: excluding queries that did not finish can bias the successful-query
percentiles low, so those percentiles do not describe all attempted queries) and,
for the trace and session probes, the payload text bytes each query read
(``payload_text_bytes_read``). Results are aggregated
server-side, so the latency covers reading the payload columns but not
transferring them to a client. A probe with no acknowledged candidate is counted as
skipped, never as a success. These numbers are database latency for these queries,
not application API or browser latency, and they add read load during the run.

During-load candidates use 64 monotonic-time buckets, keeping the earliest
acknowledgement in each bucket, plus one matured fallback across idle/resume.
The bucket width is ``ceil(min-age / 63)`` (at least 1 ns). Retention is therefore
bounded at 65 samples regardless of request rate or run length, and an eligible
sample is not overwritten by a high request rate before reaching its minimum
age. Selection is the newest eligible **retained sample**, not the exact newest
acknowledgement. After-settle sampling is unchanged. In-process regression tests
exercise both ID-query dispatch paths at the profile request rates; they are not
real database latency measurements or evidence that full-scale profiles ran.

Application read workload
-------------------------

The four categories are ``trace-list``, ``attribute-filter``, ``session-view`` and
``dashboard``. Supply a JSON array with actual current read-only API requests,
usually captured from a test account in the application. Use the generated project,
session IDs and time window; ensure queries return relevant seeded data, not an
empty project or a static health response. A probe is HTTP-level API latency, not
browser rendering/end-user latency.

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

A request interrupted by normal load-window completion is counted in
``window_censored_requests`` (``measured-with-censoring``), not as a server error
or a successful latency sample; its elapsed time when cut off is kept in
``censored_response_elapsed_lower_bound`` (a lower bound on its latency). An HTTP failure or a client timeout while the load
window remains open is still an error. The harness does not seed dashboards or
authenticate a browser; those are dependencies, not fabricated measurements.

Artifacts, provenance and resource measurements
-----------------------------------------------

``--out run.json`` creates a new mode-0600 JSON result with schema
``fi.otlpbench.v4`` (older results are refused by ``report`` and ``publish``
because measurement meanings changed: v4 changed the latency histogram, the
per-window lag summaries and the in-flight telemetry) and a separate ``run.json.telemetry.jsonl``
stream. ``provenance`` records:

* ``command_line``: the exact invocation with credential-like flag values,
  ``KEY=value`` pairs and URL userinfo/credential parameters replaced by
  ``<redacted>`` (flags that only NAME an environment variable are kept).
* ``generator_build``: Go version, module, VCS revision/time/modified of the
  benchmark binary, plus ``--generator-revision``.
* ``generator_host``: OS, kernel, architecture, CPU model, logical CPUs,
  GOMAXPROCS and memory of the LOAD GENERATOR host (not the collector).
* ``clickhouse``: version, timezone, uptime, the spans table's engine, sorting,
  primary and partition keys, ``system.clusters`` shard/replica counts, the
  declared topology, and ``server_hardware``: the memory (``OSMemoryTotal``),
  cgroup CPU and memory limits and ``max_threads`` that the server answering the
  verification connection reports about itself (one node of a cluster; a metric
  the server does not report is left out, never recorded as zero).
* ``collector``: the ``/healthz`` status, build, ``runtime`` (logical CPUs,
  ``GOMAXPROCS`` and any Go memory limit of the collector process) and admission
  limits (or the declared budget), and the budget check.
* ``system_under_test``: the revision, its source, and whether a declared
  ``--revision`` agrees with what the collector reports (a conflict is a warning).
* ``clickhouse`` also records the verification user's insert-related session
  settings, the server's MergeTree fsync and deduplication defaults, the SHA-256
  of the table DDL and the table's active-part storage before the load and after
  verification (table-wide, including other data); ``collector.writer`` is the
  writer's insert mode and retry policy from ``/healthz``.

``otlpbench publish --markdown new.md --json new.json --telemetry
run.json.telemetry.jsonl run.json`` renders the shareable form: stages, outcomes,
verification with missing ranges, the latency table, backlog, recovery, the 24
disclosure fields (captured, partial or not captured), a "Not measured" section,
limitations and warnings. Its headline is a pass at the offered rate, not a
capacity figure: it states that the offered spans/s was sustained (verified), and
the measured acknowledged spans/s over the actual load window (load start to the
completion of the last export). It is withheld, with every reason, unless the run
is executed, at least ``--min-sustained`` long (default 1 hour), completely
verified with nothing missing or unexpected and no stored span of a refused
request, started and acknowledged all offered load, saw every sentinel and had
no fault. A telemetry stream of another run is refused. Each progress line's
accounting identity is checked against in-flight spans and requests that the
generator counts on their own (added when a request starts, removed when it
finishes), not derived from the outcome classes, so a finished request missing
from its class breaks the identity (and nothing may be in flight on the final
line).

First/last resource snapshots are in the main result; periodic resources (10s
cadence, serial bounded queries) and 1s cumulative-counter samples (with in-flight
spans and requests) go to the stream. Every stream line has a schema version, run ID and kind.
Do not commit unreviewed artifacts: URLs, queries, project names and
machine/container labels can identify a private deployment even though credential
headers are excluded. Per-node optional queries collect ClickHouse
``system.asynchronous_metrics``, ``system.metrics``, ``system.disks`` and active
``system.parts`` by table; Docker stats record named container CPU%, memory,
network and cumulative block I/O with Docker's displayed units.

Sizing worksheet: inputs, not invented nodes
--------------------------------------------

Copy ``sizing-input.template.json`` outside the repository and replace every null
with a measurement. The builder rejects missing, nonpositive, infinite or
inconsistent inputs. A pre-filled node-count recommendation is intentionally
absent until a capacity test supplies it::

    go run ./cmd/otlpbench size /path/to/measured-sizing-inputs.json

The output is labelled ``extrapolation_not_measured_capacity``. Units/formulas:

* Retained physical bytes = spans/day x retention days x measured physical
  bytes/(logical span x replica) x replicas. Include indexes, rollups and actual
  observed compression. Measure before/after active part bytes on an otherwise
  quiet dedicated workload. Do not substitute OTLP body bytes.
* Storage nodes = ceil(retained bytes / (disk bytes/node x usable fraction)).
* Throughput shards = ceil(peak spans/s / measured sustainable spans/(s x shard)).
* Minimum nodes = max(storage nodes, throughput shards x replicas), rounded up
  to a full replica group. This is only a planning lower bound, not an SLA.

Record CPUs, RAM, disk medium/capacity/IOPS, network bandwidth/RTT, cloud/instance
class, collector replicas/resources, CH version/settings, shard/replica count,
replica write/read consistency, compression, TTLs, rollups, batching, writer retry
policy, probe rate, producer utilization and noisy-neighbor isolation for every
published row. Test failover/headroom separately; extrapolation assumes per-shard
linearity and does not establish it.

Reference methods and measurement scope
---------------------------------------

The following infrastructure tools document useful load-generation and
correctness methods. They are references, not measurements made by this harness
or a performance ranking. Keep units, workloads, acknowledgement points and
verification coverage explicit when interpreting any result.

Load generators and correctness tools:

* **OpenTelemetry Collector testbed** (collector-contrib): a ``LoadGenerator`` sends
  generated data to the collector under test and a ``MockBackend`` receives it;
  performance tests derive trace IDs from batch/item counters, correctness tests
  replay a pairwise-combinatorial "golden" dataset. It validates the collector
  against an in-memory receiver, not a database's stored rows.
  https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/main/testbed/README.md
* **telemetrygen** (collector-contrib): synthetic traces/metrics/logs over OTLP gRPC
  or HTTP with ``--rate``, ``--duration`` and ``--workers``. It does not read data
  back; load-test write-ups infer loss from the collector's own refused/failed
  counters, which the OpenTelemetry docs say do not by themselves imply data loss.
  https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/cmd/telemetrygen
  https://opentelemetry.io/docs/collector/internal-telemetry/
* **Grafana Tempo vulture**: writes test traces over
  OTLP gRPC and reads them back by ID, by TraceQL search and by TraceQL metrics,
  counting ``missingspans``, ``notfound_byid``, ``incorrectresult`` and similar; its
  validation mode runs a fixed number of write/read cycles (default 3). It checks a
  stream of sampled traces for integrity; it is not a rate-controlled throughput
  benchmark and does not account for every span of a load test.
  https://grafana.com/docs/tempo/latest/operations/tempo-vulture/

* OTLP response and retry semantics: https://opentelemetry.io/docs/specs/otlp/

The following harness features are covered by tests in this repository but have
NOT yet been exercised at full scale. They describe this implementation's
measurement scope, not capabilities established by a completed capacity run.

1. Every attempted span verified by both IDs in the real database, with exact
   distinct counts and visible duplicates, instead of item counters at a mock
   receiver or a sampled read-back.
2. Missing spans attributed to the collector's own answer per request, so data loss
   (acknowledged but missing) is separated from load shedding (rejected) and from
   unknown outcomes, and stored-but-refused spans are caught.
3. Retryable and non-retryable refusals classified by the OTLP retry rules and
   counted with their retry hints; retryable refusals exercised end to end against
   a real collector driven into backpressure (CI case above).
4. Direct database read latency per probe type (trace, session, attribute filter),
   during the sustained load and after settle, from the same run whose ingestion
   is verified, reported separately from application API latency and with cut-off
   queries kept as lower bounds.
5. A machine-readable record of the command, generator, hardware, database
   topology, collector limits and system-under-test revision for every result.
6. The same seeded workload over OTLP/HTTP protobuf, JSON and gRPC, with and
   without gzip.

Reproducible evidence needs recorded hardware, units, failure counts and workload
definitions. An unrun target is not a measured result, and an acknowledgement is
not durable storage. Capacity claims require the sustained and burst runs above
on recorded hardware, with complete verification and their limitations disclosed.
