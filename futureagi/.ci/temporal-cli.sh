#!/usr/bin/env bash
# The Temporal CLI 1.10.1 the eval-task mixed-version job runs as its local dev
# server, pinned for temporalio==1.36.1 (uv.lock); re-check the pairing when
# temporalio moves. Fetched from the upstream GitHub release, never from
# temporal.download's rate-limited proxy, and only when the Actions cache
# (keyed by the digest below) does not already hold it.
# SHA256 is the release asset digest from temporalio/cli v1.10.1.
set -euo pipefail
name=temporal_cli_1.10.1_linux_amd64
sha256=4c1500e2ce2d4de136e5fe8852d9b7f82eb62f19b6b383b3e0c040519a9d20b1
mode=${1:?usage: temporal-cli.sh digest | fetch ARCHIVE_DIRECTORY | install ARCHIVE_DIRECTORY BIN_DIRECTORY}
if [[ "$mode" == digest ]]; then
  # The cache key: a new pin is a new cache entry.
  echo "$sha256"
  exit 0
fi
archives=${2:?archive directory required}
archive="$archives/$name.tar.gz"
verify() { echo "$sha256  $1" | sha256sum -c -; }
case "$mode" in
  fetch)
    mkdir -p "$archives"
    if [[ -f "$archive" ]]; then
      echo "Temporal CLI cache hit: $name"
    else
      echo "Temporal CLI cache miss: fetching $name from upstream GitHub"
      # At most four attempts, exponential backoff (curl default), and a hard
      # wall-clock cap even if Retry-After asks for a longer wait.
      trap 'rm -f "$archive.partial"' EXIT
      timeout 180s curl --fail --location --show-error \
        --connect-timeout 10 --max-time 60 --retry 3 --retry-all-errors \
        --retry-max-time 120 \
        "https://github.com/temporalio/cli/releases/download/v1.10.1/$name.tar.gz" \
        --output "$archive.partial"
      verify "$archive.partial"
      mv "$archive.partial" "$archive"
    fi
    # A cache hit is checked exactly like a download; a mismatch fails here.
    verify "$archive"
    ;;
  install)
    bin=${3:?binary directory required}
    # Verified again by its consumer, and never downloaded here.
    verify "$archive"
    mkdir -p "$bin"
    # Extract only the expected file, without trusting archive paths/modes.
    tar -xOzf "$archive" temporal > "$bin/temporal"
    chmod 755 "$bin/temporal"
    echo "Temporal CLI $name installed from a verified archive"
    ;;
  *) echo "Unknown mode: $mode" >&2; exit 1 ;;
esac
