#!/usr/bin/env bash
# scripts/release/verify-local.sh
# The release-integrity checks that can be proven offline, on a laptop or in
# CI. Signing, provenance and checksum upload run only in CI
# (.github/workflows/release-agentcc-gateway.yml); this script checks only
# that the workflow pins every action by commit SHA and carries those steps.
#
#   1. go mod verify        the module cache matches go.sum
#   2. reproducible build   build-reproducible.sh: two builds, one sha256
#   3. sbom                 sbom.sh lists every module `go list -m all` lists
#   4. secrets lint         its fixtures behave, and the shipped configs
#                           (config*.yaml, scripts/scenarios/config/**) are clean
#   5. workflow             every `uses:` is owner/repo@<40-hex sha>, and the
#                           sbom, checksums, provenance and sign steps exist
#
# Every check runs and prints PASS, FAIL or BLOCKED (a module the Go commands
# need is not in the module cache and offline mode may not fetch it); the
# script exits 1 unless all pass. Go runs offline (GOPROXY=off,
# GOFLAGS=-mod=readonly) unless the caller sets GOPROXY or GOFLAGS; CI runs
# `go mod download all` first.
#
# Environment: VERIFY_KEEP=1 keeps the work directory (printed at the end).
set -uo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
GW=$(cd "$HERE/../.." && pwd)
REPO=$(cd "$GW/.." && pwd)
WORKFLOW="$REPO/.github/workflows/release-agentcc-gateway.yml"

[ -n "${GOPROXY+x}" ] || export GOPROXY=off
[ -n "${GOFLAGS+x}" ] || export GOFLAGS=-mod=readonly

WORK=$(mktemp -d "${TMPDIR:-/tmp}/agentcc-verify.XXXXXX")
# shellcheck disable=SC2317 # run by the EXIT trap
cleanup() {
  if [ -n "${VERIFY_KEEP:-}" ]; then echo "work dir kept: $WORK"; else rm -rf "$WORK"; fi
}
trap cleanup EXIT

declare -a RESULTS=()
MOD_GRAPH_OK=""
record() { # status name detail
  RESULTS+=("$(printf '%-8s %-20s %s' "$1" "$2" "$3")")
  echo "--> $1 $2${3:+: $3}"
}

# blocked_or_fail <log>: BLOCKED when a Go command stopped at a module the
# offline cache lacks, else FAIL. Prints "STATUS<TAB>detail".
blocked_or_fail() {
  local missing
  missing=$(grep -oE '[^ :]+@v[^:]+: module lookup disabled by GOPROXY=off' "$1" | head -1 | cut -d: -f1)
  if [ -n "$missing" ]; then
    printf 'BLOCKED\t%s is not in the module cache (GOPROXY=off); fetch it once with network\n' "$missing"
  else
    printf 'FAIL\t%s\n' "$(grep -v '^[[:space:]]*$' "$1" | tail -1)"
  fi
}

check_mod_verify() {
  echo; echo "=== 1. go mod verify"
  local log="$WORK/mod-verify.log" verdict
  if (cd "$GW" && go mod verify) >"$log" 2>&1; then
    MOD_GRAPH_OK=1
    record PASS "go mod verify" "$(tail -1 "$log")"
  else
    verdict=$(blocked_or_fail "$log")
    record "${verdict%%$'\t'*}" "go mod verify" "${verdict#*$'\t'}"
  fi
  cat "$log"
}

check_reproducible() {
  echo; echo "=== 2. reproducible build"
  local log="$WORK/build.log"
  if bash "$HERE/build-reproducible.sh" --out "$WORK/dist" 2>&1 | tee "$log"; then
    record PASS "reproducible build" "$(grep '^REPRODUCIBLE' "$log" | tail -1)"
  else
    record FAIL "reproducible build" "$(tail -1 "$log")"
  fi
}

