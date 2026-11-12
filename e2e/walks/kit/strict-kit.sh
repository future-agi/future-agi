#!/usr/bin/env bash
# H17: the strict-kit wrapper. It runs ONE walk phase under the adopted
# egress kit, egress-sinkhole-r574b, exactly as the kit's README
# documents it, and nothing else:
#   1. re-verify the archive: sha256 must be KIT_SHA256 below (fail closed, exit 70);
#   2. extract it to a fresh scratch directory (the kit is never edited);
#   3. named mode first (extend-sinkhole.sh --defaults, with its receipt), then
#      mark the query log (count-attempts.sh mark);
#   4. the walk through the walk-phase lockdown: lockdown.sh run, one --network per
#      stack bridge (the stack is already up: dependencies installed, images
#      pulled, networks created, every container restart: "no"), --ttl;
#   5. count the named-host window (count-attempts.sh count --expect zero) into
#      attempts.tsv, copy the query log and /var/log/omega-walk-audits/;
#   6. exit: the lockdown's 81 (a refused packet or DNS query), 82 (boundary or
#      restore unprovable) and 83 stay failures; a non-zero count is 80 + its exit;
#      otherwise the walk's own exit.
# Only walk phases: the command must be `yarn|npx playwright test --config
# playwright.walk.config.ts ...` (exit 64 otherwise). Unit, integration, Django,
# vitest and race gates never run under the kit (GATEWAY-CLOUD-RECIPE, "Unit
# gates are not walks"): their deliberate .invalid dials exit 81 and prove nothing.
# Usage: strict-kit.sh [--print] <kit archive> <out dir> <network>... -- <walk command>
#   --print   print the steps, verify nothing, run nothing (offline test)
# Cloud VM only (after the hash check, which is safe anywhere). The lockdown
# kills and leaves stopped every enrolled stack container: the next walk starts
# a new stack (bin/e2e down, then up).
set -uo pipefail
KIT_SHA256=31a52264e27c63626caf3120d4935e64ee5bdb841edab0b0351cee5a430849f7
KIT_ROOT=egress-sinkhole-r574b
TTL=${E2E_KIT_TTL:-3600}
PRINT=0
[[ "${1:-}" == --print ]] && { PRINT=1; shift; }
ARCHIVE=${1:?usage: strict-kit.sh [--print] <kit archive> <out dir> <network>... -- <walk command>}
OUT=${2:?usage: strict-kit.sh [--print] <kit archive> <out dir> <network>... -- <walk command>}
shift 2
NETS=()
while (($#)) && [[ "$1" != -- ]]; do
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || { echo "strict-kit: network $1 is not a Docker network name"; exit 64; }
  NETS+=(--network "$1"); shift
done
[[ "${1:-}" == -- ]] || { echo "strict-kit: missing -- before the walk command"; exit 64; }
shift
WALK=("$@")
((${#NETS[@]})) || { echo "strict-kit: name every stack bridge network the walk uses"; exit 64; }
[[ "$TTL" =~ ^[0-9]+$ ]] && ((TTL >= 30 && TTL <= 7200)) || { echo "strict-kit: --ttl must be 30-7200 s"; exit 64; }
walk_ok=0
if ((${#WALK[@]} >= 4)) && [[ "${WALK[0]}" == yarn || "${WALK[0]}" == npx ]] && [[ "${WALK[1]} ${WALK[2]}" == "playwright test" ]]; then
  for ((i = 3; i < ${#WALK[@]} - 1; i++)); do
    [[ "${WALK[i]}" == --config && "${WALK[i+1]}" == playwright.walk.config.ts ]] && walk_ok=1
  done
fi
((walk_ok)) || { echo "strict-kit: only a walk phase (playwright test --config playwright.walk.config.ts) runs under the kit"; exit 64; }

LOCKDOWN=(lockdown.sh run "${NETS[@]}" --ttl "$TTL" -- "${WALK[@]}")
if ((PRINT)); then
  printf 'verify %s sha256=%s\n' "$ARCHIVE" "$KIT_SHA256"
  printf 'extend-sinkhole.sh --defaults --receipt %s/extend-receipt.txt\n' "$OUT"
  printf 'count-attempts.sh mark %s/marker\n' "$OUT"
  printf '%s\n' "${LOCKDOWN[*]}"
  printf 'count-attempts.sh count --since %s/marker --expect zero > %s/attempts.tsv\n' "$OUT" "$OUT"
  exit 0
fi

actual=$( (sha256sum "$ARCHIVE" 2>/dev/null || shasum -a 256 "$ARCHIVE" 2>/dev/null) | awk '{print $1}')
[[ "$actual" == "$KIT_SHA256" ]] || { echo "strict-kit: kit archive sha256 mismatch (got ${actual:-none}); refusing"; exit 70; }
[[ $(uname -s) == Linux ]] || { echo 'strict-kit: cloud VM only'; exit 70; }
mkdir -p "$OUT" || exit 70
printf 'kitArchive=%s\nkitDigest=sha256:%s\nttl=%s\nnetworks=%s\n' "$ARCHIVE" "$KIT_SHA256" "$TTL" "${NETS[*]}" > "$OUT/kit.txt"
SCRATCH=$(mktemp -d /tmp/strict-kit.XXXXXX) || exit 70
tar -xf "$ARCHIVE" -C "$SCRATCH" || { echo "strict-kit: extract failed"; exit 70; }
K=$SCRATCH/$KIT_ROOT
[[ -f "$K/lockdown.sh" && -f "$K/extend-sinkhole.sh" && -f "$K/count-attempts.sh" ]] || { echo "strict-kit: kit layout unexpected"; exit 70; }
bash "$K/extend-sinkhole.sh" --defaults --receipt "$OUT/extend-receipt.txt" || { echo "strict-kit: boundary setup failed"; exit 70; }
bash "$K/count-attempts.sh" mark "$OUT/marker"
rc=0
bash "$K/${LOCKDOWN[0]}" "${LOCKDOWN[@]:1}" > "$OUT/lockdown.out" 2>&1 || rc=$?
crc=0
bash "$K/count-attempts.sh" count --since "$OUT/marker" --expect zero > "$OUT/attempts.tsv" || crc=$?
cp /var/log/omega-egress-dnsmasq.log "$OUT/dnsmasq-query.log" 2>/dev/null || echo "query log not readable" > "$OUT/dnsmasq-query.missing"
cp -R /var/log/omega-walk-audits "$OUT/walk-audits" 2>/dev/null || echo "walk audits not readable" > "$OUT/walk-audits.missing"
printf 'walkExit=%s countExit=%s\n' "$rc" "$crc" > "$OUT/exit.txt"
case "$rc" in
  81) echo "STRICT FAIL 81: a packet or DNS query the boundary refused during the walk (see lockdown.out, walk-audits)"; exit 81 ;;
  82) echo "STRICT FAIL 82: the boundary or its restore was unprovable"; exit 82 ;;
  83) echo "STRICT FAIL 83: a named host answered from upstream"; exit 83 ;;
esac
if ((crc != 0)); then echo "EGRESS PROOF FAIL (named-host DNS block): count exit $crc, walk exit $rc, see attempts.tsv"; exit $((80 + crc)); fi
exit "$rc"
