# shellcheck shell=bash
# bin/install's closing summary. Sourced; needs env.sh, bin/install's output
# helpers and colours, and the settings print_summary reads from it: DC,
# APP_SVC, DISTRIBUTED, FROM_SOURCE, LOG_FILE, USER_EMAIL and ACCOUNT_STATE
# with its ACCOUNT_* values.

# Detect alt URLs so VPS / WSL / Docker-in-VM users can pick what works for
# their network. Each detection is best-effort with a hard timeout — never
# blocks the success banner on a slow network.
detect_lan_ip() {
  if [[ "$(uname -s)" == "Darwin" ]]; then
    # First non-loopback IPv4 from `ifconfig`. en0 is wifi, en1 is ethernet,
    # but order varies — just pick the first non-127.x.
    ifconfig 2>/dev/null | awk '/inet / && $2 != "127.0.0.1" {print $2; exit}'
  elif command -v ip >/dev/null 2>&1; then
    ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") {print $(i+1); exit}}'
  else
    hostname -I 2>/dev/null | awk '{print $1}'
  fi
}
detect_public_ip() {
  # -4: an IPv6 answer would print as an invalid http://2601:…:3000 URL.
  curl -4 -fsS --max-time 3 https://ifconfig.io 2>/dev/null \
    || curl -4 -fsS --max-time 3 https://api.ipify.org 2>/dev/null \
    || true
}

