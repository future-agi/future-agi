# Harness cheatsheet

Read while writing a spec. Everything here is verified against the harness and the demonstration
flows; citations are `file:symbol`, so they survive edits to the files.

- [Primitives](#primitives)
- [The flow annotation contract](#the-flow-annotation-contract)
- [Poll budgets](#poll-budgets)
- [Pinned app facts](#pinned-app-facts)
- [Mock LLM contract](#mock-llm-contract)
- [ClickHouse and Postgres binding traps](#clickhouse-and-postgres-binding-traps)
- [Running one flow](#running-one-flow)

## Primitives

### `test`, `expect` — `e2e/lib/fixtures.ts:test`

```ts
import { test, expect } from '../../lib/fixtures';
test('<ID>: <sentence>', { tag, annotation }, async ({ page, actor, probe }, testInfo) => { … });
```

Three fixtures. `actor` is **worker-scoped** (`fixtures.ts:test.actor`): one org per worker, shared
by every spec that worker runs, nothing ever deleted. `context` is overridden
(`fixtures.ts:test.context`) to run `authInitScript` before any app code, so `page` starts signed in.
`probe` is test-scoped and disposed for you (`fixtures.ts:test.probe`).

A flow that drives login or signup itself must **not** import this module — its `context` fixture
seeds tokens before the app boots. Import `test as base` from `@playwright/test` and build the actor
and probe yourself, as `e2e/flows/auth/login.spec.ts` does. That is the one documented exception; it
is not a licence to assemble extra identities by hand (see `harness-gaps.md`).

### `provisionActor` — `e2e/lib/provisioning.ts:provisionActor`

```ts
provisionActor(req: APIRequestContext, label: string): Promise<TestActor>
// TestActor: { email, password, tokens, organizationId, workspaceId, apiKey, secretKey, api }
```

Signs up `e2e-<label>-<runId>@futureagi.com` (`POST /accounts/signup/`), logs in
(`POST /accounts/token/`), reads `GET /accounts/user-info/` for the org and default workspace, then
`GET /accounts/keys/` for the API/secret pair. `api` already carries `Authorization`,
`X-Organization-Id` and `X-Workspace-Id`. The caller owns `req` and must dispose it. Throws if
`default_workspace_id` is still null after one retry.

### `ApiClient` — `e2e/lib/api-client.ts:ApiClient`

```ts
new ApiClient(req, baseURL, headers?)
  .withAuth(tokens, organizationId?, workspaceId?): ApiClient
  .get<T>(path, params?)   .post<T>(path, data?)   .patch<T>(path, data?)   .delete(path)
```

Paths are relative to `E2E.apiUrl` and must start **and end** with `/`, as the Django routes do.
Any status ≥ 400 throws `ApiError{status, path, body}` (`api-client.ts:ApiClient.send`) — an
unexpected 400 arrives as a thrown error, not a failed expect. Type the exact envelope you read;
never add `||` fallback chains for response fields.

### `authInitScript` — `e2e/lib/auth.ts:authInitScript`

Runs inside the browser; sets `localStorage.accessToken/refreshToken/rememberMe` and
`sessionStorage.organizationId/workspaceId`, keys pinned from
`frontend/src/auth/context/jwt/utils.js`. The default `context` fixture already applies it — you
only call it when building your own `BrowserContext`.

### `E2E` — `e2e/lib/env.ts:E2E`

`appUrl`, `apiUrl`, `collectorUrl`, `gatewayUrl`, `chUrl`, `chDatabase`, `pgUrl`. Each is overridable
by the matching `E2E_*` variable, which is what attach mode uses. **Never hardcode a port in a spec.**
These are _host_ addresses: code running inside a container cannot use them.

### `flowAnnotation` — `e2e/lib/flow-meta.ts:flowAnnotation`

```ts
flowAnnotation({ id, area, userGoal, steps, backendChecks }); // → { type: 'flow', description: JSON }
```

### `sendTrace` — `e2e/lib/otlp.ts:sendTrace`

```ts
sendTrace(req, { collectorUrl, apiKey, secretKey, projectName, rootName? })
  : Promise<{ traceId, spanIds: [rootId, childId], projectName }>
```

POSTs OTLP/HTTP JSON to `${collectorUrl}/v1/traces` with `X-Api-Key` / `X-Secret-Key`. Fixed shape:
resource attributes `project_name` and `service.name`; exactly two spans — a root named
`rootName ?? 'e2e.root'` with no attributes, and a child `e2e.llm-call` carrying the single
attribute `fi.span.kind='llm'`. The collector auto-creates the Postgres `tracer_project` row under
the key's org. `traceId` is the dashed UUID as stored in ClickHouse; `spanIds` are 16-hex.
Use a fresh `await request.newContext()` (no `baseURL`) for `req`, and dispose it as the last line.
Any other span shape means writing your own payload — see `harness-gaps.md`.

### `StateProbe` — `e2e/lib/state-probe.ts:StateProbe`

```ts
probe.ch<T>(query, params?)   // ClickHouse HTTP, server-side {name:Type} binding
probe.pg<T>(text, values?)    // node-postgres, $1 placeholders
probe.apiList<T>(path, params?) // actor.api.get(...) unwrapped to body.result.table
```

`apiList` hard-codes the `{ result: { table: T[] } }` envelope
(`state-probe.ts:StateProbe.apiList`) — verified for `list_spans_observe`
(`futureagi/tracer/views/observation_span.py:list_spans_observe`) and `list_projects`
(`futureagi/tracer/views/project.py:list_projects`). For any other endpoint call
`actor.api.get<T>()` and type the real envelope. The probe is read-only: it never mutates and never
truncates.

## The flow annotation contract

`tag` must include `'@flow'` or the spec is silently left out of `FLOWS.md`. `'@smoke'` marks the
fast subset. `'@live-llm'` is grep-inverted out of every ordinary run.

The title must be exactly `<ID>: <sentence>` — the catalog heading strips `${id}: `
(`e2e/scripts/flow-catalog.mjs:renderCatalog`) and quarantine matches the id as a substring of the
composed title (`e2e/lib/quarantine.ts:grepInvertPattern`).

`e2e/scripts/flow-catalog.mjs:extractFlows` fails the whole suite's catalog when a `@flow` test has
no annotation, when any of the five fields is missing or empty, or when two flows share an id. It
does **not** check the id's format or that `area` matches the directory — those are conventions you
have to keep yourself.

## Poll budgets

`e2e/lib/state-probe.ts:POLL`. Pick by what you wait on, not by which flow you are writing. A flow
that waits on a Temporal job and _then_ reads the row it produced out of ClickHouse spends both
budgets in sequence, and `test.setTimeout` must cover the sum plus navigation plus each `UI_READY`.

| Budget         | Timeout | Covers                                                                                          |
| -------------- | ------- | ----------------------------------------------------------------------------------------------- |
| `SPAN_VISIBLE` | 15 s    | anything fi-collector writes: `spans`, curated `traces`                                         |
| `EVAL_RESULT`  | 90 s    | a Temporal eval task reaching a terminal status                                                 |
| `CDC_VISIBLE`  | 180 s   | any CDC-fed table — `tracer_eval_logger`, `model_hub_score`, datasets, prompts, simulate, usage |
| `ASYNC_JOB`    | 60 s    | other Temporal workflow completion                                                              |

Config defaults are 120 s per test and 10 s per expect (`e2e/playwright.config.ts`). The 10 s expect
default is not a first-paint budget: every browser `expect`, `toHaveURL` and `waitForResponse` in the
observe flows passes `{ timeout: UI_READY }` with `UI_READY = 60_000` declared as a spec constant,
because a UI step that takes 10 s alone takes a minute beside other workers. Do the same in a new
flow, whatever area it lives in.

## Pinned app facts

Reusable, already verified by shipped flows. Copy the constant _and_ its provenance comment.

| Fact                  | Value                                                                                                 | Pinned from                                                                                                                                             |
| --------------------- | ----------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Observe span list     | `/tracer/observation-span/list_spans_observe/`, page size 25                                          | `frontend/.../LLMTracing/SpanGrid.jsx:buildParams`                                                                                                      |
| Span-table URL        | `/dashboard/observe/${projectId}/llm-tracing?tab=traces&selectedTab=spans`                            | `LLMTracingView.jsx:handleGroupByChange`                                                                                                                |
| Project list          | `/tracer/project/list_projects/` with `project_type`, `name`, `page_number`, `page_size`              | `frontend/.../ObserveListView.jsx:fetchObserveProjects`                                                                                                 |
| Filter wire item      | `{ column_id, display_name, filter_config: { filter_type, filter_op, filter_value: [v], col_type } }` | `e2e/flows/observe/span-filter-parity.spec.ts:spanNameFilter`                                                                                           |
| Filter panel hooks    | `[data-filter-property-option]`, `[data-filter-value-trigger]`, `[data-filter-value-option]`          | `frontend/.../LLMTracing/TraceFilterPanel.jsx` — the three the shipped observe flow pins, not the only ones that exist                                  |
| AG Grid cell          | `page.locator('.ag-row [col-id="span_name"]')`                                                        | span table                                                                                                                                              |
| Span Evals tab        | `page.getByRole('tab', { name: 'Evals' })`, then `'1/1 passed'`                                       | `e2e/flows/evals/eval-task.spec.ts`                                                                                                                     |
| Login                 | `/auth/jwt/login`, `POST /accounts/token/`; a new org routes to `/auth/jwt/setup-org`                 | `e2e/flows/auth/login.spec.ts`                                                                                                                          |
| Gateway, in-container | `http://agentcc-gateway:8080/v1`, key `local-dev-only-shared-secret-replace-me`                       | `docker-compose.yml` `AGENTCC_INTERNAL_URL` (which is the bare origin — the `/v1` is the OpenAI suffix the spec appends) and `AGENTCC_INTERNAL_API_KEY` |

**Before planning locators, grep product code for hooks that already exist:**
`grep -rn 'data-[a-z-]*=' frontend/src/sections frontend/src/components`. Grep the whole tree, not a
per-area sub-directory: the section directories are named after frontend features, not after e2e
areas, so `frontend/src/sections/observe` does not exist and the observe hooks pinned above live
under `frontend/src/sections/projects/LLMTracing/`. Product code carries roughly sixty distinct
`data-*` attribute names — families such as `data-alert-*`, `data-review-*`, `data-chart-*`, more
`data-filter-*` than the three above, and over a hundred distinct `data-testid`s — so "no hook exists
here" is a claim to verify, not to assume. Role, label and text locators still come first; a hook is
the fallback where the semantics are genuinely ambiguous. Only when nothing already there fits does
the new-`data-testid` ask-condition apply.

Wire bodies verified against their serializers — the create forms' resolvers rename fields, so the
form's shape is never the wire's shape:

```ts
// custom judge model — POST /model-hub/custom_models/create/
{ model_provider: 'openai', model_name, input_token_cost: 0, output_token_cost: 0,
  config_json: { key, api_base } }
// eval template — POST /model-hub/eval-templates/create-v2/
{ name, eval_type: 'llm', instructions, model, output_type: 'pass_fail', pass_threshold: 0.5 }
// eval config — POST /tracer/custom-eval-config/
{ project, eval_template, name, model, mapping: { output: attr },
  config: { mapping: { output: attr } }, error_localizer: false }
// eval task — POST /tracer/eval-task/  (fields per futureagi/tracer/serializers/eval_task.py)
{ name, project, evals: [configId], filters: { project_id, date_range: [isoFrom, isoTo] },
  run_type: 'historical', row_type: 'spans', spans_limit: 100000, sampling_rate: 100 }
```

`sampling_rate: 100` matters — the create form defaults the slider to 50, which samples seeded spans
away. Saving a custom model runs a **live completion** before it persists, so a broken
worker → gateway → mock hop fails at `custom_models/create/`, not later inside the workflow.

## Mock LLM contract

`e2e/stack/mock-llm/server.mjs`. OpenAI-compatible, deterministic, stateless, and **not
programmable per test**.

- `POST /v1/chat/completions` → `choices[0].message.content` = `echo: ` + the content of the last
  message whose role is `user`. `finish_reason: 'stop'`. `usage` is always
  `{ prompt_tokens: 7, completion_tokens: 7, total_tokens: 14 }` — a fingerprint no real provider
  produces, so a flow can assert on it. `stream: true` yields chunks that concatenate to the same
  content.
- `GET/POST /v1/models` → `gpt-4o-mini`, `gpt-4o`, `text-embedding-3-small`.
- `POST /v1/embeddings` → one 8-dimension vector of `0.125` per input.
- No tool calls, no JSON mode, no error or latency injection, no non-OpenAI formats.

Reachable only as `http://mock-llm:8080` inside the compose network; the gateway
(`e2e/stack/gateway.e2e.yaml`) routes `gpt-4o`, `gpt-4o-mini`, `gpt-4-turbo`, `gpt-3.5-turbo`, `o1`
and `o1-mini` to it.

**Making an eval pass through it.** No shipped eval template can complete against the mock — every
one needs the judge to compose its own JSON. Author your own template whose `instructions` are a
literal reply:

```ts
instructions: `Reply with exactly this JSON: {"result": "Pass", "explanation": "${verdict} saw {{output}}"}`;
```

The backend renders the prompt, substituting `{{output}}` with the mapped span attribute; the mock
echoes it back; the verdict parser extracts `result` and `explanation`. Map `output` to an attribute
that exists — `sendTrace` sets `fi.span.kind='llm'` on the **child** span only, so the child is the
one evaluable span. Mint `verdict` per run: finding it in ClickHouse proves the whole
prompt → gateway → mock → parse → Postgres → CDC → ClickHouse path ran.

## ClickHouse and Postgres binding traps

- Every ClickHouse read takes `FINAL`. `spans`, `traces` and every CDC-fed table are
  ReplacingMergeTree; a Postgres row updated through pending → running → terminal lands as several
  versions, and an unmerged read returns the stale one.
- `spans.trace_id` is the dashed UUID **string** → bind `{t:String}`.
- Curated `traces` has no `trace_id` column — it is keyed by `id` → bind `{t:UUID}`.
- `spans.id` is the 16-hex span id.
- `count()` comes back as a **string** in JSONEachRow → `Number(rows[0].n)`.
- Postgres uses `$1` placeholders and `probe.pg(text, values)`.
- The curated `traces` row is a separate best-effort insert after the span batch — poll it with its
  own `expect.poll(..., POLL.SPAN_VISIBLE)`; do not assume it exists once the spans are visible.
- Poll a **state**, not a count, where failure has a state: `.toBe('completed')` distinguishes a row
  that arrived and failed from one the mirror has not delivered.

## Running one flow

```
bin/e2e test flows/observe/trace-ingestion.spec.ts   # one spec
bin/e2e test --grep OBS-E2E-001                      # by id
bin/e2e test flows/observe/                          # by area
bin/e2e test --grep @smoke                           # by tag
bin/e2e test harness/                                # harness self-tests: "stack wrong" vs "flow wrong"
bin/e2e test flows/evals/ --workers=1                # serially, to rule out local contention
```

From `e2e/`: `yarn typecheck` (not run in CI — run it yourself), `yarn catalog`,
`yarn catalog:check`, `yarn selftest`. Failure artifacts land in `e2e/test-results/<test>/trace.zip`
(`npx playwright show-trace <path>`) and `e2e/playwright-report/` (`bin/e2e report`).
