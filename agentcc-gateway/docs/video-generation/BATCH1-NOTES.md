# TH-8088 Batch 1 implementation notes

Date: 2026-10-02. Worktree: `feat/TH-8088-gateway-video-generation`, starting HEAD `0b8e23e2c2edc247484e194776137b395a7d83f1`.

**Implementation is present in the worktree. No implementation commits were created.** Git could not write the worktree metadata outside the sandbox's writable roots. The first two step-boundary commit attempts both failed before staging:

```text
fatal: Unable to create '/Users/nikhilpareek/rick-workspace/tickets/TH-8088/future-agi/.git/worktrees/impl/index.lock': Operation not permitted
```

There was no permission escalation or push. The requested commit order and exact file groups are recorded below so the prepared changes can be committed when Git metadata is writable. This notes file is also uncommitted for the same reason.

## Implemented, in brief order

### P1-07 — provider contract and test kit

- Added the provider-neutral adapter interface, capability/request/role/state/correlation/observation/output/error/usage types, and concurrent service registry.
- Shared types live in `internal/providers/video`; capability and tariff consume them without a dependency cycle.
- Added scripted `httptest` replies, delays, status codes, headers, malformed bodies, poll sequences, cancel/reconcile replies, correlation-based duplicate-submit counts, and synthetic PNG/container-only MP4 fixtures.
- Added `RunContractSuite(t, adapter, fixtures)` and a fake-adapter self-test. The suite currently covers prepare/submit/poll/fetch and unsupported cancel/reconcile; concrete provider request goldens and the remaining contract cases belong to the adapter batch.
- No BytePlus HTTP adapter or real provider calls were added. The old, unused `providers.VideoProvider` scaffold remains untouched.

### P1-02 — capability registry

- Added all seven exact BytePlus IDs from contract section 2, with operations, roles, resolutions, ratios, FPS, duration bounds, bounded auto duration for the 2.x family, audio behavior, prompt limit, output count, and option schemas.
- Enforced the 2.5 first-frame/first-and-last-frame adaptive ratio rule; last-only and mixed frame/reference operations fail validation. Audio-only reference is permitted only for 2.5.
- Enforced `frames = 25 + 4n` in `[29,289]`, 1.x-only seed bounds, boolean options, and `service_tier=default`; unknown fields/options report the field in `param`.
- Defaults are materialized in the normalized request. Explicit zero/null numeric fields in client JSON are rejected rather than mistaken for omitted defaults.
- Prompt limit is 16 KiB measured as UTF-8 bytes. Retired Sora IDs return `unsupported_model`, reason `retired`.
- Canonical JSON sorts keys, normalizes numeric options, and substitutes content SHA-256 digests for media sources. URL inputs require an ingress-verified digest. Inline digests are calculated and checked. A fixed golden verifies stability across number representations, key order, and signed URL changes.

### P1-03 — tariff and estimator

- Added typed usage units and BytePlus online list rates at revision `byteplus-2026-10-02`. Promotional, offline, and draft pricing are excluded.
- Implemented the token formula, frames precedence, model-maximum auto duration, conservative adaptive dimensions, and upward rounding to tokens/microdollars.
- Verified the 1080p, 16:9, 8-second, 24-FPS example: 388,800 tokens and 4,548,960 microdollars at $11.70/million tokens.
- Unknown models/combinations return `ErrUnpriced`. Video input additionally requires verified input duration and a positive operator-asserted `min_tokens_with_video_input`; zero never means free video input.

### P1-01 — config

- Added `VideoConfig` and all section 11 settings, including pin N6 additions: copy deadline/concurrency, absent-resubmit toggle, correlation secret, and the C2 minimum-token setting. Defaults keep all enablement/exception switches off.
- Added validation for store prerequisites, credentials/references, exact registry models/regions, deadlines, limits, tariff revision or explicit unpriced allowance, ACL acknowledgement, correlation mode, execution expiry, and artifact settings.
- Added `AGENTCC_VIDEO_ENABLED` and `AGENTCC_VIDEO_KILL_SWITCH` overrides and a parsed, commented YAML example. Secret values are excluded from config JSON. No generic secret URI scheme was introduced.
- Configuration only: server/route wiring is intentionally deferred. The existing server still constructs its scaffold memory store; this batch does not claim that the new enablement flag changes live route behavior. The nil-store 501 handler path is untouched.

### P1-04 — job model, state machine, stores

