# TH-8088 architecture: durable video-generation lifecycle in agentcc-gateway

Stage: ARCHITECTURE (draft for Rick, local artifact, not published). Date: 2026-10-02.
Inputs: PRD r2 at company-brain commit `5b41783f8e1383cd56148ca8c57213e0e212dbc7` (PRD.md, provider-matrix.md, decisions.md); product repo `future-agi` dev `ce6af27fa72afa793e1e89cfd7a7fea0d753cca0` read-only snapshot at `/Users/nikhilpareek/rick-workspace/tickets/TH-8088/future-agi`.
Status of this document: proposed design. Nothing here is implemented, tested, deployed or smoke-verified. Every number not tied to a cited source is marked `proposed`.

## 0. Bottom line

Build video generation as a durable, Redis-backed job lifecycle inside the existing Go gateway, reusing the pipeline engine (auth, RBAC, budget, rate limit, cost, credits, logging, otel, audit) for every video request, and put provider specifics behind a narrow adapter contract with explicit capability flags (idempotent submit, correlation lookup, cancel support, output expiry). Seedance (BytePlus ModelArk) is the first adapter because its contract is fully documented (create, get, list, delete, statuses, retention, token pricing) and it exercises the hardest cases: no provider idempotency token, auto duration, list-only reconciliation, 24h/100-download output URLs.

Recommendation and confidence:

- Durable store: Redis via the existing `internal/redisstate` client, with lease-based ownership and Lua compare-and-set. Confidence high. Reason: the gateway has no SQL database; Redis is already the shared-state store for budget, rate limits, credits, cluster membership and license revocation, and CI already runs a Redis service (`TEST_REDIS_ADDR`). Adding Postgres would be the first relational dependency in the gateway, which is a hard-to-reverse choice and would need an RFC; this design does not need it.
- Submission and polling: a single lifecycle worker (runs in every replica, claims work through Redis leases), no inline upstream call from the HTTP handler. Confidence high. Reason: one submitter path makes the crash-window analysis tractable (section 7) and matches the existing `internal/async` worker shape.
- Budget: reuse the existing `BudgetStore.RecordSpend` Lua (atomic check-and-increment in microdollars) as the reservation primitive, settle with a delta on terminal state. Confidence medium. Reason: the Lua is an `HINCRBY` so negative deltas work, but `budget.Plugin.ProcessResponse` also records spend from `rc.Metadata["cost"]`; the video settlement path must call the store directly and must NOT run the budget post-plugin twice. This needs a focused test (AT21).
- Output storage: new `internal/video/artifacts` blob interface with disk and S3 backends; the S3 backend reuses the hand-rolled SigV4 signer already in `internal/cache/backend_s3.go`. `internal/files` is an in-memory map and cannot hold 24h of multi-MB videos across replicas. Confidence high.

What would change the recommendation: if product requires job history beyond 30d, cross-region listing, or SQL-style reporting, Redis stops being the right primary store and a Postgres-backed store behind the same `Store` interface becomes the RFC. If BytePlus adds a client idempotency token, the Seedance reconciliation heuristic in section 7.4 is replaced by proven dedupe.

Hard stops for Nikhil (not decided here): managed tariff and any managed/credits charging (D06); any paid smoke run; any supplier-public output exposure exception (P21 to P24 fal/PixVerse); Nova S3 bucket ownership and billing account.

## 1. Context: what exists at dev ce6af27

| Area | Evidence (file:line) | Fact |
|---|---|---|
| Routes | `internal/server/server.go:474-479` | `POST /v1/videos`, `GET /v1/videos`, `GET /v1/videos/{video_id}`, `DELETE /v1/videos/{video_id}` registered; no cancel or content route. |
| Submit | `internal/server/handlers_video.go:29-110` | Creates a local job with `StatusQueued`, no provider call, no org, no idempotency, `n` up to 4, 24h `ExpiresAt`. Does not run `h.engine.Process`, so auth/RBAC/budget plugins never run for video. |
| Status/delete | `handlers_video.go:130,159-175` | `videoStore.Get(id)` unscoped; DELETE marks non-terminal jobs `cancelled` locally (false cancellation). |
| List | `handlers_video.go:225` | `ListByOrg("")` returns all orgs. |
| Store | `internal/video/store.go:36-69` | `sync.Map`, `GetForOrg` passes when `job.OrgID == ""`. |
| Provider interface | `internal/providers/provider.go:127-133` | `VideoProvider{SubmitVideo, GetVideoStatus, GetVideoContent, CancelVideo}` exists; no provider implements it (`grep -rl VideoProvider internal` returns only `provider.go`). |
| Models | `internal/models/video.go` | Provider-facing request with one `InputImage`; no roles, no references, no audio. |
| Cross-cutting enforcement | `internal/server/handlers_image.go:55-115`, `internal/pipeline/engine.go:73-145` | Image/chat handlers run `engine.Process(ctx, rc, providerCall)`: pre-plugins auth(20) → rbac(30) → quota(35) → budget(40) → validation(70) → ratelimit(80) → cache(200); post-plugins cost(500) → credits(510) → logging/otel/audit/prometheus (parallel group). |
| Org identity | `internal/server/handlers.go:324-342`, `internal/tenant/resolve.go:9`, `internal/plugins/auth/auth.go:130-141` | Org comes from the API key's metadata (`key_org_id`), set by the auth plugin and peeked by `peekKeyOrgID`. Never from the request body. |
| Model permission | `internal/plugins/rbac/rbac.go:45-60` | Permission `models:<model>` and team model allowlists checked against `rc.Model`. |
| Budget | `internal/plugins/budget/budget.go:158-200,379-410`; `internal/redisstate/budget.go:40-60` (Lua) | Check-then-record, no reservation. Redis Lua rejects when `spent + cost > limit`, else `HINCRBY` micros. |
| Cost | `internal/plugins/cost/cost.go:82-96` | `switch rc.EndpointType`: image/speech/rerank are "skip for now"; only chat/embedding priced from `modeldb`. |
| Credits | `internal/plugins/credits/credits.go:97-141` | Managed keys (`key_type == managed`) deduct `rc.Metadata["cost"]` from Redis credits. |
| Redis client | `internal/redisstate/client.go:32-88` | `Client.Do` with circuit breaker; `Available()`; go-redis v9.18.0 in `go.mod`. |
| Egress guard | `internal/netguard/netguard.go:121-135`; used by `providers/override.go:260` and `providers/bedrock/translate.go:28` | `DialContext` refuses loopback/link-local/metadata/private (unless opted in) on the dialled address (post-DNS, covers rebinding and redirects). |
| Files | `internal/files/store.go` | In-memory, org-scoped map; no streaming, no TTL. |
| Object storage code | `internal/cache/backend_s3.go:35-200` | Hand-rolled SigV4 PUT/GET/DELETE against S3 (no AWS SDK in `go.mod`); Azure Blob and GCS backends exist alongside. |
| Secrets | `internal/secrets/resolver.go`, `config.example.yaml:18` | `${ENV}` expansion and `scheme://` secret URIs (aws, azure, gcp, vault). |
| CI | `.github/workflows/agentcc-gateway-ci.yml:30-80` | Redis 7 service, `TEST_REDIS_ADDR=localhost:6379`, `make test`, scoped `-race` on pipeline/plugins/models/otel/audit, generated-contract check. |
| Generated contracts | `scripts/generate-agentcc-gateway-contracts.py:16-18` | Admin schema → `futureagi/agentcc/contracts/gateway_admin.py` and `agentcc-gateway/internal/contracts/generated/gateway_admin.go`. No `/v1/videos` consumer in `futureagi/` (`grep -rn 'v1/videos' futureagi` empty). |

