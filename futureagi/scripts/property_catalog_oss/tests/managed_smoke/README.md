# Disposable managed OSS catalog smoke

This directory creates its own standalone ClickHouse 25.3, PostgreSQL 16, and
Kafka 4.1 Compose project. It never starts, stops, or joins an existing project.
It does not load root Compose or project `.env` files. Only these new files are
owned by this work package; the parent owns service wiring and managed identity.

The runnable scope includes **live infrastructure, installation SQL/provenance,
source/candidate parity, and optional actual managed lifecycle/API integration**.
`--application --ordered-chain` runs normal Django migrations and ORM onboarding,
the continuous supervisor, real Go sequencer/consumer, automatic qualification
and reader selection, authenticated property/value reads, and process restart.
The application stage uses separate persistent control/spool directories, as
OSS Compose does. After restart it commits a current-time span and an old-event-
time late arrival to canonical spans, publishes real managed candidates, and
requires both properties and values to appear through authenticated APIs.
The application stage then starts the actual current `fi-collector` in an
isolated, nonroot, read-only distroless container plus Redis. Authenticated
OTLP/HTTP exercises PostgreSQL key resolution, project/tenant stamping,
ClickHouse canonical writes, Kafka candidate emission, and catalog API reads.
It checks current and late event times, strings/numbers/booleans/arrays, and
an update to the same span that must replace the obsolete suggested value.
An unauthenticated request must return 401 without writing spans. Separate
read-only Kafka evidence verifies that automatic source scans did not conceal
failed candidate emission. These are tiny fixtures, not throughput/SLO evidence.
Sustained ingestion adds 24 two-span batches at the same two-second cadence
while reading both current and late-value families. Both families must first
be visible within 45 seconds of the stage starting and while submissions are
still ongoing, retain previously visible values, and catch up to all 48 distinct
spans within 45 seconds of the last accepted submission. Exact canonical scope
and candidate-emission checks remain required. The stage keeps its 100-second
total deadline. The enclosing subprocess now has a 300-second test-only envelope:
20 seconds readiness, two bounded POST/canonical/visibility phases (10/15/45
seconds each), the 100-second sustained phase, and 40 seconds orchestration.
The full `--application` run has a 2400-second aggregate deadline, not a larger
per-operation allowance. This qualifies sustained progress and final catch-up,
not a per-batch 45-second guarantee. The earlier 12-batch short-burst run
`a2fed3f7d681f26a` remains FAILED: traffic ended at 22.72 seconds before first
visibility at approximately 29 seconds; this revised qualification does not
reclassify that result.
Run `f4f35b3313929d88` passed initial and replacement ingestion but was terminated
by the former 150-second combined-process cap before sustained qualification
finished. That run remains FAILED, with no sustained result. Enlarging the
outer test envelope does not change any product or individual visibility limit.
A subsequent quiet-workspace check waits for all candidate repairs to settle,
appends a tombstone only for this run's synthetic late span, and requires its
property and value to disappear without a new notification. If this fails, a
separate diagnostic emits one genuine late OTLP span and checks whether the
resulting repair removes the stale suggestion without losing unrelated history.
The primary deletion gate stays failed even if that diagnostic passes; this
does not claim that the user-facing trace-delete endpoint has been tested.
A final deterministic fault-injection stage appends one update to the run's
synthetic `otlp_live` span after the real VALUES checkpoint finishes and before
the original independent audit runs. Only the disposable supervisor launcher
installs this test wrapper; there is no product hook or environment switch.
One normal late OTLP observation starts the build; the injected update emits no
notification. The exact build-bound capture (including node/table identity and
physical parts) must stay unchanged while the live value changes. Both original
independent audits and real activation must succeed. The API must first expose
that captured build's unchanged value while the new source-repair generation
remains pending. A fixture-only post-completion rendezvous records this API
observation before allowing the next normal polling pass; it writes no product
ACK. A separate full repair must claim that exact notice, capture the updated
value in a new table, pass both audits, activate, acknowledge its claimed notice,
and expose the updated API value. The 75-second latency target is reported
separately from a bounded 180-second correctness wait. A slower successful repair
records `latency_target_met=false`; it must still prove all capture, audit, ACK,
and authenticated API invariants. This accepted release tradeoff does not alter
product query limits or retry uncertain writes.
All typed, scope, history and pagination API assertions run again afterward.
Neither audit results nor catalog/control records are fabricated. This is
deliberate fault injection, not production throughput or arbitrary import proof.
A separate historical-import stage then waits for all existing repair requests
to be acknowledged and for the active event cutoff to exceed the synthetic
span's version. It appends one logical update using the previous version plus
one, keeping that number below the cutoff and preserving all timestamps. No
notification is emitted. The actual value API must replace the stale value
within 45 seconds while preserving `plan=Pro`. Source readback and repair-file
changes are recorded in `catalog-old-version-import.json`. This specifically
tests the old-version/no-notification gap; it cannot be satisfied by an earlier
pending repair or by changing a fixture clock/lease.

