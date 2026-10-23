OTLP ingestion benchmark methodology
====================================

This document states exactly what each number reported by ``otlpbench`` means,
how it is measured, and which comparisons it does not support. It describes
the tool, result schema ``fi.otlpbench.v3`` and publication schema
``fi.otlpbench.publication.v1``. It is not a capacity claim: the TH-8687
acceptance profiles (15,000 spans/s for 24 hours, 60,000 spans/s for 60 seconds)
have **not** been run. The evidence section at the end lists only small runs,
each labelled with where it ran.

Units: every count is **spans** unless it says requests. A trace is several
spans (8 by default). Latencies are milliseconds; offsets are seconds from the
start of the load.

The five stages
---------------

A span passes five stages. Each is counted separately, as a total in the result
and as a 1-second series in the telemetry stream (``run.json.telemetry.jsonl``).

**Offered** (``planned_spans``)
    ``rate x duration``: what the profile calls for. A plan, not a measurement.

**Scheduled** (``schedule.scheduled_spans``)
    The load is open loop: request *k* is due at ``load start + k x batch_size /
    rate`` whether or not earlier requests have finished. Scheduled counts spans
    whose due time passed while the generator ran.
    ``scheduled_but_not_started_spans`` counts those never sent: k6 calls them
    dropped iterations. A closed-loop client would silently omit them.

**Attempted** (``counters.attempted_spans``)
    Spans in export requests the generator started. Every attempted request ends
    in exactly one outcome class, decided from the response alone. The generator
    never retries. The identity

    ::

        attempted = acknowledged + otlp_partial_rejected + whole_request_rejected + ambiguous

    is checked on every result. Every telemetry progress line keeps the same
    identity with ``in_flight_spans`` added (``otlpbench publish --telemetry``
    checks each line).

**Accepted** (``counters.acknowledged_spans``)
    The collector answered OTLP success, minus partial rejections. **This is not
    durability.** The fi-collector acknowledges an export when the rows enter its
    buffer, before the ClickHouse insert. A crash can lose every buffered row,
    which the admission budget (``max_pending_rows``, 20,000 by default) bounds.
    The collector reports its insert mode on ``/healthz`` (``writer``). With
    ``async_insert`` the writer does not wait for the flush, so an acknowledged
    insert is not even a stored one.

**Durable** (three figures, each with a different meaning)
    * ``verification.persisted_distinct_attempted_spans``: after the settle window,
      an independent read connection checks the exact trace and span ID of every
      attempted span, chunk by chunk. This is what the database holds. It is not
      a guarantee that the data survives a disk or node failure.
    * ``verification.durable_lag``: for every distinct stored span, ClickHouse's own
      ``created_at`` (``DEFAULT now64(6)``, which the collector does not set) minus
      the scheduled send of the span's request, after the clock correction below.
      ``created_at`` is evaluated when the server processes the INSERT, *before*
      the part is committed. The figure is therefore a **lower bound on
      scheduled-send-to-commit latency**. A span stored twice counts once, with
      its first insert. Spans whose ``created_at`` precedes their scheduled send
      are counted in ``spans_inserted_before_scheduled_send``. Such spans point to
      clock error beyond the measured uncertainty, or to a forged value.
    * The collector's own count of inserted rows (``rows_inserted`` from
      ``/healthz``, sampled into the backlog series), when
      ``--collector-health-url`` is given. This is the collector's view only.

**Read-visible** (``lag``)
    Measured on **sentinels**. A sentinel is the first span of every *k*-th
    request of the load itself, so it adds no traffic. *k* is chosen so a run has
    at most ``--max-sentinels`` sentinels (40,960 by default). Only sentinels of
    fully acknowledged requests are samples. A refused, ambiguous or partially
    rejected request is not expected to be visible, and its sentinel is counted in
    ``dropped_not_acknowledged_sentinels``. While the load runs, and through the
    settle window, every unseen sentinel is polled every ``--poll-interval``
    (100 ms by default). The poll uses a ``start_time`` window so ClickHouse
    prunes by its primary key. Each sentinel's visibility instant is bracketed:

    ::

        start of the last poll that missed it  <  visible  <=  end of the first poll that saw it

    Both bounds are timed from the **scheduled** send:
    ``read_visible_lag_upper_bound_from_scheduled_send`` and
    ``read_visible_lag_lower_bound_from_scheduled_send``. The true lag lies
    between them. The upper bound includes poll and query delay.
    ``visibility_lag_upper_bound`` is the same upper bound timed from the actual
    send (the v2 figure). Sentinels never seen by the end of the settle window
    are ``unseen_sampled_spans``, never a lag value. Per-window summaries are
    reported by scheduled send: one window per 24th of the run, between 10 s and
    1 h wide, so a 24-hour run reports each hour.

