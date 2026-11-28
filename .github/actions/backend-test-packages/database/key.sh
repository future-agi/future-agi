#!/usr/bin/env bash
# Run in futureagi, with the test services running. Hash the actual image ID
# of the running database container rather than its floating tag; assume
# nothing about a different runner.
set -euo pipefail
compose=(docker compose -f docker-compose.test.yml -p futureagi-test)
# Only the database service makes and loads the dump; the other services'
# floating tags would only add misses.
container=$("${compose[@]}" ps -q test-db)
id=$(docker inspect --format '{{.Image}}' "$container")
[[ $id =~ ^sha256:[0-9a-f]{64}$ ]]
ids="test-db:$id"
# The Compose file's own bytes, not `docker compose config`: the rendered form
# changes with the Compose release a runner image ships, and that alone
# missed the cache. The file interpolates no variables, so its bytes are the
# whole service definition; the image IDs above pin what each tag resolved to.
config=$(sha256sum < docker-compose.test.yml)
python_version=$(.venv/bin/python -c 'import platform; print(platform.python_version())')
uv_version=$(uv --version | cut -d ' ' -f2)
tools="$python_version|$uv_version"
# PostgreSQL dump/restore utilities run inside the database service image.
# GitHub can roll runner-image versions during a workflow, so that release is
# not a database input; keep concrete service images, Compose config, host
# architecture, and the pinned Python/uv toolchain in the exact recipe key.
runtime=$(printf '%s\n' "$ids" "$config" "$(uname -m)" "$tools" | sha256sum)
python3 -I .ci/db_dump.py key "$runtime"
