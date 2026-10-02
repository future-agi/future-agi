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
#
# A packaged chart with the images it pins, as helm-release.yml runs it
# before publishing:
#
#   CHART=futureagi-1.42.0.tgz PUBLISHED=1 deploy/helm/futureagi/hack/kind-smoke.sh
#
# CHART is a chart directory or .tgz (default: this checkout's chart).
# PUBLISHED=1 pulls the images the chart names (its appVersion tag and stamped
# digests) instead of loading $TAG into kind; with DOCKERHUB_USERNAME and
# DOCKERHUB_TOKEN set, pulls are authenticated through an image pull Secret.
# The namespace enforces Pod Security POD_SECURITY (default restricted; set
# baseline or none to relax it), with the privileged code sandbox off.
set -euo pipefail

here=$(cd "$(dirname "$0")/.." && pwd)
chart=${CHART:-$here}
published=${PUBLISHED:-0}
if [ "$published" = 1 ]; then
  tag=""
else
  tag=${TAG:?set TAG to the tag of the locally built Future AGI images, or PUBLISHED=1}
fi
cluster=${KIND_CLUSTER:-futureagi}
ns=${NAMESPACE:-futureagi}
release=futureagi
timeout=${HELM_TIMEOUT:-25m}
pod_security=${POD_SECURITY:-restricted}
images=(futureagi/future-agi futureagi/frontend futureagi/fi-collector futureagi/agentcc-gateway)
# The first admin, created by the bootstrap job from bootstrap.admin.
admin_email=smoke-admin@example.com
admin_password='Smoke-Test-2026!x'

work=$(mktemp -d)

# The values files ship inside the chart: take them from the directory, or
# from the unpacked package when CHART is a .tgz (helm installs the .tgz).
case "$chart" in
  *.tgz)
    tar -xzf "$chart" -C "$work"
    values_dir="$work/futureagi"
    ;;
  *) values_dir="$chart" ;;
esac
image_args=()
[ -n "$tag" ] && image_args+=(--set image.tag="$tag")

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

if [ "$published" = 1 ]; then
  say "published images: the cluster pulls what the chart names"
else
  say "load the Future AGI images ($tag)"
  for image in "${images[@]}"; do
    docker image inspect "$image:$tag" >/dev/null || fail "$image:$tag is not in the local Docker daemon"
    kind load docker-image "$image:$tag" --name "$cluster"
  done
fi

say "namespace $ns (Pod Security: $pod_security)"
kubectl get namespace "$ns" >/dev/null 2>&1 || kubectl create namespace "$ns"
if [ "$pod_security" != none ]; then
  kubectl label namespace "$ns" --overwrite \
    "pod-security.kubernetes.io/enforce=$pod_security" \
    "pod-security.kubernetes.io/enforce-version=latest"
fi
if [ "$published" = 1 ] && [ -n "${DOCKERHUB_USERNAME:-}" ] && [ -n "${DOCKERHUB_TOKEN:-}" ]; then
  kubectl -n "$ns" create secret docker-registry dockerhub \
    --docker-server=https://index.docker.io/v1/ \
    --docker-username="$DOCKERHUB_USERNAME" --docker-password="$DOCKERHUB_TOKEN" \
    --dry-run=client -o yaml | kubectl apply -f -
  image_args+=(--set "global.imagePullSecrets[0].name=dockerhub")
fi

kubectl -n "$ns" create secret generic smoke-admin \
  --from-literal=email="$admin_email" --from-literal=name="Smoke Admin" \
  --from-literal=password="$admin_password" \
  --dry-run=client -o yaml | kubectl apply -f -

say "install (bundled datastores)"
helm upgrade --install "$release" "$chart" --namespace "$ns" \
  -f "$values_dir/examples/bundled.yaml" \
  ${image_args[@]+"${image_args[@]}"} \
  --set config.telemetry=false \
  --set bootstrap.admin.existingSecret=smoke-admin \
  --wait --timeout "$timeout"
kubectl -n "$ns" get pods -o wide
kubectl -n "$ns" logs "job/$release-bootstrap" --tail=40

say "every Deployment and StatefulSet is rolled out"
for workload in $(kubectl -n "$ns" get deployments,statefulsets -o name); do
  kubectl -n "$ns" rollout status "$workload" --timeout=5m
done

say "API health, and the first admin (bootstrap.admin) signs in, through a port-forward"
kubectl -n "$ns" port-forward "svc/$release-backend" 18000:8000 >/dev/null 2>&1 &
forward=$!
for _ in $(seq 1 30); do
  curl -fsS http://127.0.0.1:18000/health/ >/dev/null 2>&1 && break
  sleep 2
done
curl -fsS http://127.0.0.1:18000/health/
echo
signed_in=0
curl -sS --fail-with-body -H 'Content-Type: application/json' \
  -d "{\"email\": \"$admin_email\", \"password\": \"$admin_password\"}" \
  http://127.0.0.1:18000/accounts/token/ >"$work/token.json" && signed_in=1
kill "$forward" 2>/dev/null || true
if [ "$signed_in" != 1 ] || ! grep -q '"access"' "$work/token.json"; then
  cat "$work/token.json" >&2
  fail "the first admin cannot sign in with the password from bootstrap.admin.existingSecret"
fi
echo "ok   $admin_email signed in"

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

say "a second account with manage.py create_user (install notes, step 3)"
kubectl -n "$ns" exec "deploy/$release-backend" -c backend -- python manage.py create_user \
  --email "smoke-$(date +%s)@example.com" --name "Smoke Test" --password "$admin_password"

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
kubectl -n "$ns" logs "job/$release-bootstrap" | grep -qF "first admin $admin_email already exists: left unchanged" ||
  fail "the pre-upgrade bootstrap job did not leave the first admin as it was"
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
