#!/usr/bin/env bash
# Cache is keyed by the exact Ubuntu runner image + this script. Never use a
# prefix restore: indexes and archives must describe the same base packages.
set -euo pipefail
cache=${1:?usage: install-test-packages.sh ABSOLUTE_CACHE_DIRECTORY}
[[ "$cache" == /* ]] || { echo "Cache path must be absolute" >&2; exit 1; }
mkdir -p "$cache/lists/partial" "$cache/archives/partial" "$cache/sources"
packages=(ffmpeg xmlsec1 pgbouncer bubblewrap)
runner_sources=/etc/apt/sources.list.d/ubuntu.sources
options=(
  -o "Dir::Etc::sourcelist=/dev/null"
  -o "Dir::Etc::sourceparts=$cache/sources"
  -o "Dir::State::lists=$cache/lists"
  -o "Dir::Cache::archives=$cache/archives"
  -o Acquire::Retries=2
  -o Acquire::http::Timeout=20
  -o Acquire::https::Timeout=20
  -o APT::Update::Error-Mode=any
)
# A mid-sync archive fails update with "unexpected size" (exit 100) and clears
# within minutes, so retry it. A timeout (exit 124) means a slow source: stop.
refresh() {
  local try status
  for try in 1 2 3; do
    timeout 120s sudo apt-get "${options[@]}" update && return 0
    status=$?
    if [[ $status == 124 || $try == 3 ]]; then return "$status"; fi
    echo "::warning::apt index refresh failed (exit $status); retry in $((try * 20))s"
    sleep $((try * 20))
  done
}
download() {
  refresh && timeout 180s sudo apt-get "${options[@]}" install -y --download-only \
    --no-install-recommends "${packages[@]}"
}
if [[ ! -f "$cache/complete" ]]; then
  echo "APT cache miss: refresh Ubuntu indexes and download ffmpeg/ffprobe + xmlsec1 + pgbouncer + bubblewrap"
  # Only Ubuntu supplies these packages. Do not refresh unrelated repositories.
  # The runner's mirror+file source selects azure.archive.ubuntu.com over HTTP:
  # observed transfers of the 9 MB libcodec2 package took multiple minutes even
  # when index refresh took seconds. Prefer Ubuntu's canonical HTTPS archive,
  # keeping the distro suites/components and Signed-By checks, without editing
  # host sources. The cache keeps this file: its lists are named by these URIs.
  sed 's|^URIs:.*|URIs: https://archive.ubuntu.com/ubuntu|' \
    "$runner_sources" > "$cache/sources/ubuntu.sources"
  apt_source=canonical
  echo "APT source: canonical (https://archive.ubuntu.com/ubuntu)"
  # Separate bounds make the formerly quiet 33-39 minute stall diagnosable.
  # Retries are for asset transport only, never tests or package installation.
  if ! download; then
    # Lists must all come from one source. Complete .debs stay: apt re-checks
    # their hashes against the new indexes.
    echo "::warning::canonical Ubuntu archive failed; retrying with the runner's own apt sources"
    sudo rm -rf "$cache/lists" "$cache/archives/partial"
    mkdir -p "$cache/lists/partial" "$cache/archives/partial"
    cp "$runner_sources" "$cache/sources/ubuntu.sources"
    apt_source=runner
    echo "APT source: runner ($runner_sources)"
    download
  fi
  echo "$apt_source" > "$cache/complete"
else
  echo "APT cache hit: $(cat "$cache/complete") source indexes and archives (no network)"
fi
# Fail closed for incomplete/stale archives; do not silently fall back to apt
# downloads in a test job. apt validates package hashes against its indexes.
timeout 120s sudo apt-get "${options[@]}" install -y --no-download \
  --no-install-recommends "${packages[@]}"
# apt's root-owned partial directories otherwise prevent the cache action from
# reading the tree. Only this dedicated cache is made readable.
sudo chmod -R a+rX "$cache"
ffmpeg -version | head -n 1
ffprobe -version | head -n 1
xmlsec1 --version
/usr/sbin/pgbouncer --version | head -n 1
bwrap --version
