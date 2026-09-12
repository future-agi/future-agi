# Local Error Feed runtime

This starts the Node worker and three independent periodic Django jobs on an
existing application network. It does not start databases, migrate them, build
images, pull images or publish anything. Use it only with local/test credentials
until the release checks below are complete.

## Prepare

1. Build `omega-error-feed:local` in the private Omega checkout using
   `workers/error-feed-node/README.md`. Omega stays in that image; none of its
   source is copied into this repository.
2. Apply this branch's Django migrations to the intended local database. Use the
   repository's explicit bootstrap/operator procedure, not application startup.
   Run `python manage.py check` once before starting the scheduled processes.
3. Create the Kafka topic `error-feed.trace-available.v1`. Enable the collector's
   `FI_ERROR_FEED_ENABLED` and `FI_ERROR_FEED_KAFKA_BROKERS` only against the intended
   local broker. It must acknowledge synchronous ClickHouse inserts.
   The existing root Compose configuration forwards these variables and defaults
   notifications off. Recreate only the collector after changing its environment;
   do not launch another database or broker. The collector image must include this
   branch's notifier code, not just the new environment variables.
4. Enable `ERROR_FEED_OMEGA_ENABLED` in the Django API. Set the chosen project's
   scanner configuration to `engine=omega`, `enabled=true`, and a `scan_version`
   matching the Node worker's `OMEGA_ENGINE_VERSION`. Other projects keep the
   existing scanner.

Keep runtime env files and secrets outside the repository. Set these Compose
variables in your shell; paths must be absolute:

| Variable                    | Required value                                            |
| --------------------------- | --------------------------------------------------------- |
| `OMEGA_BACKEND_IMAGE`       | A prebuilt local backend image containing this branch     |
| `OMEGA_WORKER_ENV_FILE`     | Worker configuration file described below                 |
| `OMEGA_BACKEND_ENV_FILE`    | Existing local Django DB/CH/serving/gateway configuration |
| `OMEGA_WORKER_SECRETS_DIR`  | Worker-only directory containing its three secret files   |
| `OMEGA_APPLICATION_NETWORK` | Existing network shared with Kafka, CH and the Django API |
| `OMEGA_WORKER_IMAGE`        | Optional; defaults to `omega-error-feed:local`            |

The worker env file needs these values:

```dotenv
OMEGA_DJANGO_URL=http://backend:8000
OMEGA_INTERNAL_API_SECRET_FILE=/run/omega-secrets/internal-api-key
OMEGA_KAFKA_BROKERS=kafka:9092
OMEGA_ENGINE_VERSION=omega-v1
OMEGA_CONCURRENCY=4
OMEGA_CLICKHOUSE_URL=http://clickhouse:8123
OMEGA_CLICKHOUSE_DATABASE=default
OMEGA_CLICKHOUSE_USERNAME=omega_reader
OMEGA_CLICKHOUSE_PASSWORD_FILE=/run/omega-secrets/clickhouse-password
AGENTCC_BASE_URL=http://agentcc-gateway:8080/v1
AGENTCC_API_KEY_FILE=/run/omega-secrets/gateway-key
# Set an explicitly allowed, priced model from your gateway configuration.
OMEGA_MODEL_ID=YOUR_CONFIGURED_MODEL
```

Replace service names and ports with the actual local endpoints. Secret files
must be readable by container UID 1000; do not make them world-readable. Give
Node a dedicated SELECT-only CH account. It does not receive PostgreSQL credentials
or Django's env file. Backend grouping also needs the existing embedding service
and centroid-write CH configuration, in addition to its PostgreSQL connection.

Current account-profile compatibility is unresolved: ClickHouse `readonly=1`
rejects changes to the query's execution/result limits, while `readonly=2`
rejects the worker's explicit `readonly=1` override. Both failures were reproduced
on local ClickHouse 25.3 using read-only probes. The existing query guard remains
unchanged. Before changing it, obtain explicit approval and verify the dedicated
account's SELECT-only grants and read-only profile; do not give Node a writable
account to bypass this check. No database accounts or settings were changed.

## Start and stop

From the repository root:

```sh
docker-compose -f deploy/error-feed-local/compose.yaml --profile omega config --quiet
docker-compose -f deploy/error-feed-local/compose.yaml --profile omega up -d
docker-compose -f deploy/error-feed-local/compose.yaml --profile omega logs -f
docker-compose -f deploy/error-feed-local/compose.yaml --profile omega stop
```

For local development with an older dependency image, set `OMEGA_BACKEND_SOURCE`
to this checkout's absolute `futureagi` directory and add
`-f deploy/error-feed-local/compose.source.yaml` to each command. That override
mounts only Django source read-only into the three maintenance containers; it does
not mount any source into Node. The image must still supply compatible Python
dependencies. It is not a deployable image release.

Reconciliation runs a bounded page every 60 seconds after the previous cycle.
Grouping drains up to 25 pending reports, then waits five seconds. A command
failure exits its container; Docker restarts that process. A per-report grouping
failure stays pending and rotates behind other reports. Usage processes up to 100
receipts every 30 seconds. None of these jobs uses Temporal,
and grouping retries do not invoke the Omega investigation again.
The scheduled commands skip repeated Django system checks after that preflight;
otherwise even a reconciliation cycle imports unrelated URL and model-provider code.

Billing enqueueing is explicitly off by default. The usage job can prepare durable
receipts, but does not send charges until
`ERROR_FEED_OMEGA_BILLING_EMIT_ENABLED=true` is explicitly set. Do not enable it
until the deployed usage consumer has been verified to deduplicate by `event_id`:
a lost Redis acknowledgement can cause a retry of the same event. Missing gateway
prices remain unpriced, not free. The immutable report supplies customer/project
attribution; the shared internal gateway key is not the customer's identity.

Reports and evidence use separate disk-backed volumes. A new volume inherits
UID-1000 directory ownership from the worker image. The report volume holds
completed results until Django acknowledges publication. Do not use `down -v`
or delete that volume while results remain pending. Scratch files are removed
after normal attempts; crashed-process residue needs an operator retention policy.
Trace byte limits bound individual downloads, not the host's total disk capacity.

## Verification and remaining release checks

The config tests check local-image-only execution, independent bounded schedules,
separate disk volumes, secret isolation and actual Compose rendering:

```sh
futureagi/.venv/bin/python -m unittest discover -s deploy/error-feed-local -p 'test_*.py' -v
```

The Node image was tested as UID 1000 with a read-only root filesystem: a synced
synthetic report survived container replacement on a separate ext4 test directory.
The default local Docker data disk was full, so its named-volume write returned
`ENOSPC`. This setup does not repair or prune existing Docker storage.

This is local integration wiring, not production readiness. Still verify provider
accuracy for the file-tool engine, collector-to-Feed execution, gateway tenant
billing and consumer deduplication, full API contracts, and deployment capacity.
The current checkout lacks cloud modules needed for full contract regeneration.

Existing clustering remains a compatibility adapter: free-form `kind` values may
fragment under category partitioning. Its PG transaction spans external clustering
calls, and ClickHouse centroids cannot roll back with PG. Atharva's replacement
should address that write boundary before a high-volume rollout. These tests do
not establish throughput for 20–30 million traces or clustering accuracy.