A post-audit import stage then inserts one uniquely scoped synthetic span after
the real final independent audit and fencing, before source-part acknowledgement
and activation. Its event time is inside the current increment and its source
version is 1; no notification is emitted. The test requires the exact event's
durable repair notice before activation, later qualified API visibility within
the bounded 180-second correctness wait, and preserved unrelated history. Its
60-second latency target is reported separately. Mere eventual visibility cannot
pass it. An exclusive pre-INSERT attempt file prevents retrying an uncertain
commit. `catalog-post-audit.json` records the evidence. The injection is installed
only in this disposable supervisor; no product hook or environment knob exists.
For this one gate, the fixture pauses merges on its **owned disposable**
`default.spans` table and verifies in-flight merges have drained. Scheduling is
restored in `finally`, even if STOP returned an uncertain result. A broad repair
from unrelated part replacement could conceal the specific detector regression,
so it cannot satisfy this gate. This is fault isolation, not a proposed production
setting or proof of acceptable merge cost. `post-audit-merge-isolation.json`
records restoration. The injection report also records the actual detector's
pre-scan/durable inventories, metadata snapshots, and part-query inputs/results;
observing wrappers preserve original calls, results, and failures without adding
source queries. Failed runs remain failed even if a later broad repair might
have recovered the row.
Before insertion, the test also waits for the saved inventory to match the
real pre-scan inventory and for prior repair notices to be acknowledged by the
normal controller. Pausing merges does not itself settle a merge that already
happened. Unsettled cycles still run the unmodified detector and repair; the
fixture writes no inventory/acknowledgement and does not inject on those cycles.
`post-audit-baseline.json` records this precondition within the existing timeout.

The final application stage creates two additional workspaces using normal ORM
operations after the existing installation is live: one in the same organization,
one in a new organization. It sends authenticated OTLP through the actual
collector and requires automatic property/value onboarding within a bounded
180-second correctness wait. The original 60-second latency target remains
visible in the report and is not reported as passed when missed.
It checks project and workspace queries, foreign/mixed project rejection, real
cursor isolation, API-key/header scope, unchanged installation identity, and
continued original-workspace reads. No lifecycle record or allowlist is authored
by the fixture. Evidence is saved in `catalog-workspaces.json`; offline guard
tests are not substitutes for this live stage.

Subsequent relational phases use normal ORM source fixtures in all three
workspaces, then update and soft-delete the original workspace's source parents.
They check configured values, definitions, search/cursors and foreign scope
rejection, including child records whose timestamps did not change. Dataset
coverage remains definitions-only, not cell-value ingestion.

The projectless phases then create a fourth actual workspace containing dataset
and simulation definitions but no trace project. Normal discovery must qualify
it, with ten real terminal stream/checkpoint proofs despite zero span source.
The fixture adds a real Observe project, sends one authenticated OTLP request,
verifies its catalog values, and soft-deletes that last project. Relational
definitions must remain and the deleted project scope must be rejected.
These phases are wired but are not proven until a successful live run.
After the final deletion, the runner restarts the actual supervisor, sequencer,
and consumer, proves new process IDs and healthy progress across all four
workspaces, and verifies the projectless APIs again. It requires unchanged
installation identity bytes and reader-control count. The readback and process
evidence are separate; wiring alone is not proof of a successful restart.

It also tests the old activation-control enum upgrade on a populated separate
disposable database. No source spans or existing application catalog are changed
by that schema-upgrade stage.

This is not complete release E2E: root Compose/release-image startup, the browser,
replicated ClickHouse, all relational families and broader failure coverage remain
required. APIClient uses actual auth/middleware and a scoped DRF router because
the full URL import attempts unrelated NLTK downloads (external egress is denied).
The full lifecycle gate is tracked in [lifecycle_contract.md](lifecycle_contract.md).
`result.json` always records that distinction, including in transport-only mode.

