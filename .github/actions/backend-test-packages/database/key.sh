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
config=$("${compose[@]}" config | sha256sum)
if [[ ${1:-running} == probe ]]; then
  tools='3.12.3|0.13.9'
else
  python_version=$(.venv/bin/python -c 'import platform; print(platform.python_version())')
  uv_version=$(uv --version | cut -d ' ' -f2)
  tools="$python_version|$uv_version"
fi
runtime=$(printf '%s\n' "$ids" "$config" "${ImageVersion:?}" "$(uname -m)" "$tools" | sha256sum)
python3 -I .ci/db_dump.py key "$runtime"
