#!/usr/bin/env bash
# Run a registry pull and retry it only on a known infrastructure failure.
#
#   registry-retry.sh <label> <command...>
#
# The command's output is classified with .github/ci-infra-signatures.json:
#   rate_limit  retried after 30, 60, then 120 s;
#   transient   retried after 10, 20, then 30 s;
#   permanent   (auth, tag, configuration, any other 4xx) fails at once;
#   anything else fails at once, so an unknown error is never retried.
# A rate limit is checked first: its 429 also matches the permanent 4xx rule.
# At most REGISTRY_RETRY_ATTEMPTS attempts (default 4). The command's exit
# status is the script's. Wrap only pulls, never `up`: a retried `up` could
# hide a service that crashed.
set -uo pipefail

if (($# < 2)); then
  echo "usage: registry-retry.sh <label> <command...>" >&2
  exit 2
fi
label=$1
shift
signatures="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/ci-infra-signatures.json"
pattern() {
  jq -er --arg class "$1" '[.[] | select(.class == $class) | .regex] | if length > 0 then join("|") else error("no \($class) signature") end' "$signatures"
}
if ! rate_limit=$(pattern rate_limit) || ! transient=$(pattern transient) || ! permanent=$(pattern permanent); then
  echo "::error::$label: could not read the signatures in $signatures."
  exit 2
fi
attempts=${REGISTRY_RETRY_ATTEMPTS:-4}
if ! [[ $attempts =~ ^[1-9][0-9]*$ ]]; then
  echo "::error::$label: REGISTRY_RETRY_ATTEMPTS must be a positive integer, not '$attempts'."
  exit 2
fi
log=$(mktemp)
trap 'rm -f "$log"' EXIT

for ((attempt = 1; ; attempt++)); do
  status=0
  "$@" > "$log" 2>&1 || status=$?
  cat "$log"
  if ((status == 0)); then
    exit 0
  fi
  if grep -Eiq "$rate_limit" "$log"; then
    kind="registry rate limit" delay=$((30 << (attempt - 1)))
  elif grep -Eiq "$permanent" "$log"; then
    kind=""
  elif grep -Eiq "$transient" "$log"; then
    kind="transient registry failure" delay=$((attempt * 10))
  else
    kind=""
  fi
  if [[ -z $kind ]] || ((attempt >= attempts)); then
    echo "::error::$label failed (attempt $attempt/$attempts, exit $status)${kind:+ after a $kind}."
    exit "$status"
  fi
  echo "$label: $kind on attempt $attempt/$attempts; retrying in ${delay}s."
  sleep "$delay"
done
