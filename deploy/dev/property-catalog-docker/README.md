# Unified property catalog on the DEV Docker host

This is the canonical end-to-end DEV path for
`FI_PROPERTY_CATALOG_MODE=kafka`. The rendered producer always sets
`FI_CATALOG_MODE=disabled`; do not combine the unified and legacy modes. The
collector's [`CATALOG_DEV.md`](../../../fi-collector/CATALOG_DEV.md) and its
optional `property-catalog.dev.span-attribute-catalog.v1` topic covers the legacy
span-attribute catalog, not this path.

This bundle renders a new, isolated Compose project for the supplied DEV VM.
It does not replace, restart, reconfigure, or depend on any current Compose
service. It attaches new containers to the two existing external DEV networks.
The renderer itself performs local file validation only: it never invokes
Docker, SSH, Kafka, PostgreSQL, ClickHouse, or any other network client.

The default workload contains exactly two long-running processes:

- one current `fi-collector` hot producer from the reviewed collector image;
- one current `fi-property-catalog-consumer` from that same reviewed image,
  pinned to the dedicated topic/group.

The collector runtime must be an exact local Docker image ID in
`sha256:<64 lowercase hex>` form. Tags and repository digests are rejected.
Producer and consumer execute the image-native
`/usr/local/bin/fi-collector` and
`/usr/local/bin/fi-property-catalog-consumer`; neither binary is bind-mounted
from the host.

There is also one opt-in `operator` profile. It runs
`python manage.py ch25_property_catalog_dev_rollout` in the configured current
backend image and exits. It is not a Temporal worker and cannot register a
schedule. With no extra argument it performs the command's zero-I/O dry run.

The renderer accepts two explicit runtime profiles. The existing
`futureagi.property-catalog-dev-docker` format retains the DEV-cloud-shaped
operator. The `futureagi.property-catalog-oss-dev-docker` format requires a
reviewed `property-catalog-oss-*` operator image built from `Dockerfile.oss` and keeps
both `CLOUD_DEPLOYMENT` and `PROPERTY_CATALOG_DEV_CLOUD_DEPLOYMENT` unset.
This stricter qualification bundle is independent of the root OSS stack,
which now default-enables its own isolated unified Kafka/catalog pipeline.

## Fixed safety boundary

- Environment is exactly `development`. Cloud deployment is exactly `DEV` for
  the DEV-cloud profile and exactly unset for the OSS profile; the renderer
  rejects a mismatch.
- The source database is exactly `futureagi`.
- The only writable catalog target is the configured dedicated database. Its
  name must be a safe lowercase ClickHouse identifier, differ from the source,
  and must not be the production `property_catalog` database.
- Public property-catalog reads, the legacy catalog, snapshot reads, periodic
  reconciliation, and OTLP traffic authorization are all forced off.
- Sentry, OpenTelemetry export, deployment telemetry, and integrations are
  disabled in the one-shot operator; its only runtime routes are the reviewed
  internal DEV PostgreSQL and ClickHouse endpoints.
- The producer listens at `127.0.0.1:4317` and `127.0.0.1:4318` inside its
  container. No service has `ports`, `expose`, host networking, or a route.
- The Compose project uses existing external networks. It creates no network,
  volume, image, or current-service dependency and has `pull_policy: never`.
  Both Go services use the same exact reviewed local collector image ID.
  The collector carrier image's declared `/var/lib/fi-collector` volume is
  explicitly shadowed with bounded tmpfs, preventing an anonymous Docker
  volume outside the reviewed runtime bind.
- Source, control-writer, consumer-writer, and ledger-reader identities are
  separate. Credential values live only in purpose-built mode-`0600` env
  files. The renderer emits references, never values.
- The producer and operator share exactly one physical host runtime directory.
  The revision fence, drain proof, and producer retirement paths are fixed to:

  - `/var/lib/property-catalog-runtime/catalog-spool/revision-fence-v2.json`
  - `/var/lib/property-catalog-runtime/catalog-spool/producer-drain-proof-v2.json`
  - `/var/lib/property-catalog-runtime/catalog-spool/producer-state-retirements-v1.json`

- The operator always has `SERVICE_TYPE=bootstrap`,
  `STARTUP_DB_MUTATION_MODE=operator`, and `NO_STARTUP_DB_MUTATIONS=true`.
  Explicit `--execute` remains the only path to the reviewed rollout writes.

