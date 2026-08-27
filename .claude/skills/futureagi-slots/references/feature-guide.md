# FutureAGI development slots: feature guide

This guide is the developer-facing reference for running parallel FutureAGI
worktrees. The root `Makefile` is the only supported entry and exit point; do
not invoke files under `slots/compose/` directly.

## What a slot is

A slot is an isolated development identity numbered from 1 through 20. Each
slot combines:

- a frontend built from and bind-mounted to its worktree;
- a complete provider map, with each provider either shared or private;
- browser routes based on the slot number;
- slot-specific generated configuration and lifecycle metadata;
- logical state identity, and optionally physically isolated infrastructure.

`SLOT=auto` selects the first unused slot. It is a selector only: `auto` never
appears in a hostname or container name.

The normal topology is intentionally asymmetric:

```text
shared control plane (one per machine)
  PostgreSQL, ClickHouse, Redis, RabbitMQ, MinIO, Temporal, Temporal UI, Traefik
             |
shared default providers (one pool, reused by slots)
  backend/worker, simulation, gateway, collector, serving, executor,
  PeerDB group, Jaeger
             |
slot 01      +-- private frontend
slot 02      +-- private frontend + selected private providers
slot 03      +-- private frontend + private backend/state closure
```

The first slot starts the control plane and shared default provider pool, so it
creates many containers even when `SERVICES=none`. Later frontend-only slots
normally add just one frontend container and reuse that pool.

## Feature summary

| Capability | Behaviour |
|---|---|
| Parallel worktrees | Up to 20 registered slots can run at once. |
| Always-fresh UI | Every slot always owns a frontend from its current worktree. |
| Selective isolation | `SERVICES` makes only affected provider groups private. |
| Safe sharing | Omitted providers use a validated shared default, never silently changed worktree code. |
| Stateful isolation | Logical state is namespaced; selected engines can also receive private containers and volumes. |
| Automatic routing | Traefik exposes predictable `*.N.localhost` browser URLs. |
| Resource protection | Startup estimates memory and rejects unsafe topologies before creating them. |
| Persistent cleanup | Normal down preserves named volumes and generated slot state. |
| Recovery | Interrupted startup and replacement can be cleaned without deleting volumes. |
| Agent support | Claude Code and Codex share this canonical workflow through repository skills. |

## Choose the private services

`SERVICES` controls additional private provider groups. The frontend is always
private and must not be included in `SERVICES`.

| Value | Use it when the worktree changes | Private estimate |
|---|---|---:|
| `none` | frontend only | 0 MiB beyond frontend |
| `backend` | Django/API, backend dependencies, or shared state contract | 1,300 MiB |
| `simulation` | simulation runner | 700 MiB |
| `gateway` | agent control gateway | 350 MiB |
| `collector` | telemetry collector | 350 MiB |
| `serving` | model serving | 600 MiB |
| `executor` | code executor | 600 MiB |
| `peerdb` | PeerDB integration and UI | 700 MiB |
| `observability` | tracing/Jaeger | 500 MiB |
| `all` | cross-cutting or uncertain changes | every provider group |

Multiple groups use commas, for example
`SERVICES=gateway,serving`. Dependencies are expanded automatically.

A private backend also makes `simulation`, `collector`, and `peerdb` private.
Those groups are state-coupled and must consume the same state identity. This
means `SERVICES=backend` is deliberately larger than a backend container plus
worker.

Before using a shared default, `slot-up` compares the provider's relevant paths
against `${BASE_REF:-origin/dev}` and checks staged, unstaged, and relevant
untracked files. Changed source cannot accidentally seed or replace a shared
default. Select that provider privately or choose the correct `BASE_REF`.

### Source update behaviour

| Provider | Development update model |
|---|---|
| Frontend | Vite source is bind-mounted and reloads during development. |
| Backend | Django source is bind-mounted and reloads during development. |
| Simulation | Backend source is bind-mounted; restart the worker after dependency changes. |
| Gateway, collector, serving, executor | Image-based; rerun `slot-up` to rebuild and recreate. |

Source-backed images are built sequentially. BuildKit still reuses its cache,
while sequential builds avoid a large transient memory spike.

Creating a new simulation provider requires
`agent_learning_kit-0.1.0-py3-none-any.whl` at the worktree root. Validation
happens before Docker is mutated.

## Worktree environment

The root `.env` in each worktree is the single application-environment source
for its slot. It may contain frontend and backend variables; for example:

```dotenv
VITE_EXPERIMENTAL_VIEW=true
OPENAI_API_KEY=local-development-key
NEW_BACKEND_SETTING=true
```

During `slot-up`, the runtime copies the worktree `.env` into the private,
mode-`0600` generated `.slots/slots/NN/slot.env` and appends the generated slot
topology. Generated container names, ports, database identities, and routing
values therefore win when a key is duplicated. If the worktree has no `.env`,
the Compose defaults and generated topology still work.

Never edit the generated `slot.env`. Edit the root `.env` in the relevant
worktree and rerun `make slot-up` for that slot. Worktree `.env` files are
ignored by Git; update the committed `.env.example` when introducing a setting
other developers need. Only variables prefixed with `VITE_` are exposed by Vite
to browser code, so secrets must never use that prefix.