print_summary() { # frontend port, backend port
  local frontend_port="$1" backend_port="$2" lan_ip public_ip collector_http_port collector_url gateway_port
  local ui_url upgrade_cmd setup_line host_api profiles
  lan_ip=$(detect_lan_ip)

  collector_http_port=$(env_value FI_COLLECTOR_OTLP_HTTP_PORT 4318)
  # The URL SDKs send traces to, as the compose files give it to the app.
  collector_url=$(compose_env_value FI_COLLECTOR_PUBLIC_URL "http://localhost:${collector_http_port}")
  gateway_port=$(env_value AGENTCC_GATEWAY_PORT 8090)
  ui_url="http://localhost:${frontend_port}"
  upgrade_cmd="git pull && ./bin/install"
  if (( FROM_SOURCE == 1 )) || [[ "$(env_value FUTURE_AGI_VERSION "")" == local ]]; then
    upgrade_cmd+=" --from-source"
  fi
  if (( DISTRIBUTED == 1 )); then
    setup_line="Distributed setup: one container per service"
  else
    setup_line="Standalone setup: one app container, next to Postgres and ClickHouse"
  fi

  printf "\n"
  if [[ -t 1 ]]; then
    printf "  ${BOLD}${GREEN}╭───────────────────────────────────────────╮${RESET}\n"
    printf "  ${BOLD}${GREEN}│${RESET}   ${BOLD}🎉  Future AGI is up${RESET}                    ${BOLD}${GREEN}│${RESET}\n"
    printf "  ${BOLD}${GREEN}╰───────────────────────────────────────────╯${RESET}\n"
  else
    say "Future AGI is up"
  fi
  say "  ${DIM}${setup_line}${RESET}"

  say ""
  if [[ "$ACCOUNT_STATE" == "$ACCOUNT_CREATED" || "$ACCOUNT_STATE" == "$ACCOUNT_EXISTS" ]]; then
    say "  ${BOLD}1. Sign in${RESET}"
    say "     ${ui_url}/auth/jwt/login   as ${USER_EMAIL}"
  elif [[ "$ACCOUNT_STATE" == "$ACCOUNT_FAILED" ]]; then
    say "  ${BOLD}${YELLOW}1. Create your account (ACTION REQUIRED)${RESET}"
    say "     The stack is running, but no account was created. Create one with:"
    say "     ${BOLD}$DC exec ${APP_SVC} python manage.py create_user${RESET}"
    say "     or sign up at ${ui_url}"
  else
    say "  ${BOLD}1. Create your account${RESET}"
    say "     ${ui_url}   ${DIM}(a short setup check, then sign-up)${RESET}"
  fi
  # The UI calls the API at VITE_HOST_API, http://localhost:8000 when unset:
  # other devices reach a working UI only once it names this machine.
  host_api=$(env_value VITE_HOST_API "")
  if [[ -z "$host_api" || "$host_api" == *://localhost* || "$host_api" == *://127.0.0.1* ]]; then
    [[ -n "$lan_ip" && "$lan_ip" != "127.0.0.1" ]] && \
      say "     ${DIM}Other devices: set VITE_HOST_API=http://${lan_ip}:${backend_port} in .env, then $DC up -d${RESET}"
  else
    [[ -n "$lan_ip" && "$lan_ip" != "127.0.0.1" ]] && \
      say "     ${DIM}Other devices on your network: http://${lan_ip}:${frontend_port}${RESET}"
    # Asks a third party for this host's address: only when it is printed.
    public_ip=$(detect_public_ip)
    [[ -n "$public_ip" && "$public_ip" != "$lan_ip" ]] && \
      say "     ${DIM}Public (only if your firewall allows it): http://${public_ip}:${frontend_port}${RESET}"
  fi

  say ""
  say "  ${BOLD}2. Get your API keys${RESET}"
  say "     In the app: Keys, in the left sidebar  →  ${ui_url}/dashboard/keys"
  say "     Copy the API key and the secret key."

  say ""
  say "  ${BOLD}3. Send your first trace${RESET} ${DIM}(on this machine; paste with your keys from step 2)${RESET}"
  say ""
  # Flush left, so the heredoc and the Python indentation survive a copy-paste.
  say "pip install fi-instrumentation-otel"
  say "export FI_API_KEY=\"<your API key>\" FI_SECRET_KEY=\"<your secret key>\" FI_BASE_URL=\"${collector_url}\""
  say "python3 - <<'PY'"
  say "from fi_instrumentation import register"
  say "from fi_instrumentation.fi_types import ProjectType"
  say "tracer_provider = register(project_name=\"my-first-project\", project_type=ProjectType.OBSERVE)"
  say "with tracer_provider.get_tracer(\"quickstart\").start_as_current_span(\"hello-future-agi\") as span:"
  say "    span.set_attribute(\"input.value\", \"Hello, Future AGI\")"
  say "tracer_provider.force_flush()"
  say "PY"
  say ""
  say "     Then open Tracing in the sidebar: ${BOLD}my-first-project${RESET} holds your first span."

  say ""
  say "  ${BOLD}Endpoints${RESET}"
  say "     UI           ${ui_url}"
  say "     API          http://localhost:${backend_port}"
  if [[ "$collector_url" == "http://localhost:${collector_http_port}" ]]; then
    say "     Traces       ${collector_url}  ${DIM}(OTLP/HTTP; this machine only)${RESET}"
  else
    say "     Traces       ${collector_url}  ${DIM}(OTLP/HTTP)${RESET}"
  fi
  say "     LLM gateway  http://localhost:${gateway_port}"
  if (( DISTRIBUTED == 1 )); then
    profiles=",$(env_value COMPOSE_PROFILES ""),"
    [[ "$profiles" == *,all,* || "$profiles" == *,full,* || "$profiles" == *,peerdb,* ]] && \
      say "     PeerDB UI    http://localhost:$(env_value PEERDB_UI_PORT 3001)  ${DIM}(peerdb / peerdb)${RESET}"
  fi

  say ""
  say "  ${BOLD}Manage${RESET}"
  say "     Logs         $DC logs -f ${APP_SVC}"
  say "     Stop         $DC down  ${DIM}(keeps your data)${RESET}"
  say "     Start        $DC up -d"
  say "     Upgrade      ${upgrade_cmd}"
  say "     Uninstall    ./bin/uninstall  ${DIM}(keeps your data; --wipe-data deletes it)${RESET}"
  say "     Settings     .env  ${DIM}(every variable: https://docs.futureagi.com/docs/self-hosting/configuration/reference)${RESET}"
  say "     Install log  $LOG_FILE"
  if (( DISTRIBUTED == 1 )); then
    say ""
    say "  ${DIM}Existing-data catalog backfill: restarts do not scan historical data."
    say "  After an upgrade, see fi-collector/PROPERTY_CATALOG_OSS.md.${RESET}"
  fi
  say ""
  say "  ${DIM}★ Star us: https://github.com/future-agi/future-agi${RESET}"
  say ""
}
