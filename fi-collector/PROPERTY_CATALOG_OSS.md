# Unified property catalog: OSS/local compatibility

This page defines the non-EE requirements for the unified property catalog. No
`ee.*` package is required. The authoritative Core transport and durability
contract is [PROPERTY_CATALOG_SEQUENCER.md](PROPERTY_CATALOG_SEQUENCER.md).

> [!IMPORTANT]
> A previous one-topic arrangement in which autoscaled collectors produced
> ordered envelopes directly is no longer production-safe. An OSS deployment
> is complete only if it provides a candidate topic, the singleton
> `fi-property-catalog-sequencer` with persistent state, a distinct ordered
> topic, and `fi-property-catalog-consumer`. The root `docker-compose.yml`
> supplies this complete local path; older one-topic Compose configurations do
> not satisfy the contract.

## Keep the two catalog paths separate

| Path | Producer switch | Consumer | Purpose |
| --- | --- | --- | --- |
| Unified property catalog | `FI_PROPERTY_CATALOG_MODE` | `fi-property-catalog-sequencer` then `fi-property-catalog-consumer` | System, eval, annotation, dataset, simulation, and span-attribute properties |
| Legacy span attributes | `FI_CATALOG_MODE` | `fi-catalog-consumer` | Pre-release span-attribute-only catalog |

Never enable both switches in one collector. Root OSS keeps
`FI_CATALOG_MODE` must remain `disabled`. `FI_PROPERTY_CATALOG_MODE=kafka` now
means candidate emission on collectors. The topic initializer in
`docker-compose.catalog-kafka.dev.yml` remains a separate, profile-gated legacy
harness and is not sufficient for the unified two-topic pipeline.

## Authoritative local arrangement

There are two deliberate arrangements:

1. The root `docker-compose.yml` is intended to be the one-command OSS path.
   It creates both unified topics, runs the singleton sequencer with an
   exclusive persistent volume and fixed owner identities, runs the ordered
   consumer, provisions isolated tables/identities, and starts the read-only
   workspace supervisor. The supervisor discovers local workspaces and
   projects from PostgreSQL, opens bounded revisions, and runs initial or
   incremental reconciliation. The sequencer, not collector replicas, admits
   a tenant only while its exact current fence is present.
2. The checked-in
   [`deploy/dev/property-catalog-docker` bundle](../deploy/dev/property-catalog-docker/README.md).
   is the stricter operator-driven qualification path. Despite its directory
   name, its catalog implementation is OSS-owned: it
   runs the Go producer and consumer from `fi-collector/` and the Python
   catalog code under
   `futureagi/tracer/services/clickhouse/v2/property_catalog/`. It does not
   import `ee.*`.

The qualification renderer has an explicit OSS format,
`futureagi.property-catalog-oss-dev-docker`. It requires a reviewed operator
image built from `Dockerfile.oss`, keeps `CLOUD_DEPLOYMENT` unset through
bootstrap and steady state, and otherwise retains the same isolated database,
Kafka, credential, and activation gates. Use
`deploy/dev/property-catalog-docker/config.oss.example.yaml` as the fail-closed
starting point. This path expects a qualification stack originally initialized
with `CH25_DATABASE=futureagi`; it must not be used to rename or repoint an
existing self-host database.

The root stack preserves the same boundaries with local defaults. All secrets,
Kafka retention/partitions, polling intervals, lifecycle walls, epoch,
projection, producer stream, source database, and isolated target database are
environment-overridable. The target database must be a safe lowercase
ClickHouse identifier and must differ from the source database. The bootstrap
scripts reject unsafe or source-identical names, malformed credentials, and
any target that does not contain exactly the six pinned tables.

The supervisor and sequencer share one canonical version-2 multi-tenant fence
registry. Before each cycle the supervisor reconciles the exact active
workspace inventory under the registry lock, and every workspace publication
preserves the other tenants. The registry is deliberately bounded to 256
workspaces; an over-bound inventory or corrupt registry fails the whole cycle
closed before any workspace lifecycle work starts. Its cross-language drain
lease codec accepts the reviewed 60-minute protocol ceiling; deployments may
configure a shorter lifecycle lease.

User-facing catalog reads remain `off` by default. That is intentional: a new
install has no workspace until onboarding, and forcing `read` before its first
activation would turn an otherwise healthy OSS page into a 503. The Kafka
producer, consumer, schema, and reconciliation catalog are on; read cutover is
performed only after activation and an explicit workspace allowlist.

## Data flow and ownership

| Component | Role |
| --- | --- |
| PostgreSQL | Authoritative relational sources for eval templates/configs, simulation eval configs, annotation labels, and dataset columns |
| Source ClickHouse | Authoritative spans and historical span attributes |
| Python reconciler/operator | Opens a bounded `building` revision, projects relational and historical span definitions/values, writes control evidence, and publishes the revision fence |
| `fi-collector` | Produces deterministic, unsequenced live span-attribute candidates globally after canonical writes; it owns no revision or ordered state |
| Candidate Kafka topic | Workspace-keyed bounded candidates from every collector replica |
| `fi-property-catalog-sequencer` | Sole owner of revision admission, sequence, stream, drain, receipts, dedupe, and ordered-envelope spool state |
| Ordered Kafka topic | Transactional ordered envelopes; distinct from the candidate topic |
| `fi-property-catalog-consumer` | Validates sequence and lease evidence, then writes catalog data and the delivery ledger |
| Isolated ClickHouse catalog | Six additive tables: definitions, attribute values, checkpoints, activations, deliveries, and source streams |
| Backend definition/value APIs | Remain off until an explicit admitted `shadow` or `read` configuration targets the isolated catalog |

