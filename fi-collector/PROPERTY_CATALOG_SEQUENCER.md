# Production-safe unified property-catalog Kafka contract

This document is the Core contract for the unified property-catalog hot path.
It describes binaries and configuration implemented under `fi-collector/`; it
does not create Kafka topics, replicas, credentials, persistent volumes, or
deployment manifests.

## Required topology

```text
canonical ClickHouse span write succeeds
  -> autoscaled fi-collector instances
       build deterministic bounded candidates
       key every candidate by workspace UUID
       hand off to a bounded asynchronous queue
  -> candidate Kafka topic
  -> exactly one fi-property-catalog-sequencer
       fsync exact candidate receipt
       commit candidate offset
       resolve current workspace build fence
       own sequence, stream, drain, and ordered-envelope spool state
       publish with a fixed transactional identity
  -> ordered Kafka topic
  -> fi-property-catalog-consumer (read_committed)
  -> isolated property-catalog tables and delivery ledger
```

The candidate and ordered topics must be distinct. Recommended names are:

- `futureagi.<environment>.property-catalog.candidates.v1`
- `futureagi.<environment>.property-catalog.ordered.v1`

Candidates contain no catalog revision, build token, producer stream, or
sequence. Their Kafka key is exactly the canonical workspace UUID. Only the
singleton sequencer may allocate ordered state.

## Collector contract

Set `FI_PROPERTY_CATALOG_MODE=kafka` on every autoscaled collector. In this
mode, the existing `FI_PROPERTY_CATALOG_KAFKA_*` variables refer to the
**candidate** topic, not the ordered topic.

Required variables:

| Variable | Contract |
| --- | --- |
| `FI_PROPERTY_CATALOG_MODE` | Exact value `kafka` |
| `FI_PROPERTY_CATALOG_ENVIRONMENT` | `development` or `production` |
| `FI_PROPERTY_CATALOG_DEV_ACK` | Exact development acknowledgement; development only |
| `FI_PROPERTY_CATALOG_PROD_ACK` | Exact production acknowledgement; production only |
| `FI_PROPERTY_CATALOG_EPOCH` | Non-zero UInt16; must match the sequencer |
| `FI_PROPERTY_CATALOG_PROJECTION_VERSION` | Non-zero UInt16; must match the sequencer |
| `FI_PROPERTY_CATALOG_KAFKA_BROKERS` | Comma-separated candidate brokers |
| `FI_PROPERTY_CATALOG_KAFKA_TOPIC` | Candidate topic |

Optional candidate controls:

| Variable | Default | Hard ceiling |
| --- | ---: | ---: |
| `FI_PROPERTY_CATALOG_KAFKA_CLIENT_ID` | Environment-specific collector candidate ID | 255 bytes |
| `FI_PROPERTY_CATALOG_KAFKA_DELIVERY_TIMEOUT` | `10s` | `10s` |
| `FI_PROPERTY_CATALOG_SHUTDOWN_TIMEOUT` | `10s` | `2m` |
| `FI_PROPERTY_CATALOG_QUEUE_DEPTH` | `64` batches | `1024` batches |
| `FI_PROPERTY_CATALOG_MAX_SPANS_PER_BATCH` | `20000` | `100000` |
| `FI_PROPERTY_CATALOG_MAX_KEYS_PER_SPAN` | `128` | `4096` |
| `FI_PROPERTY_CATALOG_MAX_ARRAY_MEMBERS_PER_SPAN` | `256` | `16384` |
| `FI_PROPERTY_CATALOG_MAX_ENCODED_BYTES_PER_SPAN` | `64KiB` | `512KiB` |
| `FI_PROPERTY_CATALOG_MAX_CANDIDATE_SPANS` | `512` | `20000` |
| `FI_PROPERTY_CATALOG_MAX_CANDIDATE_BYTES` | `512KiB` | `512KiB` |

After the canonical ClickHouse write, candidate construction validates the
entire canonical batch before handoff. Kafka calls never run on the canonical
drain goroutine. Handoff is non-blocking; a full queue is logged as a catalog
gap and canonical ingestion continues. The worker uses one delivery deadline
for the entire candidate batch, so latency cannot multiply by candidate count.
It stops the batch on the first publish failure. Process death, queue overflow,
or timed-out candidate delivery is recovered by canonical reconciliation.

