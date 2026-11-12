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
│    agentcc-gateway ──► mock-llm (OpenAI-compatible, deterministic, reads :28082) │
│    backend / worker ─► mock-slack (OAuth and chat, host :28081)                  │
│    backend / worker ─► webhook-sink (org webhooks, reads :28083)                 │
│    peerdb (catalog · temporal · flow-api · flow-workers · server · minio · init) │
│      └── CDC mirrors PG→CH: tracer_eval_logger, model_hub_score, datasets,       │
│          prompts, simulate, usage_apicalllog …                                   │
│                                                                                 │
│  deliberately NOT started: serving · code-executor · sized workers · peerdb-ui   │
└─────────────────────────────────────────────────────────────────────────────────┘
```

Because code-executor is not started, `e2e/stack/e2e.env` sets `CODE_EXECUTOR_LOCAL_FALLBACK=true` so
code evals run in the worker instead of failing with "Code executor unavailable".

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

| Service                       | Port          | Service                       | Port          |
| ----------------------------- | ------------- | ----------------------------- | ------------- |
| frontend                      | 3100          | Postgres                      | 25432         |
| backend                       | 8100          | ClickHouse HTTP / native      | 28123 / 29000 |
| agentcc-gateway               | 28090         | Redis                         | 26379         |
| fi-collector OTLP HTTP / gRPC | 24318 / 24317 | MinIO API / console           | 29005 / 29006 |
| observation Kafka             | 29093         | mock-llm / webhook-sink reads | 28082 / 28083 |
| fi-collector admin            | 29464         | Temporal                      | 27233         |
| peerdb-server                 | 29900         | peerdb-ui (not started)       | 23001         |

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
bin/e2e build all         # all three
```

Each build prints the line to run next; the stack picks the images up through these variables:

| Built target | Variable to set on `bin/e2e up`      | Also retags            |
| ------------ | ------------------------------------ | ---------------------- |
| `backend`    | `FUTURE_AGI_VERSION=e2e-local`       | the `worker` container |
| `frontend`   | `FRONTEND_VERSION=e2e-local`         | —                      |
| `collector`  | `E2E_FI_COLLECTOR_VERSION=e2e-local` | observation consumer   |

```bash
bin/e2e build all
FUTURE_AGI_VERSION=e2e-local FRONTEND_VERSION=e2e-local E2E_FI_COLLECTOR_VERSION=e2e-local bin/e2e up
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

With `E2E_GATEWAY_PROFILE=r7alt` the Standalone gateway mounts `e2e/stack/gateway.r7alt.yaml` instead
of `gateway.e2e.yaml` (see "Gateway harness pieces"); export the same value for `up`, `test` and `down`.

With `E2E_MANAGED_MODELS_ON_FOR_NEW_ORGS=1` (either stack) the API runs with
`AGENTCC_MANAGED_MODELS_ON_FOR_NEW_ORGS=1`: a new organization's first gateway key turns Future AGI
models on by itself (`futureagi/agentcc/services/managed_models_default.py`). Export the same value
for `up` and `test`. Flows tagged `@new-org-default` run only then, and the managed-mock inspector
accepts the setting only then (`lib/profile.ts`). Unset, the stack keeps the shipped default (off).
Flows that need a new organization to start with Future AGI models off (GW-E2E-007, where an admin
turns the switch on) are tagged `@new-org-default-off` and never run on a stack that declares the
default on (`lib/quarantine.ts`).

`bin/e2e build standalone-app` is the last step on its own: it assembles `futureagi/standalone`
from the four component images already built with that tag. The managed-mock inspection
(`lib/managed-mock.ts`) checks the Standalone stack against its own spec
(`lib/managed-mock-standalone.ts`). The Distributed-only background attestation is skipped there.

### The MCP-on profile (`E2E_GATEWAY_PROFILE=mcp`)

H-MCP keeps stock MCP-off coverage intact and adds a separate Standalone profile.
Run the following on the cloud test VM, not on a developer laptop:

```bash
export E2E_STACK=standalone E2E_GATEWAY_PROFILE=mcp DOCKER_CONTEXT=default
bin/e2e build standalone
bin/e2e build mcpmock
bin/e2e up
bin/e2e test flows/gateway/connectors/browse.spec.ts flows/gateway/mcp/
```

`build mcpmock` reuses `agentcc-gateway/cmd/agentcc-mcpmock/mcpmock` and the
scenarios' common tool/resource/prompt fixtures. A tiny test entrypoint serves
that handler over HTTPS (and HTTP for transport checks); TLS verification stays
on. The test image generates its own short-lived certificate and key. Only the
public certificate is extracted, into ignored `e2e/.mcp/`; no operator credential
is read. Rebuild the mock after its seven-day certificate expires.

The same command uses `agentcc-catalog` to generate a CN30a-derived fixture
catalog. Both the gateway and Django mount that one bundle. Its only app is
**E2E Echo**, an API-key app whose URL is fixed to the isolated mock; it has no
host variables and never contacts a real vendor. GW-E2E-022 browses it and uses
the real Connect form, checks the vault and CN-ADD tool postures, calls echo with
a unique marker and proves the destructive tool is refused. This is a local
connector-path proof, not a claim that every catalog vendor was contacted.

The mock has no host port or default-network attachment: it is on the internal
`e2e-public` network only. The profile inspector checks the network, image,
command and profile; `inspectMcpCatalog` additionally attests the mounted bundle,
TLS trust and fixed upstream before any MCP flow seeds a server or adds an app.
`AGENTCC_MCP_STATE_KEYS` is a fixed test value in the MCP-only overlay. Stock and
r7alt retain their MCP-off behavior; GW-E2E-023..025, 028 and 029 are selected only on `mcp`,
and a reported MCP-off state there fails rather than skips.

### The real-catalogue profile (`E2E_GATEWAY_PROFILE=mcp-catalog`)

A second MCP-on Standalone profile, for browsing the connector catalogue this
build ships rather than H-MCP's one-app fixture. Run it on the cloud test VM:

```bash
export E2E_STACK=standalone E2E_GATEWAY_PROFILE=mcp-catalog DOCKER_CONTEXT=default
bin/e2e build standalone
bin/e2e up
bin/e2e test flows/gateway/connectors/browse-real-catalog.spec.ts
```

The gateway file is exactly stock plus `MCP_CATALOG_DELTA` (`mcp.enabled` and the
same fixed test state key; no MCP upstream and no `connectors.catalog_dir`, so the
gateway reads the catalogue it embeds), and the overlay
(`stack/docker-compose.mcp-catalog-e2e.yml`) only sets that state key: nothing is
mounted, so Django serves `futureagi/agentcc/connector_catalog/catalog.json`. No
MCP mock runs. GW-E2E-026 (tagged `@gw-profile-mcp-catalog`) first attests that
the app mounts nothing over that bundle and that the catalog API names it
(`lib/real-catalog.ts`), then browses it: every listed app can be added on this
build and is found, every other app it ships shows no row, and the browser is held
to the local stack, with every other request aborted before it is sent. It adds
nothing, so no vendor is called. GW-E2E-022 skips on this profile.

### The MCP client sign-in profile (`E2E_GATEWAY_PROFILE=mcp-signin`)

Everything `mcp` is (the same mock, overlay and fixture catalogue, attested the
same way), and the gateway is also an OAuth resource server for MCP clients that
sign a person in through the console's own authorization server. Run it on the
cloud test VM:

```bash
export E2E_STACK=standalone E2E_GATEWAY_PROFILE=mcp-signin DOCKER_CONTEXT=default
bin/e2e build standalone && bin/e2e build mcpmock
bin/e2e up
bin/e2e test flows/gateway/mcp/client-sign-in.spec.ts
```

The gateway file is exactly stock plus `MCP_SIGNIN_DELTA`: `MCP_DELTA`, plus
`mcp.oauth` (enabled, with Django's issuer on this stack,
`http://localhost:8100/agentcc/mcp-oauth`) and `mcp.public_url` (the gateway
address Django reports, `http://localhost:8090`, the resource an access token is
issued for; published on this host as `E2E.gatewayUrl`). The verification keys
arrive by Django's push; nothing is fetched. Any other upstream, token channel or
sign-in setting is STOP. GW-E2E-027 (tagged `@gw-profile-mcp-signin`) is an MCP
client end to end: no credential, the challenge, the metadata, the sign-in, then
an MCP session with the access token at the virtual server's URL.

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
  the login UI itself must _not_ use this fixture — `flows/auth/login.spec.ts` imports the bare
  `test` of `lib/trace-hygiene/fixture.ts` (no sign-in) for exactly that reason.
- **`probe`** — _test-scoped_ `StateProbe`, bound to the actor so API-lane reads carry the right
  org/workspace headers. Disposed automatically.

`ApiClient` throws `ApiError{status, path, body}` on any response ≥ 400. Never add `||` fallback
chains for response fields: read the serializer (or capture one live request), and code against the
one real envelope.

### Traces and evidence hygiene

Playwright traces record request and response bodies, headers, action parameters and DOM snapshots
(including the values of password inputs). The harness keeps credentials out of them in three
layers; a flow gets all three by importing `test` from `lib/fixtures.ts`, `lib/scope-actors.ts`,
`lib/walk/fixtures.ts` or, for a signed-out flow, `lib/trace-hygiene/fixture.ts`. Never import
`test` straight from `@playwright/test` in a flow (`scripts/trace-hygiene.test.mjs` fails on it).

