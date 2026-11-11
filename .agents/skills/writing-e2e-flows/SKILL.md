---
name: writing-e2e-flows
description: "Use when a feature or fix in future-agi needs an end-to-end Playwright flow under e2e/ — a new flow for user-visible behaviour, an update to a flow whose pinned endpoint, route, table or selector changed, or when a review flagged missing E2E coverage. Also use to check whether an existing flow already pins an endpoint, route, table or selector you changed, and for 'how do I test this end to end' questions about the FutureAGI product. Not for frontend unit tests, backend pytest, the API-journey scripts, for running, operating, debugging or explaining the e2e stack and its harness scripts themselves — ports, compose profile, start-up time — or for the review-time verdict on whether a change needs a flow, which is the reviewing-prs coverage gate."
metadata:
  short-description: Author a proven E2E flow for the feature you are working on
---

# Writing E2E flows

## Overview

A flow is one user goal, driven in a real browser against the whole running system and asserted in
both the UI and the backend state behind it. This skill takes you from "the feature I'm working on"
to a flow that is green on a managed stack **and** has been shown to fail when the thing it claims to
prove is broken — because an assertion nobody has watched fail is not coverage, it is decoration.

## When to use / when not to use

Use it when a change adds or alters behaviour a user can see; when a diff moves an endpoint, route,
table or selector some flow has pinned; when a review says E2E coverage is missing; or when someone
asks how to test a FutureAGI behaviour end to end.

Do not use it for frontend unit tests (`frontend/src/**/__tests__`), backend pytest
(`futureagi/**/tests/`), the browserless API-journey scripts (`frontend/scripts/api-journeys/`), or
for changing the harness itself. Extending `e2e/lib/` is its own piece of work — see step 0.5.

## Read this first (the contract)

Read all three before you plan anything. They are the contract; this skill only adds what they do
not carry — the decision procedure, the interview, design-doc mining, the pinned-facts inventory,
the harness gaps, and the proof loop.

- **`e2e/README.md` § "Writing a flow"** — in full, every time. Not skimmed, not remembered.
- **`e2e/FLOWS.md`** — what already exists, which area owns it, and the next free id.
- **The newest spec in the target area**; if the area has no flows yet,
  `e2e/flows/observe/trace-ingestion.spec.ts`, the canonical exemplar the README reproduces.

## Procedure

Track the numbered steps as todos, one each.

### 0. Ground (read first, ask second)

1. Read the three documents above.

2. Classify the change. From the repo root, with the branch checked out:

   ```
   node e2e/scripts/e2e-coverage.mjs --pr <n> --json                # when a PR exists
   node e2e/scripts/e2e-coverage.mjs --base origin/<base> --title "<branch or PR title>" --json
   ```

   Always pass `--title` in the no-PR form. One of the new-surface signals is a `feat` title in an
   area with no flows, and without a title that signal cannot fire — the same diff then classifies
   weaker than it will once the PR exists. `--pr` reads the title from GitHub.

   Prefer `--pr`; it takes the base from GitHub. Do not pass `--base` alongside it — an explicit
   `--base` wins over the PR's real base (`e2e/scripts/e2e-coverage.mjs:resolveBase`), and
   hand-resolving is precisely how a stacked branch gets classified against the wrong tree. With no
   PR, take the base from the branch's upstream or merge-base; never assume `dev`.

   It returns `classification` (NEW-FLOW / UPDATE-EXISTING / EXEMPT / UNDETERMINED), `areas`,
   `areasWithoutFlows`, `hits` (flows whose pinned surface the diff touches), `newSurfaceSignals`,
   `marker`, `verdict` and `explanation`. Two things to know: `--pr` reads the PR's title, body and
   base from GitHub but classifies **the local checkout's** diff, so the branch has to be checked
   out first; and an empty diff comes back `EXEMPT (no-changes)`, which means "nothing to classify",
   not "no flow needed".

   If the script is not in the checkout: read `FLOWS.md` and classify by hand — does any listed
   flow's area, endpoint or route appear in your diff (update it), or is this user-visible surface
   no flow covers (new flow)?

3. Resolve the ticket. If a Linear integration is available, read the issue — title, description,
   `## Acceptance Criteria`, `## Scope`, and the parent epic. If not, ask the user to paste the
   acceptance criteria or point you at the doc. Do not infer the acceptance criteria from the diff:
   the diff says what changed, the ticket says what a user is now supposed to be able to do.

