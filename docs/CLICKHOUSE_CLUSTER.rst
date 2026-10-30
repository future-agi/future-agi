Sharded ClickHouse deployment
=============================

Scope and release gates (TH-8686)
---------------------------------

This opt-in topology adds storage replication and horizontal sharding without
changing the default single-node install. It is not a capacity certification or
a claim of zero data loss under arbitrary failures. Existing installations must
use a separate database and, for bundled Kubernetes, a separate Helm release.
The packaged single-node SQL files and their historical hashes are unchanged.

Implemented contracts:

* Bundled three-shard/two-replica profile, three Keeper voters, persistent volumes,
  per-shard disruption budgets, anti-affinity, private Services and Keeper ingress
  isolation. Managed external clusters use the same schema contract.
* Original logical table names are Distributed tables; physical ``*_local``
  tables use ReplicatedMergeTree-family engines. Includes spans, trace/user/session
  dimensions, remaps, eval data, aggregate-state rollups and catalog tables.
* All MVs read local source tables and write local targets. Distributed routing
  selects one replica of a shard (``internal_replication=true``). Replication of
  parts does not re-execute the MV. No MV is attached to a Distributed table.
* New immutable native baseline, append-only per-step receipts, all-node DDL
  completion checks, topology validation and refusal to adopt unowned old tables.
  ON CLUSTER is **not atomic**: an interrupted CREATE can exist on some nodes;
  retries finish the identical IF NOT EXISTS step before recording success.
* Native bootstrap, observed catalog and PostgreSQL outbox CDC use the cluster
  layout when opted in. The outbox remains the existing versioned/state-based
  writer; it does not install a second capture path. Additive PG column changes
  update both physical and logical tables across the cluster. Type/key changes
  fail inspection instead of silently converting existing data.
* Collector synchronous delivery explicitly requests foreground Distributed
  forwarding, bounded quorum wait and complete HTTP response consumption.
  Cluster mode refuses fire-and-forget ``async_insert``.

Required before production sign-off (not implied by Helm rendering):

* Run the isolated 3x2 correctness and failover fixture below, then the **whole
  product E2E suite** with the application and collector pointed at that topology.
* Run the same suite on GKE and EKS with the intended StorageClass, CNI,
  node/zone layout, mesh and release images. Neither provider is certified here.
* Run mixed read/write capacity, long-duration soak, pod/node/zone loss, Keeper
  majority loss, disk-full, upgrade, backup/restore and replay-reconciliation tests.
* Bound dictionary memory at the real user/session cardinality. Dictionaries
  intentionally read global Distributed dimensions; they do not assume locality.
* Verify application-level deletes/maintenance operations. Distributed tables do
  not support arbitrary MergeTree mutations; maintenance must target the named
  physical table ON CLUSTER. Native versioned INSERT/replacement writes are the
  supported update path; do not rewrite an ALTER mutation to a Distributed alias.

Durability and availability
---------------------------

``insertQuorum: 2`` is the reference durability policy. A successful synchronous
write to a shard has two acknowledged replicas. With two replicas, losing one
preserves already acknowledged rows and permits reads, **but strict-quorum
writes fail**. Two copies plus a surviving write quorum after a replica loss
requires at least three replicas with quorum two. There is no setting that gives
a two-replica shard both these guarantees during a partition.

``insertQuorum: 1`` is an explicit availability tradeoff: a surviving replica
can accept writes, but an acknowledged write can be lost if its only durable
copy fails before replication. The acceptance fixture exercises this weaker mode
separately and never labels it zero-data-loss. Keeper majority is also required;
three Keeper voters tolerate one voter loss, not two. Replication is not backup.

Foreground Distributed forwarding avoids acknowledging a file in the initiator's
background distribution queue as if it were replicated data. It does not make a
multi-shard batch transactional. A failed/timed-out response can mean partial
commit across shards or materialized-view targets. Reconcile before replaying.
Deterministic block retries can deduplicate only within the configured Keeper
block-deduplication windows; different chunking, changed defaults, changed row
order or expired windows invalidate that protection. ReplacingMergeTree dedup
is not an exactly-once guarantee for additive aggregate states.

