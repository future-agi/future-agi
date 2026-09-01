# FutureAGI E2E

End-to-end flows that drive the whole product as one running system: a Chromium browser against the
production frontend image, the real Django API, the real fi-collector, the real agentcc-gateway, and
the real datastores (Postgres, ClickHouse, Temporal, Redis, RabbitMQ, MinIO) plus the PeerDB CDC
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
│    postgres · clickhouse · redis · rabbitmq · minio · temporal                   │
│    backend · worker (ALL_QUEUES) · frontend · fi-collector                       │
│    agentcc-gateway ──► mock-llm (OpenAI-compatible, deterministic, no host port) │
│    peerdb (catalog · temporal · flow-api · flow-workers · server · minio · init) │
│      └── CDC mirrors PG→CH: tracer_eval_logger, model_hub_score, datasets,       │
│          prompts, simulate, usage_apicalllog …                                   │
│                                                                                 │
│  deliberately NOT started: serving · code-executor · sized workers · peerdb-ui   │
└─────────────────────────────────────────────────────────────────────────────────┘
```

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
bin/e2e up                   # boot the isolated futureagi-e2e stack
bin/e2e test                 # run the whole suite against it
bin/e2e test flows/observe/  # or one area
bin/e2e test --grep @smoke   # or one tag
```

`bin/e2e up` composes the root `docker-compose.yml` with `e2e/stack/docker-compose.e2e.yml`, using
`e2e/stack/e2e.env` and the Compose project name `futureagi-e2e`. It starts an explicit service
list — kept in `SERVICES` in `bin/e2e` so the trimmed set is visible in one place, with
`COMPOSE_PROFILES=peerdb` in the env file making the profile-gated PeerDB services startable by
name. The overlay itself is thin: it adds the `mock-llm` service, caps ClickHouse at 3 GB (it is the
first thing an out-of-memory runner kills), sets `restart: "no"` on backend and worker so a crash
loop fails the run loudly instead of hiding, and gives fi-collector its own image tag. `up` returns
only after this readiness sequence passes:

1. `docker compose up -d --wait --wait-timeout 300` — every datastore healthcheck, including
   Temporal's slow first boot.
2. `GET http://localhost:8100/health/` — the backend is up **and** migrated (600 s budget).
3. `SELECT count() FROM spans LIMIT 0` over ClickHouse HTTP — the Django ClickHouse boot hook
   logs-and-continues on failure, so `/health/` alone can pass with a missing CH schema.
4. `GET http://localhost:3100/` — nginx is serving the frontend.
5. `peerdb-init` is force-recreated, must exit 0, **and** its log must contain no unexpected
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

| Variable                   | Default                                                      | Needed for                              |
| -------------------------- | ------------------------------------------------------------ | --------------------------------------- |
| `E2E_APP_URL`              | `http://localhost:3100`                                      | every browser step (`baseURL`)          |
| `E2E_API_URL`              | `http://localhost:8100`                                      | provisioning and the API assertion lane |
| `E2E_COLLECTOR_URL`        | `http://localhost:24318`                                     | OTLP trace seeding                      |
| `E2E_GATEWAY_URL`          | `http://localhost:28090`                                     | the mock-LLM harness self-test          |
| `E2E_CH_URL` / `E2E_CH_DB` | `http://localhost:28123` / `default`                         | storage-lane ClickHouse assertions      |
| `E2E_PG_URL`               | `postgresql://futureagi:futureagi@localhost:25432/futureagi` | storage-lane Postgres assertions        |

