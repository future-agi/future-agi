# Unified property catalog DEV workload

This directory renders the reviewed, single-workspace DEV workload for the
unified property catalog. It is not a production manifest and it is not a
general-purpose Helm chart.

## Hard boundary

Stop if any statement below is not true.

- The Kubernetes namespace is DEV and contains the exact token `dev`.
- The configuration contains exactly one organization/workspace pair and an
  explicit, sorted project allowlist.
- The target ClickHouse database is a new, isolated database with a safe
  lowercase ClickHouse identifier. The source and target databases differ, and
  the production `property_catalog` database is forbidden.
- Every image is pinned by `@sha256:<64 lowercase hex characters>`.
- The consumer image is byte-for-byte the same reference as the live collector
  image.
- The Kafka topic and consumer group are dedicated to this DEV property
  catalog. Neither may reuse a `span-attribute-catalog` topic or group.
- Production is out of scope. Do not point this workload, its Secrets, Kafka,
  Temporal, ClickHouse, OTLP routing, or image-pull configuration at production
  or a live/production-named endpoint.
- Existing tables and existing rows are protected. The rollout reads the
  existing DEV source through a server-enforced read-only identity and writes
  only the six new tables listed below. The canary collector remains unrouted
  and receives no OTLP. This rollout must not insert into, update, delete,
  truncate, backfill into, or redefine an existing table.
- The collector's OTLP sockets bind only to `127.0.0.1`, and the canary
  Service's admission selector deliberately matches no Pod. Any EndpointSlice
  address is a stop condition.

Never run `DROP DATABASE`, a wildcard drop, a source-database DDL statement, or
`kubectl delete -f` against the rendered file. The latter would also delete the
runtime PVC and its drain evidence.

## Rendered topology

The renderer emits exactly six namespaced resources and no Secret objects:

| Resource | Name | Contract |
| --- | --- | --- |
| ServiceAccount | `property-catalog-dev` | No mounted API token |
| ConfigMap | `property-catalog-dev-config` | DEV gates; public reads and the schedule default off |
| PersistentVolumeClaim | `property-catalog-dev-runtime` | One `ReadWriteOnce` POSIX filesystem |
| Deployment | `property-catalog-dev` | One Pod containing the live OTLP collector and the Python control worker |
| Service | `property-catalog-dev-otlp-canary` | Declares ports `4317`, `4318`, and `9464` but deliberately selects no Pod and has zero endpoints |
| Deployment | `property-catalog-dev-consumer` | One durable Kafka-to-ClickHouse consumer |

Both Deployments use `replicas: 1`, the `Recreate` strategy, and a 180-second
termination grace period. Do not change either Deployment to `RollingUpdate`,
increase its replica count, or create a second worker for the same workspace,
queue, topic/group, or runtime volume.

The `live-otlp-collector` and `control-plane` containers are in the same Pod and
mount the same PVC at exactly `/var/lib/property-catalog-runtime`. That volume
holds all of the following state:

- `/var/lib/property-catalog-runtime/revision-fence-v2.json`
- `/var/lib/property-catalog-runtime/producer-drain-proof-v2.json`
- `/var/lib/property-catalog-runtime/producer-state-retirements-v1.json`
- the Go producer's fsynced spool
- the Python mutation lock
- `/var/lib/property-catalog-runtime/span-dead-letter/`

The storage class must provide real POSIX file behavior for `flock`, atomic
same-directory rename, file and directory `fsync`, permissions, and durable
restart recovery. An eventually consistent object-store mount is not valid.
Both application containers run as UID/GID `65532`. A newly provisioned PVC
root may still be owned by UID `0`, so the one initializer runs Python as UID
`0` with a read-only root filesystem, privilege escalation disabled, every
capability dropped, and only `CHOWN`, `DAC_OVERRIDE`, and `FOWNER` added. It
uses `lstat` to reject a non-directory at any fixed runtime path, then changes
only the runtime, cache, home, and dead-letter directories to UID/GID `65532`
and mode `0770`. It does not recursively rewrite proof or spool files and does
not invoke a shell. Application containers remain non-root, drop every
capability, and use read-only root filesystems. The control container also
receives only these bounded `emptyDir` mounts:

