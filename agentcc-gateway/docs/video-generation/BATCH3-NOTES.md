# TH-8088 Batch 3 handoff

Date: 2026-10-03. Base/remaining HEAD: `9b79f2205`.

Implemented P1-09 through P1-12 in order. Changes are confined to
`agentcc-gateway/`. No dependencies were added, no real provider requests or
credentials were used, and nothing was pushed. Work is uncommitted because the
first `git add` was blocked by the sandbox; see the exact commands below.

## P1-09 — lifecycle and accounting

- Added the lifecycle service and bounded, leased worker, injectable clock and
  crash hooks. Acceptance persists/enqueues; only the worker submits. The
  `prepared`, `calling`, and `received` phases survive restarts, and lease
  heartbeats/fences prevent stale writes.
- Implemented account/model submit limits, account poll limits, a shared BytePlus
  list limiter, cluster/provider/copy semaphores, and atomic org/account active
  job limits. Redis failures fail closed.
- Added poll floors, backoff/jitter, Retry-After handling, schema-error handling,
  deadlines, capability-dependent reconciliation, and opt-in proven-absent
  retry. BytePlus heuristic reconciliation never proves absence. Provider account
  and region stay pinned across restarts.
- Added verified ingress persistence and result streaming through the media
  verifier into the artifact store. Copy attempts/downloads/deadline are bounded.
  Failed delivery remains `completed`, billable, and artifact `unavailable`.
  Scheduled artifact sweeping and job GC; local deletion cannot regenerate
  artifacts or erase outstanding accounting.
- Added pre-submit and provider cancellation handling, local tombstones,
  terminal-state protection, late-success accounting, and content expiry/ranges.
- Extracted the existing org/team/user/key/tag budget hierarchy selection into
  `internal/plugins/budget/scopes.go`, with parity coverage. Reservations use
  `CheckAndRecordSpend`; compensation and settlement use signed `RecordSpend`
  deltas in the same counters and original budget period.
- Added durable reservation intents keyed by intended job ID. A crash after
  charging one or more scopes but before job creation is recovered by GC. The
  recovery fence prevents a paused acceptor from charging another scope or
  creating a job after refund. GC never refunds a committed job.
- Added atomic operation receipts in the existing budget hashes for reservation,
  compensation, and settlement replay. Receipts and their counters persist so
  unresolved jobs can settle after the usual metadata/budget TTL; ordinary chat
  writes preserve that persistence. This creates a retention tradeoff below.
- Added video cost processing and the direct-settlement budget-plugin guard.
  Settlement invokes `engine.RunPostPlugins` with `EndpointType=video`,
  `video_settlement=direct`, `video_cost_usd`, org/key identity, and video ID.

Tests cover success, bounded copy failures, ambiguous submit outcomes, crash
windows, takeover, lost Redis replies, no duplicate submit, no double spend,
partial/orphan reservations, concurrent budgets/active limits, cancellation,
late success, Redis outage/recovery, kill switch, deletion, and retention.

## P1-10 — HTTP and startup/shutdown

- Replaced the scaffold with submit/status/list/content/cancel/delete handlers.
  Submit uses `engine.Process`; org identity comes only from the authenticated
  API key. Reads are tenant-scoped with indistinguishable foreign/unknown IDs.
  Added required idempotency, model/provider ACL checks, org policy overrides,
  safe public response projection, filtering/pagination, and range streaming.
- Preserved N2: nil video store returns `501 not_configured`. Errors use
  `rate_limit_exceeded`, existing budget codes, and the specified 503 variants
  for kill switch, store, tariff and managed-key failures.
- Constructed Redis/dev-memory stores, disk/S3 artifacts, BytePlus registration,
  existing-scheme secret resolution, and the cross-replica list limiter. A
  disabled provider with retained credentials can drain previously accepted
  jobs. Startup rejects invalid enabled configuration. Worker, blobs, tracing
  and audit use the server shutdown path.
- Runtime kill-switch updates use Redis; startup initializes it with SET NX,
  preserving an operator's runtime value across replica restarts (N8).