The existing aggregate-state MVs count insert events. They are not retractions:
a semantic span update with a new version can add another aggregate state, just
as on a single node. Exact latest-row queries must read ``spans FINAL`` and
aggregate the latest versions. Do not infer exact business counts from a
non-retracting MV under arbitrary updates. The fixture distinguishes same-block
retry dedup from a new-version replacement.

The collector's existing best-effort dimension writes are reconciled from their
source; they are not promoted to acknowledged canonical events by this feature.
The collector dead-letter file/receiver buffer is not a new durable distributed
queue. Preserve source retries and backups; loss of that pod's ephemeral disk is
outside the acknowledged ClickHouse-write guarantee.

Routing and query correctness
-----------------------------

Spans route by ``cityHash64(project_id, trace_id)``; trace metadata uses the same
project and string trace UUID. This keeps a trace together, evenly distributes a
large project's traces and returns updates to the original shard. A session can
contain multiple traces; one user can contain many sessions. It is impossible
to colocate all three independently without replication or concentrating large
tenants. Session/user membership can change late, so routing on those mutable
labels would strand old row versions on a different shard.

The query profile therefore forces coordinator aggregation, one replica per
shard, all-shard availability, and GLOBAL semantics for distributed IN/JOIN
subqueries. It disables shard-skipping assumptions. Session/user rollup readers
must merge aggregate states **across shards**, never concatenate local answers.
Mutable RMT identities use stable IDs (remaps use ``old_id``). Curated users
route by ``cityHash64(project_id, end_user_id)`` and curated sessions by
``cityHash64(project_id, trace_session_id)``; neither schema has an ``id`` column.
The compiler validates every routing column against its CREATE declaration
before any DDL is sent. FINAL works per
shard because every version of an identity returns to the same shard. These
settings preserve correctness, not arbitrary join efficiency: GLOBAL sets can
consume network/memory. Keep tenant/project/time restrictions and bounded
preaggregation at the application boundary.

New tables must register an explicit immutable routing key; there is no
column-name-based inference. CDC tables require an inspected single-column ID
primary key. Do not change a key or the
shard count in place. A schema name ending in ``_local`` is an administrative
surface, not an app/query-workbench dataset.

Install and external cluster contract
-------------------------------------

For a fresh private Kubernetes environment with at least three schedulable nodes::

  helm lint --strict deploy/helm/futureagi \
    -f deploy/helm/futureagi/examples/bundled.yaml \
    -f deploy/helm/futureagi/examples/clickhouse-cluster.yaml
  helm template cluster deploy/helm/futureagi \
    -f deploy/helm/futureagi/examples/bundled.yaml \
    -f deploy/helm/futureagi/examples/clickhouse-cluster.yaml

Review resources and persistent volumes before installing. Bundled PostgreSQL,
Redis, object storage and Temporal remain evaluation defaults; a replicated
ClickHouse does not make those services highly available. The default data pod
profile requests 4 GiB/2 CPU and limits 8 GiB/4 CPU per replica; it is a starting
point, not a throughput promise. Storage sizing and expansion remain operator
responsibilities. StatefulSets use OnDelete updates: upgrade one replica at a
time under an explicit quorum decision, not an automatic six-pod restart.

For managed infrastructure, overlay ``examples/clickhouse-external-cluster.yaml``.
Every node must expose the same ``remote_servers`` topology and ordinal ``shard``
macro, a unique replica macro within its shard, access to Keeper, and a ``tiered``
storage policy. Replicated paths contain installation prefix/database/shard/table.
The schema receipt table deliberately uses one global replication group with
``{shard}-{replica}`` identities, so another shard cannot miss migration history.
Configure the following profile on all application and remote-cluster users::

  async_insert = 0
  distributed_foreground_insert = 1
  insert_quorum = 2
  insert_quorum_timeout = 10000
  insert_deduplicate = 1
  deduplicate_blocks_in_dependent_materialized_views = 1
  distributed_product_mode = 'global'
  prefer_global_in_and_join = 1
  distributed_group_by_no_merge = 0
  optimize_skip_unused_shards = 0
  optimize_distributed_group_by_sharding_key = 0
  max_parallel_replicas = 1
  skip_unavailable_shards = 0

