# Observed attributes in OSS and local development

The root Compose stack runs one Kafka observation topic and one consumer:

`fi-collector → futureagi.observed-attributes.v1 → fi-property-catalog-consumer`

The consumer writes `observed_attribute_keys` and `observed_attribute_values`
in the isolated `property_catalog` ClickHouse database. Definitions and values
from relational metadata keep their native readers. There is no sequencer,
activation, epoch, revision, projection, Python supervisor or catalog schedule.

## Suggestion semantics

Ordinary historical values may remain after a span changes. A suggestion is not
proof that a current trace matches it; filtering still uses the source records.
Strings, numbers and booleans retain their types. Current eval/annotation choices
and dataset/prompt/simulation definitions remain native, permission-checked reads.

Keys preserve their exact spelling (up to 4 KiB UTF-8). Selectable string values
allow 16 KiB raw UTF-8, or 4 KiB for array members; objects keep discoverable keys
without fabricated scalar values. Extraction defaults to 128 keys and 256 array
members per span. `FI_OBSERVED_CATALOG_MAX_KEYS_PER_SPAN` and
`FI_OBSERVED_CATALOG_MAX_ARRAY_MEMBERS_PER_SPAN` tune those per-span resource
budgets (ceilings 4096/16384). Reaching a budget is logged as an extraction gap,
not a complete index. There is no workspace/project allowlist or tenant cap.

The UI uses the existing read-only POST contract for dashboard `metrics` and
`filter_values`, keeping long names/cursors out of URLs. GET remains supported
for existing clients. Both methods enforce the same workspace permissions.

Explicit privacy erasure requires coordinated quiescing/draining of affected
writers, source erasure, derived-index/cache purge, and retention checks for
Kafka/spool history before resuming. Otherwise a replay can restore suggestions.
This feature does not perform automatic destructive source or catalog cleanup.

## Run the local stack

From the repository root:

```sh
docker compose up -d --build
```

The installer (`bin/install` or `bin/install.ps1`) uses the same topology.
For development, layer `docker-compose.dev.yml` over the root file after the
normal application bootstrap. The dev overlay skips application migrations;
use the standard E2E harness for a fresh installation test.

Kafka, topic creation and the two-index bootstrap have explicit startup
dependencies. The consumer image is built from this checkout and also contains
`fi-observed-catalog-backfill`. Collector and consumer must use the same image.

## Configuration

See the repository-root `.env.example` for local defaults.

| Setting | Default / purpose |
| --- | --- |
| `PROPERTY_CATALOG_DATABASE` | `property_catalog`; must differ from the source-span database |
| `PROPERTY_CATALOG_CONSUMER_PASSWORD` | Local writer password |
| `PROPERTY_CATALOG_API_PASSWORD` | Local reader password |
| `PROPERTY_CATALOG_KAFKA_PORT` | `29092`, bound to host loopback |
| `OBSERVED_CATALOG_KAFKA_TOPIC` | `futureagi.observed-attributes.v1` |
| `OBSERVED_CATALOG_KAFKA_GROUP` | `futureagi.observed-attributes.consumer.v1` |
| `OBSERVED_CATALOG_MAX_SPOOL_FILES` | `10000` |
| `OBSERVED_CATALOG_MAX_SPOOL_BYTES` | `536870912` |

Collector/consumer process variables use the `FI_OBSERVED_CATALOG_` prefix.
The producer uses `MODE=kafka`, `KAFKA_BROKERS`, `KAFKA_TOPIC`, and
`SPOOL_DIR`. The consumer uses `KAFKA_BROKERS`, `KAFKA_TOPIC`, `KAFKA_GROUP`
and `CH_URL`, `CH_DATABASE`, `CH_USERNAME`, `CH_PASSWORD`.

Old `FI_PROPERTY_CATALOG_*`, candidate/ordered-topic and lifecycle settings do
not configure this transport. Keep the fresh observation topic/group defaults;
never point the new consumer at historical catalog events or offsets.

The backend uses `PROPERTY_CATALOG_CH_HOST`, `PROPERTY_CATALOG_CH_PORT`
(native TCP, default 9000), `PROPERTY_CATALOG_CH_USER`, and
`PROPERTY_CATALOG_CH_PASSWORD`. Compose supplies the dedicated reader identity
with `readonly=2`, which permits per-request query settings without permitting
writes. Bootstrap binds password
parameters with ClickHouse escaped-text encoding; `+/=`, quotes, backslashes and
Unicode secrets are supported.

## Storage and recovery

After the source insert returns, the collector synchronously enqueues and
fsyncs observations to its local spool. Replay publishes them to Kafka; the
consumer commits offsets only after both index writes succeed. Duplicate
delivery is supported by the min/max observation identities. The existing
source writer's asynchronous acknowledgement behavior is unchanged.

