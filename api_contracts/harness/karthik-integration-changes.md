# Hosted Harness — Change Requests for Karthik (2026-08-25)

From: Khushal (Track B — guest-side execution). Basis: a read-only
reconnaissance pass over PR #63 (`feat/hosted-harness-e2e-runtime` ←
`feat/parallel-scenario-generation`, 27 commits, head `42e2081`), checked
against the seam contracts (world-handle-interface.md v3.4,
outbound-channels.md v1.3) and against our own guest-side stack
(`hosted_scheduler.py`, `outbound.py`). I'll send the contract files
themselves.

## First, what needs NO change (verified clean)

- **World-handle v3.4 conformance is exact.** `setup(world)` /
  `ready(world)` / `check(world, calls)` — the shapes your generator
  writes (`scenario.py:275-278`'s validation, matching the
  `check(world, calls)` signature our runner invokes) line up with the
  contract's vocabulary verbatim, and the world API your scenarios are
  taught (`world.call()/put()/change()/drop()/state()`, `by=` on
  `change`/`drop` — `SKILL.md:299-309`) is the contract's. The
  write-scenarios skill doc matches too, with one scheduled exception
  noted under K3.
- **No file collision with our stack.** PR #63 touches 18 files —
  scenario generation plus the simulator prompt
  (`scenario.py`, `scenario_tools.py`, `persona_guides.py`, `run/*`,
  `simulation/engines/livekit.py`, `voice_prompt.py`); none intersect
  `hosted_scheduler.py`, `outbound.py`, or `world/*`. Rebasing past
  this PR should be mechanically clean — the one semantic issue is K1
  below, and the one small textual touchpoint is under K3.
