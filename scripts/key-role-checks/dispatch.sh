#!/usr/bin/env bash
# Usage: dispatch.sh WORKTREE BASE TESTS_ONLY DEV_BASE LANE OUTPUT_DIRECTORY REMOVAL_TESTS HISTORY_RED [--plan]
set -euo pipefail
repo=$(realpath "$1")
base=$(git -C "$repo" rev-parse "$2^{commit}")
red=$(git -C "$repo" rev-parse "$3^{commit}")
dev=$(git -C "$repo" rev-parse "$4^{commit}")
lane=$5
output=$6
removal=$(git -C "$repo" rev-parse "$7^{commit}")
history=$(git -C "$repo" rev-parse "$8^{commit}")
mode=${9:-}
cloud="$HOME/.futureagi-local/bin/omega-cloud"
head=$(git -C "$repo" rev-parse HEAD)
test -z "$(git -C "$repo" status --porcelain)" || {
  printf '%s\n' 'Commit all work before dispatch.' >&2; exit 2;
}
git -C "$repo" merge-base --is-ancestor "$base" "$red"
git -C "$repo" merge-base --is-ancestor "$red" "$head"
# Only tests may differ at the red revision.
while IFS= read -r path; do
  [[ $path == futureagi/accounts/tests/* ]] || {
    printf 'Non-test path in red revision: %s\n' "$path" >&2; exit 2;
  }
done < <(git -C "$repo" diff --name-only "$base" "$red")
git -C "$repo" merge-base --is-ancestor "$red" "$removal"
git -C "$repo" merge-base --is-ancestor "$removal" "$head"
args=(exec --lane "$lane" --repo "$repo" --ref "$base" --ref "$red" --ref "$dev" --ref "$removal" --ref "$history"
  --standard --machine n2d-standard-32 --disk-gb 200 --life 5h --timeout 4h
  --on-lost fail --keep 1h --no-env-auto
  --env FUTURE_AGI_LICENSE_URL=http://127.0.0.1:9
  --pull '.key-role-checks/*/results/**' --to "$output" --detach)
if [[ $mode == --plan ]]; then
  args+=(--plan)
elif [[ -n $mode ]]; then
  printf '%s\n' 'Only --plan is supported as an optional argument.' >&2; exit 2
else
  # One availability check, no retry loop and no local test fallback.
  "$cloud" status --lane "$lane"
  mkdir -p "$output"
  mkdir "$output/dispatch-started" || {
    printf '%s\n' 'Dispatch already attempted. Inspect job status before any new attempt.' >&2; exit 2;
  }
fi
printf 'base=git:%s red=git:%s head=git:%s dev=git:%s\n' "$base" "$red" "$head" "$dev"
"$cloud" "${args[@]}" -- bash "$repo/scripts/key-role-checks/run.sh" "$repo" "$base" "$red" "$dev" "$head" "$removal" "$history"
printf 'Next: omega-cloud job status --lane %s\n' "$lane"
printf 'Then: omega-cloud job wait --lane %s --timeout 540\n' "$lane"
printf 'After completion and result pull: omega-cloud lease release --lane %s\n' "$lane"