4. If the ticket or branch names a feature, find its design doc with the procedure in
   [references/design-doc-mining.md](references/design-doc-mining.md) — read it when you have a
   ticket id, a feature name, or a `feat(<scope>)` PR title and no doc in hand. Read only the
   sections that map to a lane: PRD requirements and success criteria feed the steps; UI-UX
   interaction flows and empty/error states feed the UI lane; the API contract feeds the API lane;
   lifecycle, edge cases and data stores feed the storage lane and the poll budget; a `TEST_PLAN.md`
   § E2E scenarios, where one exists, is a ready-made step list.

5. Check the flow you are about to plan against
   [references/harness-gaps.md](references/harness-gaps.md) — read it before every plan. A flow that
   needs a second user, a per-test empty org, a file upload, a custom OTLP shape, a programmable LLM
   answer, an email sink, or a service the stack does not start is **blocked on a harness
   extension**. That extension is its own piece of work, planned with the user, never assembled
   inside a spec.

### 1. Interview — ask only when one of these is true

One question per message, multiple choice where the options are known, and say which classification
step 0 produced before you ask.

- More than one user goal is plausible for the change.
- The behaviour may not be reachable on the OSS image or this stack profile — EE-only, `serving`,
  `code-executor`, the simulation runner, or a CDC table whose mirror is known-drifted.
- Which backend state proves the goal is not derivable from the design doc or the serializer.
- A locator would need a **new** `data-testid` — that is, after grepping product code for the hooks
  that already exist (`grep -rn 'data-[a-z-]*=' frontend/src/sections frontend/src/components`;
  section directories are not named after e2e areas, so grep the whole tree) nothing fits. Say so: a
  new one ships as its own small product PR first, per the README, and until it is released the flow
  cannot run on the managed stack.
- Step 0.5 hit a harness gap.

Nothing else is worth interrupting for. If none of these is true, go straight to the plan.

### 2. Flow plan — HARD GATE

Present the plan in the shape of
[references/flow-plan-template.md](references/flow-plan-template.md) — read that file before writing
the plan — and stop.

**Do not create or edit any file under `e2e/` until the plan is approved by whoever is directing
this session — a person, or the agent orchestrating it.** Not a skeleton, not "just the
annotation", not a draft in a scratch directory to be moved later. A plan nobody has answered is
not an approved plan, and neither is silence. An approval that comes back from the party who gave
you this work _is_ the approval; do not stall for a second one because it did not arrive from a
human.

The plan names: `id` and `area`; `userGoal`; `steps` in order; `backendChecks`; for each check its
lane, its poll budget named by what it waits on, and the `test.setTimeout` sum when budgets chain
past 120 s; the seeding strategy; the minted names and which assertion anchors on which; and the
tags.

### 3. Write

Follow the exemplar's structure in order: imports → pinned constants, each with a comment saying
where it was pinned from → `test('<ID>: <sentence>', { tag, annotation: flowAnnotation({…}) }, fn)`
→ `test.setTimeout(...)` as the first statement → seed → storage lane → UI lane → API lane →
`dispose`.

Every pinned constant carries its provenance in a comment: the frontend file, the serializer, or
"captured with `page.on('request')` from the running app". Wire bodies come from a captured request
or the DRF serializer, never from guessing — the create forms' resolvers rename fields, so the form's
shape is not the wire's shape.

Attach the seeded ids with `testInfo.attach` as you write the seeding step. CI uploads the HTML
report and nothing else, so a failure has to be readable from that report alone — the ids you seeded
are what lets anyone re-query the same rows afterwards.

Then, from `e2e/`:

```
yarn typecheck && yarn catalog
```

`yarn catalog` regenerates `FLOWS.md`. It is generated; never hand-write it or hand-edit it. Commit
it in the same commit as the spec.

### 4. Prove — definition of done

A flow is done when the spec attaches its seeded ids with `testInfo.attach`, so a CI failure is
readable from the report alone, and all four of these have happened, in order.

1. **Iterate in attach mode** against a stack you already have:
   `E2E_APP_URL=… E2E_API_URL=… E2E_COLLECTOR_URL=… E2E_CH_URL=… E2E_PG_URL=… bin/e2e test
flows/<area>/<name>.spec.ts`. Attach mode is for iteration, not for verdicts.

2. **Managed run.** If this branch changes product code the flow depends on, build it into the
   images first — `bin/e2e build backend|frontend|collector` — then `bin/e2e up` with the version
   variables the build prints, and run the flow there. Managed mode otherwise runs published
   `:latest` images, so a flow that needs your change would pass or fail for the wrong reason.

