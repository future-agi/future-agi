# E2E coverage policy

Read this when `node e2e/scripts/e2e-coverage.mjs` returns anything but `pass`, when the
classification looks wrong for the diff you just read, or when a passing `EXEMPT` still names flows
it hit.

The policy is **strict with declared exemptions**: a change the classifier says needs a flow must
either show the flow in the `e2e/FLOWS.md` diff or carry an `E2E:` line in the PR body giving a
reason. **Silence blocks; a stated reason does not.**

## Invoking it

```
node e2e/scripts/e2e-coverage.mjs --pr <n> --json                            # a PR exists
node e2e/scripts/e2e-coverage.mjs --base origin/<base> --body <file> --title "<branch or PR title>" --json
```

`--title` is not optional in the no-PR form: a `feat` title with behaviour files in an area that has
no flow is one of the new-surface signals, and with no title that signal is dead, so the same diff
comes back a classification weaker than the one the reviewer's `--pr` run will produce. Unknown flags
are rejected with exit 2 rather than ignored, so a typo cannot quietly become the no-title case.

Base resolution inside the script is: an explicit `--base` wins, else the base branch of the PR named
by `--pr`, else `origin/dev`. So **do not pass `--base` together with `--pr`** — it overrides the
PR's real base, and on a stacked PR that turns the parent branch's merged work into this PR's diff.
`--head <sha>` selects a head other than `HEAD`.

`--pr` fetches the title, body and base from GitHub, but the diff it classifies is the **local
checkout's**. The checkout must therefore be at the PR's head; `EXEMPT (no-changes)` is the symptom
of getting that wrong.

The exception is an **already-merged** PR: its base branch now contains its head, so the
PR-resolved base yields an empty diff. Pin both ends —
`--pr <n> --base <branch-point-sha> --head <head-sha>` — and say so in the review.

## Path classes

Every changed file gets exactly one class — **the table is ordered and the first match wins**, so read
it top to bottom. Behaviour classes that decide coverage are **B1–B6**; everything else is exempt from
the flow requirement. Note where **B7 sits**: it is matched before the exempt classes, so a compose
file or anything under `e2e/stack/**` never reaches E2, E3 or E5.

