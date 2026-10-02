# TH-8088 provider adapter contract and per-family notes

Stage: ARCHITECTURE draft, 2026-10-02. Companion to `architecture.md`. Checked-at dates are the dates this stage fetched the page. "PM 2026-09-30" means the fact comes from the approved provider matrix r2 and was not re-fetched here. "Unverified" means neither this stage nor the PM established it; the adapter must fail closed on it.

## 1. Go-level contract (`internal/providers/video`)

```go
package video

// Capabilities is a static, revisioned descriptor returned by every adapter.
type Capabilities struct {
    Service            string            // "byteplus", "google_gemini", ...
    Revision           string            // "byteplus-2026-10-02"; bump on any doc-driven change
    Regions            []string
    Models             []ModelCapability
    SubmitIdempotency  IdempotencyKind   // None | Token (provider dedupes on a client token)
    CorrelationLookup  LookupKind        // None | ByToken | Heuristic
    CancelSupport      CancelKind        // None | QueuedOnly | QueuedAndRunning
    OutputURLExpiry    time.Duration     // 0 = unknown
    OutputURLRefresh   bool              // re-poll returns a fresh URL
    OutputACL          ACLKind           // PrivateToken | PrivateSigned | PublicDefault | Unknown
    BillsOnFailure     Billability       // NotBilled | Billed | Unknown
    Webhook            WebhookKind       // None | Unauthenticated | Signed
    PollMinInterval    time.Duration
}

type ModelCapability struct {
    ModelID          string                      // exact upstream ID
    Operations       map[Operation]OperationSpec // text_to_video, image_first_frame, image_first_last, reference
    Durations        IntRange                    // plus Enum []int when discrete
    AutoDuration     *AutoDurationSpec           // nil = not allowed; bounded by Durations.Max
    Resolutions      []string
    AspectRatios     []string                    // includes "adaptive" where allowed
    FPS              []int
    MaxOutputs       int                         // D12: n>1 only if NativeMultiOutput
    NativeMultiOutput bool
    Audio            AudioSpec                   // Generated: always|optional|never ; InputRefs allowed roles
    Inputs           map[Role]InputSpec          // count, bytes, pixels, dims, ratio, formats
    PromptLimit      PromptLimit                 // Unit: bytes|chars|utf16 ; Max
    Options          map[string]OptionSpec       // provider_options schema (type, enum, range, default)
    Tariff           *TariffRef                  // nil = unpriced (blocks unless allowed)
    Access           AccessState                 // documented|gated|unverified|retired
}

// Adapter is implemented per provider family. All methods must be safe to call from any replica.
type Adapter interface {
    Capabilities() Capabilities
    // Prepare returns provider-specific correlation material BEFORE the call is made
    // (e.g. clientRequestToken, external_task_id, safety_identifier). Persisted by the lifecycle.
    Prepare(ctx context.Context, job *JobView) (Correlation, error)
    Submit(ctx context.Context, job *JobView, corr Correlation) (SubmitResult, error)
    Poll(ctx context.Context, ref ProviderRef) (Observation, error)
    // Fetch opens a stream for artifact idx; the lifecycle copies it to the blob store.
    Fetch(ctx context.Context, ref ProviderRef, idx int) (io.ReadCloser, FetchMeta, error)
    Cancel(ctx context.Context, ref ProviderRef) (CancelResult, error)      // ErrCancelUnsupported if CancelSupport==None
    Reconcile(ctx context.Context, job *JobView, corr Correlation) (ReconcileResult, error) // ErrReconcileUnsupported if CorrelationLookup==None
}

type SubmitResult struct {
    ProviderJobID     string
    ProviderState     string        // raw
    Normalized        State         // queued|running|completed|failed
    ProviderRequestID string
    Raw               json.RawMessage
}

type Observation struct {
    ProviderState string
    Normalized    State
    Progress      *int
    Outputs       []OutputRef      // URL or opaque handle, content type, dims, duration, bytes
    Usage         *Usage           // typed units, provider_reported
    Error         *ProviderError   // code, message (unredacted; lifecycle redacts), retryable
    RetryAfter    *time.Duration
    Raw           json.RawMessage
}

type CancelResult struct { State CancelState /* confirmed|requested|not_available */ ; ReleasesCharge bool }

type ReconcileResult struct { Found bool; ProviderJobID string; ProvenAbsent bool /* safe to resubmit */ }

// Errors: adapters wrap provider HTTP failures as *UpstreamError{Status, Code, RequestID, Retryable, Body}.
// Unknown status strings must return *SchemaError, never a success.
```

Normalization rules (lifecycle-side, table-driven per adapter):

