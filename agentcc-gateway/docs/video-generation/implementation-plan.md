# TH-8088 implementation plan

Stage: ARCHITECTURE draft, 2026-10-02. Ordered, bounded tasks for a Codex coding agent working in an isolated worktree off dev `ce6af27` (`agentcc-gateway/`). Each step names files, acceptance, and commands. No step touches production or shared branches. Paid provider calls are excluded from every step; LIVE smoke (AT27/AT28) is a separate, approved activity.

Conventions: Go 1.25 (`go.mod`); run `gofmt -l`, `go vet ./...`, `go test ./internal/video/... ./internal/providers/video/... -count=1`, and `go test -race` on those packages after every step; Redis tests need `TEST_REDIS_ADDR` (local `redis-server` or `docker run -p 6379:6379 redis:7`). Keep `make test` green at every step (existing suites must not break). Branch per `BRANCH_NAMING_CONVENTION.md`.

## Phase 1: shared lifecycle + direct Seedance adapter + common contract suite

### P1-01 Config block and validation
Files: `internal/config/config.go` (+ `config_test.go`), `config.example.yaml`.
Do: add `VideoConfig` per architecture §11 with defaults and `validateVideo()` (enabled providers need credentials, models, deadlines, limits, tariff or `allow_unpriced_models`, ACL acknowledgement; `store: redis` requires `redis.enabled`; `store: memory` requires `allow_non_durable_store`). Env overrides `AGENTCC_VIDEO_ENABLED`, `AGENTCC_VIDEO_KILL_SWITCH`.
Accept: `config_test` cases for each validation rule; example config parses; defaults all off.
Cmd: `go test ./internal/config/... -run Video -count=1`.

### P1-02 Capability registry and request validation
Files: new `internal/video/capability/{registry.go,types.go,validate.go,normalize.go,fingerprint.go,byteplus.go}` + tests.
Do: types from `provider-adapter-contract.md` §1; BytePlus records for the seven model IDs (§2) with operations, inputs, durations (auto allowed, bounded), ratios (adaptive rule for first/last frame), options schema (`watermark, camera_fixed, return_last_frame, frames, seed(1.x only), service_tier`), prompt limit (`chars`, 1000-word recommendation is advisory: enforce `max_bytes` 16 KiB proposed), retired list (Sora IDs → `unsupported_model/retired`). Canonical JSON (sorted keys, normalized numbers, media digests) + sha256 fingerprint. Unknown fields rejected.
Accept: AT01, AT03, AT32 unit tests; golden fingerprint stability test.
Cmd: `go test ./internal/video/capability/... -count=1`.

### P1-03 Tariff and estimator
Files: new `internal/video/tariff/{tariff.go,byteplus.go,estimate.go}` + tests.
Do: typed units; BytePlus rates and token formula (§2 pricing) with revision `byteplus-2026-10-02`; upper-bound estimate with auto-duration at model max; `ErrUnpriced`.
Accept: AT22 tests; unknown model → `ErrUnpriced` (never zero).

### P1-04 Job model, state machine, Redis store
Files: `internal/video/models.go` (extend), `internal/video/state.go` (new), `internal/video/store.go` (interface extended; memory store updated), new `internal/video/store_redis.go` + `store_redis_test.go`, Lua scripts as Go constants.
Do: fields from architecture §5; transition table §6 enforced in Lua (`HGET status` + allowed map) with fence check; `Accept` as two steps: reserve budget in Go via the existing `BudgetStore.RecordSpend(+estimate)` (atomic check-and-increment), then one Lua script for `SET idem NX` + job hash + index writes; if the Lua step reports an existing idempotency entry or fails, compensate with `RecordSpend(-estimate)` (the window between the two steps is covered by the test `TestAccept_CompensatesOnIdemCollision`); `Lease`/`Renew`/`Release`; `ClaimDue`, `ClaimSubmit`; `ListByOrg` (ZREVRANGE + HMGET pipeline, filters applied in Go, count = ZCARD minus filtered; document offset semantics); tombstone; GC by `expire` zset; ownerless quarantine in every read.
Accept: store tests with Redis for every method; AT09 concurrency test on `Accept`; AT25 quarantine; memory store passes the same interface tests (shared `storetest`).
Cmd: `TEST_REDIS_ADDR=localhost:6379 go test ./internal/video/... -race -count=1`.

### P1-05 Media ingress
Files: new `internal/video/media/{fetch.go,sniff.go,limits.go}` + tests; reuse `internal/netguard`.
Do: https-only, no userinfo, redirect ≤ 3 with netguard dialer, per-asset deadline, byte cap, sniff + declared type match, dimension decode for png/jpeg/webp/gif headers (stdlib `image` config decoders + webp header parse), container-only mp4/mov check, audio header check; stream to blob (P1-06).
Accept: AT04 tests including DNS rebinding and redirect-to-private with a local resolver stub.