Shutdown stops new handoffs, drains accepted batches within the single
`FI_PROPERTY_CATALOG_SHUTDOWN_TIMEOUT`, then cancels any broker call before the
producer is closed. This preserves final canonical-drain ordering without
making Kafka availability a span-ingestion dependency.

`FI_PROPERTY_CATALOG_MODE=direct_kafka_development` retains the former direct
ordered producer only for an exact development gate. Production validation
rejects it. `FI_PROPERTY_CATALOG_MODE=sequencer` is rejected by `fi-collector`
and is valid only in the dedicated sequencer binary.

## Sequencer contract

Run exactly one `/usr/local/bin/fi-property-catalog-sequencer`. The binary is
built into `fi-collector/Dockerfile`.

Required runtime and output variables:

| Variable | Contract |
| --- | --- |
| `FI_PROPERTY_CATALOG_MODE` | Exact value `sequencer` |
| `FI_PROPERTY_CATALOG_ENVIRONMENT` | `development` or `production` |
| `FI_PROPERTY_CATALOG_DEV_ACK` / `FI_PROPERTY_CATALOG_PROD_ACK` | Exactly one environment-appropriate acknowledgement |
| `FI_PROPERTY_CATALOG_EPOCH` | Same non-zero UInt16 as collectors |
| `FI_PROPERTY_CATALOG_PROJECTION_VERSION` | Same non-zero UInt16 as collectors and fences |
| `FI_PROPERTY_CATALOG_PRODUCER_STREAM_ID` | Stable canonical UUID; never change for an existing stream |
| `FI_PROPERTY_CATALOG_WORKSPACE_SCOPE_MODE` | `revision_fence` for a control-plane rollout, or `static` with an allowlist |
| `FI_PROPERTY_CATALOG_WORKSPACE_ALLOWLIST` | Sorted canonical UUIDs for `static`; absent for `revision_fence` |
| `FI_PROPERTY_CATALOG_REVISION_FENCE_FILE` | Absolute path to the atomic canonical version-2 multi-tenant fence registry (maximum 256 workspaces) |
| `FI_PROPERTY_CATALOG_SPOOL_DIR` | Absolute path on an exclusive persistent volume |
| `FI_PROPERTY_CATALOG_KAFKA_BROKERS` | Ordered-output brokers |
| `FI_PROPERTY_CATALOG_KAFKA_TOPIC` | Ordered-output topic; distinct from candidate topic |
| `FI_PROPERTY_CATALOG_SEQUENCER_TRANSACTIONAL_ID` | Fixed, environment-unique identity retained across restart |

Required candidate-consumer variables:

| Variable | Contract |
| --- | --- |
| `FI_PROPERTY_CATALOG_CANDIDATE_KAFKA_BROKERS` | Candidate brokers |
| `FI_PROPERTY_CATALOG_CANDIDATE_KAFKA_TOPIC` | Candidate topic |
| `FI_PROPERTY_CATALOG_CANDIDATE_KAFKA_CONSUMER_GROUP` | Fixed consumer group |
| `FI_PROPERTY_CATALOG_CANDIDATE_KAFKA_INSTANCE_ID` | Fixed static member identity retained across restart |

Optional sequencer controls:

