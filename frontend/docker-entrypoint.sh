#!/bin/sh
set -eu

CONFIG_PATH="${CONFIG_PATH:-/usr/share/nginx/html/config.js}"

# Escape backslashes and quotes so values cannot break out of JS strings.
escape_js_string() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

CONFIG_NAMES="
VITE_HOST_API
VITE_HELP_LINK
VITE_MIXPANEL_SESSION_REPLAY_PERCENT
VITE_SESSION_REPLAY_BLOCKED_PATH_PREFIXES
VITE_INTERACTIVE_REQUEST_TIMEOUT_MS
VITE_ADD_QUEUE_CONTINUATION_MAX_PAGES
VITE_ADD_QUEUE_CONTINUATION_WALL_MS
VITE_ANALYTICS_REQUEST_TIMEOUT_MS
VITE_AGGREGATION_REQUEST_TIMEOUT_MS
VITE_AGGREGATION_POLL_TIMEOUT_MS
VITE_FILTER_VALUE_REQUEST_TIMEOUT_MS
VITE_AGGREGATION_POLL_INITIAL_DELAY_MS
VITE_AGGREGATION_POLL_MAX_DELAY_MS
VITE_AGGREGATION_POLL_BACKOFF_FACTOR
VITE_AGGREGATION_POLL_MAX_ATTEMPTS
VITE_AGGREGATION_POLL_MAX_CONSECUTIVE_FAILURES
VITE_CURSOR_MAX_EMPTY_CONTINUATIONS
VITE_OBSERVE_GRID_MAX_CONCURRENT_REQUESTS
VITE_OBSERVE_GRID_MAX_BLOCKS_IN_CACHE
VITE_OBSERVE_CURSOR_MAX_CHECKPOINTS
VITE_OBSERVE_PAGE_TRANSITION_MAX_WAIT_MS
VITE_OBSERVE_LIST_CELL_PREVIEW_MAX_CHARS
VITE_CHUNK_IMPORT_TIMEOUT_MS
VITE_CHUNK_IMPORT_MAX_RETRIES
VITE_CHUNK_IMPORT_RETRY_BASE_DELAY_MS
VITE_INTERACTIVE_MAX_PAGE_SIZE
VITE_OBSERVE_LIST_DEFAULT_PAGE_SIZE
VITE_PROPERTY_CATALOG_PAGE_SIZE
VITE_INTERACTIVE_TABLE_PAGE_SIZE
VITE_PROPERTY_CATALOG_SEARCH_PAGE_SIZE
VITE_PROPERTY_CATALOG_COMPACT_PAGE_SIZE
VITE_PROPERTY_CATALOG_LEGACY_PAGE_SIZE
VITE_PROPERTY_CATALOG_SEARCH_DEBOUNCE_MS
VITE_PROPERTY_CATALOG_SEARCH_MAX_UTF8_BYTES
VITE_PROPERTY_PICKER_RENDER_BATCH_SIZE
VITE_PROPERTY_PICKER_PREFETCH_MARGIN_PX
VITE_ATTRIBUTE_INVENTORY_SEARCH_DEBOUNCE_MS
VITE_FILTER_VALUE_SEARCH_DEBOUNCE_MS
VITE_FILTER_AUTO_APPLY_DEBOUNCE_MS
VITE_PROPERTY_CATALOG_STALE_TIME_MS
VITE_PROPERTY_CATALOG_CACHE_TIME_MS
VITE_PROPERTY_CATALOG_LEGACY_STALE_TIME_MS
VITE_PROPERTY_CATALOG_LEGACY_CACHE_TIME_MS
VITE_FILTER_VALUE_PAGE_SIZE
VITE_FILTER_VALUE_MIN_VISIBLE_RESULTS
VITE_FILTER_VALUE_STALE_TIME_MS
VITE_FILTER_VALUE_CACHE_TIME_MS
VITE_AUTOMATION_RULE_LIST_PAGE_SIZE
VITE_OBSERVE_PROJECT_PAGE_SIZE
VITE_EVAL_METRIC_MAX_WINDOW_DAYS
VITE_SIMULATION_PREVIEW_PAGE_SIZE
VITE_SIMULATION_PREVIEW_MAX_PAGE_SIZE
VITE_GROUND_TRUTH_DATASET_PAGE_SIZE
VITE_DATASET_ROW_ADJACENCY_MAX_ROWS
"

write_config_entry() {
  config_name="$1"
  config_value=$(printenv "$config_name" 2>/dev/null || :)
  config_value=$(escape_js_string "$config_value")
  printf '  %s: "%s",\n' "$config_name" "$config_value"
}

{
  printf '%s\n' 'window.__FUTURE_AGI_CONFIG__ = {'
  for config_name in $CONFIG_NAMES; do
    write_config_entry "$config_name"
  done
  printf '%s\n' '};'
} > "$CONFIG_PATH"

