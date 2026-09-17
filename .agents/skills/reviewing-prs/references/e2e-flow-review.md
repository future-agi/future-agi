# Reviewing a change under `e2e/`

Read this when any file under `e2e/` is in the diff. It is the checklist for flow specs, the
generated catalog, and the quarantine file — not for the product code the flow exercises.

A flow is **one user goal, start to finish, asserted in the UI and in the backend state behind it**.
Everything below follows from that sentence. When a new flow arrives without a plan behind it, the
shape it should have had is the flow-plan template in the authoring skill
(`references/flow-plan-template.md` under `writing-e2e-flows`).

## The annotation is a contract

`FLOWS.md` is generated from the annotation, so the annotation is documentation the team will trust
without opening the spec. Review it as strictly as an assertion.

- **Every `steps` entry is actually performed** by the spec body, in that order. A step listing a UI
  action the spec never takes is a false catalog entry.
- **Every `backendChecks` entry is actually asserted.** A check the spec merely polls for, or reads
  and discards, is not asserted.
- Nothing the spec does that matters is **missing** from the annotation.
- The `@flow` tag is present. Without it the test never reaches the catalog, whatever the annotation
  says.
- `id` matches `<AREA>-E2E-<nnn>`, is unique across the suite, and `area` equals the `flows/<area>/`
  directory the file lives in.
- The test title **starts with the id** (`"OBS-E2E-001: …"`), because quarantine matches on a
  substring of the composed title.
- `userGoal` names a user's goal, not a mechanism. "A developer sends a trace and inspects it" is a
  goal; "exercise the span list endpoint" is not.

## Assertions that can fail

An assertion that cannot fail is worse than no assertion, because it reads as coverage.

- **Exact sets, not substrings.** `toHaveText([alpha])` proves the filter excluded the other row;
  `toContainText` passes with the filter broken.
- **Anchored on a minted value** that exists nowhere else — a name or token this test generated —
  rather than on "the first row" or "the count".
- **Deltas** where a pre-existing row would satisfy the check: count before, act, require growth.
- **A state, not a count**, wherever failure has a state: poll for the terminal status so a row that
  arrived and failed is distinguishable from one that has not arrived.
- Every read of a ClickHouse ReplacingMergeTree — the spans table and every CDC-fed table — uses
  `FINAL`. Without it an unmerged read hands back the stale version and the assertion passes on the
  wrong row.
- API-lane reads go through the same list endpoint the UI calls, and the response is typed against
  the one real envelope. **No `||` or `??` fallback chains on response fields**: read the serializer,
  or capture a live request, and code against what it actually returns.
- The flow does not assert on unfiltered eval graphs. That surface reads a table the stack's schema
  flag removes and is expected to fail locally and on the open build.
- **The PR body shows the flow failing.** The authoring skill's definition of done is a fallibility
  proof per `backendCheck`: the anchor perturbed on **one side only**, the run re-run, and the
  resulting failure quoted. Ask for it if it is absent, and check that what is quoted is a real
  assertion failure naming the minted value — not a timeout, and not a perturbation of a constant
  that feeds both the input and the assertion, which moves expected and actual together and leaves
  the run green. A green run and a passing catalog check say the spec is well-formed; only this says
  the assertion can fail. An author who says which of the runs they skipped, and why, has met the
  bar; silence about it has not.

## Waiting

- Waits on a store or an async job use the shared `POLL` budgets from `lib/state-probe.ts`, chosen by
  **what is being waited on** — collector writes, an eval task's own status, any CDC-fed table,
  generic async work — not by which flow is being written. A literal timeout on one of _those_ waits
  is a finding: it means a budget was retuned for one flow instead of at its source.
- Browser waits are the exception, and they have their own convention: every `expect`, `toHaveURL`
  and `waitForResponse` passes `{ timeout: UI_READY }`, where `UI_READY` is a named per-spec constant
  (60 s in the observe flows). The number appearing once, at the constant's declaration, is correct;
  the finding is a bare literal at the call site, or a `UI_READY` whose value drifts from its
  siblings without a reason next to it.
- A flow whose chained budgets can outrun the per-test timeout raises its **own** ceiling with
  `test.setTimeout(...)`, never the project default. The value should be justified as the sum of the
  budgets it chains plus seeding and UI time; if the arithmetic is not stated next to the call, ask
  for it.

## Isolation

- Anything the spec creates is named uniquely per worker and per run — the minted `e2e-<flow>-…`
  shape — and the assertions anchor on that minted value.
- No spec deletes anything global or tears down another flow's data. Isolation comes from the
  per-worker organization plus unique names.
- A spec that tests the login or signup UI itself must **not** import the authenticated fixtures from
  `lib/fixtures`; it uses Playwright's base test, because the shared fixture arrives already signed
  in and would make the assertion vacuous.
- Seeded identifiers are attached with `testInfo.attach` so a CI failure is diagnosable from the
  report alone, without re-running anything.

## Locators

Role, label and text first. `data-testid` only where the semantics are genuinely ambiguous — grid
cells, chart toolbars, option lists — and a new `data-testid` in product code ships as **its own
small PR**, not bundled into the flow PR.

## The catalog

- `FLOWS.md` is generated. It is never hand-edited, and any change to a spec's annotation comes with
  a regenerated `FLOWS.md` **in the same commit**. A spec-only diff means the catalog check fails.
- A `FLOWS.md` diff with no corresponding change under `e2e/flows/**` is the reverse error: the
  catalog was edited by hand.

## Flake policy and quarantine

- **No retries.** A retry count above zero anywhere — config, spec, or command — throws away the
  signal the flake carries.
- **No `test.only`**, and no `--grep-invert` on the command line: the config builds its exclusions
  from one function, and a command-line invert replaces the whole value, silently un-quarantining
  everything.
- Every quarantine entry carries `id`, `reason`, `owner`, `issue`, `added` and `expires`, with
  `expires` **within 45 days** of `added`. Quarantine is a countdown, not a parking space.
- A quarantine entry added in the same PR as the flow it disables needs an explanation in the body;
  shipping a flow already quarantined is shipping nothing.
- Removing a quarantine entry should come with evidence the flow now passes.

## Harness changes

A change under `e2e/lib/`, `e2e/stack/` or `bin/e2e` affects every flow. Check that the harness
self-tests under `e2e/harness/` still cover the primitive that changed, and that a new primitive
arrives with one. A flow that needed a harness extension should show that extension as its own
reviewable change, not hacked into the spec.