Tests exercise authenticated routing, tenant isolation on every operation,
revocation/RBAC/provider access, validation and 503 variants, replay/conflict,
local delete, verified stored ingress, output privacy/ranges, and startup/drain.

## P1-11 — observability

- Registered all ten requested metrics and the five lifecycle span names.
  Spans carry video/org/service/model/provider-job IDs, with persisted trace
  linkage. Metric labels are bounded and discard caller-provided labels.
- Added shared-state reserved/unsettled gauges, state/queue durations, polling
  outcomes, unknown/unresolved/copy-failure events, and lease-takeover counts.
- Added log/span privacy capture tests for prompt, media, keys and URLs, plus
  `/metrics` integration coverage.
- Added `deploy/video-alerts.yaml`; no existing gateway dashboard directory was
  present. Thresholds are examples. Shared balance gauges must be aggregated
  with `max` across replicas, rather than summed.

## P1-12 — admin

Added existing-admin-auth routes at the pinned `/-/admin/video/` prefix:

- `GET capabilities`: explicit registry export without credentials.
- `POST killswitch`: strict bounded JSON, Redis toggle, audit event.
- `POST jobs/{id}/attach`: strict provider ID validation and N7 state checks,
  with audit. Unknown submissions resume normally; terminal failed jobs remain
  failed, record late success, settle only, and never deliver an artifact.
- `GET unsettled`: accounting visibility including retained tombstones, without
  prompts, input/output URLs, secrets, or client metadata.

Tests cover existing admin auth, the exact prefix, capabilities, strict bodies,
kill-switch persistence, audit capture, attach rejection/acceptance, N7 terminal
semantics, and safe unsettled output.

## Validation

Commands run from `agentcc-gateway/` with:

```sh
export GOCACHE="$TMPDIR/th8088-gocache" TEST_REDIS_ADDR=127.0.0.1:36381
```

Tests use unique Redis prefixes and local fake providers. TDD recovery tests
first reproduced the orphan debit (`orphan org spend=2`) and expiring accounting
receipt (`unsettled receipt expires after 840h0m0s`), then passed after fixes.

Final formatting check used `gofmt -l` on all modified/untracked Go files:

```sh
python3 - <<'PY'
import subprocess
files = subprocess.check_output(
    ['git', 'ls-files', '-m', '-o', '--exclude-standard', '--', '*.go'],
    text=True).splitlines()
r = subprocess.run(['gofmt', '-l', *files], text=True, capture_output=True)
print(r.stdout, end='')
print('gofmt: clean' if not r.stdout and r.returncode == 0 else 'gofmt: FAILED')
raise SystemExit(r.returncode or bool(r.stdout))
PY
```

Final output: `gofmt: clean`; no file names were emitted. `git diff --check`
also passed. `go.mod`, `go.sum`, `internal/a2a/server.go`, and
`internal/server/server_test.go` have no diff from HEAD. There is no in-tree
`.video-build-cache/`.

