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
api=api_contracts/openapi/swagger.json
tools=futureagi/mcp_server/catalog/tools.generated.json
git -C "$root" ls-files --error-unmatch -- "$api" "$tools" >/dev/null
git -C "$root" diff --exit-code --quiet HEAD -- "$api" "$tools"
for repo in "$sdk" "$traceai"; do
  state=$(git -C "$repo" status --porcelain --untracked-files=all --ignored)
  [[ -z $state ]] || { echo 'Expected clean disposable package checkouts' >&2; exit 1; }
done
api_sha=$(sha256sum "$root/$api" | cut -d' ' -f1)
catalog_source=$(jq -er '.source_contract_sha256' "$root/$tools")
[[ $catalog_source == "$api_sha" ]] || { echo 'Product MCP catalog was not generated from this OpenAPI file' >&2; exit 1; }
# Keep non-Skills code on the baseline selected by this SDK, not its feature tip.
if ! baseline=$(jq -er '.non_skills_sdk_commit' "$sdk/sdkgen/contracts/SOURCE.json") || [[ ! $baseline =~ ^[a-f0-9]{40}$ ]]; then
  echo 'The resolved SDK must include Skills sync support and its full non-Skills baseline commit in SOURCE.json' >&2
  exit 1
fi
git -C "$sdk" cat-file -e "${baseline}:sdkgen/contracts/swagger.json"
(
  cd "$sdk"
  python3 -IB sdkgen/sync_contract.py "$root/$api" --backend-commit "$product" \
    --mcp-tools "$root/$tools" --skills-baseline-sdk "$baseline"
  python3 -IB sdkgen/generate.py --only python typescript
  python3 -IB sdkgen/generate.py --check --only python typescript
  python3 -IB sdkgen/check_skills_baseline.py
  # check_mcp_parity.py (every backend tool has an SDK catalog entry) is the SDK repo's own
  # gate; product CI requires only that what the SDK ships matches this backend.
)
contract=$(python3 -IB "$root/e2e/stack/check-skill-contract.py" "$root" "$sdk" "$baseline")
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