check_sbom() {
  echo; echo "=== 3. sbom"
  local sbom="$WORK/agentcc-gateway.cdx.json" bin list_log="$WORK/go-list.log" verdict
  bin=$(find "$WORK/dist" -type f -name 'agentcc-gateway_*' ! -name '*.sha256' 2>/dev/null | head -1)
  if ! bash "$HERE/sbom.sh" ${bin:+--binary "$bin"} --out "$sbom"; then
    record FAIL sbom "sbom.sh failed"
    return
  fi
  # `go list -m all` reads every listed module's go.mod for its go version,
  # and offline the cache may lack some of them; then the same build list
  # comes from `go list -m -e all`, provided lookups are all that failed.
  local list_cmd="go list -m all"
  if ! (cd "$GW" && go list -m all) >"$WORK/go-list.txt" 2>"$list_log"; then
    if grep -v 'module lookup disabled by GOPROXY=off' "$list_log" | grep -q .; then
      record FAIL sbom "go list -m all: $(tail -1 "$list_log")"
      return
    fi
    # -e lists the whole build list only if the module graph loaded, which
    # check 1 proved (go mod verify loads the full graph).
    if [ -z "$MOD_GRAPH_OK" ]; then
      record BLOCKED sbom "go list -m all needs modules the offline cache lacks, and go mod verify did not load the graph"
      return
    fi
    list_cmd="go list -m -e all"
    echo "sbom: go list -m all made $(grep -c 'module lookup disabled' "$list_log") module lookups the offline cache cannot answer (each module's go version); using $list_cmd"
    (cd "$GW" && go list -m -e all) >"$WORK/go-list.txt" 2>/dev/null
  fi
  if python3 -I - "$sbom" "$WORK/go-list.txt" "$list_cmd" <<'PY'; then
import json, sys
bom = json.load(open(sys.argv[1]))
listed = [l.split() for l in open(sys.argv[2]) if l.strip()]
main, deps = listed[0][0], {(p[0], p[1]) for p in listed[1:]}
have = {(c["name"], c["version"]) for c in bom["components"]}
missing = sorted(deps - have)
print("sbom: %d components; %s: %d modules (%d plus %s)"
      % (len(have), sys.argv[3], len(listed), len(deps), main))
if bom["metadata"]["component"]["name"] != main:
    sys.exit("sbom: metadata.component is not the main module %s" % main)
if len(have) < len(deps) or missing:
    sys.exit("sbom: missing %s" % ", ".join("%s@%s" % m for m in missing))
PY
    record PASS sbom "every module of $list_cmd is listed"
  else
    record FAIL sbom "the SBOM does not list every module $list_cmd lists"
  fi
}