The generated topic and group are deterministic and dedicated:

```text
futureagi.dev.property-catalog.<deployment_id>
futureagi.dev.property-catalog.consumer.<deployment_id>
```

For `deployment_id: kartik-0815a`, the example target is
`property_catalog_dev_kartik_0815a`, the topic is
`futureagi.dev.property-catalog.kartik-0815a`, and the group is
`futureagi.dev.property-catalog.consumer.kartik-0815a`.

## Host layout

The renderer accepts only `/home/ubuntu/property-catalog-<deployment_id>` as the host
root. For the example configuration, prepare this exact layout:

```text
/home/ubuntu/property-catalog-kartik-0815a/
├── private/
│   ├── producer.env
│   ├── operator-runtime.env
│   ├── operator-postgres.env
│   ├── operator-source-clickhouse.env
│   ├── operator-target-clickhouse.env
│   ├── consumer-write-clickhouse.env
│   └── consumer-ledger-clickhouse.env
└── runtime/
    ├── cache/
    ├── catalog-spool/
    ├── home/
    └── span-dead-letter/
```

Prepare directories without following symlinks:

```bash
DEPLOYMENT_ROOT=/home/ubuntu/property-catalog-kartik-0815a
install -d -m 0700 "$DEPLOYMENT_ROOT" "$DEPLOYMENT_ROOT/private"
sudo install -d -o ubuntu -g 65532 -m 0770 \
  "$DEPLOYMENT_ROOT/runtime" \
  "$DEPLOYMENT_ROOT/runtime/cache" \
  "$DEPLOYMENT_ROOT/runtime/home" \
  "$DEPLOYMENT_ROOT/runtime/span-dead-letter"
sudo install -d -o 65532 -g 65532 -m 0700 \
  "$DEPLOYMENT_ROOT/runtime/catalog-spool"
```

Separately prove that the reviewed image ID is already present. The renderer
does not invoke Docker, so this check is an explicit operator preflight:

```bash
COLLECTOR_IMAGE_ID=sha256:REPLACE_WITH_64_LOWERCASE_HEX
test "$(docker image inspect --format '{{.Id}}' "$COLLECTOR_IMAGE_ID")" = \
  "$COLLECTOR_IMAGE_ID"
```

Put that exact ID in `images.collector_runtime`. `pull_policy: never` ensures
Compose cannot substitute a pulled image. `--host-preflight` validates only
the dedicated directories and private credential files; it does not inspect
Docker or accept host binary overlays.

## Purpose-built private env files

Create each file with mode `0600`, owned by the invoking `ubuntu` account, one
unquoted `KEY=value` per line, and a final newline. The host validator rejects
extra keys, duplicate keys, interpolation, placeholders, symlinks, incorrect
ownership, and loose permissions. It reads values only to compare identities;
it never prints or returns them.

| File | Exact keys | Scope |
| --- | --- | --- |
| `producer.env` | `FI_PG_WRITE`, `FI_CH_USERNAME`, `FI_CH_PASSWORD` | Collector auth prerequisite plus the server-enforced read-only source ClickHouse identity; the producer is unrouted |
| `operator-runtime.env` | `SECRET_KEY` | Fresh random secret used only to initialize the isolated one-shot Django operator; never reuse the app service secret |
| `operator-postgres.env` | `PGBOUNCER_HOST`, `PGBOUNCER_PORT`, `PG_DB`, `PG_USER`, `PG_PASSWORD` | Purpose-built PostgreSQL SELECT identity used by the bounded backfill/reconciler |
| `operator-source-clickhouse.env` | `CH25_USER`, `CH25_PASSWORD` | Server-enforced read-only access to existing `futureagi` tables |
| `operator-target-clickhouse.env` | `PROPERTY_CATALOG_DEV_WRITE_CH_USER`, `PROPERTY_CATALOG_DEV_WRITE_CH_PASSWORD` | Schema/backfill/state writes only in the new isolated target; database creation is mediated through `default` |
| `consumer-write-clickhouse.env` | `FI_PROPERTY_CATALOG_CH_USERNAME`, `FI_PROPERTY_CATALOG_CH_PASSWORD` | INSERT only into `property_definition_catalog`, `span_attribute_value_catalog`, and `property_catalog_deliveries` in the isolated target |
| `consumer-ledger-clickhouse.env` | `FI_PROPERTY_CATALOG_LEDGER_CH_USERNAME`, `FI_PROPERTY_CATALOG_LEDGER_CH_PASSWORD` | SELECT only on `property_catalog_source_streams`, `property_catalog_checkpoints`, `property_catalog_activations`, and `property_catalog_deliveries` in the isolated target |

