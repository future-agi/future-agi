#!/usr/bin/env bash
# Run in futureagi, after images have been pulled. Hash the actual local image
# IDs rather than the floating tags; assume nothing about a different runner.
set -euo pipefail
compose=(docker compose -f docker-compose.test.yml -p futureagi-test)
services=$("${compose[@]}" config --services | sort)
[[ -n $services ]]
ids=$(while IFS= read -r service; do
  if [[ ${1:-running} == probe ]]; then
    image=$("${compose[@]}" config --images "$service") || exit 1
    id=$(docker image inspect --format '{{.Id}}' "$image") || exit 1
  else
    container=$("${compose[@]}" ps -q "$service") || exit 1
    id=$(docker inspect --format '{{.Image}}' "$container") || exit 1
  fi
  [[ $id =~ ^sha256:[0-9a-f]{64}$ ]] || exit 1
  printf '%s:%s\n' "$service" "$id"
done <<< "$services")
# The Compose file's own bytes, not `docker compose config`: the rendered form
# changes with the Compose release a runner image ships, and that alone
# missed the cache. The file interpolates no variables, so its bytes are the
# whole service definition; the image IDs above pin what each tag resolved to.
config=$(sha256sum < docker-compose.test.yml)
if [[ ${1:-running} == probe ]]; then
  tools='3.12.3|0.13.9'
else
  python_version=$(.venv/bin/python -c 'import platform; print(platform.python_version())')
  uv_version=$(uv --version | cut -d ' ' -f2)
  tools="$python_version|$uv_version"
fi
# PostgreSQL dump/restore utilities run inside the database service image.
# GitHub can roll runner-image versions during a workflow, so that release is
# not a database input; keep concrete service images, Compose config, host
# architecture, and the pinned Python/uv toolchain in the exact recipe key.
runtime=$(printf '%s\n' "$ids" "$config" "$(uname -m)" "$tools" | sha256sum)
python3 -I .ci/db_dump.py key "$runtime"
