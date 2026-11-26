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
check 1 'source with spaces to invalid destination' <<EOF
HEAD@{1 second ago} $seed refs/heads/feature/bad $zero
EOF
check 0 'source with spaces to valid destination' <<EOF
HEAD@{1 second ago} $seed refs/heads/fix/valid $zero
EOF
check 1 'source with spaces naming a branch-like word' <<EOF
fix/ok@{2 days  ago} $seed refs/heads/plain-name $zero
EOF
check 1 'unreadable update line' <<EOF
refs/heads/fix/0 $seed
EOF

# Real pushes through the installed hook: the same commit named by its ID and
# by a source expression containing spaces must get the same decision.
remote=$(mktemp -d)
hooks=$(mktemp -d)
trap 'rm -rf "$repo" "$remote" "$hooks"' EXIT
git init -q --bare "$remote"
printf '#!/bin/sh\nexec %s %s "$@"\n' "$shell" "$hook" >"$hooks/pre-push"
chmod +x "$hooks/pre-push"
push() {
    expected=$1 label=$2 source=$3 branch=$4
    actual=0
    git -c core.hooksPath="$hooks" push -q "$remote" "$source:refs/heads/$branch" \
        >/dev/null 2>&1 || actual=$?
    [ "$actual" -eq 0 ] || actual=1
    present=1
    git --git-dir="$remote" rev-parse -q --verify "refs/heads/$branch" >/dev/null || present=0
    if [ "$actual" -ne "$expected" ] || [ "$present" -ne $((1 - expected)) ]; then
        printf '%s: real push %s: expected exit %s, got %s (remote ref present: %s)\n' \
            "$shell" "$label" "$expected" "$actual" "$present" >&2
        fail=1
    fi
}
push 0 'by ID to valid branch' "$seed" fix/by-id
push 1 'by ID to invalid branch' "$seed" feature/by-id
push 0 'by dated expression to valid branch' 'HEAD@{0 seconds ago}' fix/by-date
push 1 'by dated expression to invalid branch' 'HEAD@{0 seconds ago}' feature/by-date
push 1 'by message search to invalid branch' 'HEAD^{/Seed branch-name test}' feature/by-text
push 1 'by expression with a tab to invalid branch' "$(printf 'HEAD@{0\tseconds ago}')" feature/by-tab
push 1 'by expression with a newline to invalid branch' "$(printf 'HEAD@{0\nseconds ago}')" feature/by-line
push 0 'by message search to valid branch' 'HEAD^{/Seed branch-name test}' fix/by-text
exit "$fail"