| Exact command | Result / final output line |
|---|---|
| `go vet -p 1 ./internal/video/... ./internal/providers/video/... ./internal/server/... ./internal/plugins/budget ./internal/plugins/cost ./internal/redisstate ./internal/secrets ./internal/metrics` | Exit 0, no output. |
| `go test -count=1 ./internal/video/... ./internal/providers/video/... ./internal/server/...` | Exit 0; `ok github.com/futureagi/agentcc-gateway/internal/server 1.300s`. All 10 tested packages passed; `storetest` has no tests. |
| `make test` | Exit 0; `ok github.com/futureagi/agentcc-gateway/internal/video/tariff 0.646s`. Runs `go test ./... -v -count=1`. |
| `go test -p 1 -race -count=1 ./internal/video/... ./internal/providers/video/... ./internal/server/... ./internal/plugins/budget ./internal/plugins/cost ./internal/redisstate ./internal/secrets ./internal/metrics` | Exit 1, final line `FAIL`, solely from the existing A2A race described below. All other packages passed; final passing package was `ok github.com/futureagi/agentcc-gateway/internal/metrics 1.287s`. |
| `go test -race -count=1 ./internal/server -run '^(TestVideo\|TestAdminVideo)'` | Exit 0; `ok github.com/futureagi/agentcc-gateway/internal/server 2.666s`. The backslash here escapes the Markdown table separator; the actual shell regex is `^(TestVideo` followed by an unescaped pipe and `TestAdminVideo)`. |
| `go test -p 1 -race -count=1 ./internal/redisstate ./internal/video ./internal/video/lifecycle ./internal/plugins/budget` | Final accounting changes: exit 0; `ok github.com/futureagi/agentcc-gateway/internal/plugins/budget 1.413s`. Redisstate 1.907s, video 4.405s, lifecycle 6.684s. |
| `go test -race -count=1 ./internal/video -run 'TestAccept_OrphanRecovery'` | Exit 0; `ok github.com/futureagi/agentcc-gateway/internal/video 1.513s`. |
| `go test -p 1 ./internal/server ./internal/video/lifecycle -run 'TestAdminVideo\|TestAttachTerminal\|TestKillSwitchStartup' -count=1` | Exit 0; server 0.711s; final line `ok github.com/futureagi/agentcc-gateway/internal/video/lifecycle 0.487s`. Pipes are unescaped in the actual shell regex. |

For copy/paste, the two regex commands above are exactly:

```sh
go test -race -count=1 ./internal/server -run '^(TestVideo|TestAdminVideo)'
go test -p 1 ./internal/server ./internal/video/lifecycle -run 'TestAdminVideo|TestAttachTerminal|TestKillSwitchStartup' -count=1
```

Checkpoint results before final recovery hardening:

- P1-09: regression, touched-package race checks, formatting, vet and `make test`
  passed. The make run ended with `ok .../internal/video/tariff 0.526s`.
- P1-10: regression ended with `ok .../internal/server 1.052s`; make ended
  with `ok .../internal/video/tariff 0.681s`. Video server race checks passed;
  the full server race run exposed the existing A2A issue.
- P1-11: metrics/privacy tests and make passed (make final line
  `ok .../internal/video/tariff 1.574s`). Concurrent broad runs also exposed
  existing short-deadline fixture flakes; final isolated regression is green.
- P1-12: admin/N7 tests passed. The serial regression command
  `go test -p 1 -count=1 ./internal/video/... ./internal/providers/video/... ./internal/server/...`
  ended with `ok .../internal/server 1.651s`. Final checks above cover all steps
  and the subsequent accounting recovery fixes.

### Known validation exception

The full server race run repeatedly reports
`TestA2AReturnImmediatelyAndCancelUnderV1WithAuth`: concurrent writes in unchanged
`internal/a2a/server.go:279-280` (`runMessageSendWithPipeline`) and `:419-420`
(`handleTasksCancel`). The final broad race log reports
`FAIL github.com/futureagi/agentcc-gateway/internal/server 2.573s`.
This is not a green full race run. A2A implementation and its existing server
test were left unchanged because this brief scopes the work to video.

Earlier concurrent broad checks caused existing fixture deadlines to expire in
BytePlus `TestFetch_StreamCapDeadlineAndDownloadLimit/body_deadline` (25ms) and
media `TestFetch_RedirectLimitAndDeadline/redirect` (100ms). Tests and deadlines
were not weakened. Independent broad checks were then run sequentially, using
`-p 1` for the broad race run. The final exact requested regression command and
unmodified `make test` both pass.

## Design refinements and limits

- Extended Batch 1 store/budget support where lifecycle recovery required it:
  active limits, takeover markers, authorized proven-absent transitions,
  accounting enumeration, orphan reservation fencing, and operation receipts.
  This reuses the existing spend hashes and limit-checking/signed-delta Lua
  primitives; reservation intents contain coordination data, not a second spend
  counter or request/media payload.
- Post-plugin dispatch is at most once: its persisted claim precedes dispatch.
  A process death in that narrow interval may lose an observer/telemetry event.
  Monetary deltas are independently durable and deduplicated. Managed-key video
  remains rejected as specified.