The generated file contains the inherited application values and can therefore
contain secrets. It is protected with mode `0600` and persists across
`slot-down` together with the other generated slot state. The explicitly
confirmed `slot-purge` operation removes it.

The frontend is always private and automatically receives its worktree's
environment. A shared provider has only one process environment and inherits
the `.env` of its current owner worktree. If a worktree changes an environment
setting that affects the backend or another provider, select that provider
privately with `SERVICES`; for example, use `SERVICES=backend` for a backend
setting. Shared-provider ownership handoff regenerates its environment from the
new owner before recreating the provider.

## Isolate stateful infrastructure

By default, state engines share physical containers but use a logical state
identity. A shared backend uses the reserved slot-0 identity; a private backend
uses its slot's identity.

| Engine | Logical separation |
|---|---|
| PostgreSQL | database |
| ClickHouse | database |
| Redis | three database numbers: application, cache, lock |
| RabbitMQ | vhost |
| MinIO | bucket |
| Temporal | namespace |

For physical isolation, pass one or more engines:

```bash
ISOLATE_INFRA=postgres,clickhouse,redis,rabbitmq,minio,temporal
```

Physical isolation requires `SERVICES=backend`, because infrastructure and the
private provider state identity must move together. Each selected engine gets
slot-private containers and named volumes.

| Engine | Estimated memory |
|---|---:|
| PostgreSQL | 700 MiB |
| ClickHouse | 1,000 MiB |
| Redis | 200 MiB |
| RabbitMQ | 500 MiB |
| MinIO | 500 MiB |
| Temporal | 900 MiB |

Isolated engine ports are bound to loopback in the slot's numeric band. The
base is `20000 + SLOT * 100`; offsets are PostgreSQL `+1`, ClickHouse HTTP
`+2`, ClickHouse native `+3`, Redis `+4`, RabbitMQ `+5`, RabbitMQ management
`+6`, MinIO API `+7`, MinIO console `+8`, and Temporal `+9`.

## Command reference

Inspect the interface without contacting Docker:

```bash
make slot-help
```

Live commands require the developer's explicit approval for local Docker work
in the current conversation and the `SLOTS_RUNTIME_APPROVED=1` guard.

| Task | Command |
|---|---|
| Start first free slot | `SLOTS_RUNTIME_APPROVED=1 make slot-up SLOT=auto SERVICES=none` |
| Start a chosen composition | `SLOTS_RUNTIME_APPROVED=1 make slot-up SLOT=3 SERVICES=gateway,serving` |
| Isolate physical state | `SLOTS_RUNTIME_APPROVED=1 make slot-up SLOT=3 SERVICES=backend ISOLATE_INFRA=postgres,redis` |
| List all registered slots | `make slot-status` |
| Inspect one slot | `make slot-status SLOT=3` |
| Print browser routes | `make slot-urls SLOT=3` |
| Follow service logs | `SLOTS_RUNTIME_APPROVED=1 make slot-logs SLOT=3 SERVICE=backend` |
| Open a service shell | `SLOTS_RUNTIME_APPROVED=1 make slot-shell SLOT=3 SERVICE=backend` |
| Run a command | `SLOTS_RUNTIME_APPROVED=1 make slot-run SLOT=3 SERVICE=backend COMMAND='python manage.py check'` |
| Stop and preserve state | `SLOTS_RUNTIME_APPROVED=1 make slot-down SLOT=3` |
| Inspect runtime health | `SLOTS_RUNTIME_APPROVED=1 make slots-doctor` |
| Recover an interrupted lifecycle command | `SLOTS_RUNTIME_APPROVED=1 make slots-recover` |
| Prune stale provider metadata | `SLOTS_RUNTIME_APPROVED=1 make slots-prune` |
| Permanently purge slot-owned state | `SLOTS_RUNTIME_APPROVED=1 make slot-purge SLOT=3 CONFIRM=3` |

`SERVICE` defaults to `frontend` for logs and shell commands. Supply it
explicitly when inspecting another provider.

### Common compositions

Frontend-only worktree, reusing all default providers:

```bash
SLOTS_RUNTIME_APPROVED=1 make slot-up SLOT=auto SERVICES=none
```

API/state-contract work, with its required private closure:

```bash
SLOTS_RUNTIME_APPROVED=1 make slot-up SLOT=4 SERVICES=backend
```

Cross-cutting work with fully private providers but shared physical databases:

```bash
SLOTS_RUNTIME_APPROVED=1 make slot-up SLOT=5 SERVICES=all
```

Temporal schedule registration is disabled by default to prevent multiple
workers registering the same namespace-wide schedules. For schedule
development, opt in on startup with `SLOT_REGISTER_TEMPORAL_SCHEDULES=true`.

## Browser routing

No hosts-file maintenance is required for normal `*.localhost` resolution.
Traefik uses the generated slot route to dispatch each hostname.

