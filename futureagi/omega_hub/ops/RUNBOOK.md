# Omega Hub operations runbook

## Running a Hub operator command

Every `omega_hub_*` command below writes (or may write), so the platform's mutation-free startup
guard (`model_hub/apps.py`) admits it only as an operator job or in the explicit local mode, like
the other backfills. Run it in a backend process with the operator prefix:

```sh
# Helm
kubectl exec deploy/<release>-backend -- env SERVICE_TYPE=bootstrap STARTUP_DB_MUTATION_MODE=operator \
  python manage.py omega_hub_storage status
# Distributed compose (deploy/README.md's $COMPOSE)
$COMPOSE exec backend env SERVICE_TYPE=bootstrap STARTUP_DB_MUTATION_MODE=operator \
  python manage.py omega_hub_storage status
# Standalone compose
docker compose exec app env SERVICE_TYPE=bootstrap STARTUP_DB_MUTATION_MODE=operator \
  python manage.py omega_hub_storage status
# A local install (ENV_TYPE local or test)
NO_STARTUP_DB_MUTATIONS=false python manage.py omega_hub_storage status
```

Without the prefix the guard refuses the command and nothing runs. The commands are
`omega_hub_storage`, `omega_hub_outbox`, `omega_hub_retention`, `omega_hub_rules_notify`,
`omega_hub_maintainer`, `omega_hub_ensure_official_publisher` and `omega_hub_import_official`.

### Bootstrap the Official publisher

On hosted or self-hosted, an operator can create or repair only the reserved `future-agi`
Official publisher using an **existing active staff user** with an organization:

```sh
env SERVICE_TYPE=bootstrap STARTUP_DB_MUTATION_MODE=operator \
  python manage.py omega_hub_ensure_official_publisher --owner-email <staff-email>
```

Use the backend/app operator prefix above for the installation. The staff user's organization
must match `OMEGA_HUB_MAINTAINER_ORG_ID` when that value is already configured. Each successful
invocation records `publisher.official_ensured`, including a no-op repeat, with the staff user as
the actor. Existing profiles are preserved. A different owner, suspension or account erasure is
refused rather than silently transferred or restored. This command creates no users,
organizations, keys or maintainer grants, and does not open the Hub rollout or import entries.
Configure the printed organization ID and grant maintainers separately. The local-only
`omega_hub_seed_dev` is not a production bootstrap path.

## What every deploy shape sets

The Hub works only where all of this is wired. Each missing value is a `manage.py check` warning
(`migrate` prints them in every bootstrap; omega_hub/ops_checks.py):

