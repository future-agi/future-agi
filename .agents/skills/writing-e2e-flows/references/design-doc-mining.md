# Finding and mining the design doc

Read when you have a ticket id, a feature name, or a `feat(<scope>)` PR title and no design doc in
hand. The goal is not to read a design doc — it is to extract the user steps, the acceptance
criteria, the API contract and the storage lifecycle so the flow plan can be written from what the
feature is supposed to do rather than from what the diff happens to touch.

- [Where docs live](#where-docs-live)
- [Section skeleton per doc type](#section-skeleton-per-doc-type)
- [Section to lane map](#section-to-lane-map)
- [Ticket to doc, in seven steps](#ticket-to-doc-in-seven-steps)
- [Area to folder fallback](#area-to-folder-fallback)
- [When there is no doc](#when-there-is-no-doc)

## Where docs live

Design docs live in a **separate private repo**, `internal-docs`, checked out beside `future-agi`
(`../internal-docs`, or wherever `$FUTUREAGI_INTERNAL_DOCS` points). `git pull` before reading —
and read the **working tree**, not `HEAD`: several folders are routinely uncommitted.

There is no index mapping feature to folder. The folder name is the index. Feature docs sit at
`internal-docs/<feature>/`; runbooks sit at the top level; `internal-docs/design/` holds
cross-cutting design notes, explicitly _not_ feature docs.

Four folder shapes:

- **(A) Phased programs** — `<feature>/{PHASES.md, WORKFLOW.md, phase<N>-<name>/}`. Per-phase files
  are fixed: `PRD.md`, `UI-UX-DESIGN.md`, `BACKEND-ARCHITECTURE.md`, `BACKEND-BUILD-GUIDE.md`,
  `FRONTEND-BUILD-GUIDE.md` (observe-revamp, evals-revamp), or `TECH_ARCH.md`, `UX_UI.md`,
  `FRONTEND_IMPL.md`, `BACKEND_IMPL.md`, `TEST_PLAN.md` (annotation-queues).
- **(B) Numbered sets** — `dashboards/{01-prd,02-ux-spec,03-backend-architecture,04-frontend-architecture}.md`;
  also dataset-analytics, e2e-testing-setup, read-write-pg-replica.
- **(C) SCREAMING-CASE design/implementation pairs** — `eval-task-redesign/*-DESIGN.md` +
  `*-IMPLEMENTATION.md`; ch-query-optimization, error-feed, ci-setup, slots-based-running.
- **(D) A single top-level `.md`** — `dashboard-revamp.md`, `WORKSPACE-ISOLATION.md`,
  `graph-scenario-workflow-v3.md`, incident write-ups.

In-repo, `futureagi/docs/` holds a few plans (annotation-queues hardening, CH25 migration) and
`docs/` holds only the commit-checks note. `futureagi/docs/E2E_TESTS.md` describes the **older**
pytest/Go collector suite, not this harness — treat it as history, not as guidance for a Playwright
flow. `internal-docs/TESTING.md` predates this harness and does
not mention it — cite `e2e/README.md` for authoring and
`internal-docs/e2e-testing-setup/02-architecture.md` for rationale.

## Section skeleton per doc type

**PRD.md / 01-prd.md** — Problem Statement → Goals (+ Non-Goals) → What Exists Today (names the
current endpoint, view and frontend component) → Figma Reference → Requirements / Feature Breakdown,
numbered per sub-feature → Interaction States → API Contract (New) → Out of Scope → Success Criteria.
Some add User Personas, Data Model with JSON schemas, Rollout Plan, or User Stories (`US-1…`).

**UI-UX-DESIGN.md / UX_UI.md / 02-ux-spec.md** — Page Structure → Component Breakdown → **Interaction
Flows** (or Screen Flow) → Visual States, Loading States, Empty States, Error States, Success
Feedback → Responsive Behavior. Some carry a **Component-to-API Mapping** section naming which
endpoint each control calls, and modal specs enumerating steps, validation and footer buttons.

**BACKEND-ARCHITECTURE.md / TECH_ARCH.md / 03-backend-architecture.md** — Overview / Decision →
Existing Infrastructure → What Needs to Be Added → **API Contract** per verb (request params +
response JSON), or Endpoint + Request Schema + Response Schema + Query Strategy → Data Flow → Files
Changed Summary → Performance Considerations → Risks, Security, Migration Notes.

**\*-BUILD-GUIDE.md / \*\_IMPL.md** — Files to Modify → URL Registration → **API Examples** (curl-able
request and response) → Testing Scope → Build Order.

**TEST_PLAN.md** (annotation-queues only) — Backend API Tests → Frontend Unit Tests → **E2E Test
Scenarios**. The only doc type carrying explicit E2E scenarios; when one exists, read it first.

**Engine/architecture designs** (e.g. the eval-task redesign) — what it replaces and why → the domain
model → uniqueness invariants → schema changes incl. **Data stores (Postgres + ClickHouse)** →
**Lifecycle flows** → workflow architecture incl. error and timeout handling → **Edge cases and
chosen defaults** → migration and cutover.

**PHASES.md** — per phase a status table (PRD / UI-UX / Backend Arch / Backend Build / Frontend Build
/ Implemented / Tested) and a `### Docs: <subfolder>` line. Post-plan addenda are keyed by ticket.

**WORKFLOW.md** — a **Code Locations** table mapping doc → backend directory, ClickHouse services,
frontend components, API layer file. The fastest way to join a diff's file list to a doc.

## Section to lane map

| Read this                                                                                                    | It feeds                                                         |
| ------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------- |
| PRD Requirements / Feature Breakdown; UI-UX Interaction Flows; modal step lists; TEST_PLAN E2E Scenarios     | `steps` — what the user does, in order                           |
| PRD Success Criteria / Success Metrics; the ticket's `## Acceptance Criteria`                                | the `userGoal`, and what "proved" means                          |
| UI-UX Visual / Empty / Error States                                                                          | UI-lane assertions, and what _not_ to assert on an empty org     |
| BACKEND-ARCH API Contract, Request/Response Schema; BUILD-GUIDE API Examples; UX_UI Component-to-API Mapping | the API lane — the same endpoint the UI calls, and the wire body |
| Schema changes / Data stores; Lifecycle flows; Error & timeout handling; Edge cases and defaults             | the storage lane, and which poll budget the check needs          |
| WORKFLOW Code Locations; Files Changed Summary                                                               | confirming the doc actually governs the files in your diff       |

Two cautions. Design docs quote CDC lag optimistically (~1–5 s); the harness measured ~20.7 s, so
size storage-lane waits from `POLL.CDC_VISIBLE`, not from the doc. And a doc describes intent — where
it disagrees with the serializer or the running app, the code wins and the doc is a finding.

## Ticket to doc, in seven steps

1. **Collect inputs.** `git branch --show-current`; `git log origin/<base>..HEAD --format='%s%n%b'`;
   `gh pr view --json title,body` if a PR exists; `git diff --name-only origin/<base>...HEAD`.
2. **Extract ticket ids.** Match `TH-?\d{3,5}` (case-insensitive) over the branch name, commit
   subjects and bodies (`[TH-####]`, `Closes TH-####`), and the PR title and body. Normalise to
   `TH-####`. None found → skip to step 5.
3. **Resolve the ticket.** If a Linear integration is available, read the issue with its relations:
   title, description (look for `## Acceptance Criteria` and a `## Scope` split into backend and
   frontend file lists), project name, parent, and attachments — the attachments are the PR URLs,
   and a merged PR's body carries "UI steps (manual)" and "Tests written", which are ready-made step
   lists. Recurse once into the parent; epics carry the design context. If no integration is
   available, ask the user to paste the acceptance criteria or point at the doc.
4. **Ticket to doc by text.** No Linear attachment or document ever points at `internal-docs` — the
   only machine-followable link is prose. In the `internal-docs` checkout:
   `grep -rln -E "TH-?(<id>|<parent id>)" --include='*.md' .`, then grep for the ticket's distinctive
   title words. Every hit's top-level directory is a candidate; prefer files named `*DESIGN*`,
   `*PRD*`, `*ARCH*`, or `PHASES.md`. Coverage is partial — a miss here is normal.
5. **Fall back to the area map** below, using the branch slug, the Linear project name, the PR
   scope, and the diff paths.
6. **Confirm by code-path join.** Grep `internal-docs` for a basename from your diff
   (`grep -rln "observation_span.py" .`) and for the touched URL prefix
   (`grep -rln "/tracer/observation-span/"`). A doc that cites your file in "Files Changed Summary"
   or your endpoint in "API Contract" is the authoritative one. If more than one folder matches,
   prefer the one whose `PHASES.md` row for that phase is not yet marked Tested; otherwise the most
   recently modified file.
7. **Pick the phase and read.** Inside the folder, match the ticket or PR title against
   `## Phase N: <name>` and its `### Docs:` line. Open PRD or TECH_ARCH first, then UI-UX for the
   flows, then the API Contract, then TEST_PLAN if it exists. Read only the sections the map above
   names.

## Area to folder fallback

| Diff touches                                                                                                                             | Folder                                                                                    |
| ---------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| `tracer/views/{trace,observation_span,trace_session,saved_view,project}.py`, `frontend/src/sections/projects`, `pages/dashboard/observe` | `observe-revamp/` (+ `ch-query-optimization/`, `clickhouse-analytics/`)                   |
| `tracer/views/eval_task.py`, `tracer/services/eval*`, anything about eval tasks or the reconciler                                        | `eval-task-redesign/`                                                                     |
| `model_hub/views/{standalone_eval,eval_runner,eval_group,…}`, `sections/evals`, eval templates or playground                             | `evals-revamp/`                                                                           |
| `model_hub/views/{annotation_queues,develop_annotations,scores}.py`, `sections/annotations`, labels or queues                            | `annotation-queues/` (+ `futureagi/docs/annotation-queues/hardening-deprecation/PLAN.md`) |
| `tracer/views/{dashboard,charts}.py`, `sections/dashboards`                                                                              | `dashboards/` + `dashboard-revamp.md`                                                     |
| dataset analytics, ClickHouse dataset tables                                                                                             | `dataset-analytics/`                                                                      |
| `tracer/views/feed`, `error_analysis`, `sections/error`                                                                                  | `error-feed/`                                                                             |
| `simulate/`, graph scenarios                                                                                                             | `graph-scenario-workflow-v3.md` (no folder)                                               |
| `agentcc/`, `agentcc-gateway/`                                                                                                           | no design folder — see the coverage CSVs                                                  |
| `ee/falcon_ai`                                                                                                                           | `falcon/`                                                                                 |
| `accounts/` RBAC, workspaces                                                                                                             | `WORKSPACE-ISOLATION.md`, `read-write-pg-replica/` for DB routing                         |
| `e2e/`, `bin/e2e`                                                                                                                        | `e2e-testing-setup/`                                                                      |
| `.github/workflows/backend-ci`                                                                                                           | `ci-setup/`                                                                               |
| release, OSS install                                                                                                                     | `release-process/`, `oss/`                                                                |

## When there is no doc

Many areas have none — gateway, simulate, settings, prompts, agents. Say so rather than inventing
one, and fall back to:

- **`internal-docs/api-ui-e2e-coverage/06-product-feature-map.csv`** — the product hierarchy with
  `primary_routes`, `api_areas` and `coverage_priority` per feature. This is the backlog: it answers
  "what exists and should be tested next", where `FLOWS.md` answers "what is tested".
- **`00-api-inventory.csv`** in the same folder — the most complete endpoint → view file → frontend
  caller map in the org, useful for pinning the endpoint the UI actually calls.
- **The legacy browser smokes** under `frontend/scripts/api-journeys/browser/` — roughly 135 of them,
  named by area. They are not run any more, but they are a per-area inventory of step lists worth
  reading before inventing your own.
- **The ticket plus the code**: the serializer for the wire body, the frontend section for the route
  and the control, and a captured request for anything the form reshapes.
