# Helm deployment feature requirements

## Problem

Future AGI supports self-hosting through Docker Compose, but has no native Kubernetes deployment path. Operators must translate a multi-service topology, secret wiring, probes, and persistent storage by hand. The chart should provide a repeatable starting point for local evaluation and production customization without changing application images.

## Goals

- Install the complete core platform with one Helm release on Kubernetes 1.24+.
- Keep the default deployment self-contained for development, evaluation, and Minikube.
- Allow PostgreSQL, ClickHouse, Redis, RabbitMQ, MinIO/S3, and Temporal to be replaced independently by externally managed services.
- Reject bundled MinIO in production mode; production installations must use AWS S3 or GCS object storage with externally managed credentials.
- Configure every application image, tag, pull policy, replica count, resources, placement, persistence, and ingress through values.
- Keep credentials in a Kubernetes Secret, support a pre-created Secret, and preserve chart-generated credentials across upgrades.
- Provide startup, readiness, and liveness probes for network-facing workloads.
- Provide a Helm test and a documented Minikube validation workflow.

## Non-goals for the initial chart

- Deploying the legacy optional PeerDB CDC profile. The default fi-collector path writes directly to ClickHouse.
- Installing a Kubernetes operator or cloud-specific infrastructure.
- Automatically configuring TLS, DNS, backups, or highly available datastores.
- Enabling the privileged code-executor by default.
- Deploying the hosted voice simulation runner, which requires external credentials and telephony infrastructure.

## Acceptance criteria

1. `helm lint deploy/helm/futureagi` succeeds.
2. Default, external-datastore, ingress, and Minikube value sets render valid Kubernetes resources.
3. `helm install` creates the core application and bundled datastore workloads without embedded static passwords.
4. A second `helm upgrade` preserves generated Secret values.
5. Backend and frontend become reachable through port-forwarding on Minikube.
6. `helm test` verifies the frontend and unauthenticated backend health endpoint.
7. Documentation covers install, configuration, external services, security constraints, upgrades, backups, and uninstall.
8. `deploymentMode=production` fails rendering when bundled MinIO is enabled or a pre-created Secret is not configured.

## Design notes

The Langfuse chart informed three choices: a self-contained development default, independent switches for external datastores, and first-class Minikube instructions. This chart deliberately avoids external subcharts in its first version so rendering and contribution tests do not require a chart-repository network fetch. Bundled datastores are single-instance and intended for evaluation or modest installations. Bundled MinIO is development-only; production mode requires AWS S3 or GCS. Production users should also use managed/HA services or add backup and disruption procedures appropriate to their cluster.
