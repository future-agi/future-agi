# TH-8088 Batch 4 handoff

Date: 2026-10-03. Starting and remaining HEAD: `b784df355`.
Branch: `feat/TH-8088-gateway-video-generation`; base `dev` at `ce6af27`.

P1-13, P1-14 and P1-15 are complete, in that order. Work is confined to
`agentcc-gateway/` plus root `CHANGELOG.md` and `INSTALLATION.md`.
All changes remain uncommitted because the first staging command hit the
sandbox restriction. Nothing was pushed.

## P1-13 — docs fold-in

- Folded the supplied README section and all 27 support-matrix rows into
  `agentcc-gateway/README.md`, without the draft HTML header. P01–P04 are
  `implemented`; no row claims live smoke verification. Corrected surrounding
  claims, endpoint listings and local documentation links.
- Made the quickstart start from the complete config example, including the
  correlation secret and explicit output-ACL acknowledgement required to
  enable the provider. No account was enabled or contacted.
- Added an Unreleased changelog entry in the existing release style, without
  a fabricated version/date/SHA. Preserved all six breaking changes and the
  release-please squash-commit footer in [migration.md](migration.md), with
  the supplied consumer migration table.
- Added the N10 Redis durability/noeviction note to root `INSTALLATION.md`,
  including the conditional eviction-policy warning and Standalone caveat.
- Aligned example comments with explicit development memory opt-in, startup
  configuration/credential rotation, required correlation secret, credential
  references and supported ${ENV}/secret URI forms. No generic secret scheme
  is shown in the example. No generated admin-contract changes.

## P1-14 — fake-provider load scenario

Added `-scenario video`, `-video-jobs` and `-video-timeout` to
`cmd/loadtest`. The scenario runs two leased workers, 16 clients, real Redis
state and accounting, an existing local HTTP fake, real artifact verification
and disk storage, and shared metric scans. Each unique job is replayed once
with the same idempotency key. The separate large-copy phase requires eight
concurrent generated streams without allocating whole outputs.

[LOADTEST.md](LOADTEST.md) contains the command, all numbers, counting rules,
measurement boundaries and limits. Actual final measurement lines:

```text
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

The scenario exited 0 and removed its artifact directory and Redis namespace.
The Redis measurement includes persistent budget-receipt growth (4,000
receipt fields in two hashes) and normal shared gauge scans.

## P1-15 — pre-review checks

[REVIEW-CHECKLIST.md](REVIEW-CHECKLIST.md) records exact commands and actual
outputs for all required checks, the import graph, config/contract checks,
credential-pattern/scope checks, and failures with their resolutions.

Two fixture-only repairs were necessary: the redirect-limit case now uses a
deterministic transport and asserts all three permitted redirects, and the
BytePlus body-deadline case warms its TLS connection before timing the body.
Their 30 ms / 25 ms deadlines and expected errors remain intact. Each passed
20 race-enabled repetitions. No video runtime or A2A implementation changed.

The first plain `make test` failed with local Redis timeouts across four
packages. After checking Redis health, the identical unmodified command
passed; no parallelism override or test relaxation was used. Precise transient
host/Redis scheduling cause was not established. The full failure and final
output excerpts are preserved in the checklist.

## Exact verification commands and final lines

All commands below ran from `agentcc-gateway/` with:

```sh
export GOCACHE="$TMPDIR/th8088-gocache" TEST_REDIS_ADDR=127.0.0.1:36381
```

```sh
go test ./internal/config/... -run 'Video|Load' -count=1
```

```text
ok  	github.com/futureagi/agentcc-gateway/internal/config	0.373s
```

```sh
python3 ../scripts/generate-agentcc-gateway-contracts.py --check
```

```text
admin contract generation check exit=0
```

```sh
go test -race -count=1 ./cmd/loadtest
```

```text
ok  	github.com/futureagi/agentcc-gateway/cmd/loadtest	6.554s
```

```sh
go run ./cmd/loadtest -scenario video -video-jobs 1000 -c 16 -video-timeout 10m
```

```text
PASS: all jobs completed and settled; duplicate submits=0; eight 200 MB copies stayed within 64 MiB incremental Go heap
```

```sh
go vet -p 1 ./cmd/loadtest ./internal/config ./internal/metrics ./internal/plugins/budget ./internal/plugins/cost ./internal/providers/video ./internal/providers/video/byteplus ./internal/providers/video/videotest ./internal/redisstate ./internal/secrets ./internal/server ./internal/video ./internal/video/artifacts ./internal/video/capability ./internal/video/lifecycle ./internal/video/media ./internal/video/storetest ./internal/video/tariff
```

```text
go vet exit=0
```

```sh
TEST_REDIS_ADDR=127.0.0.1:36381 go test -count=1 ./internal/video/... ./internal/providers/video/... ./internal/server/... ./internal/plugins/... ./internal/metrics/... ./internal/redisstate/... ./internal/secrets/...
```

```text
ok  	github.com/futureagi/agentcc-gateway/internal/plugins/toolpolicy	0.528s
ok  	github.com/futureagi/agentcc-gateway/internal/plugins/validation	1.720s
ok  	github.com/futureagi/agentcc-gateway/internal/metrics	0.259s
ok  	github.com/futureagi/agentcc-gateway/internal/redisstate	1.254s
ok  	github.com/futureagi/agentcc-gateway/internal/secrets	0.335s
```

```sh
go test -race -count=1 ./internal/video/... ./internal/providers/video/...
```

```text
?   	github.com/futureagi/agentcc-gateway/internal/video/storetest	[no test files]
ok  	github.com/futureagi/agentcc-gateway/internal/video/tariff	2.494s
ok  	github.com/futureagi/agentcc-gateway/internal/providers/video	1.923s
ok  	github.com/futureagi/agentcc-gateway/internal/providers/video/byteplus	7.285s
ok  	github.com/futureagi/agentcc-gateway/internal/providers/video/videotest	3.238s
```

```sh
make test
```

```text
=== RUN   TestPriceRejectsInvalidUsage
--- PASS: TestPriceRejectsInvalidUsage (0.00s)
PASS
ok  	github.com/futureagi/agentcc-gateway/internal/video/tariff	0.609s
make test exit=0
```

```sh
go test -race -count=20 ./internal/video/media -run '^TestFetch_RedirectLimitAndDeadline$'
```

```text
ok  	github.com/futureagi/agentcc-gateway/internal/video/media	2.050s
```

```sh
go test -race -count=20 ./internal/providers/video/byteplus -run '^TestFetch_StreamCapDeadlineAndDownloadLimit$'
```

```text
ok  	github.com/futureagi/agentcc-gateway/internal/providers/video/byteplus	3.003s
```

```sh
go test -count=1 ./internal/config -run '^(TestVideoDefaults|TestVideoExample|TestVideoValidation)$' -v
```

```text
=== RUN   TestVideoExample
--- PASS: TestVideoExample (0.00s)
PASS
ok  	github.com/futureagi/agentcc-gateway/internal/config	0.354s
```

Formatting and import-graph Python commands are reproduced verbatim in the
checklist. Their actual final output lines were:

```text
gofmt -l: files=90 unformatted=0 exit=0
Import graph: 312 packages (including tests), 2591 source paths; forbidden imports/files=0
```

```sh
git diff --check
```

```text
git diff --check exit=0
```

The contract check, vet and whitespace check are silent on success; the
exit-status lines above were printed by their shell wrappers.

## Commit handoff

The first command attempted, from the repository root, was:

```sh
git add agentcc-gateway/README.md agentcc-gateway/config.example.yaml agentcc-gateway/docs/video-generation/migration.md CHANGELOG.md INSTALLATION.md && git commit -m "docs(agentcc-gateway): document video generation contract (TH-8088 P1-13)"
```

It stopped at `git add` (exit 128), before any commit:

```text
fatal: Unable to create '/Users/nikhilpareek/rick-workspace/tickets/TH-8088/future-agi/.git/worktrees/impl/index.lock': Operation not permitted
```

No retry, escalation, alternate index or workaround was attempted. These
exact per-step commands can stage the final disjoint file groups from the
repository root once Git writes are available. They were not executed after
the initial staging failure. One Conventional Commit per step; no
Co-authored-by footer.

```sh
# P1-13
git add agentcc-gateway/README.md agentcc-gateway/config.example.yaml agentcc-gateway/docs/video-generation/migration.md CHANGELOG.md INSTALLATION.md
git commit -m "docs(agentcc-gateway): document video generation contract (TH-8088 P1-13)"

