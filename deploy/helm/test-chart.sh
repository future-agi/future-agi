#!/usr/bin/env sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
chart_dir="$script_dir/futureagi"
minikube_values="$chart_dir/examples/minikube-values.yaml"
production_s3_values="$chart_dir/examples/production-s3-values.yaml"
package_dir=$(mktemp -d)
trap 'rm -rf "$package_dir"' EXIT HUP INT TERM

helm lint "$chart_dir" --strict
helm lint "$chart_dir" --strict --values "$minikube_values"
helm lint "$chart_dir" --strict --values "$production_s3_values"
helm lint "$chart_dir" --strict --values "$production_s3_values" \
  --set config.storageBackend=gcs \
  --set-string config.s3Endpoint=
helm lint "$chart_dir" --strict --set ingress.enabled=true
helm lint "$chart_dir" --strict \
  --set codeExecutor.enabled=true \
  --set secrets.existingSecret=futureagi-production-secrets
helm lint "$chart_dir" --strict \
  --set deploymentMode=production \
  --set secrets.existingSecret=futureagi-production-secrets \
  --set postgresql.enabled=false \
  --set postgresql.external.host=postgres.example.internal \
  --set clickhouse.enabled=false \
  --set clickhouse.external.host=clickhouse.example.internal \
  --set redis.enabled=false \
  --set redis.external.host=redis.example.internal \
  --set rabbitmq.enabled=false \
  --set rabbitmq.external.host=rabbitmq.example.internal \
  --set minio.enabled=false \
  --set config.storageBackend=s3 \
  --set config.s3Endpoint=https://s3.amazonaws.com \
  --set temporal.enabled=false \
  --set temporal.external.host=temporal.example.internal

helm template futureagi "$chart_dir" --namespace futureagi >/dev/null
helm template futureagi "$chart_dir" --namespace futureagi \
  --values "$minikube_values" >/dev/null
helm template futureagi "$chart_dir" --namespace futureagi \
  --values "$production_s3_values" >/dev/null
helm template futureagi "$chart_dir" --namespace futureagi \
  --set ingress.enabled=true >/dev/null

if helm template futureagi "$chart_dir" --namespace futureagi \
  --set deploymentMode=production \
  --set secrets.existingSecret=futureagi-production-secrets >/dev/null 2>&1; then
  echo "production mode unexpectedly accepted bundled MinIO" >&2
  exit 1
fi
helm package "$chart_dir" --destination "$package_dir" >/dev/null

echo "Helm chart lint, render, and package checks passed."
