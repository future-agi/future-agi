# Hosted Runs — frontend contract (v1)

**Date:** 2026-08-25 · **Status:** four claim classes, marked per section —
FROZEN (matches a frozen source contract — build strictly) · VERIFIED
(checked against v0/the codebase) · UNCONFIRMED (an existing surface that
probably carries this — Azain to confirm) · PROPOSED (a shape this doc
invents — Azain owns the final paths/field names).
**Owners:** Khushal (vocabularies, this doc) · Azain (platform ingestion +
read endpoints — everything marked PROPOSED/UNCONFIRMED needs his
confirmation) · Nosang (consumer).
**Companion:** `frontend-contract.md` v0 (the interactive authoring UI —
sessions/stages/SSE via `/simulate/harness/`) is unchanged and stays valid.
This doc covers the NEW surface: watching a hosted execution job and reading
its results. Pinned against: hosted-execution-seams.md v1.9,
outbound-channels.md v1.2, world-handle-interface.md v3.2.

## Mental model

```
job ─▶ attempt(s) ─▶ scenarios (each runs in an isolated "world")
```

A **job** is one hosted test of a customer agent. The platform's gateway runs
it in a sandbox; the sandbox reports outbound (events, per-scenario result
receipts, artifacts). A **world** is one isolated parallel copy of the test
environment; scenarios are distributed across worlds so they can run
concurrently without seeing each other's state. A job may be retried as a
new **attempt** (`attempt_number`, 1-based; attempts are capped by job
config, and only the failure domains the job lists in
`retry.retryable_domains` are retried — typically
infrastructure/connectivity. Design for 1–2 attempts, not N).

**Attempt semantics (FROZEN):** receipts are job-scoped, keyed
`(job_id, scenario_key)`; a higher attempt supersedes the same key in place.
The scenario list is therefore always the latest known result per scenario —
a merged view, not a per-attempt snapshot. Receipts from an earlier attempt
persist until a higher attempt overwrites the same key, so if attempt 2
fails before it reaches `running`, attempt-1 results stay on screen.
`attempt_number` is a badge only. Build no cross-attempt diff view: the
contract guarantees only one winning result per `(job_id, scenario_key)`,
and whether prior-attempt receipts are retained at all is part of Q1.

The frontend talks ONLY to the Django backend; it never reaches the gateway
or sandbox.

**Where results live — THE open question for Azain (Q1).** Scenario
pre-allocation is settled: the guest's provision/begin calls create the same
`RunTest` + execution rows today's voice simulations use, and never a second
`RunTest` (outbound-channels §auth). What is NOT settled is whether receipt
ingestion writes verdicts/evaluations/artifact refs back onto those
execution rows (the way `platform.py::send_result` does on the local path)
or stores receipts separately behind a new read surface — and, as part of
the same question, whether the receipt's `scenario_id` is the
`call_execution_id` that `begin`'s batch pre-allocates per scenario (the
id `/simulate/call-executions/<id>/` keys on; note the split — one TEST
execution per run, one CALL execution per scenario). If write-back lands, the existing run-detail
UI is the results view with no new read work; if not, Azain must define a
results read endpoint. Until he answers, build result components against
the receipt shape below (mock data) — it is correct either way.

## Vocabularies (FROZEN — safe to build against today)

The stage / status / domain / event-type / kind / level enums are closed —
build strict enums for all six. Four smaller closed sets ride along and
deserve strict types too: `terminal.reason`, `log.level`, evaluation
`kind`, transcript `speaker_role`. The failure `code` values are the one
exception: their tables are closed where labelled (seams §2e/§2f) but live
across documents (errored-receipt codes in world-handle-interface.md), so
the UI treats `code` as an opaque string and renders unrecognized ones
gracefully.

### Job stage — `HarnessStage`

Ordered; render as a progress sequence. Suggested UI labels in parens.

1. `queued` (Queued)
2. `acquiring_source` (Fetching repository)
3. `understanding_agent` (Analyzing agent)
4. `generating_environment` (Designing environment)
5. `building_environment` (Building environment)
6. `validating_environment` (Validating environment)
7. `generating_data` (Seeding data)
8. `generating_scenarios` (Writing scenarios)
9. `validating_scenarios` (Validating scenarios)
10. `connecting_agent` (Connecting to agent)
11. `running` (Running scenarios)
12. `grading` (Grading)
13. `uploading_artifacts` (Uploading results)
14. `cleaning_up` (Cleaning up)

Terminal three (exactly one, mutually exclusive): `completed`, `failed`,
`canceled`. Display guidance (not a wire guarantee): treat progress as
forward-only WITHIN an attempt and build the stepper skip-tolerant —
seeing stage N implies every earlier stage of that attempt is done,
whether or not it was observed (the first stages are gateway-side and may
never surface; quick stages can be missed between polls). When
`attempt_number` increments in the poll payload, reset the stepper and
re-track from the new attempt's stage — a retried job passes back through
the early stages. A TTL-expired job terminates as `canceled` with
`reason: ttl_exceeded` (there is no timeout terminal) — key the chip off
`terminal` and the explanatory copy off `reason`.

