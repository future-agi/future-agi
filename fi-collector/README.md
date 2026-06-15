# fi-collector

The FutureAGI OpenTelemetry Collector — production write path for the new
ClickHouse 25.3 spans store. Replaces the entire
`SDK → Django OTLP → Redis → Celery → PG → PeerDB → CH 24.10 → spans_mv` chain
mandated to die by `PLAN_V2_NO_CDC.md`.

```
Customer SDK (OTLP / HTTP / gRPC)
    → fi-collector  (this binary)
       • OTLP receiver
       • memory_limiter (backpressure)
       • batch processor (10K spans / 5s)
       • clickhouse25 exporter — splits OTel attrs into typed Maps + typed JSON,
                                 writes via clickhouse-go native protocol
    → ClickHouse 25.3 spans table
```

No PG. No Redis buffer. No CDC. No `spans_mv`. The typed-Map split that used
to run inside CH (and caused the OOMs) now runs in this Go binary at ingest
time, with bounded per-batch memory.

## Two run modes

### 1. Bundled with the FutureAGI backend (single docker compose up)

The main `docker-compose.yml` at `future-agi/` adds `fi-collector` as a
service alongside Django, Postgres, ClickHouse, etc. One `docker compose up`
brings everything live; SDKs point at the collector instead of Django's
`/v1/traces` endpoint.

### 2. Standalone (just collector + ClickHouse)

```bash
cd fi-collector/
docker compose -f docker-compose.standalone.yml up
# SDKs → http://localhost:4317 (gRPC) or http://localhost:4318 (HTTP)
# ClickHouse 25.3 at localhost:18123 (HTTP) / 19000 (native)
```

Use this for testing the collector in isolation, or for deploying it as a
sidecar in a non-FutureAGI environment.

## Why a custom OTel exporter

Off-the-shelf options considered:

- **opentelemetry-collector-contrib's `clickhouseexporter`** — assumes its own
  hardcoded schema (otel_traces / otel_logs / otel_metrics). Doesn't know
  about typed Map columns, materialized hot LLM keys, the v2 schema's
  PROJECTION shapes, or the `attributes_extra` typed JSON overflow tier.
  Would require us to rebuild every dashboard query against its schema.

- **Direct CH writer in Django** — keeps Python in the hot path, which is the
  entire reason for moving off PG-as-write-target. Doesn't scale to 1B/day.

- **Custom Go OTel exporter (this)** — uses the official OTel Collector
  framework (receivers / processors / exporters / queue / retry / batching
  all come for free); custom exporter component does ONE thing: take OTLP
  span pdata and write a row matching `tracer/services/clickhouse/v2/schema/`
  via the official `clickhouse-go/v2` native driver. Same code path SigNoz,
  ClickStack, and Uptrace all use.

## Layout

```
fi-collector/
├── cmd/fi-collector/main.go               — ocb-generated entrypoint
├── pkg/
│   ├── adapter/                           — typed-Map split logic
│   │   ├── adapter.go                     — port of pg_to_ch_adapter.py:split_attributes
│   │   └── adapter_test.go                — table-driven tests pinning every branch
│   └── chwriter/                          — CH 25.3 writer
│       ├── writer.go                      — clickhouse-go/v2 batched bulk insert
│       └── writer_test.go
├── exporter/clickhouse25exporter/         — the OTel Collector component
│   ├── config.go                          — yaml-config schema
│   ├── factory.go                         — component registration with otelcol
│   ├── exporter.go                        — OTLP traces → adapter → chwriter
│   └── exporter_test.go
├── config/
│   ├── fi-collector-local.yaml            — local dev: OTLP → batch → clickhouse25
│   └── fi-collector-prod.yaml             — prod: + memory_limiter, retry, queue
├── builder-config.yaml                    — ocb manifest (which components to compile in)
├── Dockerfile                             — scratch runtime, ~25 MiB image
├── docker-compose.standalone.yml          — collector + CH only, for isolated testing
├── Makefile                               — build / run / test / docker / bench
├── go.mod / go.sum
└── README.md                              — this file
```

## Build / run

Local dev (against a CH 25.3 sidecar running at 127.0.0.1:19001):

```bash
make build               # ocb + go build → bin/fi-collector
make run                 # bin/fi-collector --config config/fi-collector-local.yaml
make test                # unit tests
make bench               # benchmark adapter + writer
```

For OCB (the OTel Collector builder): the Makefile installs it if missing.

## Operational backpressure model

```
SDK   ──HTTP/gRPC──►   fi-collector
                        ├─ OTLP receiver       (default queue: 1K requests)
                        ├─ memory_limiter      (hard ceiling — drops if exceeded)
                        ├─ batch processor     (10K spans / 5s, whichever first)
                        ├─ retry-on-failure    (exponential, max 5 min)
                        └─ persistent queue    (disk-backed, survives restart)
                                ▼
                        ClickHouse 25.3 (async_insert=1 server-side batching)
```

If ClickHouse is briefly unavailable, the persistent queue absorbs the
backlog. If memory crosses the limit, the receiver returns 429 and SDKs
back off — no silent drops, no OOM crashes. Same pattern SigNoz uses.

## Migration relationship

- This component is the **steady-state writer**. After it's deployed, the
  old `bulk_create_observation_span_task` (Celery), `PayloadStorage`
  (Redis buffer), the PG `tracer_observation_span` table, all PeerDB infra,
  the CH 24.10 `spans_mv`, and the entire `docker-compose.peerdb.yml`
  CAN be deleted (per `PLAN_V2_NO_CDC.md` §4–5).
- The **historical backfill** (one-shot, Python, lives in `planning/.../migration/scripts/`)
  populates the new CH cluster with everything PG was holding at cutover
  time. After this collector is live, new spans flow direct, and the
  historical-data tooling is archived.

## Status

This is the scaffolding + adapter + writer + exporter component, with
tests. Production readiness needs:

- Real load test (10K+ spans/sec sustained against a real CH cluster)
- Integration with the main `docker-compose.yml`
- Monitoring/metrics exporter for ops visibility
