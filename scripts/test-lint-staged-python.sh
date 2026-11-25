#!/bin/sh
# A staged backend file must not be silently skipped when Ruff is unavailable.
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/scripts" "$tmp/futureagi"
cp "$root/scripts/lint-staged-python.sh" "$tmp/scripts/"
ln -s "$(command -v dirname)" "$tmp/bin/dirname"
bash=$(command -v bash)
cd "$tmp"
git init -q
printf 'value = 1\n' > futureagi/fixture.py
git add futureagi/fixture.py
status=0
output=$(PATH="$tmp/bin" "$bash" scripts/lint-staged-python.sh futureagi/fixture.py 2>&1) || status=$?
if [ "$status" -eq 0 ] || ! printf '%s\n' "$output" | grep -Eiq 'install.*uv.*ruff'; then
    printf 'Expected a failed Python check with installation guidance, got %s: %s\n' "$status" "$output" >&2
    exit 1
fi