Bootstrap needs DDL/ON CLUSTER/CREATE USER/GRANT rights; run it in an exclusive
maintenance window. External providers that do not expose these primitives or
use a different cloud engine require a separate adapter and are not supported
merely because they provide a ClickHouse endpoint.

Transport is **private HTTP/native**, matching the existing application clients.
``privateNetwork: true`` is an explicit operator acknowledgement, not encryption.
Use a NetworkPolicy-capable CNI and an encrypting service mesh/sidecar where policy
requires TLS; never expose these endpoints publicly. This change does not claim
end-to-end TLS support for every Python/Go/native/CDC client or TLS-only managed
public services. Passwords come from Kubernetes Secrets, not generated XML.
Interserver replication is authenticated. Keeper is reachable only by data/Keeper
pods in the release. Kubernetes Secret encryption at rest, backup encryption,
least-privilege app roles and certificate/credential rotation are operator gates.

Migration, backfill, cutover and rollback
-----------------------------------------

1. Inventory source databases, clients, active eval table, custom tables,
   materialized views, remaps, dictionaries and schema hashes. Back up ClickHouse,
   PostgreSQL and Keeper; restore the backup in an isolated environment. Reserve
   room for both copies. Do not change the old release's topology flag.
2. Provision a new release/cluster and new database. Keep application traffic on
   the source. Review the plan with the new database and cluster env settings::

     python manage.py ch25_cluster_schema > cluster-plan.sql
     python manage.py ch25_cluster_schema --apply

   The new command uses ``CH_CLUSTER_NAME``, ``CH_CLUSTER_KEEPER_PREFIX``,
   ``CH_CLUSTER_SHARDS``, ``CH_CLUSTER_REPLICAS`` and
   ``CH_CLUSTER_INSERT_QUORUM`` with the existing CH connection/database config.
   Historical ``ch25_apply_schema --force`` is **not** a conversion procedure.
   A failed CREATE has no success receipt: a pre-release correction to an invalid
   alias expression can resume after the previously successful steps. Existing
   success receipts are never bypassed or rewritten; if a different routing
   expression was actually installed, stop and use a separately reviewed
   migration/new database rather than forcing a changed checksum.
   ``CH_CLUSTER_ENABLED=true`` selects this path; keep the legacy
   ``CH_USE_REPLICATED_ENGINES=false`` as Helm does. The latter controls the old
   local/hosted bootstrap and is deliberately not the new topology switch.
   New native upgrades append a numbered SQL file with named ADD COLUMN/INDEX/
   PROJECTION IF NOT EXISTS actions. Packaged ``v2/schema`` files numbered 028
   and later are included after the frozen baseline. Every new table declares
   ``-- cluster-shard-key: table_name = cityHash64(immutable_columns)`` in that
   new file; missing routing declarations fail closed. Later MVs use cumulative
   known-table mapping, including pre-existing local sources/targets. Columns update both schemas; indexes and
   projections update only local storage. MODIFY/DROP/mutations are refused.
3. Pause all canonical writers (collectors, workers, maintenance/backfills,
   metadata and eval writes), drain acknowledged work and record the cutover
   watermark. This release provides a bounded downtime migration, not an online
   CDC migration between ClickHouse clusters. The PostgreSQL outbox remains
   captured while its drain is paused; never discard its pending rows.
4. Copy latest dimension/remap/trace records first, preserving IDs, versions,
   timestamps and tombstones. Warm the target dictionaries before copying spans.
   Copy raw spans/eval data once through the **logical** destination tables so
   their local MVs populate rollups. Do **not** also copy the same rollups.
   For direct aggregate-state migration instead, use a separately reviewed plan
   with no overlapping MV population; never backfill once per replica.