3. **Fallibility proof, once per `backendCheck`.** Perturb the anchor the check rests on, run the
   flow, and read the failure — perturbing **one side only**. In `flows/evals/eval-task.spec.ts` the
   `verdict` constant feeds both the judge template's `instructions` and the assertion, so editing
   it moves expected and actual together and the run stays green. Edit the input alone:

   ```
   # in the spec, temporarily — inside the template `instructions` string only:
   #   "explanation": "${verdict}-BROKEN saw {{output}}"
   bin/e2e test flows/evals/eval-task.spec.ts
   # → expect(received).toBe(expected)
   #     Expected: "e2e-verdict-w0-m1x2y3 saw llm"
   #     Received: "e2e-verdict-w0-m1x2y3-BROKEN saw llm"
   ```

   Perturb the side under test, never a value shared by the assertion and the input — if the run
   stays green, the perturbation proved nothing. Quote the failure in your notes, restore the file,
   and run green again. A check that still passes perturbed is not a check.

4. **Record the evidence**: the exact commands, the last green run's summary, the quoted failure per
   check, and the `E2E: new <ID>` (or `updated <ID>`) line for the PR body.

### 5. Hand off

Spec and `FLOWS.md` in the same commit. No `test.only` — CI runs `forbidOnly`. No retries: a flow
that passes on the second attempt is telling you something true. Never pass `--grep-invert` on the
command line; it replaces the config's own value and un-quarantines the whole suite. Quarantine only
with an owner, a ticket and an expiry within 45 days. The PR body carries the evidence from step 4.

State plainly which of step 4's runs you did not do, and why. A flow handed over without its managed
run or its fallibility proof is a draft; naming that is what stops a reviewer reading it as coverage.

## Quick reference

Signatures, the pinned app facts (endpoints, page sizes, wire bodies, grid and filter selectors, the
in-container gateway address), the mock-LLM contract and the ClickHouse/Postgres binding traps are in
[references/harness-cheatsheet.md](references/harness-cheatsheet.md) — read it while writing the
spec.

| Primitive        | Import from             | Use it for                                     |
| ---------------- | ----------------------- | ---------------------------------------------- |
| `test`, `expect` | `../../lib/fixtures`    | every flow that starts signed in               |
| `test as base`   | `@playwright/test`      | only a flow that drives login or signup itself |
| `actor` fixture  | `../../lib/fixtures`    | worker-scoped org, keys, authed `ApiClient`    |
| `probe` fixture  | `../../lib/fixtures`    | `ch` / `pg` / `apiList` reads                  |
| `sendTrace`      | `../../lib/otlp`        | seeding observe data through the collector     |
| `POLL`           | `../../lib/state-probe` | the shared wait budgets below                  |
| `E2E`            | `../../lib/env`         | endpoints — never hardcode a port              |
| `flowAnnotation` | `../../lib/flow-meta`   | the catalog contract                           |

Pick a budget by what you are waiting on, never by which flow you are writing:

| Budget                               | Waits on                                                                                       |
| ------------------------------------ | ---------------------------------------------------------------------------------------------- |
| `POLL.SPAN_VISIBLE` (15 s)           | anything fi-collector writes — `spans`, curated `traces`                                       |
| `POLL.EVAL_RESULT` (90 s)            | a Temporal eval task reaching a terminal status                                                |
| `POLL.CDC_VISIBLE` (180 s)           | any CDC-fed table: `tracer_eval_logger`, `model_hub_score`, datasets, prompts, simulate, usage |
| `POLL.ASYNC_JOB` (60 s)              | other Temporal work                                                                            |
| `UI_READY` (60 s, per-spec constant) | every browser `expect`, `toHaveURL`, `waitForResponse`                                         |

## Red flags — STOP

