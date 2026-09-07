# Managed property catalog release checklist

Status: draft release candidate, **not release-ready**. The user requested a
draft hotfix PR against `main` for review while validation continues. This checklist does not authorize a push,
image publication, schema/grant change, or deployment.

## Scope and invariants

- Automatic installation identity, workspace discovery, allocation and recovery;
  no operator-maintained epoch or catalog revision. Existing collector, Kafka,
  sequencer, consumer and controller remain the operating pipeline.
- Historical backfill and live/current/late data use the same managed contract
  across eligible workspaces. The configured default window is 366 days, not a
  hard coverage cap: INITIAL/FULL_REPAIR can widen the lower bound using
  `retained_since`. This is not an all-history qualification claim.
- Resume exact persisted identity, build plan, source binding, journals and
  checkpoints. Do not reset, relabel or reconstruct missing proof from guesses.
  Uncertain native writes require proven resolution before replacement/replay.
- Builds and independent audits use the same isolated captured source parts.
  Version/tombstone evidence is preserved; live change detection still reads
  canonical spans. Capture reservations and cleanup remain bounded and durable.
- Product operations do not mutate canonical source tables. DDL, ATTACH and
  cleanup target only the exact installation-owned derived capture namespace.
- Automatic FOLLOW recovery requires exact terminal invalidation proof and a
  qualified replacement. Explicit DISABLE/ROLLBACK and pending manual controls
  remain authoritative; no publication-marker bypass substitutes for proof.

## Deployment prerequisites

- [ ] The lifecycle writer requires the exact read-only replica-health grant
  used during managed identity initialization:
  `SELECT(database, table, is_readonly, is_session_expired, queue_size, active_replicas, total_replicas) ON system.replicas`.
  The admission validator accepts only this complete column set for that user,
  without delegation; broader metadata access is not a substitute. Review/apply
  separately to the actual production principal; no production grant was run.
- [ ] Build and qualify immutable **backend and collector** release images from
  the final reviewed commit; update chart pins together. Keep the spool-owner
  init image digest-pinned. Existing image pins do not prove new code is present.
- [ ] Coordinate the included frontend hook update for the new bootstrap-pending
  API response: a newly onboarding workspace must poll and show loading rather
  than treat its not-yet-active vocabulary as a complete empty result.
- [ ] Provision the exact `<catalog_database>_source_capture` namespace on the
  admitted source server. The lifecycle writer needs source `spans` SELECT and
  derived-only SELECT, INSERT, CREATE TABLE, ALTER DELETE, ALTER TTL and DROP TABLE.
  Source and ledger principals remain read-only; do not grant source mutation.
- [ ] Apply the matching bounded source metadata grants, including required
  `system.parts`, `system.parts_columns`, `system.disks`, storage-policy and
  replica identity fields. Validate actual credentials and same-server admission;
  do not silently route credentials to an unsupported separate cluster.
  Exact OSS bindings/grants: `futureagi/scripts/property_catalog_oss/bootstrap_clickhouse.sh`.
- [ ] Upgrade activation-control actions additively: preserve existing values
  1/2/3 and add `FOLLOW=4`. CREATE-only installation does not upgrade an existing
  enum. Review any pinned schema/admission descriptor transition explicitly.
- [ ] Verify chart Secret references for writer/source/ledger proof credentials,
  source/catalog routes, Kafka topics/group/checkpoint configuration and network
  access. All three singleton containers must share the retained RWO spool and
  exact fence path, with compatible UID/GID and unchanged permission guards.

## Reviewed owner handover, not a one-step Helm upgrade

- [ ] Preserve the PVC, journals, checkpoints and installed identity; establish
  a recovery plan without deleting state or fabricating replacement descriptors.
- [ ] Stop new admissions and fence/drain old controller, sequencer and consumer
  ownership. Scale the old separate consumer Deployment to zero and verify no
  old writer remains; prevent its recreation during the chart transition.
