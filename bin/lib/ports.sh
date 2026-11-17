# shellcheck shell=bash
# bin/install's host-port preflight. Sourced; needs env.sh and bin/install's
# output helpers and colours.

port_holder() {
  # lsof / ss exit non-zero when nothing's bound. Disable both errexit and
  # ERR-trap inheritance just for this function so a clean "nothing's
  # listening" doesn't get reported as an install failure.
  set +eE
  local port="$1" out=""
  if command -v lsof >/dev/null 2>&1; then
    out=$(lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null)
    [[ -n "$out" ]] && awk 'NR>1 {printf "%s (pid %s)\n", $1, $2; exit}' <<<"$out"
  elif command -v ss >/dev/null 2>&1; then
    out=$(ss -ltn 2>/dev/null)
    [[ -n "$out" ]] && awk -v p=":$port\$" '$4 ~ p {print $0; exit}' <<<"$out"
  fi
  set -eE
  return 0
}

# Ports this run keeps out of suggestions: the configured port of every var
# of the stack (filled in below, before any is checked) and each replacement
# already suggested. A replacement for one var must not land on a port that a
# later var is set to: both would bind it, and compose fails with "port is
# already allocated".
RESERVED_PORTS=()
port_reserved() {
  local s
  # ${arr[@]+"${arr[@]}"} survives `set -u` on an empty array.
  for s in ${RESERVED_PORTS[@]+"${RESERVED_PORTS[@]}"}; do
    [[ "$s" == "$1" ]] && return 0
  done
  return 1
}

# Find the first free, unreserved TCP port starting from $1 and reserve it.
# Result is written to FREE_PORT (a global variable) so updates to
# RESERVED_PORTS persist — `$(find_free_port ...)` would run in a subshell,
# hiding the array mutation from the next call.
FREE_PORT=""
find_free_port() {
  local p="$1"
  FREE_PORT=""
  while (( p < 65535 )); do
    if ! port_reserved "$p" && [[ -z "$(port_holder "$p" 2>/dev/null)" ]]; then
      RESERVED_PORTS+=("$p")
      FREE_PORT="$p"
      return 0
    fi
    p=$((p + 1))
  done
  return 1
}

# All host-bound ports of the chosen stack, VAR:DEFAULT a line, as the compose
# files publish them (futureagi/tests/test_oss_install_hardening.py compares
# them). Data-store ports bind 127.0.0.1 in compose, but they still collide
# with anything else on that interface (other docker stacks, SSH tunnels, host
# services). The standalone install publishes no Postgres, ClickHouse, Redis
# or Temporal port.
stack_ports() { # distributed (0|1), COMPOSE_PROFILES
  local profiles=",$2,"
  if (( $1 == 1 )); then
    printf '%s\n' \
      FRONTEND_PORT:3000 \
      BACKEND_PORT:8000 \
      AGENTCC_GATEWAY_PORT:8090 \
      SERVING_PORT:8080 \
      CODE_EXECUTOR_PORT:8060 \
      PG_PORT:5432 \
      CH_HTTP_PORT:8123 \
      CH_PORT:9000 \
      REDIS_PORT:6379 \
      MINIO_API_PORT:9005 \
      MINIO_CONSOLE_PORT:9006 \
      TEMPORAL_PORT:7233 \
      PROPERTY_CATALOG_KAFKA_PORT:29092 \
      FI_COLLECTOR_OTLP_PORT:4317 \
      FI_COLLECTOR_OTLP_HTTP_PORT:4318 \
      FI_COLLECTOR_ADMIN_PORT:9464 \
      PEERDB_PORT:9900
    # UIs that only run under a COMPOSE_PROFILES entry.
    if [[ "$profiles" == *,all,* || "$profiles" == *,full,* || "$profiles" == *,peerdb,* ]]; then
      printf '%s\n' PEERDB_UI_PORT:3001
    fi
    if [[ "$profiles" == *,all,* || "$profiles" == *,full,* || "$profiles" == *,observability,* ]]; then
      printf '%s\n' TEMPORAL_UI_PORT:8085
    fi
  else
    printf '%s\n' \
      FRONTEND_PORT:3000 \
      BACKEND_PORT:8000 \
      FI_COLLECTOR_OTLP_PORT:4317 \
      FI_COLLECTOR_OTLP_HTTP_PORT:4318 \
      AGENTCC_GATEWAY_PORT:8090 \
      MINIO_API_PORT:9005
  fi
}

