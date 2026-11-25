# Video generation configuration

The `video:` block in `config.yaml` as designed in the TH-8088 architecture
(§11) and implementation plan (P1-01). Status: **as designed, not yet
verified against a running build**. Key names below are the architecture's;
the implementation pins them and this page is corrected if a name changes.
Everything is off by default: a gateway with no `video:` block behaves
exactly as today (`/v1/videos` answers 501 `not_configured`).

## Prerequisites

| Need | Why | Rule |
|---|---|---|
| Redis (`redis.enabled: true` with `redis.address`) | Jobs, idempotency, leases, budget reservations and the poll queue live in Redis. There is no SQL database in the gateway and the in-memory store is refused outside tests. | `video.enabled: true` with `store: redis` fails config validation without Redis, the same rule `license_auth` uses. |
| Redis persistence | A restart that loses the keyspace loses accepted, possibly paid jobs. | Run with AOF `appendfsync everysec` (or RDB snapshots you are willing to lose up to) and `maxmemory-policy noeviction`. The gateway cannot enforce this; at startup it runs `CONFIG GET maxmemory-policy` when permitted and logs a warning if eviction is enabled (proposed). |
| Redis is not optional at runtime | The generic `redis:` block in `config.example.yaml` says "fallback to in-memory on Redis outage". That fallback does **not** apply to video. When Redis is unavailable or its circuit breaker is open, every video route answers 503 `video_store_unavailable`. | Monitor the circuit breaker (see runbook). |
| Artifact storage | Completed videos are copied out of the provider before its URL expires (Seedance: 24h / 100 downloads). | Disk for a single replica or a shared mount; S3 for multiple replicas. |
| `ip_acl.trusted_proxies` behind a load balancer | Org and global IP allowlists, deny lists and per-key `allowed_ips` are checked on every `/v1/videos*` route. Behind a proxy the TCP peer is the proxy, so the client address must come from `X-Forwarded-For`. Video routes read only the entry `trusted_proxies` hops from the right, never the caller-supplied first entry. | Set it to the number of entries your proxy chain appends: `2` for a Google Cloud external Application Load Balancer sending straight to the pods (it appends `<client-ip>,<load-balancer-ip>`), `1` for a single nginx/Envoy hop that appends the client address. Leave `0` (the default) only when clients connect directly; with `0`, `X-Forwarded-For` is ignored. Verify against your own chain: a wrong value judges the wrong address. The same rule applies to every other route that records a client address (chat, completions, responses, messages, generateContent, assistants, speech, decisions). |
| Global (config-file) budget levels | With video enabled and Redis configured, `budgets.org/teams/users/keys/tags` levels read and write the same Redis ledger as video reservations and settlements, so ordinary and video spend count against one ceiling across replicas. | If a ledger read fails, ordinary requests are checked against the replica's local counters (as per-org budgets are) and are never refused as `budget_exceeded` for that reason; video creation answers 503 `budget_store_unavailable`. A spend write Redis refuses is kept only in the local counter, so the shared total can undercount it, as with per-org budgets. With video disabled, global levels stay pod-local. |
| Operator-owned provider key | Phase 1 is BYOK only. | Managed (platform-funded) keys are rejected at submit with 503 `managed_tariff_not_configured` until a managed tariff is approved (D06). |

## Full block with defaults

```yaml
video:
  enabled: false                      # master switch; off by default
  kill_switch: false                  # blocks new submissions only
  store: redis                        # redis | memory
  allow_non_durable_store: false      # memory store refused unless true (dev/test only)
  encrypt_requests: false             # when true requires video.request_encryption_key (secret URI)
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
      api_key: "${ARK_API_KEY}"       # or a secret URI, see below
      base_url: https://ark.ap-southeast.bytepluses.com/api/v3
      region: ap-southeast-1
      account_ref: byteplus-main      # label for accounting and concurrency
      models: [dreamina-seedance-2-5-260628, dreamina-seedance-2-0-260128]
      limits: { submit_rpm: 10, poll_qps: 10, max_active_tasks: 5 }
      deadlines: { connect: 10s, read: 30s, submit: 30s, run: 2h, reconcile: 15m }
      poll: { base_interval: 5s }
      allow_unpriced_models: false
      tariff_revision: byteplus-2026-10-02
      safety_identifier_mode: org_key_hmac   # none | org_key_hmac
      execution_expires_after: 7200          # seconds, sent to BytePlus; must be >= deadlines.run
      acknowledge_public_output: false
```

