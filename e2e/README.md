# FutureAGI E2E

End-to-end flows that drive the whole product as one running system: a Chromium browser against the
production frontend image, the real Django API, the real fi-collector, the real agentcc-gateway, and
the real datastores (Postgres, ClickHouse, Temporal, Redis, MinIO) plus the PeerDB CDC
mirrors that carry eval and annotation data from Postgres into ClickHouse. Every flow asserts what
the user sees **and** the backend state that must exist behind it. The only fake in the stack is the
LLM provider, mocked at the HTTP boundary _behind_ the real gateway, so routing, streaming and cost
accounting still execute. This workspace is standalone — its own `package.json`, `yarn.lock` and
Playwright config — and it imports nothing from `frontend/` or `futureagi/`.

```
┌───────────────────────────  your laptop / CI runner  ───────────────────────────┐
│                                                                                 │
│  @playwright/test (e2e/)                                                        │
│  ├── browser (Chromium) ────────────────► frontend :3100  (nginx, prod build)   │
│  ├── ApiClient (provisioning, API lane) ► backend  :8100  (Granian)             │
│  ├── OTLP seeder (lib/otlp.ts) ─────────► fi-collector :24318 /v1/traces        │
│  └── StateProbe ──┬──────────────────────► backend API   (preferred lane)       │
│                   ├──────────────────────► ClickHouse :28123 (HTTP)             │
│                   └──────────────────────► Postgres   :25432                    │
│                                                                                 │
│  docker compose -p futureagi-e2e                                                │
│    postgres · clickhouse · redis · minio · temporal                              │
│    backend · worker (ALL_QUEUES) · frontend · fi-collector · observation consumer                       │
│    agentcc-gateway ──► mock-llm (OpenAI-compatible, deterministic, no host port) │
│    backend / worker ─► mock-slack (OAuth and chat, host :28081)                  │
│    peerdb (catalog · temporal · flow-api · flow-workers · server · minio · init) │
│      └── CDC mirrors PG→CH: tracer_eval_logger, model_hub_score, datasets,       │
│          prompts, simulate, usage_apicalllog …                                   │
│                                                                                 │
│  deliberately NOT started: serving · sized workers · peerdb-ui   │
└─────────────────────────────────────────────────────────────────────────────────┘
```

Both harness stacks start the dedicated `code-executor` and set
`CODE_EXECUTOR_LOCAL_FALLBACK=false`; they never execute evaluator code in a worker.
`EVAL-E2E-016` uses the shipped templates to check link format entirely offline.
Owned HTTP listeners remain only as negative witnesses: both complete journals must
stay empty after every browser submission and the Temporal harness task. The executor
only joins internal Docker networks, with no default route. `allowed.example` and
`worker-allowed.example` resolve to the listeners at public-numbered `11.23.0.10`
and `11.23.0.11` **inside those networks**, not on the internet; `private.example`
resolves to the first listener's private interface. These aliases and local redirects
make an accidental destination request observable without contacting a real third party.
The listeners never make outbound requests. Their loopback-only journals are at
`http://127.0.0.1:28082/requests` and `http://127.0.0.1:28083/requests`
(`E2E_LINK_CANARY_URL` and `E2E_LINK_WORKER_CANARY_URL` in attach mode).
The browser's local-only request guard remains in place as an additional boundary.

Error Feed flows use the local mock Slack OAuth/API service and inspect its captured messages at
`http://localhost:28081/messages`. They seed issue transitions through the authenticated
`POST /tracer/e2e/feed-issue/` route, which exists only in the distributed E2E backend image.
That image also supplies its own Error Feed entitlement in E2E-only settings; neither fixture is
present in the product image. The E2E API and worker share a test-only integration encryption key
so the worker can decrypt the connection created during OAuth.

Eval results, annotation scores and the dataset/simulation dashboards reach ClickHouse **only**
through the PeerDB mirrors — Django writes those rows to Postgres alone. That is why PeerDB is part
of the stack and why anything asserting on them needs a CDC-sized poll budget.

---

## Quickstart

```bash
# once, from e2e/
yarn install
npx playwright install chromium

# from the repo root
bin/e2e build backend        # include the E2E-only issue seeder and entitlement in a local image
bin/e2e up                   # boot the isolated futureagi-e2e stack
bin/e2e test                 # run the whole suite against it
bin/e2e test flows/observe/  # or one area
bin/e2e test --grep @smoke   # or one tag
bin/e2e test flows/error-feed/  # Slack alert creation, delivery, and management
E2E_RECORD_VIDEO=1 bin/e2e test flows/error-feed/  # save browser recordings in test-results
```

`bin/e2e up` composes `docker-compose.distributed.yml` (the distributed topology) with `e2e/stack/docker-compose.e2e.yml`, using
`e2e/stack/e2e.env` and the Compose project name `futureagi-e2e` (override with
`E2E_COMPOSE_PROJECT` to keep a separate test volume). It starts an explicit service
list — kept in `SERVICES` in `bin/e2e` so the trimmed set is visible in one place, with
`COMPOSE_PROFILES=peerdb` in the env file making the profile-gated PeerDB services startable by
name. The overlay itself is thin: it adds the `mock-llm` and `mock-slack` services, caps ClickHouse at 3 GB (it is the
first thing an out-of-memory runner kills), sets `restart: "no"` on backend and worker so a crash
loop fails the run loudly instead of hiding, and gives collector and observation consumer the same image tag. `up` returns
only after this readiness sequence passes:

1. `docker compose up -d --wait --wait-timeout 300` — every datastore healthcheck, including
   Temporal's slow first boot.
2. `GET http://localhost:8100/health/` — the backend is up **and** migrated (600 s budget).
3. `SELECT count() FROM spans LIMIT 0` over ClickHouse HTTP — the Django ClickHouse boot hook
   logs-and-continues on failure, so `/health/` alone can pass with a missing CH schema.
4. Both observed-attribute indexes must exist in the isolated catalog database.
5. `GET http://localhost:3100/` — nginx is serving the frontend.
6. `peerdb-init` is force-recreated, must exit 0, **and** its log must contain no unexpected
   `ERROR:` lines (180 s budget). Ordering matters: mirrors can only be created once the backend's
   migrations have created the tables they replicate.

Ready looks like this, followed by the PeerDB step:

```
E2E stack ready: app http://localhost:3100  api http://localhost:8100
Waiting for peerdb-init (CDC mirror setup) ...
```

**Timing.** A cold boot on a laptop (fresh volumes, images pulled) takes about 8–9 minutes; the
backend alone spends roughly 6.5 of those minutes on first-run migrations before `/health/` answers,
which is why the budget is 600 s and not 300. Warm boots are far quicker. The suite itself is fast:
the two Observe flows run together in 8–19 s, and the eval flow — the slowest, because it waits out
CDC — takes 20.6–58.5 s (21.1 s in the run recorded for this document).