| Normalized | BytePlus | Gemini Veo | Omni (Interactions) | Runway | Luma | Kling | MiniMax | DashScope Wan/HappyHorse | xAI | Nova (Bedrock) | fal |
|---|---|---|---|---|---|---|---|---|---|---|---|
| queued | `queued` | operation `done=false` (no progress field) | interaction `status` non-terminal (background mode, unverified) | `PENDING`, `THROTTLED` | `queued` | `submitted` | `queued` | `PENDING` | `pending` | `InProgress` before start (unverified split) | `IN_QUEUE` |
| running | `running` | same as queued (Veo exposes no distinction) | same | `RUNNING` | `dreaming` (PM) | `processing` | `running` | `RUNNING` | `pending` | `InProgress` | `IN_PROGRESS` |
| completed | `succeeded` | `done=true` + `response.generateVideoResponse.generatedSamples[]` | `completed` + `steps[].content[type=video]` | `SUCCEEDED` | `completed` | `succeeded` | `succeeded` | `SUCCEEDED` | `done` | `Completed` | `COMPLETED` + result without error |
| failed | `failed`, `expired` | `done=true` + `error` | `failed` | `FAILED` | `failed` | `failed` | `failed` | `FAILED` | `failed`, `expired` | `Failed` | `COMPLETED` with error payload, or queue error |
| cancelled | `cancelled` | n/a | n/a | `CANCELLED` | n/a | n/a | `cancelled` | n/a | n/a | n/a | cancelled via cancel_url (PM) |

Anything not in the table → `*SchemaError`.

Capability registry data shape (Go tables in `internal/video/capability/<service>.go`, exported as JSON via an admin endpoint `GET /admin/video/capabilities` for docs generation):

```json
{"service":"byteplus","model":"dreamina-seedance-2-5-260628","version":"260628","region":"ap-southeast-1","operation":"image_first_last",
 "inputs":{"first_frame":{"count":1,"max_bytes":31457280,"min_px":300,"max_px":6000,"ratio":[0.4,2.5],"formats":["jpeg","png","webp","bmp","tiff","gif","heic","heif"]},"last_frame":{"count":1,"...":"..."}},
 "duration":{"min":4,"max":30,"auto":true},"resolutions":["480p","720p","1080p"],"aspect_ratios":["adaptive"],"fps":[24],
 "audio":{"generated":"optional_default_true","input_refs":[]},"max_outputs":1,
 "options":{"watermark":{"type":"bool","default":false},"seed":{"type":"int","min":-1,"max":2147483647,"models":["seedance-1-*"]},"camera_fixed":{"type":"bool"},"return_last_frame":{"type":"bool"},"service_tier":{"type":"enum","values":["default"]}},
 "tariff":{"unit":"video_tokens","revision":"byteplus-2026-10-02"},"access":"gated","evidence":"documented"}
```

## 2. BytePlus ModelArk Seedance (P01 to P04), FULL detail

Checked 2026-10-02 from the live docs HTML (content embedded as `window._ROUTER_DATA.curDoc.MDContent`; tutorial page last updated 2026-09-22 per page header):

- Tutorial: https://docs.byteplus.com/en/docs/ModelArk/2298881 (same body as `.../video-generation-tutorial`)
- Create task API: https://docs.byteplus.com/en/docs/ModelArk/create-video-generation-task-api
- Get task API: https://docs.byteplus.com/en/docs/ModelArk/get-video-generation-task-api
- List tasks API: https://docs.byteplus.com/en/docs/ModelArk/list-video-generation-tasks-api
- Seedance 2.5 model guide: https://docs.byteplus.com/en/docs/modelark/seedance-2-5
- Pricing: https://docs.byteplus.com/en/docs/ModelArk/1544106

Endpoints (region ap-southeast-1 host `ark.ap-southeast.bytepluses.com`, base path `/api/v3`):

| Operation | Method/path | Notes |
|---|---|---|
| Create | `POST /contents/generations/tasks` | returns `{ "id": "cgt-..." }`; task ID stored 7 days from `created_at` |
| Retrieve | `GET /contents/generations/tasks/{id}` | account-level QPS limit 20 |
| List | `GET /contents/generations/tasks?page_num&page_size&filter.status&filter.task_ids&filter.model&filter.service_tier` | QPS limit 1; query window `[T-7d, T)`; items include `safety_identifier`, `seed`, `created_at`, `model`, `status`, `error` |
| Cancel/Delete | `DELETE /contents/generations/tasks/{id}` | "Cancel queued video generation tasks, or delete video generation task records"; QPS limit 20 |

Auth: `Authorization: Bearer <ARK_API_KEY>`; key obtained from the ModelArk console (`https://ai.byteplus.com/ark/region:ap-southeast-1/apikey`). Model activation is per model in the console; the create page says 2.0/2.5 activation has account prerequisites (enterprise conditions listed on page; exact company entitlement unverified).

Exact model IDs and capability table (tutorial "Model capabilities"):

| Model ID | T2V | I2V first | I2V first+last | Omni refs (image/video/audio) | Edit/Extend | Audio gen | Draft | Resolutions | Ratios | Duration | FPS |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `dreamina-seedance-2-5-260628` | yes | yes | yes | yes; audio-only input allowed | yes (excluded by D04) | yes | yes | 480p, 720p, 1080p (10-bit HEVC) | 21:9,16:9,4:3,1:1,3:4,9:16 (+adaptive) | 4..30 s or -1 | 24 |
| `dreamina-seedance-2-0-260128` | yes | yes | yes | yes; audio ref must be with image/video | yes (excluded) | yes | no | 480p, 720p, 1080p, 4k (10-bit HEVC) | same | 4..15 | 24 |
| `dreamina-seedance-2-0-fast-260128` | yes | yes | yes | yes; audio with image/video | yes (excluded) | yes | no | 480p, 720p | same | 4..15 | 24 |
| `dreamina-seedance-2-0-mini-260615` | yes | yes | yes | yes; audio with image/video | yes (excluded) | yes | no | 480p, 720p | same | 4..15 | 24 |
| `seedance-1-5-pro-251215` | yes | yes | yes | no | no | yes | yes | 480p, 720p, 1080p | same | 4..12 | 24 |
| `seedance-1-0-pro-250528` | yes | yes | yes | no | no | no | no | 480p, 720p, 1080p | same | 2..12 | 24 |
| `seedance-1-0-pro-fast-251015` | yes | yes | no | no | no | no | no | 480p, 720p, 1080p | same | 2..12 | 24 |

