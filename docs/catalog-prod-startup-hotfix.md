# Catalog production startup hotfix

## Scope

Fix the two failing containers in the existing Kafka catalog pipeline. This is
not the managed-lifecycle redesign or the polling-based suggestion catalog.
No new catalog schema, periodic source scan, epoch migration, or source-span
write is part of this patch. Keep existing workspace/project authorization,
lease/digest validation, Kafka delivery, and activation checks.

Base: main `2d244e6b0566f265337c42ea50299dc5f0dbf96e`.
No production rollout is authorized here. This branch is independent of the
managed-lifecycle draft PR #2589.

## Read-only production evidence, 2026-09-07

Cluster: `gke_futureagiprimary_us-east5_futureagi-us-production`.
Namespace: `futureagi`.

- `fi-property-catalog-consumer-74c46c7cd9-bbpnz`: Ready, zero restarts at
  inspection. Readiness alone is not end-to-end delivery evidence.
- `fi-property-catalog-sequencer-0`, sequencer container: 762 restarts;
  last exit 1 at 12:35:08 UTC. Error: `producer retirement 0: retirement
  lifecycle mode differs from its build plan`.
- Its existing retirement file contains 3,284 records with the same contract:
  initial_backfill, epoch 1, revision 3, projection 3; ten streams labeled
  physical_snapshot_r3 with one consistent non-zero generation. These are
  observed values, not constants to add to the compatibility check.
- Lifecycle-controller container: 128 restarts; the prior process exited 0
  following shutdown. Kubernetes reports repeated failed startup probes.
- All three controller probes currently require `healthy=true`. Startup permits
  181 failures at 10-second intervals. The v1 health file is written after a
  full cycle with the cycle-start timestamp, making large cycles appear stale.
- The inspected health record was unhealthy, with 3,256 workspace failures
  and zero processed; errors say an expired incomplete revision requires repair.
- The deployed bootstrap and expired-incomplete-repair gates are both false.

## Minimal changes

1. Accept the validated revision-scoped physical-snapshot retirement format.
   Do not bypass plan identity, inventory, lineage, or digest validation, and
   do not hardcode a production epoch, revision, projection, or workspace.
2. Report controller liveness separately from catalog readiness. Publish a
   heartbeat during bounded work; an expired operation deadline must fail
   liveness even if the heartbeat thread still runs. Workspace failures must
   continue to fail readiness.
3. Update only the matching startup/liveness probes in the deployment chart;
   keep readiness strict and validate health-file format, timestamps and fields.

## Release gates and follow-up

- PASS: reproduced physical-snapshot rejection before the fix; package and
  sequencer-command Go tests pass, including invalid-proof rejection.
- PASS: patched loader validated all 3,284 retirement proofs from a read-only
  copy of the production file. The local copy was unchanged. Proof bytes and
  the one-off replay harness are not included in the commit. This does not
  qualify the remaining startup fence/checkpoint reads or actual Kafka delivery.
- PASS: 13 focused controller-health tests and 42 existing production-lifecycle
  tests (actual Django test settings, mocked runtime boundaries, no services).
- PASS: nine rendered Helm probe/projection tests, existing data-Kafka chart
  regressions and strict Helm lint. Missing, malformed, stale and future-dated
  health records still fail; v1 retains its existing strict behavior.
- PASS: Python lint/format checks and whitespace checks.
- Build and verify the collector and controller-sidecar images, then pin their
  actual digests. Image builds and deployment have not happened for this patch.
- Review the rendered workload diff before rollout. Source spans, database
  grants, Kafka offsets and catalog data must remain unchanged.
- The existing chart enforces a shared collector/sequencer/consumer image and
  matching lifecycle-sidecar/web-backend digests. This patch does not change
  those constraints or any pins. A normal Helm rollout with new pins therefore
  includes those workloads; do not claim a sidecar-only rollout is supported.
- Expired incomplete revisions are a separate operational blocker. A reviewed,
  bounded use of the existing repair path is required after validating the
  pending/active state and writer ownership. Do not enable fleet-wide repair,
  discard spool files, relabel rows, or delete data as a startup workaround.
- Passing these local tests proves the narrow patch, not production catalog
  freshness. Verify actual consumer progress and scoped property/value APIs
  after the approved rollout and repair.
