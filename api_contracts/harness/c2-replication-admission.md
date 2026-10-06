# C2 — Replication & Admission — v1.3 (FROZEN)

**Boundary**: scheduler (`hosted_scheduler.py` WorldPool / `hosted_entrypoint.py`)
↔ in-sandbox provisioner (`ProcessRuntimeProvider._provision_sync`,
`process_runtime.py` — Track B, single writer). Also touches `job.py`
(guest admission validation, Track B).

**Status**: v1.3 FROZEN, 2026-08-31 (post review round 3 — this is the FINAL
round; the contract FREEZES with this version, no round 4). Any further
change is a new-version reopening, not a review round. Cross-contract
reconciliations this version dispatches against the frozen siblings —
chiefly the `port_not_consumable` terminal-not-degrade reclassification
(adopting C1 v1.3 §4 decision 2) — are PENDING the post-freeze
cross-contract consistency pass (enumerated in §6 and the changelog). This
version adopts C1 v1.3's and C4 v1.3's frozen decisions. Implements the
decisions of `parallelism-plan-v10.md` (§1 mechanism paragraphs, §2 anchors,
§3 P0-C C2 row, §6 admission arithmetic, §7 partial-failure +
literal-endpoint rows). This document does not re-decide anything the plan
decided; where the plan left a choice open it is marked **OPEN DECISION**.
Every `file:line` below was re-verified 2026-08-31 against
`~/Desktop/agent-learning-kit` (`src/fi/alk/harness/`) and
`~/Desktop/future-agi` (`futureagi/simulate/`) working trees.

**Companion contracts**: C1 (world port model — v1.3 FROZEN — owns the
allocation rule, the effective-W=1 declared-port carve-out, the
consumability split, and the `port_not_consumable` attribution mapping AND
its terminal-not-degrade classification, decision 2), C3 (call affinity —
v0.4 FROZEN — dispatch-ack, name-uniqueness preflight), C4 (degrade &
surfacing — v1.3 FROZEN — owns the reason vocabulary table, its transport
end-to-end, and deployment order; C2 references reasons, it does not
re-legislate them). All three siblings are frozen; C2's residual
reconciliations against them are dispatched to the post-freeze consistency
pass, never applied here.

**Plan deviations (recorded deliberately)** — mirror of C3's block:

- The plan states "world 0 failing = JOB FAILURE" unqualified (§2 C2 row,
  §7 partial-failure row). C2 SCOPES it to the FIRST `provision()` call
  (`pool.start()`): on reconcile calls the raise is caught by WorldPool
  and the job continues on healthy worlds (§5 rules 1/2R). Deviation from
  the row's letter, not its intent — the job never dies mid-run to a
  condition the scheduler's sick-world machinery already owns.
- The plan's admission arithmetic reads the fit inputs from the job's own
  declaration (`job.json` `cpu_units`/`memory_mb` — plan §6, P0-C C2
  row). C2 reads RUNTIME-OBSERVED sandbox resources instead (§2):
  the snapshot lane launches with NO `Resources`
  (`hosted_harness_gateway.py:931-935`), so the declaration is not the
  sandbox's actual size in production. The declared values keep their
  platform pre-check and guest-gate jobs unchanged.

---

## 0. Glossary

