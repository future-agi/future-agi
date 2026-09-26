# Telemetry and outbound connections

A self-hosted Future AGI install makes one kind of call home on its own:
**deployment telemetry** to Future AGI. It is on by default and you can turn it
off with one variable. The only other connection made by default is not to
Future AGI: litellm, the model-provider library, downloads its model price list
from GitHub when each API and worker process starts. Every other outside service (HubSpot, Slack, Mixpanel,
PostHog, reCAPTCHA, Sentry, Mailgun) stays off until you give it a key. With no
key set, the app makes no request to that service, logs nothing about it above
debug level, and never slows down or fails a signup or login because of it.

This page applies to all three setups: **Standalone** (the default
`docker-compose.yml`), **Distributed** (`docker-compose.distributed.yml`) and
**Helm** (Distributed on Kubernetes).

## At a glance

| Connection | On by default? | Destination | Controlled by |
| --- | --- | --- | --- |
| Deployment telemetry | **Yes** | `FUTURE_AGI_TELEMETRY_URL` (default `https://api.futureagi.com`) | `FUTURE_AGI_TELEMETRY_DISABLED=true` turns it off |
| HubSpot lead sync | No | `api.hubapi.com` | `HUBSPOT_API_TOKEN` |
| Slack "new user" message | No | your webhook | `SLACK_WEBHOOK_CHANNEL` |
| Slack internal alerts | No | your webhook | `ERROR_LOGS_WEBHOOK` |
| Mixpanel (server) | No | `api.mixpanel.com` | `MIX_PANEL_TOKEN` |
| PostHog (server) | No | `POSTHOG_HOST` | `POSTHOG_API_KEY` |
| reCAPTCHA | No | `www.google.com/recaptcha` | `RECAPTCHA_SECRET_KEY`, `RECAPTCHA_ENABLED` |
| Sentry | No | your DSN | `SENTRY_DSN` |
| Email | No (printed to the app log) | Mailgun | `MAILGUN_API_KEY` |
| Enterprise licence check | Only with a licence | `FUTURE_AGI_LICENSE_URL` | `EE_LICENSE_KEY` |
| Google Tag Manager (UI page) | No | `www.googletagmanager.com` | `VITE_GTM_ID` at frontend build time |
| Model price list (litellm) | **Yes**, at each API and worker start | `raw.githubusercontent.com` | `LITELLM_LOCAL_MODEL_COST_MAP=True` in `.env` uses the list bundled with the image (prices as of the pinned litellm release) |
| Public-IP lookup (installer only) | No | `ifconfig.io`, then `api.ipify.org` | `./bin/install` makes it only when `VITE_HOST_API` names a host other than `localhost` |

