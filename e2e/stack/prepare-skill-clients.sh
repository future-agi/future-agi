#!/usr/bin/env bash
# Consumer on the ephemeral browser runner. Never install from an editable checkout.
set -euo pipefail
dist=${1:?verified pair directory}
cohort=${2:?cohort}
[[ $(git rev-parse HEAD) == "${CTXHUB_APP_COMMIT:?}" ]]
verified=$(python3 -I e2e/stack/verify-skill-packages.py "$dist" "${SKILL_TRACE_PAIR_SHA256:?}" "$CTXHUB_APP_COMMIT" "${SDK_COMMIT:?}" "${TRACEAI_COMMIT:?}" --require-sums)
mapfile -t wheels <<< "$verified"
sdk="$dist/${wheels[0]}"; trace="$dist/${wheels[1]}"
export UV_PYTHON_PREFERENCE=only-system
case "$cohort" in
  host)
    envdir="$RUNNER_TEMP/skills-python"
    e2e/stack/prepare-skill-traces.sh interpreters "$dist" "$envdir"
    echo "CTXHUB_J5_PYTHON=$envdir/plain/bin/python" >> "$GITHUB_ENV"
    ;;
  typescript)
    e2e/stack/prepare-skill-traces.sh typescript "$dist"
    ;;
  install-turn)
    # The unprivileged installer must be able to traverse its interpreter path,
    # not read RUNNER_TEMP or the runner's HOME. The venv contains only packages.
    prefix=$(mktemp -d /tmp/skills-install.XXXXXX)
    uv venv --python 3.11 "$prefix/venv"
    uv pip install --python "$prefix/venv/bin/python" "${sdk}[context-deepagents,otel]" "${trace}[grpc]" deepagents==0.4.11 langchain-openai
    chmod -R a+rX "$prefix/venv"; chmod 755 "$prefix"
    sudo useradd --create-home --shell /bin/bash skills-guest
    sudo -n -H -u skills-guest "$prefix/venv/bin/python" -I -c 'from fi.context import ContextClient'
    printf 'SKILL_INSTALL_PYTHON=%s\nSKILL_INSTALL_USER=skills-guest\n' "$prefix/venv/bin/python" >> "$GITHUB_ENV"
    ;;
  campaign-*)
    envdir="$RUNNER_TEMP/campaign-python"
    uv venv --python 3.11 "$envdir"
    uv pip install --python "$envdir/bin/python" "$sdk" httpx
    for command in inventory plan apply verify; do "$envdir/bin/python" -I -m fi.context "$command" --help >/dev/null; done
    "$envdir/bin/python" -I -c 'import fi.context.journal, fi.context.lock, fi.context.materialize'
    echo "CTXHUB_J5_PYTHON=$envdir/bin/python" >> "$GITHUB_ENV"
    if [[ $cohort == campaign-cold || $cohort == campaign-configured || $cohort == campaign-registered* ]]; then
      npm install --prefix "$RUNNER_TEMP/codex" --ignore-scripts --include=optional --no-audit --no-fund @openai/codex@0.167.1
      binary="$RUNNER_TEMP/codex/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex"
      digest=$(node --input-type=module -e 'import {CODEX_BINARY_SHA256} from "./e2e/lib/skills-r3/j5-native-fixture.mjs"; console.log(CODEX_BINARY_SHA256)')
      printf '%s  %s\n' "$digest" "$binary" | sha256sum -c -
      [[ $("$binary" --version) == 'codex-cli 0.167.1' ]]
      printf 'J5_CODEX_BINARY=%s\nJ5_CODEX_SHA256=%s\n' "$binary" "$digest" >> "$GITHUB_ENV"
    fi
    ;;
  application|registered-recovery) ;;
  *) echo "Unknown package consumer: $cohort" >&2; exit 2 ;;
esac
if [[ $cohort == application || $cohort == registered-recovery ]]; then
  j6="$RUNNER_TEMP/j6-wheels"
  # The guest consumes the same two immutable wheels (not a copied virtualenv).
  cmp "$sdk" "$j6/${wheels[0]}"
  app=("$j6"/ctxhub_support_application-*.whl)
  [[ ${#app[@]} == 1 && -f ${app[0]} ]]
  printf '%s  %s\n' "${J6_APP_SHA256:?}" "${app[0]}" | sha256sum -c -
  all=("$j6"/*); [[ ${#all[@]} == 2 ]]
  envdir="$RUNNER_TEMP/j6-python"
  uv venv --python 3.11 "$envdir"
  uv pip install --python "$envdir/bin/python" "${all[@]}"
  uv pip check --python "$envdir/bin/python"
  "$envdir/bin/python" -I -c 'import ctxhub_support.registry, fi.context.adapters.simulation_entrypoint, ctxhub_support.recovery'
  printf 'J6_APP_PYTHON=%s\nJ6_WHEELS=%s\n' "$envdir/bin/python" "$j6" >> "$GITHUB_ENV"
fi
