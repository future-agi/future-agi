# Future AGI Helm chart

Future AGI on Kubernetes. The chart runs the **Distributed** setup: one
Deployment per service, so each one scales on its own. The same chart
installs the open-source and the Enterprise edition.

<!-- Artifact Hub badge, once the repository is registered
     (deploy/helm/artifacthub-repo.yml holds its repositoryID):
[![Artifact Hub](https://img.shields.io/endpoint?url=https://artifacthub.io/badge/repository/futureagi)](https://artifacthub.io/packages/helm/futureagi/futureagi)
-->

- [What it runs](#what-it-runs)
- [Install](#install)
- [Verify](#verify)
- [Choose a setup](#choose-a-setup)
- [Requirements](#requirements)
- [External datastores](#external-datastores)
- [Sizing presets](#sizing-presets)
- [Exposing it](#exposing-it)
- [Enterprise](#enterprise)
- [Secrets](#secrets)
- [Upgrading](#upgrading)
- [Backup and restore](#backup-and-restore)
- [Observability](#observability)
- [GitOps: Argo CD and Flux](#gitops-argo-cd-and-flux)
- [Security](#security)
- [Troubleshooting and support bundle](#troubleshooting-and-support-bundle)
- [Developing from a git checkout](#developing-from-a-git-checkout)
- [Values](#values)

## What it runs

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
chart's `appVersion`. A published chart also pins each of them to the digest
its release built (`image.digests`); a digest applies only while the image's
tag is the chart's `appVersion` and its repository is the published one, so
`--set image.tag=...` runs that tag as usual.

The datastores are **external** by default: PostgreSQL, ClickHouse, Redis,
Temporal and S3-compatible object storage that you run and back up. For an
evaluation, the chart can run each of them itself (**bundled**: one replica
each, not highly available, no backups).

Postgres changes reach ClickHouse through the outbox change data capture
(`FI_CDC_MODE=outbox`: triggers plus a drain in the Temporal workers). There
is no PeerDB and no RabbitMQ; live updates use Redis. The chart has no
subcharts.

To run everything on one machine with Docker instead, use the Standalone
install (`./bin/install`); `./bin/install --distributed` runs this same
topology with Docker Compose.

## Install

The chart is published as an OCI artifact on GitHub Container Registry, one
version per platform release:

```sh
VERSION=X.Y.Z   # a platform release, without the "v"
helm install futureagi oci://ghcr.io/future-agi/charts/futureagi --version "$VERSION" \
  -n futureagi --create-namespace -f my-values.yaml --timeout 20m
```

- The chart's version is the platform version (`X.Y.Z`), and its
  `appVersion` (`vX.Y.Z`) is the image tag it runs. There is no `latest`
  tag: always pass `--version`. A published version is never overwritten.
- The examples ship inside the chart. `helm pull
  oci://ghcr.io/future-agi/charts/futureagi --version "$VERSION" --untar`
  puts them in `futureagi/examples/`; or pass one by its tag-pinned URL,
  `-f https://raw.githubusercontent.com/future-agi/future-agi/v$VERSION/deploy/helm/futureagi/examples/bundled.yaml`.
- Helm 4 can also install by digest:
  `helm install futureagi oci://ghcr.io/future-agi/charts/futureagi@sha256:<digest> ...`.
- The packaged chart is also attached to the GitHub Release `vX.Y.Z` (see
  [Verify](#verify)), for registries that cannot proxy GHCR.

> **Before the first published chart.** Until a platform release that
> contains this chart ships, `oci://ghcr.io/future-agi/charts/futureagi` has
> no versions. Install from a git checkout instead: `helm install futureagi
> deploy/helm/futureagi ...` in place of `oci://... --version "$VERSION"`
> (see [Developing from a git checkout](#developing-from-a-git-checkout)),
> and point `image.tag` (and `image.registry`) at images built from the same
> branch and pushed where your cluster can pull them. The bootstrap job runs
> `python manage.py bootstrap_install`, which no image published
> before this chart contains; with such an image it fails with
> `Unknown command: 'bootstrap_install'`.

Helm waits for the bootstrap job (migrations and schema, a few minutes on
the first install), then prints the next steps. If Helm times out first,
the job keeps running: wait for it to finish
(`kubectl -n futureagi get job futureagi-bootstrap`) before you run Helm
again, since a new run replaces the job mid-way.

### Uninstall

```sh
helm uninstall futureagi --namespace futureagi
```

Kept on purpose, delete them when you mean it:

- `futureagi-secrets`: the generated keys. `INTEGRATION_ENCRYPTION_KEY`
  decrypts the integration credentials stored in Postgres; a reinstall reuses it.
- The bootstrap ServiceAccount (Helm hooks are not deleted by `uninstall`).
- Secrets created by `externalSecrets` (their ExternalSecrets are hooks with
  `creationPolicy: Orphan`).
- The bundled datastores' PersistentVolumeClaims (`data-futureagi-postgres-0`, ...).

```sh
kubectl -n futureagi delete secret futureagi-secrets
kubectl -n futureagi delete serviceaccount futureagi-bootstrap
kubectl -n futureagi delete pvc -l app.kubernetes.io/instance=futureagi
```

## Verify

Each published chart is signed keylessly with cosign by the release workflow
(GitHub OIDC, no key to manage) and carries a build provenance attestation.
Both name the workflow and the tag that built it:

```sh
VERSION=X.Y.Z
cosign verify --new-bundle-format=false ghcr.io/future-agi/charts/futureagi:$VERSION \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-identity https://github.com/future-agi/future-agi/.github/workflows/helm-release.yml@refs/tags/v$VERSION
gh attestation verify oci://ghcr.io/future-agi/charts/futureagi:$VERSION --repo future-agi/future-agi \
  --signer-workflow future-agi/future-agi/.github/workflows/helm-release.yml
```

`--new-bundle-format=false` makes cosign check the chart's signature, as the
release workflow does. Without it, cosign checks the provenance attestation
instead, and passes even if the signature is missing.

The GitHub Release `vX.Y.Z` carries the same package and what you need to
mirror it:

| Asset | What it is |
| --- | --- |
| `futureagi-X.Y.Z.tgz` | the chart, byte for byte the registry copy |
| `futureagi-X.Y.Z.tgz.sigstore.json` | its cosign signature bundle |
| `futureagi-X.Y.Z.sha256` | checksums |
| `futureagi-images-X.Y.Z.txt` | every image the chart can pull, one `repository:tag@sha256:...` per line |
| `futureagi-hauler-X.Y.Z.yaml` | the same images plus the chart, as a Hauler manifest (`hauler store sync --filename ...`) |
| `support-bundle.sh` | the [support bundle](#troubleshooting-and-support-bundle) script |

```sh
gh release download v$VERSION -R future-agi/future-agi -p "futureagi-$VERSION.tgz*"
cosign verify-blob futureagi-$VERSION.tgz --bundle futureagi-$VERSION.tgz.sigstore.json \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-identity https://github.com/future-agi/future-agi/.github/workflows/helm-release.yml@refs/tags/v$VERSION
```

The chart pins every Future AGI image to its digest, so a verified chart
also fixes the exact images it runs. The images themselves are not signed
yet: see [Verifying an image](https://docs.futureagi.com/docs/self-hosting/images#verifying-an-image).
Flux can check the signature before every install and upgrade: see
[GitOps](#gitops-argo-cd-and-flux).

## Choose a setup

| Setup | Values | Datastores | For |
| --- | --- | --- | --- |
| Evaluation | [`examples/bundled.yaml`](examples/bundled.yaml) | bundled, reached through port-forwards | trying it on any cluster |
| Local cluster | [`examples/local.yaml`](examples/local.yaml) | bundled, LoadBalancer Services on localhost | Docker Desktop, k3s, OrbStack, Rancher Desktop |
| Production | [`examples/external.yaml`](examples/external.yaml) (or [`examples/cloud/`](examples/cloud)) plus [`examples/sizes/`](examples/sizes) and an exposure example | yours | teams and production |
| Enterprise | the production files plus [`examples/enterprise.yaml`](examples/enterprise.yaml) | yours | a licensed install, SSO, proxies, air-gap |

Values files layer: later `-f` files win. The other examples:
[`gateway-api.yaml`](examples/gateway-api.yaml),
[`ingress-traefik.yaml`](examples/ingress-traefik.yaml) and
[`ingress.yaml`](examples/ingress.yaml) (legacy ingress-nginx) expose the
install; [`external-secrets.yaml`](examples/external-secrets.yaml) and
[`airgap.yaml`](examples/airgap.yaml) are for Enterprise-style operations;
[`gitops/`](examples/gitops) holds an Argo CD Application and a Flux
HelmRelease (not values files).

### Evaluation

Every datastore in the cluster, reached through port-forwards:

```sh
helm install futureagi oci://ghcr.io/future-agi/charts/futureagi --version "$VERSION" \
  -n futureagi --create-namespace --timeout 20m \
  -f https://raw.githubusercontent.com/future-agi/future-agi/v$VERSION/deploy/helm/futureagi/examples/bundled.yaml
```

```sh
kubectl -n futureagi port-forward svc/futureagi-frontend 3000:80 &
kubectl -n futureagi port-forward svc/futureagi-backend 8000:8000 &
kubectl -n futureagi port-forward svc/futureagi-fi-collector 4318:4318 &   # traces: FI_BASE_URL=http://localhost:4318
kubectl -n futureagi port-forward svc/futureagi-minio 9005:9000 &   # file downloads
kubectl -n futureagi exec -it deploy/futureagi-backend -c backend -- python manage.py create_user
open http://localhost:3000
```

Add an LLM provider key for the built-in evals, at install or later (same
version, so reusing the values is safe):

```sh
helm upgrade futureagi oci://ghcr.io/future-agi/charts/futureagi --version "$VERSION" \
  -n futureagi --reset-then-reuse-values --set secrets.llm.openaiApiKey=sk-... --timeout 20m
```

This restarts the application pods, not the bundled datastores.

### Local cluster

[`examples/local.yaml`](examples/local.yaml) bundles every datastore and
publishes the UI (3000), API (8000), OTLP (4317, 4318) and file downloads
(9005) as LoadBalancer Services on localhost, which Docker Desktop,
OrbStack, Rancher Desktop and k3s provide: no port-forwards. Elsewhere a
LoadBalancer needs `minikube tunnel`, cloud-provider-kind or MetalLB. For
evaluation only: it exposes the bundled bucket.

### Production

1. Create the Secrets for your datastores (or put the passwords in your
   values file; the chart then stores them in its own Secret):

   ```sh
   kubectl create namespace futureagi
   kubectl -n futureagi create secret generic futureagi-postgres --from-literal=password='...'
   kubectl -n futureagi create secret generic futureagi-s3 \
     --from-literal=access-key='...' --from-literal=secret-key='...'
   ```

2. Start `my-values.yaml` from [`examples/external.yaml`](examples/external.yaml)
   (or [`examples/cloud/gke.yaml`](examples/cloud/gke.yaml),
   [`eks.yaml`](examples/cloud/eks.yaml), [`aks.yaml`](examples/cloud/aks.yaml)),
   replace the example hosts, check the
   [external datastore contract](#external-datastores), and pick a size and
   a way in:

   ```sh
   helm pull oci://ghcr.io/future-agi/charts/futureagi --version "$VERSION" --untar
   helm install futureagi oci://ghcr.io/future-agi/charts/futureagi --version "$VERSION" \
     -n futureagi -f my-values.yaml \
     -f futureagi/examples/sizes/medium.yaml -f futureagi/examples/gateway-api.yaml \
     --timeout 20m
   ```

With only external datastores, the bootstrap job runs **before** anything
else is created (a `pre-install` hook), so the API starts on a ready schema.
A values file that leaves out a required setting fails at once with a
message that names it, before anything is created. Keep `my-values.yaml` in
version control: every upgrade passes it again.

### Enterprise

The same chart and the same public image, plus a license from a Secret:

```sh
kubectl -n futureagi create secret generic futureagi-license --from-literal=EE_LICENSE_KEY='...'
helm install futureagi oci://ghcr.io/future-agi/charts/futureagi --version "$VERSION" \
  -n futureagi -f my-values.yaml \
  --set edition=ee --set license.existingSecret=futureagi-license --timeout 20m
```

[`examples/enterprise.yaml`](examples/enterprise.yaml) adds SSO, a
declarative first admin, email, a corporate proxy and CA, and a private
registry mirror. See [Enterprise](#enterprise).

### First account and traces

```sh
# Interactive:
kubectl -n futureagi exec -it deploy/futureagi-backend -c backend -- python manage.py create_user
# Scripted:
kubectl -n futureagi exec deploy/futureagi-backend -c backend -- python manage.py create_user \
  --email admin@example.com --name "Admin" --password '<password>'
```

The password must pass the sign-up rules: 8+ characters, not a common
password, not all digits. Or let the bootstrap job create the first admin
from a Secret (`bootstrap.admin.existingSecret`), which suits GitOps.

Traces go to fi-collector over OpenTelemetry, with the API keys from the UI;
[OTLP](#otlp) lists the endpoints. `helm test futureagi -n futureagi`
checks that the API, UI, collector and gateway answer.

fi-collector also fills the observed-attribute index: the span attributes and
values that trace filters, dashboard widgets and tasks suggest. It writes the
index itself, as its `observed_catalog_writer` user
(`FI_OBSERVED_CATALOG_MODE=direct`); no Kafka is involved. With
`bootstrap.propertyCatalog=false`, create the index and that user before
sending traces. To publish to your own Kafka and `fi-property-catalog-consumer`
instead, set `FI_OBSERVED_CATALOG_KAFKA_BROKERS` in `fiCollector.extraEnv`;
`FI_OBSERVED_CATALOG_MODE: disabled` there turns suggestions off.

## Requirements

- Kubernetes 1.27 or newer.
- Helm 3.10 or newer, or Helm 4. Every chart change is linted and rendered
  with Helm 3.22 and 4.3, validated against the Kubernetes 1.27 and 1.37
  schemas, and installed and upgraded on kind.
- For an evaluation: about 4 CPUs and 8 GiB of memory free, and a default
  StorageClass (bundled datastores use PersistentVolumeClaims).
- For production: the datastores in [External datastores](#external-datastores),
  reachable from the cluster, and metrics-server for the autoscalers.
- For the [Gateway API](#gateway-api-recommended) routes: the Gateway API
  CRDs v1.2 or newer (route `timeouts` are in the Standard channel from
  v1.2) and a Gateway controller.

### Supported configurations

| | Supported: tested in CI, fixed in patch releases | Community: expected to work, not tested in CI | Not supported today |
| --- | --- | --- | --- |
| Cluster | Kubernetes 1.27 to 1.37; kind | GKE, EKS, AKS ([`examples/cloud/`](examples/cloud)); OpenShift (`global.compatibility.openshift`); k3s, Docker Desktop, OrbStack | the code sandbox in a `baseline` or `restricted` Pod Security namespace (it is privileged) |
| Helm and GitOps | Helm 3.22, Helm 4.3 | older Helm 3 from 3.10; Argo CD; Flux | |
| PostgreSQL | 16 (11 or newer works), direct connection, password auth | PgBouncer in transaction mode (`postgres.pooler`); a read replica | IAM database authentication |
| ClickHouse | 25.3 or newer, single node, with the `tiered` storage policy | an operator-managed single-node server (Altinity) | TLS-only endpoints (ClickHouse Cloud); replicated clusters |
| Redis | 6 or newer, databases 0 to 4, plain or TLS for the app | managed Redis (ElastiCache, Memorystore, Azure Cache) | TLS for the LLM gateway (it has no Redis TLS) and for fi-collector |
| Temporal | self-hosted 1.2x, plain gRPC | the temporalio/temporal chart | Temporal Cloud (TLS, API keys) |
| Object storage | AWS S3, S3-compatible (MinIO) with access keys | GCS through HMAC keys | IAM roles for service accounts, Workload Identity, Entra ID; Azure Blob |
| Exposure | Gateway API, Ingress, LoadBalancer | Envoy Gateway, Traefik, GKE Gateway, AWS Load Balancer Controller | |
| Bundled datastores | evaluation | | production use: one replica each, no backups |

## External datastores

Each datastore has `mode: external` (default) or `mode: bundled`, and they
mix freely (for example bundled Temporal with your own PostgreSQL). The
contract an external datastore has to meet:

### PostgreSQL

- PostgreSQL 16 (11 or newer works). Logical decoding is **not** needed: CDC
  uses triggers and an outbox table.
- The `pg_trgm` extension: a migration runs `CREATE EXTENSION pg_trgm`, so
  the user needs the right to create it, or an administrator creates it in
  the database first.
- `postgres.external.sslMode`: `require`, or `verify-full` with the server's
  CA (below).
- `PG_HOST` (`postgres.external.host`) must reach PostgreSQL **directly**: the
  outbox CDC holds a session advisory lock and migrations set session
  options, which a transaction-mode pooler breaks. Django's own connections
  can go through PgBouncer with `postgres.pooler` (`pool_mode=transaction`,
  `server_reset_query_always=1`,
  `ignore_startup_parameters=extra_float_digits,search_path`); the bootstrap
  job always connects directly. `postgres.readReplica` routes opted-in reads
  to a replica.
- Connections: every backend thread and every worker activity slot may hold
  one. Keep `max_connections` above
  `backend pods × backend.granian.workers × backend.granian.threads` plus,
  for each worker Deployment, `pods × queues it polls × maxConcurrentActivities`,
  plus about 20 for the bootstrap job, the collector and your own tools.
  Put a pooler in front before raising `maxReplicas`.
- Backups with point-in-time recovery: see [Backup and restore](#backup-and-restore).

With `sslMode` `verify-ca` or `verify-full`, the pods need the server's CA.
The simplest way is `global.caBundle` (a full bundle, public roots
included): the chart then mounts it and sets `PGSSLROOTCERT` on every pod
that connects to PostgreSQL. To trust the database's CA only, mount it
yourself (the bootstrap job reuses `backend.extraVolumes` unless you set its
own):

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

### ClickHouse

- ClickHouse 25.3 or newer, **single node**, over plain HTTP (8123) and the
  native protocol (9000): the schema installer and fi-collector do not speak
  TLS to ClickHouse, and the schema uses non-replicated engines.
- The spans table needs a storage policy named `tiered` with a `cold`
  volume: parts move to `cold` after 7 days and are deleted after 90. The
  bootstrap job stops with `native tiered storage policy required` without
  it. A server config file such as:

  ```xml
  <clickhouse>
    <storage_configuration>
      <disks>
        <cold_local><path>/var/lib/clickhouse/cold/</path></cold_local>
        <!-- or an object-storage disk:
        <cold_s3>
          <type>s3</type>
          <endpoint>https://s3.us-east-1.amazonaws.com/my-bucket/clickhouse-cold/</endpoint>
          <use_environment_credentials>true</use_environment_credentials>
        </cold_s3> -->
      </disks>
      <policies>
        <tiered>
          <volumes>
            <hot><disk>default</disk></hot>
            <cold><disk>cold_local</disk></cold>
          </volumes>
          <move_factor>0.05</move_factor>
        </tiered>
      </policies>
    </storage_configuration>
  </clickhouse>
  ```

  The bundled ClickHouse ships the same policy
  ([`files/clickhouse/config.d/storage-policy.xml`](files/clickhouse/config.d/storage-policy.xml)).
- The configured user needs CREATE on the database and, for the
  observed-attribute index, CREATE DATABASE, CREATE USER and GRANT (or set
  `bootstrap.propertyCatalog=false` and create them yourself). The bundled
  ClickHouse has only the `default` user, so `clickhouse.user` stays
  `default` with it.

### Redis

Redis 6 or newer. The app uses databases 0 to 3 and the LLM gateway
database 4 (`agentccGateway.redis.db`) whenever it runs more than one
replica. `redis.external.tls` turns on TLS for the app; the gateway and
fi-collector have no Redis TLS, so with TLS on, run the gateway with one
replica or point it at a plain Redis (`AGENTCC_REDIS_ADDRESS` in
`agentccGateway.extraEnv`). `sizes/medium.yaml` and `large.yaml` autoscale
the gateway, so they need the plain Redis:
[`examples/cloud/aks.yaml`](examples/cloud/aks.yaml) (Azure Cache for Redis
is TLS-only) sets one. No backups needed: Redis holds cache, locks and
live-update state.

### Temporal

A self-hosted Temporal server (1.2x, the temporalio/temporal chart for
example) over plain gRPC, with the namespace (`temporal.namespace`, default
`default`) created in advance. Temporal Cloud (TLS, API keys) is not
supported yet.

### Object storage

AWS S3, Google Cloud Storage (HMAC keys) or any S3-compatible service, with
an access key and secret key: IAM roles for service accounts, Workload
Identity and Entra ID are not supported. On GCS, create the bucket
(`objectStorage.bucket`) in advance and let anyone read objects by URL;
elsewhere the app creates it on the first upload with that policy (anonymous
reads of an object by its URL; no listing, no writes). Browsers download
stored files by plain object URLs, so the bucket's endpoint must be
reachable from your users' browsers.

### Bundled datastores

| Datastore | External: set | Bundled: runs | Bundled image |
| --- | --- | --- | --- |
| PostgreSQL | `postgres.external.host`, `postgres.password` or `postgres.existingSecret` | StatefulSet, 20 GiB volume | `postgres:16.15-trixie`, pinned by digest |
| ClickHouse | `clickhouse.external.host` (password optional) | StatefulSet, 50 GiB volume, the Standalone install's low-memory settings | `clickhouse/clickhouse-server:25.3-alpine` |
| Redis | `redis.external.host` (password optional) | StatefulSet with a password, append-only file on 2 GiB | `redis:7.4.11-alpine`, pinned by digest |
| Temporal | `temporal.external.address` | the single-binary dev server, SQLite on a 5 GiB volume | `temporalio/temporal:1.9.1` |
| Object storage | `objectStorage.backend`, keys, `objectStorage.external.endpoint` | one MinIO server, 20 GiB volume | the last community MinIO release (as in Docker Compose), pinned by digest |

Bundled datastores are for evaluation: one replica each, no replication,
no backups, and upgrades of their images are yours to plan. PostgreSQL,
Redis and MinIO are pinned by digest on their default tags only: another
`<datastore>.bundled.image.tag`, or `image.pinDigests=false`, runs by tag
unless you also set that image's `digest`. Moving from bundled to external
means migrating the data yourself (`pg_dump`, `clickhouse-backup`,
`mc mirror`).

When any datastore is bundled, the bootstrap job runs after the release's
resources (`post-install`) because it has to reach them; the API answers
`/health/` meanwhile, the Temporal workers wait until the job's migrations
are applied, and Helm returns only once the job has finished.

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
the StatefulSet without its pod, and upgrade with the new size in your
values file, which re-creates it:

```sh
kubectl -n futureagi patch pvc data-futureagi-postgres-0 \
  -p '{"spec":{"resources":{"requests":{"storage":"40Gi"}}}}'
kubectl -n futureagi delete statefulset futureagi-postgres --cascade=orphan
# my-values.yaml now has postgres.bundled.persistence.size: 40Gi
helm upgrade futureagi oci://ghcr.io/future-agi/charts/futureagi --version "$VERSION" \
  -n futureagi -f my-values.yaml --timeout 20m
```

Turning Redis persistence on or off works the same way, without the patch.
A different StorageClass needs a new volume: move the data yourself.

## Sizing presets

Layer one of [`examples/sizes/`](examples/sizes) over your datastore values.
They follow how the hosted service runs, scaled down:

| Preset | For | Replicas | Also |
| --- | --- | --- | --- |
| Evaluation ([`bundled.yaml`](examples/bundled.yaml)) | trying it | 1 of each, datastores included | about 2.5 CPUs and 5 GiB requested |
| [`small.yaml`](examples/sizes/small.yaml) | a team, up to a few million spans a day | 1 of each, default resources | about 3 CPUs and 8 GiB requested, datastores not included |
| [`medium.yaml`](examples/sizes/medium.yaml) | production for one organisation | autoscaled on CPU and memory: backend 2 to 6, all-queues worker 2 to 6, UI 2 to 4, collector 2 to 6, gateway 2 to 6 (sharing state in Redis) | soft zone and node spread, disruption budgets, 300 s worker drain; at least two nodes |
| [`large.yaml`](examples/sizes/large.yaml) | many teams, heavy evaluation or simulation load | backend 3 to 12; a worker Deployment per queue: `default` 2 to 6, `tasks_s` 2 to 8, `tasks_l` 2 to 6, `tasks_xl` 1 to 4, `agent_compass` 1 to 4; gateway 2 to 8 | drains of 300 to 900 s per queue, hard node spread, Guaranteed-QoS collector, PgBouncer; three nodes across zones |

Scaling notes:

- `backend.autoscaling`, `worker.allQueues.autoscaling`, each
  `worker.queues[].autoscaling`, `fiCollector.autoscaling`,
  `agentccGateway.autoscaling` and `frontend.autoscaling` add
  HorizontalPodAutoscalers (CPU and memory targets, optional `behavior`).
- `worker.queues` gives a busy queue its own Deployment, with its own drain
  time (`gracefulShutdownSeconds`), preStop, disruption budget and
  placement; the all-queues worker then stops polling it.
- The exact-aggregation worker stays at one replica with one slot: it is the
  admission boundary for expensive exact analytics.
- `topologySpread.preset` (`soft` by default, `hard`, `none`) spreads every
  component that can run more than one replica over zones and nodes.
- More replicas mean more PostgreSQL connections: see the
  [connection budget](#postgresql).

## Exposing it

Without URLs the install is reachable through port-forwards only: the UI
calls the API on `http://localhost:8000`. For access from other machines,
use the Gateway API, an Ingress, LoadBalancer Services, or your own proxy
with `urls.app` and `urls.api` set.

The URLs follow from the hosts (https for TLS hosts) and set `BASE_URL`,
`FRONTEND_URL`, `APP_URL` (the UI's URL: invite and password-reset links
point there), the CSRF origins, the UI's API URL and
`FI_COLLECTOR_PUBLIC_URL`. Without them they are `http://localhost:8000`,
`http://localhost:3000` and `http://localhost:4318`, the port-forwards. Once
the URLs are public, `ALLOWED_HOSTS` becomes the API host (plus localhost,
the in-cluster names and the pod's own IP, which load balancers that
health-check pods directly send as the Host: an AWS ALB with
`target-type: ip`, GKE) and `CORS_ALLOWED_ORIGINS` the UI's origin; set
`config.allowedHosts` or `config.corsAllowedOrigins` to `"*"` for the old
allow-all behaviour.

### Gateway API (recommended)

[`examples/gateway-api.yaml`](examples/gateway-api.yaml) attaches routes to
a Gateway you run (Envoy Gateway, GKE Gateway, Istio, Cilium, ...), with the
Gateway API CRDs v1.2 or newer:

- an HTTPRoute for the UI;
- an HTTPRoute for the API, sending `/v1/traces` and `/tracer/v1/traces` to
  the collector's OTLP/HTTP port, `/ws/` with a long timeout
  (`gatewayApi.timeouts.websocket`, 24 h), and the rest with
  `gatewayApi.timeouts.request` (300 s: many Gateways cut at 15 s by default);
- optionally a GRPCRoute for OTLP/gRPC on its own host
  (`gatewayApi.otlpGrpc`), and a route to the LLM gateway
  (`gatewayApi.llmGateway`) for applications outside the cluster.

**GKE Gateway** health-checks every pod itself and ignores the readiness
probes: by default `GET /` on the serving port, with the pod's IP as the
Host. The API answers `/` with a redirect and the collector's OTLP port has
no `/`, so both would be marked unhealthy (`no healthy upstream`).
`gatewayApi.gke.healthChecks.enabled`, on in
[`examples/cloud/gke.yaml`](examples/cloud/gke.yaml), adds a
`HealthCheckPolicy` (`networking.gke.io/v1`) for each Service the routes use,
probing what its readiness probe probes:

| Service | Load balancer health check |
| --- | --- |
| backend | `GET /health/` with Host `localhost`, on the serving port |
| fi-collector | `GET /healthz` on the admin port, 9464 |
| agentcc-gateway (with `gatewayApi.llmGateway`) | `GET /readyz` on the serving port |
| frontend | `GET /` on the serving port |

The probes come from `35.191.0.0/16` and `130.211.0.0/22`: include them in
`networkPolicy.ingressFrom` when you set it. Envoy-based Gateway classes
(`gke-l7-regional-external-managed`, `gke-l7-rilb`) also send the traffic
itself from the region's proxy-only subnet, so add that range too. GKE opens
its own firewall for these ranges, except in a Shared VPC, where the host
project's admin must. On EKS, [`examples/cloud/eks.yaml`](examples/cloud/eks.yaml) does the
same for the ALB with `alb.ingress.kubernetes.io/healthcheck-*` annotations
on the backend and collector Services.

### Ingress

- Traefik: [`examples/ingress-traefik.yaml`](examples/ingress-traefik.yaml).
  Traefik sets timeouts per entrypoint: raise its 60 s read timeout for
  large uploads in Traefik's own values.
- ingress-nginx (retired upstream; kept as legacy):
  [`examples/ingress.yaml`](examples/ingress.yaml), with
  `ingress.websocket` for a second Ingress that gives `/ws/` its own long
  timeouts.
- The API host must differ from the UI host. `ingress.otlp.enabled` routes
  `/v1/traces` on the API host to the collector. With bundled object
  storage, `ingress.objects.host` publishes file downloads (evaluation only).

### LoadBalancer

`<component>.service.type: LoadBalancer` publishes a component directly;
[`examples/local.yaml`](examples/local.yaml) does it for everything on a
laptop cluster. `fiCollector.service.type=LoadBalancer` is the simplest way
to take OTLP/gRPC from outside without a Gateway.

### OTLP

| From | OTLP/gRPC | OTLP/HTTP |
| --- | --- | --- |
| Inside the cluster | `<release>-fi-collector.<namespace>.svc:4317` | `http://<release>-fi-collector.<namespace>.svc:4318/v1/traces` |
| Through the Gateway API | `gatewayApi.otlpGrpc.host:443` | `https://<api or otlp host>/v1/traces` |
| Through the Ingress (`ingress.otlp.enabled`) | not routed | `https://<api host>/v1/traces` |
| Outside, without either | `fiCollector.service.type=LoadBalancer` | the same load balancer, port 4318 |
| Through a port-forward | `kubectl -n futureagi port-forward svc/futureagi-fi-collector 4317:4317` | `kubectl -n futureagi port-forward svc/futureagi-fi-collector 4318:4318`, then `http://localhost:4318/v1/traces` |

Future AGI SDKs take the OTLP/HTTP base URL as `FI_BASE_URL`. The app shows
the same URL in its SDK snippet and setup screen (`FI_COLLECTOR_PUBLIC_URL`):
`urls.otlp`, else the Gateway API or Ingress OTLP host, else
`http://localhost:4318`. Set `urls.otlp` when you expose the collector
another way.

### Timeouts and WebSockets

The API holds WebSockets open for live updates and serves long requests
(dataset uploads, LLM calls). Whatever sits in front must allow both:

| In front | Where to set it |
| --- | --- |
| Gateway API route | `gatewayApi.timeouts.request` and `.websocket` (route `timeouts`) |
| Envoy Gateway | also a `ClientTrafficPolicy` (client idle timeout) and a `BackendTrafficPolicy` (backend timeouts) on the Gateway |
| GKE Gateway | a `GCPBackendPolicy` with `timeoutSec` on the backend Service |
| AWS ALB (Load Balancer Controller) | `alb.ingress.kubernetes.io/load-balancer-attributes: idle_timeout.timeout_seconds=3600` |
| ingress-nginx | `proxy-read-timeout` and `proxy-send-timeout` annotations on the WebSocket Ingress (`ingress.websocket.annotations`) |
| Traefik | the entrypoint's `respondingTimeouts` |

## Enterprise

One chart and one image for both editions: `edition: ee` plus a license
turns on the Enterprise features in the public `futureagi/future-agi` image.

- **License.** `license.existingSecret` (key `EE_LICENSE_KEY`) or
  `license.key`; `edition: ee` refuses to render without one. The backend,
  every worker and the bootstrap job read it. Check it with
  `kubectl -n futureagi logs deploy/futureagi-backend -c backend | grep license_startup`.
  `secrets.eeLicenseKey` still works but is deprecated. `license.heartbeat`
  and `license.url` control the license check-in.
- **A private image.** If you were given one, set `image.registry` and
  `backend.image.repository` (or `global.imageRegistry`) and
  `global.imagePullSecrets`; the chart never switches images by edition.
  The published chart pins each image's digest on the release tag whatever
  the registry, so also set `image.pinDigests=false` (or the image's own
  `backend.image.digest`) unless the registry is a mirror made with
  `crane copy` or `oras copy -r`, which keep the digests: a different build
  under the same repository path fails with `ImagePullBackOff`.
- **SSO.** `auth.google`, `auth.github` and `auth.microsoft` take a client ID
  and secret, inline or from `existingSecret` (Google's settings are named
  `AUTH0_CLIENT_ID` and `AUTH0_CLIENT_SECRET`; Microsoft signs in through the
  multi-tenant `common` endpoint). The install notes print the redirect URIs
  to register, `<api>/saml2_auth/{auth,github,microsoft}/callback/`. SAML is
  configured per organization in the app; its ACS URL is
  `<api>/saml2_auth/acs/` and its entity ID `https://<app host>`.
- **First admin.** `bootstrap.admin.existingSecret` (keys `email`, `name`,
  `password`) makes the bootstrap job create the first account if it does
  not exist, in place of the `kubectl exec ... create_user` step. A password
  the sign-up rules reject fails the job, which says why.
- **Corporate proxy.** `global.proxy.httpProxy`, `httpsProxy` and `noProxy`
  set `HTTP_PROXY`, `HTTPS_PROXY` and `NO_PROXY` on every application pod.
  `NO_PROXY` always includes the cluster's names, the release's Services and
  the datastore hosts; add an S3 or MinIO endpoint to `noProxy` when it is
  reachable directly. Object storage goes through the proxy too, unless the
  host it connects to matches `noProxy` (for AWS S3 with no endpoint set,
  `<bucket>.s3.<region>.amazonaws.com`). The LLM gateway does not use the
  proxy yet.
- **Custom CA.** `global.caBundle.configMap` (or `.secret`) mounts a PEM
  bundle at `/etc/futureagi/ca/ca.crt` and points `SSL_CERT_FILE`,
  `REQUESTS_CA_BUNDLE` and friends at it. It replaces the pods' trust store,
  so it must include the public roots:
  `cat /etc/ssl/certs/ca-certificates.crt corp.pem > bundle.pem`.
- **Air-gap.** `global.airgap` turns off telemetry, the license heartbeat
  and model downloads. One minimal registration attempt remains (instance
  id, version and deployment type); offline it fails harmlessly and is
  retried. Mirror the chart and its images with the release's
  `futureagi-images-X.Y.Z.txt` or Hauler manifest, or with
  `hack/list-images.sh` for your own values (add
  `--set global.imageRegistry=` if they already name the mirror, so that it
  lists the upstream images); `crane copy` and
  `oras copy -r` keep the digests, and a mirror that re-pushes images needs
  `image.pinDigests=false`. Then set `global.imageRegistry` (it rewrites
  every image, datastores included, whichever registry they come from) and
  `global.imagePullSecrets`. A pull-through cache of Docker Hub alone goes
  in `image.registry` and each Docker Hub `<datastore>.bundled.image.registry`
  instead: the bundled MinIO comes from ghcr.io.
  [`examples/airgap.yaml`](examples/airgap.yaml) covers pre-seeding the
  embedding models.
- **OpenShift.** `global.compatibility.openshift.adaptSecurityContext: auto`
  drops the fixed user, group and seccomp settings on OpenShift so the
  restricted SCC assigns them; `<datastore>.bundled.podSecurityContext` and
  `containerSecurityContext` override the bundled datastores. The code
  sandbox needs a privileged SCC, or `codeExecutor.localFallback`.
- **Secrets from a store** and rollouts on rotation: see [Secrets](#secrets).

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
| `PROPERTY_CATALOG_API_PASSWORD`, `PROPERTY_CATALOG_CONSUMER_PASSWORD` | ClickHouse users of the observed-attribute index (the API reads it, fi-collector writes it) | the bootstrap job resets them |
| `AGENTCC_WEBHOOK_SECRET` | the gateway sends its request logs to the backend with it | the gateway's request logs are refused until it and the backend restart |

Bundled datastores get generated passwords in the same Secret. A bundled
PostgreSQL keeps the password it was initialized with: set
`postgres.password` before the first install if you want a specific one.

To manage the application keys yourself (required with Argo CD),
create a Secret with all seven keys and set `secrets.existingSecret`
(`secrets.agentccWebhookSecret`, when set, takes the place of
`AGENTCC_WEBHOOK_SECRET`). Moving an existing install over? Copy the seven
values out of `<release>-secrets` first: new keys sign everyone out and cannot
decrypt stored credentials.

```sh
kubectl -n futureagi create secret generic futureagi-app \
  --from-literal=SECRET_KEY="$(openssl rand -hex 32)" \
  --from-literal=INTEGRATION_ENCRYPTION_KEY="$(python3 -c 'import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())')" \
  --from-literal=AGENTCC_INTERNAL_API_KEY="$(openssl rand -hex 32)" \
  --from-literal=AGENTCC_ADMIN_TOKEN="$(openssl rand -hex 32)" \
  --from-literal=PROPERTY_CATALOG_API_PASSWORD="$(openssl rand -hex 16)" \
  --from-literal=PROPERTY_CATALOG_CONSUMER_PASSWORD="$(openssl rand -hex 16)" \
  --from-literal=AGENTCC_WEBHOOK_SECRET="$(openssl rand -hex 24)"
```

Every other credential also takes an existing Secret: `postgres`,
`clickhouse`, `redis` and `objectStorage` (`existingSecret`),
`secrets.llm.existingSecret` (any of `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
`GOOGLE_API_KEY`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`),
`license.existingSecret`, `auth.*.existingSecret`,
`config.email.existingSecret` (`MAILGUN_API_KEY`) and
`bootstrap.admin.existingSecret`. Anything else: `secrets.extra` (stored in
the chart's Secret) or `config.extraEnvFrom`.

**From a secret store.** With the External Secrets Operator,
`externalSecrets.secrets.<group>` renders an ExternalSecret that fills the
Secret named by that group's `existingSecret`
([`examples/external-secrets.yaml`](examples/external-secrets.yaml), with
Vault). They are pre-install hooks, so the Secrets exist before the
bootstrap job, with `creationPolicy: Orphan`, so they survive upgrades and
uninstalls. Seed the seven application keys in the store once.

**Rotation.** Pods read Secrets at start. `reloader.enabled` annotates every
Deployment for Stakater Reloader, which restarts them when a Secret they use
changes.

## Upgrading

Chart and platform versions move together: chart `X.Y.Z` runs images
`vX.Y.Z`.

- **Supported paths:** any patch within a minor, and from the previous minor
  (N-1 to N). Going further, upgrade one minor at a time.
- **Migrations** are expand then contract across one release, so the old
  pods keep serving while the bootstrap job migrates.
- **Renamed values** keep working for two minors with a `DEPRECATED` line in
  the install notes; a removed one fails the render and names its
  replacement. A change that alters a StatefulSet or a PersistentVolumeClaim
  is marked as breaking (`feat(helm)!`) and ships with migration steps.
- **Release notes** carry a migrations callout when a release has one; the
  chart's changelog on Artifact Hub lists the chart's own changes first.
  Read both before upgrading.

```sh
NEW=X.Y.Z
helm upgrade futureagi oci://ghcr.io/future-agi/charts/futureagi --version "$NEW" \
  -n futureagi -f my-values.yaml --timeout 20m --rollback-on-failure   # Helm 3: --atomic
```

**Always pass your values file; never `--reuse-values` across versions.**
`--reuse-values` keeps the previous release's merged values, so new keys
and changed defaults in the new chart are silently dropped. Within one
version, or when you do not have the file, `--reset-then-reuse-values`
(Helm 3.14 or newer, Helm 4) takes the new chart's defaults and applies your
previous overrides over them. Helm 4 renamed `--atomic` to
`--rollback-on-failure`.

The bootstrap job runs first (`pre-upgrade`) with the new image:
migrations, seeds, ClickHouse schema, CDC and schedules, all idempotent.
Only then are the Deployments rolled. Generated secrets are read back and
never regenerated.

A failed bootstrap stops the upgrade before any Deployment is rolled:
`kubectl -n futureagi logs job/futureagi-bootstrap` says why. The chart's
Secret is written before the job (the job reads it), so a changed password or
key is already in place, and a pod that restarts meanwhile starts with it.
Fix the cause and run the upgrade again, or go back with
`helm rollback futureagi -n futureagi`, which puts back the previous
revision's Deployments and Secret. A job still running after Helm's
`--timeout` must finish before you retry: a new run replaces it mid-way.
`bootstrap.activeDeadlineSeconds` (18 minutes) stops it before the
documented `--timeout 20m`.

**Rollback is image-only.** `helm rollback` never reverts the database
schema. A rollback across a migration that the old version cannot run on
means restoring the pre-upgrade backup, so take one before every minor
upgrade.

Some bundled datastore settings cannot change after install: see
[Install-time settings](#install-time-settings).

### Moving to the first published chart

From an install made with a git checkout of this chart:

- **`ALLOWED_HOSTS` is derived.** With `config.allowedHosts` empty (the
  default) and a public API URL (`urls.api`, `gatewayApi.api.host` or
  `ingress.api.host`), it is no longer `*` but the API host, localhost, the
  in-cluster names of the backend and the pod's IP (`$(POD_IP)`, from a new
  `POD_IP` variable, for load balancers that health-check pods by IP). A
  request with any other Host gets `400 Bad Request`: add the other names
  your clients or proxies use to `config.allowedHosts`, or set it to `"*"`
  for the old behaviour.
- **`CORS_ALLOWED_ORIGINS` is derived.** With `config.corsAllowedOrigins`
  empty and a public UI URL, only the UI's origin (plus
  `config.extraCsrfOrigins`) may call the API with credentials, not every
  origin: list other browser origins in `config.corsAllowedOrigins`, or set
  it to `"*"`.
- The bundled PostgreSQL and Redis restart once, onto images pinned by
  digest (`postgres:16.15-trixie`, `redis:7.4.11-alpine`); their data stays.
  MinIO keeps the same pinned image, but an `objectStorage.bundled.image.tag`
  of your own now runs that tag (the default tag's digest used to win over
  it).
- Longer grace periods: backend 90 s, workers 15 s more for their preStop,
  collector and gateway 45 s.
- Components with more than one replica are spread over zones and nodes
  (`topologySpread.preset: soft`).
- The LLM gateway uses Redis database 4 when it runs more than one replica:
  keep it free.
- **External Redis over TLS with a scaled gateway is refused.** The gateway
  has no Redis TLS, so with `redis.external.tls` it gets no shared state, and
  the render now fails when it runs more than one replica
  (`agentccGateway.replicas` above 1 or `agentccGateway.autoscaling.enabled`,
  as in the medium and large presets). Either set `AGENTCC_REDIS_ADDRESS`
  in `agentccGateway.extraEnv` to a Redis without TLS (and
  `AGENTCC_REDIS_PASSWORD` if it has one), or set
  `agentccGateway.redis.enabled=false` with `agentccGateway.replicas: 1` and
  autoscaling off.
- `secrets.eeLicenseKey` is deprecated in favour of `license.*`.

## Backup and restore

What to back up, and how:

| Data | Holds | Back it up with |
| --- | --- | --- |
| PostgreSQL | accounts, projects, datasets, evals, the CDC outbox | managed point-in-time recovery, CloudNativePG backups, or `pg_dump` |
| ClickHouse | traces, spans and analytics | `BACKUP DATABASE ... TO S3(...)` (25.3 or newer) or `clickhouse-backup`, including the `cold` volume |
| Object storage | uploads, datasets and exports | bucket versioning and replication |
| Temporal | workflow state and schedules | its own database; bundled Temporal: a volume snapshot |
| `<release>-secrets` (or your `secrets.existingSecret`) | `SECRET_KEY`, `INTEGRATION_ENCRYPTION_KEY`, ... | export it and keep it offline, next to the database backups |
| Redis | cache, locks, live updates | nothing: it is rebuilt |

Without `INTEGRATION_ENCRYPTION_KEY`, the integration credentials in a
restored database cannot be decrypted. Export it once:

```sh
kubectl -n futureagi get secret futureagi-secrets -o yaml > futureagi-secrets.backup.yaml
```

To restore:

1. Scale the application down, so nothing writes meanwhile:
   `kubectl -n futureagi scale deploy -l app.kubernetes.io/instance=futureagi --replicas=0`.
2. Restore PostgreSQL, then ClickHouse from a backup taken at or after the
   same point, then the bucket.
3. Put back the application Secret (or your `secrets.existingSecret`).
4. Run `helm upgrade` with the same chart version and values: the bootstrap
   job re-applies the schema, CDC and Temporal schedules, and the
   Deployments come back. Autoscaled Deployments stay at 0 (an autoscaler
   does not scale up from 0): scale them to 1 yourself.
5. Verify: `helm test futureagi -n futureagi`, sign in, open a recent trace
   and download a file.

For bundled datastores, Velero (or your volume snapshots) covers the
PersistentVolumeClaims; stop the application first for a consistent copy.

## Observability

- **Logs.** `config.envType: production` (the default) writes JSON logs;
  `config.logLevel` sets the level. Every component logs to stdout.
- **Health.** The API answers `/health/`, fi-collector `/healthz` on its
  admin port 9464 (with its insert counters), the gateway `/healthz`.
  `helm test` checks them all. The app's first-run setup screen
  (`/api/setup-checks/`) checks every datastore.
- **Metrics.** The chart has no ServiceMonitor yet. The LLM gateway serves
  Prometheus metrics at `/-/metrics` (with the admin token) when
  `agentccGateway.config.prometheus.enabled` is true. Watch the datastores
  and Temporal with their own exporters.
- **The platform's own traces.** `config.otel` exports them over
  OpenTelemetry; set the `OTEL_*` variables in `config.extraEnv`.
- **Errors.** Set `SENTRY_DSN` in `secrets.extra`.

## GitOps: Argo CD and Flux

[`examples/gitops/`](examples/gitops) has an Argo CD Application and a Flux
OCIRepository with a HelmRelease for the published chart.

- **Argo CD** renders the chart with `helm template`, so Helm's `lookup`
  returns nothing and generated secrets would change on every sync. Set
  `secrets.existingSecret` to a Secret with all seven application keys
  ([Secrets](#secrets)), `AGENTCC_WEBHOOK_SECRET` included, and give bundled
  datastores explicit passwords (`postgres.password`, ...) or existing
  Secrets. Register `ghcr.io/future-agi/charts` as a Helm repository with
  `enableOCI: "true"` if your version asks for it. Argo CD cannot tell an
  install from an upgrade, so it runs every Helm install and upgrade hook on
  every sync: the Secret and the bootstrap ServiceAccount in PreSync. The
  bootstrap job carries Argo CD's own hook, which takes precedence: PreSync
  when it is a pre-install hook (every datastore external); otherwise a Sync
  hook in wave -1, which runs once the bundled datastores (wave -2) are ready
  and before the rest of the release (wave 0), on the first sync and every
  later one. Helm and Flux ignore these annotations.
- **Flux**'s helm-controller runs Helm against the live cluster, so `lookup`
  works and generated secrets stay put; `existingSecret` values are still
  the cleaner choice for GitOps. The OCIRepository verifies the chart's
  cosign signature before every install and upgrade:

  ```yaml
  verify:
    provider: cosign
    matchOIDCIdentity:
      - issuer: ^https://token\.actions\.githubusercontent\.com$
        subject: ^https://github\.com/future-agi/future-agi/\.github/workflows/helm-release\.yml@refs/tags/v.*$
  ```

Pin `targetRevision` (Argo CD) or `ref.semver` (Flux) to an exact version,
and upgrade by changing it, following [Upgrading](#upgrading).

## Security

- Every pod runs as a non-root user with `RuntimeDefault` seccomp, no
  privilege escalation and all capabilities dropped: the settings the Pod
  Security Admission `restricted` level asks for (with the code sandbox
  off; OpenShift: see [Enterprise](#enterprise)). The application
  pods have read-only root filesystems (writable emptyDirs for /tmp, logs and
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
- With public URLs, `ALLOWED_HOSTS` and `CORS_ALLOWED_ORIGINS` are limited
  to your hosts (see [Exposing it](#exposing-it)).
- Stored files are downloaded by plain object URLs, so the bucket lets
  anyone who can reach its endpoint read an object whose URL they know
  (never list or write). Keep that endpoint inside the network your users
  reach it from, and never publish the bundled bucket beyond an evaluation.
- reCAPTCHA (`config.recaptcha`) is off by default. On, it guards sign-up,
  login and token refresh for every host but localhost, and needs
  `secrets.extra.RECAPTCHA_SECRET_KEY` plus a frontend image built with
  `VITE_GOOGLE_SITE_KEY` (a build-time setting the published image lacks):
  without both, every login is refused.
- No pod mounts a service account token, unless
  `serviceAccount.automountServiceAccountToken` is on (for a sidecar that
  needs one): then the backend, worker, UI, collector, gateway and serving
  pods do. The code sandbox never does.
- Supply chain: the chart is signed and attested ([Verify](#verify)), has
  no subcharts, and pins the Future AGI images and the bundled PostgreSQL,
  Redis and MinIO images by digest (the datastores on their default tags
  only).
- Deployment telemetry (`config.telemetry`) sends an instance id, the
  version, admin emails and usage counts every 6 hours; never traces, prompts
  or other content. HubSpot, Slack, PostHog and Sentry stay off unless you
  set their keys. `global.airgap` turns off the calls the platform makes on
  its own (telemetry, the license heartbeat, price-list and model downloads);
  one minimal registration attempt remains, and offline it fails harmlessly
  and is retried.

## Troubleshooting and support bundle

| Symptom | Look at |
| --- | --- |
| `helm install` fails at once with "fix these values" | the listed keys |
| `helm install` times out | `kubectl logs job/<release>-bootstrap`; let a running job finish before retrying; raise `--timeout` together with `bootstrap.activeDeadlineSeconds` (the first bootstrap migrates an empty database) |
| bootstrap: `... is not reachable after 600s` | the host and port in the values, NetworkPolicies, DNS, `global.proxy.noProxy` |
| bootstrap: `native tiered storage policy required` | the ClickHouse [storage policy](#clickhouse) |
| bootstrap: `Unknown command: 'bootstrap_install'` | the backend image predates this chart (every image published before this chart does): set `image.tag` to images built from the chart's checkout |
| bootstrap or fi-collector: certificate verify failed | `global.caBundle`, or `PGSSLROOTCERT` and the CA mount (see [PostgreSQL](#postgresql)) |
| `helm upgrade`: `... cannot change` (StatefulSet ...) | [Install-time settings](#install-time-settings) |
| Workers log `waiting for the database migrations` | the bootstrap job: `kubectl logs job/<release>-bootstrap` |
| UI loads but every call fails, or CORS errors | `urls.api` / the API host; `config.corsAllowedOrigins`; with port-forwards, forward the backend to localhost:8000 |
| API answers 400 Bad Request | `config.allowedHosts`: the Host header is not an allowed host |
| The load balancer marks the API or collector unhealthy (`no healthy upstream`, 502, 503) | its own health checks, which skip the readiness probes: GKE `gatewayApi.gke.healthChecks`, EKS the `alb.ingress.kubernetes.io/healthcheck-*` Service annotations ([Gateway API](#gateway-api-recommended)) |
| WebSockets drop, or long uploads fail after 15 to 60 s | [Timeouts and WebSockets](#timeouts-and-websockets) |
| Custom code evals fail with "sandbox unavailable" | `codeExecutor` (see [Security](#security)) |
| The first-run setup screen marks a service down | `kubectl get pods`, then that service's logs |
| Filters and widgets suggest no attributes, fi-collector logs `observed catalog replay failed` | the observed-attribute index and its `observed_catalog_writer` user (created by the bootstrap job unless `bootstrap.propertyCatalog=false`) |

When you ask for help, attach a support bundle. It collects the release's
status and values (with passwords, keys, tokens, proxy URLs and the logins
in any URL redacted), its workloads, events, logs and the setup checks, and
never reads a Secret:

```sh
curl -fsSLO https://github.com/future-agi/future-agi/releases/download/v$VERSION/support-bundle.sh
bash support-bundle.sh -n futureagi -r futureagi   # writes futureagi-support-<release>-<time>.tar.gz
```

From a git checkout, run `deploy/helm/futureagi/hack/support-bundle.sh` with
the same options (`--help` lists them).

Open an issue at <https://github.com/future-agi/future-agi/issues>, or ask
on Discord.

## Developing from a git checkout

The chart lives in `deploy/helm/futureagi`. Install from the checkout with
the path in place of the OCI reference, and images built from the same
branch:

```sh
helm install futureagi deploy/helm/futureagi -n futureagi --create-namespace \
  -f deploy/helm/futureagi/examples/bundled.yaml \
  --set image.registry=<your registry> --set image.tag=<your tag> --timeout 20m
```

```sh
deploy/helm/futureagi/hack/check.sh             # lint, template, kubeconform, invariants, docs and schema
python3 deploy/helm/futureagi/hack/values_docs.py  # after editing values.yaml
TAG=local deploy/helm/futureagi/hack/kind-smoke.sh # install, check, upgrade and roll back on kind
deploy/helm/futureagi/hack/package.sh --no-digests --destination dist   # the release package, without digests
deploy/helm/futureagi/hack/list-images.sh -- -f my-values.yaml           # every image those values pull
```

Document every new key in `values.yaml` with a `# -- ` comment on the line
above it, naming the environment variable it sets in brackets;
`values_docs.py` regenerates `values.schema.json` and the table below, and
`check.sh` (run by `.github/workflows/helm-ci.yml` with Helm 3 and Helm 4,
on the source and on the packaged chart) fails when they are stale. Keep
existing keys working: a rename gets a shim and a `DEPRECATED` note.
Scope chart commits as `(helm)`, e.g. `fix(helm): ...`, so they lead the
chart's changelog.

## Values

Every key, generated from `values.yaml` by `hack/values_docs.py`.
Bracketed names are the environment variables a key sets;
the [configuration reference](https://docs.futureagi.com/docs/self-hosting/configuration/reference) explains each one.

<!-- values-table:start -->
| Key | Default | Description |
| --- | --- | --- |
| `nameOverride` | `""` | Override the chart name used in resource names. |
| `fullnameOverride` | `""` | Override the resource name prefix. Default: the release name, plus "-futureagi" unless the release name already contains it. |
| `global.imageRegistry` | `""` | Registry that replaces every image's own, Future AGI and third-party alike (docker.io, and ghcr.io for the bundled MinIO): a mirror holding every image `hack/list-images.sh` lists, e.g. `registry.example.com/futureagi-mirror`. Not a pull-through cache of one upstream: for Docker Hub alone set `image.registry` and each Docker Hub `<datastore>.bundled.image.registry` to it instead, and leave `objectStorage.bundled.image.registry` on ghcr.io (or a ghcr.io cache). Empty keeps each image's own registry. |
| `global.imagePullSecrets` | `[]` | Pull secrets added to every pod, e.g. `[{name: regcred}]`. |
| `global.storageClass` | `""` | StorageClass of every PersistentVolumeClaim the chart creates. Empty uses the cluster default. Install-time only for the bundled datastores (see "Install-time settings" in README.md). |
| `global.airgap` | `false` | Air-gapped install: turns off the platform's own calls to the internet. Turns telemetry off [FUTURE_AGI_TELEMETRY_DISABLED=true] (one minimal registration attempt remains; offline it fails harmlessly and is retried), the license heartbeat off unless `license.heartbeat` is `true` [FUTURE_AGI_ENTERPRISE_HEARTBEAT_DISABLED=true], sets [LITELLM_LOCAL_MODEL_COST_MAP=True] on the Python pods and serving, and [HF_HUB_OFFLINE=1, TRANSFORMERS_OFFLINE=1] on serving, whose models must then be pre-seeded on `serving.persistence` (examples/airgap.yaml). Mirror the images first (`hack/list-images.sh`) and set `global.imageRegistry` and `global.imagePullSecrets`. |
| `global.proxy.httpProxy` | `""` | [HTTP_PROXY, http_proxy] e.g. `http://proxy.example.com:3128`. Empty: no proxy. |
| `global.proxy.httpsProxy` | `""` | [HTTPS_PROXY, https_proxy] e.g. `http://proxy.example.com:3128`. Empty: no proxy. |
| `global.proxy.noProxy` | `""` | [NO_PROXY, no_proxy] extra hosts, domains or CIDRs that bypass the proxy, comma-separated. The chart always adds localhost, 127.0.0.1, the release's Services, `.<namespace>`, `.svc`, `.cluster.local` and the PostgreSQL, ClickHouse, Redis and Temporal hosts; add an external object-storage endpoint here when it is reachable without the proxy (MinIO in your network, an S3 VPC endpoint). Object storage requests match against the host they go to: with `objectStorage.external.endpoint` empty (AWS S3), that is `<bucket>.s3.<region>.amazonaws.com`, so list `.s3.<region>.amazonaws.com`, not `s3.amazonaws.com`. Set only with `httpProxy` or `httpsProxy`. |
| `global.caBundle.configMap` | `""` | ConfigMap holding the PEM bundle. Use `configMap` or `secret`, not both. |
| `global.caBundle.secret` | `""` | Secret holding the PEM bundle. |
| `global.caBundle.key` | `"ca.crt"` | Key of the PEM bundle in the ConfigMap or Secret. It must contain every CA the pods need, public roots included when the pods also reach the internet: it replaces the image's trust store [SSL_CERT_FILE, REQUESTS_CA_BUNDLE, CURL_CA_BUNDLE, NODE_EXTRA_CA_CERTS; PGSSLROOTCERT when `postgres.external.sslMode` is verify-ca or verify-full]. |
| `global.compatibility.openshift.adaptSecurityContext` | `"auto"` | `auto`: on OpenShift (the security.openshift.io/v1 API exists) leave runAsUser, runAsGroup, fsGroup and seccompProfile out of every pod and container security context, so the restricted-v2 SCC assigns them. `force`: always. `disabled`: never. |
| `image.registry` | `"docker.io"` | Registry of the Future AGI images. |
| `image.tag` | `""` | Tag of every Future AGI image (backend, workers, frontend, fi-collector, agentcc-gateway, serving, code-executor). Empty uses the chart's appVersion. A component's own `image.tag` wins. |
| `image.pullPolicy` | `"IfNotPresent"` | Pull policy of every image unless a component sets its own. |
| `image.pinDigests` | `true` | Pin each Future AGI image to the digest in `image.digests`, but only while its tag is the chart's appVersion and its repository path the published one: `--set image.tag=...` or another repository path runs that image by tag. Another registry (`image.registry`, `global.imageRegistry`) keeps the digest, which suits a mirror that copies the images with `crane copy` or `oras copy -r` (they keep the digests). Set `false` for a mirror that re-pushes images, or for your own build under the same repository path (or give that component its own `image.digest`): the published digest does not exist there and the pull fails. The bundled PostgreSQL, Redis and MinIO images are pinned the same way, on their default tags only. |
| `image.digests` | `{}` | Digests (`sha256:...`) of the release's images, by component: backend (also the workers and the bootstrap job), frontend, fiCollector, agentccGateway, serving, codeExecutor. Empty in git; the published chart carries the digests of its own release. A component's own `image.digest` wins. |
| `urls.app` | `""` | Public URL of the UI, e.g. `https://futureagi.example.com` [FRONTEND_URL, APP_URL, EXTRA_CSRF_ORIGINS]. Invite and password-reset links and app emails point here, with its scheme. Empty: derived from `ingress.app.host` with `ingress.enabled`, else from `gatewayApi.app.host` with `gatewayApi.enabled` (https unless `gatewayApi.tls` is false), else `http://localhost:3000`. |
| `urls.api` | `""` | Public URL of the API, e.g. `https://api.futureagi.example.com` [BASE_URL, and the UI's VITE_HOST_API]. Empty: derived from `ingress.api.host` with `ingress.enabled`, else from `gatewayApi.api.host` with `gatewayApi.enabled`, else `http://localhost:8000`. |
| `urls.otlp` | `""` | [FI_COLLECTOR_PUBLIC_URL] Public OTLP/HTTP base URL of fi-collector, e.g. `https://otlp.example.com`: what SDKs outside the cluster set as FI_BASE_URL, shown in the install notes, the in-app SDK snippet and the setup screen. Set it when the collector is exposed another way (e.g. `fiCollector.service.type=LoadBalancer`). Empty: derived from `ingress.otlp` with `ingress.enabled`, else from `gatewayApi.otlp` with `gatewayApi.enabled`, else `http://localhost:4318` (the port-forward in the install notes). |
| `urls.objects` | `""` | Public URL browsers download stored files from [MINIO_URL]; used when `objectStorage.backend` is `minio`. Empty: derived from `ingress.objects.host` with `ingress.enabled`, else the external endpoint, else `http://localhost:<objectStorage.bundled.service.downloadPort>` (9005). |
| `config.envType` | `"production"` | [ENV_TYPE] `production`: JSON logs, DEBUG off, refuses published default secrets. `local`: colored console logs. |
| `config.logLevel` | `"INFO"` | [LOG_LEVEL] of the backend, workers and bootstrap job. |
| `config.allowedHosts` | `""` | [ALLOWED_HOSTS], comma-separated. `*` accepts any Host header. A list also gets localhost and the in-cluster service names, which the probes and the workers use, and the pod's own IP (`$(POD_IP)`), which load balancers that health-check pods directly send as the Host. Empty: the API host once the API has a public URL (`urls.api`, `gatewayApi.api.host` or `ingress.api.host`), else `*`. |
| `config.corsAllowedOrigins` | `""` | [CORS_ALLOWED_ORIGINS] browser origins allowed to call the API with credentials, comma-separated. `*`: every origin. Empty: the UI's origin plus `extraCsrfOrigins` once the UI has a public URL (`urls.app`, `gatewayApi.app.host` or `ingress.app.host`), else every origin. |
| `config.extraCsrfOrigins` | `""` | [EXTRA_CSRF_ORIGINS] in addition to the UI URL, comma-separated. |
| `config.telemetry` | `true` | Deployment telemetry: an instance id, the version, admin emails and usage counts every 6 hours, never traces, prompts or other content. `false` sets FUTURE_AGI_TELEMETRY_DISABLED=true (one minimal ping remains). |
| `config.recaptcha` | `false` | [RECAPTCHA_ENABLED] reCAPTCHA on sign-up, login and token refresh, for every Host but localhost. Needs `secrets.extra.RECAPTCHA_SECRET_KEY` (without it every such login is refused) and a frontend image built with VITE_GOOGLE_SITE_KEY (a build-time setting: the published image has none, so its logins fail). |
| `config.otel` | `false` | [OTEL_ENABLED] export the platform's own traces over OpenTelemetry (configure OTEL_* in `config.extraEnv`). |
| `config.usageEvents` | `false` | [USAGE_EVENTS_ENABLED] billing usage events on the Redis stream usage:events, from the backend, workers and fi-collector. Only for installs that run the usage consumer (Future AGI Cloud): anywhere else nothing drains the stream and it fills Redis. |
| `config.cdcMode` | `"outbox"` | [FI_CDC_MODE] Postgres to ClickHouse change data capture. `outbox`: triggers plus a drain in the Temporal workers (no PeerDB). `off` removes it (Observe views then miss relational data). |
| `config.email.mailgunSenderDomain` | `""` | [MAILGUN_SENDER_DOMAIN]. Email stays off until `secrets.mailgunApiKey` is set too; invites then return a link to share yourself. |
| `config.email.fromEmail` | `""` | [DEFAULT_FROM_EMAIL] sender of every app email (invites, password resets). Empty: `Future AGI <noreply@<mailgunSenderDomain>>`. |
| `config.email.replyTo` | `""` | [DEFAULT_REPLY_TO_EMAIL] Reply-To of app emails. Empty: no Reply-To header, so replies go to the sender. |
| `config.email.serverEmail` | `""` | [SERVER_EMAIL] sender of error emails. |
| `config.email.existingSecret` | `""` | Existing Secret with the Mailgun API key [MAILGUN_API_KEY] (wins over `secrets.mailgunApiKey`). |
| `config.email.existingSecretKey` | `"MAILGUN_API_KEY"` | Key of the Mailgun API key in `existingSecret`. |
| `config.extraEnv` | `{}` | Extra environment variables for the backend, workers and bootstrap job, as `NAME: value`. Every supported key: https://docs.futureagi.com/docs/self-hosting/configuration/reference (rows marked H). |
| `config.extraEnvFrom` | `[]` | Extra `envFrom` sources for the backend, workers and bootstrap job, e.g. `[{secretRef: {name: my-env}}]`. |
| `secrets.existingSecret` | `""` | Existing Secret with the application keys: SECRET_KEY, INTEGRATION_ENCRYPTION_KEY, AGENTCC_INTERNAL_API_KEY, AGENTCC_ADMIN_TOKEN, PROPERTY_CATALOG_API_PASSWORD, PROPERTY_CATALOG_CONSUMER_PASSWORD and AGENTCC_WEBHOOK_SECRET (unless `agentccWebhookSecret` is set). Empty: generated. Required with Argo CD, which cannot `lookup` the generated Secret. |
| `secrets.llm.existingSecret` | `""` | Existing Secret with any of OPENAI_API_KEY, ANTHROPIC_API_KEY, GOOGLE_API_KEY, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY (missing keys are fine). Overrides the values below. |
| `secrets.llm.openaiApiKey` | `""` | [OPENAI_API_KEY] server-wide key for built-in evals and the gateway. Workspaces can also add their own in the UI. |
| `secrets.llm.anthropicApiKey` | `""` | [ANTHROPIC_API_KEY] |
| `secrets.llm.googleApiKey` | `""` | [GOOGLE_API_KEY] Gemini (Google AI Studio); the gateway gets it as GEMINI_API_KEY. |
| `secrets.llm.awsAccessKeyId` | `""` | [AWS_ACCESS_KEY_ID] AWS Bedrock. |
| `secrets.llm.awsSecretAccessKey` | `""` | [AWS_SECRET_ACCESS_KEY] AWS Bedrock. |
| `secrets.llm.awsRegion` | `"us-east-1"` | [AWS_REGION] AWS Bedrock region (not secret). |
| `secrets.eeLicenseKey` | `""` | Deprecated: use `license.key` or `license.existingSecret`. [EE_LICENSE_KEY] Enterprise Edition license, still honoured when `license.*` is empty. |
| `secrets.mailgunApiKey` | `""` | [MAILGUN_API_KEY] turns email on, with `config.email`. `config.email.existingSecret` wins. |
| `secrets.agentccWebhookSecret` | `""` | [AGENTCC_WEBHOOK_SECRET] shared secret the gateway sends its request logs to the backend with. Empty: read from `existingSecret` when that is set, else generated. |
| `secrets.extra` | `{}` | Any other secret environment variables for the backend, workers and bootstrap job, as `NAME: value` (e.g. SENTRY_DSN, DAYTONA_API_KEY). Stored in <fullname>-secrets. |
| `edition` | `"oss"` | `oss` or `ee`. `ee` requires a license (`license.key` or `license.existingSecret`) and shows the license and SSO settings in the install notes. It never changes the image. |
| `license.key` | `""` | [EE_LICENSE_KEY] Enterprise license key, stored in <fullname>-secrets. Prefer `existingSecret`. Setting a license also turns on the Enterprise apps, whose migrations the bootstrap job runs. |
| `license.existingSecret` | `""` | Existing Secret with the license key (wins over `key`). The backend, the workers and the bootstrap job read it. |
| `license.existingSecretKey` | `"EE_LICENSE_KEY"` | Key of the license in `existingSecret`. |
| `license.url` | `""` | [FUTURE_AGI_LICENSE_URL] license activation and heartbeat server. Empty: the application default (Future AGI's license service). |
| `license.heartbeat` | `null` | License heartbeat [FUTURE_AGI_ENTERPRISE_HEARTBEAT_DISABLED, inverted]. Empty (null): on, off with `global.airgap`. `true` or `false` wins. |
| `license.publicKey` | `""` | [EE_LICENSE_PUBLIC_KEY] PEM public key that verifies the license. Honoured only by images without a built-in license key (pre-GA builds); leave empty unless Future AGI gave you one. |
| `license.clockSkewSeconds` | `300` | [EE_LICENSE_CLOCK_SKEW_SECONDS] tolerance when checking the license's validity window; set only while a license is configured. |
| `auth.google.clientId` | `""` | [AUTH0_CLIENT_ID] OAuth client ID of a Google Cloud "Web application" client (the app names Google's settings AUTH0_*). |
| `auth.google.clientSecret` | `""` | [AUTH0_CLIENT_SECRET] stored in <fullname>-secrets. Prefer `existingSecret`. |
| `auth.google.existingSecret` | `""` | Existing Secret with the client ID and secret (wins over the values above). |
| `auth.google.clientIdKey` | `"AUTH0_CLIENT_ID"` | Key of the client ID in `existingSecret`. |
| `auth.google.clientSecretKey` | `"AUTH0_CLIENT_SECRET"` | Key of the client secret in `existingSecret`. |
| `auth.github.clientId` | `""` | [GITHUB_CLIENT_ID] of a GitHub OAuth app. |
| `auth.github.clientSecret` | `""` | [GITHUB_CLIENT_SECRET] stored in <fullname>-secrets. Prefer `existingSecret`. |
| `auth.github.existingSecret` | `""` | Existing Secret with the client ID and secret (wins over the values above). |
| `auth.github.clientIdKey` | `"GITHUB_CLIENT_ID"` | Key of the client ID in `existingSecret`. |
| `auth.github.clientSecretKey` | `"GITHUB_CLIENT_SECRET"` | Key of the client secret in `existingSecret`. |
| `auth.microsoft.clientId` | `""` | [MICROSOFT_CLIENT_ID] of a Microsoft Entra app registration (multi-tenant: the app signs in through the `common` endpoint). |
| `auth.microsoft.clientSecret` | `""` | [MICROSOFT_CLIENT_SECRET] stored in <fullname>-secrets. Prefer `existingSecret`. |
| `auth.microsoft.existingSecret` | `""` | Existing Secret with the client ID and secret (wins over the values above). |
| `auth.microsoft.clientIdKey` | `"MICROSOFT_CLIENT_ID"` | Key of the client ID in `existingSecret`. |
| `auth.microsoft.clientSecretKey` | `"MICROSOFT_CLIENT_SECRET"` | Key of the client secret in `existingSecret`. |
| `externalSecrets.enabled` | `false` | Render the ExternalSecrets below (needs the External Secrets Operator and its CRDs). |
| `externalSecrets.apiVersion` | `"external-secrets.io/v1"` | API version of the ExternalSecret objects. |
| `externalSecrets.secretStoreRef` | `{"name": "", "kind": "ClusterSecretStore"}` | SecretStore or ClusterSecretStore every ExternalSecret reads from, e.g. `{name: vault, kind: ClusterSecretStore}`. An entry's own `secretStoreRef` wins. |
| `externalSecrets.refreshInterval` | `"1h"` | How often the operator re-reads the store. |
| `externalSecrets.secrets` | `{}` | One ExternalSecret per entry, as `<group>: {data: {<SECRET_KEY>: {key, property}}, dataFrom: [...], secretStoreRef: {...}}`. Groups and the Secret each creates: `app` (secrets.existingSecret), `llm` (secrets.llm.existingSecret), `license` (license.existingSecret), `email` (config.email.existingSecret), `google`, `github`, `microsoft` (auth.*.existingSecret), `admin` (bootstrap.admin.existingSecret), `postgres`, `clickhouse`, `redis`, `objectStorage` (<store>.existingSecret). That existingSecret value must be set. See examples/external-secrets.yaml. |
| `reloader.enabled` | `false` | Add `reloader.annotations` to every Future AGI Deployment (needs Stakater Reloader, or a tool that reads the same annotations). |
| `reloader.annotations` | `{"reloader.stakater.com/auto": "true"}` | Annotations added to the Deployments' metadata. |
| `postgres.mode` | `"external"` | `external` or `bundled`. |
| `postgres.database` | `"futureagi"` | [PG_DB] database name. |
| `postgres.user` | `"futureagi"` | [PG_USER] |
| `postgres.password` | `""` | [PG_PASSWORD]. External: required unless `existingSecret`. Bundled: empty generates one. |
| `postgres.existingSecret` | `""` | Existing Secret holding the password. |
| `postgres.existingSecretPasswordKey` | `"password"` | Key of the password in `existingSecret`. |
| `postgres.external.host` | `""` | [PG_HOST] PostgreSQL 16 server (11 or newer works), reached directly: the outbox CDC holds a session advisory lock and migrations set session options. Put a pooler in `postgres.pooler`, not here. |
| `postgres.external.port` | `5432` | [PG_PORT] |
| `postgres.external.sslMode` | `"prefer"` | [PGSSLMODE] disable, allow, prefer, require, verify-ca or verify-full. verify-ca and verify-full need the server's CA: mount it with `backend`, `worker`, `bootstrap` and `fiCollector` `extraVolumes`/`extraVolumeMounts`, and point PGSSLROOTCERT at it in `config.extraEnv` and `fiCollector.extraEnv`. |
| `postgres.pooler.enabled` | `false` | Send the backend's and workers' Django connections through a transaction-mode pooler such as PgBouncer [PGBOUNCER_HOST, PGBOUNCER_PORT], for many API and worker pods. `PG_HOST` stays the direct server (outbox CDC, migrations), and the bootstrap job always connects directly [PG_DIRECT_HOST]. PgBouncer settings the app needs: `pool_mode = transaction`, `server_reset_query_always = 1`, `ignore_startup_parameters = extra_float_digits,search_path`. |
| `postgres.pooler.host` | `""` | [PGBOUNCER_HOST] host of the pooler. |
| `postgres.pooler.port` | `6432` | [PGBOUNCER_PORT] port of the pooler. |
| `postgres.readReplica.enabled` | `false` | Register a read replica (or a pooler in front of one) for the backend and workers [PGBOUNCER_READ_HOST, PGBOUNCER_READ_PORT, PG_READ_DB]. Only the models and features in `optIn` read from it; everything else stays on the primary. |
| `postgres.readReplica.host` | `""` | [PGBOUNCER_READ_HOST] host of the replica, or of its pooler. Same user and password as the primary. |
| `postgres.readReplica.port` | `5432` | [PGBOUNCER_READ_PORT] |
| `postgres.readReplica.database` | `""` | [PG_READ_DB] database on the replica. Empty: `postgres.database`. |
| `postgres.readReplica.optIn` | `[]` | [READ_REPLICA_OPT_IN] model class names (e.g. `Dashboard`) and `feature:` keys routed to the replica. Empty: nothing reads from it. |
| `postgres.bundled.image.registry` | `"docker.io"` | Registry of the bundled PostgreSQL image. |
| `postgres.bundled.image.repository` | `"library/postgres"` | Repository of the bundled PostgreSQL image. Debian-based, like the compose setups, so data directories are interchangeable. |
| `postgres.bundled.image.tag` | `"16.15-trixie"` | Tag of the bundled PostgreSQL image: an exact 16.x release on Debian (the compose setups run `postgres:16`, also Debian). |
| `postgres.bundled.image.digest` | `""` | Digest pinned after the tag; wins over the chart's own pin. Empty: the default tag above runs pinned to the digest the chart was tested with (while `image.pinDigests` is on), and any other tag or repository runs by tag. |
| `postgres.bundled.parameters` | `{"max_connections": "300", "shared_buffers": "256MB"}` | Server settings passed as `-c name=value`. |
| `postgres.bundled.persistence.size` | `"20Gi"` | Size of the data volume. Install-time only (see "Install-time settings" in README.md). |
| `postgres.bundled.persistence.storageClass` | `""` | StorageClass of the data volume. Empty: `global.storageClass`, else the cluster default. Install-time only. |
| `postgres.bundled.resources` | see values.yaml | Resources of the bundled PostgreSQL. |
| `postgres.bundled.nodeSelector` | `{}` | Node selector of the bundled PostgreSQL. Empty: the top-level `nodeSelector`. |
| `postgres.bundled.tolerations` | `[]` | Tolerations of the bundled PostgreSQL. Empty: the top-level `tolerations`. |
| `postgres.bundled.affinity` | `{}` | Affinity of the bundled PostgreSQL. Empty: the top-level `affinity`. |
| `postgres.bundled.podSecurityContext` | `{}` | Pod securityContext merged over the chart's (runAsNonRoot, the image's user and group, fsGroup, RuntimeDefault seccomp). `global.compatibility.openshift.adaptSecurityContext` drops the fixed IDs. |
| `postgres.bundled.containerSecurityContext` | `{}` | Container securityContext merged over the chart's (no privilege escalation, all capabilities dropped). |
| `clickhouse.mode` | `"external"` | `external` or `bundled`. ClickHouse 25.3 or newer. |
| `clickhouse.database` | `"default"` | [CH_DATABASE, CH25_DATABASE] database of traces and analytics. |
| `clickhouse.user` | `"default"` | [CH_USERNAME] needs CREATE on the database; the bootstrap also creates the observed-attribute index (CREATE DATABASE, CREATE USER, GRANT) unless `bootstrap.propertyCatalog` is false. Bundled: `default`, the bundled server's only user. |
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
| `clickhouse.bundled.lowMemory` | `true` | Small caches and merge pools and no system log tables (deploy/standalone/clickhouse), as in the Standalone install. Turn off on nodes with 8 GiB or more for ClickHouse. |
| `clickhouse.bundled.persistence.size` | `"50Gi"` | Size of the data volume. Install-time only (see "Install-time settings" in README.md). |
| `clickhouse.bundled.persistence.storageClass` | `""` | StorageClass of the data volume. Empty: `global.storageClass`, else the cluster default. Install-time only. |
| `clickhouse.bundled.resources` | see values.yaml | Resources of the bundled ClickHouse. |
| `clickhouse.bundled.nodeSelector` | `{}` | Node selector of the bundled ClickHouse. Empty: the top-level `nodeSelector`. |
| `clickhouse.bundled.tolerations` | `[]` | Tolerations of the bundled ClickHouse. Empty: the top-level `tolerations`. |
| `clickhouse.bundled.affinity` | `{}` | Affinity of the bundled ClickHouse. Empty: the top-level `affinity`. |
| `clickhouse.bundled.podSecurityContext` | `{}` | Pod securityContext merged over the chart's (runAsNonRoot, the image's user and group, fsGroup, RuntimeDefault seccomp). `global.compatibility.openshift.adaptSecurityContext` drops the fixed IDs. |
| `clickhouse.bundled.containerSecurityContext` | `{}` | Container securityContext merged over the chart's (no privilege escalation, all capabilities dropped). |
| `redis.mode` | `"external"` | `external` or `bundled`. Cache, locks and the live-update channel layer. |
| `redis.password` | `""` | [REDIS_PASSWORD] Must be URL-safe (letters, digits, `-._~`): it is part of the Redis URLs. External: empty means no AUTH. Bundled: empty generates one. |
| `redis.existingSecret` | `""` | Existing Secret holding the password. |
| `redis.existingSecretPasswordKey` | `"password"` | Key of the password in `existingSecret`. |
| `redis.external.host` | `""` | [REDIS_HOST] Redis 6 or newer with databases 0-4 (the app uses 0 to 3; the LLM gateway 4, see `agentccGateway.redis`). |
| `redis.external.port` | `6379` | [REDIS_PORT] |
| `redis.external.tls` | `false` | Connect the app over TLS (`rediss://`). fi-collector has no Redis TLS: with TLS on it runs without Redis (key revocation then reaches it through its 5-minute auth cache). |
| `redis.bundled.image.registry` | `"docker.io"` | Registry of the bundled Redis image. |
| `redis.bundled.image.repository` | `"library/redis"` | Repository of the bundled Redis image. |
| `redis.bundled.image.tag` | `"7.4.11-alpine"` | Tag of the bundled Redis image: an exact 7.x release. |
| `redis.bundled.image.digest` | `""` | Digest pinned after the tag; wins over the chart's own pin. Empty: the default tag above runs pinned to the digest the chart was tested with (while `image.pinDigests` is on), and any other tag or repository runs by tag. |
| `redis.bundled.maxmemory` | `"384mb"` | Redis `maxmemory`; keep it below the memory limit. |
| `redis.bundled.maxmemoryPolicy` | `"volatile-lru"` | Redis `maxmemory-policy`. volatile-lru evicts only keys with a TTL (cache entries), never locks or state. |
| `redis.bundled.persistence.enabled` | `true` | Keep an append-only file on a volume. Without it a restart drops cache, locks and in-flight state (the app recovers). Install-time only. |
| `redis.bundled.persistence.size` | `"2Gi"` | Size of the data volume. Install-time only (see "Install-time settings" in README.md). |
| `redis.bundled.persistence.storageClass` | `""` | StorageClass of the data volume. Empty: `global.storageClass`, else the cluster default. Install-time only. |
| `redis.bundled.resources` | see values.yaml | Resources of the bundled Redis. |
| `redis.bundled.nodeSelector` | `{}` | Node selector of the bundled Redis. Empty: the top-level `nodeSelector`. |
| `redis.bundled.tolerations` | `[]` | Tolerations of the bundled Redis. Empty: the top-level `tolerations`. |
| `redis.bundled.affinity` | `{}` | Affinity of the bundled Redis. Empty: the top-level `affinity`. |
| `redis.bundled.podSecurityContext` | `{}` | Pod securityContext merged over the chart's (runAsNonRoot, the image's user and group, fsGroup, RuntimeDefault seccomp). `global.compatibility.openshift.adaptSecurityContext` drops the fixed IDs. |
| `redis.bundled.containerSecurityContext` | `{}` | Container securityContext merged over the chart's (no privilege escalation, all capabilities dropped). |
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
| `temporal.bundled.podSecurityContext` | `{}` | Pod securityContext merged over the chart's (runAsNonRoot, the image's user and group, fsGroup, RuntimeDefault seccomp). `global.compatibility.openshift.adaptSecurityContext` drops the fixed IDs. |
| `temporal.bundled.containerSecurityContext` | `{}` | Container securityContext merged over the chart's (no privilege escalation, all capabilities dropped). |
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
| `objectStorage.bundled.image.digest` | `""` | Digest pinned after the tag; wins over the chart's own pin. Empty: the default tag above runs pinned to the digest the chart was tested with (while `image.pinDigests` is on), and any other tag or repository runs by tag. |
| `objectStorage.bundled.persistence.size` | `"20Gi"` | Size of the data volume. Install-time only (see "Install-time settings" in README.md). |
| `objectStorage.bundled.persistence.storageClass` | `""` | StorageClass of the data volume. Empty: `global.storageClass`, else the cluster default. Install-time only. |
| `objectStorage.bundled.goMemLimit` | `"384MiB"` | [GOMEMLIMIT] soft memory limit; keep it below the memory limit. |
| `objectStorage.bundled.service.type` | `"ClusterIP"` | Service type of the bundled object storage. `LoadBalancer` (examples/local.yaml) also publishes `downloadPort` for browser downloads. Evaluation only: it exposes the bucket. |
| `objectStorage.bundled.service.downloadPort` | `9005` | Local port of browser downloads [MINIO_URL `http://localhost:<port>`, unless `urls.objects` or `ingress.objects.host` is set]: the Service's download port with a `LoadBalancer` or `NodePort` type, else the local end of the install notes' port-forward. |
| `objectStorage.bundled.resources` | see values.yaml | Resources of the bundled object storage. |
| `objectStorage.bundled.nodeSelector` | `{}` | Node selector of the bundled object storage. Empty: the top-level `nodeSelector`. |
| `objectStorage.bundled.tolerations` | `[]` | Tolerations of the bundled object storage. Empty: the top-level `tolerations`. |
| `objectStorage.bundled.affinity` | `{}` | Affinity of the bundled object storage. Empty: the top-level `affinity`. |
| `objectStorage.bundled.podSecurityContext` | `{}` | Pod securityContext merged over the chart's (runAsNonRoot, the image's user and group, fsGroup, RuntimeDefault seccomp). `global.compatibility.openshift.adaptSecurityContext` drops the fixed IDs. |
| `objectStorage.bundled.containerSecurityContext` | `{}` | Container securityContext merged over the chart's (no privilege escalation, all capabilities dropped). |
| `backend.image.registry` | `""` | Registry. Empty: `image.registry`. |
| `backend.image.repository` | `"futureagi/future-agi"` | Repository of the backend image (also the workers' and the bootstrap job's). |
| `backend.image.tag` | `""` | Tag. Empty: `image.tag`, else the chart's appVersion. |
| `backend.image.digest` | `""` | Optional digest (`sha256:...`) pinned after the tag. |
| `backend.image.pullPolicy` | `""` | Pull policy. Empty: `image.pullPolicy`. |
| `backend.replicas` | `1` | Replicas when autoscaling is off. More than one needs the Redis channel layer, which this chart always configures. |
| `backend.preStopSleepSeconds` | `10` | Seconds each API pod sleeps before it stops (preStop), so Services and load balancers stop sending it requests first. 0: none. |
| `backend.terminationGracePeriodSeconds` | `90` | Seconds a stopping API pod gets: the preStop sleep, then in-flight requests. Raise it to at least your load balancer's read timeout for long requests. |
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
| `backend.autoscaling.behavior` | `{}` | HorizontalPodAutoscaler `behavior` (autoscaling/v2), e.g. `{scaleDown: {stabilizationWindowSeconds: 300}}`. Empty: the Kubernetes defaults. |
| `backend.pdb.enabled` | `true` | PodDisruptionBudget for the API. |
| `backend.pdb.maxUnavailable` | `1` | At most this many API pods down during voluntary disruptions. |
| `backend.extraEnv` | `{}` | Extra environment variables for the API only, as `NAME: value`. |
| `backend.podAnnotations` | `{}` | Pod annotations. |
| `backend.podLabels` | `{}` | Extra pod labels. |
| `backend.podSecurityContext` | `{}` | Merged over the chart's pod security context (non-root uid 1000, fsGroup 1000, RuntimeDefault seccomp). |
| `backend.containerSecurityContext` | `{}` | Merged over the chart's container security context (read-only root filesystem, no privilege escalation, all capabilities dropped). |
| `backend.extraVolumes` | `[]` | Extra volumes. |
| `backend.extraVolumeMounts` | `[]` | Extra volume mounts. |
| `backend.nodeSelector` | `{}` | Node selector of the API pods, the bootstrap job and the `helm test` pod. Empty: the top-level `nodeSelector`. |
| `backend.tolerations` | `[]` | Tolerations of the API pods, the bootstrap job and the `helm test` pod. Empty: the top-level `tolerations`. |
| `backend.affinity` | `{}` | Affinity of the API pods, the bootstrap job and the `helm test` pod. Empty: the top-level `affinity`. |
| `backend.topologySpreadConstraints` | `[]` | Topology spread constraints. Empty: the top-level `topologySpreadConstraints`. |
| `worker.image.registry` | `""` | Registry. Empty: `backend.image.registry`, else `image.registry`. |
| `worker.image.repository` | `""` | Repository. Empty: `backend.image.repository` (workers run the backend image). |
| `worker.image.tag` | `""` | Tag. Empty: `backend.image.tag`, else `image.tag`, else the chart's appVersion. |
| `worker.image.digest` | `""` | Optional digest. Empty: `backend.image.digest`. |
| `worker.image.pullPolicy` | `""` | Pull policy. Empty: `backend.image.pullPolicy`, else `image.pullPolicy`. |
| `worker.gracefulShutdownSeconds` | `60` | [TEMPORAL_GRACEFUL_SHUTDOWN_TIMEOUT] seconds a stopping worker lets running activities finish. The pod's grace period is this plus `preStopSleepSeconds` plus 30 s. A `queues` entry can set its own. |
| `worker.preStopSleepSeconds` | `15` | Seconds each worker pod sleeps before it stops (preStop). 0: none. A `queues` entry can set its own. |
| `worker.priorityClassName` | `""` | PriorityClass of every worker pod. Empty: the top-level `priorityClassName`. A `queues` entry can set its own. |
| `worker.allQueues.enabled` | `true` | One Deployment polling every generic queue (default, tasks_s, tasks_l, tasks_xl, trace_ingestion, agent_compass). This worker also drains the outbox CDC. |
| `worker.allQueues.replicas` | `1` | Replicas when autoscaling is off. |
| `worker.allQueues.excludedQueues` | `["simulation_runner"]` | [TEMPORAL_EXCLUDED_QUEUES] queues it does not poll. simulation_runner needs the separate simulation-runner image (a `queues` entry with its own image). |
| `worker.allQueues.excludeDedicatedQueues` | `true` | Also exclude every `queues` entry [TEMPORAL_EXCLUDED_QUEUES], so heavy queues run only on their own Deployments. `false`: the all-queues worker polls them too. |
| `worker.allQueues.maxConcurrentActivities` | `50` | [TEMPORAL_MAX_CONCURRENT_ACTIVITIES] activity slots per queue. |
| `worker.allQueues.maxConcurrentWorkflowTasks` | `50` | [TEMPORAL_MAX_CONCURRENT_WORKFLOW_TASKS] workflow-task slots per queue. |
| `worker.allQueues.resources` | see values.yaml | Resources of each all-queues worker pod. |
| `worker.allQueues.autoscaling.enabled` | `false` | HorizontalPodAutoscaler for the all-queues worker. |
| `worker.allQueues.autoscaling.minReplicas` | `1` | Minimum replicas. |
| `worker.allQueues.autoscaling.maxReplicas` | `4` | Maximum replicas. |
| `worker.allQueues.autoscaling.targetCPUUtilizationPercentage` | `70` | Target average CPU utilization (percent of requests). |
| `worker.allQueues.autoscaling.targetMemoryUtilizationPercentage` | `""` | Target average memory utilization (percent of requests). Empty: not used. |
| `worker.allQueues.autoscaling.behavior` | `{}` | HorizontalPodAutoscaler `behavior` (autoscaling/v2). Empty: the Kubernetes defaults. |
| `worker.exactAggregation.enabled` | `true` | A single-slot worker for the exact_aggregation queue, the admission boundary for expensive exact analytics [EXACT_AGGREGATION_TASK_QUEUE=exact_aggregation]. Off: that work runs on tasks_xl. |
| `worker.exactAggregation.resources` | see values.yaml | Resources of the exact-aggregation worker. |
| `worker.queues` | `[]` | Dedicated per-queue Deployments, added to the all-queues worker (which stops polling them, see `allQueues.excludeDedicatedQueues`), e.g. `[{name: tasks_s, replicas: 2, maxConcurrentActivities: 200}]`; examples/sizes/large.yaml has a full split. Each entry: name (required), replicas, maxConcurrentActivities, maxConcurrentWorkflowTasks, resources, image, extraEnv (wins over `worker.extraEnv` and the chart's own values), autoscaling {enabled, minReplicas, maxReplicas, targetCPUUtilizationPercentage, targetMemoryUtilizationPercentage, behavior} (gaps filled from `allQueues.autoscaling`), gracefulShutdownSeconds, preStopSleepSeconds, pdb {enabled, maxUnavailable}, and nodeSelector, tolerations, affinity, topologySpreadConstraints, priorityClassName, podAnnotations, podLabels (each falling back to the `worker` one). |
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
| `frontend.autoscaling.targetMemoryUtilizationPercentage` | `""` | Target average memory utilization (percent of requests). Empty: not used. |
| `frontend.autoscaling.behavior` | `{}` | HorizontalPodAutoscaler `behavior` (autoscaling/v2), e.g. `{scaleDown: {stabilizationWindowSeconds: 300}}`. Empty: the Kubernetes defaults. |
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
| `fiCollector.preStopSleepSeconds` | `10` | Seconds each collector pod waits before it stops (preStop, the kubelet's sleep action: Kubernetes 1.30 or newer, skipped on older clusters), so Services stop sending it spans first. 0: none. |
| `fiCollector.terminationGracePeriodSeconds` | `45` | Seconds a stopping collector gets: the preStop sleep, then it flushes its batches to ClickHouse. |
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
| `fiCollector.autoscaling.targetMemoryUtilizationPercentage` | `""` | Target average memory utilization (percent of requests). Empty: not used. |
| `fiCollector.autoscaling.behavior` | `{}` | HorizontalPodAutoscaler `behavior` (autoscaling/v2), e.g. `{scaleDown: {stabilizationWindowSeconds: 300}}`. Empty: the Kubernetes defaults. |
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
| `agentccGateway.preStopSleepSeconds` | `10` | Seconds each gateway pod waits before it stops (preStop, the kubelet's sleep action: Kubernetes 1.30 or newer, skipped on older clusters), so Services stop sending it requests first. 0: none. |
| `agentccGateway.terminationGracePeriodSeconds` | `45` | Seconds a stopping gateway gets. Keep it at least the preStop sleep + `config.server.shutdown_timeout` (30 s, for in-flight streams) + 5 s (the gateway then sends its buffered request logs): 10 + 30 + 5 = 45. With `config.otel.exporter: otlp`, add up to 15 s for the exporter's last flush. |
| `agentccGateway.existingConfigMap` | `""` | Existing ConfigMap with the gateway configuration under the key `config.yaml`. Empty: rendered from `config`. |
| `agentccGateway.config` | see values.yaml | Gateway configuration (agentcc-gateway/config.example.yaml documents every field). `${VAR}` expands from the environment: provider keys come from `secrets.llm`. `server.port` is also the container port: the chart pins it with the AGENTCC_PORT variable, which wins over an `existingConfigMap` too. |
| `agentccGateway.controlPlaneSync` | `true` | Pull keys and org settings from the backend on start [AGENTCC_CONTROL_PLANE_URL, AGENTCC_SYNC_ON_STARTUP], so every replica and a restarted pod serve the same keys. |
| `agentccGateway.allowPrivateProviderURLs` | `false` | Let org providers use base URLs on private networks, such as a local Ollama or vLLM [AGENTCC_ALLOW_PRIVATE_PROVIDER_URLS], for the gateway and the backend. Loopback, link-local and cloud metadata addresses stay refused. Only when everyone who can add a provider is trusted: it puts every in-cluster service in reach of a provider URL. Gateway and backend images from before this setting ignore it and refuse every private URL. |
| `agentccGateway.redis.enabled` | `"auto"` | Keep rate limits, budgets and other shared gateway state in the release's Redis [AGENTCC_REDIS_ADDRESS, AGENTCC_REDIS_PASSWORD, AGENTCC_REDIS_DB]. `auto`: on when the gateway runs more than one replica (`replicas` above 1 or autoscaling), off with `redis.external.tls` (the gateway has no Redis TLS): give it a Redis without TLS with AGENTCC_REDIS_ADDRESS in `extraEnv` instead (examples/cloud/aks.yaml). The chart refuses more than one replica without either. |
| `agentccGateway.redis.db` | `4` | [AGENTCC_REDIS_DB] Redis database of the gateway, apart from the app's 0-3. |
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
| `agentccGateway.autoscaling.targetMemoryUtilizationPercentage` | `""` | Target average memory utilization (percent of requests). Empty: not used. |
| `agentccGateway.autoscaling.behavior` | `{}` | HorizontalPodAutoscaler `behavior` (autoscaling/v2), e.g. `{scaleDown: {stabilizationWindowSeconds: 300}}`. Empty: the Kubernetes defaults. |
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
| `serving.enabled` | `false` | Embedding model server for embedding-based evals, knowledge bases, Vector DB columns and (with an Enterprise Edition license) Error Feed clustering [MODEL_SERVING_URL]. Off: those features are unavailable and the setup screen marks them off. |
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
| `bootstrap.installHook` | `"auto"` | When the job runs on install. `auto`: pre-install when every datastore is external (the app starts on a ready schema), post-install when any is bundled (they must exist first). It always runs pre-upgrade. Argo CD runs a pre-install job in PreSync, a post-install one after the bundled datastores and before the rest of the release. |
| `bootstrap.propertyCatalog` | `true` | Create the observed-attribute index database and its two ClickHouse users (needs CREATE USER and GRANT). `false`: create them yourself. |
| `bootstrap.waitTimeoutSeconds` | `600` | Seconds to wait for each datastore to accept connections. |
| `bootstrap.clickhouseTimeoutSeconds` | `600` | Deadline of the ClickHouse schema step, in seconds. |
| `bootstrap.backoffLimit` | `2` | Retries of a failed job. |
| `bootstrap.activeDeadlineSeconds` | `1080` | Deadline of the whole job, in seconds. Keep it below `helm install/upgrade --timeout` (20m in every documented command), so Helm reports how the job ended; raise both together for a long migration. |
| `bootstrap.ttlSecondsAfterFinished` | `86400` | Seconds a finished job (and its logs) is kept. `0`: deleted as soon as it finishes. Empty: kept until the next install or upgrade replaces it. |
| `bootstrap.resources` | see values.yaml | Resources of the bootstrap job. |
| `bootstrap.serviceAccount.create` | `true` | Create a dedicated ServiceAccount for the job (it exists before any other resource on install). |
| `bootstrap.serviceAccount.name` | `""` | Name of the job's ServiceAccount. Empty: <fullname>-bootstrap when created, else the namespace default. |
| `bootstrap.serviceAccount.annotations` | `{}` | Annotations, e.g. for cloud IAM database authentication. |
| `bootstrap.podAnnotations` | `{}` | Pod annotations. |
| `bootstrap.extraVolumes` | `[]` | Extra volumes, e.g. the CA of `postgres.external.sslMode=verify-full`. Empty: `backend.extraVolumes`. |
| `bootstrap.extraVolumeMounts` | `[]` | Extra volume mounts. Empty: `backend.extraVolumeMounts`. |
| `bootstrap.admin.existingSecret` | `""` | Existing Secret with the admin's email, name and password (the sign-up rules: 8+ characters, not a common password, not all digits). Empty: create the first account yourself (install notes, step 3). |
| `bootstrap.admin.emailKey` | `"email"` | Key of the email in `existingSecret`. |
| `bootstrap.admin.nameKey` | `"name"` | Key of the full name in `existingSecret`. |
| `bootstrap.admin.passwordKey` | `"password"` | Key of the password in `existingSecret`. |
| `ingress.enabled` | `false` | One Ingress for the UI, the API and OTLP/HTTP. |
| `ingress.className` | `""` | IngressClass, e.g. `nginx`. |
| `ingress.annotations` | `{}` | Ingress annotations (e.g. cert-manager, proxy body size and timeouts for uploads and WebSockets). |
| `ingress.tls` | `[]` | TLS entries, e.g. `[{secretName: futureagi-tls, hosts: [futureagi.example.com, api.futureagi.example.com]}]`. Hosts listed here get https URLs. |
| `ingress.app.host` | `""` | Host of the UI. |
| `ingress.api.host` | `""` | Host of the API. It must differ from the UI host: the API serves many top-level paths. |
| `ingress.otlp.enabled` | `true` | Route OTLP/HTTP (`/v1/traces`, `/tracer/v1/traces`) to fi-collector. |
| `ingress.otlp.host` | `""` | Host for OTLP/HTTP. Empty: the API host. |
| `ingress.objects.host` | `""` | Host of the bundled object storage (`objectStorage.mode=bundled`), for browser downloads [MINIO_URL]. Evaluation only, like the bundled storage itself: it publishes the bucket through the Ingress. Empty: not exposed. |
| `ingress.websocket.enabled` | `false` | A second Ingress for the API's WebSocket paths (`/ws/`) with its own annotations, so long-lived connections get long timeouts without raising them for every request. Same class, host and TLS as the main one. |
| `ingress.websocket.annotations` | `{}` | Annotations of the WebSocket Ingress, over `ingress.annotations`. ingress-nginx: `nginx.ingress.kubernetes.io/proxy-read-timeout: "3600"` and `proxy-send-timeout: "3600"`. Traefik has no per-Ingress timeout: raise the entrypoint's `respondingTimeouts` (examples/ingress-traefik.yaml). |
| `gatewayApi.enabled` | `false` | Render HTTPRoutes for the UI and the API (OTLP/HTTP on the exact paths `/v1/traces` and `/tracer/v1/traces` to fi-collector), plus the optional routes below. URLs (`urls.*`) are derived from these hosts unless the ingress is on. |
| `gatewayApi.parentRefs` | `[]` | Gateways (and listeners) the routes attach to, e.g. `[{name: public, namespace: gateway-system, sectionName: https}]`. |
| `gatewayApi.tls` | `true` | The Gateway terminates TLS for these hosts: derived URLs use https. `false`: http. |
| `gatewayApi.annotations` | `{}` | Annotations on every route. |
| `gatewayApi.app.host` | `""` | Hostname of the UI. Empty: `ingress.app.host`. |
| `gatewayApi.api.host` | `""` | Hostname of the API. It must differ from the UI host. Empty: `ingress.api.host`. |
| `gatewayApi.otlp.enabled` | `true` | Route OTLP/HTTP to fi-collector [FI_COLLECTOR_PUBLIC_URL]. |
| `gatewayApi.otlp.host` | `""` | Hostname for OTLP/HTTP. Empty: the API host. |
| `gatewayApi.otlpGrpc.enabled` | `false` | A GRPCRoute for OTLP/gRPC (the TraceService) to fi-collector's gRPC port. The listener must accept HTTP/2 (HTTPS). |
| `gatewayApi.otlpGrpc.host` | `""` | Hostname for OTLP/gRPC; it must differ from the HTTP route hosts. |
| `gatewayApi.llmGateway.enabled` | `false` | An HTTPRoute to the LLM gateway (agentcc-gateway), for applications outside the cluster; every call needs a virtual key. It also opens the gateway in the NetworkPolicies. |
| `gatewayApi.llmGateway.host` | `""` | Hostname of the LLM gateway. It must differ from the app and API hosts: each route sends `/` to its own service. |
| `gatewayApi.llmGateway.timeout` | `"310s"` | Route timeout (Gateway API duration) of LLM calls; keep it above the gateway's `config.server.write_timeout`. Empty: the Gateway's default. |
| `gatewayApi.timeouts.request` | `"300s"` | Route timeout of API requests (Gateway API duration, e.g. `300s`). Empty: the Gateway's default, often 15 s, which cuts long requests. |
| `gatewayApi.timeouts.websocket` | `"24h"` | Route timeout of the WebSocket paths (`/ws/`). `0s` disables it where the Gateway supports that; some also need a backend or client traffic policy for idle connections (README). |
| `gatewayApi.gke.healthChecks.enabled` | `false` | GKE Gateway only: a HealthCheckPolicy (networking.gke.io/v1) for each Service the routes use. GKE's load balancer ignores readiness probes and sends `GET /` with the pod's IP as the Host, which the API (a redirect) and the collector's OTLP port (no `/`) fail; these probe the API's `/health/` with Host `localhost`, the collector's `/healthz` on its admin port 9464, the LLM gateway's `/readyz` and the UI's `/`. Needs the GKE Gateway controller (its CRDs). |
| `networkPolicy.enabled` | `false` | NetworkPolicies: the datastores, the gateway, serving and the code sandbox accept traffic only from this release's pods; the UI, API and collector also from `ingressFrom`. A component whose Service is a LoadBalancer or NodePort accepts any source on its Service ports. Egress is open, except for the code sandbox. |
| `networkPolicy.ingressFrom` | `[]` | Peers (NetworkPolicy `from` entries) that may reach the UI, API and collector, e.g. your ingress controller's namespace: `[{namespaceSelector: {matchLabels: {kubernetes.io/metadata.name: ingress-nginx}}}]`. Empty: any source. |
| `serviceAccount.create` | `true` | Create a ServiceAccount for the application pods. |
| `serviceAccount.name` | `""` | Name. Empty: <fullname> when created, else the namespace default. |
| `serviceAccount.annotations` | `{}` | Annotations of the application pods' ServiceAccount. Object storage does not use them: it always authenticates with `objectStorage` keys. |
| `serviceAccount.automountServiceAccountToken` | `false` | Mount the ServiceAccount's token in the pods that run as it: the backend, workers, UI, collector, gateway and serving (never the code sandbox). The application does not call the Kubernetes API; turn it on for a sidecar that needs the token (e.g. a Vault agent). |
| `nodeSelector` | `{}` | Default node selector. |
| `tolerations` | `[]` | Default tolerations. |
| `affinity` | `{}` | Default affinity. |
| `topologySpreadConstraints` | `[]` | Default topology spread constraints of the application Deployments. Entries without a `labelSelector` get each component's. |
| `topologySpread.preset` | `"soft"` | Spread of the backend, UI, collector, gateway and worker pods that can run more than one replica and set no `topologySpreadConstraints`: `soft` prefers other zones and nodes, `hard` never puts two replicas on one node while another is free (needs at least two nodes), `none` leaves placement to the scheduler. Bundled datastores are never spread. |
| `priorityClassName` | `""` | PriorityClass of every pod. |
| `commonLabels` | `{}` | Extra labels on every resource. |
<!-- values-table:end -->