Environment overrides: `AGENTCC_VIDEO_ENABLED`, `AGENTCC_VIDEO_KILL_SWITCH`
(implementation plan P1-01), following the existing `AGENTCC_*` convention.

## Field reference

### Top level

| Key | Default | Meaning | Validation |
|---|---|---|---|
| `enabled` | `false` | Registers the lifecycle worker, the Redis store and the `/content` and `/cancel` routes. | Requires `redis.enabled` when `store: redis`. |
| `kill_switch` | `false` | Refuses new submissions with 503 `video_submissions_disabled`. Polling, result copy, cancel, delete and settlement continue. Jobs already accepted but not yet submitted wait until `submit_by`, then fail `submit_deadline_exceeded`. | Also settable at runtime through the Redis key `video:v1:killswitch` via the admin endpoint (see runbook). |
| `store` | `redis` | `redis` or `memory`. | `memory` requires `allow_non_durable_store: true` and is for tests only. |
| `allow_non_durable_store` | `false` | Lets a memory store start. | Never set in production: jobs vanish on restart and replicas disagree. |
| `encrypt_requests` | `false` | Encrypt the stored canonical request (prompt, options) at rest. | When `true`, `request_encryption_key` must be a resolvable secret URI. |

### `submit`

| Key | Default | Meaning |
|---|---|---|
| `sync_wait` | `5s` | How long the HTTP handler waits for the worker to move the job past `prepared` before answering 202. |
| `deadline` | `30s` | `submit_by = created_at + deadline`. A job not submitted by then fails `submit_deadline_exceeded` and its reservation is released. |
| `unknown_reconcile_deadline` | `15m` | How long a `submission_unknown` job is reconciled before it fails `submission_unresolved`. Must stay well under BytePlus's 7-day task-record retention. |
| `lease_ttl` | `60s` | Worker lease on a job; renewed every 20s while working. Another replica takes over after expiry. |

### `poll`

| Key | Default | Meaning |
|---|---|---|
| `max_concurrent_total` | `32` | Cluster-wide upstream poll concurrency (Redis semaphore). |
| `per_provider_max_concurrent` | `8` | Same, per provider. |
| `max_interval` | `60s` | Ceiling for the ×1.5 exponential backoff with full jitter while `queued`. Resets to the provider base interval on `running`. |
| `max_consecutive_failures` | `20` | 5xx/timeouts in a row before the job is marked `reconciliation: pending` with an alert. The job is not failed; upstream may be fine. |
| `max_unknown_status` | `3` | Unknown provider status strings in a row before the job fails `adapter_schema_error`. |
| `timeout_reconcile_window` | `2h` | After `provider_timeout`, how long settlement polling continues at a slow interval (5 min) to capture cost. |

### `media`

| Key | Default | Meaning |
|---|---|---|
| `max_inline_bytes` | `20971520` (20 MiB) | Cap on base64 `source.data`. Provider records may be stricter. |
| `fetch_timeout` | `20s` | Per input URL. |
| `total_ingress_timeout` | `60s` | Per request across all inputs. |
| `max_decoded_pixels` | `36000000` | Decoded `width × height` cap for images. |

Byte, count, dimension and ratio limits per input role are not configurable:
they come from the model's capability record.

### `artifacts`

