#!/usr/bin/env bash
# Install the chart on a kind cluster with every datastore bundled, check that
# it works, then check that a volume resize is refused, upgrade it in place
# and roll back. .github/workflows/helm-ci.yml runs this
# after building the images; by hand, with images built from this checkout
# (./bin/install --from-source tags them `local`):
#
#   TAG=local deploy/helm/futureagi/hack/kind-smoke.sh
#
# Needs docker, kind, kubectl, helm and curl, and futureagi/future-agi,
# futureagi/frontend, futureagi/fi-collector and futureagi/agentcc-gateway at
# $TAG in the local Docker daemon. Datastore images are pulled by the cluster.
# KIND_CLUSTER (default futureagi) is created when missing and kept afterwards;
# NAMESPACE defaults to futureagi.
set -euo pipefail

chart=$(cd "$(dirname "$0")/.." && pwd)
tag=${TAG:?set TAG to the tag of the locally built Future AGI images}
cluster=${KIND_CLUSTER:-futureagi}
ns=${NAMESPACE:-futureagi}
release=futureagi
timeout=${HELM_TIMEOUT:-25m}
images=(futureagi/future-agi futureagi/frontend futureagi/fi-collector futureagi/agentcc-gateway)

work=$(mktemp -d)

say() { printf '\n== %s\n' "$*"; }
fail() {
  echo "FAIL: $*" >&2
  exit 1
}

diagnostics() {
  say "diagnostics"
  kubectl -n "$ns" get all,pvc,secrets,serviceaccounts -o wide || true
  kubectl -n "$ns" get events --sort-by=.lastTimestamp | tail -60 || true
  for pod in $(kubectl -n "$ns" get pods -o name 2>/dev/null); do
    echo "---- $pod"
    kubectl -n "$ns" describe "$pod" | sed -n '/^Containers:/,/^Events:/p' | grep -E 'State|Reason|Exit Code|Ready|Restart' || true
    kubectl -n "$ns" logs "$pod" --all-containers --tail=150 || true
    kubectl -n "$ns" logs "$pod" --all-containers --previous --tail=50 2>/dev/null || true
  done
}
trap 'rc=$?; if [ "$rc" -ne 0 ]; then diagnostics; fi; exit "$rc"' EXIT

say "kind cluster $cluster"
if ! kind get clusters 2>/dev/null | grep -qx "$cluster"; then
  kind create cluster --name "$cluster" --wait 180s
fi
kubectl config use-context "kind-$cluster"

say "load the Future AGI images ($tag)"
for image in "${images[@]}"; do
  docker image inspect "$image:$tag" >/dev/null || fail "$image:$tag is not in the local Docker daemon"
  kind load docker-image "$image:$tag" --name "$cluster"
done

say "install (bundled datastores)"
helm upgrade --install "$release" "$chart" --namespace "$ns" --create-namespace \
  -f "$chart/examples/bundled.yaml" \
  --set image.tag="$tag" \
  --set config.telemetry=false \
  --wait --timeout "$timeout"
kubectl -n "$ns" get pods -o wide
kubectl -n "$ns" logs "job/$release-bootstrap" --tail=40

say "every Deployment and StatefulSet is rolled out"
for workload in $(kubectl -n "$ns" get deployments,statefulsets -o name); do
  kubectl -n "$ns" rollout status "$workload" --timeout=5m
done

say "API health through a port-forward"
kubectl -n "$ns" port-forward "svc/$release-backend" 18000:8000 >/dev/null 2>&1 &
forward=$!
for _ in $(seq 1 30); do
  curl -fsS http://127.0.0.1:18000/health/ >/dev/null 2>&1 && break
  sleep 2
done
curl -fsS http://127.0.0.1:18000/health/
echo
kill "$forward" 2>/dev/null || true

say "helm test"
helm test "$release" --namespace "$ns" --logs --timeout 5m

say "the bootstrap job prepared every datastore"
tables=$(kubectl -n "$ns" exec "statefulset/$release-clickhouse" -- sh -c \
  'clickhouse-client --password "$CLICKHOUSE_DEFAULT_PASSWORD" -q "EXISTS TABLE default.spans"')
[ "$tables" = "1" ] || fail "ClickHouse has no spans table"
triggers=$(kubectl -n "$ns" exec "statefulset/$release-postgres" -- sh -c \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT count(*) FROM pg_trigger WHERE tgname LIKE '"'"'fi_cdc_%'"'"'"')
[ "${triggers:-0}" -gt 0 ] || fail "no outbox CDC capture triggers in Postgres"
kubectl -n "$ns" exec "statefulset/$release-temporal" -- \
  temporal schedule list --address 127.0.0.1:7233 | grep -q outbox-cdc-drain ||
  fail "the outbox CDC drain schedule is not registered"
echo "ok   ClickHouse schema, $triggers CDC triggers, Temporal schedules"

say "first account"
kubectl -n "$ns" exec "deploy/$release-backend" -c backend -- python manage.py create_user \
  --email "smoke-$(date +%s)@example.com" --name "Smoke Test" --password 'Smoke-Test-2026!x'

say "a bundled volume cannot be resized by an upgrade: refused before anything changes"
installed=$(helm history "$release" --namespace "$ns" --max 1 | awk 'NR == 2 {print $1}')
if helm upgrade "$release" "$chart" --namespace "$ns" --reuse-values \
  --set postgres.bundled.persistence.size=21Gi >"$work/refused.txt" 2>&1; then
  fail "helm upgrade accepted a new postgres.bundled.persistence.size"
fi
grep -q "postgres.bundled.persistence.size is 21Gi" "$work/refused.txt" || {
  cat "$work/refused.txt" >&2
  fail "the refused upgrade does not name postgres.bundled.persistence.size"
}
now=$(helm history "$release" --namespace "$ns" --max 1 | awk 'NR == 2 {print $1}')
[ "$now" = "$installed" ] || fail "the refused upgrade recorded revision $now"

say "upgrade in place: the bootstrap job runs again, generated keys stay"
before=$(kubectl -n "$ns" get secret "$release-secrets" -o jsonpath='{.data.SECRET_KEY}')
helm upgrade "$release" "$chart" --namespace "$ns" --reuse-values \
  --set-string backend.podAnnotations.smoke/upgraded="$(date +%s)" \
  --set-string secrets.extra.SMOKE_REVISION=upgraded \
  --wait --timeout "$timeout"
after=$(kubectl -n "$ns" get secret "$release-secrets" -o jsonpath='{.data.SECRET_KEY}')
[ -n "$before" ] && [ "$before" = "$after" ] || fail "SECRET_KEY changed on upgrade"
kubectl -n "$ns" get "job/$release-bootstrap" -o jsonpath='{.status.succeeded}' | grep -qx 1 ||
  fail "the pre-upgrade bootstrap job did not succeed"
kubectl -n "$ns" rollout status "deploy/$release-backend" --timeout=5m
helm test "$release" --namespace "$ns" --timeout 5m

say "rollback puts the previous revision's Secret back"
helm rollback "$release" "$installed" --namespace "$ns" --wait --timeout "$timeout"
[ -z "$(kubectl -n "$ns" get secret "$release-secrets" -o jsonpath='{.data.SMOKE_REVISION}')" ] ||
  fail "helm rollback kept the upgraded Secret"
after=$(kubectl -n "$ns" get secret "$release-secrets" -o jsonpath='{.data.SECRET_KEY}')
[ "$before" = "$after" ] || fail "SECRET_KEY changed on rollback"
kubectl -n "$ns" rollout status "deploy/$release-backend" --timeout=5m

say "passed"
