# Phase 1 video load test

Run date: 2026-10-03 (America/Los_Angeles). Batch 4 on top of `b784df355`.
This is a local fake-provider measurement, not a live-provider smoke test or a
production capacity promise.

## Reproduce

From `agentcc-gateway/`, with the local `th8088-redis` container available:

```sh
export GOCACHE="$TMPDIR/th8088-gocache" TEST_REDIS_ADDR=127.0.0.1:36381
go run ./cmd/loadtest -scenario video -video-jobs 1000 -c 16 -video-timeout 10m
```

No credentials, external gateway, or provider accounts are used. The scenario
requires a literal loopback Redis address and generates a unique Redis prefix.
It creates synthetic artifacts in a temporary directory under the working
directory, then removes those artifacts and only its own Redis keys. It does
not flush Redis or reset server statistics. Run from the module directory.

## Workload and measurement boundaries

- 1,000 unique five-second text-to-video requests, 16 concurrent clients, and
  one same-key/same-body replay for every request. Every replay must return the
  original job. Two independently owned workers in one process compete for the
  same Redis queues, with their regular 250 ms tick cadence and lease fencing.
- Real lifecycle acceptance, capability validation, tariff estimation, durable
  Redis state, org and key budget reservation/settlement, cost post-plugin,
  media verification, disk copy, checksums, fsync, and artifact indexing. The
  existing fake provider handles submit/poll/fetch over loopback HTTP; the load
  adapter gives each fake submit a unique upstream ID. Fake completions report
  1,000 video tokens and a tiny, container-only MP4 fixture.
- Real shared-ledger metrics refresh is enabled on every worker tick. Total
  concurrency is 32, per-provider concurrency 32, and copy concurrency 8.
  Fake poll minimum/base interval is one second; fake rates and active limits
  are raised above this workload. The submit deadline is the scenario deadline.
  These are harness settings, not recommendations for a live BytePlus account.
- Submit latency measures `Service.Accept`, including persistence and budget
  reservation, excluding worker execution. Status latency measures org-scoped
  `Service.Get`. Neither includes HTTP auth, gateway routing, request/response
  serialization, or the HTTP handler's default five-second synchronous wait.
  Status reads run with 16 clients in rounds separated by 250 ms until every
  job completes and settles. Percentiles use the existing load tester's sorted
  linear interpolation, retaining sub-millisecond precision. Replay latency is
  reported separately.
- Redis operations mean **commands issued by this scenario's Go Redis client**.
  A pipeline counts each member, and script cache misses count their attempted
  commands. The interval spans acceptance through worker shutdown, including
  replays, client status reads, claims, leases, budget operations, artifact
  indexing, GC, and shared metric scans. Lua-internal `redis.call` operations,
  Redis initialization, post-run assertions, storage inspection, and cleanup
  are excluded. This is not a count of network round trips or Redis CPU work.
- After the small-job run, a separate eight-job phase replaces fake Fetch with
  synthetic streams of exactly 200,000,000 bytes each (decimal MB). A barrier
  requires all eight streams to open concurrently. The MP4 header and an
  open-ended `mdat` atom precede generated zero bytes; there is no encoded
  video or 200 MB source allocation. Reads are capped at 32 KiB and paced by
  one millisecond to provide a sustained sampling window. All streams pass
  through the actual lifecycle verifier and disk store. This phase does not
  exercise upstream HTTP transfer, S3, or content download to an end user.
- Memory is sampled with `runtime.ReadMemStats` every 20 ms, after a baseline GC,
  across acceptance, copies and settlement. Reports include heap allocation,
  heap in use, Go runtime memory, post-GC heap, and mean heap by copy-progress
  quarter. No forced GC runs during sampling. The pass gate requires all eight
  full artifacts, observed concurrency eight, zero duplicate submissions, and
  peak incremental Go heap below a fixed 64 MiB envelope while 1.6 GB passes
  through. OS page cache, Redis process memory and process RSS are not measured
  by these Go memory counters.

## Capacity interpretation

Each job must be `completed`, have one available artifact of the expected
length, and be `settled`. Small-job org and key budget totals must equal the
sum of actual settled costs. A missing or repeated upstream submit fails the
run; idempotent API replays are not counted as duplicate upstream submits.

