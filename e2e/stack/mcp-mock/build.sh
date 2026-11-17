#!/usr/bin/env bash
# Input to bin/e2e build mcpmock; run on the same host as the managed stack.
# Builds the real catalog generator's fixture output and one isolated mock.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
OUT="$ROOT/e2e/.mcp"
mkdir -p "$OUT/catalog/apps"
cp -R "$ROOT/e2e/stack/mcp-mock/catalog/apps/e2e-echo" "$OUT/catalog/apps/"
(cd "$ROOT/agentcc-gateway" && go run ./cmd/agentcc-catalog build \
  --catalog "$OUT/catalog" --django-copy '' --date 2026-10-24 --source-commit "$(git rev-parse HEAD)")
docker build -t futureagi/agentcc-mcpmock:e2e-local -f "$ROOT/e2e/stack/mcp-mock/Dockerfile" "$ROOT"
container=$(docker create futureagi/agentcc-mcpmock:e2e-local)
trap 'docker rm "$container" >/dev/null' EXIT
# Only the public certificate leaves the test image; never its generated key.
docker cp "$container:/tls/ca.pem" "$OUT/ca.pem"