- Budget hashes carrying video receipts deliberately do not auto-expire, even
  for daily/weekly periods. Unresolved accounting has no safe fixed expiry.
  This preserves late settlement correctness but grows retained accounting
  data. A future compactor must prove that all referenced jobs/operations are
  retired before deleting receipts; ordinary TTL-based cleanup is unsafe.
- Redis remains the Batch 1 single-primary schema, without a Redis Cluster
  cross-slot fallback. Memory storage remains explicitly opted-in development
  behavior. Disabling a provider can drain its existing jobs only while its
  pinned account/region credentials remain configured.
- Runtime kill switch is initialized once and then controlled through Redis;
  changing startup config does not overwrite an existing operator toggle.

## Git status and exact checkpoint commands

The first P1-09 `git add` failed with:

```text
fatal: Unable to create '/Users/nikhilpareek/rick-workspace/tickets/TH-8088/future-agi/.git/worktrees/impl/index.lock': Operation not permitted
```

No retry, permission escalation, alternate index, commit, or other workaround
was attempted. These are the exact add/commit commands for the four intended
checkpoints, from `agentcc-gateway/`. Only the first add was attempted. They
describe the contents at each checkpoint: later steps update shared lifecycle,
store/model and server files, so file-only staging of the combined final
worktree cannot reconstruct four independent historical snapshots. Review/split
those shared changes before using these as separate commits.

```sh
# P1-09 (the add command that was blocked)
git add internal/video internal/plugins/budget/budget.go internal/plugins/budget/scopes.go internal/plugins/budget/video_test.go internal/plugins/cost/cost.go internal/plugins/cost/video_test.go internal/redisstate/budget.go internal/redisstate/budget_video_test.go internal/redisstate/ratelimit_window.go internal/providers/video/videotest/fakeprovider.go
git commit -m "feat(agentcc-gateway): add video lifecycle worker (TH-8088 P1-09)"

# P1-10
git add internal/server/handlers.go internal/server/handlers_video.go internal/server/handlers_video_test.go internal/server/server.go internal/server/video_setup.go internal/secrets/video.go internal/secrets/video_test.go internal/video/models.go internal/video/lifecycle/service.go
git commit -m "feat(agentcc-gateway): wire video HTTP API (TH-8088 P1-10)"

# P1-11
git add internal/metrics/registry.go internal/metrics/video.go internal/metrics/video_test.go internal/video/lifecycle internal/video/models.go internal/video/store.go internal/video/store_memory.go internal/video/store_redis.go internal/server/server.go internal/server/video_setup.go internal/server/handlers_video.go internal/server/handlers_video_test.go deploy/video-alerts.yaml
git commit -m "feat(agentcc-gateway): instrument video lifecycle (TH-8088 P1-11)"

# P1-12, including final recovery hardening and this handoff
git add internal/server/admin_video.go internal/server/admin_video_test.go internal/server/server.go internal/server/handlers_video_test.go internal/server/video_setup.go internal/secrets/video.go internal/video/lifecycle internal/video/store_reservation.go internal/video/store_reservation_test.go internal/video/store_redis.go internal/video/store_redis_test.go internal/redisstate/budget.go internal/redisstate/budget_video_test.go docs/video-generation/BATCH3-NOTES.md
git commit -m "feat(agentcc-gateway): add video admin operations (TH-8088 P1-12)"
```

## Left for batch 4 / later work

- P1-13 docs/config/changelog/contracts fold-in, including the pinned root Redis
  installation note; none of those files were changed in this batch.
- P1-14 fake-provider load test and capacity/Redis-operations measurements.
  Include shared gauge scans and retained accounting-receipt growth in sizing.
- P1-15 pre-review checklist and production readiness checks; investigate/fix
  the separately identified A2A race before claiming a clean full race suite.
- Provider cohorts L06-L10 and credentialed live-provider gates remain deferred.
- Evaluate safe accounting-receipt compaction and a durable outbox if reliable
  delivery of every post-plugin telemetry event becomes a requirement.

Stopped after this handoff. No docs fold-in, provider cohort, or load-test work
was started.
