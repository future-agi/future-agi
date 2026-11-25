#!/usr/bin/env bash
# Every image the chart pulls for a set of values: the Future AGI images, the
# bundled datastores, init containers and the helm test pod. One reference per
# line, sorted, `registry/repository:tag@sha256:...` when the digest is known.
# helm-release.yml builds the Release's images lock (futureagi-images-X.Y.Z.txt)
# and Hauler manifest with it; mirror these references into a private registry
# for an air-gapped install. List them before global.imageRegistry points at
# the mirror (or pass --set global.imageRegistry=), so they name the upstream
# images.
#
#   deploy/helm/futureagi/hack/list-images.sh [options] [CHART] [-- HELM_ARGS...]
#
# CHART is a chart directory or a packaged .tgz (default: this chart).
# HELM_ARGS go to `helm template` (e.g. `-f my-values.yaml --set ...`). Without
# them, the chart's examples/bundled.yaml is used with every optional
# component that has an image switched on, i.e. every image the chart can
# pull.
#
# Options:
#   --resolve         look up the digest of every reference without one
#                     (docker buildx imagetools, crane or oras; needs registry
#                     access) so that every line is pinned
#   --require-digest  fail if any reference is left without a digest
#   --format lines    the default: one reference per line
#   --format hauler   a Hauler manifest (content.hauler.cattle.io/v1) of the
#                     images, plus the chart itself from
#                     oci://ghcr.io/future-agi/charts
#
# HELM and PYTHON (Python 3 with PyYAML) name the binaries (default: from PATH).
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=lib.sh
. "$here/lib.sh"
helm=${HELM:-helm}
python=${PYTHON:-python3}
chart_repo=${CHART_REPO:-oci://ghcr.io/future-agi/charts}

resolve=0
require_digest=0
format=lines
chart=""
helm_args=()

usage() {
  sed -n '2,/^set -euo/p' "$0" | sed '$d; s/^# \{0,1\}//' >&2
  exit "${1:-2}"
}
fail() {
  echo "list-images: $*" >&2
  exit 1
}

while [ $# -gt 0 ]; do
  case $1 in
    --resolve) resolve=1 ;;
    --require-digest) require_digest=1 ;;
    --format)
      [ $# -ge 2 ] || usage
      format=$2
      shift
      ;;
    --format=*) format=${1#--format=} ;;
    -h | --help) usage 0 ;;
    --)
      shift
      helm_args=("$@")
      break
      ;;
    -*) fail "unknown option $1 (see --help)" ;;
    *)
      [ -z "$chart" ] || fail "one chart only (got $chart and $1)"
      chart=$1
      ;;
  esac
  shift
done
case $format in lines | hauler) ;; *) fail "--format is lines or hauler" ;; esac
chart=${chart:-$(cd "$here/.." && pwd)}

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# A packaged chart is unpacked, so that its own examples/ are used.
if [ -f "$chart" ]; then
  tar -xzf "$chart" -C "$work"
  chart=$(find "$work" -mindepth 2 -maxdepth 2 -name Chart.yaml -exec dirname {} \; | head -n1)
  [ -n "$chart" ] || fail "no Chart.yaml in the package"
fi
[ -f "$chart/Chart.yaml" ] || fail "$chart is not a chart"

if [ ${#helm_args[@]} -eq 0 ]; then
  helm_args=(-f "$chart/examples/bundled.yaml")
  # Every top-level component with both `enabled` and `image` (serving,
  # codeExecutor, ...), switched on.
  optional=$("$python" - "$chart/values.yaml" <<'PY'
import sys
import yaml

with open(sys.argv[1]) as f:
    values = yaml.safe_load(f)
for key, value in values.items():
    if isinstance(value, dict) and "enabled" in value and isinstance(value.get("image"), dict):
        print(key)
PY
  ) || fail "cannot read $chart/values.yaml (PYTHON needs PyYAML)"
  for component in $optional; do
    helm_args+=(--set "$component.enabled=true")
  done
fi

"$helm" template futureagi "$chart" --namespace futureagi "${helm_args[@]}" >"$work/rendered.yaml" ||
  fail "helm template failed"

# `image:` fields of every container, init container and hook; quotes dropped.
grep -E '^[[:space:]]*(- )?image:[[:space:]]*[^[:space:]]' "$work/rendered.yaml" |
  sed -E 's/^[[:space:]]*(- )?image:[[:space:]]*//; s/^["'\'']//; s/["'\''][[:space:]]*$//; s/[[:space:]]+$//' |
  sort -u >"$work/refs.txt"
[ -s "$work/refs.txt" ] || fail "the rendered manifests have no image"

: >"$work/images.txt"
while IFS= read -r ref; do
  if [[ "$ref" != *@sha256:* ]] && [ "$resolve" = 1 ]; then
    digest=$(resolve_digest "$ref") || fail "cannot resolve the digest of $ref (needs docker buildx, crane or oras, and registry access)"
    ref="$ref@$digest"
  fi
  if [[ "$ref" != *@sha256:* ]] && [ "$require_digest" = 1 ]; then
    fail "$ref has no digest (add --resolve)"
  fi
  printf '%s\n' "$ref" >>"$work/images.txt"
done <"$work/refs.txt"
sort -u -o "$work/images.txt" "$work/images.txt"

case $format in
  lines) cat "$work/images.txt" ;;
  hauler)
    name=$(sed -nE 's/^name: *"?([^" #]+)"?.*/\1/p' "$chart/Chart.yaml")
    version=$(sed -nE 's/^version: *"?([^" #]+)"?.*/\1/p' "$chart/Chart.yaml")
    printf '# Hauler manifest (https://docs.hauler.dev) of the %s chart %s and\n' "$name" "$version"
    printf '# every image it pulls:\n'
    printf '#   hauler store sync --filename %s-hauler-%s.yaml\n' "$name" "$version"
    printf 'apiVersion: content.hauler.cattle.io/v1\nkind: Images\nmetadata:\n  name: %s-images-%s\nspec:\n  images:\n' "$name" "$version"
    sed 's/^/    - name: /' "$work/images.txt"
    printf -- '---\napiVersion: content.hauler.cattle.io/v1\nkind: Charts\nmetadata:\n  name: %s-chart-%s\nspec:\n  charts:\n' "$name" "$version"
    printf '    - name: %s\n      repoURL: %s\n      version: %s\n' "$name" "$chart_repo" "$version"
    ;;
esac