Model providers, SSO providers and integrations you add in the product are
called only when you configure them and use them. See also
[The installer](#the-installer).

## Deployment telemetry

Future AGI uses deployment telemetry to count the installs that are running,
which versions they run, and roughly how much they are used. It never includes
anything you send through the product.

### When it is sent

- **Registration**, once per install. The app tries to register right after the
  first account is created, whether through the sign-up page or
  `python manage.py create_user` (which `./bin/install` runs). It runs on a
  background thread, so the sign-up page returns without waiting for it;
  `create_user` waits for it before exiting, which takes a few seconds, longer
  if the telemetry URL does not answer (three tries, each bounded by
  `FUTURE_AGI_TELEMETRY_TIMEOUT_SECONDS`, which `./bin/install` sets to 2 for
  that command unless you set it). If that attempt fails, the
  scheduled job below retries it. Registration is sent again only if you turn
  telemetry off or back on, or if the install loses the signing secret it
  received the first time.
- **Heartbeat**, every `FUTURE_AGI_TELEMETRY_INTERVAL_HOURS` (6 by default),
  started by a recurring Temporal schedule with up to
  `FUTURE_AGI_TELEMETRY_JITTER_SECONDS` (30 minutes by default) of random delay.
  Each heartbeat covers the previous fixed UTC window, for example 06:00 to 12:00.

Nothing is sent at startup or on login.

### What registration contains

```json
{
  "schema_version": 1,
  "instance_id": "5b0c6a4e-8f0e-4c55-9d1a-2f5c1a7e9b3d",
  "version": "1.8.0",
  "deployment_type": "docker",
  "timestamp": "2026-09-25T10:15:00Z",
  "telemetry_disabled": false,
  "users": [{ "email": "owner@example.com", "domain": "example.com" }]
}
```

| Field | Meaning |
| --- | --- |
| `schema_version` | Version of this payload format. |
| `instance_id` | A random UUID created on first run and stored in your database. It identifies the install, not a person. |
| `version` | The first of `FUTURE_AGI_VERSION`, `SERVICE_VERSION` and `GIT_SHA` that names a release (`unknown` and `latest` are skipped), else `unknown`. |
| `deployment_type` | `FUTURE_AGI_DEPLOYMENT_TYPE` if set; otherwise `kubernetes`, `docker` or `bare_metal`, detected. |
| `timestamp` | When the payload was built (UTC). |
| `telemetry_disabled` | `false` here. `true` on the opt-out registration described below. |
| `users` | One entry for each active account that is an organization owner or admin, or a Django staff or superuser account (for example one made with `manage.py createsuperuser`), at the time of registration, most recent login first, at most 500. On a new install that is normally only the first account. |
| `email` | In each `users` entry: the account's email address, lowercased. |
| `domain` | In each `users` entry: the part of `email` after the `@`. |

### What a heartbeat contains

```json
{
  "schema_version": 1,
  "instance_id": "5b0c6a4e-8f0e-4c55-9d1a-2f5c1a7e9b3d",
  "version": "1.8.0",
  "window_start": "2026-09-25T06:00:00Z",
  "window_end": "2026-09-25T12:00:00Z",
  "active_users_count": 3,
  "traces_count": 1520,
  "spans_count": 20417,
  "projects_count": 1,
  "eval_logger_count": 212,
  "model_hub_evaluations_count": 40,
  "dataset_eval_runs_count": 96,
  "total_evaluations_count": 348,
  "simulation_runs_count": 2,
  "simulation_calls_count": 18,
  "experiments_count": 1,
  "gateway_requests_count": 930,
  "datasets_count": 2
}
```

`schema_version`, `instance_id` and `version` are the same as in registration.
`window_start` and `window_end` bound the UTC window the counts cover (start
included, end excluded). Every count covers only that window, across the whole
install:

| Field | Counts |
| --- | --- |
| `active_users_count` | Distinct users who created a project, dataset, evaluation or experiment, plus owners of projects that received spans. |
| `traces_count` | Distinct traces received. |
| `spans_count` | Spans received. |
| `projects_count` | Projects created. |
| `eval_logger_count` | Evaluation results logged on traces. |
| `model_hub_evaluations_count` | Evaluations created. |
| `dataset_eval_runs_count` | Evaluation cells written in datasets. |
| `total_evaluations_count` | The sum of the three evaluation counts above. |
| `simulation_runs_count` | Simulation runs started. |
| `simulation_calls_count` | Calls those simulation runs completed. |
| `experiments_count` | Experiments created. |
| `gateway_requests_count` | Requests through the Agent Command Center gateway. |
| `datasets_count` | Datasets created. |

A count is `null` when it could not be read (for example, ClickHouse was
unavailable), so a failure is never reported as zero.

### What is never sent

Prompts, model outputs, trace or span contents and attributes, dataset rows,
evaluation inputs or results, API keys, provider keys, passwords, names,
organization or project names, and the email addresses of members who are not
owners, admins, staff or superusers. The receiving server sees the IP address
the request comes from, as any HTTPS server does; the payload does not contain
it.

### How it is sent

- HTTPS `POST` of JSON to `FUTURE_AGI_TELEMETRY_URL` + `/telemetry/register/`
  or `/telemetry/heartbeat/`, with a `FUTURE_AGI_TELEMETRY_TIMEOUT_SECONDS`
  timeout (5 by default), tried up to 3 times. A payload over 512 KiB is not
  sent.
- The registration response carries a per-install secret. Heartbeats are
  signed with it (HMAC-SHA256, `X-FAGI-Telemetry-Signature` header) so the
  receiver can reject forged ones.
- A heartbeat that cannot be delivered is kept as a JSON file in
  `FUTURE_AGI_TELEMETRY_BUFFER_DIR` (a directory only the app's user can read)
  and sent on a later run, oldest first. Files older than 30 days are deleted.
- Telemetry failures are logged as warnings and never stop the app, a sign-up
  or a login.

On Future AGI's side, the receiving service stores registrations and
heartbeats and can pass registrations on to Future AGI's own CRM,
product-analytics and team-chat tools.

### Turning it off

Set `FUTURE_AGI_TELEMETRY_DISABLED=true` (`1`, `yes` and `on` also work) and
recreate the containers:

- **At install:** `./bin/install --no-telemetry` (Windows:
  `.\bin\install.ps1 -NoTelemetry`) writes the line to `.env` before anything
  starts, so even the first account's registration is the minimal one below.
  The installer prints what telemetry sends before it asks for that account's
  email.
- **Standalone / Distributed:** add the line to `.env` and run
  `docker compose up -d` (with `-f docker-compose.distributed.yml` for
  Distributed). Both compose files pass the variable to every backend process.
- **Helm:** `config.telemetry=false` in your values, which sets the variable
  for the backend, the workers and the bootstrap job.

With telemetry off:

- no email addresses and no heartbeats are sent, and any buffered heartbeats
  are deleted;
- **one** opt-out registration is still sent, so Future AGI can count installs
  that have opted out. It contains only `schema_version`, `instance_id`,
  `version`, `deployment_type`, `timestamp` and `telemetry_disabled: true`, and
  is sent again only if you turn telemetry back on and off.

To make no connection to Future AGI at all, also block outbound traffic to
`FUTURE_AGI_TELEMETRY_URL` at your network. The failed attempts are logged as
warnings and change nothing else.

### Settings

| Variable | Default | Effect |
| --- | --- | --- |
| `FUTURE_AGI_TELEMETRY_DISABLED` | `false` | `true` turns telemetry off as described above. |
| `FUTURE_AGI_TELEMETRY_URL` | `https://api.futureagi.com` | Where registrations and heartbeats go. |
| `FUTURE_AGI_TELEMETRY_INTERVAL_HOURS` | `6` | Heartbeat interval. One of 1, 2, 3, 4, 6, 8, 12 or 24; anything else falls back to 6. |
| `FUTURE_AGI_TELEMETRY_JITTER_SECONDS` | `1800` | Maximum random delay added to each scheduled run. |
| `FUTURE_AGI_TELEMETRY_TIMEOUT_SECONDS` | `5` | Timeout for each request. |
| `FUTURE_AGI_TELEMETRY_BUFFER_DIR` | Standalone `/data/telemetry`, otherwise a directory under the system temp dir | Where undelivered heartbeats wait. |
| `FUTURE_AGI_VERSION` | the image tag in `.env` | Reported as `version`. When blank, `SERVICE_VERSION` or `GIT_SHA` from the image is reported instead, else `unknown`. |
| `FUTURE_AGI_DEPLOYMENT_TYPE` | `docker` in both compose files, detected elsewhere (`kubernetes` under Helm) | Reported as `deployment_type`. |

### Seeing what your install sent

- On the first telemetry run each process logs one `deployment_telemetry_disclosure`
  line saying what it sends, where, and how to turn it off.
- The last registration payload is kept in your own database:

  ```bash
  # Standalone
  docker compose exec app python manage.py shell -c \
    "from tfc.deployment_telemetry.models import DeploymentTelemetryState as S; print(S.objects.values('instance_id', 'registration_kind', 'registered_at', 'registration_metadata', 'last_heartbeat_at').first())"
  ```

  For Distributed, run the same command with
  `docker compose -f docker-compose.distributed.yml exec backend`.
- Heartbeats that are waiting to be delivered are plain JSON files in
  `FUTURE_AGI_TELEMETRY_BUFFER_DIR`.

## Third-party services

Each of these is off until its key is set. None is needed to run Future AGI.

### HubSpot lead sync (Future AGI Cloud)

- **Key:** `HUBSPOT_API_TOKEN`.
- **With the key:** email and SSO sign-ups (and a user without an
  organization creating one) create or update a HubSpot contact, except when
  `ENV_TYPE=local`, and each login marks the contact as logged in. The login
  update runs in the background, so HubSpot never delays or fails a login.
- **Without it:** no HubSpot request, no background thread, no log line above
  debug.
- Accounts created with a password chosen up front (the open-source sign-up
  page and `manage.py create_user`) never create a HubSpot contact, key or not.

### Slack

- **`SLACK_WEBHOOK_CHANNEL`:** posts a "new user joined" message after an email
  or SSO sign-up, except when `ENV_TYPE=local`.
- **`ERROR_LOGS_WEBHOOK`:** posts internal alerts, for example a failed HubSpot
  update or an unavailable embeddings service. Never posted when `ENV_TYPE` is
  `local` or `test`.
- **Without them:** nothing is posted.

### Product analytics

- **`MIX_PANEL_TOKEN`:** server-side Mixpanel events (sign-up, login, SDK use).
  Each request has a 5-second timeout and is not retried, and a Mixpanel
  failure never fails a sign-up or login.
- **`POSTHOG_API_KEY`** (and `POSTHOG_HOST`, default
  `https://us.i.posthog.com`): server-side PostHog events for API requests.
- **Browser analytics** (`VITE_MIXPANEL_TOKEN`, `VITE_POSTHOG_KEY`) are baked
  into the frontend at build time. The frontend image built from this
  repository sets neither.
- **The UI page itself** contacts no Google host: Google Tag Manager loads only
  in a frontend built with `VITE_GTM_ID`, and reCAPTCHA's script only in one
  built with `VITE_GOOGLE_SITE_KEY`. The published images set neither.

### reCAPTCHA

- **Default:** off on a self-hosted install; the compose files set
  `RECAPTCHA_ENABLED=false`. Future AGI Cloud checks sign-up, login and token
  refresh by default.
- **To turn it on:** set `RECAPTCHA_SECRET_KEY` and `RECAPTCHA_ENABLED=true` on
  the backend, and build the frontend with `VITE_GOOGLE_SITE_KEY` (the
  published frontend image has none). On Helm, `config.recaptcha=true` sets
  `RECAPTCHA_ENABLED`; the chart refuses to render it without
  `RECAPTCHA_SECRET_KEY` (in `secrets.extra` or `config.extraEnv`) or a
  `config.extraEnvFrom` source.
- **When `RECAPTCHA_ENABLED` is unset** (outside compose), the check runs only
  if `RECAPTCHA_SECRET_KEY` is set and `ENV_TYPE` is not `local` or
  `development`.
- `RECAPTCHA_ENABLED=true` without a secret is a misconfiguration: the check
  can never pass, so sign-ins that go through it are rejected.

### AWS and GCP Marketplace (Future AGI Cloud)

The marketplace sign-up endpoints (`/accounts/aws-marketplace/`,
`/accounts/gcp-marketplace/`) are not served by an open-source install. The
AWS Marketplace client uses only `AWS_MARKETPLACE_ACCESS_KEY_ID` and
`AWS_MARKETPLACE_SECRET_ACCESS_KEY`, never the default AWS credential chain,
so your Bedrock keys or instance role are never used for it.

### Sentry, email and licence

- **Sentry:** error reports are sent only when `SENTRY_DSN` is set.
  `SENTRY_ENABLED=false` turns them off even then.
- **Email:** with `MAILGUN_API_KEY` set, invites and password resets go out
  through Mailgun. Without it, emails are written to the app log instead of
  being sent.
- **Enterprise licence:** with `EE_LICENSE_KEY` set, the app activates the
  licence with `FUTURE_AGI_LICENSE_URL` (default `https://api.futureagi.com`)
  when a licensed feature first needs it, and sends a licence heartbeat every
  24 hours. The heartbeat carries the licence id, `instance_id`, `version`,
  `deployment_type`, a timestamp, a nonce and sequence number, and the same
  usage counts as a telemetry heartbeat for the last 24 hours. It is separate from deployment telemetry:
  `FUTURE_AGI_TELEMETRY_DISABLED` does not stop it,
  `FUTURE_AGI_ENTERPRISE_HEARTBEAT_DISABLED=true` does.

## The installer

`./bin/install` and `.\bin\install.ps1` pull the images from Docker Hub, or
build them with `--from-source`; apart from that and the lookup below, they
talk only to the stack they start. Before they ask for the first account's
email, they print what deployment telemetry will send and how to opt out
(`--no-telemetry`, `-NoTelemetry`).

One lookup is the bash installer's alone: when `VITE_HOST_API` in `.env` names
a host other than `localhost` or `127.0.0.1`, it asks `https://ifconfig.io`
(then `https://api.ipify.org` if that fails) for this host's public IP address,
to print a "Public" URL in its final summary. Those services see the request's
IP address. With `VITE_HOST_API` unset or local, the default, the installer
makes no such request; the PowerShell installer never does.