Request body (create API, verified field list): `model` (required), `content[]` (required; items `{type:"text", text}`, `{type:"image_url", image_url:{url}, role}`, `{type:"video_url", video_url:{url}, role}`, `{type:"audio_url", audio_url:{url}, role}`, `{type:"draft_task", draft_task:{id}}`), `omni_reference_task_type` (`auto|reference|edit|extend`, default auto; 2.5), `resolution`, `ratio` (`16:9,4:3,1:1,3:4,9:16,21:9,adaptive`), `duration` (int seconds, or -1 auto), `frames` (int, `25+4n` in `[29,289]`, takes precedence over duration), `generate_audio` (default true), `watermark` (default false), `seed` (`[-1, 2147483647]`, Seedance 1.x only), `camera_fixed` (default false), `return_last_frame` (default false), `draft` (default false), `service_tier` (default `default`), `callback_url`, `execution_expires_after` (default 172800 s, range `[3600, 259200]`), `priority` (2.x), `safety_identifier` (end-user identifier string, returned unchanged in list items).

Roles: first frame = `first_frame` (or blank), last frame = `last_frame`; references = `reference_image`, `reference_video`, `reference_audio`. Image constraints: URL, base64 or asset ID; formats jpeg/png/webp/bmp/tiff/gif (+heic/heif on 1.5 and 2.0 series); ratio (0.4, 2.5); width/height (300, 6000) px; single image < 30 MB; request body ≤ 64 MB; 2.5 omni reference 1..30 images (guide says "up to 50 omni reference assets" total). Real human faces in reference images/videos are rejected by 2.0/2.5 (portrait asset guide applies).

Seedance 2.5 task-type constraints (model guide "Read before use"): first/last-frame tasks require `ratio=adaptive`; video editing requires `ratio=adaptive` and `duration=-1`; extension requires `ratio=adaptive`; with `omni_reference_task_type=auto` the model infers edit/extend from prompt intent and validates asynchronously (error `InvalidParameter.TaskTypeConstraint`); setting `reference|edit|extend` validates synchronously at create.

Gateway consequences (D04 excludes edit/extend): for any 2.5 request carrying `reference_video`, the adapter sends `omni_reference_task_type=reference` so an edit/extend classification fails synchronously at create rather than becoming a paid edit job; the gateway never sends `edit`/`extend`/`draft_task`/`draft=true`. `duration=-1` is accepted only with the estimator bound at the model maximum (30 s for 2.5, 15 s for 2.0) and is disclosed in `estimate.basis=upper_bound`; the operator may disable auto duration per model. `frames` is exposed as `provider_options.frames` with the `25+4n` validation. `callback_url` is never sent (callback has no documented authentication; polling baseline). `execution_expires_after` is set from config and must be ≥ `deadlines.run` so BytePlus does not expire before the gateway deadline. `safety_identifier` is set to `HMAC-SHA256(org_id || key_id || end_user_id, gateway_secret)` truncated (fixed per end user as the docs request), which also serves the list-based reconciliation below. `seed` is forwarded only for 1.x models.

Response of retrieve (verified): `id, model, status, content.video_url (+ content.last_frame_url), usage.completion_tokens, usage.total_tokens, created_at, updated_at, seed, resolution, ratio, duration (integer approximation = floor(frames/24)), frames, framespersecond, generate_audio, service_tier, execution_expires_after, draft, draft_task_id, error{code,message} (null on success)`.

Status values (verified on get and callback docs): `queued`, `running`, `succeeded`, `failed`, `expired` (task exceeded `execution_expires_after`), `cancelled` ("Only tasks in the queued state can be canceled. Canceled tasks are automatically deleted after 24 hours").

Cancel/delete: `DELETE .../tasks/{id}` cancels a `queued` task; on terminal tasks it deletes the record. Mapping: `CancelSupport=QueuedOnly`; the adapter calls DELETE only when the last observation was `queued`, then re-polls to confirm `cancelled` (receipt ≠ confirmation); for `running` it returns `cancel_not_available`. The gateway never calls DELETE for local delete (R10), because that would erase the provider record needed for reconciliation.

Output: `content.video_url` valid 24 hours, 2.5 URLs downloadable at most 100 times; BytePlus recommends TOS data subscription for long-term backup. Mapping: `OutputURLExpiry=24h`, `OutputURLRefresh=false` (re-poll returns the same URL; not verified to extend expiry), `OutputACL=PrivateSigned` (the URL is a TOS object URL on `ark-content-generation-ap-southeast-1.tos-ap-southeast-1.volces.com`; whether it is signed vs public-by-obscurity is NOT verified; treat as `Unknown` for the enablement gate until a live object is inspected under the approved smoke, then update).