- **Runner-side alignment, nothing for you to do but worth knowing:**
  `checks.py` is ours, and we're aligning it to v3.4's return
  conventions — `check` returning `""`/whitespace now counts as held,
  a bare `False` from `ready` becomes broken; exception-is-broken is
  unchanged. Two invariants to keep in view as generation evolves:
  (1) return values carry the verdict, exceptions carry breakage —
  `ready` returning a non-empty string is "precondition didn't hold",
  `ready`/`check` *raising* is `ready_broken`/`check_broken`, and the
  grading distinguishes them so a broken check never reads as a
  finding about the agent under test; (2) `call_failed` (call
  machinery, infrastructure, retried once on another world) vs.
  `driver_crashed` (scheduler's own machinery, not retried) keep
  infra hiccups from being *attributed* to your scenario code — note
  a second `call_failed` after the one retry does still land the
  scenario as errored in the suite counts.

## K1 — `scenario_key` (and a `scenario_id` slot) are absent from the `Scenario` model

Confirmed by `git grep -n "scenario_key"` over both `origin/pr-63` and
its base `feat/hosted-harness-e2e-runtime`, scoped to
`src/fi/alk/harness/`: zero hits in either. The `Scenario` model
(`scenario.py:152-204`) has no such field — its complete list is `name`,
`use_case`, `branch`, `tests`, `setup_code`, `ready_code`, `instruction`,
`persona`, `variables`, `fixture`, `solution`, `sub_goals`, `max_turns`,
`background_noise`.

Our guest side hard-requires two identity fields. `hosted_scheduler.py`
defines its own `Scenario` Protocol (line 162) declaring
`scenario_key: str` and `scenario_id: str` (the platform id assigned at
pre-allocation), and reads `scenario.scenario_key` at eight call sites
(978, 1128, 1146, 1205, 1243, 1277, 1411, 1450) — six of those eight
followed one line later by a `scenario.scenario_id` read.
`outbound.py` requires `scenario_key: str = Field(min_length=1)` on the
event payloads that report scenario progress (`ScenarioStartedPayload`,
`ScenarioRetriedPayload`, lines 634/642) and on the result-receipt model
(line 2341), and the contract makes `scenario_id` REQUIRED on every
receipt. Nothing crashes today because nothing wires your `Scenario`
into that Protocol yet — but `Scenario` is a pydantic `BaseModel`, and
reading an attribute pydantic never defined raises `AttributeError`,
not a typed error. The day someone connects the two, every one of those
call sites breaks before a single scenario runs.

outbound-channels.md v1.3 already names the requirement: *"each persona
entry in the provision payload carries `scenario_key`"* — a stable,
cross-attempt identity (a retried attempt matches on `scenario_key` to
avoid re-registering). The base branch's `platform.py::persona_of`
(Rishav's — not in your diff) maps `Scenario` one-to-one onto a persona
entry, so the natural home for the field is on `Scenario` itself.

The ask, precisely:

1. `scenario_key: str` — yours, populated deterministically at
   generation. Format: **ASCII slug, non-empty, unique per job, stable
   across regenerations** of the same logical scenario. `use_case` +
   `branch` (this PR's own new disambiguator) is the natural source —
   e.g. `slugify(use_case)-slugify(branch)` with a short hash suffix
   when either is empty. The format matters because the value ships as
   an HTTP header (`X-Scenario-Key`) with no charset validation
   anywhere in the path, `use_case`/`branch` are free-text LLM output
   (and this PR adds multilingual persona vocabulary), and both fields
   default to `""` — an all-empty suite would otherwise collapse every
   receipt onto one idempotency key `(job_id, scenario_key)`. Your
   existing dedupe on the lowercased/stripped pair (`scenarios.py:441`)
   guarantees pair-uniqueness for non-empty pairs — note it skips
   entries where `use_case` is empty, which is exactly the case the
   hash suffix above exists for.
2. `scenario_id: str = ""` — just the slot; leave it empty. The guest
   fills it from the platform's provision response at pre-allocation;
   you never populate it.

## K2 — the provision wire shape: we need your §3

Hosted scenario pre-allocation goes through `endpoints.scenarios` with
the attempt bearer (the base branch's `platform.py::PlatformClient.
provision` posting `/run-tests/provision/` with `X-Api-Key`/
`X-Secret-Key` is the local-SDK path — Rishav's file, explicitly
local-only per the contract, and unreferenced by our guest stack).

The `provision` call's payload and path shape is, per the seam
ownership table, **your deliverable — the Scenario Generation Contract
§3, listed as "in review"** — and outbound-channels v1.3 defers to it
by name (the companion `begin` call routes through the base branch's
`platform.py::begin`, Rishav's side — we'll chase that shape with
him). §3 hasn't reached us: our `ScenariosClient` carries
`provision_path`/`begin_path` as injectable placeholders because of
exactly this. And it gates more than paperwork — the contract makes
provision success a prerequisite for the `running` stage, so neither
side can write the pre-allocation call until §3 exists.

The ask: send us §3 (or its current draft state) — the provision
request/response shape. We'll un-stub `ScenariosClient` against it and
pin it into outbound-channels as an amendment so it's frozen for
everyone. The base branch's `persona_of` precedent (`{name, role,
situation, outcome, persona}` + the new `scenario_key`) is a fine
starting point if §3 is still unwritten — say so and we'll draft it
together from that shape.

## K3 — merge coordination

PR #63's base is `feat/hosted-harness-e2e-runtime` — Rishav's branch.
Our own stack rebases onto that same branch, and PR #63 will land a
second wave of commits on top of it once merged. One known textual
touchpoint: v3.4's implementation-delta list assigns *us* two
line-level edits to `skills/write-scenarios/SKILL.md` (the
"collection is not always a list" dict-shape note doesn't exist in
hosted worlds), and your PR touches that file too — trivially
resolvable, worth knowing it's coming. Beyond that there's no
mechanical collision, but the landing order (yours, Rishav's latest,
ours) isn't yet agreed — worth settling at the call rather than each
of us discovering it independently at rebase time.

## Suggested order of work

1. K1 (both identity fields on `Scenario`) — blocks the first
   end-to-end wire of generated scenarios into the hosted scheduler;
   nothing plays until it lands.
2. K2 (§3 handover) — provision gates the `running` stage; the sooner
   we have the shape, the sooner the guest's scenarios client stops
   being a stub. Doesn't block your generation work itself.
3. K3 (merge/landing order + the SKILL.md touchpoint) — cheap to agree
   now, expensive to discover after three branches have all moved.