| Variable | Default / bound |
| --- | --- |
| `FI_PROPERTY_CATALOG_KAFKA_CLIENT_ID` | Environment-specific ordered-output client |
| `FI_PROPERTY_CATALOG_CANDIDATE_KAFKA_CLIENT_ID` | Environment-specific candidate client |
| `FI_PROPERTY_CATALOG_KAFKA_DELIVERY_TIMEOUT` | `10s`, maximum `10s` |
| `FI_PROPERTY_CATALOG_SEQUENCER_TRANSACTION_TIMEOUT` | `30s`, maximum `2m` |
| `FI_PROPERTY_CATALOG_SEQUENCER_STARTUP_TIMEOUT` | `10s`, maximum `2m` |
| `FI_PROPERTY_CATALOG_REPLAY_INTERVAL` | `1s`, maximum `30s` |
| `FI_PROPERTY_CATALOG_SHUTDOWN_TIMEOUT` | `10s`, maximum `2m` |
| `FI_PROPERTY_CATALOG_QUEUE_DEPTH` | `64`, maximum `1024` |
| `FI_PROPERTY_CATALOG_MAX_CHUNK_ROWS` | `2000`, maximum `10000` |
| `FI_PROPERTY_CATALOG_MAX_CHUNK_BYTES` | `256KiB`, maximum `512KiB` |
| `FI_PROPERTY_CATALOG_MAX_SPOOL_FILES` | `10000`, maximum `1000000` |
| `FI_PROPERTY_CATALOG_MAX_SPOOL_BYTES` | `512MiB`, maximum `1TiB` |
| `FI_PROPERTY_CATALOG_CANDIDATE_RECEIPT_MAX_FILES` | `10000`, maximum `1000000` |
| `FI_PROPERTY_CATALOG_CANDIDATE_RECEIPT_MAX_BYTES` | `512MiB`, maximum `1TiB` |
| `FI_PROPERTY_CATALOG_CANDIDATE_RECENT_IDS` | `10000`, maximum `1000000` |

The binary acquires an exclusive local owner lock under the spool directory,
then establishes the fixed Kafka transactional producer epoch before it starts
the candidate consumer. The static candidate member identity fences a stale
consumer; the fixed transactional identity fences a stale ordered producer.
Changing either identity creates a different owner and is unsafe.

## Durable state and crash boundaries

`FI_PROPERTY_CATALOG_SPOOL_DIR` must be a single-writer persistent volume that
survives process and pod restart. It contains:

- the fixed owner lock and identity marker;
- exact candidate receipts under `candidate-receipts/`;
- durable candidate completion/dedupe evidence, including cumulative
  non-admitted skips and their typed reasons;
- ordered-envelope spool files;
- producer acknowledgement state and drain/retirement proofs.

The sequencer processes one candidate at a time:

1. Validate candidate bytes and workspace key.
2. Fsync the exact topic/partition/offset/key/value receipt and its directory.
3. Commit that candidate offset.
4. Resolve the current build fence and fsync the ordered envelope.
5. Persist completion/dedupe evidence, then remove the receipt.

A crash after step 2 or 3 replays the local receipt. A crash after step 4 is
deduped against the ordered spool's source candidate digest. Kafka redelivery
is deduped by durable coordinate and recent candidate identity; replay below a
compacted offset fails closed.

A valid canonical candidate for a workspace with no current building fence,
outside the rollout allowlist, or outside the current build's project/time
scope is an explicit typed non-admission. The sequencer durably records the
skip reason and cumulative count, completes the receipt, and continues. It
logs `candidate rollout gap`; reconciliation is the recovery path. Malformed
records, identity conflicts, epoch/projection conflicts, corrupt fence state,
and transient I/O are not converted into skips: their receipt remains and the
sequencer fails closed for supervisor retry.

Do not start the sequencer with an empty/replaced volume after it has produced
an ordered stream. Core cannot reconstruct the sole owner's exact local
sequence, receipt, and drain evidence from Kafka configuration alone; loss of
that volume is an operator blocker, not a reason to weaken validation.

## Deployment prerequisite and verification

Core is ready only when a separate deployment change provides all of the
following together: both topics, candidate routing on every collector, one
sequencer replica, an exclusive persistent volume, fixed owner identities, the
revision-fence mount, and the existing ordered consumer configured with
read-committed isolation. A consumer without the sequencer is intentionally
inert. This repository change does not supply those manifests.

Repository-local verification:

```bash
cd fi-collector
go test ./pkg/propertycatalog ./pkg/server \
  ./cmd/fi-collector ./cmd/fi-property-catalog-sequencer \
  ./cmd/fi-property-catalog-consumer
go build ./cmd/fi-collector ./cmd/fi-property-catalog-sequencer \
  ./cmd/fi-property-catalog-consumer
```