The source user and all three target ClickHouse users must be distinct. The
producer and operator source usernames must match. `PG_DB` and `PG_USER` must
match the frozen provenance values in the reviewed non-secret configuration.
Do not point any env file at a production endpoint or reuse the broad backend
`.env` file.

Although the current collector calls its mandatory PostgreSQL DSN
`FI_PG_WRITE`, this deployment publishes no listener and authorizes no OTLP
traffic. Do not send it traffic during the historical backfill phase.

## Render and validate offline

Copy `config.example.yaml` to a reviewed file outside the checkout and replace
every `REPLACE_*` value. The example already carries Kartik's exact DEV
organization, workspace, dense/sparse test projects, 365-day window, and
isolated target name.

For an OSS-built operator, copy `config.oss.example.yaml` instead and build the
referenced operator image from `Dockerfile.oss`. The qualification source must
have been initialized with `CH25_DATABASE=futureagi`; do not rename or repoint
an existing self-host database merely to satisfy this qualification contract.

```bash
CONFIG=/home/ubuntu/property-catalog-kartik-0815a/reviewed-config.yaml
COMPOSE=/home/ubuntu/property-catalog-kartik-0815a/compose.yaml
PYTHON=futureagi/.venv/bin/python

"$PYTHON" deploy/dev/property-catalog-docker/render.py \
  --config "$CONFIG" --validate-only
"$PYTHON" deploy/dev/property-catalog-docker/render.py \
  --config "$CONFIG" --host-preflight --output "$COMPOSE"
"$PYTHON" deploy/dev/property-catalog-docker/render.py \
  --config "$CONFIG" --host-preflight --validate-rendered "$COMPOSE"
```

The last command must report two long-running services, one dry-run operator,
and no published ports. `docker compose config` is an additional syntax check,
not a replacement for the checked-in validator:

```bash
docker compose -f "$COMPOSE" config --quiet
```

## Dedicated Kafka topic

The renderer never administers Kafka and the Compose project never changes the
current broker service. Before starting the new containers, use separately
approved Kafka administration to prove that the exact generated topic is
dedicated to this deployment. On a new DEV-only topic, create six partitions,
replication factor one, 72-hour retention, and a 1 MiB message limit. Do not
delete, resize, or reconfigure any existing topic.

For the existing standalone DEV broker, an operator must first inspect and then
create only the missing exact topic. The `apache/kafka-native` broker image does
not include the administration scripts, so use the separately installed
`apache/kafka` CLI image on the broker's existing Docker network:

Start only the broker infrastructure if it is not already running. The legacy
topic initializer is profile-gated and is not started by this command:

```bash
docker compose \
  -f fi-collector/docker-compose.catalog-kafka.dev.yml \
  up -d volume-init kafka
```

```bash
TOPIC=futureagi.dev.property-catalog.kartik-0815a
KAFKA_NETWORK=property-catalog-dev
KAFKA_CLI_IMAGE=apache/kafka:4.1.0
KAFKA_BROKER=property-catalog-kafka-dev:9092

docker run --rm --network "$KAFKA_NETWORK" "$KAFKA_CLI_IMAGE" \
  /opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server "$KAFKA_BROKER" --describe --topic "$TOPIC"

# Run only after the describe proves this exact new topic is absent.
docker run --rm --network "$KAFKA_NETWORK" "$KAFKA_CLI_IMAGE" \
  /opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server "$KAFKA_BROKER" --create \
  --topic "$TOPIC" --partitions 6 --replication-factor 1 \
  --config retention.ms=259200000 --config max.message.bytes=1048576

# This successful describe is a mandatory pre-start gate. A missing topic can
# remain latent until the first hot drain proof even when the initial backfill
# itself writes directly to the isolated catalog tables.
docker run --rm --network "$KAFKA_NETWORK" "$KAFKA_CLI_IMAGE" \
  /opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server "$KAFKA_BROKER" --describe --topic "$TOPIC"
```

