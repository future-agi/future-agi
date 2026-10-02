# TH-8088 implementation pins (Rick, 2026-10-02)

Decisions taken by Rick after the docs stage (docs-writer card t_6f8f2351 notes N1–N10) and the first Codex pass. These bind every implementation batch; they refine the architecture, they do not reopen it.

| # | Topic | Pin |
|---|---|---|
| N1 | Secret URIs | No generic `secret://` scheme exists. Credentials use `${ENV}` expansion or the schemes `internal/secrets/resolver.go` already supports (`vault://`, `aws-sm://`, `gcp-sm://`, `azure-kv://`). Remove `secret://` from config examples. |
| N2 | `video.enabled=false` | Keep today's behaviour: routes answer **501 `not_configured`** when the video store is nil. `503 video_disabled` is not introduced. |
| N3 | Rate-limit error code | Reuse the existing ratelimit plugin code **`rate_limit_exceeded`** (and `budget_exceeded` / `org_budget_exceeded` from the budget plugin). Do not invent `rate_limited`. |
| N4 | Admin route prefix | Admin video routes live under the existing **`/-/admin/video/...`** prefix with the existing admin auth. |
| N5 | README matrix rows | P01–P04 rows read `implemented` only because the README section lands in the same PR as the BytePlus adapter. |
| N6 | Config keys beyond §11 | Add `copy.deadline` (10m), `copy.max_concurrent` (8), `reconcile.allow_absent_resubmit` (false), `video.correlation_secret` (required when any provider uses `safety_identifier_mode: org_key_hmac`; `${ENV}`). **Not in Phase 1:** `encrypt_requests` / `request_encryption_key` — canonical requests are stored plain in Redis under the metadata retention; documented as a follow-up. |
| N7 | Operator attach after terminal failure | `POST /-/admin/video/jobs/{id}/attach` is accepted only while `reconcile_state ∈ {pending, unresolved}`. If the job is still `submission_unknown`, it resumes as `queued/running` normally. If it already failed with `submission_unresolved`, status stays `failed` (D11): polling resumes for **settlement only**, `late_success_at` is recorded, no artifact is delivered. Audited. |
| N8 | Kill switch | Config value is read at startup only. Runtime toggling is the Redis key `video:v1:killswitch` via the admin endpoint. Credential rotation requires a restart. No general config hot-reload is assumed. |
| N9 | Doc placement | `agentcc-gateway/docs/video-generation/` (README links point there). |
| N10 | INSTALLATION.md | Root `INSTALLATION.md` gets a short gateway Redis note (AOF `appendfsync everysec` or RDB, `maxmemory-policy noeviction`) in the docs fold-in step. |
| C1 | Budget reservation primitive | Reservation uses the existing **`BudgetStore.CheckAndRecordSpend`** (limit-checking Lua, `checkAndRecordScript`) with the positive estimate; compensation and settlement use `RecordSpend` with a signed delta. `RecordSpend` alone does not enforce a limit. |
| C2 | BytePlus minimum charge with video input | The minimum-token rule for requests containing video input is not recovered from the pricing page. Estimator **fails closed**: a request with any `reference_video` input is `ErrUnpriced` unless the operator sets `providers.byteplus.min_tokens_with_video_input` (documented as an operator-asserted value). |
| C3 | Go build cache under the Codex sandbox | Tests run with `GOCACHE` under `$TMPDIR` (writable in the sandbox). Never commit a build cache; `.video-build-cache/` must not exist in the tree. |
