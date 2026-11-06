# Temporal health sentinel

`python manage.py check_temporal_health` checks Temporal without changing queues,
schedules, or workflows. An unhealthy report sends one consolidated Slack message
through `send_critical_slack_notification` and exits nonzero. Connection/check errors
also alert and fail the command. Connection and checks have a 60-second total deadline.

## Expected workers

By default the command checks the common queues in `TASK_QUEUES`, with
`exact_aggregation` replaced by `settings.EXACT_AGGREGATION_TASK_QUEUE`. This avoids
requiring a dedicated worker where exact aggregation runs on `tasks_xl`.

Set `TEMPORAL_HEALTH_TASK_QUEUES` to a comma-separated **complete replacement list**
for the deployment. For example, a deployment with dedicated exact-aggregation and
simulation workers can use:

```sh
TEMPORAL_HEALTH_TASK_QUEUES=default,trace_ingestion,tasks_xl,exact_aggregation,tasks_l,tasks_s,agent_compass,simulation_runner
```

Include `simulation_runner` wherever that worker is provisioned; it is not part of
the default list. Include any other dedicated queues this deployment requires.
The override must not be empty. A queue is healthy if a workflow or activity poller
has polled it within two minutes.

## Schedule checks

- Paused schedules always appear in the report, including cron/calendar schedules.
- Unrestricted interval schedules are stale after three times their shortest
  interval plus configured jitter. Only stale candidates or incomplete listings
  require `describe()` calls.
- A schedule with no recorded runs gets the same grace period from `created_at`
  (or `start_at`, whichever is later).
- Active scheduled workflows, exhausted finite schedules, schedules that have not
  started, and schedules past their end time are not reported stale.
- Cron/calendar schedules and schedules with calendar exclusion windows are checked
  for paused state only. Temporal normalizes cron expressions into calendars;
  a fixed duration cannot reliably determine missed starts for these schedules.

## Alerts and heartbeat

Identical reports are suppressed for one hour per Temporal host/namespace using
Django's shared cache. Keep the default Redis-backed cache for CronJobs: a local
memory cache cannot deduplicate across processes. Changed reports alert immediately.
Failed Slack deliveries release the cache key for retry, and cache failures still
attempt to send the alert. Suppressed unhealthy reports still exit nonzero.

An optional `--heartbeat-url` is requested after every attempt, even when Temporal
is unhealthy or the check raises. It means **the sentinel ran**, not that Temporal
is healthy. Heartbeat failures are best effort and log only the exception type;
neither the URL nor exception text is logged.

## Rollout

The CronJob is deployed separately in the deployment repository, after this backend
image is available. Configure the expected queue list independently in each region,
Temporal connection/namespace, Redis cache, the existing critical Slack webhook,
and an optional heartbeat URL from a secret. No schema migration is needed.

Before enabling periodic alerts, run the command once against staging with its
actual expected queues, verify both a healthy result and an intentionally unhealthy
result, and verify Slack delivery and the heartbeat. Unit tests use SDK models and
mock external calls; they do not replace this deployment check.
