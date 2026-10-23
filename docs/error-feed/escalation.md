# Deterministic issue escalation (TH-8613)

Issues start For review. Automatic escalation compares findings in a settled
one-hour window with the preceding 24 hours' hourly average. Defaults require
at least 20 findings, three times the baseline, and 10 additional findings,
confirmed across 15 minutes. Recovery requires the count to stay below the
configured recovery limit for 10 minutes. The incident baseline stays frozen
until recovery. Checks run every five minutes with a ten-minute processing
allowance; a quiet issue therefore recovers without another finding arriving.

Workspace administrators can edit eight thresholds from Error Feed's settings
button. Selecting any issue status manually protects that choice from automatic
updates, including selecting its current status. Workspace policy revisions,
issue status revisions and membership revisions fence concurrent updates.

## Schema and rollout

Migration 0117 adds one workspace policy table, issue ownership/evidence fields,
and finding occurrence timestamps. Migration 0118 resets every live legacy Error
Feed issue with status Escalating to For review and system ownership, in bounded
batches. This intentionally includes past user-selected Escalating values because
the old schema cannot distinguish them from the creation default. Other legacy
statuses, deleted issues, and explicit manual choices recorded by the new code
are preserved. Rerunning the migration does not reset already adopted issues.
No AuditLog or source-configuration/task schema changes are included.

The migration does not replay historical growth or immediately re-escalate issues.
It schedules runtime checks, which require fresh confirmation before escalation
and honor the rollout controls below. Missing source timestamps block automatic
transitions; runtime retries recover original scanner/eval completion times in
bounded batches. Unavailable timestamps are never replaced with migration time.
Omega completion times available in PostgreSQL are recovered during migration.
For proactive preparation or retrying unavailable evidence, run
`python manage.py prepare_feed_escalation_evidence --help` for selected workspaces.

Automation defaults to `ERROR_FEED_ESCALATION_MODE=shadow` and scanner sources.
After checking shadow results, select workspace UUIDs in
`ERROR_FEED_ESCALATION_WORKSPACE_IDS`, qualified sources in
`ERROR_FEED_ESCALATION_SOURCES`, and set the mode to `enabled`. `disabled` stops
checks. These deployment controls do not replace the workspace's UI thresholds.

## Reproduce the focused checks

From `futureagi/`, use `bin/test` with the repository's isolated test services:

```sh
bin/test tracer/tests/test_feed_escalation_*.py \
  tracer/tests/test_cluster_severity_status.py \
  tracer/tests/test_feed_f6_memberships.py tracer/tests/test_feed_alerts.py
```

From `frontend/`, run the escalation-settings API test and the three Error Feed
component tests with Vitest, then `yarn lint` and `yarn contracts:check`.
From `e2e/`, run `yarn typecheck`, `yarn catalog:check`, and
`yarn test harness/error-feed-profile.spec.ts`.

## Managed E2E profile

The eight `ESC-E2E-*` flows need the dedicated entitled local profile; they reject
shared development/remote backends. Generate a short-lived development license
outside the checkout (the script refuses official bundled licensing roots):

```sh
python e2e/scripts/create-error-feed-license.py --output /tmp/error-feed-license.env
export TH8613_LICENSE_ENV=/tmp/error-feed-license.env
export E2E_STACK=error-feed
```

Build images from this checkout before starting the profile. The entitled backend
uses `futureagi/Dockerfile`, because the ordinary `bin/e2e build` uses the OSS
Dockerfile. Registry access and the backend base image are required.

```sh
docker build -f futureagi/Dockerfile -t futureagi/future-agi:th8613-local futureagi
docker build --build-arg VITE_HOST_API=http://localhost:28113 \
  -t futureagi/frontend:th8613-local frontend
docker build -t futureagi/fi-collector:th8613-local fi-collector
docker build -t futureagi/agentcc-gateway:error-feed-e2e agentcc-gateway
export AGENTCC_GATEWAY_VERSION=error-feed-e2e
bin/e2e up
bin/e2e test flows/error-feed --grep ESC-E2E --workers=1
bin/e2e down
```

The profile uses backend port 28113 and collector HTTP port 34320 to avoid the
manual demo stack. Default bind mounts point directly at this checkout. On a VM
that shares only a temporary staging directory, set `TH8613_BIND_ROOT` and provide
`th8613-worktree.json` there with `profile: "th8613"` and the absolute
`source_root`; the driver verifies the checkout marker and production module
digests before publishing fixtures. Do not point this profile at a shared DB.

## Validation evidence

Use the PR description and CI jobs for results tied to a particular commit.
The entitled profile runs the eight `ESC-E2E-*` flows; the ordinary OSS profile
excludes them because it does not provide Error Feed entitlement. The source
qualification harness separately checks collector publication, real Temporal
evaluation and session trace breadth.

The profile uses production Django settings. Account setup has a separate
six-minute fixture budget because it runs before a flow can extend its own
timeout; each account-creation command remains bounded to two minutes.