| Value                                                                                                                                                                                                                                                                        | Who uses it                              | Without it                                                                                                                                       |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| The checker process (`services/omega-hub-checker`, `node src/main.mjs`) with `OMEGA_HUB_URL`, `OMEGA_HUB_CHECKER_TOKEN`, `OMEGA_HUB_CHECKER_ID`, `OMEGA_HUB_RUNNER_IMAGE` (the release's runner) and a Docker daemon it may start runner containers on (runsc in production) | claims each upload and runs it sandboxed | uploads queue and the stall page fires after 10 minutes                                                                                          |
| `OMEGA_HUB_CHECKER_TOKEN` on the app, the same secret the checker sends                                                                                                                                                                                                      | the checker endpoints                    | W004; uploads answer 503 `OMEGA_HUB_CHECKER_NOT_CONFIGURED` and nothing is stored; the operations check pages `omega_hub_checker_not_configured` |
| `OMEGA_HUB_MAINTAINER_ORG_ID`, plus `omega_hub_maintainer grant` for each maintainer (an Admin or Owner of that organisation)                                                                                                                                                | review, and the maintainers' pages       | W005; nothing can be reviewed                                                                                                                    |
| `OMEGA_HUB_OPS_ALERT_EMAILS` (optional, comma-separated on-call addresses)                                                                                                                                                                                                   | every operations page also goes there    | W006 when there is also no active maintainer: pages end as `omega_hub_ops_alert_unrouted` logs                                                   |
| `OMEGA_HUB_METRICS_TOKEN` (a scrape-only secret) and the checker's `OMEGA_HUB_CHECKER_HEALTH_PORT`                                                                                                                                                                           | the scraper and the liveness probe       | W007; the metrics endpoint answers 503                                                                                                           |
| A Temporal worker on the `tasks_s` queue, and `register_temporal_schedules` in bootstrap                                                                                                                                                                                     | the four schedules below                 | no reaping, paging, outbox drain or retention                                                                                                    |
| `OMEGA_HUB_STORAGE=object`, `OMEGA_HUB_BUCKET` and object credentials holding `ops/iam/app-policy.json`                                                                                                                                                                      | uploads and bundles                      | 503s, and the storage page                                                                                                                       |

For Standalone and Distributed, set `COMPOSE_PROFILES=hub` (or a comma-separated list containing `hub`) in
`.env` or in the Compose process environment, with a real shared `OMEGA_HUB_CHECKER_TOKEN`.
A CLI-only `--profile hub` flag is not forwarded to the app: the same profile selection must
reach the app via the compose-wired `OMEGA_HUB_COMPOSE_PROFILES` marker. Without `hub` (or `*`)
the effective token is absent even if an earlier install generated a secret: `hub_publish` is
false, checker authentication refuses requests, and uploads return 503 before anything is
stored. Published placeholder tokens are never accepted. Helm omits this marker and retains
its own checker configuration. The production Compose overlay pins the marker to `hub`,
because its checker always runs against the external runner, including with profiles `full`.
This is a configuration check, not a runtime health probe; the stall alerts and checker
health checks below still apply.
The Hub itself remains visible according to rollout.

W004, W005 and W007 are skipped in a local environment (`ENV_TYPE` local or test), where
`scripts/omega-local/up.sh` wires all of them, starts the checker and builds its runner image.

## Signals

The Hub metrics endpoint uses a dedicated read-only scrape token, never the checker token. With
`OMEGA_HUB_METRICS_TOKEN` unset it answers 503 `OMEGA_HUB_METRICS_DISABLED`.

```sh
curl -fsS -H "X-Omega-Hub-Metrics-Token: $OMEGA_HUB_METRICS_TOKEN" \
  "$OMEGA_HUB_URL/omega-hub/internal/ops/metrics"
```

The paging does not depend on a scraper: the five-minute `omega-hub-operations-check` Temporal
activity emails the Hub maintainers (through the outbox, at most once an hour per alert) when

- `omega_hub_checker_stalled`: a job has waited in the queue for more than 10 minutes since it last
  entered it (`queued_at`, reset on every requeue, so a healthy idle checker never pages and a job
  the reaper has just requeued is not counted from its submission's arrival);
- `omega_hub_checker_not_configured`: `OMEGA_HUB_CHECKER_TOKEN` is unset outside a local
  environment (`omega_hub_checker_configured 0`);
- `omega_hub_security_report_sla`: an open security report has passed 8 business hours in the
  support calendar: `OMEGA_HUB_SUPPORT_TIMEZONE` (IANA zone, default UTC), `OMEGA_HUB_SUPPORT_HOURS`
  (`HH:MM-HH:MM`, Monday to Friday, default 09:00-17:00) and `OMEGA_HUB_SUPPORT_HOLIDAYS`
  (comma-separated `YYYY-MM-DD`). A value that does not parse is warning W008 and an
  `omega_hub_support_calendar_invalid` log, and the clock runs on the default calendar;
- `omega_hub_storage_unprovisioned`: the bucket is missing, unversioned or has no object lock, and
  this deploy's credential cannot fix it (`omega_hub_storage_ready 0`);
- `omega_hub_bundle_lock_renewal_overdue` (warning): a live entry's bundle lock lapses within 14
  days, or an approval more than two days old was never renewed (`omega_hub_bundle_locks_overdue`):
  the nightly retention run has stopped.

Pages go to the active maintainers and to `OMEGA_HUB_OPS_ALERT_EMAILS`
(`omega_hub_ops_alert_recipients`). An expired RUNNING lease is reaped by the same activity even
with no checker alive. A page with nobody to receive it is logged as `omega_hub_ops_alert_unrouted`
on every check, and is not counted as paged.

The checker exposes `/healthz` and `/metrics` on its private `OMEGA_HUB_CHECKER_HEALTH_PORT`
listener. `/healthz` is safe as a liveness probe: an idle checker is judged by its polling loop, a
busy one by its job's claim and accepted heartbeats within the 15-minute lease, so a long check is
never restarted (services/omega-hub-checker/README.md).

## Checker stopped or stalled

1. Confirm the queue and oldest age from the metrics endpoint, or the alert email.
2. Check the checker `/healthz`; do not expose it outside the private operations network.
3. Restart the checker with the same pinned `OMEGA_HUB_RUNNER_IMAGE`, checker token, and
   `OMEGA_HUB_CHECKER_ID`.
4. Confirm `/healthz` reports a new claim and that the queued count falls.
5. An abandoned job's lease is reaped within five minutes of expiring; it is requeued, and after
   three attempts the submission becomes `check_error` and the maintainers get an email.

## Email delivery failure

A decision writes an `omega_hub_email_outbox` row in its own transaction. After the commit the
request wakes the Temporal worker under a 2-second deadline (2.5 s measured end to end against a
blackholed Temporal frontend, where an unbounded wake-up took 75 s); when Temporal does not answer,
the process stops trying for 30 seconds (later wake-ups return at once) and the 30-second
`omega-hub-outbox-drain` schedule delivers the row, so a Temporal outage costs a decision at most
one bounded wake-up. Delivery
retries with backoff (8 attempts, up to an hour apart) and never changes a committed decision.
A row that runs out of attempts becomes a dead letter:

```sh
python manage.py omega_hub_outbox --list         # id, template, attempts, last error; no bodies
python manage.py omega_hub_outbox --id <uuid>    # replay one, after fixing the cause
python manage.py omega_hub_outbox --all-dead     # replay every dead row
```

(with the operator prefix above). A replay sends the row's frozen subject and body and writes one
`outbox.replayed` audit event. Do not send a manual decision email unless the row is confirmed
dead. Sent and dead rows are deleted after 90 days; an account erasure removes the person's
address from the outbox (`outbox.forget_address`).

## Storage: provisioning, permissions and restore

**Who provisions the bucket.** Every deploy shape's bootstrap step runs `migrate`, and after it the
Hub provisions its bucket (`OMEGA_HUB_BUCKET`, default `omega-hub`): private, versioned, object
lock on, and a lifecycle rule that expires non-current `raw/` versions after a day. The bootstrap
output says `Omega Hub storage: bucket ... is ready` or why not. The operations check repeats the
check every five minutes (storage not up at bootstrap, or a standalone boot that skipped an
unchanged migrate) and pages `omega_hub_storage_unprovisioned` while it fails. While the check
finds the bucket not ready, new uploads answer 503 `OMEGA_HUB_STORAGE_NOT_PROVISIONED` (with
`Retry-After: 300`) and checkers are handed no work, so no upload is accepted that could not reach
approval and no check attempt is spent; jobs already queued wait and resume. Any write the bucket
or the credential refuses is a typed 503 (`OMEGA_HUB_STORAGE_NOT_PROVISIONED` or
`OMEGA_HUB_STORAGE_DENIED`), never a raw error. A passing `omega_hub_storage status` or
`provision` lifts the pause at once; otherwise the next check does, within five minutes.

```sh
python manage.py omega_hub_storage status      # read-only; exit 1 and the problems when not ready
python manage.py omega_hub_storage provision   # create or complete the configuration
```

**Permissions.** `ops/iam/app-policy.json` is everything the Hub's own credential needs (replace
`omega-hub` with `OMEGA_HUB_BUCKET`): object read, write and delete, per-version read and delete
(retention and restore), object retention (bundles are written under a 90-day GOVERNANCE lock), and
read access to the bucket's versioning, object-lock and lifecycle configuration (the status check).
Creating or completing the bucket also needs `ops/iam/provision-policy.json`. Where the Hub's
credential must stay object-scoped, create the bucket in infrastructure code with object lock and
versioning and add the lifecycle rule `omega-hub-raw-noncurrent-expiry` (prefix `raw/`, non-current
versions expire after 1 day); `status` then passes with no write. Verified on MinIO
(RELEASE.2025-10-15) with a user holding exactly `app-policy.json`: `status`, raw put, read and
purge of every version, a bundle put under its GOVERNANCE retention, delete, `versions`, `restore`
and the deferred purge of a locked bundle all pass, while creating a bucket is refused. MinIO also
accepts version deletes under plain `s3:DeleteObject`, so it cannot prove the AWS per-version
actions (`s3:ListBucketVersions`, `s3:GetObjectVersion`, `s3:DeleteObjectVersion`): those follow
the AWS documentation and have not been exercised on AWS.

**A bucket without object lock.** S3 and MinIO enable object lock only when a bucket is created,
so `provision` refuses such a bucket and says so. Point `OMEGA_HUB_BUCKET` at a new name (the Hub
creates it) and copy the objects across.

**GCS.** As for the rest of the platform, the bucket is created by Terraform. The S3 interop API
has no object lock, so give the bucket object versioning and a retention policy there; the Hub
checks that the bucket exists and is versioned and sends no lock headers. Not yet exercised on GCS.

**Retention.** The nightly `omega-hub-retention` schedule purges expired raw uploads and removed
entries' files. A bundle version still inside its retention lock is deferred to a later night, not
an error; any other storage error is logged as `omega_hub_retention_purge_failed`, the rest of the
run continues, and the run is marked failed.

**Approved bundles stay locked.** A bundle is written under a 90-day GOVERNANCE lock. Every
bootstrap's `migrate` (once the bucket is ready) and, before purging anything, the same nightly run
extend the lock on every retained version of each live
entry's bundle to 90 days from now once fewer than 30 days remain (`Version.bundle_locked_until`
records the date; services/bundle_locks.py). The app credential may delete object versions, for
erasure and retention, but holds no `s3:BypassGovernanceRetention`, so it can neither delete a
locked version nor shorten its lock: an approved pin cannot be deleted by the app for as long as
its entry is live. A renewal that fails is logged as `omega_hub_bundle_lock_renew_failed` and fails
the run. A removed entry's bundles are no longer renewed; their lock lapses and the run purges them
once the entry has been removed for 90 days. A legal takedown of a live bundle needs an operator
credential with `s3:BypassGovernanceRetention`; record it in the incident receipt.

