#!/usr/bin/env bash
# Cloud-only. Prepare clients BEFORE bin/e2e up; never source a developer .env.
# E2E_STACK=standalone bin/e2e build standalone
# All modes require the three expected component SHAs and the independent pair-lock digest.
# `interpreters` prints the two interpreter exports for Playwright's Python flows.
set -euo pipefail
[[ $(uname -s) == Linux ]] || { echo 'Requires a Linux E2E lane' >&2; exit 2; }
case "${1:-}:${E2E_STACK:-distributed}" in
  interpreters:distributed|interpreters:standalone|packages:standalone|typescript:standalone) ;;
  *) echo 'Expected interpreters (distributed or standalone), packages or typescript (standalone only)' >&2; exit 2 ;;
esac
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
    (umask 077; mkdir -p "$envdir")
    envdir=$(cd "$envdir" && pwd)
    for mode in traced plain; do uv venv --python 3.11 "$envdir/$mode"; done
    wheel="$dist/${names[0]}"
    uv pip install --python "$envdir/traced/bin/python" "${wheel}[otel]" "$dist/${names[1]}" httpx
    uv pip install --python "$envdir/plain/bin/python" "$wheel" httpx
    "$envdir/plain/bin/python" -I -c 'import importlib.util; assert importlib.util.find_spec("opentelemetry") is None'
    "$envdir/traced/bin/python" -I -c 'from fi.telemetry import agent_scope; from fi_instrumentation.otel import HTTPSpanExporter, register'
    if [[ -n ${GITHUB_ENV:-} ]]; then
      printf 'SKILL_TRACES_PYTHON=%s\nSKILL_TRACES_NO_OTEL_PYTHON=%s\n' "$envdir/traced/bin/python" "$envdir/plain/bin/python" >> "$GITHUB_ENV"
    fi
    printf 'export SKILL_TRACES_PYTHON=%q SKILL_TRACES_NO_OTEL_PYTHON=%q\n' "$envdir/traced/bin/python" "$envdir/plain/bin/python"
    ;;
  typescript)
    "${verify[@]}" --stage "$root/$stack" --typescript-only >/dev/null
    bin/e2e compose build skills-ts-runtime
    ;;
  packages)
    "${verify[@]}" --stage "$root/$stack" >/dev/null
    bin/e2e compose build native-runtime skills-ts-runtime
    ;;
  *) echo 'Usage: prepare-skill-traces.sh packages <artifacts> | interpreters <artifacts> <new-dir>' >&2; exit 2 ;;
esac