Any flow with storage-lane assertions needs `E2E_CH_URL` and `E2E_PG_URL` as well — pointed at the
attached stack's stores. If you omit them the probe hits the managed stack's ports and the spec
fails at the probe, by design: there is no silent skip.

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
bin/e2e build all         # all three
```

Each build prints the line to run next; the stack picks the images up through these variables:

| Built target | Variable to set on `bin/e2e up`      | Also retags            |
| ------------ | ------------------------------------ | ---------------------- |
| `backend`    | `FUTURE_AGI_VERSION=e2e-local`       | the `worker` container |
| `frontend`   | `FRONTEND_VERSION=e2e-local`         | —                      |
| `collector`  | `E2E_FI_COLLECTOR_VERSION=e2e-local` | —                      |

```bash
bin/e2e build all
FUTURE_AGI_VERSION=e2e-local FRONTEND_VERSION=e2e-local E2E_FI_COLLECTOR_VERSION=e2e-local bin/e2e up
bin/e2e test
```

The root compose reuses `FUTURE_AGI_VERSION` for fi-collector's image too; the E2E overlay decouples
it behind `E2E_FI_COLLECTOR_VERSION` so a backend-only build never forces a collector build (and so
CI can retag the backend alone).

**Which one to use when.**

- _Frontend iteration_: author and debug specs in **attach mode** against your dev stack — hot
  reload makes the loop instant. Then `bin/e2e build frontend` and a managed run before you push,
  because that is the only run that exercises the artifact users get.
- _Backend iteration_: `bin/e2e build backend` each cycle. Rebuild cost is small — `Dockerfile.oss`
  installs dependencies from `requirements.txt` in an earlier layer, so an edit re-runs only the
  `COPY . .` layer — and the production image keeps the readiness contract honest by running
  migrations. (The collector is a cached Go build, the cheapest of the three; the frontend re-runs
  the full Vite production build every time and is the slowest by far.)
- _Before pushing_: `bin/e2e build all` on fresh volumes (`bin/e2e down -v` first) — exactly the
  artifacts a user receives.

**Why not the dev overlay.** `docker-compose.dev.yml` looks like the obvious vehicle for local code
and is not one. It hardcodes `FAST_STARTUP: "true"` in `environment:`, which cannot be overridden
from an env file and which _skips migrations_ — on fresh volumes the stack comes up unmigrated. It
serves a Vite dev server instead of the nginx artifact the product ships, it shares the `:dev` image
tags with any dev stack you are running, and its `--reload` watcher restarts the backend mid-test.
Attach mode covers the hot-reload need without any of that. In CI none of this applies: the workflow
builds `:e2e-ci` images from the PR's own code.

---

## Writing a flow

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

- **`actor`** — _worker-scoped_. Signs up a brand-new user on a brand-new org
  (`e2e-w<workerIndex>-<runId>@futureagi.com`), logs in, resolves the organization and default
  workspace, and mints the org API/secret key pair. The `futureagi.com` domain is deliberate: it is
  the signup path's reCAPTCHA special case, the belt to the `localhost` Host suspenders. One org per
  worker is what makes full parallelism safe without truncating anything. It carries
  `actor.api` — an `ApiClient` already sending `Authorization`, `X-Organization-Id` and
  `X-Workspace-Id`.
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
A `changes` job decides, per image, whether to build from the PR's code (tagged `:e2e-ci`) or use the
released `:latest` — so a frontend-only PR does not rebuild the backend. The job then checks the flow
catalog, boots the stack with `bin/e2e up`, runs `bin/e2e test`, and always uploads the Playwright
HTML report (7-day retention); on failure it dumps `bin/e2e ps` and the last 200 log lines. The
`E2E Tests Pass` gate fails closed unless every dependency succeeded or was legitimately skipped.

**Wall time in CI has not been measured yet** — the job has never run on a real PR. Record it on the
first run and put the number here; the hard timeout is 60 minutes and the boot budgets above are the
laptop-measured ones.

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

**Everything is strange after a product change.** Wipe and rebuild:
`bin/e2e down -v && bin/e2e up`. The stack keeps no state worth preserving.

---

## Known product issues that affect E2E

Building and live-verifying this stack surfaced several product-side defects. None are caused by the
E2E setup; each is worked around here so the suite can run, and each needs its own fix. The full
list with evidence lives in
[`../../internal-docs/e2e-testing-setup/05-findings-log.md`](../../internal-docs/e2e-testing-setup/05-findings-log.md).

- **`peerdb-setup-mirrors.sh` is fail-open** — peer and mirror failures are printed and swallowed,
  then it reports "Done!" and exits 0. `bin/e2e` inspects the log and fails closed instead.
- **`peerdb-init` races the backend's migrations** — its `depends_on` never includes the backend, so
  on fresh volumes every `CREATE MIRROR` fails. `bin/e2e up` orders readiness before mirror setup.
- **ClickHouse mirror DDL is behind the Postgres models** — `model_hub_score` and
  `simulate_agent_definition` cannot be mirrored on a fresh install, which means **annotation scores
  never reach ClickHouse on a fresh stack** (annotation columns and graphs stay empty). Warned about,
  not silenced.
- **`peerdb-init` is not given `CH25_DROP_LEGACY_CDC_CHAIN`** by the root compose, so it recreates a
  retired mirror. The E2E overlay passes the flag.
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
- Repo-wide testing overview: [`../TESTING.md`](../TESTING.md)
- Frontend test conventions: [`../frontend/TESTING.md`](../frontend/TESTING.md)
- Architecture spec and decision record:
  [`../../internal-docs/e2e-testing-setup/02-architecture.md`](../../internal-docs/e2e-testing-setup/02-architecture.md)
