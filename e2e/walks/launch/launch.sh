#!/usr/bin/env bash
# H16: launch one walk group (e2e/walks/launch/groups.json) on its own cloud VM,
# with the gateway cloud recipe's flags (GATEWAY-CLOUD-RECIPE.md):
#   --repo <worktree>@<exact sha> --skip-nested --standard --machine n2d-standard-32
#   --life 4h --timeout 150m (over 60m, which --standard needs) --on-lost fail
#   --prepare: futureagi uv sync, agentcc-gateway go mod download (go.sum restored),
#     frontend and e2e yarn installs, and the e2e Playwright browsers in a
#     job-writable directory (the image's /opt/ms-playwright is another version).
#   The gwtest Postgres/Redis stack of the unit-gate recipe is NOT started: walks
#   bring up their own e2e stack, and under the egress kit every running container
#   must belong to a selected walk network.
# Usage: launch.sh <group> [plan|run|print]   (default run)
#   plan   omega-cloud --plan: what ships and the scan, no VM
#   run    host-guard, then a detached launch; prints the EXEC-JOB line
#   print  the omega-cloud arguments only (offline test: harness/walk-harness.spec.ts)
# Pulls land in WALK_PULL_ROOT (absolute), by default ~/.futureagi-local/walk-landed/<worktree
# name>: never inside a git worktree, which the rig refuses only at pull time, after the walk ran.
# Before launch: HEAD is the commit shipped and the tracked tree is clean (an
# untracked futureagi/ee/cloud is private: never read, never shipped). After it:
# `omega-cloud job status --lane <lane>` before any wait or pull; a lost or
# vm-gone job is finished (keep its receipt, never wait on or pull it again),
# then `omega-cloud lease release --lane <lane>`.
# Exit 77 (capped / pack gate): wait 60 s and retry, at most 30 times. Exit 74
# (gcloud login expired): blocked, never retried. Anything else: printed.
# E2E_VALUE_FREE=1 (HR-JRN-01, the one switch): the VM runs the walks value-free
# (--env E2E_VALUE_FREE=1; run-walks.sh lists lib/value-free-reporter.ts first in its
# reporter override, seals each profile's publish directory and exports only what the seal
# check vouches for), and the pull is ONLY e2e/walk-results/<group>/<profile>/vfout/** per
# profile of the group, plus the command-exit.txt the job writes (one integer).
# Never the publish directory itself: one the check rejects can stay where it is (it holds
# sources), so only the export decides what is transferred. `vfout` is
# shorter than any registered value can be, and the job runs under e2e/walks/vm/value-free-job.sh
# with exactly these pulls: it lands the exit file, then runs the publication boundary's final
# check (lib/value-free-boundary.mjs) over everything they would transfer and the job's held
# output; any registered value in a pulled path or file, or in that output, moves every pull
# aside, so nothing lands (the job exits 1). A gate or hosted job assembles no publish
# directory, so a value-free launch of it is refused (exit 64), never pulled empty. Also,
# before anything is printed, planned or dispatched, the choke point's declaration check
# (lib/value-free-boundary.mjs declare) reads exactly these pulls; any pull that is not one
# exact path below the pull root (absolute, escaping, the root or above, a glob, a blank)
# refuses the launch (exit 64, a fixed line; the declaration is never printed), so no job is
# created to collect it. Off (unset), every flag, pull and the job command are as they were.
set -u
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
WT=${WALK_WT:-$(cd "$HERE/../../.." && pwd)}
GROUP=${1:?usage: launch.sh <group> [plan|run|print]}
MODE=${2:-run}
OC=${OMEGA_CLOUD:-$HOME/.futureagi-local/bin/omega-cloud}
GUARD=${HOST_GUARD:-$HOME/.futureagi-local/bin/host-guard.sh}
PULL_ROOT=${WALK_PULL_ROOT:-$HOME/.futureagi-local/walk-landed/$(basename "$WT")}
SHA=${WALK_SHA:-$(git -C "$WT" rev-parse HEAD)}
read_group() { node -e '
  const g = JSON.parse(require("fs").readFileSync(process.argv[1], "utf8"))[process.argv[2]];
  if (!g || process.argv[2].startsWith("_")) { console.error("no walk group " + process.argv[2]); process.exit(2); }
  const profiles = (g.profiles || []).filter((p) => /^[a-z0-9-]+$/.test(p));
  console.log([g.job, g.lane, g.productSha || "-", ...profiles].join(" "));' "$HERE/groups.json" "$GROUP"; }
group_line=$(read_group) || exit 2
read -r JOB GROUP_LANE PRODUCT PROFILES <<<"$group_line"
[[ "$PRODUCT" == - ]] && PRODUCT=''
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "WALK_SHA must be a full commit id"; exit 64; }
git -C "$WT" cat-file -e "$SHA^{commit}" || { echo "no commit $SHA in $WT"; exit 64; }
LANE=${WALK_LANE:-$GROUP_LANE}
[[ "$LANE" =~ ^gw-[a-z0-9-]+$ ]] || { echo "lane $LANE is not gw-<id>"; exit 64; }
PREPARE='export PLAYWRIGHT_BROWSERS_PATH=$HOME/ms-pw; (cd futureagi && uv sync --locked) && (cd agentcc-gateway && go mod download all && git checkout -- go.sum && go version | grep -E "go1[.]26[.]") && (cd frontend && yarn install --frozen-lockfile) && (cd e2e && yarn install --frozen-lockfile && npx playwright install chromium firefox)'
VALUE_FREE=0
if [[ "${E2E_VALUE_FREE:-}" == 1 ]]; then
  [[ "$JOB" == walks ]] || { echo "value-free mode publishes walk runs only: the $JOB job assembles no publish directory, so it is refused"; exit 64; }
  [[ -n "${PROFILES:-}" ]] || { echo "value-free mode: group $GROUP lists no profile to publish"; exit 64; }
  VALUE_FREE=1