Ports come from `e2e/stack/e2e.env` and are chosen to collide with neither a normal dev stack nor
`futureagi-test` (1xxxx), so the E2E stack can run side by side with both:

| Service                       | Port          | Service                  | Port          |
| ----------------------------- | ------------- | ------------------------ | ------------- |
| frontend                      | 3100          | Postgres                 | 25432         |
| backend                       | 8100          | ClickHouse HTTP / native | 28123 / 29000 |
| agentcc-gateway               | 28090         | Redis                    | 26379         |
| fi-collector OTLP HTTP / gRPC | 24318 / 24317 | MinIO API / console      | 29005 / 29006 |
| observation Kafka             | 29093         |                          |               |
| fi-collector admin            | 29464         | Temporal                 | 27233         |
| peerdb-server                 | 29900         | peerdb-ui (not started)  | 23001         |

Always address the stack as `localhost`, never `127.0.0.1`: login skips reCAPTCHA only when the
request `Host` contains `localhost`.

Other subcommands:

```bash
bin/e2e ui                   # Playwright UI mode against the running stack
bin/e2e report               # open the last HTML report
bin/e2e ps                   # compose ps for the futureagi-e2e project
bin/e2e logs --tail 200      # compose logs (add a service name to narrow)
bin/e2e compose <args>       # raw compose passthrough with the e2e env file/project
bin/e2e down                 # stop the stack;  bin/e2e down -v  also wipes volumes
```

---

## The three ways to run it

### 1. Managed (the default)

`bin/e2e` owns the stack and the harness talks to it on the ports above — no environment variables
needed. Managed mode runs the **published** `:latest` images, so it verifies the released system,
not your working tree. Use it for writing flows, for reproducing a CI failure, and as the baseline.

### 2. Attach — point the harness at a stack you already have

Every endpoint in `lib/env.ts` is overridable, so the suite can run against any stack (your normal
dev stack, a remote environment, a second E2E stack):

```bash
E2E_APP_URL=http://localhost:3000 \
E2E_API_URL=http://localhost:8000 \
E2E_COLLECTOR_URL=http://localhost:4318 \
bin/e2e test flows/observe/trace-ingestion.spec.ts
```

| Variable                   | Default                                                      | Needed for                                |
| -------------------------- | ------------------------------------------------------------ | ----------------------------------------- |
| `E2E_APP_URL`              | `http://localhost:3100`                                      | every browser step (`baseURL`)            |
| `E2E_API_URL`              | `http://localhost:8100`                                      | provisioning and the API assertion lane   |
| `E2E_COLLECTOR_URL`        | `http://localhost:24318`                                     | OTLP trace seeding                        |
| `E2E_GATEWAY_URL`          | `http://localhost:28090`                                     | the mock-LLM harness self-test            |
| `E2E_CH_URL` / `E2E_CH_DB` | `http://localhost:28123` / `default`                         | storage-lane ClickHouse assertions        |
| `E2E_CH_PASSWORD`          | `e2e-clickhouse-password`, the managed stacks' `CH_PASSWORD` | the same, as ClickHouse user `default`    |
| `E2E_CATALOG_CH_URL`       | `E2E_CH_URL` (or its default above)                          | optional separate catalog ClickHouse host |
| `E2E_CATALOG_CH_DB`        | `property_catalog`                                           | optional catalog database override        |
| `E2E_PG_URL`               | `postgresql://futureagi:futureagi@localhost:25432/futureagi` | storage-lane Postgres assertions          |

Any flow with storage-lane assertions needs `E2E_CH_URL` and `E2E_PG_URL` as well — pointed at the
attached stack's stores. If you omit them the probe hits the managed stack's ports and the spec
fails at the probe, by design: there is no silent skip.