echo "[entrypoint] wrote ${CONFIG_PATH}"

http_enabled=${FRONTEND_HTTP_ENABLED:-true}
http_port=${FRONTEND_HTTP_PORT:-80}
tls_enabled=${FRONTEND_TLS_ENABLED:-false}
tls_port=${FRONTEND_TLS_PORT:-443}
reload_interval=${FRONTEND_TLS_RELOAD_INTERVAL:-30}
cert_file=${FRONTEND_TLS_CERT_FILE:-}
key_file=${FRONTEND_TLS_KEY_FILE:-}

case "$http_enabled:$tls_enabled" in
  true:true|true:false|false:true) ;;
  false:false) echo '[entrypoint] HTTP and TLS cannot both be disabled' >&2; exit 1 ;;
  *) echo '[entrypoint] FRONTEND_HTTP_ENABLED and FRONTEND_TLS_ENABLED must be true or false' >&2; exit 1 ;;
esac

check_port() {
  case "$2" in
    ''|*[!0-9]*) echo "[entrypoint] $1 must be a port from 1 to 65535" >&2; exit 1 ;;
  esac
  if [ "$2" -lt 1 ] || [ "$2" -gt 65535 ]; then
    echo "[entrypoint] $1 must be a port from 1 to 65535" >&2
    exit 1
  fi
}

if [ "$http_enabled" = true ]; then check_port FRONTEND_HTTP_PORT "$http_port"; fi
if [ "$tls_enabled" = true ]; then
  check_port FRONTEND_TLS_PORT "$tls_port"
  if [ "$http_enabled" = true ] && [ "$http_port" -eq "$tls_port" ]; then
    echo '[entrypoint] HTTP and TLS ports must differ' >&2
    exit 1
  fi
  case "$reload_interval" in
    ''|*[!0-9]*) echo '[entrypoint] FRONTEND_TLS_RELOAD_INTERVAL must be 1-60 seconds' >&2; exit 1 ;;
  esac
  if [ "$reload_interval" -lt 1 ] || [ "$reload_interval" -gt 60 ]; then
    echo '[entrypoint] FRONTEND_TLS_RELOAD_INTERVAL must be 1-60 seconds' >&2
    exit 1
  fi
  for path in "$cert_file" "$key_file"; do
    # Paths go into nginx directives; reject characters that could inject directives.
    case "$path" in
      ''|*[!a-zA-Z0-9_./-]*) echo '[entrypoint] TLS file paths must be nonempty and contain only letters, digits, _, -, . and /' >&2; exit 1 ;;
    esac
    if [ ! -s "$path" ] || [ ! -r "$path" ]; then
      echo "[entrypoint] TLS file missing, empty or unreadable: $path" >&2
      exit 1
    fi
  done
fi

# /tmp is writable even in the Helm read-only-root deployment. Its ConfigMap
# nginx.conf need not include this file, but the image's nginx.conf does.
mkdir -p /tmp/futureagi-nginx
{
  if [ "$http_enabled" = true ]; then
    printf 'listen %s;\nlisten [::]:%s;\n' "$http_port" "$http_port"
  fi
  if [ "$tls_enabled" = true ]; then
    printf 'listen %s ssl;\nlisten [::]:%s ssl;\n' "$tls_port" "$tls_port"
    printf 'ssl_certificate "%s";\nssl_certificate_key "%s";\n' "$cert_file" "$key_file"
    printf '%s\n' 'ssl_protocols TLSv1.2 TLSv1.3;' \
      'ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305;' \
      'ssl_prefer_server_ciphers off;' \
      'ssl_session_cache shared:SSL:10m;' \
      'ssl_session_tickets off;'
  fi
} > /tmp/futureagi-nginx/listen.conf

if [ "$tls_enabled" = true ] && [ "${1:-}" = nginx ]; then
  # After exec this PID becomes nginx's master; HUP replaces workers without
  # interrupting connections. A rejected config leaves its old workers alive.
  nginx_pid=$$
  last_hash=$(cat "$cert_file" "$key_file" | md5sum)
  (
    while kill -0 "$nginx_pid" 2>/dev/null; do
      sleep "$reload_interval"
      if [ ! -s "$cert_file" ] || [ ! -s "$key_file" ] ||
         [ ! -r "$cert_file" ] || [ ! -r "$key_file" ]; then
        echo '[tls-reload] certificate or key missing, empty or unreadable; keeping current configuration' >&2
        continue
      fi
      new_hash=$(cat "$cert_file" "$key_file" | md5sum)
      if [ "$new_hash" != "$last_hash" ]; then
        last_hash=$new_hash
        echo '[tls-reload] certificate files changed; signalling nginx master' >&2
        kill -HUP "$nginx_pid" 2>/dev/null || exit 0
      fi
    done
  ) &
fi

exec "$@"