### P1-06 Artifact blob store
Files: new `internal/video/artifacts/{store.go,disk.go,s3.go,sweeper.go}` + tests; move SigV4 helper from `internal/cache/backend_s3.go` into `internal/awsv4` (shared, no behaviour change; keep cache tests green).
Do: `Put(ctx, key, r io.Reader, meta)` streaming with cap, `Open(ctx, key, rangeHeader)`, `Delete`, `Stat`; disk backend with `O_EXCL` temp + rename; S3 backend with `UNSIGNED-PAYLOAD` streaming PUT (or buffered when `max_bytes` small), range GET, DELETE, prefix enforcement; sweeper by `expire` zset.
Accept: disk tests; S3 tests against an httptest S3 stub asserting signed headers and prefix denial (AT20).

### P1-07 Adapter contract package and test kit
Files: new `internal/providers/video/{adapter.go,types.go,errors.go,registry.go}`, `internal/providers/video/videotest/{suite.go,fakeprovider.go,media.go}`.
Do: interfaces per contract §1; registry keyed by service; `RunContractSuite`; generic scriptable fake (`Script{Submit, Poll[], Fetch, Cancel, List}`), duplicate-submit detector keyed by correlation token.
Accept: suite runs against the fake-of-fake (self-test).

### P1-08 BytePlus Seedance adapter
Files: new `internal/providers/video/byteplus/{adapter.go,client.go,map.go,reconcile.go}` + `testdata/` + tests.
Do: HTTP client with netguard dialer and deadlines; `Prepare` sets `safety_identifier` (HMAC, config secret `video.correlation_secret` or derived from the gateway key store secret; must not be the API key); `Submit` builds `content[]` with roles, `omni_reference_task_type=reference` when any `reference_video`, `generate_audio`, `resolution`, `ratio` (adaptive forced for first/last frame), `duration` or `frames`, `watermark`, `camera_fixed`, `return_last_frame`, `seed` (1.x), `execution_expires_after`, never `callback_url/draft/draft_task`; `Poll` maps statuses (`queued, running, succeeded, failed, expired, cancelled`), usage `completion_tokens`, `content.video_url`, `error{code,message}`; `Fetch` streams the URL (netguard; counts downloads, max 100 awareness); `Cancel` only from `queued`, re-poll to confirm; `Reconcile` list heuristic (§2) with 1 QPS limiter; error mapping (401/403 provider_access_denied, 429 retry, 4xx submit_rejected, 5xx unknown).
Accept: contract suite green with recorded fixtures (retrieve response from the official example); AT02 role tests; AT07 tests; reconcile tests for unique/zero/ambiguous matches.

### P1-09 Lifecycle service and worker
Files: new `internal/video/lifecycle/{service.go,worker.go,submit.go,poll.go,copy.go,settle.go,reconcile.go,cancel.go,hooks.go,clock.go}` + tests.
Do: architecture §7 exactly; Redis rate limiter per account for submit/poll; per-provider semaphores; settlement via `BudgetStore.RecordSpend(delta)` for each hierarchy level (extract `recordOrgHierarchicalSpend` levels into a reusable helper in `internal/plugins/budget` or duplicate the level logic behind a small interface, with a test proving parity) and `engine.RunPostPlugins` with a settlement `RequestContext` (`EndpointType=video`, `Metadata{video_settlement:"direct", video_cost_usd, org_id, auth_key_id, key_type, video_id}`); cost plugin `case "video"` reads `video_cost_usd`; budget post-plugin skips when `video_settlement=="direct"`.
Accept: AT05, AT06, AT07, AT08, AT11 (crash harness), AT15, AT16, AT17, AT19, AT21, AT24, AT31 tests green with Redis + fakes.

