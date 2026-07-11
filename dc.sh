#!/usr/bin/env bash
set -euo pipefail

# Non-essential containers for local eval work. `slim`/`s` stops them so
# the machine breathes when you don't need serving, gateway, or the
# code-executor sandbox.
SLIM_CONTAINERS=(
  futureagi-serving-1
  futureagi-agentcc-gateway-1
  futureagi-code-executor-1
  futureagi-fi-collector-1
)

usage() {
  cat <<'EOF'
Docker Compose helper for the Future AGI OSS stack.

Usage:
  ./dc.sh [env] [action] [service]
  ./dc.sh [env] <svc1> <svc2> ...    # force-recreate listed services

Environment (optional, default: dev):
  dev, d      docker-compose.yml + docker-compose.dev.yml
              Backend + frontend build from local source;
              ./futureagi and ./frontend are volume-mounted so edits are live.
  prod, p     docker-compose.yml only
              Pulls the mainline futureagi/future-agi image; no local build.

Actions:
  (empty)     Restart backend + worker (quick restart, no rebuild)
  b           Build (all or specific service). Same tag as your compose.
  u           Up all services (or specific service)
  d           Down all services
  r           Down + up (full recreate; brings infra first)
  rs          Down + up + slim (full recreate, then stop non-essentials)
  slim, s     Stop non-essential containers only
  help, -h    Show this help

Service-list shorthand:
  Any argument list that doesn't start with a known action is treated as
  a list of services to force-recreate (down + up --force-recreate).
  Picks up code changes without rebuilding the image.

Examples:
  ./dc.sh                              Restart backend + worker (dev)
  ./dc.sh r                            Full recreate all services (dev)
  ./dc.sh rs                           Full recreate + stop non-essentials
  ./dc.sh slim                         Stop non-essential containers only
  ./dc.sh b backend                    Build backend image only
  ./dc.sh u backend                    Start backend only
  ./dc.sh backend worker               Force-recreate backend + worker
  ./dc.sh prod                         Restart backend + worker (prod mode)
  ./dc.sh prod r                       Full recreate (prod mode, pulls images)
EOF
}

# Parse environment
ENVIRONMENT=dev
case "${1:-}" in
  prod|p)
    ENVIRONMENT=prod
    shift
    ;;
  dev|d)
    ENVIRONMENT=dev
    shift
    ;;
esac

ACTION=${1:-}

# Compose files, in overlay order. dev = base + dev overlay; prod = base only.
compose_files=("-f" "docker-compose.yml")
if [[ "$ENVIRONMENT" == "dev" ]]; then
  compose_files+=("-f" "docker-compose.dev.yml")
fi
# Local-only overrides (gitignored). Picked up automatically if present.
if [[ -f "docker-compose.override.yml" ]]; then
  compose_files+=("-f" "docker-compose.override.yml")
fi
compose_cmd=(docker compose "${compose_files[@]}")

# Print the docker compose command before running it. Reading the invocation
# is how you learn the flag soup.
run_compose() {
  echo "+ ${compose_cmd[*]} $*"
  "${compose_cmd[@]}" "$@"
}

# Known action keywords. Anything else = service-list shorthand: force-recreate
# only the listed services, leaving the rest running.
KNOWN_ACTIONS_RE='^(b|u|d|r|rs|s|slim|-h|--help|help)$'
if [[ -n "$ACTION" && ! "$ACTION" =~ $KNOWN_ACTIONS_RE ]]; then
  SERVICES=("$@")
  echo "force-recreate ($ENVIRONMENT): ${SERVICES[*]}"
  run_compose up -d --force-recreate --no-deps "${SERVICES[@]}"
  exit 0
fi

shift || true
SERVICE=${1:-}

slim_containers() {
  echo "stopping non-essential containers..."
  for c in "${SLIM_CONTAINERS[@]}"; do
    if docker ps -aq -f "name=^${c}$" | grep -q .; then
      docker rm -f "$c" 2>/dev/null && echo "  removed $c"
    fi
  done
  echo "done"
}

case "$ACTION" in
  "")
    if [[ "$ENVIRONMENT" == "dev" ]]; then
      echo "restart backend + all workers (dev)"
      run_compose restart \
        backend \
        worker \
        worker-default \
        worker-tasks-s \
        worker-tasks-l \
        worker-tasks-xl \
        worker-trace-ingestion \
        worker-agent-compass
    else
      echo "restart backend + worker (prod)"
      run_compose restart backend worker
    fi
    ;;
  b)
    echo "build --no-cache ($ENVIRONMENT)"
    if [[ -n "$SERVICE" ]]; then
      run_compose build --no-cache "$SERVICE"
    else
      run_compose build --no-cache
    fi
    ;;
  u)
    echo "up ($ENVIRONMENT) ${SERVICE:+service: $SERVICE}"
    if [[ -n "$SERVICE" ]]; then
      run_compose up -d "$SERVICE"
    else
      run_compose up -d
    fi
    ;;
  d)
    echo "down ($ENVIRONMENT)"
    run_compose down
    ;;
  r)
    echo "down + up ($ENVIRONMENT)"
    run_compose down
    echo "starting infra services..."
    run_compose up -d postgres redis rabbitmq temporal minio clickhouse
    echo "waiting for infra to be healthy..."
    sleep 5
    echo "starting app services..."
    run_compose up -d
    ;;
  rs)
    echo "down + up + slim ($ENVIRONMENT)"
    run_compose down
    run_compose up -d
    echo "waiting 5s for containers to stabilize..."
    sleep 5
    slim_containers
    ;;
  s|slim)
    slim_containers
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    echo "Error: unknown action '$ACTION'"
    echo ""
    echo "Hint: valid actions are: b, u, d, r, rs, slim, help"
    echo "      run './dc.sh help' for full usage"
    exit 1
    ;;
esac
