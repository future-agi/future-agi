#!/usr/bin/env bash
# H16 productSha (e2e/walks/launch/groups.json): build and walk the product commit a group
# names, under the launched commit's harness. Run from the checkout's root:
#   product-overlay.sh <launched HEAD> <productSha>
# The harness paths are lib/walk/receipt.ts HARNESS_PATHS. When the product paths already
# equal the product commit's, nothing changes and nothing is printed. Otherwise the product
# paths are restored from the product commit, HEAD moves to it (reset --soft) and the harness
# paths keep the launched commit's copy: the overlay layout of GATEWAY-CLOUD-RECIPE "Walk
# proofs", which run-walks.sh and the walk receipts check. No harness file is rewritten
# (run-walks.sh is one of them, and it is running). It then prints two lines for run-walks.sh:
#   WALK_OVERLAY_FROM=<launched HEAD>
#   WALK_OVERLAY_PATHS=<harness paths>
# Exit 71: HEAD is not the launched commit. 72: tracked changes, or the result is not that
# layout. 64: productSha is not a full commit id present here (launch.sh ships it with --ref).
set -uo pipefail
LAUNCHED=${1:?usage: product-overlay.sh <launched HEAD> <productSha>}
PRODUCT=${2:?usage: product-overlay.sh <launched HEAD> <productSha>}
HARNESS=(e2e bin/e2e bin/lib agentcc-gateway/cmd/agentcc-mcpmock agentcc-gateway/cmd/agentcc-otlpmock)
EXCLUDE=()
for p in "${HARNESS[@]}"; do EXCLUDE+=(":!$p"); done
[[ $(git rev-parse HEAD) == "$LAUNCHED" ]] || { echo "HEAD is not $LAUNCHED"; exit 71; }
[[ -z $(git --no-optional-locks status --porcelain --untracked-files=no) ]] \
  || { echo "tracked changes: the product is not checked out over them"; exit 72; }
[[ "$PRODUCT" =~ ^[0-9a-f]{40}$ ]] || { echo "productSha is not a full commit id"; exit 64; }
git cat-file -e "$PRODUCT^{commit}" 2> /dev/null \
  || { echo "productSha $PRODUCT is not here (launch.sh ships it with --ref)"; exit 64; }
git diff --quiet "$PRODUCT" HEAD -- . "${EXCLUDE[@]}" && exit 0
git restore --source="$PRODUCT" --staged --worktree -- . "${EXCLUDE[@]}" || exit 72
git reset --quiet --soft "$PRODUCT" || exit 72
git diff --quiet HEAD -- . "${EXCLUDE[@]}" || { echo "the product paths are not $PRODUCT's copy"; exit 72; }
git diff --quiet "$LAUNCHED" -- "${HARNESS[@]}" || { echo "the harness paths are not $LAUNCHED's copy"; exit 72; }
echo "WALK_OVERLAY_FROM=$LAUNCHED"
echo "WALK_OVERLAY_PATHS=${HARNESS[*]}"
