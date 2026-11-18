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
zero=$(printf '%s' "$seed" | tr '0-9a-f' '0')
fail=0
check() {
    expected=$1 label=$2
    actual=0
    "$shell" "$hook" >/dev/null || actual=$?
    if [ "$actual" -ne "$expected" ]; then
        printf '%s: %s: expected exit %s, got %s\n' "$shell" "$label" "$expected" "$actual" >&2
        fail=1
    fi
}
while read -r expected branch; do
    git checkout -q -B "$branch" "$seed"
    check "$expected" "$branch" <<EOF
refs/heads/$branch $seed refs/heads/$branch $zero
EOF
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

git checkout -q --detach "$seed"
check 0 'detached HEAD to valid branch' <<EOF
HEAD $seed refs/heads/fix/valid $zero
EOF
check 0 'tag, deletion and protected branch' <<EOF
refs/tags/v1 $seed refs/tags/v1 $zero
(delete) $zero refs/heads/legacy $seed
HEAD $seed refs/heads/main $zero
EOF
check 0 'no updates' </dev/null

git checkout -q fix/0
check 1 'valid checkout to invalid destination' <<EOF
refs/heads/fix/0 $seed refs/heads/feature/bad $zero
EOF
check 1 'invalid destination after protected branch' <<EOF
HEAD $seed refs/heads/dev $seed
HEAD $seed refs/heads/feature/bad $zero
EOF
exit "$fail"
