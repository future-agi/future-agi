#!/usr/bin/env bash
# Run before setup-buildx creates its builder. Fast-path headroom is deliberately
# larger than the ~65 GB Docker volume used by the existing cold-build route.
set -euo pipefail

# df values are available KiB / available inodes, not machine or volume sizes.
# When the checkout and Docker share a device, reserve room for BOTH consumers.
enough_headroom() { # same-device(yes/no), docker KiB, docker inodes, checkout KiB, checkout inodes
  local docker_kib=$((64 * 1024 * 1024)) docker_inodes=2000000
  local host_kib=$((16 * 1024 * 1024)) host_inodes=500000 value
  for value in "${@:2}"; do [[ $value =~ ^[0-9]+$ ]] || return 1; done
  if [[ $1 == yes ]]; then
    (( $2 >= docker_kib + host_kib && $3 >= docker_inodes + host_inodes &&
       $4 >= docker_kib + host_kib && $5 >= docker_inodes + host_inodes ))
  else
    (( $2 >= docker_kib && $3 >= docker_inodes && $4 >= host_kib && $5 >= host_inodes ))
  fi
}

has_room() {
  local root=$1 work=$2 same=no root_dev work_dev
  root_dev=$(stat -Lc %d "$root") || return 1
  work_dev=$(stat -Lc %d "$work") || return 1
  # The fast path is deliberately limited to known layouts. Runner tools/temp
  # need the host reserve; a separate containerd snapshot store could consume
  # image-sized space on a third volume. Leave unusual layouts on full reclaim.
  local mount
  for mount in / "${RUNNER_TEMP:-$work}"; do
    [[ $(stat -Lc %d "$mount") == "$work_dev" ]] || return 1
  done
  if [[ -d /var/lib/containerd ]]; then
    [[ $(stat -Lc %d /var/lib/containerd) == "$root_dev" ]] || return 1
  fi
  if [[ $root_dev == "$work_dev" ]]; then same=yes; fi
  local dk di wk wi
  read -r dk di < <(df -k --output=avail,iavail "$root" | tail -1) || return 1
  read -r wk wi < <(df -k --output=avail,iavail "$work" | tail -1) || return 1
  enough_headroom "$same" "$dk" "$di" "$wk" "$wi"
}

reclaim_disk() {
  local root work=${GITHUB_WORKSPACE:-$PWD} mode=${1:-}
  [[ $# == 0 || ( $# == 1 && $mode == --ordinary ) ]] || { echo 'Usage: reclaim-runner-disk.sh [--ordinary]' >&2; return 2; }
  root=$(docker info --format '{{.DockerRootDir}}')
  [[ -d $root ]] || { echo 'Cannot resolve Docker data-root' >&2; return 1; }
  df -k --output=source,target,avail,iavail "$root" "$work"
  if [[ $mode == --ordinary ]] && has_room "$root" "$work"; then
    echo 'Cold build and checkout reserves available; keeping runner tools and Docker data-root.'
    return
  fi
  local unused=(/usr/share/dotnet /opt/ghc /usr/local/lib/android
    /opt/hostedtoolcache/CodeQL /usr/local/share/boost)
  # RECLAIM_DELETE=background deletes the unused toolchains without waiting
  # (1 to 2 minutes of every job). It only frees space on /, nothing later
  # reads those directories, and Docker's data moves to /mnt below, so the
  # freed-space fast path is not re-checked in that mode. The default waits.
  if [[ ${RECLAIM_DELETE:-wait} == background ]]; then
    sudo nohup rm -rf "${unused[@]}" >/dev/null 2>&1 &
  else
    sudo rm -rf "${unused[@]}" || true
    if [[ $mode == --ordinary ]] && has_room "$root" "$work"; then
      echo 'Runner tool cleanup freed the cold-build reserve; keeping Docker data-root.'
      return
    fi
  fi
  # No measured headroom (or a different caller): keep the established full
  # reclaim path. The reserve is a fast-path threshold, not a reason to fail a
  # build which could fit. Never move into a pre-existing Docker directory.
  if [[ $root != /var/lib/docker || -L $root || ! -d /mnt || -e /mnt/docker || -L /mnt/docker ]]; then
    echo 'Cannot safely relocate this runner Docker data-root.' >&2
    return 1
  fi
  df -k --output=source,target,avail,iavail /mnt
  sudo systemctl stop docker docker.socket
  sudo mv "$root" /mnt/docker
  sudo ln -s /mnt/docker "$root"
  sudo systemctl start docker
  timeout 60 bash -c 'until docker info >/dev/null 2>&1; do sleep 2; done'
  docker info --format 'docker root: {{.DockerRootDir}}'
  df -k --output=source,target,avail,iavail "$root" "$work"
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then reclaim_disk "$@"; fi
