#!/usr/bin/env bash
# scripts/release/build-reproducible.sh [--out DIR]
# Builds cmd/agentcc twice and fails unless both binaries have the same
# sha256. Each build runs on its own copy of the module source, in its own
# temp directory (so a host path that leaks into the binary differs between
# the two) with its own empty build cache (so the second build cannot reuse
# the first one's objects), using the release flags:
#
#   CGO_ENABLED=0 go build -trimpath -buildvcs=false -ldflags="-s -w"
#
# -s -w are the Dockerfile's flags, so the binary is the one the image ships.
# SOURCE_DATE_EPOCH is fixed (the caller's, else the last commit's time, else
# 315532800): the Go toolchain embeds no timestamp, and the variable pins any
# step that would. GOOS and GOARCH pass through (default: the host's).
#
# Go runs offline (GOPROXY=off, GOFLAGS=-mod=readonly) unless the caller sets
# GOPROXY or GOFLAGS.
#
# Prints both sha256 values and REPRODUCIBLE <sha256> on success. With --out,
# keeps the binary as DIR/agentcc-gateway_<goos>_<goarch> and its checksum
# line in DIR/agentcc-gateway_<goos>_<goarch>.sha256.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
GW=$(cd "$HERE/../.." && pwd)
OUT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --out) OUT=${2:?--out needs a directory}; shift 2 ;;
    *) echo "usage: build-reproducible.sh [--out DIR]" >&2; exit 2 ;;
  esac
done

case "$OUT" in "" | /*) ;; *) OUT="$PWD/$OUT" ;; esac
[ -n "${GOPROXY+x}" ] || export GOPROXY=off
[ -n "${GOFLAGS+x}" ] || export GOFLAGS=-mod=readonly

sha256() { if command -v sha256sum >/dev/null; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1; }

if [ -z "${SOURCE_DATE_EPOCH:-}" ]; then
  SOURCE_DATE_EPOCH=$(git -C "$GW" log -1 --format=%ct 2>/dev/null || true)
  SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH:-315532800}
fi
export SOURCE_DATE_EPOCH CGO_ENABLED=0
GOOS=$(cd "$GW" && go env GOOS)
GOARCH=$(cd "$GW" && go env GOARCH)
export GOOS GOARCH
NAME="agentcc-gateway_${GOOS}_${GOARCH}"

WORK=$(mktemp -d "${TMPDIR:-/tmp}/agentcc-repro.XXXXXX")
trap 'chmod -R u+w "$WORK" 2>/dev/null; rm -rf "$WORK"' EXIT

echo "build-reproducible: $(cd "$GW" && go version), $GOOS/$GOARCH, SOURCE_DATE_EPOCH=$SOURCE_DATE_EPOCH"
sums=()
for n in 1 2; do
  src="$WORK/src-$n-$RANDOM/agentcc-gateway"
  mkdir -p "$src" "$WORK/cache-$n"
  # The module source as it is on disk; build outputs and local state stay out.
  (cd "$GW" && tar --exclude=./dist --exclude=./bin -cf - .) | (cd "$src" && tar -xf -)
  (cd "$src" && GOCACHE="$WORK/cache-$n" go build -trimpath -buildvcs=false -ldflags="-s -w" \
    -o "$WORK/out-$n/$NAME" ./cmd/agentcc)
  sums+=("$(sha256 "$WORK/out-$n/$NAME")")
  echo "build $n: ${sums[$((n - 1))]}  ($src)"
done

if [ "${sums[0]}" != "${sums[1]}" ]; then
  echo "NOT REPRODUCIBLE: the two builds differ" >&2
  exit 1
fi
if [ -n "$OUT" ]; then
  mkdir -p "$OUT"
  cp "$WORK/out-1/$NAME" "$OUT/$NAME"
  echo "${sums[0]}  $NAME" >"$OUT/$NAME.sha256"
fi
echo "REPRODUCIBLE ${sums[0]}"