- `/tmp`: `256Mi`
- `/app/backend/logs`: `64Mi`
- `/app/backend/tfc/logs`: `64Mi`

The RWO PVC is mounted only at `/var/lib/property-catalog-runtime`; no
`hostPath` or unbounded scratch volume is admitted.

After a revision is durably active, Python rereads its conflict-visible
activation, fenced reservation, exact build plan, manifest, checkpoints, and
lineage anchor before atomically publishing the bounded retirement high-water.
Go never retires the current/fenced-only producer checkpoint. It may compact a
terminal old checkpoint only after this activation proof exists, a strictly
newer valid fence is visible, and no matching spool envelope remains.

The live collector has `FI_CATALOG_MODE=disabled`. Only the unified
`FI_PROPERTY_CATALOG_MODE=kafka` path is enabled. The dedicated worker polls
only `property_catalog_dev_sidecar`, with one activity slot and one workflow
task slot. The generic all-queues worker excludes this queue.

`FI_GRPC_ADDR=127.0.0.1:4317` and
`FI_HTTP_ADDR=127.0.0.1:4318`; neither OTLP listener accepts Pod-network
traffic. The admin listener on `9464` remains available to the Pod health
probes. The canary Service cannot reach any of these ports because its selector
is deliberately unmatched.

## New table inventory

The clean installer accepts an absent or empty isolated target database and
creates exactly these tables:

1. `property_definition_catalog`
2. `span_attribute_value_catalog`
3. `property_catalog_checkpoints`
4. `property_catalog_activations`
5. `property_catalog_deliveries`
6. `property_catalog_source_streams`

If the target database already contains any table, schema installation refuses
to run. If an architecture reset is required, first stop this workload and
inspect the exact isolated target. A separately reviewed DEV reset may remove
only the six names above. If any other name is present, stop; do not delete it
and do not drop the database. This renderer performs no deletion.

## Prerequisites

Create a private operator configuration from `config.example.yaml`; do not
commit the filled file or the rendered YAML. Preserve the field order because
the v1 renderer treats the canonical order as part of its reviewed input.

Review all of the following before rendering:

- one canonical UUIDv4 organization ID, one canonical UUIDv4 workspace ID, and
  `1..256` sorted, unique canonical UUIDv4 project IDs;
- one canonical UUIDv4 hot producer stream ID;
- a bounded half-open backfill window expressed as UTC whole hours, no longer
  than 366 days;
- a DEV source database and a different, empty target database with a safe
  lowercase ClickHouse identifier (no naming prefix is required);
- six exact remote-provenance values captured through the reviewed DEV
  identities: write/source ClickHouse `hostName()`, PostgreSQL
  `current_database()`/`current_user()`, canonical literal
  `inet_server_addr()`, and integer `inet_server_port()`;
- digest-pinned backend and collector images, with `images.consumer` exactly
  equal to `images.collector`;
- DEV-only Temporal, ClickHouse, and Kafka endpoints;
- a dedicated DEV Kafka topic and consumer group; and
- a `ReadWriteOnce` filesystem storage class with the POSIX guarantees above.

The dedicated Kafka topic must exist before workload apply so the empty hot
stream can durably fence. Use the platform's reviewed topic-management path; do
not enable broker auto-creation for this rollout.

### Remote provenance

The renderer requires all six values below in the canonical `provenance`
section. It never infers them from a DNS endpoint and supplies no default.
They are expected identities, not credentials, and are emitted to the
ConfigMap under these exact settings:

| Configuration value | Runtime setting | Must equal |
| --- | --- | --- |
| `write_clickhouse_hostname` | `PROPERTY_CATALOG_DEV_EXPECTED_WRITE_CH_HOSTNAME` | case-sensitive `hostName()` through the isolated control writer while connected to the existing control database (`default` in the reviewed runtime) |
| `source_clickhouse_hostname` | `PROPERTY_CATALOG_DEV_EXPECTED_SOURCE_CH_HOSTNAME` | case-sensitive `hostName()` through the source reader |
| `postgres_database` | `PROPERTY_CATALOG_DEV_EXPECTED_PG_DATABASE` | `current_database()` through the backend identity |
| `postgres_user` | `PROPERTY_CATALOG_DEV_EXPECTED_PG_USER` | `current_user` through the backend identity |
| `postgres_server_address` | `PROPERTY_CATALOG_DEV_EXPECTED_PG_SERVER_ADDRESS` | canonical literal IP returned by `inet_server_addr()`; DNS names and `NULL` are invalid |
| `postgres_server_port` | `PROPERTY_CATALOG_DEV_EXPECTED_PG_SERVER_PORT` | integer returned by `inet_server_port()` |

