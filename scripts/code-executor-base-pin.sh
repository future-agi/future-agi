#!/usr/bin/env bash
set -euo pipefail

# Print the futureagi/code-executor-base version (vX.Y.Z[-suffix]) that
# futureagi/code-executor/Dockerfile pins in ARG CODE_EXECUTOR_BASE, or fail
# with a GitHub Actions error when the pin is not an immutable version tag.
# backend-ci.yml checks that a changed base comes with an unpublished pin;
# release-images.yml publishes the pinned tag when it does not exist yet.
#
# Usage: scripts/code-executor-base-pin.sh [Dockerfile]

dockerfile="${1:-futureagi/code-executor/Dockerfile}"
ref=$(sed -nE 's/^[[:space:]]*ARG[[:space:]]+CODE_EXECUTOR_BASE=([^[:space:]]+).*/\1/p' \
  "$dockerfile" | head -n1)
re='^futureagi/code-executor-base:(v[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9.]+)?)(@sha256:[a-f0-9]{64})?$'
if [[ ! "$ref" =~ $re ]]; then
  echo "::error file=${dockerfile}::pin ARG CODE_EXECUTOR_BASE=futureagi/code-executor-base:vX.Y.Z[@sha256:...] (found '${ref}')" >&2
  exit 1
fi
printf '%s\n' "${BASH_REMATCH[1]}"