| UI or service | Slot-N URL |
|---|---|
| Frontend | `http://N.localhost` |
| Backend API | `http://api.N.localhost` |
| Temporal UI | `http://temporal.N.localhost` |
| PeerDB UI | `http://peerdb.N.localhost` |
| MinIO API | `http://minio.N.localhost` |
| MinIO console | `http://minio-console.N.localhost` |
| RabbitMQ management | `http://rabbitmq.N.localhost` |
| Gateway | `http://gateway.N.localhost` |
| Model serving | `http://serving.N.localhost` |
| Collector | `http://collector.N.localhost` |
| Executor | `http://executor.N.localhost` |
| Jaeger | `http://jaeger.N.localhost` |

If port 80 is unavailable, set `SLOTS_HTTP_PORT`, for example
`SLOTS_HTTP_PORT=8088`. All concurrently active slots must use the same public
port, and URLs become `http://N.localhost:8088` and so on. Always use
`make slot-urls SLOT=N` instead of reconstructing URLs manually.

## What happens during `slot-up`

1. Acquire the registry lock and resolve `SLOT=auto` or the requested number.
2. Validate service selection, base-reference compatibility, prerequisites,
   ports, and projected memory before starting the topology.
3. Stage a mode-`0600` slot environment file (worktree `.env` followed by
   generated topology), manifest, and Traefik route under `.slots/`.
4. For same-slot replacement, stop the old private topology while retaining
   its volumes and committed recovery information.
5. Create the external `futureagi-slots` network and start the shared control
   plane if needed.
6. Provision logical databases, vhosts, buckets, and Temporal namespace.
7. Create missing shared default providers, building source-backed providers
   sequentially.
8. Build selected private providers and the always-private frontend
   sequentially.
9. Restart only Traefik so it reloads the new route, then start the private slot
   Compose project.
10. Commit the registry entry and remove the recovery journal.

The backend container runs migrations automatically during normal startup.
The backend worker uses fast startup and does not repeat migrations.

The PeerDB provider is a group rather than a single long-running container. It
can include its catalog, Temporal and initialization job, MinIO and setup job,
flow API and worker, server, UI, and final initialization job. One-shot helper
containers may remain visible in Docker after successfully completing.

## Resource admission

Before startup, the slot system reads Docker's available memory and only lowers
its configured cap; it never raises Docker Desktop's allocation. The default
configured cap is 16 GiB, and startup admits at most 75% of the effective cap.

The estimate includes:

- 2,600 MiB when the first slot must start the shared control plane;
- the missing shared provider pool on its first use;
- 350 MiB for every private frontend;
- selected private provider estimates;
- selected isolated-infrastructure estimates.

If the projected topology exceeds the limit, startup fails before committing
the slot. `FORCE=1` bypasses this guard and must never be used without explicit
authorization for that risk.

## Stop, replace, recover, and purge

`slot-down` is the normal exit. It stops the slot's private project and isolated
engines, releases provider references, and stops shared providers that lose
their final reference. When the last slot stops, the control plane and shared
network also stop. It deliberately does not pass Compose `--volumes`.

Generated state and named volumes persist across ordinary cleanup, allowing a
slot to be started again without losing its data. Starting an already-used slot
performs a controlled replacement of that slot.

If startup is interrupted, `slots-recover` removes only the partial project
recorded in the recovery journal and retains volumes. It covers both a failed
first startup and a failed same-slot replacement; rerun `slot-up` afterward.
Recovery also understands interrupted down and purge operations. A result
ending in `-retry` means the registry did not commit, so rerun the original
Make command. A result ending in `-complete` means only post-commit artifact
cleanup remained. Purge is never resumed automatically: it requires renewed
explicit authorization and the exact `CONFIRM=<slot>` value.

`slots-prune` repairs stale, zero-reference shared-provider metadata left by an
interrupted or older implementation. It is not required during normal down.

`slot-purge` is destructive. `CONFIRM` must exactly equal `SLOT`; it removes
exact slot-owned volumes and private-provider logical state, while preserving
shared logical state and shared volumes. Restate the target and obtain explicit
authorization immediately before invoking it.

Never stop, delete, or reconfigure Docker resources outside the FutureAGI slot
projects.

## Generated files and sources of truth

Slot metadata is kept under `.slots/` and is intentionally separate from
worktree source. The main implementation references are:

- `Makefile`: public command interface;
- `slots/catalog.py`: service names, dependencies, memory estimates, and port
  bands;
- `slots/runtime.py`: lifecycle planning, routing, admission, and sharing;
- `slots/config/compose-catalog.yaml`: topology and routing contract;
- `slots/compose/`: internal Compose fragments, never a public entry point;
- `slots/README.md`: repository-level overview;
- `.claude/skills/futureagi-slots/SKILL.md`: canonical agent operating policy;
- `.agents/skills/futureagi-slots/SKILL.md`: Codex adapter to the canonical
  skill.

## Current command-output limitation

The lifecycle operations work, but the current Python adapter captures child
process output. Consequently `slot-logs` may buffer followed logs,
`slot-shell` is not a fully interactive terminal, and `slot-run` can hide the
command's standard output behind its JSON result. Treat these as CLI-output
limitations, not container-health failures; `slot-status`, `slot-urls`, and the
browser routes remain reliable inspection paths.