The consumer creates only its dedicated group through normal consumption; it
never reuses an application or previous test group.

## Bootstrap order

1. Re-run the host preflight and rendered-file validation.
2. Prove the exact target database is absent and snapshot existing DEV database
   and table inventories using read-only queries.
3. Prove the exact dedicated topic exists and has the reviewed configuration.
4. Start the new unrouted producer and consumer. The producer intentionally
   tolerates an empty pre-bootstrap runtime directory. The consumer may restart
   until the operator creates the isolated six-table schema.
5. Run the operator with no argument. This validates the request and performs
   no database or network I/O.
6. Run `--execute` only after the preceding evidence is reviewed. This is the
   sole action that may create/backfill the isolated target. It must never be
   pointed at `futureagi` as a target.
7. Run `--status`, check Kafka lag/ledger evidence, and compare the before/after
   inventory. Existing database/table definitions must be unchanged.

```bash
docker compose -f "$COMPOSE" up -d \
  property-catalog-producer property-catalog-consumer

# Zero-I/O plan/dry run: no command argument is passed.
docker compose -f "$COMPOSE" --profile operator run --rm \
  property-catalog-operator

# The only authorized catalog mutation path; target database must be new.
docker compose -f "$COMPOSE" --profile operator run --rm \
  property-catalog-operator --execute --initial-backfill-wall-ms 1200000

docker compose -f "$COMPOSE" --profile operator run --rm \
  property-catalog-operator --status
```

Pass the explicit 1,200,000 ms initial-backfill wall allowance on the first
execution for the reviewed 33-project, 12-month scope. The allowed ceiling is
1,740,000 ms so the corresponding initial-build lease retains at least 60
seconds of headroom. The reservation lease is immutable: retrying a short
default execution cannot lengthen its lease, and an expired incomplete revision
must remain fail-closed under a fresh suffix-isolated DEV catalog.

The expected isolated target contains exactly these six tables:

1. `property_definition_catalog`
2. `span_attribute_value_catalog`
3. `property_catalog_checkpoints`
4. `property_catalog_activations`
5. `property_catalog_deliveries`
6. `property_catalog_source_streams`

Qualification is not complete merely because containers are running. Require:

- producer and consumer logs contain no gap, poison, credential, or provenance
  failures;
- Kafka group lag reaches zero for the exact dedicated group;
- the delivery ledger and activation row agree with the shared-volume fence and
  drain proof;
- sparse and dense Kartik queries meet the semantic and latency matrix; and
- the before/after inventory proves that no existing DEV table changed.

Do not enable public reads or schedule registration in the bootstrap Compose
file. After the exact activation is qualified, the separately rendered
steady-state overlay below may add only the dedicated reconciliation worker
and its opt-in registrar. Public reads remain a separate backend deployment
decision.

## Post-activation steady state

The initial operator credentials are intentionally removable after a one-shot
qualification. If this catalog is approved for ongoing DEV testing, first run
`provision_existing_steady.py` without `--execute`. It proves that the target
already contains exactly the six catalog tables, that the supplied bootstrap
activation is the active one, that no open or draining lifecycle reservation
remains, and that the scoped identities/files are absent. A failed or expired
reservation is rejected: create a fresh isolated DEV catalog instead of
repairing ledger state in place.
Execution only re-creates the source SELECT, catalog control-writer, catalog
consumer-writer, catalog-ledger-reader, and PostgreSQL SELECT identities. It
does not create or alter a table and does not write a source or catalog row.

```bash
ACTIVATION_SHA256='<exact 64-character active activation digest>'
PROPERTY_CATALOG_DATABASE='th7247_catalog_dev_kartik_0817j'
ROOT=/home/ubuntu/property-catalog-kartik-0816h

python deploy/dev/property-catalog-docker/provision_existing_steady.py \
  --suffix 0816h \
  --target-database "$PROPERTY_CATALOG_DATABASE" \
  --bootstrap-activation-sha256 "$ACTIVATION_SHA256"

FI_PROPERTY_CATALOG_STEADY_ACK=FI_PROPERTY_CATALOG_ENABLE_EXISTING_DEV_CATALOG_STEADY_STATE \
python deploy/dev/property-catalog-docker/provision_existing_steady.py \
  --suffix 0816h \
  --target-database "$PROPERTY_CATALOG_DATABASE" \
  --bootstrap-activation-sha256 "$ACTIVATION_SHA256" \
  --validity-days 7 \
  --execute
```