fi
case "$JOB" in
  gate) CMD=(bash e2e/walks/vm/gate.sh "$SHA") ;;
  walks) CMD=(bash e2e/walks/vm/run-walks.sh "$GROUP" "$SHA") ;;
  hosted) CMD=(bash e2e/walks/hosted/run-hosted.sh "$GROUP" "$SHA") ;;
  *) echo "group $GROUP has an unknown job $JOB"; exit 2 ;;
esac
# The cloud rig reserves several exit codes (724). Land the command's true
# status, but return only 0/1 to the rig so refusal never destroys the VM.
# Value-free: value-free-job.sh does that too, then runs the final boundary check
# over exactly the pulls (one list, built once here, for both).
EXIT_FILE=e2e/walk-results/command/command-exit.txt
if ((VALUE_FREE)); then
  read -r -a profile_list <<<"$PROFILES"
  pulls=()
  for profile in "${profile_list[@]}"; do pulls+=("e2e/walk-results/$GROUP/$profile/vfout/**"); done
  pulls+=("$EXIT_FILE")
  node "$WT/e2e/lib/value-free-boundary.mjs" declare -- "${pulls[@]}" || { echo 'value-free mode: a pull is not one exact path below the pull root, so the launch is refused before dispatch'; exit 64; }
  CMD=(bash e2e/walks/vm/value-free-job.sh "$EXIT_FILE" "${pulls[@]}" -- "${CMD[@]}")