There is no pending-ready journal. A failure between the source insert and
local enqueue, a full/lost spool, or expired Kafka history can leave missing
observations. Repair a bounded source range with `fi-observed-catalog-backfill`;
ordinary restarts do not launch historical scans.

For a span backfill, set `PROJECT_ID` to one current project UUID and `SINCE` /
`UNTIL` to an inclusive/exclusive RFC3339 range. Supply read-only source
credentials in `FI_PG_DSN` and `FI_OBSERVED_BACKFILL_CH_URL`,
`FI_OBSERVED_BACKFILL_CH_DATABASE`, `FI_OBSERVED_BACKFILL_CH_USERNAME`,
`FI_OBSERVED_BACKFILL_CH_PASSWORD`. The PG connection verifies current project
ownership; it is not the catalog writer.

Preview first; without `--apply` the command does not publish observations:

```sh
docker compose run --rm --no-deps \
  --entrypoint /usr/local/bin/fi-observed-catalog-backfill \
  -e FI_PG_DSN -e FI_OBSERVED_BACKFILL_CH_URL \
  -e FI_OBSERVED_BACKFILL_CH_DATABASE -e FI_OBSERVED_BACKFILL_CH_USERNAME \
  -e FI_OBSERVED_BACKFILL_CH_PASSWORD \
  fi-property-catalog-consumer \
  --source spans --project "$PROJECT_ID" --since "$SINCE" --until "$UNTIL" \
  --page-size 64 --max-pages 100 --page-delay 100ms
```

To apply the reviewed scope, repeat with `--apply --checkpoint /backfill/progress.json`
and mount an operator-owned writable directory at `/backfill`. The container
runs as UID/GID 65532, so grant that identity access to the checkpoint directory.
Keep the checkpoint across bounded invocations and resume the same scope.

For historical catalog rows, select `--source legacy` with an explicit
`--legacy-epoch`, `--legacy-revision`, and `--legacy-build`; omit time-range
flags. Apply additionally requires `--verified-legacy`. This reads the selected
old data and publishes new observations; it does not mutate or delete the old
catalog. Confirm these flags against the shipped binary's `--help`.

The collector keeps both its observation spool and the existing span dead-letter
file on `fi-collector-data`. Kafka keeps `property-catalog-kafka-data`.
Removing obsolete service/volume declarations does not remove existing physical
volumes. Preserve old sequencer state, Kafka topics, ClickHouse tables and rows
during upgrades. Retire only the inventoried old lifecycle processes and
schedules as a coordinated deployment step; do not use broad orphan/volume
cleanup to migrate.

## Schema

Local bootstrap applies
`futureagi/tracer/services/clickhouse/v2/observed_catalog/schema.sql` directly
to the isolated database. It grants the writer SELECT/INSERT and the reader
SELECT on the two new tables only. Before credentials or grants are changed,
a read-only metadata gate checks the actual column names/types, min/max timestamp
aggregates, full sorting/primary identity (including `value_json`), partitioning,
constraints and absence of TTL. An incompatible pre-existing table fails startup;
`IF NOT EXISTS` does not silently accept it or rewrite existing data.
No PostgreSQL bootstrap or source-table grant is required.

The local bootstrap creates plain AggregatingMergeTree tables. For replicated
deployments, render the separate schema through the existing
`apply_schema_rewriter.py` with the deployment's cluster/Keeper topology.
Do not route it through the source schema runner or modify historical numbered
SQL files. Existing catalog tables may coexist with the new indexes.

## Validate

Service-free deployment checks:

```sh
python3 -m unittest discover -s deploy/tests -p test_observed_catalog_compose.py -v
```

For bounded ClickHouse/Kafka/PostgreSQL and OTLP HTTP integration without the
root application stack, use the three-service fixture and explicit loopback
commands in [TESTING.md](../TESTING.md#observed-catalog-integration-isolated-dependencies).
The CI job uses this fixture, repeats bootstrap, and rejects missing or skipped
integration proofs. Three-replica qualification is optional and separate.

Use the existing `fi-collector` Go suite, backend `futureagi/bin/test`, and
`bin/e2e` Observe flows for runtime validation. E2E builds backend and collector
from source with `bin/e2e build backend` and `bin/e2e build collector`; set
`FUTURE_AGI_VERSION=e2e-local E2E_FI_COLLECTOR_VERSION=e2e-local` for startup.
The E2E stack explicitly includes the consumer and maps Kafka to port 29093.

Check project ownership and ports before starting a harness alongside another
checkout. `futureagi/bin/test down` deletes its test volumes; do not use it to
make room for another task.
