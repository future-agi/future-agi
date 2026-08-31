# C3 — Call Affinity & Dispatch Acknowledgment (v0.4 — FROZEN)

**Boundary**: world (per-world agent worker, in-sandbox) ↔ CallRunner
(`src/fi/alk/harness/call_runner.py` + the LiveKit engine it drives,
`src/fi/simulate/simulation/engines/livekit.py`) ↔ LiveKit server (EU,
`wss://livekit-eu.futureagi.com`).

**Status**: v0.4 FROZEN, 2026-08-31 — round-3 review fixes applied; this is
the FINAL round and the contract FREEZES with this version (no round 4). This
contract is CO-AUTHORED: Khushal + Claude (Track B) and Azain
(LiveKit/CallRunner owner). LiveKit-operational specifics Khushal cannot
verify from this side are marked **AZAIN TO CONFIRM** and are carried into
implementation as open confirmations against the frozen text; **DECIDED**
marks are settled and are not reopened. Any residual that could not be fully
closed before freeze is recorded inline as **KNOWN RESIDUAL (frozen)**.
Everything else is normative.

**Implements**: parallelism-plan-v10.md §1 (call-affinity block), §3 (P0-B
consequences: worker-knob mechanism, C3 consequences incl. the ack-retry
rule), §7 (lost-dispatch + static-name rows). This document pins those
decisions at the seam; it does not reopen them. Evidence:
`~/fai-parallelism-evidence/p0b_results.md` + the five archived
`p0b_probe-*` logs.

**Plan deviations (recorded deliberately)**:

- The plan's P0-C C3 row reads "typed failure on mis-dispatch". Post-hoc
  mis-dispatch DETECTION is impossible — the plan's own §1/§7 say so
  (delivery to a same-named registrant succeeds; no post-hoc signal
  distinguishes it). This contract therefore implements PREVENTION (the
  §2.1 uniqueness guards) plus a typed failure on NO-JOIN (§4). Deviation
  from the row's letter, not its intent.
- The plan's §3 says dispatch-ack is "implemented in `call_runner.py`".
  Locus refined (**DECIDED**, §4.5): the ack ladder lives INSIDE the
  LiveKit engine (`engines/livekit.py`), adjacent to its `create_dispatch`
  site — the wire-level room name, the dispatch id, and the room join
  events are all local there, none visible to `call_runner.py`. The typed
  surfacing is a scoped Track B change spanning THREE files
  (engine + CallRunner + scheduler), gated on a hosted-harness opt-in
  (§4.5): the engine raises the ladder's OWN typed exception carrying a
  structured marker, CallRunner re-raises it as `CallAborted` carrying that
  marker, and the scheduler's `CallAborted` catch selects receipt code
  `voice_dispatch_unacknowledged`. This **REVISES v0.3's "call_runner-only,
  scheduler unchanged" claim** (round-3 finding 1): the scheduler catch
  (`hosted_scheduler.py:1885-1894`) hardcodes `call_failed` for every
  `CallAborted`, so it HAD to change. Track B owns all three sites (with
  Azain).
- The plan's §1/§7 define the uniqueness guard WORLD_INDEX-only and call
  platform-authored bundles "already safe (`-w{{WORLD_INDEX}}`)". C3
  WIDENS the guard (§2.1): BOTH `{{WORLD_INDEX}}` AND `{{JOB_ID}}` are
  required, so a WORLD_INDEX-only name — including the plan's cited
  shape — now FAILS bundle preflight at requested W>1.
  (Platform-authored bundles still pass: both paths render both
  placeholders, `bundle_author_v2.py:646-649`, `:749-751`.) Deliberate
  widening for the shared-server cross-job case, not a plan re-opening.

**Code anchors**: agent-learning-kit at current HEAD (2026-08-31). Line
numbers below were re-verified against that tree for this draft; where they
drift from older contract copies (e.g. bundle-producer-contract §2.7's
`call_runner.py:181-190`), THIS document's anchors are the fresher ones.
livekit-agents facts are 1.7.1-wheel facts as verified in p0b_results.md.

---

## 1. Affinity mechanism — dispatch-by-name determinism

Scenario-to-world call affinity is carried by ONE mechanism: the LiveKit
**agent name** the dispatch targets. No other affinity mechanism exists or
may be introduced (no room-membership inference, no worker locality, no
participant-identity matching).

The chain, each link code-verified:

1. Platform-authored bundles render a job- and world-unique agent name.
   The multi-component path hardcodes the literal
   `uber-voice-booking-{{JOB_ID}}-w{{WORLD_INDEX}}` via `setdefault`
   (`bundle_author_v2.py:646-649`); the single-component path derives the
   prefix from `root.name` and direct-assigns the same
   `-{{JOB_ID}}-w{{WORLD_INDEX}}` suffix (`:749-751`).
   `{{JOB_ID}}`/`{{WORLD_INDEX}}` are fixed placeholders
   (`process_preflight.py:107`; renderer `process_runtime.py:389-395`).
2. At spawn, the provisioner records the RENDERED `LIVEKIT_AGENT_NAME`
   on the process handle (`process_runtime.py:1631`).
3. A world whose processes render exactly one distinct name surfaces it as
   `runtime.metadata["livekit_agent_name"]`; zero or >1 distinct names
   leave the key absent (`process_runtime.py:1132-1155`).
4. CallRunner reads that key — the only read site
   (`call_runner.py:236-243`) — and aborts pre-dial, typed
   `voice_dispatch_identity_unavailable`, when absent
   (`call_runner.py:674-679`).
5. The engine creates the dispatch by that exact name:
   `create_dispatch(CreateAgentDispatchRequest(agent_name=..., room=...))`
   (`engines/livekit.py:1064-1077`).

**Normative:**

- Every dispatch MUST target the world's metadata agent name verbatim. The
  dispatch identity MUST come from the RENDERED environment (it does:
  `process_runtime.py:1631`), never from the injected/spawn environment —
  see §2.2 for why the distinction is load-bearing.
- At W>1, agent names MUST be unique per world AND per job (§2.1 is the
  guard). Two registrants sharing one name — two worlds of one job, or
  two concurrent jobs on the shared server — is the one failure mode
  dispatch-ack CANNOT catch: delivery succeeds, to an arbitrary
  same-named registrant; a same-named agent joins, so the ack PASSES; and
  evidence is silently cross-contaminated. Prevention is the only
  defense.