| term | meaning |
|---|---|
| requested W | `job.runtime.parallelism`, raw and never clamped guest-side (`hosted_entrypoint.py:134-143`); passed to the `WorldPool(instances=parallelism)` constructor (`hosted_entrypoint.py:1830-1836` — the `:1836` cite) and thence to the provisioner as `provision(instances=...)` — `hosted_scheduler.py:819`/`:824` on `start()`, `:1114`/`:1119` on reconcile |
| W′ / admitted W | requested W after the §2 admission clamp |
| effective W / E | the world count actually provisioned and returned — requested W after EVERY §1 stage; written `E` in §3's invariant and §7's partial-failure rows (never `W′`, which is stage 1's output only) |
| attempt ceiling | an explicit provider-instance integer: the latched upper bound on effective W for this attempt, monotonically non-increasing across `provision()` calls, mirrored into `build.json`'s effective field on every write (§1 rule 3) |
| degrade ledger | `build.json`'s `degrade_events` list (shape owned by C4 §2: entries `{reason, from_w, to_w}`) recording ceiling reductions; C2 owns the writer side (§6), the Track C′ emitter (`hosted_entrypoint.py`) turns it into `parallelism_degraded` events once |
| cpu_fit / mem_fit | the resource-fit terms of the admission math, computed from RUNTIME-OBSERVED sandbox resources (§2) |

---

## 1. The replicate-vs-degrade decision pipeline

Replication is decided in ONE place — `_provision_sync`
(`process_runtime.py:4440-4637`) — as an ordered pipeline. Each stage takes
the ceiling the previous stage left and MAY lower it, never raise it:

| # | stage | locus (today / target) | may lower ceiling to | ledger reason | reason owner |
|---|---|---|---|---|---|
| 0 | platform + guest validation (context, not a stage C2 adds) | serializer 1..8 + voice `W ≤ cpu_units` reject (`harness_job.py:113, 205-211`); guest `W ≤ cpu_units` reject (`job.py:180-181`); preflight 1..8 (`hosted_entrypoint.py:1801-1809`) | — (reject, never degrade) | — | C4 / this §3 |
| 1 | **admission clamp** (NEW, Track B) | top of `_provision_sync`, BEFORE `plan_ports` (`:4450`) | W′ = max(1, min(W, cpu_fit, mem_fit)) — fit terms from RUNTIME-OBSERVED sandbox resources (§2) | `resource_limited` — **this contract is the anchor for that emitter** | C2 (§2) |
| 2 | **pre-plan secret scan** (NEW, Track B) | after secret resolution, still BEFORE `plan_ports` (`:4450`) and before any world builds | 1 (degrade tier only; the warn tier never lowers the ceiling, §4) | `literal_local_endpoint` | C2 (§4) |
| 3 | port plan | `plan_ports` (`process_runtime.py:233-247`) | 1 (code-fixed `fixed_port` only, post-C1) | `fixed_port` | C1 |
| 4 | conformance gate | guard `:4578`, gate execution `:4581-4593` (genuine-gate-failure latch `:4594-4599`) — the two `_ensure_world` calls at `:4579-4580` are stage-5 catch sites, not this stage | 1 (genuine gate failure); the LISTENER check does NOT lower the ceiling | `conformance_gate_failed` (the canary return — which itself can never carry `port_not_consumable`). SEPARATELY the gate-stage declared-port LISTENER check (C1 v1.3 §4) is **NOT a ledger/degrade producer**: a listener found on any declared `fixed_port` → **TERMINAL JOB FAILURE, reason `port_not_consumable`** (§5 rule 3, §8, §11), raised out-of-band — never a ledger append or ceiling drop | C1 / C2 (§8) |
| 5 | **world builds — partial-failure catch** (NEW, Track B; armed on the first-call/digest-mismatch branch — FIRST BUILD + DIGEST REBUILD, NOT reconcile, §1 rule 4 / §5) | BOTH `_ensure_world` call sites (gate branch `:4579-4580`, main loop `:4613-4614`) | k (the contiguous built prefix), k ≥ 1 — OR no ceiling change: a `port_not_consumable` hit is a TERMINAL JOB FAILURE, not a degrade | `world_start_failed` (ledger, drop to k). At the gate-branch site a world-≥1 bind death whose bind evidence names a DECLARED port by a consumable-declared process → **TERMINAL JOB FAILURE, reason `port_not_consumable`** (C1 v1.3 §4 bind-evidence, formula-port exclusion; NOT a ledger append); fallback `conformance_gate_failed` | C2 (§5, §8) |

Rules (normative):

1. **Ordering is fixed.** Stage 1 before stage 2 before `plan_ports`. In
   particular the secret scan MUST complete before `plan_ports` is called —
   this is the plan §1 round-9 mechanism, and §4 states why any later timing
   is non-conformant.
2. **A stage runs only while the incoming ceiling exceeds its floor.** The
   scan (stage 2) is SKIPPED when the ceiling entering it is already 1 —
   literal declared-port references are legal at effective W=1 (C1's
   carve-out), and a second ledger entry at the same effective value would
   violate C4's strictly-decreasing rule. `plan_ports` and the gate already
   behave this way (`:246` requires `instances > 1`; `:4578` requires
   `effective > 1`).
3. **The ceiling latches per attempt — as explicit provider state.** The
   latch is an **effective-ceiling integer** held on the provider instance:
   initialized by stage 1 to W′, lowered (never raised) by later stages,
   and mirrored into `build.json`'s effective field on every write. The
   name is normative by behavior; the implementation may rename the
   attribute. Stages 1-2 are deterministic per job identity and computed
   once (stage 2 literally runs once — secrets are loaded once and
   latched, `:4463-4473`, `_secrets_loaded`); the genuine gate-failure
   path additionally keeps its existing conformance-False latch
   (`:4594-4599`); stage 5's catches lower the ceiling directly, on the
   first provision call only (§5 rules 2/2R).
   A later reconcile `provision()` call reconciles to the ceiling —
   it MAY rebuild/replace sick worlds below it (unchanged sick-world
   recovery) but MUST NOT exceed it. Rationale: a ceiling increase has no
   representable event — the degrade payload is bounded
   `1 ≤ effective < requested` (`outbound.py:633`, ingestion
   `hosted_harness_ingestion.py:586`) and C4 defines `effective` as strictly
   decreasing per attempt; growth would leave the FE showing a stale
   degrade. (The WorldPool reconcile comment "can grow the pool back up",
   `hosted_scheduler.py:1223-1226`, refers to sick-world recovery within the
   ceiling, which remains legal.)
   **State locus + rebuild survival (normative):** all three —
   the ceiling, the degrade ledger, and the retained scan port-set
   (§4 item 4) — live as provider-instance state. The ceiling and the
   ledger are MIRRORED into `build.json` on every write (`build.json` is
   their durable READ surface, not their home). The retained scan
   port-set is IN-MEMORY provider state ONLY — a set of ports, never
   values — and is NOT written to `build.json` at all (it would leak
   nothing a log warning does not, but it is simply not part of the
   persisted surface). On the same-job-identity **digest rebuild**
   (`process_runtime.py:4536-4541`, where `build_output` is replaced and
   `_conformance_checked` reset — the trichotomy's branch (c), rule 4) the
   provider MUST **copy forward** the prior effective ceiling, the degrade
   ledger, and the in-memory retained scan port-set into the fresh state;
   the ceiling remains monotone non-increasing across the rebuild.
   **Scan-staleness rule (DECIDED):** the scan cannot re-run on a digest
   rebuild (secrets were loaded once and the file deleted, `:4463-4473`),
   so the provider RE-CLASSIFIES the retained port-set against the NEW
   manifest's declared-port set — a retained port that the new manifest
   NEWLY declares → ceiling lowered to 1 pre-plan with
   `literal_local_endpoint` (§6's dedup rules apply; §4 item 4). A NEW
   attempt (a fresh provider instance) legitimately resets all three —
   the invariants are per-attempt.
   **Serialization precondition (stated explicit):** this per-attempt
   provider state (ceiling + ledger + retained port-set) assumes a
   SINGLE WRITER — WorldPool already serializes every `provision()` call
   under `_provider_lock` (`hosted_scheduler.py:818`, `:1113`); no C2
   mechanism adds its own locking, and a caller invoking `provision()`
   outside that lock is out of contract.
4. **Plan-once / carry-forward is PER BUILD IDENTITY, keyed on WHICH
   `_provision_sync` branch runs — a crisp trichotomy.** `plan_ports`
   runs with `instances = ` the post-stage-2 ceiling, never the raw
   requested value (today `:4450` passes the raw argument, on every call):
   - **(a) FIRST BUILD** of a build identity — the initial `pool.start()`
     provision, taking the first-call/digest-mismatch branch
     (`process_runtime.py:4454`, `self._manifest is None`). `plan_ports`
     RUNS; the pre-plan secret scan (stage 2) RUNS; the stage-5 catches
     are ARMED (§5).
   - **(b) RECONCILE** — SAME build identity, a later sick-world-recovery
     `provision()` call taking the `else`/reconcile branch (`:4548-4554`).
     Carry the FIRST build's `port_plan` FORWARD UNCHANGED: the reconcile
     branch that today REPLACES `context.port_plan` on every later call
     MUST instead reuse it (Track B change; the `:4450` re-run is skipped
     on reconcile, or its result discarded — under the discard variant
     C1 §2's companion parenthetical "receives the latched ceiling on
     every call" stays literally true). Stage 2 does NOT re-run (secrets
     loaded once), and the stage-5 catches are NOT armed — a rebuild
     failure re-raises to WorldPool (rule 2R: down-markers + bounded
     retry).
   - **(c) DIGEST REBUILD** — a NEW build identity mid-job (bundle-digest
     change), taking the SAME first-call branch (`:4454`,
     `self._bundle_digest != bundle_digest`), which tears the prior build
     down and constructs a FRESH `SpawnContext` (`:4478`) carrying a
     freshly-computed `port_plan` (`:4450`). Treat it as a FIRST BUILD of
     the NEW identity: `plan_ports` RE-PLANS against the new manifest, the
     pre-plan scan's retained port-set is RE-CLASSIFIED against the new
     declared-port set (§4 item 4), and the stage-5 catches are RE-ARMED —
     BUT the attempt ceiling and degrade ledger carry FORWARD, monotone
     non-increasing (§1 rule 3, `:4536-4541`). **Required ordering
     (pinned):** the digest detection + retained-port-set reclassification
     MUST occur in the rebuild branch BEFORE the new `plan_ports` runs —
     SIBLING-consistent with C1 §2's per-build-identity freeze (freeze
     binds the RECONCILE path; the digest rebuild is a new build of the
     same job identity, legitimately re-planned).

   So "plan runs once / carry forward" is PER BUILD IDENTITY, not per job.
   This kills BOTH previously-ambiguous readings (re-planning at a later,
   lower ceiling on reconcile, and re-planning at raw W) and makes the
   digest-rebuild re-plan explicit, not an exception. Aligned with C1 §2's
   freeze locus — belt and suspenders, both required.
5. **`requested` in every ledger entry and event is the raw requested W** —
   constant across the attempt, never a prior stage's output (C4 §2).
6. At requested W=1 the pipeline is a no-op: nothing is representable as a
   degrade (`hosted_entrypoint.py:1900-1902`; guard `:4570-4576`) and
   nothing needs to be.

---

## 2. Admission math — W′ (the `resource_limited` anchor)

**Formula (normative shape; constants are Track D's, see OD-1):**

```
cpu_obs     = runtime-observed CPU quota (vCPU)    # guest, provision start; cgroup quota, NOT host cores
mem_obs_gib = runtime-observed memory limit (GiB)  # guest, provision start
# per-dimension: observed if the read succeeds and is bounded; else the DECLARED job.json value (fallback rule)
cpu_fit     = floor( (cpu_obs     - R_cpu) / (c_world + c_call) )
mem_fit     = floor( (mem_obs_gib - R_mem) / (m_world + m_call) )
W'          = max(1, min(W_requested, cpu_fit, mem_fit))
```

- **Inputs — DECIDED: RUNTIME-OBSERVED sandbox resources**, read by the
  guest at provision start:
  - `cpu_obs` MUST be the **cgroup CPU QUOTA** binding the sandbox —
    cgroup v2 `cpu.max` (quota/period), or cgroup v1
    `cpu.cfs_quota_us`/`cpu.cfs_period_us`; equivalently
    `sched_getaffinity` where cores are pinned. It MUST NOT be
    `os.cpu_count()` (nor any `nproc`/`/proc/cpuinfo`-equivalent), which
    returns the HOST core count and IGNORES the sandbox quota — reading
    host cores is exactly the over-admit the runtime-observed decision
    exists to prevent (the snapshot lane's box is quota-limited below the
    host).
  - `mem_obs_gib` via the cgroup/proc memory limit (cgroup v2
    `memory.max` / v1 `limit_in_bytes`, `/proc` fallback).
  - The exact read syscalls are Track B's choice; each value MUST be the
    limit actually binding the sandbox. These are lane-independent ground
    truth. Rationale: the gateway sizes the sandbox from job.json ONLY in
    dockerfile mode (`hosted_harness_gateway.py:918-929`); the snapshot
    lane (`:931-935`) launches `CreateSandboxFromSnapshotParams` with NO
    `Resources` — the box's size comes from the snapshot, so declared
    `cpu_units`/`memory_mb` are not the sandbox's actual size in
    production. The reads are guest-local at provision start — no new
    gateway plumbing.
- **Read-failure fallback — DECIDED (so "admission never raises" holds):**
  PER DIMENSION, if the observed read FAILS, returns `None`, or is
  UNBOUNDED (cgroup v2 `cpu.max`/`memory.max` == `max`), FALL BACK to that
  dimension's DECLARED `job.json` value (`cpu_units` for cpu,
  `memory_mb`→GiB for memory) as a safe conservative bound — the guest
  gate already rejects `W > cpu_units` (`job.py:180-181`), so the declared
  cap can never admit past the box. The fallback is per-dimension: a failed
  cpu read with a good memory read uses declared cpu + observed memory.
  `W' = max(1, min(W_requested, cpu_fit, mem_fit))` with each fit term
  computed from its observed-or-declared value. Admission still NEVER
  raises (§7 read-failure row).
- **Dockerfile-lane note (explanatory only, non-normative):** in
  dockerfile mode the observed values coincide with the gateway's sizing —
  cpu is passed through verbatim (`:923`) and memory is
  `max(4, (memory_mb + 1023) // 1024)` GiB (`:924` — i.e.
  `max(4, ceil(memory_mb/1024))`: `memory_mb` in MiB, rounded UP to whole
  GiB, 4 GiB floor). v1.1's gateway-mirror formula survives ONLY as this
  lane's explanation of what the guest will observe; it is no longer an
  admission input anywhere.
- **`job.json`'s declared values keep their platform-side jobs**:
  `cpu_units`/`memory_mb` (`RuntimeRequirements`,
  `fi/simulate/runtime/spec.py:65-75`) remain the platform pre-checks
  (serializer bounds + voice reject, `harness_job.py:113, 205-211`), and
  the guest `W ≤ cpu_units` gate (`job.py:180-181`) keys on the DECLARED
  `cpu_units` UNCHANGED. Observed and declared cpu CAN diverge (snapshot
  lane); the MIN of the two binds W′: the declared gate rejects
  `W > cpu_units` before stage 1 ever runs, and stage 1's observed
  `cpu_fit` clamps below the box's real size — neither side can admit
  past the other.
- **Reserve/cost constants** (interim, plan §6, ESTIMATES ±40%): harness
  reserve `R_cpu = 0.5` vCPU, `R_mem = 1` GiB; per-world in-call cost
  voice-tier `c_world = 0.6` vCPU, `m_world = 0.7` GiB; simulator per active
  call `c_call = 0.2` vCPU, `m_call = 0.25` GiB. Worked example (observed:
  `cpu_obs = 4`, `mem_obs_gib = 4` — e.g. the registered snapshot's box,
  or the dockerfile lane at `cpu_units = 4`, `memory_mb = 1024` where the
  gateway's 4 GiB floor lands the same observation): cpu_fit =
  (4−0.5)/0.8 ≈ 4.4 → 4; mem_fit = (4−1)/0.95 ≈ 3.2 → 3;
  W′ = min(4, 4, 3) = 3. **Track D replaces these with measurements**
  (plan §4/§6); until then the voice-tier column is the single default
  for every job (see OD-1).
- **`max(1, …)` is load-bearing**: admission NEVER fails a job and never
  produces W′ = 0. A box too small even for W=1 is discovered by the run
  itself, not by this clamp.
- **Emit**: iff W′ < W_requested, append ledger entry
  `{reason: resource_limited, from_w: W_requested, to_w: W'}` (§6; shape
  per C4 §2). W′ may land anywhere in `1..W_requested−1` —
  `resource_limited` and `world_start_failed` are the two reasons that
  routinely settle above 1 (C4 §2; Amendment A). No entry when
  W′ == W_requested.
- **Locus**: `process_runtime.py`, Track B (plan §3 P0-C C2 row); the
  ledger write shares the single write site `:4601-4607`.

### The guest universal rule — DECISION RECORDED: KEPT, universal, unchanged

`job.py:180-181` rejects `parallelism > cpu_units`
(`hosted_parallelism_exceeds_cpu`) for EVERY hosted job, at `load_job`,
before any C2 stage runs. The plan offered kept-or-scoped; C2 records
**KEPT**:

- Plan §6's arithmetic uses it as the binding limit for the light-agent
  tier ("W≤cpu_units, then W≤8") — cpu_fit alone can exceed `cpu_units`
  for cheap agents (worked: (4−0.5)/0.35 = 10 for text-tier), so scoping
  the rule away would silently admit more workers than vCPUs on the
  strength of ±40% estimates.
- C4 §5 already describes it as "defense in depth, unchanged".
- The platform-side twin stays connector-scoped (`harness_job.py:205-211`)
  and its reject condition is literally universal by set inclusion — the
  condition enumerates all four connector choices the serializer admits
  (`harness_job.py:206`, `:72`); the guest rule is the universal backstop
  behind it.
- It is a REJECT (job fails validation), never a clamp — the clamp voice
  belongs to §2's math and to C4 §5's `HARNESS_PARALLELISM_ENABLED` belt.
  Consequence: by the time stage 1 runs, `W_requested ≤ cpu_units`
  (DECLARED) holds; §2's MIN rule covers observed-vs-declared divergence.

Scoping or relaxing this rule is a version bump of THIS contract first.

---

## 3. What the scheduler side does — assert-only, no change

`hosted_scheduler.py` already tolerates a contiguous shortfall; C2 requires
**no behavioral scheduler change** (plan §2). Verified:

- `WorldPool.start()` accepts fewer worlds than `instances` and rejects
  only a malformed result: zero worlds, duplicate indices, a non-contiguous
  index set, or more than requested (`hosted_scheduler.py:812-845`,
  contiguity check `:832-844`). `_effective_size = len(runtimes)` (`:845`).
- `lease()` (`:877+`) and executor sizing key off `effective_size`
  (`:788-793`, `:1420`), not off requested W.
- The entrypoint already warns loudly when the pool is short WITHOUT a
  recorded degrade (`hosted_entrypoint.py:1927-1934`) — that warning firing
  is a C2 conformance FAILURE tripwire, not a feature to rely on.

**The invariant the provisioner MUST uphold** (this is the contract line
two engineers implement against):

> `provision(instances=W)` returns exactly the worlds `0 .. E−1`, ordered,
> contiguous from 0, with `1 ≤ E ≤ W`; `E` equals `build.json`'s
> `effective_parallelism`; and every shortfall (`E < W`) is accompanied by
> a degrade ledger (§6; C4 §2's shape) whose final entry's `to_w == E`. It never
> returns a world set with a hole, never renumbers a world, never returns
> zero worlds (that is a raised job failure, §5), and never exceeds the
> attempt ceiling on any later call.

Track B MAY add assertions on the scheduler side restating this invariant
(assert-only); anything stronger is out of scope.

---

## 4. The pre-plan literal-endpoint secret scan (plan §1 mechanism)

The secret-refs channel is guest-only: the platform cannot inspect secret
VALUES (write-only, stripped pre-persist; they resolve only in-sandbox —
plan P0-V; the platform-visible `environment_values` channel is C4 §7's,
not this section's).

**Mechanism (normative, Track B):**

1. **Timing**: at provision start, on the FIRST `provision()` call for a
   job identity, the guest resolves secret refs ONCE — the existing
   load-and-delete (`_load_and_delete_secrets`,
   `process_runtime.py:4463-4473`) HOISTED to before the `plan_ports` call
   at `:4450` (today it sits after it, inside the first-call branch). The
   scan runs immediately after resolution: **before `plan_ports`, before
   any world builds**.
1b. **Resolution ERRORS fail closed (typed JOB FAILURE).** A resolution
   FAILURE at provision start — missing or corrupt secrets file, vault
   fault; distinct from "a literal was found" — is a typed JOB FAILURE.
   Rationale: proceeding unscanned would silently bypass the mandatory
   scan, and the job cannot succeed without its secrets anyway. Never
   warn-and-continue, never an unscanned run (§7 row). **Per-ref
   resolution gap (DECIDED, same shape):** an INDIVIDUAL `secret_refs`
   alias with no value present in the loaded file is the same typed JOB
   FAILURE — fail closed, same rationale: the job cannot run correctly
   without its secrets, and a partial scan silently bypasses the mandate
   (§7 row).
2. **Scan set — TWO TIERS** (adopting C1 v1.3 §5.4's decided scope;
   the tier decision is C1's, the mechanism is this section's). Both
   tiers run ONLY while the ceiling entering the scan stage exceeds 1
   (§1 rule 2's skip; C1 v1.3 §5's W-scoping) — at ceiling 1 there is
   no scan and no warning: today's behavior exactly.
   **(a) Degrade tier** — literals matching a DECLARED port, in ALL
   THREE loopback forms: the value carries `localhost:<declared>`,
   `127.0.0.1:<declared>`, or `[::1]:<declared>` (all three carried since
   v1.2 — C4 v1.3 §7's degrade-tier loopback amendment is therefore
   ALREADY SATISFIED on C2's side, its still-open observation notwithstanding;
   internally consistent too — a declared-port
   literal is exactly what the degrade-to-1 CURES, in whichever loopback
   spelling, so a declared-port `[::1]` literal belongs in THIS tier,
   not the warn tier), where `<declared>` ranges over every
   `SourceProcess.fixed_port` value in the manifest (the same set
   `plan_ports` collects, `:236-240` — computed directly from the
   manifest here, pre-plan).
   **(b) Warn tier** — any OTHER `localhost:<p>` / `127.0.0.1:<p>` /
   `[::1]:<p>` literal — a NON-declared port, in ANY variable (C1 v1.3
   §5.4b's decided scope: the warning text is GENERIC and the scan never
   evaluates per-process authoritativeness): **warning only
   (alias + port, item 5's hygiene), W unchanged — the job runs at the
   requested W.** Rationale (C1 §5.4b): a non-declared port has no
   listener at ANY W, so degrading cures nothing. Secondary note: where
   the variable happens to be a capability variable a process's own
   `environment` declares, it is additionally re-asserted world-correct
   over the injected literal at spawn (the authoritative-endpoint pass,
   §4a's carve-out) — the job runs at requested W either way. The
   observed real job (`TOOLS_API_URL=http://localhost:18090` — 18090
   declared nowhere) is warn-tier: it warns and RUNS at requested W, it
   does not degrade.
3. **On a degrade-tier hit** (and only while the incoming ceiling > 1, §1
   rule 2): set the attempt ceiling to 1 and append ledger entry
   `{reason: literal_local_endpoint, from_w: <incoming ceiling>, to_w: 1}`
   **FIRST — then** call `plan_ports(instances=1)`. At instances=1 the
   declared port is honored exactly as today (`PortPlan.port_for`,
   `:224-230`; C1 §2's plan-time carve-out) — the degrade is a genuine
   cure: a secret carrying `localhost:<declared>` (e.g. `localhost:8080`,
   the declared tools-api port) binds the declared port and runs as today.
4. **Latch + retained scan result**: the scan runs exactly once per job
   identity (the secrets file is deleted on load; the values latch in
   memory, `_secrets_loaded`); its ceiling reduction and ledger entry
   live as provider-instance state mirrored into `build.json` (§1 rule
   3's state locus) and persist across every reconcile call. **The
   provider additionally retains the SCAN RESULT as a set of literal
   loopback PORTS found — ports only, NEVER values, held IN MEMORY and
   never written to `build.json`** (secret values are loaded once and the
   file deleted, `:4463-4473`; §1 rule 3's state locus). On a
   same-job-identity **digest rebuild** (branch (c), §1 rule 4;
   `:4454`/`:4478`/`:4536-4541`) the ceiling, ledger, and in-memory
   retained port-set are copied forward, and the port-set is
   **RE-CLASSIFIED against the NEW manifest's declared-port set — this
   reclassification MUST run in the rebuild branch BEFORE the new
   `plan_ports` call** (the pinned ordering, §1 rule 4 (c)): a retained
   port the new manifest NEWLY declares → ceiling lowered to 1 pre-plan
   with `literal_local_endpoint` (§6's dedup rules apply); §1 rule 3's
   scan-staleness rule is the same fact from the state side. The value
   SCAN itself does not re-run (secrets file deleted); only the retained
   port-set is re-classified. A NEW attempt (fresh provider) resets all of
   it.
5. **Hygiene**: the degrade event carries only the closed payload
   (requested/effective/reason — `outbound.py:624-638`). An accompanying
   `log` warning MAY name the matched port number and the secret ALIAS, and
   MUST NOT reproduce any other part of a secret value (same rule shape as
   §4a's spawn-time override warning — this contract's own rule). The
   warn tier's warning follows the same rule.
6. **No admission reject, ever, on this channel** (plan P0-V locus
   constraint): the outcome is degrade-to-1, warn-and-run, or the fallback
   below — post-admission by construction, since the platform never saw
   the value. (Item 1b's resolution-ERROR failure is a provision-time
   typed failure, not an admission reject.)

**Fallback if early resolution proves infeasible** (plan §1, stated so the
claim can be dropped honestly, never silently substituted): the cure claim
is DROPPED — the job FAILS LOUD with `literal_local_endpoint` as the
failure code (a failure object diagnosis, not a degrade event; C4 §2
already shares the reason string across both shapes) and the user fixes
their env. Discovering infeasibility is a Track B implementation finding;
recording the switch is a version bump of this section.

**Non-conformant timing (MUST NOT)**: running the check at per-world secret
materialization — inside the build loop — is explicitly non-conformant. By
then world 0 is already built on an ALLOCATED port
(`plan_ports` ran; C1 §2 gives world 0 the formula port at effective W>1)
and reconcile only tears down worlds `≥ effective`
(`process_runtime.py:4611-4612`) — it never rebuilds world 0, so the
"cure" would require re-running `plan_ports` and rebuilding world 0
mid-flight. A per-world check can only warn; it cannot cure.

---

## 4a. Spawn-time override warning (guarded keys) — normative home

This is the section C1 §3/§4 and C3 §2.2 point at as C2's; C3 consumes it
as the diagnosis signal for the injected-secrets channel and does not
re-legislate it.

**Mechanism (normative, Track B):** at the spawn env merge
(`process_runtime.py:1591-1594` — `**build_environment, **rendered,
**injected, **authoritative_endpoints`), if an injected secret OVERRIDES
any of the guarded keys, surface a WARNING through the existing warning
channel (a `log` event, level `warning`). **Trigger (DECIDED, exactly
three conditions):** the injected value is present for the key AND a
rendered value is present for the key AND the two differ. Injected
present with rendered ABSENT is NOT a warning — nothing is overridden;
the injected value is simply the variable's only source. The warning
names the KEY; it MUST NOT log either value (the injected one is a
secret).

**Guarded key set (exactly four, per C3 §2.2):** `LIVEKIT_AGENT_NAME`,
`FI_LOAD_THRESHOLD`, `FI_NUM_IDLE_PROCESSES`, `FI_WORKER_HEALTH_PORT`.

**Authoritative-endpoint carve-out:** capability configuration variables
the process's own `environment` declares are re-asserted to the
world-correct address AFTER secret injection (`**authoritative_endpoints`
last in the merge, `:1591-1594`; the set is built at `:1583-1587` —
`:1576-1582` is the explanatory comment above it) — such
variables cannot be overridden and are therefore EXEMPT from this warning.
None of the four guarded keys is a capability variable, which is exactly
why the injected override wins for them. Attribution, stated precisely:
C3 §2.2 establishes that fact for `LIVEKIT_AGENT_NAME` and explicitly
scopes its claim to that one key; the `FI_*` generalization is **C2's own
verified claim** — the authoritative set is built exclusively from
capability configuration addresses that the process's own `environment`
declares (`:1583-1587`), and none of the `FI_*` knobs is a capability
configuration name.

Not a degrade: the warning is NOT a member of the closed degrade-reason
enum and NOT a spawn failure (C3 §2.2). The per-call consequences (no-join,
dispatch-ack) are C3's domain.

---

## 5. Partial world-start failure — catch, continue, or fail

Today `_provision_sync` propagates a per-world build failure UNCAUGHT from
BOTH `_ensure_world` call sites (plan §2): the conformance-gate branch
(`process_runtime.py:4579-4580`) and the main loop (`:4613-4614`).
`_ensure_world` raises typed `ProcessRuntimeError` (or propagates
`BaseException`) and, WHEN the failing layer attached partial handles
(`exc.partial_handles` — the publication is CONDITIONAL, `:4683-4687`,
`:4697-4699`), publishes them into `self._world_handles[world_index]`
before re-raising, so a partially-built world is reclaimable; a world can
equally fail with NOTHING published. C2 adds the catch at **both** sites,
**armed on the first-call/digest-mismatch branch ONLY** — the FIRST BUILD
of a build identity (`pool.start()`) AND a mid-job DIGEST REBUILD, both
taking the `:4454` branch (§1 rule 4 (a)/(c)); it is **NOT armed on
RECONCILE calls** (the `:4548-4554` branch, §1 rule 4 (b)), where rule 2R
governs — a rebuild failure re-raises to WorldPool:

**Rule 1 — world 0 failing on the first-call/digest-mismatch branch = JOB
FAILURE (MUST); on reconcile calls the job continues.** Both behaviors are
normative (the branch, not the caller, decides — §1 rule 4):

- **First-call/digest-mismatch branch** — the initial `WorldPool.start()`
  `provision()` (`hosted_scheduler.py:812-825`, §1 rule 4 (a)) OR a mid-job
  DIGEST REBUILD (§1 rule 4 (c)): a failure of `_ensure_world(0)`
  propagates uncaught, exactly as today. E = 0 is unrepresentable: the
  degrade payload requires `1 ≤ effective < requested` (`outbound.py:633`;
  ingestion `:586`; C4 §2). Emitters MUST NOT attempt an `effective: 0`
  payload; the job fails with the underlying typed error, and the FE shows
  a failure banner, not a degrade notice (C4 §9).
- **Reconcile calls**: the same raise out of `provision()` is CAUGHT by
  WorldPool's reconcile loop (`hosted_scheduler.py:1121-1152`) — the
  still-down worlds get typed §2f down-markers and the job CONTINUES on
  the remaining healthy worlds. No job failure, no degrade event: mid-run
  attrition is the scheduler's domain (§6's emission split). This is the
  world-0 instance of rule 2R's general reconcile rule.

**Rule 2 — main-loop catch (`:4613-4614`) — first-call/digest-mismatch
branch ONLY (DECIDED).** Rule 2's catch → ledger entry →
continue-on-the-prefix behavior belongs to the provisioning phase: the
FIRST BUILD (`WorldPool.start()`'s `provision()`) and a mid-job DIGEST
REBUILD (§1 rule 4 (a)/(c)), never a reconcile call. A failure of
`_ensure_world(k)` for `k ≥ 1` there:

- The sequential `for world_index in range(effective)` loop guarantees
  worlds `0..k−1` are already built and contiguous — **NO renumbering**,
  ever. The built prefix IS the surviving world set.
- Tear down whatever the failed world PUBLISHED (`_teardown_world(k)` —
  the same primitive the reconcile-down pass uses, `:4611-4612`).
  Publication is conditional (above): `_teardown_world` MUST tolerate a
  world with no published handles — or none at all — as a no-op. Never
  orphan a live engine.
- Set the attempt ceiling and `effective` to `k`; ledger update per §6's
  dedup rule — append `{reason: world_start_failed,
  from_w: <prior ceiling>, to_w: k}` if no `world_start_failed` entry
  exists, else UPDATE the existing entry's `to_w` downward (never a second
  entry); RE-WRITE `build.json` (the write at `:4601-4607` already
  happened with the pre-catch values — a stale record here is a
  silent-shortfall bug, the exact `hosted_entrypoint.py:1927-1934` warning
  case).
- Latch (§1 rule 3): later reconcile calls reconcile to the new ceiling;
  they MAY rebuild sick worlds below it, MUST NOT retry the failed world
  upward.
- Return worlds `0..k−1` and continue the run at the new ceiling.

**Rule 2R — reconcile calls: re-raise, the scheduler's domain (DECIDED).**
On a RECONCILE `provision()` call, a rebuild failure at ANY world index —
world 0 included — propagates OUT of `provision()` (the stage-5 catches
are NOT armed) and is CAUGHT by WorldPool's reconcile loop
(`hosted_scheduler.py:1121-1152`): typed §2f down-markers on the
still-down worlds, bounded retry with backoff (the M5 design, preserved),
and the job CONTINUES on the healthy worlds — leased worlds survive
(`:1217-1222`). **NO ledger entry, NO ceiling change, NO teardown of
healthy worlds.** Mid-run attrition is the scheduler's domain, per §6's
own emission split; a provisioner-side catch here would double-own the
condition and turn recoverable attrition into a permanent ceiling cut.
(v1.1's reconcile-path "orphan rule" is DELETED by this decision — the
stranding scenario it legislated cannot arise once reconcile failures
re-raise instead of lowering the ceiling.) The `world_start_failed`
producer scoping this implies for C4 (reconcile-call failures produce
scheduler down-markers, never ledger entries or degrade events) is folded
into the single **RECONCILE AT POST-FREEZE CONSISTENCY PASS** note in §6.

**Rule 3 — gate-branch catch (`:4579-4580`).** `_ensure_world(1)` failing
in the conformance-gate branch (world 0 built, world 1 not): catch, tear
down whatever world 1 published (conditional publication, as in rule 2),
then attribute the outcome by **C1 v1.3 §4's mapping** (referenced, not
re-legislated). Two of the arms are TERMINAL JOB FAILURES, two are
graceful degrades — the distinction is the whole point of C1's split:

- **`port_not_consumable` → TERMINAL JOB FAILURE (NOT a degrade to 1).**
  A world-index ≥ 1 startup failure when world 0's copy started AND
  bind-error log evidence (the `[Errno 48]`/address-in-use class) is in
  hand, in EITHER of C1 v1.3 §4's two `port_not_consumable` arms: (i) the
  errored port is a DECLARED `fixed_port` value and the failing process is
  CONSUMABLE-declared (a lying consumable binding its own declared port),
  or (ii) the errored port is a non-formula, non-declared port (e.g. the
  framework default 8081) and the failing process is knob-bearing
  (carries `FI_WORKER_HEALTH_PORT`). RAISE A TERMINAL JOB FAILURE, reason
  `port_not_consumable`, carrying C1's diagnosis (*"the agent is declared
  parallel-capable but did not honor its assigned port; fix it to read its
  port env, or request parallelism=1 to run serially"*). It does **NOT**
  lower the ceiling to 1 and does **NOT** append a `degrade_events` entry
  — world 0 is already internally inconsistent (its consumers and
  capability URLs point at the unbound formula port) and the frozen plan
  cannot re-consist it (no mid-flight re-plan; C1 §2/§4 decision 2). There
  is nothing to degrade INTO. The FE shows a failure banner, not a degrade
  notice — the same shape as the world-0 job-failure case.
- **`world_start_failed` → graceful degrade** (unchanged): a collision ON
  a formula port is the stale-squat class (a prior worker still holding
  it; C1 v1.3 §4's anti-false-positive), and any other (non-consumable,
  non-knob-bearing) process's failure → set the effective ceiling
  (§1 rule 3's integer) to 1 directly, ledger `world_start_failed` per §6.
- **`conformance_gate_failed` → graceful degrade**: a knob-bearing
  process failing with NO bind-error evidence (a conformant agent can die
  at world 1 for port-unrelated reasons) → ceiling to 1, ledger
  `conformance_gate_failed`.

Attribution precision may degrade; loudness may not (C1 §4). **The catch
does NOT stamp a synthetic gate verdict** — it MUST NOT fabricate
`build_output.conformance = False`: the latch is the ceiling integer,
decoupled from the conformance flag, and the old conformance-False latch
(`:4594-4599`, which re-stamps `conformance_gate_failed` at `:4599`)
remains ONLY for the genuine gate-failure path. For the graceful-degrade
arms the catch's recorded reason is preserved across later calls by its
ledger entry + the ceiling, never by re-stamping; the terminal
`port_not_consumable` arm ends the job, so nothing is preserved forward.

**Gate declared-port LISTENER check (C1 v1.3 §4, adopted) — also
TERMINAL.** Distinct from the bind-death catch above: AFTER the gate
worlds build, a check that any declared `fixed_port` has a live listener
(the lying-consumable-with-tolerant-`started_check` residual) → **TERMINAL
JOB FAILURE, reason `port_not_consumable`**, raised out-of-band as its own
provision step — NOT a ledger append, NOT a ceiling drop, and (per C1 §4's
locus decoupling) NOT setting `build_output.conformance = False`. Same
diagnosis and FE failure-banner shape (§7, §8).

**Rule 4 — what is NOT a start failure.** The post-build readiness poll
(`:4622-4635`) marking a world `UNHEALTHY` is not this section's subject:
the world exists, the scheduler's sick-world path owns it
(`hosted_scheduler.py` `_down`/reconcile). Only a raise out of
`_ensure_world` is.

---

## 6. The degrade ledger — writer-side rules (transport is C4's)

**C4 OWNS the transport, end to end.** The normative shape is C4 §2's:
`build.json` carries `degrade_events` — a list of `{reason, from_w, to_w}`
in causal order — the legacy scalar fields (`requested_parallelism` /
`effective_parallelism` / `degrade_reason`, `process_runtime.py:4601-4607`)
mirror the final state for old readers, and the emitter emits one
`parallelism_degraded` event per entry, in list order. C2 does not
re-legislate any of that (OD-2 closed — resolved: C4's shape). C2 keeps
only the WRITER side — who appends when:

- **Writers (COMPLETE enumeration — Track B, inside `_provision_sync`,
  all on the first-build/digest-rebuild branch, never on reconcile —
  §1 rule 4 (a)/(c))**: stage 1 (admission clamp) appends the first entry
  pre-loop; stage 2 (scan, degrade tier) appends before `plan_ports`;
  **stage 3** — `fixed_port`: when `plan_ports` forces
  `effective_instances` to 1 (`process_runtime.py:245-246`) the PIPELINE
  appends the entry (the `PortPlan` itself carries only the legacy
  `degraded_reason` string, never a ledger write); **stage 4** —
  `conformance_gate_failed`: the genuine gate-failure return
  (`:4591-4593`) appends; stage 5's catches append `world_start_failed`
  or `conformance_gate_failed` (the GRACEFUL-degrade arms only) when they
  fire and re-write `build.json` (§5; reconcile-call failures never write,
  rule 2R). `from_w` is the ceiling entering the stage; `to_w` the ceiling
  it set. Maintaining the legacy-mirror fields on every write is a writer
  duty (C4 §2's rule). No code outside this enumeration appends.
  **`port_not_consumable` is NOT a ledger writer** — neither the
  stage-5 gate-branch bind-death arm nor the gate-stage declared-port
  LISTENER check appends a `degrade_events` entry: per C1 v1.3 §4
  decision 2, both are TERMINAL JOB FAILURES (§5 rule 3, §7, §8, §11),
  raised out-of-band, not degrade producers.
- **C4's reciprocal ask is ALREADY SATISFIED.** C4 v1.3 §2's "SIBLING
  AMENDMENT REQUIRED (C2 §6 — writer enumeration)" asks C2 §6 to
  additionally enumerate the stage-3 `fixed_port` writer
  (`process_runtime.py:245-246`) and the stage-4 GENUINE-gate writer
  (the caller at `:4591-4593`). Both are already in the Writers bullet
  above (since v1.2) — the request is SATISFIED, no C2 change owed.
- **RECONCILE AT POST-FREEZE CONSISTENCY PASS (C4 is v1.3 FROZEN — the
  single consolidated note; supersedes both prior "verify at next round"
  notes).** Two cross-contract reconciliations against frozen C4 v1.3 are
  dispatched to the post-freeze consistency pass (C2 cannot amend a frozen
  sibling; both are recorded here, not applied):
  1. **`port_not_consumable` terminal-not-degrade reclassification (from
     C1 v1.3 §4 decision 2).** Frozen C4 v1.3 §2 still lists
     `port_not_consumable` as one of SIX `parallelism_degraded` degrade
     reasons (with §6 FE degrade copy and a §9 degrade row). The
     consistency pass MUST reclassify it in C4 as a **terminal
     job-failure reason** — still C4-surfaced, but a JOB FAILURE (FE
     failure banner), NOT a `parallelism_degraded` event — and DROP it
     from the `degrade_events` vocabulary / the ingestion degrade-validator
     accepted set (`hosted_harness_ingestion.py:579`) and the guest
     `DegradeReason` enum (`outbound.py:581-583`), keeping it only as a
     surfaced failure reason. Both `port_not_consumable` DETECTION loci —
     the stage-5 gate-branch bind-death arm and the gate declared-port
     listener check — carry into that pass, as does the formula-port
     stale-squat exclusion.
  2. **`world_start_failed` reconcile-call scoping (from §5 rule 2R).**
     C4 v1.3 §2's `world_start_failed` producer row and writer enumeration
     ("C2 provision catch at BOTH `_ensure_world` sites") must carry the
     first-build/digest-rebuild-branch-only scoping: a reconcile-call
     rebuild failure produces scheduler down-markers, never a ledger entry
     or degrade event.
- **Latch no-append rule (MUST)**: the reconcile-path conformance latch
  (`:4594-4599`) is a LEGACY-FIELD re-stamp — it restates the
  already-latched final state into the legacy mirror fields only and
  MUST NOT append a `degrade_events` entry (aligned with C4 §2's
  `conformance_gate_failed` row, which records it as a mirror-write, not
  a producer).
- **Dedup rule (MUST)**: a repeat failure with an already-present reason
  UPDATES that entry's `to_w` downward — never a second entry. C4 §2's
  invariants (≤1 entry per reason, `to_w` strictly decreasing down the
  list) bind the ledger AT ALL TIMES, not just at emission.
- **State locus**: the ledger (with the ceiling) is provider-instance
  state mirrored into `build.json` on every write; `build.json` is the
  durable read surface. Copied forward across the same-job-identity digest
  rebuild (`:4536-4541`); reset only by a new attempt (§1 rule 3).
- **Emission split (normative)**: the Track C′ emitter
  (`hosted_entrypoint.py:1896-1908` path) emits the ledger ONCE, at
  entrypoint emission. Ledger updates occurring AFTER emission are NOT
  re-emitted as `parallelism_degraded` events — run-time world losses
  surface through the scheduler's existing sick-world/world-down typed
  codes (`hosted_scheduler.py:1121-1152`). The domain split, stated
  explicitly: **the degrade ledger describes provisioning clamps; mid-run
  attrition is the scheduler's domain.**
- **Track attribution**: `build.json`'s WRITER side is Track B; the emit
  side (`outbound.py` enum + `hosted_entrypoint.py` emitter) is Track C′
  (plan §4's single-writer table; C4 §2/§3).
- Reason strings are exactly C4 §2's closed vocabulary — C4 owns the table,
  the guest enum extension (`outbound.py:581-583`), the ingestion validator
  extension (`hosted_harness_ingestion.py:579`), and the validator-first
  deployment order. C2 adds no names; it is the named producer of
  `resource_limited` (§2) and `world_start_failed` (§5), and the mechanism
  locus for `literal_local_endpoint` (§4; name reserved by C4).

---

## 7. Failure shapes at this boundary (closed)

| condition | shape | loud via |
|---|---|---|
| W_requested > cpu_units (guest) | job validation failure `hosted_parallelism_exceeds_cpu` (`job.py:180-181`) | typed reject before anything provisions |
| resources fit fewer worlds than requested | continue at W′, ledger `resource_limited` | `parallelism_degraded` event(s) |
| observed cpu/memory read at provision start FAILS, returns `None`, or is UNBOUNDED (`max`) | that dimension FALLS BACK to its declared `job.json` value (`cpu_units` / `memory_mb`); W′ = max(1, min(W, cpu_fit, mem_fit)) with observed-or-declared per dimension; admission NEVER raises (§2 fallback rule) | no failure — an ordinary (possibly clamped) admission; ledger `resource_limited` iff W′ < requested |
| resolved secret carries `localhost:<declared>` / `127.0.0.1:<declared>` / `[::1]:<declared>` (degrade tier — all three loopback forms), ceiling > 1 | pre-plan degrade to 1, `literal_local_endpoint`; declared port honored; job runs as today | degrade event + log warning (alias/port only) |
| resolved secret carries any OTHER `localhost` / `127.0.0.1` / `[::1]` port literal — a NON-declared port, in ANY variable (warn tier — the observed `localhost:18090`) | warning only, W unchanged; job RUNS at requested W (declared capability variables additionally re-asserted world-correct, §4a) | log warning (alias/port only); NO degrade event |
| secret RESOLUTION fails at provision start (missing/corrupt file, vault fault — distinct from "literal found"), OR an individual `secret_refs` alias has no value in the loaded file (§4 item 1b's per-ref gap) | typed JOB FAILURE, fail closed — an unscanned (or partially scanned) run silently bypasses the mandatory scan, and the job cannot run correctly without its secrets | typed error before `plan_ports`; never warn-and-continue |
| same as degrade tier, but early resolution infeasible (fallback) | JOB FAILURE, code `literal_local_endpoint` | failure object as diagnosis — never silent substitution |
| world k ≥ 1 fails to build (main loop, first-build/digest-rebuild branch) | continue at E = k on the contiguous prefix, ledger `world_start_failed` (dedup: repeat failures update `to_w`, §6); the failed world's published partials torn down (§5 rule 2) | degrade event (if pre-emission); re-written build.json |
| world 1 fails in the gate branch — stale-squat (formula-port bind evidence), a non-consumable/non-knob-bearing process, or NO bind evidence | GRACEFUL degrade to E = 1 via the ceiling (no synthetic gate verdict); reason `world_start_failed` / `conformance_gate_failed` per C1 v1.3 §4 mapping | degrade event; ceiling + ledger hold it |
| world 1 fails in the gate branch — `port_not_consumable` arm: declared-port bind by a CONSUMABLE-declared process, OR a non-formula/non-declared default port by a KNOB-BEARING worker (C1 v1.3 §4, both with bind evidence) | **TERMINAL JOB FAILURE**, reason `port_not_consumable` — NOT a degrade; world 0 already internally inconsistent, no W=1 salvage (C1 §4 decision 2); no ceiling drop, no ledger entry | failure banner, not a degrade notice; carries C1's diagnosis (fix the port env, or request parallelism=1) |
| gate declared-port LISTENER check finds a live listener on any declared `fixed_port` at effective W>1 (C1 v1.3 §4, adopted) | **TERMINAL JOB FAILURE**, reason `port_not_consumable`, raised out-of-band as a distinct provision step — no ceiling drop, no ledger append, no synthetic `conformance = False` | failure banner, not a degrade notice |
| world 0 fails to build at the FIRST provision call (`pool.start()`) | JOB FAILURE — typed `ProcessRuntimeError` propagates; NO degrade event (E = 0 unrepresentable, `1 ≤ effective`) | failure banner, not a degrade notice |
| ANY world — index 0 included — fails to (re)build on a RECONCILE call (§5 rule 2R) | raise propagates (stage-5 catches not armed) and is caught by WorldPool (`hosted_scheduler.py:1121-1152`); typed down-markers + bounded retry; job CONTINUES on healthy worlds; NO ledger entry, NO ceiling change, NO teardown of healthy worlds | scheduler sick-world path — no degrade event, no job failure |
| provisioner returns short with no ledger | NON-CONFORMANT — tripwire warning `hosted_entrypoint.py:1927-1934` fires | must not occur; §9 checklist item |
| malformed world set (hole, dup, zero, over-count) | scheduler raises (`hosted_scheduler.py:832-844`) | RuntimeError — provisioner bug by definition |

Nothing at this boundary is permitted to hang, to renumber a world, or to
fail silently.

---

## 8. What C2 does NOT own · accepted platform properties

- The allocation rule, the effective-W=1 declared-port carve-out, the
  consumability split, worker-knob env vars, and the
  `port_not_consumable` attribution mapping AND its terminal-not-degrade
  classification (C1 v1.3 §4 decision 2) → **C1**.
- **`port_not_consumable` is a TERMINAL JOB FAILURE, not a degrade
  (C1 v1.3 §4 decision 2) — recorded here as the mechanism note.** C2's
  Track B provisioner is where it is RAISED: the stage-5 gate-branch
  bind-death arm (§5 rule 3) and the gate declared-port listener check
  (§5 rule 3) each raise a terminal job failure carrying reason
  `port_not_consumable` and C1's diagnosis, out-of-band — never a
  `degrade_events` append, never a ceiling drop, never a synthetic
  `build_output.conformance = False`. A NON-consumable code-fixed
  `fixed_port` is the clean, unchanged CONTRAST: it stays a plan-time
  graceful W=1 degrade, reason `fixed_port` (stage 3, §1). The C4-side
  surfacing reclassification is dispatched to the post-freeze consistency
  pass (§6).
- Dispatch-ack, agent-name uniqueness preflight, retry idempotency →
  **C3** (the name guard is a Track B deliverable but a C3 precondition).
- Reason vocabulary, the degrade-ledger TRANSPORT shape
  (`degrade_events`, `{reason, from_w, to_w}` — §6/OD-2), guest enum +
  ingestion validator extensions, deployment-order pins, FE surfacing,
  the `HARNESS_PARALLELISM_ENABLED` clamp, and the platform-visible
  `environment_values` literal-endpoint check → **C4**.
- **Shared-uid property, recorded as ACCEPTED** (plan §1 round-3
  correction): per-process svc-user separation exists in code but is
  bypassed on Daytona — the sandbox forces a single fixed user and rejects
  `os_user` overrides, so every process runs under one uid (the hosted
  guest constructs the provider with `require_declared_user=False` + a
  null resolver, `process_runtime.py:4386-4392`; probe-confirmed uid 2000).
  W>1 multiplies same-uid processes holding per-call secrets with mutual
  /proc visibility. This is a known platform property C2 neither weakens
  nor fixes — it is equally true at W=1 today. Replication does not create
  it; it widens its blast radius, and that is accepted and recorded.

---

## 9. Named amendments (text to add; do not rewrite the host documents)

**A. Spine `hosted-execution-seams.md` §5 degrade payload
(`hosted-execution-seams.md:906` — the `"effective": 1` verbatim line):**

> *Amendment (C2/C4):* the payload bound is `1 ≤ effective < requested` —
> `"effective"` is not verbatim 1 (`resource_limited` and
> `world_start_failed` routinely settle above 1), and an attempt may carry
> multiple `parallelism_degraded` events with constant `requested` and
> strictly decreasing `effective`. The reason enum is C4 §2's table.
> Ingestion already accepts the general bound
> (`hosted_harness_ingestion.py:586`); only this spine TEXT lags. C4 owns
> the vocabulary and transport; C2 co-sponsors the bound.

**B. Spine §1 `scenario_count` bullet (`hosted-execution-seams.md:329-330`,
"hosted admission range: 1..10 in V1"):**

> *Annotation (C2):* deliberately diverged in code, BOTH sides, for
> 20-scenario local regression runs: the guest caps at 25
> (`job.py:176-177`, with the widening comment `:173-175`) and the
> platform serializer admits 1..25 (`serializers/harness_job.py:186`).
> The spine's 1..10 is historical text, not the deployed gate; the
> scheduler sizes its executor from the real scenario list either way.
> Recorded so the divergence is a decision, not drift.

**C. Shared-uid property**: recorded in §8 above and in C1 §7; no spine
text change — it amends expectations, not schemas. (Working-group
sign-off list per plan §9 still applies to A and B.)

---

## 10. Conformance checklist

Admission (Track B):

1. `provision(instances=W)` on a box whose fit math yields W′ < W builds
   exactly W′ worlds and the ledger carries `resource_limited` with
   `to_w = W′`; at fit ≥ W no entry exists.
2. W′ is never 0, never above requested W, and never above the fit;
   admission never raises. Inputs are the RUNTIME-OBSERVED sandbox
   resources read by the guest at provision start (cpu via the cgroup CPU
   QUOTA — cgroup v2 `cpu.max` / v1 `cpu.cfs_quota_us`+`cpu.cfs_period_us`,
   or `sched_getaffinity`; NOT `os.cpu_count()`/host cores; memory via the
   cgroup/proc limit — §2), NOT `job.json`'s declared values.
   Read-failure fallback (§2, §7 row): a failed / `None` / unbounded read
   on a dimension FALLS BACK to that dimension's DECLARED value
   (`cpu_units` / `memory_mb`) — asserted per-dimension, and admission
   still does not raise. Dockerfile-lane only: the observed values equal
   the gateway's sizing (cpu verbatim, memory `max(4, ceil(memory_mb/1024))`
   GiB — `services/hosted_harness_gateway.py:918-929`); the snapshot lane
   (`:931-935`) has no such equality and the test MUST NOT assume it.
3. `job.py:180-181` unchanged: `parallelism > cpu_units` (DECLARED) still
   REJECTS every hosted job (kept-universal decision, §2); combined with
   stage 1, the MIN of declared and observed binds W′.
4. `plan_ports` is PER BUILD IDENTITY (§1 rule 4's trichotomy), with the
   post-clamp/post-scan ceiling — not raw W (change site
   `process_runtime.py:4450`): (a) FIRST BUILD (`pool.start()`,
   first-call branch `:4454`) runs it; (b) a RECONCILE `provision()` call
   (`:4548-4554`) leaves `context.port_plan` VALUE-IDENTICAL to the first
   build's (carried forward; same test as C1 checklist 5's second half);
   (c) a mid-job DIGEST REBUILD (`:4454` on a digest change, fresh
   `SpawnContext` `:4478`) RE-PLANS against the new manifest AND
   re-classifies the retained scan port-set BEFORE the new `plan_ports`
   runs (checklist 12b), carrying the ceiling+ledger forward monotone.

Pre-plan scan (Track B):

5. Secrets are resolved before `plan_ports`; a planted secret value
   containing `localhost:<declared>` (equivalently `127.0.0.1:<declared>`
   or `[::1]:<declared>` — all three loopback forms are degrade-tier,
   §4 item 2a) at requested W=4 → ledger
   `literal_local_endpoint`/`to_w: 1`, `plan_ports(instances=1)`, declared
   port bound, job completes as today (the DEGRADE-tier test; C1
   checklist item 13's planted-declared-port half is the same test from
   C1's side).
5b. The observed-real-job replay is the WARN tier: a secret carrying
   `TOOLS_API_URL=http://localhost:18090` (18090 declared nowhere) at
   requested W=4 → warn-tier warning (alias + port only), NO degrade
   event, NO ceiling change — the job RUNS at W=4 with declared
   capability variables re-asserted world-correct (§4 item 2b; C1
   checklist item 13's warn-tier half).
6. Same planted degrade-tier value at requested W=1 → no scan, no event,
   job unchanged.
7. Same value with the ceiling already 1 from stage 1 → no second ledger
   entry (§1 rule 2).
8. The scan warning names at most alias + port; no secret value appears in
   any log or event.
9. Grep-level: no scan logic inside the world-build loop (the per-world
   materialization timing is non-conformant, §4).
9b. A planted resolution ERROR (missing/corrupt secrets file) at provision
   start → typed JOB FAILURE before `plan_ports`; likewise a planted
   `secret_refs` alias with no value in the loaded file (§4 item 1b's
   per-ref gap) → the same typed JOB FAILURE; no unscanned or partially
   scanned run ever proceeds (§4 item 1b).
9c. A planted secret overriding a guarded key (e.g. `FI_LOAD_THRESHOLD`,
   with a rendered value present) → §4a's spawn-time warning fires,
   naming the key and neither value; a planted secret for a guarded key
   with NO rendered value present → NO warning (nothing is overridden —
   §4a's three-condition trigger); a planted secret aliasing a declared
   capability variable (e.g. `TOOLS_API_URL`) → NO override warning
   (authoritative-endpoint carve-out) — the re-asserted value wins.

Partial failure (Track B):

10. Planted failure of world k=2 at W=4 on the FIRST provision call →
    worlds 0..1 survive with their original indices, world 2's published
    partials are torn down (and a failure publishing nothing is tolerated
    — §5 rule 2), ledger appends `world_start_failed`/`to_w: 2`,
    `build.json` re-written, run completes on 2 worlds; a later reconcile
    call does NOT rebuild world 2 (above the latched ceiling).
10b. Reconcile symmetry (§5 rule 2R): after (10), a planted rebuild
    failure of sick world j=1 on a RECONCILE call → the raise propagates
    out of `provision()` and WorldPool catches it
    (`hosted_scheduler.py:1121-1152`): typed down-marker on world 1,
    bounded retry with backoff, and the job CONTINUES on world 0. The
    ledger is UNCHANGED (no new entry, no `to_w` update), the ceiling
    stays 2, healthy worlds and leased worlds are untouched
    (`:1217-1222`), and no `parallelism_degraded` event is emitted — the
    loss surfaces via the scheduler's typed codes only (§6 emission
    split).
11. Planted failure of world 0 at the FIRST provision call
    (`pool.start()`) → job fails with the typed `ProcessRuntimeError`; NO
    `parallelism_degraded` event exists anywhere; FE shows failure, not
    degrade. The same planted failure on a RECONCILE call → caught by
    WorldPool (`hosted_scheduler.py:1121-1152`), typed down-marker, job
    continues (§5 rule 1).
12. Planted failure of world 1 inside the gate branch — GRACEFUL arm
    (formula-port stale-squat, or no bind evidence) → degrade to 1 with
    the C1 v1.3 §4-mapped reason (`world_start_failed` /
    `conformance_gate_failed`); the ceiling + ledger keep effective=1
    across every later provision call with the SAME reason, and
    `build_output.conformance` is NOT stamped False by the catch (§5
    rule 3).
12t. Planted world-1 gate-branch bind death naming a DECLARED port by a
    consumable-declared process (or a non-formula default port by a
    knob-bearing worker), WITH bind evidence → **TERMINAL JOB FAILURE**,
    reason `port_not_consumable`: NO `degrade_events` entry anywhere, NO
    ceiling drop, `build_output.conformance` NOT stamped False, FE shows a
    failure banner (§5 rule 3, §7). Likewise a planted live listener on a
    declared `fixed_port` after the gate worlds build → the same terminal
    `port_not_consumable` failure via the gate declared-port listener
    check (out-of-band, distinct provision step).
12b. Digest rebuild = branch (c) (§1 rule 4): after any degrade, a
    same-job-identity bundle-digest change (`process_runtime.py:4454` on
    the digest mismatch, fresh `SpawnContext` `:4478`, `build_output`
    replaced `:4536-4541`) → `plan_ports` RE-PLANS against the new
    manifest, the stage-5 catches are RE-ARMED, and the fresh
    `build_output` carries the prior ceiling and ledger FORWARD; the
    ceiling never rises across the rebuild (§1 rule 3). AND: the retained
    scan port-set is re-classified BEFORE the new `plan_ports` runs (the
    pinned ordering, §1 rule 4 (c)) — a warn-tier run whose retained
    port-set contains port P, rebuilt with a manifest that NEWLY declares
    P as a `fixed_port` → ceiling lowered to 1 pre-plan with
    `literal_local_endpoint` (retained ports only — no secret value is
    read or logged; §1 rule 3's scan-staleness rule, §4 item 4).

Scheduler seam (assert-only):

13. `WorldPool.start()` and `lease()` diffs against HEAD are empty or
    assert-only (`hosted_scheduler.py:812-845, 877+`).
14. The shortfall-without-degrade warning (`hosted_entrypoint.py:1927-1934`)
    never fires in the E2E ladder — every shortfall has a ledger entry.

Ledger & events (with C4):

15. A forced two-stage attempt (resource_limited 4→2, then
    world_start_failed 2→1) yields TWO events, in order, `requested: 4`
    constant, `effective` 2 then 1 — both accepted by ingestion and both
    visible in `serialize_job["events"]` (C4 §8's round-trip test covers
    the vocabulary; this item covers the multi-entry ledger).
16. Legacy scalar `build.json` fields mirror the final ledger entry.

---

## 11. Open decisions

| # | decision | options | owner |
|---|---|---|---|
| OD-1 | Admission constants: reserves (`R_cpu`, `R_mem`) and per-world/per-call costs — the formula shape and inputs are pinned (§2: inputs are RUNTIME-OBSERVED sandbox resources, decided v1.2); the numbers are plan-§6 estimates ±40% | (a) keep the single voice-tier default until Track D measures; (b) Track-D-measured table (replaces §6 estimates per plan §4); (c) manifest-declared per-bundle costs (new producer field — needs Rishav, NOT recommended for v1) | Khushal, after Track D |
| OD-2 | ~~Ledger key naming~~ — **RESOLVED: C4's shape** (key `degrade_events`, entries `{reason, from_w, to_w}` — C4 §2 owns the transport; C2 §6 keeps only the writer-side rules) | closed | — |
| OD-3 | Scan-infeasibility fallback trigger (§4): what concretely counts as "early resolution proves infeasible" before the cure claim is dropped | Track B implementation finding; recording it = version bump of §4 | Track B → Khushal |

Deliberately NOT open here: the guest universal rule (recorded KEPT, §2);
the `port_not_consumable` split AND its terminal-not-degrade
classification (C1 resolved both: split, and v1.3 §4 decision 2 makes it a
TERMINAL JOB FAILURE, not a degrade — C2 implements it as terminal
throughout, §5 rule 3 / §6 / §7 / §8, with the C4-surfacing
reclassification dispatched to the post-freeze consistency pass, §6); the
reason vocabulary (C4's, closed); the ledger transport shape (C4's — OD-2
closed); the scan tier decision (C1 §5.4's, adopted in §4).

---

## 12. Versioning

Versions independently of the spine. Ambiguities become amendments, never
guesses.

**Changelog**
- v1.3 (2026-08-31): review round 3 (7 findings applied) — **FINAL round;
  the contract is now FROZEN (no round 4).** Any further change is a
  new-version reopening. BLOCKER: `port_not_consumable` reclassified from a
  degrade/ledger reason to a **TERMINAL JOB FAILURE** throughout C2
  (adopting C1 v1.3 §4 decision 2 — a lying/non-conformant consumable
  worker leaves world 0 internally inconsistent under the frozen plan, and
  degrade-to-1 does not re-plan, so it cannot cure it → loud job failure
  with diagnosis). Both detection loci — the stage-5 gate-branch bind-death
  arm and the gate declared-port LISTENER check — now RAISE a terminal job
  failure out-of-band (no `degrade_events` append, no ceiling drop, no
  synthetic `conformance=False`); a NON-consumable code-fixed `fixed_port`
  stays the clean plan-time W=1 degrade (reason `fixed_port`). Fixed in §1
  stage-table rows 4/5, §5 rule 3, §6 writers, §7 (two new terminal rows +
  split gate-branch row), §8, §10 checklist 12/12t, §11. MAJOR: CPU
  observation primitive pinned — `cpu_obs` MUST read the cgroup CPU QUOTA
  (v2 `cpu.max` / v1 `cpu.cfs_quota_us`+`cpu.cfs_period_us`, or
  `sched_getaffinity`), NOT `os.cpu_count()`/host cores (§2, checklist 2,
  changelog); read-failure fallback added — a failed / `None` / unbounded
  read on a dimension FALLS BACK to the DECLARED `job.json` value so
  admission never raises (§2, new §7 row, checklist 2). MAJOR: plan-once
  freeze reconciled with the digest-rebuild path via a crisp THREE-branch
  predicate keyed on WHICH `_provision_sync` branch runs — (a) FIRST BUILD
  (`:4454`) plans + scans + arms catches; (b) RECONCILE (`:4548-4554`)
  carries the plan forward, catches NOT armed; (c) DIGEST REBUILD (`:4454`
  on digest change, fresh `SpawnContext` `:4478`) re-plans + re-classifies
  the retained port-set BEFORE the new `plan_ports` + re-arms catches, but
  carries the ceiling+ledger forward monotone — "plan runs once / carry
  forward" is PER BUILD IDENTITY, not per job (§1 rules 3/4, §4 item 4, §5
  arming, checklist 4/12b). MAJOR: frozen-sibling reconciliation — the two
  "SIBLING AMENDMENT REQUIRED (C4 — verify at next round)" notes converted
  into ONE consolidated "RECONCILE AT POST-FREEZE CONSISTENCY PASS (C4 is
  v1.3 FROZEN)" note (§6); stale version pins bumped (C4 v1.3, C3 v0.4, C1
  v1.3 in the normative body); C4's reciprocal stage-3/4 writer request
  marked ALREADY SATISFIED in §6. MINOR: §1 rule 3 state-locus
  contradiction fixed — ceiling + ledger mirror into `build.json` on every
  write, the retained scan port-set is IN-MEMORY provider state ONLY
  (ports, never values) and is NOT written to `build.json`. MINOR: §0
  glossary `provision(instances=...)` cite corrected —
  `hosted_scheduler.py:819`/`:824` (start), `:1114`/`:1119` (reconcile);
  `:1836` is the `WorldPool(instances=parallelism)` constructor arg in
  `hosted_entrypoint.py`. NIT: §4a authoritative-endpoint-set cite fixed to
  `:1583-1587` (`:1576-1582` is comment); `:1591-1594` merge cite kept.
  **Post-freeze cross-contract consistency-pass reconciliation list
  (frozen):** (1) C4 must reclassify `port_not_consumable` as a terminal
  job-failure reason (still C4-surfaced, FE failure banner), dropping it
  from the `degrade_events` vocabulary / ingestion degrade-validator set
  (`hosted_harness_ingestion.py:579`) and the guest `DegradeReason` enum
  (`outbound.py:581-583`) — both detection loci and the formula-port
  stale-squat exclusion carry into that pass; (2) C4's `world_start_failed`
  producer row must carry the first-build/digest-rebuild-branch-only
  scoping (reconcile-call failures are scheduler down-markers, never ledger
  entries); (3) C4 v1.3 §7's still-open observation that C2 omits
  `[::1]:<declared>` from the degrade tier is stale — C2 has carried all
  three loopback forms since v1.2 (SATISFIED on C2's side).
- v1.2 (2026-08-31): review round 2 (12 findings applied) + C4 v1.2 /
  C1 v1.2 sibling adoptions. MAJOR: admission inputs DECIDED
  runtime-observed — the guest reads the sandbox's ACTUAL resources at
  provision start (cpu via an `os.cpu_count()`-equivalent, memory via
  the cgroup/proc limit); rationale: the gateway sizes the sandbox from
  job.json only in dockerfile mode (`hosted_harness_gateway.py:918-929`),
  the snapshot lane (`:931-935`) launches with NO `Resources` — declared
  values are not the box's real size in production; the gateway-mirror
  formula demoted to a dockerfile-lane explanatory note; declared values
  keep the platform pre-checks and the guest `W ≤ cpu_units` gate
  (declared), MIN of observed/declared binds W′; worked example redone on
  observed values (§2, checklists 2-3, OD-1, plan-deviations block).
  C4-sibling amendment adopted: `[::1]:<declared>` joins the DEGRADE
  tier — all three loopback forms; warn tier = any OTHER loopback
  literal, non-declared port (a declared-port `[::1]` literal is exactly
  what degrade-to-1 cures, so it belongs in the degrade tier; §4 item
  2a, §7 rows). §6 writer enumeration COMPLETED: stage-3 `fixed_port`
  (pipeline-appended when `plan_ports` forces effective 1, `:245-246`)
  and stage-4 `conformance_gate_failed` (genuine gate return,
  `:4591-4593`) writers added, plus the latch no-append rule (the
  `:4594-4599` conformance latch is a legacy-field re-stamp, never a
  ledger append) — aligned with C4 §2. Reconcile plan stability DECIDED
  (§1 rule 4, aligned with C1 v1.2's freeze locus): `plan_ports` runs
  ONCE, on the first `provision()` call, with the post-stage-2 ceiling;
  the reconcile branch (`:4548-4554`) carries the first call's plan
  forward unchanged (both ambiguous readings killed; checklist 4).
  Reconcile symmetry DECIDED (§5 rule 2 scoped FIRST-provision-call-only;
  new rule 2R): on reconcile calls a rebuild failure at ANY world index
  re-raises to WorldPool (`hosted_scheduler.py:1121-1152`) →
  down-markers + bounded M5 retry, job continues on healthy worlds, NO
  ledger entry, NO ceiling change, NO teardown of healthy worlds
  (leased-world survival `:1217-1222`); v1.1's reconcile-path orphan
  rule DELETED (its stranding scenario cannot arise); checklists 10/10b
  + §7 rows updated; C4 sibling note added (first-call scoping of the
  `world_start_failed` producer). MINOR: plan-deviations block added
  (world-0-failure scoped to the first provision call; admission inputs
  observed-not-declared); per-ref resolution gap DECIDED (an alias with
  no value in the loaded file = typed JOB FAILURE, fail closed — §4 item
  1b, §7, checklist 9b); digest-rebuild scan staleness DECIDED (retained
  scan result = literal loopback PORT set, ports only never values;
  re-classified against the NEW manifest's declared ports on rebuild —
  newly-declared port → ceiling 1 pre-plan, `literal_local_endpoint`,
  dedup applies; §1 rule 3, §4 item 4, checklist 12b); §4a trigger
  DECIDED (injected present AND rendered present AND differing;
  injected-present/rendered-absent = no warning), C3-citation overreach
  fixed (C3 §2.2 scopes the not-a-capability-variable claim to
  `LIVEKIT_AGENT_NAME`; the `FI_*` generalization stated as C2's own
  verified claim, `:1580-1587`), warn-tier scope aligned to C1 v1.2
  (ANY variable; "non-authoritative" dropped), both-tiers W-scoping made
  explicit in §4. Sibling adoptions from C1 v1.2 §4: the gate-stage
  declared-port LISTENER check admitted as a second `port_not_consumable`
  producer (§1 row 4, §6 writers — with a C4-verify note), the
  bind-evidence formula-port exclusion, and "knob-bearing WORKER
  process" phrasing (§5 rule 3, §7 row). NIT: "published handles"
  wording fixed — publication is conditional (`:4683-4687`,
  `:4697-4699`), `_teardown_world` tolerates absent worlds (§5 intro,
  rules 2/3, checklist 10); serialization precondition stated (provider
  state assumes WorldPool's `_provider_lock` single-writer,
  `hosted_scheduler.py:818, :1113` — §1 rule 3); §7 partial-failure rows
  use `E` (post-stage-5) instead of `W′` per the glossary's distinction.
- v1.1 (2026-08-31): review round 1 + C1 v1.1 sibling amendments. Sizing
  cite corrected to `services/hosted_harness_gateway.py:923-924` (was
  `serializers/harness_job.py`). Admission memory DECIDED sandbox-actual:
  `sandbox_gib = max(4, ceil(memory_mb/1024))` mirrors the gateway formula
  verbatim; mb→gib conversion defined; worked example redone
  (memory_mb=1024 → 4 GiB → mem_fit 3 → W′=3); "exact, not a proxy"
  scoped to cpu only. §6 competing ledger shape DELETED — C4 §2 owns the
  transport (`degrade_events`, `{reason, from_w, to_w}`); OD-2 closed
  "resolved: C4's shape"; C2 §6 keeps writer-side rules only, with the
  dedup rule (repeat failure updates `to_w`, never a second entry;
  invariants bind at all times) and the emission split (C′ emits the
  ledger once at entrypoint emission; post-emission updates surface via
  the scheduler's typed codes, `hosted_scheduler.py:1121-1152` —
  provisioning clamps vs mid-run attrition domain split). New §4a
  spawn-time override warning (four guarded keys, merge site
  `process_runtime.py:1591-1594`, authoritative-endpoint carve-out) —
  the section C1 §3/§4 and C3 §2.2 point at. §5 rule 2 gains the orphan
  rule (reconcile repeat failure at world j tears down ALL worlds ≥ j,
  healthy ones included, before returning the 0..j−1 prefix). Digest
  rebuild copy-forward pinned (`:4536-4541`): ceiling + ledger carried
  into the fresh build_output, monotone non-increasing; state locus =
  provider instance mirrored to build.json; per-attempt reset. World-0
  job-failure rule SCOPED to the first provision call (`pool.start()`);
  reconcile raises are caught by WorldPool with typed down-markers and
  the job continues. Latch made explicit provider state (effective-
  ceiling integer, mirrored on every write); the gate-branch catch sets
  it directly and stamps NO synthetic gate verdict (conformance-False
  latch reserved for genuine gate failures). Secret-resolution ERRORS
  fail closed as a typed JOB FAILURE (§4 item 1b, §7 row, checklist 9b).
  Emitter attribution fixed: emit side is Track C′, build.json writer is
  Track B. Stage table reworked: row 4 excludes `:4579-4580` (stage-5
  catch sites) and produces `conformance_gate_failed` only;
  `port_not_consumable` comes ONLY from the stage-5 gate-branch catch per
  C1 §4's bind-evidence rule. Serializer W-reject reworded: literally
  universal by set inclusion (all four connector choices enumerated,
  `harness_job.py:206`, `:72`). `resource_limited` AND
  `world_start_failed` both noted as routinely settling above 1. Sibling
  adoptions: TWO-TIER pre-plan scan (degrade tier only on
  declared-port literals; warn tier for other localhost/127.0.0.1/[::1]
  literals — warn + run) and the observed-real-job replay
  (`localhost:18090`) moved to the warn tier (checklist 5b).
- v1.0 (2026-08-31): initial draft implementing parallelism-plan-v10 —
  five-stage decision pipeline with per-attempt latch, admission math
  (W′ formula, job-declared inputs, `resource_limited` anchor), guest
  universal rule recorded KEPT, pre-plan secret scan mechanism + fallback +
  non-conformant timing, partial-failure catch at both `_ensure_world`
  sites with world-0 job-failure rule and no-renumbering, assert-only
  scheduler invariant, build.json degrade ledger, amendments A-C,
  conformance checklist, OD-1..3.