Offline harness checks (ownership and fault scope/order, not live E2E):

```sh
FI_SKIP_CH25_SCHEMA_APPLY=1 TESTING=false CH_ENABLED=false \
  CH_HOST=127.0.0.1 CH_PORT=2 CH_HTTP_PORT=3 CH_DATABASE=test_catalog_offline \
  CH25_HOST=127.0.0.1 CH25_TCP_PORT=2 CH25_HTTP_PORT=3 \
  CH25_DATABASE=test_catalog_offline PG_HOST=127.0.0.1 PG_PORT=1 \
  REDIS_URL=redis://127.0.0.1:4/0 \
  DJANGO_SETTINGS_MODULE=tfc.settings.test PYTHONPATH="$PWD/futureagi" \
  futureagi/.venv/bin/python \
  -m pytest futureagi/scripts/property_catalog_oss/tests/managed_smoke -q
```

Verify those loopback ports have no listeners before the offline command.
The root pytest fixtures can issue schema DDL, so do not rely on default test
ports or a loopback hostname: a local port may forward to a shared database.
The live runner provisions its own endpoints and does not use these settings.

## Run

Requirements: a running Docker daemon, Docker Compose v2+, Go matching
`fi-collector/go.mod`, and Python 3.11+ with Django and `clickhouse-driver`.
The normal backend virtualenv supplies the Python dependencies. Dependency
images may need to be pulled. Budget about 6 GiB of Docker memory with the
application stage, plus existing workload headroom. Nothing restarts the Docker
daemon. The collector's test image copies only the freshly compiled Linux
binary and checked-in config; the build context excludes manifests, credentials
and logs. This tests the real command but not the multi-binary release Dockerfile.

From the worktree root:

```sh
PYTHON="$PWD/futureagi/.venv/bin/python"
HARNESS=futureagi/scripts/property_catalog_oss/tests/managed_smoke/run.py

# Render and validate only; no containers, networks, or volumes are created.
"$PYTHON" "$HARNESS" plan

# Execute the currently implemented live scope. Removes only its own resources.
"$PYTHON" "$HARNESS" run --transport-only

# Runs the implemented application and transport stages. Still returns 2 until
# all release gates are implemented and proven; this is not production readiness.
"$PYTHON" "$HARNESS" run --application --ordered-chain
```

The script prints its unique `property-catalog-managed-<random>` temporary
directory. It owns a `pcmanaged-<random>` Compose project, five unique loopback
ports, a private network, and two new data volumes. Port allocation races fail
the run; the script never frees an occupied port. No production endpoint or
existing Compose project can be selected through the CLI.

Every subprocess and the aggregate run have deadlines: 2400 seconds total for
`--application`, including at most 2100 seconds for the expanded application
process chain after migrations, further bounded by the parent's deadline minus
20 seconds. Transport-only runs retain their existing 900-second envelope.
The expanded chain includes seven later relational/projectless transitions plus
restart/schema qualification. The earlier envelope left only about 170 seconds
for the application chain and 230 seconds overall after a roughly 670-second run
failed at workspace onboarding; that failed result remains failed.
These are aggregate budgets, not single-query or stage allowances.
Other per-operation caps, leases and product query timeouts remain unchanged.
Source-change repair, post-audit repair and new-workspace onboarding distinguish
their original 75/60/60-second latency targets from the accepted 180-second correctness waits;
their parent process allowance is 240 seconds for setup and isolation checks.
The infrastructure gates retain
180-second service readiness, 35-second Kafka probe, 60-second source-reader
deadline, and at most eight one-row source pages. Container memory, CPU, PID,
log, Kafka record size, retention, and broker storage are capped. The source
reader retains its checked-in query row/byte/time ceilings and read-only role.

Use `--keep` only when you want to inspect this run's resources after completion.
Clean up using its **exact printed directory**, including after interruption:

```sh
"$PYTHON" "$HARNESS" cleanup --directory /absolute/printed/property-catalog-managed-RUN_ID
```

Cleanup verifies the run manifest, Compose digest, and exact project/resource
ownership labels. It never uses Docker prune or removes shared images. It removes
the disposable project's containers, network, and catalog/source data volumes;
those test data are not recoverable. Its uniquely tagged local collector image
is removed only after checking its run-ownership label. Logs, manifest, fixtures,
compiled binaries and results stay in the temporary directory. Downloaded base
images remain cached. There is no host directory deletion command.

