# C1 — World Port Model — v1.3 (FROZEN)

**Boundary**: bundle authoring (`bundle_author_v2.py` / `bundle_v2.py`, Track A′)
↔ port plan (`process_runtime.plan_ports` / `PortPlan`) ↔ in-sandbox
provisioner (`ProcessRuntimeProvider`, Track B). Producer-facing rules land as
named amendments to `bundle-producer-contract.md` (owner: Rishav) — §8 below.

**Status**: v1.3 FROZEN, 2026-08-31 (post review round 3 — this is the FINAL
round; the contract FREEZES with this version, no round 4). Any further change
is a new-version reopening, not a review round. Cross-contract reconciliation
items dispatched by this version's decisions (the SIBLING AMENDMENT REQUIRED
notes below, chiefly decision 2's terminal-not-degrade reclassification of
`port_not_consumable`) are PENDING the post-freeze cross-contract consistency
pass. Implements the decisions of `parallelism-plan-v10.md` (§1 decision
record, §3 worker-knob mechanism, P0-C C1 row, §7 risk rows). This document
does not re-decide anything the plan decided; where the plan left a choice
open it is marked **OPEN DECISION**. Every `file:line` below was re-verified
against the working trees on 2026-08-31 (`agent-learning-kit` at
`src/fi/alk/harness/`, `ride-voice-agent`, and the plan's evidence directory).

**Companion contracts**: C2 (`c2-replication-admission.md` — owns the
pre-plan secret scan mechanism, the W′ admission math, and the
`_ensure_world` catch sites; its §1 pipeline is the ordering every "before
`plan_ports`" claim below refers to), C3 (call affinity — dispatch-ack), C4
(degrade & surfacing — owns the reason vocabulary transport end-to-end and
deployment order). C1 defines the port model those contracts assume. Where
a revision finds a sibling draft diverging from a C1 decision, the
divergence is flagged inline as **SIBLING AMENDMENT REQUIRED** rather than
silently papered over; siblings amended concurrently carry
verify-at-next-round notes instead.

**Plan deviations (recorded deliberately)** — mirror of C3's block:

- Plan §1's premise "the agent's tools API declares a fixed port (18090)"
  is corrected: 18090 is only the compose HOST mapping's default
  (`ride-voice-agent/docker-compose.yml:30`,
  `"${TOOLS_PORT:-18090}:8080"`); the declared (container listen) port is
  **8080** — which is what authoring hardcodes into `fixed_port` (§1).
  The observed literal `localhost:18090` therefore matches no declared
  port and is warn-tier (§5.4b).
- The plan's W=1 carve-out phrasing ("requested **or degraded-to**") is
  NARROWED to plan-time: only a plan made at effective 1 honors the
  declared port; a mid-provision degrade keeps the frozen formula-port
  plan (§2).
- The flagship observed job's outcome FLIPS vs the plan's "keeps working
  at W=1": under §5.4's tiers it RUNS at requested W=4 with a warning —
  it never degrades (18090 is declared nowhere; §5.4b, checklist 13).

---

## 0. Glossary

| term | meaning |
|---|---|
| W / requested | `job.runtime.parallelism` (spine §1; `hosted_entrypoint.py:134` returns it raw, never clamped) |
| effective W | the world count actually provisioned, after admission (C2), the pre-plan secret scan (C2), `plan_ports`, and the conformance gate |
| declared port | the integer in a `SourceProcess.fixed_port` declaration (`bundle_v2.py:180`) |
| allocated port | a formula port: per-world `15000 + 100*world_index + ordinal`, job-shared `14000 + ordinal` (`process_runtime.py:166-168, 224-230`; spine §2b) |
| env-consumable | the process binds whatever listen port its rendered environment tells it to — the declared port is only its compatibility default |
| code-fixed | the listen port is hardcoded in the repo's code; no environment value can move it |
| command-fixed | the listen port is fixed in the process's `run_command` — e.g. a Dockerfile exec-form `CMD` carrying `--port 8080`, copied verbatim into `run_command` by `_dockerfile_run` (`bundle_author_v2.py:456-491`); no environment value moves it; movable only by rewriting the command. For C1's split it behaves as code-fixed until the command is rewritten |
| plan-time (effective) W=1 / W>1 | the plan's EFFECTIVE instance count, computed INSIDE `plan_ports`: the `instances` value it is called with — requested W after the C2 admission clamp and the C2 pre-plan scan, which run BEFORE it (`c2-replication-admission.md` §1 rule 4) — further forced to 1 by any code-fixed port in the manifest (§1/§2). The PortPlan freezes at plan time; later mid-provision degrades never re-plan (§2) |

---

## 1. The consumability split

Spine §2b defines `fixed_port` as a listen port "the environment cannot
override" (`hosted-execution-seams.md:465-467`). C1 splits that single
concept in two. The split is declared in the manifest, on `SourceProcess`:

```
fixed_port: int | None            # unchanged (bundle_v2.py:180)
fixed_port_consumable: bool = False   # NEW field, beside it
```

| declaration | meaning | behavior at requested W>1 |
|---|---|---|
| `fixed_port: null` | no port opinion (e.g. platform-authored LiveKit control, `bundle_author_v2.py:746`) | allocated formula port, all worlds — already true at HEAD |
| `fixed_port: P, fixed_port_consumable: false` | **code-fixed** — today's semantics, unchanged | degrade to effective W=1, reason `fixed_port` — today's behavior (`process_runtime.py:245-246`), loud, never silent |
| `fixed_port: P, fixed_port_consumable: true` | **env-consumable** — the process binds its rendered port; `P` is the compatibility port honored at plan-time W=1 (§2) | parallelizes: every world gets an allocated port (§2) |

Rules:

- `fixed_port_consumable: true` with `fixed_port: null` is a validation
  error, named `fixed_port_consumable_requires_fixed_port` (the flag
  qualifies a declaration; there is nothing to qualify).
- The field exists on `SourceProcess` only. `ManagedProcess` has no
  `fixed_port` (`bundle_v2.py:153-166`) and gains nothing here.
- **Mixed bundles are classified** (DECIDED, this revision): a bundle
  carrying BOTH a code-fixed port and a consumable declaration plans at
  effective 1 — the code-fixed port forces `effective_instances = 1`
  inside `plan_ports` (reason `fixed_port` at requested W>1) — and the
  consumable process's declared port IS honored: the consumability
  iff-rule keys on the plan's EFFECTIVE instances (§2), and code-fixed
  forcing is a plan-time producer of effective 1. This matches today's
  behavior exactly (`fixed_ports` collects ALL declared ports,
  `plan_ports`, `process_runtime.py:236-240`; `port_for` returns them,
  `:225-226`).
- Default `false` preserves today's semantics for every existing sealed
  manifest byte-for-byte: no reseal, no behavior change at any W.
  **Serialization rule (normative — enforcement site pinned this
  revision)**: the field is omit-when-default — a `false` value is never
  written — and the omission MUST hold in the exact
  `model_dump(mode="json")` that `seal_bundle_v2` consumes
  (`bundle_v2.py:654-657`; its docstring warns that adding an optional
  field re-keys every previously sealed bundle) and MUST round-trip
  through `load_bundle_v2`. So re-serializing an existing sealed manifest
  reproduces its bytes and digest unchanged (checklist 3). Non-normative
  hint (CORRECTED this revision): a pydantic v2 `@field_serializer` CANNOT
  drop a key from `model_dump` — it only rewrites that field's VALUE, so it
  cannot achieve omit-when-default. The omission needs either a model-level
  `@model_serializer` that rebuilds the dict without the defaulted key, or
  a dump-site exclusion applied where `seal_bundle_v2` calls
  `model_dump(mode="json")` (`bundle_v2.py:654`) — e.g. `exclude_defaults`
  or an explicit `exclude` set. Either is acceptable; the field-serializer
  shape named in prior revisions is not.
- **Duplicate declared ports are a preflight reject**, new code
  `fixed_port_duplicate`: two `SourceProcess`es declaring the same
  `fixed_port` value cannot both bind it in the shared network namespace.
  Today nothing checks this — `_verify_fixed_port_not_reserved`
  (`process_preflight.py:534-549`) checks only band membership — so the
  collision survives preflight and dies at spawn-time bind. The reject
  lands beside `fixed_port_reserved`, regardless of consumability flags
  (at plan-time W=1 both would bind the same port; at plan-time W>1 a
  consumable pair is formula-allocated, but the declaration is still a
  contradiction and stays rejected).
- **Deployment order**: `SourceProcess` is `extra="forbid"`
  (`bundle_v2.py:170`), so a manifest carrying the new field fails
  validation on a pre-C1 guest. The guest (ALK) MUST deploy before any
  producer emits the field — this is the same snapshot-digest pin C4
  already requires for the FE W>1 control (plan §3 P0-C row, pin ii).
- **Authoring MUST NOT declare consumable without wiring it.** Declaring
  `fixed_port_consumable: true` obligates the author to wire the process
  so its **run command** actually consumes the rendered per-world port.
  Preflight enforces this with a new code `fixed_port_consumable_unwired`
  (lands beside `fixed_port_reserved`, `process_preflight.py:534-549`).
  **The exact check (DECIDED this revision — one clause only)** — the
  declaration passes iff the `{{PORT_<own-name>}}` token appears **in the
  value of an `environment` key that the `run_command` itself references**
  (`$KEY` / `${KEY}` inside an `sh -c` argument — producer contract §2.3's
  pattern). This is the ONLY valid consumability wiring. The earlier
  clause "(i) the token in an element of `run_command`" is **DELETED**: it
  was invalid. `run_command` is **copied verbatim and never
  template-rendered or shell-expanded** — `_provision_sync` does
  `command = list(process.run_command)` (`process_runtime.py:1597`) with no
  render pass; only `render_environment` (`process_runtime.py:423-445`,
  called at `:1558`) resolves `{{...}}` tokens, and it covers
  `process.environment` exclusively, never `run_command`. A
  `{{PORT_<self>}}` token parked in a `run_command` argv would therefore be
  exec'd as the literal bytes `{{PORT_<self>}}` — the producer contract
  §2.3 pins "argv exec'd directly, no shell, `$VAR` not expanded" — so it
  can never carry a port. There is likewise NO "known readers" escape
  hatch — the sole clause is satisfied by the command's `$KEY` reference to
  a token-bearing env value, never by out-of-band knowledge of what a
  process's code reads. (The tool-proxy is not an example: it declares no
  `fixed_port` (`bundle_author_v2.py:503-517`), so this check never applies
  to it — and its `run_command` references no env key, so it would NOT
  pass either.)
  Env-token presence alone is likewise an **insufficient proxy**: a token
  sitting in an env var the command never references moves nothing — the
  command-fixed `--port 8080` argv (tools-api, below) would ignore it and
  the flag would be a lie the sandbox discovers only at effective W>1, via
  §4's gate declared-port listener check.

**The known instances, named** (P0-V verified):

- `tools-api` / `api`: the SERVER bind is **command-fixed today**. The
  repo's Dockerfile pins it in the command
  (`ride-voice-agent/tools-api/Dockerfile:9`,
  `CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]`),
  and `_dockerfile_run` copies that argv verbatim into `run_command`
  (`bundle_author_v2.py:456-491`, wired via `run_override` at `:671`);
  authoring separately hardcodes `port = 8080` (`:659`) into `fixed_port`
  (`:372`). No environment value moves this bind. **Concrete required
  change (Track A′, this revision):** rewrite the `run_command` to the
  §2.3 `sh -c` form —
  `["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port $FI_TOOLS_PORT"]` —
  and author an `environment` entry `FI_TOOLS_PORT: "{{PORT_tools-api}}"`.
  The command references the token-bearing env value by `$FI_TOOLS_PORT`,
  which is exactly (and only) what the single-clause
  `fixed_port_consumable_unwired` check accepts; the flag flip alone,
  without this command rewrite + env entry, is precisely what the check
  rejects. This rewrite + flip is what un-degrades the observed real job's
  W=4 request.
- The reference agent (ride-voice) consumes its tools endpoint from env —
  `TOOLS_API_URL`, default `http://localhost:18090`
  (`ride-voice-agent/agent/agent.py:188`) — and its dispatch name from env
  (`agent/agent.py:119`). `agent.py:188` is the **client** side only: it
  says the caller follows the rewritten endpoint, nothing about the
  server's bind. The earlier v1.0 reading of it as evidence of server-side
  env-consumability was wrong.

---

## 2. The allocation rule

Pinned by plan §1, scoped to **plan-time W** — the `instances` value
`plan_ports` is called with (the post-clamp/post-scan ceiling,
`c2-replication-admission.md` §1 rule 4), NOT the effective W a later
mid-provision degrade may settle on. `plan_ports`
(`process_runtime.py:233-247`) is the single change site; `PortPlan.port_for`
(`:224-230`) is the single read path — everything downstream (env tokens,
capability addresses, `started_check` probes, spawn handles) already goes
through it and MUST continue to.

**At plan-time W>1** (only reachable when no code-fixed port exists, the
admission clamp left the ceiling above 1, and the pre-plan scan found no
declared-port literal — `c2-replication-admission.md` §1 stages 1-3):

- EVERY world, **including world 0**, gets the allocated formula port for a
  consumable process. World 0 is not special-cased to the declared value.
- The declared port value is **excluded from the allocator's output**. This
  is structural, not a runtime search: allocated ports live only in the
  reserved bands `[14000,14100) ∪ [15000,15800)` and a legal `fixed_port`
  may not be inside those bands or the rabbitmq-management shifts
  `[24000,24100) ∪ [25000,25800)` (`fixed_port_reserved`,
  `process_preflight.py:136-152, 536-547`; spine §2b v1.15). No formula
  output can ever equal a legally-declared port. `plan_ports` MUST still
  assert the exclusion per plan (belt against a future band edit).
- Consequence (the guarantee §5 leans on), CONDITIONED on truthful
  consumable wiring: after a plan at W>1 **nothing in the sandbox
  listens on the declared port** — for the job's whole lifetime,
  including after any mid-provision degrade (below). A stray literal
  reference to a declared port fails LOUD — connection refused — never
  silently cross-wires to another world. **Residual, recorded**: a
  syntactically-passing but LYING declaration — the run command
  references the token, the server binds the declared value anyway, and
  a tolerant `log_marker` `started_check` never probes the port — leaves
  world 0's copy listening on the declared port, so a stray literal
  cross-wires into world 0 undetected by §1's syntactic check. §4's gate
  declared-port listener check is the runtime enforcement, and its
  resolution is a **terminal job failure** (`port_not_consumable`), NOT a
  degrade — world 0 is already internally inconsistent and the frozen plan
  cannot re-consist it (decision 2, §4).

**At plan-time effective W=1** — requested W=1, the C2 admission clamp
forcing 1, the C2 pre-plan scan forcing 1 (all BEFORE `plan_ports`), or a
code-fixed port forcing `effective_instances` to 1 INSIDE `plan_ports`
(the mixed-bundle path, §1) — the declared port is honored **exactly as
today**: `port_for` returns the
declared value for the consumable process, `{{PORT_<name>}}` renders it,
the process binds it, and literal references to it keep working. The
carve-out is **plan-time only**; it is what makes the pre-plan scan's
degrade-to-1 a genuine cure (§5a).

**Mid-provision degrades do NOT re-enter the carve-out.** A graceful
degrade to effective 1 that lands AFTER the PortPlan froze — the
`conformance_gate_failed` gate branch (§4) or C2's `world_start_failed` —
never re-runs `plan_ports`: world 0 stays on its formula port. This is
**harmless by construction and documented as such**: every env reference in
world 0 (its own `{{PORT_<self>}}`, every capability address) was already
rewritten to that formula port (§3), so the surviving world is
self-consistent; the declared value simply remains unbound, and §5's
guarantees continue to hold. (C2 §4's non-conformant-timing rule —
reconcile never rebuilds world 0 — is the same fact seen from the other
side.) **`port_not_consumable` is NOT in this set (decision 2, §4):** it is
a TERMINAL JOB FAILURE, not a graceful degrade — a consumable-declared
process that binds its declared port instead of its allocated formula port
leaves world 0's own consumers and capability URLs (already rewritten to
the unbound formula port) internally inconsistent, and the frozen plan
cannot re-consist world 0 (no mid-flight re-plan). The job dies loud (§4);
world 0 is not salvaged into a W=1 run.