5. Backfill in fixed, ordered project/time chunks. Select explicit non-materialized
   column names from ``system.columns``; do not assume historical column order.
   Stream bounded blocks and preserve the source values of DEFAULT columns.
   Record source count, identity/version checksum, destination checksum and a
   stable chunk token. Replays require the same bytes/order and unexpired dedup
   windows. An ambiguous old chunk requires reconciliation or a fresh staging
   database, not blind replay into additive rollups. There is no automatic
   destructive reset or unattended historical backfill in this release.
6. Verify every project/time bucket against latest raw source rows: distinct
   physical span identities, versions/tombstones, root traces, eval IDs/results,
   cross-trace sessions, cross-session users, remaps, filtered lists and dashboard
   aggregates. Compare raw-derived values, not only pre-existing MV counters.
   Check six physical copies and three logical shards, not six copies summed.
   Confirm no ``system.distribution_queue`` files, replication queue backlog,
   readonly replicas, lost parts or unresolved DDL tasks.
7. Atomically switch **all** CH clients (API, workers, collector, read-only SQL
   client, catalog consumers and admin jobs) to the target database/endpoint.
   Keep single-node source volumes untouched. Resume outbox draining, then
   canonical ingestion; run product E2E and an explicit read/write smoke test.
8. Before target-only writes, rollback is simply restoring all old client
   endpoints. After target-only writes, pause again and reconcile/replay those
   writes to the source before reverting. A blind endpoint rollback loses new
   data. Do not delete the old database/PVCs until the retention and rollback
   window are approved. Topology changes use the same staged process.

Useful operator queries (run from an administrative connection)::

  SELECT * FROM system.distributed_ddl_queue WHERE status != 'Finished';
  SELECT database, table, is_readonly, is_session_expired, queue_size,
         absolute_delay, lost_part_count FROM system.replicas;
  SELECT * FROM system.distribution_queue;
  SELECT _shard_num, count() FROM spans FINAL GROUP BY _shard_num;
  SELECT identity, step, sha256 FROM cluster_schema_versions ORDER BY step;

Run the replica/queue queries on every node (or clusterAllReplicas). A healthy
initiator alone is not cluster health. Never sum replicated copies as data volume.

Reproducible verification
-------------------------

Static/contract tests from the repository root::

  (cd fi-collector && go test ./pkg/chwriter ./pkg/observedcatalog ./cmd/fi-collector)
  (cd futureagi && pytest --reuse-db tracer/tests/test_ch25_cluster_schema.py \
    tracer/tests/test_ch25_apply_schema_replicated.py \
    tracer/services/clickhouse/tests/test_oss_outbox_cdc.py \
    tfc/tests/test_bootstrap_install.py)
  python deploy/helm/futureagi/hack/values_docs.py --check
  python deploy/helm/futureagi/hack/test_cluster.py

The local 3x2 fixture uses approximately 5.3 GiB of container memory limits, six
loopback HTTP ports and unique Compose project/volume names. Do not start it on a
shared VM without resource-owner coordination::

  python deploy/clickhouse/cluster-acceptance/generate.py \
    --output /path/to/NEW/isolated-fixture --port 18123
  # Review, then run the exact unique docker compose command it prints.
  python deploy/clickhouse/cluster-acceptance/acceptance.py \
    --directory /path/to/NEW/isolated-fixture --allow-faults

Use an environment with clickhouse-connect. The script does not delete volumes;
faults only stop/restart named services in its generated project. Run each full
acceptance against a fresh fixture; it intentionally fails if an earlier run's
rows remain. It checks schema replay, local MV fanout, block retry dedup, cross-
shard grouping, eval insertion, RMT replacement and replica-loss policy. It is
**not** the whole-app E2E suite, a benchmark, a cloud certification or a backup
restore test. Keep command output and image digests as qualification evidence.