- Extended the persisted job with pinned identity, canonical request, idempotency/correlation, attempt/fencing, deadlines, cancellation/reconciliation, estimates/usage/accounting, artifacts, deletion/retention, trace and late-success fields. Removed the never-emitted `in_progress` constant. Job response helpers no longer echo prompts.
- Added transition validation in Go and Lua, including immutable terminal states and pinned identity/deadlines. Artifact expiry can update an already-completed job without changing its terminal status.
- Added Redis hashes and org/submit/due/reconcile/expire indexes, idempotency `SET NX` with fingerprint, live leases with monotonically increasing fences, fenced writes/tombstones/GC, and ownerless-org quarantine on reads and claims. Queue members survive lease-holder death, allowing takeover after lease expiry.
- Redis operations use `redisstate.Client` and fail closed. Legacy store methods remain available; new lifecycle methods take context and explicit leases. Memory storage returns copies and runs the same shared store suite.
- Reservation uses the existing budget limit-checking Lua through `BudgetStore.CheckAndRecordSpend`; compensation uses signed `RecordSpend` deltas. There is no separate spend counter/ledger. Partial hierarchy reservations and idempotency collisions are compensated.
- A lost accept-write reply is checked against the durable idempotency binding before refunding. If the result cannot be established, the reservation is retained and `ErrReservationUncertain` is returned.
- Tests include exactly one job and one net reservation for 100 concurrent same-org/key accepts, the explicit `TestAccept_CompensatesOnIdemCollision`, budget-cap concurrency, partial-hierarchy/Lua-failure compensation, lost Redis reply, private-field persistence across store recreation, key expiry, GC preserving a newer binding, tombstones, fencing, ownerless hashes, and Redis-unavailable failure.

## Design refinements and remaining limits

1. **C1 implementation gap:** the repository had `checkAndRecordScript`, but no exported `CheckAndRecordSpend` method. Added a small method around that existing script in `internal/redisstate/budget.go`; existing `RecordSpend` behavior is unchanged. The older architecture/plan references to reserving via `RecordSpend` are superseded by C1.
2. **Unverified provider facts remain closed:** BytePlus output ACL and failure billability stay `unknown`. For reference counts not recovered in the pinned document, the registry conservatively allows one asset per role; 2.5 reference images use the documented limit of 30. Expanding those limits needs verified evidence and a revision update. Adaptive estimation uses the image-ratio bound of 2.5 and rounds dimensions upward.
3. **Reservation crash gap remains a production prerequisite:** the mandated reserve-then-accept sequence compensates observable failures and collisions. The approved text also describes an intended-job reservation marker, but the existing budget store exposes aggregate counters only. A process death after budget reservation and before job persistence cannot yet be automatically attributed/refunded. This batch does not invent a parallel ledger; lifecycle/accounting work must resolve that gap before production. The existing budget API also chooses the current period on each call; settlement across a daily/weekly/monthly boundary needs an anchored-period design in the lifecycle/budget work.
4. **Redis topology:** the specified job/org/queue key layout uses multi-key scripts on one Redis primary. Redis Cluster cross-slot execution is not supported. Org/operation key components are escaped to avoid delimiter collisions.
5. **Persistence helpers:** added `store_memory.go` and `store_codec.go` to separate backend mechanics and hash serialization from the interface. Timestamps are persisted as Unix seconds; public JSON exclusions do not discard private persisted fields.
6. **Existing repository checks:** full-tree formatting and vet had pre-existing findings before implementation. They are left untouched to avoid unrelated changes. Changed Go files are formatted, and scoped vet passes. Details below.
7. **Commits:** the sandbox blocked Git metadata writes; the requested per-step commits and committed notes could not be produced. No attempt was made to work around the sandbox.

## Validation commands and results

Every Go invocation used this cache location; Redis tests used the supplied endpoint:

```sh
export GOCACHE="$TMPDIR/th8088-gocache"
export TEST_REDIS_ADDR=127.0.0.1:36381
```

Tests were written and run failing before implementing each package/behavior. Initial package failures were undefined-type/build failures; later regressions failed on the specific missing behavior (deadline mutation, lost-reply refund, artifact expiry, explicit zero defaults, idempotency expiry, and hash field names), then passed after fixes.

After **each** step, ran:

```sh
gofmt -l .
go vet ./...
go test -count=1 ./internal/video/... ./internal/providers/video/... ./internal/config/...
go test -race -count=1 ./internal/video/...
make test
```