Implementation shape (normative on Track B): within `plan_ports`, only
code-fixed ports force `effective_instances` to 1; `PortPlan` then treats
a consumable declaration as fixed **iff** the plan's EFFECTIVE instance
count — computed inside `plan_ports`, AFTER code-fixed forcing — equals
1. It never keys on the requested `instances` argument: in a mixed
bundle the code-fixed forcing lands the plan at effective 1 and the
consumable declaration IS honored (§1). **The plan is frozen PER BUILD
IDENTITY** — it is never re-derived by any later degrade on the reconcile
path. It IS legitimately re-derived by a same-job-identity **digest
rebuild**: the first-call/digest-mismatch branch
(`process_runtime.py:4454`) tears the prior build down and, at
`:4478-4500`, constructs a fresh `SpawnContext` carrying the freshly
computed `port_plan` (`plan_ports`, `:4450`); the effective ceiling and
degrade ledger carry forward per C2 §1 rule 3 (build_output replaced,
`:4536-4541`), monotone non-increasing. That is NOT a contradiction of the
freeze: freeze binds the RECONCILE path, the digest rebuild is a new build
of the same job identity. **Freeze locus, pinned (Track B change)**: the
reconcile branch that today REPLACES `context.port_plan` on every later
`provision()` call (`process_runtime.py:4548-4554`) MUST instead carry the
FIRST call's `port_plan` forward unchanged; C2 §1 rules 3-4 (`plan_ports`
receives the latched post-stage-2 ceiling on every call) are the companion
guarantee — belt and suspenders, both required.
`degraded_reason: "fixed_port"` is emitted for code-fixed ports only, and
only when requested W>1 (spine §2b v1.15; payload bound
`1 <= effective < requested`, `outbound.py:630-638`).

