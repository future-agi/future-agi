#!/usr/bin/env bash
# Prints the commit that scripts/check_update_safety.py compares the checkout
# with (its --base). The gate reads what changed since that commit and refuses
# an edited or deleted migration, so a wrong base fails a clean tree.
#
#   pull_request  the pull request's base commit
#   push          the commit the branch pointed at before the push
#   merge_group   the commit the merge group is built on
#   anything else origin/main, the released line
#
# "Anything else" is a scheduled run, a manual dispatch, and the all-zero
# "before" of a newly pushed branch. It must not be origin/dev: a scheduled run
# checks out the default branch (main), and against dev's history a release
# fix that exists only on main reads as a changed migration.
set -euo pipefail

case "${EVENT_NAME:?EVENT_NAME is required}" in
  pull_request) base=${PR_BASE:-} ;;
  push) base=${PUSH_BEFORE:-} ;;
  merge_group) base=${MERGE_GROUP_BASE:-} ;;
  *) base= ;;
esac
if [[ "$EVENT_NAME" == pull_request || "$EVENT_NAME" == merge_group ]]; then
  if ! [[ "$base" =~ ^[0-9a-f]{40}$ ]] || [[ "$base" =~ ^0+$ ]]; then
    echo "Missing immutable comparison base for $EVENT_NAME" >&2
    exit 1
  fi
elif [[ -z "$base" || "$base" =~ ^0+$ ]]; then
  base=origin/main
fi
printf '%s\n' "$base"
