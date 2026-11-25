# Video generation operator runbook

How to enable, watch, degrade and recover the `/v1/videos` lifecycle. Based
on the TH-8088 architecture (§5 persistence, §7 lifecycle and crash windows,
§9 retention, §15 rollout and rollback, §16 alerts) and the implementation
plan (P1-11 observability, P1-12 admin endpoints). Status: **as designed,
not yet exercised on a running build**. Thresholds marked "proposed" have
not been tuned against real traffic.

Admin paths below are written as the architecture names them
(`/admin/video/...`). The gateway's existing admin routes live under
`/-/admin/` behind the admin token; the final prefix is pinned at
implementation.

## 1. Levers, from softest to hardest

| Lever | Effect | Scope | How |
|---|---|---|---|
| Kill switch | New submissions answer 503 `video_submissions_disabled`. Jobs already accepted keep polling, copying results, settling cost; cancel and delete keep working. Accepted-but-unsubmitted jobs wait until their `submit_by`, then fail `submit_deadline_exceeded` with the reservation released. | All providers, all replicas | `video.kill_switch: true` in config, or set the Redis key `video:v1:killswitch` through `POST /admin/video/killswitch` (takes effect on every replica without restart). Env override `AGENTCC_VIDEO_KILL_SWITCH`. |
| Provider disable | Same as the kill switch, for one service: submissions for it answer 503 `provider_not_configured`. | One provider | `video.providers.<id>.enabled: false` |
| Video disable | All video routes stop serving (501 `not_configured` as today, or 503 `video_disabled`, see api-reference). The worker stops. Redis records remain untouched. | Everything | `video.enabled: false`, or `AGENTCC_VIDEO_ENABLED=false` |
| Binary rollback | Old scaffold ignores the `video:v1:` keyspace. | Everything | Deploy the previous image; do not flush Redis. |

Never purge Redis video keys as part of a rollback. Reservations,
tombstones and reconciliation state are the only record of paid upstream
work.

## 2. Rollout order

1. Deploy with `video.enabled: false`. Nothing changes for callers.
2. Confirm Redis: `redis.enabled: true`, AOF `appendfsync everysec` or RDB,
   `maxmemory-policy noeviction`. Check the startup log for the eviction
   warning (proposed check: `CONFIG GET maxmemory-policy`). If `CONFIG` is
   not permitted for the gateway's Redis user, verify it by hand.
3. Confirm artifact storage: a writable `artifacts.disk.root` on a shared
   mount, or an S3 bucket the gateway can PUT, GET (with `Range`) and
   DELETE under `artifacts.s3.prefix` only.
4. In an isolated environment with a test API key and an explicit budget
   ceiling on that key or organization, set `video.enabled: true`,
   `kill_switch: true`, and `providers.byteplus.enabled: true`. Verify
   `/v1/videos` now answers 503 `video_submissions_disabled` (not 501).
5. Flip `kill_switch: false`. The first submission is the first paid
   request; paid smoke (AT27) is a separate approval and is not part of this
   rollout.
6. Pilot: allowlist organizations through the existing RBAC model
   permission `models:byteplus/<model>` and `auth_allowed_providers`.
7. Further providers are enabled one cohort at a time, after each passes
   the shared contract suite and its own approved smoke.

Rollback is the reverse: `kill_switch: true` → `providers.<id>.enabled:
false` → `video.enabled: false` → previous binary. Each step keeps
observability of what already ran.

## 3. What to watch

Metrics (Prometheus registry, P1-11):

| Metric | Meaning |
|---|---|
| `video_jobs_total{service,model,status}` | Terminal counts |
| `video_state_duration_seconds{state}` | Time spent per state |
| `video_poll_requests_total{service,outcome}` | Upstream polls by outcome |
| `video_submission_unknown_total` | Jobs that entered `submission_unknown` |
| `video_unresolved_total` | Jobs that ended `failed/submission_unresolved` |
| `video_result_copy_failures_total` | Artifacts that ended `unavailable` |
| `video_reserved_micros` | Budget currently reserved, not settled |
| `video_unsettled_micros` | Reservations the gateway could not settle (unknown billability) |
| `video_queue_age_seconds` | Age of the oldest `prepared` job |
| `video_lease_takeovers_total` | Jobs claimed by a replica after another's lease expired |

Alerts (§16, all thresholds proposed):

| Alert | Condition | Severity | First action |
|---|---|---|---|
| Submission unknown | `video_submission_unknown_total` increases within 10 min | warn | Section 5 |
| Unresolved submission | `video_unresolved_total` increases | page | Section 5 |
| Queue age | `video_queue_age_seconds` p95 > `video.submit.deadline` | warn | Worker not claiming: check replicas, Redis, kill switch, provider submit rate limit |
| Poll failures | failed polls > 20% of polls per provider over 5 min | warn | Provider outage or credential problem; jobs are not failed by this alone |
| Copy failures | `video_result_copy_failures_total` increases | warn | Section 7 |
| Unsettled exposure | `video_unsettled_micros` > configured ceiling | page | Section 6 |
| Redis breaker open | Redis circuit breaker open while `video.enabled` | page | Section 8 |
| Lease churn | `video_lease_takeovers_total` > 5/min | warn | Replicas crashing or paused longer than `lease_ttl`; check GC pauses, OOM kills |