Capture and review those results before rendering. At runtime the source
ClickHouse connection must additionally prove server-locked `readonly=1` with
no client override. Its configured database/user must match
`currentDatabase()`/`currentUser()`. PostgreSQL must prove a login,
non-superuser role without create-role, create-db, replication, or bypass-RLS;
both default and current transaction read-only must be on, and effective DML
privileges across non-system tables must be zero. Any mismatch stops before
schema installation or source reads.

### Secret references

The configuration supplies names only. Do not place a username, password,
connection string, token, or registry credential in the configuration or
rendered YAML.

| Configuration reference | Pod use | Required keys/contract |
| --- | --- | --- |
| `secrets.backend_env` | `control-plane` `envFrom` | Purpose-built minimal DEV property-catalog backend environment Secret, including the read-only PostgreSQL connection whose authoritative identity is checked against `provenance`; the manifest names no individual keys |
| `secrets.collector_env` | `live-otlp-collector` `envFrom` | Purpose-built minimal DEV property-catalog collector Secret with only startup/auth prerequisites such as `FI_PG_WRITE`; no existing-table ClickHouse writer identity |
| `secrets.source_read_clickhouse` | Python and Go source readers | `username`, `password`; server-enforced SELECT-only access to the DEV source, injected as Python `CH25_*` and collector `FI_CH_*` credentials |
| `secrets.control_write_clickhouse` | Python bootstrap/reconciler | `username`, `password`; least-privilege access to the isolated target and its six tables |
| `secrets.consumer_write_clickhouse` | Go catalog sink | `username`, `password`; inserts only into `property_definition_catalog`, `span_attribute_value_catalog`, and `property_catalog_deliveries` |
| `secrets.consumer_ledger_clickhouse` | Go durable checkpoint reader | `username`, `password`; SELECT only on `property_catalog_source_streams`, `property_catalog_checkpoints`, `property_catalog_activations`, and `property_catalog_deliveries` in the isolated six-table target |
| `secrets.image_pull` | Both Pods | Kubernetes registry Secret containing `.dockerconfigjson` |

All seven Secret names must be distinct and must contain the exact tokens `dev`
and `property-catalog`. Do not reuse a general backend, collector, core,
production, or cross-workspace Secret. The consumer writer and ledger usernames
must also differ; runtime validation rejects identical usernames. Explicit
manifest environment variables override the corresponding values in the two
whole-Secret `envFrom` imports. Audit those imports anyway: they must contain no
production endpoint or credential. In particular, the collector's explicit
`FI_CH_USERNAME` and `FI_CH_PASSWORD` references override any same-named values
in `secrets.collector_env`, so it has no identity capable of inserting into an
existing span table.

## Offline render and review

Run these commands from the repository root. Substitute a private file path for
`CONFIG`; the checked-in example is deliberately unrenderable until every
placeholder is replaced.

```bash
CONFIG=/private/path/property-catalog-dev.yaml
RENDERED=/tmp/property-catalog-dev.yaml

futureagi/.venv/bin/python -m unittest discover \
  -s deploy/dev/property-catalog \
  -p 'test_*.py' \
  -v

futureagi/.venv/bin/python deploy/dev/property-catalog/render.py \
  --config "$CONFIG" \
  --check

futureagi/.venv/bin/python deploy/dev/property-catalog/render.py \
  --config "$CONFIG" \
  --output "$RENDERED"
```

Omitting `--bootstrap-activation-sha256` is mandatory for the first render. It
must produce `PROPERTY_CATALOG_DEV_RECONCILE_ENABLED=false` and the annotation
`futureagi.com/property-catalog-schedule=disabled`. The renderer rejects
placeholders, production/live tokens, a second workspace, tag-only images,
mismatched images or runtime paths, non-purpose-built or reused secrets, and
non-DEV topic/group names before it writes output.

