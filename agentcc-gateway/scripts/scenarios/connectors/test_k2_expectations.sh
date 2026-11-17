#!/usr/bin/env bash
# Synthetic resolver regression: no binaries or vendor endpoints.
set -euo pipefail
source "${1:-$(dirname "$0")/lib_k2_replay.sh}"
work=$(mktemp -d)
mkdir -p "$work/catalog/apps/example/providers/rest/fixtures" "$work/common"
owned=$work/catalog/apps/example/providers/rest/fixtures/replay-expectations.json
shared=$work/common/example.json
printf '{"tools":2,"drive_checks":[]}\n' > "$owned"
[ "$(k2_expectations "$work/catalog" example rest "$work/common")" = "$owned" ]
printf '{"drive_checks":[],"tools":2}\n' > "$shared"
[ "$(k2_expectations "$work/catalog" example rest "$work/common")" = "$owned" ]
printf '{"tools":3,"drive_checks":[]}\n' > "$shared"
if k2_expectations "$work/catalog" example rest "$work/common"; then echo 'conflicting expectations accepted' >&2; exit 1; fi
mv "$owned" "$work/owned.saved"
[ "$(k2_expectations "$work/catalog" example rest "$work/common")" = "$shared" ]
mv "$shared" "$work/shared.saved"
if k2_expectations "$work/catalog" example rest "$work/common"; then echo 'missing expectations accepted' >&2; exit 1; fi
echo 'PASS app-owned, equal duplicate, conflicting duplicate, fallback and missing expectations'