**Restore drill.** Before deleting an approved bundle during a drill, record the exact version,
delete only that bundle key, restore the version, and verify the pin through `omega hub add` or the
bundle checksum. Never disable versioning on the production bucket.

```sh
python manage.py omega_hub_storage versions --key bundles/<sha>.tar.gz
python manage.py omega_hub_storage restore --key bundles/<sha>.tar.gz --version <version-id>
```

While the object is missing, every read of it (download, files, review) answers 503
`OMEGA_HUB_OBJECT_MISSING` with `Retry-After: 300` and logs `omega_hub_object_missing` with the key;
alert on that log. `versions` and `restore` need the object-scoped credential only. Record the bucket, key, version
id, checksum, operator, UTC timestamps and the verification in the incident receipt. The local
filesystem backend (development and tests only) has the same version and restore behaviour: every
put is a retained version and a delete leaves the versions, so the drill runs the same there.

## Schedules

The shared Temporal registrar (`register_temporal_schedules`, run by every deploy's bootstrap)
owns `omega-hub-operations-check` (5 minutes), `omega-hub-outbox-drain` (30 seconds),
`omega-hub-retention` (02:17 daily) and `omega-hub-rules-notify` (03:47 daily; it announces a new
rules version once when `OMEGA_HUB_RULES_NOTICE_SUMMARY` is set, and is a no-op after that). They
run on the `tasks_s` queue.
