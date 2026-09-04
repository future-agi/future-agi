# Future AGI Helm chart

This chart deploys Future AGI and its core services on Kubernetes. It mirrors the repository's Docker Compose topology while exposing Kubernetes-native configuration for persistence, probes, resources, ingress, secrets, and external datastores. Bundled MinIO is provided only for development and Minikube; production mode requires AWS S3 or GCS object storage.

## Prerequisites

- Kubernetes 1.24+
- Helm 3.11+
- A default `ReadWriteOnce` StorageClass when persistence is enabled
- About 10 CPU cores and 10–16 GiB RAM for the default stack; use the Minikube profile below for a smaller smoke test
- At least 15 GiB of free container-runtime disk for the published backend image and the bundled services

## Install

```bash
helm install futureagi ./deploy/helm/futureagi \
  --namespace futureagi --create-namespace
kubectl get pods --namespace futureagi --watch
```

The default `deploymentMode: development` installs MinIO and generates random credentials on first install, preserving them during upgrades. For production, set `deploymentMode: production`, disable MinIO, configure AWS S3 or GCS, and provide `secrets.existingSecret`. The chart rejects production values that retain bundled MinIO or chart-generated credentials.

Access the services locally:

```bash
kubectl port-forward --namespace futureagi svc/futureagi-futureagi-frontend 3000:80
kubectl port-forward --namespace futureagi svc/futureagi-futureagi-backend 8000:80
kubectl port-forward --namespace futureagi svc/futureagi-futureagi-minio 9005:9000
```

Open <http://localhost:3000>; the API health endpoint is <http://localhost:8000/health/>.

## Minikube smoke test

```bash
minikube start --cpus=4 --memory=8192 --disk-size=30g
helm upgrade --install futureagi ./deploy/helm/futureagi \
  --namespace futureagi --create-namespace \
  --values deploy/helm/futureagi/examples/minikube-values.yaml \
  --wait --timeout 20m
kubectl get pods --namespace futureagi
helm test futureagi --namespace futureagi --logs
```

The local values disable serving, code execution, persistence, and backend gRPC, and reduce requests. They are not production sizing.

On Docker Desktop, Minikube shares Docker's global disk image; `--disk-size` does not create free host capacity. Check `docker system df` first. On Apple Silicon, the backend release image is currently `linux/amd64` and requires emulation, while the other platform images are multi-architecture.

## Secrets

With `secrets.existingSecret` set, the referenced Secret must contain:

| Key | Consumer |
|---|---|
| `django-secret-key` | Backend and workers |
| `integration-encryption-key` | Encrypted integration credentials |
| `agentcc-internal-api-key` | Backend and AgentCC gateway |
| `agentcc-admin-token` | Backend and AgentCC gateway |
| `postgresql-password` | Application, collector, PostgreSQL, and Temporal |
| `clickhouse-password` | Application and collector (empty for bundled ClickHouse) |
| `rabbitmq-password` | Application and RabbitMQ |
| `object-storage-access-key` | Application access to AWS S3 or GCS HMAC |
| `object-storage-secret-key` | Application access to AWS S3 or GCS HMAC |
| `minio-root-user` | Bundled development MinIO only |
| `minio-root-password` | Bundled development MinIO only |

Example:

```bash
kubectl create secret generic futureagi-production-secrets \
  --namespace futureagi \
  --from-literal=django-secret-key="$(openssl rand -hex 32)" \
  --from-literal=integration-encryption-key="$(openssl rand -base64 32)" \
  --from-literal=agentcc-internal-api-key="$(openssl rand -hex 32)" \
  --from-literal=agentcc-admin-token="$(openssl rand -hex 32)" \
  --from-literal=postgresql-password="$(openssl rand -hex 16)" \
  --from-literal=clickhouse-password= \
  --from-literal=rabbitmq-password="$(openssl rand -hex 16)" \
  --from-literal=object-storage-access-key="$OBJECT_STORAGE_ACCESS_KEY" \
  --from-literal=object-storage-secret-key="$OBJECT_STORAGE_SECRET_KEY"
```