The existing-customer `test` (see Fixtures below) dates its org through `E2E_PG_URL`, so any flow on
it needs `E2E_PG_URL` too. Once the Simulate restriction (#3502) is in, the attached backend must also
set `RESTRICT_SIMULATE_TO_ENVIRONMENTS_CUTOFF=2026-10-01T00:00:00Z`, as both overlays and the root
`docker-compose.dev.yml` do, or SIM-E2E-001 and `harness/provisioning.spec.ts` fail.

For split-store attachments, set `E2E_CATALOG_CH_URL` and/or `E2E_CATALOG_CH_DB`
for catalog assertions (`probe.catalogCh()`). Source assertions (`probe.ch()`)
continue to use `E2E_CH_URL` / `E2E_CH_DB`. Catalog queries use unqualified table
names so the selected catalog database applies; neither override is required for
the managed stack.

Attach mode is for **iteration**, not for verdicts. It never exercises the nginx artifact or the
runtime `window.__FUTURE_AGI_CONFIG__` injection that the shipped frontend image performs, and the
stack it points at may be configured differently in a dozen ways.

### 3. Testing your local code

Managed mode pulls `:latest`, so local changes are injected by building them into the same
production images CI and users run:

```bash
bin/e2e build backend     # futureagi/future-agi:e2e-local   (futureagi/Dockerfile.oss, context futureagi/)
bin/e2e build frontend    # futureagi/frontend:e2e-local     (context frontend/)
bin/e2e build collector   # futureagi/fi-collector:e2e-local (context fi-collector/)
bin/e2e build executor    # futureagi/code-executor:e2e-local (futureagi/code-executor/)
bin/e2e build all         # all four
```

Each build prints the line to run next; the stack picks the images up through these variables:

| Built target | Variable to set on `bin/e2e up`      | Also retags            |
| ------------ | ------------------------------------ | ---------------------- |
| `backend`    | `FUTURE_AGI_VERSION=e2e-local`       | the `worker` container |
| `frontend`   | `FRONTEND_VERSION=e2e-local`         | —                      |
| `collector`  | `E2E_FI_COLLECTOR_VERSION=e2e-local` | observation consumer   |
| `executor`   | `CODE_EXECUTOR_VERSION=e2e-local`    | —                      |

```bash
bin/e2e build all
FUTURE_AGI_VERSION=e2e-local FRONTEND_VERSION=e2e-local E2E_FI_COLLECTOR_VERSION=e2e-local CODE_EXECUTOR_VERSION=e2e-local bin/e2e up
bin/e2e test
```

The E2E overlay sets the collector and observation consumer image together through
`E2E_FI_COLLECTOR_VERSION`, so both run the same observation protocol.

**Which one to use when.**

- _Frontend iteration_: author and debug specs in **attach mode** against your dev stack — hot
  reload makes the loop instant. Then `bin/e2e build frontend` and a managed run before you push,
  because that is the only run that exercises the artifact users get.
- _Backend iteration_: `bin/e2e build backend` each cycle. Rebuild cost is small — `Dockerfile.oss`
  installs dependencies from `requirements.txt` in an earlier layer, so an edit re-runs only the
  `COPY . .` layer — and the production image keeps the readiness contract honest by running
  migrations. (The collector is a cached Go build, the cheapest of the three; the frontend re-runs
  the full Vite production build every time and is the slowest by far.)
- _Before pushing_: `bin/e2e build all`, then validate fresh installation and retained-data
  upgrade behavior. Only reset volumes belonging to your explicitly disposable test project.

**Why not the dev overlay.** `docker-compose.distributed.dev.yml` looks like the obvious vehicle for local code
and is not one. It hardcodes `FAST_STARTUP: "true"` in `environment:`, which cannot be overridden
from an env file and which _skips migrations_ — on fresh volumes the stack comes up unmigrated. It
serves a Vite dev server instead of the nginx artifact the product ships, it shares the `:dev` image
tags with any dev stack you are running, and its `--reload` watcher restarts the backend mid-test.
Attach mode covers the hot-reload need without any of that. In CI none of this applies: the workflow
builds `:e2e-ci` images from the PR's own code.

### The Standalone stack (`E2E_STACK=standalone`)

With `E2E_STACK=standalone`, every `bin/e2e` subcommand targets the Standalone setup instead:
`docker-compose.yml` (one `app` container that runs the API, the workers, the gateway, the collector
and the UI, next to Postgres and ClickHouse) plus `e2e/stack/docker-compose.standalone-e2e.yml`,
with `e2e/stack/standalone-e2e.env`, as Compose project `futureagi-e2e-standalone`. The harness
ports are the same, so run only one of the two stacks at a time. Its images are built from this
checkout by `bin/lib/build-local.sh`, the recipe of `./bin/install --from-source`, tagged with
that env file's `FUTURE_AGI_VERSION` (`e2esa`) instead of `local`:

```bash
bin/e2e build standalone             # slim backend, frontend, fi-collector, agentcc-gateway, then futureagi/standalone
E2E_STACK=standalone bin/e2e up
E2E_STACK=standalone bin/e2e test
E2E_STACK=standalone bin/e2e down -v
```

`bin/e2e build standalone-app` is the last step on its own: it assembles `futureagi/standalone`
from the four component images already built with that tag. The managed-mock inspection
(`lib/managed-mock.ts`) checks the Standalone stack against its own spec
(`lib/managed-mock-standalone.ts`). The Distributed-only background attestation is skipped there.

### The live observed-catalog backfill harness (`harness/catalog-backfill.spec.ts`)

`lib/catalog-lifecycle.ts` drives the real `fi-observed-catalog-backfill` binary against a running
stack. Because it starts containers, it refuses to run against anything it was not explicitly
pointed at: nothing here has a default, and every guard fails closed.

| Variable                  | Required for              | Meaning                                                                                                                                                                                     |
| ------------------------- | ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `E2E_H5_LIVE`             | the live run              | Must be `1`. Without it only the offline `@h5-guard` assertions run.                                                                                                                        |
| `E2E_H5_DOCKER_CONTEXT`   | the live run              | The Docker context of your approved local runtime. `DOCKER_CONTEXT` must equal it.                                                                                                          |
| `E2E_H5_DOCKER_SOCKET`    | the live run              | That context's `unix:///` socket. The manifest and the live context must both match it.                                                                                                     |
| `E2E_H5_RUNTIME_MANIFEST` | the live run              | Absolute path to a run manifest **outside the checkout** holding the machine pins.                                                                                                          |
| `E2E_H5_CH_USER`          | the live run              | A SELECT-only ClickHouse user. `default`/`admin`/the catalog writer are rejected.                                                                                                           |
| `E2E_H5_CH_PASSWORD`      | the live run              | That user's password. There is no credential fallback.                                                                                                                                      |
| `E2E_H5_AUDIT_CONTAINER`  | Kafka images without CLIs | `1` to allow the task-owned audit container when the broker lacks the shell tools.                                                                                                          |
| `E2E_H5_EXPECT_WRONG`     | fallibility proofs only   | Injects one deliberate defect so a passing assertion can be shown to fail. One of `source-id`, `preview-count`, `checkpoint`, `index-value`, `api-type`, `late-time`; unset for a real run. |

`E2E_H5_DOCKER_CONTEXT` names the context; it does not create one. Declare the context you actually
run this stack under, and export the same value as `DOCKER_CONTEXT` so the harness and the `docker`
CLI cannot drift apart. `DOCKER_HOST` and `DOCKER_TLS_VERIFY` must be unset — the harness only ever
attaches to a local runtime.

`E2E_H5_DOCKER_SOCKET` is the endpoint that context resolves to — read it from
`docker context inspect "$E2E_H5_DOCKER_CONTEXT" --format '{{.Endpoints.docker.Host}}'`. It must be an
absolute `unix:///` path, so a TCP daemon can never be an approved runtime, and it is compared for
exact equality twice: the run manifest's `socket` must equal it, and so must the endpoint the live
context reports at preflight. Any container runtime that exposes a Unix socket works — the harness
has no preference between Colima, Docker Desktop, OrbStack, Rancher or a plain dockerd.

---

## Writing a flow

The `writing-e2e-flows` skill (`/writing-e2e-flows` in Claude Code, `$writing-e2e-flows` in Codex)
walks a developer through this section, the design doc, and the proof-of-done loop; `reviewing-prs`
applies the coverage rule below plus the team standards.

A flow is one user goal, start to finish, asserted in the UI and in the backend state behind it.
Specs live in `flows/<area>/<name>.spec.ts`; the directory is the area and the catalog groups by it.

```ts
import { request } from "@playwright/test";
import { test, expect } from "../../lib/fixtures";
import { sendTrace } from "../../lib/otlp";
import { POLL } from "../../lib/state-probe";
import { E2E } from "../../lib/env";
import { flowAnnotation } from "../../lib/flow-meta";

test(
  "OBS-E2E-001: SDK trace appears in Observe with coherent backend state",
  {
    tag: ["@flow", "@smoke"],
    annotation: flowAnnotation({
      id: "OBS-E2E-001",
      area: "observe",
      userGoal:
        "A developer sends a trace from their app and inspects it in Observe",
      steps: [
        "send an OTLP trace with the org API key",
        "open Observe project list" /* … */,
      ],
      backendChecks: [
        "project row auto-created in PG tracer_project, scoped to the actor org" /* … */,
      ],
    }),
  },
  async ({ page, actor, probe }, testInfo) => {
    /* … */
  },
);
```

### Fixtures (`lib/fixtures.ts`)

- **`actor`** — _worker-scoped_. Provisions a clean account through the test-image-only
  `/accounts/e2e/clean-signup/` endpoint (no demo rows or pending seed request).
  The public `/accounts/signup/` still seeds demos and is exercised by AUTH-E2E-004.
  Creates a brand-new user on a brand-new org
  (`e2e-w<workerIndex>-<runId>@futureagi.com`), logs in, resolves the organization and default
  workspace, and mints the org API/secret key pair. The `futureagi.com` domain keeps
  organization naming consistent. One org per worker makes full parallelism safe
  without truncating anything. It carries
  `actor.api` — an `ApiClient` already sending `Authorization`, `X-Organization-Id` and
  `X-Workspace-Id`. Both stacks set `RESTRICT_SIMULATE_TO_ENVIRONMENTS_CUTOFF` to
  2026-10-01T00:00:00Z, so once the Simulate restriction (#3502) is in, this org is a new
  customer's: in Simulate it sees only Environments.
- **The existing-customer `test`** (`lib/existing-customer.ts`) — the same fixtures, but `actor`'s org
  is dated 2026-09-30, before the cutoff, so it keeps every Simulate page. Import `test` and `expect`
  from it instead of `lib/fixtures` only for a flow that needs something new orgs lose (Agent
  Definition, Scenarios, Run Simulation, a prompt's Simulation tab, Observe's Replay actions), in a
  spec file of its own: `flows/observe/agent-call-log-pager.spec.ts` is the example.
  `harness/existing-customer.spec.ts` pins it.
- **`page`** — the built-in fixture, but its `context` is overridden to run `authInitScript` before
  any app code: it seeds `localStorage.accessToken/refreshToken/rememberMe` and
  `sessionStorage.organizationId/workspaceId`, so specs start already signed in. A spec that tests
  the login UI itself must _not_ use this fixture — `flows/auth/login.spec.ts` imports Playwright's
  base `test` for exactly that reason.
- **`probe`** — _test-scoped_ `StateProbe`, bound to the actor so API-lane reads carry the right
  org/workspace headers. Disposed automatically.

`ApiClient` throws `ApiError{status, path, body}` on any response ≥ 400. Never add `||` fallback
chains for response fields: read the serializer (or capture one live request), and code against the
one real envelope.

### The flow annotation is a contract

`flowAnnotation({ id, area, userGoal, steps, backendChecks })` plus the `@flow` tag is what puts a
spec in `FLOWS.md` — the file is _generated_ from these annotations, so an annotation that claims a
step the spec does not perform, or a backend check it does not assert, is a bug in the same way a
wrong assertion is. Update the annotation in the same commit as the spec. `id` follows
`<AREA>-E2E-<nnn>` (`AUTH-`, `OBS-`, `EVAL-`, …), must be unique, and `area` must equal the
`flows/<area>/` directory. Add `@smoke` to flows worth running as a fast subset.

### The three assertion lanes

Assert in as many lanes as the flow has meaning in, from the outside in:

1. **UI lane** — what the browser shows: `page.getByRole(...)`, `page.getByText(...)`, grid cells.
2. **API lane** — `probe.apiList(path, params)` against _the same list endpoint the UI calls_, which
   exercises the real ClickHouse dispatch and query builders. This is the preferred non-UI lane;
   `OBS-E2E-002` uses it to prove the filtered table and the API agree row for row.
3. **Storage lane** — `probe.ch()` / `probe.pg()`, for cases where the write-path _shape_ is the
   contract: org scoping, foreign keys, curated-table rows, CDC arrival. Read these tables with
   `FINAL`: `spans` is a ReplacingMergeTree, and so is any CDC-fed table whose Postgres row is
   updated through a lifecycle — an eval entry moves pending → running → terminal, a mirror sync
   lands each version in ClickHouse, and an unmerged read hands you the stale one. ClickHouse
   queries bind parameters server-side (`WHERE trace_id = {t:String}`), Postgres queries use `$1`
   placeholders. The probe is read-only — it never mutates and never truncates.

Everything downstream of a write is eventually consistent, so poll with the shared budgets in
`lib/state-probe.ts` rather than inventing timeouts:

| Budget              | Timeout | Covers                                                                                                                                                                         |
| ------------------- | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `POLL.SPAN_VISIBLE` | 15 s    | collector-written spans: OTLP POST → row readable in ClickHouse (measured 1.6–2.6 s; the collector's batch ticker dominates)                                                   |
| `POLL.EVAL_RESULT`  | 90 s    | a Temporal eval task reaching `completed` — the schedule ticks every 10 s, plus worker latency                                                                                 |
| `POLL.CDC_VISIBLE`  | 180 s   | any read of a CDC-fed table (`tracer_eval_logger`, `model_hub_score`, …): PG write → PeerDB mirror → ClickHouse. ~20.7 s typically, but observed past 90 s under parallel load |
| `POLL.ASYNC_JOB`    | 60 s    | generic Temporal workflow completion                                                                                                                                           |

Pick by _what you are waiting on_, not by which flow you are writing: `SPAN_VISIBLE` for anything
the collector writes, `EVAL_RESULT` for a Temporal eval task's own status, and `CDC_VISIBLE` for
every table the PeerDB mirror feeds — eval results, annotation scores, the dataset and simulation
dashboards. A flow that waits on a Temporal job _and then_ reads the row it produced out of
ClickHouse spends both budgets, in sequence.

```ts
await expect
  .poll(async () => {
    const rows = await probe.ch<{ n: string }>(
      "SELECT count() AS n FROM spans FINAL WHERE trace_id = {t:String}",
      { t: seeded.traceId },
    );
    return Number(rows[0].n);
  }, POLL.SPAN_VISIBLE)
  .toBe(seeded.spanIds.length);
```

The per-test timeout is 120 s, so a flow whose budgets can outrun it raises its own ceiling with
`test.setTimeout(...)` rather than lifting the project default for everyone. `EVAL-E2E-001` is the
case in point: it waits out `EVAL_RESULT` (90 s) for the task to complete and then `CDC_VISIBLE`
(180 s) for the row to reach ClickHouse — 270 s worst case before seeding and the UI step — so it
sets `test.setTimeout(300_000)`.

`UI_READY` (60 s in the observe flows) is **one browser action's** first-paint budget, not a
stage's. A stage that chains many actions — a property picker exercised for twenty searches, an
inspection that loads several pages — must be split so that each action (or each short chain
that shares one page load) is its own `test.step(..., { timeout: UI_READY })`; the stage itself
is then bounded by the flow's `test.setTimeout` ceiling, which counts every such step in its
arithmetic. One 60 s budget over twenty actions fails on the shared CI runner at exactly 60.0 s,
at whichever action happens to be running, and reads as three different bugs on three runs.

### Locators

Role, label and text first — the frontend is MUI, its components are accessible, and this is the
same priority `frontend/TESTING.md` states. Reach for `data-testid` only where the semantics are
genuinely ambiguous (AG Grid cells, chart toolbars, filter-panel option lists), add it to product
code as its own small PR, and keep it out of everything else: there is no blanket testid retrofit.
Where a grid column is the subject, addressing the cell directly
(`page.locator('.ag-row [col-id="span_name"]')`) is clearer than a text match that could hit a
tooltip.

### Naming, isolation and cleanup

The `actor` is shared by every spec in a worker, so anything a spec creates must be unique to that
spec: name it `e2e-<flow>-${testInfo.workerIndex}-${Date.now().toString(36)}` and assert against
that name, never against "the first project". Nothing global is ever deleted, and no flow tears
down another flow's data; isolation comes from the fresh per-worker org plus unique names. When you
want a genuinely clean slate, wipe the stack: `bin/e2e down -v && bin/e2e up`.

### Every assertion must be able to fail

An assertion that cannot fail is worse than no assertion, because it reads as coverage. Concretely:

- Assert **exact sets**, not "contains": `await expect(spanNames).toHaveText([alpha])` proves the
  filter excluded the other trace; `toContainText` would pass with the filter broken.
- Anchor on a value **you** minted and that exists nowhere else. `EVAL-E2E-001` puts a random
  `e2e-verdict-<suffix>` string into the judge prompt and then finds it in ClickHouse, which proves
  the entire prompt → gateway → mock → parse → Postgres → CDC → ClickHouse path ran.
- Measure **deltas** where a pre-existing row would satisfy the check: `AUTH-E2E-001` counts active
  auth-token rows before the UI submit and requires the count to grow, because API provisioning has
  already created some.
- Poll a **state**, not a **count**, where failure has a state: the eval flow polls for
  `status = 'completed'`, so a row that arrived and failed is distinguishable from a row the mirror
  has not delivered yet.

### Harness self-tests

`harness/` holds the tests of the harness itself — provisioning, the probe, OTLP seeding, and the
gateway→mock-LLM path. They run in CI alongside the flows and are the fastest way to tell "my flow
is wrong" from "the stack is wrong" after a boot:

```bash
bin/e2e test harness/
```

Local runs use 3 workers (4 or more concurrent browsers overwhelm the stack's single-Granian-worker
backend and the SPA never finishes booting); CI uses 2. Also available from `e2e/`: `yarn typecheck`
and `yarn selftest` (unit tests for the catalog generator).

---

## The flow catalog

`FLOWS.md` is the answer to "what is tested". It is generated — never edit it by hand:

```bash
yarn catalog          # regenerate FLOWS.md from the specs' annotations
yarn catalog:check    # fail if the committed FLOWS.md is stale (CI gate)
```

`yarn catalog` lists the suite with a custom reporter, extracts every `@flow` test's annotation,
groups by area and renders id, title, goal, spec path, tags, user steps and backend checks. It
**fails** — rather than quietly emitting less — when a `@flow` test has no annotation, when a
required annotation field is missing or empty, when two flows share an id, or when the suite
contains no flows at all.

The listing runs with `E2E_INCLUDE_QUARANTINED=1` and `E2E_LIVE_LLM=1` on purpose: the catalog
describes which flows _exist_, so quarantining a flow or gating it behind a live LLM must never
delete it from `FLOWS.md`. CI runs `yarn catalog:check` before booting the stack, so a spec whose
annotation changed without a regenerated catalog fails fast.

---

## Declaring coverage on a PR

Every PR states its E2E coverage in one line of the body — `E2E: new <ID>`, `E2E: updated <ID>`,
`E2E: covered-by <ID>`, or `E2E: exempt (<reason>)`. From `e2e/`, `yarn coverage` classifies your
branch against `origin/dev`, or against the PR's base branch with `--pr <n>`: it maps changed paths
to product areas, checks whether any existing flow pins an endpoint, route, table or selector you
touched, looks for new-surface signals (a new route, `paths.js` key, `urls.py` entry, views module,
page, collector handler, a migration surfaced by a serializer, or a `feat` title in an area that has
no flow yet), and tells you which line to write. Docs, CI, tests-only, and stack-shape changes are
exempt without a line. A behaviour change with no line is `needs-marker`; a `new` id must appear in
the `FLOWS.md` diff; a change that adds user-visible surface needs a new flow, so any other line on
it needs a reviewer to accept it.

`yarn coverage --pr <n>` reads the title, body and base from GitHub. Without a PR, supply the title
yourself — the `feat`-in-an-uncovered-area signal reads it, and nothing else provides it:

```bash
yarn coverage --base origin/<base> --title "<branch or PR title>" --body <file>
```

---

## Quarantine and the flake policy

**`retries: 0`.** A flow that passes only on the second attempt is telling you something true about
the product, and a retry throws that signal away. A spec that flakes twice is quarantined with a
ticket and an owner — never retried.

Quarantine lives in `e2e/.quarantine.json`, a flat array whose entry shape matches the backend's
`futureagi/.test_quarantine.json`:

```json
[
  {
    "id": "OBS-E2E-002",
    "reason": "filter panel option list occasionally renders after the response settles",
    "owner": "@your-handle",
    "issue": "TH-1234",
    "added": "2026-08-25",
    "expires": "2026-10-09"
  }
]
```

- `id` is matched as a **substring of the composed test title**, so use the flow id.
- Keep `expires` within **45 days** of `added` — the standing rule, matching the backend's
  quarantine file; the loader does not enforce the cap, it only stops applying an entry once the
  date has passed, at which point the spec runs again and fails loudly. Quarantine is a countdown,
  not a parking space.
- The loader **fails open**: if the file is missing or unparseable it warns and quarantines nothing,
  because a broken JSON file must not be able to silently disable the suite.
- **Check the spec's recent history before adding an entry.** A flow that failed last week may
  have been fixed since; an entry for a flow that passes only removes coverage. Count failures on
  unrelated changes only: a flow that fails on the branch that is changing it is not flaky.
- Quarantine works the same in every shard, and the shard parity check leaves quarantined flows
  out of both the listing and the results.

Two escape hatches, both environment variables read by `lib/quarantine.ts`:

```bash
E2E_INCLUDE_QUARANTINED=1 bin/e2e test   # run quarantined flows too (verifying a fix)
E2E_LIVE_LLM=1 bin/e2e test --grep @live-llm   # run flows that need a real provider
```

`@live-llm` flows are excluded from every ordinary run — the stack's LLM is the deterministic mock.
Both exclusions are produced by the single `grepInvertPattern()` function feeding the config's
`grepInvert`, deliberately: Playwright's CLI `--grep-invert` _replaces_ the config value entirely, so
passing the live-LLM filter on the command line would silently un-quarantine everything.

---

## CI

`.github/workflows/e2e-ci.yml` runs on PRs to and pushes on `dev`/`main`, plus merge queue entries.
The suite runs as six shards per stack (see [Shards](#shards) below). Each shard builds the images
from the PR's code (tagged `:e2e-ci`) with the registry layer cache, boots its own stack with
`bin/e2e up` and runs its share of the spec files with `bin/e2e test`; on failure it dumps
`bin/e2e ps` and the last 200 log lines and uploads its own Playwright report. A `report` job then
merges every shard's results into the one Playwright HTML report (7-day retention) and proves that
the shards together ran the whole suite. The `E2E Tests Pass` gate fails closed unless every
dependency, the report job included, succeeded or was legitimately skipped.

After the Playwright suite, the last shard runs the SDK live suites from
[futureagi-sdk](https://github.com/future-agi/futureagi-sdk) against the stack it booted, on every
PR and push: `python/tests/platform` calls every operation of the generated Python client and
`src/platform/__tests__/live.test.ts` covers the TypeScript transport, so a backend change that
breaks the SDKs fails here before it ships. The SDK checkout is the branch named like the PR's
branch when the SDK repository has one, else the base branch (`dev` or `main`); a manual run can
name any ref with the `sdk-ref` input. The account comes from `sdkgen/local_api_key.py --signup`
(signup, token, user-info, keys — the same calls as `lib/provisioning.ts`). The suites take about a
minute; they are skipped only when the stack never booted.

The same job runs the suite against the Standalone stack as a nightly schedule and on demand (run
the workflow by hand with `stack: standalone`). A pull request that carries the `e2e-standalone`
label runs it next to the Distributed leg, and `E2E Tests Pass` waits for both. The label counts
only if the PR has it when a run starts, so add it, then push or close and reopen the PR: a re-run
keeps the labels of the run it repeats. Adding a label starts no run, on purpose (see below). The
Standalone leg builds the four component images with the layer cache, assembles the app image with
`bin/e2e build standalone-app`, and boots with `E2E_STACK=standalone`.
The `upgrade` job in `.github/workflows/standalone-ci.yml` runs weekly, on demand, and on a pull
request labelled `upgrade-check` (the same rule: add the label, then push). It installs v1.41.1
with its own `./bin/install` and sends a trace. Then it re-runs `./bin/install` at the head commit
and checks four things: the install stays on the Distributed setup
(`COMPOSE_FILE=docker-compose.distributed.yml` in `.env`), its backend, worker, frontend and gateway
run the head commit's `:local` images, the first account still signs in, and the trace is still
there.

### Shards

`E2E_SHARD=k/N` makes `bin/e2e test` run one of `N` shares of the spec files. CI sets it per matrix
leg (`E2E_SHARDS` in the workflow, currently 6); it works locally too:

```bash
E2E_SHARD=2/6 bin/e2e test     # the second of six shares
```

The split balances **measured time**, not test count (`lib/shard.ts`). Playwright's own `--shard`
splits by count in file order. Most tests here are harness tests that take almost no time, so
`--shard=1/4` would keep nearly every flow, and nearly all of the run time, in the first shard.
The plan gives each spec file its recorded time from `e2e/.test-durations.json`, places the longest
files first, each into the least loaded shard, and is a pure function of the checkout: every shard
of a run computes the same split, and a re-run repeats it. A spec without a recorded time (a new
flow) counts as the median file, so it runs at once. One very long flow fills a shard by itself;
no number of shards makes a run shorter than its longest flow.

What sets the shard count: every shard pays a fixed cost before its first test (image build, pulls,
and the stack boot, most of which is the database migrate). Measured on the standard GitHub
runners in November 2026:

- Unsharded, the suite took about 90 minutes.
- With four shards a shard took 36 to 48 minutes, because one flow waited out a 30-minute product
  default.
- That flow now gives its own project a five-minute settle (`lib/metric-settle.ts`). It asserts
  that the value is reported as not final while the conversations are open, and that its own poll
  first observes the final value no earlier than five minutes after their last arrival, less 5
  seconds of clock tolerance. That is an observation time, not the publication time, which the
  API does not return. A backend unit test pins the 30-minute default.
- With six shards a shard takes about 20 to 30 minutes, 11 to 15 of them fixed cost (the boot
  alone varied from 7 to 11), and `E2E Tests Pass` reports a little over 30 minutes after the push.

**Parity is a gate, not an assumption.** The `report` job lists the suite without `E2E_SHARD` and
compares that with the merged results (`scripts/shard-report.mjs parity`). It fails when a listed
test has no result in any shard, ran in two shards, never started, or when a shard reported a test
the listing does not have. Quarantined and `@live-llm` flows are out of both sides.

**Run-time skips.** A test the listing expects to pass but that skips itself while it runs fails
the gate when the repository variable `E2E_ENFORCE_RUNTIME_SKIPS` is `true`, unless
`.runtime-skips.json` lists its flow id for that stack: `{ "id": "OBS-E2E-031", "stack":
"distributed" | "standalone", "reason": "...", "issue": "<ticket>", "owner": "...", "expires":
"YYYY-MM-DD" }`. The id must equal the id in the test's flow annotation; a title match is not
enough. An entry that is not by design names the ticket that removes it, an owner, and an expiry
at most 45 days ahead; after that date it no longer allows the skip. Each listed skip gets a
warning on the run, and an entry whose test ran and passed is reported as out of date. Expiry is
judged on the run's date, so re-running only the report job cannot change its verdict. Today the
list is empty: OBS-E2E-031 and OBS-E2E-032 (TH-10597), whose churn guard once measured no DOM
mutations in CI, now run and pass on both stacks.

The backend has a stricter rule for its own skips in `futureagi/.ci/runtime-skips.json`
(`BACKEND_ENFORCE_RUNTIME_SKIPS`). Each rule names the exact test files it covers, each with the
number of tests measured to skip there, optionally the exact node ids it is limited to, an
anchored reason, the ticket that removes it, an owner
and an expiry at most 45 days after the run date; there is no by-design exemption. A skip is
allowed only for a listed file and a matching reason, for the test path the independent inventory
recorded, and only up to that file's measured count. An enforced run fails on an unexpected skip
and on a rule problem: an expired rule that still matches, and, judged on the whole run and only
when its evidence is complete, a file over its count or a rule file the run scheduled that matched
nothing. A rule can be limited to oss runs (`"runs": "oss"`: a fork pull request or a merge group,
without the private package) or internal runs (`"runs": "internal"`).

A skip that depends on data other tests leave behind cannot have an exact count. Its rule says why
in `"varies"`, and each of its files maps to the number of tests the inventory collects there: an
upper bound, checked against the inventory on every complete run, and matching nothing is no
problem. Exact counts stay the default, and no rule varies today. The last one, for
`tracer/tests/test_hourly_aggregate_state_route_live_ch.py`, went once its tests ran against the
CI ClickHouse sidecar, which they seed themselves.

Work that needs to run once per stack runs on the **last** shard: the flow catalog check and the
SDK live suites. The last shard, because the longest spec file always goes to shard 1. That shard
uploads a record of those steps' outcomes, and the `report` job fails without a record that says
all of them passed, so they cannot be skipped on every shard unseen. A manual run with
`host_update_fallibility` stays unsharded.

`E2E Tests Pass` passes in two cases only: every shard and the `report` job succeeded, or the
`changes` job found nothing that needs the suite. A skipped `report` job does not pass.

**Why edits and labels start no run.** Backend CI, Python lint and E2E CI start only on `opened`,
`synchronize` and `reopened`. A run for any other activity type becomes the workflow's newest
suite for the head commit. GitHub then ignores the required check of the older, real suite, so the
merge queue refuses the PR, although the real run is green. Blacksmith edits every new PR's body
about 4 s after it opens, which is how #4332 (green run 45070958943, then skipped run 45070969031)
and #4001 were refused. This is not documented by GitHub, and `gh pr checks --required` cannot
show it, because it still lists the older green runs. A retarget therefore starts no run of these
workflows either. The merge queue re-tests every merge into dev. For a retarget, **Retarget proof**
(`.github/workflows/retarget-proof.yml`) first checks that GitHub delivered the retarget with a
merge of the head onto the new base's current tip. GitHub often sends a retarget, its automatic
ones included, with the merge from before it; then that check fails and nothing else runs, and a
push or a close and reopen lets the pull request's own runs test the new base. Otherwise it calls
Backend CI and E2E CI on that merge. Their jobs appear as `retarget-backend / …` and
`retarget-e2e / …`, never under a required name, and they never share or cancel the pull request's
own runs. Like a merge group, its backend leg runs without the private package: no secrets are
forwarded. A Backend CI or E2E CI run on a pull request whose suite was selected, ran and passed
records the merge candidate it tested (artifact `ci-candidate-<kind>-attempt-<n>`). The **"CI ran
on this base"** check (`.github/workflows/ci-base-gate.yml`, not required) passes for a
retargeted pull request into main only when both a Backend CI and an E2E CI run recorded this pull
request, this head, this base and its current tip, and the tree of GitHub's current merge
candidate. It re-reads all of that, and fails closed when anything is missing, skipped, from
another pull request or stale. A green verdict is **proved against the base tip at its last run**,
not at merge time: the base can move after it. A receipt is written by the producing run's own
workflow, which for a pull request comes from that pull request's merge commit, so this check has
the same trust as the required checks. If `CI_SCOPE_MODE` ever narrows the suites, a retargeted
pull request whose suite was not selected records no receipt and stays red into main. Until the
repository owner makes it required on main, push or reopen after a retarget into main if it is
red. Update CI is not required and still starts on edits and labels (`update-rehearsal` starts the
Real rehearsals).

To rebalance after flows were added or got slower, download the `e2e-merged-results-distributed`
artifact of a green run and regenerate the recorded times:

```bash
node scripts/shard-report.mjs durations --merged merged-report.json   # rewrites .test-durations.json
```

### Why the E2E stack still migrates from zero

Backend CI restores a cached dump of a migrated database in its pytest shards
(`futureagi/.ci/db_dump.py`). The E2E stacks do not, on purpose. The stack's Postgres also holds
Temporal's own tables, and a migration registers Temporal search attributes while it runs. A
restored database would skip that registration, or carry another job's Temporal state. Every
shard therefore boots the way an install does: `manage.py migrate` on an empty database.

---

## Host update assertion fallibility (opt-in CI tool)

`scripts/host-update-fallibility.mjs` proves the existing **SET-E2E-002** assertions can fail.
It does not add a flow, change its five `backendChecks`, boot a stack, or qualify the host updater's
apply/rollback lifecycle. Authoring or unit-testing this driver is **not** a real fallibility proof.
Only a retained `proof-report.json` with `qualified: true`, its real child reports, and the restored
final green establish that proof.

After the branch has been reviewed and pushed, a separately authorized operator can request it:

```bash
gh workflow run e2e-ci.yml --repo future-agi/future-agi --ref <reviewed-branch> \
  -f stack=distributed -f host_update_fallibility=true
```

The boolean defaults to `false`. Enabling it with any other stack fails in `changes`, before
builds. The workflow still runs the **unchanged normal suite first**; only a successful distributed
manual dispatch can then run the proof. PR/push/schedule behavior, dispatch's default `standalone`,
`bin/e2e`'s default distributed stack, permissions and push-only cache publication are unchanged.
The driver itself refuses non-opted-in/non-dispatch/non-CI execution, another checkout, quarantine
or live-provider overrides, and debug/extra-reporter overrides. It accepts only `--output-dir` with
a new absolute directory outside the checkout and public status directory; there is no arbitrary
command, spec, grep, retry or timeout passthrough.

The sequence is serial: original green, **six mutants covering five checks**, exact-byte restoration,
original green again. Every mutant starts from the same SHA256-pinned source, temporarily written
at the original spec path so relative fixtures/contracts still resolve. All anchors are checked
for uniqueness before edits. The spec's annotation, `@flow`, seeding, authentication, 240s timeout
and ordinary `UI_READY=60s` remain unchanged.

| Check | One-sided perturbation                                             | Required failure evidence                                                                                            |
| ----- | ------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------- |
| 1     | First API candidate expectation A → B; seed stays A                | Expected minted B / received minted A at that assertion                                                              |
| 2     | Only first available-version UI expected array A → B               | Exact locator and expected B / displayed A array diff; only this mutant expect uses 1s, below its unchanged 60s step |
| 3     | Second API candidate expectation B → A; atomic replacement stays B | Expected minted A / received minted B after replacement                                                              |
| 4     | Omit owned report deletion; keep `ownsStatus=true`                 | Unchanged unavailable API expectation rejects available snapshot B; original `finally` still cleans its own file     |
| 5a    | Only POST's expected status 405 → 200                              | POST expected 200 / received 405                                                                                     |
| 5b    | Anonymous GET's expected status 401 → 200                          | Expected 200 / received 401, after unchanged method checks                                                           |

Each child invokes the installed Playwright CLI on only the original file, with one worker,
zero retries, one repetition, `--forbid-only`, the existing config and JSON-only reporting. It
never replaces the config's quarantine filter. JSON, stdout/stderr, seed attachment, source copy,
trace/screenshot attachments and a plain expected-failure summary live in distinct per-run
subdirectories. The separately named `host-update-fallibility-distributed` artifact retains these
and the machine-readable source/output hashes and runtime versions for seven days. It never writes
expected mutant failures into the ordinary `playwright-report`, blob report or `test-results`.

A child has a **360s deadline**. The eight-run sequence has a **20-minute aggregate cap**, not eight
independent six-minute allowances: each launch gets the lesser of 360s and the remaining aggregate
budget. At either deadline the process group receives TERM then KILL (5s + at most 2s grace), the
proof stops unqualified, and source restoration/evidence writing run in `finally`. The CI step's
22-minute limit leaves headroom for that cleanup; the existing job limit is unchanged. The driver
handles SIGINT/SIGTERM the same way; SIGKILL or runner loss cannot execute JavaScript cleanup, so the
private original backup is retained and no proof is valid without its restoration hash and final
green. It refuses concurrent source edits rather than overwriting them.

A nonzero child exit alone is **never** evidence. The parser requires precisely the original test,
retry zero, matching flow annotation, seed A/B identities, the intended source assertion and step,
concrete expected/received values, and no global, earlier-stage or teardown errors. Missing or
malformed JSON, skips, flakes, retries, launch/connection/step/test deadlines, an absent page, and
failure at any other assertion all stop the sequence. The script only inspects the public status
slot: it never removes an existing report, flow lock or temporary, and never clears state after a
crash to force a later pass. Only the original flow owns report cleanup.

Offline validation (no Docker or browser) is included in `yarn selftest` through
`scripts/host-update-fallibility.test.mjs`. Unit test doubles are explicitly labelled and can never
produce `qualified: true`; real CI proof still must be run and reviewed separately.

---

## Troubleshooting

**The first boot takes forever.** It does: about 8–9 minutes cold, of which roughly 6.5 are the
backend's first-run migrations before `/health/` answers. `backend not healthy in 600s` means it
took longer still — check `bin/e2e logs backend` for a migration error before assuming a hang.

**`CH spans table missing — boot-hook schema apply failed`.** The backend came up but its ClickHouse
schema did not apply; the boot hook logs and continues, so this check is the only thing that catches
it. `bin/e2e logs backend | grep -i clickhouse` usually shows the reason. A `down -v` and a fresh
`up` is the reliable repair.

**`peerdb-init exited 0 but reported errors (CDC mirrors missing)`.** The mirror setup script is
fail-open — it prints failures and exits 0 anyway — so `bin/e2e` reads its log and fails closed. Any
mirror that failed means a CDC-fed surface (eval results, annotation scores) will be permanently
empty for that boot, which would show up as an unexplained eval-flow timeout later. The usual cause
is that the backend's migrations had not created the tables yet, so running `bin/e2e up` again once
`/health/` answers is the fix — it force-recreates `peerdb-init` and re-checks its log.

**`WARN: known schema-drift mirrors failed (ticketed)`.** Expected, and not fatal. The ClickHouse
destination DDL for `model_hub_score` and `simulate_agent_definition` is behind the Postgres models,
so those two mirrors cannot be created on a fresh stack. They are listed in `KNOWN_DRIFTED_MIRRORS`
in `bin/e2e`; shrink that list to nothing when the DDL is fixed. Any _other_ mirror error fails the
boot.

**Port already allocated.** Something else holds one of the ports above — most often a second E2E
stack, or a dev PeerDB on 9900 (ours is 29900). `bin/e2e ps` shows what this project has;
`docker ps` shows the rest. The ports are deliberately disjoint from dev defaults and from
`futureagi-test`, so a collision is almost always another copy of this stack.

**A flow fails and you want to see why.** Playwright keeps a trace and a screenshot for every failed
test under `e2e/test-results/<test-name>/` (`trace.zip` — open it with
`npx playwright show-trace <path>`), and the HTML report under `e2e/playwright-report/`, which
`bin/e2e report` opens. Both directories are gitignored. A flow can also attach its seeded fixture
data to the report — `OBS-E2E-001` attaches the trace and span ids it sent, so you can query the
same rows by hand afterwards; do the same in new flows whose failure would otherwise be unreadable.

**Timeouts in the eval flow specifically.** That flow waits on CDC. If the mirrors did not get
created (see above) it can never pass; if they did, check `bin/e2e logs peerdb-flow-worker`.

**A flow times out in a full-suite run but passes on its own.** Parallel workers share one small
stack, and on a loaded laptop the same UI step that takes 15 s alone can take a minute — enough for
a CDC-sized budget to run out. Confirm by re-running the flow by itself
(`bin/e2e test flows/evals/ --workers=1`); if it passes there, you are looking at local contention,
not a product regression. That is a reason to run fewer workers locally, never a reason to add a
retry.

**Everything is strange after a product change.** Inspect the failing service's
logs and the source/image versions first. Keep retained fixtures, Kafka offsets
and failed-bootstrap state for diagnosis; do not wipe volumes as a retry strategy.

---

## Known product issues that affect E2E

Building and live-verifying this stack surfaced several product-side defects. None are caused by the
E2E setup; each is worked around here so the suite can run, and each needs its own fix. The full
list with evidence lives in
[`../../internal-docs/e2e-testing-setup/05-findings-log.md`](../../internal-docs/e2e-testing-setup/05-findings-log.md).

- The current root startup graph replaces the old shell/log-inspection workaround:
  PG migrations and native ClickHouse initialization precede PeerDB setup, then
  CDC readiness gates the backend/workers. The new setup does not create the
  retired PG span mirror. The harness no longer force-recreates mirror jobs or
  accepts `model_hub_score`/`simulate_agent_definition` failures as warnings.
  Source columns are owned by PeerDB and checked against PostgreSQL metadata;
  existing incompatible destinations stop startup instead of being overwritten.
  Qualify both fresh and retained installations using the updated images before
  treating this source/configuration change as a successful runtime release.
- **Unfiltered eval graphs read a table the drop flag removes** (`eval_metrics_hourly`), so they are
  likely to 500 on any local or OSS stack. **Flows must not assert on unfiltered eval graphs.**
- **The backend `:latest` image is ~15 GB uncompressed** (CUDA/NVIDIA wheels the OSS backend never
  uses on CPU hosts) — this is most of the cold-boot and CI disk cost.

Two measured behaviours worth knowing, which are not defects but shaped the harness: PG→ClickHouse
CDC lag is about 20.7 s end to end, roughly twice the mirror's 10 s sync interval; and no shipped
eval template can complete against a deterministic mock LLM, because every one of them requires the
judge model to compose its own JSON — which is why `EVAL-E2E-001` authors its own judge template.

---

## Reference

- Flow catalog (generated): [`FLOWS.md`](FLOWS.md)
- Agent skills: `.agents/skills/writing-e2e-flows`, `.agents/skills/reviewing-prs` (Claude Code reads
  them through `.claude/skills/` symlinks). To use `reviewing-prs` in another repo, link it into your
  home skill directories — **run this from the future-agi repo root**, because `$PWD` is what makes
  the link absolute, and `ln -s` exits 0 while creating a dangling link from anywhere else:

  ```bash
  mkdir -p ~/.agents/skills ~/.claude/skills
  ln -s "$PWD"/.agents/skills/reviewing-prs ~/.agents/skills/reviewing-prs
  ln -s "$PWD"/.agents/skills/reviewing-prs ~/.claude/skills/reviewing-prs
  ```

- Repo-wide testing overview: [`../TESTING.md`](../TESTING.md)
- Frontend test conventions: [`../frontend/TESTING.md`](../frontend/TESTING.md)
- Architecture spec and decision record:
  [`../../internal-docs/e2e-testing-setup/02-architecture.md`](../../internal-docs/e2e-testing-setup/02-architecture.md)
