# TH-8088 test architecture

Stage: ARCHITECTURE draft, 2026-10-02. Maps PRD r2 acceptance AT01 to AT32 onto concrete test layers in `agentcc-gateway` at dev `ce6af27`. Nothing here has been run. LIVE cases remain gated behind Nikhil's spend approval.

## 1. Layers available in this repo

| Layer | Mechanism in repo | Used for |
|---|---|---|
| L-unit | `go test ./internal/video/...` table-driven tests | validation, normalization, state machine, estimator, fingerprint |
| L-contract | `internal/providers/video/videotest` shared suite: every adapter must pass `videotest.RunContractSuite(t, adapter, fixtures)` against an `httptest.Server` fake built from recorded fixtures (`testdata/<service>/*.json`: request shape assertions + canned responses) | per-provider request mapping, status normalization, error mapping, cancel semantics, output fetch |
| L-fake | httptest fake providers with scriptable behaviour (delays, 429 + Retry-After, 5xx, malformed body, slow body, URL expiry, out-of-order events) | lifecycle integration without network |
| L-redis | `skipIfNoRedis`-style helper reading `TEST_REDIS_ADDR` (pattern from `internal/redisstate/ratelimit_test.go:85-94`); each test uses a unique key prefix and flushes it | store, idempotency, leases, budget reservation, concurrency |
| L-crash | lifecycle worker driven by a `Clock` and `FailpointHook` interface (`hook.Before("submit.call")`, `hook.After("submit.received")`), tests inject panics/`os.Exit`-like aborts (goroutine abort + fresh worker instance over the same Redis) | AT11, AT24 |
| L-race | `go test -race` on `internal/video/...` and `internal/providers/video/...` (add to the scoped `-race` CI step) | AT09, AT16, AT21 |
| L-security | netguard-backed fetch tests with a local DNS-rebinding stub (httptest + custom resolver), redirect servers, oversized bodies | AT04, AT12, AT13, AT20, AT23 |
| L-api | `httptest` end-to-end through `server.New` with a memory key store and Redis store | AT12, AT13, AT25, AT26, AT30 |
| LIVE | real provider, approved key, spend ceiling, isolated environment; recorded as evidence bundles per R17 | AT27, AT28, AT29 and all `unverified` capability cells |

Fixtures: `internal/providers/video/<service>/testdata/` holds (a) `capabilities.json` (the registry record snapshot), (b) `submit_request.golden.json` per operation (what the adapter must send; asserted by the fake with a JSON-diff that ignores volatile fields), (c) `responses/*.json` recorded from the official docs (BytePlus retrieve response copied verbatim from the API reference examples), and (d) `errors/*.json`. Fixtures never contain real keys, prompts with PII, or real media; test media are generated 64×64 PNGs and 1-second synthetic MP4s built in `videotest` at test time (ftyp/moov only).

## 2. AT mapping

