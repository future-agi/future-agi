# C4 — Degrade Reasons & Surfacing (v1.3 — FROZEN)

**Date:** 2026-08-31 · **Status:** FROZEN — round-3 review fixes applied
(this version); the contract is now FROZEN (no round 4). Any further change
is a new-version reopening, not a review round.
**Boundary:** guest ↔ platform ingestion ↔ frontend.
**Owners:** Khushal (this doc, vocabulary) · Track C′ (guest emitters:
`outbound.py`, `hosted_entrypoint.py`) · Track B (reason producers in
`process_runtime.py`) · Track E (ingestion validator, serializer clamp,
shared admission guard, FE) · Karthik (review; candidate validator owner).
**Pinned against:** `parallelism-plan-v10.md` §2 (degrade-event-path row),
§3 (P0-C C4 row), §7 (rollout-flip + silent-vanish rows), §4 track table
(row 272 — serializer is Track E's `environment_values` literal-endpoint
locus);
`outbound-channels.md` v1.4 §Channel 1; `frontend-hosted-runs.md` v1;
`hosted-execution-seams.md` §5; `c1-world-port-model.md` v1.3 §4
(`port_not_consumable` attribution mapping AND its terminal-not-degrade
classification, decision 2 / D28 — reconciled into this doc at the
post-freeze consistency pass, see §2) + §5.4a (two-tier scan,
`[::1]` now in the degrade tier — SATISFIED, see §7);
`c2-replication-admission.md` v1.3 (§1 pipeline stages, §5 catch rules, §6
writer side; its ledger-shape divergence is RESOLVED in this doc's favor,
decision D7, see §2; `port_not_consumable` terminal in C2 too);
`c3-call-affinity.md` v0.4 (§2.1 submit-time admission dependency — the
serializer flag-AND-digest enforcement is this doc's decision D12,
satisfied by §4/§5 below).
**Amends:** `outbound-channels.md` `parallelism_degraded` reason set (v1.4
lists two members); `hosted-execution-seams.md:906` (`"effective": 1`
verbatim → `1 ≤ effective < requested`); `hosted-execution-seams.md` §5
stage phrasing (fixed "stage `validating_environment`" → provision-time
emitters' stage MAY vary, `building_environment`/`validating_environment`;
§2 already declares this); `frontend-hosted-runs.md` event vocabulary
section. This doc is the amendment source; those files conform to this table
on their next version bump.

All file:line citations verified 2026-08-31 against
`~/Desktop/agent-learning-kit` (guest, `src/fi/alk/harness/`) and
`~/Desktop/future-agi` (platform, `futureagi/simulate/`) working trees.

**Plan deviations (recorded deliberately)** — mirror of the sibling blocks:

- The plan (§8 FE row wording) frames the effective-W display as
  "FE-only; the read path already exists" — i.e. the FE derives it from the
  existing 100-event feed. C4 DEVIATES: the display is an ingestion-WRITE
  into persisted ATTEMPT-LEVEL fields (§6), which `serialize_job` reads
  DIRECTLY (`harness_provider.py:110` reads the latest attempt); the plan's
  100-event read path is exactly what §6 REJECTS — a long run evicts the
  degrade event from the `harness_provider.py:113` window and a feed-derived
  display silently reverts to the requested value. The deviation is from the
  plan's locus, not its intent (the value shown is unchanged).
- The plan's §4 track table locates the W>1 clamp in the serializer only.
  C4 keeps the serializer check as the friendly early rejection but pins the
  AUTHORITATIVE enforcement at `register_attempt`
  (`hosted_harness.py:153-199`) — the single chokepoint every attempt passes
  through (§4 pin (ii), §5, decision D12). Deviation from the row's letter
  (one locus) to close the rerun/retry re-admission gap the single-locus
  reading leaves open; not a plan re-opening.

---

## §1 The seam today — why a new reason's STRUCTURED signal vanishes (verified)

The degrade path is built end to end, but its vocabulary is a CLOSED enum on
both ends, and a rejected event survives only as prose — the structured
degrade (machine-readable reason + effective-W display) is what is lost:

| Hop | Where | Behavior today |
|---|---|---|
| Produce | `process_runtime.py:245–246` (`plan_ports`: `degraded_reason="fixed_port"`); conformance gate `:4578–4593` (`effective=1`, `degrade_reason=reason`); fields written `:4601–4607` | reason recorded in `build.json` |
| Emit | `hosted_entrypoint.py:1904–1917` | emits `parallelism_degraded` only when `effective < requested`; otherwise logs a warning (no representable payload) |
| Guest enum | `outbound.py:581–583` (`DegradeReason`: `conformance_gate_failed`, `fixed_port`) | CLOSED — an unlisted reason cannot be constructed |
| Ingest | `hosted_harness_ingestion.py:576–587` — reason set `:579`, bound `1 ≤ effective < requested` `:586` | CLOSED — unlisted reason → per-event rejection |
| Rejection storage | `:475–496` — stored with `accepted=False`, `payload=None`; watermark `:527–556` advances over accepted AND rejected | rejection is recorded but invisible downstream |
| FE read | `harness_provider.py:113` — `events.filter(accepted=True)` into `serialize_job` (`:188–224`) | rejected events never reach the FE |
| Guest reaction | batch response from `ingest_event_batch` (`hosted_harness_ingestion.py:99–134`, `rejected[]` at `:133`); guest advances its watermark through rejections (`outbound.py:1395–1397`, `advance_watermark` `:1420`), drops the record (`drop_many` `:1593–1605`, never re-emitted), then surfaces each rejection as an error-level `log` event (`hosted_entrypoint.py:920–927`) plus a `log`-kind artifact copy of the dropped payload (the `upload_artifact` call at `:930`; per outbound-channels.md the `log` event IS accepted and FE-visible) | no retry; structured event lost — only prose + bytes survive |

Net: a guest emitting a reason the deployed platform does not list still
leaves an operator trail — an error `log` line naming the rejected sequence
and an artifact copy of the payload — but the STRUCTURED degrade vanishes:
no machine-readable reason, no effective-W display, nothing §6's surfacing
can render. That is the **silent-vanish seam** this contract closes (plan §7
"silent vanish" row — silent for the structured signal, not for all operator
output). The vocabulary below, the deployment order in §4, and the
round-trip test in §8 are the three closers; none is optional.

---

## §2 Reason vocabulary v2 (closed, end-to-end)

`parallelism_degraded` payload (unchanged shape):
`{"requested": int, "effective": int, "reason": <enum>}` with
`1 ≤ effective < requested`. The `reason` enum becomes:

