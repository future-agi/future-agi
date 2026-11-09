#!/usr/bin/env bash
# H16 walk job (cloud VM only). Usage: run-walks.sh <group> <expected HEAD>
# For the group in e2e/walks/launch/groups.json:
#   1. HEAD is the commit launched and the tracked tree is clean;
#   2. the stack's images are built from this checkout (bin/e2e build standalone,
#      or build all for Distributed), skipped when the same commit already built
#      them on this lease VM;
#   3. per profile: the readiness check (e2e/scripts/gateway-profile.mjs) refuses a
#      profile this head cannot run before anything starts; otherwise bin/e2e up
#      with the walk pins (E2E_WALK_PINS=1), the walks selected by the group's grep
#      in both browsers (playwright.walk.config.ts, at most 3 workers), the app's
#      log tail, and bin/e2e down (volumes kept: fresh orgs isolate each walk);
#   4. contact sheets of every screenshot, and summary.json.
# Overlay mode (WALK_OVERLAY_FROM=<walk branch commit>, WALK_OVERLAY_PATHS="e2e bin/e2e
# ..."; GATEWAY-CLOUD-RECIPE "Walk proofs: overlay the harness onto the feature
# head"): <expected HEAD> is the PRODUCT commit, checked out, with the walk
# branch's harness-owned paths checked out over it. Tracked changes are allowed
# only under those paths and only as exactly that commit's copy; the receipts
# record the overlay (lib/walk/receipt.ts sourceState).
# Results: e2e/walk-results/<group>/ (pulled by the launcher). With
# E2E_WALK_STRICT=1 (launch.sh WALK_STRICT=1) each walk phase, and only it, runs
# under the adopted egress kit through e2e/walks/kit/strict-kit.sh, which needs
# E2E_KIT_ARCHIVE; otherwise walks run with the Playwright boundary alone
# (ASSETS-PENDING runs, never strict acceptance).
# With E2E_VALUE_FREE=1 (HR-JRN-01; launch.sh forwards it) each profile's walk run lists
# lib/value-free-reporter.ts FIRST in its reporter override, publishes only into its own
# publish directory, $P/value-free-publish, and the seal check runs on it before the job
# ends, exporting only what it vouches for (round 6): an intact publication, or an intact
# refusal record alone, is copied to $P/vfout; a rejected one (a broken seal, even one left
# in place because it holds sources) exports nothing and fails the job. The launcher pulls
# only those export directories (and command-exit.txt); every other result file here, the
# publish directories included, stays on the VM. Round 7: value-free walks run only under
# e2e/walks/vm/value-free-job.sh (launch.sh's value-free job command), which gives the job a
# registries root, E2E_VALUE_FREE_REGISTRIES: each profile's run registers into its own
# registry below it, which the run leaves for the job's final boundary check over exactly
# what the launch pulls. `vfout` is shorter than any registered value can be
# (lib/value-free-seal.mjs refuses a longer export name). Round 8: the export runs through the
# publication choke point (node e2e/lib/value-free-boundary.mjs export), never the seal CLI, and
# its output is not captured or echoed: it goes into the job output the final check holds and
# scans. Off, the override and every result are as they were.
set -uo pipefail
[[ $(uname -s) == Linux ]] || { echo 'cloud VM only'; exit 70; }
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
GROUP=${1:?usage: run-walks.sh <group> <expected HEAD>}
EXPECT=${2:?usage: run-walks.sh <group> <expected HEAD>}
OUT=$ROOT/e2e/walk-results/$GROUP
mkdir -p "$OUT"
cd "$ROOT"
[[ $(git rev-parse HEAD) == "$EXPECT" ]] || { echo "HEAD is not $EXPECT"; exit 71; }
# Product identity ignores the harness-owned paths (lib/walk/receipt.ts HARNESS_PATHS).
HARNESS_EXCLUDE=(':!e2e' ':!bin/e2e' ':!bin/lib' ':!agentcc-gateway/cmd/agentcc-mcpmock' ':!agentcc-gateway/cmd/agentcc-otlpmock')
OVERLAY_EXCLUDE=()
if [[ -n "${WALK_OVERLAY_FROM:-}" ]]; then
  [[ "$WALK_OVERLAY_FROM" =~ ^[0-9a-f]{40}$ ]] || { echo "WALK_OVERLAY_FROM is not a full commit id"; exit 64; }
  read -r -a OVERLAY_PATHS <<<"${WALK_OVERLAY_PATHS:-}"
  ((${#OVERLAY_PATHS[@]})) || { echo "WALK_OVERLAY_PATHS names no path"; exit 64; }
  for p in "${OVERLAY_PATHS[@]}"; do
    case "${p%/}" in
      e2e|bin/e2e|bin/lib|agentcc-gateway/cmd/agentcc-mcpmock|agentcc-gateway/cmd/agentcc-otlpmock) OVERLAY_EXCLUDE+=(":!${p%/}") ;;
      *) echo "overlay path $p is not harness-owned"; exit 64 ;;
    esac
  done
  git diff --quiet "$WALK_OVERLAY_FROM" -- "${OVERLAY_PATHS[@]}" || { echo "overlay paths are not $WALK_OVERLAY_FROM's copy"; exit 72; }
  export E2E_WALK_OVERLAY_FROM=$WALK_OVERLAY_FROM E2E_WALK_OVERLAY_PATHS="${OVERLAY_PATHS[*]}"
fi
[[ -z $(git --no-optional-locks status --porcelain --untracked-files=no -- . ${OVERLAY_EXCLUDE[@]+"${OVERLAY_EXCLUDE[@]}"}) ]] || { git status --short | head; exit 72; }
if [[ "${E2E_VALUE_FREE:-}" == 1 && ! -d "${E2E_VALUE_FREE_REGISTRIES:-}" ]]; then
  echo 'value-free walks run under e2e/walks/vm/value-free-job.sh: its final boundary check reads the runs registries'
  exit 64
fi
cfg() { node -e 'const g = JSON.parse(require("fs").readFileSync(process.argv[1], "utf8"))[process.argv[2]];
  const v = g[process.argv[3]]; console.log(Array.isArray(v) ? v.join(" ") : (v ?? ""));' \
  "$ROOT/e2e/walks/launch/groups.json" "$GROUP" "$1"; }
STACK=$(cfg stack); read -r -a PROFILES <<<"$(cfg profiles)"; GREP=$(cfg grep); PRODUCT=$(cfg productSha)
REPLICAS=$(cfg replicas)
export CI=1 PLAYWRIGHT_BROWSERS_PATH=$HOME/ms-pw E2E_STACK=$STACK E2E_WALK_PINS=1 E2E_GATEWAY_REPLICAS=1
export FUTURE_AGI_LICENSE_URL=http://127.0.0.1:9 FUTURE_AGI_TELEMETRY_URL=http://127.0.0.1:9 FUTURE_AGI_TELEMETRY_DISABLED=true
export OLLAMA_HOST=127.0.0.1:9 OLLAMA_URL=http://127.0.0.1:9 OLLAMA_BASE_URL=http://127.0.0.1:9
export LITELLM_LOCAL_MODEL_COST_MAP=True PYTHONDONTWRITEBYTECODE=1
[[ -n "$PRODUCT" ]] && export E2E_WALK_PRODUCT_SHA=$PRODUCT
# The managed-mock inspector names the Docker context it reads (lib/managed-mock.ts):
# the VM's current one, recorded; bin/e2e and the probes use the same daemon.
[[ -z "${DOCKER_HOST:-}" ]] && export DOCKER_CONTEXT=$(docker context show)
{
  echo "realHead=$(git rev-parse HEAD)"; echo "dirty=0"; echo "group=$GROUP stack=$STACK profiles=${PROFILES[*]}"
  echo "grep=$GREP replicasDeclared=$REPLICAS"
  if [[ -n "$PRODUCT" ]]; then
    if git diff --quiet "$PRODUCT" HEAD -- . "${HARNESS_EXCLUDE[@]}"; then echo "productIdenticalTo=$PRODUCT"
    else echo "productDiffersFrom=$PRODUCT"; fi
  fi
  [[ -n "${WALK_OVERLAY_FROM:-}" ]] && echo "overlayFrom=$WALK_OVERLAY_FROM overlayPaths=${OVERLAY_PATHS[*]}"
  date -u +%FT%TZ; docker version --format 'docker {{.Server.Version}}'; nproc
  echo "dockerContext=${DOCKER_CONTEXT:-unset} dockerHost=${DOCKER_HOST:-unset}"
} > "$OUT/source.txt" 2>&1

summary=()
note() { summary+=("$1"); printf '%s\n' "$1" | tee -a "$OUT/steps.txt"; }

# All four Distributed components are local head-specific tags, never :latest.
if [[ "$STACK" == distributed ]]; then
  export FUTURE_AGI_VERSION=walk-${EXPECT:0:12} FRONTEND_VERSION=walk-${EXPECT:0:12}
  export E2E_FI_COLLECTOR_VERSION=walk-${EXPECT:0:12} AGENTCC_GATEWAY_VERSION=walk-${EXPECT:0:12}
fi
manifest=$OUT/../.images-$STACK.json
stamp=$OUT/../.built-$STACK
# The images' inputs: HEAD, plus (overlay mode) the overlay's copy of the build
# inputs it may carry (bin/lib, the gateway module's test mocks).
BUILD_KEY=$EXPECT
[[ -n "${WALK_OVERLAY_FROM:-}" ]] && BUILD_KEY="$EXPECT+$(git diff "$EXPECT" -- bin/lib agentcc-gateway | git hash-object --stdin)"
build_rc=0
if [[ -f "$stamp" && -f "$manifest" && $(cat "$stamp") == "$BUILD_KEY" ]]; then
  note "build stack=$STACK reused (built from $EXPECT on this VM)"
else
  start=$SECONDS
  if [[ "$STACK" == standalone ]]; then bin/e2e build standalone > "$OUT/build.log" 2>&1 || build_rc=$?
  else
    (bin/e2e build all &&
      docker build -t "futureagi/agentcc-gateway:$AGENTCC_GATEWAY_VERSION" agentcc-gateway &&
      docker tag futureagi/future-agi:e2e-local "futureagi/future-agi:$FUTURE_AGI_VERSION" &&
      docker tag futureagi/frontend:e2e-local "futureagi/frontend:$FRONTEND_VERSION" &&
      docker tag futureagi/fi-collector:e2e-local "futureagi/fi-collector:$E2E_FI_COLLECTOR_VERSION") > "$OUT/build.log" 2>&1 || build_rc=$?
  fi
  if ((build_rc == 0)); then
    node e2e/scripts/walk-images.mjs built "$STACK" "$EXPECT" "$manifest" >> "$OUT/build.log" 2>&1 || build_rc=$?
  fi
  note "build stack=$STACK rc=$build_rc secs=$((SECONDS - start))"
  ((build_rc == 0)) && echo "$BUILD_KEY" > "$stamp"
fi
if ((build_rc != 0)); then tail -60 "$OUT/build.log"; exit 3; fi

fail=0
for profile in "${PROFILES[@]}"; do
  P=$OUT/$profile; mkdir -p "$P"
  export E2E_GATEWAY_PROFILE=$profile
  ready_rc=0
  node e2e/scripts/gateway-profile.mjs "$profile" "$STACK" > "$P/profile.txt" 2> "$P/refused.txt" || ready_rc=$?
  if ((ready_rc != 0)); then
    note "profile=$profile REFUSED rc=$ready_rc: $(head -c 600 "$P/refused.txt")"
    continue
  fi
  if [[ "$REPLICAS" == 2 && "$profile" == mcp* ]]; then export E2E_GATEWAY_REPLICAS=2; else export E2E_GATEWAY_REPLICAS=1; fi
  start=$SECONDS; up_rc=0
  timeout 1500 bin/e2e up > "$P/up.log" 2>&1 || up_rc=$?
  if ((up_rc != 0)); then
    note "profile=$profile UP-FAILED rc=$up_rc secs=$((SECONDS - start))"
    bin/e2e ps > "$P/ps.txt" 2>&1; bin/e2e logs --tail 300 > "$P/stack-logs.txt" 2>&1
    bin/e2e down > "$P/down.log" 2>&1
    fail=1; continue
  fi
  export E2E_WALK_IMAGES="$P/images.json"
  if ! node e2e/scripts/walk-images.mjs running "$STACK" "$EXPECT" "$manifest" > "$E2E_WALK_IMAGES" 2> "$P/images-error.log"; then
    note "profile=$profile IMAGE-MISMATCH (not the locally built head)"
    bin/e2e down > "$P/down.log" 2>&1; fail=1; continue
  fi
  cat "$E2E_WALK_IMAGES" >> "$OUT/source.txt"
  note "profile=$profile up rc=0 secs=$((SECONDS - start)) replicas=$E2E_GATEWAY_REPLICAS"
  curl -fsS http://localhost:28090/healthz > "$P/gateway-health.txt" 2>&1 || true
  start=$SECONDS; walk_rc=0
  export E2E_WALK_COUNTS="$P/counts.json"
  export E2E_CENSUS_RUN_ID="${E2E_CENSUS_RUN_ID:-$EXPECT-$GROUP-$profile-$(date +%s)-$$}"
  export E2E_CENSUS_RESULTS="${E2E_CENSUS_RESULTS:-$P/census-results}"
  export E2E_WALK_PRODUCT_SHA="${E2E_WALK_PRODUCT_SHA:-$EXPECT}"
  # PLAYWRIGHT_JSON_OUTPUT_FILE set: the JSON report too (the CLI reporter list replaces the config's).
  if [[ "${E2E_VALUE_FREE:-}" == 1 ]]; then
    # The value-free reporter first (it assembles, scans and seals the publication); a fresh publish directory per profile,
    # and the run's own registry below the job's (round 7: the run leaves it for value-free-job.sh's final boundary check).
    export E2E_VALUE_FREE_PUBLISH="$P/value-free-publish"
    export E2E_VALUE_FREE_REGISTRY="$E2E_VALUE_FREE_REGISTRIES/$profile"
    mkdir -m 700 "$E2E_VALUE_FREE_REGISTRY" || fail=1
    WALK_CMD=(yarn playwright test --config playwright.walk.config.ts --grep "$GREP" \
      --reporter=./lib/value-free-reporter.ts,line,./walks/vm/counts-reporter.cjs"${PLAYWRIGHT_JSON_OUTPUT_FILE:+,json}")
  else
    WALK_CMD=(yarn playwright test --config playwright.walk.config.ts --grep "$GREP" \
      --reporter=line,./walks/vm/counts-reporter.cjs"${PLAYWRIGHT_JSON_OUTPUT_FILE:+,json}")
  fi
  if [[ "${E2E_WALK_STRICT:-}" == 1 ]]; then
    # H17: the walk phase only, under the adopted kit; the stack is already up.
    project=${E2E_COMPOSE_PROJECT:-futureagi-e2e}; [[ "$STACK" == standalone ]] && project=${E2E_COMPOSE_PROJECT:-futureagi-e2e-standalone}
    read -r -a nets <<<"$(docker network ls --filter "label=com.docker.compose.project=$project" --format '{{.Name}}' | tr '\n' ' ')"
    (cd e2e && E2E_WALK_OUT="walk-results/$GROUP/$profile/pw" bash walks/kit/strict-kit.sh "${E2E_KIT_ARCHIVE:?}" \
      "$P/kit" "${nets[@]}" -- "${WALK_CMD[@]}") > "$P/walk.log" 2>&1 || walk_rc=$?
  else
    (cd e2e && E2E_WALK_OUT="walk-results/$GROUP/$profile/pw" timeout 3600 "${WALK_CMD[@]}") > "$P/walk.log" 2>&1 || walk_rc=$?
  fi
  note "profile=$profile walk rc=$walk_rc secs=$((SECONDS - start)) strict=${E2E_WALK_STRICT:-0}"
  ((walk_rc == 0)) || fail=1
  if [[ "${E2E_VALUE_FREE:-}" == 1 ]]; then
    # After Playwright exits, before anything is pulled: the choke point's export (round 8) copies an intact publication
    # (0) or a clean refusal record alone (1) to $P/vfout, the only path pulled for the profile; a rejected one exports
    # nothing (1). It prints one STATUS constant into this job's output, which value-free-job.sh holds for the final check.
    seal_rc=0
    node e2e/lib/value-free-boundary.mjs export "$E2E_VALUE_FREE_PUBLISH" "$P/vfout" || seal_rc=$?
    note "profile=$profile value-free seal rc=$seal_rc"
    ((seal_rc == 0)) || fail=1
    unset E2E_VALUE_FREE_PUBLISH
    unset E2E_VALUE_FREE_REGISTRY
  fi
  bin/e2e logs --tail 400 app > "$P/app-tail.log" 2>&1 || true
  bin/e2e down > "$P/down.log" 2>&1 || note "profile=$profile down failed"
done

node e2e/scripts/walk-contact-sheets.mjs "$OUT" > "$OUT/contact-sheets.log" 2>&1 || note "contact sheets failed"
node -e 'const fs = require("fs"); const [out, lines] = [process.argv[1], process.argv.slice(2)];
  fs.writeFileSync(out + "/summary.json", JSON.stringify({ steps: lines, writtenAt: new Date().toISOString() }, null, 2));' \
  "$OUT" ${summary[@]+"${summary[@]}"}
exit "$fail"
