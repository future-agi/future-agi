# Video API migration and release notes

The root changelog records the unreleased entry. Release-please assigns the
version; retain the following Conventional Commit footers in the Phase 1
squash commit so the generated release includes every breaking change.

## Phase 1 squash commit

```
feat(gateway): durable video generation lifecycle with BytePlus Seedance adapter

Replaces the in-memory /v1/videos scaffold with a Redis-backed job
lifecycle (single submitter path, leases, idempotency, budget
reservation and settlement, private artifact copy) and adds the first
provider adapter, BytePlus ModelArk Seedance. Off by default
(video.enabled: false). No provider is smoke-verified yet.

BREAKING CHANGE: POST /v1/videos requires an Idempotency-Key header
(400 missing_idempotency_key without it).
BREAKING CHANGE: DELETE /v1/videos/{id} is local-only and no longer
reports status "cancelled"; it returns
{deleted:true, deletion_scope:"local_only", upstream_may_continue,
accounting_retained}. Use the new POST /v1/videos/{id}/cancel for
best-effort upstream cancellation.
BREAKING CHANGE: status vocabulary is submitting | submission_unknown |
queued | running | completed | failed | cancelled. The scaffold constant
in_progress is removed; no code path ever emitted it.
BREAKING CHANGE: prompt is no longer echoed in status or list responses.
BREAKING CHANGE: n > 1 is rejected with 400 unless the selected model
natively supports multiple outputs; the scaffold accepted n up to 4
without acting on it.
BREAKING CHANGE: request fields duration (float), size and style are
replaced by duration_seconds (integer), resolution and schema-validated
provider_options; unknown fields are rejected with 400 unknown_field.

Refs: TH-8088
```

## Migration notes for API consumers

| Before (dev `ce6af27`) | After | What to change |
|---|---|---|
| `POST /v1/videos` without headers beyond auth | `Idempotency-Key` required | Generate a unique key per intended generation; reuse the same key and body on network retries only. |
| `DELETE /v1/videos/{id}` returned `status: "cancelled"` on non-terminal jobs without contacting any provider | `DELETE` deletes locally; `POST /v1/videos/{id}/cancel` asks the provider | Call `/cancel` first if you want upstream work stopped; expect `cancel_unsupported` or `cancel_not_available` where the provider cannot. Then delete if you also want the job hidden. |
| `status` ∈ `queued, in_progress, completed, failed, cancelled` (`in_progress` never emitted) | `submitting, submission_unknown, queued, running, completed, failed, cancelled` | Treat `submitting` and `submission_unknown` as non-terminal; read `retry_safe` and `upstream_may_continue` before retrying. |
| `prompt` echoed on status | not echoed | Keep the prompt client-side if you need it. |
| `n` up to 4 accepted | `n: 1` unless native multi-output | Send `n: 1` or omit. |
| `duration` (float seconds), `size`, `style` | `duration_seconds` (integer), `resolution`, `aspect_ratio`, `provider_options` | Rename fields; unknown fields now fail with 400. |
| No `/content` route | `GET /v1/videos/{id}/content?artifact_index=0` | Download from the gateway; no provider URLs are returned. |
| `/v1/videos` answered 501 `not_configured` when no store was wired | Same when `video.enabled: false` | No change for deployments that keep video off. |

Preserved: paths `/v1/videos` and `/v1/videos/{id}`; list parameters
`limit`, `offset`, `order`, `status`, `model`; response fields `id`,
`object`, `status`, `model`, `created_at`, `expires_at`, `completed_at`,
`error`, `usage`, `cost`, `metadata`.

No data migration: the scaffold held jobs only in process memory. Any Redis
job hash with an empty `org_id` is quarantined (404, never listed, never
submitted).