### P1-10 HTTP handlers and routes
Files: `internal/server/handlers_video.go` (rewrite), `internal/server/server.go` (routes + wiring: construct Redis store, blob store, adapter registry, lifecycle worker start/stop with the existing shutdown path), `internal/server/handlers_video_test.go`.
Do: submit through `engine.Process`; status/list/content/cancel/delete org-scoped; error codes per architecture §4; `Idempotency-Key` required; range streaming for content; 503 variants for disabled/kill switch/store unavailable; keep `h.videoStore == nil → 501 not_configured` when `video.enabled=false` (today's behaviour).
Accept: AT10, AT12, AT13, AT18, AT20, AT23, AT26, AT30 tests; existing `server` tests still pass.

### P1-11 Observability
Files: `internal/metrics/registry.go` (register video metrics), lifecycle spans via `internal/otel`, alert rule examples under `deploy/` (proposed path; confirm with existing dashboards).
Accept: AT23 metrics/trace tests; `/metrics` exposes the new series.

### P1-12 Admin and ops endpoints (minimal)
Files: `internal/server/admin_video.go` (new), routes under the existing admin auth.
Do: `GET /admin/video/capabilities` (registry export), `POST /admin/video/killswitch`, `POST /admin/video/jobs/{id}/attach` (operator attaches a provider job id to an unresolved job; audited), `GET /admin/video/unsettled`.
Accept: admin auth tests; audit events emitted.

### P1-13 Docs, config example, changelog, generated contracts
Files: `agentcc-gateway/README.md` (replace "video shipped" wording with the support matrix state table), `config.example.yaml`, `CHANGELOG.md` (breaking: Idempotency-Key required; DELETE local-only; cancel endpoint; `in_progress` removed; prompt no longer echoed), `docs/` gateway video page (docs-writer stage owns prose; this step adds the skeleton and the capability export).
Generated contracts: the admin JSON schema (`api_contracts/gateway/agentcc-admin.schema.json`) gains `VideoProviderConfig` and `VideoCapability` definitions only if the Django backend will manage video provider config through the admin API (decision for docs/implementation review; default: not in Phase 1). If added: run `python3 scripts/generate-agentcc-gateway-contracts.py` and commit both generated files; CI runs `--check` and `gofmt -l internal/contracts/generated`.
Accept: `python3 scripts/generate-agentcc-gateway-contracts.py --check` passes; README no longer claims unverified support.

### P1-14 Load test scenario
Files: `cmd/loadtest` video scenario against the fake provider.
Accept: report with p95 submit/status, Redis ops per job, zero duplicate submits over ≥ 1,000 jobs, memory flat during 8 concurrent 200 MB copies (synthetic).

### P1-15 Pre-review checklist
`make test`; `go test -race ./internal/video/... ./internal/providers/video/... ./internal/pipeline/... ./internal/plugins/...`; `gofmt -l .` empty; `go vet ./...`; `python3 scripts/generate-agentcc-gateway-contracts.py --check`; import-graph test proving no `license_auth`/EE import under `internal/video` and `internal/providers/video`; secret scan; config example loads with `video.enabled=false` and with `true` + memory store refused.

## Cohort plans (one PR each, after Phase 1 merges; each inherits the common suite and adds LIVE gates)

### L06 Google: Gemini Veo 3.1 (+ Omni and Vertex as gated records)
Add `internal/providers/video/googlegemini` (Veo: `predictLongRunning` + operations GET + files download with API key; credential_ref to existing gemini provider; durations enum; `referenceImages` not for Lite; no cancel) and registry records for Omni (`unverified`, adapter with synchronous bounded call, `store=false`) and Vertex (`unverified`, binding probe documented). Contract fixtures from the Veo REST samples. LIVE: AT28 Veo T2V/I2V/first-last/references; Omni background/URI; Vertex binding probe.

### L07 Runway and Luma
`runway` adapter (`text_to_video`, `image_to_video`, `GET/DELETE /v1/tasks/{id}`, `X-Runway-Version: 2024-11-06`, statuses, `estimatedCost/cost`, URL refresh; DELETE guarded against terminal) and `luma` adapter (`/v1/generations`, keyframes, fail-closed HDR+10s, presigned refresh). LIVE: cancel semantics, URL refresh, HDR/10s probe.

### L08 Kling and MiniMax
`kling` (`external_task_id = video_id`, `GET /tasks?external_task_ids=`, multi-shot prompt passthrough, hotlink output copy) and `minimax` (`/v2/video_generation`, roles, mutual exclusion, required `ratio` for T2V, usage seconds/images, DELETE queued-only). LIVE: cancel, URL lifetime, billing fields. P13/P15 legacy records stay `unverified`.

### L09 DashScope Wan3/HappyHorse, xAI, Nova
`dashscope` (video-synthesis endpoint, `X-DashScope-Async`, regional workspace host, media type allowlist excluding file/web/edit sources, task poll), `xai` (`/v1/videos/generations`, statuses incl. `expired`), `bedrocknova` (SigV4 via `providers/bedrock/auth.go`, `clientRequestToken = video_id`, `GetAsyncInvoke`, S3 read with range; requires bucket config and IAM; HARD STOP on account). Pin enums/status strings from the full DashScope pages before coding (P1-02 pattern: registry revision bump).

### L10 fal Pika (+ LTX/PixVerse candidates)
`fal` queue adapter with host validation of returned URLs, optional signed-webhook receiver (AT14), `OutputACL=PublicDefault` gate; models as registry records. Enablement blocked until Nikhil authorizes exposure or private controls are proven.

## Regeneration and verification commands (summary)

```
cd agentcc-gateway
gofmt -l . ; go vet ./...
TEST_REDIS_ADDR=localhost:6379 make test
go test -race -count=1 ./internal/video/... ./internal/providers/video/... ./internal/pipeline/... ./internal/plugins/... ./internal/models/... ./internal/otel/... ./internal/audit/...
cd .. && python3 scripts/generate-agentcc-gateway-contracts.py --check
```
