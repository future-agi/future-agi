# Outbound Channels Contract (v1.4)

**Date:** 2026-08-25 · **Status:** FROZEN — conform, don't redesign
**Owners / defects:** Khushal (guest emitters) ↔ Azain (platform ingestion).
**Pinned against:** seam contract ≥ v1.5.
**Changelog:** 1.4 (2026-08-25) — `baseline_frozen` cardinality pinned:
one event per store (multi-store bundles were ambiguous). Terminal/fence
ordering clarified: "fenced never emits a terminal" means fenced before
the terminal was delivered; a fence arriving after the delivered
terminal (e.g. on the manifest push) does not retract it — guest still
exits 3, platform keeps the terminal, gateway's superseded record wins.
Scenarios channel: transport is owned here, but the provision/begin BODY
schema + paths are the Scenario Generation Contract's (Karthik) —
unpublished, flagged by three independent implementations, must land
before integration.
1.3 (2026-08-25) — Authentication: capabilities-file
rejection table added (the file's failure vocabulary had no owner — a
guest that cannot load capabilities has no channel and can only signal
via exit code); canonicalization: `allow_nan=False` made explicit (NaN /
Infinity are not RFC 8259 — the emitter pre-checks and fails typed);
events: guest treats `acked_through_sequence` as untrusted — values
outside `[watermark, next_sequence)` are rejected locally, never applied.
1.2 (2026-08-24) — one-receipt-per-scenario rule, budget as
upload admission, attempt registration + cancel signal (spine-paired),
job-scoped pre-allocation, watermark semantics, duration_ms, error-map
holes, kind additions, manifest digest scope, skipped/errored bodies.
1.1, 1.0 — superseded.

The three channels the guest uses to report to the platform: **events**,
**result receipts**, **artifacts**. All traffic is outbound HTTPS; the
platform never calls in. Guest claims are evidence, not authority — the
platform's finalizer decides terminal truth.

## Shared vocabulary (inline, so this file is standalone)

`FailureDomain`: `agent` · `simulator` · `environment` · `connectivity` ·
`infrastructure` · `grading` · `platform_sync`.

`HarnessStage`: `queued`, `acquiring_source`, `understanding_agent`,
`generating_environment`, `building_environment`, `validating_environment`,
`generating_data`, `generating_scenarios`, `validating_scenarios`,
`connecting_agent`, `running`, `grading`, `uploading_artifacts`,
`cleaning_up`, and the terminal three: `completed`, `failed`, `canceled`.

**Canonicalization (every digest in this file):** serialize with Python
`json.dumps(value, sort_keys=True, separators=(",", ":"),
ensure_ascii=False)`, encode UTF-8, sha256 — and `allow_nan=False`: for
valid input the bytes are identical, and NaN/Infinity (not RFC 8259) fail
typed at the emitter instead of producing unparseable bytes (v1.3).
Scopes: event digest = the
`payload` object; receipt digest and manifest digest = the whole object
with the `digest` key absent. Fields marked "always present" are emitted
as `null` when unknown — absent and `null` are different bytes. Serialize
once, spool the bytes, re-send verbatim; never re-serialize on retry. The
platform verifies receipt and manifest digests; event digests MAY be
verified; mismatch → `422 digest_mismatch`.

**Timestamps:** RFC 3339, UTC, `Z`, millisecond precision; display-only
and non-authoritative (the platform stamps `received_at`; ordering is by
`(attempt_number, sequence)`). Durations cross as explicit
monotonically-measured `*_ms` integer fields, never derived from
timestamps.

**Error responses:** every 4xx/5xx body is
`{"error": "<code>", "message": "...", "retryable": bool}`. Guest
behavior by status: `401` (expired) / `403` (fence, scope, mismatch) →
stop emitting, exit code 3 (spine §0.6 — superseded, never an infra
retry); `400`/`409`/`422` → permanent for that item: surface via a `log`
event (level `error`) and continue; `404` → retry 3× then treat the
channel as failed and finalize `platform_sync`; `413
artifact_budget_exceeded` → stop uploading non-reserved kinds, log,
continue the run; `429` → honor `Retry-After`; `5xx`/network → retry
with backoff (base `retry.initial_backoff_seconds`, cap
`retry.max_backoff_seconds`, full jitter). **Catch-all: any unlisted 4xx
is permanent for that item.** Emission is an async flusher over the
fsync-first local spool — it never blocks the call loop.

