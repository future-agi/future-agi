# Phase 4 handoff — runs through the hosted pipeline + the deletion (Azain)

## What this phase is, in one paragraph

The cutover. RL environments become runnable through the pipeline you built:
the run button (and the existing RunTest execute view) initiates a normal
RunTest whose cases dial through `SimulationRunnerWorkflow` → ALK → LiveKit,
except every case now has a disposable world copy behind it — the agent's
tools hit the harness via a per-case capability URL, grading fires on each
result PATCH, and copies are cleaned by a terminal sweep plus a janitor.
When that works end to end, the old native harness run path is guarded off
and its dead code deleted. This is the phase where "multiple users, multiple
environments, parallel runs" becomes literally true.

## Ground rules

- **The interface bible is `api_contracts/harness/phase-seam-contracts.md`,
  revision v3** — check the revision note at its top; it ships beside this
  handoff (uncommitted by design; `api_contracts/harness/` is canonical).
  Seam C is your surface; Seam B is what Jayasurya's world service gives
  you; Seam A §A3/A4 defines the verdict and copy-lifecycle semantics your
  hooks rely on. Deviations = a conversation with Khushal, not a local edit.
- **You hold a veto that should be exercised early**: Contract 1's room
  template becomes `hosted-{run_id}-i{index}-{test_case_id}` — the
  `-i{index}-` insertion into `_voice_livekit_runtime`'s `room_name`
  (`services/hosted_runner.py:703`, consumed at `:708` and `:720`) is the
  one change to your pipeline the whole design leans on. The parser on the backend already expects that
  shape (`views/harness_hook.py`). If you see a problem with it, say so
  before anything else gets built on it.
- Branch `feat/harness-phase4-runs` off `feat/harness` **after Phases 2 and
  3 have merged** (you depend on both; don't fork early — the diamond
  rebase is worse than the wait). Conventional commits, DCO (`git commit
  -s`), no co-author trailers. Expect to regenerate any migration numbers
  at rebase time.

## What already exists for you (branch `feat/harness-phase1-foundations` + descendants)

- `RunTest.rl_environment` / `rl_world` FKs; `RLWorldCopy` with token,
  status machine, `call_execution` idempotency constraint, `expires_at`.
- `HarnessHookView` + `HarnessRoomConfigView`
  (`simulate/views/harness_hook.py`): the public routes, throttled,
  CSRF-exempt, strict token parsing, forwarding to the harness with the
  internal bearer. **You own `_resolve_room` and the room-config success
  body** — the stub currently 404s everything well-formed and 501s the
  (unreachable) resolved branch. Resolution per Seam C2: parse `index` →
  the run's pre-created CallExecutions in positional order → copy by
  `call_execution` → the config payload. The positional contract is your
  own (`precreate_alk_sim_call_executions` ordering).
- The internal persistence surface — with one usage rule: **backend-side
  code calls the ORM and service functions directly** (`RLWorldCopy`
  lookups, `_store_reported_evaluations`), never loopback-HTTP to its own
  `rl-harness` routes — those exist for the harness. The `rl-verdicts`
  view's semantics (400 on duplicate names and empty lists, 500 on the
  missing seeded template; Seam A3 mapping) are still the behavior your
  direct calls must reproduce.
- Jayasurya's Seam B routes (`/internal/materialize`, `prepare`, `grade`,
  `drop`, `janitor/sweep`) with fully specified request/response tables —
  including the prior-copy answer table in B1 you must branch on (a
  `failed` copy 422s; `dropped|expired` means fail the CallExecution, never
  redial).

## The work, by area