| Thought                                                                                                                | Reality                                                                                                                                                                                                                                                                                                                 |
| ---------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| "I'll add the annotation later."                                                                                       | `@flow` without an annotation breaks `yarn catalog` for the whole suite; the catalog is CI's first gate, before the stack boots.                                                                                                                                                                                        |
| "`toContainText` is fine here."                                                                                        | It passes with the feature broken. The README's rule is exact sets: `toHaveText([alpha])` proves the other rows are gone.                                                                                                                                                                                               |
| "I'll raise the project timeout."                                                                                      | That slows every flow for one flow's budget. Raise this test's own ceiling with `test.setTimeout`, sized as the sum of the chained budgets.                                                                                                                                                                             |
| "Skip the managed run, CI will catch it."                                                                              | CI builds the frontend from source, but a backend, collector or gateway your PR does not touch runs as the released `:latest`. A flow depending on an unreleased change to one of those is not exercised there at all.                                                                                                  |
| "This spec has never been executed; the first run will confirm the locators."                                          | Then the first run is the author's, not the reviewer's. An unexecuted spec is a draft, and the plan you agreed to said "proven".                                                                                                                                                                                        |
| "It typechecks, it collects, the catalog renders — it's verified."                                                     | All three pass on a flow whose every assertion is wrong. They check the shape, not the claim. Only step 4.3 checks the claim.                                                                                                                                                                                           |
| "Assert on the first row / on the count."                                                                              | `actor` is shared by every spec in the worker and nothing is ever deleted. Anchor on a value you minted; measure a delta where prior rows exist.                                                                                                                                                                        |
| "The mock can just return X."                                                                                          | It cannot be programmed. It echoes the last user message with fixed usage. Shape the prompt so the echo parses — see the cheatsheet's recipe.                                                                                                                                                                           |
| "Use `127.0.0.1`."                                                                                                     | Login skips reCAPTCHA only when the request `Host` contains `localhost`. Address the stack as `localhost`.                                                                                                                                                                                                              |
| "I'll add the `data-testid` in this PR."                                                                               | Grep first — the hook you need usually exists. If it genuinely does not, the README still wants the attribute as its own small product PR, and your local managed run uses the released frontend until you `bin/e2e build frontend`. CI does build the frontend from source, so a same-PR attribute is exercised there. |
| "There's no `aria-pressed`, so I'll assert the computed style — I check the unselected one too, so it can still fail." | That answers a different objection. A locator bound to a CSS value breaks on any restyle and cannot be verified without the stack. It is the `data-testid` case; treat it as one.                                                                                                                                       |
| "One retry won't hurt."                                                                                                | `retries: 0` is deliberate. A flow that passes on attempt two is reporting a real product behaviour; a retry throws that signal away. Quarantine with owner, ticket and expiry instead.                                                                                                                                 |
| "I'll read the spans table without `FINAL`."                                                                           | `spans`, `traces` and every CDC-fed table are ReplacingMergeTree. An unmerged read hands you the stale version of a row that moved through a lifecycle.                                                                                                                                                                 |
| "This wait is a UI refresh, not an eventually-consistent store, so the default budget is right."                       | The 10 s expect default is not a first-paint budget. A UI step that takes 10 s alone takes a minute beside other workers. Use the spec's `UI_READY`.                                                                                                                                                                    |
| "`FLOWS.md` is generated text; I'll hand-produce what the generator would emit."                                       | The gate is a byte compare, and the file says "do not edit" in its own header. Run `yarn catalog`.                                                                                                                                                                                                                      |
| "`login.spec.ts` bypasses the fixtures, so I can assemble the actors I need by hand."                                  | That file is a documented single-actor exception because it drives the login UI. Multiple identities is a harness gap: propose the `lib/` extension, do not build it inside a flow.                                                                                                                                     |
| "The product's own path doesn't reach that state, so I'll drive the API instead."                                      | You have just changed what the flow proves. That is an interview question, not an implementation detail.                                                                                                                                                                                                                |
| "The commit is mostly refactor; I know which claim is the user-visible one."                                           | Two agents reading the same commit picked two different goals. If more than one goal is plausible, ask.                                                                                                                                                                                                                 |
| "The diff tells me what changed; I don't need the ticket."                                                             | The diff says what changed. The ticket says what a user can now do — which is what the flow asserts, and what its `userGoal` has to state.                                                                                                                                                                              |

## Common mistakes

Read [references/footguns.md](references/footguns.md) when a run fails for a reason you did not
predict — it is the full symptom → cause → fix list. The eight that bite first:

- Title is not exactly `<ID>: <sentence>` — the catalog renders the id twice and quarantine by id
  stops matching.
- `area` in the annotation does not equal the `flows/<area>/` directory — nothing validates it; it
  silently opens a new section in `FLOWS.md`.
- Forgetting `yarn catalog` after touching a title, tag or annotation — CI fails before boot.
- `spans.trace_id` bound as anything but `{t:String}`, or curated `traces` looked up by a
  `trace_id` column that does not exist (it is keyed by `id`, bound `{t:UUID}`).
- ClickHouse `count()` compared as a number — it comes back a string; wrap it in `Number(...)`.
- `probe.apiList` used on an endpoint that does not return `{ result: { table } }` — it hard-codes
  that envelope.
- In-container callers given a host URL — the eval judge runs in the worker container and must
  address the gateway over the compose network.
- `yarn typecheck` skipped — CI does not run it, and Playwright strips types without checking them.

## Out of scope

- Retrofitting `data-testid` across product code in this PR. One attribute, its own small PR, only
  where the semantics are genuinely ambiguous.
- Extending `e2e/lib/` from inside a spec. A new fixture, probe or seeder is its own piece of work,
  planned with the user.
- Flows for services this stack profile does not start — `serving`, `code-executor`, the simulation
  runner — or assertions on surfaces whose CDC mirror is known-drifted.
- Committing. Prepare the spec, the regenerated `FLOWS.md` and the evidence; the user decides when
  it lands.