| Class  | What it covers                                                                                                                                                                                                                                                                                                                                                                                                                                    | Effect                                                                                                                                                                                   |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **E0** | Generated and lock files: the OpenAPI schema, generated contracts and types, gateway generated contracts, lockfiles, pinned test durations, `e2e/FLOWS.md`                                                                                                                                                                                                                                                                                        | Evidence only — never decides, but the `FLOWS.md` diff is the proof a flow was added                                                                                                     |
| **E1** | Docs: `**/*.md`, `**/*.mdx`, `docs/**`, issue templates, licence and notice files                                                                                                                                                                                                                                                                                                                                                                 | Exempt · `docs`                                                                                                                                                                          |
| **B7** | Stack shape: `docker-compose*.yml` at the root and under `futureagi/`, **all of `e2e/stack/**`\*\*, the backend entrypoint                                                                                                                                                                                                                                                                                                                        | **Not** counted as behaviour · `stack-shape` — no flow required, but the suite must still boot. Matched **before** E2–E5, so these paths never fall through to `tooling` or `tests-only` |
| **E2** | Repo tooling: `.github/**`, hooks, `scripts/**`, root `package.json`, release config, `deploy/**`, env examples, agent-config directories                                                                                                                                                                                                                                                                                                         | Exempt · `tooling`                                                                                                                                                                       |
| **E3** | Tests only: `futureagi/**/tests/**`, `__tests__`, `*.test.*`, `*_test.go`, `conftest.py`, storybook, mocks, and `e2e/**` plus `bin/e2e` — **except `e2e/stack/**`, which B7 already claimed**. A test lives in a test *directory*: `test\_\*.py`is deliberately **not** a rule of its own, because`futureagi/simulate/\*\*/test_execution.py`is the product's test-execution domain, and`frontend/src/api/tests/` is a production API module (B2) | Exempt · `tests-only`                                                                                                                                                                    |
| **E4** | Services the E2E stack does not start: model serving, code executor, the simulation runner                                                                                                                                                                                                                                                                                                                                                        | Exempt · `not-in-stack`                                                                                                                                                                  |
| **E5** | Backend internals with no request, response or UI effect: settings, logging, telemetry, licensing, management commands, constants, types, requirements, Dockerfiles — plus every backend path not matched by a behaviour rule                                                                                                                                                                                                                     | Exempt · `backend-internal`                                                                                                                                                              |
| **E6** | Gateway code off the exercised path (everything under the gateway that is not B6)                                                                                                                                                                                                                                                                                                                                                                 | Exempt · `gateway-internal`                                                                                                                                                              |
| **E7** | Frontend non-behaviour: theme, styles, assets, locales, build config, `package.json`, presentational leaf components                                                                                                                                                                                                                                                                                                                              | Exempt · `cosmetic`                                                                                                                                                                      |
| **E9** | Anything the table does not match                                                                                                                                                                                                                                                                                                                                                                                                                 | Exempt · `unclassified` — inspect it yourself; an unclassified behaviour file is a classifier gap worth reporting                                                                        |
| **B1** | Frontend routes, pages, dashboard navigation config                                                                                                                                                                                                                                                                                                                                                                                               | Behaviour — strong new-surface signal on additions                                                                                                                                       |
| **B2** | Everything else under `frontend/src/**` — feature sections, API modules, hooks, contexts, data-bearing shared components                                                                                                                                                                                                                                                                                                                          | Behaviour, mapped to an area by directory                                                                                                                                                |
| **B3** | Backend API surface: `urls.py`, `views/`, `serializers/`, `contracts.py`, routers, permissions, middleware, capabilities, authentication                                                                                                                                                                                                                                                                                                          | Behaviour, mapped to an area by app                                                                                                                                                      |
| **B4** | Migrations and the ClickHouse schema tree                                                                                                                                                                                                                                                                                                                                                                                                         | Behaviour                                                                                                                                                                                |
| **B5** | The collector (`fi-collector/**`)                                                                                                                                                                                                                                                                                                                                                                                                                 | Behaviour — the ingest path two observe flows pin                                                                                                                                        |
| **B6** | Gateway on the exercised path: the chat handler, the OpenAI provider, registry, pipeline, routing, models, streaming, config                                                                                                                                                                                                                                                                                                                      | Behaviour                                                                                                                                                                                |

## Decision procedure

1. Partition the changed files into classes. Let `R` = the files in B1–B6.
2. `R` empty → **`EXEMPT`**, with `autoReason` taken from the dominant exempt class (generated files
   never win the tie). No marker required, and no nagging: a docs or tooling PR passes in silence.
3. `R` non-empty and a **new-surface signal** fired → **`NEW-FLOW`**, naming the area(s). Signals: an
   added `path:` entry in a routes file, a genuinely new key in `frontend/src/routes/paths.js` (a key
   that is added and not also removed — editing a value is not a signal), a new file under `pages/`,
   an added `path(` / `re_path(` / `.register(` line in a `urls.py`, a new file under `views/`, a
   migration creating a model or a field a same-PR serializer exposes, a new collector endpoint, or a
   `feat` title with behaviour files in an area that has no flow. Files the table classes as E0–E3 —
   specs, unit tests, docs, tooling — never raise a signal, whatever their path looks like.

   `areas` can be empty on a `NEW-FLOW`: it is derived from B-class files that map to an area, and a
   route-only diff (`frontend/src/routes/**` is B1, which carries no area) has none. An empty `areas`
   list is not evidence the classification is wrong.

4. `R` non-empty, no signal, but the diff **hits a pinned surface** → **`UPDATE-EXISTING`**, naming
   the flow ids. A hit means an added or removed line contains a string literal one of the flows
   pins: an API path, an app route, a table name, a grid or filter selector, the gateway route.
5. Otherwise → **`UNDETERMINED`**: behaviour files in an area, no signal, no hit. The author declares.

A new-surface signal outranks a pinned-surface hit: a PR that both adds a route and touches a pinned
endpoint is `NEW-FLOW`, because the new screen is the thing with no coverage.

## Verdicts

Each marker kind carries its own check, and the kind's check has to pass too — a well-formed marker is
necessary, never sufficient. The exception is `EXEMPT`, where no declaration is owed at all and the
marker is not examined.

