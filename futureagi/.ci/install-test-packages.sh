#!/usr/bin/env bash
# Cache is keyed by the exact Ubuntu runner image + this script. Never use a
# prefix restore: indexes and archives must describe the same base packages.
set -euo pipefail
cache=${1:?usage: install-test-packages.sh ABSOLUTE_CACHE_DIRECTORY}
[[ "$cache" == /* ]] || { echo "Cache path must be absolute" >&2; exit 1; }
mkdir -p "$cache/lists/partial" "$cache/archives/partial" "$cache/sources"
# Only Ubuntu supplies these packages. Do not refresh unrelated repositories.
# The runner's mirror+file source selects azure.archive.ubuntu.com over HTTP:
# observed transfers of the 9 MB libcodec2 package took multiple minutes even
# when index refresh took seconds. Use Ubuntu's canonical HTTPS archive, keeping
# the distro suites/components and Signed-By checks, without editing host sources.
sed 's|^URIs:.*|URIs: https://archive.ubuntu.com/ubuntu|' \
  /etc/apt/sources.list.d/ubuntu.sources > "$cache/sources/ubuntu.sources"
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
if [[ ! -f "$cache/complete" ]]; then
  echo "APT cache miss: refresh Ubuntu indexes and download ffmpeg/ffprobe + xmlsec1"
  # Separate bounds make the formerly quiet 33-39 minute stall diagnosable.
  # Retries are for asset transport only, never tests or package installation.
  timeout 120s sudo apt-get "${options[@]}" update
  timeout 180s sudo apt-get "${options[@]}" install -y --download-only \
    --no-install-recommends ffmpeg xmlsec1
  touch "$cache/complete"
else
  echo "APT cache hit: using runner-image-matched indexes and archives (no network)"
fi
# Fail closed for incomplete/stale archives; do not silently fall back to apt
# downloads in a test job. apt validates package hashes against its indexes.
timeout 120s sudo apt-get "${options[@]}" install -y --no-download \
  --no-install-recommends ffmpeg xmlsec1
# apt's root-owned partial directories otherwise prevent the cache action from
# reading the tree. Only this dedicated cache is made readable.
sudo chmod -R a+rX "$cache"
ffmpeg -version | head -n 1
ffprobe -version | head -n 1
xmlsec1 --version
