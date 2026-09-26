#!/usr/bin/env bash
# Static checks of the chart, the same ones .github/workflows/helm-ci.yml runs:
#   * the chart's defaults, and inconsistent values, refuse to render and say
#     what to set
#   * helm lint --strict and helm template for every value set
#   * kubeconform -strict on every rendered manifest, per Kubernetes version
#   * invariants of the rendered manifests (hack/rendered_checks.py)
#   * values.yaml, values.schema.json and the README values table agree
#   * the pre-commit Prettier run skips the templates and those generated files
#   * the ClickHouse config files match the ones the Standalone install uses
#
#   deploy/helm/futureagi/hack/check.sh
#
# HELM, KUBECONFORM and PYTHON (with PyYAML) name the binaries (default: from
# PATH), KUBE_VERSIONS
# the Kubernetes versions to validate against, OUT_DIR where the rendered
# manifests go (default: a temporary directory).
set -euo pipefail

chart=$(cd "$(dirname "$0")/.." && pwd)
repo=$(cd "$chart/../../.." && pwd)
helm=${HELM:-helm}
kubeconform=${KUBECONFORM:-kubeconform}
# Python 3 with PyYAML.
python=${PYTHON:-python3}
kube_versions=${KUBE_VERSIONS:-"1.27.0 1.33.0"}
out=${OUT_DIR:-$(mktemp -d)}
mkdir -p "$out"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

# name|values files (space-separated, relative to the chart)
value_sets=(
  "bundled|examples/bundled.yaml"
  "external|examples/external.yaml"
  "ingress|examples/external.yaml examples/ingress.yaml"
  "bundled-ingress|examples/bundled.yaml examples/ingress.yaml ci/bundled-ingress.yaml"
  "all-components|examples/external.yaml examples/ingress.yaml ci/all-components.yaml"
  "overrides|examples/bundled.yaml ci/overrides.yaml"
)

echo "== chart defaults: refuse to render, with guidance"
if "$helm" template futureagi "$chart" >"$out/default.txt" 2>&1; then
  fail "the chart rendered without any datastore configured"
fi
# The hint is the first install command most people copy: it must wait long
# enough for the first bootstrap.
for expected in "postgres.external.host is required" "examples/bundled.yaml" "--timeout 20m"; do
  grep -qF -- "$expected" "$out/default.txt" || {
    cat "$out/default.txt" >&2
    fail "the default render error does not mention: $expected"
  }
done
echo "ok   defaults fail with guidance"

echo "== inconsistent values: refuse to render"
# name|expected message|helm arguments (over examples/bundled.yaml)
refused=(
  "recaptcha without its key|config.recaptcha needs secrets.extra.RECAPTCHA_SECRET_KEY|--set config.recaptcha=true"
)
for case in "${refused[@]}"; do
  IFS='|' read -r name expected args <<<"$case"
  # shellcheck disable=SC2086 # args is a word list
  if "$helm" template futureagi "$chart" -f "$chart/examples/bundled.yaml" $args >"$out/refused.txt" 2>&1; then
    fail "rendered despite: $name"
  fi
  grep -qF -- "$expected" "$out/refused.txt" || {
    cat "$out/refused.txt" >&2
    fail "the error for \"$name\" does not mention: $expected"
  }
  echo "ok   $name"
done
rm -f "$out/refused.txt"

for set in "${value_sets[@]}"; do
  name=${set%%|*}
  args=()
  for file in ${set#*|}; do
    args+=(-f "$chart/$file")
  done
  echo "== $name"
  "$helm" lint "$chart" --strict "${args[@]}" >"$out/$name.lint.txt" 2>&1 || {
    cat "$out/$name.lint.txt" >&2
    fail "helm lint ($name)"
  }
  "$helm" template futureagi "$chart" --namespace futureagi "${args[@]}" >"$out/$name.yaml"
  for version in $kube_versions; do
    "$kubeconform" -strict -summary -kubernetes-version "$version" "$out/$name.yaml" ||
      fail "kubeconform ($name, Kubernetes $version)"
  done
done

echo "== rendered invariants"
# No manifest may carry an unexpanded template or an empty image.
if grep -nE '<no value>|image:( *| *"")$' "$out"/*.yaml; then
  fail "a rendered manifest has an unset value"
fi
"$python" "$chart/hack/rendered_checks.py" "$out"

echo "== values, schema and README"
"$python" "$chart/hack/values_docs.py" --check

echo "== the pre-commit formatter leaves the templates and generated files alone"
# scripts/lint-staged-root-format.mjs runs Prettier on staged YAML, JSON and
# Markdown: it cannot parse Go templates, and it would reformat what
# values_docs.py writes, which the check above compares byte for byte.
if [ -f "$repo/scripts/lint-staged-root-format.mjs" ]; then
  for path in "deploy/helm/*/templates/" deploy/helm/futureagi/values.schema.json deploy/helm/futureagi/README.md; do
    grep -qxF -- "$path" "$repo/.prettierignore" 2>/dev/null || fail ".prettierignore does not list $path"
  done
  echo "ok   ignored"
else
  echo "skip (not in the repository)"
fi

echo "== ClickHouse config files match the Standalone install"
if [ -d "$repo/deploy/platform/clickhouse" ]; then
  diff -u "$repo/deploy/platform/clickhouse/config.d/zz-small-host.xml" "$chart/files/clickhouse/config.d/zz-small-host.xml"
  diff -u "$repo/deploy/platform/clickhouse/users.d/zz-small-host.xml" "$chart/files/clickhouse/users.d/zz-small-host.xml"
  diff -u "$repo/futureagi/.ci/clickhouse-storage-policy.xml" "$chart/files/clickhouse/config.d/storage-policy.xml"
  echo "ok   identical"
else
  echo "skip (not in the repository)"
fi

echo "all checks passed (manifests in $out)"
