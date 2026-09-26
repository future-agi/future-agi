# Future AGI Helm chart

Future AGI on Kubernetes. The chart runs the **Distributed** setup: one
Deployment per service, so each one scales on its own.

| Component | Workload | Image |
| --- | --- | --- |
| API (Django on Granian, REST and WebSockets) | `<release>-backend` Deployment | `futureagi/future-agi` |
| Temporal workers: every generic queue, plus the single-slot exact-aggregation queue and optional per-queue workers | `<release>-worker*` Deployments | `futureagi/future-agi` |
| UI | `<release>-frontend` Deployment | `futureagi/frontend` |
| OTLP collector (traces into ClickHouse) | `<release>-fi-collector` Deployment | `futureagi/fi-collector` |
| LLM gateway | `<release>-agentcc-gateway` Deployment | `futureagi/agentcc-gateway` |
| Embedding model server (optional) | `<release>-serving` Deployment | `futureagi/serving` |
| Code eval sandbox (optional, privileged) | `<release>-code-executor` Deployment | `futureagi/code-executor` |
| Schema, seeds, ClickHouse schema, CDC, Temporal schedules | `<release>-bootstrap` Job (Helm hook) | `futureagi/future-agi` |

Every Future AGI image takes one tag, `image.tag`, which defaults to the
chart's `appVersion`.

> **Set `image.tag` until a release ships `bootstrap_install`.** The bootstrap
> job runs `python manage.py bootstrap_install`, which no published image up
> to v1.41.1 contains, the chart's first default `appVersion` included. Until
> a published release contains the command, point `image.tag` (and
> `image.registry`) at images built from the same checkout as this chart and
> pushed where your cluster can pull them. Otherwise the bootstrap job fails
> with `Unknown command: 'bootstrap_install'`.

The datastores are **external** by default: PostgreSQL, ClickHouse, Redis,
Temporal and S3-compatible object storage that you run and back up. For an
evaluation, the chart can run each of them itself (**bundled**: one replica
each, not highly available, no backups).

Postgres changes reach ClickHouse through the outbox change data capture
(`FI_CDC_MODE=outbox`: triggers plus a drain in the Temporal workers). There
is no PeerDB and no RabbitMQ; live updates use Redis.

To run everything on one machine with Docker instead, use the Standalone
install (`./bin/install`); `./bin/install --distributed` runs this same
topology with Docker Compose.