Consequence: video must become a pipeline-processed endpoint like image, not a side door. Everything tenant/budget/trace related comes for free once `engine.Process` wraps the submit, and once the lifecycle worker runs the post-plugins at settlement with a reconstructed `RequestContext`.

## 2. Decision summary (ADR)

| # | Decision | Why | Alternatives rejected | Reversibility |
|---|---|---|---|---|
| A1 | Redis-backed `video.Store` (hashes + sorted sets + Lua), reusing `redisstate.Client` | No SQL in gateway; Redis already shared state; CI has Redis | Postgres (first SQL dep, RFC-level); SQLite/bbolt (single replica only); keep memory (fails R05/R16) | Store is behind an interface; swapping backend later is reversible. Key schema is versioned (`video:v1:`). |
| A2 | Handler persists + enqueues; one lifecycle worker per replica claims via lease; handler waits up to `submit.sync_wait` (proposed 5s) for state to advance before answering 202 | One submitter path, crash windows enumerable, replicas interchangeable | Inline upstream call in handler (two submitter paths, harder lease story); external queue (Temporal is not in the gateway stack) | Reversible. |
| A3 | Idempotency scope `org + operation + sha256(Idempotency-Key)` with request fingerprint, enforced by Lua `SET NX` | R12/R13; replay must be atomic across replicas | Local mutex (fails across replicas) | Reversible, but key semantics are a public contract: changing them later is a breaking change. |
| A4 | Budget reservation = `BudgetStore.RecordSpend(+estimate)` at accept, settlement = `RecordSpend(actual - estimate)` at terminal; release only known-unused amounts | Reuses the one existing atomic budget primitive; no parallel ledger | Separate reservation counter (parallel mechanism, drift risk) | Reversible. |
| A5 | New `providers/video` adapter contract (`provider-adapter-contract.md`) replacing the unused `providers.VideoProvider` | Existing interface lacks roles, references, capability flags, idempotency semantics, correlation lookup | Extend `VideoProvider` in place (would still be a different shape) | Reversible; interface unused today. |
| A6 | Artifact blob store `internal/video/artifacts` with disk + S3 (SigV4 reuse) backends; tenant-authorized streaming retrieval via gateway; no provider URLs in responses | R11, D05; `internal/files` is memory-only | Presigned provider URLs (expiry, exposure); extend `internal/files` (would need a rewrite anyway) | Reversible. |
| A7 | Separate `POST /v1/videos/{id}/cancel`; `DELETE` is local-only | D10, R09, R10 | Keep DELETE=cancel (false statement) | Public contract: once shipped, hard to reverse. Flagged in migration notes. |
| A8 | Per-provider config under `video.providers.<id>` with `enabled: false` default; credentials via `${ENV}`/`scheme://` like existing providers, or `credential_ref` to an existing `providers[]` entry (gemini, bedrock) | R18, D06 | Reuse `providers[]` list directly (video capabilities are not chat routing) | Reversible. |
| A9 | Fail closed when Redis is unavailable: submit/cancel/delete/status return 503 `video_store_unavailable`; no memory fallback | Security/quota fail closed (coding bible §3) | Memory fallback (would re-create ownerless, non-durable jobs) | Reversible. |
| A10 | Tariff table `internal/video/tariff` versioned per provider+model+resolution(+input_video); unknown tariff blocks managed keys always and BYOK keys unless `allow_unpriced_models: true` | R14/R15, D06 (unknown price is not zero) | Treat unknown as zero (explicitly forbidden) | Reversible. |

Hard-to-reverse items that should go through an RFC per `brain/org/decision-making.md`: the public `/v1/videos` contract shape (section 4) and the cancel/delete semantics (A7). Everything else is reversible behind interfaces.

## 3. Components

### 3.1 Existing, reused unchanged

`pipeline.Engine` and all plugins; `auth.KeyStore`; `tenant.Store`; `redisstate.Client/BudgetStore/RateLimiter/CreditStore`; `netguard.DialContext`; `secrets.Resolver`; `privacy.Redactor`; `otel`, `metrics`, `audit`; `cache/backend_s3.go` SigV4 signer (lifted into a shared helper); `providers/bedrock/auth.go` SigV4 for Nova; `providers/gemini` HTTP client setup for Veo/Omni credentials.

### 3.2 Existing, modified

- `internal/server/handlers_video.go`: rewritten to run `engine.Process` for submit, org-scoped reads, cancel and content routes.
- `internal/server/server.go`: route table adds `POST /v1/videos/{video_id}/cancel` and `GET /v1/videos/{video_id}/content`; store construction switches to Redis store when `video.enabled`.
- `internal/video/models.go`: job record extended (section 5); `in_progress` constant removed (never produced by the scaffold, see section 12).
- `internal/video/store.go`: `Store` interface extended; `MemoryStore` kept for unit tests and refused in production config unless `allow_non_durable_store: true`.
- `internal/config/config.go`: `Video VideoConfig` block (section 11) and validation.
- `internal/plugins/cost/cost.go`: `case "video":` reads `rc.Metadata["video_cost_usd"]` written by the lifecycle settlement (so the chain cost → budget → credits → logging stays intact). No other plugin change.
- `internal/providers/provider.go`: `VideoProvider` removed (unused) or left with a deprecation comment; new contract lives in `internal/providers/video`.

### 3.3 New