Inspect the complete file, then confirm the default-off gates without applying
anything:

```bash
rg -n 'PROPERTY_CATALOG_DEV_RECONCILE_ENABLED|PROPERTY_CATALOG_READ_MODE|PROPERTY_CATALOG_DEV_EXPECTED_|FI_CATALOG_MODE|FI_PROPERTY_CATALOG_MODE|FI_GRPC_ADDR|FI_HTTP_ADDR|property-catalog-schedule|current-phase|otlp-admission' "$RENDERED"
```

Expected values are `false`, `off`, `disabled`, `kafka`, and `disabled`,
plus the loopback addresses `127.0.0.1:4317` and `127.0.0.1:4318`, current phase
`no-otlp-traffic`, admission value `separate-approval-required`, and all six
exact reviewed provenance values. Treat any difference as a stop condition.

## DEV bootstrap

Set these shell variables to the exact reviewed values from the private
configuration. Do not infer them from the currently selected kubectl context.

```bash
NAMESPACE='<exact-dev-namespace>'
WORKSPACE_ID='<exact-workspace-uuid>'
SCHEDULE_ID="unified-property-catalog-dev-${WORKSPACE_ID}"
```

Before step 1, verify the kubectl context, namespace, referenced Secrets,
dedicated Kafka topic/group, empty target database, and source read-only grants.
Take a read-only inventory of the source and target tables and retain it as
evidence. Stop if the context or any endpoint is production, if the target is
not empty, or if any old table would be changed. Also inspect the existing DEV
exporter, ingress, Service selectors, and DNS aliases: none may name
`property-catalog-dev-otlp-canary`. If any traffic can reach the canary, stop
and escalate before applying this workload.

### 1. Apply with reconciliation disabled

```bash
kubectl -n "$NAMESPACE" apply -f "$RENDERED"
kubectl -n "$NAMESPACE" rollout status deployment/property-catalog-dev --timeout=10m
kubectl -n "$NAMESPACE" get deployment/property-catalog-dev deployment/property-catalog-dev-consumer
kubectl -n "$NAMESPACE" get service/property-catalog-dev-otlp-canary
```

At this point the control worker and live collector must share one running Pod.
The consumer may restart until the bootstrap creates the six target tables; do
not declare it ready yet. The schedule and public property-catalog reads remain
off. Applying these resources does not change existing OTLP routing: the
Service is annotated `manual-canary-only`, `default-traffic=disabled`, and
`current-phase=no-otlp-traffic`; its selector matches no Pod.

### 2. Prove the canary remains unrouted

Confirm the Deployment is healthy through its admin-port `9464` probes, while
the canary Service exposes zero endpoint addresses:

```bash
kubectl -n "$NAMESPACE" get endpointslice \
  -l kubernetes.io/service-name=property-catalog-dev-otlp-canary \
  -o json \
  | jq -e '[.items[].endpoints[]?.addresses[]?] | length == 0'
kubectl -n "$NAMESPACE" logs deployment/property-catalog-dev \
  -c live-otlp-collector --tail=100
```

The `jq` expression must return `true`. Any endpoint address is a stop
condition. The Service does not provide an admin route either; health checks
reach the Pod's `9464` listener directly. The OTLP processes listen only on
loopback, at `127.0.0.1:4317` and `127.0.0.1:4318`.

Do not change an exporter, ingress, Service selector, DNS alias, or existing
collector. Send no OTLP payload of any kind to the canary. Initial
schema/backfill qualification runs with the canary unrouted; the Go/Python fence
handshake drains an empty stream and does not require real traffic. Ingestion
and hot-path testing are a later, separately reviewed phase and are not
authorized by this runbook.

### 3. Prove the plan and inspect status

The command without a mode flag is a zero-I/O plan. `--status` is read-only.

```bash
kubectl -n "$NAMESPACE" exec deployment/property-catalog-dev \
  -c control-plane -- \
  python manage.py ch25_property_catalog_dev_rollout

kubectl -n "$NAMESPACE" exec deployment/property-catalog-dev \
  -c control-plane -- \
  python manage.py ch25_property_catalog_dev_rollout --status
```