| Step | Focused tests | Video race | make test | Full vet / formatting |
|---|---|---|---|---|
| P1-07 | pass | no video tests yet | pass | existing vet findings / 74 existing formatting paths |
| P1-02 | pass | pass | pass | same baseline findings |
| P1-03 | pass | pass | pass | same baseline findings |
| P1-01 | pass | pass | pass | same baseline findings |
| P1-04 | pass | pass | pass on retry after temporary Redis outage | baseline vet findings / 73 unchanged formatting paths |

Copied last output lines from the first four step checkpoints:

```text
P1-07 focused:
ok  	github.com/futureagi/agentcc-gateway/internal/providers/video/videotest	1.893s
ok  	github.com/futureagi/agentcc-gateway/internal/config	0.507s
P1-07 make test:
?   	github.com/futureagi/agentcc-gateway/internal/video	[no test files]

P1-02 make test:
PASS
ok  	github.com/futureagi/agentcc-gateway/internal/video/capability	1.832s
vet=1 targeted=0 race=0 make=0

P1-03 make test:
PASS
ok  	github.com/futureagi/agentcc-gateway/internal/video/tariff	1.270s
vet=1 targeted=0 race=0 make=0

P1-01 make test:
PASS
ok  	github.com/futureagi/agentcc-gateway/internal/video/tariff	1.701s
vet=1 targeted=0 race=0 make=0
```

The full suite passed after the initial P1-04 implementation. A later final run encountered a temporary external Redis outage (`127.0.0.1:36381: connect: connection refused`) after the focused and race commands passed. The endpoint subsequently answered `PONG` without any service/config change by this implementation. The retry is recorded below; tests were not weakened to skip a configured but unavailable Redis.

### Final focused tests — PASS

```sh
go test -count=1 ./internal/video/... ./internal/providers/video/... ./internal/config/...
```

```text
ok  	github.com/futureagi/agentcc-gateway/internal/video	3.479s
ok  	github.com/futureagi/agentcc-gateway/internal/video/capability	1.048s
?   	github.com/futureagi/agentcc-gateway/internal/video/storetest	[no test files]
ok  	github.com/futureagi/agentcc-gateway/internal/video/tariff	0.439s
ok  	github.com/futureagi/agentcc-gateway/internal/providers/video	1.177s
ok  	github.com/futureagi/agentcc-gateway/internal/providers/video/videotest	2.817s
ok  	github.com/futureagi/agentcc-gateway/internal/config	1.568s
```

### Final video race tests — PASS

```sh
go test -race -count=1 ./internal/video/...
```

```text
ok  	github.com/futureagi/agentcc-gateway/internal/video	4.404s
ok  	github.com/futureagi/agentcc-gateway/internal/video/capability	2.207s
?   	github.com/futureagi/agentcc-gateway/internal/video/storetest	[no test files]
ok  	github.com/futureagi/agentcc-gateway/internal/video/tariff	1.918s
```

### Full suite retry — PASS, exit 0

```sh
make test
```

```text
=== RUN   TestAdaptiveUpperBound
--- PASS: TestAdaptiveUpperBound (0.00s)
=== RUN   TestUnknownTariffNotZero
--- PASS: TestUnknownTariffNotZero (0.00s)
=== RUN   TestPriceRejectsInvalidUsage
--- PASS: TestPriceRejectsInvalidUsage (0.00s)
PASS
ok  	github.com/futureagi/agentcc-gateway/internal/video/tariff	0.917s
```

### Unset Redis environment — PASS; Redis cases skip

```sh
env -u TEST_REDIS_ADDR go test -count=1 ./internal/video/...
```

```text
ok  	github.com/futureagi/agentcc-gateway/internal/video	1.808s
ok  	github.com/futureagi/agentcc-gateway/internal/video/capability	0.320s
?   	github.com/futureagi/agentcc-gateway/internal/video/storetest	[no test files]
ok  	github.com/futureagi/agentcc-gateway/internal/video/tariff	0.466s
```

### Full vet — FAIL on unchanged baseline files

```sh
go vet ./...
```

