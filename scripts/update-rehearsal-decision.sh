#!/usr/bin/env bash
# Decides whether Update CI runs its two Real rehearsals for this event, and
# prints `upgrade=<bool>` and `abc=<bool>` for $GITHUB_OUTPUT. Each rehearsal
# takes about three hours, so a pull request runs them only when it can change
# their result:
#
#   schedule, workflow_dispatch, push to dev   both, always
#   pull request into main        both (a release)
#   label update-rehearsal        both (ask for them on any pull request)
#   update tooling changed        both
#   what the images build and     latest-stable -> PR only: it builds the pull
#   boot changed (migrations,     request and boots it on real data; a-b-c installs
#   Dockerfiles, entrypoints,     published releases and builds nothing from it
#   settings)
#   anything else                 neither (main push, merge_group, other pull requests)
#
# Inputs, from the workflow: EVENT_NAME, BASE_REF, and for a pull request
# LABELLED, TOOLING_CHANGED and BUILT_CHANGED, each "true" or "false".
set -euo pipefail

upgrade=false
abc=false
case "${EVENT_NAME:?EVENT_NAME is required}" in
  schedule | workflow_dispatch)
    upgrade=true abc=true why="every $EVENT_NAME run"
    ;;
  push)
    why="push outside dev"
    if [[ ${BASE_REF:-} == dev ]]; then upgrade=true abc=true why="full dev control"; fi
    ;;
  pull_request)
    for name in LABELLED TOOLING_CHANGED BUILT_CHANGED; do
      case "${!name:-}" in
        true | false) ;;
        *)
          echo "$name must be true or false for a pull request, got '${!name:-}'" >&2
          exit 1
          ;;
      esac
    done
    if [[ "${BASE_REF:-}" == main ]]; then
      upgrade=true abc=true why="pull request into main"
    elif [[ "$LABELLED" == true ]]; then
      upgrade=true abc=true why="label update-rehearsal"
    elif [[ "$TOOLING_CHANGED" == true ]]; then
      upgrade=true abc=true why="update tooling changed"
    elif [[ "$BUILT_CHANGED" == true ]]; then
      upgrade=true why="a migration, image build, entrypoint or settings file changed"
    else
      why="nothing a rehearsal builds or runs changed; add the update-rehearsal label and re-run this workflow to run them"
    fi
    ;;
  *)
    why="never on $EVENT_NAME"
    ;;
esac
echo "Real update rehearsals: latest-stable to PR=$upgrade, a-b-c=$abc ($why)" >&2
printf 'upgrade=%s\nabc=%s\n' "$upgrade" "$abc"