| Package | Responsibility |
|---|---|
| `internal/video/store_redis.go` | Redis store: job hash, org index, idempotency index, due-queue, leases, tombstones, GC. All multi-key writes are Lua scripts with a lease fencing check. |
| `internal/video/lifecycle` | `Service` (submit/accept, status, list, cancel, delete), `Worker` (submission, polling, reconciliation, result copy, settlement, GC), state machine + transition validation. |
| `internal/video/capability` | Capability registry: records keyed by `service+model+version+region+operation`, validation of request against record, request normalization and fingerprint. Static Go tables per adapter, revisioned. |
| `internal/video/tariff` | Tariff table and estimator; typed usage units. |
| `internal/video/media` | Ingress: URL fetch through netguard with limits and decoded checks; inline base64 decode; media type sniffing. |
| `internal/video/artifacts` | Blob store interface (`Put(stream)`, `Open`, `Delete`, `Stat`), disk and S3 backends, retention sweeper. |
| `internal/providers/video` | Adapter contract (`provider-adapter-contract.md`). |
| `internal/providers/video/byteplus` | Seedance adapter (Phase 1). Later: `googlegemini`, `googlevertex`, `runway`, `luma`, `kling`, `minimax`, `dashscope`, `xai`, `bedrocknova`, `fal`. |
| `internal/providers/video/videotest` | Shared contract suite + httptest fake providers + recorded fixtures (see `test-architecture.md`). |

## 4. API surface

All routes require an API key (same `KeyAuth` middleware and auth plugin as chat). Org is derived from the key. `video_id` format: `video_<ulid>` (26 char Crockford base32, sortable). Unknown JSON keys are rejected with 400 `unknown_field` (R03). Error envelope is the existing `models.APIError` (`{"error":{"type","code","message","param"}}`).

### 4.1 Submit: `POST /v1/videos`

Headers: `Authorization: Bearer <key>`, `Idempotency-Key: <1..255 printable chars>` (required; 400 `missing_idempotency_key`).

Request body:

```json
{
  "model": "byteplus/dreamina-seedance-2-5-260628",
  "prompt": "A girl holding a fox, wind in her hair, sound of wind",
  "inputs": [
    {"role": "first_frame", "media_type": "image/png", "source": {"url": "https://cdn.example.com/a.png"}},
    {"role": "last_frame",  "media_type": "image/png", "source": {"data": "<base64>"}},
    {"role": "reference_audio", "media_type": "audio/mpeg", "source": {"url": "https://cdn.example.com/a.mp3"}}
  ],
  "duration_seconds": 8,
  "resolution": "1080p",
  "aspect_ratio": "16:9",
  "fps": 24,
  "audio": true,
  "n": 1,
  "provider_options": {"watermark": false, "camera_fixed": false, "seed": 42},
  "metadata": {"trace": "abc"},
  "end_user_id": "user-123"
}
```

Rules: `model` must be a service-qualified ID present in the capability registry (else 400 `unsupported_model`). `inputs[].role` ∈ `first_frame|last_frame|reference_image|reference_video|reference_audio`; allowed roles, counts, byte/pixel limits come from the capability record. `source` is exactly one of `url` (https only) or `data` (base64, bounded by `video.media.max_inline_bytes`). `duration_seconds` is a finite integer where the model requires integers; `-1`/auto is accepted only when the record marks `auto_duration_bounded=true` and the estimator can bound cost by the model maximum. `provider_options` is validated against the record's option schema (names, types, enums); unknown keys 400 `unknown_provider_option`. `n` must equal 1 unless the record has `max_outputs > 1` and `native_multi_output=true` (D12). `metadata` ≤ 16 keys, 64/512 char limits (proposed). `end_user_id` optional, ≤ 128 chars, hashed before being sent to providers that accept an end-user identifier.

Response `202 Accepted` (or `200` on replay of a terminal job):

```json
{
  "id": "video_01J9...",
  "object": "video",
  "status": "queued",
  "provider_state": "queued",
  "model": "byteplus/dreamina-seedance-2-5-260628",
  "resolved": {"service": "byteplus", "model_id": "dreamina-seedance-2-5-260628", "region": "ap-southeast-1", "capability_revision": "byteplus-2026-10-02"},
  "created_at": 1790970000,
  "updated_at": 1790970003,
  "submitted_at": 1790970003,
  "deadlines": {"submit_by": 1790970030, "run_by": 1790977200},
  "retry_safe": true,
  "upstream_may_continue": false,
  "estimate": {"unit": "video_tokens", "quantity": 1105920, "usd": 11.83, "tariff_revision": "byteplus-2026-10-02", "basis": "upper_bound"},
  "metadata": {"trace": "abc"}
}
```

`status` is one of `submitting|submission_unknown|queued|running|completed|failed|cancelled`. On replay with a different fingerprint: 409 `idempotency_conflict`. On replay of a locally deleted job: 410 `deleted_job` with `{id, deleted:true}`.

Other submit errors: 401 (middleware), 403 `missing_org` (authenticated key without `org_id` metadata), 403 `model_forbidden` (RBAC/team), 403 `provider_not_allowed` (`auth_allowed_providers`), 413 `request_too_large`/`media_too_large`, 429 `budget_exceeded` (existing budget plugin shape) / `rate_limited` with `Retry-After`, 503 `video_disabled`, 503 `video_submissions_disabled` (kill switch), 503 `provider_not_configured`, 503 `tariff_missing`, 503 `managed_tariff_not_configured` (managed keys, D06), 503 `video_store_unavailable`.

### 4.2 Status: `GET /v1/videos/{video_id}`

```json
{
  "id": "video_01J9...", "object": "video",
  "status": "completed", "provider_state": "succeeded",
  "model": "...", "resolved": {...},
  "created_at": 0, "updated_at": 0, "submitted_at": 0, "completed_at": 0,
  "last_checked_at": 0,
  "progress": null,
  "error": null,
  "cancellation": {"state": "none"},
  "reconciliation": {"state": "none"},
  "upstream_may_continue": false,
  "retry_safe": true,
  "artifacts": [
    {"index": 0, "content_type": "video/mp4", "bytes": 18311245, "duration_seconds": 8, "width": 1920, "height": 1080, "state": "available", "expires_at": 1791056403}
  ],
  "usage": {"unit": "video_tokens", "quantity": 246840, "source": "provider_reported"},
  "cost": {"usd": 2.64, "source": "reconciled", "tariff_revision": "byteplus-2026-10-02", "currency": "USD"},
  "metadata": {}
}
```

`progress` is `null` unless the provider reports a number. `error` is `{"code","message","provider_request_id","retryable"}` with provider text passed through the redactor. Foreign or unknown ID: 404 `video_not_found` (indistinguishable). Ownerless record: 404.

### 4.3 Result: `GET /v1/videos/{video_id}/content?artifact_index=0`

Streams the copied artifact with `Content-Type`, `Content-Length`, `Content-Disposition: attachment; filename="video_<id>_<idx>.mp4"`, `Accept-Ranges: bytes` (disk and S3 backends both support range reads; proposed to implement in Phase 1 for disk, Phase 1b for S3). 409 `result_not_ready` while non-terminal; 410 `output_expired` after `expires_at`; 410 `output_unavailable` when copy failed (job stays `completed`, `artifacts[].state = unavailable`, `error.code` on the artifact); 404 foreign/unknown. Never redirects to a provider URL.