Outcome classes
---------------

* **acknowledged**: HTTP 200 or gRPC OK, with a valid OTLP response and no
  rejected spans.
* **partial rejection**: a success response with ``rejected_spans`` > 0. OTLP
  does not say which spans were rejected, so loss inside such a request is a
  lower bound.
* **whole-request rejected, retryable**: HTTP 429 or 503, or gRPC
  ``RESOURCE_EXHAUSTED`` with ``RetryInfo``. The retry hint is recorded.
* **whole-request rejected, non-retryable**: any other HTTP 4xx, gRPC
  ``INVALID_ARGUMENT``, ``RESOURCE_EXHAUSTED`` without ``RetryInfo``, and similar.
* **ambiguous**: transport errors, timeouts, 5xx responses other than 503,
  malformed success bodies, and gRPC codes a client can produce locally. Spans
  in an ambiguous request may or may not be stored.

Missing spans are decomposed by class (``missing_by_acknowledgement``). Only
**acknowledged-but-missing** is data loss. They are also listed as request
ranges (``missing_ranges``): maximal runs of consecutive requests of one class,
each missing at least one span, with request indexes and scheduled offsets.
Stored spans of refused requests are a rejection-contract violation.

Latency figures
---------------

* ``otlp_request_latency``: from the actual send to the response. A stalled
  collector delays every later send, and this figure omits that delay, because
  the delayed requests are timed from their late start (coordinated omission).
* ``otlp_request_latency_from_scheduled_send``: the same requests timed from
  their scheduled send. It includes the stall.
* ``schedule.start_delay_after_scheduled_send`` and ``late_start_requests``:
  how late requests started, and how many started more than 10 ms (and more
  than 1 s) late.

Every latency is a histogram with 1 ms buckets to 10 s and 1 s buckets to 24 h.
A quantile is reported as its bucket's upper edge, capped at the exact
maximum, so it is never below the true value and never above the max. Raw
buckets are kept in the result for the read-visible upper bound and the durable
lag.

Clock offset
------------

The durable lag compares the server's clock (``created_at``) with the
generator's schedule. Before the load and after the settle window, five probes
read ``now64(6)`` from the server over the verification connection. For the
probe with the shortest round trip, the offset is the server time minus the
midpoint of the request, and the uncertainty is half that round trip. The
uncertainty includes HTTP and query overhead, so it is conservative. The
before-load offset is applied to the durable lag. When the offset or its
uncertainty exceeds 10 ms the result is flagged
``offset_or_uncertainty_exceeds_10ms``, and durable lags below that are not
resolved. Read-visible lag uses only the generator's clock.

Collector backlog
-----------------

With ``--collector-health-url``, the collector's ``/healthz`` is sampled every
``--collector-poll-interval`` (1 s by default) during the load and the settle
window. The sample records ``admission.buffered_rows`` (rows parked for the
next flush plus rows of the flush in progress, including its retries: what the
budget bounds), the budget, and the writer's insert, retry and dead-letter
counters. The summary separates **backlog growth** from **throughput**:

* the least-squares slope of buffered rows over the load, and over its second
  half, where a ramp-up no longer explains growth;
* samples at the budget, where one more request would not have fitted;
* whether, and how long after the load, buffered rows reached zero;
* collector restarts (counter resets), unreachable windows, and insert stalls
  (rows buffered with no completed insert for at least twice the flush age,
  and at least 5 s).

A run whose backlog keeps growing has not reached a steady state, whatever its
acknowledged rate.

Recovery
--------

A fault is a disruption of the system under test during the load. **The tool
never stops, pauses or restarts infrastructure.** There are two ways a fault
enters a run:

* **Injected**: a test harness that owns the system injects it (``Options.Faults``).
  The repository's tests restart the in-process collector (a graceful stop, then a
  new collector on the same ports). Separately, they hold every request from the
  collector's writer to ClickHouse, as a paused server holds an accepted
  connection.