Traces: spans `video.submit`, `video.provider.submit`, `video.provider.poll`,
`video.result.copy`, `video.settle`, all tagged with `video_id`, `org_id`,
`service`, `model_id`, `provider_job_id`, `attempt_id`. Logs carry
correlation IDs and safe codes only; prompts, media, keys and signed URLs
never appear.

Admin reads: `GET /admin/video/capabilities` (registry export; what this
build can accept), `GET /admin/video/unsettled` (jobs with
`settlement_state` `unsettled` or `reserved` past terminal).

## 4. How the worker behaves

- Every replica runs the same lifecycle worker. Work is claimed from Redis
  queues with a lease (`video.submit.lease_ttl`, default 60s, renewed every
  20s). Every write carries a fencing token; a replica that was paused and
  resumes with a stale lease cannot overwrite newer state.
- Submission is a single path: the handler persists and enqueues; the
  worker writes `phase=calling` **before** the HTTP request to the
  provider, then `phase=received` with the provider job ID after. Those two
  writes bound the crash window.
- Polling honours `Retry-After`, backs off ×1.5 with jitter to
  `poll.max_interval`, and is capped cluster-wide
  (`poll.max_concurrent_total`) and per provider. Client status reads never
  reach the provider.
- Budget: an upper-bound estimate is reserved at accept through the
  existing budget store; settlement applies the delta at terminal state.
  The reservation is released only when the gateway knows no upstream
  charge exists (synchronous rejection, pre-submit cancel, provider-
  confirmed cancel of a queued Seedance task).

Replica loss: a job mid-flight is picked up by another replica once the
lease expires (≤ 60s). `run_by` and `submit_by` are persisted, so a
takeover does not reset deadlines. Expect `video_lease_takeovers_total` to
tick once per in-flight job during a rolling restart; that is normal.

## 5. `submission_unknown` and `submission_unresolved`

A job is `submission_unknown` when the provider request was written to the
wire and no usable response was persisted (read timeout, EOF, crash
between `calling` and `received`). The gateway never resubmits blindly.

What happens automatically (`adapter.Reconcile`, per provider capability):

| Provider | Method | Outcome quality |
|---|---|---|
| BytePlus Seedance | Lists tasks filtered by model and status within the attempt window and matches `safety_identifier` (requires `safety_identifier_mode: org_key_hmac`). Exactly one match → attached. Zero or several → unresolved. Listing is capped at 1 QPS by BytePlus, so this is slow. | heuristic |
| Kling (future) | `GET /tasks?external_task_ids=<video_id>` | exact |
| Nova (future) | Re-invoke with the same `clientRequestToken` | exact |
| Veo, Omni, Vertex, Runway, Luma, MiniMax, DashScope, xAI, fal (future) | None documented | unresolved after the deadline |

After `video.submit.unknown_reconcile_deadline` (default 15 min) without a
match the job becomes `failed` with `error.code: submission_unresolved`,
`retry_safe: false`, `upstream_may_continue: true`, the reservation is kept
as `unsettled`, and the page-level alert fires.

Operator procedure for an unresolved job:

1. Read the job: `video_id`, `org_id`, `service`, `model_id`,
   `attempt_started_at`, `correlation_token` (the `safety_identifier`
   value), `account_ref`. Nothing here is secret.
2. In the provider console or API, look for a task created in
   `[attempt_started_at − 60s, attempt_started_at + submit deadline + 60s]`
   for that model. For BytePlus the list endpoint returns
   `safety_identifier`, `created_at`, `model`, `status` and `error`; the
   task record exists for 7 days, so do this within the week.
3. Exactly one candidate: attach it with
   `POST /admin/video/jobs/{video_id}/attach` body
   `{"provider_job_id": "cgt-..."}` (proposed Phase 1b endpoint, audited).
   The job is polled again under the original organization and settles
   cost normally. Whether the caller-visible status returns to
   `queued`/`running` or stays `failed/submission_unresolved` with late
   settlement is not pinned by the architecture for attaches after the
   reconcile deadline; D11 says a returned failure is not silently rewritten
   as success. Confirm at implementation and update this step.
4. No candidate and you are confident the provider never received it:
   leave the job failed, release the reservation through the unsettled
   procedure (section 6), and tell the caller to submit again with a new
   `Idempotency-Key`. `reconcile.allow_absent_resubmit` stays `false`;
   automatic resubmission on "absent" is not safe for Seedance.
5. Several candidates: do not guess. Settle cost for all of them against the
   organization after confirming with the provider which ones are yours;
   attach none.