Redis storage is the sum of `MEMORY USAGE` for the scenario's owned keys after
settlement. It excludes global Redis allocator overhead, replication/AOF
buffers, and other namespaces. Budget hashes retain reservation and settlement
receipts without TTL; settlement does not free them. Their growth must be
included in long-term capacity planning. Shared gauge refresh reads all retained
job metadata, so its cost also grows with retained jobs and replica count.

## Recorded output

The local Redis reported `redis_version:7.4.11`, `aof_enabled:1`, and
`maxmemory_policy:noeviction`. The following is the actual command output;
exit status was 0. No broad Go test suite ran during measurement. The small
load-test helper race check completed as the scenario started.

```text
2026/10/03 21:14:30 INFO parsed litellm pricing data loaded=3412 skipped=405
2026/10/03 21:14:30 INFO pipeline initialized plugins=[cost]
Video fake-provider load: jobs=1000 clients=16 workers=2 redis=127.0.0.1:36381 runtime=go1.25.0 darwin/arm64 cpus=10
Lifecycle: completed=1000 settled=1000 replayed=1000 upstream_submits=1000 duplicate_submits=0 elapsed=5m46.208s
Latency (service boundary, ms): submit_p95=1055.542 replay_p95=494.621 status_p95=143.181 status_samples=87083
Redis client commands: total=304579 per_job=304.579 (includes replays, status polling, workers, gauge scans; Lua internals excluded)
Shared gauge scan: jobs=1000 commands=1001 elapsed=1.397065s
Redis owned state: keys=2006 bytes=13024296 bytes_per_job=13024.3 budget_bytes=417760 receipt_fields=4000 persistent_budget_hashes=2
Starting eight concurrent synthetic 200 MB lifecycle copies
Copies: completed=8 bytes_each=200000000 total_bytes=1600000000 peak_concurrent=8 elapsed=2m22.887s
Copy Go memory (MiB): baseline_heap=5.97 peak_heap=49.93 peak_heap_delta=43.97 post_gc_heap=5.97 peak_heap_inuse=52.12 peak_sys=66.27 samples=6717 interval=20ms
Copy progress 0-25%: mean_heap_mib=17.05 samples=2154
Copy progress 25-50%: mean_heap_mib=9.21 samples=1339
Copy progress 50-75%: mean_heap_mib=9.04 samples=1354
Copy progress 75-100%: mean_heap_mib=8.98 samples=1587
PASS: all jobs completed and settled; duplicate submits=0; eight 200 MB copies stayed within 64 MiB incremental Go heap
```

The 1,000-job phase completed in 346.208 seconds (2.89 completed jobs/s).
The measured submit/status latency is substantial on this local Docker/host
setup; these results establish the workload and invariants, not a latency SLO.

The eight-copy phase moved 1.6 GB with an early 43.97 MiB peak heap increase.
Mean heap fell from 17.05 MiB in the first quarter to 9.21, 9.04 and 8.98 MiB
in the remaining quarters; post-GC heap returned to its 5.97 MiB baseline.
This supports bounded streaming memory, with a transient startup/ledger-scan
peak rather than growth proportional to copied bytes. It is not a claim of
constant allocation or flat OS RSS.

Retained Redis state was 13,024.3 bytes per job in this workload. Of that,
417.8 bytes per job were in two persistent budget hashes holding 4,000
reservation/settlement receipt fields (two operations times two scopes times
1,000 jobs). More budget scopes increase retained receipt storage. One shared
gauge refresh issued 1,001 client commands and took 1.397065 seconds over
1,000 retained jobs; the full workload command count includes these scans.

## Harness validation

```sh
export GOCACHE="$TMPDIR/th8088-gocache" TEST_REDIS_ADDR=127.0.0.1:36381
go test -race -count=1 ./cmd/loadtest
```

Actual output:

```text
ok  	github.com/futureagi/agentcc-gateway/cmd/loadtest	6.554s
```

The tests verify container validity and bounded/cancellable synthetic reads,
missing/duplicate-submit failure detection, Redis command counting across
pipelines and errors, and propagation of concurrent client failures.
