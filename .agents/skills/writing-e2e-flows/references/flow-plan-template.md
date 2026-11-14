# Flow plan template

Read before writing the plan. The plan is what the user approves; nothing under `e2e/` is created or
edited until they have.

## The contract

Every field is required. "Not applicable" is an answer, but it has to be written down.

| Field                 | What it must say                                                                                                                                          |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `id`                  | The next free `<AREA>-E2E-<nnn>` in that area, taken from `FLOWS.md`. Format `^[A-Z]+-E2E-\d{3}$`.                                                        |
| `area`                | The `flows/<area>/` directory name. Must equal the annotation's `area`. New areas are allowed — say so explicitly and give the prefix.                    |
| `spec path`           | `e2e/flows/<area>/<kebab-subject>.spec.ts`.                                                                                                               |
| `userGoal`            | One sentence, in the user's terms, naming the goal and not the mechanism. This is what the flow claims; if it is narrowed later, the plan is re-approved. |
| `steps`               | What the user does, in order. The list a person could follow by hand.                                                                                     |
| `backendChecks`       | What must be true behind the UI. One line each; these become the annotation's `backendChecks` verbatim.                                                   |
| lane per check        | UI, API (the same list endpoint the UI calls), or storage (`probe.ch` with `FINAL` / `probe.pg`). Say which for every check.                              |
| poll budget per check | Named by **what it waits on**: `SPAN_VISIBLE`, `EVAL_RESULT`, `CDC_VISIBLE`, `ASYNC_JOB`, or `UI_READY` for browser waits.                                |
| `test.setTimeout`     | The sum, shown as arithmetic, when the budgets plus navigation plus each `UI_READY` can exceed 120 s. Say "not needed" and why if they cannot.            |
| seeding               | `sendTrace` or API writes. For API writes, name where each wire body is pinned from — a captured request or the serializer.                               |
| minted names          | The `e2e-<flow>-${testInfo.workerIndex}-${Date.now().toString(36)}` values, and which assertion anchors on which.                                         |
| tags                  | `@flow` always. `@smoke` only if it is fast _and_ exercises a subsystem no other smoke does. `@live-llm` never for the default suite.                     |
| fallibility plan      | For each `backendCheck`, the anchor you will perturb to prove it can fail.                                                                                |
| harness gaps          | Any gap this flow touches, and which of the three options you are proposing (narrow / split / drop). "None" if none.                                      |
| open questions        | Anything the interview did not settle.                                                                                                                    |

## Area prefixes

From `AREA_OF_E2E_DIR` in `e2e/scripts/e2e-coverage.mjs` — the classifier and the flow ids share one
table, so a new area needs a prefix added there too.

| Directory        | Prefix   | Directory       | Prefix   |
| ---------------- | -------- | --------------- | -------- |
| `auth`           | `AUTH`   | `simulate`      | `SIM`    |
| `observe`        | `OBS`    | `gateway`       | `GW`     |
| `evals`          | `EVAL`   | `error-feed`    | `ERR`    |
| `tasks`          | `TASK`   | `alerts`        | `ALERT`  |
| `datasets`       | `DATA`   | `dashboards`    | `DASH`   |
| `prompts`        | `PROMPT` | `settings`      | `SET`    |
| `agents`         | `AGENT`  | `get-started`   | `GST`    |
| `prototype`      | `PROTO`  | `falcon-ai`     | `FALCON` |
| `knowledge-base` | `KB`     | `sdk-ingestion` | `SDK`    |
| `annotations`    | `ANNOT`  |                 |          |

## A filled example

This is the plan that `e2e/flows/observe/span-filter-parity.spec.ts` was built from.

---

**id** `OBS-E2E-002` · **area** `observe` · **spec** `e2e/flows/observe/span-filter-parity.spec.ts`

**userGoal** — A developer narrows the span table to one operation and trusts the result set.

**steps**

1. seed two traces with distinct root-span names
2. open the project's span table by its span-view URL
3. filter by one span name
4. read the filtered table

**backendChecks**

| #   | Check                                                                                               | Lane                                                                         | Budget                                                 |
| --- | --------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------- | ------------------------------------------------------ |
| 1   | all four spans of both traces present in ClickHouse `spans` (`FINAL`)                               | storage (`probe.ch`)                                                         | `SPAN_VISIBLE` — fi-collector writes `spans`           |
| 2   | project row auto-created in Postgres `tracer_project`, scoped to the actor org                      | storage (`probe.pg`)                                                         | none; the row exists once check 1 has passed           |
| 3   | the UI row set equals the span-list API result for the equivalent filter — same ClickHouse dispatch | UI + API (`probe.apiList` on `/tracer/observation-span/list_spans_observe/`) | `UI_READY` (60 s) on the grid and on `waitForResponse` |

**test.setTimeout** — `240_000`. `SPAN_VISIBLE` (15 s) + navigation + 3 × `UI_READY` (180 s) already
passes the 120 s default; the remainder is headroom so a slow run fails on the assertion that ran out
rather than on the outer timeout.

**seeding** — two `sendTrace` calls against `E2E.collectorUrl` with the actor's key pair, sharing one
`projectName`, with `rootName` set to `alpha` and `beta` respectively. No API writes, so no wire body
to pin. The filter item the panel puts on the wire _is_ pinned, from a captured request:
`{ column_id: 'span_name', display_name: 'Span Name', filter_config: { filter_type: 'text',
filter_op: 'in', filter_value: [value], col_type: 'SYSTEM_METRIC' } }`.

**minted names** — `suffix = ${testInfo.workerIndex}-${Date.now().toString(36)}`;
`projectName = e2e-obs2-${suffix}`; `alpha = e2e.alpha-${suffix}`; `beta = e2e.beta-${suffix}`.
Check 1 anchors on the two returned `traceId`s. Check 2 anchors on `projectName` plus
`actor.organizationId`. Check 3 anchors on `alpha`: the grid must read **exactly** `[alpha]`, which is
what proves the beta spans were excluded rather than merely not looked at.

**tags** — `['@flow']`. Not `@smoke`: `OBS-E2E-001` already covers the ingestion subsystem in the
smoke set, and this flow's UI leg is slower.

**fallibility plan**

- Check 1 — change one seeded `rootName` after the poll so the expected count cannot be reached;
  expect the count assertion to report the shortfall.
- Check 2 — assert against `actor.organizationId` with one character altered; expect
  `toHaveLength(1)` to receive `0`.
- Check 3 — swap the asserted grid text from `[alpha]` to `[beta]`; expect `toHaveText` to quote the
  actual row set. This is the one that matters: it is the check that would silently pass under
  `toContainText`.

**harness gaps** — none. Two traces in one project is within `sendTrace`'s fixed shape, and the three
`data-filter-*` hooks the panel needs already exist in product code.

**open questions** — none.

---

## Presenting it

Show the plan and stop. Do not create the spec file, a skeleton, the annotation, or a scratch draft
to be moved in later. If the user answers with a change, restate the affected fields and confirm
before writing.
