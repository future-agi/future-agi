#!/bin/sh
# Build omega-hub-runner:<commit7> from an Omega source tree exported at one commit (SPEC §4.4):
#
#   C=<commit>; D=$TMPDIR/omega-hub-src-$(printf %.9s "$C")
#   mkdir -p "$D" && git -C <omega checkout> archive "$C" | tar -x -C "$D"
#   services/omega-hub-checker/runner/build.sh "$D" "$C"
#
# Prints the image tag and digest, and writes them to runner/.built (git-ignored), or to
# $OMEGA_HUB_RUNNER_BUILT when set (scripts/omega-local keeps the served checkout unwritten).
set -eu
SRC=${1:?usage: build.sh <exported omega dir> <commit>}
COMMIT=${2:?usage: build.sh <exported omega dir> <commit>}
case "$COMMIT" in *[!0-9a-f]*|"") echo "commit must be hex" >&2; exit 2;; esac
[ -f "$SRC/pnpm-workspace.yaml" ] || { echo "$SRC is not an exported Omega tree" >&2; exit 2; }
: "${DOCKER_CONTEXT:=colima}"
export DOCKER_CONTEXT
HERE=$(cd "$(dirname "$0")" && pwd)
TAG="omega-hub-runner:$(printf %.7s "$COMMIT")"
# The judge's files (runner/judge) come in as the named build context `judge`; the main context is
# the exported Omega tree only.
docker build -f "$HERE/Dockerfile" --build-context judge="$HERE/judge" --build-arg OMEGA_COMMIT="$COMMIT" \
  --build-arg WITH_CHROMIUM="${WITH_CHROMIUM:-1}" -t "$TAG" "$SRC"
DIGEST=$(docker image inspect "$TAG" --format '{{.Id}}')
printf '%s %s %s\n' "$TAG" "$DIGEST" "$COMMIT" | tee "${OMEGA_HUB_RUNNER_BUILT:-$HERE/.built}"