## Authentication — `/run/futureagi/capabilities.json`

The gateway **registers the attempt with the platform before creating the
sandbox** (spine §0 step 2a): the platform mints the token and records
`(organization, job_id, attempt_id, attempt_number, fence, expires_at)` —
that row is what ingestion validates bearers and fences against. The
gateway then uploads this file: owner `svc-control`, mode 0600, loaded
into memory at emitter startup and unlinked; never placed in any customer
process's environment or argv.

```json
{
  "schema_version": "futureagi.harness-capabilities.v1",
  "job_id": "uuid",
  "attempt_id": "uuid",
  "attempt_number": 1,
  "fence": "opaque-string",
  "expires_at": "2026-08-25T12:00:00.000Z",
  "token": "bearer",
  "endpoints": {
    "events":    "https://<platform>/simulate/api/harness/attempts/<attempt_id>/events/",
    "results":   "https://<platform>/simulate/api/harness/attempts/<attempt_id>/results/",
    "artifacts": "https://<platform>/simulate/api/harness/attempts/<attempt_id>/artifacts/",
    "scenarios": "https://<platform>/simulate/api/harness/attempts/<attempt_id>/scenarios/"
  }
}
```

- Every request: `Authorization: Bearer <token>` +
  `X-Harness-Fence: <fence>`.
- **Token lifecycle:** valid for this attempt until `expires_at`; NOT
  one-use — it authenticates every request the attempt makes; never
  replayable into another attempt. The gateway MUST set
  `expires_at ≥ sandbox TTL + flush window (120s) + 300s`.
- **Every `endpoints.*` value ends with `/`; the guest concatenates,
  never path-joins.**
- **Scope:** append/upload/submit on the four endpoints; the `scenarios`
  provision/begin responses are the only reads; no finalization.
- **Binding:** the token's attempt is authoritative; a body whose
  `job_id`/`attempt_id`/`attempt_number` disagrees → `403
  attempt_mismatch`.
- **Supersession:** `attempt_number` (1-based, monotonic per job) appears
  here and on every event and receipt; ingestion rejects writes from
  below the job's high-water number (`409 attempt_superseded`), which
  advances when the gateway registers the next attempt — so a fenced
  attempt's in-flight requests cannot land after registration of its
  successor.