Until the attach endpoint ships there is no supported way to perform step
3; a direct Redis edit of the job hash is not an approved procedure and is
not described here. Leave the job unresolved and settle cost through
section 6.

## 6. Unsettled reservations

`settlement_state: unsettled` means the job is terminal but the gateway
could not decide whether the provider billed it. Causes: provider failure
where billability is `Unknown` (BytePlus failed tasks until confirmed on
smoke), `provider_timeout`, `submission_unresolved`, a cancel that the
provider did not confirm before the job ran.

- `GET /admin/video/unsettled` lists them with `reserved_micros`.
- Check the provider's usage or billing record for the `provider_job_id`
  (or the correlation token when there is none).
- Billed: settle at the provider-reported usage. Not billed: release.
  Both actions go through the settlement path (idempotent, audited), never
  by editing the budget hash directly.
- Reservations whose job hash is gone (crash between budget reserve and
  job write, W-gap in §7.1) are released by the GC when no job references
  the ledger entry.

The reservation stays counted against the organization's budget until you
act. That is the intended fail-safe; it keeps unknown exposure visible.

## 7. Output copy failures and retention

- A `completed` job with `artifacts[].state: unavailable` means the provider
  succeeded and billed, but the copy failed after 3 bounded retries within
  `copy.deadline` (10 min). Callers get 410 `output_unavailable`. The
  gateway never regenerates. Check egress to the provider CDN, blob backend
  errors, and `artifacts.max_bytes`.
- Seedance URLs are valid 24h and at most 100 downloads; re-polling does
  not refresh them. Once the provider URL is dead, the artifact cannot be
  recovered.
- Retention sweeper (`video:v1:expire`, any replica, leased): deletes blobs
  at `expires_at` (`artifacts.ttl`, default 24h after `completed_at`) and
  marks artifacts `expired`; deletes job hashes at `retention.job_metadata`
  (30d) unless settlement or reconciliation is open. Idempotency keys
  follow `retention.idempotency` and are refreshed while unresolved.
- Disk backend: watch free space under `artifacts.disk.root`; worst case
  per organization pilot is roughly 50 GB/day (§17 assumption, 100 jobs ×
  500 MB).
- Local delete by the caller purges the blob immediately and leaves a
  tombstone; the sweeper removes the tombstone at the retention horizon.

## 8. Redis unavailable

The video subsystem fails closed. Nothing is served from memory.

| Symptom | Cause | Action |
|---|---|---|
| Every video route returns 503 `video_store_unavailable` | Redis down or the gateway's circuit breaker open | Restore Redis; the breaker closes on its own. Jobs resume from their persisted state; leases expire and are re-claimed. |
| Submissions 503, status reads fine | Breaker half-open or write errors | Same; check Redis memory and `noeviction` |
| Jobs stuck `submitting/prepared`, queue age alert | Workers cannot claim | Check Redis, then the kill switch, then provider submit rate limits |
| After recovery, many `provider_timeout` failures | Outage longer than `deadlines.run` | Expected; reconciliation polls for 2h (`poll.timeout_reconcile_window`) to capture cost. Review unsettled list afterwards. |
| Keys missing after a Redis restart | No AOF/RDB, or eviction | Paid work may be untracked. Reconcile against provider billing by `account_ref` for the window; this is manual and should never be needed if persistence is configured. |

Check before declaring recovery: `redis.address` reachable from every
replica, `maxmemory-policy noeviction`, AOF enabled, and
`video_lease_takeovers_total` settled back to zero.

## 9. Provider credential and quota problems

- Jobs failing with `provider_access_denied`: the operator key is invalid,
  the model is not activated for the account, or the account is not
  entitled. Fix in the ModelArk console; the caller's gateway key is not at
  fault. No budget is consumed (synchronous rejection releases the
  reservation).
- Provider 429 on submit before any bytes are sent: the job goes back to
  `prepared` and retries within `submit_by`. On poll: honoured as waiting,
  not failure. Lower `limits.submit_rpm`/`poll_qps` if this is constant;
  BytePlus caps retrieval at 20 QPS and listing at 1 QPS per account, shared
  by every gateway using that account.
- Rotating the provider key: update the secret and restart the replicas
  (the architecture assumes the existing config reload path for the kill
  switch; whether provider credentials hot-reload is not established).
  In-flight jobs keep polling with the new credential (same account).
  Changing `account_ref` is not a rotation; pinned jobs keep the old label
  for accounting.

## 10. Things that are not operator actions

- Marking a `failed` job `completed` by hand. Late success is recorded as
  `late_success_at` and settled; the public status does not change (D11).
- Deleting the provider task to "cancel" a running Seedance job. Only
  `queued` tasks can be cancelled upstream; DELETE on a terminal task erases
  the record reconciliation needs. The gateway never calls provider delete
  for a local delete.
- Setting `acknowledge_public_output: true` to make a config load.
- Flushing `video:v1:*` to clear an alert.