Pricing (pricing page, "Video generation" section, USD per million video tokens, online inference): 2.5: 480p/720p 10.70 without input video, 6.40 with; 1080p 11.7 / 7.0. 2.0: 480p/720p 7.0 / 4.3; 1080p 7.7 / 4.7; 4K 4.0 / 2.4. 2.0 Fast: 480p/720p 5.6 / 3.3 (list; enterprise-only 25% promotion until 2026-10-07 14:00 UTC+8). 2.0 Mini: 3.5 / 2.1 (list; enterprise-only 60% promotion same window). 1.5 Pro: 2.4 with audio, 1.2 without (offline 1.2/0.6). 1.0 Pro: 2.5 (offline 1.25). 1.0 Pro Fast: 1.0 (offline 0.5). Token estimate formula (BytePlus): `(input video s + output video s) × W × H × fps / 1024`; minimum token consumption applies when input includes video (table in a Lark sheet, not recovered); draft mode bills two tasks; examples: 2.5 480p/16:9/5s USD 0.514, 720p USD 1.156, 1080p USD 2.843. Billing is on `usage.completion_tokens` of successful output (pricing text says "successful output only" per PM; this stage saw the tiered formula but did not see an explicit failed-task statement on the recovered pricing page, so `BillsOnFailure=Unknown` until confirmed on smoke; reservation kept as unsettled on failure, alert, operator release).

Rate limits: model-level RPM and max concurrent tasks (values on a model list page not recovered; account specific), non-inference QPS: retrieve 20, list 1, cancel/delete 20. Mapping: `PollMinInterval=5s` proposed; poll QPS limiter per account at 10 (half the cap) proposed.

Idempotency and correlation: no client idempotency token exists (`SubmitIdempotency=None`). `CorrelationLookup=Heuristic`: list `filter.model=<model>` and `filter.status` ∈ {queued, running, succeeded, failed} (4 calls at 1 QPS, or paginate) within `[T-7d, T)`, match items with `safety_identifier == ours` and `created_at` within `[attempt_started_at - 60s, attempt_started_at + submit deadline + 60s]`; exactly one match → found; zero across all statuses after the window → proven absent only if the window closed ≥ 2 minutes ago (proposed) and no `queued` lag is possible (not provable; so treated as "absent, resubmit allowed" only when the operator enables `reconcile.allow_absent_resubmit`, default false → unresolved). Ambiguous → unresolved. This is a heuristic and is labelled as such in the capability (`evidence: heuristic`).

Webhook: `callback_url` POST with the retrieve body; retries 3× when no 200 within 5s; no signature or shared secret documented → `Webhook=Unauthenticated` → not used.

Retention: task records 7 days; our metadata retention 30d exceeds it, so after 7 days reconciliation can no longer query BytePlus; `reconcile_by` must be < 7d (default 15 min / 2h satisfies).

Errors: create-time validation errors are synchronous HTTP 4xx with `error.code` (ModelArk error codes page); 2.5 task-type constraint errors can arrive asynchronously as `failed` with `InvalidParameter.TaskTypeConstraint`. Adapter maps synchronous 4xx to `failed/submit_rejected` (reservation released), 401/403 to `provider_access_denied`, 429 to retry (with the prepared-phase rule), 5xx to unknown/retry per section 7.2 of the architecture.

## 3. Google Veo via Gemini API (P05, P06)

Checked 2026-10-02: https://ai.google.dev/gemini-api/docs/veo (page footer "Last updated 2026-09-17 UTC").

- Endpoint: `POST https://generativelanguage.googleapis.com/v1beta/models/{model}:predictLongRunning` (REST samples), poll `GET https://generativelanguage.googleapis.com/v1beta/{operation.name}`; auth header `x-goog-api-key`. Result at `response.generateVideoResponse.generatedSamples[0].video.uri`; download requires the API key (files download).
- Models: `veo-3.1-generate-preview`, `veo-3.1-fast-generate-preview`, `veo-3.1-lite-generate-preview` (Preview), `veo-3.0-generate-001`, `veo-3.0-fast-generate-001` (Stable; PM notes the guide labels 3.0 deprecated: not re-verified here, keep no new 3.0 adapter per decisions ledger).
- Parameters table: `prompt`, `image`, `lastFrame` (with `image`), `referenceImages` (up to 3; **n/a for Lite**), `video` (extension; 3.1/3.1 Fast only; excluded by D04), `aspectRatio` 16:9|9:16, `durationSeconds` "4","6","8" (must be 8 with extension, references, 1080p, 4k), `personGeneration`, `resolution` 720p|1080p|4k (4k not for Lite), `seed` (Veo 3). Output 1 video per request, 24 fps, audio always on.
- Contradiction (Veo Lite references): the parameter table says `referenceImages` n/a for Lite while the Lite `durationSeconds` footnote says "Must be 8 when using reference images". Resolution: fail closed; Lite capability record has no `reference_image` role until a live request proves otherwise (AT32).
- Lifecycle: operation polling only; no cancel/delete documented on this page → `CancelSupport=None`; no webhook. Retention: videos stored 2 days (extension resets the timer). `OutputURLExpiry=48h`, `OutputACL=PrivateToken` (download needs API key). Idempotency: none; `CorrelationLookup=None` (operations list not documented for video on this page; unverified).
- Billability: "You will not be charged if your video is blocked from generating" (audio/safety block) → `BillsOnFailure=NotBilled` for safety blocks; other failures unverified.
- Pricing (PM 2026-09-30 from the pricing page): per output second USD 0.40 (720/1080) and 0.60 (4k) standard; Fast 0.10/0.12/0.30; Lite 0.05/0.08. Unit `output_seconds`.
- Latency: 11 s to 6 min (doc). Poll base 10 s (doc example).
- Region gates: EU/UK/CH/MENA `allow_adult` only for `personGeneration`.
- Credential reuse: existing `providers[]` gemini entry (`credential_ref`), the HTTP client pattern in `internal/providers/gemini/gemini.go`.

