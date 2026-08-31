# FutureAGI development slots

The slot system runs multiple worktree development environments through one
root Make interface. Every slot receives a private frontend; other providers
can be shared or made private per slot.

For the complete topology, feature, command, routing, persistence, and recovery
reference used by both Claude Code and Codex, see the
[development slots feature guide](../.claude/skills/futureagi-slots/references/feature-guide.md).

Do not run the Compose assets in this directory directly. Inspect the interface
without contacting Docker with:

```bash
make slot-help
```

Live targets are deliberately locked. After the developer explicitly approves
local runtime testing, invoke them with `SLOTS_RUNTIME_APPROVED=1`.

Common examples:

```bash
# Frontend-only worktree; reuse the default backend and infrastructure.
SLOTS_RUNTIME_APPROVED=1 make slot-up SLOT=3 SERVICES=none

# Backend change; create a private backend and consolidated worker.
# Simulation, collector, and PeerDB also become private because they consume
# the backend provider's state identity.
SLOTS_RUNTIME_APPROVED=1 make slot-up SLOT=3 SERVICES=backend

# Physically isolate selected state engines as well.
SLOTS_RUNTIME_APPROVED=1 make slot-up SLOT=3 SERVICES=backend ISOLATE_INFRA=postgres,redis

make slot-status SLOT=3
make slot-urls SLOT=3
SLOTS_RUNTIME_APPROVED=1 make slot-down SLOT=3
```

Ordinary cleanup preserves logical data and named volumes. Persistent state is
deleted only by the explicitly confirmed `slot-purge` target.

The first slot creates one shared default provider pool. Later slots reuse that
pool and normally add only their private frontend. `SERVICES` replaces selected
groups with worktree-private providers; changed worktree source never becomes a
shared default implicitly.

## Worktree environment

Each slot inherits application configuration from the root `.env` of its own
worktree. The slot runtime copies that file into its private generated
`.slots/slots/NN/slot.env`, then appends generated container, routing, port, and
state values. Generated values take precedence when the same key appears in
both places. Do not edit `slot.env`; edit the worktree `.env` and rerun
`make slot-up` for that slot.

Because the generated file includes the worktree's application values, it may
contain secrets. It is written with mode `0600`, remains after `slot-down`, and
is removed with the rest of the generated slot metadata by `slot-purge`.

The root `.env` can contain frontend and backend values. Only `VITE_`-prefixed
values are exposed to browser code. Because a shared provider has one process
environment, a worktree-specific backend or provider setting requires selecting
that provider with `SERVICES`; the always-private frontend receives its
worktree's values automatically. Shared providers inherit the `.env` belonging
to their current owner worktree.

Source-backed images are rebuilt sequentially before containers start. This
keeps repeated builds incremental while avoiding the memory spike of a parallel
Compose build. A topology that creates the simulation provider requires
`agent_learning_kit-0.1.0-py3-none-any.whl` at the worktree root, as required by
`Dockerfile.simulation-runner.dev`; `slot-up` checks this before changing Docker.

Backend slots do not re-register namespace-wide Temporal schedules by default.
For schedule-development work, opt in with
`SLOT_REGISTER_TEMPORAL_SCHEDULES=true` on the Make invocation.

`slot-down` stops slot-private containers, isolated engines, and providers that
lost their last reference while retaining named volumes and generated state.
The confirmed `slot-purge` target also works after `slot-down`: it removes exact
slot-owned Compose volumes and, for a private backend, its provider-bound state.
It never deletes shared logical state.

If Docker or the invoking process stops during a lifecycle command,
`make slots-recover` reconciles its journal without deleting named volumes. It
removes partial first-start and same-slot replacement runtime; rerun `slot-up`
afterward. For interrupted down or purge, a result ending in `-retry` means the
original Make command must be run again. Purge always requires renewed explicit
authorization and its exact `CONFIRM=<slot>` value.

## Browser URLs

For slot 3 the default routes are:

- `http://3.localhost`
- `http://api.3.localhost`
- `http://temporal.3.localhost`
- `http://peerdb.3.localhost`

Set `SLOTS_HTTP_PORT` when port 80 is unavailable. All active slots must use the
same public port. The status and URL targets print the resolved routes and any
slot-banded isolated-engine ports.

## Agent skills

Claude Code discovers the canonical workflow at
`.claude/skills/futureagi-slots/SKILL.md`. Codex discovers the adapter at
`.agents/skills/futureagi-slots/SKILL.md`; the adapter points to the canonical
instructions so behavior stays aligned.

Both skills require explicit approval before they run live Docker slot tests or
mutate local Docker runtime state.