| Marker kind         | Its own check                                                                 |
| ------------------- | ----------------------------------------------------------------------------- |
| `new <ID>`          | The `FLOWS.md` diff adds a `### <ID>` heading                                 |
| `updated <ID>`      | A changed file under `e2e/flows/**` contains `<ID>`                           |
| `covered-by <ID>`   | `<ID>` already exists in `FLOWS.md` (spec unchanged; the CI run is the proof) |
| `exempt (<reason>)` | `<reason>` is one of the known reasons, or `harness-gap <what>`               |

| Classification    | Passes when                                                                           | Otherwise                                                                                             |
| ----------------- | ------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| `EXEMPT` (auto)   | Always — no marker needed, none requested, and a malformed one is not held against it | —                                                                                                     |
| `UPDATE-EXISTING` | Any marker kind whose own check passes — including `E2E: exempt (<reason>)`           | No marker → `needs-marker`. A marker whose own check fails → `block`                                  |
| `NEW-FLOW`        | **`E2E: new <ID>` only**, with its own check passing                                  | Any other kind, `exempt` included → `block`, "reviewer override required". No marker → `needs-marker` |
| `UNDETERMINED`    | Any marker kind whose own check passes — including `E2E: exempt (<reason>)`           | No marker → `needs-marker`. A marker whose own check fails → `block`                                  |

`NEW-FLOW` is the only classification that restricts the kind; everywhere else a declared exemption
passes. A malformed marker is a `block` on every classification that owes a declaration — and so is a
second `E2E:` line — but not on `EXEMPT`, so an author who copies the exemption word the tool just
printed is never blocked on a diff that needed no marker in the first place.

Exit code is 0 on `pass`, 1 on everything else.

### Mapping a verdict into the review