## 4. Google Gemini Omni Flash (P07)

Checked 2026-10-02: https://ai.google.dev/gemini-api/docs/omni (Last updated 2026-09-23 UTC) and https://ai.google.dev/gemini-api/docs/interactions-overview (2026-09-17).

- Endpoint: `POST https://generativelanguage.googleapis.com/v1beta/interactions` with `model: gemini-omni-1.1-flash`, `input` (text and media parts), `response_format {type: video, resolution: 360p|720p|1080p(upscaled), delivery: "uri" for outputs > 4 MB}`, `aspect_ratio` 16:9|9:16. REST response: video is inside `steps[].content[]` with `type: video`, `mime_type`, `data` (base64) or a `uri`; `output_video` is SDK-only (do not deserialize it from REST).
- Async pattern: the documented video samples are synchronous `interactions.create` calls that return `status: completed` with inline data. The Interactions overview advertises `background=true` for long-running tasks (background execution guide) and `interactions.get` retrieval; this stage did NOT verify that Omni video supports `background=true`, nor cancel, nor how long a `uri` stays valid. Resolution: Phase 1 capability record for Omni is `access: unverified` and disabled; the adapter is designed as a synchronous-call adapter with a bounded request deadline (proposed 10 min) and `submission_unknown` on timeout (no reconciliation possible without a verified background mode). Enablement requires verifying background mode + URI expiry under the approved smoke (AT32 lists Omni).
- Audio references: uploaded audio references unsupported; video references ≤ 3 clips ≤ 3 s, audio in them ignored. Duration control: no explicit duration parameter documented on this page → auto duration, unbounded estimate → `tariff` must be token-based with an upper bound from the pricing page (PM: USD/M tokens input 1.50, text output 9.00, video output 17.50) and a configured `max_video_output_tokens` ceiling (operator value; proposed hard stop until a measured sample exists).
- Regions: uploaded editing/extension restricted in EEA/CH/UK (excluded by D04 anyway).
- Retention: Interactions API stores requests by default (`store=false` opts out); gateway sets `store=false` unless multi-turn is needed (it is not, D04). SynthID watermark preserved.

## 5. Google Vertex (P08)

Checked 2026-10-02: https://docs.cloud.google.com/vertex-ai/generative-ai/docs/models/veo/3-1-generate (model cards) and the redirect target of the old reference URL, https://docs.cloud.google.com/gemini-enterprise-agent-platform/reference/rest/v1/projects.locations.endpoints/predict (page dated 2026-05-07).