```text
# github.com/futureagi/agentcc-gateway/internal/guardrails
# [github.com/futureagi/agentcc-gateway/internal/guardrails]
internal/guardrails/engine_test.go:624:6: assignment copies lock value to _: sync/atomic.Bool contains sync/atomic.noCopy
# github.com/futureagi/agentcc-gateway/internal/models
# [github.com/futureagi/agentcc-gateway/internal/models]
internal/models/errors_test.go:109:40: call of Unmarshal passes non-pointer as second argument
# github.com/futureagi/agentcc-gateway/internal/mcp
# [github.com/futureagi/agentcc-gateway/internal/mcp]
internal/mcp/registry.go:161:27: call of append copies lock value: github.com/futureagi/agentcc-gateway/internal/mcp.RegisteredTool contains github.com/futureagi/agentcc-gateway/internal/mcp.ToolStats contains sync/atomic.Int64 contains sync/atomic.noCopy
internal/mcp/registry.go:191:28: call of append copies lock value: github.com/futureagi/agentcc-gateway/internal/mcp.RegisteredTool contains github.com/futureagi/agentcc-gateway/internal/mcp.ToolStats contains sync/atomic.Int64 contains sync/atomic.noCopy
# github.com/futureagi/agentcc-gateway/internal/routing
# [github.com/futureagi/agentcc-gateway/internal/routing]
internal/routing/race.go:99:2: the raceCancel function is not used on all paths (possible context leak)
internal/routing/race.go:167:2: this return statement may be reached without using the raceCancel var defined on line 99
```

### Additional verification

```sh
go vet ./internal/video/... ./internal/providers/video/... ./internal/config/... ./internal/redisstate/...
go list -deps ./internal/video/... ./internal/providers/video/...
git diff --check
git diff --quiet -- internal/server/handlers_video.go internal/server/server.go go.mod go.sum
```

All four commands passed. Scoped vet and diff checks produced no output. The video dependency graph contained no `license_auth`, `enterprise`, or `ee` package paths. Changed Go files had no `gofmt -l` output. All 73 remaining full-tree formatting paths are unchanged files (the original count was 74; the edited video model is now formatted). `.video-build-cache/` does not exist.

Final full-tree formatting output ended with:

```text
internal/translation/anthropic/to_anthropic.go
internal/translation/finish_reason.go
internal/translation/gemini/error_test.go
internal/translation/gemini/stream.go
```

The broad race command also passed during store verification:

```sh
go test -race -count=1 ./internal/video/... ./internal/providers/video/... ./internal/config/...
```

Its last lines were:

```text
ok  	github.com/futureagi/agentcc-gateway/internal/providers/video	6.417s
ok  	github.com/futureagi/agentcc-gateway/internal/providers/video/videotest	5.324s
ok  	github.com/futureagi/agentcc-gateway/internal/config	12.375s
```

## Deferred to subsequent batches

- BytePlus HTTP request mapping, response fixtures, real adapter registration and the full per-provider contract matrix.
- Media ingress verification/digest population and SSRF controls; artifact disk/S3 copy, retrieval and physical deletion.
- Lifecycle worker, polling/reconciliation, safe resubmission proofs, deadline processing, hierarchy scope selection, terminal settlement and post-plugin double-charge prevention.
- Closing the reservation crash/period-boundary gaps described above before production use.
- HTTP/server wiring, disabled/kill-switch behavior, authorization/RBAC, managed-key rejection, cancel/content/admin routes, metrics/tracing/audit, and documentation rollout changes.
- Provider smoke verification, account permissions, output ACL and failure billing. No real credentials or paid calls were used.

## Pending commit sequence

These commands are a handoff recipe, **not commands that succeeded here**. Run from `agentcc-gateway/` once the worktree's Git metadata is writable. They preserve the brief's order and do not push.

```sh
git add -- internal/providers/video
git commit -m 'feat(agentcc-gateway): add video provider adapter contract (TH-8088 P1-07)'

git add -- internal/video/capability
git commit -m 'feat(agentcc-gateway): add video capability registry (TH-8088 P1-02)'

git add -- internal/video/tariff
git commit -m 'feat(agentcc-gateway): add video tariff estimator (TH-8088 P1-03)'

git add -- internal/config/config.go internal/config/video_test.go config.example.yaml
git commit -m 'feat(agentcc-gateway): add video configuration validation (TH-8088 P1-01)'

git add -- internal/video/models.go internal/video/state.go internal/video/state_test.go internal/video/store.go internal/video/store_codec.go internal/video/store_memory.go internal/video/store_redis.go internal/video/store_redis_test.go internal/video/store_test.go internal/video/storetest internal/redisstate/budget.go docs/video-generation/BATCH1-NOTES.md
git commit -m 'feat(agentcc-gateway): add durable video job store (TH-8088 P1-04)'
```
