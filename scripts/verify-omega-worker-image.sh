#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
image="${1:?image is required}"
node scripts/verify-omega-worker-contents.mjs "$image"

# Exercise the shipped artifact first; imports must not start either daemon.
docker run --rm --network none "$image" worker/worker.mjs --healthcheck
docker run --rm --network none "$image" --input-type=module -e \
  "await import('./worker/daemon.mjs'); await import('./worker/grouping-daemon.mjs');"
docker run --rm -i --network none "$image" --input-type=module \
  < futureagi/ee/workers/error-feed-node/kafka-codec-smoke.mjs

# Tests/fixtures are intentionally absent from the production image. Mount only
# the reviewed worker source, using the image's installed runtime dependencies.
test_source=$(mktemp -d)
trap 'rm -rf "$test_source"' EXIT
tar -C futureagi/ee/workers/error-feed-node --exclude=node_modules --exclude='*.tgz' -cf - . \
  | tar -C "$test_source" -xf -
chmod -R a+rX "$test_source"
docker run --rm --network none \
  --mount "type=bind,src=$test_source,dst=/app/worker,readonly" \
  --entrypoint /bin/sh "$image" -c \
  'node --test worker/*.test.mjs worker/grouping/*.test.mjs worker/grouping/f6/*.test.mjs'