- Model IDs: `veo-3.1-generate-001` (GA, release 2025-11-17, retirement "2026-11-17 or later"), `veo-3.1-fast-generate-001`; Lite card present on the page (PM). Region `us-central1`; consumption: Provisioned Throughput and Fixed quota supported, **Pay-as-you-go not supported** → access gated by quota purchase; audio input not supported; up to 4 outputs per prompt (native multi-output, D12 allows if approved); 4/6/8 s; references require 8 s; image ≤ 20 MB; 720p/1080p/4K (standard), 720p/1080p (fast).
- API binding: the generic `endpoints.predict` reference states that Veo video requests use `VideoGenerationModelInstance`/`VideoGenerationModelParams` on `POST https://{service-endpoint}/v1/projects/{project}/locations/{location}/publishers/google/models/{model}:predict`. The long-running variant (`:predictLongRunning` + `:fetchPredictOperation`) that earlier Vertex Veo docs used was NOT confirmed on the pages fetched today. Resolution: Vertex adapter remains `access: unverified` for the operation binding; implementation starts only after a live `predictLongRunning` vs `predict` check on an authorized project (no spend needed for a 400 probe, but it is a provider call and therefore waits for Rick's go-ahead). Pricing unverified. `CancelSupport` unverified. Credential reuse: Vertex path of `providers/gemini` (`isVertexAI`, project/location).

## 6. Runway (P09, P10)

Checked 2026-10-02: https://docs.dev.runwayml.com/openapi.json (646,945 bytes fetched).

- Base `https://api.dev.runwayml.com`; header `X-Runway-Version: 2024-11-06` (required, only version string present in the spec); `Authorization: Bearer`.
- Paths: `POST /v1/text_to_video`, `POST /v1/image_to_video`, `GET /v1/tasks/{id}`, `DELETE /v1/tasks/{id}`, plus `/v1/generate/video` (Model Router, not used: D02 forbids automatic model selection). Models in spec: `gen4.5`, `gen4_turbo`, `gen4_aleph` (video-to-video, excluded), image models.
- Task statuses (schema constants): `PENDING`, `THROTTLED`, `RUNNING`, `SUCCEEDED`, `FAILED`, `CANCELLED` (other constants belong to other resources). Poll rule: "Consumers of this API should not expect updates more frequent than once every five seconds for a given task" → `PollMinInterval=5s`.
- Cancel/delete: `DELETE /v1/tasks/{id}`: "Tasks that are running, pending, or throttled can be canceled by invoking this method. Invoking this method for other tasks will delete them... Aborted and deleted tasks will not be able to be fetched again." → `CancelSupport=QueuedAndRunning`, but the same verb deletes terminal tasks: the adapter must check the last observed state and never call DELETE on a terminal task (local delete must not call it either).
- Cost fields: task objects carry `estimatedCost` ("The maximum credits this task may charge. The final amount may be lower after the task completes.") and terminal `cost` ("Final cost in credits for a terminal task. Fully refunded tasks report 0..."). Use `estimatedCost.credits` to tighten the reservation after submit and `cost` for settlement; unit `credits`, USD 0.01/credit (PM pricing page), gen4.5 12 credits/s, gen4_turbo 5 credits/s, output-format surcharges (PM).
- Outputs: URLs expire within 24-48 hours; "fetch the task again to get fresh URLs" (workflow and conversation schemas say so explicitly; task output URLs per PM 24-48h) → `OutputURLExpiry=24h`, `OutputURLRefresh=true`.
- Idempotency: no idempotency header in the spec (grep "Idempotency" empty) → `SubmitIdempotency=None`, `CorrelationLookup=None`. Webhook: not verified. Audio references exist for some endpoints (spec descriptions) but not for `text_to_video` first-frame flows per PM; keep `Audio.InputRefs` empty until the exact schema is pinned.

## 7. Luma Agents (P11)

Checked 2026-10-02: https://docs.agents.lumalabs.ai/guides/videos/generation/.

- `POST https://agents.lumalabs.ai/v1/generations` with `model: ray-3.2`, `type: video`, `prompt` (1..6000 chars), `aspect_ratio`, `video {resolution 360p|540p|720p|1080p, duration 5s|10s, start_frame, end_frame, keyframes[1..64] + keyframe_indexes, hdr, exr_export, loop}`. Poll `GET /v1/generations/{id}` until `state` is `completed` or `failed` (initial `queued`); outputs are presigned MP4 URLs (`X-Amz-Expires=3600` in the example) → `OutputURLExpiry=1h`, `OutputURLRefresh=true` (PM: re-poll refreshes), `OutputACL=PrivateSigned`.
- Contradiction (HDR + 10s): the multi-keyframe prose says keyframes lift the anchor restrictions "arbitrary frame positions, `duration: "10s"`, and `hdr: true` are all supported", while the validation table says `10s` "is rejected with `hdr`, `start_frame`, or `end_frame`" and `hdr` "rejected with ... `10s`", and pricing says HDR is 5-second only. Resolution: fail closed on `hdr=true && duration=10s` in every mode; allow `keyframes + 10s (SDR)` and `keyframes + hdr (5s)` separately; `start_frame/end_frame + 10s` rejected (table). Reopen after a live probe.
- Cancel/delete/webhook: not on this page → `CancelSupport=None`, `Webhook=None` until verified. Idempotency: none documented. Pricing (PM): SDR 5s/10s USD 360p .06/.18, 540p .15/.45, 720p .30/.90, 1080p 1.20/3.60; HDR/EXR separate → unit `output_seconds` with a per-video grid. Audio support unverified (no audio parameter on this page) → `Audio.Generated=never` until verified.

## 8. Kling 3.0 (P12, P13)

Checked 2026-10-02: https://kling.ai/document-api/api/video/3-0-omni/text-to-video.md (LLM-optimized official markdown).

- `POST https://api-singapore.klingai.com/text-to-video/kling-3.0` (and `/image-to-video/kling-3.0` per PM), `Authorization: Bearer`. Body: `prompt` (≤ 3072 chars; multi-shot syntax), `settings {multi_shot, audio native|off, resolution 720p|1080p|4k, aspect_ratio 16:9|9:16|1:1, duration 3..15 int}`, `options {callback_url, external_task_id (unique within the account, queryable, does not replace the system id), watermark_info}`.
- Status values: `submitted`, `processing`, `succeeded`, `failed`. Query: `GET /tasks?task_ids=` or `?external_task_ids=` (batch, comma separated; cannot combine), list with `start_time` default `end_time - 30 days`, cursor pagination, `billing[]` with `charge_type`, `amount`, `currency CNY/USD`, `list_price`. Results "cleared after 30 days", hotlink protection on URLs → `OutputURLExpiry=30d` (hotlink-protected; `OutputACL=Unknown` until the protection mechanism is understood; copy promptly anyway).
- Idempotency: `external_task_id` is correlation, not dedupe (nothing says a duplicate `external_task_id` is rejected) → `SubmitIdempotency=None`, `CorrelationLookup=ByToken` (gateway sets `external_task_id = video_id`). Cancel/delete: not present in this document → `CancelSupport=None`. Callback: present, authentication not documented → polling. Pricing: billing fields per task; unit price sheet not recovered → unpriced (gate).
- P13 variants: unverified; registry has them as `access: unverified`.

## 9. MiniMax H3 (P14, P15)

Checked 2026-10-02: https://platform.minimax.io/docs/api-reference/video-generation-v2-create.

- `POST https://api.minimax.io/v2/video_generation`, Bearer auth. Body: `model` (`MiniMax-H3`, `MiniMax-H3-Max`), `content[]` (`text` required; `image_url` roles `first_frame`/`last_frame`; `reference_image` ≤ 9, `reference_video` ≤ 3 (2..15 s each, ≤ 15 s total, ≤ 50 MB), `reference_audio` ≤ 3 (≤ 15 MB); image-to-video and reference modes are mutually exclusive), `resolution` (required: H3 `768P|2K`; Max `480P|768P`), `duration` (required int: H3 4..15, Max 5..15), `ratio` (required for T2V, not `adaptive`; ignored/adaptive for I2V), `callback_url` (challenge verification, statuses `queued, running, succeeded, failed, cancelled`). Request body ≤ 64 MB.
- Response: `task_id`; query `GET /v2/query/video_generation/{task_id}` (PM) returns `task {id, model, status, created_at, updated_at, content.url, resolution, duration, usage {total_seconds, input_seconds, output_seconds, input_image_count}, ratio, task_type, modality}` → typed units `output_seconds`, `input_seconds`, `images`.
- Cancel/delete (PM): `DELETE /v2/video_generation/{id}` cancels queued or deletes succeeded/failed, not running → `CancelSupport=QueuedOnly`, never call on terminal from local delete. URL lifetime and webhook auth unverified (callback has a challenge handshake but no signature documented) → polling. Idempotency: none. Pricing (PM): USD/s H3 .08 (768P) / .13 (2K); Max .05 / .08; input charges separate. P15 legacy Hailuo: separate v1 contract unverified; registry `unverified`.

## 10. Alibaba DashScope Wan 3.0 and HappyHorse 1.1 (P16 to P18)

Checked 2026-10-02 via search-result snapshots of the official pages (bodies not fully fetched in this stage; mark field-level details as "to pin at implementation"):

- Wan3.0 API reference: https://www.alibabacloud.com/help/en/model-studio/wan3-video-generation-api-reference (Last Updated Sep 28, 2026; "Currently in preview"). `POST https://{WorkspaceId}.{region}.maas.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis` with header `X-DashScope-Async: enable`, Bearer `DASHSCOPE_API_KEY`; regions seen: ap-southeast-1, us-east-1, ap-northeast-1, eu-central-1 (+ cn-beijing, cn-hongkong for HappyHorse). Models `wan3.0-video`, `wan3.0-video-prime`. `input.prompt` and/or `input.media[] {type: first_frame (max 1) | last_frame (max 1) | reference_image (max 10) | reference_video (max 5, ≤ 15 s total) | reference_audio (max 5, ≤ 15 s total) | file | web_link..., url}`; `parameters {resolution 480P|720P|1080P, ratio adaptive|..., duration up to 30, prompt_extend, watermark}`. The all-in-one model routes edit/extend by prompt intent and media type. Task query via `/api/v1/tasks/{task_id}` (Wan 2.7 pattern, PM); "task_id and generated video URL are valid for 24 hours" (model release page).
- Resolution of the P16 gap: binding exists and is the same `video-synthesis` endpoint; the gateway only allows media types `first_frame|last_frame|reference_image|reference_video|reference_audio` and rejects `file`/`web_link`/`video` (edit source) references (provider-side fetch of arbitrary documents/web pages is both out of D04 scope and an SSRF-by-proxy risk). Exact enum names, status strings, cancel and expiry must be pinned from the full page at implementation (L09).
- HappyHorse: https://www.alibabacloud.com/help/en/model-studio/happyhorse-text-to-video-api-reference, `.../happyhorse-image-to-video-api-reference`, `.../happyhorse-reference-to-video-api-reference`: same endpoint; models `happyhorse-1.1-t2v`, `happyhorse-1.1-i2v` (no `ratio` parameter), `happyhorse-1.1-r2v` (`reference_image` list); `parameters {resolution 720P, ratio 16:9, duration 5}` in the example; watermark text "Happy Horse" when enabled. P18 binding resolved at the operation level; limits/enums to pin.
- Pricing (PM, Singapore): Wan3 USD/s .05/.10/.20 (480/720/1080), Prime .068/.14/.28; HappyHorse .07/.14/.18. Unit `output_seconds` (+ `input_seconds` for references, rule to pin). Idempotency none; correlation none documented; cancel unverified → `None`.

## 11. xAI Grok Imagine Video 1.5 (P19)

Checked 2026-10-02: https://docs.x.ai/developers/model-capabilities/video/generation (Last updated September 21, 2026).

- `POST https://api.x.ai/v1/videos/generations {model: grok-imagine-video-1.5, prompt, duration 1..15, aspect_ratio, resolution 480p|720p|1080p}` → `{request_id}`; `GET https://api.x.ai/v1/videos/{request_id}` → `status pending|done|expired|failed`, `video {url, duration, respect_moderation}`. T2V internally does text-to-image then image-to-video. Temporary URLs, lifetime unverified → `OutputURLExpiry=0 (unknown)`, copy immediately. Cancel/webhook not documented → `None`. Idempotency none. Pricing (PM): USD/s .08/.14/.25, image input .01. Reference-to-video with preset voices: separate page, pin at L09. Regions (PM model card): us-east-1/us-west-2.

## 12. Amazon Nova Reel 1.1 via Bedrock (P20)

Checked 2026-10-02: https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_StartAsyncInvoke.html.

- `POST /async-invoke` (bedrock-runtime, SigV4; reuse `providers/bedrock/auth.go`) body `{clientRequestToken (1..256 chars, "Specify idempotency token to ensure that requests are not duplicated"), modelId amazon.nova-reel-v1:1, modelInput {...}, outputDataConfig {s3OutputDataConfig {s3Uri}}, tags}` → `{invocationArn}`. Requires `bedrock:InvokeModel`; `GetAsyncInvoke`/`ListAsyncInvokes` for status (PM). Output `output.mp4` + manifest in the customer bucket; expiry governed by the bucket policy.
- Mapping: `SubmitIdempotency=Token` (gateway sets `clientRequestToken = video_id`), `CorrelationLookup=ByToken` via re-invoke with the same token (AWS semantics: same token → same invocation; dedupe window unverified, so re-invoke is attempted only within `reconcile.deadline` and the returned ARN is compared with any ARN found via `ListAsyncInvokes` filtered by time), `CancelSupport=None` (not verified), `OutputACL=PrivateToken` (customer S3), `OutputURLExpiry=0` (S3 object, gateway copies into its own blob store or serves from the bucket with range reads via SigV4). Tariff per output second: numeric amount not recovered (PM) → unpriced gate. Region us-east-1 only (PM).

## 13. fal (Pika P21, P22; LTX P23; PixVerse P24 aggregator route)

Checked 2026-10-02: https://fal.ai/docs/documentation/model-apis/inference/queue (page head; webhooks page per PM 2026-09-30 sources, not re-fetched).

- `POST https://queue.fal.run/{endpoint_id}` → `request_id` + `status_url`, `response_url`, `cancel_url` (REST). Statuses `IN_QUEUE`, `IN_PROGRESS`, `COMPLETED`; runner failures auto-retried up to 10 times by fal (so a single gateway submit may cost one generation but never two; good) ; result errors must be inspected on `COMPLETED`. Cancel via `cancel_url` only while queued (PM). Webhook: signed (Ed25519, JWKS, timestamp per PM sources) → `Webhook=Signed`, usable once the signature verification is implemented and tested (AT14); polling remains default. Output: fal CDN, public by default unless ACL/lifecycle configured (PM) → `OutputACL=PublicDefault` → enablement blocked pending Nikhil's exact authorization or proven private controls (hard stop, not decided here). Idempotency none; correlation none. Pricing: Pika 2.5 I2V USD .15/.25/.50 per 5 s at 480/720/1080 (PM, page inconsistency noted); Pika 2.2 and LTX unpriced → gate. All fal-hosted `status_url/response_url/cancel_url` must be validated to the `fal.run`/`queue.fal.run` hosts before use.

## 14. Candidates (P23 to P26) and retired (P27)

- PixVerse native v6 (`app-api.pixverse.ai/openapi/v2/video/{text,img}/generate`, API-KEY header, PM): status/upload/cancel/expiry unverified → `unverified`.
- Replicate inventory (P25): versions unverified → `unverified`.
- Runway third-party catalog (P26): optional explicit route; never a fallback; same Runway adapter with different model IDs, each pinned individually.
- OpenAI Sora (P27): retired 2026-09-24; no adapter; model IDs rejected with 400 `unsupported_model` (`reason: retired`) so aliases cannot be silently re-routed.

## 15. Summary capability flags per adapter (as designed)

| Service | SubmitIdempotency | CorrelationLookup | Cancel | Output expiry / refresh | OutputACL | BillsOnFailure | Webhook | Tariff |
|---|---|---|---|---|---|---|---|---|
| byteplus | None | Heuristic (list + safety_identifier) | QueuedOnly | 24h, 100 downloads / no | Unknown until smoke | Unknown | Unauthenticated (unused) | video_tokens (verified) |
| google_gemini (Veo) | None | None | None | 48h / no | PrivateToken | NotBilled for safety blocks | None | output_seconds (PM) |
| google_gemini (Omni) | None | None | None | unknown | unknown | Unknown | None | multimodal_tokens, upper bound required |
| google_vertex | None | None | unverified | unverified | PrivateToken (IAM) | Unknown | None | unverified |
| runway | None | None | QueuedAndRunning (DELETE) | 24-48h / yes | Unknown | cost field (refunds reported as 0) | unverified | credits (PM) |
| luma | None | None | None | 1h / yes | PrivateSigned | Unknown | None | per-video grid (PM) |
| kling | None | ByToken (external_task_id) | None | 30d / hotlink | Unknown | billing[] per task | Unauthenticated | unpriced |
| minimax | None | None | QueuedOnly (DELETE) | unknown | Unknown | Unknown | Challenge, unsigned | output_seconds + input (PM) |
| dashscope (Wan3, HappyHorse) | None | None | unverified | 24h (Wan3 release page) | Unknown | Unknown | unverified | output_seconds (PM) |
| xai | None | None | None | unknown | Unknown | Unknown | None | output_seconds (PM) |
| bedrock_nova | Token | ByToken (re-invoke) | None (unverified) | S3 policy | PrivateToken | Unknown | None | unpriced |
| fal | None | None | QueuedOnly (cancel_url) | configurable | PublicDefault | Unknown | Signed (per PM) | partial |

Every `Unknown`/`unverified` cell is a fail-closed gate in the capability registry, visible in the support matrix, and listed in `test-architecture.md` as a LIVE-only check.