| reason | producer set (complete) | track | meaning | typical effective |
|---|---|---|---|---|
| `fixed_port` | `plan_ports`, `process_runtime.py:245–246` (post-C1: only for code-fixed, non-env-consumable ports) | exists | a process declares a listen port the environment cannot override; only one world can hold it | 1 |
| `conformance_gate_failed` | TWO list-writing producers: (1) `run_conformance_gate`'s constant return — the ONLY reason string it ever returns (`process_runtime.py:4084–4165`; the cause cannot travel in the string, comment `:4146–4148`); (2) the gate-branch catch fallback when C1 §4's three-condition attribution rule is not satisfied. PLUS one MIRROR-write (not a ledger entry): the reconcile-path latch (`process_runtime.py:4594–4599`, the `elif self._conformance_checked and build_output.conformance is False` branch) re-stamps `degrade_reason` on every later `provision()` call — under C2 §1 rule 3's latch and the ≤1-entry-per-reason dedup it restates the already-latched final state into the legacy mirror fields only; it MUST NOT append a new `degrade_events` entry. This latch is reserved for GENUINE gate failures: C2 §5 rule 3 DECOUPLES the gate-branch `_ensure_world(1)` catch from it — the catch sets the effective-ceiling integer directly and MUST NOT fabricate a synthetic `conformance = False`, so the latch never re-stamps the catch's C1 §4-attributed reason (it does NOT "extend" the latch to the catch) | exists | the 2-world canary gate failed; environment not certified for W>1 | 1 |
| `resource_limited` | C2 admission clamp W′ = min(W, cpu_fit, mem_fit), locus `process_runtime.py` (Track B; lands beside the `:4601–4607` write site) | NEW | sandbox resources fit fewer worlds than requested | W′ ∈ 2..requested−1, or 1 |
| `literal_local_endpoint` | guest pre-plan secret scan at provision start — BEFORE `plan_ports`, before any world builds (plan §1 mechanism; Track B) | NEW | a resolved secret value carries a literal `localhost:<declared>` / `127.0.0.1:<declared>` / `[::1]:<declared>` (IPv6 loopback per §7's pattern; see the C2 sibling amendment in §7); W=1 re-plan honors the declared port so the job runs as today | 1 |
| `world_start_failed` | C2 provision catch at BOTH `_ensure_world` sites — conformance-gate branch `process_runtime.py:4579–4580` AND main loop `:4613–4614` (Track B), **armed ONLY on the first-build / digest-rebuild branch** (C2 v1.3 §5): a reconcile-call rebuild failure is NOT a degrade — it re-raises to WorldPool (`hosted_scheduler.py:1121–1152`) as a scheduler down-marker, never a `degrade_events` entry | NEW | worlds 0..k−1 built; world k failed; job continues at W′=k | k ∈ 1..requested−1 (first build / digest rebuild only) |

> **`port_not_consumable` is NOT in this table — it is a TERMINAL JOB
> FAILURE, not a degrade reason (C1 v1.3 §4 decision 2 / D28; adopted in
> this consistency pass).** It was formerly a sixth degrade-enum member; it
> is now removed from the `parallelism_degraded` / `degrade_events`
> vocabulary and surfaced as a provision-time typed job failure. See §2's
> "TERMINAL job-failure reason" block below, the §9 job-failure rows, and
> the §6 failure-banner note.

Rules (normative):

- The degrade enum is closed at exactly these FIVE members, unconditionally
  (`resource_limited`, `literal_local_endpoint`, `world_start_failed`,
  `fixed_port`, `conformance_gate_failed`). `port_not_consumable` was the
  sixth; C1 v1.3 §4 decision 2 (D28) reclassifies it OUT of the degrade
  enum to a terminal job failure — see the block below. Adding a member is a
  version bump of THIS document first, then the §4 deployment order.
- **Producer sets are complete**: each reason lists its complete producer set
  in the table above; no code outside that set writes it. Single-producer for
  every reason except `conformance_gate_failed`'s documented pair.
- **`requested` is constant** per attempt: the admitted `runtime.parallelism`
  as validated by the platform serializer. It never restates a prior degrade.
- **`effective` is strictly decreasing** across an attempt's degrade events.
  An attempt MAY emit more than one `parallelism_degraded` (e.g.
  `resource_limited` 4→2, then `world_start_failed` 2→1); at most one event
  per reason per attempt. Consumers take the LAST accepted event as current
  effective W (the platform's attempt-level projection, §6, implements
  exactly this at ingestion).
- **Transport (normative — C4 OWNS this shape seam-wide): `build.json`
  carries `degrade_events`** — a LIST of `{reason, from_w, to_w}` appended
  in causal order. Track B writes and updates the list.
  - **Mirror set (exact):** `requested_parallelism` stays the CONSTANT raw
    requested W for the attempt — it is NEVER mirrored from any entry. Only
    `effective_parallelism` and `degrade_reason`
    (`process_runtime.py:4601–4607`) mirror the FINAL entry:
    `effective_parallelism = last.to_w`, `degrade_reason = last.reason`
    (no-degrade: `effective_parallelism = requested_parallelism`,
    `degrade_reason = None`). Old readers of the legacy scalars see the
    settled state.
  - **Entry → event mapping (exact):** the emitter
    (`hosted_entrypoint.py:1904` path, Track C′) emits ONE
    `parallelism_degraded` event per list entry, in list order; each event
    carries `requested` = the attempt constant (`requested_parallelism`),
    `effective` = that entry's `to_w`, `reason` = that entry's `reason`.
  - **Writer enumeration (complete — every enum member's list-write site):**
    (1) `resource_limited` — C2 stage-1 admission clamp, top of
    `_provision_sync` before `plan_ports`; (2) `literal_local_endpoint` —
    C2 stage-2 pre-plan secret-scan degrade tier, after secret resolution,
    still before `plan_ports`; (3) `fixed_port` — `plan_ports`' code-fixed
    degrade (`process_runtime.py:245–246`), copied into the list by the
    pipeline; (4) `conformance_gate_failed` — the gate stage. The GENUINE
    gate-failure write is the gate CALLER at
    `process_runtime.py:4591–4593` (the `if not passed: effective = 1;
    degrade_reason = reason` branch that stamps the verdict) — NOT
    `run_conformance_gate`'s return, which only yields the constant reason
    string (`:4084–4165`). The gate-branch `_ensure_world(1)` catch
    (`:4579–4580`) additionally writes a degrade entry HERE only for its
    GRACEFUL arms, attributing per C1 v1.3 §4's mapping (a
    non-control/non-knob-bearing process's failure, or a knob-bearing
    failure with NO bind evidence, or a formula-port stale-squat →
    `world_start_failed` / `conformance_gate_failed`). Its TERMINAL arm — a
    world-≥1 bind death whose bind evidence names a DECLARED port by a
    consumable-declared process, or a non-formula/non-declared default port
    by a knob-bearing worker — maps to `port_not_consumable`, which is a
    TERMINAL JOB FAILURE raised OUT-OF-BAND and appends NO `degrade_events`
    entry (C1 v1.3 §4 decision 2 / D28; see the terminal block below);
    (5) `world_start_failed` — the main-loop `_ensure_world(k)` catch
    (`:4613–4614`), plus the gate-branch graceful case in (4). No code
    outside this enumeration appends to the list; the reconcile-path
    latch (`:4594–4599`) is a legacy-mirror re-stamp only (§2 table,
    `conformance_gate_failed` row). `port_not_consumable` is NOT a
    `degrade_events` writer at all — both its detection loci raise terminal
    job failures (terminal block below).
    **SIBLING AMENDMENT REQUIRED (C2 §6 — writer enumeration):** C2 §6's
    Writers bullet enumerates only stages 1, 2 and 5 (admission clamp,
    scan, `_ensure_world` catches). It MUST additionally enumerate the
    stage-3 writer (`plan_ports`' `fixed_port` code-fixed degrade,
    `process_runtime.py:245–246`, C1's) and the stage-4 GENUINE-gate writer
    (the caller at `:4591–4593`) so the writer set is complete on C2's side
    too — matching this enumeration's five members.
  - The two invariants above — ≤1 event per reason per attempt, `effective`
    strictly decreasing — bind the LIST, and therefore the emitted events.
  - **Emission is ONE-SHOT, not re-emission (normative; C2 §6's emission
    split governs).** The emitter emits the ledger ONCE, at entrypoint
    emission (`hosted_entrypoint.py:1904` path). "One event per entry, in
    list order" is a mapping over the ledger AT emission time — it MUST NOT
    be read as re-emission: ledger updates that occur AFTER emission are NOT
    re-emitted as `parallelism_degraded` events. Post-emission world losses
    surface through the scheduler's existing sick-world / world-down typed
    codes (`hosted_scheduler.py:1121–1152`), never a second degrade event.
    C2 §6 states the domain split normatively (provisioning clamps vs.
    mid-run attrition); C4 references it so this doc's per-entry mapping
    cannot be misread as a re-emission license.
  - **Missing-list fallback (normative — emitter MUST NOT emit nothing).**
    If `build.json` carries NO `degrade_events` list — a snapshot where
    Track C′ (v2 emitter) is ahead of Track B (list writer), so only the
    legacy scalar fields are present — the emitter FALLS BACK to the legacy
    scalars (`requested_parallelism` / `effective_parallelism` /
    `degrade_reason`, `process_runtime.py:4601–4607`) and still emits
    today's `fixed_port` / `conformance_gate_failed` degrade events under
    the same `1 ≤ effective < requested` guard. The list is the forward
    shape; its absence degrades to today's single-field behavior, never to
    silence.
  - **RESOLVED divergence (D7):** C2 formerly specified a competing ledger
    shape (`degrades: [{reason, effective}]`, its old §6). Resolved in
    C4's favor — decision D7: `degrade_events` with `{reason, from_w,
    to_w}` is the ONE seam-wide shape; C2 (v1.1+, amended concurrently
    with this version) carries writer-side rules only and adds no shape.
- **W′ = 0 is unrepresentable** (`1 ≤ effective`, enforced at ingestion
  `:586`). World 0 failing to build **at the FIRST provision call**
  (`pool.start()`) is a JOB FAILURE (C2 v1.3 §5 rule 1), never a degrade
  event; the typed error propagates and the FE shows a failure banner.
  Emitters MUST NOT attempt an `effective: 0` payload. Scoped precisely per
  C2: a world-0 (or pool re-provision) failure on a later RECONCILE call is
  NOT a job failure — it is caught by WorldPool
  (`hosted_scheduler.py:1121–1152`), the still-down worlds get typed
  down-markers, and the job CONTINUES on the healthy worlds (scheduler
  sick-world domain, no degrade event).
- `effective == requested` is not a degrade: no event exists for it. The
  guest already guards this — the `if effective < requested:` emit call is
  `hosted_entrypoint.py:1904–1908`; the `else` warning branch is
  `:1910–1917` (logs a warning instead of emitting).
- Event-level `stage` is the attempt's current stage at emit. The spine's
  fixed "stage `validating_environment`" phrasing
  (`hosted-execution-seams.md` §5) is amended: emitters at provision time may
  carry `building_environment`/`validating_environment`; ingestion does not
  validate stage for this type (verified: `_event_payload_error:576–587`
  checks payload only).
- `literal_local_endpoint` fallback (plan §1): if pre-plan secret resolution
  proves infeasible, the degrade-to-1 cure claim is DROPPED — the job fails
  loud with `literal_local_endpoint` as the failure `code` (failure object,
  not a degrade event). The reason string is shared between both shapes on
  purpose; the FE copy in §6 covers both.

**TERMINAL job-failure reason — `port_not_consumable` (C1 v1.3 §4
decision 2 / D28; reconciled into C4 at the post-freeze consistency pass).**
`port_not_consumable` is NOT a degrade reason: it is a provision-time
TERMINAL JOB FAILURE, surfaced the SAME way C1/C2's other provision-time
typed job failures are (the secret-resolution ERROR at provision start,
D10, and the `literal_local_endpoint` fallback when pre-plan resolution is
infeasible, §2 last bullet). A process FLAGGED consumable (or a knob-bearing
worker) that LIES at runtime — binds its DECLARED port instead of its
allocated formula port — leaves world 0 internally inconsistent (its own
consumers and capability URLs were rewritten to a formula port nothing
binds), and the frozen plan cannot re-consist world 0 (no mid-flight
re-plan, C1 §2). There is nothing to degrade INTO, so the job fails LOUD
with a `failure` object (reason/`code` `port_not_consumable`), never a
`parallelism_degraded` event and never a `degrade_events` entry. The FE
shows a FAILURE BANNER (not a degrade notice — like the W′=0 world-0
job-failure case, §9), carrying C1's diagnosis: *"the agent is declared
parallel-capable but did not honor its assigned port; fix it to read its
port env, or request parallelism=1 to run serially (the declared port is
then honored)."* This is sharply distinct from a NON-consumable `fixed_port`
(code-fixed), which still gets the CLEAN plan-time W=1 degrade, reason
`fixed_port` (unchanged, §2 table row 1).

**Two detection loci — both RAISE the terminal failure (C1 v1.3 §4):**

- **(1) Bind-death arm.** At effective W>1 the provisioner builds worlds 0
  and 1 before gating; a world-index ≥ 1 startup failure caught at the
  gate-branch `_ensure_world(1)` site (`process_runtime.py:4579–4580`) is
  attributed by the bind-error evidence in the failing process's log tail
  (the `[Errno 48]`/address-in-use class), reading the errored port off the
  error line: (a) a DECLARED `fixed_port` value bound by a
  **consumable-declared** process, OR (b) a **non-formula, non-declared**
  default port (e.g. 8081) bound by a **knob-bearing** worker (carries
  `FI_WORKER_HEALTH_PORT`) → **`port_not_consumable`, TERMINAL JOB FAILURE**.
  **Formula-port stale-squat exclusion:** a **formula-assigned** port in the
  bind evidence is the stale-squat class (a prior worker still holding it)
  → graceful `world_start_failed`, NEVER `port_not_consumable`. A
  knob-bearing failure with NO bind evidence → graceful
  `conformance_gate_failed`; any other (non-consumable, non-knob-bearing)
  process's failure → C2's graceful `world_start_failed`.
- **(2) Gate declared-port listener check (D16, NEW — Track B).** At
  effective W>1, AFTER the gate worlds (0 and 1) build, a DISTINCT provision
  step verifies that NO listener exists on ANY declared `fixed_port` value
  (bind-probe or socket scan). A listener found → **`port_not_consumable`,
  TERMINAL JOB FAILURE, LOUD** — the net for the lying-consumable residual a
  syntactically-passing §1 check and a tolerant `started_check` cannot see.
  Locus-decoupled (C1 §4): it raises its own terminal failure OUT-OF-BAND —
  it MUST NOT set `build_output.conformance = False` and MUST NOT make
  `run_conformance_gate` return a third string. KNOWN RESIDUAL (frozen):
  point-in-time, so a pre-check-window binder and a post-gate late binder
  stay undetected (C1 §4).

Note the gate's own return can never carry detail: `run_conformance_gate`
returns only the constant `"conformance_gate_failed"`
(`process_runtime.py:4084–4165`; its `:4146–4148` comment pins that the cause
cannot travel in the reason string). C1 v1.3 and C2 v1.3 both treat
`port_not_consumable` as terminal (per their changelogs / §4 decision 2 and
C2 §5 rule 3); this section aligns C4 with them.

---

## §3 Wire + persistence contract per hop

1. **Guest enum** (`outbound.py:581–583`): Track C′ extends `DegradeReason`
   with the v2 members. The emitter (`hosted_entrypoint.py:1904` path) emits
   one event per `build.json` `degrade_events` entry, in causal order (§2
   transport rule), ONCE at entrypoint emission — post-emission ledger
   updates are NOT re-emitted (C2 §6 emission split; §2). If the
   `degrade_events` list is absent (v2 emitter ahead of the list writer),
   the emitter falls back to the legacy single fields and still emits
   today's `fixed_port` / `conformance_gate_failed` events — never nothing
   (§2 missing-list fallback). The legacy single fields are otherwise read
   only as the final-state mirror. Every producer in §2 writes one of the v2
   strings, nothing else.
2. **Ingestion validator** (`hosted_harness_ingestion.py:579`): Track E
   extends the accepted set to the v2 vocabulary. Everything else is
   unchanged: unknown reason → per-event rejection (stored `accepted=False`,
   `:493`), watermark advances (`:527–556`), `ingest_event_batch`'s batch
   response lists it in `rejected[]` (`:133`).
3. **Persistence**: an accepted `parallelism_degraded` keeps its full payload
   (`:490`); the FE feed is the last 100 accepted events
   (`harness_provider.py:113`, key `"events"` `:215`). An accepted
   `parallelism_degraded` ADDITIONALLY updates the attempt-level projection
   fields (§6 decision, Track E) — the feed is a recent-activity window,
   not the degrade display's source of truth.
4. **Rejection still yields no structured event** — this contract does NOT
   reopen the rejection path (the guest's drop-and-advance at
   `outbound.py:1593–1605` stays, as does the error-`log` + artifact
   surfacing at `hosted_entrypoint.py:920–930`; prose is the ceiling). The
   §4 deployment order is what guarantees a conformant guest never has a v2
   reason rejected; the §8 test is what proves it.

---

## §4 Deployment order — BOTH pins (normative, blocking)

**Pin (i) — validator first.** The platform ingestion validator accepting the
v2 vocabulary MUST be live in an environment BEFORE (or in the same deploy
as) any guest snapshot that emits a v2 reason is registered as that
environment's `ALK_DAYTONA_SNAPSHOT`. Rationale: a rejected event degrades to
prose (§1 — an error `log` line + artifact bytes, no machine-readable reason,
no effective-W display) — the reverse order turns every new-reason degrade
into an unstructured log line §6's surfacing cannot render. Guest deploys are
snapshot rebakes; platform deploys are Django releases; "atomic" means the
same release window with the validator migrated first.
**Rollback rule (MUST) — corrected: there is no "registered-snapshot
list".** The real artifacts a rollback must consult are (a) the per-attempt
`snapshot_name` / `snapshot_digest` records stamped on every attempt at
`register_attempt` (`hosted_harness.py:186–193`) and (b) the operator env
var `settings.ALK_DAYTONA_SNAPSHOT` / `ALK_DAYTONA_SNAPSHOT_DIGEST`
(`tfc/settings/settings.py:699–700`) naming the currently-deployed
snapshot. The ingestion validator MUST NOT be reverted (v2 vocabulary
removed) while EITHER the operator env var names a v2-emitting snapshot OR
any per-attempt record — crucially, any IN-FLIGHT attempt still provisioning
or running — carries a v2-emitting snapshot. The in-flight-emitter risk is
explicit: an attempt registered under a v2 snapshot can emit a v2 reason at
any point up to its terminal event, so a validator revert that races a live
attempt turns that attempt's structured degrade into prose (the §1 seam).
The releasing engineer checks BOTH surfaces (env var + non-terminal attempt
records), not a nonexistent list, before any validator rollback.
**Dockerfile-mode limitation (accepted, dev-only):** dev guests built via
`ALK_DAYTONA_DOCKERFILE` (`hosted_harness_gateway.py:582`; env var
`tfc/settings/settings.py:704`) carry NO meaningful snapshot digest —
`register_attempt` stamps `snapshot_digest=None` when `self.snapshot` is
empty (`hosted_harness_gateway.py:849–857`) — so the digest-keyed rollback
check cannot key on them. Accepted as dev-only; consequence: a validator
revert racing a dockerfile-mode dev guest degrades to prose in dev (the §1
seam), never in prod.

**Pin (ii) — W>1 admission gated on flag AND snapshot digest, AUTHORITATIVELY
enforced at `register_attempt` (decision D12, Track E).** The FE explicit
parallelism control (§6) MUST NOT go live (flag §5 stays off) until the
digest check confirms the DEPLOYED guest snapshot carries (a) the Track B
cross-world `agent_name_not_world_unique` preflight guard (to land in the
existing `process_preflight.py`; the GUARD is absent at ALK HEAD — the file
exists; plan §1) and (b) C1's port model.

**The authoritative enforcement locus is `register_attempt`
(`hosted_harness.py:153–199`) — the SINGLE shared admission guard.** The W>1
gate (flag AND digest, else clamp to 1 with `metadata.parallelism_clamped`)
MUST be factored into ONE shared guard invoked at `register_attempt`,
because that function is the SINGLE chokepoint EVERY attempt passes through:
fresh create, `rerun_saved` (`harness_provider.py:384`, `:599`),
`harness_sandbox.rerun` (`:99`), and automatic gateway retry all reach
`register_attempt` via `DaytonaHostedGateway.launch`
(`hosted_harness_gateway.py:849`). The create-time serializer check (§5)
REMAINS as an early, friendly rejection at submit — but it is NOT the
enforcement authority: `register_attempt` is, so no re-admission path (rerun
or retry) can escape the gate by never re-entering the serializer. This is
Track E (the guard and its two call surfaces — serializer + register_attempt
— are Track E's files). **A saved W=4 job reruns at W=1** if the flag or the
registered digest no longer qualify at rerun time: the shared guard
re-evaluates at each `register_attempt`, records `metadata.parallelism_clamped`,
and admits the rerun clamped — the saved requested value is never honored on
trust. The guard reads the flag `HARNESS_PARALLELISM_ENABLED` AND the
currently-registered digest `settings.ALK_DAYTONA_SNAPSHOT_DIGEST`
(`tfc/settings/settings.py:700`) against the `HARNESS_PARALLEL_SNAPSHOT_DIGESTS`
allowlist; because the locus is `register_attempt`, it covers direct API
POSTs and every rerun/retry path, not just the FE control.

**Digest is an operator-maintained env var — SOFTENED claim + required
invariant.** `ALK_DAYTONA_SNAPSHOT_DIGEST` (`tfc/settings/settings.py:699–700`)
is set by the operator; it is NOT derived from `ALK_DAYTONA_SNAPSHOT`. There
is therefore NO "caught automatically, no human re-run" guarantee — a
re-registered guard-less snapshot is caught only if its digest is absent
from the allowlist, which depends on the operator having maintained both in
step. Two invariants are REQUIRED (operational, MUST): (1) the snapshot and
its digest allowlist entry MUST be updated in LOCKSTEP — a snapshot swap
without the matching allowlist update, or vice versa, is an operator error
the guard cannot self-correct; (2) an empty or unset digest (`""`, the
default at `:700`) MUST FAIL CLOSED — it never matches the allowlist, so an
unset digest clamps W>1 to 1 rather than admitting on an empty match.

**The preflight/readiness check is the CONTINUOUS ADVISORY surface**: every
preflight/readiness call compares the registered snapshot digest
(`settings.ALK_DAYTONA_SNAPSHOT_DIGEST`, already in the preflight response —
`harness_provider.py:325–336`) against the same allowlist and reports
`parallelism_enabled` (§5) — it informs the FE; `register_attempt` enforces.
This satisfies `c3-call-affinity.md` v0.4 §2.1's SIBLING AMENDMENT REQUIRED:
C3 v0.4 asks only that the flag-AND-digest gate be enforced at submit-time
admission on BOTH the FE and direct-API paths (its residual was that the
clamp had been flag-only); decision D12 pins exactly that (flag AND digest,
at the register_attempt chokepoint), so the C3 §2.1 dependency is SATISFIED
against v0.4 text — no further C3 change needed.

**Dockerfile-mode limitation** — same note as pin (i): dockerfile-mode dev
guests carry no meaningful digest (`register_attempt` stamps
`snapshot_digest=None`), so neither the rollback check nor this digest check
can key on them; accepted as dev-only (see §5 for the flag-only dev
carve-out). Rationale (plan §7 rollout-flip row): at ALK HEAD, bundles with
no fixed port already provision W worlds concurrently
(`bundle_author_v2.py:746` authors `port=None` for LiveKit agents), and the
world-unique agent name is only a user-overridable `setdefault`
(`:646–649`; hand-authored path `:749–750`) — opening the sanctioned W>1
path against a guard-less snapshot reopens the silent cross-world
contamination window that dispatch-ack cannot catch.

---

## §5 Rollout belt — ONE shared admission guard (flag + digest), authoritative at `register_attempt` (Track E)

The serializer default is already 1 (`harness_job.py:113`,
`parallelism = IntegerField(default=1, min_value=1, max_value=8)`), so a
default-only clamp is vacuous. The belt clamps EXPLICIT requests through a
SINGLE shared admission guard, not a per-locus reimplementation:

- New settings `HARNESS_PARALLELISM_ENABLED` and
  `HARNESS_PARALLEL_SNAPSHOT_DIGESTS` (both absent from the codebase today —
  verified by grep; Track E introduces them). **The W>1 gate logic — flag
  `HARNESS_PARALLELISM_ENABLED` truthy AND the currently-registered guest
  snapshot digest (`settings.ALK_DAYTONA_SNAPSHOT_DIGEST`,
  `tfc/settings/settings.py:700`) present in
  `HARNESS_PARALLEL_SNAPSHOT_DIGESTS` — MUST be factored into ONE shared
  admission guard**, invoked at TWO surfaces:
  - **`register_attempt` (`hosted_harness.py:153–199`) — the AUTHORITATIVE
    enforcement (pin ii, D12).** This is the single chokepoint EVERY attempt
    passes through (fresh create, `rerun_saved` `harness_provider.py:384`/`:599`,
    `harness_sandbox.rerun` `:99`, and automatic gateway retry — all via
    `DaytonaHostedGateway.launch`, `hosted_harness_gateway.py:849`). If the
    guard denies (flag off OR digest unlisted), the attempt is ADMITTED
    AT parallelism 1 (its effective admitted W forced to 1) and
    `metadata.parallelism_clamped: {requested: N}` is recorded — the job's
    stored requested value is preserved (see the re-evaluation pin below).
    No re-admission path (rerun/retry) can
    escape, because none skips `register_attempt`. A SAVED W=4 job whose
    flag/digest no longer qualify at rerun time is admitted at W=1 by this
    same guard. **Pinned so reruns re-evaluate honestly:** the guard forces
    the ATTEMPT's admitted parallelism and records the clamp on the
    attempt/job metadata; it MUST NOT destroy the job's stored REQUESTED
    value (`job.payload.runtime.parallelism`). Preserving the requested value
    is what lets a later rerun re-evaluate against the then-current
    flag/digest (a W=4 job saved while qualifying reruns at W=1 when it no
    longer qualifies, and vice versa) — the clamp is a per-attempt admission
    decision, not a permanent rewrite of intent.
  - **The create-time serializer (`harness_job.py` validate, beside the
    voice reject `:205–212`) — an EARLY, FRIENDLY rejection only.** It runs
    the same shared guard at submit so a fresh create gets immediate,
    honest feedback (clamp recorded there too), but it is NOT the
    enforcement authority — `register_attempt` is. This division is
    deliberate: the serializer improves UX; the chokepoint guarantees
    coverage.
  - Clamp, not reject: a W=4 request with the flag off (or the digest
    unlisted) is ADMITTED at W=1. The clamp MUST be recorded on the job
    (`metadata.parallelism_clamped: {requested: N}`) so support and the FE
    (§6) can see it; it MUST NOT emit a `parallelism_degraded` event (those
    are guest evidence). The preflight/readiness check remains the
    continuous ADVISORY surface (pin ii).
- The clamp runs AFTER field validation, so the 1..8 bound and the voice
  `parallelism ≤ cpu_units` reject (`:205–212`; vacuously all-jobs today —
  connectors are voice-only, `:72`) keep their error behavior for honest
  feedback even while clamped.
- Flag stays OFF in prod until pin (ii) passes. Flag is SET in dev/E2E so the
  W=2/W=4 ladder can run.
- **Dockerfile-mode dev carve-out — flag-only (dev-only; adopting C3 v0.4's
  referenced carve-out).** When `ALK_DAYTONA_DOCKERFILE`
  (`tfc/settings/settings.py:704`) is set — the dev/E2E lane, which carries
  NO meaningful registered snapshot digest (`register_attempt` stamps
  `snapshot_digest=None`, `hosted_harness_gateway.py:849–857`) — the shared
  guard SKIPS the digest half and W>1 requires the FLAG ONLY. This is
  explicitly DEV-ONLY: production is always the snapshot lane, where the
  guard ALWAYS requires BOTH flag and digest (an empty/unset digest fails
  closed, pin ii). This satisfies the dockerfile-mode flag-only carve-out
  that `c3-call-affinity.md` v0.4 §9 already references as accepted
  ("C4 accepts a dockerfile-mode dev carve-out — flag-only — as dev-only").
- **FE transport**: the preflight/readiness response
  (`harness_provider.py:325–336`) gains a `parallelism_enabled` boolean
  (Track E) reflecting flag AND digest-allowlist state (pin ii). The FE reads
  this one authoritative signal; §6 pins the false-state behavior (control
  visible but disabled, never hidden).
- **No stale echo (Track E)**: when admission would clamp (flag unset or
  digest unlisted), the preflight echo `effective_parallelism`
  (`harness_provider.py:328` — today it echoes the requested value back
  verbatim) MUST reflect the clamped value (1), not the requested value.
  The advisory surface must never promise a W the serializer will refuse.
- Defense in depth, unchanged: the guest re-checks W ≤ cpu_units for every
  hosted job (`job.py:180–181`, `hosted_parallelism_exceeds_cpu`).

**FE auto-request hunk is DEV-ONLY and MUST NEVER merge.** The uncommitted
working-tree change at `frontend/src/pages/dashboard/harness/HarnessCreate.jsx:281`
(`parallelism: Math.min(4, Math.max(1, Number(scenarioCount) || 1))`) is the
plan §7 rollout-flip trigger. It is replaced by §6's explicit control
(default 1) and MUST be reverted from any branch before PR. Review gate:
`git diff origin/main -- frontend/.../HarnessCreate.jsx` shows no
`parallelism:` expression other than the explicit control's state.

---

## §6 Frontend surfacing (Track E)

**Create form — explicit parallelism control.**

- Default **1**. Range `1 .. min(8, runtime.cpu_units)` (mirrors the
  serializer bound `:113` and the voice reject `:205–212` so the user cannot
  submit a value the platform bounces).
- No auto-derivation from scenario count (see §5). Copy near the control:
  parallel worlds may reduce to fewer at runtime; the run detail shows the
  effective value.
- When preflight returns `parallelism_enabled: false` (§5), the control is
  VISIBLE but disabled — locked at 1 — with explanatory text (parallel
  execution is not yet enabled for this environment). Never hidden; this is
  the one pinned behavior.

**Run detail — effective-W display. DECISION: attempt-level projection
(Track E — ingestion + serializer are its files).** The event FEED is not
the display's source of truth: `serialize_job` returns only the last 100
accepted events (`harness_provider.py:113`), so a long run's later events
EVICT the degrade events from the window and a feed-derived display would
silently revert to the requested value. Rules:

- **The projection is ATTEMPT-LEVEL, never job-level.** An accepted
  `parallelism_degraded` event updates persisted fields ON THE ATTEMPT
  record only: the current effective parallelism and the ordered reason
  list. It MUST NOT write job-level fields. `serialize_job` reads the
  LATEST attempt (`harness_provider.py:110`,
  `job.attempts.order_by("-attempt_number").first()`), so the display
  naturally reflects the current attempt; a NEW attempt starts with a
  CLEARED projection (empty reason list, effective = requested) and shows
  its OWN degrade state, never a prior attempt's. This is why the fields
  are attempt-level: attempt 2 of a rerun must not inherit attempt 1's
  degrade.
- **Update ONLY on the first store of the event; take
  `min(current_effective, event.effective)` (idempotent + order-safe).**
  The projection update is tied to the `event_id` dedup path
  (`hosted_harness_ingestion.py:479–483` — the `if …exists(): return`
  guard in `_store_event`): the fields are written only when the event is
  stored for the FIRST time, so a redelivered duplicate can never
  double-append a reason. Effective parallelism is set to
  `min(current_effective, event.effective)`, and a reason is appended only
  if not already present — so a duplicate or OUT-OF-ORDER delivery can
  never RAISE the effective W or double-list a reason. (This is stronger
  than relying on §2's strictly-decreasing invariant, which binds the
  emitted stream but not redelivery/ordering at ingestion.)
- `serialize_job` exposes those attempt fields DIRECTLY (beside the
  existing attempt-derived status fields). The FE effective-W display and
  the degrade reason notice read the ATTEMPT FIELDS, never the 100-event
  window. The window remains what it is today: a recent-activity feed.
- With no degrade event, display the requested value. `serialize_job` MUST
  add `job.runtime = {"parallelism", "cpu_units"}` (from `job.payload`,
  beside the existing `job.source` at `harness_provider.py:192`) so the
  detail page has a value without any event.
- Display shape: `Parallelism: 2 (requested 4)` plus the reason line below.
  `world_index` remains a debug tooltip (frontend-hosted-runs.md rule).

**Run detail — platform clamp notice.** When `metadata.parallelism_clamped`
is present (§5; `serialize_job` already exposes metadata,
`harness_provider.py:193`), the run detail MUST show a notice: "requested
{N}, admitted 1 — parallel execution not enabled for this deployment" (or
equivalent copy). Info styling, like the degrade notices; it is not a guest
degrade event and never renders §6's reason copy.

**Degrade reason display — human copy (normative, keyed on the closed enum):**

| reason | copy |
|---|---|
| `fixed_port` | "The agent declares a fixed network port, so scenarios ran one at a time." |
| `conformance_gate_failed` | "The environment failed its parallel-readiness check, so scenarios ran one at a time." |
| `resource_limited` | "The sandbox had fewer resources than requested — running {effective} scenario(s) at a time." |
| `literal_local_endpoint` | "An environment value points at a fixed local address, so scenarios ran one at a time." |
| `world_start_failed` | "Some parallel copies of the environment failed to start — continuing with {effective}." |

(`port_not_consumable` is NO LONGER in this degrade-copy table — it is a
TERMINAL JOB FAILURE, not a degrade. §2 removed it from the enum per C1 v1.3
§4 decision 2 / D28.)

**Terminal job-failure banner — `port_not_consumable` (not a degrade
notice).** When a job fails with `failure.code` `port_not_consumable`
(either detection locus, §2 terminal block), the run detail shows a FAILURE
BANNER — the same failure-surface used for the other provision-time typed
job failures (the world-0-at-first-provision job failure, the
secret-resolution failure, and the `literal_local_endpoint` fallback), NOT
a degrade notice and NOT §6's neutral/info degrade styling. The banner
carries C1's diagnosis: the agent is declared parallel-capable but did not
honor its assigned port — fix it to read its port env, or request
parallelism=1 to run serially (the declared port is then honored).

Unknown reason string (forward-compat: FE may lag a future v3): render
"Parallelism was reduced to {effective}." — never crash, never hide the
event. Degrade is a notice, not an error state: neutral/info styling; it
MUST NOT be presented as a scenario or agent failure.

---

## §7 Literal-endpoint checks — the TWO platform-visible channels (Track E)

Three channels can carry a literal `localhost`/`127.0.0.1`/`[::1]:<port>`
endpoint. TWO are platform-visible and are this section's subject; the third
is guest-only and belongs to C2. Both platform scans WARN (never reject in
v1) and fire for W>1 requests only. Detection pattern for both:
`(localhost|127\.0\.0\.1):\d+` or the IPv6 loopback `\[::1\]:\d+` — the
declared fixed port is unknowable platform-side (the bundle is authored
in-sandbox), so the pattern is port-generic. At W=1 the checks are silent
(the declared-port carve-out makes literals legal at effective W=1).

**Channel 1 — inline `source.environment_values` (submit-time plaintext;
the most platform-visible literal channel).** A job's `source` carries
`environment_values` as plaintext at submit — the serializer sees it
directly (`serializers/harness_job.py:64`, where the source validator
already inspects `environment_values`). **Track E MUST scan this inline
plaintext channel at submit time**: when `runtime.parallelism > 1`, scan
every `source.environment_values` value for the pattern and WARN (job
metadata + FE notice). This is the plan v10 serializer-locus mandate (plan
§4 track table row 272; plan §7 risk row, lines 323 — the `environment_values`
channel is inspected at request time, before any strip). It is the primary
platform check, not an afterthought.

**Channel 2 — vaulted secret-values re-scan (ADDITIONAL, not a
replacement).** The secret-values endpoint
(`harness_job.py:253–276`, `HarnessSecretValuesSerializer`; view
`views/harness_job.py:222–236`) vaults each value as a `HostedHarnessSecret`
and returns `secret_refs`. **DECIDED — warn at job submission, W>1 requests
only:** when `runtime.parallelism > 1`, the platform re-scans its own
vaulted values for the job's `secret_refs` and WARNS — never a reject in v1
(the guest pre-plan scan is the enforcement backstop; reject can be
tightened later without a wire change). W-scoped: a W=1 request never fires
it (the upload-time warning is dropped — the secret-values endpoint is
jobless, so W is unknown there). Cost accepted: one vault read per W>1
submit; covers platform-vaulted refs only. This scan is IN ADDITION to
Channel 1, which covers the inline plaintext values Channel 2 never sees.

**Channel 3 (recorded, NOT this section's) — guest secret-refs values.**
Secret-ref VALUES resolve only in-sandbox; the guest pre-plan scan owns them
(C2 §4, two-tier). Listed here only so the channel inventory is complete:
Channels 1+2 are platform-side (this section, Track E); Channel 3 is
guest-side (C2, Track B).

**SIBLING NOTE (C2 §4 — guest scan loopback set) — SATISFIED (consistency
pass).** C1 v1.3 §5.4a lists the IPv6 loopback `[::1]:<port>` in BOTH the
degrade tier and the warn tier, so the C1 side is SATISFIED. C2's side is
now ALSO satisfied: C2 v1.3 §4 carries all three loopback forms
(`localhost` / `127.0.0.1` / `[::1]`) in BOTH tiers (C2 v1.3 changelog
records it has done so since v1.2). The earlier "C2 degrade tier OMITS
`[::1]`" ask is resolved; no C2 change is owed.

---

## §8 Round-trip contract test — closing the silent-vanish seam (blocking)

A new reason is DONE only when this test passes; it is the merge gate for any
enum change on either side.

- **Platform test** (required, blocking): for EVERY member of §2's FIVE-reason
  vocabulary, ingest a batch containing a `parallelism_degraded` event with
  that reason through the real ingestion entrypoint and assert (a) the
  stored event has `accepted=True` with payload intact, (b) the event
  appears in `serialize_job(job)["events"]` — i.e. it survives to the exact
  feed the FE renders (`harness_provider.py:113,215`), and (c) the
  attempt-level projection fields (§6: effective parallelism + reason
  list) are updated and exposed by `serialize_job` with that reason and
  effective value. Also assert the negative: an unlisted reason comes back
  in `rejected[]` with `accepted=False` — the rejection contract stays
  intact, and rejected events MUST NOT touch the attempt fields.
  **`port_not_consumable` is a NEGATIVE case here (D28):** it is NO LONGER
  a degrade reason, so a `parallelism_degraded` event carrying it MUST be
  REJECTED (`accepted=False`, in `rejected[]`) — the ingestion
  degrade-validator set (`hosted_harness_ingestion.py:579`) lists only the
  five members and the guest `DegradeReason` enum (`outbound.py:581–583`)
  no longer constructs it. It is exercised as a TERMINAL JOB FAILURE
  (`failure.code` `port_not_consumable`, provision-time), not through the
  degrade-event path (§2 terminal block, §9).
- **Event-window eviction test** (required, blocking; Track E): ingest one
  accepted `parallelism_degraded`, then plant >100 subsequent accepted
  events; assert the degrade event is evicted from
  `serialize_job["events"]` (the `:113` cap) while the attempt-level
  fields still carry the effective value and reason — the display
  survives eviction.
- **Multi-attempt projection test** (required, blocking; Track E): degrade
  attempt 1 (e.g. `resource_limited` 4→2), then register a second attempt
  (rerun) and degrade it differently (e.g. `world_start_failed` 2→1, or no
  degrade at all). Assert `serialize_job` — which reads the LATEST attempt
  (`harness_provider.py:110`) — shows attempt 2's OWN projection (its
  reason list + effective value, or a cleared projection = requested when
  attempt 2 did not degrade), NEVER attempt 1's. Proves the projection is
  attempt-level and a new attempt starts cleared (§6).
- **Idempotency + ordering test** (required, blocking; Track E): ingest an
  accepted `parallelism_degraded` (say 4→2), then RE-DELIVER the identical
  event (same `event_id`) AND deliver an out-of-order duplicate carrying a
  HIGHER effective (say 4→3). Assert the attempt fields still read
  effective = 2 with the reason listed exactly ONCE — the dedup path
  (`hosted_harness_ingestion.py:479–483`) suppresses the second store, and
  `min(current_effective, event.effective)` plus append-if-absent prevent
  any raise of effective W or double-append (§6).
- **FE test** (required): the §6 copy table module renders every enum member
  and the unknown-string fallback (keyed off one shared vocabulary constant,
  not string literals scattered in components).
- **Guest test** (Track C′): `DegradeReason` equals §2's table verbatim.
- **Guest invariant tests** (required): the two §2 invariants — `effective`
  strictly decreasing, ≤1 event per reason per attempt — are guest-side
  MUSTs; unit tests assert them on `build.json`'s `degrade_events` list and
  the emitted event stream.
- **Platform cross-event check** (Track E, warning-only): the validator
  performs a per-attempt cross-event check of the same two invariants and
  LOGS violations — it never rejects on them. No new reject paths;
  per-event validation is unchanged.
- **Cross-repo lockstep**: guest and platform cannot import each other's
  enum. The fixture list in each repo's test IS this document's table; a
  reviewer changing one repo's list without a matching C4 version bump is a
  contract violation. New-reason procedure: bump this doc → platform
  validator + tests → deploy (pin i) → guest enum + emitter + tests →
  snapshot rebake.

---

## §9 Failure shapes summary

| Condition | Guest | Platform | FE |
|---|---|---|---|
| degrade at W>1, reason in v2 enum | one event per `degrade_events` entry, causal order, ≤1 per reason, `effective` decreasing | accept, persist payload, watermark advances | render effective W + §6 copy |
| degrade computed but `effective == requested` | no event; `log` warning (`hosted_entrypoint.py:1910–1917`) | — | — |
| world 0 fails to build at the FIRST provision call (`pool.start()`) | JOB FAILURE (C2 v1.3 §5 rule 1) — no degrade event (W′=0 unrepresentable) | failure object on the job | failure banner, not a degrade notice |
| consumable/knob-bearing process binds its DECLARED port (bind-death arm) OR a live listener sits on a declared `fixed_port` after the gate worlds build (gate listener check, D16), effective W>1 (§2 terminal block) | TERMINAL JOB FAILURE, reason `port_not_consumable`, raised out-of-band — NO degrade event, NO `degrade_events` entry (C1 v1.3 §4 decision 2 / D28); formula-port stale-squat excluded → graceful `world_start_failed` instead | failure object on the job, `failure.code` `port_not_consumable` | failure banner with C1's diagnosis (fix the port env, or request parallelism=1); NOT a degrade notice |
| world 0 (or pool re-provision) fails on a RECONCILE call | raise caught by WorldPool (`hosted_scheduler.py:1121–1152`); typed down-markers, job CONTINUES on healthy worlds | scheduler sick-world path — no degrade event, no job failure | no degrade notice |
| reason not in deployed validator's enum | drop + advance (`outbound.py:1593–1605`), then error `log` event (`hosted_entrypoint.py:920–927`) + artifact copy of the payload (`upload_artifact` at `:930`) — deployment-order violation, see pin (i) | reject `accepted=False`, row retained; the error `log` event is accepted | structured degrade invisible (no reason copy, no effective-W — only the prose `log` line in the feed) — MUST be prevented by §4, proven by §8 |
| explicit W>1 with `HARNESS_PARALLELISM_ENABLED` unset OR snapshot digest not in `HARNESS_PARALLEL_SNAPSHOT_DIGESTS` (fresh create, rerun, or retry) | receives attempt at W=1 | the SHARED admission guard at `register_attempt` (`hosted_harness.py:153–199`) clamps to 1 and records `metadata.parallelism_clamped` (authoritative, D12); the serializer runs the same guard at submit as the friendly early rejection; preflight returns `parallelism_enabled: false` and echoes `effective_parallelism: 1` (§5 no-stale-echo) | control visible but DISABLED, locked at 1 with explanatory text (§6); run detail shows the §6 clamp notice from `metadata.parallelism_clamped` |
| inline `source.environment_values` value carries a literal local endpoint, W>1 request (Channel 1, platform plaintext) | — (platform-side; the guest scan covers secret refs, not inline `environment_values`) | §7 Channel 1 submit-time warn (`serializers/harness_job.py:64`; W>1 only, never reject; port-generic pattern) | warning notice; not a degrade |
| vaulted secret-value (job `secret_refs`) carries a literal local endpoint, W>1 request (Channel 2, platform vaulted) | — (platform-side) | §7 Channel 2 submit-time re-scan warn (W>1 only, never reject; one vault read/submit) | warning notice; not a degrade |
| secret-ref value carries `localhost:<DECLARED port>`, W>1 (Channel 3, guest, degrade tier) | pre-plan scan degrades to 1, `literal_local_endpoint`; declared port honored, job runs as today (C1 §5.4a) | accepts the degrade event | §6 copy for `literal_local_endpoint` |
| secret-ref value carries a localhost literal to a NON-declared port (the observed `localhost:18090`), W>1 (Channel 3, guest, warn tier) | pre-plan scan WARNS only (alias + port), W unchanged — the job RUNS at requested W (C1 §5.4b) | accepts the `log` warning; no degrade event exists | no degrade notice; the warning is feed-visible prose |

---

## §10 Conformance checklist

Deployment & rollout:

- [ ] Ingestion validator lists the full §2 vocabulary
      (`hosted_harness_ingestion.py:579`) and is deployed before/atomic with
      any v2-emitting guest snapshot (pin i).
- [ ] Validator rollback guard: the v2 vocabulary is never reverted while
      EITHER the operator env var (`ALK_DAYTONA_SNAPSHOT_DIGEST`,
      `tfc/settings/settings.py:699–700`) OR any non-terminal per-attempt
      record (`snapshot_name`/`snapshot_digest`, `hosted_harness.py:186–193`)
      — including in-flight attempts — carries a v2-emitting snapshot; there
      is NO "registered-snapshot list" (pin i MUST; decision 13).
- [ ] W>1 admission AUTHORITATIVELY enforced by the SHARED admission guard
      at `register_attempt` (`hosted_harness.py:153–199`, §5/D12): flag
      truthy AND registered snapshot digest in
      `HARNESS_PARALLEL_SNAPSHOT_DIGESTS`, else clamp to 1 +
      `metadata.parallelism_clamped`. Covers fresh create, `rerun_saved`
      (`harness_provider.py:384`/`:599`), `harness_sandbox.rerun` (`:99`),
      and gateway retry — a direct API POST or a rerun of a saved W=4 job at
      W=4 with either condition failing is admitted at W=1 (pin ii; satisfies
      C3 v0.4 §2.1).
- [ ] The create-time serializer runs the SAME shared guard at submit as the
      friendly early rejection (beside `harness_job.py:205–212`) — not the
      enforcement authority; `register_attempt` is.
- [ ] The same flag+digest check re-runs at EVERY preflight/readiness call
      (`harness_provider.py:325–336`) as the continuous advisory surface —
      pin (ii).
- [ ] Preflight response carries `parallelism_enabled`; FE control is
      disabled-not-hidden when false (§5/§6).
- [ ] Preflight echo `effective_parallelism` (`harness_provider.py:328`)
      returns 1 — never the requested value — whenever admission would
      clamp (§5 no-stale-echo).
- [ ] `HARNESS_PARALLELISM_ENABLED` flag OFF in prod, ON in dev/E2E; the
      empty/unset digest (`""`, `tfc/settings/settings.py:700`) FAILS CLOSED
      (never allowlist-matches; pin ii).
- [ ] Dockerfile-mode dev carve-out: when `ALK_DAYTONA_DOCKERFILE`
      (`tfc/settings/settings.py:704`) is set, the shared guard requires the
      FLAG ONLY (digest skipped — dev carries no meaningful digest); prod
      always requires BOTH (§5; satisfies C3 v0.4 §9's referenced carve-out).
- [ ] `HarnessCreate.jsx:281` auto-request hunk absent from every PR diff.

Vocabulary & emitters:

- [ ] Guest `DegradeReason` (`outbound.py:581–583`) equals §2's FIVE members
      verbatim (`resource_limited`, `literal_local_endpoint`,
      `world_start_failed`, `fixed_port`, `conformance_gate_failed`) —
      `port_not_consumable` is NOT among them (terminal job failure, D28).
- [ ] Each reason's producer set is exactly §2's; no code outside that set
      writes it (single-producer everywhere except `conformance_gate_failed`'s
      documented pair).
- [ ] `build.json` `degrade_events` list (`{reason, from_w, to_w}`) written
      in causal order by exactly §2's enumerated writers (Track B);
      `requested_parallelism` stays the attempt constant (never mirrored
      from an entry); `effective_parallelism`/`degrade_reason` mirror the
      FINAL entry (`= last.to_w` / `= last.reason`); emitter emits one
      event per entry in order, `requested` = the constant, `effective` =
      entry `to_w`, `reason` = entry reason (Track C′). No `degrades` key
      anywhere (D7).
- [ ] `requested` constant, `effective` strictly decreasing, ≤1 event per
      reason per attempt — binding the `degrade_events` list; no
      `effective: 0` anywhere; guest unit tests assert both invariants (§8).
- [ ] Enum-member write sites named (§2 writer enumeration): (3) `fixed_port`
      at `process_runtime.py:245–246`; (4) genuine gate at the caller
      `:4591–4593` (not `run_conformance_gate`'s return); SIBLING AMENDMENT
      REQUIRED that C2 §6 enumerate the stage-3 and stage-4 writers too
      (C2 §6 currently lists only stages 1/2/5).
- [ ] `port_not_consumable` is a TERMINAL JOB FAILURE (NOT a degrade,
      D28 / C1 v1.3 §4 decision 2): removed from the degrade enum, the
      ingestion validator set, and the FE degrade copy; surfaced as a
      provision-time `failure.code` `port_not_consumable` + FE failure
      banner. Both detection loci raise it — the bind-death arm (world-≥1
      bind evidence naming a DECLARED port by a consumable process, or a
      non-formula/non-declared default port by a knob-bearing worker) AND
      the gate declared-port listener check (D16) — with the formula-port
      stale-squat exclusion (bind evidence must name a declared, non-formula
      port; a formula-port collision is graceful `world_start_failed`).
- [ ] Reconcile-path latch (`process_runtime.py:4594–4599`) re-stamps the
      legacy mirror only; grep shows no `degrade_events` append there; C2 §5
      rule 3 DECOUPLES the gate-branch catch from it (no synthetic
      `conformance = False`).
- [ ] Spine §5 amended: `1 ≤ effective < requested`
      (`hosted-execution-seams.md:906`); outbound-channels reason set
      amended.

Surfacing:

- [ ] Create form: default 1, max `min(8, cpu_units)`, no scenario-count
      derivation.
- [ ] `serialize_job` carries `job.runtime.{parallelism,cpu_units}`.
- [ ] Ingestion projects accepted `parallelism_degraded` events into
      ATTEMPT-LEVEL fields ONLY (never job-level): effective W + reason list;
      update happens ONLY on first store (tied to the `event_id` dedup,
      `hosted_harness_ingestion.py:479–483`) with
      `min(current_effective, event.effective)` + append-if-absent so a
      redelivered/out-of-order duplicate cannot raise effective W or
      double-append. `serialize_job` reads the latest attempt
      (`harness_provider.py:110`); a NEW attempt starts cleared (§6, Track E).
- [ ] Run detail renders effective W from the attempt fields; §6 copy per
      reason; unknown-reason fallback; info styling.
- [ ] Run detail shows the clamp notice when `metadata.parallelism_clamped`
      is present (§6; metadata exposed at `harness_provider.py:193`).
- [ ] §7 Channel 1 (inline `source.environment_values`,
      `serializers/harness_job.py:64`) AND Channel 2 (vaulted `secret_refs`
      re-scan) both submit-time warn for W>1 requests only (never W=1, never
      a reject); IPv6 loopback pattern covered.

Tests:

- [ ] Platform round-trip test: every reason accepted + visible in
      `serialize_job["events"]` + reflected in the attempt-level fields;
      unlisted reason rejected (and never touches the attempt fields).
- [ ] Event-window eviction test: >100 subsequent events after a degrade —
      event evicted from the feed, attempt fields (and the display they
      drive) survive (§8).
- [ ] Multi-attempt projection test: attempt 2 shows its OWN projection
      (or cleared = requested), never attempt 1's (§8).
- [ ] Idempotency + ordering test: a redelivered same-`event_id` event and
      an out-of-order higher-effective duplicate leave effective W and the
      reason list unchanged (min + append-if-absent; §8).
- [ ] FE copy-table test covers every member + fallback.
- [ ] Guest enum test equals §2.
- [ ] Guest invariant unit tests: `effective` strictly decreasing, ≤1 event
      per reason per attempt (list + emitted stream).
- [ ] Platform cross-event invariant check is warning-only: logs, never
      rejects.
- [ ] E2E ladder (W=2/W=4, flag on in dev) observes at least one real
      degrade event rendered in the FE.

---

## §11 Changelog

- Consistency-pass reconciliation (post-freeze, 2026-08-31 — NOT a review
  round, no version bump; adopting C1 v1.3 §4 decision 2 / D28 and C2 v1.3
  §5): **`port_not_consumable` reclassified from a degrade reason to a
  TERMINAL JOB FAILURE.** Removed from the §2 degrade enum (now FIVE members:
  `resource_limited`, `literal_local_endpoint`, `world_start_failed`,
  `fixed_port`, `conformance_gate_failed`), from the writer enumeration, from
  the ingestion validator set (`hosted_harness_ingestion.py:579`) and guest
  `DegradeReason` (`outbound.py:581–583`), and from the §6 FE degrade-copy
  table; ADDED as a provision-time typed job failure (`failure.code`
  `port_not_consumable` + FE failure banner with C1's diagnosis), surfaced
  like the other provision-time job failures (secret-resolution ERROR,
  `literal_local_endpoint` fallback, world-0-at-first-provision). Its two
  detection loci — the bind-death arm and the gate declared-port listener
  check (D16) — both RAISE the terminal failure out-of-band, with the
  formula-port stale-squat exclusion (bind evidence must name a declared,
  non-formula port). §2 table/terminal block, writer enumeration, §6, §8
  round-trip test (now a negative case for the degrade path), §9 (new
  terminal row), §10 checklist updated. `world_start_failed` producer row
  scoped to the first-build / digest-rebuild branch only (reconcile-call
  rebuild failures → scheduler down-markers, `hosted_scheduler.py:1121–1152`,
  never `degrade_events`; C2 v1.3 §5). Sibling version pins swept to the
  frozen siblings (C1 v1.3, C2 v1.3, C3 v0.4); §7 C2 `[::1]` degrade-tier ask
  marked SATISFIED (C2 v1.3 carries all three loopback forms). Naming/pins
  and the terminal reclassification only — the degrade transport, projection,
  and admission mechanics are unchanged.
- v1.3 (2026-08-31, round-3 fixes — **the contract is now FROZEN; no round
  4**): BLOCKER — the W>1 admission gate (flag AND digest, else clamp to 1 +
  `metadata.parallelism_clamped`) is factored into ONE shared admission
  guard invoked AUTHORITATIVELY at `register_attempt`
  (`hosted_harness.py:153–199`), the single chokepoint every attempt passes
  through (fresh create, `rerun_saved` `harness_provider.py:384`/`:599`,
  `harness_sandbox.rerun` `:99`, gateway retry — all via
  `DaytonaHostedGateway.launch` `:849`); the create-time serializer check
  remains as the friendly early rejection, not the authority; a saved W=4
  job reruns at W=1 when the flag/digest no longer qualify (§4 pin (ii), §5,
  §9, §10; Track E; decision D12). MAJOR: dockerfile-mode dev carve-out —
  when `ALK_DAYTONA_DOCKERFILE` is set the digest half is SKIPPED and W>1
  requires the flag ONLY (dev-only; prod always both; §5; satisfies C3 v0.3
  §9's referenced carve-out). MAJOR: "caught automatically, no human re-run"
  claim REMOVED — `ALK_DAYTONA_SNAPSHOT_DIGEST` is an operator-maintained env
  var (`tfc/settings/settings.py:699–700`), not derived from
  `ALK_DAYTONA_SNAPSHOT`; replaced by a REQUIRED operational invariant
  (snapshot + digest allowlist updated in lockstep; empty/unset digest `""`
  FAILS CLOSED; §4 pin (ii)). MAJOR: §7 now covers BOTH platform channels —
  a NEW submit-time scan of the inline `source.environment_values` plaintext
  channel (`serializers/harness_job.py:64`; plan v10 serializer-locus
  mandate, plan lines 272/323) plus the existing vaulted-refs re-scan
  (ADDITIONAL, not a replacement); guest secret-refs scan recorded as the
  third channel; §9 rows split accordingly. MAJOR: degrade projection is
  ATTEMPT-LEVEL (never job-level); `serialize_job` reads the latest attempt
  (`harness_provider.py:110`); a new attempt starts cleared; multi-attempt
  test added (§6/§8). MINOR: projection updates ONLY on first store (tied to
  the `event_id` dedup, `hosted_harness_ingestion.py:479–483`) with
  `min(current_effective, event.effective)` + append-if-absent — redelivered
  / out-of-order duplicates can neither raise effective W nor double-append;
  idempotency test added (§6/§8). MINOR: C3 pin bumped to v0.3; submit-time
  enforcement labelled decision D12; C3 §2.1 amendment SATISFIED against v0.3
  text. MINOR: `[::1]` tier owner — C1 v1.2 §5.4a already has `[::1]` in the
  degrade tier (SATISFIED); the outstanding amendment is C2's degrade tier
  ALONE (§7). MINOR: world-0-failure=job-failure scoped to the FIRST
  provision call per C2 v1.2 §5 rule 1 (reconcile-time world-0 failure →
  down-markers + continue, `hosted_scheduler.py:1121–1152`; §2, §9). MINOR:
  emission timing referenced normatively (C2 §6 emission split — emit once,
  post-emission ledger updates NOT re-emitted) and the emitter missing-list
  fallback pinned (no `degrade_events` list → legacy scalars, still emits
  today's `fixed_port`/`conformance_gate_failed`, never nothing; §2/§3).
  MINOR: concrete write sites named — (3) `fixed_port`
  `process_runtime.py:245–246`, (4) genuine gate = the caller `:4591–4593`
  (not `run_conformance_gate`'s return); SIBLING AMENDMENT REQUIRED that C2
  §6 enumerate the stage-3/stage-4 writers (currently only 1/2/5). MINOR:
  Plan-deviations block added (effective-W display moved from the plan's
  FE-only 100-event read path to ingestion-write + persisted attempt fields
  + serializer read — the read path §6 rejects). MINOR: rollback rule fixed —
  no "registered-snapshot list"; the artifacts are the per-attempt
  `snapshot_name`/`snapshot_digest` records (`hosted_harness.py:186–193`)
  plus the operator env var; validator MUST NOT revert while any per-attempt
  record OR in-flight attempt carries a v2-emitting snapshot (in-flight-emitter
  risk named). NIT: citations — artifact-copy upload is `:930`
  (`hosted_entrypoint.py`), effective==requested warning branch is
  `:1910–1917` (`:1904–1908` is the emit call), reconcile latch is
  `:4594–4599`. NIT: C2 §5 rule 3 characterization corrected — it DECOUPLES
  the gate-branch catch from the `:4594–4599` latch (no synthetic
  `conformance = False`), it does not "extend" it. NIT: spine §5
  stage-phrasing amendment (`validating_environment` → provision-time stages
  may vary) added to the Amends header (§2 already declared it). No enum
  change; the six-member vocabulary is unchanged and now FROZEN.
- v1.2 (2026-08-31, round-2 fixes): Transport spec completed — C4 owns the
  `degrade_events` shape seam-wide: exact mirror set
  (`requested_parallelism` = attempt constant, never entry-mirrored;
  `effective_parallelism`/`degrade_reason` = final entry), entry→event
  payload mapping, complete per-reason list-write enumeration, and the
  C2 `degrades`-shape divergence recorded RESOLVED in C4's favor
  (decision D7; C2 keeps writer-side rules only). Pin (ii) enforcement
  locus pinned to the platform serializer at SUBMIT TIME (flag +
  registered-digest allowlist; covers direct API POSTs; preflight stays
  the continuous advisory surface) — satisfies C3 v0.2 §2.1's sibling
  amendment; §5/§10 updated to match. DECIDED: attempt-level projection —
  accepted degrade events update persisted attempt fields at ingestion,
  `serialize_job` exposes them, the FE reads them, never the 100-event
  window (`harness_provider.py:113` cap); round-trip test asserts the
  fields; new >100-event eviction test (Track E). C1 v1.1 §4's
  THREE-condition `port_not_consumable` rule adopted verbatim
  (bind-error log evidence required); header pins bumped (C1 v1.1,
  C2 v1.1, C3 v0.2). Reconcile-path latch (`process_runtime.py:4598–4599`)
  added to `conformance_gate_failed`'s producer set as a legacy-mirror
  re-stamp, never a new ledger entry (C2 latch/dedup rules). §9
  `environment_values` row fixed to the two-tier truth (degrade only on
  DECLARED-port literals; the observed `localhost:18090` warns and RUNS at
  requested W) with channels kept distinct (platform submit-time warn vs
  guest pre-plan scan). Clamp surfacing: FE run-detail notice pinned on
  `metadata.parallelism_clamped` (`harness_provider.py:193`). Pin (ii)
  guard wording fixed (`process_preflight.py` exists; the GUARD is what's
  absent at ALK HEAD). Dockerfile-mode limitation noted on pins (i)/(ii)
  (dev guests bypass snapshot registration; accepted dev-only,
  prose-degrade consequence in dev). No-stale-echo pin: preflight
  `effective_parallelism` (`harness_provider.py:328`) reflects the clamped
  value. One SIBLING AMENDMENT REQUIRED added (C2 §4: `[::1]:<port>` in
  BOTH scan tiers).
- v1.1 (2026-08-31, round-1 fixes): §1/§9 rewritten to verified rejection
  behavior — a rejected event yields an accepted, FE-visible error `log`
  event + artifact copy (`hosted_entrypoint.py:902–929`); what vanishes is
  the STRUCTURED degrade, and pin (i)'s rationale now says so.
  `port_not_consumable` split RESOLVED per C1 §4 (enum unconditionally six;
  false `conformance_reason` free-text option deleted — `run_conformance_gate`
  returns only the constant, `process_runtime.py:4084–4165`). Normative
  `build.json` `degrade_events` list transport (legacy fields mirror last
  entry; one event per entry; invariants bind the list). `parallelism_enabled`
  in the preflight response; FE control disabled-not-hidden. Pin (ii) made
  continuous against `HARNESS_PARALLEL_SNAPSHOT_DIGESTS`; pin (i) gains a
  rollback MUST. §7 decided: submit-time warn, W>1 only, IPv6 loopback added.
  Emitter table aligned to C1 (complete producer sets;
  `conformance_gate_failed`'s two producers). Invariants guest-unit-tested +
  platform warning-only cross-check (§8). Citation fixes
  (`process_runtime.py:245–246`; `ingest_event_batch`,
  `hosted_harness_ingestion.py:99–134`/`133`). Pinned against C1 v1.0 and
  C2 (in draft).
- v1 (2026-08-31): initial draft.