# Catch host-port collisions BEFORE the slow docker compose up. Without this,
# compose retries 3× on what's actually a structural conflict. A taken port
# gets a free one in .env, asked for unless non-interactive; the run stops
# while any stays taken.
check_host_ports() { # distributed (0|1), project, non_interactive (0|1)
  local distributed="$1" project="$2" non_interactive="$3"
  local entry i j var port holder suggestion apply answer host_api c
  local -a port_vars=() port_values=() unresolved=()

  # Every var's port, as configured and then as switched below.
  while IFS= read -r entry; do
    port_vars+=("${entry%%:*}")
    port_values+=("$(env_value "${entry%%:*}" "${entry##*:}")")
  done < <(stack_ports "$distributed" "$(env_value COMPOSE_PROFILES "")")
  RESERVED_PORTS=("${port_values[@]}")

  for i in "${!port_vars[@]}"; do
    var="${port_vars[$i]}"
    port="${port_values[$i]}"

    # Two vars on one port collide inside the stack, whatever else is
    # listening. A run that failed that way left nothing bound, so only this
    # check catches it on the re-run.
    holder=""
    for (( j = 0; j < i; j++ )); do
      if [[ "${port_values[$j]}" == "$port" ]]; then
        holder="${port_vars[$j]} (same port)"
        break
      fi
    done

    if [[ -z "$holder" ]]; then
      holder=$(port_holder "$port" 2>/dev/null) || holder=""

      if [[ -z "$holder" ]]; then
        ok "  $var=$port  free"
        continue
      fi

      # Allow our own already-running container to "hold" the port — re-runs
      # of bin/install on the same project are fine. Anything else collides.
      # Ask Docker rather than trusting the holder's process name: Colima
      # forwards published ports through `ssh`, not docker-proxy.
      if docker ps --format '{{.Names}}\t{{.Ports}}' 2>/dev/null \
           | awk -v p=":$port->" -v proj="^${project}-" \
                '$0 ~ p && $1 ~ proj' \
           | grep -q .; then
        ok "  $var=$port  (held by this project's container — fine)"
        continue
      fi
    fi

    # Find a suggested replacement.
    suggestion=""
    find_free_port $((port + 1)) && suggestion="$FREE_PORT" || true
    if [[ -z "$suggestion" ]]; then
      warn "  $var=$port  is taken by $holder  (no free port found above)"
      unresolved+=("$var=$port  ←  $holder")
      continue
    fi

    warn "  $var=$port  is taken by $holder"

    # Offer to switch. Auto-accept in non-interactive mode (CI / -y) so the
    # script can keep going unattended.
    apply=0
    if [[ "$non_interactive" -eq 1 ]]; then
      apply=1
      say "      ${DIM}→ auto-switching to $var=$suggestion${RESET}"
    else
      printf "      ${DIM}→ use ${BOLD}$var=$suggestion${RESET}${DIM} instead? [Y/n] ${RESET}"
      read -r answer || answer=""
      case "$answer" in
        ""|y|Y|yes|YES) apply=1 ;;
        *) apply=0 ;;
      esac
    fi

    if (( apply == 1 )); then
      set_env_var "$var" "$suggestion"
      port_values[i]="$suggestion"
      ok "  $var=$suggestion  written to .env"
      # Keep VITE_HOST_API in sync when BACKEND_PORT moves: the UI calls the
      # API there, http://localhost:8000 when it is unset. A URL naming another
      # host is the user's own and stays.
      if [[ "$var" == "BACKEND_PORT" ]]; then
        host_api=$(env_value VITE_HOST_API "")
        if [[ -z "$host_api" || "$host_api" =~ ^https?://localhost:[0-9]+/?$ ]]; then
          set_env_var VITE_HOST_API "http://localhost:${suggestion}"
          ok "  VITE_HOST_API=http://localhost:${suggestion}  (kept in sync)"
        fi
      fi
    else
      unresolved+=("$var=$port  ←  $holder")
    fi
  done

  if (( ${#unresolved[@]} > 0 )); then
    say ""
    say "  ${BOLD}${YELLOW}Unresolved port conflicts:${RESET}"
    for c in "${unresolved[@]}"; do say "    • $c"; done
    say ""
    say "  Free the port (stop the process / container) or set the var in .env"
    say "  to a free port, then re-run ${BOLD}./bin/install${RESET}."
    exit 1
  fi
}