* **Declared**: an operator performs it on an isolated stack and passes
  ``--declare-fault name@offset+duration``. It is recorded as declared, not
  observed.

For each fault the result reports:

* the outcomes of the requests scheduled inside it, including requests never
  sent;
* the time from the fault's end to the first acknowledgement (100 ms
  resolution, an upper bound);
* the pre-fault 95th percentile of buffered rows (the baseline), the peak, and
  the time until the backlog was back at that baseline;
* the time until every acknowledged sentinel scheduled before the fault ended
  was visible, and how many never were;
* the acknowledged-but-missing spans the exact-ID verification places in its
  window.

The **recovery time** is the largest of the acknowledgement, backlog and
sentinel figures. A fault counts as recovered only when all three were observed
within the run.

A recovery run passes (``measured-recovery-complete``) only when all of these
hold:

* the verification completed;
* no acknowledged span is missing;
* no refused span is stored, and no unexpected row exists;
* every fault was injected and restored without error, and recovered;
* the planned load was fully started.

Visible duplicates and ambiguous-but-persisted spans are reported, not hidden.

``acknowledged_loss_bound_rows`` is the number of faults times
``max_pending_rows``, with its source. It is the most a crash can lose by the
collector's design. It is a statement to check a crash case against, not a
measurement. Rows the writer dead-lettered are reported from ``/healthz``: they
are acknowledged and missing from ClickHouse until an operator replays them.

Publication
-----------

``otlpbench publish --markdown new.md --json new.json [--telemetry run.jsonl]
[--min-sustained 1h] result.json`` renders the Markdown and the JSON from one
structure. A publication contains:

* the stages and outcomes;
* the verification, with its missing ranges;
* the latency table, the backlog and the recovery results;
* all 24 disclosure fields, each marked captured, partial or not captured;
* an explicit **Not measured** list, with the result's limitations and warnings.

The headline, **verified sustained throughput**, is the acknowledged spans the
exact-ID verification found stored, divided by the scheduled load duration. It
is printed only when all of these hold; otherwise every reason is listed and no
throughput figure appears:

* the run was executed;
* it lasted at least ``--min-sustained`` (1 hour by default; the acceptance
  profile is 24 hours);
* its verification completed with no missing and no unexpected span;
* it started and acknowledged the whole offered load;
* every sentinel became visible;
* it had no fault;
* no telemetry line breaks the accounting identity.

Comparisons this tool does not support
--------------------------------------

* **Acknowledged throughput is not durable throughput.** The collector
  acknowledges before it inserts. Compare durable or verified figures only with
  other durable or verified figures.
* **Do not compare a latency timed from the actual send with one timed from
  the scheduled send**, or a lower bound with an upper bound. State which of
  the bounds you quote.
* **Do not rank spans/s against vendor figures.** Vendors publish events,
  requests, traces, observations or documents per second, with payloads from
  about 500 B to 100 KB and different acknowledgement points.
* **Do not compare a self-hosted measurement with a vendor's cloud plan
  limits or quotas.** Those are pricing limits, not measured capacity.
