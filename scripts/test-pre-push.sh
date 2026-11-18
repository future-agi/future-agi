#!/bin/sh
# Run with `sh scripts/test-pre-push.sh [shell]` to check the actual hook.
set -eu
shell=${1:-sh}
hook=$(cd "$(dirname "$0")/.." && pwd)/.husky/pre-push
repo=$(mktemp -d)
trap 'rm -rf "$repo"' EXIT
git init -q "$repo"
cd "$repo"
git -c user.name=Test -c user.email=test@example.invalid -c core.hooksPath=/dev/null \
    commit -q --allow-empty -m 'Seed branch-name test'
seed=$(git rev-parse HEAD)
fail=0
while read -r expected branch; do
    git checkout -q -B "$branch" "$seed"
    actual=0
    "$shell" "$hook" >/dev/null || actual=$?
    if [ "$actual" -ne "$expected" ]; then
        printf '%s: %s: expected exit %s, got %s\n' "$shell" "$branch" "$expected" "$actual" >&2
        fail=1
    fi
done <<'CASES'
0 main
0 master
0 develop
0 dev
0 feat/AUTH-123-user-login
0 fix/0
0 docs/v2
0 refactor/cleanup-utils
0 test/A--Z
0 perf/PERF-500-list-pagination
0 chore/7D
1 feature/new-panel
1 ci/build
1 Fix/example
1 plain-name
1 fix/-start
1 fix/end-
1 fix/a_b
1 fix/x.y
1 feat/sub/path
1 dev-extra
CASES
exit "$fail"