1. **Capture time** (`lib/trace-hygiene/hook.ts`, installed by the auto fixture): before the
   runner writes `trace.zip`, the runner's step events and every context chunk are scrubbed as one
   set: request and response bodies of the credential endpoints (`lib/trace-hygiene/routes.mjs`,
   pinned to the product's real routes) are dropped from the archive; `Authorization`, `Cookie`,
   `Set-Cookie` and `X-*-Key/-Token/-Secret` header values, cookie values, credential-named fields,
   invite/reset/verify URL tokens (path and query), password-input values in DOM snapshots and the
   `fill`/`type` values aimed at them (and wherever else those values appear, such as a step title)
   become `[REDACTED]`. The merged archive is scrubbed again, then audited.
2. **Post-processing** (`lib/trace-hygiene/archive.mjs`): `globalTeardown` in both configs sweeps
   every trace archive left in the output directory (a spec that bypassed the fixtures, a worker that
   died). For traces from older heads: `node scripts/trace-scrub.mjs <dir>`.
3. **Independent audit** (`scripts/evidence-audit/`): a broad detector that shares no code or list
   with the scrubber — JWTs, auth schemes, provider key prefixes, cookie pairs, password-field
   values, URL tokens, its own credential-name vocabulary (matched by containment: `loginPasswordValue`
   counts, `token_type` and `api_key_id` do not), hex/base64url entropy, and the values the worker
   minted (`lib/minted-values.ts`, memory only) — over every byte: inside archives (names, comments,
   extra fields and unclaimed bytes included), through JSON, percent, base64 and base64url layers,
   and over the printable spans (ASCII, UTF-16) of binary payloads. Reaching any decoding bound
   (`LIMITS` in `audit.mjs`) refuses, like a finding. In the worker it checks each kept trace; in
   `globalTeardown` it checks the whole output directory. Any finding moves the file to the OS temp
   directory (`fa-e2e-evidence-refused/`), writes a value-free `evidence-audit.json` and fails the
   run. Where evidence LANDS, run the gate itself: `node scripts/evidence-audit.mjs --report <file>
--quarantine <dir outside the tree> <paths…>` (exit 1 = refused, 3 = a refused file could not be
   moved). `walks/launch/launch.sh` pulls only from `e2e/walk-landed/<launch id>/`, which
   `walks/vm/land.sh` fills on the VM with byte-identical copies of what the auditor read, and only
   after a clean audit (or a refusal whose files were all quarantined); any other outcome lands just
   `command-exit.txt` (86 refused, 87 gate failed) and a verified value-free report.

`e2e/walk-landed/` holds `<launch-id>/` on the VM. On the Mac, `launch.sh` pulls into `~/.futureagi-local/walk-landed/<worktree name>/<group>/` by default, outside every git worktree. To choose another place, set `WALK_PULL_ROOT` to an absolute path outside any worktree.

Reports name the kind, file, archive entry, offset, layer and the field name — never the value; a
path or entry name that itself carries a finding is reported by position only.

Test-input fixtures carrying synthetic credentials go under `os.tmpdir()`, never `testInfo.outputPath()`:
the output directory is evidence, and the auditor refuses it.

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

| Budget               | Timeout | Covers                                                                                                                                                                         |
| -------------------- | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `POLL.SPAN_VISIBLE`  | 15 s    | collector-written spans: OTLP POST → row readable in ClickHouse (measured 1.6–2.6 s; the collector's batch ticker dominates)                                                   |
| `POLL.EVAL_RESULT`   | 90 s    | a Temporal eval task reaching `completed` — the schedule ticks every 10 s, plus worker latency                                                                                 |
| `POLL.CDC_VISIBLE`   | 180 s   | any read of a CDC-fed table (`tracer_eval_logger`, `model_hub_score`, …): PG write → PeerDB mirror → ClickHouse. ~20.7 s typically, but observed past 90 s under parallel load |
| `POLL.ASYNC_JOB`     | 60 s    | generic Temporal workflow completion                                                                                                                                           |
| `POLL.GATEWAY_SYNC`  | 75 s    | the gateway's periodic org-config and key sync (`AGENTCC_SYNC_INTERVAL`, 60 s in both stacks) plus 15 s: a change Django's push on save did not carry                          |
| `POLL.SCHEDULE_TICK` | 150 s   | a Temporal schedule's next tick after a retry backoff: a 30-60 s interval (`agentcc_alerts.py`) after the 30 s first retry (`webhook_delivery.retry_delay`), plus the run      |

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

#### Gateway harness pieces

Small `lib/` and `stack/` pieces the gateway flows share. Each has its own test: an offline half
that runs without a stack (`bin/e2e test harness/<spec> --grep offline`, or `yarn selftest` for the
`scripts/*.test.mjs` ones) and, where it reads the stack, a live half that runs with the suite.

Base harness components:

| Piece                                                                               | Gives a flow                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | Its own test                                                                      |
| ----------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------- |
| `lib/gateway-admin.ts` `GatewayAdmin`                                               | the gateway admin API with the stack's dev token: `config()`, `orgConfig(org)` (`null` on 404), `putOrgConfig` (proof fallback only), `batch` / `cancelBatch(id, org)`, `key(id)`; local gateways only                                                                                                                                                                                                                                                                                                                                                                               | `harness/gateway-admin.spec.ts`                                                   |
| `lib/state-probe.ts` `POLL.GATEWAY_SYNC`, `POLL.SCHEDULE_TICK`                      | the two waits above, named by what they wait on                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | `scripts/state-probe.test.mjs`                                                    |
| `lib/clock.ts` `gatewayNow`, `freshWindow`                                          | the gateway's own time (its `Date` header, never the host clock) and a wait until 5-35 s into its minute, so a per-minute pair of calls never straddles a window                                                                                                                                                                                                                                                                                                                                                                                                                     | `harness/clock.spec.ts`                                                           |
| `lib/public-network.ts` `PUBLIC_NETWORK`; the `e2e-public` network in both overlays | an internal compose network on `11.88.0.0/24`, outside every private, reserved and CGNAT range, so the product's SSRF guards (Django `ssrf_guard.classify_ip`, the gateway's netguard) treat it as public with their defaults on; nothing on it leaves the host. Members: the app containers (Standalone `app`; Distributed `agentcc-gateway`, `backend`, `worker`), `mock-llm` at `11.88.0.11` (`llm.e2e-public.test`), `webhook-sink` at `11.88.0.10` (`hooks.e2e-public.test`) and `smtp-sink` at `11.88.0.12` (`smtp.e2e-public.test`); those aliases exist on this network only | the managed-mock inspector (below)                                                |
| `lib/sinks.ts` `WebhookSink`; `stack/webhook-sink/server.mjs`                       | `url(channel, { failFirst })` to save as an org webhook (the product refuses private webhook addresses at save and delivery); `received(channel)` reads what arrived (host `127.0.0.1:28083`, `E2E_WEBHOOK_SINK_URL`); `/fail/<n>` answers the channel's first n requests 500                                                                                                                                                                                                                                                                                                        | `scripts/webhook-sink.test.mjs`, `harness/webhook-sink.spec.ts`                   |
| `lib/upstream.ts` `MockUpstream`; `stack/mock-llm/server.mjs` scoped routes         | `baseUrl(scenario, { latencyMs, status })` as an org provider's base URL (with `api_path_prefix: '/v1'`): `/s/<scenario>/<lat=ms,status=code or ->/v1/...` answers exactly the unscoped echo (usage 7/7/14) after the delay, or that status with an OpenAI error body; `requests(scenario)` reads every call it received (host `127.0.0.1:28082`, `E2E_MOCK_LLM_URL`)                                                                                                                                                                                                                | `scripts/mock-llm.test.mjs`, `harness/mock-upstream.spec.ts`                      |
| `lib/managed-mock.ts` public-network rule                                           | the inspector the mock-model flows run before registering a model accepts exactly the declared `e2e-public`: the project's own internal bridge on exactly its subnet, attached to exactly the members (an unlisted container is STOP), the pinned addresses and aliases, no pinned alias on `default`; every other service on `default` only; the new read URLs must be `localhost`                                                                                                                                                                                                  | `harness/managed-mock.spec.ts` (`--grep 'managed mock'`: offline N1-N7 per stack) |
| `lib/sinks.ts` `MailSink`; the `smtp-sink` service in both overlays                 | `host` / `port` (`smtp.e2e-public.test:1025`, the public-classified address: the product refuses a private SMTP host with its defaults on) to save as an SMTP alert's provider config; `messagesTo(address)` reads every message to exactly that address, oldest first, with subject and HTML (mailpit `axllent/mailpit:v1.31.4`, STARTTLS with a self-signed certificate, which the product always asks for and does not verify; API on host `127.0.0.1:28025`, `E2E_MAIL_SINK_URL`); a pinned `e2e-public` member, inspected with the others                                       | `harness/smtp-sink.spec.ts`                                                       |
| `lib/temporal-probe.ts` `TemporalProbe`                                             | `listSchedules`, `describeSchedule(id)` (paused, interval, queue, workflow, the TaskRunnerInput's activity, action count), `pause` / `unpause` / `trigger` / `deleteSchedule` on both stacks: the `temporal` CLI in Standalone `app` (`127.0.0.1:7233`) or the Distributed `temporal` service (`temporal:7233`) via `bin/e2e compose exec` (DOCKER_HOST, never a context). Standalone only: the product's `registerSchedules()`, `--list` and `setIntervalViaManager`. Its CLI JSON: `fixtures/temporal/schedule-describe.json`                                                      | `harness/temporal-probe.spec.ts`                                                  |
| `lib/profile.ts`; `stack/gateway.r7alt.yaml`; `E2E_GATEWAY_PROFILE` in `bin/e2e`    | the gateway config a stack runs: `stock` (default, `gateway.e2e.yaml`) or `r7alt` (Standalone only: exactly stock plus `R7ALT_DELTA`, bodies logged, cache on, other timeouts). `bin/e2e` mounts it for every subcommand and refuses anything else; a flow that needs a profile is tagged `@gw-profile-<name>` and runs only on that profile (`grepInvertPattern`; the catalog lists all); `gatewayProfile()` is what a flow reads its expectations from; `PROFILE_FINGERPRINTS` is what `GET /-/config` shows of each                                                               | `harness/gateway-profile.spec.ts`, `scripts/quarantine.test.mjs`                  |
| `lib/managed-mock.ts` profile rule                                                  | the declared profile's file must be the mounted one (`gateway profile <p> expects <file> mounted`), `r7alt` must be exactly stock plus its delta and is Standalone only; the routing contract stays the stock file's; the receipt carries `profile` and the mounted file's hash                                                                                                                                                                                                                                                                                                      | `harness/managed-mock.spec.ts` (offline P3, P4, N8-N12)                           |
| `lib/gateway-grid.ts` `GatewayGridReader`, `GRID_VIEWPORT`                          | a console AG Grid table read as the user sees it: `headers()` left to right, `column(colId)` / `columnHeaded(label)` top to bottom by `row-index`, `row(i)` / `cell(i, colId)` at a display index, `rowByHeader(i)`, `rowTexts(i)`, and `displayedRowCount()` from `aria-rowcount` (rows a virtualised grid has not rendered count too); AG Grid leaves the DOM out of display order after a column move or a re-sort, so no read uses DOM order. `GRID_VIEWPORT` (1440 x 900) is the width the grids are laid out for                                                               | `harness/gateway-grid.spec.ts` (`--grep offline`)                                 |
| `stack/gateway.e2e.yaml` `guardrails`; `lib/managed-mock.ts` guardrails rule        | the guardrail plugin on stock, r7alt and mcp profiles with no rule of the file's own, so the checks an organization saves in the console (Org Config, Guardrails) run on the stack, and only those (GW-E2E-026); the inspector accepts the section only as exactly `enabled: true`, since a file rule could name a webhook or an external provider                                                                                                                                                                                                                                   | `harness/managed-mock.spec.ts` (offline)                                          |

Integration export harness (Distributed only):

- `lib/export-sinks.ts` declares exact `INTEGRATIONS_*` overrides on **backend and
  worker only**, mapping Datadog, Mixpanel and Linear to local webhook-sink channels
  and S3 to the separate `storage-sink` MinIO service. PostHog uses a per-flow
  self-hosted sink URL; it does not qualify PostHog Cloud.
- `inspectManagedMock({ exportSinks: true })` requires every override and the
  project's storage sink on its default network only. It preserves stock/r7alt/MCP
  profile checks and public-only MCP isolation. Standalone export opt-in is
  deliberately refused; these flows do not yet qualify Standalone exports.
- The webhook sink validates protocol shapes and supports refused/revoked dummy
  credentials and per-channel stop/start. `lib/sinks.ts` exposes those controls.
  `scripts/webhook-sink.test.mjs` and `scripts/export-sink-contract.test.mjs` pin
  the offline contract; `harness/managed-mock.spec.ts` E1-E4 pin the inspector.
- `StorageSink` reads gzip JSON Lines and object metadata and provisions disposable
  users restricted to a minted bucket prefix. `lib/integration-export.ts` drives
  shared UI steps and scoped request-log/sync probes; `lib/org-member.ts` and
  `ScopeActors.workspaceAdmin()` supply publicly invited role witnesses.

INT-E2E-001..008 are ported flow drafts until managed browser runs and a one-sided
fallibility proof for every backend check are recorded. Catalog generation and
runtime proof run on cloud, never on the developer laptop.

MCP profile extension and shared harness interfaces:

| Piece                                                                                                                             | Gives a flow                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | Its own test                                                                                                                 |
| --------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `lib/gateway-admin.ts` `GatewayAdmin`                                                                                             | the gateway admin API with the stack's dev token: `config()`, `orgConfig(org)` (`null` on 404), `putOrgConfig` (proof fallback only), `batch` / `cancelBatch(id, org)`, `key(id)`; local gateways only                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | `harness/gateway-admin.spec.ts`                                                                                              |
| `lib/state-probe.ts` `POLL.GATEWAY_SYNC`, `POLL.SCHEDULE_TICK`                                                                    | the two waits above, named by what they wait on                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | `scripts/state-probe.test.mjs`                                                                                               |
| `lib/clock.ts` `gatewayNow`, `freshWindow`                                                                                        | the gateway's own time (its `Date` header, never the host clock) and a wait until 5-35 s into its minute, so a per-minute pair of calls never straddles a window                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | `harness/clock.spec.ts`                                                                                                      |
| `lib/public-network.ts` `PUBLIC_NETWORK`; the `e2e-public` network in both overlays                                               | an internal compose network on `11.88.0.0/24`, outside every private, reserved and CGNAT range, so the product's SSRF guards (Django `ssrf_guard.classify_ip`, the gateway's netguard) treat it as public with their defaults on; nothing on it leaves the host. Members: the app containers (Standalone `app`; Distributed `agentcc-gateway`, `backend`, `worker`), `mock-llm` at `11.88.0.11` (`llm.e2e-public.test`), `webhook-sink` at `11.88.0.10` (`hooks.e2e-public.test`) and `smtp-sink` at `11.88.0.12` (`smtp.e2e-public.test`); those aliases exist on this network only                                                                                                                                                                                                                                                                                                     | the managed-mock inspector (below)                                                                                           |
| `lib/sinks.ts` `WebhookSink`; `stack/webhook-sink/server.mjs`                                                                     | `url(channel, { failFirst })` to save as an org webhook (the product refuses private webhook addresses at save and delivery); `received(channel)` reads what arrived (host `127.0.0.1:28083`, `E2E_WEBHOOK_SINK_URL`); `/fail/<n>` answers the channel's first n requests 500                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | `scripts/webhook-sink.test.mjs`, `harness/webhook-sink.spec.ts`                                                              |
| `lib/upstream.ts` `MockUpstream`; `stack/mock-llm/server.mjs` scoped routes                                                       | `baseUrl(scenario, { latencyMs, status })` as an org provider's base URL (with `api_path_prefix: '/v1'`): `/s/<scenario>/<lat=ms,status=code or ->/v1/...` answers exactly the unscoped echo (usage 7/7/14) after the delay, or that status with an OpenAI error body; `requests(scenario)` reads every call it received (host `127.0.0.1:28082`, `E2E_MOCK_LLM_URL`)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | `scripts/mock-llm.test.mjs`, `harness/mock-upstream.spec.ts`                                                                 |
| `lib/managed-mock.ts` public-network rule                                                                                         | the inspector the mock-model flows run before registering a model accepts exactly the declared `e2e-public`: the project's own internal bridge on exactly its subnet, attached to exactly the members (an unlisted container is STOP), the pinned addresses and aliases, no pinned alias on `default`; every other service on `default` only; the new read URLs must be `localhost`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | `harness/managed-mock.spec.ts` (`--grep 'managed mock'`: offline N1-N7 per stack)                                            |
| `lib/sinks.ts` `MailSink`; the `smtp-sink` service in both overlays                                                               | `host` / `port` (`smtp.e2e-public.test:1025`, the public-classified address: the product refuses a private SMTP host with its defaults on) to save as an SMTP alert's provider config; `messagesTo(address)` reads every message to exactly that address, oldest first, with subject and HTML (mailpit `axllent/mailpit:v1.31.4`, STARTTLS with a self-signed certificate, which the product always asks for and does not verify; API on host `127.0.0.1:28025`, `E2E_MAIL_SINK_URL`); a pinned `e2e-public` member, inspected with the others                                                                                                                                                                                                                                                                                                                                           | `harness/smtp-sink.spec.ts`                                                                                                  |
| `lib/temporal-probe.ts` `TemporalProbe`                                                                                           | `listSchedules`, `describeSchedule(id)` (paused, interval, queue, workflow, the TaskRunnerInput's activity, action count), `pause` / `unpause` / `trigger` / `deleteSchedule` on both stacks: the `temporal` CLI in Standalone `app` (`127.0.0.1:7233`) or the Distributed `temporal` service (`temporal:7233`) via `bin/e2e compose exec` (DOCKER_HOST, never a context). Standalone only: the product's `registerSchedules()`, `--list` and `setIntervalViaManager`. Its CLI JSON: `fixtures/temporal/schedule-describe.json`                                                                                                                                                                                                                                                                                                                                                          | `harness/temporal-probe.spec.ts`                                                                                             |
| `lib/profile.ts`; `stack/gateway.r7alt.yaml`; `E2E_GATEWAY_PROFILE` in `bin/e2e`                                                  | the gateway config a stack runs: `stock` (default, `gateway.e2e.yaml`) or `r7alt` (Standalone only: exactly stock plus `R7ALT_DELTA`, bodies logged, cache on, other timeouts). `bin/e2e` mounts it for every subcommand and refuses anything else; a flow that needs a profile is tagged `@gw-profile-<name>` and runs only on that profile (`grepInvertPattern`; the catalog lists all); `gatewayProfile()` is what a flow reads its expectations from; `PROFILE_FINGERPRINTS` is what `GET /-/config` shows of each                                                                                                                                                                                                                                                                                                                                                                   | `harness/gateway-profile.spec.ts`, `scripts/quarantine.test.mjs`                                                             |
| `lib/profile.ts` mcp; `stack/gateway.mcp.yaml`; `stack/mcp-mock/`; `lib/public-network.ts` `MCP_MOCK` (H-MCP)                     | the mcp gateway profile, Standalone only: the gateway serves MCP (exactly stock plus `MCP_DELTA`: `mcp.enabled` and a fixed test state key; no MCP upstream of its own) and `bin/e2e up` also starts `mcp-mock`, agentcc-mcpmock built from `agentcc-gateway/cmd/agentcc-mcpmock` by `bin/e2e build mcpmock`, with the scenarios' common fixtures (read-only `echo` and `get_weather`, destructive `delete_file`, one resource, one prompt) behind a fixed test bearer. It sits on `e2e-public` only, at `11.88.0.13` (`mcp.e2e-public.test`), with no default network and no host port, so only members of the internal test network can reach it and it has no route to external hosts. Flows save it as an org server; the MCP flows (GW-E2E-023..025) are tagged `@gw-profile-mcp`; `PROFILE_SERVES_MCP` is what a flow reads, and `GatewayAdmin.mcpServed()` what the gateway shows | `harness/managed-mock.spec.ts` (offline P5, P6, N13-N16), `harness/gateway-profile.spec.ts`, `harness/gateway-admin.spec.ts` |
| `lib/managed-mock.ts` profile rule                                                                                                | the declared profile's file must be the mounted one (`gateway profile <p> expects <file> mounted`), `r7alt` must be exactly stock plus its delta and is Standalone only; the routing contract stays the stock file's; the receipt carries `profile` and the mounted file's hash                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | `harness/managed-mock.spec.ts` (offline P3, P4, N8-N12)                                                                      |
| `lib/gateway-grid.ts` `GatewayGridReader`, `GRID_VIEWPORT`                                                                        | a console AG Grid table read as the user sees it: `headers()` left to right, `column(colId)` / `columnHeaded(label)` top to bottom by `row-index`, `row(i)` / `cell(i, colId)` at a display index, `rowByHeader(i)`, `rowTexts(i)`, and `displayedRowCount()` from `aria-rowcount` (rows a virtualised grid has not rendered count too); AG Grid leaves the DOM out of display order after a column move or a re-sort, so no read uses DOM order. `GRID_VIEWPORT` (1440 x 900) is the width the grids are laid out for                                                                                                                                                                                                                                                                                                                                                                   | `harness/gateway-grid.spec.ts` (`--grep offline`)                                                                            |
| `lib/seeded-clickhouse.ts` `seedSharedSession`, `liveSessionRows`                                                                 | one internal trace-session id in two or more projects of one organization, a state `trace_sessions` (ORDER BY `(project_id, trace_session_id)`) and `spans` hold but the collector never writes: for a control proving a lookup stays keyed to a record's own project (F54 row-source liveness). Loopback stacks only; writes `spans` (label `attrs_string['e2e.seeded']`) and `trace_sessions` (per-plan nonce in fixture-only `e2e_seed_id String DEFAULT ''`, added only on the loopback stack after the foreign-row preflight), never PG or the dict; a plan re-run writes nothing; collector rows have no marker and are refused even at a newer version; tombstones require the marker in their INSERT SELECT                                                                                                                                                                      | `scripts/seeded-clickhouse.test.mjs`, `harness/seeded-clickhouse.spec.ts`                                                    |
| `lib/profile.ts` mcp-catalog; `stack/gateway.mcp-catalog.yaml`; `stack/docker-compose.mcp-catalog-e2e.yml`; `lib/real-catalog.ts` | the mcp-catalog gateway profile, Standalone only: MCP served exactly as under mcp but with no MCP upstream at all and no connector catalogue named (stock plus `MCP_CATALOG_DELTA`), no MCP mock, and Django serving the catalogue this build bundles with nothing mounted over it; `PROFILE_REAL_CATALOG` tells it from mcp; GW-E2E-026 is tagged `@gw-profile-mcp-catalog`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | `harness/managed-mock.spec.ts` (offline P8, P9, N17-N18), `harness/real-catalog.spec.ts`, `harness/gateway-profile.spec.ts`  |

#### Gateway walks (`playwright.walk.config.ts`)

The gateway walks (W00-W19) run each flow under `flows/walks/` in Chromium and Firefox
projects, with separate org sets, at most 3 workers per Standalone VM, a receipt per test, and a
browser boundary that aborts and records every non-local request. `bin/e2e` reads the gateway
profiles (`stock`, `r7alt`, `mcp`, `mcp-catalog`, `mcp-signin`, `mcp-otel`, `pricefeed`,
`onekey-cat`) from `e2e/stack/gateway-profiles.json` and refuses, before anything starts, a profile
this head cannot run. `E2E_WALK_PINS=1` adds the walk environment pins with every port on
loopback, and `E2E_GATEWAY_REPLICAS=2` adds a second gateway (MCP profiles). Managed overlays
always include `docker-compose.walk-mocks.yml`; `E2E_WALK_MOCKS=1` opts into starting the named
walk mock services, not adding the overlay. Pieces, launchers, the strict egress-kit wrapper and what is owed:
[`walks/README.md`](walks/README.md).

#### Value-free publication mode (`lib/value-free.ts`)

For evidence that leaves the VM (HR-JRN-01). It has one switch: `E2E_VALUE_FREE=1` for the run.
`test.use({ scopeValueFree: true })` and `new ScopeActors(prefix, roster, { valueFree: true })` can
only confirm it. Either one without the switch is refused, and so is `false` under it.

**What leaves the machine is one publish directory, and nothing else.** The value-free reporter
assembles it when the run ends, after every reporter and worker has flushed
(`lib/value-free-publish.ts`). By default it is `value-free-publish` next to the first project's
output directory: `e2e/value-free-publish` for `playwright.config.ts`. `E2E_VALUE_FREE_PUBLISH`
names another. Transfer only through the publication choke point (`lib/value-free-boundary.mjs`),
the one program a consumer runs:

```bash
node e2e/lib/value-free-boundary.mjs declare -- <pull>...  # the walk launcher, before dispatch: prints nothing, or VFE3
node e2e/lib/value-free-boundary.mjs publish --registries <dir> [--print <held>] -- <pull>...  # the final check
node e2e/lib/value-free-boundary.mjs export <publish-dir> <export-dir>  # the seal check; pull <export-dir>/** only
node e2e/lib/value-free-boundary.mjs ci --registries <dir> --print <held> --publish <dir> --github-output <file>
node e2e/lib/value-free-boundary.mjs seal <publish-dir>                 # 0: intact publication; 1: anything else
```

`node e2e/lib/value-free-seal.mjs <publish-dir> [--export <export-dir>]` still works, with the
same exit codes, for callers outside this tree: run as a program it forwards to `seal` and prints
nothing of its own.

The check exits 0 for an intact publication. It exits 1 for an intact refusal record (transfer
it alone) or a missing directory. A broken seal also exits 1: anything changed or added after
assembly, or a refusal record holding anything outside its vocabulary. The check holds no
registry, so it cannot scan a record of its own, and it publishes **nothing**: it moves the
directory off the pull path to a sibling `.<name>-withheld-*` on the machine, never deleting
it. A directory that holds sources (a checkout, a package, or source files at its top) is never
touched, so it stays where it is: a consumer transfers on the check's verdict, never on what
is left at the path. With `--export`, the verdict is a directory: an intact publication, or an
intact refusal record alone, is copied to `<export-dir>`, sealed again there against the seal
the check read, and anything else exports nothing; whatever was at `<export-dir>` before is
moved aside first. The export directory's name must be shorter than `VALUE_FREE_MIN_LENGTH` (6),
the shortest value a run can register, so no registered value fits in it; a longer name exports
nothing. Every other file the run writes, including test-results, the reports where Playwright
put them, attachment sources and the publish directory itself, stays on the machine.

**The publication choke point** (`lib/value-free-boundary.mjs`) is the stopping rule: every byte
that crosses the boundary is scanned against the run's registered values immediately before the
transfer, and any hit refuses the whole export. A consumer runs it last, from its pull root, with
exactly the patterns it pulls (`<dir>/**` or an exact relative path, holding no blank; anything
else is refused). It reads:

- every path component of every file and directory a pull transfers, relative to the pull root,
  and that whole relative path, so a value across components is found too;
- every such file's content, through every layer, with the reporter's own `Scan`
  (`lib/value-free-reading.mjs`);
- the held output (`--print`) as its own document(s), through every layer: a pretty-printed JSON
  document is one only without the status constant after it, so its base64 fields are decoded
  here;
- the exact bytes it emits: the held output, the newline it is given when it does not end in one,
  and the status constant after it, as one buffer, which is what it then writes, in full (CI: also
  the step output line the upload echoes).

It keeps three invariants, each with its own regression:

- **I1, the registries whole or nothing.** The values come from `--registries`: the runs'
  registries, which the consumer created and gave each run (`E2E_VALUE_FREE_REGISTRY`). A runner
  given a registry adopts it, recorded by its own file there, and leaves it to its creator; the
  check removes them once read. Registries it cannot read whole (missing, a line cut short or not
  JSON, a run registry no runner adopted, a hard link, or a string shorter than
  `VALUE_FREE_MIN_LENGTH`, which a run never registers) are never read as an empty list: the
  export is refused, nothing but `VFE1` is printed and the exit is 1.
- **I2, exact bytes, constants only.** What it prints of its own is one status constant, never a
  reason, a count, a path or anything it scanned: `VFOK` (clean), `VFRR` (a refused run: only its
  refusal record crosses), `VFE1` (registries), `VFE2` (a registered value, or a layer it cannot
  read), `VFE3` (a link, a hard link, a special file, a pattern it cannot enumerate), `VFE4` (no
  intact publication), `VFE5` (usage, an unreadable held output, any error). Each is shorter than
  `VALUE_FREE_MIN_LENGTH` bytes, newline included, so no registered value fits in one, with or
  without a registry. Nothing goes to stderr.
- **I3, every consumer.** No script or workflow runs the seal or boundary internals, or captures,
  pipes, redirects or echoes the choke point's output. `harness/value-free-consumers.spec.ts`
  keeps the inventory over `e2e/**`, `bin/**` and `.github/workflows/**`, with the one form each
  consumer below may use.

Any refusal (a hit, a scan problem, a link, a hard link or a special file, a pattern it cannot
enumerate, registries it cannot read whole, any error) **refuses the whole export**: every pull's
entry, the exit file included, is moved aside to a sibling `.<name>-withheld-*` on the machine
(never deleted), the held output is not printed, it exits 1 and prints one constant. A refused walk
therefore lands nothing, not even `command-exit.txt`; its receipt (`exec/exit.json`, exit 1, not
lost) and the constant in `output.log` tell it from a lost VM.

A pattern it cannot enumerate exactly (a glob, a blank, an absolute or escaping path, an empty
pattern) refuses the whole export with `VFE3`, and **nothing any declared pattern reaches stays on
the pull path**. After the exact pulls move to their siblings,
`lib/value-free-reach.mjs` reads every declared pattern the way the walk rig's collect reads a pull
(cloud `vm/exec/exec-collect.sh`: prefixed with the pull root unless absolute, split on blanks, each
word globbed by bash with globstar, nullglob and dotglob, read lexically, a directory whole), so a
withheld dot sibling a glob reaches counts too. Everything reached below the pull root moves, by its
path below it, into one quarantine outside the pull root, `value-free-withheld-*` beside the
registries (on the walk VM that is `/tmp`, which the rig never collects); never a sibling, never
deleted. A pattern that reaches the pull root itself (an empty pattern, or one climbing above the
root) reaches the whole root, so **every entry below the pull root moves, the checkout's `.git`
included**: the checkout is emptied rather than any of it transferred. No bash (the deepest literal
directory bounding each word is read instead), no quarantine or a move that failed makes it a check
that failed (`VFE5`). The walk job's wrapper withholds the same way in bash when the check does not
reach its verdict.

**A pull declared outside the pull root** (an absolute path, an escaping
one, the root's parent) is refused twice over:

- **before dispatch**, the walk launcher runs the choke point's `declare` check on exactly the pulls
  it is about to declare: any pull that is not one exact path below the pull root (absolute,
  escaping, the root or above, empty, a glob, a blank) prints `VFE3` and refuses the launch (exit
  64, a fixed line that never echoes the declaration), before `print`, `plan`, host-guard or
  `omega-cloud`, so no job is created to collect it;
- **on the VM**, if one is ever dispatched, the final check withholds what it reaches that the rig
  transfers, inside the pull root or not. The rig takes a regular file whose lexical path lies below
  `/Users` or `/Volumes` (`exec-collect.sh`'s `add()`), so such a target there moves into the
  quarantine below `outside/` by its absolute path, never deleted, and the verdict is `VFE3`; what
  the rig never transfers is never moved. A directory that holds the pull root, the working
  directory, the registries or the quarantine, or is one the rig transfers from, never moves itself:
  what it holds is read in its place, so `..` reaches the root's siblings, not only its entries. The
  registries, the quarantine and this machine's home (unless the rig could collect from it, below)
  are never read.

**A pull is admitted only where it canonically lies**. On the
path that ACCEPTS a pull too, the choke point resolves every existing ancestor below the pull root
before anything is classified (`canonicalEntry`): the deepest that exists must lie exactly where
the pull spells it, so a link below the root on the way (`link/sub/**`, `a/door/f.txt`) refuses
the pull. What crosses must be disjoint, canonically, from the private side (the registries, an
unread home): a pull whose tree holds the registries is refused. A pull refused so is withheld the
way the rig reads it (the link moves where the rig would take it; the private side never moves).
`isProtectedPath` reads a path, the working directory and home both as spelled and resolved, so a
link to one is protected too.

**Every path is resolved before it is classified** (two cases, both
reproduced by an actual rig transfer: a TMPDIR spelled `../outside/tmp`, through `/tmp/..` or as a
link passed the wrapper's private-side check, so its registries and its fallback quarantine went
where a declared `../outside/**` reached them; and a home below `/Volumes` or `/Users` was never read,
so a receipt declared below it stayed). `realpath` resolves links, `.`, `..` and relative spellings;
the pull root, the registries, the quarantine and the home are compared in that canonical form, and
so is where each reached entry lies (its directory resolved, the entry itself never followed). Whether
the rig would take an entry is read both ways, its lexical path (`add()`'s own reading) and its
canonical one, since withholding more is the safe side.

- **The private side.** The walk job wrapper makes a fresh directory in each candidate (TMPDIR, then
  `/tmp`), resolves it and takes the first that lies neither at or below the pull root (either
  spelling) nor at, below or above `/Users` or `/Volumes` (or their resolved forms); a candidate that
  fails is removed (it is empty). Its registries root and its fallback quarantine go there. If no
  candidate qualifies, **nothing runs**: one constant (`VFE5`), no exit file, exit 1. The choke
  point's quarantine is chosen the same way: beside the registries, else the system temporary
  directory, else `/tmp`, each made, resolved and checked, so nothing withheld is ever collectable.
- **The home.** This machine's home is left unread only when neither its canonical path nor its
  spelling is a directory the rig could collect from. A home below `/Users` or `/Volumes` (or a link
  to one, or one holding the pull root) is read like any other directory: what a pull reaches below
  it is withheld; the home itself never moves.
- **Links.** The rig follows a link that a pattern names (`../outside/link/**`), so a match reached
  through a link (one that is not the pull root's own spelling, nor `/Users` or `/Volumes`) is
  withheld by moving that link, never what it points to. A match that resolves into the quarantine or
  an unread home but that the rig would take by its spelling is a check that failed (`VFE5`).

The consumers:

- **CI** (`.github/workflows/e2e-ci.yml`, the e2e job): with `E2E_VALUE_FREE=1`,
  `Prepare the value-free registry` creates a 0700 registries root with the run's registry and a
  held-output file before the suite; `Run E2E suite` holds its output there; then
  `Value-free publication boundary` (`ci` mode) uploads the sealed `html/` alone, or a refused
  run's `REFUSED.json` alone, only when it, its paths, the held output and the step output line
  are clean, and prints exactly those bytes. Anything else uploads nothing, prints one constant
  and fails the job. The stack dump on failure is skipped in this mode: the stack's logs are never
  scanned. Unset, the first step and the boundary step are skipped, the suite prints as before and
  the upload is exactly what it was. GitHub Actions is down for the org (billing), so this is
  proven by running the steps' own scripts from the workflow file; a real CI run is owed.
- **Walk launches** (`e2e/walks/launch/launch.sh`): `E2E_VALUE_FREE=1 launch.sh <group>`
  forwards the switch and runs the job under `e2e/walks/vm/value-free-job.sh` with exactly its
  pulls: each profile's `walk-results/<group>/<profile>/vfout/**` and
  `walk-results/command/command-exit.txt`. The wrapper gives the job a fresh 0700 registries root
  (`E2E_VALUE_FREE_REGISTRIES`) and holds its output on the VM. `run-walks.sh` lists
  `./lib/value-free-reporter.ts` first in its reporter override, gives each profile's run its own
  registry below that root, publishes into `walk-results/<group>/<profile>/value-free-publish`,
  and exports it with the choke point's `export` mode to `walk-results/<group>/<profile>/vfout`
  (its constant joins the held job output); a rejected publication exports nothing and fails the
  job. The wrapper then writes the true exit code to `command-exit.txt` (one integer)
  and runs the choke point's `publish` mode over the pulls and the held output. The job exits 0 or
  1 only. `playwright.walk.config.ts` turns trace,
  screenshot and video off in this mode. A gate or hosted job assembles no publish directory, so a
  value-free launch of it is refused (exit 64), and `run-walks.sh` refuses value-free mode without
  the wrapper's registries root (exit 64). Before anything is printed, planned or dispatched, the
  launcher runs `declare` over exactly these pulls; a pull that is not one exact path below the pull
  root refuses the launch (exit 64), so no job is created.

- **An external runner and nightly** (outside this tree) transfer
  `e2e/value-free-publish/**` from the checkout after the seal CLI. The reporter reads the publish
  directory's own path and every published entry's path below the checkout whole as well as by
  component, so a value spanning them (`value-free-publish/MANIFEST.json`) refuses the run, and the
  seal CLI prints only the choke point's constant. Their documented way is the choke point itself:
  create a registries root, give the run its registry, hold the run's output, then
  `node e2e/lib/value-free-boundary.mjs publish --registries <root> --print <held> -- e2e/value-free-publish/**`
  from the checkout before the pull. Adopting it is owed to main.

What the choke point does not cover: the walk rig's own channels (the paths it lists in
`manifest.tsv` and lands files under, and its prepare output), which carry the VM checkout's
absolute path. The value-free rules put them out of its scope on one condition, which
`harness/value-free-disjoint.spec.ts` proves: every value a value-free run mints and registers is
disjoint from the checkout path and each of its components, no substring overlap either way
(absolute local paths reaching a public artifact stay the public-push hygiene gate's job). Nor
text the CI platform renders from the workflow source itself (step names, artifact names), which is
source, not run output; and, in the held output, a
JSON document that is neither the whole output nor one line of it (one framed by other lines), which
is read as text, its encoded fields not decoded.

A publication holds only allowlisted sources, under names the harness chooses:

| Path                                                       | What                                                                                                         |
| ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `reports/json-<n>.json`, `blob-<n>.zip`, `counts-<n>.json` | every json, blob and walk-counts report, projected to its closed record schema, every string sanitized       |
| `html/` (`html-<n>/`)                                      | the HTML report: its embedded data re-serialized entry by entry, its text files sanitized, binaries left out |
| `attachments/<n>.<ext>`                                    | every file a json report attaches by path, copied by content (text sanitized, a binary withheld)             |
| `runner.log`                                               | the runner's stdout and stderr, in the order written, sanitized as one text: lines of text, never a document |
| `MANIFEST.json`                                            | the seal: every other file with its size and `sha256:` digest                                                |

In every report, a test's stdout and stderr become one pointer to `runner.log`. A blob's stdio
events are dropped, because a value split across chunks would read whole again when they are
joined.

**Every report record is closed** (see
`lib/value-free-record.mjs`). A record is a JSON document the harness itself publishes as a
report: `reports/json-<n>.json`, `reports/counts-<n>.json`, each event line of
`reports/blob-<n>.zip`'s `report.jsonl`, the HTML report's embedded `report.json` and
`<fileId>.json`, `MANIFEST.json` and `REFUSED.json`. Each record type admits a closed vocabulary
of field names, taken field by field from Playwright 1.62's serializers and
`walks/vm/counts-reporter.cjs`, and one kind per field: a name (a string, sanitized), a type (one
of a fixed list), a count, a boolean, a hash, a payload (an attachment's body), another record
type or a list. The open containers Playwright fills with arbitrary data (config and project
metadata, reporter options, `webServer`) are projected to their empty value. The builder publishes
a record only after it is projected and validated (the codec, before anything of it is kept), and
the publication scan and the final check refuse a published record that holds anything else,
before reading further into it. An unknown field (`unknown-field`), a kind its field does not
admit, an arbitrary nested object included (`unknown-kind`), a record that is not one document of
its type, and any other file under `reports/` refuse the publication. A junit report has no closed
schema, so the plan refuses one before any worker. Payloads are not records: `attachments/`, the
HTML viewer and an attachment's body inside a record are sanitized and scanned through every
layer, as below.

**`runner.log` is a closed record too** (R2, the structural re-review's S2;
`lib/value-free-record.mjs` `RUNNER_LOG` and `checkLog`). Its schema admits one kind, a line of
text: a name, as a title, a message or a stack is in every other record. A document in the
runner's output is a record the runner log admits no kind for, and refuses the publication
(`unknown-kind`, area `runner-log`): a JSON object or array that opens at the start of a line
(after white space) and closes at the end of one, on that line, across lines or as the whole
output, or an embedded zip. A fragment inside a line (`Error: API 400 on /x: {...}`) is text of
that line. The builder reads the runner's output so before anything in it is traversed (and does
not write `runner.log`), the publication scan and the final check read a published or pulled
`runner.log` so, and the output the runner prints at its exit is read so too. A json or junit
reporter with no output file would write its report into that output: the plan refuses it before
any worker (`it writes its report to the runner output`).

Every source is sanitized, and then scanned, through every layer it holds
(`lib/value-free-codec.ts`, with the scan, the shared layer helpers and the zip codec in plain
Node in `lib/value-free-reading.mjs`, which the final boundary check reads with too). One reading
serves both, so the scan reads exactly what the sanitizer wrote, from the original bytes and never
through a lossy text round trip. It reads:

- zips, entry by entry, their names too;
- embedded zip `data:` URLs;
- in JSON and JSONL, the base64 fields (blob `base64` and stdio `data`, `buffer` anywhere,
  the json report's attachment `body`);
- every string, and every object key, that is itself a JSON document;
- every JSON string and key on its own, so a value escaped twice inside a nested document is still
  found.

Each decode is one layer. The bound is `NESTING_BOUND`, 8 layers; the deepest real report layer
is 5. The scan looks for every registered value, raw and JSON-escaped, colour codes removed or
not. It also reads every path component and the merged output, which is printed only after the
same sanitizing. Any of the following **refuses the run**:

- a scan hit;
- a payload nested deeper than the bound;
- a payload in an encoding the codec does not decode: a binary that is not a zip inside a
  field, gzip for example. An attachment whose `contentType` declares it binary is withheld
  instead;
- a zip it cannot read;
- a value refused at registration (below);
- a reporter whose output cannot be resolved, which means any reporter other than the built-in ones
  and `walks/vm/counts-reporter.cjs`;
- an unsafe target: a report that would write into the publish directory, hold sources or have a
  source extension, including through a `PLAYWRIGHT_*` override;
- a source that is a link, a hard link or a special file;
- anything written into the publish directory before assembly.

A refused run fails. Its publish directory then holds only `REFUSED.json`
(`{ "format": "e2e-value-free-refusal/1", "status": "refused", "reasons": [...] }`). The record
and its name are published content. Each reason is cut to a fixed vocabulary
(`lib/value-free-vocabulary.mjs`):

- reason codes;
- value kinds and the fixed registration kinds. A label a caller passes to `registerAs` that is
  not one of them is recorded as `value`;
- the category of the published file (`area`: `runner-log`, `reports`, `html`, `attachments`,
  `manifest`, ...);
- the layer (`place`: `name`, `content`, `zip-entry`, `base64-field`, `nested-json`, ...);
- lengths, depths and `file:line`.

There is never a value, a path or a location string. The record, its name and the publish
directory's own path, below the checkout, are scanned like every published file. When the path
holds a registered value, or no clean record can be written (the full one, then reason codes
alone), **nothing** is published and the run fails. The run's only output is one
`value-free: REFUSED.` line. At the runner's exit the seal and the output about to be printed are
checked once more.

When value-free mode is on, every email, UUID, org key, JWT and ScopeActors prefix becomes
`vf-<run>-<kind>-<16 hex>`, and so does every value a worker registers. The token is an
HMAC-SHA256 under the run salt, after a five-letter segment the salt also picks. That covers:

- ScopeActors evidence (`seeded-scopes`, `scope-api-receipt`);
- `walk-receipt.json`;
- `attachEvidence(testInfo, ...)`;
- log lines;
- the report.

- ScopeActors registers every identity value it seeds or reads. After provisioning, and again at
  teardown, it reads back from Postgres everything the seeded orgs hold:

  - user ids, emails, local parts and names;
  - org ids and names;
  - every workspace's id and name, the owners' default workspaces included;
  - every org key, whole, as its 8-character prefix and masked.

  It also registers the passwords, JWTs and invite tokens it handles. Names that every seeded org
  shares (`Default Workspace`) are registered too, so absolute paths that contain them are tokenized
  in the reports. Value-free, the set mints every email at `<prefix>.test`:
  signup names an org after its email domain's first label (`accounts/utils.py` `first_signup`), so
  each set's orgs are named for its own prefix, never the fixed `futureagi` that `@futureagi.com`
  gives them, which is a component of every checkout below `/path/to/futureagi`. Off, the
  domain is `futureagi.com`, as it was. So the checkout's own absolute path now appears in a
  value-free run's reports as it is: it is not a registered value.

- Some values cannot be substituted safely, so registering one **refuses the run**:

  - a value shorter than `VALUE_FREE_MIN_LENGTH` (6);
  - a value that overlaps the token syntax. It contains the run's `vf-<run>` or any token's shape,
    or it is part of a token's fixed text, alone or followed by hex.

  The refusal records the kind of value, its length and `file:line`, never the value. ScopeActors
  gives every name it makes a long, random prefix.

- The runner creates the salt (or takes `E2E_VALUE_FREE_SALT`) before it starts a worker. The same
  value gets the same token in every worker of a run, and the next run gets different tokens and
  a different segment. The salt is never written to evidence.
- Registrations are shared through a 0700 directory under the system temp dir
  (`E2E_VALUE_FREE_REGISTRY`), written before anything that could quote them is sent. The report
  therefore tokenizes a worker's org name as that worker did. The directory is deleted when the
  run ends.
- The runner holds its own stdout and stderr. When the run ends it prints them once as one stream,
  on stdout only. Both streams appear in the order they were written, sanitized as one text. The
  line reporter prints a test's title before that test's worker can register a value in it, so
  nothing it prints reaches the terminal before then. While it holds, it prints only progress
  counts.
- Colour and other escape codes (ECMA-48, including colon-form colours and OSC hyperlinks, raw or
  JSON-escaped) are removed before anything is matched, so a highlighted UUID is still found.
- Raw values stay in memory for the requests that need them.
- The report needs `lib/value-free-reporter.ts` as the **first** reporter. `playwright.config.ts`
  adds it when the switch is set. A CLI `--reporter=` replaces the config's list, so name it first
  there: `--reporter=./lib/value-free-reporter.ts,line`. Listed without the switch, it refuses the
  run. It scrubs titles, errors, steps and attachments when each test ends, and again when the run
  ends.
- As defence in depth, it also scrubs the files the run leaves on the machine
  (`lib/value-free-scrub.ts`), which are never pulled.
- An attachment that is a link is never followed, and a hard link or a special file is never read.
- `attachEvidence` with a path takes only a file under the test output directory.
- The ScopeActors fixture refuses a value-free test without that reporter, or with trace,
  screenshot or video on. Pixels and trace network bodies cannot be hashed, and `WalkReceipt.shot`
  records a withheld note instead of a screenshot.
- **Nothing provisions before the publication is admitted**.
  The reporter decides admission in `onBegin`, synchronously, before any worker is forked: first,
  with a publication plan that refuses nothing, it admits the run; a plan with any reason throws
  there (Playwright records the reporter error and fails the run; the refusal record is still
  assembled at exit) and admits nothing. The grant is the runner's own in-process state, never
  anything in the environment. S3: a worker is admitted only by its
  own runner, asked over the IPC channel the runner forked it with, the one input of a worker that
  nothing set from outside can supply (worker mode itself is chosen by `TEST_WORKER_INDEX`). Before
  any worker exists the runner answers every child it forks that asks (`node:diagnostics_channel`
  `child_process`) with its grant at that moment, or nothing; the worker accepts only its own
  runner's grant for its run's salt and registry. A process with no such channel, or whose parent
  does not answer within 10 seconds, is not admitted, whatever its environment holds (a reporter
  marker, a salt, a registry, an `E2E_VALUE_FREE_ADMISSION` proof). The runner still drops a
  pre-set marker or proof when it starts. Without admission, `provisionActor` (before signup), the
  API client (before any request), `ScopeActors.provision` (the shared dependency W00 and every
  walk provisions through) and the ScopeActors fixture refuse, and nothing is sent.
- The walk receipt guard (`lib/walk/receipt-values.mjs`) runs on every receipt in both modes. In
  value-free mode it runs on the sanitized receipt.
- Unset, every output is exactly what it was before, and no publish directory is made.

Its tests:

- `harness/value-free.spec.ts`, `harness/value-free-outputs.spec.ts` and
  `harness/value-free-publish.spec.ts` (offline). The last runs one child run per attack on the
  publication boundary and reads back only what each publishes. The attacks are:

  - a test writing into the publish directory;
  - a symlinked or hard-linked attachment;
  - an env redirect of the HTML report into the publish directory;
  - a late custom reporter;
  - a json report with a source extension;
  - an attachment source outside the output directory;
  - a value split across stdout and stderr;
  - a value overlapping the token syntax;
  - a workspace named `vf-value-`;
  - encoded payloads: the value base64'd in a `buffer` field, a zip in one, and a JSON document
    in a string holding base64 JSON and a value with quotes. Each must publish. The final
    published attachment is decoded layer by layer, and must hold the token in the json, html
    and blob reports;
  - a value with quotes inside a JSON document that is an object key, inside another: it must
    publish with the token in the json, html and blob reports; one nested past the bound in keys
    is refused;
  - a payload nested past the bound, and a gzipped one: both refused;
  - `runner.log` registered, and a synthetic registration label. Both are refused, with a record
    that names neither;
  - the record's own format text registered, and a publish directory named after a value: both
    publish nothing.

  The scan (`harness/value-free/scan.ts`) decodes every layer itself, independently of the codec,
  gzip included, to a depth of 32. The same file also covers the codec's bound and its
  idempotence, the vocabulary, the seal check's withholding, and the CI seal step run from the
  workflow file against intact, refused, broken, missing and html-less directories.
  `harness/walk-harness.spec.ts` covers the value-free launch's pulls and its job command, and runs
  `run-walks.sh`'s own seal step against them: an intact publication and an intact refusal record
  are transferred, a rejected publication holding sources transfers nothing and keeps them intact.

- `harness/value-free-walk-boundary.spec.ts` (offline): the walk job end to end. Each case
  runs the launch's own value-free job command from a scratch pull root, with `run-walks.sh`'s own
  walk environment block and seal step around the real boundary child under the walk's reporter
  list, and reads back independently every file the launch's own pulls would transfer (path and
  every layer) and the job's printed output. The child registers one name: a control, `export`,
  `value-free-export`, one equal to each other fixed pulled path component of six characters or
  more (`walk-results`, the first profile, `command`, `command-exit.txt`, `MANIFEST.json`,
  `runner.log`, `reports`, `counts-1.json`, `REFUSED.json`), a word of the final check's own line,
  and a name the job prints. None may cross; each case also pins what is pulled and the job's exit.
  It also has cases with real adopted registries: a name ending in LF that the
  job's output ends without, one ending in CR LF after a bare CR (I2), and `registries` registered
  before a registry line is cut short (I1). The same file covers the export name's length guard,
  the final check's CLI (a value one layer down, as a component and across components, in the held
  output, ending in the newline the output lacks or spanning the output and the status; a symlink,
  a hard link, an unenumerable or escaping pattern; missing, partial, non-JSON or short-string
  registries; a registered word of its own status lines), the choke point's constants, and both CLIs
  reached through a linked directory.

- `harness/value-free-structural.spec.ts` (offline): a
  pull through an in-root link to an outside directory, an exact path whose ancestor is a link, and
  a pull whose tree holds the registries are refused on the accepted path (the link withheld, the
  outside and the registries untouched), with a clean control; a link to the working directory or
  home is protected. An unknown field, an unknown kind and an arbitrary nested object in a json,
  counts, blob or html report record refuse in the builder, the publication scan and the final
  check, with controls, and an attachment payload with arbitrary keys still publishes; a junit
  report refuses at plan time. A pre-set reporter marker or admission proof is no admission; a
  refused plan throws in `onBegin`; `provisionActor` and the API client send nothing without
  admission; and the first reporter with a clean plan admits (control). R2: a document in the
  runner output (one line, a block of lines, the whole output, indented, an embedded zip) refuses
  in the builder, the publication scan, the final check and the output printed at exit, while lines
  of text (a fragment inside a line included) publish; a json or junit reporter with no output file
  refuses at plan time; a worker marker, salt, registry and the parent-pid proof all set from inside
  a worker of a runner that admitted nothing are no admission (the test waits out the 10-second
  bound); and two real value-free child runs (`value-free/admission.config.ts` and
  `admission.child.ts`, a stub request context, no network) show a worker provisions when its
  runner's value-free reporter is first and sends nothing when it is listed after `line`
  (controls).

- `harness/value-free-consumers.spec.ts` (offline, I3): the consumer inventory; the CI
  job's own value-free steps run from the workflow file around the real boundary child, which
  registers `intact`; and a pull of `e2e/value-free-publish` with a control, `intact`,
  `e2e/value-free-publish` and `value-free-publish/MANIFEST.json` registered, through the seal CLI
  and through the choke point. None may cross.

- `harness/value-free-reach.spec.ts` (offline): a held output that is
  one pretty-printed JSON document (with or without its final newline, or a json report's
  attachment `base64` field) holding the value base64'd is refused, with compact and clean controls
  (I2); and a pattern the check cannot enumerate exactly (the glob `reports/*/raw.log`, a glob in
  the first component, a pattern holding a blank, an absolute pattern inside the root, an empty
  pattern, a glob whose literal part holds the other pulls, and that glob with no bash) leaves
  nothing the rig's own reading of the pulls would take, and what it withheld stays on the
  machine; the walk job wrapper with that glob pull, when the check refuses and when it never
  reaches its verdict (I1). Declarations: the `declare` check refuses every pull that
  is not one exact path below the pull root, and passes the launcher's own; the walk launcher (a
  copy beside a `groups.json` whose group name escapes the pull root, host-guard and `omega-cloud`
  stubbed) exits 64 in `print`, `plan` and `run` with nothing dispatched; a declaration outside the
  pull root (an absolute exact receipt, an escaping exact path, an escaping directory pull, an
  absolute glob, the root's parent, and the receipt with no bash) leaves nothing the rig's reading
  would take and withholds everything on the machine; a declared path the rig never transfers is
  left where it is; and the wrapper with an absolute and an escaping outside target, when the check
  refuses and when it never reaches its verdict.

- `harness/value-free-disjoint.spec.ts`, which needs the Standalone stack:
  `value-free/mint.child.ts` provisions the J03 actor set through the ScopeActors fixture under the
  switch, with a registry the test gives the run; the choke point's own `registeredValues` reads
  back every value it registered (every seeded email, user id, org and workspace name among them),
  and each must be disjoint from this checkout's path and each of its components, both ways, case
  folded. Overlaps are reported by length and component only.

- `harness/value-free-live.spec.ts`, which needs the Standalone stack. It runs the J03 two-tenant
  actor set three times: value-free twice, then off. Each run carries adversarial probes:

  - a registered org name in a plain attach;
  - the displayed key;
  - a colour-split UUID in a log line;
  - a UUID in a test title;
  - binary attachments, a text file attached by path, and a file left unattached;
  - every workspace and org name of both owners, read from Postgres;
  - an org key's prefix in a test title, printed by the line reporter;
  - a json report selected by `PLAYWRIGHT_JSON_OUTPUT_NAME`, and one whose file name carries the
    key prefix.

  A fourth value-free run creates a workspace with a five-character name, and must be refused with
  only its refusal record published. Without the switch, the explicit option and the reporter
  listed alone must both be refused. The scan reads only what each value-free run publishes: every
  path and file of its publish directory, with the json, html and blob reports opened, and its
  stdout, stderr and the two together. It looks for every raw value Postgres holds for the seeded
  actors, shared and short names included. Off makes no publish directory, so everything it leaves
  is scanned as the positive control. There, the walk receipt guard refuses the raw email the
  control writes into its receipt, an expected failure. The value-free runs'
  own seeded values, read back for their own windows, must each be disjoint from the checkout's path
  and its components; the checkout's path is not a registered value, so in a value-free run's output
  an occurrence of a forbidden value wholly inside an occurrence of that path (the off run's and the
  key probe's `futureagi` org name, inside `/path/to/futureagi`) is the path, not a leak,
  and every other occurrence still is.

---

## Cross-product launch journeys

The S1 platform shard reserves **JRN-E2E-000 / J00** and **JRN-E2E-003 / J03**:

- [`flows/journeys/default-off-census.spec.ts`](flows/journeys/default-off-census.spec.ts): seven default-OFF census legs.
- [`flows/journeys/two-tenant-isolation.spec.ts`](flows/journeys/two-tenant-isolation.spec.ts): eight two-tenant isolation legs.

These are **executable rehearsals with remaining harness dependencies, not full
release acceptance coverage**. Each leg has a separate `@flow @journey` test and a
`journey-contract` annotation. IDs are `JRN-E2E-000.L1` through `.L7` and
`JRN-E2E-003.L1` through `.L8`; `journey-flow-id` retains the allocated journey ID
(HR-JRN-10 requests native multi-leg catalog support).

J03.L1 now runs without a definition-time skip. A seeds through the public API;
B attempts direct-parent and mismatched-parent writes; authenticated A API and
PG reads run in `finally` after **every** B attempt. A successful cross-org write
is a failure, never an expected failure or a reason to skip. The historical
`Cell.dataset=NULL` case is separate (`JRN-E2E-003.L1-legacy`, HR-JRN-09), so it
cannot change the ordinary-cell result to skipped. The whole J03.L1 release
obligation still needs that historical case.

J00.L2 exercises capabilities, runtime config and five absent UI routes on the
standalone stock rehearsal. J00.L3 executes the signed-in edge HTTP matrix at
both public frontend and API origins. J00.L5 executes the three unavailable
Results paths against an owned dataset; J00.L6 reads capabilities for both orgs
and their members. J03.L2 seeds an empty legacy KB and checks enumeration;
J03.L3 exercises duplicate-row, column-rename and delete-row writes with A API
and PG graph reads; J03.L4 executes the three Results absence paths. J03.L5 and
L7 execute edge/sandbox absence requests. Incomplete contracts then report a
specific harness-gap skip **only after checking there were no soft failures**.
W00, full Console/MCP capabilities, server readiness/registry/DNS, playground
resource bounds, revoked references and retained-fixture restart/replacement
still require the named harness additions. No skipped or partial leg closes a
release obligation, including any `if_absent: null` leg.

**Value-free publication is a provisioning prerequisite for these journeys.**
HR-JRN-01 has not been accepted into this composition. The journey entrypoint
refuses before W00 dependencies or ScopeActors can provision; direct spec runs
have an explicit definition-time harness-gap skip. Keeping raw reports private
and pulling sanitized summaries does not satisfy this contract. There is no
operator override. An accepted native reporter, publication boundary and live
combined proof must replace the gate before these legs execute. The walk
boundary remains unchanged: an external asset violation is a separate finding,
not suppressed to make an isolation test green.

Cloud-only rehearsal (currently refuses under HR-JRN-01): use
`playwright.journeys.config.ts`, which consumes `playwright.census.config.ts`.
That accepted graph runs W00 once for Chromium and once for Firefox before
that browser's journey consumers; never pass `--no-deps`. Export a fresh
`E2E_CENSUS_RUN_ID` for each invocation, `E2E_CENSUS_RESULTS` outside Playwright
output cleanup, `E2E_VALUE_FREE=1`, the independently verified
`E2E_WALK_PRODUCT_SHA`, and the running image attestation in `E2E_WALK_IMAGES`.
Use the native value-free reporter first, without a JSON-only fallback.

Bind the product and candidate trees in a clean, committed Git composition;
a Git-less archive cannot attest source provenance.
Do not call the harness branch a public-dev product run: its product differs
from `783e3d3173ffef1dc3a4521300a998b2cb4d8f14`. Stock-profile absence checks are
not a substitute for the integrated LM-708 served/admitted manifest (HR-JRN-02),
and cannot qualify T-FULL/T-SHELL or enable a capability. Always legs need no
applicability reader. Integrated acceptance, unimplemented subcases and decisive
fallibility for each full leg remain owed until real evidence closes them.
Product failures go to launch triage, never to weakened assertions.

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

Two escape hatches, both environment variables read by `lib/quarantine.ts`:

```bash
E2E_INCLUDE_QUARANTINED=1 bin/e2e test   # run quarantined flows too (verifying a fix)
E2E_LIVE_LLM=1 bin/e2e test --grep @live-llm   # run flows that need a real provider
```

`@live-llm` flows are excluded from every ordinary run — the stack's LLM is the deterministic mock.
A flow tagged `@gw-profile-<name>` runs only when `E2E_GATEWAY_PROFILE` declares that gateway profile.
All three exclusions are produced by the single `grepInvertPattern()` function feeding the config's
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

After the Playwright suite, the same job runs the SDK live suites from
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
only if the PR has it when the run starts, so add it and then push: a re-run keeps the labels of
the run it repeats. The Standalone leg builds the four component images with the layer cache,
assembles the app image with `bin/e2e build standalone-app`, and boots with `E2E_STACK=standalone`.
The `upgrade` job in `.github/workflows/standalone-ci.yml` runs weekly, on demand, and on a pull
request labelled `upgrade-check` (the same rule: add the label, then push). It installs v1.41.1
with its own `./bin/install` and sends a trace. Then it re-runs `./bin/install` at the head commit
and checks four things: the install stays on the Distributed setup
(`COMPOSE_FILE=docker-compose.distributed.yml` in `.env`), its backend, worker, frontend and gateway
run the head commit's `:local` images, the first account still signs in, and the trace is still
there.

**Wall time in CI has not been measured yet** — the job has never run on a real PR. Record it on the
first run and put the number here; the hard timeout is 90 minutes and the boot budgets above are the
laptop-measured ones.

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

| Check | One-sided perturbation | Required failure evidence |
| --- | --- | --- |
| 1 | First API candidate expectation A → B; seed stays A | Expected minted B / received minted A at that assertion |
| 2 | Only first available-version UI expected array A → B | Exact locator and expected B / displayed A array diff; only this mutant expect uses 1s, below its unchanged 60s step |
| 3 | Second API candidate expectation B → A; atomic replacement stays B | Expected minted A / received minted B after replacement |
| 4 | Omit owned report deletion; keep `ownsStatus=true` | Unchanged unavailable API expectation rejects available snapshot B; original `finally` still cleans its own file |
| 5a | Only POST's expected status 405 → 200 | POST expected 200 / received 405 |
| 5b | Anonymous GET's expected status 401 → 200 | Expected 200 / received 401, after unchanged method checks |

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
