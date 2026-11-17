# Integrations

Settings > Integrations connects an organization to external platforms. The
export destinations receive the AI Gateway's request logs; Slack receives Error
Feed alerts, and Linear, offered where the deployment allows Error Feed,
receives the issues created from Error Feed clusters.

## Export destinations

Each export connection runs a sync on its schedule
(`futureagi/integrations/temporal/activities.py`, `_sync_export_integration`).
The first sync sends the last 24 hours of request logs (Datadog: the last 18
hours), or those since the date chosen in the Add Integration wizard; each
later sync sends the request logs that arrived since the previous successful
one, overlapping it by one minute. A connection in the default workspace
exports the organization's gateway request logs, which carry no workspace, as
Request Logs shows them there.

A sync that fails puts the connection in Error with one sentence saying what
happened and what to do. Newly stored failure diagnostics and failure fields in API responses contain a
validated category, exception type, HTTP status and known vendor code; worker
logs also give the file, line and function where the exception was raised.
They never retain vendor exception text, response bodies or raw destination
URLs. Historical API rows are projected onto the same safe fields; old storage
and already-emitted logs are not rewritten. The scheduler tries it again
after its sync interval, then after
twice that for each further failure in a row, at most an hour apart; Retry on
the connection's page tries again after the one-minute cooldown; Edit replaces an export's
credentials (checked with the platform before they are saved). A successful
Edit makes the connection Active and the next manual action is Sync Now.
The first successful sync sends the one-minute-overlap window again.
Owners and Admins get an email once a connection has been in Error for an
hour. The recovery flow is `INT-E2E-008` (a PostHog stand-in stopped and started again,
recovered once by Retry and once by the scheduler).

| Destination                    | What a sync sends                                                                                                                                                                                                                                                                                                                                                                                                                           | Proof flow                                          |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------- |
| PostHog (Cloud or self-hosted) | One `agentcc_request` event per request log to `<host>/batch/`, up to 500 per batch.                                                                                                                                                                                                                                                                                                                                                        | `INT-E2E-001` (self-hosted stand-in)                |
| Datadog                        | One log per request log to the site's log intake (`/api/v2/logs`, up to 500 per batch), then the window's `agentcc.gateway.*` series (requests, errors, latency, tokens, cost) to `/api/v2/series`. Only the last 18 hours, which the intake keeps: a start date older than that is refused, and a sync whose window reaches further back (after a long Error or pause) reads `partial` and says how many older request logs were not sent. | `INT-E2E-002`                                       |
| Mixpanel                       | One `agentcc_request` event per request log, with an `$insert_id` derived from its request id: to `/track` with the project token (request logs from the last 5 days; a batch `/track` answers `0` to fails the sync), or, with the project's API secret, to `/import?strict=1` (request logs of any age).                                                                                                                                  | `INT-E2E-003` (`/track`), `INT-E2E-005` (`/import`) |
| Cloud Storage: Amazon S3       | One gzip JSON Lines object per batch of up to 500 request logs, at `<prefix>/logs/YYYY/MM/DD/hour=HH/batch_<id>.jsonl.gz`.                                                                                                                                                                                                                                                                                                                  | `INT-E2E-004`                                       |

With PostHog Cloud the connection stores `https://us.i.posthog.com` or
`https://eu.i.posthog.com` (the wizard's US Cloud or EU Cloud) as its host and a
sync sends the same `/batch/` request there; unit tests cover that stored host
only. No test delivers to PostHog Cloud: `INT-E2E-001` proves delivery to a
self-hosted PostHog stand-in, so whether PostHog Cloud accepts the batch is not
verified here.

Each connection's card under Connections and its page header show where it
delivers: the PostHog address entered, the Datadog site's API host (for
example `https://api.datadoghq.eu`), `https://api.mixpanel.com`, or
`s3://<bucket>/<prefix> in <region>`. Creating an export connection creates no
FutureAGI project. Delivery is at least once, not exactly once. The card and
page count successful request-log deliveries, including overlap repeats, not
unique request IDs. Datadog intake and S3 objects do not deduplicate these
repeats. A failed multi-batch export can also have delivered earlier batches;
its failed attempt is not added to the successful-delivery counter.

The table names the end-to-end flows in `e2e/flows/integrations/`.
Their Distributed managed proof remains pending.
The updated refusal/recovery flows are drafts until their managed browser
runs and one-sided perturbation proofs pass; static checks are not that proof.
Each flow connects its destination in the wizard, lets the product run its sync, and checks what
arrived at a local stand-in for the vendor. The list of the destinations it
does not offer is `UNLISTED_DESTINATIONS` in
`frontend/src/sections/settings/integrations/constants.js`; a connection that
already exists for one of them stays listed and manageable.

## Linear

Connect Linear
checks the personal API key with Linear (its teams and the key's user) and
stores one Linear connection for the workspace; a second key is refused until
the first is disconnected. On an Error Feed issue, Create issue lists the
key's teams and creates the issue in the team picked, titled
`[<cluster id>] <issue title>`, and the issue then links to it. Disconnect
removes the key; issues already created stay linked. The planned browser proof is `INT-E2E-007`; it is not an acceptance claim.

## Endpoint overrides

Some destinations have their API hosts fixed in code. Each of these
variables replaces one of them for that integration's service alone, for
tests and for a self-hosted proxy in front of the vendor. Leave them unset in
production: unset, each integration calls the vendor itself.

| Variable                       | Replaces                                                       |
| ------------------------------ | -------------------------------------------------------------- |
| `INTEGRATIONS_DATADOG_URL`     | the Datadog site's API and log intake hosts                    |
| `INTEGRATIONS_MIXPANEL_URL`    | `https://api.mixpanel.com`                                     |
| `INTEGRATIONS_S3_ENDPOINT_URL` | Amazon S3, with an S3-compatible endpoint addressed path-style |
| `INTEGRATIONS_LINEAR_URL`      | `https://api.linear.app` (Linear's GraphQL API)                |

They are read only by the integration services
(`futureagi/integrations/services/endpoints.py`), never shown in the console
and never set per organization or per request. No shipped compose file, Helm
value or example env file sets them. When one is set, the API and the workers
log one `integration_endpoint_override` line at start naming the variable and
its host. The value must be an `http` or `https` URL with a host (a port and a
path are optional) and no credentials, query or fragment; with any other value
the API and the workers stop at start with an error naming the variable. The harness in `e2e/stack` sets each of them on its API and worker only,
pointing them at local sinks. Its Distributed managed proof remains pending.