- [Requirements](#requirements)
- [Quick start: evaluation](#quick-start-evaluation)
- [Production install](#production-install)
- [First account, UI and traces](#first-account-ui-and-traces)
- [Upgrade](#upgrade)
- [Uninstall](#uninstall)
- [External and bundled datastores](#external-and-bundled-datastores)
- [Secrets](#secrets)
- [Public URLs and ingress](#public-urls-and-ingress)
- [Sizing](#sizing)
- [Security](#security)
- [GitOps (Argo CD, Flux)](#gitops-argo-cd-flux)
- [Troubleshooting](#troubleshooting)
- [Developing the chart](#developing-the-chart)
- [Values](#values)

## Requirements

- Kubernetes 1.27 or newer, Helm 3.10 or newer.
- For an evaluation: about 4 CPUs and 8 GiB of memory free, and a default
  StorageClass (bundled datastores use PersistentVolumeClaims).
- For production, reachable from the cluster:
  - PostgreSQL 16 (11 or newer works), connected to directly: a
    transaction-mode pooler such as PgBouncer breaks the CDC advisory lock.
  - ClickHouse 25.3 or newer, over plain HTTP (8123) and the native protocol
    (9000). The configured user needs CREATE on the database and, for the
    observed-attribute index, CREATE DATABASE, CREATE USER and GRANT (or set
    `bootstrap.propertyCatalog=false` and create them yourself).
  - Redis 6 or newer, databases 0 to 3.
  - Temporal 1.2x or newer (the temporalio/temporal Helm chart, for example),
    plain gRPC, with the namespace created. Temporal Cloud (TLS, API keys) is
    not supported yet.
  - AWS S3, Google Cloud Storage (HMAC keys) or any S3-compatible service,
    with an access key and secret key: IAM roles for service accounts and
    Workload Identity are not supported.

## Quick start: evaluation

Every datastore in the cluster, reached through port-forwards:

```sh
helm install futureagi deploy/helm/futureagi \
  -f deploy/helm/futureagi/examples/bundled.yaml \
  --namespace futureagi --create-namespace --timeout 20m
```

Until a release ships `bootstrap_install` (see above), add
`--set image.registry=<your registry> --set image.tag=<your tag>`.

Helm waits for the bootstrap job (migrations and schema, a few minutes on
the first install), then prints the next steps: the port-forwards, the
first-account command and where to send traces. If Helm times out first,
the job keeps running: wait for it to finish
(`kubectl -n futureagi get job futureagi-bootstrap`) before you run Helm
again, since a new run replaces the job mid-way.

```sh
kubectl -n futureagi port-forward svc/futureagi-frontend 3000:80 &
kubectl -n futureagi port-forward svc/futureagi-backend 8000:8000 &
kubectl -n futureagi port-forward svc/futureagi-fi-collector 4318:4318 &   # traces: FI_BASE_URL=http://localhost:4318
kubectl -n futureagi port-forward svc/futureagi-minio 9005:9000 &   # file downloads
kubectl -n futureagi exec -it deploy/futureagi-backend -c backend -- python manage.py create_user
open http://localhost:3000
```

Add an LLM provider key for the built-in evals, at install or later:

```sh
helm upgrade futureagi deploy/helm/futureagi --reuse-values \
  --namespace futureagi --set secrets.llm.openaiApiKey=sk-... --timeout 20m
```

This restarts the application pods, not the bundled datastores.

## Production install

1. Create the Secrets for your datastores (or put the passwords in your
   values file; the chart then stores them in its own Secret):

   ```sh
   kubectl create namespace futureagi
   kubectl -n futureagi create secret generic futureagi-postgres --from-literal=password='...'
   kubectl -n futureagi create secret generic futureagi-s3 \
     --from-literal=access-key='...' --from-literal=secret-key='...'
   ```

2. Copy [`examples/external.yaml`](examples/external.yaml), replace the
   example hosts, and add [`examples/ingress.yaml`](examples/ingress.yaml) for
   public endpoints:

   ```sh
   helm install futureagi deploy/helm/futureagi --namespace futureagi \
     -f my-values.yaml -f deploy/helm/futureagi/examples/ingress.yaml --timeout 20m
   ```

With only external datastores, the bootstrap job runs **before** anything
else is created (a `pre-install` hook), so the API starts on a ready schema.
A values file that leaves out a required setting fails at once with a
message that names it, before anything is created.

Pin the version you run: `--version` for a packaged chart, or `image.tag`.

## First account, UI and traces

```sh
# Interactive:
kubectl -n futureagi exec -it deploy/futureagi-backend -c backend -- python manage.py create_user
# Scripted:
kubectl -n futureagi exec deploy/futureagi-backend -c backend -- python manage.py create_user \
  --email admin@example.com --name "Admin" --password '<8+ characters>'
```

Traces go to fi-collector over OpenTelemetry, with the API keys from the UI:

| From | OTLP/gRPC | OTLP/HTTP |
| --- | --- | --- |
| Inside the cluster | `<release>-fi-collector.<namespace>.svc:4317` | `http://<release>-fi-collector.<namespace>.svc:4318/v1/traces` |
| Through the ingress (`ingress.otlp.enabled`) | not routed | `https://<api host>/v1/traces` |
| Outside, without an ingress | `fiCollector.service.type=LoadBalancer` | the same load balancer, port 4318 |
| Through a port-forward | not forwarded | `kubectl -n futureagi port-forward svc/futureagi-fi-collector 4318:4318`, then `http://localhost:4318/v1/traces` |

Future AGI SDKs take the OTLP/HTTP base URL as `FI_BASE_URL`. The app shows
the same URL in its SDK snippet and setup screen (`FI_COLLECTOR_PUBLIC_URL`):
`urls.otlp`, else the ingress's OTLP host, else `http://localhost:4318` (the
port-forward). Set `urls.otlp` when you expose the collector another way.

`helm test futureagi -n futureagi` checks that the API, UI, collector and
gateway answer.

## Upgrade

```sh
helm upgrade futureagi deploy/helm/futureagi --namespace futureagi -f my-values.yaml --timeout 20m
```

The bootstrap job runs first (`pre-upgrade`) with the new image: migrations,
seeds, ClickHouse schema, CDC and schedules, all idempotent. Only then are
the Deployments rolled. Generated secrets are read back and never
regenerated. Read the release notes for a migrations callout before
upgrading across several versions.

A failed bootstrap stops the upgrade before any Deployment is rolled:
`kubectl -n futureagi logs job/futureagi-bootstrap` says why. The chart's
Secret is written before the job (the job reads it), so a changed password or
key is already in place, and a pod that restarts meanwhile starts with it.
Fix the cause and run the upgrade again, or go back with
`helm rollback futureagi -n futureagi`, which also puts back the previous
revision's Secret (`--atomic` does this on its own). A job still running
after Helm's `--timeout` must finish before you retry: a new run replaces it
mid-way. `bootstrap.activeDeadlineSeconds` (18 minutes) stops it before the
documented `--timeout 20m`.

Some bundled datastore settings cannot change after install: see
[Install-time settings](#install-time-settings).

## Uninstall

```sh
helm uninstall futureagi --namespace futureagi
```

Kept on purpose, delete them when you mean it:

- `futureagi-secrets`: the generated keys. `INTEGRATION_ENCRYPTION_KEY`
  decrypts the integration credentials stored in Postgres; a reinstall reuses it.
- The bootstrap ServiceAccount (Helm hooks are not deleted by `uninstall`).
- The bundled datastores' PersistentVolumeClaims (`data-futureagi-postgres-0`, ...).

```sh
kubectl -n futureagi delete secret futureagi-secrets
kubectl -n futureagi delete serviceaccount futureagi-bootstrap
kubectl -n futureagi delete pvc -l app.kubernetes.io/instance=futureagi
```

## External and bundled datastores

Each datastore has `mode: external` (default) or `mode: bundled`, and they
mix freely (for example bundled Temporal with your own PostgreSQL).

| Datastore | External: set | Bundled: runs | Bundled image |
| --- | --- | --- | --- |
| PostgreSQL | `postgres.external.host`, `postgres.password` or `postgres.existingSecret` | StatefulSet, 20 GiB volume | `postgres:16` |
| ClickHouse | `clickhouse.external.host` (password optional) | StatefulSet, 50 GiB volume, the Standalone install's low-memory settings | `clickhouse/clickhouse-server:25.3-alpine` |
| Redis | `redis.external.host` (password optional) | StatefulSet with a password, append-only file on 2 GiB | `redis:7-alpine` |
| Temporal | `temporal.external.address` | the single-binary dev server, SQLite on a 5 GiB volume | `temporalio/temporal:1.9.1` |
| Object storage | `objectStorage.backend`, keys, `objectStorage.external.endpoint` | one MinIO server, 20 GiB volume | the last community MinIO release (as in Docker Compose) |

Bundled datastores are for evaluation: one replica each, no replication,
no backups, and upgrades of their images are yours to plan. Moving from
bundled to external means migrating the data yourself (`pg_dump`,
`clickhouse-backup`, `mc mirror`).

When any datastore is bundled, the bootstrap job runs after the release's
resources (`post-install`) because it has to reach them; the API answers
`/health/` meanwhile, the Temporal workers wait until the job's migrations
are applied, and Helm returns only once the job has finished.

With `postgres.external.sslMode` `verify-ca` or `verify-full`, give every
pod that connects to PostgreSQL the server's CA and point `PGSSLROOTCERT` at
it (the bootstrap job reuses `backend.extraVolumes` unless you set its own):

```yaml
config:
  extraEnv:
    PGSSLROOTCERT: /etc/pg-ca/ca.crt
backend:
  extraVolumes: &pgCaVolume
    - name: pg-ca
      secret: {secretName: pg-ca}
  extraVolumeMounts: &pgCaMount
    - {name: pg-ca, mountPath: /etc/pg-ca, readOnly: true}
worker:
  extraVolumes: *pgCaVolume
  extraVolumeMounts: *pgCaMount
fiCollector:
  extraEnv:
    PGSSLROOTCERT: /etc/pg-ca/ca.crt
  extraVolumes: *pgCaVolume
  extraVolumeMounts: *pgCaMount
```

### Install-time settings

A bundled datastore's volume comes from its StatefulSet's
`volumeClaimTemplates`, which Kubernetes does not let an upgrade change. These
keys are therefore fixed at install:

- `<datastore>.bundled.persistence.size` and `.storageClass` (and
  `global.storageClass`, which they default to), for `postgres`,
  `clickhouse`, `redis`, `temporal` and `objectStorage`;
- `redis.bundled.persistence.enabled`.

`helm upgrade` refuses a change to them and names the key (it compares with
the live StatefulSet; `helm template` cannot see one, so it does not check).
To grow a volume whose StorageClass allows expansion, resize the claim, drop
the StatefulSet without its pod, and upgrade with the new size, which
re-creates it:

```sh
kubectl -n futureagi patch pvc data-futureagi-postgres-0 \
  -p '{"spec":{"resources":{"requests":{"storage":"40Gi"}}}}'
kubectl -n futureagi delete statefulset futureagi-postgres --cascade=orphan
helm upgrade futureagi deploy/helm/futureagi -n futureagi --reuse-values \
  --set postgres.bundled.persistence.size=40Gi --timeout 20m
```

Turning Redis persistence on or off works the same way, without the patch.
A different StorageClass needs a new volume: move the data yourself.

## Secrets

The chart generates these once and keeps them in `<release>-secrets` (a
Helm hook, so it exists before the bootstrap job runs, and `helm rollback`
puts back the target revision's copy), reading them back on every upgrade:

| Key | What it is | If it changes |
| --- | --- | --- |
| `SECRET_KEY` | signs logins, tokens and links | everyone is signed out |
| `INTEGRATION_ENCRYPTION_KEY` | Fernet key of stored integration credentials | stored credentials cannot be decrypted |
| `AGENTCC_INTERNAL_API_KEY` | the backend's key on the LLM gateway | the pods pick up the new one on restart |
| `AGENTCC_ADMIN_TOKEN` | the gateway's admin API and control-plane sync | as above |
| `PROPERTY_CATALOG_API_PASSWORD`, `PROPERTY_CATALOG_CONSUMER_PASSWORD` | ClickHouse users of the observed-attribute index | the bootstrap job resets them |

Bundled datastores get generated passwords in the same Secret. A bundled
PostgreSQL keeps the password it was initialized with: set
`postgres.password` before the first install if you want a specific one.

To manage the application keys yourself (required with Argo CD and Flux),
create a Secret with all six keys and set `secrets.existingSecret`. Moving an
existing install over? Copy the six values out of `<release>-secrets` first:
new keys sign everyone out and cannot decrypt stored credentials.

```sh
kubectl -n futureagi create secret generic futureagi-app \
  --from-literal=SECRET_KEY="$(openssl rand -hex 32)" \
  --from-literal=INTEGRATION_ENCRYPTION_KEY="$(python3 -c 'import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())')" \
  --from-literal=AGENTCC_INTERNAL_API_KEY="$(openssl rand -hex 32)" \
  --from-literal=AGENTCC_ADMIN_TOKEN="$(openssl rand -hex 32)" \
  --from-literal=PROPERTY_CATALOG_API_PASSWORD="$(openssl rand -hex 16)" \
  --from-literal=PROPERTY_CATALOG_CONSUMER_PASSWORD="$(openssl rand -hex 16)"
```

LLM provider keys: `secrets.llm.*` inline, or `secrets.llm.existingSecret`
with any of `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`,
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`. Any other secret setting:
`secrets.extra` (stored in the chart's Secret) or `config.extraEnvFrom`.

## Public URLs and ingress

Without URLs the install is reachable through port-forwards only: the UI
calls the API on `http://localhost:8000`. For access from other machines,
either set `urls.app` and `urls.api` (your own proxy) or enable the ingress:

```yaml
ingress:
  enabled: true
  className: nginx
  tls:
    - secretName: futureagi-tls
      hosts: [futureagi.example.com, api.futureagi.example.com]
  app:
    host: futureagi.example.com
  api:
    host: api.futureagi.example.com     # must differ from the UI host
  otlp:
    enabled: true                       # /v1/traces on the API host -> fi-collector
```

The URLs follow from the hosts (https for hosts listed under `tls`), and set
`BASE_URL`, `FRONTEND_URL`, `APP_URL` (the UI's URL: invite and
password-reset links point there), the CSRF origins, the UI's API URL and,
with `ingress.otlp`, `FI_COLLECTOR_PUBLIC_URL`. Without them they are
`http://localhost:8000`, `http://localhost:3000` and `http://localhost:4318`,
the port-forwards.
The API needs WebSocket support and long timeouts at the ingress (dataset
uploads, LLM calls): see the annotations in `examples/ingress.yaml`.
With bundled object storage, `ingress.objects.host` publishes file downloads.

## Sizing

Requests below are the chart's defaults; limits are about 2 to 4 times higher.

| Profile | Replicas | CPU requests | Memory requests | Notes |
| --- | --- | --- | --- | --- |
| Evaluation (`examples/bundled.yaml`) | 1 of each | about 2.5 CPUs | about 5 GiB | datastores included |
| Small team | backend 2, worker 2 | about 3 CPUs | about 6 GiB | plus external datastores |
| Larger | backend HPA 2 to 6, worker HPA, collector HPA, per-queue workers for `tasks_s` and `trace_ingestion` | scale on CPU | 1 to 3 GiB per worker | size ClickHouse for your trace volume |

Scaling notes:

- `backend.autoscaling`, `worker.allQueues.autoscaling`,
  `fiCollector.autoscaling`, `agentccGateway.autoscaling` and
  `frontend.autoscaling` add HorizontalPodAutoscalers (metrics-server needed).
- Busy queues get their own Deployment with `worker.queues`, e.g.
  `[{name: tasks_s, replicas: 3, maxConcurrentActivities: 200}]`.
- The exact-aggregation worker stays at one replica with one slot: it is the
  admission boundary for expensive exact analytics.
- PostgreSQL connections: every backend thread and worker activity slot may
  hold one. Keep `max_connections` above the sum.

## Security

- Every pod runs as a non-root user with `RuntimeDefault` seccomp, no
  privilege escalation and all capabilities dropped. The application pods
  have read-only root filesystems (writable emptyDirs for /tmp, logs and
  static files). Override per component with `podSecurityContext` and
  `containerSecurityContext`.
- The code sandbox (`codeExecutor.enabled`) is the exception: nsjail needs a
  **privileged** container. Pod Security Admission `baseline` and
  `restricted` namespaces and some managed clusters refuse it. With the
  sandbox off, custom code evals are refused; set `codeExecutor.localFallback`
  to `true` only when everyone who can create evals is trusted: the code then
  runs inside the worker pods.
- `networkPolicy.enabled` limits the datastores, gateway, serving and the
  sandbox to this release's pods, lets `networkPolicy.ingressFrom` (or anyone)
  reach the UI, API and collector, and blocks the sandbox from every private,
  link-local and metadata address. A component whose Service is a
  `LoadBalancer` or `NodePort` accepts any source on its Service ports, since
  its clients arrive from outside the cluster.
- reCAPTCHA (`config.recaptcha`) is off by default. On, it guards sign-up,
  login and token refresh for every host but localhost, and needs
  `secrets.extra.RECAPTCHA_SECRET_KEY` plus a frontend image built with
  `VITE_GOOGLE_SITE_KEY` (a build-time setting the published image lacks):
  without both, every login is refused.
- No pod mounts a service account token.
- Deployment telemetry (`config.telemetry`) sends an instance id, the
  version, admin emails and usage counts every 6 hours; never traces, prompts
  or other content. HubSpot, Slack, PostHog and Sentry stay off unless you
  set their keys.

## GitOps (Argo CD, Flux)

Helm's `lookup` returns nothing under Argo CD, so generated secrets would
change on every sync. Set `secrets.existingSecret`, and give bundled
datastores explicit passwords (`postgres.password`, ...) or existing Secrets.
Argo CD maps the chart's hooks to sync phases: the Secret and the bootstrap
job to PreSync (PostSync for the job when a datastore is bundled).

## Troubleshooting

| Symptom | Look at |
| --- | --- |
| `helm install` fails at once with "fix these values" | the listed keys |
| `helm install` times out | `kubectl logs job/<release>-bootstrap`; let a running job finish before retrying; raise `--timeout` together with `bootstrap.activeDeadlineSeconds` (the first bootstrap migrates an empty database) |
| bootstrap: `... is not reachable after 600s` | the host and port in the values, NetworkPolicies, DNS |
| bootstrap: `Unknown command: 'bootstrap_install'` | the backend image predates this chart (every published image up to v1.41.1 does): set `image.tag` to images built from the chart's checkout |
| bootstrap or fi-collector: certificate verify failed | `PGSSLROOTCERT` and the CA mount (see [External and bundled datastores](#external-and-bundled-datastores)) |
| `helm upgrade`: `... cannot change` (StatefulSet ...) | [Install-time settings](#install-time-settings) |
| Workers log `waiting for the database migrations` | the bootstrap job: `kubectl logs job/<release>-bootstrap` |
| UI loads but every call fails | `urls.api` / `ingress.api.host`; with port-forwards, forward the backend to localhost:8000 |
| Custom code evals fail with "sandbox unavailable" | `codeExecutor` (see Security) |
| The first-run setup screen marks a service down | `kubectl get pods`, then that service's logs |

## Developing the chart

```sh
deploy/helm/futureagi/hack/check.sh             # lint, template, kubeconform, docs and schema
python3 deploy/helm/futureagi/hack/values_docs.py  # after editing values.yaml
TAG=local deploy/helm/futureagi/hack/kind-smoke.sh # install, check and upgrade on kind
```

Document every new key in `values.yaml` with a `# -- ` comment on the line
above it; `values_docs.py` regenerates `values.schema.json` and the table
below, and `check.sh` (run by `.github/workflows/helm-ci.yml`) fails when
they are stale.

## Values

Every key, generated from `values.yaml` by `hack/values_docs.py`.
Bracketed names are the environment variables a key sets;
[docs/configuration.md](../../../docs/configuration.md) explains each one.

<!-- values-table:start -->
| Key | Default | Description |
| --- | --- | --- |
| `nameOverride` | `""` | Override the chart name used in resource names. |
| `fullnameOverride` | `""` | Override the resource name prefix. Default: the release name, plus "-futureagi" unless the release name already contains it. |
| `global.imageRegistry` | `""` | Registry prepended to every image, Future AGI and third-party, e.g. a pull-through mirror (`registry.example.com/dockerhub`). Empty keeps each image's own registry. |
| `global.imagePullSecrets` | `[]` | Pull secrets added to every pod, e.g. `[{name: regcred}]`. |
| `global.storageClass` | `""` | StorageClass of every PersistentVolumeClaim the chart creates. Empty uses the cluster default. Install-time only for the bundled datastores (see "Install-time settings" in README.md). |
| `image.registry` | `"docker.io"` | Registry of the Future AGI images. |
| `image.tag` | `""` | Tag of every Future AGI image (backend, workers, frontend, fi-collector, agentcc-gateway, serving, code-executor). Empty uses the chart's appVersion. A component's own `image.tag` wins. |
| `image.pullPolicy` | `"IfNotPresent"` | Pull policy of every image unless a component sets its own. |
| `urls.app` | `""` | Public URL of the UI, e.g. `https://futureagi.example.com` [FRONTEND_URL, APP_URL, EXTRA_CSRF_ORIGINS]. Invite and password-reset links and app emails point here, with its scheme. Empty: derived from `ingress.app.host`, else `http://localhost:3000`. |
| `urls.api` | `""` | Public URL of the API, e.g. `https://api.futureagi.example.com` [BASE_URL, and the UI's VITE_HOST_API]. Empty: derived from `ingress.api.host`, else `http://localhost:8000`. |
| `urls.otlp` | `""` | [FI_COLLECTOR_PUBLIC_URL] Public OTLP/HTTP base URL of fi-collector, e.g. `https://otlp.example.com`: what SDKs outside the cluster set as FI_BASE_URL, shown in the install notes, the in-app SDK snippet and the setup screen. Set it when the collector is exposed another way (e.g. `fiCollector.service.type=LoadBalancer`). Empty: derived from `ingress.otlp`, else `http://localhost:4318` (the port-forward in the install notes). |
| `urls.objects` | `""` | Public URL browsers download stored files from [MINIO_URL]; used when `objectStorage.backend` is `minio`. Empty: derived from `ingress.objects.host`, else the external endpoint, else `http://localhost:9005`. |
| `config.envType` | `"production"` | [ENV_TYPE] `production`: JSON logs, DEBUG off, refuses published default secrets. `local`: colored console logs. |
| `config.logLevel` | `"INFO"` | [LOG_LEVEL] of the backend, workers and bootstrap job. |
| `config.allowedHosts` | `"*"` | [ALLOWED_HOSTS], comma-separated. `*` accepts any Host header. A restricted list also gets localhost and the in-cluster service names, which the probes and the workers use. |
| `config.corsAllowedOrigins` | `""` | [CORS_ALLOWED_ORIGINS], comma-separated. Empty allows every origin. |
| `config.extraCsrfOrigins` | `""` | [EXTRA_CSRF_ORIGINS] in addition to the UI URL, comma-separated. |
| `config.telemetry` | `true` | Deployment telemetry: an instance id, the version, admin emails and usage counts every 6 hours, never traces, prompts or other content. `false` sets FUTURE_AGI_TELEMETRY_DISABLED=true (one minimal ping remains). |
| `config.recaptcha` | `false` | [RECAPTCHA_ENABLED] reCAPTCHA on sign-up, login and token refresh, for every Host but localhost. Needs `secrets.extra.RECAPTCHA_SECRET_KEY` (without it every such login is refused) and a frontend image built with VITE_GOOGLE_SITE_KEY (a build-time setting: the published image has none, so its logins fail). |
| `config.otel` | `false` | [OTEL_ENABLED] export the platform's own traces over OpenTelemetry (configure OTEL_* in `config.extraEnv`). |
| `config.cdcMode` | `"outbox"` | [FI_CDC_MODE] Postgres to ClickHouse change data capture. `outbox`: triggers plus a drain in the Temporal workers (no PeerDB). `off` removes it (Observe views then miss relational data). |
| `config.email.mailgunSenderDomain` | `""` | [MAILGUN_SENDER_DOMAIN]. Email stays off until `secrets.mailgunApiKey` is set too; invites then return a link to share yourself. |
| `config.email.fromEmail` | `""` | [DEFAULT_FROM_EMAIL] sender of every app email (invites, password resets). Empty: `Future AGI <noreply@<mailgunSenderDomain>>`. |
| `config.email.replyTo` | `""` | [DEFAULT_REPLY_TO_EMAIL] Reply-To of app emails. Empty: no Reply-To header, so replies go to the sender. |
| `config.email.serverEmail` | `""` | [SERVER_EMAIL] sender of error emails. |
| `config.extraEnv` | `{}` | Extra environment variables for the backend, workers and bootstrap job, as `NAME: value`. Every supported key is in docs/configuration.md. |
| `config.extraEnvFrom` | `[]` | Extra `envFrom` sources for the backend, workers and bootstrap job, e.g. `[{secretRef: {name: my-env}}]`. |
| `secrets.existingSecret` | `""` | Existing Secret with the application keys: SECRET_KEY, INTEGRATION_ENCRYPTION_KEY, AGENTCC_INTERNAL_API_KEY, AGENTCC_ADMIN_TOKEN, PROPERTY_CATALOG_API_PASSWORD and PROPERTY_CATALOG_CONSUMER_PASSWORD. Empty: generated. Required with Argo CD or Flux, which cannot `lookup` the generated Secret. |
| `secrets.llm.existingSecret` | `""` | Existing Secret with any of OPENAI_API_KEY, ANTHROPIC_API_KEY, GOOGLE_API_KEY, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY (missing keys are fine). Overrides the values below. |
| `secrets.llm.openaiApiKey` | `""` | [OPENAI_API_KEY] server-wide key for built-in evals and the gateway. Workspaces can also add their own in the UI. |
| `secrets.llm.anthropicApiKey` | `""` | [ANTHROPIC_API_KEY] |
| `secrets.llm.googleApiKey` | `""` | [GOOGLE_API_KEY] Gemini (Google AI Studio); the gateway gets it as GEMINI_API_KEY. |
| `secrets.llm.awsAccessKeyId` | `""` | [AWS_ACCESS_KEY_ID] AWS Bedrock. |
| `secrets.llm.awsSecretAccessKey` | `""` | [AWS_SECRET_ACCESS_KEY] AWS Bedrock. |
| `secrets.llm.awsRegion` | `"us-east-1"` | [AWS_REGION] AWS Bedrock region (not secret). |
| `secrets.eeLicenseKey` | `""` | [EE_LICENSE_KEY] Enterprise Edition license. |
| `secrets.mailgunApiKey` | `""` | [MAILGUN_API_KEY] turns email on, with `config.email`. |
| `secrets.agentccWebhookSecret` | `""` | [AGENTCC_WEBHOOK_SECRET] shared secret the gateway sends to the backend's webhooks. |
| `secrets.extra` | `{}` | Any other secret environment variables for the backend, workers and bootstrap job, as `NAME: value` (e.g. SENTRY_DSN, DAYTONA_API_KEY). Stored in <fullname>-secrets. |
| `postgres.mode` | `"external"` | `external` or `bundled`. |
| `postgres.database` | `"futureagi"` | [PG_DB] database name. |
| `postgres.user` | `"futureagi"` | [PG_USER] |
| `postgres.password` | `""` | [PG_PASSWORD]. External: required unless `existingSecret`. Bundled: empty generates one. |
| `postgres.existingSecret` | `""` | Existing Secret holding the password. |
| `postgres.existingSecretPasswordKey` | `"password"` | Key of the password in `existingSecret`. |
| `postgres.external.host` | `""` | [PG_HOST] PostgreSQL 16 server (11 or newer works). Connect directly: a transaction-mode pooler breaks the CDC advisory lock. |
| `postgres.external.port` | `5432` | [PG_PORT] |
| `postgres.external.sslMode` | `"prefer"` | [PGSSLMODE] disable, allow, prefer, require, verify-ca or verify-full. verify-ca and verify-full need the server's CA: mount it with `backend`, `worker`, `bootstrap` and `fiCollector` `extraVolumes`/`extraVolumeMounts`, and point PGSSLROOTCERT at it in `config.extraEnv` and `fiCollector.extraEnv`. |
| `postgres.bundled.image.registry` | `"docker.io"` | Registry of the bundled PostgreSQL image. |
| `postgres.bundled.image.repository` | `"library/postgres"` | Repository of the bundled PostgreSQL image. Debian-based, like the compose setups, so data directories are interchangeable. |
| `postgres.bundled.image.tag` | `"16"` | Tag of the bundled PostgreSQL image. |
| `postgres.bundled.image.digest` | `""` | Optional digest (`sha256:...`) pinned after the tag. |
| `postgres.bundled.parameters` | `{"max_connections": "300", "shared_buffers": "256MB"}` | Server settings passed as `-c name=value`. |
| `postgres.bundled.persistence.size` | `"20Gi"` | Size of the data volume. Install-time only (see "Install-time settings" in README.md). |
| `postgres.bundled.persistence.storageClass` | `""` | StorageClass of the data volume. Empty: `global.storageClass`, else the cluster default. Install-time only. |
| `postgres.bundled.resources` | see values.yaml | Resources of the bundled PostgreSQL. |
| `postgres.bundled.nodeSelector` | `{}` | Node selector of the bundled PostgreSQL. Empty: the top-level `nodeSelector`. |
| `postgres.bundled.tolerations` | `[]` | Tolerations of the bundled PostgreSQL. Empty: the top-level `tolerations`. |
| `postgres.bundled.affinity` | `{}` | Affinity of the bundled PostgreSQL. Empty: the top-level `affinity`. |
| `clickhouse.mode` | `"external"` | `external` or `bundled`. ClickHouse 25.3 or newer. |
| `clickhouse.database` | `"default"` | [CH_DATABASE, CH25_DATABASE] database of traces and analytics. |
| `clickhouse.user` | `"default"` | [CH_USERNAME] needs CREATE on the database; the bootstrap also creates the observed-attribute index (CREATE DATABASE, CREATE USER, GRANT) unless `bootstrap.propertyCatalog` is false. |
| `clickhouse.password` | `""` | [CH_PASSWORD]. External: may be empty. Bundled: empty generates one. |
| `clickhouse.existingSecret` | `""` | Existing Secret holding the password. |
| `clickhouse.existingSecretPasswordKey` | `"password"` | Key of the password in `existingSecret`. |
| `clickhouse.propertyCatalogDatabase` | `"property_catalog"` | [PROPERTY_CATALOG_DATABASE] database of the observed-attribute index; must differ from `database`. |
| `clickhouse.external.host` | `""` | [CH_HOST] ClickHouse host. Plain HTTP and native protocol: the schema installer and fi-collector do not speak TLS to ClickHouse. |
| `clickhouse.external.httpPort` | `8123` | [CH_HTTP_PORT] |
| `clickhouse.external.nativePort` | `9000` | [CH_PORT] native protocol port. |
| `clickhouse.bundled.image.registry` | `"docker.io"` | Registry of the bundled ClickHouse image. |
| `clickhouse.bundled.image.repository` | `"clickhouse/clickhouse-server"` | Repository of the bundled ClickHouse image. |
| `clickhouse.bundled.image.tag` | `"25.3-alpine"` | Tag of the bundled ClickHouse image. 25.3 is the floor for the v2 spans schema. |
| `clickhouse.bundled.image.digest` | `""` | Optional digest (`sha256:...`) pinned after the tag. |
| `clickhouse.bundled.lowMemory` | `true` | Small caches and merge pools and no system log tables (deploy/platform/clickhouse), as in the Standalone install. Turn off on nodes with 8 GiB or more for ClickHouse. |
| `clickhouse.bundled.persistence.size` | `"50Gi"` | Size of the data volume. Install-time only (see "Install-time settings" in README.md). |
| `clickhouse.bundled.persistence.storageClass` | `""` | StorageClass of the data volume. Empty: `global.storageClass`, else the cluster default. Install-time only. |
| `clickhouse.bundled.resources` | see values.yaml | Resources of the bundled ClickHouse. |
| `clickhouse.bundled.nodeSelector` | `{}` | Node selector of the bundled ClickHouse. Empty: the top-level `nodeSelector`. |
| `clickhouse.bundled.tolerations` | `[]` | Tolerations of the bundled ClickHouse. Empty: the top-level `tolerations`. |
| `clickhouse.bundled.affinity` | `{}` | Affinity of the bundled ClickHouse. Empty: the top-level `affinity`. |
| `redis.mode` | `"external"` | `external` or `bundled`. Cache, locks and the live-update channel layer. |
| `redis.password` | `""` | [REDIS_PASSWORD] Must be URL-safe (letters, digits, `-._~`): it is part of the Redis URLs. External: empty means no AUTH. Bundled: empty generates one. |
| `redis.existingSecret` | `""` | Existing Secret holding the password. |
| `redis.existingSecretPasswordKey` | `"password"` | Key of the password in `existingSecret`. |
| `redis.external.host` | `""` | [REDIS_HOST] Redis 6 or newer with databases 0-3 (the app uses 0 to 3). |
| `redis.external.port` | `6379` | [REDIS_PORT] |
| `redis.external.tls` | `false` | Connect the app over TLS (`rediss://`). fi-collector has no Redis TLS: with TLS on it runs without Redis (key revocation then reaches it through its 5-minute auth cache). |
| `redis.bundled.image.registry` | `"docker.io"` | Registry of the bundled Redis image. |
| `redis.bundled.image.repository` | `"library/redis"` | Repository of the bundled Redis image. |
| `redis.bundled.image.tag` | `"7-alpine"` | Tag of the bundled Redis image. |
| `redis.bundled.image.digest` | `""` | Optional digest (`sha256:...`) pinned after the tag. |
| `redis.bundled.maxmemory` | `"384mb"` | Redis `maxmemory`; keep it below the memory limit. |
| `redis.bundled.maxmemoryPolicy` | `"volatile-lru"` | Redis `maxmemory-policy`. volatile-lru evicts only keys with a TTL (cache entries), never locks or state. |
| `redis.bundled.persistence.enabled` | `true` | Keep an append-only file on a volume. Without it a restart drops cache, locks and in-flight state (the app recovers). Install-time only. |
| `redis.bundled.persistence.size` | `"2Gi"` | Size of the data volume. Install-time only (see "Install-time settings" in README.md). |
| `redis.bundled.persistence.storageClass` | `""` | StorageClass of the data volume. Empty: `global.storageClass`, else the cluster default. Install-time only. |
| `redis.bundled.resources` | see values.yaml | Resources of the bundled Redis. |
| `redis.bundled.nodeSelector` | `{}` | Node selector of the bundled Redis. Empty: the top-level `nodeSelector`. |
| `redis.bundled.tolerations` | `[]` | Tolerations of the bundled Redis. Empty: the top-level `tolerations`. |
| `redis.bundled.affinity` | `{}` | Affinity of the bundled Redis. Empty: the top-level `affinity`. |
| `temporal.mode` | `"external"` | `external` (a Temporal cluster, e.g. the temporalio/temporal Helm chart) or `bundled` (the single-binary dev server with SQLite on a volume). |
| `temporal.namespace` | `"default"` | [TEMPORAL_NAMESPACE] must exist on an external server (the bundled one creates it). |
| `temporal.external.address` | `""` | [TEMPORAL_HOST] frontend `host:port`, e.g. `temporal-frontend.temporal.svc:7233`. Plain gRPC: TLS and Temporal Cloud API keys are not supported yet. |
| `temporal.bundled.image.registry` | `"docker.io"` | Registry of the bundled Temporal image (the Temporal CLI, `temporal server start-dev`). |
| `temporal.bundled.image.repository` | `"temporalio/temporal"` | Repository of the bundled Temporal image. |
| `temporal.bundled.image.tag` | `"1.9.1"` | Tag of the bundled Temporal image; the version the Standalone install ships. |
| `temporal.bundled.image.digest` | `""` | Optional digest (`sha256:...`) pinned after the tag. |
| `temporal.bundled.ui` | `false` | Serve the Temporal Web UI on port 8233 of the temporal Service (`kubectl port-forward`). |
| `temporal.bundled.persistence.size` | `"5Gi"` | Size of the SQLite volume (schedules and workflow history). Install-time only (see "Install-time settings" in README.md). |
| `temporal.bundled.persistence.storageClass` | `""` | StorageClass of the volume. Empty: `global.storageClass`, else the cluster default. Install-time only. |
| `temporal.bundled.goMemLimit` | `"512MiB"` | [GOMEMLIMIT] soft memory limit of the server; keep it below the memory limit. |
| `temporal.bundled.resources` | see values.yaml | Resources of the bundled Temporal server. |
| `temporal.bundled.nodeSelector` | `{}` | Node selector of the bundled Temporal. Empty: the top-level `nodeSelector`. |
| `temporal.bundled.tolerations` | `[]` | Tolerations of the bundled Temporal. Empty: the top-level `tolerations`. |
| `temporal.bundled.affinity` | `{}` | Affinity of the bundled Temporal. Empty: the top-level `affinity`. |
| `objectStorage.mode` | `"external"` | `external` (AWS S3, GCS or any S3-compatible service) or `bundled` (one MinIO, for evaluation). |
| `objectStorage.backend` | `"s3"` | [STORAGE_BACKEND] `s3` (AWS), `gcs` (Google Cloud Storage with HMAC keys) or `minio` (any other S3-compatible endpoint). Bundled always uses `minio`. |
| `objectStorage.bucket` | `"futureagi"` | [UPLOAD_BUCKET_NAME] bucket for uploads, datasets and exports. Created on the first upload (not on GCS) with a public-read policy. |
| `objectStorage.region` | `"us-east-1"` | [S3_REGION, AWS_DEFAULT_REGION] bucket region. |
| `objectStorage.accessKey` | `""` | [S3_ACCESS_KEY, or GCS_HMAC_ACCESS_KEY for gcs]. External: required unless `existingSecret`. Bundled: empty generates one. |
| `objectStorage.secretKey` | `""` | [S3_SECRET_KEY, or GCS_HMAC_SECRET_KEY for gcs]. External: required unless `existingSecret`. Bundled: empty generates one. |
| `objectStorage.existingSecret` | `""` | Existing Secret holding the access and secret keys. |
| `objectStorage.existingSecretAccessKeyKey` | `"access-key"` | Key of the access key in `existingSecret`. |
| `objectStorage.existingSecretSecretKeyKey` | `"secret-key"` | Key of the secret key in `existingSecret`. |
| `objectStorage.external.endpoint` | `""` | [S3_ENDPOINT_URL] e.g. `https://s3.us-east-1.amazonaws.com` or `http://minio.storage:9000`. Empty with `s3` uses s3.amazonaws.com. |
| `objectStorage.bundled.image.registry` | `"ghcr.io"` | Registry of the bundled object storage image. |
| `objectStorage.bundled.image.repository` | `"coollabsio/minio"` | Repository of the bundled object storage image: the last community MinIO release, as in the compose setups. |
| `objectStorage.bundled.image.tag` | `"RELEASE.2025-10-15T17-29-55Z"` | Tag of the bundled object storage image. |
| `objectStorage.bundled.image.digest` | see values.yaml | Digest pinned after the tag. |
| `objectStorage.bundled.persistence.size` | `"20Gi"` | Size of the data volume. Install-time only (see "Install-time settings" in README.md). |
| `objectStorage.bundled.persistence.storageClass` | `""` | StorageClass of the data volume. Empty: `global.storageClass`, else the cluster default. Install-time only. |
| `objectStorage.bundled.goMemLimit` | `"384MiB"` | [GOMEMLIMIT] soft memory limit; keep it below the memory limit. |
| `objectStorage.bundled.resources` | see values.yaml | Resources of the bundled object storage. |
| `objectStorage.bundled.nodeSelector` | `{}` | Node selector of the bundled object storage. Empty: the top-level `nodeSelector`. |
| `objectStorage.bundled.tolerations` | `[]` | Tolerations of the bundled object storage. Empty: the top-level `tolerations`. |
| `objectStorage.bundled.affinity` | `{}` | Affinity of the bundled object storage. Empty: the top-level `affinity`. |
| `backend.image.registry` | `""` | Registry. Empty: `image.registry`. |
| `backend.image.repository` | `"futureagi/future-agi"` | Repository of the backend image (also the workers' and the bootstrap job's). |
| `backend.image.tag` | `""` | Tag. Empty: `image.tag`, else the chart's appVersion. |
| `backend.image.digest` | `""` | Optional digest (`sha256:...`) pinned after the tag. |
| `backend.image.pullPolicy` | `""` | Pull policy. Empty: `image.pullPolicy`. |
| `backend.replicas` | `1` | Replicas when autoscaling is off. More than one needs the Redis channel layer, which this chart always configures. |
| `backend.granian.workers` | `1` | Granian worker processes per pod. |
| `backend.granian.threads` | `2` | Runtime threads per Granian worker. |
| `backend.granian.accessLog` | `false` | Log every request. |
| `backend.collectStatic` | `true` | Run collectstatic into an emptyDir before the API starts (static files of /admin and /docs). |
| `backend.service.type` | `"ClusterIP"` | Service type of the API. |
| `backend.service.port` | `8000` | Service port of the API. |
| `backend.service.annotations` | `{}` | Service annotations. |
| `backend.resources` | see values.yaml | Resources of each API pod. |
| `backend.autoscaling.enabled` | `false` | HorizontalPodAutoscaler for the API. |
| `backend.autoscaling.minReplicas` | `2` | Minimum replicas. |
| `backend.autoscaling.maxReplicas` | `6` | Maximum replicas. |
| `backend.autoscaling.targetCPUUtilizationPercentage` | `70` | Target average CPU utilization (percent of requests). |
| `backend.autoscaling.targetMemoryUtilizationPercentage` | `""` | Target average memory utilization (percent of requests). Empty: not used. |
| `backend.pdb.enabled` | `true` | PodDisruptionBudget for the API. |
| `backend.pdb.maxUnavailable` | `1` | At most this many API pods down during voluntary disruptions. |
| `backend.extraEnv` | `{}` | Extra environment variables for the API only, as `NAME: value`. |
| `backend.podAnnotations` | `{}` | Pod annotations. |
| `backend.podLabels` | `{}` | Extra pod labels. |
| `backend.podSecurityContext` | `{}` | Merged over the chart's pod security context (non-root uid 1000, fsGroup 1000, RuntimeDefault seccomp). |
| `backend.containerSecurityContext` | `{}` | Merged over the chart's container security context (read-only root filesystem, no privilege escalation, all capabilities dropped). |
| `backend.extraVolumes` | `[]` | Extra volumes. |
| `backend.extraVolumeMounts` | `[]` | Extra volume mounts. |
| `backend.nodeSelector` | `{}` | Node selector. Empty: the top-level `nodeSelector`. |
| `backend.tolerations` | `[]` | Tolerations. Empty: the top-level `tolerations`. |
| `backend.affinity` | `{}` | Affinity. Empty: the top-level `affinity`. |
| `backend.topologySpreadConstraints` | `[]` | Topology spread constraints. Empty: the top-level `topologySpreadConstraints`. |
| `worker.image.registry` | `""` | Registry. Empty: `backend.image.registry`, else `image.registry`. |
| `worker.image.repository` | `""` | Repository. Empty: `backend.image.repository` (workers run the backend image). |
| `worker.image.tag` | `""` | Tag. Empty: `backend.image.tag`, else `image.tag`, else the chart's appVersion. |
| `worker.image.digest` | `""` | Optional digest. Empty: `backend.image.digest`. |
| `worker.image.pullPolicy` | `""` | Pull policy. Empty: `backend.image.pullPolicy`, else `image.pullPolicy`. |
| `worker.gracefulShutdownSeconds` | `60` | [TEMPORAL_GRACEFUL_SHUTDOWN_TIMEOUT] seconds a stopping worker lets running activities finish. The pod's grace period is 30 s longer. |
| `worker.allQueues.enabled` | `true` | One Deployment polling every generic queue (default, tasks_s, tasks_l, tasks_xl, trace_ingestion, agent_compass). This worker also drains the outbox CDC. |
| `worker.allQueues.replicas` | `1` | Replicas when autoscaling is off. |
| `worker.allQueues.excludedQueues` | `["simulation_runner"]` | [TEMPORAL_EXCLUDED_QUEUES] queues it does not poll. simulation_runner needs the separate simulation-runner image (a `queues` entry with its own image). |
| `worker.allQueues.maxConcurrentActivities` | `50` | [TEMPORAL_MAX_CONCURRENT_ACTIVITIES] activity slots per queue. |
| `worker.allQueues.maxConcurrentWorkflowTasks` | `50` | [TEMPORAL_MAX_CONCURRENT_WORKFLOW_TASKS] workflow-task slots per queue. |
| `worker.allQueues.resources` | see values.yaml | Resources of each all-queues worker pod. |
| `worker.allQueues.autoscaling.enabled` | `false` | HorizontalPodAutoscaler for the all-queues worker. |
| `worker.allQueues.autoscaling.minReplicas` | `1` | Minimum replicas. |
| `worker.allQueues.autoscaling.maxReplicas` | `4` | Maximum replicas. |
| `worker.allQueues.autoscaling.targetCPUUtilizationPercentage` | `70` | Target average CPU utilization (percent of requests). |
| `worker.exactAggregation.enabled` | `true` | A single-slot worker for the exact_aggregation queue, the admission boundary for expensive exact analytics [EXACT_AGGREGATION_TASK_QUEUE=exact_aggregation]. Off: that work runs on tasks_xl. |
| `worker.exactAggregation.resources` | see values.yaml | Resources of the exact-aggregation worker. |
| `worker.queues` | `[]` | Dedicated per-queue Deployments, added to (not replacing) the all-queues worker, e.g. `[{name: tasks_s, replicas: 2, maxConcurrentActivities: 200}]`. Each entry: name (required), replicas, maxConcurrentActivities, maxConcurrentWorkflowTasks, resources, image, extraEnv (wins over `worker.extraEnv` and the chart's own values). |
| `worker.pdb.enabled` | `true` | PodDisruptionBudget for every worker Deployment. |
| `worker.pdb.maxUnavailable` | `1` | At most this many pods of each worker Deployment down during voluntary disruptions. |
| `worker.extraEnv` | `{}` | Extra environment variables for the workers only, as `NAME: value`. |
| `worker.podAnnotations` | `{}` | Pod annotations. |
| `worker.podLabels` | `{}` | Extra pod labels. |
| `worker.podSecurityContext` | `{}` | Merged over the chart's pod security context (as for the backend). |
| `worker.containerSecurityContext` | `{}` | Merged over the chart's container security context (as for the backend). |
| `worker.extraVolumes` | `[]` | Extra volumes. |
| `worker.extraVolumeMounts` | `[]` | Extra volume mounts. |
| `worker.nodeSelector` | `{}` | Node selector. Empty: the top-level `nodeSelector`. |
| `worker.tolerations` | `[]` | Tolerations. Empty: the top-level `tolerations`. |
| `worker.affinity` | `{}` | Affinity. Empty: the top-level `affinity`. |
| `worker.topologySpreadConstraints` | `[]` | Topology spread constraints. Empty: the top-level `topologySpreadConstraints`. |
| `frontend.image.registry` | `""` | Registry. Empty: `image.registry`. |
| `frontend.image.repository` | `"futureagi/frontend"` | Repository of the UI image. |
| `frontend.image.tag` | `""` | Tag. Empty: `image.tag`, else the chart's appVersion. |
| `frontend.image.digest` | `""` | Optional digest (`sha256:...`) pinned after the tag. |
| `frontend.image.pullPolicy` | `""` | Pull policy. Empty: `image.pullPolicy`. |
| `frontend.replicas` | `1` | Replicas when autoscaling is off. |
| `frontend.helpLink` | `""` | [VITE_HELP_LINK] where the sidebar Help entry points. Empty: the community Discord. |
| `frontend.service.type` | `"ClusterIP"` | Service type of the UI. |
| `frontend.service.port` | `80` | Service port of the UI. |
| `frontend.service.annotations` | `{}` | Service annotations. |
| `frontend.resources` | see values.yaml | Resources of each UI pod. |
| `frontend.autoscaling.enabled` | `false` | HorizontalPodAutoscaler for the UI. |
| `frontend.autoscaling.minReplicas` | `2` | Minimum replicas. |
| `frontend.autoscaling.maxReplicas` | `4` | Maximum replicas. |
| `frontend.autoscaling.targetCPUUtilizationPercentage` | `70` | Target average CPU utilization (percent of requests). |
| `frontend.pdb.enabled` | `true` | PodDisruptionBudget for the UI. |
| `frontend.pdb.maxUnavailable` | `1` | At most this many UI pods down during voluntary disruptions. |
| `frontend.extraEnv` | `{}` | Extra environment variables for the UI (runtime VITE_* settings), as `NAME: value`. |
| `frontend.podAnnotations` | `{}` | Pod annotations. |
| `frontend.podLabels` | `{}` | Extra pod labels. |
| `frontend.podSecurityContext` | `{}` | Merged over the chart's pod security context (non-root uid 101, the image's nginx user). |
| `frontend.containerSecurityContext` | `{}` | Merged over the chart's container security context (read-only root filesystem). |
| `frontend.nodeSelector` | `{}` | Node selector. Empty: the top-level `nodeSelector`. |
| `frontend.tolerations` | `[]` | Tolerations. Empty: the top-level `tolerations`. |
| `frontend.affinity` | `{}` | Affinity. Empty: the top-level `affinity`. |
| `frontend.topologySpreadConstraints` | `[]` | Topology spread constraints. Empty: the top-level `topologySpreadConstraints`. |
| `fiCollector.image.registry` | `""` | Registry. Empty: `image.registry`. |
| `fiCollector.image.repository` | `"futureagi/fi-collector"` | Repository of the OTLP collector image. |
| `fiCollector.image.tag` | `""` | Tag. Empty: `image.tag`, else the chart's appVersion. |
| `fiCollector.image.digest` | `""` | Optional digest (`sha256:...`) pinned after the tag. |
| `fiCollector.image.pullPolicy` | `""` | Pull policy. Empty: `image.pullPolicy`. |
| `fiCollector.replicas` | `1` | Replicas when autoscaling is off. |
| `fiCollector.service.type` | `"ClusterIP"` | Service type. `LoadBalancer` exposes OTLP/gRPC (4317) outside the cluster; OTLP/HTTP can go through the ingress instead. |
| `fiCollector.service.grpcPort` | `4317` | OTLP/gRPC port. |
| `fiCollector.service.httpPort` | `4318` | OTLP/HTTP port (POST /v1/traces). |
| `fiCollector.service.annotations` | `{}` | Service annotations (e.g. for a cloud load balancer). |
| `fiCollector.goMemLimit` | `"900MiB"` | [GOMEMLIMIT] soft memory limit; keep it below the memory limit. |
| `fiCollector.resources` | see values.yaml | Resources of each collector pod. |
| `fiCollector.autoscaling.enabled` | `false` | HorizontalPodAutoscaler for the collector. |
| `fiCollector.autoscaling.minReplicas` | `2` | Minimum replicas. |
| `fiCollector.autoscaling.maxReplicas` | `6` | Maximum replicas. |
| `fiCollector.autoscaling.targetCPUUtilizationPercentage` | `70` | Target average CPU utilization (percent of requests). |
| `fiCollector.pdb.enabled` | `true` | PodDisruptionBudget for the collector. |
| `fiCollector.pdb.maxUnavailable` | `1` | At most this many collector pods down during voluntary disruptions. |
| `fiCollector.extraEnv` | `{}` | Extra environment variables (FI_* overrides, see fi-collector/README.md), as `NAME: value`. |
| `fiCollector.podAnnotations` | `{}` | Pod annotations. |
| `fiCollector.podLabels` | `{}` | Extra pod labels. |
| `fiCollector.podSecurityContext` | `{}` | Merged over the chart's pod security context (non-root uid 65532, distroless). |
| `fiCollector.containerSecurityContext` | `{}` | Merged over the chart's container security context (read-only root filesystem). |
| `fiCollector.extraVolumes` | `[]` | Extra volumes, e.g. the CA of `postgres.external.sslMode=verify-full`. |
| `fiCollector.extraVolumeMounts` | `[]` | Extra volume mounts. |
| `fiCollector.nodeSelector` | `{}` | Node selector. Empty: the top-level `nodeSelector`. |
| `fiCollector.tolerations` | `[]` | Tolerations. Empty: the top-level `tolerations`. |
| `fiCollector.affinity` | `{}` | Affinity. Empty: the top-level `affinity`. |
| `fiCollector.topologySpreadConstraints` | `[]` | Topology spread constraints. Empty: the top-level `topologySpreadConstraints`. |
| `agentccGateway.image.registry` | `""` | Registry. Empty: `image.registry`. |
| `agentccGateway.image.repository` | `"futureagi/agentcc-gateway"` | Repository of the LLM gateway image. |
| `agentccGateway.image.tag` | `""` | Tag. Empty: `image.tag`, else the chart's appVersion. |
| `agentccGateway.image.digest` | `""` | Optional digest (`sha256:...`) pinned after the tag. |
| `agentccGateway.image.pullPolicy` | `""` | Pull policy. Empty: `image.pullPolicy`. |
| `agentccGateway.replicas` | `1` | Replicas when autoscaling is off. |
| `agentccGateway.existingConfigMap` | `""` | Existing ConfigMap with the gateway configuration under the key `config.yaml`. Empty: rendered from `config`. |
| `agentccGateway.config` | see values.yaml | Gateway configuration (agentcc-gateway/config.example.yaml documents every field). `${VAR}` expands from the environment: provider keys come from `secrets.llm`. `server.port` is also the container port: the chart pins it with the AGENTCC_PORT variable, which wins over an `existingConfigMap` too. |
| `agentccGateway.controlPlaneSync` | `true` | Pull keys and org settings from the backend on start [AGENTCC_CONTROL_PLANE_URL, AGENTCC_SYNC_ON_STARTUP], so every replica and a restarted pod serve the same keys. |
| `agentccGateway.gcpCredentials.existingSecret` | `""` | Existing Secret with a GCP service-account JSON for Vertex AI, mounted read-only [GOOGLE_APPLICATION_CREDENTIALS]. |
| `agentccGateway.gcpCredentials.key` | `"credentials.json"` | Key of the JSON in `existingSecret`. |
| `agentccGateway.service.type` | `"ClusterIP"` | Service type of the gateway. Keep it internal unless your apps call the gateway directly. |
| `agentccGateway.service.port` | `8080` | Service port of the gateway. |
| `agentccGateway.service.annotations` | `{}` | Service annotations. |
| `agentccGateway.goMemLimit` | `"450MiB"` | [GOMEMLIMIT] soft memory limit; keep it below the memory limit. |
| `agentccGateway.resources` | see values.yaml | Resources of each gateway pod. |
| `agentccGateway.autoscaling.enabled` | `false` | HorizontalPodAutoscaler for the gateway. |
| `agentccGateway.autoscaling.minReplicas` | `2` | Minimum replicas. |
| `agentccGateway.autoscaling.maxReplicas` | `6` | Maximum replicas. |
| `agentccGateway.autoscaling.targetCPUUtilizationPercentage` | `70` | Target average CPU utilization (percent of requests). |
| `agentccGateway.pdb.enabled` | `true` | PodDisruptionBudget for the gateway. |
| `agentccGateway.pdb.maxUnavailable` | `1` | At most this many gateway pods down during voluntary disruptions. |
| `agentccGateway.extraEnv` | `{}` | Extra environment variables (AGENTCC_* and provider keys referenced by `config`), as `NAME: value`. |
| `agentccGateway.extraEnvFrom` | `[]` | Extra `envFrom` sources, e.g. a Secret with more provider keys. |
| `agentccGateway.podAnnotations` | `{}` | Pod annotations. |
| `agentccGateway.podLabels` | `{}` | Extra pod labels. |
| `agentccGateway.podSecurityContext` | `{}` | Merged over the chart's pod security context (non-root uid 65532; the image is a static binary). |
| `agentccGateway.containerSecurityContext` | `{}` | Merged over the chart's container security context (read-only root filesystem). |
| `agentccGateway.nodeSelector` | `{}` | Node selector. Empty: the top-level `nodeSelector`. |
| `agentccGateway.tolerations` | `[]` | Tolerations. Empty: the top-level `tolerations`. |
| `agentccGateway.affinity` | `{}` | Affinity. Empty: the top-level `affinity`. |
| `agentccGateway.topologySpreadConstraints` | `[]` | Topology spread constraints. Empty: the top-level `topologySpreadConstraints`. |
| `serving.enabled` | `false` | Embedding model server for embedding-based evals, knowledge bases, Vector DB columns and Error Feed clustering [MODEL_SERVING_URL]. Off: those features are unavailable and the setup screen marks them off. |
| `serving.image.registry` | `""` | Registry. Empty: `image.registry`. |
| `serving.image.repository` | `"futureagi/serving"` | Repository of the model server image. |
| `serving.image.tag` | `""` | Tag. Empty: `image.tag`, else the chart's appVersion. Append `-gpu` for the CUDA image (linux/amd64). |
| `serving.image.digest` | `""` | Optional digest (`sha256:...`) pinned after the tag. |
| `serving.image.pullPolicy` | `""` | Pull policy. Empty: `image.pullPolicy`. |
| `serving.replicas` | `1` | Replicas. |
| `serving.resources` | see values.yaml | Resources of each model server pod (add `nvidia.com/gpu` for the GPU image). |
| `serving.persistence.enabled` | `false` | Keep downloaded models on a volume (ReadWriteOnce: one replica). Off: an emptyDir, downloaded again after each restart. |
| `serving.persistence.size` | `"20Gi"` | Size of the model cache. |
| `serving.persistence.storageClass` | `""` | StorageClass of the model cache. Empty: `global.storageClass`, else the cluster default. |
| `serving.extraEnv` | `{}` | Extra environment variables (e.g. TEXT_EMBEDDING_MODEL), as `NAME: value`. |
| `serving.podAnnotations` | `{}` | Pod annotations. |
| `serving.podLabels` | `{}` | Extra pod labels. |
| `serving.podSecurityContext` | `{}` | Merged over the chart's pod security context (non-root uid 1000). |
| `serving.containerSecurityContext` | `{}` | Merged over the chart's container security context. |
| `serving.nodeSelector` | `{}` | Node selector. Empty: the top-level `nodeSelector`. |
| `serving.tolerations` | `[]` | Tolerations. Empty: the top-level `tolerations`. |
| `serving.affinity` | `{}` | Affinity. Empty: the top-level `affinity`. |
| `codeExecutor.enabled` | `false` | nsjail sandbox for custom code evals. It needs `privileged: true` (Linux namespaces): Pod Security Admission `restricted`/`baseline` namespaces and some managed clusters (GKE Autopilot, Fargate) refuse it. |
| `codeExecutor.localFallback` | `false` | [CODE_EXECUTOR_LOCAL_FALLBACK] with the sandbox off (or unreachable), run custom code evals inside the worker pods, next to the platform's secrets (RestrictedPython in a subprocess). `false` (default) refuses them with "Code executor unavailable". Enable only when everyone who can create evals is trusted. |
| `codeExecutor.image.registry` | `""` | Registry. Empty: `image.registry`. |
| `codeExecutor.image.repository` | `"futureagi/code-executor"` | Repository of the code sandbox image. |
| `codeExecutor.image.tag` | `""` | Tag. Empty: `image.tag`, else the chart's appVersion. |
| `codeExecutor.image.digest` | `""` | Optional digest (`sha256:...`) pinned after the tag. |
| `codeExecutor.image.pullPolicy` | `""` | Pull policy. Empty: `image.pullPolicy`. |
| `codeExecutor.replicas` | `1` | Replicas. |
| `codeExecutor.resources` | see values.yaml | Resources of each sandbox pod. |
| `codeExecutor.allowInternetEgress` | `true` | With `networkPolicy.enabled`: let eval code reach the internet (every private, link-local and metadata range stays blocked). `false` blocks all egress but DNS. |
| `codeExecutor.extraEnv` | `{}` | Extra environment variables, as `NAME: value`. |
| `codeExecutor.podAnnotations` | `{}` | Pod annotations. |
| `codeExecutor.podLabels` | `{}` | Extra pod labels. |
| `codeExecutor.nodeSelector` | `{}` | Node selector. Empty: the top-level `nodeSelector`. |
| `codeExecutor.tolerations` | `[]` | Tolerations. Empty: the top-level `tolerations`. |
| `codeExecutor.affinity` | `{}` | Affinity. Empty: the top-level `affinity`. |
| `bootstrap.installHook` | `"auto"` | When the job runs on install. `auto`: pre-install when every datastore is external (the app starts on a ready schema), post-install when any is bundled (they must exist first). It always runs pre-upgrade. |
| `bootstrap.propertyCatalog` | `true` | Create the observed-attribute index database and its two ClickHouse users (needs CREATE USER and GRANT). `false`: create them yourself. |
| `bootstrap.waitTimeoutSeconds` | `600` | Seconds to wait for each datastore to accept connections. |
| `bootstrap.clickhouseTimeoutSeconds` | `600` | Deadline of the ClickHouse schema step, in seconds. |
| `bootstrap.backoffLimit` | `2` | Retries of a failed job. |
| `bootstrap.activeDeadlineSeconds` | `1080` | Deadline of the whole job, in seconds. Keep it below `helm install/upgrade --timeout` (20m in every documented command), so Helm reports how the job ended; raise both together for a long migration. |
| `bootstrap.ttlSecondsAfterFinished` | `86400` | Seconds a finished job (and its logs) is kept. |
| `bootstrap.resources` | see values.yaml | Resources of the bootstrap job. |
| `bootstrap.serviceAccount.create` | `true` | Create a dedicated ServiceAccount for the job (it exists before any other resource on install). |
| `bootstrap.serviceAccount.name` | `""` | Name of the job's ServiceAccount. Empty: <fullname>-bootstrap when created, else the namespace default. |
| `bootstrap.serviceAccount.annotations` | `{}` | Annotations, e.g. for cloud IAM database authentication. |
| `bootstrap.podAnnotations` | `{}` | Pod annotations. |
| `bootstrap.extraVolumes` | `[]` | Extra volumes, e.g. the CA of `postgres.external.sslMode=verify-full`. Empty: `backend.extraVolumes`. |
| `bootstrap.extraVolumeMounts` | `[]` | Extra volume mounts. Empty: `backend.extraVolumeMounts`. |
| `ingress.enabled` | `false` | One Ingress for the UI, the API and OTLP/HTTP. |
| `ingress.className` | `""` | IngressClass, e.g. `nginx`. |
| `ingress.annotations` | `{}` | Ingress annotations (e.g. cert-manager, proxy body size and timeouts for uploads and WebSockets). |
| `ingress.tls` | `[]` | TLS entries, e.g. `[{secretName: futureagi-tls, hosts: [futureagi.example.com, api.futureagi.example.com]}]`. Hosts listed here get https URLs. |
| `ingress.app.host` | `""` | Host of the UI. |
| `ingress.api.host` | `""` | Host of the API. It must differ from the UI host: the API serves many top-level paths. |
| `ingress.otlp.enabled` | `true` | Route OTLP/HTTP (`/v1/traces`, `/tracer/v1/traces`) to fi-collector. |
| `ingress.otlp.host` | `""` | Host for OTLP/HTTP. Empty: the API host. |
| `ingress.objects.host` | `""` | Host of the bundled object storage (`objectStorage.mode=bundled`), for browser downloads [MINIO_URL]. Empty: not exposed. |
| `networkPolicy.enabled` | `false` | NetworkPolicies: the datastores, the gateway, serving and the code sandbox accept traffic only from this release's pods; the UI, API and collector also from `ingressFrom`. A component whose Service is a LoadBalancer or NodePort accepts any source on its Service ports. Egress is open, except for the code sandbox. |
| `networkPolicy.ingressFrom` | `[]` | Peers (NetworkPolicy `from` entries) that may reach the UI, API and collector, e.g. your ingress controller's namespace: `[{namespaceSelector: {matchLabels: {kubernetes.io/metadata.name: ingress-nginx}}}]`. Empty: any source. |
| `serviceAccount.create` | `true` | Create a ServiceAccount for the application pods. |
| `serviceAccount.name` | `""` | Name. Empty: <fullname> when created, else the namespace default. |
| `serviceAccount.annotations` | `{}` | Annotations of the application pods' ServiceAccount. Object storage does not use them: it always authenticates with `objectStorage` keys. |
| `serviceAccount.automountServiceAccountToken` | `false` | Mount the service account token (the application does not call the Kubernetes API). |
| `nodeSelector` | `{}` | Default node selector. |
| `tolerations` | `[]` | Default tolerations. |
| `affinity` | `{}` | Default affinity. |
| `topologySpreadConstraints` | `[]` | Default topology spread constraints of the application Deployments. |
| `priorityClassName` | `""` | PriorityClass of every pod. |
| `commonLabels` | `{}` | Extra labels on every resource. |
<!-- values-table:end -->
