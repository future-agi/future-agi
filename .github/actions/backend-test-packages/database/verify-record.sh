#!/usr/bin/env bash
# Validate the complete cache/artifact record. Never use paths from a manifest
# as inputs to sha256sum. stdout is just the resulting eligibility flag.
set -euo pipefail
cd "${1:?record directory}"
recipe=${2:?recipe key}
shopt -s dotglob nullglob
entries=(*)
for entry in "${entries[@]}"; do [[ -f $entry && ! -L $entry ]]; done
if [[ -f cold-only ]]; then
  [[ $(cat cold-only) == "$recipe" ]]
  (( ${#entries[@]} == 1 ))
  echo false
else
  [[ $(cat recipe) == "$recipe" ]]
  (( ${#entries[@]} == 4 ))
  sha256sum dump.sql.gz preseed recipe | cmp - checksums
  grep -Eq '^[0-9a-f]{64}$' preseed
  echo true
fi