Next render the second-stage overlay from the exact bootstrap Compose file.
The renderer adds one worker polling only a workspace-isolated queue named
`property_catalog_dev_sidecar_<workspace UUID without hyphens>`, with one
activity/workflow slot and one registrar behind the `registrar` profile. This
prevents a sidecar for one DEV workspace from consuming another workspace's
reconciliation activity.
The worker keeps ordinary per-query limits unchanged but admits a reviewed
1,200,000 ms aggregate wall and matching immutable lease for a scheduled tick
that is promoted to a 34-project full repair. Temporal overlap remains `SKIP`,
so a long repair cannot race a later two-minute tick.
It preserves loopback-only OTLP, `PROPERTY_CATALOG_READ_MODE=off`, the source
database, the isolated target, credential split, runtime bind, and all
container hardening.

```bash
python deploy/dev/property-catalog-docker/render_existing_steady.py \
  --base-compose "$ROOT/compose.yaml" \
  --bootstrap-activation-sha256 "$ACTIVATION_SHA256" \
  --output "$ROOT/steady.compose.yaml"

docker compose -f "$ROOT/compose.yaml" -f "$ROOT/steady.compose.yaml" \
  config --quiet
docker compose -f "$ROOT/compose.yaml" -f "$ROOT/steady.compose.yaml" \
  up -d property-catalog-producer property-catalog-consumer \
  property-catalog-control
docker compose -f "$ROOT/compose.yaml" -f "$ROOT/steady.compose.yaml" \
  --profile registrar run --rm property-catalog-registrar
```

Before declaring steady state healthy, trigger or wait for one 120-second
revision and prove a new active activation, zero Kafka lag, no gap/poison
ledger rows, and unchanged source-table fingerprints. Stop the worker and
revoke the short-lived identities after the review window if the DEV rollout
is not being kept.

### Hand off hot writes to the live DEV collector

The bootstrap producer is deliberately unrouted and therefore cannot observe
normal application OTLP traffic. After activation, render a second fail-closed
overlay for the existing `futureagi` Compose project. It copies only the
reviewed unified-catalog mode, workspace, stream, fence/spool, Kafka topic, and
exact local collector image from the bootstrap Compose. It does not copy the
bootstrap source/PostgreSQL credentials or listener addresses, and adds no
port. The live collector retains its existing canonical ClickHouse and API-key
configuration from the base application Compose files.

```bash
python deploy/dev/property-catalog-docker/render_live_collector_handoff.py \
  --bootstrap-compose "$ROOT/compose.yaml" \
  --output "$ROOT/live-collector-handoff.compose.yaml"

docker compose \
  -f /home/ubuntu/future-agi/docker-compose.yml \
  -f /home/ubuntu/future-agi/docker-compose.dev.yml \
  -f "$ROOT/live-collector-handoff.compose.yaml" config --quiet
```

Before recreating the live collector, stop only the unrouted bootstrap
`property-catalog-producer`; never run two producers with one stream ID and
spool. Then recreate only `fi-collector` with the rendered overlay. Do not stop
the consumer or control worker. Verify canonical trace acceptance, Kafka lag
zero, a matching delivery-ledger row, and active-catalog visibility for one
fresh unique marker before treating the handoff as complete.

## Stop and remove only the new project

```bash
docker compose -f "$COMPOSE" down
```

Do not add `--volumes`, do not remove either external network, and do not stop
or recreate the current backend, ClickHouse, PostgreSQL, PgBouncer, Kafka, or
collector services. The runtime directory and dedicated topic are retained as
rollout evidence until an explicit retirement decision.

## Offline tests

```bash
futureagi/.venv/bin/python -m unittest discover -s deploy/dev/property-catalog-docker \
  -p 'test_*.py' -v
```

The tests mutate in-memory documents and temporary local files only. They do
not launch containers or contact any external service.
