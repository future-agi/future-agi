# RL Environment — UI flow improvements

Scope: UI/UX only. Backend endpoints for validation and retry are stubbed in the API
layer with a documented contract; the platform team wires the servers.

Base branch: `feat/hosted-bundle-v2-production`.

## 1. Preflight → provide → validate (HarnessCreate)

Problem: the landing flow scans requirements and lets you paste values, but "Ready to
run" is asserted from _non-empty_ values alone. Nothing confirms the credentials actually
work before a run is spent.

Change: a three-checkpoint **Readiness** pipeline, always visible once a source is set.

1. **Scan** — preflight the source, detect required credentials + packaging (existing).
2. **Provide** — fill the missing values (existing input rows, now inside the pipeline).
3. **Validate** — live-check every provided credential _works_ (new). Per-credential
   result: checking / valid / invalid(reason). Overall gate.

- `Run end to end` lights up only once validation passes. A secondary "Run without
  validating" escape hatch preserves the existing non-blocking philosophy.
- New API: `validateHarnessCredentials(payload)` → `{ overall, results: [{ environment_name, status: "valid"|"invalid"|"checking", detail }] }`.

## 2. Retry from the failed step (HarnessDetail)

Problem: a run that fails at stage N shows the error but offers no way forward except
starting a brand-new environment from scratch.

Change: on a failed run, a **recovery** card + header CTA:

- `Retry from <failed stage>` (primary) — resumes the pipeline at the stage it died on.
- `Restart from the beginning` (secondary).
- The left stepper marks the failed stage and anchors the retry.
- New API: `retryHarnessJob(id, { from_stage })`.

## 3. Scenario detail accordion (StageOutput, scenarios kind)

Problem: generated scenarios render as flat name + instruction + use_case. The rich
generation detail (goal, sub-goals, persona, background noise, actors, variables) is
dropped.

Change: each scenario is an accordion. Collapsed shows name + persona + goal + sub-goal
count + status. Expanded shows every populated section: Goal, Sub-goals (checklist),
Persona (attributes), Background noise, Actors / sub-actors, Variables, with a raw-JSON
fallback for anything unmapped. Sections render only when they carry data.

Data reality (from source audit): today the scenario `data` object guarantees only
`name`, `instruction`, `use_case`, `scenario_key`. The richer fields
(`goal`, `sub_goals`, `persona`, `background_noise`, `actors`/`sub_actors`, `variables`)
are **aspirational** — not yet emitted. Assembly spreads `{**doc, ...}`
(`hosted_harness_gateway.py:955-975` / `2578-2587`), so any keys the team authors into
`scenario.json` pass through verbatim. The UI is therefore built to the _target_ schema
and degrades gracefully: unpopulated sections are omitted, and today's minimal data still
renders name + instruction + use_case. The team's BE task is to emit the richer fields.

## API wiring decisions (verified against the contract gate)

The repo enforces **backend-first** contracts: `apiPath()` only accepts paths already in
the generated Swagger surface, and the contract-exception manifest is capped at zero
entries (`check-api-endpoint-registry-contract.mjs`). So the frontend cannot introduce a
brand-new endpoint without the backend shipping it first.

- **Validation (#1)** reuses the existing, contracted `preflight` endpoint via
  `validateHarnessCredentials()`. It returns a per-credential result shape
  (`{ overall, results: [{ environment_name, status, detail }] }`) the UI renders. A single
  seam is left for the team to swap in a true live-liveness endpoint later (the `invalid`
  state with a reason is already rendered when the result carries it).
- **Retry (#2)** — `retryHarnessJob(id, { from_stage })` posts to
  `/simulate/api/harness-jobs/{id}/retry/`, a **new backend action the team will add** to
  `HarnessJobViewSet`. It is written as a plain client call (not `apiPath`) so the build
  stays green pre-backend; a `TODO` marks the switch to `apiPath` once the action is in
  Swagger. Until the backend lands it 404s and the UI shows a graceful error.
  **Restart from the beginning** needs no new endpoint — it routes to the create flow.

## Verifier

- Faithful dark-theme prototype screenshotted and scored by an independent fable-5 critic
  against a top-studio bar; iterate to >=9/10.
- Real MUI components ported to match; `eslint` clean; existing + new vitest specs pass.