## What the live smoke proves

1. Starts fresh services with unique resources and records exact image IDs.
2. Creates the canonical spans table using checked-in schema `002`, `013`, and
   `014`, and reuses the CI-local tiered storage XML.
3. Proves the isolated catalog database is absent, then runs the actual
   `bootstrap_clickhouse.sh` and `bootstrap_postgres.sh` **twice** to verify
   additive bootstrap and seven-table/user setup. No table shape is patched to
   satisfy an old fixture.
4. Exercises the parent's `inspect_installation` with actual ClickHouse SELECTs:
   six empty tables and a real nonempty control plan, including
   `ARRAY JOIN JSONExtractArrayRaw` and `JSONExtractString`. The actual
   coordinator allocates one finite 120-second OPEN reservation at nondefault
   epoch 7/projection 3, so a silent default fallback fails the test. No build
   is qualified or activated. Fresh resolution omits all version/producer
   settings; writable/read-only restarts must preserve identity bytes without
   more SQL. Read-only resolution with a missing file must refuse without SQL.
   The fresh and metadata-adoption cases use separate disposable file paths.
   This tests the library initializer, not full supervisor process startup.
5. Creates distinct candidate and ordered topics on the new broker only.
6. Writes six synthetic physical spans: two live logical spans in the target
   project, an obsolete version, a deleted span plus its tombstone, and a
   different project's span. No application/user data is imported.
7. Uses the real Go `BuildCandidates` and `CandidateProducer` to publish a
   managed candidate twice. A real Kafka client reads it back at offsets 0 and
   1, checks the workspace key, exact bytes, and stable candidate identity.
   Managed candidates must have version 2 and unallocated epoch/projection 0.
8. Uses the real Python `CanonicalSpanSourceReader` against ClickHouse and the
   checked-in historical value projection. Compares key/type/value/fingerprint
   tuples with the Kafka candidate and an independent explicit golden set.
   Covers strings, escaped strings, Unicode, positive/negative/zero numbers,
   booleans, mixed scalar arrays, duplicate array values, key-only maps/nulls/
   empty arrays, promoted `model`, version collapse, soft deletion, and tenant
   exclusion. Maps/nulls are key definitions, not invented scalar suggestions.
9. Proves the source identity rejects an INSERT containing zero source rows.

The default transport-only scope does **not** claim the ordered topic was consumed, a revision was
qualified/activated, relational source definitions
were reconciled, or backend APIs returned catalog results. It writes no fake
fence, drain proof, lease, checkpoint, delivery, or activation record. The real
coordinator does write its normal reservation and fence to disposable storage.
The optional `--application --ordered-chain` stages additionally exercise the
real ordered consumer, automatic lifecycle/activation and authenticated API
checks described above. Their evidence remains separate from release readiness.

## Evidence and maintenance

`result.json`, `partial-result.json`, `identity-evidence.json`,
`parity-evidence.json`, `kafka-evidence.json`, the source fixture,
numbered command logs, and the rendered Compose JSON remain under the run
directory. The manifest contains only this disposable run's generated local
password and is mode 0600 inside a mode 0700 directory. The before/after
container inventory reports whether preexisting containers are still running;
the harness never operates on those IDs.

`collector-continuous.json` records actual submissions and visible-value progress.
`catalog-deletion.json` records the canonical-only deletion assertion, separately
from `catalog-deletion-repair-diagnostic.json`. A diagnostic success cannot
override the primary failure or the aggregate nonzero result.

Run harness-only safety tests without Docker or service IO:

```sh
"$PYTHON" -m unittest discover \
  -s futureagi/scripts/property_catalog_oss/tests/managed_smoke \
  -p 'test_*.py' -v
```

Those are unit tests of resource scoping and diagnostic controls, **not** live
smoke or E2E evidence.
The old `fi-catalog-dev-smoke` targets the legacy catalog. The existing
`TestFranzLoopbackEnvelopeConsumerDeliveryAndReplay` uses a fake lease guard.
Neither substitutes for the managed lifecycle gate. The DEV Docker renderer
also attaches to existing external networks, so this harness reuses its
contracts and the OSS bootstrap scripts without invoking that renderer.