The plan must name only the six-table write allowlist and report
`legacy_source_access=select_only` and `zero_io=true`. Before the first
bootstrap, status may report `schema_ready=false`; it must still name the exact
isolated target and must not name a production or source target.

### 4. Execute the one-time bootstrap

The fixed sequence is schema, backfill, reconcile, qualify, activate. It may not
be partially selected or reordered.

```bash
BOOTSTRAP_JSON=/tmp/property-catalog-dev-bootstrap.json

kubectl -n "$NAMESPACE" exec deployment/property-catalog-dev \
  -c control-plane -- \
  python manage.py ch25_property_catalog_dev_rollout --execute \
  | tee "$BOOTSTRAP_JSON"

jq -e '.completed == ["schema","backfill","reconcile","qualify","activate"]' \
  "$BOOTSTRAP_JSON"

ACTIVATION_SHA256="$(jq -er '.evidence[] | select(.stage == "qualify") | .evidence.activation_sha256' "$BOOTSTRAP_JSON")"
printf '%s\n' "$ACTIVATION_SHA256" | rg '^[0-9a-f]{64}$'
```

Do not enable the schedule if the command fails, if the completion list differs,
or if no lowercase qualification digest is present. Preserve the JSON as
bootstrap evidence; it contains no credential values.

### 5. Verify status and the consumer's external readiness contract

```bash
kubectl -n "$NAMESPACE" exec deployment/property-catalog-dev \
  -c control-plane -- \
  python manage.py ch25_property_catalog_dev_rollout --status

kubectl -n "$NAMESPACE" rollout status \
  deployment/property-catalog-dev-consumer --timeout=10m
kubectl -n "$NAMESPACE" logs deployment/property-catalog-dev-consumer \
  -c consumer --tail=200
```

Status must report `schema_ready=true`, `active=true`, and exactly the six target
tables. The consumer intentionally has no HTTP readiness endpoint. Kubernetes
process availability alone is insufficient. All three external gates are
required:

1. The consumer process remains stable with no checkpoint, sequence, lease,
   ClickHouse, or Kafka errors.
2. Approved Kafka administration tooling reports lag `0` for the exact
   configured dedicated consumer group on the exact configured topic, across
   every partition.
3. A read-only query using the dedicated consumer-ledger identity can see
   committed `transport='kafka'` rows in `property_catalog_deliveries` for this
   exact organization/workspace, with no `outcome='gap'` rows; rollout status
   independently confirms the active revision in `property_catalog_activations`.

Do not manufacture Kafka traffic to make these checks pass. The bootstrap's hot
stream terminal provides its own delivery evidence. If that evidence is absent,
stop and diagnose it; do not send a trace in this phase.

## Enable the 120-second schedule

Enable reconciliation only after every bootstrap and readiness gate above is
green and the activation digest was captured from the successful `qualify`
evidence.

Rendering the enabled form changes the Pod template annotations, so the
`property-catalog-dev` Deployment performs a `Recreate` restart. The unrouted
canary endpoint is briefly unavailable, but live DEV ingestion is unchanged.
Two candidate collector Pods must never overlap.

```bash
ENABLED_RENDERED=/tmp/property-catalog-dev-enabled.yaml

futureagi/.venv/bin/python deploy/dev/property-catalog/render.py \
  --config "$CONFIG" \
  --bootstrap-activation-sha256 "$ACTIVATION_SHA256" \
  --check

futureagi/.venv/bin/python deploy/dev/property-catalog/render.py \
  --config "$CONFIG" \
  --bootstrap-activation-sha256 "$ACTIVATION_SHA256" \
  --output "$ENABLED_RENDERED"

kubectl -n "$NAMESPACE" apply -f "$ENABLED_RENDERED"
kubectl -n "$NAMESPACE" rollout status deployment/property-catalog-dev --timeout=10m

kubectl -n "$NAMESPACE" exec deployment/property-catalog-dev \
  -c control-plane -- \
  python manage.py register_temporal_schedules --property-catalog-only

kubectl -n "$NAMESPACE" exec deployment/property-catalog-dev \
  -c control-plane -- \
  python manage.py register_temporal_schedules --describe "$SCHEDULE_ID"
```

