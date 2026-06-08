#!/bin/sh
set -eu

CONFIG_PATH="${CONFIG_PATH:-/usr/share/nginx/html/config.js}"
VITE_HOST_API="${VITE_HOST_API:-}"
VITE_POSTHOG_KEY="${VITE_POSTHOG_KEY:-}"
VITE_POSTHOG_HOST="${VITE_POSTHOG_HOST:-}"

# Escape " so the value can't break out of the JS string literal.
escaped_host_api=$(printf '%s' "$VITE_HOST_API" | sed 's/"/\\"/g')
escaped_posthog_key=$(printf '%s' "$VITE_POSTHOG_KEY" | sed 's/"/\\"/g')
escaped_posthog_host=$(printf '%s' "$VITE_POSTHOG_HOST" | sed 's/"/\\"/g')

cat > "$CONFIG_PATH" <<EOF
window.__FUTURE_AGI_CONFIG__ = {
  VITE_HOST_API: "${escaped_host_api}",
  VITE_POSTHOG_KEY: "${escaped_posthog_key}",
  VITE_POSTHOG_HOST: "${escaped_posthog_host}"
};
EOF

echo "[entrypoint] wrote ${CONFIG_PATH} (VITE_HOST_API=${VITE_HOST_API})"
exec "$@"
