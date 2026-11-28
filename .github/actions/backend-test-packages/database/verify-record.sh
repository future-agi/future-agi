#!/usr/bin/env bash
# Validate a complete saved-dump record for this exact recipe, or fail. Never
# use paths from a manifest as inputs to sha256sum.
set -euo pipefail
cd "${1:?record directory}"
recipe=${2:?recipe key}
shopt -s dotglob nullglob
entries=(*)
for entry in "${entries[@]}"; do [[ -f $entry && ! -L $entry ]]; done
(( ${#entries[@]} == 4 ))
[[ $(cat recipe) == "$recipe" ]]
sha256sum dump.sql.gz preseed recipe | cmp - checksums
grep -Eq '^[0-9a-f]{64}$' preseed