A failed job names the stage it failed IN via `failure.stage` — drive the
banner copy from it ("Failed while building environment", never just
"Failed" — the copy is "Failed while " + the stage's suggested label,
lowercased). A job-level `failure.domain: agent` means the customer's own
code broke (its process failed to build or start) — still a fault report,
not a grade. In the poll payload, top-level `stage` holds the terminal
value once `terminal` is non-null; the failed-in stage lives only in
`failure.stage` (part of Q2 — PROPOSED shape).

### Scenario status (4-way, closed)

- `passed` — every sub-goal held (an agent result).
- `failed` — ≥1 sub-goal not held (an agent result — the run worked; the
  agent fell short). Render as a finding, not an error.
- `errored` — no verdict exists; something broke (`failure` object set).
  Render as a run-level fault badged by its `domain` — for scenario
  receipts that is typically `simulator`, or `environment` for an
  unavailable world (per the world-handle contract's code table).
- `skipped` — never ran (job ended first). Exact receipt body:
  `scenario_attempt: 1`, `world_index: null`, `sub_goals: []`,
  `evaluations: []` (empty arrays, not null), `call: null`,
  `failure: null`. Neutral rendering; no artifacts exist.

The failed-vs-errored split is the product's core honesty rule: **never
present an `errored` scenario as the agent failing its test.**

### Failure attribution — `FailureDomain`

`agent` · `simulator` · `environment` · `connectivity` · `infrastructure` ·
`grading` · `platform_sync`. Every failure object is
`{domain, stage, code, message}` — display `message` (human sentence),
badge the `domain`, keep `code` for tooltips/support (code tables:
hosted-execution-seams.md §2e/§2f; world-handle-interface.md for errored
receipts).

### Event `type` (closed, 9 members — the live-timeline vocabulary)

Payload keys per type:

- `stage_changed` `{from, to}` — `from` always present, `null` on the
  first transition (present-with-null, not optional); the event-level
  `stage` equals `to`.
- `parallelism_degraded` `{requested, effective, reason}` — `effective` ∈
  `1..requested-1` (V1 always emits 1); `reason` ∈
  `conformance_gate_failed | fixed_port`.
- `baseline_frozen` `{inputs_digest, baseline_ref}`.
- `baseline_inputs_changed` `{previous_digest, current_digest}` —
  `previous_digest` null when there was no prior baseline.
- `world_unhealthy` `{world_index, cause}` — `cause` free text ≤200.
- `scenario_started` `{scenario_key, world_index, scenario_attempt}`.
- `scenario_retried` `{scenario_key, from_world, to_world}`.
- `log` `{level, message}` — `level` ∈ `debug|info|warning|error`;
  long messages arrive pre-truncated with a trailing `…[truncated]`
  marker.
- `terminal` `{stage, reason, failure, scenario_counts}` — at most one per
  attempt, last emitted; `reason` ∈ `null | ttl_exceeded | user_canceled`;
  `scenario_counts` `{passed, failed, errored, skipped}` (advisory). A
  superseded/fenced attempt emits NO terminal event — never block a view
  on one arriving; the next attempt's events are what you'll see.

Wire envelope around every event (per outbound-channels §Channel 1):
`{event_id, attempt_number, sequence, emitted_at, stage, type, payload}`;
canonical ordering is `(attempt_number, sequence)` — never sort by
`emitted_at`.
Whether the read endpoint reproduces this envelope verbatim is part of Q2.

### Artifact kinds (closed)

`recording_combined`, `recording_stereo`, `recording_customer`,
`recording_assistant`, `transcript`, `tool_trace`, `result`, `build`,
`trace`, `log`, `other`. Recordings are **mp4** (AAC-LC; stereo = customer
left, assistant right) — the authoring UI's `GET recording/` endpoint
serves different audio (`audio/wav` etc. per v0); a hosted results player
must handle mp4 audio. Track mapping vs the authoring UI's vocabulary:
`caller` ↔ `customer`, `agent` ↔ `assistant`. `transcript` = JSON array of
`{speaker_role, content, start_time_ms?, end_time_ms?}` with
`speaker_role ∈ assistant|user|system|tool_calls|tool_call_result`.

**Artifact level** (closed, 5 members) caps what exists — the UI must
degrade, not assume:

| level | UI can expect |
|---|---|
| `metadata-only` | verdicts + `build`/`result`/`log` only — no `transcript`, `tool_trace`, `trace`, or recordings |
| `traces` | + `transcript`, `tool_trace`, `trace` |
| `traces-and-recordings` | + recordings (playback) |
| `full` | + `other` (never required at any level) |
| `local-only` | never appears on a hosted job (rejected at admission) |

The enum is frozen, but the job's level VALUE only reaches the frontend via
the PROPOSED poll payload (`artifacts_level`) — degradation logic is
mock-fed until that lands. A missing recording on a `traces` job is
CORRECT, not a bug. A receipt's `transcript_artifact` may also be `null`
on budget refusal — show "transcript unavailable", never an error state.

## Display rules (normative)

- **Receipts are the truth for per-scenario results.** Event-carried counts
  (`terminal.scenario_counts`) are advisory — fine for a live ticker,
  reconciled against receipts once terminal. The poll payload's
  `scenario_counts` should be receipt-derived (authoritative) — confirm
  with Azain (Q2). A scenario has no row until its receipt lands
  (receipts are emitted only at final status) — mid-run, show
  `scenario_total` minus the resolved sum as pending (both from the poll
  payload).
- **Durations:** render `call.duration_ms` for call length; the RFC-3339
  timestamps are display-only. Job elapsed time has no `*_ms` field —
  derive it from the PROPOSED payload's `started_at`/`updated_at` as a
  display-only approximation (this is the one deliberate departure from
  the seam rule that durations are never derived from timestamps;
  mock-fed until Q2 lands).
- **Evaluations are informational.** `status` is determined by sub-goals
  alone; a `checkpoint: passed: false` evaluation can sit on a `passed`
  scenario. Render evaluations as a secondary list, never as a verdict.
- **Retry:** a scenario is tried at most twice, the second time on a
  different world. Only the final try gets a receipt;
  `scenario_attempt: 2` ON THE RECEIPT is the retry marker — render a
  subtle "retried" chip from it. Never two result rows.
- **Evidence-partial:** any job that ends before every scenario resolved
  (cancel, TTL, or any failure) may have receipts synthesized as `skipped`
  and an incomplete artifact set — show a banner, keep whatever results
  exist visible. Banner copy by terminal `reason`: `user_canceled` →
  "run canceled; partial results"; `ttl_exceeded` → "run hit its time
  limit; partial results"; `reason: null` (the common case — the job
  failed) → "run ended early; partial results". `evidence_partial` and
  `reason` arrive via the PROPOSED poll payload — banner is mock-fed
  until it lands.