- `pass` → fill the `## E2E coverage` line and write no finding.
- `needs-marker` → **P1 "E2E coverage undeclared"**. Quote the classification, the areas, and the
  exact `E2E:` line the author should add (the script's `explanation` already spells it out).
- `block` → **P1**, titled for the cause: "Declared exemption contradicts the diff" for a `NEW-FLOW`
  answered with `exempt`, "Declared flow is not in the catalog" when `FLOWS.md` does not add it,
  "Malformed E2E declaration" for a grammar problem.
- `NEW-FLOW` + `E2E: exempt (...)` is the one case that is not the author's to decide. Ask the human
  one question — accept the override, or hold the PR for a flow — and record their answer.
- `EXEMPT` with `autoReason: no-changes` **reports `pass` and exits 0, and you must not accept it**.
  It means the checkout has no diff against the base: the review is being run on the wrong ref. The
  green exit code is the trap, not the answer. Fix the checkout and rerun.
- **When the gate post-dates the PR** — the script is absent from the base you diffed against — the
  classification still goes in the E2E block, but a missing marker is a **P2**, not a P1. The author
  could not declare against a gate that did not exist. State that reason; do not drop the finding.

## Marker grammar

One line in the PR body. Text inside HTML comments is ignored, so a template's example does not
count; a second live `E2E:` line is an error.

```
E2E: new <ID>[, <ID>…]
E2E: updated <ID>[, <ID>…]
E2E: covered-by <ID>[, <ID>…]
E2E: exempt (<reason>)
```

`<ID>` matches `^[A-Z]+-E2E-\d{3}$`. `covered-by` ids must already exist in `FLOWS.md`.

`<reason>` is one of `docs`, `tooling`, `tests-only`, `not-in-stack`, `backend-internal`,
`gateway-internal`, `cosmetic`, `generated`, `stack-shape`, `merge-back`, `unclassified`, `refactor`,
`test-support`, or `harness-gap <what>` — the last one naming the missing harness capability, so the
gap is a countdown rather than a shrug. Every `autoReason` the tool prints is in that list except
`no-changes`, which names an empty diff — a wrong checkout to fix, never a reason to declare.

## Worked examples

### `UPDATE-EXISTING` — a fix inside a pinned surface

A one-file frontend change to the trace-detail pane (`frontend/src/components/traceDetail/…`).
Classes: one B2 file. No new-surface signal. The diff's hunks touch a route and a grid selector the
observe trace-ingestion flow pins → `UPDATE-EXISTING OBS-E2E-001`.

The author's options are `E2E: updated OBS-E2E-001` if the pinned literal itself moved and the spec
was edited to match, or `E2E: covered-by OBS-E2E-001` if the flow is unchanged and the CI run is the
proof. Silence is `needs-marker` → P1. As reviewer, also confirm the flow's assertions still mean
something: if the PR added a user-visible outcome inside the same user goal, the flow should have
grown an assertion rather than stayed still.

### `NEW-FLOW` — a feature that adds surface

A large PR adding a simulation-ingestion endpoint: a new `views/` file, four added lines in
`urls.py`, a new migration, new frontend sections, plus a compose change. Classes: B3, B4, B2, B7 —
`R` is non-empty and the added `urls.py` lines and the new `views/` file are new-surface signals →
`NEW-FLOW`, area `simulate`.

Passing needs `E2E: new SIM-E2E-001` **and** a `### SIM-E2E-001` heading added in the `FLOWS.md`
diff. `E2E: exempt (refactor)` on this diff is a `block` you must escalate to the human, not accept
quietly. The B7 compose file adds no flow requirement of its own but does mean the suite has to
still boot.

### `EXEMPT` — and which flavour of exempt it is

A PR touching only `.github/workflows/*.yml`: every file is E2, `R` is empty → `EXEMPT (tooling)`,
verdict `pass`, no marker required and none requested. The E2E block reads
`EXEMPT (tooling) · none · marker: n/a · verdict: pass` and the review contains **no** E2E finding.

The `autoReason` is worth reading, because the ordered table makes neighbouring diffs land in
different flavours:

| PR touches only…                                                  | Class                        | Block reads                                                                 |
| ----------------------------------------------------------------- | ---------------------------- | --------------------------------------------------------------------------- |
| `.github/workflows/**`                                            | E2                           | `EXEMPT (tooling) · none · marker: n/a · verdict: pass`                     |
| `**/*.md`                                                         | E1                           | `EXEMPT (docs) · none · marker: n/a · verdict: pass`                        |
| `e2e/flows/**`, `e2e/lib/**`, `bin/e2e`                           | E3                           | `EXEMPT (tests-only) · none · marker: n/a · verdict: pass`                  |
| `e2e/stack/**` or `docker-compose*.yml`                           | **B7**, matched before E2–E5 | `EXEMPT (stack-shape) · none · marker: n/a · verdict: pass`                 |
| backend internals — a ClickHouse query builder, a service, a task | E5                           | `EXEMPT (backend-internal) · <flows, if any> · marker: n/a · verdict: pass` |

**`backend-internal` is the flavour that still needs your eyes.** Pinned hits are computed for every
non-exempt-class file, including E5 ones, but the `EXEMPT` verdict is decided before hits are
consulted — so this row is the one place where the script can name flows on the same line as
`verdict: pass`. That is not a contradiction to reconcile: the classifier is saying "no B-class file
changed, so I am not asking for a declaration", while the hit list is saying "these flows pin a
literal your diff moved". When hits appear under `EXEMPT`, **read the diff yourself** before writing
the block. A backend service change with no `views/` or `serializers/` file beside it can still alter
what a user sees — a filter, a dropdown's contents, a chart's numbers — and if it did, the honest
output is a P2 naming the flow whose assertion should have grown, not a silent `pass`. The script
labels the list `informational` for exactly this reason. Hits under `EXEMPT (docs)`, `(tooling)` or
`(tests-only)` mean nothing by contrast: those classes are excluded from hit scanning entirely.

The `tests-only` and `stack-shape` rows are the pair to keep straight. A spec-only change is `tests-only` — the PR _is_ the E2E
change, so review it with the flow-review checklist rather than asking it for coverage. A change to
`e2e/stack/**` or a compose file is `stack-shape`: no flow is owed either, but the obligation that
replaces it is that the suite still boots, so the question to ask is whether CI ran the harness on
this diff, not whether a flow exists.

Do not manufacture a coverage finding on any of them. "Tooling PRs should have flows too" is not this
policy.