| Key | Default | Meaning |
|---|---|---|
| `backend` | `disk` | `disk` or `s3`. |
| `ttl` | `24h` | Retention after `completed_at`. Exposed as `artifacts[].expires_at`. After expiry `/content` answers 410 `output_expired`; nothing is regenerated. |
| `max_bytes` | `2147483648` (2 GiB) | Download cap per artifact; copy aborts past it and the artifact becomes `unavailable`. |
| `disk.root` | `/var/lib/agentcc/video` | Layout `{root}/{org}/{job}/{index}.mp4`. Must be writable by every replica that may copy or serve, so use a shared mount or S3 for more than one replica. Range reads supported. |
| `s3.bucket`, `s3.prefix`, `s3.region` | `""`, `video/`, `""` | Operator-owned bucket. Keys are `{prefix}{org}/{job}/{index}.mp4`; the gateway refuses to read or write outside the prefix. Object ACL private. |
| `s3.access_key`, `s3.secret_key` | `${AWS_ACCESS_KEY_ID}`, `${AWS_SECRET_ACCESS_KEY}` | SigV4 credentials (the gateway's existing signer, no AWS SDK). |
| `s3.sse` | `AES256` | Server-side encryption header. |
| `presign.enabled`, `presign.ttl` | `false`, `15m` | Optional presigned delivery for S3 only; disclosed as `delivery: presigned`. TTL ≤ 15 min. |

Range requests on S3 are Phase 1b.

### `retention`

| Key | Default | Meaning |
|---|---|---|
| `job_metadata` | `720h` (30 days) | Job hash lifetime. Not applied while `settlement_state` is `reserved` or `unsettled` or `reconciliation` is `pending`. |
| `idempotency` | `720h` | Idempotency index TTL, refreshed while the job is unresolved. After expiry a replayed key is new paid work. |

Budget operation receipts (reservation, refund and settlement) deduplicate
replayed budget writes. Each lives in its own key, `<redis.prefix>budget:receipt:<operation>`,
for `job_metadata` plus 24h, and never less than the TTL of the budget counter it
protects. Budget counters keep their period TTL (48h daily, 8 days weekly, 35 days
monthly; `total` never expires) and hold only spend, so video volume does not
slow the budget reads made on every chat request.

### `limits`

| Key | Default | Meaning |
|---|---|---|
| `org_max_active_jobs` | `10` | Non-terminal jobs per organization. Excess submissions get 429 with `Retry-After`. |
| `org_submit_rpm` | `60` | Submit rate per organization. |

### `providers.<id>`

Common keys for every family. `enabled: false` is the default everywhere.

| Key | Meaning | Validation when `enabled: true` |
|---|---|---|
| `enabled` | Turns the adapter on. `false` makes submissions for that service answer 503 `provider_not_configured`; existing jobs keep being polled and settled. | — |
| `api_key` | Credential. `${ENV}` expansion or a secret URI (below). | Required unless `credential_ref` is set. Never a literal key in the file. |
| `credential_ref` | `provider:<name>` reuses credentials from an existing `providers[]` entry (planned for `google_gemini`, `bedrock_nova`). | The referenced provider must exist. |
| `base_url`, `region` | Upstream host and region label pinned on every job. | Required. |
| `account_ref` | Label used for accounting and the per-account concurrency counter. | Required. |
| `models` | Exact upstream model IDs this deployment offers. | Non-empty; every ID must exist in the capability registry for this service. |
| `limits.submit_rpm`, `limits.poll_qps`, `limits.max_active_tasks` | Operator caps, enforced through the Redis rate limiter and counters. | All required (R07: enablement fails if limits are missing). |
| `deadlines.connect`, `read`, `submit`, `run`, `reconcile` | Per-provider deadlines; `run` sets `run_by`, `reconcile` bounds `submission_unknown`. | All required. |
| `poll.base_interval` | First poll interval; backoff grows from here. | Required; not below the provider's documented minimum. |
| `allow_unpriced_models` | Lets BYOK keys submit models without a tariff. | Default `false`. Managed keys are blocked regardless. |
| `tariff_revision` | Pins the price table used for estimates and settlement. | Required unless `allow_unpriced_models: true`. |
| `acknowledge_public_output` | Operator acknowledgement that the provider's output storage is public by default or unknown. | Required `true` to enable a provider whose `OutputACL` is `public_default` or `unknown`. Reserved for explicit authorization; do not set it to make a config load. |

### `providers.byteplus` specifics

| Key | Meaning |
|---|---|
| `base_url` | `https://ark.ap-southeast.bytepluses.com/api/v3` for region `ap-southeast-1`. |
| `models` | Any of `dreamina-seedance-2-5-260628`, `dreamina-seedance-2-0-260128`, `dreamina-seedance-2-0-fast-260128`, `dreamina-seedance-2-0-mini-260615`, `seedance-1-5-pro-251215`, `seedance-1-0-pro-250528`, `seedance-1-0-pro-fast-251015`. |
| `limits.poll_qps` | BytePlus caps task retrieval at 20 QPS and task listing at 1 QPS per account. Default 10 leaves headroom when more than one gateway shares the account. |
| `limits.max_active_tasks` | BytePlus's concurrent-task cap is per model and per account and is not published on the recovered pages; set it from your console. |
| `safety_identifier_mode` | `org_key_hmac` sends `safety_identifier = HMAC-SHA256(org_id ‖ key_id ‖ end_user_id, gateway secret)`, truncated. It is also what reconciliation matches on after a crash. `none` disables both. The HMAC secret is a separate config value (`video.correlation_secret` in the implementation plan), never the API key. |
| `execution_expires_after` | Sent to BytePlus (default 172800 upstream, allowed 3600..259200). Must be ≥ `deadlines.run` or BytePlus can expire the task before the gateway's own deadline. |
| `tariff_revision` | `byteplus-2026-10-02`: USD per million video tokens from the ModelArk pricing page as read on 2026-10-02. Estimates use the BytePlus token formula `(input_video_s + output_s) × W × H × fps / 1024` at the model maximum when duration is auto. |
| `acknowledge_public_output` | Keep `false`. Whether Seedance output URLs are signed or public-by-obscurity is not verified; the gate stays until an approved live object is inspected. |

The adapter never sends `callback_url`, `draft`, `draft_task`, or the
`edit`/`extend` task types; those operations are out of scope (D04) and the
callback has no documented authentication.

### `providers.openrouter` specifics

OpenRouter is an aggregator route: it proves the OpenRouter path for a model,
not the model vendor's native API, credentials, billing or retention.

| Key | Meaning |
|---|---|
| `base_url` | `https://openrouter.ai/api/v1` (HTTPS, no query). |
| `region` | `global` (the only value accepted). |
| `models` | Any of `bytedance/seedance-1-5-pro`, `google/veo-3.1-lite`, `kwaivgi/kling-v3.0-std`, `alibaba/wan-3.0`, `minimax/hailuo-3-max`, `runway/gen-4.5`, `x-ai/grok-imagine-video-1.5-lite`. Requests name them as `openrouter/<vendor>/<model>`. |
| `poll.base_interval` | At least 5s; OpenRouter suggests about 30s between polls. |
| `tariff_revision` | `openrouter-2026-11-06`: the list `pricing_skus` of the public video catalog read on 2026-11-06, used only for the upper-bound reservation (advertised discounts ignored; Kling 3.0 Std and Wan 3.0 reserved at their observed 5-second minimum billed duration; 15% headroom on Seedance video tokens). Each job settles at the USD `usage.cost` OpenRouter reports. A null or missing cost, or `is_byok: true` (the vendor bills your own key directly), leaves the job unsettled with the reservation held; it is re-read for accounting at most 12 times, five minutes apart. |
| `safety_identifier_mode` | `none` (default) or `org_key_hmac`, which sends a keyed HMAC of org, key and end user as OpenRouter's `user` field (observability only; it is not a deduplication key). |
| `execution_expires_after`, `min_tokens_with_video_input`, `acknowledge_public_output` | Not accepted: they are BytePlus-specific. Outputs are copied only through OpenRouter's authenticated content endpoint into gateway storage; signed download URLs are never followed or stored. |

OpenRouter documents no submit idempotency key, job lookup, list or cancel
operation. A submit that was sent but got no definite answer (timeout, 429,
499, 5xx) stays `submission_unknown` and is never resubmitted; 400 and 402
are definite refusals and release the reservation. `POST /cancel` answers 409
`cancel_unsupported`, and local delete reports `upstream_may_continue`.

## BytePlus ModelArk setup

1. Create an API key in the ModelArk console for region `ap-southeast-1`
   (`https://ai.byteplus.com/ark/region:ap-southeast-1/apikey`).
2. Activate each Seedance model you list under `models`. Activation is per
   model; Seedance 2.0 and 2.5 carry account prerequisites described on the
   create-task page. Your account's entitlement is not something the gateway
   can check; an unactivated model surfaces as a synchronous provider
   rejection after submit (`failed` with `submit_rejected` or
   `provider_access_denied`, depending on the ModelArk error code).
3. Export the key as `ARK_API_KEY` (or store it in a secrets backend, next
   section) and keep `api_key: "${ARK_API_KEY}"` in the file.
4. Set `limits` and `deadlines` from your console quotas. Config validation
   refuses an enabled provider with any of them missing.
5. Leave `enabled: false` until Redis persistence is confirmed and the
   rollout order in the runbook is followed.

No live request is made by config validation. The first real request is
the first paid request.

## Credentials: environment variables and secret URIs

Provider credentials follow the gateway's existing conventions:

- `${ENV_VAR}` expansion, as used by every `providers[]` entry.
- Secret URIs resolved by `internal/secrets` at load time. The resolver
  recognises these schemes and no others:

  | Scheme | Backend | Config block |
  |---|---|---|
  | `vault://<path>#<field>` | HashiCorp Vault KV v2 | `secrets.vault` |
  | `aws-sm://<path>#<field>` | AWS Secrets Manager | `secrets.aws` |
  | `gcp-sm://<path>#<field>` | GCP Secret Manager | `secrets.gcp` |
  | `azure-kv://<path>#<field>` | Azure Key Vault | `secrets.azure` |

  The `#<field>` selector is optional (`internal/secrets/resolver.go`,
  `ParseURI`). Example: `api_key: "aws-sm://agentcc/byteplus#ARK_API_KEY"`.
  There is no generic `secret://` scheme; a value with an unknown scheme is
  treated as a literal and will fail against the provider.

Never commit a real key. `config.example.yaml` ships every provider block
commented out with `${...}` placeholders.

## Validation summary

Config load fails, with the offending key in the message, when:

- `video.enabled: true`, `store: redis` and Redis is not enabled with an
  address.
- `store: memory` without `allow_non_durable_store: true`.
- `encrypt_requests: true` without a resolvable `request_encryption_key`.
- An enabled provider is missing `api_key`/`credential_ref`, `models`, any
  `limits.*`, any `deadlines.*`, or `poll.base_interval`.
- An enabled provider lists a model unknown to the capability registry.
- An enabled provider has no `tariff_revision` and
  `allow_unpriced_models: false`.
- An enabled provider's output ACL is `public_default` or `unknown` and
  `acknowledge_public_output` is not `true`.
- `providers.byteplus.execution_expires_after < deadlines.run`.

Derived from §9 and the §11 comments rather than stated as explicit
validation rules; confirm at implementation: `artifacts.backend: s3` with
an empty `bucket` or `region`; `artifacts.presign.enabled` with the disk
backend or a `ttl` above 15 minutes.

Keys referenced in the architecture prose but not in the §11 schema
block, so their final names are pinned at implementation:
`copy.deadline` (10 min), `copy.max_concurrent` (8 per replica),
`reconcile.allow_absent_resubmit` (default `false`),
`video.correlation_secret`, `video.request_encryption_key`.

## Minimal production example (single provider, two replicas)

```yaml
redis:
  enabled: true
  address: "redis.internal:6379"
  password: "${REDIS_PASSWORD}"

video:
  enabled: true
  artifacts:
    backend: s3
    s3: { bucket: "my-gateway-video", prefix: "video/", region: "ap-southeast-1" }
  providers:
    byteplus:
      enabled: true
      api_key: "${ARK_API_KEY}"
      base_url: https://ark.ap-southeast.bytepluses.com/api/v3
      region: ap-southeast-1
      account_ref: byteplus-main
      models: [dreamina-seedance-2-5-260628]
      limits: { submit_rpm: 10, poll_qps: 10, max_active_tasks: 5 }
      deadlines: { connect: 10s, read: 30s, submit: 30s, run: 2h, reconcile: 15m }
      poll: { base_interval: 5s }
      tariff_revision: byteplus-2026-10-02
      execution_expires_after: 7200
```

This example has not been run against a build; it parses only once P1-01
lands.