## Result receipt — the per-scenario payload shape (FROZEN)

What ingestion receives per scenario (trimmed to what the UI renders; the
full wire shape is outbound-channels.md §Channel 2):

```jsonc
{
  "scenario_key": "suspended-account-blocked",  // stable id, joins everything
  "scenario_id": "<platform id>",               // the pre-allocated row (its
                                                // relation to execution_id: Q1)
  "scenario_attempt": 1,                        // 1 or 2; 2 = was retried
  "world_index": 2,                             // null for skipped
  "status": "passed",                           // 4-way above
  "sub_goals": [ { "name": "…", "held": true, "reason": null, "judged": false } ],
  "evaluations": [                              // may be empty; two closed variants
    { "name": "…", "kind": "metric",     "score": 0.86, "reason": "…" },  // score 0.0–1.0
    { "name": "…", "kind": "checkpoint", "passed": true, "reason": "…" } ],
  "call": {                                     // null when never called
    "started_at": "…", "ended_at": "…", "duration_ms": 184211, "turns": 5,
    "transcript_artifact": "sha256:<id>",       // null when level/budget excluded it
    "recording_artifacts": ["sha256:<id>"] },
  "failure": null                               // {domain, stage, code, message} when errored
}
```

- `sub_goals[].held` is `null` (with `reason: null`) for goals never
  evaluated (errored mid-call) — render "not evaluated", not failed.
  `reason` names why a goal did not hold; null when it held. Caveat: a
  code check that returned bare `False` yields the literal string
  `"False"` — render that as "check failed" rather than printing it.
- `sub_goals[].judged`: `true` = an LLM judge settled the goal, `false` = a
  code check did. Surface as a small icon/tooltip ("judged" vs "checked"),
  nothing louder.
- `world_index` is an internal placement detail — at most a debug tooltip,
  never a primary UI element.
- **Scenario display identity for V1 is `scenario_key`** — a stable opaque
  id (today's generator emits slugs, but the format is Karthik's to pin) —
  render as-is; don't transform it. A human title field, if any, arrives
  with Karthik's scenario contract (in review) — do not block on it.

## Read endpoints

### VERIFIED — what the `/simulate/harness/` proxy is and isn't

The proxy surfaces in `frontend-contract.md` v0 are the **authoring UI's**
— they read a harness session folder. Hosted runs execute in a sandbox and
never create a session folder, so `GET simulations`,
`GET simulations/<run_id>`, `GET recording/…` — and the `GET environments`
list, which is built from sessions — will NEVER contain hosted jobs or
results. Do not build any hosted view on them.

