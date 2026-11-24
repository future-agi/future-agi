#!/usr/bin/env bash
# Prints the commit that scripts/check_update_safety.py compares the checkout
# with (its --base). The gate reads what changed since that commit and refuses
# an edited or deleted migration, so a wrong base fails a clean tree, and a
# base equal to the checkout hides every changed migration and changed SQL
# source (the checks of the whole tree still run).
#
#   pull_request  the pull request's base commit
#   push          the commit the branch pointed at before the push
#   merge_group   the commit the merge group is built on
#   anything else the released line (below)
#
# "Anything else" is a scheduled run, a manual dispatch, and the all-zero
# "before" of a newly pushed branch. It must not be origin/dev: a scheduled run
# checks out the default branch (main), and against dev's history a release
# fix that exists only on main reads as a changed migration.
#
# The released line is origin/main, unless the checkout is itself a commit of
# origin/main. Then origin/main holds nothing the checkout lacks, the diff is
# empty, and the change checks (migrations, ClickHouse SQL) see nothing. That
# is every scheduled run. Such a run compares with the newest exact release
# tag (vX.Y.Z, not a preview) in the checkout's history that is not the
# checkout itself: what the release changed. With no such tag the script
# fails: there is no honest base.
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
  if git merge-base --is-ancestor HEAD origin/main; then
    head=$(git rev-parse 'HEAD^{commit}')
    base=
    # Only an exact vX.Y.Z is a release: a tag glob also matches preview
    # builds such as v1.33.1-pr2483.11b8626a0, which sort above v1.33.0.
    while IFS= read -r tag; do
      [[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || continue
      if [[ "$(git rev-parse "${tag}^{commit}")" != "$head" ]]; then
        base=$tag
        break
      fi
    done < <(git tag --list 'v[0-9]*' --merged HEAD --sort=-version:refname)
    if [[ -z "$base" ]]; then
      echo "update-safety-base: the checkout is a commit of origin/main and no release tag (vX.Y.Z) lies before it; fetch tags and full history" >&2
      exit 1
    fi
  fi
fi
printf '%s\n' "$base"