1. **Initiation service** (Seam C1) — one function, two doors (D11).
   Door 2 exists (`RunTestExecutionView` delegates when
   `run_test.rl_environment` is set). Door 1 — the environment's own run
   endpoint — is a **new tenant-facing backend route you own**: nothing
   like it exists today (the `api/rl-harness/*` routes are
   internal-bearer-only by design, and `HarnessProxyView` forwards to the
   harness, which must never create runs). Agree the path, permission
   classes and org scoping with Khushal before you start; C1 fixes only
   the sequence and response body. The all-or-nothing pre-dial
   sequence is enumerated in C1; two items are new platform behavior you
   should sanity-check against your own pipeline knowledge: a **balance
   check** (the hosted path has none today — the native path's
   `check_call_balance` never runs for hosted) and a DB-counted org
   admission cap. On any prepare failure: drop the stamped copies, 422
   naming the scenario, nothing dials. The response carries NO tokens
   (bearer capabilities; room-config is the agent's only source).
2. **Result hooks** (Seam C3) — `mark_alk_sim_call_ongoing` flips the copy
   `ready → in_call` and records `world_copy_missing`;
   `ingest_alk_sim_result` calls `/internal/grade` after storing the result
   and reports verdicts; failures land in
   `call_metadata["harness_error"]` (never `error_message` — 
   `_apply_payload` clobbers it). Grade failure must never block result
   ingestion.
3. **Terminal sweep** — `finalize_hosted_execution` sweeps every remaining
   copy in EVERY branch (completed/cancelled/failed — note the completed
   branch returns early today; hook the sweep before every return).
   Graded-in-sweep copies still report verdicts; gate copies never do.
4. **Janitor** (Temporal Schedule — follow `tfc/temporal/schedules/simulate.py`
   precedent): direction ① row-driven expiry (grace rule for `grading`
   rows), direction ② compute `live_db_names` → `POST /internal/janitor/sweep`.
   `HARNESS_MAX_LEASE_SECONDS` bounds everything.
5. **The deletion** — guards at the five verified native-placement sites,
   enumerated in the plan's §6 Phase 4 (NOT §9, which only counts them):
   (a) the `_execute_with_temporal` voice arm (`simulate/views/run_test.py`),
   (b) the `start_test_execution_workflow` choke point
   (`simulate/temporal/client.py:25`), which also catches the
   `ai_tools/tools/agents/run_agent_test.py:135` bypass, (c+d) the two
   ai_tools rerun entry points — these go through `rerun_call_executions`,
   NOT the choke point (`ai_tools/tools/simulation/rerun_call_execution.py:194`,
   `rerun_test_execution.py:273`) and also gain the hosted-eligibility check
   they skip today, and (e) `CallExecutionWorkflow`'s **call-placement arm
   only** — the workflow stays registered for hosted eval-only reruns, but
   its placement arm still gets a guard. Then retire
   `CallDispatcherWorkflow`/`PhoneNumberDispatcherWorkflow` and delete the
   v0 harness webhook/run path once the equivalence test is green.
6. **Deployment notes that are yours to confirm**: `FI_VERSION` pinning for
   the ALK image is a named deployment requirement (it's set out-of-band —
   only you know where); two findings from the design verification are
   addressed to you — `HOSTED_RUNNER_ENABLED` is set only on the
   worker-simulation-runner service, never on the backend that routes, so
   hosted routing is dead in the execute view's ladder today; and the
   missing balance check above.

## Testing expectations

- The plan's Phase 4 tests: the equivalence test (both doors produce the
  same run), idempotency pair (duplicate result PATCH → replayed verdicts,
  single config row), the no-hang test (a case whose copy failed never
  dials), sweep drills (cancel mid-call → all copies settled, store empty),
  janitor drills. `bin/test` lane; the harness test stack includes the
  store on 15433.
- Two more plan-mandated deliverables easy to miss: a **golden room-name
  test** rendering the template against the pinned ALK wheel's
  `_resolve_room_name` (the only proof the `-i{index}-` shape renders
  identically in the shipped SDK), and adding `futureagi/simulate/**` to
  `.github/workflows/api-contracts.yml`'s two `paths:` filters (~lines 6
  and 43) — the contract check never fires on this work otherwise.
- End-to-end voice needs real LiveKit credentials + an agent worker
  carrying the ~15-line room-config snippet (Contract 1 in the Rebuild
  Plan) + its triple entered as ProviderCredentials. Only you can bless
  the E2E — budget for it; it is the phase's long pole.
- Exit (from the plan): a run initiated from an environment flows hosted
  end-to-end with verdicts on the RunTest; the native harness path refuses
  with a pointer; the deletion inventory is empty.

## Sharp edges (review-sourced)

- The SDK swallows the pre-dial status PATCH response — that is WHY copies
  are stamped eagerly at initiation; nothing can stop a dial after it
  starts. Don't move prepare later.
- `test_case_id` in the room name is the SDK's derived id, not the
  CallExecution id — the `index` is the only reliable join key; that's the
  positional contract your precreate ordering already guarantees.
- Room-config responses and hook URLs carry bearer capabilities: redact
  `/simulate/harness-hook/<token>/…` path segments from access logs;
  never log room-config bodies.
- **Reruns of a harness RunTest are an open decision** — take it to
  Khushal early: `_dispatch_hosted_rerun` (`simulate/views/run_test.py`)
  reuses the same TestExecution and reset CallExecution rows, but by then
  those rows' copies are terminal, which B1 refuses (`stage: "lease"`) —
  a literal rerun today would dial with tools that 404. Either reruns
  re-stamp fresh copies (new call_execution rows) or harness runs refuse
  `call_and_eval` reruns outright.
- The janitor's contract constants are yours to provision in compose/helm
  (defaults cover code): `HARNESS_GRADE_GRACE_SECONDS`,
  `HARNESS_MAX_LEASE_SECONDS`. Helm lives in the separate deployment repo
  — the same place `FI_VERSION` gets set.
- One pre-existing failure on `feat/harness` you'll see in the suite:
  `test_provision_rejects_voice_agent_definition`
  (`simulate/tests/test_alk_simulate_ingestion.py:266`; repro:
  `bin/test --no-services "simulate/tests/test_alk_simulate_ingestion.py::TestProvisionRunTest::test_provision_rejects_voice_agent_definition"`)
  — expects 400, gets 200, fails identically on a clean stashed HEAD
  (verified 2026-08-21), so it's a base-branch entitlement-env issue: not
  yours, not Phase 1's; flagged to the team.
