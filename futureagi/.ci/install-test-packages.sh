#!/usr/bin/env bash
# Cache is keyed by the exact Ubuntu runner image + this script. Never use a
# prefix restore: indexes and archives must describe the same base packages.
set -euo pipefail
cache=${1:?usage: install-test-packages.sh ABSOLUTE_CACHE_DIRECTORY}
[[ "$cache" == /* ]] || { echo "Cache path must be absolute" >&2; exit 1; }
mkdir -p "$cache/lists/partial" "$cache/archives/partial" "$cache/sources"
packages=(ffmpeg xmlsec1 pgbouncer)
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
# refresh TRIES BOUND. A mid-sync archive fails update with "unexpected size"
# (exit 100) and clears within minutes, so a source given more than one try is
# asked again. A timeout (exit 124) means a slow source: stop.
refresh() {
  local tries=$1 bound=$2 try status
  for ((try = 1; try <= tries; try++)); do
    timeout "$bound" sudo apt-get "${options[@]}" update && return 0
    status=$?
    if [[ $status == 124 || $try == "$tries" ]]; then return "$status"; fi
    echo "::warning::apt index refresh failed (exit $status); retry in $((try * 20))s"
    sleep $((try * 20))
  done
}
# download TRIES REFRESH_BOUND DOWNLOAD_BOUND
download() {
  refresh "$1" "$2" && timeout "$3" sudo apt-get "${options[@]}" install -y --download-only \
    --no-install-recommends "${packages[@]}"
}
if [[ ! -f "$cache/complete" ]]; then
  echo "APT cache miss: refresh Ubuntu indexes and download ffmpeg/ffprobe + xmlsec1 + pgbouncer"
  started=$SECONDS
  # Only Ubuntu supplies these packages. Do not refresh unrelated repositories.
  # Two Ubuntu sources, both checked against the runner's Signed-By keyring.
  # The runner's own mirror list goes first, with one short try. Measured on
  # 40 shards of one queue run (27 Nov 2026): the mirror refreshed the indexes
  # in 3 s and fetched the packages in 1 to 2 s on all 9 shards that reached
  # it, while archive.ubuntu.com took 49 to 178 s to fetch them on 8 shards
  # and failed on 9 after 120 to 299 s. The short bound is for the opposite
  # day: the mirror once took minutes over the 9 MB libcodec2 package. The
  # cache keeps the source file: its lists are named by these URIs.
  cp "$runner_sources" "$cache/sources/ubuntu.sources"
  apt_source=runner
  echo "APT source: runner ($runner_sources), one bounded try"
  if ! download 1 30s 30s; then
    # Lists must all come from one source. Complete .debs stay: apt re-checks
    # their hashes against the new indexes. Same suites, components and
    # Signed-By; only the URIs change, and no host source is edited.
    echo "::warning::the runner's apt mirror did not finish within its bound; retrying with Ubuntu's canonical archive"
    sudo rm -rf "$cache/lists" "$cache/archives/partial"
    mkdir -p "$cache/lists/partial" "$cache/archives/partial"
    sed 's|^URIs:.*|URIs: https://archive.ubuntu.com/ubuntu|' \
      "$runner_sources" > "$cache/sources/ubuntu.sources"
    apt_source=canonical
    echo "APT source: canonical (https://archive.ubuntu.com/ubuntu)"
    # Separate bounds make the formerly quiet 33-39 minute stall diagnosable.
    # Retries are for asset transport only, never tests or package installation.
    download 3 120s 180s
  fi
  echo "$apt_source" > "$cache/complete"
  # One line to grep when the two sources are measured again.
  echo "APT packages served by the $apt_source source in $((SECONDS - started))s"
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
