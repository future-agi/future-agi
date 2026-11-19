#!/usr/bin/env bash
# Cloud-only. Prepare the standalone components BEFORE bin/e2e up; never source a developer .env.
# E2E_STACK=standalone bin/e2e build standalone
# Both modes require the three expected component SHAs and the independent pair-lock digest.
# `interpreters` prints the two interpreter exports for Playwright's Python flows.
set -euo pipefail
[[ $(uname -s) == Linux && ${E2E_STACK:-} == standalone ]] || { echo 'Requires a Linux standalone E2E lane' >&2; exit 2; }
root=$(cd "$(dirname "$0")/../.." && pwd)
cd "$root"
stack=e2e/stack
dist=$(cd "${2:?release artifact directory with pair.json}" && pwd)
product=$(git rev-parse HEAD)
[[ ${CTXHUB_APP_COMMIT:?set the expected product SHA} == "$product" ]] || { echo 'Product checkout does not match expected SHA' >&2; exit 2; }
verify=(python3 -I "$root/$stack/verify-skill-packages.py" "$dist" "${SKILL_TRACE_PAIR_SHA256:?supply the independently recorded pair.json SHA-256}" "$product" "${SDK_COMMIT:?set expected SDK SHA}" "${TRACEAI_COMMIT:?set expected traceAI SHA}")
case ${1:-} in
  interpreters)
    envdir=${3:?new cloud interpreter directory}
    [[ ! -e "$envdir" ]] || { echo 'Choose a new interpreter directory' >&2; exit 2; }
    wheels=$("${verify[@]}")
    mapfile -t names <<< "$wheels"
    mkdir -m 700 -p "$envdir"
    envdir=$(cd "$envdir" && pwd)
    for mode in traced plain; do uv venv --python 3.11 "$envdir/$mode"; done
    wheel="$dist/${names[0]}"
    uv pip install --python "$envdir/traced/bin/python" "${wheel}[otel]" "$dist/${names[1]}" httpx
    uv pip install --python "$envdir/plain/bin/python" "$wheel" httpx
    "$envdir/plain/bin/python" -I -c 'import importlib.util; assert importlib.util.find_spec("opentelemetry") is None'
    printf 'export SKILL_TRACES_PYTHON=%q SKILL_TRACES_NO_OTEL_PYTHON=%q\n' "$envdir/traced/bin/python" "$envdir/plain/bin/python"
    ;;
  packages)
    "${verify[@]}" --stage "$root/$stack" >/dev/null
    bin/e2e compose build native-runtime skills-ts-runtime
    ;;
  *) echo 'Usage: prepare-skill-traces.sh packages <artifacts> | interpreters <artifacts> <new-dir>' >&2; exit 2 ;;
esac