else
  # Everything that may land from the VM, in one list: the files the independent
  # evidence auditor reads and stages, every one of them pulled ONLY
  # from this launch's landing directory, never from the tree the job wrote.
  LANDS=('e2e/walk-results/**/steps.txt' 'e2e/walk-results/**/summary.json' 'e2e/walk-results/**/counts.json'
    'e2e/walk-results/**/walk-receipt.json' 'e2e/walk-results/**/command-exit.txt' 'e2e/FLOWS.md'
    'e2e/walk-results/**/evidence-audit.json')
  # One landing directory per launch (a reused lease VM keeps older ones):
  # e2e/walk-landed/<id>/ holds copies of LANDS files at their repo paths.
  LAND_ID=${WALK_LAND_ID:-l$(date -u +%Y%m%dt%H%M%S)-$$}
  [[ "$LAND_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] || { echo "WALK_LAND_ID must be 1-64 of [A-Za-z0-9._-]"; exit 64; }
  LANDING=e2e/walk-landed/$LAND_ID
  # e2e/walks/vm/land.sh runs the command, then the auditor
  # (e2e/scripts/evidence-audit.mjs) reads every byte of every file that would
  # land, quarantines refused files under $HOME/evidence-refused (never pulled),
  # stages the clean ones, has the report and the landing re-checked by separate
  # auditor runs, and only then renames the result into $LANDING. Any failure
  # lands just the command exit (86 refused, 87 gate failed) and a verified
  # value-free report or a constants-only stub. The cloud rig reserves several
  # exit codes (724): land.sh returns only 0/1, the true status is landed.
  CMD=(bash e2e/walks/vm/land.sh "$LAND_ID" "${#LANDS[@]}" "${LANDS[@]}" -- "${CMD[@]}")
fi
opts=(--lane "$LANE" --repo "$WT@$SHA" --skip-nested --machine n2d-standard-32 --standard
  --life 4h --timeout "${WALK_TIMEOUT:-150m}" --keep 60m --on-lost fail --env CI=1
  --env FUTURE_AGI_LICENSE_URL=http://127.0.0.1:9 --env OLLAMA_HOST=127.0.0.1:9
  --env OLLAMA_URL=http://127.0.0.1:9 --env OLLAMA_BASE_URL=http://127.0.0.1:9
  --env LITELLM_LOCAL_MODEL_COST_MAP=True --env PYTHONDONTWRITEBYTECODE=1
  --prepare "$PREPARE")
if ((VALUE_FREE)); then
  # Value-free: each profile's export (what the seal check vouched for), and the job's one-integer exit file, which the
  # final boundary check passed. Nothing else: the publish directory and the staging and withheld siblings (.vfout-*,
  # .command-exit.txt-withheld-*) never match them.
  opts+=(--env E2E_VALUE_FREE=1)
  for p in "${pulls[@]}"; do opts+=(--pull "$p"); done
  opts+=(--to "$PULL_ROOT/$GROUP")
else
  for g in "${LANDS[@]}"; do opts+=(--pull "$LANDING/$g"); done
  opts+=(--to "$PULL_ROOT/$GROUP")
fi
# Raw logs, Playwright traces and fixture attachments stay on the VM: even
# synthetic credential-shaped values must not be pulled. Counts are emitted by
# counts-reporter.cjs; steps/summary contain only step results and readiness.
# walk-receipt.json is the explicit, credential-free origin/count census summary
# from lib/walk/receipt.ts, not the raw report or seeded-scopes/API attachments.
# Pulled files land under <job>/files/<worktree>/e2e/walk-landed/<id>/e2e/...;
# the verdict is e2e/walk-results/command/command-exit.txt there.
# The product commit is shipped with the tree; run-walks.sh builds it when its product paths
# differ from the launched commit's (e2e/walks/vm/product-overlay.sh).
[[ -n "$PRODUCT" && "$PRODUCT" != "$SHA" ]] && opts+=(--ref "$PRODUCT")
# WALK_STRICT=1 (H17): ship the adopted kit archive alone, re-verified here and again
# on the VM, and run each walk phase under it. Large kit shipments wait while
# research's freeze-path shipping holds the pack slots.
KIT_SRC=${WALK_KIT_ARCHIVE:-$WT/e2e/walk-kit/egress-sinkhole-r574b.tar}
KIT_SHA256=31a52264e27c63626caf3120d4935e64ee5bdb841edab0b0351cee5a430849f7
if [[ "${WALK_STRICT:-}" == 1 ]]; then
  KIT_DIR=$PULL_ROOT/$GROUP/kit-ship
  opts+=(--ship "$KIT_DIR" --env E2E_WALK_STRICT=1 --env "E2E_KIT_ARCHIVE=$KIT_DIR/egress-sinkhole-r574b.tar")
fi
if [[ "$MODE" == print ]]; then printf '%s\n' "${opts[@]}" -- "${CMD[@]}"; exit 0; fi

# The pull root, before any VM exists: absolute, and outside every git worktree (the rig
# resolves a relative --to from the worktree, and refuses one inside a worktree only when
# it pulls, after the walk has run).
[[ "$PULL_ROOT" == /* ]] || { echo "WALK_PULL_ROOT must be an absolute path"; exit 64; }
probe=$PULL_ROOT
while [[ ! -e "$probe" ]]; do probe=$(dirname "$probe"); done
if git -C "$probe" rev-parse --show-toplevel > /dev/null 2>&1; then
  echo "WALK_PULL_ROOT $PULL_ROOT is inside a git worktree: pulled files never land in one; choose a directory outside every worktree"
  exit 64
fi
# The tree the VM gets must be exactly the commit named.
[[ $(git -C "$WT" rev-parse HEAD) == "$SHA" ]] || { echo "HEAD of $WT is not $SHA"; exit 71; }
[[ -z $(git -C "$WT" --no-optional-locks status --porcelain --untracked-files=no) ]] || { echo "$WT has tracked changes"; exit 72; }
other=$(git -C "$WT" --no-optional-locks status --porcelain --untracked-files=normal | grep '^??' | grep -v '^?? futureagi/ee/cloud/$' || true)
[[ -z "$other" ]] || { echo "$WT has untracked paths besides futureagi/ee/cloud:"; echo "$other"; exit 72; }
export CLOUDSDK_STORAGE_PROCESS_COUNT=2 CLOUDSDK_STORAGE_THREAD_COUNT=4 OMEGA_EXEC_SESSION=gw
"$GUARD" "$LANE" >&2 || { echo "host-guard refused"; exit 73; }
mkdir -p "$PULL_ROOT/$GROUP/logs"
if [[ "${WALK_STRICT:-}" == 1 ]]; then
  if grep -lsq '"lane": *"rs-' "$HOME"/.futureagi-local/locks/cloud-pack/slot-*/owner.json; then
    echo "a research lane holds a pack slot (freeze-path shipping): strict kit shipment waits"; exit 75
  fi
  mkdir -p "$KIT_DIR" && cp "$KIT_SRC" "$KIT_DIR/egress-sinkhole-r574b.tar" || exit 70
  got=$(shasum -a 256 "$KIT_DIR/egress-sinkhole-r574b.tar" | awk '{print $1}')
  [[ "$got" == "$KIT_SHA256" ]] || { echo "kit archive sha256 mismatch ($got); refusing"; exit 70; }
fi
if [[ "$MODE" == plan ]]; then cd "$WT" && exec "$OC" exec "${opts[@]}" --plan -- "${CMD[@]}"; fi
log=$PULL_ROOT/$GROUP/logs/launch.out
n=0
while :; do
  n=$((n + 1))
  out=$(cd "$WT" && "$OC" exec "${opts[@]}" --detach -- "${CMD[@]}" 2>&1); rc=$?
  { echo "try $n $(date '+%F %T %Z') sha=$SHA rc=$rc"; echo "$out"; } >>"$log"
  if grep -qE '^EXEC-JOB .*job=j-' <<<"$out"; then grep -E '^EXEC-JOB ' <<<"$out" | tail -1; exit 0; fi
  if grep -qE '^EXEC .*exit=74 ' <<<"$out"; then echo "$out" | tail -5; echo "BLOCKED: gcloud login expired (exit 74); not retried"; exit 74; fi
  if grep -qE '^EXEC .*exit=77 ' <<<"$out" && [ "$n" -lt 30 ]; then sleep 60; continue; fi
  echo "$out" | tail -15; echo "launch rc=$rc"; exit 1
done