- [ ] Only after zero old owners, transfer the retained spool to the singleton
  StatefulSet containing sequencer, controller and consumer. Keep `OnDelete`:
  rendering/upgrading alone does not replace existing pods. Explicitly coordinate
  pod replacement and controller startup; never overlap old and new owners.
- [ ] Verify exact resume, consumer progress, controller health, qualification
  and reader/API correctness before declaring the handover successful.

## Qualification evidence and open gates

- Emergency-release scope: projectless-workspace qualification is deferred by
  user decision. It is not a passing test or a claim that such workspaces cannot
  exist. The latest standalone run passed all preceding stages, including
  source-change repair (48.1s), post-audit repair (42.0s), multi-workspace
  onboarding (55.8s), and relational seed/update/delete checks. Projectless seed
  exceeded its 90s budget; subsequent projectless transitions and final restart
  were not reached. The overall run remains FAILED with owned cleanup verified.
  Earlier restart checks passed; the later final restart is not proven.
  Three-replica qualification remains required for the emergency release.
- Release acceptance update: slower automatic visibility is acceptable only if
  complete, correct API results and preserved history/isolation/recovery are
  proven. Source-change repair, post-audit repair and new-workspace checks retain their 75/60/60s
  latency targets in evidence but permit up to 180s to prove correctness.
  A missed latency target is recorded explicitly, not called a performance pass.
  This changes fixture waits only, not product timeouts, admission or write proof.
- Frozen regression including the managed INITIAL cutoff correction and ACTIVE/
  resume checkpoint batching, replica-health grant admission and background
  bootstrap budget: **4,244 tests passed in 135.86 seconds**, without
  deselection. Packaging checks passed
  293 tests plus 42 subtests; one PowerShell-parser check was skipped because
  PowerShell was unavailable. Replicated-fixture unit checks passed 80 tests
  plus 44 subtests. Chart rendering passed 36 tests. These are not live cluster
  or release-image qualification. The updated managed harness passed 251 tests
  plus 62 subtests; the accepted longer correctness waits are fixture-only and
  leave product query/lease limits unchanged.
- ACTIVE and resume checkpoint validation now read the exact ten planned streams in one
  bounded query instead of ten. Per-stream physical caps, latest-version conflict
  checks, tenant/build scope, projection/fence validation, strict replica agreement
  and fresh before/after attestations remain intact. The existing 4,209 tests
  passed again in 136.85 seconds; a focused final-source run passed 238 tests,
  including 19 new batching tests. The query/attestation reduction is verified
  with simulated transports; end-to-end latency is being measured separately.
  Resume batching additionally preserves missing checkpoints as a valid unfinished
  build while rejecting invalid present checkpoints. Six additional resume cases
  passed and are included in the final 4,234-test regression above.