* **Do not compare read-visible lag with vendor latencies whose definitions
  differ.** Each of these is documented by its vendor and not measured by us:

  * LangSmith reports "trace ingestion" P50 630 ms and P99 1.47 s, with the
    stage undefined (https://www.langchain.com/blog/introducing-smithdb).
  * Braintrust reports separate flush-to-server and visible-for-reads times for
    one shot of 100 documents of 100 KB (https://www.braintrust.dev/blog/brainstore).
  * Arize reports a P50-only ingest-to-read figure that includes UI load
    (https://arize.com/docs/ax/concepts/adb/performance).

  None of these is a per-span read-visible lag under sustained load from the
  scheduled send.
* **Do not compare runs across ClickHouse default changes.** CI pins
  25.3.14.14; insert deduplication and async-insert defaults differ in 26.2 and
  later.
* **Do not compare durable lags below the clock limit** (10 ms, or the
  reported uncertainty) across runs, or treat a clock-limited run's small lags
  as resolved.
* **A short, mock or CI-sized run is not capacity evidence**, and a rate
  extrapolated from one is not a soak test.
* **A single-node run says nothing about** replication consistency, failover or
  multi-shard scaling.
* **Visible duplicates are a lower bound.** ReplacingMergeTree merges can
  remove duplicates before verification. Nothing here supports an exactly-once
  or effectively-once claim, or a "zero data loss" claim beyond the spans a
  complete verification checked.
* **The generator never retries.** These runs do not measure a real
  OpenTelemetry SDK or Collector exporter's retry behaviour.
* **Do not quote storage, compression or cost per span from these runs.** The
  payloads are synthetic.

Reference methods
-----------------

The methods below come from infrastructure tooling and standards. Each is
documented by its maintainers and not measured by us. Our runs are not ranked
against them.

* OpenTelemetry Collector testbed: a sent == received count gate against an
  in-memory mock backend on every merge
  (https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector-contrib/main/testbed/tests/scenarios.go).
* Grafana Tempo Vulture and Loki Canary: sampled write-then-read probes with
  typed missing, duplicate and unexpected counters
  (https://grafana.com/docs/tempo/latest/operations/tempo-vulture).
* wrk2 and HdrHistogram: latency corrected from the intended start. The wrk2
  README describes a 1.4 s pause that made the corrected p99 about 200x the
  uncorrected one (https://github.com/giltene/wrk2).
* ClickHouse LogHouse: a 1-hour hard stop of ClickHouse at 120,000 events/s.
  The post states the system could not tell which specific data was still
  outstanding
  (https://clickhouse.com/blog/reliable-opentelemetry-ingestion-at-scale).
* TPCx-IoT: a full disclosure report detailed enough to replicate, reporting
  the lower of two measured runs
  (https://www.tpc.org/TPC_Documents_Current_Versions/pdf/TPCX-IOT_v2.1.2.pdf).
* Langfuse documents a manual four-step runbook for missing events
  (https://langfuse.com/faq/all/self-hosting-missing-events-after-ingestion).

Evidence so far (small runs, 2026-10-22)
----------------------------------------

These are functional measurements on a development machine (Apple M2 Pro), where
the load average was 35 to 93 from unrelated jobs. They are not capacity
figures, and are not comparable with the figures above.

Real ClickHouse 25.3.14.14 (a shared development container with a 2-CPU
cgroup), real in-process collector with PostgreSQL authentication bypassed:

* ``TestLiveClickHouseCollectorPipeline``, 20 spans: 20 of 20 verified by ID.
  The insert lag (``durable_lag``) covered 20 of 20 stored spans, at p50/p99
  49/601 ms in one run and 1304/1704 ms in another. The read-visible upper
  bound from the scheduled send was p50/p99 401/876 ms (lower bound p50 7 ms),
  and 1937/2337 ms in the other run. The clock offset was -30.8 +/- 4.2 ms and
  -18.3 +/- 29.7 ms, so both runs are flagged clock-limited.
* ``TestLiveClickHouseRecovery``, 160 spans per case:

  * A graceful collector restart lasting 0.8 s left 8 requests ambiguous. No
    acknowledged span was missing, there were no duplicates, and recovery took
    0.185 s.
  * Holding every writer request for 1.5 s against a 24-row budget filled the
    budget (24 rows buffered), and 10 requests were refused with a retry hint.
    No acknowledged span was missing. Recovery took 1.909 s: the backlog was
    back at its baseline 1.909 s after the fault, and the earlier sentinels
    were all visible after 1.736 s.

Mock ClickHouse, real in-process collector (``recovery_test.go``,
``publish_test.go``):

* **Coordinated omission.** A 1.4 s collector stall at 1,000 spans/s: request
  p99 was 35 ms timed from the actual send and 1,377 ms timed from the
  scheduled send, a measured ratio of about 39. Under the race detector it was
  137 ms against 1,453 ms. The test asserts that the actual-send p99 stays below
  1,000 ms and the scheduled-send p99 reaches at least 1,300 ms. When the
  corrected figure is computed from the actual send instead, the test fails,
  with both p99 values at 34 ms.
* **Durable lag is falsifiable.** A ``created_at`` forged 3 s late moves its
  p50 above 3,000 ms. A server clock 2 s ahead is measured and corrected. A
  ``created_at`` before the scheduled send is counted.
* **Recovery detects loss.** With the collector's shutdown drain removed, the
  restart case reports 4 acknowledged-but-missing spans.

None of these runs exercises 1,296,000,000 or 3,600,000 spans, a real
OpenTelemetry exporter, a cluster, or a storage outage longer than 1.5 s.
