#!/usr/bin/env bash
# The base a pull_request run actually tested: its test merge's first parent.
#
#   ci-pr-inputs.sh check BASE   # in the checked-out test merge; prints the first parent
#   ci-pr-inputs.sh self-test    # fixture repository: both lag directions
#
# GitHub does not keep the event's pull_request.base.sha equal to the test
# merge's first parent, and either can lag the other:
#   - base.sha lags: the event keeps the base tip of the PR's last base sync
#     while the merge ref already uses the newer tip (#4231: c735cc1f vs
#     640b34b6, 12 commits ahead).
#   - the merge ref lags: the event names the new base tip while the merge ref
#     was still computed on an older one (#4114: 1cca6b23 vs 5f99ae97, 2 behind).
# Both are commits of the same base branch, so one must be an ancestor of the
# other. Anything else is not this pull request's base and fails the run.
set -euo pipefail

check() {
  local base=$1 parent
  parent=$(git rev-parse HEAD^1)
  if ! git merge-base --is-ancestor "$base" "$parent" &&
    ! git merge-base --is-ancestor "$parent" "$base"; then
    echo "::error title=PR inputs::base $base and the test merge's first parent $parent are not on one line of history" >&2
    return 1
  fi
  printf '%s\n' "$parent"
}

self_test() {
  local repo
  repo=$(mktemp -d)
  trap 'rm -rf "$repo"' RETURN
  g() { git -C "$repo" -c user.name=ci -c user.email=ci@example.invalid -c commit.gpgsign=false "$@"; }
  commit() { g commit -q --allow-empty -m "$1" && g rev-parse HEAD; }
  merge() { g checkout -q --detach "$1" && g merge -q --no-ff --no-edit "$2" >/dev/null && g rev-parse HEAD; }
  expect() { # name, merge, base, wanted first parent or "fail"
    local got status=0
    g checkout -q --detach "$2"
    got=$(cd "$repo" && check "$3" 2>/dev/null) || status=$?
    if [[ $4 == fail ]]; then
      [[ $status != 0 ]] || { echo "self-test $1: accepted an unrelated base" >&2; return 1; }
    else
      [[ $status == 0 && $got == "$4" ]] || { echo "self-test $1: got '$got' (exit $status), wanted $4" >&2; return 1; }
    fi
  }
  g init -q -b base
  local b0 b1 b2 head side exact base_lags ref_lags unrelated
  b0=$(commit b0)
  g checkout -q -b feature
  head=$(commit head)
  g checkout -q -b side "$b0"
  side=$(commit side)
  g checkout -q base
  b1=$(commit b1)
  b2=$(commit b2)
  exact=$(merge "$b2" "$head")
  base_lags=$exact     # the event names b1, the merge already uses b2 (#4231)
  ref_lags=$(merge "$b1" "$head")   # the event names b2, the merge used b1 (#4114)
  unrelated=$(merge "$side" "$head")
  expect exact "$exact" "$b2" "$b2"
  expect base-sha-lags "$base_lags" "$b1" "$b2"
  expect merge-ref-lags "$ref_lags" "$b2" "$b1"
  expect unrelated-first-parent "$unrelated" "$b2" fail
  expect missing-base "$exact" 0000000000000000000000000000000000000001 fail
  echo "ci-pr-inputs self-test: 5 cases passed"
}

case ${1:-} in
  check) check "${2:?base sha}" ;;
  self-test) self_test ;;
  *) echo "usage: $0 check BASE | self-test" >&2; exit 2 ;;
esac
