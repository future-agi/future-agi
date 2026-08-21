# Span attribute catalog: development qualification

This catalog is additive. Its runtime is disabled by default, and production
must remain disabled until the development evidence and code are reviewed.
Neither ingestion mode may `ALTER`, `UPDATE`, `DELETE`, or insert into `spans`
or another pre-existing table.

## Storage and replication

Schema files `025_span_attribute_catalog.sql` and
`026_span_attribute_catalog_delivery.sql` create six independent tables. The
dev harness applies their six pinned `CREATE TABLE` statements directly to a
new `th7247_catalog_dev_*` database; it does not invoke the normal schema runner
or write `schema_versions`.

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

## Mutually exclusive ingestion modes

`FI_CATALOG_MODE` accepts `disabled`, `direct`, or `kafka`. Enabled modes also
require `FI_CATALOG_ENVIRONMENT=development`, a non-zero epoch, a canonical
producer-stream UUID, and a dedicated durable spool directory.

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

## Development Kafka

Start the dedicated single-node broker and pre-created six-partition topic:

```sh
docker compose -f fi-collector/docker-compose.catalog-kafka.dev.yml up -d
```

It is loopback-only, plaintext, replication-factor 1, and marked
`production-use: forbidden`. It is for equivalence/fault testing only. A later
production proposal requires a separate managed multi-broker service with TLS,
authentication, RF=3, and measured capacity; it must not reuse the Mimir demo
broker.

The broker compose file does not supervise application processes. Run the
consumer as a separate service from the exact candidate image and give it a
durable restart policy. A brand-new topic/group may start once with
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
