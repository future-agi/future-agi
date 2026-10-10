# Video generation API reference

Contract for `/v1/videos` as designed in the TH-8088 architecture (API
surface §4, job record §5, state machine §6, lifecycle §7). Status of this
page: **as designed, not yet verified against a running build**. Where the
current scaffold at dev `ce6af27` behaves differently, the difference is
called out. Nothing here is a statement that a provider works end to end;
LIVE smoke is pending authorization.

## Conventions

- Authentication: the same `Authorization: Bearer <key>` as every other
  route. The organization is derived from the key's metadata, never from
  the request body. A key without an organization gets 403 `missing_org`.
- IDs: `video_<ulid>` (26-character Crockford base32, time-sortable).
- Errors: the existing envelope
  `{"error":{"type","code","message","param"}}`. `param` names the offending
  field when one exists.
- Unknown JSON keys are rejected with 400 `unknown_field`.
- Timestamps are Unix seconds.
- `model` is service-qualified: `byteplus/dreamina-seedance-2-5-260628`.
  The gateway resolves it against the capability registry and pins
  `service`, `model_id`, `region` and `capability_revision` on the job.
  Later alias changes never move an existing job.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/videos` | Submit a generation (requires `Idempotency-Key`) |
| `GET` | `/v1/videos/{video_id}` | Status (reads the gateway store only) |
| `GET` | `/v1/videos/{video_id}/content?artifact_index=0` | Stream the gateway copy of the result |
| `GET` | `/v1/videos` | List jobs in your organization |
| `POST` | `/v1/videos/{video_id}/cancel` | Best-effort upstream cancellation |
| `DELETE` | `/v1/videos/{video_id}` | Local deletion only; does not cancel |

`/content` and `/cancel` do not exist at dev `ce6af27`; they arrive with the
Phase 1 change.

## Submit: `POST /v1/videos`

Headers:

| Header | Required | Rule |
|---|---|---|
| `Authorization` | yes | Bearer API key |
| `Idempotency-Key` | yes | 1 to 255 printable characters. Missing: 400 `missing_idempotency_key`. See "Idempotency" below. |
| `Content-Type` | yes | `application/json` |

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

| Field | Type | Rules |
|---|---|---|
| `model` | string | Required. Service-qualified ID present in the capability registry, else 400 `unsupported_model`. Retired IDs (OpenAI Sora family) answer 400 `unsupported_model` with `reason: retired`; they are never re-routed. |
| `prompt` | string | Required unless the model documents a prompt-less mode. Limit is model-specific and measured in the upstream unit (bytes, characters or UTF-16 units) from the capability record. |
| `inputs[]` | array | Typed media. `role` ∈ `first_frame`, `last_frame`, `reference_image`, `reference_video`, `reference_audio`. Allowed roles, counts, byte, pixel and dimension limits come from the capability record for the exact model, never from a global maximum. |
| `inputs[].media_type` | string | Declared type. Must match the sniffed type family, else 400 `media_type_mismatch`. |
| `inputs[].source` | object | Exactly one of `url` (https only; no userinfo; no private, loopback, link-local or metadata addresses after DNS resolution, re-checked on each of at most 3 redirects) or `data` (base64, bounded by `video.media.max_inline_bytes`). |
| `duration_seconds` | integer | Finite integer where the model requires one. `-1` (auto) is accepted only when the record marks `auto_duration_bounded` and the cost estimate can be bounded at the model maximum; the response then shows `estimate.basis: "upper_bound"`. |
| `resolution`, `aspect_ratio`, `fps` | string / string / integer | From the record's enums. Nothing is rounded, cropped or substituted. Seedance first/last-frame jobs require `aspect_ratio: "adaptive"` (provider rule). |
| `audio` | bool | Generated audio. Honoured only where the record allows `optional`; `always` and `never` models reject a contradicting value. |
| `n` | integer | Must be `1` unless the record has `max_outputs > 1` **and** `native_multi_output: true`. The gateway never fans one request out into several paid provider calls. |
| `provider_options` | object | Validated against the record's option schema (names, types, enums, ranges). Unknown keys: 400 `unknown_provider_option`. Options can never carry credentials or callback URLs. Seedance exposes `watermark`, `camera_fixed`, `return_last_frame`, `frames` (`25+4n` in `[29,289]`, takes precedence over `duration_seconds`), `service_tier`, and `seed` on 1.x models only. |
| `metadata` | object | ≤ 16 keys, key ≤ 64 chars, value ≤ 512 chars (proposed). Echoed back unchanged. |
| `end_user_id` | string | Optional, ≤ 128 chars. Hashed before it reaches a provider that accepts an end-user identifier. |

Validation and media fetch happen before any budget reservation and before
any provider call. A rejected request has cost nothing.

Response `202 Accepted` (a replay of an already terminal job returns `200`
with the status object):

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

The handler waits up to `video.submit.sync_wait` (default 5s) for the
lifecycle worker to move the job past `submitting/prepared`, then answers
with whatever the job says. If the worker has not reached the provider
yet, `status` is `submitting`; keep polling. `submitting` is not upstream
acceptance. `queued` and `running` are only ever reported after the
provider's job ID has been durably recorded.

`estimate` is the upper bound reserved against your budget at accept time.
`cost` on the status object is the settled amount. Both carry
`tariff_revision` so a price change is auditable.

### Submit errors

| HTTP | `code` | Meaning |
|---|---|---|
| 400 | `missing_idempotency_key` | Header absent or outside 1..255 printable characters |
| 400 | `unknown_field` | Unknown top-level or nested key |
| 400 | `unsupported_model` | Not in the registry; `reason: retired` for Sora IDs |
| 400 | `unknown_provider_option` | Key not in the model's option schema |
| 400 | `media_type_mismatch` | Declared `media_type` does not match sniffed bytes |
| 400 | `unsupported_combination` | Documented-but-disputed combination that fails closed (for example Veo Lite references, Luma HDR + 10s); message carries the citation ID |
| 400 | other validation codes | Stable code plus `param` naming the field (duration, count, ratio, role, limits) |
| 401 | `invalid_api_key` | From the key middleware |
| 403 | `missing_org` | Authenticated key without organization metadata |
| 403 | `model_forbidden` | RBAC or team model allowlist denies `models:<model>` |
| 403 | `provider_not_allowed` | Key's `auth_allowed_providers` excludes the service |
| 409 | `idempotency_conflict` | Same key, different request fingerprint |
| 410 | `deleted_job` | Same key replayed after local delete; body `{"id": "...", "deleted": true}` |
| 413 | `request_too_large` | Body over the server limit |
| 413 | `media_too_large` | An input exceeds the record's byte or pixel ceiling |
| 429 | `budget_exceeded` | Existing budget plugin shape; also `org_budget_exceeded` at the organization level. Nothing was written. |
| 429 | `rate_limited` | Organization submit rate or active-job cap; `Retry-After` set. Note: the existing rate-limit plugin emits `rate_limit_exceeded`; the final code is pinned at implementation. |
| 501 | `not_configured` | `video.enabled: false`. Current scaffold behaviour, kept by the implementation plan. The architecture also names 503 `video_disabled` for the disabled state; treat either as "video is off". |
| 503 | `video_submissions_disabled` | Kill switch on. Existing jobs keep progressing. |
| 503 | `provider_not_configured` | Service known to the registry but `video.providers.<id>.enabled: false` or credentials unresolved |
| 503 | `tariff_missing` | No tariff for the model and `allow_unpriced_models: false` |
| 503 | `managed_tariff_not_configured` | Managed (platform-funded) key. Blocked until a managed tariff is approved (D06). |
| 503 | `video_store_unavailable` | Redis unavailable or circuit open. The gateway fails closed; there is no in-memory fallback. |

## Status: `GET /v1/videos/{video_id}`

Reads only the gateway store. Client polling never fans out to the
provider; the lifecycle worker polls upstream on its own schedule.

```json
{
  "id": "video_01J9...", "object": "video",
  "status": "completed", "provider_state": "succeeded",
  "model": "byteplus/dreamina-seedance-2-5-260628",
  "resolved": {"service": "byteplus", "model_id": "dreamina-seedance-2-5-260628", "region": "ap-southeast-1", "capability_revision": "byteplus-2026-10-02"},
  "created_at": 1790970000, "updated_at": 1790970403, "submitted_at": 1790970003, "completed_at": 1790970403,
  "last_checked_at": 1790970403,
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

| Field | Notes |
|---|---|
| `status` | Normalized state, see "Status vocabulary" |
| `provider_state` | Raw upstream string (`succeeded`, `expired`, `THROTTLED`, `IN_QUEUE`, ...). Always present once the provider has been observed. |
| `progress` | `null` unless the provider reports a number. Never invented. |
| `error` | `null` or `{"code","message","provider_request_id","retryable"}`. Provider text passes through the redactor. |
| `cancellation.state` | `none`, `requested`, `confirmed`, `unsupported`, `not_available`, `not_applicable` |
| `reconciliation.state` | `none`, `pending`, `resolved`, `unresolved` |
| `artifacts[].state` | `pending`, `copying`, `available`, `unavailable`, `expired`, `deleted` |
| `artifacts[].expires_at` | Gateway retention (`video.artifacts.ttl`, default 24h after `completed_at`). Provider URL expiry is separate and may appear as `provider_expiry_hint`. |
| `usage.source` | `estimate`, `provider_reported` or `reconciled` |
| `cost.source` | Same vocabulary. `estimate` until settlement. |

The `prompt` is not echoed (the scaffold at `ce6af27` echoed it; removed
for privacy, see the changelog entry).

Errors: 404 `video_not_found` for unknown IDs, IDs owned by another
organization, and ownerless legacy records. The three cases are
indistinguishable by design. 503 `video_store_unavailable` when Redis is
down.

## Result: `GET /v1/videos/{video_id}/content?artifact_index=0`

Streams the gateway's private copy. Never redirects to a provider URL.

Response headers: `Content-Type` (`video/mp4` or `video/quicktime`),
`Content-Length`, `Content-Disposition: attachment; filename="video_<id>_<idx>.mp4"`,
`Accept-Ranges: bytes`. Range requests are implemented for the disk backend
in Phase 1; S3 range support is Phase 1b.

Authorization: same organization as the job, job not deleted, artifact
`available`, `now < expires_at`.

| HTTP | `code` | Meaning |
|---|---|---|
| 404 | `video_not_found` | Unknown, foreign or deleted |
| 409 | `result_not_ready` | Job not terminal, or artifact still `copying` |
| 410 | `output_expired` | Past `expires_at`; the blob is gone. No regeneration. |
| 410 | `output_unavailable` | Provider succeeded but the copy failed after bounded retries. The job stays `completed` and billable; `artifacts[].error` has the reason. |

Optional presigned delivery (`video.artifacts.presign.enabled`, S3 only,
TTL ≤ 15 min) is disclosed on the status object as `delivery: presigned`.
It is off by default.

## List: `GET /v1/videos`

Query parameters:

| Param | Rule |
|---|---|
| `limit` | 1..100, default 20 |
| `offset` | ≥ 0, default 0 |
| `order` | `asc` or `desc` (default) by `created_at`, ties broken by `id` |
| `status` | One of the normalized states |
| `model` | Registry-known service-qualified ID |

Invalid values are rejected with 400 `invalid_filter`; they are never
silently ignored.

```json
{"object": "list", "data": [{"id": "video_01J9...", "...": "status objects"}], "total": 42, "limit": 20, "offset": 0, "has_more": true}
```

`total` is computed with the same organization and filters as `data`.
Locally deleted and ownerless jobs are never listed or counted. Offset
pagination can shift when jobs are inserted concurrently; this is
documented behaviour, not snapshot isolation.

## Cancel: `POST /v1/videos/{video_id}/cancel`

Repeat-safe. Cancellation is best effort and only becomes `cancelled` when
either the gateway can prove the provider call never started
(`scope: pre_submit`) or the provider confirms it (`scope: provider`). A
provider receipt is `requested`, not `confirmed`.

| HTTP | Body | When |
|---|---|---|
| 200 | `{"id","status":"cancelled","cancellation":{"state":"confirmed","scope":"pre_submit"\|"provider"}}` | Cancelled before submission, or provider confirmed |
| 202 | `{"id","status":"running","cancellation":{"state":"requested"}}` | Provider accepted the request; outcome arrives on a later poll |
| 200 | current terminal status with `cancellation.state: "not_applicable"` | Job already `completed`, `failed` or `cancelled` |
| 409 | `cancel_unsupported` | Adapter has no cancel capability (Veo, Luma, Kling, xAI as documented today) |
| 409 | `cancel_not_available` | Provider cannot cancel in the current state (Seedance and MiniMax: `running` cannot be cancelled, only `queued`) |
| 404 | `video_not_found` | Unknown or foreign |

Accounting is never released by a cancel request. Only a provider-confirmed
cancellation of a job the adapter marks as not billed when cancelled
releases the reservation; otherwise the reservation stays until settlement.

## Local delete: `DELETE /v1/videos/{video_id}`

Deletes the job from your organization's view and purges the gateway copy
of the output. It does **not** contact the provider and does **not** stop
upstream work or charges.

```json
{"id": "video_01J9...", "object": "video", "deleted": true, "deletion_scope": "local_only", "upstream_may_continue": true, "accounting_retained": true}
```

| HTTP | When |
|---|---|
| 200 | Deleted, or already deleted by the same organization (tombstone) |
| 404 | Unknown or foreign |

What is kept: a tombstone with `deleted_at`, organization, provider
correlation, the charge ledger and the idempotency digest, until
reconciliation and retention end. The lifecycle keeps polling a deleted job
only if accounting is unsettled, never to restore output. A later provider
event cannot resurrect the job.

### Cancel versus delete

| | `POST .../cancel` | `DELETE ...` |
|---|---|---|
| Talks to the provider | yes, if the adapter supports it | never |
| Can stop charges | only when the provider confirms before billing | no |
| Job remains visible | yes | no (404 afterwards) |
| Output remains downloadable | yes if completed before cancel took effect | no, purged |
| Idempotent replay of the original key | returns the job | 410 `deleted_job` |

The scaffold at `ce6af27` reported `cancelled` on DELETE without talking to
any provider. That was a false statement and is removed; see the changelog
entry.

## Status vocabulary

| `status` | Meaning | Terminal |
|---|---|---|
| `submitting` | Accepted and durable; the worker has not yet recorded a provider job ID. Internal phases `prepared`, `calling`, `received`. | no |
| `submission_unknown` | The provider call was sent but no usable response was persisted. Reconciliation is running or awaiting an operator. `retry_safe: false`. | no |
| `queued` | Provider acknowledged and the provider job ID is persisted; not yet running upstream | no |
| `running` | Provider reports work in progress | no |
| `completed` | Provider terminal success **and** at least one validated artifact copied, or copy failed with `artifacts[].state: unavailable` (still billable) | yes |
| `failed` | Provider failure, validation or budget rejection at submit, deadline expiry, or unresolved submission. See `error.code`. | yes |
| `cancelled` | Confirmed cancellation (pre-submit or provider) | yes |

Allowed transitions:

```
submitting/prepared  -> submitting/calling | cancelled(pre_submit) | failed(submit_rejected|budget|validation)
submitting/calling   -> queued | running | completed | failed | submission_unknown | cancelled(provider)
submission_unknown   -> queued | running | completed | failed(submission_unresolved) | cancelled(provider)
queued               -> running | completed | failed | cancelled
running              -> completed | failed | cancelled
terminal             -> no state change; artifact, settlement and reconciliation substates may still change
```

A terminal state is never regressed by a later observation. A `running`
report after `completed` only updates `last_checked_at`. An unknown
provider status string is an adapter error, not success: the job stays
where it is, and after `video.poll.max_unknown_status` consecutive unknowns
it fails with `adapter_schema_error`.

Job `error.code` values set by the gateway (provider codes pass through
redacted):

| `error.code` | Set when | `upstream_may_continue` | `retry_safe` |
|---|---|---|---|
| `submit_rejected` | Provider answered a synchronous 4xx validation error | false | true (fix the request, new key) |
| `provider_access_denied` | Provider 401/403: operator credential or model activation problem, not the caller's key | false | true after the operator fixes config |
| `submit_deadline_exceeded` | Worker could not submit before `deadlines.submit_by` (worker down, kill switch, provider disabled) | false | true |
| `provider_disabled` | Provider disabled before submission and `submit_by` passed | false | true |
| `provider_timeout` | `run_by` passed with no terminal provider state | **true** | false |
| `submission_unresolved` | `submission_unknown` could not be reconciled before `reconcile_by` | **true** | **false** |
| `adapter_schema_error` | Repeated unknown provider status strings | true | false |
| `budget_exceeded` / validation codes | Rejected before any provider call | false | true |

### `upstream_may_continue` and `retry_safe`

- `upstream_may_continue: true` means the provider may still be generating
  or may already have billed, even though the gateway reports `failed`,
  `cancelled` or deleted. Expect a cost settlement later. A late provider
  success after `provider_timeout` settles cost and records
  `late_success_at` but does not flip `status` back to `completed` (D11).
- `retry_safe: true` means replaying the same `Idempotency-Key` cannot
  create a second paid generation. `false` means the gateway cannot prove
  that (unknown submission, unresolved reconciliation): a new key is a new
  paid job, and replaying the old key returns this job unchanged.

## Idempotency

- Scope: `organization + operation + sha256(Idempotency-Key)`, enforced by
  an atomic `SET NX` in Redis so concurrent replicas agree on one job.
- A replay with the same request fingerprint (canonical, validated request
  with media digests) returns the existing job: `202` while non-terminal,
  `200` when terminal. No second reservation, no second provider call.
- A replay with a different fingerprint: 409 `idempotency_conflict`.
- A replay after local delete: 410 `deleted_job`.
- Keys are scoped per organization; another organization reusing your
  string gets its own job.
- Retention: `video.retention.idempotency` (default 30 days), refreshed
  while the job's accounting or submission is unresolved. After expiry a
  replay is new paid work; do not treat a key as safe forever.
- Use a fresh key for every intentionally new generation. Retrying a
  network failure reuses the key and body unchanged.

## Media rules

- `https://` only. `http://`, userinfo in the URL, gateway hostnames and
  query strings carrying known secret parameter names are refused.
- Every hop (≤ 3 redirects) is dialled through the gateway's egress guard;
  loopback, link-local, private and cloud-metadata addresses are refused
  after DNS resolution, so rebinding is covered.
- Limits come from the model's capability record (Seedance: < 30 MB per
  image, ≤ 64 MB request, 300..6000 px per side, ratio 0.4..2.5). Decoded
  pixel cap `video.media.max_decoded_pixels` (default 36 Mpx). Video and
  audio inputs get a container or header parse only; no decode.
- Inputs are fetched once at submit and staged, so a later worker retry
  does not refetch from your URL.
- The original client URL is forwarded to the provider only when the
  provider must fetch it itself; gateway credentials are never placed in
  URLs. Signed client URLs have their signatures redacted in logs.

## Compatibility with the `ce6af27` scaffold

Preserved: paths `/v1/videos`, `/v1/videos/{id}`; list parameters `limit`,
`offset`, `order`, `status`, `model`; fields `id`, `object`, `status`,
`model`, `created_at`, `expires_at`, `completed_at`, `error`, `usage`,
`cost`, `metadata`.

Changed intentionally: `Idempotency-Key` required; `DELETE` is local only
and no longer reports `cancelled`; new `/cancel` and `/content` routes;
`status` gains `submitting`, `submission_unknown`, `running`; the scaffold
constant `in_progress` is removed (no code path ever emitted it); `prompt`
is no longer echoed; `n > 1` rejected unless the model natively supports
multiple outputs (the scaffold accepted up to 4 and did nothing with them);
the scaffold's `duration` (float), `size` and `style` fields are replaced by
`duration_seconds`, `resolution` and validated `provider_options`.