check_secrets() {
  echo; echo "=== 4. secrets lint"
  local out
  out=$(cd "$HERE" && bash ./secrets-lint.sh testdata/leaky.yaml)
  if [ $? -ne 1 ] || [ "$out" != "$(cat "$HERE/testdata/leaky.expected")" ]; then
    record FAIL "secrets lint" "testdata/leaky.yaml does not give testdata/leaky.expected"
    diff <(echo "$out") "$HERE/testdata/leaky.expected"
    return
  fi
  if ! (cd "$HERE" && bash ./secrets-lint.sh testdata/clean.yaml); then
    record FAIL "secrets lint" "testdata/clean.yaml is reported"
    return
  fi
  local files=() f
  while IFS= read -r f; do files+=("${f#"$REPO"/}"); done < <(
    find "$GW" -maxdepth 1 -type f -name 'config*.yaml'
    find "$GW/scripts/scenarios/config" -type f
  )
  if [ ${#files[@]} -eq 0 ]; then
    record FAIL "secrets lint" "no shipped configs found"
  elif (cd "$REPO" && bash "$HERE/secrets-lint.sh" "${files[@]}"); then
    record PASS "secrets lint" "fixtures behave; ${#files[@]} shipped configs clean"
  else
    record FAIL "secrets lint" "a shipped config holds a key-shaped value (above)"
  fi
}

# lint_workflow <file>: the workflow rules; prints one line per break and
# exits 1 if there is any.
lint_workflow() {
  python3 -I - "$1" <<'PY'
import re, sys
path = sys.argv[1]
lines = open(path, encoding="utf-8").read().splitlines()
errors = []
PINNED = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(/[^@\s]+)?@[0-9a-f]{40}$")
USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(.*?)\s*(?:#.*)?$")
uses, local = [], []
for n, line in enumerate(lines, 1):
    m = USES.match(line)
    if not m:
        continue
    ref = m.group(1).strip("'\"")
    if ref.startswith("./"):
        # A reusable workflow in this repository runs at the caller's own
        # commit: nothing to pin. Its own `uses:` lines are reported below.
        local.append(ref)
    elif PINNED.match(ref):
        uses.append(ref)
    else:
        errors.append("line %d: `uses: %s` is not pinned to a full commit SHA" % (n, ref))
if not uses:
    errors.append("no SHA-pinned `uses:` lines")
root = path.rsplit("/.github/", 1)[0]
for ref in local:
    try:
        called = open("%s/%s" % (root, ref[2:]), encoding="utf-8").read().splitlines()
    except OSError:
        errors.append("`uses: %s`: no such file" % ref)
        continue
    refs = {m.group(1).strip("'\"") for m in map(USES.match, called) if m}
    loose = sorted(r for r in refs if not r.startswith("./") and not PINNED.match(r))
    if loose:
        print("workflow: NOTE %s (called, not owned here) pins by tag: %s" % (ref, ", ".join(loose)))

def step(step_id):
    """The text of the step whose `id:` is step_id, up to the next step."""
    for n, line in enumerate(lines):
        m = re.match(r"^(\s*)(-\s+)?id:\s*%s\s*$" % re.escape(step_id), line)
        if not m:
            continue
        start = n
        while start > 0 and not re.match(r"^\s*-\s", lines[start]):
            start -= 1
        indent = len(lines[start]) - len(lines[start].lstrip())
        end = start + 1
        while end < len(lines) and not (
            lines[end].strip() and (len(lines[end]) - len(lines[end].lstrip())) <= indent
        ):
            end += 1
        return "\n".join(lines[start:end])
    return None

REQUIRED = {
    "sbom": "scripts/release/sbom.sh",
    "checksums": "sha256sum",
    "provenance": "cosign attest",
    "sign": "cosign sign",
}
for step_id, needle in REQUIRED.items():
    text = step(step_id)
    if text is None:
        errors.append("no step with `id: %s`" % step_id)
    elif needle not in text:
        errors.append("step `%s` does not run `%s`" % (step_id, needle))
if not re.search(r"^\s*id-token:\s*write\s*(#.*)?$", "\n".join(lines), re.M):
    errors.append("no job grants `id-token: write` (keyless signing needs it)")
for e in errors:
    print("workflow: " + e)
if not errors:
    print("workflow: %d `uses:` lines pinned by SHA, %d local reusable workflow call(s)" % (len(uses), len(local)))
sys.exit(1 if errors else 0)
PY
}

check_workflow() {
  echo; echo "=== 5. release workflow"
  local out
  out=$(cd "$HERE" && lint_workflow testdata/unpinned-workflow.yml)
  if [ $? -ne 1 ] || [ "$out" != "$(cat "$HERE/testdata/unpinned-workflow.expected")" ]; then
    record FAIL workflow "testdata/unpinned-workflow.yml does not give testdata/unpinned-workflow.expected"
    diff <(echo "$out") "$HERE/testdata/unpinned-workflow.expected"
    return
  fi
  if [ ! -f "$WORKFLOW" ]; then
    record FAIL workflow "missing ${WORKFLOW#"$REPO"/}"
  elif lint_workflow "$WORKFLOW"; then
    record PASS workflow "fixture caught; actions pinned by SHA; sbom, checksums, provenance and sign steps present"
  else
    record FAIL workflow "see above"
  fi
}

echo "verify-local: GOPROXY=$GOPROXY GOFLAGS=$GOFLAGS ($(cd "$GW" && go version 2>&1))"
check_mod_verify
check_reproducible
check_sbom
check_secrets
check_workflow

echo
echo "=== verify-local summary"
status=0
for line in "${RESULTS[@]}"; do
  echo "$line"
  case "$line" in PASS*) ;; *) status=1 ;; esac
done
[ "$status" -eq 0 ] && echo "verify-local: all checks pass" || echo "verify-local: NOT all checks pass"
exit "$status"
