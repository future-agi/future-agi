# Experiment run control

**Prerequisite: start the barrier stand-in with the Docker recipe below before running `bin/e2e test`.**

`DATA-E2E-021` uses the real API, Postgres and Temporal worker. Only the external
LLM is a labelled **STANDIN**, with no upstream access and no product-state writes.
The test creates synthetic organizations using `scopeActors` and seeds datasets,
prompts, a dependent evaluator and the experiment through public endpoints.

## Managed stack recipe

Run on an isolated Linux Docker host, from the repository root. Build the current
backend and frontend with `bin/e2e build backend` and `bin/e2e build frontend`, then
start them with `FUTURE_AGI_VERSION=e2e-local FRONTEND_VERSION=e2e-local bin/e2e up`.
The frontend test image must compile with `VITE_EXPERIMENTS_NEW_PAGES=true` and
`exp.runControl` admitted; prototype mode must remain OFF. The capability is
registered in `madeReal.js`; the isolated image enables `experimentsBasic` only for
this proof. The product flag remains OFF until integrated qualification passes.

There is deliberately no new compose service, and `bin/e2e test` does not start
this provider. Run the Docker recipe below from the repository root before the flow
and attach the provider to the backend's existing network. Host port 28092 is
separate from managed mock-llm's 28082; mock-llm has no drain/finish barriers:

```bash
backend=$(bin/e2e compose ps -q backend)
project=$(docker inspect "$backend" --format '{{index .Config.Labels "com.docker.compose.project"}}')
# backend is on the project's default network and on e2e-public; use the default one.
network=$(docker network ls --filter "label=com.docker.compose.project=$project" \
  --filter "label=com.docker.compose.network=default" --format '{{.Name}}')
docker run -d --name run-control-standin \
  --network "$network" --network-alias run-control-standin \
  -p 127.0.0.1:28092:8080 --read-only --cap-drop ALL \
  --security-opt no-new-privileges \
  --mount "type=bind,src=$PWD/e2e/flows/datasets/run-control-standin.py,dst=/standin.py,readonly" \
  python:3.12-alpine python -I /standin.py
curl --fail http://localhost:28092/health
```

The API and worker call `http://run-control-standin:8080/v1`. The browser test uses
only `http://localhost:28092` to inspect counters and release the two barriers.
The first ten selected outputs complete, the next ten wait for `:drain`, and the
final four wait for `:finish`. Sibling calls never wait. The separate finish barrier
makes the persisted resumed `Running` state observable before completion. Each run
has 24 rows, so its 48 total judge calls fit below the default 50/minute quota;
no quota override or subscription mutation is needed.

```bash
(cd e2e && yarn typecheck && yarn catalog)
bin/e2e test flows/datasets/run-control.spec.ts --workers=1 --browser=chromium
bin/e2e test flows/datasets/run-control.spec.ts --workers=1 --browser=firefox
```

Do not use retries or intercept successful API responses. The flow opens the new
DSX experiment Data page, expands the Runs chip, then uses the selected run's own
menu: **Stop → Stop run**, and later **Resume · 4 left** on that same run. It checks
the chip's canonical run UUID against the selected variant's persisted
`NativeRunBinding` before acting: DSX sends canonical run IDs, while the
control read and cell queries use variant IDs. Each observed stop/resume
body must name exactly that one canonical run; the existing variant and
execution checks remain unchanged. It does not use the classic **Manage runs** dialog
or the header Stop, which targets all active runs. Running and Completed are read
from that run's **Show run details** popover; Stopped is on its chip. Completed
runs must offer neither Stop nor Resume, while their other menu actions remain.
Locator source citations are beside the helpers in the spec.

The flow observes actual UI requests, re-reads their public control endpoint and
checks Postgres identities, values and modification times. It releases its barriers
in `finally`; fixtures use unique markers, so failed attempts cannot satisfy a
later attempt's assertions. The HTML report includes seeded scope, experiment and
execution identities.

## Timing and negative controls

The main test retains its 960-second ceiling for Temporal polling, browser
readiness (60 seconds per action/assertion) and public-API seeding. The legacy
whole-stop test retains its 480-second ceiling. The browser's storage checks read
Postgres; separate CDC qualification is described below.
Runtime browser requests outside `localhost` are blocked. Both foreign-organization and authorized-other-workspace
requests must return 404 for reads, selected Stop and Resume.

Fallibility must be established against the isolated backend, then the unmodified
image restored: suppress a stop signal; change a completed result during resume;
repeat a provider call; and remove experiment workspace scoping. Each mutation
must fail the corresponding state, preservation, call-count or refusal assertion.
A catalog entry or typecheck alone is not evidence that the flow passed.

## Repeated-run and CDC qualification

Run ten independent attempts per browser, with retries still disabled:

```bash
bin/e2e test flows/datasets/run-control.spec.ts --grep 'stop one experiment run' --repeat-each=10 --workers=1 --browser=chromium
bin/e2e test flows/datasets/run-control.spec.ts --grep 'stop one experiment run' --repeat-each=10 --workers=1 --browser=firefox
```

Record the UTC start before those runs. The companion read-only check uses the
stack's internal services, not a separate pytest ClickHouse port. After both
browsers pass, run it with that start value:

```bash
backend=$(bin/e2e compose ps -q backend)
docker cp e2e/flows/datasets/run-control-cdc.py "$backend:/tmp/run-control-cdc.py"
docker exec "$backend" python /tmp/run-control-cdc.py --since "$START_UTC" --minimum 20
```

It compares the exact output, evaluation and reason cell identities, values,
statuses and timestamps with ClickHouse `FINAL`, joining through each snapshot
dataset's organization and workspace. It also checks that another synthetic
organization cannot match those cells and that source tenant-column names are
present in the CDC tables (including the Score schema). It performs no writes.
Execution bindings and experiment statuses are Postgres-only assertions: this
check does not invent CDC mirrors for them.

## Design boundary

Cooperative Resume continues the same open execution after the selected run's
**Stop → Stop run**. It does not resume executions cancelled by the legacy
whole-experiment Stop path. The second test still sends that path's empty-body
API request after the selected run is paused and the experiment reads Cancelled.
At that point DSX offers no whole-experiment Stop: its header requires active runs
or a Running/Queued experiment with none (apart from a run-control-off
legacy-stop retry, which does not apply here), per
`frontend/src/sections/experiment-detail/ExperimentRow.jsx:425-427,1133`.
The empty-body API path cancels workflows rather than pausing them; the test keeps
its cancellation result, subsequent Resume refusal and sibling-preservation checks.
