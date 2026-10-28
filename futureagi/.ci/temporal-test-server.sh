#!/usr/bin/env bash
# temporalio==1.21.1 currently selects SDK Java 1.40.0. Pin the upstream asset
# rather than letting each test hit temporal.download's rate-limited proxy.
# SHA256 is the release asset digest from temporalio/sdk-java v1.40.0.
set -euo pipefail
mode=${1:?usage: temporal-test-server.sh fetch|install ARCHIVE_DIRECTORY [BIN_DIRECTORY]}
archives=${2:?archive directory required}
name=temporal-test-server_1.40.0_linux_amd64
sha256=524465a96274831e004326f6e2f2a5f3ef9aaed72c2069cf16c1d64b8b1ec3ca
archive="$archives/$name.tar.gz"
verify() { echo "$sha256  $1" | sha256sum -c -; }
case "$mode" in
  fetch)
    mkdir -p "$archives"
    if [[ -f "$archive" ]]; then
      echo "Temporal archive cache hit: $name"
    else
      echo "Temporal archive cache miss: fetching $name from upstream GitHub"
      # At most four attempts, exponential backoff (curl default), and a hard
      # wall-clock cap even if Retry-After asks for a longer wait.
      trap 'rm -f "$archive.partial"' EXIT
      timeout 180s curl --fail --location --show-error \
        --connect-timeout 10 --max-time 30 --retry 3 --retry-all-errors \
        --retry-max-time 120 \
        "https://github.com/temporalio/sdk-java/releases/download/v1.40.0/$name.tar.gz" \
        --output "$archive.partial"
      verify "$archive.partial"
      mv "$archive.partial" "$archive"
    fi
    verify "$archive"
    ;;
  install)
    bin=${3:?binary directory required}
    # Also verify artifacts/cache restores on every consumer. Never download
    # here: a missing/corrupt artifact fails setup, not a later workflow test.
    verify "$archive"
    mkdir -p "$bin"
    # Extract only the expected file, without trusting archive paths/modes.
    tar -xOzf "$archive" "$name/temporal-test-server" > "$bin/temporal-test-server"
    chmod 755 "$bin/temporal-test-server"
    echo "Temporal $name installed from verified archive; SDK download disabled"
    ;;
  *) echo "Unknown mode: $mode" >&2; exit 1 ;;
esac
