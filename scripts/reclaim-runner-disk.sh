#!/usr/bin/env bash
# GitHub-hosted runners: / is the small volume (the backend image alone
# unpacks ~15GB of CUDA/nvidia libs), so docker's data moves to /mnt (~65GB
# free). Run it before docker/setup-buildx-action, whose builder container
# would otherwise live under the old root.
set -euo pipefail
df -h / /mnt
sudo rm -rf /usr/share/dotnet /opt/ghc /usr/local/lib/android \
  /opt/hostedtoolcache/CodeQL /usr/local/share/boost || true
# No guard on the move: if it is left half done, docker is broken, so every
# later step must fail the job rather than carry on.
sudo systemctl stop docker docker.socket
sudo mv /var/lib/docker /mnt/docker
sudo ln -s /mnt/docker /var/lib/docker
sudo systemctl start docker
timeout 60 bash -c 'until docker info >/dev/null 2>&1; do sleep 2; done'
docker info --format 'docker root: {{.DockerRootDir}}'
df -h / /mnt