| AT | Layer(s) | Concrete tests (package: name) | Pass condition |
|---|---|---|---|
| AT01 | L-unit, L-api | `capability: TestValidate_BoundaryOptions`, `server: TestSubmit_UnsupportedModel400`, `TestSubmit_UnknownProviderOption400` | 400 with `param` naming the field; fake provider counter = 0; budget counter unchanged |
| AT02 | L-contract | `byteplus: TestSubmit_FirstFrameRole`, `TestSubmit_FirstLastRoles`, `TestSubmit_LastOnlyRejected` (Kling), golden request diff | image appears exactly once with the right `role`; missing/extra/invalid image rejected before any call |
| AT03 | L-unit | `capability: TestDurationEdges` (min, max, min-1, max+1, 2.5, NaN, Inf, "8" string), `TestPromptLimitUnit` (bytes vs chars vs utf16), `TestRatioResolutionCombos`, `TestDefaultsDisclosed` | table-driven, all rows |
| AT04 | L-security | `media: TestFetch_LoopbackRefused`, `TestFetch_MetadataRefused`, `TestFetch_RedirectToPrivateRefused`, `TestFetch_DNSRebindRefused` (resolver returns public then private), `TestFetch_Oversize413`, `TestFetch_MIMEMismatch`, `TestFetch_UserinfoRejected`, `TestFetch_NoAuthForwarded` (fake asserts no `Authorization` header), `capability: TestPublicACLBlocksEnable` | each refusal is a typed error, no secret in error text |
| AT05 | L-fake, L-redis | `lifecycle: TestSubmit_QueuedOnlyAfterReceipt` | job is `submitting` until the fake acks; after ack `queued` with `provider_job_id`; never `queued` without id (assert via Redis watch) |
| AT06 | L-fake, L-redis | `lifecycle: TestComplete_CopiesAndValidates` | fake serves MP4; artifact `available`, `GET /content` bytes == fake bytes, sniffed `video/mp4` |
| AT07 | L-contract, L-fake | `lifecycle: TestPoll_429RetryAfter`, `TestPoll_5xxBackoff`, `TestSubmit_401IsProviderAccessDenied`, `TestSubmit_ModerationFailure`, `TestPoll_MalformedBodySchemaError` | bounded retries, correct codes, submit count == 1 |
| AT08 | L-fake with fake clock | `lifecycle: TestRunDeadline_TimeoutPersists` | advance clock past `run_by`; status failed/provider_timeout, `upstream_may_continue=true`; restart worker (new instance, same Redis): `run_by` unchanged; reconciliation entry exists; alert metric incremented |
| AT09 | L-redis, L-race | `lifecycle: TestReplay_SameKeySameID`, `TestReplay_Concurrent100Replicas` (100 goroutines × 2 "replicas" over one Redis) | exactly one job id, one reservation (budget hash total == one estimate), fake submit count ≤ 1 |
| AT10 | L-api | `server: TestIdempotency_ChangedBody409`, `TestIdempotency_ScopedByOrg`, `TestIdempotency_MissingKey400`, `TestIdempotency_ExpiredKeyDocumented` (TTL expiry → new job, documented) | |
| AT11 | L-crash | `lifecycle: TestCrash_BeforeCall` (abort at `submit.call`, phase prepared → resubmit once), `TestCrash_AfterSendBeforeReceipt` (fake records request, abort before response read → `submission_unknown`; reconciliation: Kling fake resolves by token, BytePlus fake list resolves by safety_identifier, Luma fake → unresolved), `TestCrash_AfterReceiptBeforeSave`, `TestCrash_AfterSaveBeforeReply` (client replay returns same id) | no second provider submit in any case; `retry_safe=false` on unresolved |
| AT12 | L-api | `server: TestTenant_OrgBCannotReadA` (status/content/cancel/delete all 404), `TestTenant_ListNeverCrossesOrgs`, `TestTenant_CountMatchesRows` | response bodies identical to the not-found case |
| AT13 | L-api | `server: TestAuth_NoOrg403`, `TestAuth_RevokedKey401`, `TestRBAC_ModelDenied403`, `TestAllowedProviders403`, `TestManagedKeyBlockedWithoutTariff503`, `TestWrongRegionModelRejected` | all before any provider call (fake counter 0) and before reservation |
| AT14 | L-contract (fal only, when webhook enabled) | `fal: TestWebhook_ForgedSignature`, `TestWebhook_MissingHeaders`, `TestWebhook_ExpiredTimestamp`, `TestWebhook_Replay`, `TestWebhook_UnknownID`, `TestWebhook_DuplicateIsNoop` | ledger unchanged on duplicates |
| AT15 | L-unit | `lifecycle: TestTransitions_NoRegressFromTerminal`, `TestTransitions_DuplicateSuccessSettlesOnce`, `TestTransitions_UnknownStateIsSchemaError` | |
| AT16 | L-race, L-fake | `lifecycle: TestCancel_PreSubmitExcludesRacingSubmit` (cancel and submitter race on lease; exactly one wins), `TestCancel_QueuedMapsCapability` (byteplus QueuedOnly, runway QueuedAndRunning, luma None), `TestCancel_ReceiptNotFinal` | |
| AT17 | L-fake | `lifecycle: TestCancel_ProviderRefuses`, `TestCancel_Timeout`, `TestCancel_CompletionRace` | output retained, charge state truthful |
| AT18 | L-api, L-redis | `server: TestDelete_LocalOnly`, `TestDelete_TombstoneRetainsLedger`, `TestDelete_ReplayReturns410`, `TestDelete_LateCallbackCannotResurrect` (late poll result after delete updates ledger only), `TestDelete_Repeat200` | |
| AT19 | L-fake with fake clock + blob | `lifecycle: TestCopy_ProviderURLExpiredBeforeRead` (fake returns 403 after T), `TestCopy_FailureKeepsCompletedAndBillable` (artifact unavailable, settled), `TestCopy_RetriesBounded`, `TestRetention_LocalExpiry410` | no second submit, no regeneration |
| AT20 | L-api | `server: TestContent_RangeRequests`, `TestContent_MultiArtifactIndexes`, `artifacts: TestS3_PrefixOwnershipDenied` (key outside prefix rejected), `TestContent_NoSignedURLInTraces` (otel exporter capture) | |
| AT21 | L-redis, L-race | `lifecycle: TestBudget_ConcurrentReservationsCannotOverspend` (cap 10 USD, 50 concurrent submits of 1 USD → ≤ 10 accepted, Redis total == accepted×1), `TestBudget_SettleOnce` (duplicate terminal events), `TestBudget_UnknownBillabilityStaysUnsettled`, `TestBudget_PostPluginDoesNotDoubleRecord` | |
| AT22 | L-unit | `tariff: TestBytePlusTokens` (formula + minimum rule + input video), `TestMiniMaxInputOutputSeconds`, `TestRunwayCredits`, `TestOmniTokens`, `TestNovaS3Separate`, `TestUnknownTariffNotZero` | revision ids asserted |
| AT23 | L-security | `lifecycle: TestLogs_NoPromptNoMediaNoKeys` (slog capture), `TestMetrics_LabelAllowlist`, `TestTrace_SpansLinked` (submit/poll/result share trace id) | |
| AT24 | L-crash, L-redis | `lifecycle: TestRestart_ReplicaTakeover` (kill worker A mid-poll, worker B claims after lease TTL, same provider/account), `TestRedisOutage_FailClosedThenRecover` (stop Redis via test proxy; submit 503; resume; jobs continue), `TestKillSwitch_BlocksNewSpendOnly` | |
| AT25 | L-api, L-redis | `store: TestOwnerlessQuarantined` (seed hash with empty org → 404, not listed, never queued), `server: TestLegacyPathsStillServe` | |
| AT26 | L-api | `server: TestList_Empty200`, `TestList_StableTieOrder`, `TestList_InvalidFilters400`, `TestList_CountMatchesFilter`, `TestList_OffsetUnderInsertDocumented` | |
| AT27 | LIVE | `cmd/videosmoke` (new, build tag `live`) runs one Seedance text-to-video and one first-frame job with explicit model/region, records build SHA, config digest, gateway id, provider id, terminal status, downloaded file hash, playability (`ffprobe` if present), cost from `usage.completion_tokens` × tariff; writes an evidence JSON | requires approved key and spend ceiling |
| AT28 | LIVE | same tool per provider/mode matrix; unsupported modes recorded as N/A with schema citation | per provider |
| AT29 | Release audit (manual + generated) | `GET /admin/video/capabilities` export diffed against the support summary; Sora IDs rejected (`TestRetiredModelRejected`) | |
| AT30 | L-api + docs | config examples parsed by `config.Load` in `config_test`; disabled adapter → 503 `provider_not_configured`; secret scanner (existing CI) | |
| AT31 | L-fake with fake clock | `lifecycle: TestWorkerUnavailable_SubmitDeadline` (no worker running → status `submitting` then failed `submit_deadline_exceeded` at `submit_by`, reservation released) | never indefinite |
| AT32 | L-unit | `capability: TestFailClosed_LumaHDR10s`, `TestFailClosed_VeoLiteReferences`, `TestFailClosed_OmniAudioRef`, `TestFailClosed_Pika25T2V`, `TestFailClosed_Wan3FileRefs` | 400 `unsupported_combination` with citation id |