Relational changes are reflected by the next successful reconcile, not by the
Go hot path. New span attributes are admitted by the sequencer only while the
workspace has a matching `building` fence. Candidates outside that rollout
are durably counted/skipped so one dark workspace cannot livelock the
singleton; reconciliation recovers them. Activation makes the completed
revision visible to definition and value readers.

## Repository-local verification

Run these commands from the repository root. They do not contact production.

Confirm the root Compose service contract, including the two distinct topics,
fixed sequencer identities, and exclusive persistent state:

```bash
docker compose -f docker-compose.yml config --services
docker compose -f docker-compose.yml config --format json > /tmp/futureagi-oss-compose.json
python3 - <<'PY'
import json

d = json.load(open("/tmp/futureagi-oss-compose.json"))
s = d["services"]
collector = s["fi-collector"]["environment"]
sequencer = s["fi-property-catalog-sequencer"]
sequencer_env = sequencer["environment"]
consumer = s["fi-property-catalog-consumer"]["environment"]
candidate = collector["FI_PROPERTY_CATALOG_KAFKA_TOPIC"]
ordered = sequencer_env["FI_PROPERTY_CATALOG_KAFKA_TOPIC"]
volumes = {
    (volume["source"], volume["target"], volume.get("read_only", False))
    for volume in sequencer["volumes"]
}
assert candidate != ordered
assert sequencer_env["FI_PROPERTY_CATALOG_CANDIDATE_KAFKA_TOPIC"] == candidate
assert consumer["FI_PROPERTY_CATALOG_KAFKA_TOPIC"] == ordered
assert ("property-catalog-sequencer-data", "/var/lib/property-catalog-sequencer", False) in volumes
assert ("fi-collector-data", "/var/lib/property-catalog-control", True) in volumes
assert sequencer_env["FI_PROPERTY_CATALOG_SEQUENCER_TRANSACTIONAL_ID"]
assert sequencer_env["FI_PROPERTY_CATALOG_CANDIDATE_KAFKA_CONSUMER_GROUP"]
assert sequencer_env["FI_PROPERTY_CATALOG_CANDIDATE_KAFKA_INSTANCE_ID"]
assert s["backend"]["environment"]["PROPERTY_CATALOG_READ_MODE"] == "off"
print({"candidate_topic": candidate, "ordered_topic": ordered, "sequencer": True, "read_mode": "off"})
PY
```

The check must pass with distinct candidate and ordered topics, fixed sequencer
ownership, its exclusive volume, and `read_mode=off` until explicit cutover.

Start only the catalog dependencies in a disposable Compose project when doing
a first-boot qualification. Never run `down -v` against an existing OSS
project:

```bash
docker compose -p futureagi-catalog-proof -f docker-compose.yml up -d \
  postgres clickhouse redis \
  property-catalog-kafka-volume-init property-catalog-runtime-volume-init \
  property-catalog-postgres-bootstrap \
  property-catalog-clickhouse-bootstrap \
  property-catalog-kafka property-catalog-topic-init \
  fi-collector fi-property-catalog-sequencer \
  fi-property-catalog-consumer property-catalog-supervisor
docker compose -p futureagi-catalog-proof -f docker-compose.yml ps
```

The volume initialization and bootstrap jobs must exit `0`; Kafka, collector,
sequencer, consumer, and supervisor must remain running. Before any workspace
exists the sequencer may have no fence. Do not create an empty fence file: the
supervisor publishes the first canonical fence after onboarding creates a
workspace and project.

Build and test all three Go processes:

```bash
cd fi-collector
go build ./cmd/fi-collector ./cmd/fi-property-catalog-sequencer ./cmd/fi-property-catalog-consumer
go test ./...
docker build -t futureagi/fi-collector:property-catalog-oss-audit .
cd ..
```

Run the backend catalog contract, lifecycle, and definition/value API suite:

```bash
cd futureagi
uv sync --frozen --group dev
uv run pytest -q \
  tracer/services/clickhouse/v2/test_catalog_dev_schema.py \
  tracer/services/clickhouse/v2/test_property_catalog_schema_contract.py \
  tracer/tests/test_property_catalog_*.py \
  tracer/tests/test_unified_property_catalog_*.py \
  tracer/tests/test_attribute_catalog_dev_snapshot.py \
  tfc/temporal/schedules/tests/test_property_catalog.py
cd ..
```

The stricter live qualification still follows every preflight, credential,
isolated-schema, topic, bootstrap, status, and activation gate in the canonical
DEV Docker guide. A running broker by itself is not an end-to-end property
catalog.