For a development install with bundled MinIO, also provide `minio-root-user` and `minio-root-password`; generated chart Secrets populate all four storage keys automatically. Secret values passed directly with `--set` are retained in Helm release history, so production mode requires an existing Secret. Prefer an external secret controller when one is available.

## External datastores

Disable a bundled service and provide its external endpoint. Credentials continue to come from the Secret described above.

```yaml
postgresql:
  enabled: false
  database: futureagi
  username: futureagi
  external:
    host: postgres.example.internal
    port: 5432

clickhouse:
  enabled: false
  external:
    host: clickhouse.example.internal
    tcpPort: 9000
    httpPort: 8123
    username: default

redis:
  enabled: false
  external: {host: redis.example.internal, port: 6379}

rabbitmq:
  enabled: false
  external: {host: rabbitmq.example.internal, port: 5672, username: futureagi}

temporal:
  enabled: false
  external: {host: temporal.example.internal, port: 7233}
```

Object storage is configured separately below. MinIO has no external production mode in this chart.

## Production object storage

Bundled MinIO is a development convenience, not a production datastore. Start with [examples/production-s3-values.yaml](examples/production-s3-values.yaml):

```yaml
deploymentMode: production
secrets:
  existingSecret: futureagi-production-secrets
config:
  storageBackend: s3
  s3Bucket: futureagi-production
  s3Endpoint: https://s3.amazonaws.com
  s3Region: us-east-2
  s3Secure: true
minio:
  enabled: false
```

The bucket must exist and the configured access key must have the required object and bucket permissions. For GCS S3 interoperability, use `storageBackend: gcs`, keep `minio.enabled: false`, and store the GCS HMAC access ID and secret in the `object-storage-access-key` and `object-storage-secret-key` Secret keys. When `s3Endpoint` is empty, the chart defaults to `https://s3.amazonaws.com` for S3 and `https://storage.googleapis.com` for GCS.

## Ingress

Ingress is disabled by default. The frontend calls the URL in `config.backendUrl` from the user's browser, so it must be publicly reachable. A split-domain example:

```yaml
config:
  frontendUrl: https://app.example.com
  backendUrl: https://api.example.com
ingress:
  enabled: true
  className: nginx
  hosts:
    - host: app.example.com
      paths: [{path: /, pathType: Prefix, service: frontend}]
    - host: api.example.com
      paths: [{path: /, pathType: Prefix, service: backend}]
  tls:
    - secretName: futureagi-tls
      hosts: [app.example.com, api.example.com]
```

TLS certificates and DNS records are intentionally managed outside this chart.

## Production guidance

- Pin all application images to immutable release tags or digests before upgrading.
- Use an existing Secret or an external secret controller; do not keep production credentials in a values file.
- Set `deploymentMode: production`, disable bundled MinIO, and use AWS S3 or GCS object storage.
- Use managed or highly available PostgreSQL, ClickHouse, Redis, RabbitMQ, object storage, and Temporal for critical installations.
- Configure backups and test restores before sending production data.
- Set realistic requests/limits from observed workload usage.
- The code executor needs a privileged container for `nsjail`; it is disabled by default and will not run on clusters that prohibit privileged workloads.
- The bundled StatefulSets are single-instance and do not claim high availability.

## Validate and upgrade

```bash
helm lint deploy/helm/futureagi
./deploy/helm/test-chart.sh
helm template futureagi deploy/helm/futureagi --namespace futureagi >/tmp/futureagi.yaml
helm upgrade futureagi ./deploy/helm/futureagi \
  --namespace futureagi --values values.production.yaml \
  --wait --timeout 20m
helm test futureagi --namespace futureagi --logs
```

See [TESTING.md](TESTING.md) for the initial Minikube validation record and the
environment limitations encountered during that run.

Review image and schema migration notes for each Future AGI release. Stateful data is retained when a release is uninstalled if it lives in StatefulSet PVCs; confirm your cluster's reclaim policy and backup process.

## Uninstall

```bash
helm uninstall futureagi --namespace futureagi
kubectl delete namespace futureagi
```

Deleting the namespace also deletes PVCs in that namespace and is destructive. Back up data first.