Hosted bounded acceptance
-------------------------

``.github/workflows/clickhouse-cluster-ci.yml`` runs independently on a disposable
hosted runner, on relevant PR paths or manual dispatch. It has a 40-minute hard
limit and a 90-span fixture, not a load benchmark. Application mode adds a 256 MiB
PostgreSQL and 128 MiB Redis to the 3x2 topology (5.625 GiB total container limits).
It installs OSS dependencies before services start. The runner resolves the
caller-relative fixture directory before switching its working directory and
import root to ``futureagi/``, matching the backend image and Standalone bootstrap.
Regression subprocesses import the real demo-data loader and all four packaged
JSON assets from both repository-root and unrelated launch directories; no data
is substituted and migrations remain enabled. Fixture environment configuration
also precedes backend imports. A subprocess preflight poisons
ambient connection variables and a dotenv file, loads the real Django settings,
and verifies the default/pooler, direct and replica database aliases plus legacy/
v2/catalog/CDC ClickHouse and Redis/cache/channel targets. All aliases are pinned
to the generated loopback endpoints; stale cached settings fail before networking
or migration. Dotenv loading and incidental remote model-metadata fetching are
disabled in this fixture. The runtime repeats the resolved-settings checks before
and after Django startup. Optional libpq service, file and option variables are
removed entirely (empty service-file paths are not safe defaults). Once PostgreSQL
is running, real libpq and all three Django aliases must connect and verify their
owned database/user identities before migrations; the evidence gate requires that
executed connection proof. The runner then runs actual Django migrations,
replays the native bootstrap, creates every inspected CDC mirror/dependent view,
and proves captured insert/update/delete through the real outbox installer/drain.
It then applies a later additive table/MV/column migration without replacing the
baseline and checks global rollups plus strict and explicitly weaker failure
policies. An evidence JSON gate refuses absent/skipped proofs. Only this job's
uniquely named project is removed; sanitized logs/evidence are uploaded for seven
days, never the generated password/config files. No GKE/EKS resources are created.

Equivalent explicitly provisioned isolated run::

  python deploy/clickhouse/cluster-acceptance/generate.py \
    --output /path/to/NEW/fixture --port 28123 --application-deps
  # Start the exact generated project on the approved host, then:
  futureagi/.venv/bin/python deploy/clickhouse/cluster-acceptance/acceptance.py \
    --directory /path/to/NEW/fixture --application --allow-faults

This mode publishes CH HTTP on 28123-28128, PostgreSQL on 28129, Redis on 28130
and the first CH native port on 28131. The actual product UI/Temporal-worker E2E
suite remains a separate release gate; this job does not silently claim it.

Evidence and ClickHouse references
----------------------------------

Implementation evidence is the checked-in planner/runner, chart, unit tests and
isolated harness. Helm lint/template and unit tests alone do not prove server SQL
semantics or fault tolerance. The acceptance, product E2E, GKE/EKS and full-scale
soak gates must be recorded separately; do not replace a missing run with a pass.

ClickHouse documentation for the storage semantics above (reviewed 2026-10-21):

* ClickHouse replication: per-shard replication, block dedup and independent
  Keeper paths. https://clickhouse.com/docs/reference/engines/table-engines/mergetree-family/replication
* Distributed engine: forwarding, internal_replication and background-queue
  durability. https://clickhouse.com/docs/reference/engines/table-engines/special/distributed
* Transaction boundaries: single-table vs multi-shard insert guarantees.
  https://clickhouse.com/docs/concepts/features/operations/insert/transactions
* Keeper plus shard/replica reference topology.
  https://clickhouse.com/docs/guides/oss/deployment-and-scaling/examples/2-shards-2-replicas

Ingest buffering is a separate durability layer, not supplied by replicas alone.
This topology does not add an object-storage write-ahead log or storage/compute
separation. Replication and sharding do not establish a workload's capacity;
qualification requires the recorded correctness, fault and scale gates above.
