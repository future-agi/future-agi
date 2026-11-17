#!/usr/bin/env bash
# The LANDING gate of one walk launch (walks/launch/launch.sh), on
# the VM. It runs the job's command, then decides what may leave the VM.
#
# The launcher pulls ONLY e2e/walk-landed/<launch-id>/... . That directory
# appears once, at the very end, by a single rename of a directory this script
# assembled and verified. It holds:
#   - copies of the files the LANDS globs match, byte-identical to what the
#     independent auditor read (e2e/scripts/evidence-audit.mjs --stage), and
#     ONLY when the audit completed and was clean, or refused with EVERY
#     refused file quarantined outside the tree (then the clean rest lands);
#   - e2e/walk-results/command/command-exit.txt: the command's true exit
#     code; 86 when its evidence was refused; 87 when the gate itself failed
#     (an auditor crash, a quarantine or staging that did not complete, a
#     report that does not verify). A failing command keeps its own code.
#   - e2e/walk-results/command/evidence-audit.json: the auditor's value-free
#     report once a SEPARATE check passed (evidence-audit.mjs --check-report:
#     shape whitelist, this launch's nonce, a fresh audit of the report's
#     bytes), or else a constants-only stub written by this script.
# Before the rename, a fresh auditor process audits everything that is about
# to land. On ANY failure on the way, only command-exit.txt and the verified
# report (or the stub) land: the original evidence is never reachable by a
# pull pattern, whatever the auditor or the quarantine did. Raw evidence and
# the quarantine stay on the VM. A reused lease VM cannot hand back an older
# landing: the launch id is new per launch, and an existing landing or work
# directory for it refuses the run.
# Exit 0/1 only; the true code is the landed command-exit.txt.
#
# Usage (cwd = the repo root): land.sh <launch-id> <n> <glob x n> -- <command...>
set -u
ID=${1:-}
N=${2:-}
if [[ ! $ID =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ || ! $N =~ ^[0-9]+$ ]] || (($# < N + 3)); then
  echo "usage: land.sh <launch-id> <n> <glob x n> -- <command...>" >&2
  exit 1
fi
shift 2
GLOBS=("${@:1:N}")
shift "$N"
[[ ${1:-} == -- ]] || { echo "land.sh: expected -- before the command" >&2; exit 1; }
shift
AUDITOR=e2e/scripts/evidence-audit.mjs
LANDED=e2e/walk-landed
LANDING=$LANDED/$ID
WORK=$LANDED/.work-$ID
QUARANTINE=${HOME:?}/evidence-refused/$ID
REFUSED=86
GATE_FAILED=87

if [[ -e $LANDING || -e $WORK ]]; then
  echo "land.sh: $LANDING or its work directory exists already; nothing lands" >&2
  exit 1
fi
mkdir -p "$WORK" || exit 1

"$@"
rc=$?

failure_code() { # the command's own failure wins over the gate's
  if [ "$rc" -ne 0 ]; then echo "$rc"; else echo "$1"; fi
}
stub() { # <dir> <reason> <auditor exit>: a report made of constants only
  mkdir -p "$1/e2e/walk-results/command" &&
    printf '{"rule":"708 add.78","auditor":"e2e/scripts/evidence-audit","nonce":"%s","refused":true,"reason":"%s","auditorExit":%d}\n' \
      "$ID" "$2" "$3" > "$1/e2e/walk-results/command/evidence-audit.json"
}

# 1. One auditor run over every file the globs match: audit, quarantine the
#    refused files, stage the clean ones (fresh directory, re-hashed copies).
args=()
for g in "${GLOBS[@]}"; do args+=(--glob "$g"); done
node "$AUDITOR" --root . --nonce "$ID" --report "$WORK/audit.json" --quarantine "$QUARANTINE" \
  --stage "$WORK/final" "${args[@]}" > "$WORK/audit.log" 2>&1
audit=$?
# 2. A separate check of the report it wrote (shape, nonce, its own audit).
state=$(node "$AUDITOR" --check-report "$WORK/audit.json" --nonce "$ID" 2>> "$WORK/audit.log") || state=invalid
case "$audit:$state" in
  0:clean) code=$rc OUT=$WORK/final ;;
  1:refused) code=$(failure_code "$REFUSED") OUT=$WORK/final ;;
  *) code=$(failure_code "$GATE_FAILED") OUT=$WORK/refusal ;;
esac
mkdir -p "$OUT/e2e/walk-results/command"
case "$state" in
  clean | refused | incomplete) cp "$WORK/audit.json" "$OUT/e2e/walk-results/command/evidence-audit.json" ;;
  *) stub "$OUT" auditor-report-invalid "$audit" ;;
esac
echo "$code" > "$OUT/e2e/walk-results/command/command-exit.txt"

# 3. A fresh auditor process over everything about to land.
node "$AUDITOR" --root "$OUT" --nonce "$ID.final" --report "$WORK/final-audit.json" "$OUT" \
  > "$WORK/final-audit.log" 2>&1
final=$?
fstate=$(node "$AUDITOR" --check-report "$WORK/final-audit.json" --nonce "$ID.final" 2>> "$WORK/final-audit.log") ||
  fstate=invalid
if [[ $final != 0 || $fstate != clean ]]; then
  code=$(failure_code "$GATE_FAILED")
  OUT=$WORK/stub
  stub "$OUT" landing-check-failed "$final" || exit 1
  echo "$code" > "$OUT/e2e/walk-results/command/command-exit.txt" || exit 1
fi

# 4. One rename: the landing directory appears complete or not at all.
mv "$OUT" "$LANDING" || exit 1
echo "land.sh: landed $LANDING (state=$state final=$fstate code=$code)"
[ "$code" -eq 0 ] && exit 0 || exit 1
