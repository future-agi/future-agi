#!/usr/bin/env bash
# GitHub-hosted runners: / is the small volume (the backend image alone
# unpacks ~15GB of CUDA/nvidia libs), so docker's data moves to /mnt (~65GB
# free). Run it before docker/setup-buildx-action, whose builder container
# would otherwise live under the old root.
#
# RECLAIM_DELETE=background deletes the unused toolchains without waiting.
# The deletion only frees space on / and took 1 to 2 minutes of every job;
# nothing later reads those directories, and docker's data is on /mnt before
# the script returns. The default waits, as before.
set -euo pipefail
df -h / /mnt
unused=(/usr/share/dotnet /opt/ghc /usr/local/lib/android
  /opt/hostedtoolcache/CodeQL /usr/local/share/boost)
if [[ "${RECLAIM_DELETE:-wait}" == background ]]; then
  sudo nohup rm -rf "${unused[@]}" > /dev/null 2>&1 &
else
  sudo rm -rf "${unused[@]}" || true
fi
# No guard on the move: if it is left half done, docker is broken, so every
# later step must fail the job rather than carry on.
sudo systemctl stop docker docker.socket
sudo mv /var/lib/docker /mnt/docker
sudo ln -s /mnt/docker /var/lib/docker
sudo systemctl start docker
timeout 60 bash -c 'until docker info >/dev/null 2>&1; do sleep 2; done'
docker info --format 'docker root: {{.DockerRootDir}}'
df -h / /mnt