### 4.4 List: `GET /v1/videos?limit=20&offset=0&order=desc&status=completed&model=...`

Validates `limit` 1..100, `offset` ≥ 0, `order` ∈ asc|desc, `status` ∈ known set, `model` must be registry-known (400 `invalid_filter` otherwise). Ordering `created_at desc, id desc` (ULID gives a total order). Response `{"object":"list","data":[...status objects...],"total":N,"limit":L,"offset":O,"has_more":bool}`. Documented: offset pagination may shift under concurrent inserts. Tombstoned (locally deleted) and ownerless jobs are never returned or counted.

### 4.5 Cancel: `POST /v1/videos/{video_id}/cancel`

Repeat-safe. Outcomes: 200 `{"id","status":"cancelled","cancellation":{"state":"confirmed","scope":"pre_submit"|"provider"}}`; 202 `{"status":"running","cancellation":{"state":"requested"}}`; 409 `cancel_unsupported` (adapter lacks cancel); 409 `cancel_not_available` (provider cannot cancel in this state, e.g. Seedance `running`); 200 with current terminal state if already terminal (`cancellation.state = not_applicable`). Accounting is never released by a cancel; only a provider-confirmed cancellation of a queued job releases the reservation when the adapter marks `cancel_releases_charge=true` (Seedance: cancelled queued tasks are not billed per "successful output only" pricing note; treated as release only after the provider status reads `cancelled`).

### 4.6 Local delete: `DELETE /v1/videos/{video_id}`

200 `{"id","object":"video","deleted":true,"deletion_scope":"local_only","upstream_may_continue":true|false,"accounting_retained":true}`. Repeat on same org returns 200 again (tombstone). Missing/foreign 404. Side effects: artifacts purged from blob store, job hash reduced to a tombstone (`deleted_at`, org, provider correlation, ledger, idempotency digest) retained until reconciliation and retention end; the job is removed from the org list index; polling continues only if accounting is unsettled (to settle cost), never to restore output.

## 5. Job record (Redis hash `video:v1:job:{id}`)

| Field | Type | Notes |
|---|---|---|
| `id`, `org_id`, `key_id`, `creator` | string | `org_id` required; empty means quarantined. |
| `status`, `provider_state`, `phase` | string | `phase` ∈ `prepared|calling|received` during `submitting`. |
| `service`, `model_id`, `region`, `account_ref`, `capability_revision` | string | pinned at accept; aliases never mutate them (R16). |
| `request_canonical` | JSON | validated, normalized request (prompts and inline media replaced by digests in logs; stored encrypted at rest if `video.encrypt_requests` and a key is configured, else plain with the retention policy). |
| `fingerprint`, `idem_digest`, `idem_op` | string | sha256 hex. |
| `provider_job_id`, `provider_request_id`, `correlation_token` | string | `correlation_token` = value we sent that the provider echoes (Kling `external_task_id`, Nova `clientRequestToken`, Seedance `safety_identifier`). |
| `attempt_id`, `attempt_started_at` | string/int | per submission attempt. |
| `lease_owner`, `lease_fence` | string/int | fence increments on every lease acquisition; all Lua writes check `ARGV.fence == HGET lease_fence`. |
| `created_at`, `updated_at`, `submitted_at`, `completed_at`, `last_checked_at` | int (unix s) | |
| `submit_by`, `run_by`, `reconcile_by`, `next_poll_at`, `poll_interval_ms`, `poll_failures` | int | persisted deadlines (R07). |
| `cancel_state`, `cancel_requested_at` | string/int | |
| `reconcile_state`, `reconcile_reason` | string | `none|pending|resolved|unresolved`. |
| `error_code`, `error_message`, `error_retryable`, `upstream_may_continue`, `retry_safe` | | |
| `estimate_unit`, `estimate_qty`, `estimate_usd`, `reserved_micros`, `settled_micros`, `tariff_revision`, `usage_json`, `settlement_state` | | `settlement_state` ∈ `reserved|settled|released|unsettled`. |
| `artifacts_json` | JSON | per index: content_type, bytes, dims, duration, blob_key, state, expires_at, error. |
| `deleted_at`, `expires_at` | int | tombstone and metadata retention. |
| `metadata_json`, `end_user_hash` | | |

Indexes: `video:v1:org:{org}:jobs` (zset, score `created_at`, member `id`), `video:v1:idem:{org}:{op}:{digest}` (string `id`, TTL = idempotency retention, refreshed while unsettled), `video:v1:due` (zset, score `next_poll_at`), `video:v1:submit` (zset, score `created_at`), `video:v1:lease:{id}` (string, PX), `video:v1:reconcile` (zset), `video:v1:expire` (zset by `expires_at` for GC). Namespace `video:v1:` allows a key-schema migration later.

Persistence requirement for operators: Redis must run with AOF `appendfsync everysec` or RDB snapshots and `maxmemory-policy noeviction` for the video keyspace (document in config.example and INSTALLATION.md). The gateway cannot enforce this; it checks `CONFIG GET maxmemory-policy` at startup when permitted and logs a warning if eviction is enabled (proposed).

## 6. State machine

States: `submitting` (phases `prepared` → `calling` → `received`), `submission_unknown`, `queued`, `running`, `completed`, `failed`, `cancelled`. Terminal: `completed`, `failed`, `cancelled`.

