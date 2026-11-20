#!/usr/bin/env bash
# Build from the three checkouts selected by the workflow. Do not consume local packages.
set -euo pipefail
root=$(git rev-parse --show-toplevel)
sdk=$(realpath "${1:?SDK checkout}"); traceai=$(realpath "${2:?traceAI checkout}")
dist=${3:?new absolute output directory}
product=$(git -C "$root" rev-parse HEAD)
[[ $dist == /* && ! -e $dist && $product == "${GITHUB_SHA:?}" ]]
sdk_commit=$(jq -er '.sdk.commit' <<< "${SKILL_REFS:?resolved branches and commits}")
trace_commit=$(jq -er '.traceai.commit' <<< "$SKILL_REFS")
[[ $(git -C "$sdk" rev-parse HEAD) == "$sdk_commit" ]]
[[ $(git -C "$traceai" rev-parse HEAD) == "$trace_commit" ]]
printf 'Testing %s with %s @ %s and %s @ %s\n' "$product" \
  "$(jq -er '.sdk.branch' <<< "$SKILL_REFS")" "$sdk_commit" "$(jq -er '.traceai.branch' <<< "$SKILL_REFS")" "$trace_commit"
for repo in "$sdk" "$traceai"; do
  state=$(git -C "$repo" status --porcelain --untracked-files=all --ignored)
  [[ -z $state ]] || { echo 'Expected clean disposable package checkouts' >&2; exit 1; }
done
contract=$(python3 -IB "$root/e2e/stack/check-skill-contract.py" "$root" "$sdk" "$product")
mkdir -p "$dist"
for repo in "$sdk" "$traceai"; do uv build --python 3.11 --wheel "$repo/python" --out-dir "$dist"; done
# Use the pnpm major matching each checkout's frozen lock format, not a floating CLI.
pnpm_locked() {
  case $(sed -n "s/^lockfileVersion: ['\"]*\([0-9]*\).*/\1/p" pnpm-lock.yaml) in
    6) npx --yes pnpm@8.15.0 "$@" ;;
    9) npx --yes pnpm@10.30.1 "$@" ;;
    *) echo 'Unsupported pnpm lockfile version' >&2; return 1 ;;
  esac
}
(cd "$sdk/typescript/futureagi"
 pnpm_locked install --ignore-workspace --frozen-lockfile --ignore-scripts
 pnpm_locked run build
 npm pack --ignore-scripts --pack-destination "$dist")
(cd "$traceai/typescript"
 pnpm_locked --filter '@traceai/fi-core...' install --frozen-lockfile --ignore-scripts
 pnpm_locked --workspace-concurrency=1 --filter '@traceai/fi-core...' run build
 for pkg in fi-semantic-conventions fi-core; do pnpm_locked --dir "packages/$pkg" pack --pack-destination "$dist"; done)

cd "$dist"
# Match exactly one real artifact per role before issuing the source/build receipt.
record() {
  [[ $# == 1 && -f $1 ]] || { echo 'Expected exactly one package for this role' >&2; return 1; }
  jq -n --arg file "$1" --arg sha "$(sha256sum "$1" | cut -d' ' -f1)" '{file:$file,sha256:$sha}'
}
shopt -s nullglob
sdk_py=$(record futureagi-*.whl); trace_py=$(record fi_instrumentation_otel-*.whl)
sdk_ts=$(record future-agi-sdk-*.tgz); core=$(record traceai-fi-core-*.tgz); sem=$(record traceai-fi-semantic-conventions-*.tgz)
jq -n --arg product "$product" --argjson refs "$SKILL_REFS" --argjson contract "$contract" \
  --argjson sdk_python "$sdk_py" --argjson traceai_python "$trace_py" --argjson sdk_ts "$sdk_ts" \
  --argjson traceai_ts "$core" --argjson semconv_ts "$sem" \
  '{schema:2,sources:{$product,sdk:$refs.sdk.commit,traceai:$refs.traceai.commit},
    branches:($refs | map_values(.branch)),contract:$contract,
    artifacts:{$sdk_python,$traceai_python,$sdk_ts,$traceai_ts,$semconv_ts}}' > pair.json
sha256sum -- *.whl *.tgz > SHA256SUMS
sha=$(sha256sum pair.json | cut -d' ' -f1)
python3 -I "$root/e2e/stack/verify-skill-packages.py" "$dist" "$sha" "$product" "$sdk_commit" "$trace_commit" --require-sums
printf 'pair_sha256=%s\n' "$sha" >> "${GITHUB_OUTPUT:?}"
printf 'sdk_sha256=%s\n' "$(jq -er '.sha256' <<< "$sdk_py")" >> "$GITHUB_OUTPUT"