The scoped registration command refuses to contact Temporal unless exactly one
property-catalog schedule is configured; it does not reconcile unrelated DEV
schedules. Its only entry must be
`unified-property-catalog-dev-<workspace UUID>`, every 120 seconds, on queue
`property_catalog_dev_sidecar`, with overlap policy `SKIP`. Exactly one
workspace schedule may exist. Do not create a cron job or a second Temporal
schedule for the same workspace.

Observe at least one scheduled run, re-run rollout `--status`, and repeat the
three external consumer gates. Keep `PROPERTY_CATALOG_READ_MODE=off` during
bootstrap and schedule qualification. Enabling `shadow` or `read` in an API
workload is a separate reviewed DEV action requiring its dedicated read
identity and acknowledgement; this workload does not perform it. Schedule
enablement also does not authorize OTLP: the canary must remain unrouted after
this runbook completes.

## Status checks

Use these without changing application state:

```bash
kubectl -n "$NAMESPACE" get deployment/property-catalog-dev \
  deployment/property-catalog-dev-consumer service/property-catalog-dev-otlp-canary \
  pvc/property-catalog-dev-runtime

kubectl -n "$NAMESPACE" exec deployment/property-catalog-dev \
  -c control-plane -- \
  python manage.py ch25_property_catalog_dev_rollout --status

kubectl -n "$NAMESPACE" exec deployment/property-catalog-dev \
  -c control-plane -- \
  python manage.py register_temporal_schedules --describe "$SCHEDULE_ID"
```

Also retain Kafka group lag, target ledger, activation, Pod restart, and proof
that the canary stayed unrouted. A successful `kubectl rollout status` does not
replace those checks.

## Rollback

Rollback preserves the isolated six-table database, the delivery ledger, the
PVC, and all existing data. It never drops or truncates a table.

1. Pause the schedule if it exists, then describe it and verify it is paused.

   ```bash
   kubectl -n "$NAMESPACE" exec deployment/property-catalog-dev \
     -c control-plane -- \
     python manage.py register_temporal_schedules --pause "$SCHEDULE_ID"

   kubectl -n "$NAMESPACE" exec deployment/property-catalog-dev \
     -c control-plane -- \
     python manage.py register_temporal_schedules --describe "$SCHEDULE_ID"
   ```

2. Re-render without `--bootstrap-activation-sha256`, apply the disabled form,
   and wait for its `Recreate` restart. This restores
   `PROPERTY_CATALOG_DEV_RECONCILE_ENABLED=false`.

   ```bash
   futureagi/.venv/bin/python deploy/dev/property-catalog/render.py \
     --config "$CONFIG" \
     --output "$RENDERED"

   kubectl -n "$NAMESPACE" apply -f "$RENDERED"
   kubectl -n "$NAMESPACE" rollout status deployment/property-catalog-dev --timeout=10m
   ```

3. Make no routing change. There is no OTLP routing rollback in this phase
   because the canary was never a traffic destination. Reconfirm that existing
   exporter, ingress, Service, and DNS configuration still points only to the
   pre-existing DEV collector. If the canary received traffic, stop and
   escalate; that is a boundary violation, not a normal rollback path.

4. Stop the unified consumer and producer/control workload while retaining the
   PVC and evidence.

   ```bash
   kubectl -n "$NAMESPACE" scale \
     deployment/property-catalog-dev-consumer --replicas=0
   kubectl -n "$NAMESPACE" scale \
     deployment/property-catalog-dev --replicas=0
   kubectl -n "$NAMESPACE" get pvc/property-catalog-dev-runtime
   ```

5. Keep unified API reads `off` in every API workload. Preserve the bootstrap
   JSON, schedule status, logs, Kafka lag, ClickHouse ledger/activation checks,
   and runtime PVC for diagnosis.

Do not apply the replica-one manifest again until a new reviewed attempt. Do
not delete the PVC, the six new tables, an old table, or any existing row as
part of rollback. Cleanup of the six new DEV tables, if explicitly approved,
is a separate stopped-workload reset under the inventory gate above; production
remains untouched.