Allowed transitions (anything else is rejected by the store's Lua and logged as `video_illegal_transition`):

```
submitting/prepared  -> submitting/calling | cancelled(pre_submit) | failed(submit_rejected|budget|validation)
submitting/calling   -> queued | running | completed | failed | submission_unknown | cancelled(provider)
submission_unknown   -> queued | running | completed | failed(submission_unresolved) | cancelled(provider)
queued               -> running | completed | failed | cancelled
running              -> completed | failed | cancelled
completed/failed/cancelled -> (no state change) ; artifact delivery, settlement and reconciliation substates may still change
```

Rules: a terminal state is never regressed by a later observation (R06, AT15); a `running` observation after `completed` updates `last_checked_at` only. `completed` requires provider terminal success AND at least one validated artifact copied (or `artifacts[].state=unavailable` with the job still `completed` and billable, R11). `failed` after `run_by` elapses sets `error.code=provider_timeout`, `upstream_may_continue=true`, `reconcile_state=pending`; a later provider success settles cost and records `late_success_at` but does not flip status (D11). `provider_state` always carries the raw provider value (`succeeded`, `expired`, `THROTTLED`, `IN_QUEUE`, ...). Unknown provider status strings are an adapter error: the job stays in its current state, `poll_failures` increments, and after `poll.max_unknown_status` (proposed 3) the job fails with `adapter_schema_error` and an alert.

Substates: `cancellation.state` ∈ `none|requested|confirmed|unsupported|not_available|not_applicable`; `reconciliation.state` ∈ `none|pending|resolved|unresolved`; per-artifact `state` ∈ `pending|copying|available|unavailable|expired|deleted`; `settlement_state` as above.

## 7. Submission worker, poller, idempotency and crash windows

### 7.1 Accept path (HTTP handler, synchronous)

1. Read body (existing `maxBodySize`), parse, reject unknown fields.
2. `rc.EndpointType = "video"`, `rc.Model = req.Model`; `peekKeyOrgID`, `resolveOrgConfig`; refuse if `org_id == ""` (403 `missing_org`) before anything else.
3. Resolve capability record (service, model, region, operation from roles). 400/503 as in 4.1. Validate request and provider_options against the record. Compute canonical request and fingerprint.
4. Media ingress (section 8) for URL inputs: fetch, verify, store to a temp blob (`video:v1:ingress:{id}:{n}` key in blob store) so the worker does not refetch from the client URL after the request returns. Inline data decoded and checked the same way.
5. Estimate cost upper bound from tariff (section 10). Missing tariff: 503 unless BYOK and `allow_unpriced_models`.
6. `engine.Process(ctx, rc, providerCall)` where `providerCall` is the store transaction below. Pre-plugins enforce auth, RBAC model permission, quota, budget check, validation, rate limit. The cache plugin is bypassed (`rc.Flags.NoCache`, or EndpointType exclusion, to be confirmed in code).
7. Store transaction (two steps, both idempotent and tested together):
   a. Reserve budget with the existing `BudgetStore.RecordSpend(org, level, key, period, model, +estimate)` for each configured level (atomic check-and-increment in Redis Lua; blocked → 429 `budget_exceeded`, nothing else written).
   b. One Lua script: `SET idem NX` → if the key already exists, return the existing id (the handler compensates the reservation with `RecordSpend(-estimate)` and answers with the existing job's state: 200/202, 409 on fingerprint mismatch, 410 on tombstone); else `HSET job ... status=submitting phase=prepared settlement_state=reserved`, `ZADD org index`, `ZADD submit queue`, `ZADD expire`.
   The gap between (a) and (b) is covered by AT09 (concurrent replay) and `TestAccept_CompensatesOnIdemCollision`; a crash between them leaves an over-reservation that the GC releases when no job references it (reservation ledger entry carries the intended job id).
8. Handler waits up to `submit.sync_wait` (proposed 5s, polling the hash every 250 ms, or via Redis keyspace notification if enabled) for `phase != prepared`, then responds with whatever the job says. Post-plugins (logging, otel, audit) run with `rc.Metadata["video_id"]`; cost is not known yet so the cost plugin writes nothing.

### 7.2 Submission worker (any replica)

Loop: `ZPOPMIN`-style claim via Lua on `video:v1:submit` with lease `SET video:v1:lease:{id} {replica} NX PX {lease_ttl}` (proposed 60s, renewed every 20s while working). Steps per job:

1. Load hash; verify `status=submitting`. If `phase=calling` (a previous owner died mid-call): go to reconciliation (7.4), never resubmit blindly.
2. If `phase=prepared` and `now > submit_by`: fail with `submit_deadline_exceeded`, release reservation (known-unused), done.
3. Check kill switch and provider enablement: if disabled, leave in `prepared` with `next_poll_at` backoff until `submit_by`, then fail `provider_disabled`.
4. Per-provider submit rate limit via `redisstate.RateLimiter` keyed `video:{provider}:{account_ref}:submit` (Seedance RPM is model-level, 429 on excess; see adapter notes). If limited: reschedule.
5. Write `phase=calling, attempt_id=ulid, attempt_started_at=now, correlation_token=<adapter-provided>` with fence check. This write happens BEFORE the HTTP request is built.
6. `adapter.Submit(ctx with connect/read deadlines, req)` → `SubmitResult{ProviderJobID, ProviderState, ProviderRequestID, RawStatus}`.
7. On success: Lua write `phase=received status=<normalized> provider_job_id=... submitted_at=now next_poll_at=now+base_interval run_by=submitted_at+run_deadline`, `ZADD due`, `ZREM submit`.
8. On definitive synchronous rejection (4xx validation, 401/403 provider auth, 404 model): `status=failed error_code=<normalized>`, release reservation (no upstream work exists), `upstream_may_continue=false`. Provider 401/403 is reported as `provider_access_denied` (configuration), not as the caller's fault (PRD).
9. On 429 / 5xx / connect timeout BEFORE any bytes of the request body were sent (Go `http.Client` reports this distinctly only for connect/TLS errors): `phase=prepared` again with backoff (safe: no call reached the provider). On read timeout, EOF, or any error after the request was written: `status=submission_unknown reconcile_state=pending reconcile_by=now+unknown_reconcile_deadline`, `ZADD reconcile`.
10. Release lease.

### 7.3 Poller (any replica)

Claims from `video:v1:due` where score ≤ now, with lease. Per-provider concurrency cap = Redis semaphore (`INCR`/`DECR` with TTL, proposed 8 per provider per cluster, 32 total) so client polls never fan out upstream (R07: status reads only read Redis). Per job: `adapter.Poll(ctx, ref)` → `Observation{ProviderState, Normalized, Progress*, Outputs[], Usage*, Error*, RetryAfter*, RawStatus}`.

- Interval: provider base (Seedance proposed 5s, Runway 5s minimum per OpenAPI "not more frequent than once every five seconds", Veo 10s per docs example, Luma 5s after a 30s initial wait per docs, Kling/MiniMax/xAI proposed 5s), exponential ×1.5 with full jitter to `poll.max_interval` (proposed 60s) while `queued`, reset to base on `running`.
- On provider 429: honour `Retry-After` if present, else backoff; counts as waiting, not failure. On 5xx/timeouts: `poll_failures++`, backoff; after `poll.max_consecutive_failures` (proposed 20) the job is marked `reconcile_state=pending` with an alert but not failed (upstream may be fine).
- On `now > run_by`: `failed/provider_timeout`, `upstream_may_continue=true`, `reconcile_by=now+timeout_reconcile_window` (proposed 2h), job moved to `reconcile` queue at a slow interval (proposed 5 min).
- On terminal success: write provider_state and usage first (so billing is captured even if the copy fails), then enqueue result copy (same worker, bounded `copy.deadline` proposed 10 min, retries 3 with backoff, re-polling the provider for a fresh URL when the adapter marks `output_url_refreshable`). Copy success → artifacts available, `status=completed`, settlement. Copy failure after retries → `status=completed`, artifact `unavailable`, settlement still performed (R11).
- On terminal failure: settle per adapter billability flag (`BillsOnFailure`), release only when the adapter says failed runs are not charged (Seedance: "successful output only" per pricing page; Veo: "You will not be charged if your video is blocked"); otherwise keep the reservation as `unsettled` and alert.

### 7.4 Reconciliation (submission_unknown and timeouts)

Per adapter capability (see contract): `Reconcile(ctx, job)` may use (a) a proven idempotent resubmit (`SubmitIdempotency=token`: Nova `clientRequestToken`; the same token is re-sent, AWS returns the same invocation), (b) a correlation query (`CorrelationLookup=by_token`: Kling `GET /tasks?external_task_ids=`), (c) a bounded list heuristic (`CorrelationLookup=heuristic`: Seedance `GET /contents/generations/tasks?filter.model=&filter.status=` matching `safety_identifier` and `created_at ∈ [attempt_started_at - 60s, attempt_started_at + submit_deadline]` and request shape; requires exactly one match, otherwise unresolved), or (d) none (fal, Luma, Veo, Omni, Runway, MiniMax, xAI, DashScope: no client token in the documented submit schema; Runway and MiniMax list endpoints could be used heuristically later but are not relied on in this revision).

Outcomes: found → attach `provider_job_id`, continue as `queued/running`; proven absent (a or b returns nothing) → back to `prepared` and resubmit; otherwise after `reconcile_by` → `failed/submission_unresolved`, `retry_safe=false`, `upstream_may_continue=true`, reservation kept as `unsettled`, alert `video_submission_unresolved` for operator action (admin endpoint to attach a provider job id manually is proposed for Phase 1b: `POST /admin/video/jobs/{id}/attach` with audit).

### 7.5 Crash windows (R13) with the concrete sequence

| Window | What the store shows after crash | Recovery | Paid-twice risk |
|---|---|---|---|
| W0: after HTTP accept, before worker claim | `submitting/prepared`, in submit queue | any replica claims after lease expiry; normal submit | none |
| W1: after `phase=calling` write, before request sent | `submitting/calling`, no provider id | treated as unknown (cannot distinguish from W2 without provider help); reconciliation per 7.4; Nova/Kling resolve exactly, Seedance heuristically, others fail unresolved | none by construction (no blind resubmit); cost is operator time |
| W2: request sent, response not received | same as W1 | same | none |
| W3: response received, before `phase=received` write | same as W1 (provider has a job) | same; heuristic finds it for Seedance when unique | none |
| W4: after `received` write, before HTTP reply | job `queued` with provider id; client got no reply | client replays `Idempotency-Key` → same id (A3) | none |
| W5: crash while polling | lease expires (≤60s), another replica claims; `run_by` persisted so the clock does not reset (AT08) | none |
| W6: crash during result copy | artifact `copying`; retry from provider URL (within provider expiry) or mark `unavailable` | none (same upstream job) |
| W7: crash between provider terminal write and settlement | `settlement_state=reserved` with terminal status; settlement job re-run idempotently (settled flag in Lua) | none; budget settles once |

Replica takeover is driven only by lease expiry; fencing tokens stop a paused-then-resumed worker from writing stale state (its fence is older).

### 7.6 Kill switch

`video.kill_switch: true` (config, hot-reloaded with the existing config reload path) or Redis key `video:v1:killswitch` set by an admin endpoint: submit returns 503 `video_submissions_disabled`; queued `prepared` jobs stay pending until `submit_by`; polling, result copy, cancel, delete and settlement continue (R16). Per-provider `enabled: false` behaves the same for that provider only.

## 8. Media ingress

- Allowed sources: `https://` URLs (no userinfo, no gateway hostnames, no query strings containing known secret parameter names), or inline base64. `http://` refused. Redirects: max 3, each hop re-checked; the dialer is `netguard.DialContext(net.Dialer{Timeout: connect}, false)` so post-DNS private/loopback/metadata addresses are refused on every hop (covers rebinding, AT04).
- Limits (per capability record, never a global maximum): count per role, encoded bytes (Seedance: 30 MB per image, 64 MB request; MiniMax: 30 MB image, 50 MB video, 15 MB audio), decoded pixel limit (`width*height ≤ 36 Mpx` proposed) and dimension ranges from the record (Seedance 300..6000 px, ratio 0.4..2.5), video duration via container parse (ftyp/moov scan only; no full decode in Phase 1; mark `decoded_check=container_only`), audio duration via header parse.
- Content type is sniffed from bytes (`http.DetectContentType` plus magic for webp/heic/mp4/mov/wav/mp3); the declared `media_type` must match the sniffed family or 400 `media_type_mismatch`. Decompression bombs are not applicable to raw images; animated GIF frame count is capped (proposed 1 frame accepted when the provider expects a still).
- Fetch deadline per asset (proposed 20s), total ingress budget per request (proposed 60s), streamed to blob with a hard byte cap; no buffering beyond the cap.
- Forwarding to providers: assets are re-hosted only when the provider accepts base64 or uploads; otherwise the original client URL is forwarded as-is after validation (the provider fetches it). Gateway credentials are never placed in URLs. Signed client URLs have their query strings redacted in logs (`privacy.Redactor` pattern `[?&](X-Amz-Signature|sig|token|signature)=...`).
- Supplier-side exposure: adapters declare `OutputACL ∈ private_token|private_signed|public_default|unknown`; `public_default` and `unknown` block enablement unless `video.providers.<id>.acknowledge_public_output: true` is set by the operator, which the PRD reserves for Nikhil's explicit authorization (hard stop, not decided here).

## 9. Output copy, retrieval, retention

- Copy runs through netguard too (provider CDN hosts are public). Download is streamed to the blob store with `Content-Length` and a cap (`artifacts.max_bytes` proposed 2 GiB for 4K/30s). Validation: non-empty, sniffed `video/mp4` (or `video/quicktime`), `moov` atom present; `ffprobe` is optional (`artifacts.probe_command`) and off by default.
- Blob key: `video/{org}/{job}/{index}.mp4`; S3 backend prefix is operator-owned (`artifacts.s3.bucket/prefix`); object ACL private; server-side encryption header set when configured. Disk backend path `artifacts.disk.root/{org}/{job}/`.
- Retrieval: gateway streams with range support from blob; authorization = same org as job and job not deleted and artifact `available` and `now < expires_at`. No presigned URLs by default; optional `artifacts.presign.enabled` with ≤ 15 min TTL (proposed) only for S3 with a private bucket, disclosed in the response as `delivery: presigned`.
- Retention: `artifacts.ttl` default 24h after `completed_at` (D05); `expire` zset sweeper (any replica, leased) deletes blobs and marks artifacts `expired`. Job metadata TTL 30d (D05) unless `settlement_state ∈ reserved|unsettled` or `reconcile_state=pending`, in which case the record and the idempotency key are retained (R12). Provider URL expiry (Seedance 24h/100 downloads, Veo 2 days, Runway 24-48h, Luma presigned 1h, Kling 30d, xAI unverified) is separate and exposed only as `artifacts[].provider_expiry_hint` (optional).
- No regeneration on expiry (410 `output_expired`), no re-download after local delete.

## 10. Budget, usage units, tracing, logging

- Units (`internal/video/tariff`): `video_tokens` (BytePlus), `output_seconds` (Veo, Luma per-video grid converted, MiniMax, Wan, HappyHorse, xAI, Pika), `input_seconds` (MiniMax, Wan reference), `images` (xAI image input, MiniMax image allowance), `credits` (Runway, 12 credits/s gen4.5 at USD 0.01/credit), `multimodal_tokens` (Omni), `storage_bytes` (gateway artifacts; priced only in managed mode). Each usage line: `{unit, quantity, source: estimate|provider_reported|reconciled, tariff_revision, currency, usd}`.
- Estimate (reservation): per-record formula with the model maximum when auto values are allowed. BytePlus: `tokens = (input_video_s + output_s) * W * H * fps / 1024` (pricing page formula, labelled an estimate by BytePlus) with the minimum-consumption rule applied when the input contains video; `usd = tokens/1e6 * rate(resolution, input_has_video)`. Rates and formula from `pricing.md` recovered 2026-10-02 (section 2 of the adapter contract). Example upper bound for 1080p 16:9 (1920×1080) 8s at 24fps without video input: 1920*1080*24*8/1024 = 388,800 tokens → 388,800/1e6*11.7 = USD 4.55 (computed here, not a BytePlus quote; BytePlus's own example says 1080p/16:9/5s = USD 2.843 ≈ 0.569/s, consistent within rounding of their minimum rules).
- Settlement: provider-reported usage (`usage.completion_tokens` for BytePlus; `usage.output_seconds/input_seconds/input_image_count` for MiniMax; Runway `cost` credits on terminal task; Veo output seconds from request; Omni token usage from interaction) × tariff → `settled_micros`; delta applied through `BudgetStore.RecordSpend(org, level, key, period, model, delta)` for every level the budget plugin uses (`recordOrgHierarchicalSpend` is the reference; the lifecycle calls the same helper extracted into a function). Then a settlement `RequestContext` is built (`EndpointType=video`, `Model`, `Metadata{org_id, auth_key_id, key_type, video_id, video_cost_usd}`) and `engine.RunPostPlugins` executes so cost → credits → logging/otel/audit see one terminal record. The budget post-plugin must skip when `rc.Metadata["video_settlement"]=="direct"` to avoid double recording (small guard in `budget.ProcessResponse`; tested in AT21).
- Managed keys (`key_type=managed`): blocked at submit with 503 `managed_tariff_not_configured` until a tariff is approved (D06 hard stop). No credits deduction path is exercised in this revision.
- Per-org and per-upstream-account concurrency: `video:v1:active:{org}` and `video:v1:active:{provider}:{account}` counters (Lua INCR with limit, DECR at terminal) enforce `max_active_jobs` (proposed org default 10, provider default from docs: BytePlus "maximum concurrent tasks" is model-level and account-wide, value not published on the recovered page; configure per account).
- Tracing: spans `video.submit`, `video.provider.submit`, `video.provider.poll`, `video.result.copy`, `video.settle`, all carrying `video_id`, `org_id`, `service`, `model_id`, `provider_job_id`, `attempt_id`, state; linked by `trace_id` stored on the job (`rc.TraceID` at accept). Logs contain correlation IDs and safe codes only; prompts, inline media, provider URLs with signatures and API keys never appear (redactor + explicit field allowlist; AT23).
- Metrics (Prometheus registry): `video_jobs_total{service,model,status}`, `video_state_duration_seconds{state}`, `video_poll_requests_total{service,outcome}`, `video_submission_unknown_total`, `video_unresolved_total`, `video_result_copy_failures_total`, `video_reserved_micros`, `video_unsettled_micros`, `video_queue_age_seconds`, `video_lease_takeovers_total`.

## 11. Configuration schema (proposed)

```yaml
video:
  enabled: false                      # master switch; off by default
  kill_switch: false                  # blocks new submissions only
  store: redis                        # redis | memory
  allow_non_durable_store: false      # memory store refused unless true (dev/test only)
  encrypt_requests: false             # when true requires video.request_encryption_key (secret://)
  submit:
    sync_wait: 5s
    deadline: 30s                     # submit_by = created_at + deadline
    unknown_reconcile_deadline: 15m
    lease_ttl: 60s
  poll:
    max_concurrent_total: 32
    per_provider_max_concurrent: 8
    max_interval: 60s
    max_consecutive_failures: 20
    max_unknown_status: 3
    timeout_reconcile_window: 2h
  media:
    max_inline_bytes: 20971520
    fetch_timeout: 20s
    total_ingress_timeout: 60s
    max_decoded_pixels: 36000000
  artifacts:
    backend: disk                     # disk | s3
    ttl: 24h
    max_bytes: 2147483648
    disk: { root: /var/lib/agentcc/video }
    s3: { bucket: "", prefix: "video/", region: "", access_key: "${AWS_ACCESS_KEY_ID}", secret_key: "${AWS_SECRET_ACCESS_KEY}", sse: "AES256" }
    presign: { enabled: false, ttl: 15m }
  retention:
    job_metadata: 720h                # 30d
    idempotency: 720h
  limits:
    org_max_active_jobs: 10
    org_submit_rpm: 60
  providers:
    byteplus:
      enabled: false
      api_key: "${ARK_API_KEY}"       # or secret://...
      base_url: https://ark.ap-southeast.bytepluses.com/api/v3
      region: ap-southeast-1
      account_ref: byteplus-main      # label for accounting and concurrency
      models: [dreamina-seedance-2-5-260628, dreamina-seedance-2-0-260128]   # must exist in the capability registry
      limits: { submit_rpm: 10, poll_qps: 10, max_active_tasks: 5 }           # operator values; BytePlus model-level caps are account-specific
      deadlines: { connect: 10s, read: 30s, submit: 30s, run: 2h, reconcile: 15m }
      poll: { base_interval: 5s }
      allow_unpriced_models: false
      tariff_revision: byteplus-2026-10-02
      safety_identifier_mode: org_key_hmac   # none | org_key_hmac
      execution_expires_after: 7200          # seconds, sent to BytePlus; must be >= deadlines.run
      acknowledge_public_output: false
    google_gemini:
      enabled: false
      credential_ref: provider:gemini-main  # reuse an existing providers[] entry
      ...
```

Validation at load: `enabled` providers must have credentials, non-empty `models`, all deadlines and limits set (R07 "enablement fails if operator limits/deadlines are missing"), a tariff revision or `allow_unpriced_models`, and `OutputACL` not public/unknown unless acknowledged. `video.enabled` with `store: redis` requires `redis.enabled` (same rule as `license_auth`, `config.go:1281`).

## 12. Migration and compatibility

- Existing scaffold jobs live only in process memory; a restart already loses them. No data migration. Any Redis job hash with empty `org_id` (should not exist) is quarantined: not listed, 404 on read, never queued (AT25).
- Breaking, intentional (R20, with changelog + rollout notice): `Idempotency-Key` required on submit; `DELETE` no longer reports `cancelled`; `status` vocabulary adds `submitting`, `submission_unknown`, `running`; the scaffold constant `in_progress` was never emitted by any code path (`handlers_video.go` only writes `queued`), so removing it breaks no observable behavior; `n > 1` rejected unless the model natively supports it (scaffold accepted up to 4 without doing anything).
- Preserved: paths `/v1/videos`, `/v1/videos/{id}`, list query params `limit/offset/order/status/model`, fields `id, object, status, model, created_at, expires_at, completed_at, error, usage, cost, metadata`. `prompt` is no longer echoed in status responses (privacy; the scaffold echoed it); flagged as a deliberate change.
- README claims "video generation shipped" (`agentcc-gateway/README.md:15,46,321,471`); the docs stage must replace them with the support matrix states (documented / implemented / deployed / gated / smoke-verified) per R18.

## 13. OSS / EE / managed matrix (D06)

| Capability | OSS (CE image) | EE | Managed cloud |
|---|---|---|---|
| Adapters, durable lifecycle, idempotency, tenant isolation, media safety, local budget/usage, traces | Shipped, operator/BYOK keys | Same | Same code; managed keys blocked at submit until tariff exists |
| Existing EE gates (license_auth, enterprise governance) | untouched | untouched | untouched |
| Managed provider spend, credits deduction for video, output hosting charges | not shipped | not shipped | HARD STOP: requires approved tariff (pricing owners) |
| No `license_auth`/EE import in `internal/video/**` or `internal/providers/video/**` (test asserts the import graph) | | | |

## 14. Security threat list

| Threat | Control |
|---|---|
| SSRF via input URLs, callback URLs, provider base URLs | netguard dialer on every fetch; https only; redirect re-check; no callback URLs accepted from clients in this revision (webhooks are operator-configured and off by default) |
| Cross-tenant read/cancel/delete | org from key only; every store read is `GetForOrg`; list index per org; 404 indistinguishable |
| Ownerless job assignment | empty org quarantined at store level |
| Double spend on retry/crash | idempotency Lua; single submitter; no blind resubmit; reconciliation per proven capability |
| Budget overrun under concurrency | atomic reserve via budget Lua; active-job counters |
| Secret leakage | keys resolved via secrets layer; never in URLs; redactor on provider error bodies and URLs; `provider_options` cannot carry credentials (schema allowlist) |
| Malicious media | sniffing, size/pixel caps, container-only parse, no decode of untrusted video in-process |
| Provider CDN exposure | `OutputACL` gate; private gateway copy; no provider URLs in responses |
| Replay of webhooks | webhooks disabled by default; when enabled per adapter, require documented signature (fal Ed25519/JWKS per P21 sources; BytePlus and Kling callbacks have no documented auth and remain polling-only) |
| Lease split-brain | fencing token checked in Lua |
| Log/metric cardinality and privacy | fixed label sets; prompts never logged |
| Config mistakes | load-time validation, `enabled:false` defaults, kill switch |

## 15. Rollout and rollback

Rollout: (1) merge behind `video.enabled=false` (no behavior change; existing scaffold routes keep returning 501 `not_configured` when disabled, exactly as today); (2) enable store + Seedance in an isolated environment with a test key and spend ceiling (paid smoke is a separate Nikhil approval, AT27); (3) pilot allowlist orgs; (4) per-provider enablement as cohorts pass the common suite + smoke.

Rollback: set `video.kill_switch=true` (no new spend, accepted jobs still observable, cancel/delete/results work); then `video.providers.<id>.enabled=false`; then `video.enabled=false` (routes return 503 `video_disabled`; Redis records remain for later recovery; never purged by rollback). Reverting the binary without clearing Redis is safe: the old scaffold ignores the `video:v1:` keyspace.

## 16. Observability and alerts (proposed thresholds)

Alerts: `video_submission_unknown_total` > 0 in 10 min (warn), `video_unresolved_total` > 0 (page), `video_queue_age_seconds` p95 > `submit.deadline` (warn), poll failure ratio > 20% per provider over 5 min (warn), `video_result_copy_failures_total` > 0 (warn), `video_unsettled_micros` > configured ceiling (page), Redis circuit breaker open while `video.enabled` (page), lease takeovers > 5/min (warn).

## 17. Workload assumptions and measurable targets (all proposed)

Assumptions: pilot 10 submits/s peak, 100 concurrent active jobs, provider generation 30s..6min (Veo doc: 11s..6min; Luma: under 2 min typical for 5s/720p), artifacts 5..500 MB, 24h retention → ≤ 100 jobs × 500 MB = 50 GB worst case per day per org pilot.

Targets (PRD success criteria, to be validated by load tests, not promised): submit p95 < 500 ms excluding media fetch and provider time (Redis Lua + budget + plugins; expected dominant cost is media fetch which is excluded); status p95 < 300 ms (single `HGETALL`); terminal reflected within 2 poll intervals + copy time; 100% job survival across replica kill tests; zero duplicate submissions in AT09/AT11 runs of ≥ 1,000 iterations.

Expected bottleneck: result copy bandwidth and blob write (not Redis): 100 concurrent 200 MB copies = 20 GB in flight. Mitigation: `copy.max_concurrent` (proposed 8 per replica) and streaming to blob with no buffering. Second bottleneck: provider poll QPS caps (BytePlus retrieve 20 QPS account-level, list 1 QPS) shared across replicas, handled by the Redis rate limiter. Load tests: `cmd/loadtest` extended with a video scenario against httptest fake providers with configurable latency; assert p95s, no duplicate submits, Redis ops per job ≤ 25 (proposed budget), and memory flat during copies.

## 18. What would change this design

- Provider idempotency tokens from BytePlus → remove heuristic reconciliation.
- A requirement for durable job history > 30d or analytics → Postgres store behind `Store` (RFC).
- Approval of managed tariff → enable credits path + storage pricing (already shaped, blocked by config).
- Verified webhook authentication for BytePlus/Kling/MiniMax → optional faster path; polling remains the fallback.

## Open questions for Nikhil

1. Nova Reel requires a customer S3 output bucket in us-east-1 and AWS billing; which account/bucket, and is the gateway allowed to write there? (Hard stop: new spend/account.)
2. fal (Pika) and PixVerse default-public outputs: block (default) or authorize an exact exposure scope for the pilot?
3. Managed tariff: no managed video until pricing owners approve; confirm the pilot is BYOK-only.