**Scenario pre-allocation under hosted:** the guest performs the
provision call (Scenario Generation Contract §3) and the
execution-row pre-allocation (`start` + `batch`, the `platform.py::begin`
sequence) against `endpoints.scenarios` with this bearer —
`X-Api-Key`/`X-Secret-Key` are local-SDK-only. Response envelope is the
existing `{"result": {...}}`. Rules: **job-scoped and idempotent** — a
retried attempt re-reads the existing registration (matching
`scenario_key`s return their existing ids; it never creates a second
`RunTest`). A retried attempt also **reuses the prior attempt's sealed
scenario set** when its artifacts exist; it regenerates only if none were
sealed — `scenario_key` is thereby a stable cross-attempt identity.
**Cross-doc requirement (pending in Karthik's document):** each persona
entry in the provision payload carries `scenario_key`. Provision success
is a prerequisite for `running`; failure after retries fails the job in
`validating_scenarios`, domain `platform_sync`.

**Capabilities-file rejection table (v1.3; closed).** A guest that cannot
load `capabilities.json` has NO channel — no token, no endpoints — so it
cannot report its own failure: it exits non-zero (and not 3), which the
gateway records as an `infrastructure` failure in stage `queued` (this
turns spine §0 step 6's catch-all from an accident into the rule). No
event is emitted for any of these. Codes (typed on the guest's
`CapabilitiesError`):

| code | meaning |
|---|---|
| `capabilities_file_missing` | the gateway never performed spine §0 step 4 |
| `capabilities_file_unreadable` | OS-level read failure (permission, IO) |
| `capabilities_file_malformed` | not parseable JSON, or not a JSON object |
| `capabilities_field_invalid` | a field fails schema validation (messages built from field paths only — never echo values: the file carries the bearer) |
| `capabilities_schema_unsupported` | `schema_version` this guest does not implement |
| `capabilities_endpoint_invalid` | an endpoint violates shape rules (e.g. trailing slash) |
| `capabilities_endpoint_insecure` | a non-`https` endpoint (all traffic is outbound HTTPS) |
| `capabilities_expired` | `expires_at` already past at load — an infrastructure fault, NOT a supersession; must not surface as exit 3 |
| `capabilities_attempt_mismatch` | an endpoint's `<attempt_id>` path segment disagrees with the declared `attempt_id` — caught locally instead of as the platform's eventual `403` |

## Channel 1 — Events

`POST <endpoints.events>` with a batch (≤100 events, each `payload`
≤32 KB):

```json
{
  "schema_version": "futureagi.harness-event.v1",
  "events": [
    { "event_id": "event_<32hex-or-uuid>",
      "job_id": "uuid", "attempt_id": "uuid", "attempt_number": 1,
      "sequence": 17,
      "emitted_at": "2026-08-25T10:14:03.412Z",
      "stage": "running",
      "type": "scenario_started",
      "payload": { "scenario_key": "…", "world_index": 2, "scenario_attempt": 1 },
      "digest": "sha256:<64-hex of canonical payload>" } ]
}
```

Response `200`:
`{ "acked_through_sequence": 17, "rejected": [ { "event_id": "…", "sequence": 5, "code": "…", "message": "…" } ] }`.
The guest treats `acked_through_sequence` as untrusted input: a value
below the current watermark or at/above the guest's next unallocated
sequence is rejected locally (typed error, watermark unchanged) — a
malformed ack must not be able to discard pending records (v1.3).

- `event_id`: opaque ≤64 chars, unique per attempt (`event_<32hex>` is
  legal; strict UUID not required).
- **Sequencing:** one allocator, one lock, assigned at spool append,
  contiguous from 1. The platform orders by `(attempt_number,
  sequence)`; **the watermark is highest-processed** — accepted AND
  rejected sequences both advance it, so a rejected event closes its
  sequence and creates no gap. Genuine gaps (lost batches) are held up
  to 60s, then released and recorded.
- **Delivery:** at-least-once; dedupe on `event_id`; the guest advances
  its spool cursor through the watermark; a rejected event is dropped
  from the spool and its payload written to the artifact spool (`log`
  kind) — it is never re-emitted.
- **Event `type` vocabulary (closed; unknown → per-event rejection
  `event_type_unknown`):**
  - `stage_changed` `{from, to}` — `from` always present, `null` on the
    first transition; event-level `stage` equals `to`.
  - `parallelism_degraded` `{requested, effective, reason}` — `effective`
    `1..requested-1` (V1 emits 1); `reason` ∈
    `conformance_gate_failed | fixed_port`.
  - `baseline_frozen` `{inputs_digest, baseline_ref}` — the §5.3 build
    summary, structured. Cardinality: ONE event PER STORE — a bundle
    declaring several stores emits one `baseline_frozen` for each
    (`baseline_ref` identifies the store's baseline; consumers must not
    assume a single event per attempt) (v1.4).
  - `baseline_inputs_changed` `{previous_digest, current_digest}` —
    `previous_digest` always present, `null` when none.
  - `world_unhealthy` `{world_index, cause}` — `cause` free text ≤200.
  - `scenario_started` `{scenario_key, world_index, scenario_attempt}` —
    `scenario_attempt` ∈ {1, 2}.
  - `scenario_retried` `{scenario_key, from_world, to_world}`.
  - `log` `{level, message}` — `level` ∈ `debug|info|warning|error`;
    truncated to fit with a trailing `…[truncated]` marker.
  - `terminal` `{stage, reason, failure, scenario_counts}` — exactly one
    per attempt, last emitted. `stage` ∈ the terminal three, equal to
    the event-level `stage`; `reason` ∈ `null | ttl_exceeded |
    user_canceled` (a fenced attempt never emits a terminal event — it
    exits 3; ordering clarification, v1.4: "fenced" here means fenced
    BEFORE the terminal event was delivered — a fence that lands after
    the terminal is on the wire but before later traffic, e.g. the
    manifest push, does not retract the delivered terminal: the guest
    still exits 3, the platform keeps the terminal it accepted, and the
    gateway's superseded record wins for attempt bookkeeping);
    `failure` = `{domain, stage, code, message}` or null;
    `scenario_counts` `{passed, failed, errored, skipped}` (advisory —
    receipts are the truth).
- **Redaction (enforced before emit):** no secret values, no endpoint
  userinfo, no raw audio/transcript content; the emitter runs the same
  secret-content scan as the artifact sealer.

**Cancellation signal (spine §0/§5):** the gateway writes
`/run/futureagi/cancel.json` `{"reason": "user_canceled" |
"ttl_exceeded"}` and then sends SIGTERM to the entrypoint; the guest
reads the reason for its terminal event. SIGTERM (or TTL) starts the
**120s flush window**; the gateway MUST NOT delete the sandbox before
the guest exits or the window elapses.

## Channel 2 — Result receipts

**At most one receipt per `scenario_key`, emitted only when the scenario
reaches its final local status** — after the single retry (if any)
resolves. The failed first try is recorded by `scenario_retried` /
`world_unhealthy` events, never by a receipt. `POST <endpoints.results>`:

```json
{
  "schema_version": "futureagi.harness-result.v1",
  "job_id": "uuid", "attempt_id": "uuid", "attempt_number": 1,
  "scenario_key": "suspended-account-blocked",
  "scenario_id": "<platform id from pre-allocation>",
  "scenario_attempt": 1,
  "world_index": 2,
  "status": "passed | failed | errored | skipped",
  "sub_goals": [ { "name": "…", "held": true, "reason": null,
                   "judged": false } ],
  "evaluations": [
    { "name": "customer_agent_task_completion", "kind": "metric",
      "score": 0.86, "reason": "…" },
    { "name": "…", "kind": "checkpoint", "passed": true, "reason": "…" } ],
  "call": { "started_at": "…", "ended_at": "…", "duration_ms": 184211,
            "turns": 5,
            "transcript_artifact": "sha256:<id> | null",
            "recording_artifacts": ["sha256:<id>"] },
  "failure": null,
  "digest": "sha256:<64-hex>"
}
```

- **Join:** `scenario_id` REQUIRED (pre-allocation is a prerequisite and
  idempotent across attempts, so key↔id is stable). The platform
  resolves attempt → job → run via the registration row; receipts never
  carry `run_id`.
- **Idempotency:** keyed `(job_id, scenario_key)`. Same key + digest →
  `200` duplicate. Same key, same attempt, different digest → `409
  receipt_conflict` (platform keeps the first; guest logs, no retry).
  Higher `attempt_number` supersedes; lower → `409 attempt_superseded`.
- `status`: `passed` (all sub-goals held) / `failed` (≥1 not held — an
  agent result) / `errored` (no verdict; `failure` set — the code table
  lives in the world-handle contract) / `skipped` (never ran).
- **`skipped` receipt body (exact):** `scenario_attempt: 1`,
  `world_index: null`, `sub_goals: []`, `evaluations: []`, `call: null`,
  `failure: null`. The guest synthesizes these during the flush window;
  the finalizer backfills any still missing and marks the attempt
  evidence-partial.
- **`errored` receipt body:** `call` is `null` unless the call started;
  if it started, `started_at`/`duration_ms` are measured and `ended_at`
  is the abort time. `sub_goals` lists every declared goal with
  `held: null, reason: null` for the unevaluated ones. `failure` set
  (`{domain, stage, code, message}`, vocabularies above).
- `sub_goals[].judged` — boolean: `SubGoal.judged != ""`.
- `evaluations` — two closed variants: `kind: "metric"` requires
  `{name, kind, score (0.0–1.0), reason}`; `kind: "checkpoint"` requires
  `{name, kind, passed (bool), reason}`. Empty list when none apply.
- `call.duration_ms` — monotonic, authoritative; the timestamps are
  display-only.
- **Ordering:** referenced artifacts uploaded and acked BEFORE the
  receipt (`422 artifact_unknown`, evaluated within the job's
  namespace). `transcript_artifact` null only when the artifact level
  excludes transcripts or the upload was refused by budget (named in a
  `log` event either way).

## Channel 3 — Artifacts

Content-addressed, write-only, immutable, scoped to
`(organization, job_id)`: `200 already-exists` and `artifact_unknown` are
evaluated within the job's namespace — an attempt may reference artifacts
from an earlier attempt of the same job; nothing outside the job is ever
confirmed or referenced.

**3a. Upload.** `PUT <endpoints.artifacts><64-hex>/` (bare hex in URLs;
`sha256:` prefix in JSON fields only; `manifest` is a reserved segment).
Headers: `X-Artifact-Kind`, `X-Artifact-Size` (authoritative; mismatch →
`422 size_mismatch`), `Content-Type`, optional `X-Scenario-Key`.
Responses: `201` stored / `200` already exists / `422 digest_mismatch`
(re-upload once, then the referencing scenario is `errored`) / `413
artifact_budget_exceeded` / `422 artifact_level_forbidden` (a kind the
job's level forbids; guest logs, scenario NOT errored). Uploads >64 MB
use chunked transfer.

`kind` vocabulary (closed): `recording_combined`, `recording_stereo`,
`recording_customer`, `recording_assistant`, `transcript`, `tool_trace`,
`result`, `build`, `trace`, `log`, `other`. (Local sealer mapping:
`agent-tool-calls.jsonl` → `tool_trace`; `result.json` → `result`.)

**Formats:** recordings mp4 (AAC-LC, ≥16 kHz; stereo = customer left,
assistant right). `transcript` = JSON array of
`{speaker_role: "assistant"|"user"|"system"|"tool_calls"|
"tool_call_result", content, start_time_ms?, end_time_ms?}`.

**Budget = upload admission** (there is no retroactive dropping —
uploads are immutable): `job.artifacts.max_artifact_bytes` is a
**per-job cumulative** cap across attempts, deduplicated by digest; the
platform enforces it server-side (+10% slack → `413`), the guest
enforces it first. The budget is partitioned by reservation:
`build` + `transcript` + `tool_trace` + `result` are reserved (always
admitted); recordings next; `trace`/`log`/`other` last — when the
remaining budget cannot admit an artifact of a non-reserved kind, the
upload is **refused newest-first** and named in a `log` event; the
receipt then carries `null`/omits that id. Silent truncation is
forbidden. (Implementation delta: `seal_artifacts` raises at the cap
today and treats level-suppressed kinds as missing; the hosted sealer
refuses-by-class with a log event and treats level-suppressed kinds as
not-expected.)

**Artifact level table** (`job.artifacts.level`; ingestion reads the
level off the job record):

| level | must upload | must not upload |
|---|---|---|
| `metadata-only` | `build`, `result`, `log` | recordings, `trace`, `tool_trace`, `transcript` |
| `traces` | + `trace`, `tool_trace`, `transcript` | recordings |
| `traces-and-recordings` | + recordings | — |
| `full` | same as traces-and-recordings, plus `other` is admitted | — |
| `local-only` | rejected at hosted admission (`local_only_not_hosted`) | |

`other` is admitted only at `full`; it is never required at any level.

**3b. Manifest.** After the terminal event, `POST
<endpoints.artifacts>manifest/` (trailing slash — Django POSTs don't
survive the redirect):

```json
{ "schema_version": "futureagi.harness-manifest.v1",
  "job_id": "uuid", "attempt_id": "uuid", "attempt_number": 1,
  "entries": [ { "artifact_id": "sha256:<id>", "kind": "…",
                 "size": 1048576, "scenario_key": "…" } ],
  "complete": true,
  "digest": "sha256:<64-hex of the whole object minus digest>" }
```

Idempotent on `(attempt_id, digest)` → `200`; the digest covers the
whole object (including `complete` and the ids), so a later
`complete: true` manifest is a distinct document that supersedes an
earlier `complete: false` one from the same attempt. The platform
verifies every entry exists in the job's namespace with matching size.
The manifest ack is a completion precondition for the finalizer; the
guest retries within the flush window, then exits 0 (the terminal event
is already delivered). An unacked manifest finalizes the attempt
`platform_sync`; already-accepted receipts are retained; whether the
gateway re-attempts is decided by `retry.retryable_domains` alone
(`platform_sync` retries only if the job listed it).

## Sequencing (normative, per attempt)

```
events flow from entrypoint start
per scenario: artifacts (transcript, recordings) → result receipt
…
terminal event → skipped receipts → manifest → exit 0
```

Flush window: 120s from the cancel signal / TTL / terminal event. Drain
priority: terminal event → receipts (incl. synthesized `skipped`) →
manifest (`complete: false` legal on cancellation; finalizer treats it
as evidence-partial, not invalid).

## Failure semantics summary

| Condition | Guest | Platform |
|---|---|---|
| network / 5xx / 429 | spool + backoff (honor Retry-After) | — |
| 401 expired / 403 fence, scope, mismatch | stop emitting, exit 3 | superseded; no infra retry |
| 400 / 409 / 422 (item-level) | log, permanent for the item | reject; keep first on conflicts |
| 404 | retry 3×, then finalize `platform_sync` | — |
| 413 budget | stop non-reserved uploads, log, continue | reject |
| duplicate event / artifact / manifest | — | idempotent accept |
| manifest never acked | exit 0 after window | finalize `platform_sync`; receipts retained |

## Implementation deltas (code this contract changes)

- `events.py`: highest-processed watermark ack with per-event rejections
  (today: id-set ack), contiguous per-attempt sequence allocator,
  spool-the-bytes re-send.
- A hosted event model (`sequence`/`attempt_id`/`attempt_number`/`stage`/
  `digest`); the existing `CanonicalEvent` (`run_id`, `monotonic_ns`,
  `futureagi.simulation-event.v1`) remains the local-SDK wire.
- `artifacts.py::seal_artifacts`: refuse-by-class budget + level
  awareness; local-kind → wire-kind mapping.
- Platform (none of this exists today): attempt registration + token
  minting (gateway-called), attempt-token authentication resolving
  attempt → job → organization (the fleet `InternalServiceAuthentication`
  secret and `X-Api-Key` are both unusable), the four endpoint routes,
  event/receipt/manifest ingestion with the idempotency keys above, and
  idempotent job-scoped provision/begin behind `endpoints.scenarios`.
- **Scenarios channel payload ownership (v1.4):** THIS contract owns only
  the transport for `endpoints.scenarios` — bearer auth, the shared fence
  latch (a 401/403 here fences all four channels), deadlines, and the
  error taxonomy. The `provision`/`begin` request/response BODY schema
  and path suffixes are owned by the Scenario Generation Contract
  (Karthik) and are NOT yet published; the guest keeps them injectable.
  That contract MUST land before integration — three independent
  implementations have now flagged this same gap.
- Spine v1.5 records: attempt registration (§0 step 2a), cancel.json +
  SIGTERM + the 120s pre-delete hold (§0/§5), exit code 3, evidence_seam
  bundle field, `no_sql_store` preflight, §5 step 3.5, degrade reasons
  trimmed to `conformance_gate_failed | fixed_port`.