- Room names are per-call and deterministic: prefix
  `harness-{job8}-a{attempt}-{scenario_key}-s{scenario_attempt}`
  (`call_runner.py:296-299`); the engine appends its own
  `-{invocation_id}-{test_case_id[-12:]}` suffix in managed room mode
  unless `room_name_verbatim` is set (which CallRunner does not set). Any
  layer that keys on the room (§4.3's dispatch-list check) MUST use the
  wire-level room name the engine actually dialed, not the prefix.

**Evidence (P0-B, 2026-08-31, EU LiveKit).** Server-side dispatch-by-name
routing is deterministic: **73 correct deliveries, 0 foreign, across the
two loaded runs** (final clean run 40/40; `p0b_results.md`). The other
three runs delivered nothing and are VACUOUS for the no-foreign claim —
they neither support nor weaken it. Scope honestly stated: the probe
workers ran on a dev Mac — P0-B verifies SERVER-SIDE routing only, not the
in-sandbox join/media path. Losses observed in run 1788173178 were drops
(§3), never misroutes.

## 2. Preconditions — the agent-name override channels

Dispatch-by-name is only as good as name uniqueness. Two channels can
defeat the platform's job- and world-unique rendering; each has a named
owner and a pinned failure shape. Neither is "covered" by the other.

### 2.1 Bundle-env channel → bundle preflight reject (Track B, C3 precondition)

The platform's unique name is applied via `setdefault`
(`bundle_author_v2.py:646-649`): a bundle-supplied static
`LIVEKIT_AGENT_NAME` OVERRIDES it. A hand-authored or overridden bundle
with a static (or job-shared) name at W>1 produces silent cross-world —
and, on the shared server, cross-job — contamination.

- **Guard**: `agent_name_not_world_unique` — REQUESTED job parallelism
  > 1 AND any process's `LIVEKIT_AGENT_NAME` template lacking EITHER
  `{{WORLD_INDEX}}` OR `{{JOB_ID}}` → bundle preflight REJECT, before
  any provisioning, no call ever placed. BOTH placeholders are required:
  `{{WORLD_INDEX}}` gives world-scoped uniqueness (cross-WORLD
  contamination), `{{JOB_ID}}` gives job-scoped uniqueness (cross-JOB
  contamination) — all jobs share `wss://livekit-eu.futureagi.com`, so
  two concurrent jobs registering one name deliver each dispatch to an
  arbitrary worker ACROSS JOBS, and the ack passes (a same-named agent
  joins); prevention is the only defense (§1). The platform-authored
  multi-component path already complies (literal
  `uber-voice-booking-{{JOB_ID}}-w{{WORLD_INDEX}}`,
  `bundle_author_v2.py:646-649`), as does the single-component path
  (`:749-751`). The world half is specified at
  `bundle-producer-contract.md` §2.7; the guard is **ABSENT at ALK
  HEAD** (grep-verified 2026-08-31 — no hit anywhere in `src/`). Track B
  ships it in `process_preflight.py` (+0.25 d). The within-world
  multi-name rule (`process_runtime.py:1132-1155`) is the behavioral
  template, not the location.
- **SIBLING AMENDMENT** (`bundle-producer-contract.md` §2.7) — **APPLIED
  (consistency pass)**: the mandated example was
  `LIVEKIT_AGENT_NAME: "agent-w{{WORLD_INDEX}}"` (lacking `{{JOB_ID}}`, which
  would FAIL this guard); §2.7 now mandates
  `"agent-{{JOB_ID}}-w{{WORLD_INDEX}}"` and its rule text requires BOTH
  placeholders at W>1. Satisfied.
- **Accepted false positive**: the guard keys on REQUESTED W>1 at
  bundle preflight — effective W does not exist yet at that point. A job that
  would have degraded to W=1 anyway is still rejected. Accepted; the
  reject message MUST tell the user to request W=1 explicitly if a
  static name is intended.
- **Scope note — W=1 cross-job collision is PRE-EXISTING**: a static
  name at W=1 collides across concurrent jobs on the shared server
  today, unchanged by this project. Documented here; extending the same
  guard (require `{{JOB_ID}}` at W=1 too) is RECOMMENDED as a follow-up;
  out of C3's normative scope.
- This guard is a HARD PRECONDITION of C3: W>1 MUST NOT be reachable in
  any deployed snapshot that lacks it. Enforcement mechanism, stated
  precisely: the platform serializer enforces the
  `HARNESS_PARALLELISM_ENABLED` flag AND a snapshot-digest allowlist
  match at SUBMIT-TIME ADMISSION — covering the FE path AND the direct
  API path. **Track E adds** the continuous ADVISORY surface to the
  platform readiness endpoint (`harness_provider.py:325-336`): TODAY that
  endpoint only ECHOES `effective_parallelism` back as the raw requested
  value (`:328`) plus the snapshot name and digest (`:329-334`) — it does
  NO digest comparison, carries NO allowlist, and returns NO
  `parallelism_enabled` field. C4 §5 / pin (ii) adds all three as Track E
  work (digest-allowlist match + flag, plus `parallelism_enabled` on the
  echo); the submit-time serializer is the enforcement, the readiness
  endpoint the continuous advisory echo. Not a one-shot rollout check.
  Terminology, used consistently in this document: **bundle preflight** =
  the guest-side pass in `process_preflight.py` (where this section's name
  guard lands); **platform readiness endpoint** = `harness_provider.py:325-336`
  (where C4's advisory digest check will run once Track E lands it).
- **SIBLING NOTE** (`c4-degrade-surfacing.md`) — **SATISFIED**. C3
  depends on submit-time serializer enforcement of BOTH the
  `HARNESS_PARALLELISM_ENABLED` flag AND the snapshot-digest allowlist,
  covering the FE path and the direct API path. **C4 §5 / pin (ii)** now
  pins exactly that — the platform serializer, at submit time, requires
  the flag truthy AND the currently-registered snapshot digest present in
  `HARNESS_PARALLEL_SNAPSHOT_DIGESTS`; the preflight/readiness endpoint is
  the continuous advisory echo. Present and satisfied — no further C4
  change is needed for C3. (Referenced by SECTION — C4 §5 / pin (ii),
  the frozen locus — deliberately NOT by any decision identifier, which
  may not survive verbatim in C4's frozen text.)
- Scope, stated plainly: the guard closes the bundle-env channel ONLY —
  not "every W>1 path" (§2.2 exists).

### 2.2 Injected-secrets channel → loud no-join + spawn-time warning

Spawn env merges FOUR layers, in order: `**build_environment, **rendered,
**injected, **authoritative_endpoints` (`process_runtime.py:1591-1594`).
Authoritative capability endpoints re-assert world-correct values AFTER
injected secrets — but only for capability configuration variables the
process's own `environment` declares. `LIVEKIT_AGENT_NAME` is NOT among
them, which is exactly why the injected override wins for that variable
(this claim is scoped to it; capability endpoints like `TOOLS_API_URL`
are protected). So a job secret aliased `LIVEKIT_AGENT_NAME` overrides
the world-unique name in the worker's actual environment IN EVERY WORLD,
while the dispatch still targets the RENDERED name
(`process_runtime.py:1631`). This channel is invisible to bundle
preflight (secrets resolve in-sandbox, after bundle preflight has run).

- **Failure shape at W>1 (and W=1)**: every world's worker registers under
  the injected name; every dispatch targets a rendered name; the general
  result is an ALL-WORLDS NO-JOIN — loud (each call fails typed, §6), but
  undiagnosed without the warning below.
- **Documented residual risk (real, not prevented)**: if the injected
  static value EQUALS one world's rendered name, that world's dispatches
  remain deliverable — to an arbitrary same-named registrant — which is
  genuine residual contamination, not merely a no-join. The spawn-time
  warning still fires in the other W−1 worlds, so the condition is
  DIAGNOSED but not prevented. This is the accepted residual of the
  secrets channel: values are invisible to bundle preflight by design.
- **Spawn-time warning (normative home: C2; C3 consumes it)**: injected
  value present for a guarded key and differing from the rendered value
  → a surfaced WARNING through the existing warning channel (a `log`
  event, level `warning`, naming the key but NEVER the values — the
  injected one is a secret). NOT a member of the closed degrade-reason
  enum; NOT a spawn failure. Per C1 §4, C2 legislates this warning; C3
  references it as the diagnosis signal for this channel and does not
  re-legislate it. The guarded key set MUST cover `LIVEKIT_AGENT_NAME` +
  `FI_LOAD_THRESHOLD` + `FI_NUM_IDLE_PROCESSES` +
  `FI_WORKER_HEALTH_PORT`.
- **SIBLING NOTE** (C2) — **SATISFIED**. C2 v1.3 §4a now legislates the
  spawn-time override warning with exactly the four-key set
  (`LIVEKIT_AGENT_NAME`, `FI_LOAD_THRESHOLD`, `FI_NUM_IDLE_PROCESSES`,
  `FI_WORKER_HEALTH_PORT`), as a MUST, and attributes the normative home
  correctly to C2 (its §4a opens "This is the section C1 §3/§4 and C3
  §2.2 point at as C2's"). The earlier c2:220 inversion — C2's draft
  attributing the warning to C3 — no longer exists. C1 v1.3 §4 likewise
  requires all four keys verbatim; C1, C2, and C3 agree on the set.
  Present and satisfied.
- Dispatch-ack (§4) converts the resulting no-joins into typed,
  bounded-time failures instead of full-timeout hangs.

### 2.3 Within-world ambiguity (exists today)

A world rendering more than one distinct `LIVEKIT_AGENT_NAME` gets NO
dispatch identity (`process_runtime.py:1146-1155`, warning logged) and the
call aborts pre-dial: typed `voice_dispatch_identity_unavailable`
(`call_runner.py:674-679`). Unchanged by this contract.

## 3. The lost-dispatch mode (normative framework facts)

These are livekit-agents **1.7.1** facts, verified against the genuine
wheel (provenance note in `p0b_results.md`), plus the run-178 log evidence.
They are premises of §4 and §5; an implementation MUST NOT assume the
opposite of any of them.

- `WorkerOptions.load_threshold` defaults to **0.7** in production
  (`worker.py:148`), and the load metric is **SYSTEM CPU percent** —
  machine-wide, not per-worker (`worker.py:98`). Crossing it marks the
  worker "at full capacity → unavailable".
- Dispatches created while no eligible worker is available are **DROPPED,
  not queued**. Log-evidenced (run 1788173178): rooms created inside w0's
  unavailability window (10:46:39.29 → 10:46:41.79) were never delivered —
  not after recovery (the worker kept receiving later rooms for seconds;
  the driver drained 25 s more), and not to any other worker (0 foreign).
  Every `create_dispatch` call succeeded server-side — with
  `p0b_results.md`'s caveat carried verbatim: that claim is INFERRED
  from the driver not aborting (only round-0 dispatch IDs were printed),
  not from per-call dispatch IDs.
- **Server eligibility lags local recovery**: run-178 shows a dispatch
  lost ~0.4 s AFTER the worker's own "available again" log line. A
  worker-local availability signal is therefore NOT a delivery guarantee.
- Workers drain for **3600 s on SIGTERM**; prewarm default
  `num_idle_processes = min(cpu_count, 4)` per worker (`worker.py:208`).
- Production relevance: a W=4 sandbox running four real voice agents sits
  near/above 70% system CPU routinely — this mode is MORE likely in
  production than in the near-idle probe that still tripped it. The
  final 40/40 PASS run passed because the threshold never fired, not
  because the mode is absent.

## 4. Dispatch acknowledgment

### 4.1 Decision

**Default = ADOPT** end-to-end dispatch-ack verification, implemented
INSIDE the LiveKit engine (`engines/livekit.py`), adjacent to its
`create_dispatch` site, as a concurrent ack task nested inside the
engine's readiness wait (**DECIDED** — §4.3 for the shape, §4.5 for the
locus rationale;
Track B with Azain, +0.25–0.5 d, exercised in the E2E ladder). Declining
requires **refuting the run-178 log** — absence of losses in other runs
is not a refutation. Until ack lands (or if C3's ack is descoped per the
plan's descope trigger), the interim backstop is §4.6.

### 4.2 The ack signal — end-to-end, defined

A dispatch is acknowledged when, and only when, there is **end-to-end
evidence of delivery**: the dispatched agent (the participant the named
worker's job spawns) has JOINED the call's room within the ack ladder's
windows (§4.3). The ack clock STARTS at the FIRST `create_dispatch`
return (§4.3's wall-clock definition) — the initial creation question is
already covered by the engine's existing typed failures (§4.5); ack
covers only the delivery question. The engine's readiness signal remains
the ULTIMATE end-to-end ack — the ladder adds bounded re-delivery
attempts underneath the readiness wait, it does not replace it.

**Attribution rule (DECIDED)**: the ack attributes a join to THIS
dispatch by DISPATCH IDENTITY where the server exposes it — dispatch
state via `ListAgentDispatch` showing this dispatch's job running AND
the agent participant present in the room — with agent-name match on
the joined participant as the FALLBACK. Name-based attribution is sound
ONLY under §2.1's job+world-unique guard: absent the guard, a
same-named foreign registrant's join is indistinguishable from
delivery. That dependency is explicit and load-bearing.

Explicitly NOT an ack, each for an evidenced reason:

- `create_dispatch` API success — all of run-178's lost dispatches had it
  (§3; itself carrying §3's inference caveat — only round-0 dispatch IDs
  were printed, the driver not aborting is the evidence).
- Worker-local availability ("available again", registration liveness,
  health-port 200) — run-178's ~0.4 s eligibility lag (§3).
- Server-side dispatch listing alone — a listed dispatch may still be
  awaiting a job spawn; listing is the retry KEY (§4.3), not the ack.

**AZAIN TO CONFIRM (signal source)**: the concrete join signal available
to the engine on EU LiveKit — room participant-joined event for the
agent's participant vs. polling room participants vs. dispatch state in
`ListAgentDispatch` responses (whether the EU server version populates a
usable delivery/job state on the dispatch object) — and the exact
participant-identity convention the dispatched agent joins under (what
identity/name the fallback match keys on). The contract requires the
signal to be end-to-end; Azain picks which LiveKit surface provides it.

### 4.3 The ack ladder — concurrent with the readiness wait; bounded, typed, keyed on the server dispatch list

**Placement (DECIDED): the ladder runs CONCURRENT with — nested inside —
the engine's readiness wait, never sequentially before it.** After the
initial `create_dispatch` returns, the engine proceeds into its readiness
wait exactly as today; a concurrent ack task evaluates delivery at the
+20 s and +40 s marks (each un-acked mark = delete stale dispatch +
re-create, attempts 2 and 3) and, at the +60 s mark, ABORTS the readiness
wait and raises the typed failure (step 4). Because the ladder nests
inside the 120 s readiness window, "exhaustion preempts the readiness
timeout" is true by construction (60 < 120), and `run_seconds` and its
60 s pad are UNTOUCHED — `call_runner.py:99` and `:705-711` are
unchanged; the ladder adds no outer-clock change anywhere.

**Budget semantics (DECIDED): wall-clock, fixed marks.** The ≤60 s ack
budget is WALL-CLOCK time from the FIRST `create_dispatch` return. Window
boundaries sit at the fixed +20 s / +40 s / +60 s marks regardless of
per-attempt overhead: an attempt's `ListAgentDispatch` /
`DeleteAgentDispatch` / `create_dispatch` calls run INSIDE its window,
and an attempt whose calls have not completed by its window mark is
FORFEITED — no extension; the next mark's evaluation (or exhaustion)
proceeds on schedule.

**Forfeit + in-flight-create reconciliation (DECIDED — MUST; closes the
in-flight-create race).** "Forfeited" MUST NOT mean "fire a fresh
`create_dispatch` at the next mark regardless." When a prior attempt's
`create_dispatch` is itself still in-flight at the next mark (the create
call has not returned), a blind new create would leave TWO dispatches
possibly outstanding for one (room, agent name) — the exact violation of
step 2's invariant. So before issuing any new `create_dispatch`, the
ladder MUST reconcile the prior attempt's in-flight create: **cancel it**,
OR **confirm via `ListAgentDispatch` that it did not materialize**. If it
can be NEITHER cancelled NOR confirmed-absent, the create is treated as
**possibly-outstanding** and the ladder issues NO new dispatch that mark
(it forfeits the re-creation — never the invariant). The
at-most-one-outstanding invariant WINS over the fixed-mark cadence in this
corner: a mark may pass with no re-dispatch, but never with a
double-delivery. The clock still advances on schedule; an
all-possibly-outstanding ladder simply reaches +60 s exhaustion (step 4)
having re-created fewer than twice.

At each un-acked mark (+20 s, +40 s):

1. **List before re-dispatch (MUST).** Query the room's dispatches
   (`ListAgentDispatch`, keyed on the wire-level room name — §1). If a
   dispatch for THIS agent name is still listed, the engine MUST NOT
   create another. It either deletes the stale dispatch first
   (`DeleteAgentDispatch`) and then re-dispatches, or waits out to the
   next mark (§7 frozen residual 3).
2. **Invariant (MUST): at most ONE outstanding dispatch per
   (room, agent name) at any moment.** Double-delivery contaminates the
   very evidence the ack protects.
3. **Bounded attempts (DECIDED).** Three attempts total: the initial
   dispatch plus the re-creations at the +20 s and +40 s marks; the
   +60 s mark is exhaustion, then STOP. **AZAIN TO CONFIRM**: whether
   the 20 s windows / 2 re-creations are sane on EU LiveKit (§8).
4. **Exhaustion → typed failure, never a silent drop (MUST).** At the
   +60 s mark with no ack, the ack task ABORTS the engine's readiness
   wait by raising **the ladder's OWN typed exception** — carrying
   `voice_dispatch_unacknowledged` in a STRUCTURED field (never in
   message text), plus room name, agent name, attempts made, and the
   window marks. It MUST NOT let the readiness wait fall through to an
   `asyncio.TimeoutError`: that path (`engines/livekit.py:1409-1435`)
   maps to `TestCaseStatus.AGENT_UNAVAILABLE` → `WorldUnavailable` →
   world retirement (§4.6), the exact outcome decision 2 (§7) forbids.
   The engine's handler maps this ladder exception to the structured-marker
   `CallAborted` of §4.5; the marker is read from that structured field,
   NEVER string-matched from `case.failure.message`.
   Classification (**DECIDED**): a
   scenario-level call failure (errored), NEVER world retirement —
   run-178's cause is machine-wide system CPU; retiring one world
   misattributes a machine-wide condition and can cascade retirements.
   **Ladder-attempt create failures fold in (DECIDED)**: a
   `create_dispatch` failure on attempts 2-3 counts against the ack
   budget (its attempt is spent; the ladder proceeds to the next mark)
   and converts to `voice_dispatch_unacknowledged` on exhaustion — only
   the INITIAL, pre-ladder create failure keeps the existing
   `livekit_dispatch_failed`/`livekit_dispatch_timeout` shapes (§4.5).
5. **Participant-presence checks are NON-CONFORMANT as the retry guard
   (MUST NOT).** A "is the agent participant in the room yet?" check
   passes during a slow join — a dispatch accepted by a worker whose job
   is still spawning has NO participant in the room yet — so the check
   green-lights a re-dispatch in exactly the race it targets, and the
   retry double-delivers. (Participant join remains the ACK signal in
   §4.2; it is banned only as the *re-dispatch admission* check. The
   difference: for ack, a false "not joined" merely waits; for retry
   admission, it dispatches twice.)

Failure of the LIST call itself (ListAgentDispatch error/timeout): the
engine MUST treat the dispatch as possibly-outstanding — i.e. it MUST NOT
re-dispatch on a failed list — and either retry the list within the
attempt's own window or forfeit that attempt to the next mark (through to
exhaustion, step 4). Never "assume absent".

**Clocks (DECIDED, restated).** The ladder adds NO new outer clock: it
nests inside the existing 120 s readiness window
(`READINESS_TIMEOUT_SECONDS = 120`, `simulator_voice.py:31`) and ends
that wait early — at the +60 s wall-clock mark — on exhaustion. An
unacknowledged dispatch therefore fails typed at ≤60 s (wall-clock from
the first `create_dispatch` return, per the budget definition above) and
never reaches the readiness timeout. The readiness timeout /
`WorldUnavailable` backstop (§4.6) remains the backstop ONLY for calls
that ACKED but never became ready. `run_seconds`
(`call_runner.py:705-711`) already contains the full readiness window,
so the ladder consumes no budget the call did not already have — the
outer `wait_for` math is unchanged (`call_runner.py:99` pad included).

### 4.4 Non-conformant implementations (summary of MUST NOTs)

- Treating `create_dispatch` success, worker-local availability, or a
  bare dispatch listing as delivery.
- Re-dispatching without listing the room's dispatches first.
- Guarding retries on participant presence.
- Running the ladder sequentially BEFORE the readiness wait instead of
  nested inside it (§4.3 placement), or touching `run_seconds`/its pad.
- Unbounded retries; extending a forfeited attempt past its fixed window
  mark; measuring windows per-attempt instead of wall-clock (§4.3).
- Dropping an unacknowledged dispatch without a typed failure.
- Creating a second dispatch while one for the same (room, agent name)
  is listed; issuing a new create while a prior attempt's create is
  in-flight without first reconciling it (cancel / confirm-absent /
  possibly-outstanding-so-skip, §4.3 forfeit rule).
- Deleting or re-creating a dispatch AFTER the join is observed or the
  readiness wait completes — the ack task MUST stop evaluating marks on
  EITHER event. A late delete would remove a dispatch that was already
  delivered (the join-race, AZAIN TO CONFIRM §8).
- Raising the exhaustion failure as an `asyncio.TimeoutError` (or letting
  the readiness wait time out into one), which surfaces as
  `AGENT_UNAVAILABLE`/world retirement instead of the typed code (§4.3
  step 4, §4.6).
- Surfacing the exhaustion code by string-matching `case.failure.message`
  instead of reading the structured marker (§4.5).

**KNOWN RESIDUAL (frozen).** The delete-a-delivered-dispatch race
(the §4.4 bullet, AZAIN §8 item 10) is fully closed ONLY if the join
signal is an event/push edge the ack task can stop on. If EU LiveKit
offers only POLLING for the agent's join, a mark can fire a delete in the
poll interval between a real join and its next observation. The contract
MANDATES stopping mark evaluation on the join edge and PREFERS an
event/push signal (§8 item 10), which shrinks the window to the signal's
own latency, but cannot eliminate a pure-poll residual by text alone — the
closing fact is operational (which signal EU exposes), gated on Azain's
confirmation, not contractual. Bounded (one poll interval), diagnosed, and
further bounded by the delete/join server semantics (§8 item 5). Recorded
frozen for that reason.

### 4.5 Implementation locus and seams

- **Locus (DECIDED)**: the ack ladder lives INSIDE the LiveKit engine
  (`engines/livekit.py`), adjacent to its dispatch-creation site
  (`:1064-1077`), as the concurrent ack task nested inside the engine's
  readiness wait (§4.3) — the wire-level room name, the dispatch id, the
  room join events, and the readiness wait itself are all local to the
  engine, and none is visible to `call_runner.py`. This supersedes the
  v0.1 split (and refines the plan's "implemented in `call_runner.py`" —
  see the plan deviations note in the header).
- **Cross-lane gate (DECIDED — round-3 finding 4)**: `engines/livekit.py`
  is SHARED by the local lane and the hosted lane. The ack ladder MUST be
  gated on a **hosted-harness-only opt-in** — present only on the hosted
  path (the world / parallelism runtime context the hosted provisioner
  threads through to the engine), ABSENT on the local lane. When the
  opt-in is absent — every local-lane managed external-room simulation —
  the engine's readiness path is byte-unchanged: no ladder, no
  re-dispatch, no new failure code. The opt-in is the sole activation
  condition. Single writer: Track B, with Azain.
- **This is a scoped Track B change spanning engine + CallRunner +
  scheduler (REVISES v0.3 — round-3 finding 1)**. v0.3 claimed
  "call_runner-only, scheduler unchanged"; that was WRONG. The scheduler's
  `except CallAborted` catch (`hosted_scheduler.py:1885-1894`) hardcodes
  `_failure("call_failed", str(exc))` for EVERY `CallAborted` — CallRunner
  cannot influence the receipt code from its side, so surfacing a DISTINCT
  code REQUIRES a scheduler change. The corrected chain, each cite
  re-verified 2026-08-31:
  1. **Engine (ack task).** On +60 s exhaustion the ladder raises its OWN
     typed exception (NOT an `asyncio.TimeoutError`, §4.3 step 4 / §4.6),
     carrying `voice_dispatch_unacknowledged` in a STRUCTURED field, never
     message text. The engine's failure handler maps it to a failure
     outcome whose structured marker (a distinct `failure.code`, never a
     substring of `failure.message`) identifies ack-exhaustion. This
     deliberately BYPASSES the readiness-stage `asyncio.TimeoutError`
     handler (`engines/livekit.py:1409-1435`) that would map to
     `TestCaseStatus.AGENT_UNAVAILABLE`.
  2. **CallRunner (one NARROW change, and only this one).** No LADDER code
     lives in `call_runner.py`; its single permitted change reads that
     structured marker and raises `CallAborted` carrying the marker as a
     STRUCTURED attribute (change site `call_runner.py:928-933`, today's
     unconditional `voice_call_not_completed` `CallAborted` wrap) — never
     string-matched from `case.failure.message`. This amends v0.2's "no
     ack code in call_runner" and v0.3's "passthrough mapping clause".
  3. **Scheduler (NEW — the change v0.3 denied).** The `except CallAborted`
     catch (`hosted_scheduler.py:1885-1894`) gains a **code-selection
     branch**: read the structured marker on the `CallAborted`; if it marks
     ack-exhaustion → `_failure("voice_dispatch_unacknowledged", …)`, else
     the byte-unchanged `_failure("call_failed", …)`.
  4. **Domain + retry.** `voice_dispatch_unacknowledged` is ADDED to
     `_CODE_DOMAIN` (`hosted_scheduler.py:346-361`, beside the `call_failed`
     row at `:358`) as domain **INFRASTRUCTURE**, so `_is_retryable`
     (`:443-447`) retries it ONCE on a fresh world — identical to
     `call_failed`. Retry-once-on-fresh-world is UNCHANGED, and the retry
     is DELIBERATE: post-spike retries have genuine success probability
     (run-178's post-recovery deliveries — the worker kept receiving later
     rooms for seconds after the spike, §3); worst-case ack spend is
     2×60 s per scenario (one ladder per attempt-world), bounded, accepted.
  5. **Fallback hardening (MUST — round-3 finding 1c).** `_failure`
     (`hosted_scheduler.py:462-464`) and `_is_retryable` (`:443-447`) both
     index `_CODE_DOMAIN[code]` DIRECTLY and would `KeyError` on any
     unmapped code — and this runs INSIDE the `CallAborted` exception
     handler, where a `KeyError` would mask the real failure. Both MUST
     gain a graceful fallback for an unknown code: **default domain
     INFRASTRUCTURE + retryable**. This makes the new code safe even if the
     map-add and the catch-branch land out of order across a deploy, and
     hardens the seam against every future code — not just this one.
- **SIBLING AMENDMENT REQUIRED** (`world-handle-interface.md`,
  errored-receipt table :86-99, `call_failed` row **:97**): add a
  `voice_dispatch_unacknowledged` row — domain `infrastructure`, retried
  once on another world — beside `call_failed` and `driver_crashed`. That
  closed table is the home for every receipt code the scheduler emits; it
  gained `call_failed` (v3.3) and `driver_crashed` (v3.4) by this same
  amendment, so the shape is established.
- **SIBLING AMENDMENT REQUIRED** (`parallelism-plan-v10.md` §4
  single-writer table): (a) `engines/livekit.py` is ABSENT from the table
  and MUST be listed under **Track B (with Azain)** — the ladder lives
  there; (b) the `hosted_scheduler.py` row reads "assert-only", but C3's
  Track B change is NOT assert-only: it adds the `except CallAborted`
  code-selection branch, the `_CODE_DOMAIN` row, and the
  `_failure`/`_is_retryable` fallback. The row MUST record this C3
  scheduler change (C2's "no behavioral scheduler change" is about
  WorldPool provisioning — a disjoint concern from the receipt-code catch).
- The engine's existing typed failures (`livekit_dispatch_timeout`,
  `livekit_dispatch_failed`, both PREPARING-stage) stay as-is for the
  INITIAL, pre-ladder `create_dispatch` only; a create failure on ladder
  attempts 2-3 counts against the ack budget and converts to
  `voice_dispatch_unacknowledged` on exhaustion (§4.3 step 4). Ack wraps
  the delivery question; the initial creation question keeps its shapes.
- The ack layer has the wire-level room name locally (§1) and needs a
  server API client with dispatch list/delete grants. **AZAIN TO CONFIRM**: the
  harness's LiveKit API key carries `ListAgentDispatch` /
  `DeleteAgentDispatch` permissions on EU, and the deployed server version
  supports both.
- **AZAIN TO CONFIRM (server semantics the retry key depends on)**:
  (a) whether a dispatch created during a zero-eligible-worker window —
  the run-178 dropped class — REMAINS LISTED in `ListAgentDispatch`
  afterwards, or vanishes; the delete-first branch is meaningful only if
  stale dispatches linger. (b) whether `DeleteAgentDispatch` on an
  in-flight dispatch can race a slow join (delivery landing after the
  delete ack), or the server guarantees no delivery after delete.
  (c) whether the observed ~0.4 s server-eligibility lag has a known
  bound on our server version.

### 4.6 Interim backstop (exists today; retained after ack lands)

The engine's readiness-stage timeout with no target joined surfaces as
`TestCaseStatus.AGENT_UNAVAILABLE`, which CallRunner raises as
`WorldUnavailable("target agent never joined the room: ...")`
(`call_runner.py:900-912`) — the scheduler retires the world and retries
the scenario elsewhere (world-handle-interface semantics). This backstop
is typed and loud but slow (a full readiness timeout per occurrence) and
is not a substitute for ack: it cannot distinguish a dropped dispatch from
a dead worker, and at W>1 with system-CPU shedding it can burn every
world's retry budget on the same machine-wide condition. After ack lands,
this backstop covers ONLY calls that acked but never became ready — the
ack ladder nests inside the readiness wait and ends it early at ≤60 s on
exhaustion, so it preempts this backstop by construction (§4.3, 60 < 120).
Crucially the ladder ends the wait by raising its OWN typed exception, NOT
by letting the readiness wait fall into the `asyncio.TimeoutError` path
(`engines/livekit.py:1409-1435`) that maps to `AGENT_UNAVAILABLE` →
`WorldUnavailable` (`call_runner.py:900-912`) → world retirement — the
exact outcome §7 decision 2 forbids. Ack exhaustion and this backstop are
therefore DISJOINT outcomes: exhaustion → `voice_dispatch_unacknowledged`
(scenario errored, structured marker, §4.5); acked-but-never-ready →
`AGENT_UNAVAILABLE`/`WorldUnavailable` (this backstop). The marker that
distinguishes them lives in a STRUCTURED field, never string-matched from
`case.failure.message`.

## 5. Worker knobs at this boundary

Delivery mechanism and producer mandate are C1's (the knobs are
`WorkerOptions` constructor arguments; livekit-agents 1.7.1 has NO env-var
and NO CLI override for any of them — C1 defines harness-set env vars and
the producer contract mandates conformant agents read them into
`WorkerOptions`). C3 pins the VALUES and the boundary consequences.

### 5.1 `load_threshold` → `FI_LOAD_THRESHOLD` → infinity

In-sandbox conformant workers MUST set
`load_threshold = float(FI_LOAD_THRESHOLD)` with the harness setting it to
infinity (`inf`). Rationale (plan §3): the shedding signal is SYSTEM CPU,
identical for all W workers in one sandbox — per-worker thresholds
differentiate nothing, and any spike sheds every world at once; the
admission clamp W′ (C2) is the SOLE intended throttle. Non-conformant
agents keep 0.7 and WILL shed under load — **dispatch-ack (§4) is the
universal net for the resulting drops, independent of agent conformance.**

### 5.2 Worker health port → `FI_WORKER_HEALTH_PORT`

Each world's worker MUST bind its health server to its own allocated
per-world port, read from `FI_WORKER_HEALTH_PORT` (the harness sets that env
to the world's `{{PORT_<name>}}` allocation; existing token mechanism,
`process_runtime.py:397-410`).
A non-conformant agent binds the framework default **8081 in every
world**: at W>1 the second binder dies `[Errno 48]` (that collision class
is log-evidenced in probe run 1788173234 on the probe's own ports;
runs 018/072 are consistent-with but inferred — plan §3). The environment
conformance gate (`process_runtime.py:4578-4593`) is the net: the failure
MUST surface LOUDLY, never a hang. Per C1 v1.3 §4 decision 2 (D28), a
knob-bearing worker squatting the default health port in every world is a
**TERMINAL JOB FAILURE, reason `port_not_consumable`** (NOT a degrade — the
classification C4 v1.3's consistency pass adopts); a knob-bearing failure
with no bind evidence, or a formula-port stale-squat, stays a graceful
`conformance_gate_failed` / `world_start_failed` degrade.

### 5.3 Prewarm → `FI_NUM_IDLE_PROCESSES`

Framework default `num_idle_processes = min(cpu_count, 4)` per worker
(`worker.py:208`) → up to 16 idle processes at W=4 before any call.
Conformant workers MUST read `FI_NUM_IDLE_PROCESSES`. The harness-set
value is a Track D calibration output (measured, not guessed); until
measured, the working default is 1 per world. **AZAIN TO CONFIRM**: an
acceptable prewarm floor from the CallRunner side — whether cold job spawn
at `num_idle_processes=1`-per-world keeps first-call join inside the ack
window, or a higher floor is needed.

### 5.4 Teardown — SIGTERM → SIGKILL escalation (MUST)

Workers drain 3600 s on SIGTERM (§3). World/worker teardown MUST use the
escalating terminate-then-kill path (`_terminate_and_wait`,
`process_runtime.py:1030-1063` — exists; the requirement is that worker
processes go through it, never a bare `terminate()`). Evidence for the
cost of getting this wrong: run-234's total failure was run-178's workers
surviving `terminate()` and squatting the ports (`p0b_results.md`). The
integration teardown test (plan §5) asserts the escalation fires.

## 6. Failure vocabulary at this boundary

| Condition | Where raised | Type / code | Behavior |
|---|---|---|---|
| Voice config missing pre-dial | `call_runner.py:668-672` | `CallAborted` `voice_capability_unavailable` | scenario errored, no dial |
| No/ambiguous dispatch identity (§2.3) | `call_runner.py:674-679` | `CallAborted` `voice_dispatch_identity_unavailable` | scenario errored, no dial |
| Name template lacking `{{WORLD_INDEX}}` or `{{JOB_ID}}` at requested W>1, bundle-env channel (§2.1) | `process_preflight.py` (Track B, ships) | bundle preflight reject `agent_name_not_world_unique` | job fails `validating_environment`, nothing provisioned; reject message points at requesting W=1 |
| Injected-secret name override (§2.2) | spawn-time warning (normative home C2; Track B ships) | `log` warning (key named, values never) + per-call no-join failures | loud, diagnosed by the warning; §2.2's equal-name residual is diagnosed, not prevented |
| INITIAL `create_dispatch` API timeout/error (pre-ladder only, §4.5) | `engines/livekit.py:1078-1106` | `livekit_dispatch_timeout` / `livekit_dispatch_failed` | existing engine shapes, unchanged; ladder-attempt (2-3) create failures instead fold into the ack budget (§4.3 step 4) |
| Dispatch unacknowledged at the +60 s mark (§4.3) | engine ack task raises its OWN typed exception (structured marker, NOT `asyncio.TimeoutError`, `engines/livekit.py`, ships, hosted opt-in only) → CallRunner re-raises as `CallAborted` with the structured marker (`call_runner.py:928-933`) → scheduler `CallAborted` code-selection branch (`hosted_scheduler.py:1885-1894`) picks the receipt code | typed `voice_dispatch_unacknowledged`, domain INFRASTRUCTURE (`_CODE_DOMAIN` :346-361, beside `call_failed` :358; `_is_retryable` :443-447 / `_failure` :462-464 with unknown-code fallback) | scenario errored (DECIDED), never world retirement; surfaces at ≤60 s WALL-CLOCK from the first `create_dispatch` return (§4.3's budget definition), ending the readiness wait early; scheduler retries once on a fresh world like `call_failed` (§4.5) |
| Agent acked but never became ready (backstop, §4.6) | `call_runner.py:900-912` | `WorldUnavailable` | world retired, scenario retried elsewhere |
| Mis-dispatch to a foreign world/job | — | **no detector exists or is possible post-hoc** | prevented ONLY by §1 uniqueness + §2.1 guard; never observed in P0-B (0 foreign across the 73 loaded-run deliveries; the three zero-delivery runs are vacuous) |

## 7. Decisions + frozen residual

1. **Ladder shape and budget — DECIDED (§4.3)**: the ack ladder runs
   CONCURRENT with — nested inside — the engine's 120 s readiness wait
   (`simulator_voice.py:31`), never sequentially before it. Initial
   dispatch, re-creation (delete-stale + re-create) at the +20 s and
   +40 s wall-clock marks, typed exhaustion at the +60 s mark aborting
   the readiness wait. The ≤60 s budget is WALL-CLOCK from the FIRST
   `create_dispatch` return with FIXED window marks; a slow attempt is
   forfeited, never extended. Exhaustion preempts the readiness timeout
   by construction (60 < 120 — the ladder ends the very wait it nests
   in); `run_seconds` and its 60 s pad are untouched
   (`call_runner.py:99, 705-711` unchanged). Sanity of the 20 s
   windows / 2 re-creations on EU LiveKit: AZAIN TO CONFIRM (§8).
2. **Classification of ack exhaustion — DECIDED (§4.3/§4.5)**:
   scenario-level call failure (errored), NEVER world retirement.
   run-178's cause is machine-wide system CPU: retiring one world
   neither fixes nor avoids it, misattributes the condition, and can
   cascade retirements. Surfaced through §4.5's engine→CallRunner→scheduler
   chain — the ladder's OWN typed exception (structured marker, NEVER an
   `asyncio.TimeoutError` and so NEVER the readiness-stage
   `AGENT_UNAVAILABLE`/`WorldUnavailable` path) → CallRunner's
   structured-marker re-raise → the scheduler's `CallAborted`
   code-selection branch → receipt code `voice_dispatch_unacknowledged`,
   domain INFRASTRUCTURE, retried once on a fresh world like `call_failed`
   — deliberate and bounded (§4.5). `WorldUnavailable` remains only the
   §4.6 backstop's shape (acked-but-never-ready).
3. **Stale-dispatch handling on retry — KNOWN RESIDUAL (frozen)** (§4.3
   step 1): delete-first (deterministic, but see the AZAIN §8-item-5
   delete/join race) vs wait-out to the next mark (race-free; bounded —
   the fixed +60 s exhaustion mark caps what waiting can eat). BOTH
   options are already bounded and safe under the frozen ladder, and
   both uphold the at-most-one-outstanding invariant (§4.3 step 2 + the
   forfeit rule); the tie-break is an IMPLEMENTATION choice settled at
   build time by the §8-item-4 lingering-semantics answer (if dropped
   dispatches vanish from the list, delete-first is a no-op and wait-out
   wins; if they linger, delete-first is meaningful). Frozen as a bounded
   implementation choice, not reopened as a contract decision. Precedence
   vs the readiness clock is not part of this (resolved in §4.3).

## 8. AZAIN TO CONFIRM (collected)

1. §4.2 — the concrete end-to-end join signal the engine should consume
   on EU LiveKit (participant event vs poll vs dispatch job-state field),
   and the exact participant-identity convention the dispatched agent
   joins under (what the name-match fallback keys on).
2. §4.3 — whether the pinned ack budget (fixed +20/+40/+60 s wall-clock
   marks, 2 re-creations, ≤60 s total) is sane on EU LiveKit.
3. §4.5 — API key grants for `ListAgentDispatch`/`DeleteAgentDispatch`
   on EU, and server-version support for both.
4. §4.5(a) — do dropped dispatches (run-178 class) remain listed in
   `ListAgentDispatch` afterwards, or vanish?
5. §4.5(b) — `DeleteAgentDispatch` vs in-flight slow join: can delivery
   land after a delete ack?
6. §4.5(c) — known bound on the server-eligibility lag (~0.4 s observed)?
7. §5.3 — acceptable prewarm floor (`FI_NUM_IDLE_PROCESSES`) for
   first-call join latency vs the 20 s ack window.
8. LiveKit-EU room-count/quota headroom at W=4 (three services × 4
   concurrent calls — plan §6/§7 assigns this bound to Azain; goes in the
   pre-ladder quota gate).
9. §4.3/§4.5 — does `DeleteRoom` cancel a still-pending dispatch
   server-side? Engine cleanup deletes the managed room
   (`engines/livekit.py:1392`, `:1581`); an abort or teardown mid-ladder
   leaves one dispatch outstanding — if room deletion does not cancel
   it, what residual server-side state (if any) needs explicit
   `DeleteAgentDispatch` cleanup?
10. §4.3/§4.4 — join-signal shape and the delete-a-delivered-dispatch
    race. If the join signal is POLLING (item 1's poll option), a mark
    can evaluate un-acked while the agent joined mid-interval, so the
    mark's delete would remove an ALREADY-DELIVERED dispatch. An
    event/push participant-joined signal closes this (the ack task stops
    on the join edge); a poll does not. Confirm an event/push join signal
    is available on EU LiveKit and PREFER it over polling; if only
    polling is available, confirm the acceptable poll interval that keeps
    this race negligible.
11. §4.3 — LiveKit API rate/throttle limits under W SYNCHRONIZED ladders.
    After a machine-wide CPU spike every world crosses the +20 s / +40 s
    marks together, so the EU server can see up to W simultaneous bursts
    of `ListAgentDispatch` + `DeleteAgentDispatch` + `create_dispatch`
    against the shared server. Confirm the server tolerates W synchronized
    ladders without throttling — throttling would itself manifest as
    further un-acks and could synchronize a retry storm.

## 9. Conformance checklist

Affinity & preconditions:

- [ ] Every dispatch targets the world's `runtime.metadata["livekit_agent_name"]` verbatim; no other affinity mechanism present.
- [ ] `agent_name_not_world_unique` bundle preflight guard present in `process_preflight.py` and rejecting, at requested W>1, any `LIVEKIT_AGENT_NAME` template lacking `{{WORLD_INDEX}}` or `{{JOB_ID}}`, before provisioning (tests: hand-authored static-name bundle at W=2 → bundle preflight reject, zero worlds built; template with `{{WORLD_INDEX}}` but no `{{JOB_ID}}` → same reject; reject message tells the user to request W=1 explicitly).
- [ ] Spawn-time warning (C2's, consumed here) fires when an injected value differs from the rendered one for any of `LIVEKIT_AGENT_NAME` / `FI_LOAD_THRESHOLD` / `FI_NUM_IDLE_PROCESSES` / `FI_WORKER_HEALTH_PORT`; warning names the key, never a value.
- [ ] W>1 unreachable on any deployed snapshot lacking the guard: C4's digest + `HARNESS_PARALLELISM_ENABLED` check enforced by the platform serializer at submit-time admission on both the FE and direct API paths (C4 §5 / pin (ii) — the §2.1 dependency is SATISFIED; C4 accepts a dockerfile-mode dev carve-out — flag-only — as dev-only).

Dispatch-ack:

- [ ] Ack signal is end-to-end join; `create_dispatch` success, worker-local availability, and bare listings are not treated as delivery.
- [ ] Before any re-dispatch, the room's dispatches are listed; a listed dispatch for the same agent name blocks creation (delete-first or wait per §7 frozen residual 3).
- [ ] Invariant holds under test: never two outstanding dispatches for one (room, agent name) — including the slow-join race (delay the worker's job spawn past the ack window; assert single delivery) AND the in-flight-create race (a prior attempt's create still in-flight at the next mark → reconcile-or-forfeit, never a blind second create, §4.3 forfeit rule).
- [ ] The ack task STOPS evaluating marks once the join is observed or the readiness wait completes; no delete/re-create fires after either edge (§4.4).
- [ ] No participant-presence check anywhere in the retry-admission path.
- [ ] The ladder runs CONCURRENT with (nested inside) the readiness wait, marks at +20/+40/+60 s WALL-CLOCK from the first `create_dispatch` return; a slow attempt is forfeited at its mark, never extended; exhaustion at +60 s aborts the readiness wait and raises `voice_dispatch_unacknowledged` from the engine with room, agent name, attempts, window marks; nothing is silently dropped.
- [ ] The exhaustion failure is the ladder's OWN typed exception, NOT an `asyncio.TimeoutError` — it bypasses `engines/livekit.py:1409-1435` and never surfaces `AGENT_UNAVAILABLE`/`WorldUnavailable`; the ack-exhaustion marker travels in a STRUCTURED field, never string-matched from `case.failure.message`.
- [ ] The ack ladder is gated on the hosted-harness opt-in; the local lane's engine readiness path is byte-unchanged (no ladder, no re-dispatch, no new code).
- [ ] Ack exhaustion classifies scenario-errored (never world retirement) and surfaces before the 120 s readiness timeout (nesting makes this structural); `WorldUnavailable` fires only for acked-but-never-ready calls.
- [ ] No LADDER code in `call_runner.py`; its single change is the structured-marker re-raise (`call_runner.py:928-933`), the marker read from a STRUCTURED field/attribute (never `case.failure.message`).
- [ ] Scheduler `except CallAborted` catch (`hosted_scheduler.py:1885-1894`) selects receipt code `voice_dispatch_unacknowledged` on the marker, `call_failed` otherwise; `voice_dispatch_unacknowledged` is in `_CODE_DOMAIN` (`:346-361`, beside `call_failed` `:358`) as INFRASTRUCTURE and retries once on a fresh world.
- [ ] `_failure` (`:462-464`) and `_is_retryable` (`:443-447`) fall back to INFRASTRUCTURE + retryable for any unmapped code — no `KeyError` inside the `CallAborted` handler.
- [ ] SIBLING AMENDMENTS land: `world-handle-interface.md` errored-receipt table (:97) gains the `voice_dispatch_unacknowledged` row (domain infrastructure, retried once); `parallelism-plan-v10.md` §4 single-writer table lists `engines/livekit.py` under Track B and records `hosted_scheduler.py` as no-longer-assert-only for C3.
- [ ] A `create_dispatch` failure on ladder attempts 2-3 spends that attempt and converts to `voice_dispatch_unacknowledged` on exhaustion; only the initial pre-ladder create failure surfaces as `livekit_dispatch_failed`/`livekit_dispatch_timeout`.
- [ ] Failed `ListAgentDispatch` never leads to a re-dispatch.
- [ ] `run_seconds` and its 60 s pad are byte-identical to HEAD (`call_runner.py:99, 705-711`); the outer `wait_for` never fires on an ack-exhausted call.
- [ ] Run-178 replay (ladder): dispatches created inside a forced unavailability window are recovered by retry or surface as the typed failure — never absent from results.

Worker knobs & teardown:

- [ ] Conformant reference agent reads `FI_LOAD_THRESHOLD` (set to inf in-sandbox) and `FI_NUM_IDLE_PROCESSES` into `WorkerOptions`; health port bound from its `FI_WORKER_HEALTH_PORT` env (the harness sets it to the world's `{{PORT_<name>}}` allocation).
- [ ] Non-conformant health-port collision surfaces as a loud degrade via the conformance gate, never a hang.
- [ ] Worker teardown goes through SIGTERM→SIGKILL escalation; planted-failure test shows no port squatting (run-234 class).

## 10. Versioning

Versions independently of the spine. Amendments append to the changelog;
ambiguities become amendments, never guesses.

**Changelog**
- Consistency-pass reconciliation (post-freeze, 2026-08-31 — NOT a review
  round, no version bump): §5.2 names `FI_WORKER_HEALTH_PORT` verbatim
  (was described as "the `{{PORT_<name>}}` env"; per C1's naming request),
  and the §9 worker-knob checklist row matches; §5.2's health-port-collision
  surfacing aligned to C1 v1.3 §4 decision 2 / D28 (a knob-bearing default-
  port squat is a TERMINAL `port_not_consumable` job failure, not a degrade);
  the `bundle-producer-contract.md` §2.7 SIBLING AMENDMENT (both-placeholder
  agent name) marked APPLIED; sibling version pins swept to the frozen
  siblings (C2 v1.3, C1 v1.3 in the §2.2 SATISFIED notes). No mechanism
  change — naming, classification alignment, and pins only.
- v0.4 (2026-08-31): round-3 review fixes (10 findings applied) — FINAL
  round; the contract is now **FROZEN** (no round 4). BLOCKER (finding 1):
  the typed surfacing needs SCHEDULER changes — v0.3's "call_runner-only,
  scheduler unchanged" claim was WRONG (the `except CallAborted` catch at
  `hosted_scheduler.py:1885-1894` hardcodes `call_failed` for every
  `CallAborted`). REVISED to a scoped Track B change spanning
  engine + CallRunner + scheduler: the engine raises the ladder's OWN typed
  exception with `voice_dispatch_unacknowledged` in a STRUCTURED field →
  CallRunner re-raises as `CallAborted` carrying the structured marker
  (`call_runner.py:928-933`) → the scheduler catch gains a code-selection
  branch reading the marker → receipt code `voice_dispatch_unacknowledged`;
  `_CODE_DOMAIN` (:346-361, beside `call_failed` :358) gains the code as
  INFRASTRUCTURE, and `_failure`/`_is_retryable` (:462-464 / :443-447) are
  HARDENED with an unknown-code fallback (default INFRASTRUCTURE +
  retryable) so no unmapped code KeyErrors inside the handler. §4.5/§6/§9
  rewritten; "scheduler behavior unchanged" removed; retry-once-on-fresh-
  world unchanged. New SIBLING AMENDMENT REQUIRED: `world-handle-interface.md`
  errored-receipt table (:97) gains a `voice_dispatch_unacknowledged` row.
  MAJOR (finding 2): the exhaustion abort must NOT surface as a
  readiness-stage `asyncio.TimeoutError` (→ `AGENT_UNAVAILABLE` →
  `WorldUnavailable` → world retirement, `engines/livekit.py:1409-1435`,
  `call_runner.py:900-912`); the ladder raises its own typed exception, the
  marker read from a STRUCTURED field never `case.failure.message` —
  pinned in §4.3 step 4 / §4.5 / §4.6. MAJOR (finding 3): forfeit rule
  rewritten to close the in-flight-create race — before a new create the
  ladder reconciles any in-flight create (cancel / confirm-absent /
  possibly-outstanding → no new dispatch); at-most-one-outstanding wins
  over the fixed-mark cadence. MAJOR (finding 4): the ack ladder is gated
  on a hosted-harness-only opt-in so local-lane managed external-room
  simulations are unaffected (§4.5); new SIBLING AMENDMENT REQUIRED — the
  plan's §4 single-writer table must list `engines/livekit.py` under
  Track B (and record `hosted_scheduler.py` as no-longer-assert-only for
  C3). MINOR (finding 5): §4.4 MUST NOT gains "no delete/re-create after
  the join is observed or the readiness wait completes"; new AZAIN item on
  the polling-join delete-a-delivered-dispatch race (prefer event/push).
  MINOR (finding 6): the C2 spawn-warning sibling note marked SATISFIED
  (C2 v1.2 §4a legislates the warning, names all four keys, home correct;
  the c2:220 inversion is gone). MINOR (finding 7): the C4 dependency
  referenced by SECTION (C4 §5 / pin (ii): submit-time serializer
  enforcement of flag AND digest), not by a "D12" identifier, and marked
  present-and-satisfied. MINOR (finding 8): §2.1 present-tense overclaim
  fixed — `harness_provider.py:325-336` today only ECHOES
  effective_parallelism + snapshot name/digest; the digest comparison,
  allowlist, and `parallelism_enabled` field are Track E work ("Track E
  adds"). MINOR (finding 9): new AZAIN item on LiveKit API rate/throttle
  limits under W synchronized ladders. NIT (finding 10): cites fixed —
  code→domain table is `_CODE_DOMAIN` at :346-361 (call_failed :358), NOT
  :338-339 (a comment); CallAborted catch :1885-1894 (not :1885-1891); C1
  reference bumped v1.1→v1.2.
- v0.3 (2026-08-31): round-2 review fixes (10 findings applied). MAJOR:
  ladder placement DECIDED — the ack ladder runs CONCURRENT with (nested
  inside) the engine's readiness wait, never sequentially before it
  (evaluation at the +20/+40 s marks, exhaustion at +60 s aborting the
  wait); "60 < 120 preempts by construction" is now structural, and
  `run_seconds` + its 60 s pad are untouched (`call_runner.py:99,
  705-711`) — §4.3/§4.5/§7.1 rewritten. Budget semantics DECIDED —
  ≤60 s is WALL-CLOCK from the FIRST `create_dispatch` return, fixed
  window marks regardless of per-attempt overhead, slow attempts
  forfeited never extended (§4.3; §6 row cites the definition).
  CallRunner seam DECIDED — one NARROW passthrough mapping clause is the
  single permitted `call_runner.py` change (engine reason starting
  `voice_dispatch_unacknowledged` → same receipt code instead of generic
  `call_failed` text-wrapping, `call_runner.py:928-933`); domain
  INFRASTRUCTURE, scheduler retry UNCHANGED (once on a fresh world like
  `call_failed` — deliberate, bounded at 2×60 s/scenario); full
  engine→CallRunner→scheduler chain cited (`hosted_scheduler.py:338-339,
  443-448, 1885-1891`); "no ack code in call_runner" amended to "no
  LADDER code". MINOR: p0b inference caveat carried into §3/§4.2 ("all
  create_dispatch calls succeeded" is inferred from the driver not
  aborting; only round-0 IDs printed); C4 sibling note narrowed to the
  true gap (continuous-check text exists; §5 serializer clamp was
  flag-only — C4 concurrently pinning flag AND digest at submit,
  decision D12; verify at C4's next round); `{{JOB_ID}}` guard widening
  recorded as a plan deviation (plan §1/§7 are WORLD_INDEX-only and call
  platform bundles "already safe"; WORLD_INDEX-only names now FAIL
  bundle preflight at W>1); AZAIN item 9 added (DeleteRoom vs pending
  dispatch, `engines/livekit.py:1392, 1581`); ladder-attempt create
  failures pinned (count against the ack budget, convert on exhaustion;
  only the initial create keeps the existing dispatch-failure shapes).
  NIT: stale "FI_* trio" parenthetical fixed (C1 §4 now requires all
  four keys verbatim) + c2:220 warning-attribution inversion folded into
  the C2 sibling note; "(retryable)" dropped from
  `livekit_dispatch_timeout` in §6 (the engine flag is not consumed by
  call_runner's mapping); "bundle preflight" vs "platform readiness
  endpoint" disambiguated throughout (§2.1 terminology note).
- v0.2 (2026-08-31): round-1 review fixes (12 findings applied). BLOCKER:
  §2.1 guard now requires BOTH `{{WORLD_INDEX}}` AND `{{JOB_ID}}`
  (cross-job collision on the shared EU server; ack passes on a
  same-named join, prevention-only) + accepted requested-W false
  positive + W=1 cross-job scope note. MAJOR: ack ladder locus DECIDED
  into `engines/livekit.py` (CallRunner receives the typed failure only;
  plan locus refinement recorded); clocks DECIDED (N=2, 20 s/attempt,
  ≤60 s total, preempts the 120 s readiness timeout; exhaustion =
  scenario errored, never world retirement) — resolves former OPEN
  DECISIONS 1 and 2. MINOR: §2.2 equal-name residual contamination
  stated (overclaim corrected); precondition enforcement restated as
  C4's submit-time admission digest+flag check; spawn-time warning's
  normative home moved to C2 (four-key set); merge order corrected to
  the four-layer truth (`process_runtime.py:1591-1594`, claim scoped to
  `LIVEKIT_AGENT_NAME`); name-pattern description corrected
  (multi-component literal `:646-649` vs single-component `root.name`
  derivation `:749-751`); plan-deviation notes recorded (prevention +
  no-join vs "typed failure on mis-dispatch"); ack attribution rule
  DECIDED (dispatch identity first, name-match fallback sound only under
  the §2.1 guard); no-foreign evidence base restated (73 loaded-run
  deliveries; zero-delivery runs vacuous). Three SIBLING AMENDMENT
  REQUIRED notes added (bundle-producer-contract §2.7 example, C4 §4
  check locus, C2 warning key set). Open: §7's decision 3, §8's eight
  confirmations.
- v0.1 (2026-08-31): initial draft (Khushal + Claude, Track B) for Azain's
  co-author review. All code anchors re-verified at ALK HEAD; framework
  facts per the 1.7.1 wheel verification in p0b_results.md.