### UNCONFIRMED — where hosted results probably land

The surface that CAN carry hosted results is the platform's existing
run-test/execution views — the ones today's simulation UI consumes
(reference client: `frontend/src/api/tests/testDetails.js` and
`testRuns.js`; routes under `runTests` / `testExecutions` in
`frontend/src/utils/axios.js`). Whether hosted receipts actually land
there is Q1; treat "reuse run-detail UI" as the likely-but-unconfirmed
path until Azain answers.

### PROPOSED — paths and field names need Azain

The field VALUES in these shapes mirror the frozen vocabularies above and
will not drift; the paths, field names, and envelope are proposals.
Standard backend auth; success envelope is the platform's
`{"status": true, "result": {…}}` (`status` is a boolean); errors are
expected to use the standard platform error envelope
(`tfc/utils/api_errors.py` — `{"status": false, "code", "message",
"error", …}`) — Q4 is just confirming that. The shapes below are the
`result` object inside the success envelope. Note the error envelope ALSO
carries a `result` key, so branch on the boolean `status`, never on
`result` being present.

- `GET /simulate/api/harness/jobs/<job_id>/` →
  ```jsonc
  { "stage": "running",            // terminal value once terminal is set
    "terminal": null,              // null | completed | failed | canceled
    "reason": null,                // null | ttl_exceeded | user_canceled
    "failure": null,               // {domain, stage, code, message}
    "evidence_partial": false,
    "attempt_number": 1,
    "scenario_total": 30,            // denominator for progress display
    "scenario_counts": {"passed": 0, "failed": 0, "errored": 0, "skipped": 0},
    "run_test_id": "…",              // handle into the existing run-detail
                                     // views (§UNCONFIRMED); inert until Q1
    "artifacts_level": "traces-and-recordings",  // the job's artifact level
    "started_at": "…", "updated_at": "…" }
  ```
  Poll every 2–5s for the progress view.
- `GET /simulate/api/harness/jobs/<job_id>/timeline/` → recent ingested
  events (envelope + ordering per the event section above). OPTIONAL for
  V1 — the poll endpoint alone carries the demo.
- **Artifact bytes** (recordings/transcripts by `sha256:<id>`): how the
  platform serves these to the browser is Q3 — playback UI is mock-fed
  until answered.

## Build order

Today, mock-fed (nothing blocks these):
1. Enums/types for the six closed vocabularies (stages, statuses, domains,
   event types, kinds, levels).
2. Job progress component: skip-tolerant stage stepper + terminal states +
   `failure.stage`-aware failure banner — from a mocked poll payload.
3. Scenario result list: 4-way status rendering (incl. the
   skipped/errored honesty rule), sub-goals, evaluations-as-secondary,
   retry chip from `scenario_attempt` — from mocked receipts (correct
   regardless of how Azain lands ingestion); plus the evidence-partial
   banner, driven by `evidence_partial`/`reason` from the same mocked
   poll payload as item 2.

Blocked on Azain:
4. Results drill-in: extend the existing run-detail views as the LIKELY
   surface (Q1), with the mp4 player, mapping the four recording kinds
   onto the authoring player's track labels (hosted has no `track` param
   — four separate artifacts by kind).
5. Wire the poll endpoint, timeline, and artifact-byte serving (Q2/Q3)
   when they land.

(How the frontend obtains a `job_id` to poll at all — job submission and
job list — is Q0, undefined today.)

## Open questions (Q0–Q4 — all Azain)

0. How does the frontend obtain a `job_id` — what are the job submission
   and job list surfaces? (Not defined anywhere yet; this doc deliberately
   does not propose them, but the progress view is unreachable without
   them.)
1. Does receipt ingestion write back onto the pre-allocated execution rows
   (local `send_result` parity), or do hosted results need their own read
   surface? Includes: is the receipt's `scenario_id` the
   `call_execution_id` that `begin`'s batch pre-allocates (the id
   `/simulate/call-executions/<id>/` keys on)?
2. Confirm/adjust the poll endpoint path + payload (incl.
   `evidence_partial`, `reason`, `artifacts_level`, `scenario_total`,
   receipt-derived `scenario_counts`, stage-goes-terminal) and the
   timeline envelope.
3. How are artifact bytes served to the browser?
4. Confirm the new endpoints use the standard `api_errors` envelope.

## Not in v1

- V1 assumes polling; SSE/websocket for hosted progress is out of scope
  regardless of what Azain lands.
- No cancel control in the frontend (note for later: cancellation has a
  120s flush window — results keep landing up to ~2 min after a cancel).
- No per-world visualization; no artifact browsing beyond
  transcript/recording playback.