`started_check` with `port: true` probes "the process's OWN allocated port
(the port formula, or its `fixed_port`)" (spine §2b:519-523) — it reads
`port_for`, so it follows this rule with no separate change. Same for the
spawn handle's `port` (`process_runtime.py:1623`).

---

## 3. The env rewrite — extends the existing mechanism, never a parallel one

C1 introduces **zero new rewriting machinery**. Both existing channels
already render per-world values through `port_plan.port_for`; the §2 rule
makes them emit per-world values for consumable processes automatically:

1. **Env tokens** `{{PORT_<name>}}` / `{{HOST_<name>}}` — resolved by
   `render_template` via `port_plan.port_for(name, world_index)`
   (`process_runtime.py:397-410`; HOST is always `localhost`, `:400-401`).
   Closed vocabulary per spine §2b and producer contract §2.4; nothing new
   is added to it.
2. **Capability URLs** — `build_endpoints` computes each capability's
   address from `port_for(capability.service, world_index)`
   (`process_runtime.py:326-352`, port at `:339`) and processes consume it
   as `{{<CONFIGURATION_NAME>}}` (e.g. `TOOLS_API_URL` via the
   `tool_proxy` capability, `bundle_author_v2.py:722-727`; wired into the
   control process's environment at `:643-644`).

Producer obligation (amendment, §8): every reference from one process to
another's port MUST go through a token or a capability name — never a
literal. An env-consumable process additionally references its own
`{{PORT_<self>}}` (§1). Anything literal is outside the rewrite and gets
§5's failure shape.

Spawn-time merge order, stated because it is part of the endpoint identity
model (verified, `process_runtime.py:1583-1595`): `build_environment` <
rendered `environment` < injected job secrets < **authoritative
endpoints** — capability variables that the process's own `environment`
declares are re-asserted to the world-correct address AFTER secret
injection. So a job secret aliased `TOOLS_API_URL` cannot poison a
platform-authored control process's tools endpoint; injected secrets win
only for variables the process does not declare (that residual is §5's
subject, and the loud-not-silent `LIVEKIT_AGENT_NAME` case of it is C2's
spawn-time override warning — now legislated at
`c2-replication-admission.md` v1.3 §4a; see §4's sibling note).

---

## 4. Worker-knob env mandate (producer contract) + non-conformance failure shape

livekit-agents 1.7.1 exposes `load_threshold`, `num_idle_processes`, and
the worker health port as **WorkerOptions constructor arguments only** —
no env-var and no CLI override exists (verified against the genuine 1.7.1
wheel, `p0b_results.md`: defaults `load_threshold=0.7` at `worker.py:148`,
`num_idle_processes=min(cpu_count,4)` at `worker.py:208`, health port
8081; zero `getenv` hits for any of them). The harness can only rewrite
env. Therefore C1 delivers the knobs as **harness-set env vars** and the
producer contract MANDATES that a conformant agent read them into its
`WorkerOptions` / `AgentServer` construction.

**Knob-bearing worker — operational definition (DECIDED this revision).**
A process is **knob-bearing iff its rendered `environment` contains the key
`FI_WORKER_HEALTH_PORT`**. This is the single checkable rule both tracks
use: authoring (Track A′) writes the `FI_*` trio — `FI_WORKER_HEALTH_PORT`
included — into every LiveKit-worker process (below), so the presence of
that key IS the mark; runtime attribution (Track B, §4's failure shape)
identifies a knob-bearing process by that same env key, never by guessing
"is this a worker". "control process" is NOT the test — any process
carrying `FI_WORKER_HEALTH_PORT` is knob-bearing; a process without it is
not.

**The variables** — authored unconditionally (not `setdefault`) by
Track A′ into the `environment` of **EVERY LiveKit-worker process in the
bundle** (not only a "control" process — any process running a LiveKit
worker is knob-bearing), each fed its **own** `{{PORT_<name>}}`:

| variable | authored value | conformant agent MUST |
|---|---|---|
| `FI_WORKER_HEALTH_PORT` | the worker process's OWN `{{PORT_<name>}}` (the existing token mechanism; every process has an ordinal, `process_runtime.py:170-174`, so this renders per-world at any W) | `int()` it into the worker health/HTTP port option |
| `FI_LOAD_THRESHOLD` | the string `inf` | `float()` it into `load_threshold`. Rationale (plan §3): the shedding signal is SYSTEM CPU, identical for all W workers in one sandbox — per-worker thresholds differentiate nothing and any spike sheds every world at once; C2's admission W′ math is the sole intended throttle |
| `FI_NUM_IDLE_PROCESSES` | **OPEN DECISION** — the plan mandates the variable but not the value. Options: `0` (no prewarm; slowest first dispatch), `1` (bounds idle memory at W idle processes instead of the default's up-to-16 at W=4), or a Track-D-calibrated value. Until decided, author `1` in the dev/E2E lane so the ladder exercises the read path | `int()` it into `num_idle_processes` |

A missing variable (older guest, local lane) MUST leave the agent on
library defaults — the mandate is "read when present," so a conformant
agent stays runnable outside the harness.

**Variable-name lockstep (normative)**: `FI_WORKER_HEALTH_PORT` is the
**byte-exact** env var name the conformant agent reads; its value is each
worker process's own `{{PORT_<name>}}` allocation. Any drift —
`FI_HEALTH_PORT`, `WORKER_HEALTH_PORT`, a paraphrase in a checklist — is
a silent conformance hole (the agent falls back to 8081 and §4's failure
shape fires). **SIBLING NOTE (C3 — v0.4 FROZEN; post-freeze pass)**: C3
§5.2 and its worker-knob checklist row DESCRIBE the health-port variable
(`{{PORT_<name>}}`) rather than naming `FI_WORKER_HEALTH_PORT` verbatim;
the other `FI_*` names are already verbatim in C3 (§2.2, spawn-time-warning
checklist row). C3 is now frozen, so this verbatim-naming alignment is a
post-freeze cross-contract reconciliation item, not a next-round ask.

**Required reference-agent change** (P0-V finding, normative): ride-voice
constructs `AgentServer()` with all-default options
(`ride-voice-agent/agent/agent.py:72`) — it does NOT consume any of the
three variables today. It MUST be updated to read all three per the table
above before it can serve as the W>1 reference. (Its `TOOLS_API_URL` and
`LIVEKIT_AGENT_NAME` env reads at `agent/agent.py:188` and `:119` already
conform to §3 and to producer contract §2.7.)

**Non-conformance failure shape** (normative — this is the enforceable
half, since the mandate is unenforceable for arbitrary customer agents).

**`port_not_consumable` is a TERMINAL JOB FAILURE, not a curative degrade
(DECISION, this revision — corrects the earlier degrade-to-1 framing).** A
process FLAGGED consumable (or a knob-bearing worker) that LIES at
runtime — binds the declared port instead of its allocated formula port —
leaves world 0 **internally inconsistent**: world 0's own consumers and
capability URLs were rewritten to the formula port (§3) that nothing
binds, and the frozen plan cannot re-consist world 0 (no mid-flight
re-plan, per the freeze rule §2). There is nothing to degrade INTO — a W=1
salvage would require re-planning and rebuilding world 0 mid-flight, which
the freeze forbids. So BOTH detection paths below resolve to a **LOUD JOB
FAILURE, reason `port_not_consumable`**, carrying the diagnosis: *"the
agent is declared parallel-capable but did not honor its assigned port;
fix it to read its port env, or request parallelism=1 to run serially (the
declared port is then honored)."* This is sharply DISTINCT from the CLEAN
plan-time degrade of a NON-consumable `fixed_port` (code-fixed) — that
stays a graceful W=1 degrade with the declared port honored, reason
`fixed_port` (unchanged, §2/§6 row 1). Terminal failure vs graceful
degrade is the whole point of the split.

- **Bind-death path.** A lying process binds its declared port; at
  effective W>1 the second world's copy dies at bind (`[Errno 48]` class —
  log-evidenced in probe run 234 on the probe's own ports; runs 018/072
  are consistent-with but inference, plan §3). At effective W>1 the
  provisioner builds worlds 0 and 1 before gating
  (`process_runtime.py:4578-4587`); the world-1 build failure — bind
  death, or the worker process's `started_check` timeout — surfaces at
  the `_ensure_world` catch site (`:4579-4580`), which C2 owns. C1 pins
  the **attribution mapping** for a world-index ≥ 1 startup failure when
  world 0's copy of the same process started, driven by the failing
  process's log-tail bind-error evidence (the `[Errno 48]`/address-in-use
  class) — read the errored port number off the error line:
  - errored port is a **DECLARED `fixed_port`** value AND the failing
    process is **consumable-declared** → **`port_not_consumable`
    (TERMINAL JOB FAILURE)**, regardless of knob-bearing status (the
    fourth attribution arm, DECIDED this revision: a consumable process
    binding its own declared port is the defect, whether or not it also
    carries `FI_WORKER_HEALTH_PORT`).
  - errored port is a **non-formula, non-declared** port (e.g. the
    framework default 8081) AND the failing process is **knob-bearing**
    (carries `FI_WORKER_HEALTH_PORT`, per the operational definition
    above) → **`port_not_consumable` (TERMINAL JOB FAILURE)** — the
    worker ignored its `FI_WORKER_HEALTH_PORT` allocation and squatted
    the library default in every world.
  - errored port is a **formula-assigned** port → the **stale-squat**
    class (a prior worker still holding it), which is a graceful
    **`world_start_failed`** degrade, NEVER `port_not_consumable` (the
    anti-false-positive, retained). Squat windows are bounded by C3 §5.4's
    teardown escalation (SIGTERM drain 3600 s → SIGKILL,
    `_terminate_and_wait`) — cross-referenced as the bounding mechanism,
    not re-legislated here.
  - a knob-bearing process failing at world ≥ 1 with **NO bind-error
    evidence** → graceful `conformance_gate_failed` (a knob-conformant
    agent can die at world 1 for reasons unrelated to ports; mislabeling
    that `port_not_consumable` sends the author chasing the wrong fix).
  - any **other** (non-consumable, non-knob-bearing) process's failure →
    C2's graceful `world_start_failed` (`c2-replication-admission.md` §5
    rule 3 applies this by reference).

  The `port_not_consumable` arms are a terminal job failure — LOUD, never
  a hang (the `started_check` timeout, 180 s for authored LiveKit workers,
  bounds the wait), never silent. The two `world_start_failed` /
  `conformance_gate_failed` arms remain graceful degrades (§6).
- **Gate declared-port listener check (NEW, Track B — the enforcement
  half of §2's truthful-wiring condition).** At effective W>1, AFTER the
  gate worlds (0 and 1) are built, a **distinct provision step** verifies
  that NO listener exists on ANY declared `fixed_port` value (bind-probe
  or socket scan — either is conformant). A listener found → **terminal
  JOB FAILURE, reason `port_not_consumable`, LOUD** — the net for §2's
  lying-consumable residual (a declared value bound despite a
  syntactically-passing §1 check; the tolerant-`started_check` case that
  bind-death does not catch).
  **KNOWN RESIDUAL (frozen):** the check is point-in-time, not a watch, so
  two timing gaps stay open at freeze — a listener that binds in the window
  BETWEEN the gate-world build and this check, and a post-gate LATE binder
  (a process that binds its declared port only after the check ran). Both
  leave a declared-port listener undetected for that job. Closing them
  needs a continuous port watch, which is out of scope for v1; accepted and
  recorded, not fixed.
  **Locus decoupling (DECIDED this revision — MINOR):** because this is a
  terminal failure, the listener check MUST be a **distinct provision step
  that raises a terminal job failure directly** (the same shape as a guest
  admission failure), decoupled from the conformance machinery. It MUST
  NOT set `build_output.conformance = False`, and it MUST NOT make
  `run_conformance_gate` return a third string. It must break neither of
  two facts: (a) the reconcile-path conformance latch at
  `process_runtime.py:4594-4599`
  (`elif self._conformance_checked and build_output.conformance is False`)
  — stamping a synthetic `conformance = False` would make that latch
  re-stamp on every later `provision()` call; and (b) C4's closed
  two-constant gate-return vocabulary — `run_conformance_gate` returns
  only the constant `conformance_gate_failed` (`process_runtime.py:4084-4165`,
  comment `:4146-4148`), and a third return string would collide with
  that closed fact. The listener check touches neither; it raises its own
  terminal failure out-of-band.
- **`port_not_consumable` reason string.** Today's closed vocabulary is
  `conformance_gate_failed | fixed_port` on both ends
  (`outbound.py:581-583`; ingestion `hosted_harness_ingestion.py:576-587`).
  C1 contributes the string; C4 owns carrying reason names end-to-end. But
  with this revision's decision 2, `port_not_consumable` is a
  **terminal job-failure reason, NOT a `parallelism_degraded` degrade
  event / `degrade_events` ledger entry** — a cross-contract change from
  C2's and C4's current treatment of it as a degrade reason.
- **SIBLING AMENDMENT REQUIRED (C2 + C4 — terminal-not-degrade,
  post-freeze reconciliation).** `port_not_consumable` is a **terminal
  job-failure reason, NOT a `degrade_events` entry and NOT a
  `parallelism_degraded` event** (decision 2). This reclassifies it out of:
  C2 §1's stage-table row 4 / §5 rule 3 / §6 writer enumeration (where it
  is a gate-stage/gate-branch ledger producer) and C4 §2's reason table +
  §9 rows + §6 FE degrade copy (where it is a degrade-enum member). BOTH
  siblings must instead treat it as a terminal job failure carrying the
  diagnosis above (FE shows a failure banner, not a degrade notice — like
  the W′=0 world-0 job-failure case). This is dispatched to the
  **post-freeze cross-contract consistency pass** (C2 and C4 are amended
  there, not by this frozen document). The two `port_not_consumable`
  DETECTION paths (bind-death arm + gate listener check) and the
  formula-port stale-squat exclusion carry into that pass as well; the
  "knob-bearing WORKER (not control) process" phrasing and the
  operational `FI_WORKER_HEALTH_PORT` definition likewise.
- **SIBLING NOTE (C4 — v1.3 FROZEN; residual deltas for the post-freeze
  pass).** C4 v1.3 already ADOPTED the three-condition attribution rule
  verbatim (§2 `port_not_consumable` row) and the two-tier §9 rows —
  those asks are **SATISFIED (verify retained)**. The genuinely
  OUTSTANDING deltas, all deferred to the post-freeze pass, are:
  (i) control-vs-worker phrasing — C4 says "knob-bearing control process";
  C1 v1.2+ scopes it to any knob-bearing WORKER process
  (`FI_WORKER_HEALTH_PORT`-bearing);
  (ii) the formula-port stale-squat exclusion (bind evidence must name a
  NON-formula port) is not yet in C4's rule;
  (iii) the gate declared-port listener check as a SECOND
  `port_not_consumable` producer;
  (iv) decision 2's **terminal-not-degrade reclassification** — C4 still
  carries `port_not_consumable` as a degrade-enum member with FE degrade
  copy. Items (i)-(iv) are the live C4 residual (with C2's parallel
  reclassification), reconciled post-freeze.
- Load-shedding by non-conformant agents (threshold stays 0.7) is NOT
  C1's net: dispatch-ack (C3) catches the resulting dropped dispatches
  regardless of conformance (plan §3, run-178 evidence).
- Residual, recorded: job-secret injection overrides rendered env for
  non-capability variables (`process_runtime.py:1592-1593`), so a secret
  aliased `FI_*` can override the authored value. The spawn-time override
  warning's normative home is **C2** (C1 keeps that position); it
  legislates all FOUR guarded keys — `LIVEKIT_AGENT_NAME` plus the
  `FI_*` trio — as MANDATORY members of the same warning channel (the
  earlier SHOULD was modality drift: C2 §4a's warning covers all four as
  MUST). No new mechanism.
  **SIBLING NOTE (C2 — ADOPTED, v1.3 FROZEN)**:
  `c2-replication-admission.md` v1.3 §4a carries the spawn-time
  override warning with exactly the four-key set (`LIVEKIT_AGENT_NAME`,
  `FI_LOAD_THRESHOLD`, `FI_NUM_IDLE_PROCESSES`, `FI_WORKER_HEALTH_PORT`),
  all four as MUST; C3 references it as C2's. SATISFIED.

---

## 5. User-payload literal endpoints at W>1 — what the allocator guarantees

Users can smuggle literal endpoints through two channels templates cannot
rewrite: `environment_values` (platform-visible; Track E submit-time
WARN — warn-only, NEVER a reject in v1, per C4 §7's decision; fires for
W>1 requests only) and secret refs (write-only at the platform, resolve
only in-sandbox; guest-side **pre-plan** scan, C2/Track B —
`c2-replication-admission.md` §4, plan §1). Those detection mechanisms are
NOT C1's; C1 owns the TIER DECISION for the scan (item 4 below) and the
port-plan facts the mechanisms rely on — the guarantees, each already
pinned above:

1. After a plan at W>1, GIVEN truthful consumable wiring (§2's
   condition), no listener exists on any legally-declared
   `fixed_port` value — for the job's lifetime, mid-provision degrades
   included (§2 structural exclusion + no-re-plan rule): a literal
   reference to a declared port fails connection-refused, loud, from any
   process in the sandbox. Residual (§2): a LYING declaration can leave
   world 0 listening on the declared value until §4's gate declared-port
   listener check FAILS THE JOB loudly and terminally
   (`port_not_consumable` — a job failure, not a degrade; decision 2, §4);
   checklist 12 is conditioned the same way.
2. Allocated ports never collide with any legally-declared `fixed_port`
   (band-structural + the §2 belt assertion), so the failure in (1) can
   never silently become a cross-world hit.
3. Capability variables declared in a process's own `environment` are
   re-asserted world-correct over injected secrets at spawn (§3,
   `process_runtime.py:1583-1595`) — the platform-authored control
   process's `TOOLS_API_URL` cannot be poisoned by a secret alias.
4. **The pre-plan scan has TWO tiers** (decided at v1.1 — the v1.0 draft
   told two irreconcilable stories about the observed job).
   **W-scoping (pinned, this revision)**: BOTH tiers run only when the
   ceiling entering the scan stage exceeds 1 (C2 §1 rule 2's skip; C4 §7
   is likewise W>1-only) — at plan-time W=1 there is no scan and no
   warning: today's behavior exactly.

   **(a) Degrade tier — literals matching a DECLARED fixed port.** A
   `localhost:<P>` / `127.0.0.1:<P>` / `[::1]:<P>` literal where `<P>`
   equals some
   `SourceProcess.fixed_port` in the manifest (here: `localhost:8080`) →
   degrade-to-1 **at plan time**, reason `literal_local_endpoint`.
   Because the scan runs pre-plan (before `plan_ports`, before any world
   builds — plan §1's round-9 mechanism), the degrade enters §2's
   plan-time-W=1 branch: the declared port is honored and the job runs
   exactly as today. The cure is real, and only for this tier. (Plan
   fallback stands: if early resolution proves infeasible, the cure claim
   is dropped and the job fails loud with `literal_local_endpoint` as
   diagnosis — reason reserved by C4.)

   **(b) Warn tier — literals to NON-declared ports, in ANY variable
   (DECIDED, this revision).** A `localhost:<p>` / `127.0.0.1:<p>` /
   `[::1]:<p>` literal whose port matches no declared `fixed_port` (the
   observed real job: `TOOLS_API_URL=http://localhost:18090` — 18090 is
   declared nowhere; the declared tools-api port is 8080) → **warning
   only, W unchanged** — for the literal in ANY variable, authoritative
   or not: the warning text is GENERIC, and the scan does NOT evaluate
   per-process authoritativeness. Rationale: nothing binds a
   non-declared port at ANY W, so degrading to 1 cures nothing. Where
   the variable happens to be a capability variable the process's own
   `environment` declares (`TOOLS_API_URL`), the authoritative-endpoint
   re-assertion of guarantee (3) (`process_runtime.py:1583-1595`)
   additionally overrides the injected literal with the world-correct
   address — **the job RUNS at requested W** either way; an override
   note MAY be added to the warning later, but is not required in v1.
   The warning follows C2 §4's hygiene rule: alias + port only, never
   any other part of a secret value. Consequence, stated flat: the
   observed real job at W=4 runs (literal overridden + warned); it does
   NOT degrade.

   **SIBLING NOTE (C2 — two-tier ADOPTED in C2 v1.3 §4 + checklist
   5/5b; v1.3 FROZEN) — SATISFIED**: the three formerly-residual deltas are
   all carried by frozen C2 v1.3 — (i) the warn tier's scope is ANY variable
   (C2 v1.3 §4 says so; the scan never evaluates authoritativeness);
   (ii) `[::1]:<p>` belongs to BOTH tiers' patterns (C2 v1.3 carries all
   three loopback forms in both tiers); (iii) the both-tiers W-scoping is
   explicit (C2 §1 rule 2's skip). No C2 change owed.
5. Out of scope, recorded honestly: a literal that lands INSIDE a
   reserved formula band (e.g. `localhost:15001`) could reach a live
   listener. Formula ports are contract-reserved and job-unstable
   ("nothing else may assume a port", spine §2b), so no legitimate payload
   references them; neither the scan nor the allocator defends this.

---

## 6. Failure shapes at this boundary (closed)

| condition | shape | loud via |
|---|---|---|
| code-fixed `fixed_port` at requested W>1 | degrade to effective 1, reason `fixed_port` | `parallelism_degraded` event — today's behavior, unchanged |
| non-conformant agent (health port) at plan-time W>1 | world-1 bind death in the gate branch, `[Errno 48]`-class evidence in the knob-bearing worker process's log tail (carries `FI_WORKER_HEALTH_PORT`) naming a NON-formula port (e.g. default 8081) → **TERMINAL JOB FAILURE, reason `port_not_consumable`** (decision 2); a formula-port collision in the error line is the stale-squat class → graceful `world_start_failed` degrade (§4's anti-false-positive); worker failure WITHOUT bind evidence (incl. bare started_check timeout) → graceful `conformance_gate_failed` degrade (§4) | conformance-gate branch, C2 catch (§4); job-failure diagnosis names the assigned-port fix or parallelism=1 |
| LYING consumable declaration (passes §1's one-clause check; server binds the declared value at effective W>1) | detected as world-≥1 bind death naming a DECLARED port by a consumable-declared process (fourth attribution arm) OR by the gate declared-port listener check after the gate worlds build → **TERMINAL JOB FAILURE, reason `port_not_consumable`** — world 0 is internally inconsistent, no W=1 salvage (decision 2; residuals: pre-check window, post-gate late binders — §4) | gate stage / bind-death catch, Track B (§4), LOUD job failure |
| literal declared-port reference after a plan at W>1 | connection refused from any process in the sandbox — scenario fails with the connect error | nothing listens on the declared port (§2) |
| declared-port literal in secret refs, detected pre-plan | degrade to 1 at plan time, reason `literal_local_endpoint`; declared port honored, job works | C2 scan degrade tier (§5.4a) + C1's plan-time W=1 carve-out |
| non-declared loopback literal in secret refs, ANY variable (the observed `localhost:18090`) | GENERIC warning only (alias + port), W unchanged — where the variable is a declared capability variable, the world-correct re-assertion additionally overrides the literal; job RUNS at requested W | C2 scan warn tier (§5.4b) + §3's authoritative-endpoint re-assertion |
| `fixed_port_consumable: true` without `fixed_port` | manifest validation error `fixed_port_consumable_requires_fixed_port` | model validator |
| two `SourceProcess`es declaring the same `fixed_port` value | preflight reject `fixed_port_duplicate` (today: survives to a spawn-time bind collision — `_verify_fixed_port_not_reserved`, `process_preflight.py:534-549`, checks only band membership) | `preflight_bundle`, before provisioning (§1) |
| consumable process whose run command does not reference an `environment` value carrying `{{PORT_<self>}}` (the ONLY clause — `run_command` is never rendered, §1) | preflight reject `fixed_port_consumable_unwired` (§1's one-clause check) | `preflight_bundle`, before provisioning |
| new-field manifest on a pre-C1 guest | validation failure (`extra="forbid"`) | deployment-order pin (§1) — MUST NOT arise in production |

Nothing at this boundary is permitted to hang or to fail silently.

---

## 7. What C1 does NOT own

- The pre-plan secret scan mechanism, admission W′ math, `_ensure_world`
  catch implementation → **C2** (`c2-replication-admission.md` §4, §2,
  §5 — two-tier adopted at C2 v1.3; §5.4's residual deltas SATISFIED in
  frozen C2 v1.3).
- Dispatch-ack, agent-name uniqueness preflight, room-idempotent retries →
  **C3**.
- Degrade-reason transport (guest emit → ingestion validator → FE),
  validator-first deployment, the `HARNESS_PARALLELISM_ENABLED` clamp →
  **C4**. C1 contributes exactly one new reason name,
  `port_not_consumable` — which, per decision 2 (§4), is a **terminal
  job-failure reason, NOT a `parallelism_degraded` degrade event**; C4/C2
  reconcile that classification in the post-freeze cross-contract pass.
- Per-uid isolation: every process runs as one sandbox uid on Daytona
  (`process_runtime.py:4387-4392` wiring; producer contract §2.6) — W>1
  multiplies same-uid processes with mutual /proc visibility. Recorded as
  an accepted platform property, equally true at W=1 today (plan §1
  round-3 correction); C1 neither weakens nor fixes it.

---

## 8. Named amendments (text to add; do not rewrite the host documents)

**A. Spine `hosted-execution-seams.md` §2b — `fixed_port` definition
(after the paragraph at :465-476):**

> *Amendment (C1, v2.x):* `fixed_port` splits by consumability, declared
> via `fixed_port_consumable` (default `false`). Code-fixed (`false`):
> everything above stands unchanged — honored exactly, forces effective
> parallelism 1 at requested W>1, `reason: fixed_port`. Env-consumable
> (`true`): the process binds the port its own `{{PORT_<name>}}` renders;
> once planned at W>1 every world **including world 0** gets the formula
> port and — given truthful wiring, runtime-checked by the conformance
> gate at W>1 — nothing listens on the declared value (a literal
> reference to
> it fails with connection refused, never cross-wires); at **plan-time
> effective** W=1 — requested 1, admission-clamped to 1,
> pre-plan-scan-forced to 1 (all before the port plan), or forced to 1
> inside it by a code-fixed sibling in the same bundle — the declared
> port is honored exactly as
> before. A mid-provision degrade to effective 1 does NOT re-plan: world 0
> keeps its formula port, harmlessly — all its env references were already
> rewritten to it. "A fixed port can exist in only one world" therefore
> holds for code-fixed ports; a consumable declared port exists in **no**
> world once planned at effective W>1 (mid-provision degrades included)
> and in exactly world 0 whenever the plan lands at effective 1 from ANY
> plan-time producer — requested 1, admission clamp, pre-plan scan, or
> code-fixed forcing inside the port plan. A consumable declaration whose
> run command does not reference an `environment` value carrying
> `{{PORT_<self>}}` (`$KEY` inside `sh -c` — `run_command` is exec'd
> verbatim, never rendered) is a preflight error
> (`fixed_port_consumable_unwired`). A consumable process that instead
> binds its declared port at runtime is a TERMINAL job failure
> (`port_not_consumable`), never a silent cross-wire.

**B. Spine §3 endpoint wiring (`EnvironmentRuntime`, :714+; addresses
always localhost, :740):**

> *Amendment (C1):* addresses remain `localhost` in every world (`HOST_`
> renders `localhost`, W-independent). Once planned at W>1 each world's
> endpoint ports are world-unique by the §2b formula for ALL source
> processes, consumable-fixed-port processes included; no two worlds
> share any per-world port, and no endpoint ever carries a declared
> `fixed_port` value once planned at W>1 — a later mid-provision degrade
> changes none of this (the plan is frozen).

**C. `bundle-producer-contract.md` §2.5 (ports / fixed_port / W) — append:**

> *Amendment (C1):* `fixed_port` MUST additionally declare consumability
> via `fixed_port_consumable`. Declare `true` ONLY when the process's
> **run command** consumes its rendered port through the ONE supported
> wiring: put `{{PORT_<own-name>}}` in an `environment` value and reference
> that env var from the command by `$KEY` inside an `sh -c` argument
> (§2.3's `sh -c` + `PORT` pattern). Putting the token directly in a
> `run_command` argv does NOT work — `run_command` is exec'd verbatim,
> never template-rendered and never shell-expanded, so the token would be
> passed as literal bytes. A port fixed in the command — a Dockerfile
> `CMD ... --port N` copied into `run_command` — is NOT consumable until
> the command is rewritten to the `sh -c` + env form; a token parked in an
> env var the command never references does not count and preflight rejects
> it (`fixed_port_consumable_unwired`). At W>1 a
> consumable process gets a per-world allocated port in every world; the
> declared value is honored only at plan-time W=1. Declaring `false` (or
> omitting) keeps today's honest degrade to W=1. No two processes may
> declare the same `fixed_port` value (`fixed_port_duplicate`). Never
> reference another process's port as a literal anywhere in
> `environment` — at W>1 nothing listens on declared ports and the
> reference fails with connection refused.

**D. `bundle-producer-contract.md` — new subsection beside §2.7 (its
:183-205 already legislates per-world env identity; the worker-knob
mandate is the same shape of rule) — the mandate:**

> *§2.7b Worker knobs (C1 mandate).* The harness sets
> `FI_WORKER_HEALTH_PORT` (via the process's own `{{PORT_<name>}}`),
> `FI_LOAD_THRESHOLD`, and `FI_NUM_IDLE_PROCESSES` in the environment of
> EVERY LiveKit-worker process in the bundle, each fed its own rendered
> token. **A process is knob-bearing iff its rendered `environment`
> contains `FI_WORKER_HEALTH_PORT`** — that key's presence IS the mark,
> for both authoring (which writes the trio into every LiveKit-worker
> process) and runtime attribution (which identifies knob-bearing
> processes by that key). A conformant LiveKit agent MUST read all three
> into its `WorkerOptions`/`AgentServer` construction — livekit-agents
> 1.7.1 has no env or CLI override for these; they are constructor-only.
> When absent, use library defaults (the agent stays runnable outside the
> harness). A non-conformant agent binds health port 8081 in every world
> and at W>1 the second worker dies at bind (`[Errno 48]`); the harness
> surfaces this as a **loud TERMINAL job failure** with reason
> `port_not_consumable` (world 0 is internally inconsistent — its
> consumers point at the unbound assigned port — and cannot be salvaged
> into a W=1 run; fix the agent to read its assigned port, or request
> `parallelism=1` to run serially with the declared port honored).

**E. `outbound-channels.md` reason vocabulary + spine §5 payload
(`hosted-execution-seams.md:906`)**: reserve `port_not_consumable` (C1) as
a reason STRING — but per decision 2 (§4) it is a **terminal job-failure
reason, NOT a `parallelism_degraded` / `DegradeReason` member**. It must
NOT be added to the `DegradeReason` degrade enum; it belongs with the
job-failure codes. Transport and the exact home are C4's, to settle in the
post-freeze cross-contract pass; listed here only so the amendment set is
complete in one place. (C4 adds the degrade reasons `resource_limited`,
`literal_local_endpoint`, `world_start_failed` — not C1's.)

---

## 9. Conformance checklist

Authoring (Track A′):
1. `fixed_port_consumable` exists on `SourceProcess`, default `false`;
   `true` without `fixed_port` fails validation.
2. Re-authoring the reference repo marks `tools-api` consumable
   (change site `bundle_author_v2.py:659, 372`) AND rewrites its run
   command — today command-fixed by the Dockerfile `CMD` copy
   (`tools-api/Dockerfile:9` → `_dockerfile_run`,
   `bundle_author_v2.py:456-491`, `:671`) — to the concrete §2.3 form
   `["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port $FI_TOOLS_PORT"]`
   with an `environment` entry `FI_TOOLS_PORT: "{{PORT_tools-api}}"` (the
   command references the token-bearing env value by `$FI_TOOLS_PORT` —
   the ONLY wiring the one-clause check accepts; a token in the argv is
   NOT rendered, §1), and authors the three `FI_*` vars unconditionally
   into EVERY LiveKit-worker process, each with its own `{{PORT_<name>}}`
   (§4).
3. An existing sealed manifest (no new field) validates unchanged and
   behaves identically at every W; and because `fixed_port_consumable`
   serializes omit-when-default (§1), re-serializing that manifest
   reproduces its bytes and digest unchanged — asserted on the exact
   `model_dump(mode="json")` that `seal_bundle_v2` consumes
   (`bundle_v2.py:654-657`) and round-tripped through `load_bundle_v2`.

Port plan (Track B):
4. `plan_ports(instances=4)` on a manifest whose only fixed port is
   consumable → `effective_instances == 4`, `degraded_reason is None`,
   and `port_for(p, w)` returns the formula port for every `w` including
   0, never the declared value (assert the exclusion).
5. Same manifest, `instances=1` → `port_for` returns the declared port
   (the plan-time W=1 carve-out); and after a plan made at `instances=4`,
   a RECONCILE `provision()` call leaves `context.port_plan`
   VALUE-IDENTICAL to the first call's (the §2 freeze locus,
   `process_runtime.py:4548-4554`) — a simulated mid-provision degrade
   to effective 1 still returns formula ports; no re-plan path exists ON
   THE RECONCILE BRANCH. A same-job-identity DIGEST REBUILD is the one
   legitimate re-derivation: the first-call/digest-mismatch branch
   (`:4454`) tears down and rebuilds a fresh `SpawnContext` + `port_plan`
   (`:4450`, `:4478-4500`) with ceiling/ledger carried forward
   (`:4536-4541`; C2 §1 rule 3), and the freeze is not violated (freeze
   binds the reconcile path, not a new build of the same identity).
6. A code-fixed port at `instances=4` → `effective_instances == 1`,
   `degraded_reason == "fixed_port"` (unchanged behavior); and a MIXED
   bundle (code-fixed + consumable) at `instances=4` → the same, PLUS
   `port_for` returns the consumable process's DECLARED value (the
   iff-rule keys on EFFECTIVE instances, §1/§2 — today's behavior).
7. `{{PORT_<name>}}` tokens, capability addresses, `started_check`
   probes, and spawn-handle ports all reflect (4)/(5) with no code path
   bypassing `port_for`.

Preflight:
8. Consumable process whose `run_command` does not reference an
   `environment` key whose value carries `{{PORT_<self>}}` →
   `fixed_port_consumable_unwired` (§1's one-clause check). In particular:
   a process with the token ONLY in an env var its command never
   references is rejected (env-token presence alone is not wiring); AND a
   process with the token placed directly in a `run_command` argv is ALSO
   rejected — `run_command` is exec'd verbatim, never rendered, so the
   argv-token clause was deleted this revision and no longer satisfies the
   check.
9. `fixed_port_reserved` bands unchanged and still enforced.
9b. Two `SourceProcess`es declaring the same `fixed_port` value →
    `fixed_port_duplicate` (no spawn-time bind collision ever reached).

Gate / degrade / terminal (with C2, C4):
10. At W=2 with a deliberately non-conformant agent (ignores
    `FI_WORKER_HEALTH_PORT`): world 1's knob-bearing worker process
    (carries `FI_WORKER_HEALTH_PORT`) dies at bind with `[Errno 48]`-class
    evidence in its log tail naming a NON-formula port (default 8081), and
    the job **FAILS loudly and terminally** with reason
    `port_not_consumable` within the started_check timeout — no hang, no
    silent failure, no W=1 salvage; the failure diagnosis names the
    assigned-port fix or `parallelism=1` (decision 2, §4). The same gate
    failure with the bind-evidence line suppressed → graceful degrade
    reason `conformance_gate_failed` (§4's fallback), still loud. A
    planted bind-error line naming a FORMULA port (stale-squat class) →
    graceful degrade reason `world_start_failed`, never
    `port_not_consumable` (§4's anti-false-positive).
10b. At W=2 with a LYING consumable declaration (run command references
    the token via `$KEY`, server binds the declared value anyway, tolerant
    `log_marker` started_check): the gate declared-port listener check
    (§4) — a distinct provision step that does NOT touch
    `build_output.conformance` or `run_conformance_gate`'s return — finds
    the listener after the gate worlds build and the job **FAILS loudly
    and terminally** with reason `port_not_consumable` (decision 2). The
    bind-death timing (world-1 death naming the DECLARED port by a
    consumable-declared process, the fourth attribution arm) reaches the
    same terminal failure.
11. At W=2 with the updated reference agent: no 8081 bind anywhere; each
    worker's health port equals its world's rendered
    `FI_WORKER_HEALTH_PORT`.

Literal endpoints:
12. Given truthful consumable wiring (§2's condition — the lying case is
    10b's subject), at effective W=4, `curl localhost:<declared>` →
    connection refused
    from any process in the sandbox (single shared network namespace —
    there is no per-world place to stand).
13. The observed real job shape (secret ref carrying
    `TOOLS_API_URL=http://localhost:18090`, port declared nowhere) at
    W=4 → RUNS at W=4: warn-tier warning emitted (alias + port only),
    `TOOLS_API_URL` re-asserted world-correct in every world, NO degrade
    event (§5.4b). A planted secret carrying `localhost:<declared>`
    instead → pre-plan degrade to 1, `literal_local_endpoint`, declared
    port bound, job completes as today (§5.4a; C2's checklist item 5
    covers the same test from C2's side, post its sibling amendment).

Reference agent:
14. ride-voice reads `FI_WORKER_HEALTH_PORT`, `FI_LOAD_THRESHOLD`,
    `FI_NUM_IDLE_PROCESSES` into its `AgentServer` construction
    (today it does not — `agent/agent.py:72`), and still starts with all
    three absent.

---

## 10. Open decisions

| # | decision | options | owner |
|---|---|---|---|
| OD-1 | `FI_NUM_IDLE_PROCESSES` mandated value (the variable and mandate are pinned; the value is not — plan §3 mandates only the mechanism) | `0` (no prewarm) / `1` (bounded prewarm) / Track-D-calibrated | Khushal, after Track D |
| OD-2 | Names `fixed_port_consumable`, `fixed_port_consumable_unwired`, `fixed_port_duplicate`, `fixed_port_consumable_requires_fixed_port` — semantics are pinned by this contract; the names await producer-side sign-off | rename at sign-off only, before any producer emits them | Rishav (producer contract owner) |

Both OD-1 and OD-2 survive the freeze as **owner-side fill-ins of a pinned
mechanism** (a Track-D value; a producer-side name), NOT as reopenable
contract decisions — resolving either is not a new review round.

## 11. Versioning

Versions independently of the spine. Ambiguities become amendments, never
guesses.

**Changelog**
- Consistency-pass reconciliation (post-freeze, 2026-08-31 — NOT a review
  round, no version bump): sibling version pins swept to the frozen siblings
  (C2 v1.1 → v1.3 in §3/§4/§5/§7 references), and the three C2 two-tier
  residual-delta SIBLING NOTES (warn-tier ANY-variable scope, `[::1]` in
  both tiers, both-tiers W-scoping) marked SATISFIED against frozen C2 v1.3,
  which carries all three. Pins + satisfied-status only; no normative change
  (decision 2's terminal `port_not_consumable` classification was already in
  v1.3 and is unchanged — C2/C4 adopt it in this same pass).
- v1.3 (2026-08-31): review round 3 (8 findings applied) — **FINAL round;
  the contract is now FROZEN** (no round 4). Cross-contract reconciliation
  items dispatched here are PENDING the post-freeze cross-contract
  consistency pass (listed below). MAJOR: `fixed_port_consumable_unwired`
  clause (i) DELETED — `run_command` is copied verbatim
  (`process_runtime.py:1597`) and never rendered (only `render_environment`
  at `:423-445`/`:1558` resolves tokens, over `process.environment` only);
  a token in a `run_command` argv is exec'd as literal bytes (producer
  §2.3: argv exec'd directly, no shell, `$VAR` not expanded), so the ONLY
  valid wiring is a `{{PORT_<self>}}`-bearing env value referenced by `$KEY`
  inside `sh -c` (§1, Amendment C, checklist 8); tools-api concrete rewrite
  pinned — `sh -c "... --port $FI_TOOLS_PORT"` with
  `FI_TOOLS_PORT: "{{PORT_tools-api}}"` (§1, checklist 2). MAJOR:
  `port_not_consumable` reclassified from a curative degrade to a
  **TERMINAL JOB FAILURE** (decision 2) — a consumable/knob-bearing process
  that binds its declared port instead of its allocated formula port leaves
  world 0 internally inconsistent (consumers/capability URLs point at the
  unbound formula port) and the frozen plan cannot re-consist it; both
  detection paths (world-≥1 bind death + gate listener check) fail the job
  loud with the assigned-port-or-parallelism=1 diagnosis; the CLEAN
  non-consumable `fixed_port` W=1 degrade is unchanged (§2, §4, §5
  guarantee 1, §6 rows, Amendments A/D/E). MAJOR: reason-split-by-race
  closed (decision 3) — fourth attribution arm added: bind-evidence naming
  a DECLARED port by a CONSUMABLE-DECLARED process → `port_not_consumable`
  regardless of knob-bearing status; a non-consumable process's bind death
  on a formula port stays `world_start_failed` (§4 mapping). MINOR:
  listener check pinned as a DISTINCT provision step raising a terminal
  failure directly — MUST NOT set `build_output.conformance = False` and
  MUST NOT add a third `run_conformance_gate` return string; names the two
  facts it must not break (the `:4594-4599` latch; C4's closed
  two-constant gate-return vocabulary) (§4). MINOR: freeze rule scoped to
  BUILD IDENTITY — a same-job-identity digest rebuild (first-call branch
  `:4454`, fresh `SpawnContext`+`plan_ports` `:4478-4500`/`:4450`, ledger
  carry-forward `:4536-4541`) legitimately re-derives the plan; the freeze
  binds only the reconcile path (§2, checklist 5). MINOR: knob-bearing
  worker defined operationally — a process is knob-bearing iff its rendered
  environment contains `FI_WORKER_HEALTH_PORT` (§4, Amendment D). MINOR:
  stale C4 sibling note converted — C4 v1.3's three-condition rule + two-tier
  §9 rows SATISFIED (verify retained); live C2+C4 residual narrowed to
  control-vs-worker phrasing, formula-port stale-squat exclusion, the second
  `port_not_consumable` producer, and decision 2's terminal-not-degrade
  reclassification (§4). NIT: serialization hint corrected — pydantic v2
  `@field_serializer` cannot omit a key; omit-when-default needs a
  model-level `@model_serializer` or a dump-site `exclude`/`exclude_defaults`
  at `seal_bundle_v2`'s `model_dump` (`bundle_v2.py:654`) (§1). Sibling
  notes to C2/C4 (terminal-not-degrade + the four residual deltas) and to
  C3 (verbatim `FI_WORKER_HEALTH_PORT` naming) marked for the post-freeze
  pass; C3/C4 are themselves frozen, C2 is v1.2.
- v1.2 (2026-08-31): review round 2 (14 findings applied). MAJOR: mixed
  bundles DECIDED — the consumability iff-rule keys on the plan's
  EFFECTIVE instances computed inside `plan_ports` AFTER code-fixed
  forcing, so a code-fixed sibling forces effective 1 and the consumable
  declaration IS honored (today's behavior; §1/§2, Amendment A's
  dichotomy restated over ALL plan-time producers of effective 1,
  checklist 6); freeze locus DECIDED — the reconcile branch replacing
  `context.port_plan` (`process_runtime.py:4548-4554`) MUST carry the
  first call's plan forward unchanged, C2 §1 rules 3-4 as companion
  (checklist 5 restated testably); listener guarantee DECIDED — §5
  guarantee 1 / Amendment A / checklist 12 conditioned on truthful
  consumable wiring with the lying-declaration residual recorded, plus a
  NEW runtime gate declared-port listener check at effective W>1
  (listener found → `port_not_consumable`, loud; Track B; checklist
  10b; sibling note to C2 stage table + C4 producer enumeration);
  plan-deviations block added (18090 is only the compose HOST mapping,
  `docker-compose.yml:30` — declared port 8080; carve-out narrowed to
  plan-time; the flagship job runs at W=4 with a warning, never
  degrades); C4 sibling note added (two-condition rule + §9 blanket row
  contradicted; v1.2 adoption dispatched — verify at C4's next round);
  warn-tier scope DECIDED — ANY variable, generic warning, no
  per-process authoritativeness evaluation (§5.4b; C2 note updated).
  MINOR: tool-proxy parenthetical corrected (declares no `fixed_port`,
  `bundle_author_v2.py:503-517`; run_command references no env key — no
  "known readers" escape hatch); attribution anti-false-positive — bind
  evidence must name a NON-formula port (formula collision =
  stale-squat → `world_start_failed`; C3 §5.4 teardown escalation
  cross-referenced as the squat bound); worker-knob mandate scoped to
  EVERY LiveKit-worker process, each with its own `{{PORT_<name>}}`
  (§4, Amendment D, checklists 2/10/10b); C3 sibling note narrowed to
  the §5.2 + knob-checklist-row verbatim-naming gap; SHOULD→MUST
  modality fixed (all four guarded keys mandatory in C2 §4a);
  `environment_values` wording aligned to C4 §7's warn-only-never-reject
  + both-tiers W-scoping pinned (scan only when the incoming ceiling
  exceeds 1); `[::1]:<P>` added to the degrade tier (and warn tier);
  serialization enforcement site pinned to `seal_bundle_v2`'s
  `model_dump(mode="json")` (`bundle_v2.py:654-657`) + `load_bundle_v2`
  round-trip. NIT: stale "resolves C4's conditional" sentence dropped in
  favor of C4 §2's RESOLVED text. Sibling notes converted to
  verify-at-next-round where the sibling amended concurrently.
- v1.1 (2026-08-31): review round 1 — declared-port carve-out scoped to
  PLAN-TIME W=1 only (mid-provision degrades keep the frozen PortPlan;
  world 0 stays on its formula port, documented harmless); tools-api
  corrected to COMMAND-FIXED today (Dockerfile `CMD --port 8080` copied
  verbatim into `run_command` by `_dockerfile_run`; `agent.py:188` is
  client-side only) with `command-fixed` added to the taxonomy and the
  `fixed_port_consumable_unwired` check strengthened to a run-command
  check; literal-endpoint stories reconciled into a two-tier pre-plan
  scan (degrade on declared-port match, warn-and-run otherwise — the
  observed real job at W=4 runs, it does not degrade);
  `port_not_consumable` attribution now requires bind-error log evidence
  (fallback `conformance_gate_failed`); `fixed_port_duplicate` preflight
  reject added; C2 references resolved to `c2-replication-admission.md`
  with three SIBLING AMENDMENT REQUIRED notes (two-tier scan + checklist
  5; spawn-time override warning section; C3 verbatim `FI_*` names);
  validation error named `fixed_port_consumable_requires_fixed_port`;
  sealed-manifest serialization pinned omit-when-default; checklist 12
  rephrased to the shared network namespace.
- v1.0 (2026-08-31): initial draft implementing parallelism-plan-v10 —
  consumability split, effective-W-scoped allocation rule with the W=1
  declared-port carve-out, env rewrite pinned to the existing token +
  capability mechanism, worker-knob env mandate + `port_not_consumable`
  failure shape (resolving C4's split conditional), literal-endpoint
  guarantees, amendment texts A–E, conformance checklist.