- Previous standalone run passed restart; current/late freshness (31.8/33.4 s),
  typed results, updates (44.97 s), sustained ingestion, candidate parity,
  deletion, mid-scan, old-version handling (28.29 s) and post-audit checks.
  **FAILED new-workspace qualification at 60 s**: the two workspaces reached
  revision 1 ACTIVE at 34.72/52.09 s, but the default hour-floored initial cutoff
  excluded current injected spans, yielding HTTP 200 with empty results.
  The bounded INITIAL cutoff fix passed regression. A fresh live rerun passed
  the preceding checks and same-organization onboarding in 35.3 seconds, but
  the second organization's workspace remained pending at 60 seconds. The
  supervisor started that workspace's build late. Its captured source and two
  values were present, but activation was not complete before the gate expired.
  No runtime error was recorded. Later relational/projectless/restart/schema stages remain unqualified;
  the standalone gate has not passed. Owned fixtures were cleaned.
  The next run with ACTIVE batching passed activation/restart, live/late values
  (32.2s), typed/update/sustained ingestion and deletion, then missed the 75s
  source-change gate before reaching onboarding. Captured repair revision 16 had
  the correct changed value and matching audit/ACK at 81.8s, but the API
  observation ended on revision 15 before that repair was visible. Durable
  activation and capture retirement were also verified. This run remains failed; the
  accepted longer correctness wait requires a fresh complete verification.
  The first 180s rerun also failed: the fixture's injection-arm validator still
  enforced 75s, delaying injection until much of the correctness window elapsed.
  Both fixture ends now use the same 180s constant; expired/unbounded requests
  still reject before mutation. The failed run was cleaned and is not reclassified.
  The replicated fixture dependency preflight now matches the pinned psycopg3
  requirement instead of requiring psycopg2; all three focused dependency tests pass.
  The next standalone run passed repair with authenticated API visibility in
  68.2s and old-version import in 31.4s, then missed the post-audit 60s cutoff.
  The exact post-audit event was durably noticed, but later API visibility was
  not established. That gate now uses the accepted 180s correctness wait without
  relaxing its exact-event, later-revision or history assertions. The failed run
  was cleaned; subsequent workspace/relational stages remain unqualified.
  Reproducible evidence is retained privately; no fixture identities are published.
- Actual two-replica source-capture gate passed: canonical replicated source,
  product capture/ATTACH, version/tombstone scan, independent audit, unchanged
  source and owned retirement. It is **not** three-replica application evidence.
  Gate: `futureagi/scripts/property_catalog_oss/tests/replicated_smoke/source_capture_probe.py`.
- Full three-replica application qualification has executed but has not passed.
  A retry caught a truncated drain-proof observation after real Kafka delivery.
  A diagnostic run preserved the rejected bytes: the file observation ended
  mid-field without its JSON closure or newline. The shared-file reader now
  retries incomplete observations within the existing deadline; complete invalid
  proofs still fail immediately. Three focused tests pass; full regression/live
  qualification on that final correction remains pending.
  A subsequent application run passed every application stage including restart
  and all-replica APIs, but final host-side evidence checking failed on missing
  Django settings. The checker now initializes standalone parser settings and
  distinguishes current/late reconciliation observations from the two required
  ordered probes. The full qualification rerun remains pending completion.
  An earlier run passed current/late typed API visibility on all three replicas
  and then hit a fixture restart race: it read the old shutdown health before
  the replacement controller initialized. That fixture now waits for a fresh
  health record while continuing to reject failures from the new process.
  The latest completed run passed production bootstrap, initial authenticated
  property/value reads on all three direct replicas and collector OTLP acceptance.
  It then timed out awaiting direct Kafka delivery for an out-of-build-window
  candidate, which correctly requested reconciliation. The next fixture separates
  an in-scope authenticated OTLP delivery proof from current/late observations,
  requires their API visibility on every replica, and repeats after restart.
  No synthetic delivery, activation or checkpoint is inserted to satisfy the gate.
  Earlier runs exposed two corrected product failures: exact replica-health grant
  admission and automatic bootstrap using the existing bounded background source
  budget. Full regression passed 4,244 tests after both corrections.
  This runs locally in Colima with isolated synthetic data, not production.
  The unrelated CI containers had already exited; this task did not stop or
  restart them. The protected native workload remains untouched. Only exact
  run-owned fixture resources are cleaned; production sources are unchanged.
  Lane: `futureagi/scripts/property_catalog_oss/tests/replicated_smoke/application_lane.py`.
- Preserved production evidence passed the exact loader/validator compatibility
  check only. This does not prove candidate deployment, startup, replay or serving.
- [ ] Rerun the full regression on frozen source, pass standalone restart/live
  freshness and the distinct three-replica application gate, then review image
  provenance, rendered deployment and the owner-handover plan before release.
- [ ] Promote the draft hotfix PR against `main` after tests complete; keep
  remaining gaps explicit. Production rollout requires separate approval.