## 3. Shared contract suite (per adapter)

`videotest.RunContractSuite` runs, for every (model, operation) in the adapter's capability table: golden submit request; status normalization for each documented status string; unknown status → `SchemaError`; 429 with and without `Retry-After`; 5xx; 401/403; malformed JSON; output fetch success/expired; cancel behaviour per `CancelSupport`; reconcile behaviour per `CorrelationLookup`; usage parsing to typed units; no credentials in URLs; no provider URL leaks in `Observation` fields that reach responses. A new adapter cannot be registered without passing it (registry test enumerates adapters).

## 4. Crash-injection harness

`lifecycle.Worker` takes `Hooks` with named failpoints; tests wrap the worker in a goroutine, trigger a failpoint that cancels the worker context and discards the instance (simulating process death; leases are not released), then start a second worker over the same Redis with the clock advanced past `lease_ttl`. Assertions read Redis directly. The fake provider counts submits per `safety_identifier`/`external_task_id`/`clientRequestToken` so duplicate detection is exact.

## 5. LIVE-only list (gated behind Nikhil's spend approval)

- AT27/AT28 for every family.
- BytePlus: output URL ACL behaviour (signed vs public), failed-task billing, model-level RPM/concurrency values, list heuristic effectiveness.
- Veo: Lite references contradiction; cancel absence confirmation; `generatedSamples` schema on a real operation.
- Omni: `background=true` support for video, `uri` expiry, duration behaviour, token usage reporting.
- Vertex: `predict` vs `predictLongRunning` binding; tariff; cancel.
- Runway: `estimatedCost`/`cost` presence on text_to_video tasks; URL refresh.
- Luma: HDR + 10s with keyframes; audio; cancel.
- Kling: cancel; hotlink protection; billing fields.
- MiniMax: URL lifetime; callback challenge.
- DashScope: Wan3/HappyHorse status strings, cancel, expiry; reference billing.
- xAI: URL lifetime; expired status timing.
- Nova: clientRequestToken dedupe window; S3 permissions; tariff.
- fal: effective CDN ACL; webhook signature verification against real JWKS.

## 6. CI changes (proposed)

Add `./internal/video/... ./internal/providers/video/...` to the scoped `-race` step; Redis-dependent tests skip without `TEST_REDIS_ADDR` locally and run in CI (service already present); `live` build tag never runs in CI; generated-contract check unchanged unless the admin schema gains video capability types (see implementation plan).
