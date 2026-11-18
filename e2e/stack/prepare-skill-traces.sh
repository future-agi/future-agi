#!/usr/bin/env bash
# Cloud-only. Prepare the standalone components BEFORE bin/e2e up; never source a developer .env.
# E2E_STACK=standalone bin/e2e build standalone
# Set CTXHUB_APP_COMMIT to the tested product revision for `packages`.
# `interpreters` prints the two interpreter exports for Playwright's Python flows.
set -euo pipefail
[[ $(uname -s) == Linux && ${E2E_STACK:-} == standalone ]] || { echo 'Requires a Linux standalone E2E lane' >&2; exit 2; }
root=$(cd "$(dirname "$0")/../.." && pwd)
cd "$root"
stack=e2e/stack
case ${1:-} in
  interpreters)
    dist=$(cd "${2:?release artifact directory}" && pwd)
    envdir=${3:?new cloud interpreter directory}
    [[ ! -e "$envdir" ]] || { echo 'Choose a new interpreter directory' >&2; exit 2; }
    (cd "$dist" && sha256sum -c "$root/$stack/native-runtime/wheels.sha256")
    mkdir -m 700 -p "$envdir"
    envdir=$(cd "$envdir" && pwd)
    for mode in traced plain; do uv venv --python 3.11 "$envdir/$mode"; done
    wheel="$dist/futureagi-0.8.0-py3-none-any.whl"
    uv pip install --python "$envdir/traced/bin/python" "${wheel}[otel]" "$dist/fi_instrumentation_otel-1.3.2-py3-none-any.whl" httpx
    uv pip install --python "$envdir/plain/bin/python" "$wheel" httpx
    "$envdir/plain/bin/python" -I -c 'import importlib.util; assert importlib.util.find_spec("opentelemetry") is None'
    printf 'export SKILL_TRACES_PYTHON=%q SKILL_TRACES_NO_OTEL_PYTHON=%q\n' "$envdir/traced/bin/python" "$envdir/plain/bin/python"
    ;;
  packages)
    [[ ${CTXHUB_APP_COMMIT:-} =~ ^[a-f0-9]{40}$ ]] || { echo 'Set CTXHUB_APP_COMMIT to the tested product revision' >&2; exit 2; }
    dist=${2:?directory containing SOURCE_COMMITS and the five pinned packages}
    dist=$(cd "$dist" && pwd)
    for spec in native-runtime/wheels skills-ts-runtime/packages; do
      manifest=$root/$stack/$spec.sha256
      (cd "$dist" && sha256sum -c "$manifest")
      mkdir -p "$stack/$spec"
      while read -r digest filename; do install -m 0644 "$dist/$filename" "$stack/$spec/"; done < "$manifest"
    done
    bin/e2e compose build native-runtime skills-ts-runtime
    ;;
  *) echo 'Usage: prepare-skill-traces.sh packages <artifacts> | interpreters <artifacts> <new-dir>' >&2; exit 2 ;;
esac
