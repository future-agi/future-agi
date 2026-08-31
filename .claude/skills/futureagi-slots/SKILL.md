---
name: futureagi-slots
description: Explain, select, and operate FutureAGI worktree development slots through the repository Make interface. Use for developer onboarding and feature overviews, or when starting, inspecting, testing, logging, entering, stopping, or purging a parallel local stack.
---

# FutureAGI development slots

Use the root `Makefile` as the only slot lifecycle interface. Never invoke the
slot Compose files directly.

## Explain and onboard

When a developer asks what the slot system supports, how containers and shared
state work, which command to use, or requests onboarding documentation, read
[`references/feature-guide.md`](references/feature-guide.md). For a complete
overview, present the guide in its existing order; for a focused question,
include only the relevant sections.

The guide describes capabilities, but it does not grant runtime approval. Use
`make slot-status` and `make slot-urls` when current registered state is needed;
do not contact Docker merely to explain the system.

## Runtime approval gate

Before running any target that contacts Docker, starts or stops containers,
creates networks or volumes, binds ports, provisions live state, or runs a
runtime smoke test, confirm that the user has explicitly approved local Docker
testing in the current conversation. Planning approval or permission to edit
code is not runtime approval.

Without runtime approval, limit work to `make slot-help`, Python unit tests,
static configuration inspection, and Compose rendering that does not contact
the Docker daemon.

## Choose a slot composition

Every slot always gets a private frontend from the current worktree. Do not add
`frontend` to `SERVICES`.

Determine additional private provider groups by inspecting:

1. The merge-base diff against `${BASE_REF:-origin/dev}`.
2. Staged and unstaged changes.
3. Relevant untracked files.

Use the service map printed by `make slot-help`. Select every affected group;
when uncertain, include the group rather than allowing incompatible code to use
a shared default. `SERVICES=none` is valid for a frontend-only change.

Supported provider groups are `backend`, `simulation`, `gateway`, `collector`,
`serving`, `executor`, `peerdb`, and `observability`; `all` selects every group.
Selecting a private backend also makes simulation, collector, and PeerDB
private because those providers must consume the same state identity.

When a topology must create a simulation provider, the worktree root must
contain `agent_learning_kit-0.1.0-py3-none-any.whl`, the local input required by
`Dockerfile.simulation-runner.dev`. `slot-up` validates this before mutating
Docker. If it is absent, report the exact prerequisite; do not bypass the check
or fabricate a package.

## Start and verify

After runtime approval:

1. Show the chosen slot, groups, isolated engines, and reasoning.
2. Prefer `SLOT=auto` unless the user chose a slot.
3. For the approved live operation only, prefix Make with
   `SLOTS_RUNTIME_APPROVED=1`; for example,
   `SLOTS_RUNTIME_APPROVED=1 make slot-up SLOT=<slot> SERVICES=<groups>` with
   optional `ISOLATE_INFRA=<engines>`.
4. Run `make slot-status SLOT=<slot>` and `make slot-urls SLOT=<slot>`.
5. Report provider reuse and health failures precisely.

Do not add `FORCE=1`, environment-mismatch overrides, or destructive flags
unless the user explicitly authorizes the corresponding risk.

## Operate and clean up

- Status: `make slot-status [SLOT=<slot>]`
- URLs: `make slot-urls SLOT=<slot>`
- Logs: `SLOTS_RUNTIME_APPROVED=1 make slot-logs SLOT=<slot> SERVICE=<service>`
- Shell: `SLOTS_RUNTIME_APPROVED=1 make slot-shell SLOT=<slot> SERVICE=<service>`
- Command: `SLOTS_RUNTIME_APPROVED=1 make slot-run SLOT=<slot> SERVICE=<service> COMMAND='<command>'`
- Stop while preserving data: `SLOTS_RUNTIME_APPROVED=1 make slot-down SLOT=<slot>`
- Diagnose: `SLOTS_RUNTIME_APPROVED=1 make slots-doctor`
- Recover an interrupted lifecycle operation without deleting volumes:
  `SLOTS_RUNTIME_APPROVED=1 make slots-recover`
- For startup and same-slot replacement, recovery removes the partial runtime
  while preserving volumes; rerun `slot-up` afterward. For interrupted down or
  purge, inspect whether the result ends in `-retry` or `-complete`. A retry
  requires the original Make command again; purge must cross its explicit
  authorization and `CONFIRM=<slot>` gates again.
- Remove stale runtime providers: `SLOTS_RUNTIME_APPROVED=1 make slots-prune`

`SLOTS_RUNTIME_APPROVED=1 make slot-purge SLOT=<slot> CONFIRM=<slot>` deletes
exact slot-owned volumes and private provider state; it preserves shared
logical state. Treat it as destructive: restate the exact target and obtain
explicit user authorization immediately before running it.

Never stop, remove, or reconfigure unrelated local Docker resources.
