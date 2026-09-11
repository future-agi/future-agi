# Legacy span-attribute catalog: development qualification

> [!CAUTION]
> This guide covers the legacy `FI_CATALOG_MODE` span-attribute catalog only.
> It is not an end-to-end guide for the unified property catalog. For
> `FI_PROPERTY_CATALOG_MODE`, use the canonical
> [candidate/sequencer Core contract](PROPERTY_CATALOG_SEQUENCER.md). Any DEV
> Docker bundle must implement that two-topic, singleton-owner topology.

Do not enable `FI_CATALOG_MODE` and `FI_PROPERTY_CATALOG_MODE` together. The
two paths use different consumers and wire contracts:

| Path | Producer mode | Consumer | Topic | End-to-end DEV guide |
| --- | --- | --- | --- | --- |
| Legacy span attributes | `FI_CATALOG_MODE` | `fi-catalog-consumer` | `property-catalog.dev.span-attribute-catalog.v1` | This document |
| Unified properties | `FI_PROPERTY_CATALOG_MODE` | `fi-property-catalog-consumer` | `futureagi.dev.property-catalog.<deployment_id>` | [`deploy/dev/property-catalog-docker/`](../deploy/dev/property-catalog-docker/README.md) |

This catalog is additive. Its runtime is disabled by default, and production
must remain disabled until the development evidence and code are reviewed.
Neither ingestion mode may `ALTER`, `UPDATE`, `DELETE`, or insert into `spans`
or another pre-existing table.

## Storage and replication

Schema files `025_span_attribute_catalog.sql` and
`026_span_attribute_catalog_delivery.sql` create six independent tables. The
dev harness applies their six pinned `CREATE TABLE` statements directly to a
dedicated development catalog database; it does not invoke the normal schema
runner or write `schema_versions`.

Production currently has one shard and three replicas. A future, separately
authorized production schema job must use the low-level schema runner with:

```text
--replicated --cluster cluster \
--zk-table-path-prefix /clickhouse/tables/ch25
```

That rewrites the two aggregate tables to
`ReplicatedAggregatingMergeTree` and the four state tables to
`ReplicatedReplacingMergeTree`, using one Keeper path per table. Do not use the
Django management wrapper for production: it does not expose these flags.

## Legacy ingestion modes

`FI_CATALOG_MODE` accepts `disabled`, `direct`, or `kafka`. Enabled modes also
require `FI_CATALOG_ENVIRONMENT=development`, a non-zero epoch, a canonical
producer-stream UUID, and a dedicated durable spool directory.

This section does not configure `FI_PROPERTY_CATALOG_MODE`. Keep that setting
disabled throughout this legacy qualification.

- `direct`: the collector writes its durable project-scoped v3 envelopes to
  the three catalog ingestion tables through a distinct, catalog-only
  ClickHouse identity. The complete envelope has one deadline of at most 10s.
- `kafka`: the collector has broker/topic settings only. A standalone
  `fi-catalog-consumer` uses its own catalog INSERT identity and a separate
  delivery-ledger SELECT identity. It commits a Kafka offset only after key,
  value, and ledger writes succeed. Assignment/rebalance reloads the durable
  sequence chain before fetching.

Both modes share the same codec, bounded builder, outer WAL, v3 envelope,
project split, hash chain, chunk limits, and delivery ledger. Run them in
different epochs; never enable both for the same producer process.

## Development Kafka for the legacy path

The Compose file is broker infrastructure, not an application stack. A plain
`up` starts only the reusable single-node broker; it deliberately does not
create the legacy topic:

```sh
docker compose -f fi-collector/docker-compose.catalog-kafka.dev.yml up -d
```

The broker uses Docker network `property-catalog-dev`. Container clients must
join that network and use `property-catalog-kafka-dev:9092`; the advertised
`127.0.0.1:29092` listener is only for clients running directly on the host.

Only for the legacy `FI_CATALOG_MODE` qualification, create its six-partition
topic with the explicit profile and acknowledgement:

```sh
env \
  PROPERTY_CATALOG_LEGACY_SPAN_ATTRIBUTE_ACK=PROPERTY_CATALOG_ACK_LEGACY_SPAN_ATTRIBUTE_ONLY \
  docker compose \
    -f fi-collector/docker-compose.catalog-kafka.dev.yml \
    --profile legacy-span-attribute-catalog up -d
```

The profile fails before topic creation unless the acknowledgement is exact.
Do not use this topic for `FI_PROPERTY_CATALOG_MODE`; the unified guide requires
an explicitly created and verified deployment-scoped topic and starts both Go
processes.

Its published external port is loopback-only. The broker is plaintext,
replication-factor 1, and marked `production-use: forbidden`. It is for
equivalence/fault testing only. A later production proposal requires a separate
managed multi-broker service with TLS, authentication, RF=3, and measured
capacity; it must not reuse the Mimir demo broker.

The broker Compose file does not supervise application processes. Run the
legacy consumer as a separate service from the exact candidate image and give
it a durable restart policy. A brand-new topic/group may start once with
`--start-sequence-one-only`; every restart must seed from the delivery ledger:

```text
fi-catalog-consumer --seed-from-delivery-ledger
```

The process requires `FI_CATALOG_ENVIRONMENT=development`, Kafka broker/topic/
group settings, the catalog-only ClickHouse INSERT identity in `FI_CATALOG_CH_*`,
and a separate SELECT-only delivery-ledger identity in
`FI_CATALOG_LEDGER_CH_*`. The ledger URL and database must exactly match the
catalog destination. A deployment is not healthy merely because Kafka is up:
the consumer must be running, its group must have no unexplained lag, and a
restart/rebalance must reload sequence checkpoints before fetching.

## Qualification gates

1. Snapshot every pre-existing table before the run.
2. Apply only the six pinned additive statements to an isolated dev database.
3. Grant the ingestion identity INSERT only on key, value, and deliveries.
4. Run direct and Kafka fixtures in separate fresh epochs and compare logical
   grouped key/value hashes, not physical part counts.
5. Prove duplicate delivery, restart, broker/ClickHouse outage recovery, and
   Kafka reassignment without a sequence gap.
6. Audit ClickHouse query logs for the catalog identities; their write target
   set must be exactly the three new ingestion tables.
7. Keep activation rows empty and API reads authoritative/fallback-only until
   a contiguous source fence is qualified.

Backfill is separately guarded, project-scoped, UTC half-open, keyset-paged,
and writes only key, value, and checkpoint tables. Always run `--dry-run`
first. Its source identity is SELECT-only; its target database and credentials
must differ from the source. Exact query-ID cancellation also requires the
narrow ClickHouse privilege below for each dedicated backfill identity:

```sql
GRANT SELECT(query, query_id, user) ON system.processes TO <backfill_user>;
```

Do not broaden this grant. Qualification must prove that a timed `sleep`
query is killed by its exact ID, disappears from `system.processes`, and leaves
all six catalog-table counts unchanged.