# P1-14
git add agentcc-gateway/cmd/loadtest/main.go agentcc-gateway/cmd/loadtest/video.go agentcc-gateway/cmd/loadtest/video_test.go agentcc-gateway/docs/video-generation/LOADTEST.md
git commit -m "test(agentcc-gateway): load test video lifecycle and copies (TH-8088 P1-14)"

# P1-15
git add agentcc-gateway/internal/video/media/fetch_test.go agentcc-gateway/internal/providers/video/byteplus/adapter_test.go agentcc-gateway/docs/video-generation/REVIEW-CHECKLIST.md agentcc-gateway/docs/video-generation/BATCH4-NOTES.md
git commit -m "test(agentcc-gateway): verify video phase one review gates (TH-8088 P1-15)"
```

For the final Phase 1 squash, preserve the six `BREAKING CHANGE:` footers
from [migration.md](migration.md); they cover Idempotency-Key, local-only
DELETE/new cancel endpoint, status vocabulary, prompt privacy, multi-output
rejection, and renamed/strict request fields.

## Deviations and limits

- Commits could not be created under the prescribed sandbox; changes remain
  in this worktree with the commands above.
- The docs quickstart was completed with the already-required config fields;
  the supplied support matrix and six migration/footer changes were retained.
- Two previously documented timing-sensitive fixtures were repaired after
  actual failures. The subsequent full-suite Redis timeout run and unchanged
  successful rerun are both recorded; failures were not hidden or relaxed.
- The benchmark measures lifecycle service latency, not end-to-end HTTP
  latency. Redis counts are client commands, not Lua-internal operations.
  Copy memory is Go memory, not OS RSS; large Fetch streams are synthetic.
  These boundaries are explicit in the load report.
- The pre-existing A2A full-server race remains an exception from Batch 3.
  It was not repaired or rerun, and no clean full-server race claim is made.
- Existing architecture-era configuration prose remains subject to
  [implementation-pins.md](implementation-pins.md), especially N6: request
  encryption is not in Phase 1. The executable config example is current.

Final scope verification reported 13 authorized changed/new files, no staged
changes, unchanged HEAD `b784df355`, no temporary copy directories, no in-tree
build cache, and zero credential-pattern matches. `git diff --check` exited 0.

No dependencies, real provider calls, credentials, provider cohorts, generated
admin-contract edits or pushes were added. Temporary validation logs were
folded into these reports and removed. Stopped after this handoff.
