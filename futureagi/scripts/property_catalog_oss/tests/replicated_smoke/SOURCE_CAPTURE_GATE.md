# Replicated canonical source capture gate

Fixture-only verification, using the existing two 3 GiB ClickHouse servers and
0.5 GiB Keeper. No private backend image, Docker build/pull, application startup,
catalog activation, production three-replica admission, or serving claim.

The source on both replicas is the complete checked-in `002_spans_v2.sql` table
definition: only its object header and `ReplicatedReplacingMergeTree` transport
arguments change. Indices, projections, keys, codecs, defaults, TTL and `tiered`
storage settings are retained. The existing 013/014 migrations are then applied
once through replica1 during empty-source bootstrap and verified on both replicas
(String `attributes_extra`, delete alias). The fixture's hot/default and cold/Local
paths are inside its existing owned data volumes. No STOP MERGES is used. A
single inserted block contains five physical rows: an older/newer live
value, a live/tombstoned pair, and an unchanged value.

The gate requires actual 2/2 source replication and identical physical rows,
then invokes the real product schema qualifier, native backend, durable capture
journal/reservations, capacity guard, bound NativeSourceClient, canonical scanner
and independent aggregate audit. The target must be plain **MergeTree**, contain
all five physical versions/tombstones, and exist only on replica1. The scanner
must return exactly `live=new` and `stable=kept`; its digest must match the audit
with zero conflicts. Journal-manager reconstruction must reuse that capture.
After every source read finishes, the exact derived table/reservation is retired.
Source UUID, schema, active-part checksums and physical rows must remain unchanged
on both replicas across capture and retirement. All source DDL/DML is restricted
to fixture-admin bootstrap before that baseline; the product capture writer has
only SELECT on the source. Migration 015's external dictionary is not installed
by this narrow source fixture; its unread expressions remain a separate schema
qualification test, not part of this gate's coverage claim.

This does **not** test cross-member factory routing, server/process restart,
continuous arrivals, HTTP serving, Kafka, or the full three-replica release lane.
It closes only the replicated-source physical ATTACH/scanner compatibility gap.
Offline tests validate gate safety and fixture fidelity, not ClickHouse behavior.

## Commands

From the managed-lifecycle worktree root:

```sh
CAPTURE_PY="$PWD/futureagi/.venv/bin/python"
CAPTURE_GATE=futureagi/scripts/property_catalog_oss/tests/replicated_smoke/source_capture_probe.py

# Offline only: private manifest, source hashes and canonical source SQL.
"$CAPTURE_PY" -B "$CAPTURE_GATE" plan

# ONLY after the parent confirms standalone cleanup and releases the budget.
# Substitute the exact directory/run_id printed by plan; no other endpoint input.
"$CAPTURE_PY" -B "$CAPTURE_GATE" execute RUN_DIRECTORY --confirm-run-id RUN_ID
```

Execution retains the existing local-Unix-Docker, loopback-port, ownership-token,
image-identity and full-capacity checks (6.5 GiB plus existing caps plus 2 GiB
reserve). Every preexisting resource prevents execution, even if owned. Product
source/DDL drift requires a fresh plan. Setup and execution intents are exclusive;
failed/uncertain CREATE, INSERT or ATTACH is never retried by this gate. Only
read-only readiness queries retry within a finite deadline.

Success or failure attempts cleanup of this fresh fixture's exact owned
containers, volumes and network; the private run directory and journals remain.
`capture-result.json` is authoritative only when `status=passed` **and**
`owned_resources_absent=true`. Inspect `capture-source-before.json`,
`capture-source-after.json`, `capture-spec.json`, `capture-product.sql` and
`capture-control/` for evidence. Never print `manifest.json` or `users.xml`.

If interrupted, do not replay execution. The existing ownership-checked cleanup
command remains available after confirming the gate process has stopped:

```sh
"$CAPTURE_PY" -B futureagi/scripts/property_catalog_oss/tests/replicated_smoke/run.py cleanup RUN_DIRECTORY
```

Do not run either Docker command until the parent releases the fixture budget.
