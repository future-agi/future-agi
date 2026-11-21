#!/usr/bin/env bash
# Decides what Update CI runs for this event, and prints `upgrade=<bool>`,
# `abc=<bool>` and `relevant=<bool>` for $GITHUB_OUTPUT.
#
# upgrade and abc are the two Real rehearsals. Each takes about three hours, so
# a pull request runs them only when it can change their result:
#
#   schedule, workflow_dispatch   both, always
#   pull request into main        both (a release)
#   label update-rehearsal        both (ask for them on any pull request)
#   update tooling changed        both
#   what the images build and     latest-stable -> PR only: it builds the pull
#   boot changed (migrations,     request and boots it on real data; a-b-c installs
#   Dockerfiles, entrypoints,     published releases and builds nothing from it
#   settings, boot-time schema)
#   anything else                 neither (push, merge_group, other pull requests)
#
# The first two pull-request rows hold whatever files the pull request
# touches: the workflow has no path filter for pull requests.
#
# relevant is whether the other jobs run. For a pull request it is whether its
# files match the list a push is filtered by; every other event that starts the
# workflow has already passed that list, or has none.
#
# Inputs, from the workflow: EVENT_NAME, BASE_REF, and for a pull request
# LABELLED, TOOLING_CHANGED, BUILT_CHANGED and RELEVANT_CHANGED, each "true" or
# "false". Anything else fails, and the workflow then runs every job.
set -euo pipefail

upgrade=false
abc=false
relevant=true
case "${EVENT_NAME:?EVENT_NAME is required}" in
  schedule | workflow_dispatch)
    upgrade=true abc=true why="every $EVENT_NAME run"
    ;;
  pull_request)
    for name in LABELLED TOOLING_CHANGED BUILT_CHANGED RELEVANT_CHANGED; do
      case "${!name:-}" in
        true | false) ;;
        *)
          echo "$name must be true or false for a pull request, got '${!name:-}'" >&2
          exit 1
          ;;
      esac
    done
    relevant=$RELEVANT_CHANGED
    if [[ "${BASE_REF:-}" == main ]]; then
      upgrade=true abc=true why="pull request into main"
    elif [[ "$LABELLED" == true ]]; then
      upgrade=true abc=true why="label update-rehearsal"
    elif [[ "$TOOLING_CHANGED" == true ]]; then
      upgrade=true abc=true why="update tooling changed"
    elif [[ "$BUILT_CHANGED" == true ]]; then
      upgrade=true why="a migration, image build, entrypoint, settings or boot-time schema file changed"
    else
      why="nothing a rehearsal builds or runs changed; add the update-rehearsal label and re-run this workflow to run them"
    fi
    ;;
  *)
    why="never on $EVENT_NAME"
    ;;
esac
echo "Real update rehearsals: latest-stable to PR=$upgrade, a-b-c=$abc ($why)" >&2
echo "The other Update CI jobs: $relevant" >&2
printf 'upgrade=%s\nabc=%s\nrelevant=%s\n' "$upgrade" "$abc" "$relevant"
